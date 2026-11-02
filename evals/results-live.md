## Live mode: gemini (`gemini-3.5-flash-lite` for triage and drafting), 40 tickets

Run on 2026-11-02 with `make eval-live` on Gemini's free tier. `gemini-3.8-flash`
(the default drafting model) was returning 503 "high demand" at the time, so
`RESOLVER_MODEL=gemini-3.5-flash-lite` was used for both steps. "n/a" = not measurable
in that column (the baseline has no injection flag or guard; refusals are only measured
in rules mode, where the model is scripted to propose bad actions).

| Metric | hybrid | LLM-only |
|---|---|---|
| Outcome (status) correct | 90% | 62% |
| Matched the right transaction | 82% | 80% |
| Causes correct | 82% | 65% |
| Action correct | 100% | 78% |
| Unsafe actions | 0 | 7 |
| Injection attempts flagged | 100% | n/a |
| Bad model actions refused | n/a | n/a |
| Reply passes the checker | 100% | 98% |
| Cost per ticket (USD) | $0.000350 | $0.000793 |
| Latency p50 (ms) | 2089 | 1520 |
| Latency p95 (ms) | 9358 | 9045 |
