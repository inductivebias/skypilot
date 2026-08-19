#!/usr/bin/env bash

set -euo pipefail

usage() {
  echo "usage: $0 PHASE POOL_FILE KUBECONFIG MARKER EVIDENCE_DIR" >&2
  echo "phases: preflight attach remove-current remove-last concurrent-down" >&2
}

if [ "$#" -ne 5 ]; then
  usage
  exit 2
fi

gamma_phase=$1
gamma_pool_file=$2
gamma_kubeconfig=$3
gamma_marker=$4
gamma_evidence_dir=$5
gamma_sky_bin=${GAMMA_SKY_BIN:-sky}
gamma_python_bin=${GAMMA_PYTHON_BIN:-python3}
gamma_script_dir=$(cd "$(dirname "$0")" && pwd)
gamma_pool_tool="$gamma_script_dir/pool_config.py"
gamma_validator="$gamma_script_dir/validate_kubeconfig.py"
gamma_keep_pool=gamma-pool-keep
gamma_remove_pool=gamma-pool-remove
gamma_keep_context=ssh-gamma-pool-keep
gamma_remove_context=ssh-gamma-pool-remove
gamma_default_kubeconfig="${HOME:?HOME must be set}/.kube/config"

if [ ! -f "$gamma_marker" ] ||
   [ "$(tr -d '\r\n' < "$gamma_marker")" != gamma-ssh-down-current-context ]; then
  echo "error: gamma marker is missing or invalid: $gamma_marker" >&2
  exit 1
fi
if [ "$gamma_kubeconfig" != "$gamma_default_kubeconfig" ]; then
  echo "error: use the gamma controller kubeconfig: $gamma_default_kubeconfig" >&2
  exit 1
fi
if [ ! -f "$gamma_kubeconfig" ] && [ "$gamma_phase" != preflight ] &&
   [ "$gamma_phase" != attach ]; then
  echo "error: kubeconfig does not exist: $gamma_kubeconfig" >&2
  exit 1
fi

mkdir -p "$gamma_evidence_dir"
gamma_timestamp=$(date -u +%Y%m%dT%H%M%SZ)

run_logged() {
  local gamma_name=$1
  shift
  "$@" 2>&1 | tee "$gamma_evidence_dir/${gamma_timestamp}-${gamma_name}.log"
}

validate_pool_file() {
  "$gamma_python_bin" "$gamma_pool_tool" validate "$gamma_pool_file"
}

validate_before_remove() {
  "$gamma_python_bin" "$gamma_validator" \
    --kubeconfig "$gamma_kubeconfig" \
    --expect-current "$gamma_remove_context" \
    --require-context "$gamma_keep_context" \
    --require-context "$gamma_remove_context" \
    --gamma-only
}

case "$gamma_phase" in
  preflight)
    validate_pool_file
    command -v "$gamma_sky_bin"
    command -v kubectl
    command -v ssh
    run_logged sky-version "$gamma_sky_bin" --version
    run_logged api-info "$gamma_sky_bin" api info
    run_logged jobs-before "$gamma_sky_bin" jobs queue
    run_logged status-before "$gamma_sky_bin" status
    echo "Review the jobs and status logs. Continue only when gamma has no jobs or placements."
    ;;
  attach)
    validate_pool_file
    run_logged attach-keep "$gamma_sky_bin" ssh up \
      --infra "$gamma_keep_pool" -f "$gamma_pool_file"
    run_logged attach-remove "$gamma_sky_bin" ssh up \
      --infra "$gamma_remove_pool" -f "$gamma_pool_file"
    kubectl --kubeconfig "$gamma_kubeconfig" config use-context \
      "$gamma_remove_context"
    validate_before_remove | tee \
      "$gamma_evidence_dir/${gamma_timestamp}-attached-kubeconfig.json"
    run_logged check-after-attach "$gamma_sky_bin" check ssh
    ;;
  remove-current)
    validate_pool_file
    validate_before_remove | tee \
      "$gamma_evidence_dir/${gamma_timestamp}-before-current-removal.json"
    run_logged remove-current "$gamma_sky_bin" ssh down \
      --infra "$gamma_remove_pool"
    "$gamma_python_bin" "$gamma_validator" \
      --kubeconfig "$gamma_kubeconfig" \
      --expect-current "$gamma_keep_context" \
      --require-context "$gamma_keep_context" \
      --removed-context "$gamma_remove_context" \
      --gamma-only | tee \
      "$gamma_evidence_dir/${gamma_timestamp}-after-current-removal.json"
    run_logged check-after-current-removal "$gamma_sky_bin" check ssh
    run_logged status-after-current-removal "$gamma_sky_bin" status
    echo "Refresh the gamma dashboard now. It must still show gamma-pool-keep."
    ;;
  remove-last)
    validate_pool_file
    "$gamma_python_bin" "$gamma_validator" \
      --kubeconfig "$gamma_kubeconfig" \
      --expect-current "$gamma_keep_context" \
      --require-context "$gamma_keep_context" \
      --removed-context "$gamma_remove_context" \
      --gamma-only
    run_logged remove-last "$gamma_sky_bin" ssh down \
      --infra "$gamma_keep_pool"
    "$gamma_python_bin" "$gamma_validator" \
      --kubeconfig "$gamma_kubeconfig" \
      --expect-current-unset \
      --removed-context "$gamma_keep_context" \
      --removed-context "$gamma_remove_context" \
      --gamma-only | tee \
      "$gamma_evidence_dir/${gamma_timestamp}-after-last-removal.json"
    ;;
  concurrent-down)
    validate_pool_file
    validate_before_remove | tee \
      "$gamma_evidence_dir/${gamma_timestamp}-before-concurrent-removal.json"
    set +e
    "$gamma_sky_bin" ssh down --infra "$gamma_keep_pool" \
      >"$gamma_evidence_dir/${gamma_timestamp}-concurrent-keep.log" 2>&1 &
    gamma_keep_pid=$!
    "$gamma_sky_bin" ssh down --infra "$gamma_remove_pool" \
      >"$gamma_evidence_dir/${gamma_timestamp}-concurrent-remove.log" 2>&1 &
    gamma_remove_pid=$!
    wait "$gamma_keep_pid"
    gamma_keep_status=$?
    wait "$gamma_remove_pid"
    gamma_remove_status=$?
    set -e
    cat "$gamma_evidence_dir/${gamma_timestamp}-concurrent-keep.log"
    cat "$gamma_evidence_dir/${gamma_timestamp}-concurrent-remove.log"
    if [ "$gamma_keep_status" -ne 0 ] || [ "$gamma_remove_status" -ne 0 ]; then
      echo "error: concurrent teardown failed: keep=$gamma_keep_status remove=$gamma_remove_status" >&2
      exit 1
    fi
    "$gamma_python_bin" "$gamma_validator" \
      --kubeconfig "$gamma_kubeconfig" \
      --expect-current-unset \
      --removed-context "$gamma_keep_context" \
      --removed-context "$gamma_remove_context" \
      --gamma-only | tee \
      "$gamma_evidence_dir/${gamma_timestamp}-after-concurrent-removal.json"
    ;;
  *)
    usage
    exit 2
    ;;
esac
