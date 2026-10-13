"""WP7 (spec 7.3): the documentation eval's gates on a subset of the cases, in the backend suite.

The full run (30 cases) is scripts/documentation_eval.py; here five cases cover a tempting note (the raw
hallucination the validator must catch), a Chinese-speaking warfarin patient, a procedure and a pending test.
"""

from __future__ import annotations

import pytest

from app.agents import registry
from app.aiops import evalrun, suites
from app.llm.gateway import get_gateway
from app.llm.providers import MOCK_FIXTURES, ProviderUnavailable
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


def test_a_document_the_model_cannot_draft_fails_its_case_not_the_suite(fresh_state, monkeypatch):
    """The 2026-10-12 live run: one summary's synthesis call timed out and the whole suite was recorded as error."""
    cases = {c["id"]: c for c in evals.load_cases()}
    monkeypatch.setattr(evals, "load_cases", lambda: [cases[i] for i in ("doc-02", "doc-04", "doc-05")])
    mock = MOCK_FIXTURES["discharge_summary"]

    def synthesis_down_for_doc_02(text, images, attempt):
        if "Stage: SYNTHESIS" in text and "ev-doc-02-stay" in text:
            raise ProviderUnavailable("timeout")
        return mock(text, images, attempt)

    monkeypatch.setitem(MOCK_FIXTURES, "discharge_summary", synthesis_down_for_doc_02)
    specs = [registry.require_agent(a) for a in suites.DOCUMENTATION.agents]
    out = evalrun.run_suite(suites.DOCUMENTATION, specs, gateway=get_gateway(), store=fresh_state, overrides={},
                            variant=None, live=False)
    assert all(entry["status"] != "error" for entry in out.values()), {a: e.get("error") for a, e in out.items()}
    results = {agent: {c["id"]: c for c in entry["case_results"]} for agent, entry in out.items()}
    assert all(set(r) == {"doc-02", "doc-04", "doc-05"} for r in results.values())

    summary = results["discharge_summary"]["doc-02"]
    assert summary["passed"] is False and summary["schema_valid"] is False
    assert summary["got"]["status"] == "failed" and "model unavailable (synthesis)" in summary["got"]["error"]
    instructions = results["patient_instructions"]["doc-02"]  # drafted from the signed summary: fails with it
    assert instructions["passed"] is False and instructions["schema_valid"] is False
    assert instructions["got"]["status"] == "skipped"
    assert results["nursing_handoff"]["doc-02"]["schema_valid"] is True  # the handoff does not need the summary

    # the other cases are measured; the gates are computed over them, schema validity counts the failure
    assert all(r[i]["schema_valid"] for r in results.values() for i in ("doc-04", "doc-05"))
    m, gates = out["discharge_summary"]["metrics"], out["discharge_summary"]["gates"]
    assert m["schema_validity"] == pytest.approx(2 / 3, abs=1e-4)
    assert gates["schema_validity"]["passed"] is False
    assert gates["untraceable_after_validator"]["passed"] and gates["validator_recall"]["passed"]
    assert gates["injected_blocked"]["passed"]  # from the next summary drafted (doc-04)
    assert out["patient_instructions"]["metrics"]["schema_validity"] == pytest.approx(2 / 3, abs=1e-4)
    assert out["nursing_handoff"]["metrics"]["schema_validity"] == 1.0
    assert out["nursing_handoff"]["gates"]["automated_proxies_mean_ge_4"]["passed"]


def test_a_summary_blocked_at_signing_stays_a_drafted_case_with_its_raw_metrics(fresh_state, monkeypatch):
    """Review finding 2: a draft the validator blocks at signing is 'unsigned', not 'failed' — its raw red and
    oracle items must stay in raw_hallucination_rate and validator_recall (the validator doing its job is not a
    model failure)."""
    cases = {c["id"]: c for c in evals.load_cases()}
    original_sign = evals.docs.sign

    def sign_blocks_the_physicians_summary(store, user, doc_id, request=None):
        if str(user.role) == "physician":  # the discharge summary; the nurse's instructions/handoff pass through
            raise evals.docs.DocProblem(f"Untraceable items block signing: {doc_id}")
        return original_sign(store, user, doc_id, request)

    monkeypatch.setattr(evals.docs, "sign", sign_blocks_the_physicians_summary)
    report = evals.run(fresh_state, [cases["doc-04"]])  # doc-04 has the tempting note -> raw red + oracle items
    m, row = report["metrics"], report["rows"][0]
    assert row["summary_status"] == "unsigned" and "Untraceable items block signing" in row["summary_error"]
    assert row["raw_red"] >= 1 and row["items"] > 0  # the drafted case kept its pre-validator data
    assert m["summaries_drafted"] == 1 and m["summaries_signed"] == 0
    assert m["fact_items_checked"] == row["items"] and m["raw_oracle_items"] >= 1
    assert m["raw_hallucination_rate"] is not None and m["validator_recall"] is not None
    assert m["untraceable_after_validator"] == 0  # nothing signed, but the gate must not pass vacuously
    assert report["gates"]["untraceable_after_validator"] is False
    assert report["gates"]["schema_validity"] is False
    assert row["instructions_status"] == "skipped" and row["handoff_status"] == "drafted"


def test_no_drafted_summary_fails_the_gates_instead_of_a_vacuous_pass(fresh_state, monkeypatch):
    """Review finding 3: with nothing drafted, _ratio(x, 0) == 1.0 and sum([]) == 0 must not report a perfect pass."""
    cases = {c["id"]: c for c in evals.load_cases()}

    def always_unavailable(text, images, attempt):
        raise ProviderUnavailable("timeout")

    monkeypatch.setitem(MOCK_FIXTURES, "discharge_summary", always_unavailable)
    report = evals.run(fresh_state, [cases["doc-02"]])
    m, g = report["metrics"], report["gates"]
    assert m["summaries_drafted"] == 0 and m["summaries_signed"] == 0
    assert m["validator_recall"] is None and m["schema_validity"] == 0.0
    assert g["validator_recall"] is False and g["untraceable_after_validator"] is False
    assert g["schema_validity"] is False and report["passed"] is False
    assert report["rows"][0]["summary_status"] == "failed"


def test_nursing_handoff_gate_follows_only_its_own_proxies(fresh_state, monkeypatch):
    """Review finding 1: when instructions all fail (None proxies), a good handoff must still pass its module gate
    (the gate must not take passed from the combined mean over all three document kinds)."""
    cases = {c["id"]: c for c in evals.load_cases()}

    def instructions_unavailable(text, images, attempt):
        raise ProviderUnavailable("timeout")

    monkeypatch.setitem(MOCK_FIXTURES, "patient_instructions", instructions_unavailable)
    monkeypatch.setattr(evals, "load_cases", lambda: [cases["doc-02"]])
    specs = [registry.require_agent(a) for a in suites.DOCUMENTATION.agents]
    out = evalrun.run_suite(suites.DOCUMENTATION, specs, gateway=get_gateway(), store=fresh_state, overrides={},
                            variant=None, live=False)
    assert all(e["status"] != "error" for e in out.values()), {a: e.get("error") for a, e in out.items()}
    assert out["patient_instructions"]["metrics"]["schema_validity"] < 1  # instructions failed
    assert out["nursing_handoff"]["gates"]["automated_proxies_mean_ge_4"]["passed"] is True  # the fix
