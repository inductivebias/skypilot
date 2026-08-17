from __future__ import annotations

from pathlib import Path
import subprocess
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / "build-flourish-release.sh"
DASHBOARD_INDEX = "sky/dashboard/out/index.html"


def _write_wheel(path: Path, *, include_dashboard: bool) -> None:
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr("sky/__init__.py", "")
        if include_dashboard:
            wheel.writestr(DASHBOARD_INDEX, "<!doctype html>")


def test_build_flourish_release_wheel_with_dashboard_succeeds(
        tmp_path: Path) -> None:
    wheel_path = tmp_path / "skypilot-0.13.0-py3-none-any.whl"
    _write_wheel(wheel_path, include_dashboard=True)

    completed = subprocess.run(
        [str(SCRIPT), "--verify-only",
         str(wheel_path)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert DASHBOARD_INDEX in completed.stdout


def test_build_flourish_release_wheel_missing_dashboard_fails(
        tmp_path: Path) -> None:
    wheel_path = tmp_path / "skypilot-0.13.0-py3-none-any.whl"
    _write_wheel(wheel_path, include_dashboard=False)

    completed = subprocess.run(
        [str(SCRIPT), "--verify-only",
         str(wheel_path)],
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert DASHBOARD_INDEX in completed.stderr
