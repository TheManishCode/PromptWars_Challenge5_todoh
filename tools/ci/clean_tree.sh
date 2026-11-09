#!/usr/bin/env bash
# Nothing the tests or the builds do may write into the repository. `snapshot` records what `git status` says now; `verify` fails if it has changed (so a test that leaves a file behind, or a
# build that rewrites a tracked file, is caught). In CI the snapshot is empty (a fresh checkout), so the tree must be exactly clean; on a laptop with work in progress only NEW changes count.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
f="$CI_OUT/tree.snapshot"
status() { git status --porcelain --untracked-files=all | sort; }
case "${1:-verify}" in
  snapshot) status > "$f"; echo "tree snapshot: $(wc -l < "$f") entries";;
  verify)
    [ -f "$f" ] || { echo "no snapshot: run 'tools/ci/clean_tree.sh snapshot' first"; exit 2; }
    new="$(comm -13 "$f" <(status))"
    if [ -n "$new" ]; then echo "the tree changed while the gate ran (a test or build wrote into the repository):"; echo "$new" | sed 's/^/    /'; exit 1; fi
    echo "clean: nothing was written into the tree";;
  *) echo "usage: clean_tree.sh snapshot|verify"; exit 2;;
esac
