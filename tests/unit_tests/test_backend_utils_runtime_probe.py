"""Tests for Kubernetes runtime-probe transport classification."""

from unittest import mock

import pytest

from sky import backends
from sky import clouds
from sky import exceptions
from sky import resources as resources_lib
from sky.backends import backend_utils
from sky.provision import common as provision_common
from sky.utils import status_lib


class TestVerifyKubernetesExecTransport:

    def test_successful_probe_returns_without_error(self):
        runner = mock.MagicMock()
        runner.run.return_value = (0, '', '')

        backend_utils._verify_kubernetes_exec_transport(runner,
                                                        'managed-job-cluster')

        runner.run.assert_called_once_with('true',
                                           stream_logs=False,
                                           require_outputs=True,
                                           separate_stderr=True)

    def test_failed_probe_raises_status_fetching_error(self):
        runner = mock.MagicMock()
        runner.run.return_value = (1, '', 'control plane unavailable')

        with pytest.raises(exceptions.ClusterStatusFetchingError,
                           match='Kubernetes exec transport'):
            backend_utils._verify_kubernetes_exec_transport(
                runner, 'managed-job-cluster')


def test_update_cluster_status_exec_outage_is_status_fetching_error():
    runner = mock.MagicMock()
    runner.run.side_effect = [
        (1, '', 'ray status unavailable'),
        (1, '', 'control plane unavailable'),
    ]

    resources = resources_lib.Resources(cloud=clouds.Kubernetes())
    handle = backends.CloudVmRayResourceHandle(
        cluster_name='managed-job-cluster',
        cluster_name_on_cloud='managed-job-cluster',
        cluster_yaml='/tmp/cluster.yaml',
        launched_nodes=1,
        launched_resources=resources)
    handle.provision_runtime_metadata = (
        provision_common.ProvisionRuntimeMetadata(has_ray=True))
    handle.get_command_runners = mock.MagicMock(return_value=[runner])
    record = {
        'handle': handle,
        'status': status_lib.ClusterStatus.UP,
        'cluster_hash': 'cluster-hash',
    }

    with mock.patch.object(
            backend_utils,
            '_query_cluster_status_via_cloud_api',
            return_value={'pod': (status_lib.ClusterStatus.UP, None)}), \
         mock.patch.object(backend_utils.ExternalFailureSource,
                           'get', return_value=[]), \
         mock.patch.object(backend_utils.global_user_state,
                           'add_or_update_cluster') as update_cluster:
        with pytest.raises(exceptions.ClusterStatusFetchingError,
                           match='Kubernetes exec transport'):
            backend_utils._update_cluster_status('managed-job-cluster',
                                                 record,
                                                 retry_if_missing=True)

    update_cluster.assert_not_called()
