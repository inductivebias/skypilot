"""Tests for managed-job efficiency telemetry aggregation."""

import json
from urllib import parse as urlparse

import pytest

from sky.jobs import telemetry
from sky.jobs.server import server


def test_efficiency_metrics_route_registered():
    assert '/efficiency_metrics' in {
        getattr(route, 'path', None) for route in server.router.routes
    }


def test_job_query_uses_exact_cluster_and_required_metrics():
    query = telemetry._job_query('sky-job-123')

    assert 'label_skypilot_cluster_name="sky-job-123"' in query
    assert 'DCGM_FI_PROF_SM_ACTIVE' in query
    assert 'DCGM_FI_PROF_DRAM_ACTIVE' in query
    assert 'DCGM_FI_PROF_PCIE_TX_BYTES' in query
    assert 'DCGM_FI_PROF_NVLINK_RX_BYTES' in query
    assert 'flr_progress_steps_per_second' in query
    assert 'flr_progress_mfu_percent' in query


def test_job_query_rejects_promql_injection():
    with pytest.raises(ValueError, match='cluster name'):
        telemetry._job_query('job"} or up')


def test_gmp_job_query_filters_both_exporter_label_shapes():
    query = telemetry._gmp_job_query('sky-job.123')

    assert 'exported_pod=~"^sky-job\\\\.123-(head|worker.*)$"' in query
    assert 'pod=~"^sky-job\\\\.123-(head|worker.*)$"' in query
    assert 'kube_pod_labels' not in query
    assert 'DCGM_FI_PROF_SM_ACTIVE' in query
    assert 'flr_progress_steps_per_second' in query


def test_summarize_series_calculates_percentiles_and_latest_progress():
    hardware, progress = telemetry._summarize_series([
        {
            'metric': {
                'flr_metric': 'sm_active_percent'
            },
            'values': [[1, '0'], [2, '10'], [3, '20'], [4, '30'], [5, '40']],
        },
        {
            'metric': {
                'flr_metric': 'steps_per_second'
            },
            'values': [[1, '2.5'], [5, '3.5']],
        },
    ])

    assert hardware['sm_active_percent'] == {
        'mean': 20.0,
        'p10': 4.0,
        'p25': 10.0,
        'p50': 20.0,
        'p75': 30.0,
        'p99': 39.6,
    }
    assert progress == {'steps_per_second': 3.5}


@pytest.mark.asyncio
async def test_get_job_efficiency_metrics_queries_existing_prometheus(
        monkeypatch):
    monkeypatch.delenv('SKYPILOT_GMP_PROJECT_ID', raising=False)
    monkeypatch.setattr(telemetry.core, 'get_all_contexts',
                        lambda: ['in-cluster', 'ctx-a'])
    requests = []

    async def fake_request(**kwargs):
        requests.append(kwargs)
        return json.dumps({
            'status': 'success',
            'data': {
                'result': [{
                    'metric': {
                        'flr_metric': 'mfu_percent'
                    },
                    'values': [[200, '47.5']],
                }]
            },
        })

    monkeypatch.setattr(telemetry.metrics_utils,
                        'send_metrics_request_with_port_forward', fake_request)

    result = await telemetry.get_job_efficiency_metrics('sky-job-123',
                                                        start=100,
                                                        end=200)

    assert result == {
        'available': True,
        'hardware': {},
        'progress': {
            'mfu_percent': 47.5
        },
    }
    assert len(requests) == 1
    request = requests[0]
    assert request['context'] == 'ctx-a'
    assert request['service'] == 'skypilot-prometheus-server'
    parsed = urlparse.urlparse(request['endpoint_path'])
    assert parsed.path == '/api/v1/query_range'
    params = urlparse.parse_qs(parsed.query)
    assert params['start'] == ['100']
    assert params['end'] == ['200']
    assert params['step'] == ['60']


@pytest.mark.asyncio
async def test_get_job_efficiency_metrics_queries_configured_gmp(monkeypatch):
    monkeypatch.setenv('SKYPILOT_GMP_PROJECT_ID', 'test-project')
    requests = []

    async def fake_query(project, location, query, start, end, step):
        requests.append((project, location, query, start, end, step))
        return [{
            'metric': {
                'flr_metric': 'sm_active_percent'
            },
            'values': [[200, '75']],
        }]

    monkeypatch.setattr(telemetry, '_query_gmp', fake_query)
    monkeypatch.setattr(telemetry.core, 'get_all_contexts',
                        lambda: pytest.fail('context fallback must not run'))

    result = await telemetry.get_job_efficiency_metrics('sky-job-123',
                                                        start=100,
                                                        end=200)

    assert result['available'] is True
    assert result['hardware']['sm_active_percent']['p50'] == 75
    assert len(requests) == 1
    project, location, query, start, end, step = requests[0]
    assert project == 'test-project'
    assert location == 'global'
    assert 'exported_pod=~"^sky-job-123-(head|worker.*)$"' in query
    assert (start, end, step) == (100, 200, 60)


@pytest.mark.asyncio
async def test_get_job_efficiency_metrics_collection_failure_fails_open(
        monkeypatch):
    monkeypatch.delenv('SKYPILOT_GMP_PROJECT_ID', raising=False)
    monkeypatch.setattr(telemetry.core, 'get_all_contexts', lambda: ['ctx-a'])

    async def fail_request(**kwargs):
        del kwargs
        raise RuntimeError('unavailable')

    monkeypatch.setattr(telemetry.metrics_utils,
                        'send_metrics_request_with_port_forward', fail_request)

    result = await telemetry.get_job_efficiency_metrics('sky-job-123', 100, 200)

    assert result == {'available': False, 'hardware': {}, 'progress': {}}
