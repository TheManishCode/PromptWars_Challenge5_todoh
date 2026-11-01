.PHONY: install test lint security check web redteam
install:
	pip install -e ".[dev]"
test:
	python -m pytest
lint:
	python -m pyflakes eventmate tests
	ruff check eventmate tests tools
	node --check eventmate/web/static/arcade.js
	node --check eventmate/web/static/arcade3d.js
security:
	python -m bandit -q -r eventmate -ll
check: lint security test
web:
	python -m eventmate.web

redteam:
	python -m pytest -q tests/pentest
