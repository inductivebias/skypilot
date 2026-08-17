#!/usr/bin/env bash
# Build a Flourish SkyPilot wheel with its compiled dashboard and verify it.
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "$0")" && pwd)"
DASHBOARD_DIRECTORY="$REPOSITORY_ROOT/sky/dashboard"
DASHBOARD_INDEX="sky/dashboard/out/index.html"
PYTHON_BIN="${PYTHON_BIN:-python3}"

verify_wheel() {  # $1 = wheel path
  local wheel_path="$1"
  [ -f "$wheel_path" ] || {
    echo "error: wheel not found: $wheel_path" >&2
    return 1
  }
  "$PYTHON_BIN" - "$wheel_path" "$DASHBOARD_INDEX" <<'PY'
from __future__ import annotations

import sys
import zipfile

wheel_path, dashboard_index = sys.argv[1:]
with zipfile.ZipFile(wheel_path) as wheel:
    if dashboard_index not in wheel.namelist():
        raise SystemExit(
            f"error: wheel is missing {dashboard_index}; "
            "build the dashboard before packaging"
        )
print(f"verified wheel payload: {dashboard_index}")
PY
}

if [ "${1:-}" = "--verify-only" ]; then
  [ "$#" -eq 2 ] || {
    echo "usage: $0 [--verify-only WHEEL]" >&2
    exit 2
  }
  verify_wheel "$2"
  exit 0
fi
[ "$#" -eq 0 ] || {
  echo "usage: $0 [--verify-only WHEEL]" >&2
  exit 2
}

command -v npm >/dev/null || {
  echo "error: npm is required to build the SkyPilot dashboard" >&2
  exit 1
}
command -v uv >/dev/null || {
  echo "error: uv is required to build the SkyPilot wheel" >&2
  exit 1
}
command -v git >/dev/null || {
  echo "error: git is required to verify the release checkout" >&2
  exit 1
}
command -v "$PYTHON_BIN" >/dev/null || {
  echo "error: Python executable not found: $PYTHON_BIN" >&2
  exit 1
}
if [ -n "$(git -C "$REPOSITORY_ROOT" status --porcelain)" ]; then
  echo "error: release checkout is dirty; commit or remove every change first" >&2
  git -C "$REPOSITORY_ROOT" status --short >&2
  exit 1
fi

echo "Building SkyPilot dashboard..."
npm --prefix "$DASHBOARD_DIRECTORY" ci
npm --prefix "$DASHBOARD_DIRECTORY" run build
[ -r "$REPOSITORY_ROOT/$DASHBOARD_INDEX" ] || {
  echo "error: dashboard build did not create $DASHBOARD_INDEX" >&2
  exit 1
}

temporary_directory="$(mktemp -d "${TMPDIR:-/tmp}/skypilot-flourish-release.XXXXXX")"
trap 'rm -rf "$temporary_directory"' EXIT
echo "Building SkyPilot wheel..."
uv run --no-project --with build python -m build \
  --wheel --outdir "$temporary_directory" "$REPOSITORY_ROOT"

wheel_path="$(find "$temporary_directory" -maxdepth 1 -type f -name '*.whl' -print -quit)"
[ -n "$wheel_path" ] || {
  echo "error: wheel build produced no wheel" >&2
  exit 1
}
wheel_count="$(find "$temporary_directory" -maxdepth 1 -type f -name '*.whl' | wc -l | tr -d ' ')"
[ "$wheel_count" -eq 1 ] || {
  echo "error: expected one wheel, found $wheel_count" >&2
  exit 1
}
verify_wheel "$wheel_path"

output_directory="${FLOURISH_RELEASE_OUTPUT_DIR:-$REPOSITORY_ROOT/dist/flourish-release}"
mkdir -p "$output_directory"
output_path="$output_directory/$(basename "$wheel_path")"
install -m 0644 "$wheel_path" "$output_path"
verify_wheel "$output_path"

"$PYTHON_BIN" - "$output_path" <<'PY'
from __future__ import annotations

import hashlib
import pathlib
import sys

wheel_path = pathlib.Path(sys.argv[1])
digest = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
print(f"release wheel: {wheel_path}")
print(f"sha256:{digest}")
PY
