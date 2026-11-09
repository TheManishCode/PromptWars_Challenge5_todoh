#!/usr/bin/env bash
# Shared by every tools/ci script. Sourced, not run. The same scripts are called by the workflows and by `make ci`: what passes on a laptop is what passes in CI.
# CI_OUT (default: a temporary directory outside the repository, one per checkout) holds logs, junit and coverage files, so running the gate never writes into the tree.
set -uo pipefail
ROOT="${CI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"  # CI_ROOT: tests point the scripts at a scratch repository
export ROOT
# One output directory per checkout: two gates run from two checkouts (a clean clone for review, a working tree) must never write the same junit and log files. CI sets CI_OUT explicitly.
export CI_OUT="${CI_OUT:-${TMPDIR:-/tmp}/todoh-ci-out-$(printf '%s' "$ROOT" | sha256sum | cut -c1-10)}"
export CI_TOOLS="${CI_TOOLS:-$ROOT/.ci-tools}"  # pinned binaries (tools/ci/bootstrap_tools.sh); git-ignored
export PATH="$CI_TOOLS/bin:$PATH"
mkdir -p "$CI_OUT"
cd "$ROOT" || exit 2
export PY="${PYTHON:-python}"
FAILS=0
step() { printf '%-30s %s\n' "$1" "$2"; }
# run NAME CMD...: run a command with its output kept in $CI_OUT/NAME.log; print ok or FAIL (and its last lines); remember the failure, go on
run() {
  local name="$1"; shift
  if "$@" > "$CI_OUT/$name.log" 2>&1; then step "$name" ok; else step "$name" "FAIL (log: $CI_OUT/$name.log)"; tail -n 12 "$CI_OUT/$name.log" | sed 's/^/    | /'; FAILS=$((FAILS + 1)); fi
}
finish() { if [ "$FAILS" -eq 0 ]; then step "$1" PASS; exit 0; else step "$1" "FAIL ($FAILS failing)"; exit 1; fi; }
