"""The eval harness after the post-merge live run (run-2026-10-12T225622): a failing case shows why it failed, and
the scores count what they claim to. Each test reproduces one confirmed defect with the mock model."""

from __future__ import annotations

import json
import random
from types import SimpleNamespace

import pytest

from app.agents import registry
from app.agents import trace as traces
from app.aiops import evalrun, suites
from app.core.models import Referrer
from app.llm.gateway import get_gateway
from app.llm.providers import MOCK_FIXTURES, ProviderUnavailable
from app.modules.documentation import evals as doc_evals
from app.modules.evals import build
from app.modules.evals import run as imaging
from app.modules.evals.paths import DATASETS
from app.modules.frontdesk import evals as fd
from app.modules.med_safety import evals as ms
from app.modules.protocols.library import Modality, detect_modality
from app.modules.requisitions.extraction import EXTRACT_PROMPT, Extraction, baseline_extract
from app.modules.requisitions.generator import CASES, generate

PROVIDER_504 = "Agent Platform 504 DEADLINE_EXCEEDED: the request timed out (after 2 attempts)"


def _run(suite, store) -> dict[str, dict]:
    """The suite as the runner runs it (its collecting gateway, mock model)."""
    specs = [registry.require_agent(a) for a in suite.agents]
    out = evalrun.run_suite(suite, specs, gateway=evalrun._gateway(False), store=store, overrides={}, variant=None,
                            live=False)
    assert all(e["status"] != "error" for e in out.values()), {a: e.get("error") for a, e in out.items()}
    return out


def _down(text, images, attempt):
    raise ProviderUnavailable(PROVIDER_504)


# ---------- why a case failed ----------


def test_a_failed_model_call_keeps_the_provider_error_in_its_trace(fresh_state, monkeypatch):
    """Diagnostic 1: LlmCallRecord had no error, so a captured trace could not tell a 504 from a 429 or a refusal."""
    monkeypatch.setitem(MOCK_FIXTURES, "requisition_extract", _down)
    outcome = get_gateway().structured(task="requisition_extract", prompt=EXTRACT_PROMPT,
                                       variables={"text": "Exam requested: CT head"}, schema_cls=Extraction)
    assert outcome.status == "unavailable" and outcome.error == "AI is temporarily unavailable"  # the caller's view
    (call,) = traces.get(outcome.run_id).llm_calls
    assert call.status == "unavailable" and call.error == PROVIDER_504


def test_a_failing_case_keeps_its_own_agent_runs_and_the_provider_error(fresh_state, monkeypatch):
    """Diagnostics 1-3 on a documentation case: four agents run per case (reconciliation, summary, instructions,
    handoff). The handoff's case listed all four runs, kept the first three as traces and so lost its own failing
    run, and the run file had no word of the 504."""
    cases = {c["id"]: c for c in doc_evals.load_cases()}
    monkeypatch.setattr(doc_evals, "load_cases", lambda: [cases["doc-02"]])
    monkeypatch.setitem(MOCK_FIXTURES, "nursing_handoff", _down)
    out = _run(suites.DOCUMENTATION, fresh_state)

    (handoff,) = out["nursing_handoff"]["case_results"]
    assert handoff["passed"] is False
    assert {e["error"] for e in handoff["call_errors"]} == {PROVIDER_504}
    assert {e["outcome"] for e in handoff["call_errors"]} == {"unavailable"}
    assert handoff["run_ids"], "the handoff's own run"
    assert {traces.get(r).agent_id for r in handoff["run_ids"]} == {"nursing_handoff"}
    assert [t["run_id"] for t in handoff["traces"]] == handoff["run_ids"]
    assert any(c["error"] == PROVIDER_504 for t in handoff["traces"] for c in t["llm_calls"])
    # the other documents of the same case are listed apart, not mixed into the handoff's runs
    related = handoff["related_run_ids"]
    assert related and not set(related) & set(handoff["run_ids"])
    assert {traces.get(r).agent_id for r in related} >= {"discharge_summary", "patient_instructions"}

    (summary,) = out["discharge_summary"]["case_results"]
    assert summary["call_errors"] == []
    assert {traces.get(r).agent_id for r in summary["run_ids"]} == {"discharge_summary"}


def test_a_document_skipped_after_an_earlier_step_failed_keeps_that_steps_trace(fresh_state, monkeypatch):
    """A case can fail because the pipeline step before its agent failed (instructions are drafted from the signed
    summary): the agent has no run of its own, so the failing run of the earlier step is captured."""
    cases = {c["id"]: c for c in doc_evals.load_cases()}
    monkeypatch.setattr(doc_evals, "load_cases", lambda: [cases["doc-04"]])
    monkeypatch.setitem(MOCK_FIXTURES, "discharge_summary", _down)
    out = _run(suites.DOCUMENTATION, fresh_state)

    (instructions,) = out["patient_instructions"]["case_results"]
    assert instructions["passed"] is False and instructions["got"]["status"] == "skipped"
    assert instructions["run_ids"] == []
    captured = {t["agent_id"] for t in instructions["traces"]}
    assert captured == {"discharge_summary"}, "only the failing step, not every run of the case"
    assert all(t["run_id"] in instructions["related_run_ids"] for t in instructions["traces"])


def test_a_long_phone_call_keeps_every_turn_and_not_the_summary_run(fresh_state, monkeypatch):
    """Diagnostics 2-3 on the phone agent: the voice case listed the call summary's run, and only the first three
    turns of a failing call were kept, so the turn where it went wrong was lost."""
    case = next(c for c in fd.load_cases() if c["id"] == "fd-04")  # a cancellation over five caller turns
    failing = {**case, "expected": {**case["expected"], "task": "keep"}}  # the judge now expects no change: fails
    monkeypatch.setattr(fd, "load_cases", lambda: [failing])
    out = _run(suites.FRONTDESK, fresh_state)

    (voice,) = out["voice_agent"]["case_results"]
    assert voice["passed"] is False
    assert {traces.get(r).agent_id for r in voice["run_ids"]} == {"voice_agent"}
    assert len(voice["run_ids"]) > 3, "one run per caller turn"
    assert [t["run_id"] for t in voice["traces"]] == voice["run_ids"], "every turn of the call"
    assert {traces.get(r).agent_id for r in voice["related_run_ids"]} == {"call_summary"}

    (summary,) = out["call_summary"]["case_results"]
    assert {traces.get(r).agent_id for r in summary["run_ids"]} == {"call_summary"}
    assert set(summary["related_run_ids"]) == set(voice["run_ids"])


def test_the_trace_cap_keeps_the_failing_runs_first():
    """At most MAX_TRACES traces per failing case: the failing runs are kept before the others."""
    def trace(run_id: str, outcome: str) -> traces.AgentTrace:
        return traces.AgentTrace(run_id=run_id, agent_id="voice_agent", agent_version="1", kind="run",
                                 eval_status="pending", evaluated=False, outcome=outcome,
                                 started_at="2026-10-13T10:00:00")

    n = evalrun.MAX_TRACES + 4
    runs = [trace(f"RUN-{i:05d}", "failed" if i in (0, n - 1) else "completed") for i in range(n)]
    kept, dropped = evalrun.pick_traces(runs, [])
    assert len(kept) == evalrun.MAX_TRACES and dropped == 4
    assert {"RUN-00000", f"RUN-{n - 1:05d}"} <= {t.run_id for t in kept}
    assert [t.run_id for t in kept] == sorted(t.run_id for t in kept), "in the order they ran"


def test_the_bilingual_gate_row_says_how_many_documents_it_was_computed_over(fresh_state, monkeypatch):
    """Diagnostic 4: the gate also needs 10 non-English documents, and the row showed only the ratio
    ("value 1.0, passed false")."""
    cases = {c["id"]: c for c in doc_evals.load_cases()}
    monkeypatch.setattr(doc_evals, "load_cases", lambda: [cases["doc-02"]])  # one Chinese-speaking patient
    out = _run(suites.DOCUMENTATION, fresh_state)

    gate = out["patient_instructions"]["gates"]["bilingual_consistency"]
    assert gate["value"] == 1.0 and gate["passed"] is False
    assert gate["documents"] == 1 and gate["min_documents"] == doc_evals.MIN_BILINGUAL_DOCUMENTS == 10
    assert "1 non-English" in gate["reason"] and "10" in gate["reason"]
    assert out["patient_instructions"]["extra"]["bilingual_documents"] == 1


# ---------- scoring ----------


@pytest.mark.parametrize("text", [
    # fd-17 of the live run, in shape (the negation guard looks 30 characters back from the match)
    "The assistant could not verify the caller's identity (birth date given twice) and initiated a transfer to a "
    "staff member.",
    "The assistant is transferring the caller to the front desk.",
    "The caller was put through to a team member.",
    "A transfer to a person was arranged.",
    "The caller was transferred to staff.",
])
def test_a_transfer_claimed_in_other_words_is_an_unsupported_claim(text):
    """Diagnostic 5: only "transferred" counted, so fd-17's "initiated a transfer" escaped the claim gate."""
    call = {"session": SimpleNamespace(actions=[], outcome="abandoned"),
            "summary": {"summary": text, "follow_up_needed": False}}
    assert fd.judge_summary({"id": "x"}, call)["got"]["unsupported_claims"] == ["transfer_to_human"]


@pytest.mark.parametrize("text", [
    "The caller declined a transfer and hung up.",
    "The assistant offered to transfer the caller to a team member; the caller said no.",
    "Caller asked whether the assistant could transfer them, then ended the call.",
    "Automated assistant handled the call. Actions: none.",
])
def test_a_transfer_that_is_offered_or_declined_is_not_a_claim(text):
    call = {"session": SimpleNamespace(actions=[], outcome="abandoned"),
            "summary": {"summary": text, "follow_up_needed": False}}
    assert fd.judge_summary({"id": "x"}, call)["got"]["unsupported_claims"] == []


def test_the_order_review_suite_keeps_per_type_and_the_new_without_indication_metric(fresh_state, monkeypatch):
    """Diagnostic 6: run_order_review computes per_type and the "new without indication" sub-metric; the suite
    dropped both."""
    def judged(c: dict) -> bool:  # the cases the sub-metric covers (med_safety/evals.py)
        rxcui = (c.get("order") or {}).get("rxcui")
        return (c["expected"].get("indicated") is not None and bool(rxcui)
                and not any(h["rxcui"] == rxcui for h in c.get("home") or []))

    cases = ms.load_cases("order_review")
    picked = [c for c in cases if judged(c)][:2]
    picked += [c for c in cases if c["expected"]["concerns"] and c not in picked][:2]
    monkeypatch.setattr(ms, "load_cases", lambda name: picked)
    extra = _run(suites.ORDER_REVIEW, fresh_state)["order_review"]["extra"]

    assert extra["new_without_indication_cases"] == sum(judged(c) for c in picked) >= 2
    for k in ("accuracy", "precision", "recall"):
        assert 0 <= extra[f"new_without_indication_{k}"] <= 1
    assert extra["per_type"] and all({"tp", "fp", "fn", "precision", "recall"} <= set(v)
                                     for v in extra["per_type"].values())


def test_cxr_is_an_x_ray():
    """Diagnostic 7: the generator writes "CXR PA and lateral"; `\\bxr\\b` cannot match inside "cxr"."""
    assert detect_modality("CXR PA and lateral") == Modality.XR
    assert detect_modality("AXR supine") == Modality.XR
    assert detect_modality("Chest X-ray") == Modality.XR
    assert detect_modality("CT KUB") == Modality.CT and detect_modality("MRI L-spine") == Modality.MRI


def test_an_exam_text_that_says_urgent_is_labelled_urgent():
    """Diagnostic 8 (req-11): "MRI lumbar spine urgent" with no Priority line was labelled "unspecified"."""
    case = next(c for c in CASES if "MRI lumbar spine urgent" in c.exam_texts)
    referrer = Referrer(id="R", name="Dr. R", specialty="FM", clinic="C", phone="1", fax="2", is_key=False)
    seen = 0
    for seed in range(60):
        rng = random.Random(seed)
        text, labels = generate(rng, build._patient(rng, seed), referrer, case=case)
        if "lumbar spine urgent" in text:
            seen += 1
            assert labels.stated_urgency == "urgent", (seed, text)
    assert seen > 5


def test_the_requisition_dataset_is_the_generator_output():
    """The label fix draws nothing new from the generator's random stream: the committed set is exactly what the
    generator writes (only the labels of an exam text that says "urgent" changed)."""
    committed = json.loads((DATASETS / "requisitions.json").read_text(encoding="utf-8"))
    assert build.requisition_items(random.Random(build.SEED)) == committed
    assert next(r for r in committed if r["id"] == "req-11")["labels"]["stated_urgency"] == "urgent"


def test_none_known_is_no_prior_imaging():
    """Diagnostic 9 (the part that is a plain bug): a model that copies "None known" into a list field gave a
    non-empty list, which the eval and the priors module read as a prior."""
    def s(value: str) -> dict:
        return {"value": value, "source_quote": value, "confidence": 0.9}

    out = Extraction.model_validate({
        "requested_exam": s("CT head"), "clinical_indication": s("Headache"), "relevant_history": [s("None")],
        "prior_imaging": [s("None known"), s("")], "allergies": [s("NKDA")], "renal_or_diabetes": [s("N/A")],
        "implant_hints": [s("none."), s("Pacemaker inserted 2019")], "stated_urgency": s("unspecified")})
    assert out.prior_imaging == [] and out.relevant_history == [] and out.allergies == []
    assert out.renal_or_diabetes == [] and [h.value for h in out.implant_hints] == ["Pacemaker inserted 2019"]


def test_the_mock_lists_only_priors_at_an_outside_facility():
    """prior_imaging is prior imaging at an outside facility (the owner's decision, requisition_extract@2). The
    rule-based mock gave a letter without one an item with an empty value, which counted as a prior (7 letters)."""
    letter = ("Dear colleague, please arrange a MRI head for [PERSON_1] (DOB [DATE_1], HCN [ID_1]). First unprovoked "
              "seizure last week, normal CT in emergency. Thanks, Dr. Test Referrer, Eval Clinic")
    assert baseline_extract(letter)["prior_imaging"] == []
    had = letter.replace("Thanks,", "He had a CT head at Northview General Hospital, 2023. Thanks,")
    assert [p["value"] for p in baseline_extract(had)["prior_imaging"]] == [
        "CT head at Northview General Hospital, 2023"]
    form = "Exam requested: CT head\nPrevious imaging: {}\nReferring physician: Dr. R"
    assert baseline_extract(form.format("None known"))["prior_imaging"] == []
    assert baseline_extract(form.format("CT head 2023"))["prior_imaging"] == []  # no facility to retrieve it from
    assert len(baseline_extract(form.format("MRI brain at Lakeview Diagnostics, June 2024"))["prior_imaging"]) == 1
    items = json.loads((DATASETS / "requisitions.json").read_text(encoding="utf-8"))
    assert all(bool(baseline_extract(i["text"])["prior_imaging"]) == bool(i["labels"]["prior_facility"]) for i in items)


def test_the_extraction_suite_scores_none_known_cxr_and_urgent_exam_text(fresh_state, monkeypatch, tmp_path):
    """End to end with the mock model: a model answer that lists "None known" as prior imaging no longer fails
    the prior_imaging check, req-39 (CXR) gets its modality and req-11 its urgency."""
    def says_none_known(text, images, attempt):
        out = baseline_extract(text.split("<<<\n", 1)[-1].rsplit("\n>>>", 1)[0])
        if not out["prior_imaging"]:
            out["prior_imaging"] = [{"value": "None known", "source_quote": "None known", "confidence": 0.9}]
        return out

    monkeypatch.setitem(MOCK_FIXTURES, "requisition_extract", says_none_known)
    monkeypatch.setattr(imaging, "RESULTS", tmp_path)  # the harness also writes evals/results/extraction.json
    suite = next(s for s in suites.BUILTIN if s.agents == ("requisition_extract",))
    entry = _run(suite, fresh_state)["requisition_extract"]
    extra = entry["extra"]
    assert extra["prior_imaging_accuracy"] == 1.0  # "None known" costs nothing (it was 0.30)
    assert extra["requested_modality_accuracy"] == 1.0 and extra["stated_urgency_accuracy"] == 1.0
    errors = {(c["id"], f) for c in entry["case_results"] if not c["passed"] for f in c["got"]}
    assert not {("req-39", "requested_modality"), ("req-11", "stated_urgency")} & errors
