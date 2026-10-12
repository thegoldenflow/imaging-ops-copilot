"""Platform demo data for 6.3 (WP4), written after the hospital generator.

- Hospital staff users: one StaffUser per generated Practitioner, with the role
  from its PractitionerRole code (doctor -> physician, nurse, pharmacist, clerk,
  ops -> operations_manager), its unit (PractitionerRole.location) as unit scope
  and the Practitioner id. One user per new role is on the demo login page: a
  Medicine A physician and nurse, a pharmacist and a registration clerk. The
  imaging centre's operations manager (Jordan Lee) stays the operations manager
  login; the generated patient-flow staff are extra operations managers.
- Draft documents for the signing service: a discharge summary and an SBAR
  handoff for one Medicine A inpatient, a medication reconciliation (co-signed by
  pharmacist and physician) for another. They are plain demo drafts, not AI output.

Deterministic: the choices follow the generated ids; no random numbers are drawn.
"""

from __future__ import annotations

import base64

from app.core.models import Role, StaffUser
from app.core.store import Store
from app.ehr.codes import DOC_TYPES, EXT, concept
from app.fhir.dt import fhir_datetime, ref, ref_id

ROLE_OF_CODE = {"doctor": Role.PHYSICIAN, "nurse": Role.NURSE, "pharmacist": Role.PHARMACIST, "clerk": Role.CLERK,
                "ops": Role.OPERATIONS_MANAGER}
# The first practitioner of each kind in the generator's plan: Medicine A's first physician (doctors 1-8 are
# ED) and first nurse (nurses 1-4 are ED), the first pharmacist and the first clerk.
DEMO_LOGINS = {"prac-doc-09", "prac-nurs-05", "prac-phar-01", "prac-cler-01"}
DEMO_UNIT = "MEDA"


def user_id_for(practitioner_id: str) -> str:
    return "U-" + practitioner_id.removeprefix("prac-").upper()


def hospital_staff(store: Store) -> list[StaffUser]:
    practitioners = {p["id"]: p for p in store.fhir.search("Practitioner")}
    users = []
    for role in sorted(store.fhir.search("PractitionerRole"), key=lambda r: r["id"]):
        pid = ref_id(role["practitioner"])
        code = role["code"][0]["coding"][0]["code"]
        practitioner = practitioners.get(pid)
        if practitioner is None or code not in ROLE_OF_CODE:
            continue
        users.append(StaffUser(
            id=user_id_for(pid), name=practitioner["name"][0]["text"], role=ROLE_OF_CODE[code], site_ids=[],
            demo_login=pid in DEMO_LOGINS, unit_ids=[ref_id(loc) for loc in role.get("location") or []],
            practitioner_id=pid))
    return users


def _text(lines: list[str]) -> str:
    return base64.b64encode("\n".join(lines).encode()).decode()


def _draft(doc_id: str, kind: str, module: str, patient: str, encounter: str, when, lines: list[str]) -> dict:
    system, code, display = DOC_TYPES[kind]
    return {"resourceType": "DocumentReference", "id": doc_id, "status": "current", "docStatus": "preliminary",
            "type": concept(system, code, display), "subject": ref("Patient", patient), "date": fhir_datetime(when),
            "description": f"{display} draft (demo data for the signing service)",
            "extension": [{"url": EXT + "source-module", "valueCode": module}],
            "content": [{"attachment": {"contentType": "text/plain", "language": "en", "title": f"{display} (draft)",
                                        "data": _text(lines)}}],
            "context": {"encounter": [ref("Encounter", encounter)]}}


def draft_documents(store: Store) -> list[dict]:
    from app.ehr.clock import hospital_now

    now = hospital_now(store)
    stays = sorted((e for e in store.fhir.search("Encounter", cls="IMP", status="in-progress", unit=DEMO_UNIT)),
                   key=lambda e: e["id"])
    if len(stays) < 2:
        return []
    first, second = stays[-1], stays[-2]  # the newest admissions on the unit

    def reason(enc: dict) -> str:
        concepts = enc.get("reasonCode") or []
        return (concepts[0].get("text") or concepts[0]["coding"][0].get("display", "")) if concepts else "not recorded"

    p1, p2 = ref_id(first["subject"]), ref_id(second["subject"])
    return [
        _draft("doc-demo-discharge", "discharge_summary", "discharge_summary", p1, first["id"], now, [
            "DISCHARGE SUMMARY (draft)",
            f"Reason for admission: {reason(first)}.",
            "Hospital course: improving on day 3; tolerating oral intake; mobilising with physiotherapy.",
            "Discharge medications: see the reconciled medication list.",
            "Follow-up: family physician in 7 days."]),
        _draft("doc-demo-handoff", "handoff", "nursing_handoff", p1, first["id"], now, [
            "S: Stable overnight, no acute events.",
            f"B: Admitted with {reason(first).lower()}.",
            "A: Vitals within normal limits; pain 2/10.",
            "R: Continue the current plan; reassess mobility at 10:00."]),
        _draft("doc-demo-medrec", "medrec", "medication_reconciliation", p2, second["id"], now, [
            "MEDICATION RECONCILIATION (draft)",
            "Home medications compared with the admission orders.",
            "Difference 1: a home medication is not ordered in hospital (held on admission?).",
            "Needs: pharmacist review and physician co-signature."]),
    ]


def seed_platform(store: Store) -> None:
    for user in hospital_staff(store):
        store.staff[user.id] = user
    for doc in draft_documents(store):
        store.fhir.create(doc)
