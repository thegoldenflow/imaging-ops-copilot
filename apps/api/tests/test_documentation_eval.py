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


# ---------- the handoff proxies: one defect, one proxy ----------


HANDOFF_SOURCES = {
    "Encounter/e1": ("encounter", "Stay e1, admitted 2026-10-10, day 4 of the stay"),
    "Observation/v1": ("observation", "Vital signs 21:00: heart rate 80 /min, SpO2 95 %, pain score 3"),
    "Observation/v2": ("observation", "Vital signs 05:00: heart rate 76 /min, SpO2 96 %, pain score 1"),
    "Task/t1": ("task", "Teach the inhaler"),
    "Flag/f1": ("flag", "Fall risk"),
}


def _handoff_view(**changes) -> dict:
    """A clean SBAR (every proxy 5.0) validated by the real HandoffValidator, with sections replaced by `changes`."""
    from app.modules.documentation.facts import Source
    from app.modules.documentation.validator import HandoffValidator

    content = {
        "situation": {"text": "Day 4 of the stay, heart rate 76 /min and SpO2 96 % at 05:00; fall risk.",
                      "source_refs": ["Encounter/e1", "Observation/v2", "Flag/f1"]},
        "background": [{"text": "Admitted on 2026-10-10.", "source_refs": ["Encounter/e1"]}],
        "assessment": [{"text": "Heart rate 80 /min then 76 /min.", "source_refs": ["Observation/v1", "Observation/v2"]},
                       {"text": "Pain score 3 then 1.", "source_refs": ["Observation/v1", "Observation/v2"]}],
        "recommendation": [{"text": "Teach the inhaler.", "source_refs": ["Task/t1"]},
                           {"text": "Keep fall precautions.", "source_refs": ["Flag/f1"]}],
        "not_recorded": ["intake_output"]}
    content.update(changes)
    sources = {ref: Source(ref, ref.split("/")[0], kind, name=detail, detail=detail)
               for ref, (kind, detail) in HANDOFF_SOURCES.items()}
    report = HandoffValidator(sources).validate(content)
    return {"content": content, "validation": report.model_dump(mode="json"),
            "facts": {"tasks": [{"id": "Task/t1"}], "flags": [{"id": "Flag/f1"}]}}


def test_handoff_proxies_score_a_clean_handoff_five():
    assert evals._handoff_proxies(_handoff_view()) == {"completeness": 5.0, "accuracy": 5.0, "readability": 5.0,
                                                        "format": 5.0}


def test_an_uncited_statement_costs_accuracy_only():
    """The old proxies counted it twice: accuracy 2.0 (any red) and format 3.0 (not every statement cited)."""
    view = _handoff_view(background=[{"text": "Admitted on 2026-10-10.", "source_refs": []}])
    assert [i["code"] for i in view["validation"]["issues"]] == ["no_source"]
    p = evals._handoff_proxies(view)
    claims = 6 + 1  # situation, background, 2 assessment, 2 recommendation; one not_recorded declaration
    assert p["accuracy"] == evals._scale((claims - 1) / claims)  # the share of clean claims, not a 5/4/2 step
    assert p["format"] == 5.0 and p["completeness"] == 5.0 and p["readability"] == 5.0


def test_a_number_warning_lowers_accuracy_in_proportion():
    view = _handoff_view(assessment=[{"text": "Heart rate 80 /min then 99 /min.",
                                      "source_refs": ["Observation/v1", "Observation/v2"]},
                                     {"text": "Pain score 3 then 1.", "source_refs": ["Observation/v1", "Observation/v2"]}])
    assert [(i["code"], i["value"]) for i in view["validation"]["issues"]] == [("number_not_in_source", "99")]
    p = evals._handoff_proxies(view)
    assert p["accuracy"] == evals._scale(6 / 7) and p["format"] == 5.0  # the old step gave 4.0 for any amber


def test_an_absence_written_as_a_statement_costs_format_only():
    """Version 1's sentence with a token citation (seen live on doc-25): it traces, but belongs in not_recorded."""
    view = _handoff_view(assessment=[{"text": "Heart rate 80 /min then 76 /min.",
                                      "source_refs": ["Observation/v1", "Observation/v2"]},
                                     {"text": "Pain score 3 then 1.", "source_refs": ["Observation/v1", "Observation/v2"]},
                                     {"text": "Intake and output were not recorded.", "source_refs": ["Encounter/e1"]}],
                         not_recorded=[])
    assert not view["validation"]["issues"]
    p = evals._handoff_proxies(view)
    assert p["format"] == evals._scale(7 / 8)  # 1 situation check + 7 statements, one stating an absence
    assert p["accuracy"] == 5.0 and p["completeness"] == 5.0  # intake and output is addressed, just misplaced


def test_handoff_completeness_wants_pain_and_intake_output_addressed():
    view = _handoff_view(not_recorded=[], assessment=[{"text": "Heart rate 80 /min then 76 /min.",
                                                       "source_refs": ["Observation/v1", "Observation/v2"]}])
    assert evals._handoff_proxies(view)["completeness"] == evals._scale(7 / 9)  # 9 checks: 2 not addressed


def test_a_situation_of_several_sentences_costs_format():
    view = _handoff_view(situation={"text": "Day 4 of the stay. Heart rate 76 /min at 05:00. Fall risk.",
                                    "source_refs": ["Encounter/e1", "Observation/v2", "Flag/f1"]})
    p = evals._handoff_proxies(view)
    assert p["format"] == evals._scale(6 / 7) and p["accuracy"] == 5.0
