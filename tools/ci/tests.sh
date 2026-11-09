#!/usr/bin/env bash
# The test gate. Tools first (a missing one must fail, not skip), then the whole suite with skip reasons and a junit file, then the skip guard and the clean-tree check.
#   COVERAGE=1  also measure coverage and fail under 94 (CI does this on the newest Python only)
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# two suites at once in one checkout share ports, files and the output directory: refuse rather than report a mixed result
exec 9> "$CI_OUT/.tests.lock"
flock -n 9 || { echo "another test run is already using $CI_OUT: wait for it (or use a different CI_OUT)"; exit 1; }
export HYPOTHESIS_PROFILE="${HYPOTHESIS_PROFILE:-ci}"  # derandomised: a failing property is reproducible from the log
run require-tools tools/ci/require_tools.sh
[ "$FAILS" -eq 0 ] || finish tests
run test-inventory "$PY" tools/ci/test_inventory.py
cat "$CI_OUT/test-inventory.log" | sed 's/^/    | /'
tools/ci/clean_tree.sh snapshot >/dev/null
args=(-q -p no:cacheprovider -rs "--junitxml=$CI_OUT/junit.xml")
[ -n "${COVERAGE:-}" ] && args+=(--cov=eventmate --cov-report=term-missing:skip-covered "--cov-report=xml:$CI_OUT/coverage.xml" --cov-fail-under=94)
# shellcheck disable=SC2086
run pytest "$PY" -m pytest "${args[@]}" ${PYTEST_EXTRA:-}
tail -n 3 "$CI_OUT/pytest.log" | sed 's/^/    | /'
run skip-guard "$PY" tools/ci/skip_guard.py "$CI_OUT/junit.xml"
cat "$CI_OUT/skip-guard.log" | sed 's/^/    | /'
run clean-tree tools/ci/clean_tree.sh verify
finish tests
