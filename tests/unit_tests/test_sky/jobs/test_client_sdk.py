"""Unit tests for managed jobs client SDK queue helpers."""
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from unittest import mock

import pytest

import sky
from sky import exceptions
from sky.jobs import constants as managed_job_constants
from sky.jobs.client import sdk as jobs_sdk
from sky.jobs.client import sdk_async as jobs_sdk_async
from sky.server import common as server_common
from sky.server import constants as server_constants
from sky.server import rest


@pytest.mark.parametrize('failure', [
    'health', 'workdir', 'mount', 'credentials', 'expired_budget', 'lost_ack',
    'missing_id', 'success'
])
def test_launch_classifies_the_submission_boundary(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str) -> None:
    """Distinguish preparation from delivery with the real SDK and HTTP peer."""
    posts = []

    class Handler(BaseHTTPRequestHandler):
        """Expose failures before submission and after accepting a launch."""

        def do_GET(self) -> None:  # pylint: disable=invalid-name
            assert self.path == '/api/health'
            self.send_response(503 if failure == 'health' else 200)
            self.send_header(server_constants.API_VERSION_HEADER,
                             str(server_constants.API_VERSION))
            self.send_header(server_constants.VERSION_HEADER, sky.__version__)
            self.end_headers()
            self.wfile.write(
                json.dumps({
                    'status': 'healthy',
                    'api_version': server_constants.API_VERSION,
                    'version': sky.__version__,
                    'commit': sky.__commit__,
                }).encode())

        def do_POST(self) -> None:  # pylint: disable=invalid-name
            self.rfile.read(int(self.headers['Content-Length']))
            if self.path == '/validate':
                self.send_response(200)
                self.end_headers()
                return
            posts.append(self.path)
            if failure == 'lost_ack':
                self.close_connection = True
                return
            self.send_response(200)
            if failure == 'success':
                self.send_header('X-Skypilot-Request-ID', 'accepted-request')
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    monkeypatch.setenv('SKYPILOT_DISABLE_USAGE_COLLECTION', '1')
    monkeypatch.setenv('SKYPILOT_SERVICE_ACCOUNT_TOKEN', 'sky_test')
    with ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        monkeypatch.setenv('SKYPILOT_API_SERVER_ENDPOINT',
                           f'http://127.0.0.1:{server.server_port}')
        server_common.get_server_url.cache_clear()
        task = sky.Task(run='true')
        if failure == 'workdir':
            task.workdir = str(tmp_path / 'missing')
        elif failure == 'mount':
            task.set_file_mounts({'/tmp/input': str(tmp_path / 'missing')})
        try:
            with rest.request_timeout(1):
                if failure == 'credentials':
                    monkeypatch.setenv('SKYPILOT_SERVICE_ACCOUNT_TOKEN',
                                       'invalid')
                    with pytest.raises(ValueError, match='token format'):
                        server_common.submit_authenticated_request(
                            '/jobs/launch', json={})
                elif failure == 'expired_budget':
                    expired = time.monotonic() + 2
                    monkeypatch.setattr(time, 'monotonic', lambda: expired)
                    with pytest.raises(exceptions.RequestDeadlineExceededError):
                        server_common.submit_authenticated_request(
                            '/jobs/launch', json={})
                elif failure == 'success':
                    assert jobs_sdk.launch(task) == 'accepted-request'
                else:
                    expected = (exceptions.RequestSubmissionUncertainError
                                if failure in ('lost_ack', 'missing_id') else
                                exceptions.ServerTemporarilyUnavailableError
                                if failure == 'health' else ValueError)
                    with pytest.raises(expected):
                        jobs_sdk.launch(task)
            assert posts == (['/jobs/launch'] if failure in ('lost_ack',
                                                             'missing_id',
                                                             'success') else [])
        finally:
            server.shutdown()
            worker.join(timeout=2)
            server_common.get_server_url.cache_clear()


def _unwrap(fn):
    """Strip all functools.wraps-based decorators."""
    while hasattr(fn, '__wrapped__'):
        fn = fn.__wrapped__
    return fn


def _call_raw_queue_v2(**kwargs):
    """Call queue_v2 (decorators stripped) and return the request JSON body."""
    raw_queue_v2 = _unwrap(jobs_sdk.queue_v2)
    with mock.patch.object(jobs_sdk.versions,
                           'get_remote_api_version',
                           return_value=999), \
         mock.patch.object(jobs_sdk.server_common,
                           'make_authenticated_request',
                           return_value='response') as mock_request, \
         mock.patch.object(jobs_sdk.server_common,
                           'get_request_id',
                           return_value='request-id'):
        raw_queue_v2(**kwargs)
    _, request_kwargs = mock_request.call_args
    return request_kwargs['json']


def test_queue_v2_defaults_to_lightweight_fields():
    # A high remote API version avoids the version-based field stripping so we
    # can assert the full default field set is sent.
    body = _call_raw_queue_v2(refresh=False)
    assert body['fields'] == list(
        managed_job_constants.DEFAULT_MANAGED_JOB_FIELDS)
    # The lightweight default must not pull heavy fields.
    assert 'node_names' not in body['fields']
    assert 'metadata' not in body['fields']


def test_queue_v2_fields_none_requests_all_fields():
    # fields=None is the explicit "give me everything" escape hatch.
    body = _call_raw_queue_v2(refresh=False, fields=None)
    assert body['fields'] is None


def test_queue_version_2_dispatches_to_queue_v2():
    raw_queue = jobs_sdk.queue.__wrapped__.__wrapped__

    with mock.patch.object(jobs_sdk, 'queue_v2',
                           return_value='request-id-v2') as mock_queue_v2:
        result = raw_queue(refresh=True,
                           skip_finished=True,
                           all_users=True,
                           job_ids=[1, 2],
                           version=2)

    assert result == 'request-id-v2'
    mock_queue_v2.assert_called_once_with(refresh=True,
                                          skip_finished=True,
                                          all_users=True,
                                          job_ids=[1, 2])


def test_queue_version_1_warns_and_uses_legacy_endpoint():
    raw_queue = jobs_sdk.queue.__wrapped__.__wrapped__

    with mock.patch.object(jobs_sdk.server_common,
                           'make_authenticated_request',
                           return_value='response') as mock_request, \
         mock.patch.object(jobs_sdk.server_common,
                           'get_request_id',
                           return_value='request-id-v1') as mock_get_request_id, \
         mock.patch.object(jobs_sdk.logger, 'warning') as mock_warning:
        result = raw_queue(refresh=False,
                           skip_finished=True,
                           all_users=False,
                           job_ids=[3],
                           version=1)

    assert result == 'request-id-v1'
    mock_warning.assert_called_once()
    assert 'is deprecated and will be removed in v0.13' in mock_warning.call_args.args[
        0]
    mock_request.assert_called_once()
    args, kwargs = mock_request.call_args
    assert args == ('POST', '/jobs/queue')
    assert kwargs['json']['refresh'] is False
    assert kwargs['json']['skip_finished'] is True
    assert kwargs['json']['all_users'] is False
    assert kwargs['json']['job_ids'] == [3]
    mock_get_request_id.assert_called_once_with(response='response')


def test_queue_invalid_version_raises():
    raw_queue = jobs_sdk.queue.__wrapped__.__wrapped__

    with pytest.raises(ValueError, match='Must be 1 or 2'):
        raw_queue(refresh=False, version=3)


@pytest.mark.asyncio
async def test_async_queue_passes_version_through():

    async def mock_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    with mock.patch('sky.jobs.client.sdk_async.asyncio.to_thread',
                    side_effect=mock_to_thread), \
         mock.patch.object(jobs_sdk, 'queue',
                           return_value='request-id') as mock_queue, \
         mock.patch.object(jobs_sdk_async.sdk_async,
                           '_stream_and_get',
                           new=mock.AsyncMock(
                               return_value='queue-result')) as mock_stream:
        result = await jobs_sdk_async.queue(refresh=True,
                                            skip_finished=False,
                                            all_users=True,
                                            job_ids=[9],
                                            version=2)

    assert result == 'queue-result'
    mock_queue.assert_called_once_with(True, False, True, [9], 2)
    mock_stream.assert_called_once_with(
        'request-id', jobs_sdk_async.sdk_async.DEFAULT_STREAM_CONFIG)
