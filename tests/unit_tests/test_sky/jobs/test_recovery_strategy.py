"""Unit tests for sky.jobs.recovery_strategy helpers."""

import asyncio
import contextlib
import threading
from types import SimpleNamespace
from unittest import mock

import pytest

from sky.jobs import recovery_strategy
from sky.server import common as server_common
from sky.utils import status_lib


def _launch_executor() -> recovery_strategy.StrategyExecutor:
    executor = recovery_strategy.StrategyExecutor.__new__(
        recovery_strategy.StrategyExecutor)
    executor.cluster_name = 'job-1'
    executor.pool = None
    executor.file_mounts_blob_id = None
    executor.job_id = 1
    executor.task_id = 0
    executor.dag = SimpleNamespace(tasks=[])
    executor.starting = set()
    executor.starting_lock = asyncio.Lock()
    executor.starting_signal = asyncio.Condition(lock=executor.starting_lock)
    executor._cleanup_cluster = mock.MagicMock()
    return executor


def _patch_launch_loop(monkeypatch):

    @contextlib.asynccontextmanager
    async def scheduled_launch(*args, **kwargs):
        del args, kwargs
        yield

    monkeypatch.setattr(recovery_strategy.scheduler, 'scheduled_launch',
                        scheduled_launch)
    monkeypatch.setattr(recovery_strategy.sdk, 'api_start', mock.MagicMock())
    monkeypatch.setattr(
        recovery_strategy.sdk, 'launch',
        mock.MagicMock(return_value=server_common.RequestId('launch-request')))
    monkeypatch.setattr(recovery_strategy.state, 'set_restarting_async',
                        mock.AsyncMock())
    monkeypatch.setattr(recovery_strategy.state, 'set_backoff_pending_async',
                        mock.AsyncMock())
    monkeypatch.setattr(recovery_strategy.asyncio, 'sleep', mock.AsyncMock())
    monkeypatch.setattr(
        recovery_strategy.common_utils, 'Backoff',
        mock.MagicMock(return_value=SimpleNamespace(current_backoff=lambda: 0)))


def test_is_oom_failure_detects_oomkilled():
    exc = RuntimeError(
        'Failed to run setup commands on an instance. (exit code 1). '
        'Pod p terminated: OOMKilled (exit code 137).')
    assert recovery_strategy._is_oom_failure(exc) is True


def test_is_oom_failure_detects_out_of_memory_phrase():
    assert recovery_strategy._is_oom_failure(
        RuntimeError('The container ran out of memory.')) is True


def test_is_oom_failure_is_case_insensitive():
    assert recovery_strategy._is_oom_failure(
        RuntimeError('reason: oomkilled')) is True


def test_is_oom_failure_false_for_unrelated():
    assert recovery_strategy._is_oom_failure(
        RuntimeError('/bin/bash: line 1: conda: command not found')) is False


@pytest.mark.asyncio
async def test_submit_request_cancelled_before_id_cancels_submitted_request():
    executor = recovery_strategy.StrategyExecutor.__new__(
        recovery_strategy.StrategyExecutor)
    executor._cancel_request = mock.AsyncMock()
    submit_started = threading.Event()
    finish_submit = threading.Event()

    def submit() -> server_common.RequestId[None]:
        submit_started.set()
        assert finish_submit.wait(timeout=5)
        return server_common.RequestId('launch-request')

    task = asyncio.create_task(executor._submit_request(submit))
    assert await asyncio.to_thread(submit_started.wait, 5)

    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    finish_submit.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    executor._cancel_request.assert_awaited_once_with('launch-request')


@pytest.mark.asyncio
async def test_cancel_request_api_failure_does_not_raise(monkeypatch):
    executor = recovery_strategy.StrategyExecutor.__new__(
        recovery_strategy.StrategyExecutor)
    monkeypatch.setattr(
        recovery_strategy.sdk, 'api_cancel',
        mock.MagicMock(side_effect=RuntimeError('server unreachable')))
    sdk_get = mock.MagicMock()
    monkeypatch.setattr(recovery_strategy.sdk, 'get', sdk_get)

    await executor._cancel_request('launch-request')

    sdk_get.assert_not_called()


@pytest.mark.asyncio
async def test_wait_until_job_starts_cluster_preempted_marks_retryable(
        monkeypatch):
    executor = _launch_executor()
    monkeypatch.setattr(
        recovery_strategy.backend_utils, 'refresh_cluster_status_handle',
        mock.MagicMock(return_value=(status_lib.ClusterStatus.STOPPED, None)))

    submitted_at, cluster_unavailable = await (
        executor._wait_until_job_starts_on_cluster())

    assert submitted_at is None
    assert cluster_unavailable is True


@pytest.mark.asyncio
async def test_launch_repeated_controller_error_fails_after_one_retry(
        monkeypatch):
    executor = _launch_executor()
    _patch_launch_loop(monkeypatch)
    stream = mock.MagicMock(side_effect=OSError('handle is closed'))
    monkeypatch.setattr(recovery_strategy.sdk, 'stream_and_get', stream)

    with pytest.raises(recovery_strategy.exceptions.ManagedJobControllerError,
                       match='after 2 unexpected controller/dispatch failures'):
        await executor._launch(max_retry=None)

    assert recovery_strategy.sdk.launch.call_count == 2
    assert stream.call_count == 2
    assert executor._cleanup_cluster.call_count == 2
    recovery_strategy.state.set_backoff_pending_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_launch_resource_unavailable_resets_controller_failure_budget(
        monkeypatch):
    executor = _launch_executor()
    _patch_launch_loop(monkeypatch)
    unavailable = recovery_strategy.exceptions.ResourcesUnavailableError(
        'capacity unavailable')
    unavailable.with_failover_history([
        recovery_strategy.exceptions.ResourcesUnavailableError(
            'provider stock unavailable')
    ])
    stream = mock.MagicMock(side_effect=[
        OSError('handle is closed'), unavailable,
        OSError('handle is closed'),
        OSError('handle is closed')
    ])
    monkeypatch.setattr(recovery_strategy.sdk, 'stream_and_get', stream)

    with pytest.raises(recovery_strategy.exceptions.ManagedJobControllerError):
        await executor._launch(max_retry=None)

    assert recovery_strategy.sdk.launch.call_count == 4
    assert executor._cleanup_cluster.call_count == 4


@pytest.mark.asyncio
async def test_launch_healthy_runtime_without_job_fails_after_one_retry(
        monkeypatch):
    executor = _launch_executor()
    _patch_launch_loop(monkeypatch)
    monkeypatch.setattr(recovery_strategy.sdk, 'stream_and_get',
                        mock.MagicMock())
    monkeypatch.setattr(recovery_strategy.global_user_state,
                        'get_handle_from_cluster_name',
                        mock.MagicMock(return_value=None))
    executor._wait_until_job_starts_on_cluster = mock.AsyncMock(
        return_value=(None, False))

    with pytest.raises(recovery_strategy.exceptions.ManagedJobControllerError,
                       match='runtime was healthy'):
        await executor._launch(max_retry=None)

    assert recovery_strategy.sdk.launch.call_count == 2
    assert executor._cleanup_cluster.call_count == 2
    assert executor._wait_until_job_starts_on_cluster.await_count == 2


@pytest.mark.asyncio
async def test_failover_launch_persists_provider_cluster_name(monkeypatch):

    async def fake_launch(self,
                          max_retry=3,
                          raise_on_failure=True,
                          recovery=False):
        del self, max_retry, raise_on_failure, recovery
        return 123.0

    class FakeHandle:

        def __init__(self):
            self.launched_resources = SimpleNamespace(cloud='Kubernetes',
                                                      region='cks-dev-rno2a',
                                                      zone=None)
            self.cached_cluster_info = None
            self.cluster_name_on_cloud = 'job-76-2efc4401'

    monkeypatch.setattr(recovery_strategy.StrategyExecutor, '_launch',
                        fake_launch)
    monkeypatch.setattr(recovery_strategy.backends, 'CloudVmRayResourceHandle',
                        FakeHandle)
    monkeypatch.setattr(recovery_strategy.global_user_state,
                        'get_handle_from_cluster_name',
                        mock.MagicMock(return_value=FakeHandle()))
    set_job_infra = mock.MagicMock()
    set_cluster_name_on_cloud = mock.MagicMock()
    monkeypatch.setattr(recovery_strategy.state, 'set_job_infra', set_job_infra)
    monkeypatch.setattr(recovery_strategy.state, 'set_cluster_name_on_cloud',
                        set_cluster_name_on_cloud)
    executor = recovery_strategy.FailoverStrategyExecutor.__new__(
        recovery_strategy.FailoverStrategyExecutor)
    executor.cluster_name = 'job-76'
    executor.job_id = 76
    executor.task_id = 0

    submitted_at = await executor._launch()

    assert submitted_at == 123.0
    set_job_infra.assert_called_once_with(76,
                                          cloud='Kubernetes',
                                          region='cks-dev-rno2a',
                                          zone=None,
                                          current_node_names=None)
    set_cluster_name_on_cloud.assert_called_once_with(76, 0, 'job-76-2efc4401')
