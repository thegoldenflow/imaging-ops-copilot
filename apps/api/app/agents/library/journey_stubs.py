"""Placeholder steps of the inpatient journey (spec 6.5), each run as the agent that will own the real one.

PLACEHOLDERS, NOT THE MODULES. The journey workflow (app/workflows/journey.py) needs these steps now; the modules
behind them come in later work packages, which replace the bodies below and keep the signatures:

| Step                     | Agent                      | Replaced by                                      |
| ------------------------ | -------------------------- | ------------------------------------------------ |
| orderReview              | order_review               | WP6: OrderReviewService (rules + Claude comments)  |
| medRecAdmission          | medication_reconciliation  | WP6: admission MedRec and the pharmacist workbench |
| draftDischargeSummary    | discharge_summary          | WP7: discharge summary agent and validator         |
| draftPatientInstructions | patient_instructions       | WP7: bilingual patient instructions                |
| scheduleFollowupCall     | followup_calls             | WP8: the follow-up call itself                     |

Each takes the run (`AgentRun`) and returns a `StepResult`: what it wrote, and what the workflow waits for (a
draft to be signed final, a Task to be decided). No model is called: the text is a template over the record.
Everything goes through the run's tools (the Tool Gateway), whose consent check also applies: without the
patient's consent the call is refused and the workflow opens a manual Task instead. The arguments are built only
from data that does not change afterwards (orders as of the admission, the finished stay), so an activity that
Temporal retries sends the same arguments and gets the first result back (the idempotency key), never a second
draft.

Agent code: it uses only the run (no FhirGateway, HTTP client or database).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.runtime import AgentRun

PLACEHOLDER = "Placeholder written by the journey workflow; {wp} replaces it."
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"


@dataclass
class StepResult:
    refs: list[str] = field(default_factory=list)  # resources written (or read, for the record)
    wait_for: list[str] = field(default_factory=list)  # what a person has to sign or decide
    detail: str = ""
    signer: str | None = None
    refused: str | None = None  # the gateway refused (e.g. consent missing): the workflow falls back to a person


def _refused(result) -> str:
    return f"{result.reason}: {result.detail}"


def _resources(result, rtype: str) -> list[dict]:
    return [r for r in (result.output or {}).get("resources", []) if r.get("resourceType") == rtype]


def _med(resource: dict) -> tuple[str, str]:
    concept = resource.get("medicationCodeableConcept") or {}
    coding = next((c for c in concept.get("coding") or [] if c.get("system") == RXNORM), None) or \
        next(iter(concept.get("coding") or []), {})
    return coding.get("code") or concept.get("text") or "?", concept.get("text") or coding.get("display") or "?"


def _as_of(resources: list[dict], as_of: str | None, field_name: str = "authoredOn") -> list[dict]:
    """The orders written up to a time the workflow fixed (its history): a set that a retry reads again unchanged."""
    if not as_of:
        return resources
    return [r for r in resources if (r.get(field_name) or "")[:19] <= as_of[:19]]


def _ref(r: dict) -> str:
    return f"{r['resourceType']}/{r['id']}"


# ---------- WP6 ----------


def order_review(run: AgentRun, as_of: str | None) -> StepResult:
    """Interface: review the stay's orders and put the result in the pharmacist's queue.
    Placeholder: a sign-off Task listing the medication orders written by `as_of` (hospital time)."""
    meds = run.tool("getMedications", {"encounter_id": run.encounter_id})
    if not meds.ok:
        return StepResult(refused=_refused(meds))
    orders = sorted(_as_of(_resources(meds, "MedicationRequest"), as_of), key=lambda r: r["id"])
    names = ", ".join(_med(r)[1] for r in orders[:12]) or "none"
    task = run.tool("createTask", {
        "encounter_id": run.encounter_id, "code": "workflow-signoff", "performer_role": "pharmacist",
        "description": f"Review the stay's orders ({len(orders)} medication orders so far: {names}). "
                       + PLACEHOLDER.format(wp="WP6's OrderReviewService")})
    if not task.ok:
        return StepResult(refused=_refused(task))
    ref = f"Task/{task.output['task_id']}"
    return StepResult(refs=[ref, *(_ref(r) for r in orders)], wait_for=[ref], signer="pharmacist",
                      detail=f"{len(orders)} medication orders so far sent to the pharmacist")


def medrec_admission(run: AgentRun, as_of: str | None) -> StepResult:
    """Interface: reconcile home medications against the admission orders (those written by `as_of`, the order
    review's sign-off); a draft the pharmacist and the physician co-sign. Placeholder: the comparison by RxNorm
    code, as plain text."""
    home = run.tool("getHomeMeds", {"encounter_id": run.encounter_id})
    meds = run.tool("getMedications", {"encounter_id": run.encounter_id})
    for result in (home, meds):
        if not result.ok:
            return StepResult(refused=_refused(result))
    at_home = {_med(r)[0]: (_med(r)[1], r) for r in _resources(home, "MedicationStatement")}
    ordered = {_med(r)[0]: (_med(r)[1], r) for r in _as_of(_resources(meds, "MedicationRequest"), as_of)}
    omitted = sorted(name for code, (name, _) in at_home.items() if code not in ordered)
    new = sorted(name for code, (name, _) in ordered.items() if code not in at_home)
    continued = sorted(name for code, (name, _) in ordered.items() if code in at_home)
    lines = [PLACEHOLDER.format(wp="WP6's medication reconciliation"), "",
             f"Home medications: {len(at_home)}; admission medication orders: {len(ordered)}.",
             f"Continued: {', '.join(continued) or 'none'}.",
             f"Home medications not ordered (possible omissions): {', '.join(omitted) or 'none'}.",
             f"New in hospital: {', '.join(new) or 'none'}.",
             "Each difference needs a pharmacist decision (accept, modify or reject with a reason) before signing."]
    sources = sorted({_ref(r) for _, r in [*at_home.values(), *ordered.values()]})
    draft = run.tool("draftDocument", {"encounter_id": run.encounter_id, "doc_type": "medrec",
                                       "title": "Admission medication reconciliation (preliminary)",
                                       "text": "\n".join(lines), "source_refs": sources})
    if not draft.ok:
        return StepResult(refused=_refused(draft))
    ref = f"DocumentReference/{draft.output['document_id']}"
    return StepResult(refs=[ref], wait_for=[ref], signer="pharmacist",
                      detail=f"Draft {ref.split('/')[1]}: {len(omitted)} possible omission(s), {len(new)} new; "
                             f"co-signed by pharmacist and physician")


# ---------- WP7 ----------


def discharge_summary(run: AgentRun) -> StepResult:
    """Interface: a discharge summary draft whose every fact traces to a FHIR resource; the physician signs.
    Placeholder: diagnoses, procedures and medications of the stay listed with their references."""
    bundle = run.tool("getEncounterBundle", {"encounter_id": run.encounter_id})
    if not bundle.ok:
        return StepResult(refused=_refused(bundle))

    def names(rtype: str) -> list[tuple[str, str]]:
        out = []
        for r in sorted(_resources(bundle, rtype), key=lambda x: x["id"]):
            concept = r.get("code") or r.get("medicationCodeableConcept") or {}
            label = concept.get("text") or next((c.get("display") for c in concept.get("coding") or []), None)
            out.append((label or "?", _ref(r)))
        return out

    sections = [("Diagnoses", names("Condition")), ("Procedures", names("Procedure")),
                ("Medications", names("MedicationRequest"))]
    lines = [PLACEHOLDER.format(wp="WP7's discharge summary agent"), ""]
    for title, items in sections:
        lines.append(f"{title}: " + ("; ".join(f"{n} [{ref}]" for n, ref in items) or "none recorded"))
    sources = sorted({ref for _, items in sections for _, ref in items})
    draft = run.tool("draftDocument", {"encounter_id": run.encounter_id, "doc_type": "discharge_summary",
                                       "title": "Discharge summary (preliminary)", "text": "\n".join(lines),
                                       **({"source_refs": sources[:200]} if sources else {})})
    if not draft.ok:
        return StepResult(refused=_refused(draft))
    ref = f"DocumentReference/{draft.output['document_id']}"
    return StepResult(refs=[ref], wait_for=[ref], signer="physician",
                      detail=f"Draft {ref.split('/')[1]} from {len(sources)} resources; the physician signs")


def patient_instructions(run: AgentRun) -> StepResult:
    """Interface: bilingual patient instructions from the signed discharge summary only; a nurse signs.
    Placeholder: the five sections as headings with a pointer to the summary."""
    docs = run.tool("getDocuments", {"encounter_id": run.encounter_id})
    if not docs.ok:
        return StepResult(refused=_refused(docs))
    summaries = sorted((r for r in _resources(docs, "DocumentReference")
                        if any(c.get("code") == "18842-5" for c in (r.get("type") or {}).get("coding") or [])),
                       key=lambda r: (r.get("docStatus") != "final", r["id"]))
    summary = summaries[0] if summaries else None
    lines = [PLACEHOLDER.format(wp="WP7's patient instructions"), "",
             f"Source: {_ref(summary) + ' (' + str(summary.get('docStatus')) + ')' if summary else 'no discharge summary'}",
             "Why you were in hospital / Your medicines and when to take them / Your follow-up appointments / "
             "When to get help right away / Looking after yourself at home"]
    draft = run.tool("draftDocument", {"encounter_id": run.encounter_id, "doc_type": "patient_instructions",
                                       "title": "Patient discharge instructions (preliminary)",
                                       "text": "\n".join(lines),
                                       **({"source_refs": [_ref(summary)]} if summary else {})})
    if not draft.ok:
        return StepResult(refused=_refused(draft))
    ref = f"DocumentReference/{draft.output['document_id']}"
    return StepResult(refs=[ref], wait_for=[ref], signer="nurse", detail=f"Draft {ref.split('/')[1]}; a nurse signs")


# ---------- WP8 ----------


def followup_consent(run: AgentRun) -> StepResult:
    """Interface: may the patient be called? The follow-up agent needs the patient's followup_call consent (the
    Tool Gateway checks it on every call). Placeholder: reading the encounter is the consent check."""
    context = run.tool("getEncounterContext", {"encounter_id": run.encounter_id})
    if not context.ok:
        return StepResult(refused=_refused(context))
    return StepResult(refs=[f"Encounter/{run.encounter_id}"], detail="Follow-up call consent on file")
