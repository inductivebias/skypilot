"""Tests for direct VM inventory collection."""

from unittest import mock

from sky.server import direct_vm_inventory


def test_get_direct_vm_inventory_normalizes_and_sorts_gcp_instances():
    first_page = {
        'items': {
            'zones/us-central1-b': {
                'instances': [{
                    'name': 'gpu-z',
                    'status': 'RUNNING',
                    'zone': 'zones/us-central1-b',
                    'machineType': ('zones/us-central1-b/machineTypes/'
                                    'g2-standard-8'),
                    'labels': {
                        'managed-by': 'gpu-script',
                        'owner': 'alice',
                        'gpu-type': 'l4',
                        'gpu-count': '1',
                    },
                    'guestAccelerators': [{
                        'acceleratorType': ('zones/us-central1-b/'
                                            'acceleratorTypes/nvidia-l4'),
                        'acceleratorCount': 1,
                    }],
                }],
            },
        },
    }
    second_page = {
        'items': {
            'zones/us-central1-a': {
                'instances': [{
                    'name': 'cpu-a',
                    'status': 'TERMINATED',
                    'zone': 'zones/us-central1-a',
                    'machineType': ('zones/us-central1-a/machineTypes/'
                                    'n2-standard-8'),
                    'labels': {
                        'managed-by': 'gpu-script',
                        'owner': 'bob',
                    },
                }],
            },
        },
    }
    requests = [mock.Mock(), mock.Mock()]
    requests[0].execute.return_value = first_page
    requests[1].execute.return_value = second_page
    instances_resource = mock.Mock()
    instances_resource.aggregatedList.return_value = requests[0]
    instances_resource.aggregatedList_next.side_effect = [requests[1], None]
    compute = mock.Mock()
    compute.instances.return_value = instances_resource

    with mock.patch.object(direct_vm_inventory.gcp,
                           'build',
                           return_value=compute):
        result = direct_vm_inventory.get_direct_vm_inventory(['project-a'])

    assert result == {
        'configured': True,
        'observed_at': mock.ANY,
        'instances': [{
            'cloud': 'GCP',
            'project': 'project-a',
            'name': 'cpu-a',
            'owner': 'bob',
            'status': 'TERMINATED',
            'machine_type': 'n2-standard-8',
            'zone': 'us-central1-a',
            'gpu_type': '',
            'gpu_count': 0,
        }, {
            'cloud': 'GCP',
            'project': 'project-a',
            'name': 'gpu-z',
            'owner': 'alice',
            'status': 'RUNNING',
            'machine_type': 'g2-standard-8',
            'zone': 'us-central1-b',
            'gpu_type': 'l4',
            'gpu_count': 1,
        }],
        'errors': [],
    }
    instances_resource.aggregatedList.assert_called_once_with(
        project='project-a',
        filter='labels.managed-by = gpu-script',
        fields=mock.ANY,
    )


def test_get_direct_vm_inventory_fails_open_per_project():
    with mock.patch.object(direct_vm_inventory,
                           '_list_gcp_instances',
                           side_effect=[RuntimeError('denied'), []]):
        result = direct_vm_inventory.get_direct_vm_inventory(
            ['unavailable', 'empty'])

    assert result['configured'] is True
    assert not result['instances']
    assert result['errors'] == [{
        'project': 'unavailable',
        'message': 'RuntimeError: denied',
    }]


def test_get_direct_vm_inventory_unconfigured_skips_provider():
    with mock.patch.object(direct_vm_inventory,
                           '_list_gcp_instances') as list_instances:
        result = direct_vm_inventory.get_direct_vm_inventory([])

    assert result == {
        'configured': False,
        'observed_at': mock.ANY,
        'instances': [],
        'errors': [],
    }
    list_instances.assert_not_called()
