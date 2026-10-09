"""Tests for bounded persisted job logs."""

import os
import subprocess
import sys

from sky.skylet import job_lib


def test_make_bounded_log_command_preserves_short_output(tmp_path):
    log_path = tmp_path / 'run.log'
    command = job_lib.make_bounded_log_command(
        f'{sys.executable} -c \'print("hello")\'',
        max_bytes=128,
        log_path=str(log_path))

    result = subprocess.run(command, shell=True, check=False)

    assert result.returncode == 0
    assert log_path.read_text(encoding='utf-8') == 'hello\n'


def test_make_bounded_log_command_expands_home_on_target(monkeypatch, tmp_path):
    controller_home = tmp_path / 'controller-home'
    target_home = tmp_path / 'target-home'
    controller_home.mkdir()
    target_home.mkdir()
    monkeypatch.setenv('HOME', str(controller_home))
    command = job_lib.make_bounded_log_command(
        f'{sys.executable} -c \'print("hello")\'',
        max_bytes=128,
        log_path='~/run.log')

    result = subprocess.run(command,
                            shell=True,
                            check=False,
                            env={
                                **os.environ, 'HOME': str(target_home)
                            })

    assert result.returncode == 0
    assert not (controller_home / 'run.log').exists()
    assert (target_home / 'run.log').read_text(encoding='utf-8') == 'hello\n'


def test_make_job_run_command_bounds_log_and_preserves_exit_code(
        monkeypatch, tmp_path):
    script_path = tmp_path / 'job.py'
    completed_path = tmp_path / 'completed'
    script_path.write_text(
        'import pathlib\n'
        'import sys\n'
        'sys.stdout.write(\'x\' * 1048576)\n'
        'sys.stdout.flush()\n'
        f'pathlib.Path({str(completed_path)!r}).touch()\n'
        'raise SystemExit(23)\n',
        encoding='utf-8')
    log_path = tmp_path / 'run.log'
    monkeypatch.setattr(job_lib.constants, 'SKY_PYTHON_CMD', sys.executable)
    monkeypatch.setattr(job_lib.constants, 'JOB_LOG_MAX_BYTES', 128)

    command = job_lib.make_job_run_command(7, str(script_path), str(log_path))
    result = subprocess.run(command,
                            shell=True,
                            check=False,
                            env={
                                **os.environ, 'PYTHONPATH': os.getcwd()
                            })

    assert result.returncode == 23
    assert completed_path.exists()
    output = log_path.read_bytes()
    assert len(output) == 128
    assert output.startswith(b'x')
    assert output.count(b'[SkyPilot] Log exceeded') == 1


def test_make_job_run_command_expands_paths_on_target(monkeypatch, tmp_path):
    controller_home = tmp_path / 'controller-home'
    target_home = tmp_path / 'target-home'
    controller_home.mkdir()
    target_home.mkdir()
    (target_home / 'job.py').write_text('print("hello")\n', encoding='utf-8')
    monkeypatch.setenv('HOME', str(controller_home))
    monkeypatch.setattr(job_lib.constants, 'SKY_PYTHON_CMD', sys.executable)

    command = job_lib.make_job_run_command(7, '~/job.py', '~/run.log')
    result = subprocess.run(command,
                            shell=True,
                            check=False,
                            env={
                                **os.environ, 'HOME': str(target_home)
                            })

    assert result.returncode == 0
    assert not (controller_home / 'run.log').exists()
    assert (target_home / 'run.log').read_text(encoding='utf-8') == 'hello\n'
