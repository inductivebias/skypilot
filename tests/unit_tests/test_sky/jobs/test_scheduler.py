"""Unit tests for sky.jobs.scheduler.kill_local_job_controllers.

Used during shutdown (lock-loss suicide and uvicorn graceful shutdown) to
prevent split-brain: this replica's controllers must not outlive the
moment another replica's refresh daemon could acquire the consolidation
lock. The helper must be best-effort — it runs on shutdown paths where
raising would either prevent SIGTERM or stall drain.
"""
import asyncio
import signal
from unittest import mock

import pytest

from sky.jobs import scheduler
from sky.jobs import state as managed_job_state


def _record(pid: int, started_at: float = 0.0):
    return managed_job_state.ControllerPidRecord(pid=pid, started_at=started_at)


class TestKillLocalConsolidationControllers:

    def test_no_pid_file_returns_zero(self):
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=[]):
            assert scheduler.kill_local_job_controllers() == 0

    def test_records_none_returns_zero(self):
        """Helper must tolerate the PID-file read failing (returns None)."""
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=None):
            assert scheduler.kill_local_job_controllers() == 0

    def test_signals_live_records(self):
        recs = [_record(101), _record(202), _record(303)]
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(scheduler.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(scheduler.os, 'kill') as kill_mock:
            n = scheduler.kill_local_job_controllers()
        assert n == 3
        kill_mock.assert_has_calls([
            mock.call(101, signal.SIGTERM),
            mock.call(202, signal.SIGTERM),
            mock.call(303, signal.SIGTERM)
        ],
                                   any_order=True)

    def test_skips_dead_records(self):
        """Stale entries (process exited or wrong started_at) are skipped —
        otherwise we'd SIGTERM unrelated PIDs that the OS reused."""
        recs = [_record(101), _record(202)]
        alive_lookup = {101: True, 202: False}
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(
                    scheduler.managed_job_utils,
                    'controller_process_alive',
                    side_effect=lambda r: alive_lookup[r.pid]), \
                mock.patch.object(scheduler.os, 'kill') as kill_mock:
            n = scheduler.kill_local_job_controllers()
        assert n == 1
        kill_mock.assert_called_once_with(101, signal.SIGTERM)

    def test_tolerates_process_lookup_error(self):
        """Race between alive-check and kill: the PID died in between.
        Not counted as signaled, but doesn't abort the loop."""
        recs = [_record(101), _record(202)]
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(scheduler.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(
                    scheduler.os, 'kill',
                    side_effect=[ProcessLookupError(), None]) as kill_mock:
            n = scheduler.kill_local_job_controllers()
        assert n == 1  # Only the second succeeded.
        assert kill_mock.call_count == 2

    def test_continues_on_oserror(self):
        """Per-PID OSError (e.g. EPERM) must not stop the rest."""
        recs = [_record(101), _record(202)]
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(scheduler.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(
                    scheduler.os, 'kill',
                    side_effect=[OSError('EPERM'), None]):
            n = scheduler.kill_local_job_controllers()
        assert n == 1

    def test_custom_signal(self):
        recs = [_record(101)]
        with mock.patch.object(scheduler,
                               'get_controller_process_records',
                               return_value=recs), \
                mock.patch.object(scheduler.managed_job_utils,
                                  'controller_process_alive',
                                  return_value=True), \
                mock.patch.object(scheduler.os, 'kill') as kill_mock:
            scheduler.kill_local_job_controllers(sig=signal.SIGKILL)
        kill_mock.assert_called_once_with(101, signal.SIGKILL)


@pytest.mark.asyncio
async def test_scheduled_launch_release_unblocks_next_job(monkeypatch):
    monkeypatch.setattr(scheduler.state, 'get_pool_from_job_id',
                        mock.MagicMock(return_value=None))
    monkeypatch.setattr(scheduler.file_content_utils, 'get_job_dag_content',
                        mock.MagicMock(return_value=None))
    monkeypatch.setattr(scheduler.state, 'scheduler_set_launching_async',
                        mock.AsyncMock())
    monkeypatch.setattr(scheduler.state, 'scheduler_set_alive_async',
                        mock.AsyncMock())
    monkeypatch.setattr(scheduler.controller_utils, 'LAUNCHES_PER_WORKER', 1)
    starting = set()
    lock = asyncio.Lock()
    signal_condition = asyncio.Condition(lock=lock)

    first = scheduler.scheduled_launch(1, starting, lock, signal_condition)
    release_first = await first.__aenter__()
    assert starting == {1}

    second = scheduler.scheduled_launch(2, starting, lock, signal_condition)
    enter_second = asyncio.create_task(second.__aenter__())
    await asyncio.sleep(0)
    assert not enter_second.done()

    await release_first()
    release_second = await asyncio.wait_for(enter_second, timeout=1)

    assert starting == {2}
    await release_second()
    await second.__aexit__(None, None, None)
    await first.__aexit__(None, None, None)
    assert starting == set()
