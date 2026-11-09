#!/usr/bin/env bash
# The committed /plan build must be what ui/ builds to (R-006, L1): otherwise the code that was reviewed is not the code that is served. npm ci, vitest, vite build, then no difference in
# eventmate/web/static (tracked or new files).
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
before="$(git status --porcelain --untracked-files=all -- eventmate/web/static | sort)"
run ui-install bash -c 'cd ui && npm ci --no-audit --no-fund'
run ui-vitest bash -c 'cd ui && npx vitest run'
run ui-build bash -c 'cd ui && npx vite build'
after="$(git status --porcelain --untracked-files=all -- eventmate/web/static | sort)"
if [ "$before" = "$after" ] && git diff --quiet -- eventmate/web/static; then step ui-reproduces ok
elif [ "$before" = "$after" ]; then step ui-reproduces "ok (the tree already differed from HEAD before the build; run in a clean checkout for the strict check)"
else step ui-reproduces "FAIL: the build changed eventmate/web/static (commit the rebuilt files)"; git diff --stat -- eventmate/web/static | sed 's/^/    | /'; comm -13 <(echo "$before") <(echo "$after") | sed 's/^/    | /'; FAILS=$((FAILS + 1)); fi
finish ui
