"""Unit tests for sky.jobs.recovery_strategy helpers."""

import asyncio
import threading
from types import SimpleNamespace
from unittest import mock

import pytest

from sky.jobs import recovery_strategy
from sky.server import common as server_common


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
