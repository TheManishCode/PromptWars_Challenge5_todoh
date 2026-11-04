.PHONY: install test lint security check web redteam deploy-check
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

redteam:
	python -m pytest -q tests/pentest

# what can be checked before a real host: the reference proxy configuration in front of the real application, then the container image
deploy-check:
	python -m pytest -q tests/test_proxy_deploy.py tests/pentest/test_proxy_smuggling.py
	tools/check_image.sh
