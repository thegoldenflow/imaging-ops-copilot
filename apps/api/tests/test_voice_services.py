"""WP8 (spec 7.4): patient voice services on the seeded hospital, text-simulated calls only (no audio).

The generator plants follow-up cases (stays discharged 48-72 h before 07:00 with signed instructions, one per missing
condition, one not yet due, a Chinese-speaking patient for the escalation) and call-centre appointments
(app/ehr/seed/voice.py). Covered: one end-to-end call per call-centre intent through the API (reschedule and cancel
confirmed by the clerk, lookup, FAQ, prep, clinical, transfer), the identity check, the follow-up scheduling with one
degradation test per trigger condition, the call with its red-flag escalation (rules first, without the model), the
write-back of Communication, Observation and Task, the nurse queue and its actions, the gateway increments, RBAC.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from app.agents import registry
from app.core.models import Role
from app.ehr import consent as consent_mod
from app.ehr.clock import hospital_now
from app.ehr.codes import HCN_SYSTEM, OBS_CATEGORY, PATIENT_REPORTED
from app.ehr.events import bus
from app.ehr.gateway import Actor, FhirAccessDenied, FhirGateway
from app.fhir.dt import fhir_datetime, parse
from app.llm.gateway import LlmGateway, set_gateway
from app.llm.providers import MockProvider, ProviderUnavailable
from app.modules.voice_services import followup, queue, redflags
from app.modules.voice_services import phrases as P

CLERK, NURSE, PHYSICIAN, OPS = "U-CLER-01", "U-NURS-05", "U-DOC-09", "U-OPS"


# ---------- helpers ----------


def demo(store) -> dict:
    return store.modules["voice_demo"]


def identity_line(store, patient_id: str, *, wrong: bool = False) -> str:
    p = store.fhir.read("Patient", patient_id)
    name = next(n["text"] for n in p["name"] if n.get("use") == "official")
    card = next(i["value"] for i in p["identifier"] if i["system"] == HCN_SYSTEM)
    born = p["birthDate"] if not wrong else "1901-01-01"
    return f"This is {name}, born {born}, card ending {card[-4:]}."


def chinese_identity_line(store, patient_id: str) -> str:
    p = store.fhir.read("Patient", patient_id)
    name = next(n["text"] for n in p["name"] if n.get("use") == "usual")
    card = next(i["value"] for i in p["identifier"] if i["system"] == HCN_SYSTEM)
    y, m, d = p["birthDate"].split("-")
    return f"我叫{name}，{y}年{int(m)}月{int(d)}日出生，健康卡尾号{card[-4:]}。"


def converse(client, headers, *texts: str) -> tuple[str, list[str], dict]:
    call = client.post("/api/voice/calls", headers=headers).json()
    replies = []
    out = None
    for text in texts:
        r = client.post(f"/api/voice/calls/{call['id']}/turn", headers=headers, json={"text": text})
        assert r.status_code == 200, r.text
        out = r.json()
        replies.append(out["reply"])
        if out["ended"]:
            break
    return call["id"], replies, out["call"]


def tasks_of(store, code: str) -> list[dict]:
    return [t for t in store.fhir.search("Task", code=code)]


class Down:
    """A model provider that is down: everything that needs it degrades."""

    mode = "anthropic"

    def complete_json(self, **_):
        raise ProviderUnavailable("down for the test")

    def tool_turn(self, **_):
        raise ProviderUnavailable("down for the test")


# ---------- demo data ----------


def test_demo_data_plants_follow_ups_and_call_centre_appointments(fresh_state):
    store = fresh_state
    cases = demo(store)
    fu = cases["followups"]
    assert {"zh", "en", "no_consent", "no_phone", "unsigned", "early"} <= set(fu)
    assert fu["zh"]["unit"] == "MEDA" and fu["zh"]["language"].startswith("zh") and fu["en"]["unit"] == "MEDA"
    for key, case in fu.items():
        doc = store.fhir.read("DocumentReference", case["instructions"])
        assert doc["docStatus"] == ("preliminary" if key == "unsigned" else "final")
        assert store.fhir.read("DocumentReference", case["summary"])["docStatus"] == "final"
    assert not store.fhir.read("Patient", fu["no_phone"]["patient"]).get("telecom")
    fhir = FhirGateway(Actor.system("test"), "voice_services")
    assert consent_mod.consent_status(fhir, fu["no_consent"]["patient"])["followup_call"] == "deny"
    assert any(p["code"]["coding"][0]["code"] == "415070008"
               for p in store.fhir.search("Procedure", encounter=fu["zh"]["stay"]))  # the wound to ask about
    callers = cases["callers"]
    assert {"en_fracture", "zh_anticoag", "zh_tw_lung", "en_none"} <= set(callers)
    for caller in callers.values():
        for appt_id in caller["appointments"]:
            appt = store.fhir.read("Appointment", appt_id)
            assert appt["status"] == "booked" and parse(appt["start"]) > hospital_now(store)
    assert callers["en_none"]["appointments"] == []


# ---------- the call centre: one test per intent ----------


def test_reschedule_proposes_a_new_time_and_the_clerk_confirms_it_in_one_click(fresh_state, client, login):
    store = fresh_state
    clerk = login(CLERK)
    caller = demo(store)["callers"]["en_fracture"]
    old = caller["appointments"][0]
    call_id, replies, call = converse(client, clerk, "I need to reschedule my fracture clinic appointment",
                                      identity_line(store, caller["patient"]), "The second one", "Yes, please")
    assert "full name, date of birth" in replies[0] and "option 1" in replies[1]
    second = re.search(r"option 2, (.+?) and option 3", replies[1]).group(1)
    assert f"to {second}?" in replies[2]  # "the second one" is option 2, not "one"
    assert "asked our booking clerk" in replies[3]
    assert call["patient_id"] == caller["patient"] and call["outcome"] == "in_progress"
    action = next(a for a in call["actions"] if a["action"] == "reschedule_requested")
    assert action["status"] == "approval_required" and action["appointment"] == old
    proposed = store.fhir.read("Appointment", action["proposed"])
    assert proposed["status"] == "proposed" and store.fhir.read("Appointment", old)["status"] == "booked"
    # the clerk's one click
    requests = client.get("/api/voice/requests", headers=clerk).json()["requests"]
    request = next(r for r in requests if r["task_id"] == action["approval_task_id"])
    assert request["kind"] == "reschedule" and request["appointment"]["appointment_id"] == old
    assert request["proposed"]["appointment_id"] == action["proposed"] and request["mrn"]
    r = client.post(f"/api/agents/tasks/{request['task_id']}/decision", headers=clerk,
                    json={"decision": "approve", "note": "confirmed with the clinic"})
    assert r.status_code == 200, r.text
    assert r.json()["execution"]["status"] == "ok"
    assert store.fhir.read("Appointment", action["proposed"])["status"] == "booked"
    cancelled = store.fhir.read("Appointment", old)
    assert cancelled["status"] == "cancelled" and action["proposed"] in cancelled["cancelationReason"]["text"]
    end = client.post(f"/api/voice/calls/{call_id}/turn", headers=clerk, json={"text": "No, that's all"}).json()
    assert end["ended"] and end["call"]["outcome"] == "requested" and "reschedule" in end["call"]["summary"].lower()


def test_cancel_is_requested_and_only_a_clerk_confirms_it(fresh_state, client, login):
    store = fresh_state
    clerk = login(CLERK)
    caller = demo(store)["callers"]["en_heart"]
    _, replies, call = converse(client, clerk, identity_line(store, caller["patient"]) + " I want to cancel my "
                                "appointment.", "Yes")
    assert "To confirm: cancel" in replies[0] and "cancellation" in replies[1]
    action = next(a for a in call["actions"] if a["action"] == "cancel_requested")
    assert store.fhir.read("Appointment", caller["appointments"][0])["status"] == "booked"  # not yet
    nurse = login(NURSE)
    assert client.post(f"/api/agents/tasks/{action['approval_task_id']}/decision", headers=nurse,
                       json={"decision": "approve"}).status_code == 403
    r = client.post(f"/api/agents/tasks/{action['approval_task_id']}/decision", headers=clerk,
                    json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["execution"]["status"] == "ok"
    assert store.fhir.read("Appointment", caller["appointments"][0])["status"] == "cancelled"


def test_lookup_reads_the_appointments_after_the_identity_check_and_writes_nothing(fresh_state, client, login):
    store = fresh_state
    clerk = login(CLERK)
    caller = demo(store)["callers"]["en_fracture"]
    before = store.fhir.count("Communication"), store.fhir.count("Task")
    _, replies, call = converse(client, clerk, "When is my next appointment?", identity_line(store, caller["patient"]))
    assert "2 upcoming appointment(s)" in replies[1] and "Fracture Clinic, Main Building, Level 1" in replies[1]
    assert [i["intent"] for i in call["intents"]][:1] == ["lookup"]
    assert (store.fhir.count("Communication"), store.fhir.count("Task")) == before


def test_faq_answers_from_the_knowledge_base_without_identity_in_english_and_chinese(fresh_state, client, login):
    clerk = login(CLERK)
    _, replies, call = converse(client, clerk, "What are the visiting hours?", "Where do I park?", "That's all, thanks")
    assert "11 in the morning to 8 in the evening" in replies[0] and "garage" in replies[1]
    assert call["patient_id"] is None and call["outcome"] == "resolved"
    assert [a["section"] for a in call["actions"]] == ["visiting#general-hours", "parking#rates"]
    _, replies, call = converse(client, clerk, "请问停车场在哪里？")
    assert "停车场" in replies[0] and call["language"] == "zh-Hans"


def test_prep_instructions_in_the_patients_language_write_a_communication(fresh_state, client, login):
    store = fresh_state
    clerk = login(CLERK)
    caller = demo(store)["callers"]["zh_anticoag"]
    _, replies, call = converse(client, clerk, "我想问一下预约前要准备什么", chinese_identity_line(store, caller["patient"]),
                                "第一个")
    assert "抗凝血药" in replies[-1] and "准备" in replies[-1]
    action = next(a for a in call["actions"] if a["action"] == "prep_given")
    comm = store.fhir.read("Communication", action["refs"][0].split("/")[1])
    assert comm["status"] == "completed" and comm["category"][0]["coding"][0]["code"] == "prep-instructions"
    assert "Preparation instructions" in comm["payload"][0]["contentString"]
    assert {e["url"].rsplit(":", 1)[-1]: e.get("valueCode") for e in comm["extension"]}["call-language"] == "zh-Hans"


def test_a_clinical_question_is_never_answered_and_goes_to_the_nurse_line(fresh_state, client, login):
    store = fresh_state
    clerk = login(CLERK)
    caller = demo(store)["callers"]["en_heart"]
    _, replies, call = converse(client, clerk, "Should I take an extra water pill? My ankles are swollen.",
                                identity_line(store, caller["patient"]))
    assert "not able to answer medical questions" in replies[-1]
    action = next(a for a in call["actions"] if a["action"] == "nurse_line")
    task = store.fhir.read("Task", action["refs"][0].split("/")[1])
    assert task["code"]["coding"][0]["code"] == "nurse-line" and \
        task["performerType"][0]["coding"][0]["code"] == "nurse" and task["focus"]["reference"].startswith(
            "Communication/")
    comm = store.fhir.read("Communication", task["focus"]["reference"].split("/")[1])
    assert "extra water pill" in comm["payload"][0]["contentString"]


def test_transfer_on_request_on_distress_and_after_two_unrecognised_turns(fresh_state, client, login):
    store = fresh_state
    clerk = login(CLERK)
    before = len(tasks_of(store, "call-transfer"))
    _, replies, call = converse(client, clerk, "Can I talk to a real person?")
    assert "putting you through" in replies[0] and call["outcome"] == "transferred"
    _, replies, call = converse(client, clerk, "This is ridiculous, I'm so angry, I've waited for weeks")
    assert call["outcome"] == "transferred" and "distressed" in call["transfer_reason"]
    _, replies, call = converse(client, clerk, "Blue elephants dance tonight", "Purple sky over the lake")
    assert call["outcome"] == "transferred" and call["transfer_reason"] == "two turns without a recognised request"
    tasks = tasks_of(store, "call-transfer")
    assert len(tasks) == before + 3 and all(t["performerType"][0]["coding"][0]["code"] == "clerk" for t in tasks)
    assert any(t["priority"] == "urgent" for t in tasks)


def test_two_failed_identity_checks_put_the_caller_through_to_a_person(fresh_state, client, login):
    store = fresh_state
    clerk = login(CLERK)
    caller = demo(store)["callers"]["en_fracture"]
    _, replies, call = converse(client, clerk, "I want to cancel my appointment",
                                identity_line(store, caller["patient"], wrong=True),
                                identity_line(store, caller["patient"], wrong=True))
    assert "don't match" in replies[1] and "putting you through" in replies[2]
    assert call["patient_id"] is None and call["identity_failures"] == 2 and call["outcome"] == "transferred"
    assert not any(a["action"] in ("cancel_requested", "lookup") for a in call["actions"])


def test_a_relative_calls_in_mandarin_for_the_patient(fresh_state, client, login):
    store = fresh_state
    clerk = login(CLERK)
    caller = demo(store)["callers"]["zh_anticoag"]
    p = store.fhir.read("Patient", caller["patient"])
    name = next(n["text"] for n in p["name"] if n.get("use") == "usual")
    card = next(i["value"] for i in p["identifier"] if i["system"] == HCN_SYSTEM)
    y, m, d = p["birthDate"].split("-")
    _, replies, call = converse(client, clerk, "你好，我想帮我妈妈查一下她的预约",
                                f"我妈妈叫{name}，{y}年{int(m)}月{int(d)}日出生，健康卡尾号{card[-4:]}")
    assert call["patient_id"] == caller["patient"] and call["caller_relation"] == "child"  # "my mother": her child
    assert call["language"] == "zh-Hans" and "预约" in replies[-1]
    # every Chinese reply carries the same hand-written reply in English (the storyline's subtitles); the greeting is
    # English already
    agent = [t for t in call["transcript"] if t["role"] == "agent"]
    assert agent[0]["lang"] == "en" and agent[0]["gloss"] is None
    assert [t["lang"] for t in agent[1:]] == ["zh-Hans", "zh-Hans"]
    assert agent[1]["gloss"].startswith("I can help with that. First I need to confirm who the patient is")
    given = next(n["given"][0] for n in p["name"] if n.get("use") == "official")
    assert agent[2]["gloss"].startswith(f"Thank you, I've found the record for {given}. {given} has ")
    assert all(not P.CJK.search(t["gloss"]) for t in agent[1:])
    # switching to Chinese later in a call: the subtitle says so (the English template says the opposite)
    _, replies, call = converse(client, clerk, "What are the visiting hours?", "请问停车场在哪里？")
    gloss = call["transcript"][-1]["gloss"]
    assert replies[-1].startswith("好的，我们用中文继续。") and gloss.startswith("Sure, let's continue in Chinese. ")


@pytest.mark.parametrize("lang, thanks", [("en", "Thank you"), ("zh-Hans", "谢谢"), ("zh-Hant", "謝謝")])
def test_the_ed_registration_reply_thanks_the_caller_once(lang, thanks):
    """An ED arrival's reply after the identity check is "verified", "arrival_registered" and "anything_else" in one
    turn: one thank-you, not "Thank you, I've found the record for Margaret. Thank you. I have prepared…"."""
    reply = P.cat(P.say(P.CC["verified"], lang, first="Margaret"),
                  P.say(P.CC["arrival_registered"], lang, first="Margaret"), P.say(P.CC["anything_else"], lang))
    assert reply.count(thanks) == 1 and reply.en.count("Thank you") == 1
    assert reply.en.startswith("Thank you, I've found the record for Margaret. I have prepared Margaret's registration")


def test_the_call_centre_works_without_the_model(fresh_state, client, login):
    set_gateway(LlmGateway(Down()))
    clerk = login(CLERK)
    _, replies, call = converse(client, clerk, "What are the visiting hours?")
    assert "11 in the morning" in replies[0] and call["model_status"] == "unavailable"
    set_gateway(LlmGateway(MockProvider(latency_s=0)))


def test_transcripts_reach_the_model_de_identified_in_an_untrusted_block(fresh_state, client, login):
    store = fresh_state
    seen = []

    class Spy(MockProvider):
        def complete_json(self, *, task, text, **kw):
            seen.append((task, text))
            return super().complete_json(task=task, text=text, **kw)

    set_gateway(LlmGateway(Spy(latency_s=0)))
    clerk = login(CLERK)
    caller = demo(store)["callers"]["en_fracture"]
    line = identity_line(store, caller["patient"])
    converse(client, clerk, "I want to check my appointments", line, "Is it safe to stop my pills before the scan?")
    texts = [t for task, t in seen if task == "call_center"]
    assert texts and all('<untrusted source="utterance">' in t for t in texts)
    name = line.split("This is ")[1].split(",")[0]
    assert not any(name in t or name.split()[-1] in t for t in texts)  # identity turns never go to the model
    set_gateway(LlmGateway(MockProvider(latency_s=0)))


# ---------- follow-up calls: scheduling and the four conditions ----------


def _schedule(store, key: str):
    return followup.schedule(store, demo(store)["followups"][key]["stay"], source="sweep")


def test_the_sweep_schedules_the_calls_due_and_leaves_the_early_one(fresh_state):
    store = fresh_state
    scheduled = followup.sweep(store)
    fu = demo(store)["followups"]
    rows = {k: followup.get(followup.call_id_for(c["stay"]), store) for k, c in fu.items()}
    assert rows["zh"].status == rows["en"].status == "scheduled"
    assert rows["early"] is None  # discharged about 20 hours ago: not due
    assert {followup.call_id_for(fu["zh"]["stay"]), followup.call_id_for(fu["en"]["stay"])} <= set(scheduled)
    zh = rows["zh"]
    assert zh.language == "zh-Hans" and zh.script[0]["key"] == "identity" and zh.script[-1]["key"] == "other"
    assert any(q["kind"] == "flag" for q in zh.script) and any(q["kind"] == "wound" for q in zh.script)
    comm = store.fhir.read("Communication", zh.communication_id)
    assert comm["status"] == "preparation" and comm["category"][0]["coding"][0]["code"] == "followup-call"
    assert followup.sweep(store) == []  # once


@pytest.mark.parametrize("key,missing", [("no_consent", "consent"), ("no_phone", "phone"),
                                         ("unsigned", "instructions")])
def test_a_missing_condition_gives_a_nurse_the_manual_follow_up(fresh_state, key, missing):
    store = fresh_state
    call = _schedule(store, key)
    assert call.status == "manual" and call.missing == [missing] and call.review_status == "open"
    task = store.fhir.read("Task", call.task_id)
    assert task["code"]["coding"][0]["code"] == "followup-manual" and \
        task["performerType"][0]["coding"][0]["code"] == "nurse" and task["priority"] == "routine"
    assert call.reason.split(";")[0].strip() in task["description"]
    assert call.communication_id is None and not call.script  # no call, no script


def test_no_discharge_time_is_the_fourth_degradation(fresh_state):
    """The 48-hour condition: a stay without a recorded discharge cannot be timed, so a nurse follows up."""
    store = fresh_state
    inpatient = next(e for e in store.fhir.search("Encounter", cls="IMP", status="in-progress"))
    call = followup.schedule(store, inpatient["id"], source="sweep")
    assert call.status == "manual" and "discharge" in call.missing
    assert "no discharge time" in store.fhir.read("Task", call.task_id)["description"]


def test_the_call_is_due_48_hours_after_discharge(fresh_state):
    store = fresh_state
    early = demo(store)["followups"]["early"]
    assert followup.call_id_for(early["stay"]) not in {c["id"] for c in followup.candidates(store)}
    discharged = parse(store.fhir.read("Encounter", early["stay"])["period"]["end"])
    store.modules["hospital_clock"] = {**store.modules["hospital_clock"],
                                       "now": (discharged + timedelta(hours=48, minutes=5)).isoformat()}
    assert early["stay"] in {c["id"] for c in followup.candidates(store)}


def test_consent_withdrawn_after_scheduling_stops_the_call(fresh_state):
    store = fresh_state
    call = _schedule(store, "en")
    fhir = FhirGateway(Actor.of(store.staff[NURSE]), "consent_management")
    consent_mod.change_consent(fhir, call.patient_id, "followup_call", False)
    with pytest.raises(followup.CallError):
        followup.start_call(store, call.id)
    call = followup.get(call.id, store)
    assert call.status == "manual" and "consent" in call.missing and call.task_id


# ---------- follow-up calls: the call, escalation and write-back ----------


def _answer_all(store, call_id: str, answers: dict[str, str], default: str = "no") -> list[str]:
    replies = []
    while True:
        call = followup.get(call_id, store)
        q = call.script[call.step]
        reply, call, ended = followup.answer(store, call_id, answers.get(q["key"], answers.get(q["kind"], default)))
        replies.append(reply)
        if ended:
            return replies


def _dob(store, patient_id: str) -> str:
    return store.fhir.read("Patient", patient_id)["birthDate"]


def test_the_chinese_call_escalates_heavy_wound_drainage_at_once_without_the_model(fresh_state):
    store = fresh_state
    call = _schedule(store, "zh")
    events = len(bus.events(store, types=["followup.escalated"], limit=500))
    set_gateway(LlmGateway(Down()))  # the rules alone escalate
    intro, call = followup.start_call(store, call.id)
    assert "出生日期" in intro and call.status == "in_progress"
    y, m, d = _dob(store, call.patient_id).split("-")
    reply, call, ended = followup.answer(store, call.id, f"{y}年{int(m)}月{int(d)}日")
    assert "感觉怎么样" in reply and not ended
    reply, call, ended = followup.answer(store, call.id, "还行，就是伤口一直在渗液，纱布都湿透了")
    assert "护士会在30分钟内给您回电话" in reply and call.escalated and call.escalated_by == "rules"
    assert call.script[call.step]["key"] == "other"  # straight to "anything else"
    task = store.fhir.read("Task", call.task_id)
    assert task["code"]["coding"][0]["code"] == "followup-escalation" and task["priority"] == "urgent"
    assert len(bus.events(store, types=["followup.escalated"], limit=500)) == events + 1
    reply, call, ended = followup.answer(store, call.id, "没有了")
    assert ended and call.status == "completed" and "再见" in reply
    assert call.classifier["status"] == "unavailable" and [h["code"] for h in call.rule_hits] == ["wound_drainage"]
    # the subtitles: each of the agent's turns in English, the same questions and sentences
    glosses = [t.gloss for t in call.transcript if t.role == "agent"]
    assert glosses[0].startswith("Hello, this is the automated follow-up call") and \
        glosses[0].endswith("please tell me your date of birth.")
    assert glosses[1] == "Thank you. How have you been feeling since you went home?"
    assert glosses[2].startswith("Thank you for telling me. A nurse will call you back within 30 minutes. Is there")
    assert glosses[3].startswith("Thank you, ") and "A nurse will call you back within 30 minutes" in glosses[3]
    assert not any(P.CJK.search(g) for g in glosses)
    set_gateway(LlmGateway(MockProvider(latency_s=0)))


def test_write_back_communication_observation_and_task(fresh_state):
    """Interface test: one Communication per call, patient-reported Observations, the nurse Task."""
    store = fresh_state
    call = _schedule(store, "zh")
    followup.start_call(store, call.id)
    followup.answer(store, call.id, _dob(store, call.patient_id))
    followup.answer(store, call.id, "我昨天体温38度7，伤口一直在渗液")
    _, call, _ = followup.answer(store, call.id, "没有了")
    comm = store.fhir.read("Communication", call.communication_id)
    assert comm["status"] == "completed" and comm["category"][0]["coding"][0]["code"] == "followup-call"
    exts = {e["url"].rsplit(":", 1)[-1]: e for e in comm["extension"]}
    assert exts["escalated"]["valueBoolean"] is True and comm["priority"] == "urgent"
    assert call.summary and comm["payload"][0]["contentString"].startswith(call.summary[:40])
    about = {a["reference"] for a in comm["about"]}
    assert f"Task/{call.task_id}" in about and len(call.observation_ids) >= 2
    comms = [c for c in store.fhir.search("Communication", encounter=call.encounter_id)
             if c["category"][0]["coding"][0]["code"] == "followup-call"]
    assert len(comms) == 1  # one per call
    observations = [store.fhir.read("Observation", o) for o in call.observation_ids]
    codes = set()
    for obs in observations:
        assert obs["status"] == "preliminary" and f"Observation/{obs['id']}" in about
        assert any(c["system"] == OBS_CATEGORY and c["code"] == PATIENT_REPORTED for c in obs["category"][0]["coding"])
        codes |= {c["code"] for c in obs["code"]["coding"]}
    fever = next(o for o in observations if any(c["code"] == "8310-5" for c in o["code"]["coding"]))
    assert fever["valueQuantity"]["value"] == 38.7
    assert {"wound_drainage", "fever"} <= codes
    task = store.fhir.read("Task", call.task_id)
    assert task["performerType"][0]["coding"][0]["code"] == "nurse" and task["encounter"]["reference"].endswith(
        call.encounter_id)


def test_a_clear_english_call_does_not_escalate(fresh_state):
    store = fresh_state
    call = _schedule(store, "en")
    followup.start_call(store, call.id)
    followup.answer(store, call.id, _dob(store, call.patient_id))
    replies = _answer_all(store, call.id, {"open": "I'm feeling fine, thank you.", "med": "Yes, every day.",
                                           "followup": "Yes, I know.", "wound": "It looks fine.",
                                           "care": "Yes, all good."})
    call = followup.get(call.id, store)
    assert not call.escalated and call.review_status == "none" and "Take care" in replies[-1]
    assert call.classifier["status"] == "ok" and call.classifier["escalate"] is False
    comm = store.fhir.read("Communication", call.communication_id)
    assert comm["status"] == "completed" and {e["url"].rsplit(":", 1)[-1]: e for e in comm["extension"]}[
        "escalated"]["valueBoolean"] is False


def test_an_emergency_gets_the_911_prompt_and_ends_the_call(fresh_state):
    store = fresh_state
    call = _schedule(store, "en")
    followup.start_call(store, call.id)
    followup.answer(store, call.id, _dob(store, call.patient_id))
    reply, call, ended = followup.answer(store, call.id, "Not great, I have chest pain and I can't catch my breath")
    assert ended and "911" in reply and call.emergency and call.priority == "stat"
    assert store.fhir.read("Task", call.task_id)["priority"] == "stat"


def test_the_classifier_escalates_what_the_rules_do_not_see(fresh_state):
    store = fresh_state
    call = _schedule(store, "en")
    followup.start_call(store, call.id)
    followup.answer(store, call.id, _dob(store, call.patient_id))
    _answer_all(store, call.id, {"open": "A bit dizzy when I stand up, otherwise okay.", "med": "Yes",
                                 "followup": "Yes", "care": "Yes"})
    call = followup.get(call.id, store)
    assert call.escalated and call.escalated_by == "model" and not call.rule_hits and call.priority == "urgent"


def test_two_wrong_birth_dates_stop_the_call(fresh_state):
    store = fresh_state
    call = _schedule(store, "en")
    followup.start_call(store, call.id)
    followup.answer(store, call.id, "January 1, 1901")
    reply, call, ended = followup.answer(store, call.id, "1901-01-01")
    assert ended and call.status == "not_confirmed" and call.task_id and call.review_status == "open"


def test_red_flag_rules_negation_and_temperature():
    assert [h.code for h in redflags.scan("No chest pain, no fever, I haven't fallen.")] == []
    assert [h.code for h in redflags.scan("My temperature was 38.7 this morning")] == ["fever"]
    assert redflags.scan("a bit of a fever, 37.8") == []
    assert [h.code for h in redflags.scan("伤口一直在渗液，纱布都湿透了")] == ["wound_drainage"]
    assert redflags.scan("伤口有一点点渗液") == []
    assert [h.code for h in redflags.scan("I want to talk to a nurse")] == ["wants_person"]
    assert {h.code for h in redflags.scan("我昨天摔倒了，吃不下东西")} == {"fall", "cannot_eat_drink"}


# ---------- the nurse queue ----------


def _escalated(store) -> followup.FollowupCall:
    call = _schedule(store, "zh")
    followup.start_call(store, call.id)
    followup.answer(store, call.id, _dob(store, call.patient_id))
    followup.answer(store, call.id, "伤口一直在渗液，纱布都湿透了")
    _, call, _ = followup.answer(store, call.id, "没有了")
    return call


def test_the_nurse_queue_puts_escalations_first_with_the_patients_words(fresh_state, client, login):
    store = fresh_state
    followup.sweep(store)
    call = _escalated(store)
    nurse = login(NURSE)
    items = client.get("/api/voice/followups", headers=nurse).json()["items"]
    assert items[0]["id"] == call.id and items[0]["priority"] == "urgent" and items[0]["mrn"]
    assert items[0]["red_flags"][0]["code"] == "wound_drainage" and "渗液" in items[0]["patient_words"][0]
    assert items[0]["discharged_at"] and items[0]["summary"]
    assert all(i["unit_id"] == "MEDA" for i in items)
    summary = client.get("/api/voice/followups/summary", headers=nurse).json()
    assert summary["escalated"] >= 1 and summary["urgent"] >= 1
    assert client.get("/api/voice/followups", headers=login(CLERK)).status_code == 403
    other = next(u for u in store.staff.values() if u.role == Role.NURSE and "MEDA" not in u.unit_ids)
    assert call.id not in {i["id"] for i in queue.items(store, other)}


def test_an_item_outside_the_nurses_72_hour_scope_stays_listed_without_the_patients_details(fresh_state, client,
                                                                                          login):
    store = fresh_state
    call = _escalated(store)
    enc = store.fhir.read("Encounter", call.encounter_id)
    enc["period"]["end"] = fhir_datetime(hospital_now(store) - timedelta(days=5))  # left the WP4 window
    store.fhir.update(enc)
    nurse = login(NURSE)
    r = client.get("/api/voice/followups", headers=nurse)
    assert r.status_code == 200, r.text  # one old item does not break the queue
    item = next(i for i in r.json()["items"] if i["id"] == call.id)
    assert item["restricted"] and item["priority"] == "urgent" and item["escalated"]
    assert item["mrn"] is None and item["summary"] is None and item["patient_words"] == [] and item["red_flags"] == []
    assert client.get(f"/api/voice/followups/{call.id}", headers=nurse).status_code == 403
    assert client.post(f"/api/voice/followups/{call.id}/actions", headers=nurse,
                       json={"kind": "close", "note": "done"}).status_code == 403


def test_nurse_actions_write_communications_close_and_refer(fresh_state, client, login):
    store = fresh_state
    call = _escalated(store)
    nurse = login(NURSE)
    r = client.post(f"/api/voice/followups/{call.id}/actions", headers=nurse,
                    json={"kind": "callback", "note": "Called back: drainage soaking one gauze a day"})
    assert r.status_code == 200 and r.json()["review_status"] == "open"
    r = client.post(f"/api/voice/followups/{call.id}/actions", headers=nurse,
                    json={"kind": "refer", "note": "Wound review today by the physician"})
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["review_status"] == "referred" and item["task"]["status"] == "completed"
    kinds = [a["kind"] for a in item["nurse_actions"]]
    assert kinds == ["callback", "refer"]
    comms = [store.fhir.read("Communication", a["communication_id"]) for a in item["nurse_actions"]]
    assert [c["category"][0]["coding"][0]["code"] for c in comms] == ["nurse-callback", "nurse-referral"]
    referral = store.fhir.read("Task", item["nurse_actions"][1]["task_id"])
    assert referral["performerType"][0]["coding"][0]["code"] == "doctor" and referral["priority"] == "urgent"
    assert client.post(f"/api/voice/followups/{call.id}/actions", headers=nurse,
                       json={"kind": "close", "note": "done"}).status_code == 409  # already referred
    assert client.post(f"/api/voice/followups/{call.id}/actions", headers=login(PHYSICIAN),
                       json={"kind": "close", "note": "done"}).status_code == 403


def test_a_nurse_action_is_at_hospital_time_as_the_communication_it_writes(fresh_state, client, login):
    """The call-back was stamped with the wall clock (the storyline's trail showed 03:39 next to hospital times,
    live run 10-18); the Communication it writes, the call's due time and the escalation are hospital time."""
    store = fresh_state
    call = _escalated(store)
    item = client.post(f"/api/voice/followups/{call.id}/actions", headers=login(NURSE),
                       json={"kind": "callback", "note": "Called back: drainage soaking one gauze a day"}).json()
    action = item["nurse_actions"][0]
    sent = store.fhir.read("Communication", action["communication_id"])["sent"]
    assert parse(action["at"]) == parse(sent).replace(tzinfo=None, microsecond=0)
    assert parse(action["at"]) == hospital_now(store).replace(microsecond=0)


def test_a_follow_up_call_is_timed_on_the_hospital_clock(fresh_state, client, login):
    """The call's start, end and last update were the wall clock, in the item that shows its discharge and due time
    (hospital time): the call could start before it was due. Every time on the call is hospital time."""
    store = fresh_state
    call = _escalated(store)  # scheduled, placed, answered and finished while the hospital clock stands
    now = hospital_now(store)
    assert call.created_at == call.started_at == call.ended_at == call.updated_at == now
    assert {t.at for t in call.transcript} == {now}
    item = client.get(f"/api/voice/followups/{call.id}", headers=login(NURSE)).json()
    assert parse(item["discharged_at"]) < parse(item["due_at"]) <= parse(item["started_at"]) <= parse(item["ended_at"])
    assert parse(item["updated_at"]) == now


# ---------- gateway increments ----------


def test_the_gateway_takes_patient_reported_observations_only(fresh_state):
    store = fresh_state
    enc = next(e for e in store.fhir.search("Encounter", cls="IMP", status="in-progress"))
    pid = enc["subject"]["reference"].split("/")[1]
    with registry.temporary("wp8_test", {"kind": "module", "allowed_tools": ["recordPatientReportedObservation"],
                                         "data_scope": {"resources": ["Patient"], "scope": "hospital"}}):
        fhir = FhirGateway(Actor.system("wp8-test"), "wp8_test")
        obs = {"resourceType": "Observation", "status": "preliminary", "code": {"text": "Wound drainage"},
               "category": [{"coding": [{"system": OBS_CATEGORY, "code": PATIENT_REPORTED}]}],
               "subject": {"reference": f"Patient/{pid}"}, "encounter": {"reference": f"Encounter/{enc['id']}"},
               "effectiveDateTime": fhir_datetime(hospital_now(store)), "valueString": "a lot of fluid"}
        stored = fhir.create(obs)
        assert stored.status == "preliminary"
        with pytest.raises(FhirAccessDenied):
            fhir.create({**obs, "status": "final"})
        with pytest.raises(FhirAccessDenied):
            fhir.create({**obs, "category": [{"coding": [{"code": "vital-signs"}]}]})


def test_only_a_clerk_cancels_or_moves_a_booked_appointment(fresh_state):
    store = fresh_state
    caller = demo(store)["callers"]["en_fracture"]
    appt = caller["appointments"][0]
    nurse = FhirGateway(Actor.of(store.staff[NURSE]), "call_center")
    with pytest.raises(FhirAccessDenied):
        nurse.cancel_appointment(appt, "test")
    clerk = FhirGateway(Actor.of(store.staff[CLERK]), "call_center")
    assert clerk.cancel_appointment(appt, "patient asked").status == "cancelled"
