#!/usr/bin/env bash
# The suite on every supported Python (pyproject says >=3.12; CI runs 3.12, 3.13 and 3.14). Both the author's gate and the reviewer's ran only on 3.14 once, and a privacy test that fails on 3.12 (an older
# SQLite) went unseen. `make ci` runs the main interpreter fully; this runs the same tests gate (tools, inventory, suite, skip guard, clean tree) on each OTHER version, each in its own venv built by uv
# from requirements.lock, with its own output directory. A version that cannot be set up is a FAILURE, not a pass: MATRIX_SKIP_MISSING=1 turns it into a loud "NOT RUN" (an offline laptop).
#   MATRIX_VERSIONS="3.12 3.13 3.14"   which versions (default: those three)
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
versions="${MATRIX_VERSIONS:-3.12 3.13 3.14}"
here="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
venvs="${MATRIX_VENVS:-${TMPDIR:-/tmp}/todoh-matrix-$(printf '%s' "$ROOT" | sha256sum | cut -c1-10)}"
ran=()
notrun=()
for v in $versions; do
  if [ "$v" = "$here" ]; then step "py$v" "this is the main interpreter: make ci-tests runs it in full"; continue; fi
  venv="$venvs/py$v"
  if ! [ -x "$venv/bin/python" ]; then
    if ! { uv venv --quiet --python "$v" --seed "$venv" > "$CI_OUT/matrix-venv-$v.log" 2>&1; }; then
      if [ -n "${MATRIX_SKIP_MISSING:-}" ]; then step "py$v" "NOT RUN: uv could not provide Python $v (MATRIX_SKIP_MISSING is set)"; notrun+=("$v"); continue; fi
      step "py$v" "FAIL: uv could not provide Python $v (log: $CI_OUT/matrix-venv-$v.log); set MATRIX_SKIP_MISSING=1 to accept NOT RUN"; tail -n 5 "$CI_OUT/matrix-venv-$v.log" | sed 's/^/    | /'; FAILS=$((FAILS + 1)); continue
    fi
  fi
  # the environment, exactly as `make ci-tools` builds it (the lock, the package, the documented fli commit), in this version's own venv
  if ! { "$venv/bin/python" -m pip install -q --require-hashes -r requirements.lock && "$venv/bin/python" -m pip install -q --no-deps -e . && PYTHON="$venv/bin/python" PATH="$venv/bin:$PATH" tools/ci/install_flights.sh; } > "$CI_OUT/matrix-install-$v.log" 2>&1; then
    step "py$v" "FAIL: the environment could not be built (log: $CI_OUT/matrix-install-$v.log)"; tail -n 5 "$CI_OUT/matrix-install-$v.log" | sed 's/^/    | /'; FAILS=$((FAILS + 1)); continue
  fi
  echo "=== tests on Python $v ($("$venv/bin/python" -c 'import sqlite3,sys; print(sys.version.split()[0], "sqlite", sqlite3.sqlite_version)'))"
  if PYTHON="$venv/bin/python" PATH="$venv/bin:$PATH" CI_OUT="$CI_OUT/py$v" tools/ci/tests.sh; then step "py$v" PASS; ran+=("$v"); else step "py$v" "FAIL (output: $CI_OUT/py$v)"; FAILS=$((FAILS + 1)); fi
done
[ "${#notrun[@]}" -eq 0 ] || echo "NOT RUN: Python ${notrun[*]} (the gate did not cover them)"
echo "matrix: ran ${ran[*]:-none} besides the main interpreter $here"
finish matrix
