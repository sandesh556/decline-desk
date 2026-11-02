.PHONY: setup test lint fmt hooks smoke check

setup:            ## install deps and git hooks
	uv sync
	$(MAKE) hooks

test:             ## run the test suite with coverage
	uv run pytest --cov --cov-report=term-missing

lint:             ## lint and format check
	uv run ruff check .
	uv run ruff format --check .

fmt:              ## auto-format
	uv run ruff check --fix .
	uv run ruff format .

hooks:            ## install the pre-commit secret scan
	cp scripts/pre-commit .git/hooks/pre-commit
	chmod +x .git/hooks/pre-commit

smoke:            ## end-to-end run on seeded data (the "vibe test")
	uv run python scripts/smoke.py

check: lint test smoke  ## everything that must pass before a phase tag
