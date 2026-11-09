#!/usr/bin/env bash
# The static gate: everything that can be known without running the product. (gate.sh of the review: lint, bandit, secrets, .env, docs, borrowed art.)
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
for t in actionlint shellcheck gitleaks zizmor pip-audit uv; do command -v "$t" >/dev/null || { echo "missing tool: $t (tools/ci/bootstrap_tools.sh; pip install -r tools/ci/requirements-ci.txt -r requirements.lock)"; exit 2; }; done

run actionlint actionlint -color=false
run zizmor zizmor --offline --min-severity medium .github
run shellcheck bash -c 'git ls-files "*.sh" "tools/pre-commit" | xargs shellcheck --severity=warning --shell=bash'
run ruff-pyflakes-node make lint
run bandit-no-medium-high make security
run pip-audit bash -c 'for _ in 1 2 3; do pip-audit -r requirements.lock --require-hashes --no-deps --disable-pip && exit 0; code=$?; [ $code -ne 1 ] || exit 1; sleep 20; done; exit 1'
run lock-drift bash -c '
  uv pip compile pyproject.toml --extra serve --generate-hashes --python-version 3.14 -o "$CI_OUT/runtime.lock" >/dev/null
  diff <(grep -E "^[a-z0-9_.-]+==" requirements-runtime.lock | sort) <(grep -E "^[a-z0-9_.-]+==" "$CI_OUT/runtime.lock" | sort)'
run gitleaks-history gitleaks git --config .gitleaks.toml --redact --no-banner .
# the tracked files as committed (a developer's untracked .env is not what ships): export them and scan that
run gitleaks-tracked bash -c 'rm -rf "$CI_OUT/tracked" && mkdir -p "$CI_OUT/tracked" && git archive HEAD | tar -x -C "$CI_OUT/tracked" && gitleaks dir --config .gitleaks.toml --redact --no-banner "$CI_OUT/tracked"'
run env-not-tracked bash -c '! git ls-files | grep -E "(^|/)\.env$"'
run no-local-state-tracked bash -c '! git ls-files | grep -E "\.pyc$|(^|/)__pycache__/|^eventmate/(plans|logs|wall_data|workspaces)/|\.(sqlite3|db)(-wal|-shm)?$|^solvers/|^\.hypothesis/|^ui/node_modules/|(^|/)owner_token$"'
run launch-docs-tracked bash -c 'for d in CHANGELOG.md docs/DECISIONS.md docs/HANDOFF.md docs/BLOCKED.md docs/LAUNCH_PLAN.md; do git ls-files --error-unmatch "$d" >/dev/null 2>&1 || { echo "$d is missing or untracked"; exit 1; }; done'
run borrowed-art-not-tracked bash -c '[ "$(git ls-files "eventmate/web/static/plan-story-*" "eventmate/web/static/plan-reference-*" "eventmate/web/static/landing-poster.jpg" "eventmate/web/static/landing-hero.mp4" | wc -l)" = 0 ]'
run static-provenance "$PY" -m pytest -q -p no:cacheprovider tests/test_static_provenance.py
finish static
