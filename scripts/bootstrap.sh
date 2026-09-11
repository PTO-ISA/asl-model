#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -n "${PTO_SPEC_ROOT:-}" ]]; then
  spec_dir=$PTO_SPEC_ROOT
  managed_checkout=0
else
  spec_dir="$root_dir/vendor/pto-spec"
  managed_checkout=1
fi
lock_file="$root_dir/dependencies/pto-spec.lock"
repository=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["repository"])' "$lock_file")
commit=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["commit"])' "$lock_file")

if [[ ! -d "$spec_dir/.git" ]]; then
  if [[ $managed_checkout -ne 1 ]]; then
    echo "PTO_SPEC_ROOT is not a Git checkout: $spec_dir" >&2
    exit 2
  fi
  mkdir -p "$(dirname "$spec_dir")"
  git clone --no-tags "$repository" "$spec_dir"
fi
if [[ $managed_checkout -eq 1 ]]; then
  git -C "$spec_dir" fetch --no-tags --quiet origin "$commit"
  git -C "$spec_dir" checkout --detach --quiet "$commit"
else
  current=$(git -C "$spec_dir" rev-parse HEAD)
  if [[ "$current" != "$commit" ]]; then
    echo "PTO_SPEC_ROOT commit mismatch: expected $commit, got $current" >&2
    exit 2
  fi
fi

# Generated artifacts are not commit-bound files and survive checkout. Force
# regeneration and ASLRef preparation so the reported Git identity cannot be
# paired with stale build output from an earlier revision.
make -B -C "$spec_dir" build
PTO_ASLREF_ROOT="${PTO_ASLREF_ROOT:-$spec_dir/.cache/herdtools7}" \
  make -C "$spec_dir" setup

echo "PTO_SPEC_ROOT=$spec_dir"
echo "PTO_ASLREF_ROOT=${PTO_ASLREF_ROOT:-$spec_dir/.cache/herdtools7}"
echo "ASL model dependencies are ready."
