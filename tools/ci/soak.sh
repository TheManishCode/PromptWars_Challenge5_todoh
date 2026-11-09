#!/usr/bin/env bash
# A sustained-load run against the real server in shared mode. Any 5xx, a refused step, a build that never ends, a slow p95, or growth in memory, files or threads fails.
#   SOAK_SECONDS (default 60)  SOAK_VISITORS (default 20)  SOAK_PLAN_SHARE (default 0.5)
# Shared mode refuses to start when the repository has a .env file, so the run uses an export of the committed files (and the solvers beside them), as a deploy would.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
git archive HEAD | tar -x -C "$tmp"
[ -e "${CI_SOLVERS:-$ROOT/solvers}" ] && ln -s "${CI_SOLVERS:-$ROOT/solvers}" "$tmp/solvers"
cd "$tmp" || exit 2
"$PY" tools/loadtest/soak.py --seconds "${SOAK_SECONDS:-60}" --visitors "${SOAK_VISITORS:-20}" --plan-share "${SOAK_PLAN_SHARE:-0.5}" --report "$CI_OUT/soak.json" | tee "$CI_OUT/soak.log" | tail -n 25
exit "${PIPESTATUS[0]}"
