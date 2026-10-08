"""System 3 · Chest X-ray report drafts with radiologist review and sign-off."""

import base64
import difflib
from datetime import datetime

from pydantic import BaseModel

from app.core.models import ImagingStudy
from app.core.store import Store, get_store
from app.llm.deid import age_from_dob
from app.llm.gateway import get_gateway
from app.llm.prompts import CXR_DRAFT
from app.llm.providers import mock_fixture

# ---------- Output schema ----------


class ImageQuality(BaseModel):
    adequate: bool
    notes: str


class Findings(BaseModel):
    lungs: str
    pleura: str
    heart_mediastinum: str
    bones: str
    lines_tubes: str


class UrgentFinding(BaseModel):
    finding: str
    reason: str


class CxrDraft(BaseModel):
    image_quality: ImageQuality
    findings: Findings
    impression: str
    urgent_findings: list[UrgentFinding]
    uncertainties: list[str]


SECTIONS = [
    ("lungs", "Lungs"),
    ("pleura", "Pleura"),
    ("heart_mediastinum", "Heart and mediastinum"),
    ("bones", "Bones"),
    ("lines_tubes", "Lines and tubes"),
    ("impression", "Impression"),
]

# ---------- Report state ----------


class Section(BaseModel):
    key: str
    label: str
    ai_text: str
    final_text: str
    status: str = "pending"  # pending, accepted, edited, deleted


class ReportUrgentFinding(BaseModel):
    finding: str
    reason: str
    confirmed: bool | None = None


class Report(BaseModel):
    id: str
    study_id: str
    patient_id: str
    referrer_id: str
    status: str = "draft"  # draft, signed
    source: str = "ai_draft"  # ai_draft (system 3) or dictated (no AI involved)
    ai_status: str = "not_used"  # ok, needs_human, unavailable, not_used
    ai_error: str | None = None
    image_quality: ImageQuality | None = None
    sections: list[Section]
    urgent_findings: list[ReportUrgentFinding] = []
    uncertainties: list[str] = []
    model: str = ""
    prompt_version: str = ""
    llm_mode: str = ""
    llm_call_id: str = ""
    created_at: datetime
    signed_by: str | None = None
    signed_by_id: str | None = None
    signed_at: datetime | None = None
    edit_ratio: float | None = None
    sent_to_referrer_at: datetime | None = None


def reports(store: Store) -> dict[str, Report]:
    return store.module("reports", dict)


def _by_study(store: Store) -> dict[str, str]:
    return store.module("report_by_study", dict)  # study id -> report id


def save(store: Store, report: Report) -> None:
    reports(store)[report.id] = report
    _by_study(store)[report.study_id] = report.id


def report_for_study(store: Store, study_id: str) -> Report | None:
    rid = _by_study(store).get(study_id)
    return reports(store).get(rid) if rid else None


def generate_draft(store: Store, study: ImagingStudy) -> Report:
    patient = store.patients[study.patient_id]
    image_bytes, media_type = store.images[study.image_key]
    outcome = get_gateway().structured(
        task="cxr_draft",
        prompt=CXR_DRAFT,
        variables={
            "exam_name": store.exams[study.exam_code].name,
            "age": age_from_dob(patient.dob),
            "sex": patient.sex,
            "indication": study.indication or "Not provided",
        },
        schema_cls=CxrDraft,
        tier="reasoning",
        images=[(base64.standard_b64encode(image_bytes).decode(), media_type)],
        patients=[patient],
    )
    draft = CxrDraft.model_validate(outcome.data) if outcome.status == "ok" else None
    sections = []
    for key, label in SECTIONS:
        text = ""
        if draft:
            text = draft.impression if key == "impression" else getattr(draft.findings, key)
        sections.append(Section(key=key, label=label, ai_text=text, final_text=text))
    existing = report_for_study(store, study.id)
    report = Report(
        id=existing.id if existing else store.next_id("RPT"),
        study_id=study.id, patient_id=study.patient_id, referrer_id=study.referrer_id,
        ai_status=outcome.status, ai_error=outcome.error,
        image_quality=draft.image_quality if draft else None, sections=sections,
        urgent_findings=[ReportUrgentFinding(**u.model_dump()) for u in draft.urgent_findings] if draft else [],
        uncertainties=draft.uncertainties if draft else [],
        model=outcome.model, prompt_version=outcome.prompt_version, llm_mode=outcome.mode,
        llm_call_id=outcome.call_id, created_at=datetime.now(),
    )
    save(store, report)
    store.touch()
    return report


def edit_ratio(report: Report) -> float:
    ai = "\n".join(s.ai_text for s in report.sections)
    final = "\n".join("" if s.status == "deleted" else s.final_text for s in report.sections)
    return round(1 - difflib.SequenceMatcher(None, ai, final).ratio(), 3)


def sign(store: Store, report: Report, signer_name: str, confirmed_urgent: list[int],
         signer_id: str | None = None, levels: dict[int, str] | None = None) -> list:
    from app.modules.critical import service as critical  # System 12

    for i, finding in enumerate(report.urgent_findings):
        finding.confirmed = i in confirmed_urgent
    report.status = "signed"
    report.signed_by = signer_name
    report.signed_by_id = signer_id
    report.signed_at = datetime.now()
    report.edit_ratio = edit_ratio(report)
    # Confirmed urgent findings open a case in the Critical Results Tracker.
    created = [critical.open_case(store, report, f.finding, (levels or {}).get(i, "urgent"), opened_by=signer_name)
               for i, f in enumerate(report.urgent_findings) if f.confirmed]
    store.touch()
    return created


# ---------- Mock output (used when no API key is configured) ----------

MOCK_DRAFTS = {
    "normal": {
        "image_quality": {"adequate": True, "notes": "PA view, adequate inspiration and penetration."},
        "findings": {
            "lungs": "Lungs are clear. No focal consolidation.",
            "pleura": "No pleural effusion or pneumothorax.",
            "heart_mediastinum": "Heart size within normal limits. Mediastinal contours normal.",
            "bones": "No acute osseous abnormality.",
            "lines_tubes": "None.",
        },
        "impression": "No acute cardiopulmonary abnormality.",
        "urgent_findings": [],
        "uncertainties": [],
    },
    "nodule": {
        "image_quality": {"adequate": True, "notes": "PA view, adequate inspiration."},
        "findings": {
            "lungs": "Rounded opacity approximately 1.5 cm in the right upper zone. Lungs otherwise clear.",
            "pleura": "No pleural effusion or pneumothorax.",
            "heart_mediastinum": "Heart size normal. Mediastinal contours normal.",
            "bones": "No acute osseous abnormality.",
            "lines_tubes": "None.",
        },
        "impression": "Possible 1.5 cm right upper lobe nodule. CT chest recommended for further characterization.",
        "urgent_findings": [
            {"finding": "Possible right upper lobe pulmonary nodule",
             "reason": "New nodule needs follow-up CT; referring physician should be informed."}
        ],
        "uncertainties": ["Overlap with the anterior rib could mimic a nodule; lateral view or CT would clarify."],
    },
    "effusion": {
        "image_quality": {"adequate": True, "notes": "PA view."},
        "findings": {
            "lungs": "Basal opacity on the right, likely related to fluid. Left lung clear.",
            "pleura": "Moderate right pleural effusion with blunting of the costophrenic angle.",
            "heart_mediastinum": "Heart size normal.",
            "bones": "No acute osseous abnormality.",
            "lines_tubes": "None.",
        },
        "impression": "Moderate right pleural effusion.",
        "urgent_findings": [],
        "uncertainties": ["Underlying consolidation cannot be excluded behind the effusion."],
    },
    "cardiomegaly": {
        "image_quality": {"adequate": True, "notes": "PA view."},
        "findings": {
            "lungs": "No focal consolidation. No pulmonary edema.",
            "pleura": "No pleural effusion.",
            "heart_mediastinum": "Cardiothoracic ratio increased, in keeping with cardiomegaly.",
            "bones": "No acute osseous abnormality.",
            "lines_tubes": "None.",
        },
        "impression": "Cardiomegaly without pulmonary edema.",
        "urgent_findings": [],
        "uncertainties": ["Cardiac size is less reliable if the film is not a true PA projection."],
    },
}


@mock_fixture("cxr_draft")
def _mock_cxr(text: str, images: list, attempt: int) -> dict:
    store = get_store()
    variant = None
    if images:
        raw = base64.standard_b64decode(images[0][0])
        variants = store.modules.get("phantom_variants", {})
        variant = next((variants[k] for k, (data, _) in store.images.items() if data == raw and k in variants), None)
    if variant is None:
        out = dict(MOCK_DRAFTS["normal"])
        out["uncertainties"] = ["Mock mode: this uploaded image was not analysed. Configure ANTHROPIC_API_KEY for a real draft."]
        return out
    return MOCK_DRAFTS[variant]
