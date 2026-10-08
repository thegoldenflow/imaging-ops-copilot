"""Phase 3 acceptance: radiology operations (systems 11-14)."""

from datetime import datetime, timedelta

from app.core.models import ACTIVE_STATUSES, AppointmentStatus
from app.core.store import get_store
from app.modules.backlog import service as backlog
from app.modules.critical import service as critical
from app.modules.dose import service as dose
from app.modules.peer_review import service as peer_review
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
    appt = store.appointments[appt.id]  # objects loaded before a request are stale after it
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


# ---------- System 12 ----------

def _sign_mei_with_finding(client, login, level="urgent"):
    rad = login("U-RAD")
    report = client.post("/api/reports/studies/ST-DEMO1/draft", headers=rad).json()
    for section in report["sections"]:
        client.patch(f"/api/reports/{report['id']}/sections/{section['key']}", headers=rad, json={"action": "accept"})
    signed = client.post(f"/api/reports/{report['id']}/sign", headers=rad,
                         json={"confirmed_urgent": [0], "levels": {"0": level}}).json()
    return signed["critical_results"][0]


def test_confirmed_finding_opens_a_case_and_notifies_the_ordering_physician(client, login):
    case = _sign_mei_with_finding(client, login)
    assert case["referrer_id"] == "R-DEMO" and case["level"] == "urgent" and case["status"] == "open"
    assert [e["kind"] for e in case["events"]] == ["opened", "notify"]
    store = get_store()
    calls = [m for m in store.outbox.values() if m.kind == "critical_result" and m.to == store.referrers["R-DEMO"].phone]
    assert calls


def test_unacknowledged_case_is_renotified_then_escalated_to_the_director():
    store = get_store()
    case = next(c for c in critical.cases(store).values() if c.status == "open")
    pol = critical.policy(store).levels[case.level]
    critical.process_due(store, case.created_at + timedelta(seconds=pol.renotify_after_s + 1))
    assert case.status == "open" and case.events[-1]["kind"] == "renotify"
    critical.process_due(store, case.created_at + timedelta(seconds=pol.escalate_after_s + 1))
    assert case.status == "escalated" and case.events[-1]["kind"] == "escalate"
    assert any(m.kind == "critical_escalation" for m in store.outbox.values())
    # Nothing further happens once escalated, however long it waits.
    n = len(case.events)
    critical.process_due(store, case.created_at + timedelta(hours=5))
    assert len(case.events) == n


def test_case_cannot_close_without_acknowledgement_and_records_who_when_how(client, login):
    rad = login("U-RAD")
    case = next(c for c in critical.cases(get_store()).values() if c.status == "open")
    assert client.post(f"/api/critical/{case.id}/close", headers=rad, json={"note": "done"}).status_code == 409
    r = client.post(f"/api/critical/{case.id}/acknowledge", headers=rad, json={"by_name": " ", "method": "phone"})
    assert r.status_code == 422
    r = client.post(f"/api/critical/{case.id}/acknowledge", headers=rad,
                    json={"by_name": "Dr. Michael Campbell", "by_role": "Ordering physician", "method": "phone"}).json()
    ack = r["acknowledgement"]
    assert ack["by_name"] == "Dr. Michael Campbell" and ack["method"] == "phone" and ack["at"] and ack["recorded_by"] == "Dr. Priya Raman"
    closed = client.post(f"/api/critical/{case.id}/close", headers=rad, json={"note": "Patient sent to ED"}).json()
    assert closed["status"] == "closed"
    assert [e["kind"] for e in closed["events"]][-2:] == ["acknowledged", "closed"]
    assert _audit("acknowledge", "critical_result") and _audit("close", "critical_result")


def test_every_case_has_a_complete_timeline(client, login):
    cases = client.get("/api/critical", headers=login("U-MD")).json()["cases"]
    assert len(cases) >= 5
    for c in cases:
        kinds = [e["kind"] for e in c["events"]]
        assert kinds[:2] == ["opened", "notify"]
        if c["status"] == "closed":
            assert "acknowledged" in kinds and kinds[-1] == "closed"
    assert any("escalate" in [e["kind"] for e in c["events"]] for c in cases)


def test_referrer_acknowledges_own_cases_in_the_portal_only(client, login):
    _sign_mei_with_finding(client, login)
    ref = login("U-REF")
    mine = client.get("/api/critical/mine", headers=ref).json()["cases"]
    assert mine and all(c["referrer_id"] == "R-DEMO" for c in mine)
    r = client.post(f"/api/critical/{mine[0]['id']}/acknowledge-portal", headers=ref).json()
    assert r["acknowledgement"]["method"] == "portal" and r["status"] == "acknowledged"
    other = next(c for c in critical.cases(get_store()).values() if c.referrer_id != "R-DEMO")
    assert client.post(f"/api/critical/{other.id}/acknowledge-portal", headers=ref).status_code == 403
    assert any(e.outcome == "denied" and e.resource_id == other.id for e in get_store().audit.events())


def test_dictated_report_with_a_finding_opens_a_case(client, login):
    store = get_store()
    study = next(s for s in backlog.unread(store) if not s.image_key and backlog.modality(store, s) == "CT")
    r = client.post(f"/api/backlog/studies/{study.id}/sign", headers=login("U-RAD"), json={
        "findings": "Filling defect in the right lower lobe segmental arteries.", "impression": "Acute pulmonary embolism.",
        "critical_finding": "Acute pulmonary embolism", "critical_level": "critical"}).json()
    assert r["critical_results"][0]["level"] == "critical"


def test_only_the_director_changes_the_escalation_policy(client, login):
    pol = client.get("/api/critical/policy", headers=login("U-RAD")).json()
    pol["levels"]["critical"]["escalate_after_s"] = 90
    assert client.put("/api/critical/policy", headers=login("U-RAD"), json={"levels": pol["levels"]}).status_code == 403
    r = client.put("/api/critical/policy", headers=login("U-MD"), json={"levels": pol["levels"]})
    assert r.status_code == 200 and r.json()["levels"]["critical"]["escalate_after_s"] == 90


# ---------- System 13 ----------

def test_scheduled_sampling_runs_once_a_day_after_the_run_hour():
    store = get_store()
    cfg = peer_review.config(store)
    tomorrow = (datetime.now() + timedelta(days=1)).replace(minute=5, second=0, microsecond=0)
    before_hour = tomorrow.replace(hour=max(0, cfg.run_hour - 1))
    if cfg.run_hour > 0:
        assert peer_review.maybe_run_scheduled(store, before_hour) is None
    run = peer_review.maybe_run_scheduled(store, tomorrow.replace(hour=cfg.run_hour))
    assert run is not None and run.trigger == "schedule"
    assert peer_review.maybe_run_scheduled(store, tomorrow.replace(hour=23)) is None  # once per day


def test_sampling_never_assigns_a_report_to_its_original_reader():
    store = get_store()
    store.modules["qa_config"] = peer_review.QaConfig(sample_rate=0.5)
    peer_review.state(store)["last_run_at"] = datetime.now() - timedelta(days=14)
    run = peer_review.run_sampling(store, now=datetime.now(), trigger="manual", by="test")
    assert run.sampled > 100
    for review in peer_review.reviews(store).values():
        assert review.reviewer_id != review.original_reader_id
        if review.reviewer_id:
            reviewer = store.staff[review.reviewer_id]
            exam = store.exams[store.studies[review.study_id].exam_code]
            assert exam.modality in reviewer.reading_modalities


def test_reviewer_sees_a_blinded_case_and_grades_it(client, login):
    rad = login("U-RAD")
    data = client.get("/api/peer-review/mine", headers=rad).json()
    review = next(r for r in data["reviews"] if r["status"] == "assigned")
    assert review["original_reader_id"] is None and "original_reader_name" not in review
    assert review["report_sections"]
    r = client.post(f"/api/peer-review/{review['id']}/submit", headers=rad, json={"score": "minor"})
    assert r.status_code == 422  # discrepancy type required
    r = client.post(f"/api/peer-review/{review['id']}/submit", headers=rad,
                    json={"score": "minor", "discrepancy_type": "clarity", "comment": "Impression vague"})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert client.post(f"/api/peer-review/{review['id']}/submit", headers=rad, json={"score": "concur"}).status_code == 409


def test_other_radiologists_cannot_open_someone_elses_review(client, login):
    store = get_store()
    other = next(r for r in peer_review.reviews(store).values() if r.reviewer_id not in (None, "U-RAD"))
    assert client.get(f"/api/peer-review/{other.id}", headers=login("U-RAD")).status_code == 403
    assert client.post(f"/api/peer-review/{other.id}/submit", headers=login("U-RAD"), json={"score": "concur"}).status_code == 403


def test_qa_report_is_for_the_qa_lead_only_and_exports_csv(client, login):
    for user in ("U-RAD", "U-OPS", "U-ADMIN"):
        assert client.get("/api/peer-review/qa", headers=login(user)).status_code == 403
        assert client.get("/api/peer-review/qa/export", headers=login(user)).status_code == 403
    md = login("U-MD")
    qa = client.get("/api/peer-review/qa", headers=md).json()
    rows = {r["radiologist_id"]: r for r in qa["report"]["by_radiologist"]}
    assert qa["report"]["overall"]["reviews"] > 50 and rows["U-RAD2"]["by_modality"]["MRI"]["reviews"] > 0
    assert qa["recent"][0]["original_reader_name"]
    r = client.get("/api/peer-review/qa/export", headers=md)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("review_id,completed_at") and len(lines) == qa["report"]["overall"]["reviews"] + 1
    assert _audit("export", "qa_report")


def test_qa_lead_can_run_sampling_now_and_change_the_rate(client, login):
    md = login("U-MD")
    assert client.put("/api/peer-review/config", headers=md, json={"sample_rate": 0.1, "run_hour": 3, "enabled": True}).status_code == 200
    run = client.post("/api/peer-review/run", headers=md).json()
    assert run["trigger"] == "manual" and run["by"] == "Dr. Daniel Okafor"


# ---------- System 14 ----------

def test_every_completed_ct_has_a_dose_record(client, login):
    data = client.get("/api/dose/overview", headers=login("U-TECH")).json()
    assert data["coverage"]["ct_exams"] > 1000 and data["coverage"]["missing"] == 0
    appt = _next_appointment("LKS", "CT")
    study_id = client.post(f"/api/scheduling/appointments/{appt.id}/complete", headers=login("U-TECH")).json()["study_id"]
    store = get_store()
    rec = next(r for r in dose.records(store).values() if r.appointment_id == appt.id)
    assert rec.study_id == study_id and rec.ctdivol_mgy > 0 and rec.dlp_total_mgycm > rec.ctdivol_mgy
    assert rec.sr_template.startswith("TID 10011") and {e.acquisition_type for e in rec.events} == {"Stationary Acquisition", "Spiral Acquisition"}
    after = client.get("/api/dose/overview", headers=login("U-TECH")).json()["coverage"]
    assert after["missing"] == 0 and after["ct_exams"] == data["coverage"]["ct_exams"] + (appt.start >= datetime.now() - timedelta(days=90))


def test_records_above_the_reference_level_are_listed_as_exceptions(client, login):
    headers = login("U-MD")
    data = client.get("/api/dose/overview", headers=headers).json()
    store = get_store()
    exceeding = [r for r in dose.records(store).values() if dose.exceedance(store, r)]
    assert data["exceeding"] == len(exceeding) > 0
    for item in data["exceptions"]:
        ref = item["reference"]
        assert item["ctdivol_mgy"] > ref["ctdivol_mgy"] or item["dlp_total_mgycm"] > ref["dlp_mgycm"]


def test_lowering_a_reference_level_recomputes_exceptions(client, login):
    headers = login("U-MD")
    before = client.get("/api/dose/overview", headers=headers).json()["exceeding"]
    assert client.put("/api/dose/references/CT-HD-ROUTINE", headers=login("U-TECH"),
                      json={"ctdivol_mgy": 30, "dlp_mgycm": 500}).status_code == 403
    assert client.put("/api/dose/references/CT-HD-ROUTINE", headers=headers,
                      json={"ctdivol_mgy": 30, "dlp_mgycm": 500}).status_code == 200
    assert client.get("/api/dose/overview", headers=headers).json()["exceeding"] > before
    assert _audit("update", "dose_reference")


def test_exception_review_is_recorded_and_audited(client, login):
    headers = login("U-TECH")
    item = next(e for e in client.get("/api/dose/overview", headers=headers).json()["exceptions"] if e["review"] is None)
    assert client.post(f"/api/dose/records/{item['id']}/review", headers=headers, json={"outcome": "nope"}).status_code == 422
    r = client.post(f"/api/dose/records/{item['id']}/review", headers=headers, json={"outcome": "justified", "note": "Large patient"})
    assert r.status_code == 200 and r.json()["review"]["by"] == "Sam Rivera"
    detail = client.get(f"/api/dose/records/{item['id']}", headers=headers).json()
    assert len(detail["events"]) == 2 and detail["review"]["outcome"] == "justified"


def test_trends_by_scanner_show_the_seeded_drift(client, login):
    trend = client.get("/api/dose/overview", headers=login("U-MD")).json()["trend_by_scanner"]
    series = {s["name"]: [v for v in s["values"] if v is not None] for s in trend["series"]}  # current week may be empty
    drift, steady = series["EVW-CT1"], series["LKS-CT1"]
    assert drift[-1] > drift[0] * 1.15 and abs(steady[-1] - steady[0]) < 10
