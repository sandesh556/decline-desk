.PHONY: setup browsers test lint fmt hooks smoke check demo e2e eval eval-live

setup:            ## install deps, the test browser and git hooks
	uv sync
	$(MAKE) browsers
	$(MAKE) hooks

browsers:         ## download the headless Chromium used by the browser tests
	uv run playwright install chromium

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

eval:             ## score the 40 gold tickets (scripted model, no key needed)
	uv run python -m decline_desk.evals --mode rules

eval-live:        ## same tickets with your provider + the LLM-only baseline (needs a key)
	uv run python -m decline_desk.evals --mode live --baseline --pause 4
