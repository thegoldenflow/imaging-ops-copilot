"""Placeholder steps of the inpatient journey (spec 6.5), each run as the agent that will own the real one.

PLACEHOLDERS, NOT THE MODULES. The journey workflow (app/workflows/journey.py) needs these steps now; the modules
behind them come in later work packages, which replace the bodies below and keep the signatures:

| Step                     | Agent                      | Replaced by                                        |
| ------------------------ | -------------------------- | -------------------------------------------------- |
| orderReview              | order_review               | WP6, done: app/modules/med_safety/journey.py       |
| medRecAdmission          | medication_reconciliation  | WP6, done: app/modules/med_safety/journey.py       |
| draftDischargeSummary    | discharge_summary          | WP7: discharge summary agent and validator         |
| draftPatientInstructions | patient_instructions       | WP7: bilingual patient instructions                |
| scheduleFollowupCall     | followup_calls             | WP8: the follow-up call itself                     |

WP6's steps need the module's own state (the order reviews, the reconciliation), so they live in the module and the
activities call them there; `StepResult` stays the interface. The placeholders below take the run (`AgentRun`) and
return a `StepResult`: what they wrote, and what the workflow waits for (a draft to be signed final, a Task to be
decided). No model is called: the text is a template over the record. Everything goes through the run's tools (the
Tool Gateway), whose consent check also applies: without the patient's consent the call is refused and the workflow
opens a manual Task instead. The arguments are built only from data that does not change afterwards (the finished
stay), so an activity that Temporal retries sends the same arguments and gets the first result back (the
idempotency key), never a second draft.

Agent code: it uses only the run (no FhirGateway, HTTP client or database).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.runtime import AgentRun

PLACEHOLDER = "Placeholder written by the journey workflow; {wp} replaces it."


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


def _ref(r: dict) -> str:
    return f"{r['resourceType']}/{r['id']}"


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
