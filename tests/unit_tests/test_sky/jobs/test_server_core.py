"""Tests for sky.jobs.server.core."""
from typing import Optional
from unittest import mock

import pytest

from sky import backends
from sky.jobs import state as managed_job_state
from sky.jobs.server import core as jobs_core


def _forwarded_tail(tail):
    """Call ``jobs_core.tail_logs`` with ``tail`` (mocking out the controller
    restart / backend / runner) and return the ``tail`` value forwarded to
    ``tail_managed_job_logs``."""
    fake_backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
    fake_runner = mock.MagicMock()
    fake_runner.tail_managed_job_logs.return_value = 0
    with mock.patch.object(jobs_core, '_maybe_restart_controller',
                           return_value=mock.MagicMock()), \
         mock.patch.object(jobs_core.backend_utils,
                           'get_backend_from_handle',
                           return_value=fake_backend), \
         mock.patch.object(jobs_core.managed_job_runner,
                           'current',
                           return_value=fake_runner):
        jobs_core.tail_logs(name=None,
                            job_id=1,
                            follow=False,
                            controller=False,
                            refresh=False,
                            tail=tail)
    fake_runner.tail_managed_job_logs.assert_called_once()
    return fake_runner.tail_managed_job_logs.call_args.kwargs['tail']


@pytest.mark.parametrize(
    ('given', 'expected'),
    [
        (0, None),  # dashboard download button's "all lines" sentinel
        (-1, None),  # `sky jobs logs --tail -1`
        (None, None),  # no tail -> all
        (200, 200),  # positive tail forwarded unchanged
        (5000, 5000),
    ])
def test_tail_logs_normalizes_non_positive_tail(given, expected):
    """A non-positive tail (0 / -1) means "all lines" and must be normalized
    to None before reaching the backward-seek tail reader (which asserts
    tail > 0). Otherwise the dashboard download (tail=0) raises
    AssertionError and produces an empty log."""
    assert _forwarded_tail(given) == expected


def test_default_runner_terminal_bounded_snapshot_reads_in_process(
        capsys: pytest.CaptureFixture[str]):
    runner = jobs_core._DefaultManagedJobRunner()
    handle = object.__new__(backends.LocalResourcesHandle)
    backend = mock.MagicMock(spec=backends.CloudVmRayBackend)

    with mock.patch.object(
            jobs_core.managed_job_state,
            'get_status',
            return_value=managed_job_state.ManagedJobStatus.SUCCEEDED
    ), mock.patch.object(jobs_core.managed_job_state,
                         'get_all_task_ids_names_statuses_logs',
                         return_value=[
                             (0, 'train',
                              managed_job_state.ManagedJobStatus.SUCCEEDED,
                              '/tmp/run.log', None)
                         ]), mock.patch.object(jobs_core.managed_job_utils,
                                               'stream_logs_by_id',
                                               return_value=('last line',
                                                             0)) as mock_stream:
        returncode = runner.tail_managed_job_logs(handle=handle,
                                                  backend=backend,
                                                  job_id=42,
                                                  job_name=None,
                                                  follow=False,
                                                  controller=False,
                                                  tail=1,
                                                  tail_offset=2,
                                                  task='train')

    assert returncode == 0
    assert capsys.readouterr().out == 'last line\n'
    mock_stream.assert_called_once_with(42,
                                        follow=False,
                                        tail=1,
                                        tail_offset=2,
                                        task='train',
                                        watch_disconnect=False)
    backend.tail_managed_job_logs.assert_not_called()


def test_default_runner_terminal_snapshot_waits_for_local_log():
    runner = jobs_core._DefaultManagedJobRunner()
    handle = object.__new__(backends.LocalResourcesHandle)
    backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
    backend.tail_managed_job_logs.return_value = 0

    with mock.patch.object(
            jobs_core.managed_job_state,
            'get_status',
            return_value=managed_job_state.ManagedJobStatus.SUCCEEDED
    ), mock.patch.object(
            jobs_core.managed_job_state,
            'get_all_task_ids_names_statuses_logs',
            return_value=[
                (0, 'train', managed_job_state.ManagedJobStatus.SUCCEEDED, None,
                 None)
            ]), mock.patch.object(jobs_core.managed_job_utils,
                                  'stream_logs_by_id') as mock_stream:
        returncode = runner.tail_managed_job_logs(handle=handle,
                                                  backend=backend,
                                                  job_id=42,
                                                  job_name=None,
                                                  follow=False,
                                                  controller=False,
                                                  tail=1,
                                                  task=None)

    assert returncode == 0
    mock_stream.assert_not_called()
    backend.tail_managed_job_logs.assert_called_once()


@pytest.mark.parametrize(('local', 'follow', 'controller', 'tail', 'status'), [
    (True, False, False, 1, managed_job_state.ManagedJobStatus.RUNNING),
    (True, True, False, 1, managed_job_state.ManagedJobStatus.SUCCEEDED),
    (True, False, True, 1, managed_job_state.ManagedJobStatus.SUCCEEDED),
    (True, False, False, None, managed_job_state.ManagedJobStatus.SUCCEEDED),
    (False, False, False, 1, managed_job_state.ManagedJobStatus.SUCCEEDED),
])
def test_default_runner_non_snapshot_paths_keep_backend(
        local: bool, follow: bool, controller: bool, tail: Optional[int],
        status: managed_job_state.ManagedJobStatus):
    runner = jobs_core._DefaultManagedJobRunner()
    handle_type = (backends.LocalResourcesHandle
                   if local else backends.CloudVmRayResourceHandle)
    handle = object.__new__(handle_type)
    backend = mock.MagicMock(spec=backends.CloudVmRayBackend)
    backend.tail_managed_job_logs.return_value = 0

    with mock.patch.object(jobs_core.managed_job_state,
                           'get_status',
                           return_value=status), mock.patch.object(
                               jobs_core.managed_job_state,
                               'get_all_task_ids_names_statuses_logs',
                               return_value=[
                                   (0, '', status, '/tmp/run.log', None)
                               ]), mock.patch.object(
                                   jobs_core.managed_job_utils,
                                   'stream_logs_by_id') as mock_stream:
        returncode = runner.tail_managed_job_logs(handle=handle,
                                                  backend=backend,
                                                  job_id=42,
                                                  job_name=None,
                                                  follow=follow,
                                                  controller=controller,
                                                  tail=tail,
                                                  task=None)

    assert returncode == 0
    mock_stream.assert_not_called()
    backend.tail_managed_job_logs.assert_called_once_with(handle,
                                                          job_id=42,
                                                          job_name=None,
                                                          follow=follow,
                                                          controller=controller,
                                                          tail=tail,
                                                          tail_offset=None,
                                                          task=None)
