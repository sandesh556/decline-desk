# DeclineDesk

An ops agent for the most common support ticket at a corporate-card company:
**"Why was my card declined?"**

It reads the ticket (Spanish, Portuguese or English), finds the declined authorization the
person means, diagnoses the cause with deterministic rules plus an LLM, drafts a reply in
their language, and **proposes** a fix that an admin must approve. The LLM never acts on
its own.

> Work in progress. Built test-first; see `git log` for the step-by-step history.

## Quickstart

```bash
uv sync
cp .env.example .env      # LLM_MODE=fake works with no API key
make test
```
