"""Unit tests for the gamma SSH teardown smoke-test tools."""

import json
import os
import pathlib
import subprocess
from typing import Any, Dict, Optional, Sequence

from click import testing
import pytest
from smoke_tests.ssh_node_pools.gamma import pool_config
from smoke_tests.ssh_node_pools.gamma import validate_kubeconfig


def _state(current: Optional[str], names: Sequence[str]) -> Dict[str, Any]:
    return {
        'current-context': current or '',
        'contexts': [{
            'name': name
        } for name in names],
        'clusters': [{
            'name': name
        } for name in names],
        'users': [{
            'name': name
        } for name in names],
    }


def test_validate_state_removed_current_selects_required_gamma_context(
) -> None:
    keep = 'ssh-gamma-pool-keep'
    remove = 'ssh-gamma-pool-remove'

    validate_kubeconfig.validate_state(_state(keep, [keep]), keep, False,
                                       [keep], [remove], True)


def test_validate_state_dangling_current_raises_with_remaining_contexts(
) -> None:
    keep = 'ssh-gamma-pool-keep'
    remove = 'ssh-gamma-pool-remove'

    with pytest.raises(ValueError, match='Remaining contexts'):
        validate_kubeconfig.validate_state(_state(remove, [keep]), keep, False,
                                           [keep], [remove], True)


def test_validate_state_non_gamma_context_raises() -> None:
    with pytest.raises(ValueError, match='non-gamma'):
        validate_kubeconfig.validate_state(
            _state('gke-production', ['gke-production']), 'gke-production',
            False, [], [], True)


def test_validate_state_only_context_removed_accepts_unset() -> None:
    validate_kubeconfig.validate_state(_state(None, []), None, True, [],
                                       ['ssh-gamma-pool-keep'], True)


def test_validate_kubeconfig_cli_expected_current_passes_named_option(
        tmp_path: pathlib.Path) -> None:
    keep = 'ssh-gamma-pool-keep'
    kubeconfig_path = tmp_path / 'config'
    kubeconfig_path.write_text('apiVersion: v1\n', encoding='utf-8')
    kubectl_path = tmp_path / 'kubectl'
    kubectl_path.write_text(
        '#!/usr/bin/env python3\n'
        f'print({json.dumps(json.dumps(_state(keep, [keep])))})\n',
        encoding='utf-8')
    kubectl_path.chmod(0o700)

    result = testing.CliRunner().invoke(
        validate_kubeconfig.cli,
        [
            '--kubeconfig',
            str(kubeconfig_path),
            '--kubectl',
            str(kubectl_path),
            '--expect-current',
            keep,
            '--require-context',
            keep,
            '--gamma-only',
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['current_context'] == keep


def test_run_gamma_drill_nondefault_kubeconfig_fails_closed(
        tmp_path: pathlib.Path) -> None:
    home_path = tmp_path / 'gamma-home'
    marker_path = home_path / '.sky' / 'gamma-environment'
    marker_path.parent.mkdir(parents=True)
    marker_path.write_text('gamma-ssh-down-current-context\n', encoding='utf-8')
    script_path = pathlib.Path(
        pool_config.__file__).with_name('run_gamma_drill.sh')
    result = subprocess.run(
        [
            'bash',
            str(script_path),
            'preflight',
            str(tmp_path / 'pools.yaml'),
            str(tmp_path / 'wrong-kubeconfig'),
            str(marker_path),
            str(tmp_path / 'evidence'),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            'HOME': str(home_path),
        },
    )

    assert result.returncode == 1
    assert 'use the gamma controller kubeconfig' in result.stderr


def test_pool_config_requires_exact_gamma_pools(tmp_path: pathlib.Path) -> None:
    key_path = tmp_path / 'gamma-key'
    key_path.write_text('synthetic', encoding='utf-8')
    key_path.chmod(0o600)
    config = {
        pool_config.KEEP_POOL: {
            'hosts': ['10.0.0.2'],
            'user': 'gamma',
            'identity_file': str(key_path),
        },
        pool_config.REMOVE_POOL: {
            'hosts': ['10.0.0.3'],
            'user': 'gamma',
            'identity_file': str(key_path),
        },
    }

    pool_config.validate_config(config)

    config['deploy-lambda-1'] = config[pool_config.KEEP_POOL]
    with pytest.raises(ValueError, match='Expected only'):
        pool_config.validate_config(config)


def test_pool_config_rejects_duplicate_hosts(tmp_path: pathlib.Path) -> None:
    key_path = tmp_path / 'gamma-key'
    key_path.write_text('synthetic', encoding='utf-8')
    key_path.chmod(0o600)
    config = {
        pool_name: {
            'hosts': ['10.0.0.2'],
            'user': 'gamma',
            'identity_file': str(key_path),
        } for pool_name in pool_config.EXPECTED_POOLS
    }

    with pytest.raises(ValueError, match='two different hosts'):
        pool_config.validate_config(config)


def test_pool_config_rejects_group_readable_key(tmp_path: pathlib.Path) -> None:
    key_path = tmp_path / 'gamma-key'
    key_path.write_text('synthetic', encoding='utf-8')
    key_path.chmod(0o640)
    config = {
        pool_name: {
            'hosts': ['10.0.0.2'],
            'user': 'gamma',
            'identity_file': str(key_path),
        } for pool_name in pool_config.EXPECTED_POOLS
    }

    with pytest.raises(ValueError, match='group or other permissions'):
        pool_config.validate_config(config)
