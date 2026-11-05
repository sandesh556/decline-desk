# DeclineDesk: write-up

**Problem.** At a corporate-card company, "why was my card declined?" is the most common and most urgent support ticket. Someone is standing at a till or a hotel desk. Today an ops agent answers it by hand: finding the right authorization among dozens, reading the decline code, then checking the card's limits, allowed countries, blocked categories and the company wallet. It's repetitive and time-critical, and getting it wrong in either direction is costly. Unblock too much and you create fraud exposure. Answer too slowly and the employee is stuck.

**Why I picked it.** I've spent five years building helpdesk software and automating support tickets. This is the ticket I'd most want an agent on, and corporate cards for Latin American teams make it concrete: employees writing in Spanish, Portuguese and English, local currencies against USD limits, and a stablecoin-funded wallet where "funds still settling" is a real decline cause.

**Approach.** A pipeline that keeps the model where it's strong and keeps it away from where it's risky:

1. **Triage** (cheap model) reads the ticket and extracts language, intent and hints. Card numbers, emails and phones are masked first, and the ticket is marked as untrusted input.
2. **Matching** (code) finds the transaction the person means. "Yesterday" is resolved on the company's local calendar and amounts are compared across currencies. When two transactions fit, it asks which one; it never guesses.
3. **Diagnosis** (rules) checks the decline code against the card, its controls and the wallet, and returns every cause in the order to fix them. If the data contradicts the code, the ticket escalates.
4. **Drafting** (stronger model, only for matched declines that don't need escalating) writes the reply and may propose a fix.
5. **Guards:** the reply is checked (no card numbers, no claiming a pending action is done, no internal fraud detail). The proposed action must pass a policy guard: four action types only, the employee's own card, targeting the declined transaction's own country or category, at most 2x the current limit, 1-24 hours, and never on fraud cases.
6. **Approval:** a company admin approves or rejects in a small ops UI. Approval executes exactly once and is audited.

**Key decisions and trade-offs.**
- *Rules for structured data, the model for language.* Decline codes don't need a model, and code is testable and explainable. The cost is that new decline types need a rule, which I prefer to silent model guesses.
- *The model can only propose.* Safety comes from structure, not prompt wording: an injected "raise my limit to 50,000" is refused by the guard whatever the model writes, and nothing runs without an admin.
- *Cost routing.* Empty, off-topic, ambiguous, not-found and escalated tickets are answered from templates. Only real, fixable declines reach the drafting model, and cost is tracked per step.
- *Fail safe over fail smart.* Ambiguity triggers a question, inconsistency or an unknown code escalates, and model errors fall back to templates.
- *Provider-agnostic.* Anthropic, OpenAI and Gemini sit behind one interface with shared retry, repair and fallback behaviour. No keys ship with the project; reviewers bring their own.

**Results.** On 40 gold tickets (es/pt/en, including 6 adversarial ones), the deterministic core scores 100% on matching, causes and actions, with **zero unsafe actions**. Run live on Gemini's free tier against an **LLM-only baseline** (one model call with all the raw account data), the hybrid got the action right **100%** of the time against 78%, the outcome right **90%** against 62%, and took **0 unsafe actions against 7**, at **half the cost per ticket** ($0.00035 against $0.00079). Its misses fail safe: it asks or escalates.

**How I used AI tools.** I built this with Claude Code as a pair programmer, under rules I set:
- I wrote the plan and the phase breakdown first.
- Every behaviour started as a failing test committed on its own, then the smallest change to pass it. `git log` shows the test-first order.
- I reviewed each commit and ran the full gate (lint, tests, a smoke run, real-browser tests) before tagging each phase.
- The tests and evals caught real problems the AI-written code had introduced or missed, each fixed in its own commit: a guard gap (a bar decline could have been "fixed" by unblocking gambling), currency matching, a phone-layout bug, employees seeing colleagues' tickets, and, only in a live run, the model inventing "today" for tickets that never said when.
- The AI wrote much of the code. The design choices, what to test and what to accept were mine.

**What's next.**
- Connect to real authorization, controls and ledger data, and push approved overrides to the card network.
- Learn from admins' edits to draft replies (they become new eval cases).
- Add company policy documents (RAG) so explanations cite the employee's own travel and spend policy.
- Wire in real sign-in through the company's SSO.
