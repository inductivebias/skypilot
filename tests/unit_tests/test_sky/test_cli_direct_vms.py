"""Tests for the direct VM administrator CLI."""

import json
from unittest import mock

from click.testing import CliRunner

from sky.client import sdk
from sky.client.cli import command

_INVENTORY = {
    'configured': True,
    'instances': [{
        'cloud': 'GCP',
        'project': 'flourish-gpu',
        'name': 'running-gpu',
        'owner': 'alice',
        'status': 'RUNNING',
        'machine_type': 'a2-highgpu-1g',
        'gpu_type': 'a100',
        'gpu_count': 1,
        'zone': 'us-central1-a',
    }, {
        'cloud': 'GCP',
        'project': 'flourish-gpu',
        'name': 'stopped-cpu',
        'owner': 'bob',
        'status': 'TERMINATED',
        'machine_type': 'n2-standard-8',
        'gpu_type': '',
        'gpu_count': 0,
        'zone': 'us-central1-b',
    }],
    'errors': [],
}


def test_api_direct_vms_defaults_to_running_instances():
    with mock.patch.object(sdk, 'direct_vm_inventory', return_value=_INVENTORY):
        result = CliRunner().invoke(command.api, ['direct-vms'])

    assert result.exit_code == 0, result.output
    assert 'running-gpu' in result.output
    assert '1x a100' in result.output
    assert 'stopped-cpu' not in result.output


def test_api_direct_vms_all_owner_json_filters_server_inventory():
    with mock.patch.object(sdk, 'direct_vm_inventory', return_value=_INVENTORY):
        result = CliRunner().invoke(
            command.api,
            ['direct-vms', '--all', '--owner', 'bob', '--output', 'json'])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [instance['name'] for instance in payload['instances']
           ] == ['stopped-cpu']
