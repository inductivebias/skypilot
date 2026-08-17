"""Tests for Kubernetes adaptor."""

import concurrent.futures
import gc
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sky.adaptors import kubernetes
from sky.utils import annotations


def _clear_refresh_interval_cache():
    """Clear the refresh interval env parse cache so tests can change the env."""
    kubernetes._get_kubeconfig_refresh_interval_seconds.cache_clear()  # pylint: disable=protected-access


@pytest.mark.parametrize(
    'ctor_name, api_func',
    [
        ('CoreV1Api', kubernetes.core_api),
        ('StorageV1Api', kubernetes.storage_api),
        ('RbacAuthorizationV1Api', kubernetes.auth_api),
        ('NetworkingV1Api', kubernetes.networking_api),
        ('CustomObjectsApi', kubernetes.custom_objects_api),
        ('AppsV1Api', kubernetes.apps_api),
        ('BatchV1Api', kubernetes.batch_api),
        ('CustomObjectsApi', kubernetes.custom_resources_api),
    ],
)
def test_typed_clients_cleanup(monkeypatch, ctor_name, api_func):
    """Verify typed client api_client.close() is called on GC."""
    api_client_mock = MagicMock()
    monkeypatch.setattr(kubernetes,
                        '_get_api_client',
                        lambda context=None: api_client_mock)
    monkeypatch.setattr(
        kubernetes.kubernetes.client,
        ctor_name,
        lambda api_client=None: SimpleNamespace(api_client=api_client),
    )
    obj = api_func()
    del obj
    annotations.clear_request_level_cache()
    gc.collect()

    assert api_client_mock.close.call_count == 1


def test_api_client_cleanup(monkeypatch):
    """Verify ApiClient.close() is called on GC."""
    instances = []

    class FakeApiClient:

        def __init__(self):
            self.close = MagicMock()
            instances.append(self)

    # Mock _get_api_client to return a FakeApiClient instance
    monkeypatch.setattr(kubernetes,
                        '_get_api_client',
                        lambda context=None: FakeApiClient())
    # Also mock the ApiClient class so isinstance checks work
    monkeypatch.setattr(kubernetes.kubernetes.client, 'ApiClient',
                        FakeApiClient)

    client = kubernetes.api_client()
    del client
    annotations.clear_request_level_cache()
    gc.collect()

    assert len(instances) == 1
    assert instances[0].close.call_count == 1


def test_watch_cleanup(monkeypatch):
    """Verify Watch.stop() and underlying api_client.close() are called."""
    api_client_mock = MagicMock()
    monkeypatch.setattr(kubernetes,
                        '_get_api_client',
                        lambda context=None: api_client_mock)

    class FakeWatch:

        def __init__(self, return_type=None):
            self._raw_return_type = return_type

    monkeypatch.setattr(kubernetes.kubernetes.watch, 'Watch', FakeWatch)

    w = kubernetes.watch()
    # Keep a handle to the underlying watch object so we can assert its
    # _api_client.close() was called on GC.
    underlying = w._client
    del w
    annotations.clear_request_level_cache()
    gc.collect()

    assert underlying._api_client.close.call_count == 1


def test_kubeconfig_refresh_interval_refreshes_client(monkeypatch):
    """When SKYPILOT_KUBECONFIG_REFRESH_INTERVAL_SECONDS is set and interval
    has elapsed, the next API call refreshes the client and closes the old one.
    """
    api_clients = []

    def track_get_api_client(context=None):
        mock_client = MagicMock()
        api_clients.append(mock_client)
        return mock_client

    def make_mock_core_api(api_client=None):
        # Explicit client object so getattr(self._client, 'list_namespaced_pod') always works.
        client = SimpleNamespace(api_client=api_client)
        client.list_namespaced_pod = MagicMock(return_value=MagicMock())
        return client

    monkeypatch.setattr(kubernetes, '_get_api_client', track_get_api_client)
    monkeypatch.setattr(kubernetes.kubernetes.client, 'CoreV1Api',
                        make_mock_core_api)

    monkeypatch.setenv(kubernetes.KUBECONFIG_REFRESH_INTERVAL_ENV_VAR, '1')
    _clear_refresh_interval_cache()

    # Time 0 when wrapper is created (mark refreshed), then 10 when we call
    # method so interval has elapsed.
    time_values = iter([0.0, 10.0, 10.0, 10.0])
    monkeypatch.setattr(time, 'time', lambda: next(time_values, 10.0))

    annotations.clear_request_level_cache()
    api = kubernetes.core_api()
    assert len(api_clients) == 1

    api.list_namespaced_pod(namespace='default')

    assert len(api_clients) == 2, 'Refresh should have created a second client'
    assert api_clients[0].close.call_count == 1, (
        'Old client should be closed when refresh runs')


def test_kubeconfig_refresh_interval_no_refresh_when_interval_not_elapsed(
        monkeypatch):
    """When interval has not elapsed, no refresh runs (single client)."""
    api_clients = []

    def track_get_api_client(context=None):
        mock_client = MagicMock()
        api_clients.append(mock_client)
        return mock_client

    def make_mock_core_api(api_client=None):
        client = SimpleNamespace(api_client=api_client)
        client.list_namespaced_pod = MagicMock(return_value=MagicMock())
        return client

    monkeypatch.setattr(kubernetes, '_get_api_client', track_get_api_client)
    monkeypatch.setattr(kubernetes.kubernetes.client, 'CoreV1Api',
                        make_mock_core_api)

    monkeypatch.setenv(kubernetes.KUBECONFIG_REFRESH_INTERVAL_ENV_VAR, '10')
    _clear_refresh_interval_cache()

    # Time 0 at creation, then 5 when we call method (5 < 10, no refresh).
    time_values = iter([0.0, 5.0, 5.0])
    monkeypatch.setattr(time, 'time', lambda: next(time_values, 5.0))

    annotations.clear_request_level_cache()
    api = kubernetes.core_api()
    assert len(api_clients) == 1

    api.list_namespaced_pod(namespace='default')

    assert len(api_clients) == 1
    assert api_clients[0].close.call_count == 0


def test_kubeconfig_refresh_interval_disabled_when_unset(monkeypatch):
    """When env var is unset, interval refresh is disabled."""
    monkeypatch.delenv(kubernetes.KUBECONFIG_REFRESH_INTERVAL_ENV_VAR,
                       raising=False)
    _clear_refresh_interval_cache()

    interval = kubernetes._get_kubeconfig_refresh_interval_seconds()  # pylint: disable=protected-access
    assert interval == 0.0


def test_kubeconfig_refresh_interval_invalid_value_disables_refresh(
        monkeypatch):
    """Invalid env value disables refresh and returns 0."""
    monkeypatch.setenv(kubernetes.KUBECONFIG_REFRESH_INTERVAL_ENV_VAR,
                       'not-a-number')
    _clear_refresh_interval_cache()

    interval = kubernetes._get_kubeconfig_refresh_interval_seconds()  # pylint: disable=protected-access
    assert interval == 0.0


def _api_exception(status):
    return kubernetes.kubernetes.client.exceptions.ApiException(status=status)


def _fake_core_client(method):
    api_client = MagicMock()
    return SimpleNamespace(api_client=api_client, list_namespaced_pod=method)


def test_client_401_refreshes_and_retries_once(caplog):
    first_method = MagicMock(side_effect=_api_exception(401))
    second_method = MagicMock(return_value='pods')
    first_client = _fake_core_client(first_method)
    second_client = _fake_core_client(second_method)
    getter = MagicMock(return_value=second_client)
    wrapper = kubernetes.RetryableClientWrapper(first_client, getter,
                                                ('ssh-deploy-lambda-2',), {})

    assert wrapper.list_namespaced_pod(namespace='default') == 'pods'

    getter.assert_called_once_with('ssh-deploy-lambda-2')
    first_method.assert_called_once_with(namespace='default')
    second_method.assert_called_once_with(namespace='default')
    first_client.api_client.close.assert_called_once_with()
    assert 'kubernetes.client.exceptions.ApiException: (401)' in caplog.text
    assert 'retry_succeeded=true' in caplog.text


def test_client_second_401_propagates_without_loop(caplog):
    first_error = _api_exception(401)
    second_error = _api_exception(401)
    first_client = _fake_core_client(MagicMock(side_effect=first_error))
    second_client = _fake_core_client(MagicMock(side_effect=second_error))
    getter = MagicMock(return_value=second_client)
    wrapper = kubernetes.RetryableClientWrapper(first_client, getter,
                                                ('ssh-deploy-lambda-2',), {})

    with pytest.raises(type(second_error)) as raised:
        wrapper.list_namespaced_pod(namespace='default')

    assert raised.value is second_error
    getter.assert_called_once_with('ssh-deploy-lambda-2')
    assert second_client.list_namespaced_pod.call_count == 1
    assert 'kubernetes.client.exceptions.ApiException: (401)' in caplog.text
    assert 'retry_succeeded=false' in caplog.text


def test_client_non_401_does_not_refresh():
    forbidden = _api_exception(403)
    first_client = _fake_core_client(MagicMock(side_effect=forbidden))
    getter = MagicMock()
    wrapper = kubernetes.RetryableClientWrapper(first_client, getter,
                                                ('ssh-deploy-lambda-2',), {})

    with pytest.raises(type(forbidden)) as raised:
        wrapper.list_namespaced_pod(namespace='default')

    assert raised.value is forbidden
    getter.assert_not_called()
    first_client.api_client.close.assert_not_called()


def test_concurrent_401_refreshes_one_client_generation():
    workers = 8
    barrier = threading.Barrier(workers)

    def reject_with_401(*_args, **_kwargs):
        barrier.wait(timeout=5)
        raise _api_exception(401)

    first_client = _fake_core_client(reject_with_401)
    second_method = MagicMock(return_value='pods')
    second_client = _fake_core_client(second_method)
    getter = MagicMock(return_value=second_client)
    wrapper = kubernetes.RetryableClientWrapper(first_client, getter,
                                                ('ssh-deploy-lambda-2',), {})

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(
            executor.map(
                lambda _: wrapper.list_namespaced_pod(namespace='default'),
                range(workers)))

    assert results == ['pods'] * workers
    getter.assert_called_once_with('ssh-deploy-lambda-2')
    assert second_method.call_count == workers
    first_client.api_client.close.assert_called_once_with()


def test_401_refresh_preserves_context_and_closes_old_client():
    first_client = _fake_core_client(MagicMock(side_effect=_api_exception(401)))
    second_client = _fake_core_client(MagicMock(return_value='pods'))
    calls = []

    def getter(*args, **kwargs):
        calls.append((args, kwargs))
        return second_client

    wrapper = kubernetes.RetryableClientWrapper(first_client, getter,
                                                ('ssh-deploy-lambda-2',),
                                                {'request_timeout': 30})

    result = wrapper.list_namespaced_pod(namespace='training')

    assert result == 'pods'
    assert calls == [(('ssh-deploy-lambda-2',), {'request_timeout': 30})]
    first_client.api_client.close.assert_called_once_with()


def test_interval_refresh_reloads_replaced_exec_certificate(
        monkeypatch, tmp_path):
    cert_source = tmp_path / 'client.crt'
    key_source = tmp_path / 'client.key'
    invocation_log = tmp_path / 'invocations'
    credential_script = tmp_path / 'credential.py'
    kubeconfig = tmp_path / 'config'

    cert_source.write_text('certificate-a', encoding='utf-8')
    key_source.write_text('private-key-a', encoding='utf-8')
    credential_script.write_text("""#!/usr/bin/env python3
import json
from pathlib import Path
import sys

certificate = Path(sys.argv[1]).read_text(encoding='utf-8')
private_key = Path(sys.argv[2]).read_text(encoding='utf-8')
log = Path(sys.argv[3])
log.write_text(log.read_text(encoding='utf-8') + 'called\\n'
               if log.exists() else 'called\\n', encoding='utf-8')
print(json.dumps({
    'apiVersion': 'client.authentication.k8s.io/v1beta1',
    'kind': 'ExecCredential',
    'status': {
        'clientCertificateData': certificate,
        'clientKeyData': private_key,
    },
}))
""",
                                 encoding='utf-8')
    credential_script.chmod(0o755)
    kubeconfig.write_text(f"""apiVersion: v1
kind: Config
clusters:
- cluster:
    insecure-skip-tls-verify: true
    server: https://127.0.0.1:6443
  name: cluster
contexts:
- context:
    cluster: cluster
    user: exec-user
  name: ssh-replaced-slot
current-context: ssh-replaced-slot
users:
- name: exec-user
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: {sys.executable}
      args:
      - {credential_script}
      - {cert_source}
      - {key_source}
      - {invocation_log}
""",
                          encoding='utf-8')

    class ProbeClient:

        def __init__(self, api_client):
            self.api_client = api_client

        def probe(self):
            return 'ok'

    def getter(context):
        api_client = kubernetes._get_api_client(context)  # pylint: disable=protected-access
        return ProbeClient(api_client)

    monkeypatch.setenv('KUBECONFIG', str(kubeconfig))
    monkeypatch.setenv(kubernetes.KUBECONFIG_REFRESH_INTERVAL_ENV_VAR, '1')
    _clear_refresh_interval_cache()
    time_values = iter([0.0, 10.0, 10.0, 10.0])
    monkeypatch.setattr(time, 'time', lambda: next(time_values, 10.0))

    first_client = getter('ssh-replaced-slot')
    wrapper = kubernetes.RetryableClientWrapper(first_client, getter,
                                                ('ssh-replaced-slot',), {})
    first_certificate_path = Path(
        first_client.api_client.configuration.cert_file)
    first_fingerprint = hashlib.sha256(
        first_certificate_path.read_bytes()).hexdigest()

    cert_source.write_text('certificate-b', encoding='utf-8')
    key_source.write_text('private-key-b', encoding='utf-8')
    assert wrapper.probe() == 'ok'

    refreshed_client = wrapper._client  # pylint: disable=protected-access
    second_certificate_path = Path(
        refreshed_client.api_client.configuration.cert_file)
    second_fingerprint = hashlib.sha256(
        second_certificate_path.read_bytes()).hexdigest()
    assert first_fingerprint != second_fingerprint
    assert second_certificate_path.read_text(
        encoding='utf-8') == 'certificate-b'
    assert invocation_log.read_text(encoding='utf-8').splitlines() == [
        'called', 'called'
    ]


def _create_test_kubeconfig(num_contexts):
    """Create a temporary kubeconfig with multiple contexts."""
    clusters = '\n'.join(f'- cluster:\n'
                         f'    server: https://cluster-{i}.example.com\n'
                         f'  name: cluster-{i}' for i in range(num_contexts))

    contexts = '\n'.join(f'- context:\n'
                         f'    cluster: cluster-{i}\n'
                         f'    user: user-{i}\n'
                         f'  name: context-{i}' for i in range(num_contexts))

    users = '\n'.join(f'- name: user-{i}\n'
                      f'  user: {{}}' for i in range(num_contexts))

    kubeconfig = (f'apiVersion: v1\n'
                  f'kind: Config\n'
                  f'clusters:\n'
                  f'{clusters}\n'
                  f'contexts:\n'
                  f'{contexts}\n'
                  f'current-context: context-0\n'
                  f'users:\n'
                  f'{users}\n')
    fd, path = tempfile.mkstemp(suffix='.yaml')
    os.write(fd, kubeconfig.encode())
    os.close(fd)
    return path


@pytest.mark.parametrize(
    'api_func',
    [
        kubernetes.core_api,
        kubernetes.storage_api,
        kubernetes.auth_api,
        kubernetes.networking_api,
        kubernetes.custom_objects_api,
        kubernetes.apps_api,
        kubernetes.batch_api,
        kubernetes.custom_resources_api,
    ],
)
def test_concurrent_context_isolation(monkeypatch, api_func):
    """Verify concurrent API calls with different contexts get isolated clients.

    This is a regression test for a race condition where the old implementation
    would:
    1. Call _load_config(context) which modified global
       kubernetes.client.configuration
    2. Create an API client that used that global config

    If two threads interleaved:
    - Thread A: _load_config('context-a')
    - Thread B: _load_config('context-b')  # overwrites global config
    - Thread A: CoreV1Api()  # incorrectly uses context-b!

    The fix uses new_client_from_config() which returns an ApiClient with an
    isolated Configuration object, avoiding global state.
    """
    num_contexts = 10
    iterations = 5
    contexts = [f'context-{i}' for i in range(num_contexts)]
    expected_hosts = {
        f'context-{i}': f'https://cluster-{i}.example.com'
        for i in range(num_contexts)
    }

    config_file = _create_test_kubeconfig(num_contexts)
    try:
        monkeypatch.setattr(kubernetes, '_get_config_file', lambda: config_file)

        original_get_api_client = kubernetes._get_api_client  # pylint: disable=protected-access

        def slow_get_api_client(context=None):
            assert (context is not None)
            client = original_get_api_client(context)
            time.sleep(0.001)
            return client

        monkeypatch.setattr(kubernetes, '_get_api_client', slow_get_api_client)

        for iteration in range(iterations):
            annotations.clear_request_level_cache()

            def get_api_for_context(ctx):
                api = api_func(ctx)
                # pylint: disable=protected-access
                return (ctx, api._client.api_client.configuration.host)

            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=num_contexts) as executor:
                futures = [
                    executor.submit(get_api_for_context, ctx)
                    for ctx in contexts
                ]
                results = [f.result() for f in futures]

            for requested_ctx, actual_host in results:
                expected_host = expected_hosts[requested_ctx]
                assert actual_host == expected_host, (
                    f'Iteration {iteration}: Host mismatch for '
                    f'{requested_ctx}: expected {expected_host}, '
                    f'got {actual_host}. Race condition detected.')
    finally:
        os.unlink(config_file)
