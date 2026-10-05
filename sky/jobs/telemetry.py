"""Read-only Prometheus summaries for managed-job efficiency."""

import asyncio
import json
import math
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib import parse as urlparse

from sky import core
from sky import sky_logging
from sky.metrics import utils as metrics_utils

logger = sky_logging.init_logger(__name__)

_PROMETHEUS_NAMESPACE = 'skypilot'
_PROMETHEUS_SERVICE = 'skypilot-prometheus-server'
_PROMETHEUS_PORT = 80
_QUERY_TIMEOUT_SECONDS = 30
_MAX_POINTS_PER_SERIES = 1000
_CLUSTER_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_.:/-]{1,253}$')
_PERCENTILES = (10, 25, 50, 75, 99)

_HARDWARE_EXPRESSIONS = {
    'sm_active_percent': '100 * DCGM_FI_PROF_SM_ACTIVE',
    'dram_active_percent': '100 * DCGM_FI_PROF_DRAM_ACTIVE',
    'vram_used_percent': ('100 * DCGM_FI_DEV_FB_USED / '
                          '(DCGM_FI_DEV_FB_USED + DCGM_FI_DEV_FB_FREE)'),
    'pcie_tx_bytes_per_second': 'DCGM_FI_PROF_PCIE_TX_BYTES',
    'pcie_rx_bytes_per_second': 'DCGM_FI_PROF_PCIE_RX_BYTES',
    'nvlink_tx_bytes_per_second': 'DCGM_FI_PROF_NVLINK_TX_BYTES',
    'nvlink_rx_bytes_per_second': 'DCGM_FI_PROF_NVLINK_RX_BYTES',
}
_PROGRESS_METRICS = {
    'steps_per_second': 'flr_progress_steps_per_second',
    'step': 'flr_progress_step',
    'total_steps': 'flr_progress_total_steps',
    'flops_per_second': 'flr_progress_flops_per_second',
    'mfu_percent': 'flr_progress_mfu_percent',
}


def _job_query(cluster_name_on_cloud: str) -> str:
    """Build one bounded PromQL query for all efficiency series."""
    if _CLUSTER_NAME_PATTERN.fullmatch(cluster_name_on_cloud) is None:
        raise ValueError('Invalid managed-job cluster name')
    cluster = json.dumps(cluster_name_on_cloud)
    pods = ('group by (pod, namespace, label_skypilot_cluster_name) '
            f'(kube_pod_labels{{label_skypilot_cluster_name={cluster}}})')
    expressions = []
    for name, expression in _HARDWARE_EXPRESSIONS.items():
        joined = (f'({expression}) * on (pod, namespace) '
                  f'group_left(label_skypilot_cluster_name) ({pods})')
        expressions.append(f'label_replace(({joined}), "flr_metric", "{name}", '
                           '"__name__", ".*")')
    for name, metric in _PROGRESS_METRICS.items():
        joined = (f'{metric} * on (pod, namespace) '
                  f'group_left(label_skypilot_cluster_name) ({pods})')
        expressions.append(f'label_replace(({joined}), "flr_metric", "{name}", '
                           '"__name__", ".*")')
    return ' or '.join(expressions)


def _percentile(values: List[float], percentile: int) -> float:
    """Return a linearly interpolated percentile for finite values."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _summarize_series(
    series: List[dict],
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, float]]:
    hardware_values: Dict[str, List[float]] = {
        name: [] for name in _HARDWARE_EXPRESSIONS
    }
    latest_progress: Dict[str, Tuple[float, float]] = {}
    for item in series:
        metric_name = item.get('metric', {}).get('flr_metric')
        if metric_name is None:
            continue
        for raw_timestamp, raw_value in item.get('values', []):
            try:
                timestamp = float(raw_timestamp)
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(timestamp) or not math.isfinite(value):
                continue
            if metric_name in hardware_values:
                hardware_values[metric_name].append(value)
            elif metric_name in _PROGRESS_METRICS:
                previous = latest_progress.get(metric_name)
                if previous is None or timestamp > previous[0]:
                    latest_progress[metric_name] = (timestamp, value)

    hardware = {}
    for name, values in hardware_values.items():
        if values:
            hardware[name] = {
                f'p{percentile}': _percentile(values, percentile)
                for percentile in _PERCENTILES
            }
    progress = {
        name: latest_progress[name][1]
        for name in _PROGRESS_METRICS
        if name in latest_progress
    }
    return hardware, progress


async def _query_context(context: str, query: str, start: float, end: float,
                         step: int) -> List[dict]:
    params = urlparse.urlencode({
        'query': query,
        'start': start,
        'end': end,
        'step': step,
    })
    response = await metrics_utils.send_metrics_request_with_port_forward(
        context=context,
        namespace=_PROMETHEUS_NAMESPACE,
        service=_PROMETHEUS_SERVICE,
        service_port=_PROMETHEUS_PORT,
        endpoint_path=f'/api/v1/query_range?{params}',
        timeout=_QUERY_TIMEOUT_SECONDS,
        route='job-efficiency')
    payload = json.loads(response)
    if payload.get('status') != 'success':
        raise RuntimeError('Prometheus query failed')
    result = payload.get('data', {}).get('result', [])
    return result if isinstance(result, list) else []


async def get_job_efficiency_metrics(cluster_name_on_cloud: str,
                                     start: float,
                                     end: Optional[float] = None) -> dict:
    """Aggregate hardware percentiles and latest workload progress.

    Collection failures and unsupported metrics fail open with an empty
    summary. This endpoint is decoration and must not affect job execution.
    """
    if end is None:
        end = time.time()
    if not math.isfinite(start) or not math.isfinite(end) or start <= 0:
        raise ValueError('Invalid metrics time range')
    if end < start:
        raise ValueError('Metrics end must not precede start')
    query = _job_query(cluster_name_on_cloud)
    duration = max(1, end - start)
    step = max(60, math.ceil(duration / _MAX_POINTS_PER_SERIES))
    contexts = [
        context for context in core.get_all_contexts()
        if context != 'in-cluster'
    ]
    tasks = [
        asyncio.create_task(
            asyncio.wait_for(_query_context(context, query, start, end, step),
                             timeout=_QUERY_TIMEOUT_SECONDS))
        for context in contexts
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    series = []
    for context, result in zip(contexts, results):
        if isinstance(result, Exception):
            logger.warning('Failed to query job efficiency metrics for '
                           f'context {context}: {result}')
            continue
        series.extend(result)
    hardware, progress = _summarize_series(series)
    return {
        'available': bool(hardware or progress),
        'hardware': hardware,
        'progress': progress,
    }
