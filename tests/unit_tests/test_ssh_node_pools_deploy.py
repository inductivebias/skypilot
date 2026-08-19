"""Unit tests for sky/ssh_node_pools/deploy/deploy.py helpers."""
# pylint: disable=missing-class-docstring,protected-access

import json
import shutil
import subprocess
import threading

import pytest

from sky.ssh_node_pools.deploy import deploy

_KUBECONFIG_PATH = '/tmp/kubeconfig'
_KUBECTL_PATH = shutil.which('kubectl')


class _FakeKubeconfig:

    def __init__(self,
                 contexts,
                 current_context,
                 fail_action=None,
                 dangling_after_cleanup=False):
        self.contexts = list(contexts)
        self.clusters = list(contexts)
        self.users = list(contexts)
        self.current_context = current_context
        self.fail_action = fail_action
        self.dangling_after_cleanup = dangling_after_cleanup
        self.calls = []
        self.view_count = 0

    def run_command(self, cmd, shell=False, silent=False):
        assert shell is False
        assert silent is True
        assert cmd[:4] == [
            'kubectl', '--kubeconfig', _KUBECONFIG_PATH, 'config'
        ]
        self.calls.append(cmd)
        args = cmd[4:]

        if args == ['view', '--raw', '-o', 'json']:
            self.view_count += 1
            current_context = self.current_context
            if self.dangling_after_cleanup and self.view_count > 1:
                current_context = 'missing-context'
            return json.dumps({
                'current-context': current_context or '',
                'contexts': [{
                    'name': name
                } for name in self.contexts],
                'clusters': [{
                    'name': name
                } for name in self.clusters],
                'users': [{
                    'name': name
                } for name in self.users],
            })

        action = args[0]
        if action == self.fail_action:
            return None
        if action == 'use-context':
            self.current_context = args[1]
            return f'Switched to context {args[1]!r}.'
        if action == 'unset':
            self.current_context = None
            return 'Property current-context unset.'

        name = args[1]
        entries = {
            'delete-context': self.contexts,
            'delete-cluster': self.clusters,
            'delete-user': self.users,
        }[action]
        entries.remove(name)
        return f'Deleted {name!r}.'


def _config_action(call):
    return call[4]


def _run_real_kubectl(kubeconfig_path, *args, check=True):
    assert _KUBECTL_PATH is not None
    return subprocess.run(
        [_KUBECTL_PATH, '--kubeconfig',
         str(kubeconfig_path), *args],
        check=check,
        capture_output=True,
        text=True)


def test_kubeconfig_lock_path_does_not_conflict_with_kubectl_lock():
    lock_path = deploy._kubeconfig_lock_path(_KUBECONFIG_PATH)

    assert lock_path == f'{_KUBECONFIG_PATH}.sky-ssh-node-pools.lock'
    assert lock_path != f'{_KUBECONFIG_PATH}.lock'


def test_remove_kubeconfig_context_serializes_same_kubeconfig(
        monkeypatch, tmp_path):
    first_entered = threading.Event()
    release_first = threading.Event()
    calls = []

    def remove_locked(context_name, kubeconfig_path):
        calls.append((context_name, kubeconfig_path))
        if len(calls) == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)

    monkeypatch.setattr(deploy, '_remove_kubeconfig_context_locked',
                        remove_locked)
    kubeconfig_path = str(tmp_path / 'config')
    first = threading.Thread(target=deploy._remove_kubeconfig_context,
                             args=('ssh-pool-a', kubeconfig_path))
    second = threading.Thread(target=deploy._remove_kubeconfig_context,
                              args=('ssh-pool-b', kubeconfig_path))

    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    second.join(timeout=0.1)

    assert second.is_alive()
    assert calls == [('ssh-pool-a', kubeconfig_path)]

    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()
    assert calls == [('ssh-pool-a', kubeconfig_path),
                     ('ssh-pool-b', kubeconfig_path)]


def test_remove_kubeconfig_context_current_switches_before_delete(monkeypatch):
    config = _FakeKubeconfig(['gke-prod', 'ssh-pool-7'], 'ssh-pool-7')
    monkeypatch.setattr(deploy.deploy_utils, 'run_command', config.run_command)

    deploy._remove_kubeconfig_context('ssh-pool-7', _KUBECONFIG_PATH)

    assert config.current_context == 'gke-prod'
    assert config.contexts == ['gke-prod']
    assert config.clusters == ['gke-prod']
    assert config.users == ['gke-prod']
    actions = [_config_action(call) for call in config.calls]
    assert actions.index('use-context') < actions.index('delete-context')
    use_context_call = next(
        call for call in config.calls if _config_action(call) == 'use-context')
    assert use_context_call[-1] == 'gke-prod'


@pytest.mark.skipif(_KUBECTL_PATH is None, reason='kubectl is not installed')
def test_remove_kubeconfig_context_real_kubectl_repairs_quoted_name_bug(
        tmp_path):
    kubeconfig_path = tmp_path / 'config'
    gke_context = 'gke_flourish-gpu_us-central1-a_deploy-gke'
    target_context = 'ssh-deploy-lambda-7'
    for context_name in [gke_context, target_context]:
        _run_real_kubectl(kubeconfig_path, 'config', 'set-cluster',
                          context_name, '--server=https://127.0.0.1')
        _run_real_kubectl(kubeconfig_path, 'config', 'set-credentials',
                          context_name, '--token=synthetic')
        _run_real_kubectl(kubeconfig_path, 'config', 'set-context',
                          context_name, f'--cluster={context_name}',
                          f'--user={context_name}')
    _run_real_kubectl(kubeconfig_path, 'config', 'use-context', target_context)

    old_fallback = _run_real_kubectl(kubeconfig_path, 'config', 'view', '-o',
                                     'jsonpath=\'{.contexts[0].name}\'').stdout
    single_quote = chr(39)
    assert (old_fallback.startswith(single_quote) and
            old_fallback.endswith(single_quote))
    old_switch = _run_real_kubectl(kubeconfig_path,
                                   'config',
                                   'use-context',
                                   old_fallback,
                                   check=False)
    assert old_switch.returncode != 0

    deploy._remove_kubeconfig_context(target_context, str(kubeconfig_path))

    state = json.loads(
        _run_real_kubectl(kubeconfig_path, 'config', 'view', '--raw', '-o',
                          'json').stdout)
    assert state['current-context'] == gke_context
    for key in ['contexts', 'clusters', 'users']:
        assert target_context not in {
            entry['name'] for entry in state.get(key) or []
        }


def test_remove_kubeconfig_context_non_current_preserves_current(monkeypatch):
    config = _FakeKubeconfig(['gke-prod', 'ssh-pool-7'], 'gke-prod')
    monkeypatch.setattr(deploy.deploy_utils, 'run_command', config.run_command)

    deploy._remove_kubeconfig_context('ssh-pool-7', _KUBECONFIG_PATH)

    assert config.current_context == 'gke-prod'
    actions = [_config_action(call) for call in config.calls]
    assert 'use-context' not in actions
    assert 'unset' not in actions


def test_remove_kubeconfig_context_only_context_unsets_before_delete(
        monkeypatch):
    config = _FakeKubeconfig(['ssh-pool-7'], 'ssh-pool-7')
    monkeypatch.setattr(deploy.deploy_utils, 'run_command', config.run_command)

    deploy._remove_kubeconfig_context('ssh-pool-7', _KUBECONFIG_PATH)

    assert config.current_context is None
    actions = [_config_action(call) for call in config.calls]
    assert actions.index('unset') < actions.index('delete-context')


def test_remove_kubeconfig_context_dangling_target_repairs_before_cleanup(
        monkeypatch):
    config = _FakeKubeconfig(['gke-prod'], 'ssh-pool-7')
    monkeypatch.setattr(deploy.deploy_utils, 'run_command', config.run_command)

    deploy._remove_kubeconfig_context('ssh-pool-7', _KUBECONFIG_PATH)

    assert config.current_context == 'gke-prod'
    actions = [_config_action(call) for call in config.calls]
    assert actions == ['view', 'use-context', 'view']


def test_remove_kubeconfig_context_unrelated_dangling_current_fails_closed(
        monkeypatch):
    config = _FakeKubeconfig(['gke-prod', 'ssh-pool-7'], 'missing-context')
    monkeypatch.setattr(deploy.deploy_utils, 'run_command', config.run_command)

    with pytest.raises(RuntimeError, match='unrelated invalid context'):
        deploy._remove_kubeconfig_context('ssh-pool-7', _KUBECONFIG_PATH)

    actions = [_config_action(call) for call in config.calls]
    assert actions == ['view']


def test_remove_kubeconfig_context_switch_failure_raises_before_delete(
        monkeypatch):
    config = _FakeKubeconfig(['gke-prod', 'ssh-pool-7'],
                             'ssh-pool-7',
                             fail_action='use-context')
    monkeypatch.setattr(deploy.deploy_utils, 'run_command', config.run_command)

    with pytest.raises(RuntimeError, match='switch current context'):
        deploy._remove_kubeconfig_context('ssh-pool-7', _KUBECONFIG_PATH)

    actions = [_config_action(call) for call in config.calls]
    assert 'delete-context' not in actions


def test_remove_kubeconfig_context_delete_failure_raises(monkeypatch):
    config = _FakeKubeconfig(['gke-prod', 'ssh-pool-7'],
                             'gke-prod',
                             fail_action='delete-context')
    monkeypatch.setattr(deploy.deploy_utils, 'run_command', config.run_command)

    with pytest.raises(RuntimeError, match='delete context'):
        deploy._remove_kubeconfig_context('ssh-pool-7', _KUBECONFIG_PATH)


def test_remove_kubeconfig_context_dangling_postcondition_raises(monkeypatch):
    config = _FakeKubeconfig(['gke-prod', 'ssh-pool-7'],
                             'gke-prod',
                             dangling_after_cleanup=True)
    monkeypatch.setattr(deploy.deploy_utils, 'run_command', config.run_command)

    with pytest.raises(RuntimeError, match='missing-context'):
        deploy._remove_kubeconfig_context('ssh-pool-7', _KUBECONFIG_PATH)


def test_prometheus_install_cmd_contains_required_fields():
    askpass_block = 'echo "askpass"'
    cmd = deploy._prometheus_install_cmd(askpass_block)

    # Must include the askpass block verbatim (consistent with sibling helpers).
    assert askpass_block in cmd

    # Must self-install helm if missing — the gpu-operator path installs
    # helm for GPU pools, but CPU-only pools skip that step.
    assert 'command -v helm' in cmd
    assert 'get-helm-3' in cmd

    # Must use the prometheus-community repo and the plain prometheus chart
    # (NOT kube-prometheus-stack — see spec "Do NOT use kube-prometheus-stack").
    assert 'prometheus-community' in cmd
    assert 'prometheus-community/prometheus' in cmd
    assert 'kube-prometheus-stack' not in cmd

    # Repo-scoped update is cheaper than a global `helm repo update`.
    assert 'helm repo update prometheus-community' in cmd

    # Must be idempotent (upgrade --install).
    assert 'helm upgrade --install' in cmd

    # Must target the correct kubeconfig on the remote head node.
    assert '--kubeconfig ~/.kube/config' in cmd
    assert '--namespace skypilot' in cmd
    assert '--create-namespace' in cmd

    # Release name hardcoded.
    assert 'skypilot-prometheus' in cmd

    # Must NOT pass --kube-context. The command runs on the pool's head node,
    # where `~/.kube/config` only has the default context k3s wrote — any
    # `ssh-<pool>` context name only exists in the client's merged kubeconfig.
    # The sibling `_dcgm_exporter_service_cmd` correctly omits it.
    assert '--kube-context' not in cmd

    # Values file must be created via mktemp so concurrent pool deploys don't
    # race on a shared path.
    assert 'mktemp' in cmd

    # Helm exit code must be explicitly captured and re-raised. The rm-after-
    # helm pattern would otherwise mask a helm failure with a clean exit 0.
    assert 'HELM_RET=$?' in cmd
    assert 'exit $HELM_RET' in cmd

    # Must enable node-exporter (the deliberate deviation from the skill
    # example).
    assert 'prometheus-node-exporter' in cmd

    # pushgateway and alertmanager explicitly disabled.
    assert 'prometheus-pushgateway' in cmd
    assert 'alertmanager' in cmd


def test_prometheus_install_cmd_node_exporter_enabled_not_disabled():
    """Regression: guard against ever flipping node-exporter to disabled."""
    cmd = deploy._prometheus_install_cmd('')
    # Find the prometheus-node-exporter section and verify it's enabled: true,
    # not enabled: false.
    ne_section = cmd[cmd.index('prometheus-node-exporter'):]
    # The first 'enabled:' after the node-exporter key must be 'true'.
    enabled_line = ne_section[ne_section.index('enabled:'):].splitlines()[0]
    assert enabled_line.strip() == 'enabled: true'
