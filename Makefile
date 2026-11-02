.PHONY: setup test lint fmt hooks smoke check demo e2e

setup:            ## install deps and git hooks
	uv sync
	$(MAKE) hooks

test:             ## run the test suite with coverage (browser tests excluded)
	uv run pytest --ignore=tests/e2e --cov --cov-report=term-missing

e2e:              ## real browser tests against a live local server
	uv run pytest tests/e2e -p no:randomly

demo:             ## serve the ops UI on http://127.0.0.1:8000 (fresh demo data)
	uv run uvicorn decline_desk.web.main:app --host 127.0.0.1 --port 8000

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

check: lint test smoke e2e  ## everything that must pass before a phase tag
