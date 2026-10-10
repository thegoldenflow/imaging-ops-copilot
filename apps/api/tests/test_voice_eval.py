"""WP8 eval gates in the backend suite (spec 7.4): the red-flag rules catch all 15 red-flag scripts without the model,
identity failures are put through to a person, and the eval sets have the size and shape the spec asks for.

The full reports come from scripts/voice_eval.py (evals/call_center, evals/followup, evals/voice_bilingual).
"""

from __future__ import annotations

from app.modules.voice_services import evals


def test_the_eval_sets_have_the_spec_sizes():
    calls, followups, bilingual = evals.load("call_center"), evals.load("followup"), evals.load("voice_bilingual")
    assert len(calls) == 40 and {c["intent"] for c in calls} == {"reschedule", "cancel", "lookup", "faq", "prep",
                                                                  "clinical", "transfer"}
    assert sum(c["category"] == "identity_failure" for c in calls) == 6
    assert len(followups) == 50 and sum(c["red_flag"] for c in followups) == 15
    assert len(bilingual) == 20 and sum(c["language"] == "zh" for c in bilingual) == 10


def test_the_red_flag_rules_catch_all_15_scripts_without_the_model_and_recall_meets_the_gate(fresh_state):
    report = evals.run_followups(fresh_state, [c for c in evals.load("followup") if c["red_flag"]])
    assert report["rules_only"]["escalated_by_rules"] == report["rules_only"]["cases"] == 15
    assert report["recall"] >= evals.RECALL_GATE
    assert all(set(r["expected_codes"]) <= set(r["rule_codes"]) for r in report["results"])


def test_near_misses_do_not_trip_the_rules(fresh_state):
    near = [c for c in evals.load("followup") if not c["red_flag"] and c["note"] in
            ("low reading", "negations", "light drainage", "negated fall")]
    report = evals.run_followups(fresh_state, near)
    assert len(near) == 8 and report["false_rule_hits"] == []


def test_identity_failures_go_to_a_person_and_intents_are_recognised(fresh_state):
    cases = evals.load("call_center")
    sample = [c for c in cases if c["category"] == "identity_failure"] + \
        [c for c in cases if c["category"] == "intent"][::3]
    report = evals.run_call_center(fresh_state, sample)
    assert report["identity_failures_transferred"] == 1.0
    assert report["intent_accuracy"] >= evals.INTENT_GATE
