#!/usr/bin/env bash
# Show each guard FIRE once, in a throwaway worktree of HEAD (nothing in your tree is touched): a guard that has never been seen to fail is a hope.
#   1. skip guard      a test that skips for a missing tool, not on the allow-list      -> must fail
#   2. ui diff         eventmate/web/static/plan.js edited by hand                      -> must fail
#   3. clean tree      a "test" that writes a file into the repository                  -> must fail
#   4. mutant survives the test that guards a bug is neutered                          -> the mutants gate must fail
#   5. new borrowed art / tracked .env / unpinned action                                -> the static checks must fail
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
WT="$(mktemp -d)/wt"
git worktree add -q --detach "$WT" HEAD
trap 'git worktree remove --force "$WT" >/dev/null 2>&1' EXIT
[ -e "${CI_SOLVERS:-$ROOT/solvers}" ] && ln -s "${CI_SOLVERS:-$ROOT/solvers}" "$WT/solvers"
[ -d "$ROOT/.ci-tools" ] && ln -s "$ROOT/.ci-tools" "$WT/.ci-tools"
cd "$WT" || exit 2
export CI_ROOT="$WT" PYTHONPATH="$WT" CI_OUT="$CI_OUT/demos"; mkdir -p "$CI_OUT"
expect_fail() {  # expect_fail NAME CMD...: the command must FAIL; show its last lines
  local name="$1"; shift
  if "$@" > "$CI_OUT/$name.log" 2>&1; then step "$name" "NOT CAUGHT (the guard passed)"; FAILS=$((FAILS + 1)); else step "$name" "caught (as it should)"; tail -n 4 "$CI_OUT/$name.log" | sed 's/^/    | /'; fi
}
git config user.email demo@example.test; git config user.name demo

# 1. an unexpected skip
cat > tests/test_zz_demo_skip.py <<'PY'
import pytest

def test_needs_a_tool_that_is_missing():
    pytest.skip("nginx is not installed here")
PY
"$PY" -m pytest -q -p no:cacheprovider -rs --junitxml="$CI_OUT/demo-junit.xml" tests/test_zz_demo_skip.py >/dev/null 2>&1
expect_fail skip-guard "$PY" tools/ci/skip_guard.py "$CI_OUT/demo-junit.xml"
rm tests/test_zz_demo_skip.py

# 2. plan.js touched by hand (the build then rewrites it: the tree differs from before the build)
echo "// edited by hand" >> eventmate/web/static/plan.js
expect_fail ui-diff tools/ci/ui.sh
git checkout -q -- eventmate/web/static

# 3. a test that writes into the repository
tools/ci/clean_tree.sh snapshot >/dev/null
echo '{}' > eventmate/left_behind_by_a_test.json
expect_fail clean-tree tools/ci/clean_tree.sh verify
rm eventmate/left_behind_by_a_test.json

# 4. a mutant survives because the test that should catch the bug cannot fail
sed -i 's/^def test_one_plan_cannot_send_more_first_requests_than_its_cap_in_a_day_and_another_plan_still_can(em, pid, chans, clock):$/def test_one_plan_cannot_send_more_first_requests_than_its_cap_in_a_day_and_another_plan_still_can(em, pid, chans, clock):\n    return  # neutered/' tests/test_outbound_safety.py
git -c core.hooksPath=/dev/null commit -qam "demo: neuter the cap test"
expect_fail mutant-survives tools/ci/mutants.py --only per-plan-cap-off
git reset -q --hard HEAD~1

# 5. static: a tracked .env, an action not pinned to a sha, a borrowed image
echo "SECRET=1" > .env; git add -f .env
expect_fail env-tracked bash -c '! git ls-files | grep -E "(^|/)\.env$"'
git rm -q --cached -f .env; rm -f .env
sed -i '0,/actions\/checkout@[0-9a-f]\{40\}/s//actions\/checkout@v4/' .github/workflows/ci.yml
expect_fail unpinned-action "$PY" -m pytest -q -p no:cacheprovider tests/test_ci_guards.py -k pins_every_action
git checkout -q -- .github
touch eventmate/web/static/plan-story-knot.webp; git add -f eventmate/web/static/plan-story-knot.webp
expect_fail borrowed-art bash -c '[ "$(git ls-files "eventmate/web/static/plan-story-*" | wc -l)" = 0 ]'
git rm -q --cached eventmate/web/static/plan-story-knot.webp; rm -f eventmate/web/static/plan-story-knot.webp
# 6. a BARE act, run from a checkout that has a .env it cannot parse, must print no value of it (the .actrc points it at empty files inside the project: act ignores /dev/null from a project .actrc). Without .actrc the same run prints the value: the control.
ACT="$ROOT/.ci-tools/bin/act"
if [ -x "$ACT" ]; then
  FAKE="NOT-A-REAL-SECRET-0123456789"
  printf 'this line is not valid dotenv @@ %s\n' "$FAKE" > .env
  out="$("$ACT" --list 2>&1)"; if echo "$out" | grep -q "$FAKE"; then step act-bare-with-actrc "LEAKED the value"; FAILS=$((FAILS + 1)); else step act-bare-with-actrc "caught (as it should): no value printed"; fi
  mv .actrc "$CI_OUT/actrc.bak"; out="$("$ACT" --list 2>&1)"; mv "$CI_OUT/actrc.bak" .actrc
  if echo "$out" | grep -q "$FAKE"; then step act-bare-without-actrc "control: without .actrc act DOES print the value (this is why .actrc exists)"; else step act-bare-without-actrc "NOT A CONTROL: act printed nothing even without .actrc"; FAILS=$((FAILS + 1)); fi
  expect_fail act-wrapper-refuses-near-a-env bash -c 'cd "$1" && "$2/tools/ci/act.sh" ui' _ "$WT" "$WT"
  rm -f .env
else step act-demos "skipped: act is not installed (tools/ci/bootstrap_tools.sh --act)"; fi
finish guard-demos
