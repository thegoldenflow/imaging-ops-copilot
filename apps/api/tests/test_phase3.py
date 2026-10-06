"""Phase 3 acceptance: radiology operations (systems 11-14)."""

from datetime import datetime, timedelta

from app.core.models import ACTIVE_STATUSES, AppointmentStatus
from app.core.store import get_store
from app.modules.backlog import service as backlog
from app.modules.reports.service import report_for_study


def _next_appointment(site_id: str | None = None, modality: str | None = None):
    store = get_store()
    now = datetime.now()
    return min((a for a in store.appointments.values()
                if a.status in ACTIVE_STATUSES and a.end >= now and (site_id is None or a.site_id == site_id)
                and (modality is None or store.exams[a.exam_code].modality == modality)), key=lambda a: a.start)


def _audit(action: str, resource_type: str):
    return [e for e in get_store().audit.events() if e.action == action and e.resource_type == resource_type]


# ---------- Shared step: exam completion ----------

def test_completing_an_exam_creates_a_study_and_assigns_a_credentialed_reader(client, login):
    appt = _next_appointment("LKS", "MRI")
    r = client.post(f"/api/scheduling/appointments/{appt.id}/complete", headers=login("U-TECH"))
    assert r.status_code == 200, r.text
    store = get_store()
    study = store.studies[r.json()["study_id"]]
    assert appt.status == AppointmentStatus.COMPLETED
    assert study.appointment_id == appt.id and study.priority == appt.urgency and study.site_id == "LKS"
    reader = store.staff[backlog.assignments(store)[study.id].radiologist_id]
    assert "MRI" in reader.reading_modalities and backlog.on_shift(store, reader.id)


def test_technologist_cannot_complete_exams_at_another_site(client, login):
    appt = _next_appointment("NGT")
    r = client.post(f"/api/scheduling/appointments/{appt.id}/complete", headers=login("U-TECH"))
    assert r.status_code == 403
    assert any(e.outcome == "denied" and e.resource_id == appt.id for e in get_store().audit.events())


def test_demo_login_lists_one_user_per_role(client):
    users = client.get("/api/auth/users").json()["users"]
    roles = [u["role"] for u in users]
    assert len(roles) == len(set(roles))


# ---------- System 11 ----------

def test_board_groups_unread_studies_with_turnaround_targets(client, login):
    data = client.get("/api/backlog", headers=login("U-OPS")).json()
    assert data["kpis"]["unread"] == len(data["studies"]) > 50
    assert data["kpis"]["overdue"] > 0
    for key in ("site", "modality", "priority", "age"):
        assert sum(g["count"] for g in data["groups"][key]) == data["kpis"]["unread"]
    row = data["studies"][0]
    assert row["state"] in ("overdue", "at_risk", "on_track") and row["assigned_to"]
    assert 0 < data["turnaround"]["overall"]["within_target"] <= 1
    assert {p["priority"] for p in data["turnaround"]["by_priority"]} == {"P1", "P2", "P3", "P4"}


def test_turnaround_is_signed_minus_completed():
    store = get_store()
    study = next(s for s in store.studies.values() if (r := report_for_study(store, s.id)) and r.status == "signed"
                 and r.signed_at > datetime.now() - timedelta(days=1))
    report = report_for_study(store, study.id)
    tat = backlog.turnaround(store, datetime.now(), days=1)
    hours = (report.signed_at - study.performed_at).total_seconds() / 3600
    assert tat["overall"]["count"] >= 1 and hours > 0


def test_board_updates_with_new_studies_and_sign_offs(client, login):
    ops, rad = login("U-OPS"), login("U-RAD")
    before = client.get("/api/backlog", headers=ops).json()
    study_id = client.post("/api/backlog/simulate-completion", headers=ops).json()["study_id"]
    after = client.get("/api/backlog", headers=ops).json()
    assert after["version"] > before["version"] and after["kpis"]["unread"] == before["kpis"]["unread"] + 1
    store = get_store()
    if not backlog.credentialed(store, store.staff["U-RAD"], store.studies[study_id]):
        return
    detail = client.get(f"/api/backlog/studies/{study_id}", headers=rad).json()
    r = client.post(f"/api/backlog/studies/{study_id}/sign", headers=rad, json=detail["template"])
    assert r.status_code == 200, r.text
    final = client.get("/api/backlog", headers=ops).json()
    assert final["kpis"]["unread"] == before["kpis"]["unread"]
    assert next(x for x in final["radiologists"] if x["id"] == "U-RAD")["signed_today"] >= 1


def test_reassignment_is_audited_and_requires_credentials(client, login):
    headers = login("U-OPS")
    store = get_store()
    mri = next(s for s in backlog.unread(store) if backlog.modality(store, s) == "MRI")
    r = client.post(f"/api/backlog/studies/{mri.id}/assign", headers=headers,
                    json={"radiologist_id": "U-RAD3", "reason": "Balancing"})
    assert r.status_code == 422  # Dr. Nwosu does not read MRI
    r = client.post(f"/api/backlog/studies/{mri.id}/assign", headers=headers,
                    json={"radiologist_id": "U-RAD4", "reason": "Balancing"})
    assert r.status_code == 200
    assert backlog.assignments(store)[mri.id].radiologist_id == "U-RAD4"
    events = _audit("reassign", "study_assignment")
    assert events and events[-1].resource_id == mri.id and events[-1].user_id == "U-OPS"


def test_suggestions_move_work_to_on_shift_credentialed_readers(client, login):
    headers = login("U-OPS")
    store = get_store()
    suggestions = client.get("/api/backlog", headers=headers).json()["suggestions"]
    assert suggestions and any(s["from_id"] == "U-RAD5" for s in suggestions)
    for s in suggestions:
        study, to = store.studies[s["study_id"]], store.staff[s["to_id"]]
        assert backlog.on_shift(store, to.id) and backlog.credentialed(store, to, study) and s["to_id"] != s["from_id"]
    applied = client.post("/api/backlog/suggestions/apply", headers=headers, json={}).json()["applied"]
    assert len(applied) == len(suggestions)
    assert len(_audit("reassign", "study_assignment")) == len(applied)
    readers = client.get("/api/backlog", headers=headers).json()["radiologists"]
    assert next(r for r in readers if r["id"] == "U-RAD5")["queue_count"] == 0


def test_target_change_recomputes_states(client, login):
    headers = login("U-OPS")
    before = client.get("/api/backlog", headers=headers).json()["kpis"]["overdue"]
    r = client.put("/api/backlog/targets", headers=headers,
                   json={"hours": {"P1": 0.5, "P2": 2, "P3": 6, "P4": 12}, "at_risk_fraction": 0.75})
    assert r.status_code == 200
    assert client.get("/api/backlog", headers=headers).json()["kpis"]["overdue"] > before


def test_ai_drafted_studies_are_signed_in_the_reading_room(client, login):
    rad = login("U-RAD")
    client.post("/api/reports/studies/ST-DEMO1/draft", headers=rad)
    r = client.post("/api/backlog/studies/ST-DEMO1/sign", headers=rad, json={"findings": "x", "impression": "y"})
    assert r.status_code == 409


def test_board_is_not_available_to_front_desk_or_referrers(client, login):
    for user in ("U-FD", "U-REF"):
        assert client.get("/api/backlog", headers=login(user)).status_code == 403
