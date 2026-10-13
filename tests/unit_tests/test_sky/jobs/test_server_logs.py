"""Tests for managed-job log request routing."""
from typing import Optional
from unittest import mock

import fastapi
import pytest

from sky.jobs.server import server
from sky.server.requests import payloads


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('follow', 'tail', 'consolidation', 'uses_snapshot_stream'), [
        (False, 1, True, True),
        (False, None, True, False),
        (True, 1, True, False),
        (False, 1, False, False),
    ])
async def test_logs_bounded_consolidation_snapshot_uses_short_polling(
        follow: bool, tail: Optional[int], consolidation: bool,
        uses_snapshot_stream: bool):
    request = mock.MagicMock()
    request.state.request_id = 'request-id'
    request.state.auth_user = None
    body = payloads.JobsLogsBody(job_id=42, follow=follow, tail=tail)
    background_tasks = fastapi.BackgroundTasks()
    request_task = mock.MagicMock()
    request_task.request_id = 'request-id'
    request_task.log_path = '/tmp/request-id.log'
    coroutine_task = mock.MagicMock()
    snapshot_response = mock.sentinel.snapshot_response
    legacy_response = mock.sentinel.legacy_response

    with mock.patch.object(
            server.managed_jobs_utils,
            'is_consolidation_mode',
            return_value=consolidation), mock.patch.object(
                server.executor,
                'check_request_thread_executor_available'), mock.patch.object(
                    server.executor,
                    'prepare_request_async',
                    new_callable=mock.AsyncMock,
                    return_value=request_task), mock.patch.object(
                        server.executor,
                        'execute_request_in_coroutine',
                        return_value=coroutine_task), mock.patch.object(
                            server.stream_utils,
                            'stream_response',
                            return_value=snapshot_response
                        ) as snapshot_stream, mock.patch.object(
                            server.stream_utils,
                            'stream_response_for_long_request',
                            return_value=legacy_response) as legacy_stream:
        response = await server.logs(request, background_tasks, body)

    expected_response = (snapshot_response
                         if uses_snapshot_stream else legacy_response)
    assert response is expected_response
    assert snapshot_stream.call_count == int(uses_snapshot_stream)
    assert legacy_stream.call_count == int(not uses_snapshot_stream)
