.PHONY: install test lint security check web redteam deploy-check ui hooks ci ci-full ci-tools ci-static ci-ui ci-solvers ci-tests ci-matrix ci-mutants ci-deploy-check ci-soak ci-gate ci-release post-deploy-smoke
install:
	pip install -e ".[dev]"
test:
	python -m pytest
lint:
	python -m pyflakes eventmate tests
	ruff check eventmate tests tools
	node --check eventmate/web/static/app.js
	node --check eventmate/web/static/landing.js
security:
	python -m bandit -q -r eventmate -ll
check: lint security test
web:
	python -m eventmate.web

# the planning page (/plan) is built from ui/ into eventmate/web/static (plan.js, plan.css, plan-*.webp); the build is committed, so a deploy needs no Node
ui:
	cd ui && npm ci && npx vitest run && npx vite build

redteam:
	python -m pytest -q tests/pentest

# what can be checked before a real host: the reference proxy configuration in front of the real application, then the container image
deploy-check:
	python -m pytest -q tests/test_proxy_deploy.py tests/pentest/test_proxy_smuggling.py
	tools/check_image.sh

# refuses a commit that stages .env, run-time data, caches, solvers or a secret (tools/pre-commit)
hooks:
	cp tools/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

# The gate (tools/ci/): `make ci` is exactly what the workflows run, step by step; `make ci-full` adds the container checks and the soak. docs/CI.md says what each one catches.
ci-tools:
	# the environment first (the lock has fli's own dependencies, babel and others), then the tools, then the documented fli commit on top: one sequence whatever was installed before
	pip install --require-hashes -r requirements.lock && pip install --no-deps -e . && tools/ci/bootstrap_tools.sh && pip install -r tools/ci/requirements-ci.txt && tools/ci/install_flights.sh
ci-static:
	tools/ci/static.sh
ci-ui:
	tools/ci/ui.sh
ci-solvers:
	tools/ci/solvers.sh build
ci-tests:
	tools/ci/tests.sh
# the same tests on the other supported Pythons (3.12, 3.13, 3.14 minus the main one), each in its own uv venv: tools/ci/matrix.sh
ci-matrix:
	tools/ci/matrix.sh
ci-mutants:
	tools/ci/mutants.py
ci-deploy-check:
	tools/ci/deploy_check.sh
ci-soak:
	tools/ci/soak.sh
ci:
	tools/ci/clean_tree.sh snapshot && $(MAKE) -k --no-print-directory ci-static ci-ui ci-solvers ci-tests ci-matrix ci-mutants
ci-full: ci
	$(MAKE) --no-print-directory ci-deploy-check ci-soak
# the review gate on one committed sha, in a clean worktree: tools/ci/gate.sh [SHA] [--quick]
ci-gate:
	tools/ci/gate.sh
ci-release:
	@test -n "$(TAG)" || { echo "usage: make ci-release TAG=v1.2.3"; exit 2; }
	tools/ci/release.sh $(TAG)
post-deploy-smoke:
	@test -n "$(URL)" -a -n "$(HOST)" || { echo "usage: make post-deploy-smoke URL=http://127.0.0.1:8000 HOST=todoh.example"; exit 2; }
	tools/ci/post_deploy_smoke.sh $(URL) $(HOST)
