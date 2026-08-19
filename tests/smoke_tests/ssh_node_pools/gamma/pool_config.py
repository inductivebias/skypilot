#!/usr/bin/env python3
"""Render and validate the gamma SSH node pool configuration."""

import os
import pathlib
import stat
import tempfile
from typing import Any, Dict

import click
import yaml

KEEP_POOL = 'gamma-pool-keep'
REMOVE_POOL = 'gamma-pool-remove'
EXPECTED_POOLS = {KEEP_POOL, REMOVE_POOL}


def validate_config(config: Dict[str, Any], check_files: bool = True) -> None:
    """Validate that a pool config contains only the two gamma pools."""
    if set(config) != EXPECTED_POOLS:
        raise ValueError(f'Expected only {sorted(EXPECTED_POOLS)!r}, got '
                         f'{sorted(config)!r}.')
    pool_hosts = []
    for pool_name in sorted(EXPECTED_POOLS):
        pool = config.get(pool_name)
        if not isinstance(pool, dict):
            raise ValueError(f'Pool {pool_name!r} must be a mapping.')
        hosts = pool.get('hosts')
        if not isinstance(hosts, list) or len(hosts) != 1:
            raise ValueError(
                f'Pool {pool_name!r} must contain exactly one host.')
        host = hosts[0]
        if not isinstance(host, str) or not host.strip():
            raise ValueError(f'Pool {pool_name!r} has an invalid host.')
        pool_hosts.append(host.strip())
        if not isinstance(pool.get('user'), str) or not pool['user'].strip():
            raise ValueError(f'Pool {pool_name!r} must contain a user.')
        identity_file = pool.get('identity_file')
        if not isinstance(identity_file, str) or not identity_file:
            raise ValueError(
                f'Pool {pool_name!r} must contain an identity file.')
        identity_path = pathlib.Path(identity_file).expanduser()
        if not identity_path.is_absolute():
            raise ValueError(
                f'Identity file for {pool_name!r} must be absolute.')
        if check_files:
            if not identity_path.is_file():
                raise ValueError(
                    f'Identity file for {pool_name!r} does not exist: '
                    f'{identity_path}')
            key_mode = stat.S_IMODE(identity_path.stat().st_mode)
            if key_mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise ValueError(
                    f'Identity file {identity_path} must not grant group or '
                    'other permissions.')
    if len(set(pool_hosts)) != len(pool_hosts):
        raise ValueError('Gamma pools must use two different hosts.')


def load_config(path: pathlib.Path) -> Dict[str, Any]:
    """Load a YAML mapping from path."""
    with path.open('r', encoding='utf-8') as config_file:
        config = yaml.safe_load(config_file)
    if not isinstance(config, dict):
        raise ValueError(f'Pool config {path} must contain a mapping.')
    return config


@click.group()
def cli() -> None:
    """Prepare the fixed two-pool gamma configuration."""


@cli.command('render')
@click.option('--keep-host', required=True)
@click.option('--remove-host', required=True)
@click.option('--ssh-user', required=True)
@click.option('--identity-file', required=True, type=click.Path())
@click.option('--output', required=True, type=click.Path())
@click.option('--force', is_flag=True, help='Replace an existing output file.')
def render(keep_host: str, remove_host: str, ssh_user: str, identity_file: str,
           output: str, force: bool) -> None:
    """Render the gamma pool file after checking the SSH key."""
    output_path = pathlib.Path(output).expanduser().resolve()
    identity_path = pathlib.Path(identity_file).expanduser().resolve()
    if output_path.exists() and not force:
        raise click.ClickException(
            f'Output already exists: {output_path}. Pass --force to replace.')
    config = {
        KEEP_POOL: {
            'hosts': [keep_host],
            'user': ssh_user,
            'identity_file': str(identity_path),
        },
        REMOVE_POOL: {
            'hosts': [remove_host],
            'user': ssh_user,
            'identity_file': str(identity_path),
        },
    }
    try:
        validate_config(config)
    except ValueError as error:
        raise click.ClickException(str(error)) from error

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w',
                                     encoding='utf-8',
                                     dir=output_path.parent,
                                     prefix=f'.{output_path.name}.',
                                     delete=False) as temporary_file:
        yaml.safe_dump(config, temporary_file, sort_keys=True)
        temporary_path = pathlib.Path(temporary_file.name)
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, output_path)
    click.echo(str(output_path))


@cli.command('validate')
@click.argument('path', type=click.Path(exists=True, dir_okay=False))
@click.option('--skip-file-checks',
              is_flag=True,
              help='Validate structure without reading the identity file.')
def validate(path: str, skip_file_checks: bool) -> None:
    """Fail unless path is the exact two-pool gamma configuration."""
    config_path = pathlib.Path(path).expanduser().resolve()
    try:
        validate_config(load_config(config_path),
                        check_files=not skip_file_checks)
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(f'valid gamma pool config: {config_path}')


if __name__ == '__main__':
    cli()
