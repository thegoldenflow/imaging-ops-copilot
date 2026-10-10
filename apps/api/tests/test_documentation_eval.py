"""WP7 (spec 7.3): the documentation eval's gates on a subset of the cases, in the backend suite.

The full run (30 cases) is scripts/documentation_eval.py; here five cases cover a tempting note (the raw
hallucination the validator must catch), a Chinese-speaking warfarin patient, a procedure and a pending test.
"""

from __future__ import annotations

from app.modules.documentation import evals


def test_the_eval_gates_hold_on_a_subset(fresh_state):
    cases = {c["id"]: c for c in evals.load_cases()}
    picked = [cases[i] for i in ("doc-02", "doc-04", "doc-05", "doc-09", "doc-15")]
    report = evals.run(fresh_state, picked)
    m = report["metrics"]
    assert m["summaries_signed"] == len(picked)
    assert m["raw_untraceable_items"] >= 1  # the tempting notes of doc-04 and doc-15
    assert m["untraceable_after_validator"] == 0 and m["validator_recall"] == 1.0
    assert m["injected_blocked"] == 5
    assert report["consistency"] and all(c["consistent"] for c in report["consistency"])
    assert m["zh_no_abbreviations"] == 1.0 and m["zh_terms_explained"] == 1.0
    assert all(v >= 4 for p in m["automated_proxies"].values() for v in p.values())
