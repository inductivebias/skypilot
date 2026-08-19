#!/usr/bin/env python3
"""Check the kubeconfig invariants for the gamma teardown drill."""

import datetime
import json
import pathlib
import subprocess
from typing import Any, Dict, Optional, Sequence, Set

import click

GAMMA_CONTEXT_PREFIX = 'ssh-gamma-'


def entry_names(config: Dict[str, Any], key: str) -> Set[str]:
    """Return the named entries from a kubeconfig list."""
    entries = config.get(key) or []
    if not isinstance(entries, list):
        raise ValueError(f'Kubeconfig field {key!r} is not a list.')
    result = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(
                entry.get('name'), str):
            raise ValueError(f'Kubeconfig field {key!r} has an invalid entry.')
        result.add(entry['name'])
    return result


def validate_state(config: Dict[str, Any], expected_current: Optional[str],
                   expect_current_unset: bool, required_contexts: Sequence[str],
                   removed_contexts: Sequence[str], gamma_only: bool) -> None:
    """Raise ValueError when config does not satisfy the drill invariant."""
    contexts = entry_names(config, 'contexts')
    clusters = entry_names(config, 'clusters')
    users = entry_names(config, 'users')
    current = config.get('current-context') or None
    if current is not None and not isinstance(current, str):
        raise ValueError('Kubeconfig current-context is not a string.')
    if current is not None and current not in contexts:
        raise ValueError(
            f'Current context {current!r} is missing. Remaining contexts: '
            f'{sorted(contexts)!r}.')
    if expect_current_unset and current is not None:
        raise ValueError(
            f'Expected current context to be unset, got {current!r}.')
    if expected_current is not None and current != expected_current:
        raise ValueError(
            f'Expected current context {expected_current!r}, got {current!r}.')
    for context_name in required_contexts:
        missing_groups = [
            group_name for group_name, names in [
                ('context', contexts),
                ('cluster', clusters),
                ('user', users),
            ] if context_name not in names
        ]
        if missing_groups:
            raise ValueError(f'Required name {context_name!r} is missing from '
                             f'{missing_groups!r}.')
    for context_name in removed_contexts:
        present_groups = [
            group_name for group_name, names in [
                ('context', contexts),
                ('cluster', clusters),
                ('user', users),
            ] if context_name in names
        ]
        if present_groups:
            raise ValueError(f'Removed name {context_name!r} remains in '
                             f'{present_groups!r}.')
    if gamma_only:
        non_gamma_names = sorted(name for name in contexts | clusters | users
                                 if not name.startswith(GAMMA_CONTEXT_PREFIX))
        if non_gamma_names:
            raise ValueError(f'Gamma kubeconfig contains non-gamma names: '
                             f'{non_gamma_names!r}.')


def read_kubeconfig(kubectl: str,
                    kubeconfig_path: pathlib.Path) -> Dict[str, Any]:
    """Read the flattened kubeconfig through kubectl."""
    command = [
        kubectl, '--kubeconfig',
        str(kubeconfig_path), 'config', 'view', '--raw', '-o', 'json'
    ]
    result = subprocess.run(command,
                            check=False,
                            capture_output=True,
                            text=True)
    if result.returncode != 0:
        raise ValueError(f'kubectl failed with exit {result.returncode}: '
                         f'{result.stderr.strip()}')
    try:
        config = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError('kubectl returned invalid JSON.') from error
    if not isinstance(config, dict):
        raise ValueError('kubectl returned a non-object kubeconfig.')
    return config


def evidence(config: Dict[str, Any],
             kubeconfig_path: pathlib.Path) -> Dict[str, Any]:
    """Build non-secret evidence from a validated kubeconfig."""
    return {
        'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'kubeconfig': str(kubeconfig_path),
        'current_context': config.get('current-context') or None,
        'contexts': sorted(entry_names(config, 'contexts')),
        'clusters': sorted(entry_names(config, 'clusters')),
        'users': sorted(entry_names(config, 'users')),
    }


@click.command()
@click.option('--kubeconfig',
              'kubeconfig_value',
              required=True,
              type=click.Path(exists=True, dir_okay=False))
@click.option('--kubectl', default='kubectl', show_default=True)
@click.option('--expect-current', 'expected_current')
@click.option('--expect-current-unset', is_flag=True)
@click.option('--require-context', 'required_contexts', multiple=True)
@click.option('--removed-context', 'removed_contexts', multiple=True)
@click.option('--gamma-only', is_flag=True)
def cli(kubeconfig_value: str, kubectl: str, expected_current: Optional[str],
        expect_current_unset: bool, required_contexts: Sequence[str],
        removed_contexts: Sequence[str], gamma_only: bool) -> None:
    """Validate one gamma kubeconfig state and print JSON evidence."""
    if (expected_current is None) == (not expect_current_unset):
        raise click.ClickException(
            'Pass exactly one of --expect-current or --expect-current-unset.')
    kubeconfig_path = pathlib.Path(kubeconfig_value).expanduser().resolve()
    try:
        config = read_kubeconfig(kubectl, kubeconfig_path)
        validate_state(config, expected_current, expect_current_unset,
                       required_contexts, removed_contexts, gamma_only)
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(json.dumps(evidence(config, kubeconfig_path), sort_keys=True))


if __name__ == '__main__':
    cli()  # pylint: disable=no-value-for-parameter
