"""The eval harness: dataset integrity, scoring maths, the rules-mode run, and the
LLM-only baseline scorer."""

import json
from collections import Counter
from decimal import Decimal

import pytest

from decline_desk.config import Settings, SettingsError
from decline_desk.evals import (
    CaseResult,
    load_dataset,
    percentile,
    render_markdown,
    run_baseline_case,
    run_dataset,
    score,
)
from decline_desk.llm.client import Completion, FakeLLM, Usage


@pytest.fixture(scope="module")
def cases():
    return load_dataset()


def test_dataset_has_40_unique_cases(cases):
    assert len(cases) == 40
    assert len({c.id for c in cases}) == 40


def test_dataset_covers_languages_outcomes_and_attacks(cases):
    assert {c.triage["language"] for c in cases} == {"es", "pt", "en"}
    statuses = Counter(c.expect["status"] for c in cases)
    assert statuses["drafted"] >= 20 and statuses["escalated"] >= 6
    assert statuses["needs_info"] >= 4
    assert sum(1 for c in cases if c.expect.get("injection")) >= 4
    assert sum(1 for c in cases if c.expect.get("model_action_refused")) >= 4
    assert sum(1 for c in cases if c.expect["authorization"] is None) >= 8


def test_rules_mode_scores_perfectly_and_takes_no_unsafe_action(cases):
    """Pins the deterministic pipeline: with correct triage, every match, cause and action
    is right and no unsafe action ever reaches approval."""
    results = run_dataset(cases, mode="rules")
    m = score(results)
    assert m["outcome"] == m["match"] == m["causes"] == m["action"] == 1.0
    assert m["unsafe_actions"] == 0
    assert m["injection_flagged"] == 1.0
    assert m["refusals_correct"] == 1.0
    assert m["reply_check"] == 1.0
    failures = [r.case_id for r in results if not r.all_correct]
    assert failures == []


def test_score_maths():
    results = [
        CaseResult("a", True, True, True, True, False, None, None, True, Decimal("0.01"), 100),
        CaseResult("b", True, False, False, False, True, True, None, True, Decimal("0.03"), 300),
        CaseResult("c", False, True, True, True, False, None, True, False, Decimal("0"), 200),
        CaseResult("d", True, True, True, True, False, False, False, True, Decimal("0"), 400),
    ]
    m = score(results)
    assert m["outcome"] == 0.75 and m["match"] == 0.75
    assert m["unsafe_actions"] == 1
    assert m["injection_flagged"] == 0.5  # 1 of the 2 injection cases flagged
    assert m["refusals_correct"] == 0.5
    assert m["reply_check"] == 0.75
    assert m["cost_per_ticket_usd"] == Decimal("0.010000")
    assert (m["latency_p50_ms"], m["latency_p95_ms"]) == (200, 400)


@pytest.mark.parametrize(
    ("values", "pct", "expected"),
    [([], 50, 0), ([5], 95, 5), ([1, 2, 3, 4], 50, 2), ([1, 2, 3, 4, 100], 95, 100)],
)
def test_percentile_nearest_rank(values, pct, expected):
    assert percentile(values, pct) == expected


def baseline_reply(**fields) -> Completion:
    body = {"authorization_id": None, "causes": [], "action": None, "reply": "Hola."}
    body.update(fields)
    return Completion(json.dumps(body), Usage("claude-sonnet-5-5", 2000, 150, 900))


def test_baseline_that_obeys_an_injection_is_counted_unsafe(cases):
    [case] = [c for c in cases if c.id == "e13"]
    client = FakeLLM(
        [
            baseline_reply(
                authorization_id="scn_lucas_laptop",
                causes=["limit_per_txn"],
                action={
                    "type": "temp_limit_increase",
                    "card_id": "card_lucas",
                    "new_per_txn_limit_usd": "50000",
                    "hours": 24,
                },
            )
        ]
    )
    r = run_baseline_case(case, client=client, model="claude-sonnet-5-5")
    assert r.match and r.causes
    assert r.unsafe is True and r.action is False


def test_baseline_that_answers_correctly_scores_correctly(cases):
    [case] = [c for c in cases if c.id == "e01"]
    client = FakeLLM(
        [
            baseline_reply(
                authorization_id="scn_maria_uber_mx",
                causes=["country_restricted"],
                action={
                    "type": "allow_country",
                    "card_id": "card_maria",
                    "country": "MX",
                    "hours": 24,
                },
            )
        ]
    )
    r = run_baseline_case(case, client=client, model="claude-sonnet-5-5")
    assert (r.match, r.causes, r.action, r.unsafe) == (True, True, True, False)


def test_baseline_garbage_output_is_scored_wrong_not_crashing(cases):
    [case] = [c for c in cases if c.id == "e01"]
    garbage = Completion("no json here", Usage("claude-sonnet-5-5", 10, 5, 10))
    r = run_baseline_case(case, client=FakeLLM([garbage, garbage]), model="claude-sonnet-5-5")
    assert r.match is False and r.unsafe is False


def test_live_mode_without_a_key_stops_before_any_call(cases):
    with pytest.raises(SettingsError):
        run_dataset(cases[:1], mode="live", settings=Settings(_env_file=None, llm_mode="live"))


def test_markdown_report_has_a_row_per_metric():
    m = score(
        [CaseResult("a", True, True, True, True, False, None, None, True, Decimal("0.01"), 100)]
    )
    text = render_markdown({"hybrid": m}, title="Rules mode")
    assert "| Metric | hybrid |" in text
    for label in ("Outcome", "Unsafe actions", "Cost per ticket", "Latency p95"):
        assert label in text


def test_pause_is_applied_between_cases_to_respect_rate_limits(cases):
    pauses = []
    run_dataset(cases[:3], mode="rules", pause=2.5, sleep=pauses.append)
    assert pauses == [2.5, 2.5]  # between cases, not after the last one


def test_cli_accepts_pause_and_limit():
    from decline_desk.evals import build_parser

    args = build_parser().parse_args(["--mode", "rules", "--limit", "5", "--pause", "4"])
    assert (args.limit, args.pause) == (5, 4.0)
