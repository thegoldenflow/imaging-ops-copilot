"""Phase 1 acceptance: scheduling, report generator and front desk."""

import time

from app.core.store import get_store
from app.modules.scheduling.noshow import get_model


def _call(client, headers, *utterances):
    call_id = client.post("/api/frontdesk/calls", headers=headers).json()["id"]
    replies = []
    for text in utterances:
        r = client.post(f"/api/frontdesk/calls/{call_id}/turn", headers=headers, json={"text": text})
        assert r.status_code == 200, r.text
        replies.append(r.json()["reply"])
    return call_id, replies


# ---------- System 2 ----------

def test_cancellation_produces_ranked_backfill_within_5_seconds(client, login):
    started = time.monotonic()
    r = client.post("/api/scheduling/appointments/AP-DEMO2/cancel", headers=login("U-FD"), json={"reason": "test"})
    assert time.monotonic() - started < 5
    case = r.json()["backfill_case"]
    top = case["candidates"][0]
    assert top["patient_id"] == "PT-DEMO1" and top["urgency"] == "P2"
    # Urgency always ranks first.
    ranks = [c["urgency"] for c in case["candidates"]]
    assert ranks == sorted(ranks)
    exclusions = {c["patient_id"]: c["exclusion"] for c in case["excluded"]}
    assert "Site not acceptable" in exclusions["PT-00011"]
    assert "Not ready" in exclusions["PT-00012"]


def test_first_patient_to_confirm_gets_the_slot(client, login):
    headers = login("U-OPS")
    case = client.post("/api/scheduling/appointments/AP-DEMO2/cancel", headers=headers, json={}).json()["backfill_case"]
    ids = [c["waitlist_id"] for c in case["candidates"][:2]]
    sent = client.post(f"/api/scheduling/backfill/{case['id']}/offers", headers=headers,
                       json={"waitlist_ids": ids}).json()["sent"]
    assert client.post(f"/api/scheduling/offers/{sent[1]['id']}/accept", headers=headers).status_code == 200
    assert client.post(f"/api/scheduling/offers/{sent[0]['id']}/accept", headers=headers).status_code == 409
    store = get_store()
    filled = store.modules["backfill_cases"][case["id"]]
    assert filled.status == "filled" and filled.filled_by_patient_id == sent[1]["patient_id"]


def test_offer_goes_out_in_patient_language(client, login):
    headers = login("U-OPS")
    case = client.post("/api/scheduling/appointments/AP-DEMO2/cancel", headers=headers, json={}).json()["backfill_case"]
    client.post(f"/api/scheduling/backfill/{case['id']}/offers", headers=headers,
                json={"waitlist_ids": ["WL-DEMO1"]})
    msg = next(m for m in get_store().outbox.values() if m.kind == "waitlist_offer")
    assert msg.language == "zh" and "回复 YES" in msg.body


def test_noshow_model_reports_auc_and_top_factors():
    store = get_store()
    nsm = get_model(store)
    assert nsm.auc > 0.65
    upcoming = [a for a in store.appointments.values() if a.no_show_risk is not None]
    assert upcoming and all(1 <= len(a.risk_factors) <= 3 for a in upcoming)


def test_dashboard_version_changes_after_booking(client, login):
    headers = login("U-OPS")
    v1 = client.get("/api/scheduling/dashboard", headers=headers).json()["version"]
    client.post("/api/scheduling/appointments/AP-DEMO2/cancel", headers=headers, json={})
    assert client.get("/api/scheduling/dashboard", headers=headers).json()["version"] > v1


# ---------- System 3 ----------

def test_report_flow_draft_review_sign_and_critical_result(client, login):
    rad, ref = login("U-RAD"), login("U-REF")
    report = client.post("/api/reports/studies/ST-DEMO1/draft", headers=rad).json()
    assert report["ai_status"] == "ok" and report["urgent_findings"]
    rid = report["id"]
    # Drafts are invisible to the referrer and cannot be sent.
    assert client.get(f"/api/reports/{rid}", headers=ref).status_code == 403
    assert client.post(f"/api/reports/{rid}/send", headers=rad).status_code == 409
    # Signing requires every section to be reviewed.
    assert client.post(f"/api/reports/{rid}/sign", headers=rad, json={}).status_code == 422
    for section in report["sections"]:
        client.patch(f"/api/reports/{rid}/sections/{section['key']}", headers=rad, json={"action": "accept"})
    signed = client.post(f"/api/reports/{rid}/sign", headers=rad, json={"confirmed_urgent": [0]}).json()
    assert signed["report"]["status"] == "signed" and len(signed["critical_results"]) == 1
    assert client.get(f"/api/reports/{rid}", headers=ref).status_code == 200
    assert client.post(f"/api/reports/{rid}/send", headers=rad).status_code == 200


def test_report_degrades_when_ai_unavailable(client, login):
    from app.llm.gateway import LlmGateway, set_gateway
    from app.llm.providers import ProviderUnavailable

    class Down:
        mode = "anthropic"

        def complete_json(self, **_):
            raise ProviderUnavailable("down")

    set_gateway(LlmGateway(Down()))
    report = client.post("/api/reports/studies/ST-DEMO1/draft", headers=login("U-RAD")).json()
    assert report["ai_status"] == "unavailable"
    assert all(s["ai_text"] == "" for s in report["sections"])


# ---------- System 4 ----------

def test_voice_call_verifies_then_reschedules(client, login):
    headers = login("U-FD")
    _, replies = _call(client, headers, "When is my appointment?", "Robert Taylor", "April 12 1968",
                       "I need to reschedule", "the second one", "yes")
    assert "full name" in replies[0].lower()  # nothing disclosed before verification
    assert "verified" in replies[2]
    assert "You're all set" in replies[5]
    store = get_store()
    assert store.appointments["AP-DEMO2"].status == "cancelled"
    new = [a for a in store.appointments.values() if a.patient_id == "PT-DEMO2" and a.status == "booked"]
    assert len(new) == 1


def test_voice_cancel_triggers_backfill(client, login):
    headers = login("U-FD")
    _call(client, headers, "Robert Taylor, born 1968-04-12", "Please cancel it", "yes")
    cases = get_store().modules["backfill_cases"]
    assert any(c.source_appointment_id == "AP-DEMO2" for c in cases.values())


def test_wrong_identity_reveals_nothing(client, login):
    _, replies = _call(client, login("U-FD"), "Robert Taylor, born January 1 1970")
    assert "couldn't match" in replies[0] and "CT" not in replies[0]


def test_medical_question_is_transferred(client, login):
    headers = login("U-FD")
    call_id, replies = _call(client, headers, "Robert Taylor 1968-04-12", "Is the contrast dangerous for my kidneys?")
    assert "transferring" in replies[1]
    call = get_store().modules["calls"][call_id]
    assert call.outcome == "transferred" and call.summary


def test_new_booking_schedules_reminders_and_reply_cancel_releases_slot(client, login):
    headers = login("U-OPS")
    case = client.post("/api/scheduling/appointments/AP-DEMO2/cancel", headers=headers, json={}).json()["backfill_case"]
    offer = client.post(f"/api/scheduling/backfill/{case['id']}/offers", headers=headers,
                        json={"waitlist_ids": ["WL-DEMO1"]}).json()["sent"][0]
    appt = client.post(f"/api/scheduling/offers/{offer['id']}/accept", headers=headers).json()["appointment"]
    store = get_store()
    kinds = {m.kind for m in store.outbox.values() if m.appointment_id == appt["id"]}
    assert {"booking_confirmation", "prep"} <= kinds
    confirmation = next(m for m in store.outbox.values()
                        if m.appointment_id == appt["id"] and m.kind == "booking_confirmation")
    result = client.post(f"/api/frontdesk/outbox/{confirmation.id}/reply", headers=headers, json={"text": "X"}).json()
    assert result["action"] == "cancelled" and result["backfill_case_id"]


def test_preregistration_with_mock_ohip(client, login):
    headers = login("U-OPS")
    case = client.post("/api/scheduling/appointments/AP-DEMO2/cancel", headers=headers, json={}).json()["backfill_case"]
    offer = client.post(f"/api/scheduling/backfill/{case['id']}/offers", headers=headers,
                        json={"waitlist_ids": ["WL-DEMO1"]}).json()["sent"][0]
    client.post(f"/api/scheduling/offers/{offer['id']}/accept", headers=headers)
    token = next(iter(get_store().modules["prereg"]))
    page = client.get(f"/api/public/prereg/{token}").json()
    assert page["language"] == "zh" and page["first_name"] == "Mei"
    form = {"phone": "+1-416-555-0168", "email": "mei@example.com", "address": "88 Example St",
            "preferred_language": "zh", "insurance_type": "ohip", "health_card": "4827103956",
            "health_card_version": "MC", "consent": True}
    r = client.post(f"/api/public/prereg/{token}", json=form).json()
    assert r["status"] == "completed" and r["coverage"]["valid"]
