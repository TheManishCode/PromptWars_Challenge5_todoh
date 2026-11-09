#!/usr/bin/env bash
# The review gate as one command (the reviewer's gate.sh, ported): check out ONE committed sha in a clean worktree and run the whole gate on it, so what is checked is what was committed and
# nothing untracked can hide a problem. `make ci` is the same gate on the current tree.
#   tools/ci/gate.sh [SHA] [--quick]      --quick: static, ui and mutants only (no full test suite)
# The solvers are not in git: they are linked in from CI_SOLVERS or ./solvers, as a clean clone would after building them (tools/ci/solvers.sh).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
SHA="${1:-HEAD}"; QUICK="${2:-}"
[ "$SHA" = --quick ] && { SHA=HEAD; QUICK=--quick; }
WT="$(mktemp -d)/wt"
git -C "$ROOT" worktree add -q --detach "$WT" "$SHA" || { echo "cannot check out $SHA"; exit 2; }
trap 'git -C "$ROOT" worktree remove --force "$WT" >/dev/null 2>&1' EXIT
[ -e "${CI_SOLVERS:-$ROOT/solvers}" ] && ln -s "${CI_SOLVERS:-$ROOT/solvers}" "$WT/solvers"
[ -d "$ROOT/.ci-tools" ] && ln -s "$ROOT/.ci-tools" "$WT/.ci-tools"
echo "gate on $(git -C "$WT" rev-parse --short HEAD): $(git -C "$WT" log -1 --format=%s | cut -c1-70)"
if [ -n "$(git -C "$WT" status --porcelain --untracked-files=all | grep -v ' solvers$\| \.ci-tools$')" ]; then echo "the fresh checkout is not clean"; exit 1; fi
cd "$WT" || exit 2
export PYTHONPATH="$WT"
if [ "$QUICK" = --quick ]; then make ci-static ci-ui ci-mutants; else make ci; fi
