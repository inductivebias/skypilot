"""Read-only inventory of administrator-configured direct cloud VMs."""

import datetime
from typing import Any, Dict, List

from sky import sky_logging
from sky.adaptors import gcp

logger = sky_logging.init_logger(__name__)

_MANAGED_INSTANCE_FILTER = 'labels.managed-by = gpu-script'
_INSTANCE_FIELDS = ('nextPageToken,items/*/instances('
                    'name,status,zone,machineType,labels,guestAccelerators)')


def _self_link_name(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    return value.rsplit('/', 1)[-1]


def _parse_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _normalize_gcp_instance(project: str,
                            instance: Dict[str, Any]) -> Dict[str, Any]:
    labels = instance.get('labels') or {}
    accelerators = instance.get('guestAccelerators') or []
    accelerator_count = sum(
        _parse_nonnegative_int(accelerator.get('acceleratorCount'))
        for accelerator in accelerators)
    gpu_count = _parse_nonnegative_int(labels.get('gpu-count'))
    if gpu_count == 0:
        gpu_count = accelerator_count
    gpu_type = labels.get('gpu-type') or ''
    if not gpu_type and accelerators:
        gpu_type = _self_link_name(accelerators[0].get('acceleratorType'))

    return {
        'cloud': 'GCP',
        'project': project,
        'name': instance.get('name') or '',
        'owner': labels.get('owner') or '',
        'status': instance.get('status') or 'UNKNOWN',
        'machine_type': _self_link_name(instance.get('machineType')),
        'zone': _self_link_name(instance.get('zone')),
        'gpu_type': gpu_type,
        'gpu_count': gpu_count,
    }


def _list_gcp_instances(project: str) -> List[Dict[str, Any]]:
    compute = gcp.build('compute', 'v1', cache_discovery=False)
    request = compute.instances().aggregatedList(
        project=project,
        filter=_MANAGED_INSTANCE_FILTER,
        fields=_INSTANCE_FIELDS,
    )
    instances: List[Dict[str, Any]] = []
    while request is not None:
        response = request.execute(num_retries=3)
        for scoped_list in response.get('items', {}).values():
            for instance in scoped_list.get('instances', []):
                instances.append(_normalize_gcp_instance(project, instance))
        request = compute.instances().aggregatedList_next(
            previous_request=request, previous_response=response)
    return instances


def get_direct_vm_inventory(projects: List[str]) -> Dict[str, Any]:
    """Return managed direct VMs for the fixed GCP project allowlist."""
    instances: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for project in projects:
        try:
            instances.extend(_list_gcp_instances(project))
        except Exception as e:  # pylint: disable=broad-except
            logger.warning('Failed to list direct VMs in GCP project %s: %s',
                           project, e)
            errors.append({
                'project': project,
                'message': f'{type(e).__name__}: {e}',
            })
    instances.sort(key=lambda item: (item['project'], item['name']))
    return {
        'configured': bool(projects),
        'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'instances': instances,
        'errors': errors,
    }
