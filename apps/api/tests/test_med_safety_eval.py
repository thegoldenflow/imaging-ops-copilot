"""WP6 (spec 7.2): the medication-safety evals as a gate in the backend suite (the mock model; see the reports).

Order review: 60 labelled synthetic cases, commentary schema-valid and grounded on every case, precision >= 0.80 and
recall >= 0.85 of the flagged concern types; "new without indication" reported separately. Reconciliation: 20
cases, row classification accuracy >= 0.90. The mock is written by the author of the labels, so these numbers are
optimistic; they keep the pipeline honest (schema, guards, evidence, queue), not a model's quality.
"""

from app.modules.med_safety import evals
from app.modules.med_safety import service as S


def test_eval_sets_are_complete():
    order_cases, medrec_cases = evals.load_cases("order_review"), evals.load_cases("medrec")
    assert len(order_cases) == 60 and len({c["id"] for c in order_cases}) == 60
    assert len(medrec_cases) == 20
    assert {c["expected"]["concerns"] == [] for c in order_cases} == {True, False}  # negatives and positives
    assert sum("indicated" in c["expected"] for c in order_cases) >= 40
    assert {t for c in medrec_cases for t in c["expected"]["items"].values()} == {
        "match", "omission", "dose_change", "duplicate_therapy", "new", "new_without_indication"}


def test_order_review_commentary_meets_the_gates(fresh_state):
    S.reset_state()
    report = evals.run_order_review(fresh_state, evals.load_cases("order_review"))
    m = report["metrics"]
    assert m["schema_validity"] == 1.0 and m["grounding_rate"] == 1.0
    assert m["precision"] >= 0.80 and m["recall"] >= 0.85, m
    assert m["new_without_indication"]["cases"] >= 30
    assert report["passed"]


def test_medication_reconciliation_meets_the_gate(fresh_state):
    S.reset_state()
    report = evals.run_medrec(fresh_state, evals.load_cases("medrec"))
    assert report["metrics"]["accuracy"] >= 0.90, report["results"]
    assert all(r["document"] for r in report["results"])  # every reconciliation has its draft record
