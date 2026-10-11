"""The hospital call centre (spec 7.4): the dialog of an inbound call, text-simulated in the console.

One run of the `call_center` agent per caller turn (acting as itself: the caller is the patient or a relative, not
the member of staff running the console). In each turn, in this order:

1. the rule layer: an emergency gets the 911 prompt and the nurse line, distress or a request for a person is put
   through at once (intents.py), whatever the model says;
2. a pending step (the identity check, which appointment, which new time, yes or no) is answered in code;
3. otherwise the model recognises the intent (call_agent.py; de-identified, untrusted block), or the keyword rules
   when it is unavailable; two turns without a recognised request put the caller through to a person.

The six intents of 7.4: reschedule or cancel (a proposed Appointment through the gateway and the clerk's approval:
the privileged tools moveAppointment / cancelBookedAppointment open an approval Task, which is the request's Task),
appointment lookup, FAQ (kb.py), prep instructions in the caller's language (a Communication), clinical questions
(never answered: a Communication and a nurse-line Task), transfer to a person (a Task). Personal intents need the
identity check first (identity.py, findCaller: name + birth date + the card's last four digits; two failures put the
caller through). Every sentence comes from phrases.py in English or Chinese; nothing the model writes is said.

When the agent may not run (prod mode before its eval passed) the dialog runs as the `voice_services` module:
the same tools through the gateway, the keyword rules instead of the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.agents import registry
from app.agents.runtime import AgentRun, Context, runtime
from app.core.context import acting_as_system
from app.core.models import StaffUser
from app.core.store import Store, get_store
from app.ehr import codes as C
from app.ehr.clock import hospital_now
from app.ehr.gateway import Actor, FhirGateway
from app.fhir.dt import fhir_datetime, parse
from app.llm.deid import Pseudonymizer
from app.llm.freetext_deid import FreeTextDeidentifier
from app.modules.voice_services import call_agent, identity, intents, kb
from app.modules.voice_services import phrases as P
from app.modules.voice_services.models import HospitalCall, Turn

TABLE = "voice_calls"
AGENT = "call_center"
MODULE = "voice_services"
MAX_IDENTITY_FAILURES = 2
MAX_UNRECOGNISED = 2


def table(store: Store | None = None):
    return (store or get_store()).module(TABLE, dict)


def get(call_id: str, store: Store | None = None) -> HospitalCall | None:
    return table(store).get(call_id)


def save(call: HospitalCall, store: Store | None = None) -> None:
    table(store)[call.id] = call
    (store or get_store()).touch()


def system_fhir() -> FhirGateway:
    """The module's own reads (system actor, audited): the verified patient for de-identification, their latest
    encounter for a nurse-line Task, a follow-up's conditions."""
    return FhirGateway(Actor.system("voice-services"), MODULE)


def acting_agent(agent_id: str) -> str:
    """The agent, or the module when the release gate refuses the agent (prod mode before its eval passed)."""
    spec = registry.agent(agent_id)
    return agent_id if spec is not None and registry.gate(spec) is None else MODULE


def patient_name(patient: dict | None, lang: str) -> str:
    """How the agent addresses the patient: the Chinese name in Chinese when there is one, else the given name."""
    if not patient:
        return ""
    names = patient.get("name") or []
    if lang != "en":
        usual = next((n.get("text") for n in names if n.get("use") == "usual" and n.get("text")), None)
        if usual:
            return usual
    official = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    return (official.get("given") or [official.get("text") or ""])[0]


def preferred_language(patient: dict | None) -> str:
    code = next((c.get("code") for comm in (patient or {}).get("communication") or []
                 for c in (comm.get("language") or {}).get("coding") or []), "en")
    return P.lang_of(code)


# ---------- the call ----------


def start(store: Store, user: StaffUser) -> HospitalCall:
    call = HospitalCall(id=store.next_id("VC"), started_at=datetime.now(),
                        hospital_time=fhir_datetime(hospital_now(store)), staff_user_id=user.id)
    call.transcript.append(Turn(role="agent", text=P.say(P.CC["greeting"], "en"), lang="en"))
    save(call, store)
    return call


def turn(store: Store, call_id: str, text: str) -> tuple[str, HospitalCall, bool]:
    call = get(call_id, store)
    if call is None:
        raise LookupError(f"No call {call_id}")
    if call.ended_at:
        raise ValueError("The call has ended")
    text = " ".join((text or "").split())[:500]
    if not text:
        raise ValueError("Empty utterance")
    switched = _switch_language(call, text)
    call.transcript.append(Turn(role="caller", text=text, lang=call.language))
    with acting_as_system(), runtime.start(acting_agent(AGENT), user=None, context_id=f"VoiceCall/{call.id}") as run:
        dialog = Dialog(store, call, run)
        reply = dialog.handle(text)
        if switched and not dialog.ended:
            reply = f"{P.say(P.CC['switched'], call.language)} {reply}"
        run.finish(outcome="completed")
    call.run_ids.append(run.run_id)
    call.transcript.append(Turn(role="agent", text=reply, lang=call.language, intent=dialog.intent,
                                source=dialog.source))
    if dialog.ended:
        _close(call)
    save(call, store)
    return reply, call, dialog.ended


def end(store: Store, call_id: str) -> HospitalCall:
    call = get(call_id, store)
    if call is None:
        raise LookupError(f"No call {call_id}")
    if not call.ended_at:
        if call.outcome == "in_progress" and not call.actions:
            call.outcome = "abandoned"
        _close(call)
        save(call, store)
    return call


def _switch_language(call: HospitalCall, text: str) -> bool:
    """The caller's language: Chinese as soon as they speak it or ask for it, English when they ask or speak
    English (not while they are spelling a name or reading out numbers)."""
    zh = P.lang_of("zh-TW") if call.language == "zh-Hant" else "zh-Hans"
    if intents.ASK_ENGLISH.search(text) and not intents.CHINESE.search(text):
        new = "en"
    elif intents.CHINESE.search(text) or intents.ASK_CHINESE.search(text):
        new = call.language if call.language != "en" else zh
    elif call.language != "en" and call.state.get("pending") != "identity" and len(text.split()) >= 4:
        new = "en"
    else:
        new = call.language
    changed = new != call.language
    call.language = new
    return changed and len(call.transcript) > 1


def _close(call: HospitalCall) -> None:
    call.ended_at = datetime.now()
    if call.outcome == "in_progress":
        call.outcome = "requested" if any(a.get("status") == "approval_required" or
                                          (a.get("action") == "ed_registration" and a.get("task_id"))
                                          for a in call.actions) else "resolved"
    call.summary = summarise(call)


INTENT_WORDS = {"reschedule": "to reschedule an appointment", "cancel": "to cancel an appointment",
                "lookup": "when their appointments are", "faq": "general information", "prep": "how to prepare",
                "clinical": "a clinical question", "transfer": "to speak to a person", "goodbye": None,
                "arrival": "to register an arrival at the emergency department",
                "other": None}


def summarise(call: HospitalCall) -> str:
    """The call log's summary, built from the structured result (exact; no model)."""
    who = ("the patient's " + call.caller_relation) if call.caller_relation else \
        "the patient (verified)" if call.patient_id else "a caller who was not verified"
    asked = [w for w in dict.fromkeys(INTENT_WORDS.get(i["intent"]) for i in call.intents) if w]
    parts = [f"Call from {who}" + (f" asking {', '.join(asked)}" if asked else "") + "."]
    for a in call.actions:
        parts.append(a.get("summary") or a["action"])
    if call.outcome == "transferred":
        parts.append(f"Put through to staff: {call.transfer_reason}.")
    if call.identity_failures:
        parts.append(f"Identity check failed {call.identity_failures} time(s).")
    return " ".join(parts)


# ---------- the dialog of one turn ----------


@dataclass
class _Appt:
    id: str
    service: str
    display: str
    start: datetime
    minutes: int
    location: str
    encounter_id: str | None


class Dialog:
    def __init__(self, store: Store, call: HospitalCall, run: AgentRun) -> None:
        self.store, self.call, self.run = store, call, run
        self.ended = False
        self.intent: str | None = None
        self.source: str | None = None
        self._patient: dict | None = None

    # ----- helpers -----

    @property
    def st(self) -> dict:
        return self.call.state

    @property
    def lang(self) -> str:
        return self.call.language

    def say(self, key: str, **values) -> str:
        return P.say(P.CC[key], self.lang, **values)

    @property
    def patient(self) -> dict | None:
        if self._patient is None and self.call.patient_id:
            found = system_fhir().read("Patient", self.call.patient_id)
            self._patient = found.to_fhir() if found is not None else None
        return self._patient

    def _act(self, action: str, result=None, summary: str = "", **extra) -> None:
        self.call.actions.append({"action": action, "status": result.status if result is not None else "ok",
                                  "refs": list(result.refs) if result is not None else [],
                                  "approval_task_id": getattr(result, "approval_task_id", None), "summary": summary,
                                  "at": datetime.now().isoformat(timespec="seconds"), **extra})

    def _log_intent(self, rec: intents.Recognised, text_turn: int | None = None) -> None:
        self.intent, self.source = rec.intent, rec.source
        self.call.intents.append({"intent": rec.intent, "source": rec.source, "confidence": round(rec.confidence, 2),
                                  "topic": rec.faq_topic, "turn": text_turn or len(self.call.transcript) - 1})

    def _context(self) -> Context:
        pseudo = Pseudonymizer()
        freetext = FreeTextDeidentifier(pseudo, reference=hospital_now(self.store).date())
        if self.patient:
            freetext.learn_fhir([self.patient])
        return Context([], [], pseudo, freetext)

    def _state_line(self) -> str:
        return (f"language {self.lang}; caller verified: {'yes' if self.call.patient_id else 'no'}; "
                f"waiting for: {self.st.get('pending') or 'a request'}")

    def _latest_encounter(self) -> str | None:
        found = system_fhir().search_encounters(patient_id=self.call.patient_id) if self.call.patient_id else []
        return found[0].id if found else None

    def _appt(self, view: dict) -> _Appt:
        return _Appt(view["appointment_id"], view["service"], view.get("service_display") or view["service"],
                     parse(view["start"]), view.get("minutes") or 30, view.get("location") or "",
                     view.get("encounter_id"))

    def _service_name(self, a: _Appt) -> str:
        service = P.SERVICES.get(a.service)
        return P.say(service["name"], self.lang) if service else a.display

    def _place(self, a: _Appt) -> str:
        service = P.SERVICES.get(a.service)
        return P.say(service["place"], self.lang) if service else a.location

    # ----- the turn -----

    def handle(self, text: str) -> str:
        if intents.emergency(text):
            self._log_intent(intents.Recognised("clinical", 1.0, "rules", emergency=True))
            return self._emergency(text)
        if intents.DISTRESS.search(text):
            self._log_intent(intents.Recognised("transfer", 1.0, "rules", distress=True))
            return self._transfer("the caller is distressed", urgent=True, key="transfer_distress")
        if intents.HUMAN.search(text):
            self._log_intent(intents.Recognised("transfer", 1.0, "rules"))
            return self._transfer("the caller asked for a person")
        if (arriving := intents.arrival(text)) and not self.st.get("registration"):  # WP9: an ED arrival
            self._log_intent(intents.Recognised("arrival", 1.0, "rules"))
            self.st["arrival"] = arriving
            if not self.call.patient_id:
                self.st.update(after={"intent": "arrival", "text": text}, pending="identity", id={"names": []})
                return self._identity(text, first=True)
            return self._arrival()
        pending = self.st.get("pending")
        if pending == "identity":
            return self._identity(text)
        if pending == "choose" and (reply := self._choose(text)):
            return reply
        if pending == "pick_slot" and (reply := self._pick(text)):
            return reply
        if pending == "confirm" and (reply := self._confirm(text)):
            return reply
        if pending == "anything_else" and intents.yes_no(text) == "no" and intents.recognise(text).intent not in \
                intents.PERSONAL | {"faq", "transfer"}:
            return self._goodbye()
        return self._open(text)

    def _recognise(self, text: str) -> intents.Recognised:
        rules = intents.recognise(text)
        if self.run.spec.kind == "module":
            self.call.model_status = "not_used"
            return rules
        rec = call_agent.recognise(self.run, text, self._state_line(), self._context())
        if rec is None:
            self.call.model_status = "unavailable"
            return rules
        self.call.model_status = "ok"
        if rec.intent == "other" and rules.intent != "other":
            return rules
        return rec

    def _open(self, text: str) -> str:
        rec = self._recognise(text)
        self._log_intent(rec)
        if rec.emergency:
            return self._emergency(text)
        if rec.distress:
            return self._transfer("the caller is distressed", urgent=True, key="transfer_distress")
        if (rec.intent == "other" or rec.confidence < 0.5) and not self.call.patient_id and \
                identity.parse(text, expecting=False).birth_date:  # the caller starts with who they are
            self.st.update(pending="identity", id={"names": []})
            return self._identity(text)
        if rec.intent == "other" or rec.confidence < 0.5:
            self.call.unrecognised += 1
            if self.call.unrecognised >= MAX_UNRECOGNISED:
                return self._transfer("two turns without a recognised request", key="transfer_unrecognised")
            again = self.st.get("prompt") if self.st.get("pending") in ("choose", "pick_slot", "confirm") else None
            return f"{self.say('not_understood')} {again or self.say('options')}"
        for key in ("pending", "prompt", "choose_for", "options", "slots", "confirm", "appointment"):
            self.st.pop(key, None)
        return self._dispatch(rec, text)

    def _dispatch(self, rec: intents.Recognised, text: str) -> str:
        if rec.intent == "goodbye":
            return self._goodbye()
        if rec.intent == "transfer":
            return self._transfer("the caller asked for a person")
        if rec.intent == "faq":
            return self._faq(text, rec.faq_topic)
        if not self.call.patient_id:
            self.st["after"] = {"intent": rec.intent, "text": text}
            self.st["pending"] = "identity"
            self.st["id"] = {"names": []}
            return self._identity(text, first=True)
        return self._personal(rec.intent, text)

    # ----- identity -----

    def _identity(self, text: str, first: bool = False) -> str:
        parsed = identity.parse(text, expecting=not first)
        pieces = self.st.setdefault("id", {"names": []})
        if parsed.name:
            pieces["names"] = [*pieces.get("names", []), parsed.name][-4:]
        for key in ("birth_date", "card_last4"):
            if getattr(parsed, key):
                pieces[key] = getattr(parsed, key)
        if parsed.relation:
            self.call.caller_relation = parsed.relation
        if not (pieces.get("names") and pieces.get("birth_date") and pieces.get("card_last4")):
            if not parsed.found and not first:
                rec = self._recognise(text)  # changed their mind? general questions need no identity
                if rec.intent in ("faq", "goodbye", "transfer") and rec.confidence >= 0.5:
                    self._log_intent(rec)
                    self.st.pop("pending", None)
                    return self._dispatch(rec, text)
            if first and not parsed.found:
                return self.say("ask_identity")
            missing = "ask_name" if not pieces.get("names") else "ask_dob" if not pieces.get("birth_date") else \
                "ask_card"
            return self.say(missing)
        result = self.run.tool("findCaller", {"name": identity.combine(pieces["names"]),
                                              "birth_date": pieces["birth_date"], "card_last4": pieces["card_last4"]})
        if not result.ok:
            return self._transfer(f"the identity check is unavailable ({result.reason})")
        self.st["id"] = {"names": []}
        if result.output.get("verified"):
            self.call.patient_id = result.output["patient_id"]
            if self.lang != "en":
                self.call.language = P.lang_of(result.output.get("language")) if \
                    (result.output.get("language") or "").startswith("zh") else self.lang
            self.st.pop("pending", None)
            self.st.pop("id", None)
            self._act("identity_verified", result, "Identity verified (name, birth date, card digits).")
            after = self.st.pop("after", None)
            greeting = self.say("verified", first=patient_name(self.patient, self.lang))
            if after:
                return f"{greeting} {self._personal(after['intent'], after['text'])}"
            self.st["pending"] = "anything_else"
            return f"{greeting} {self.say('options')}"
        self.call.identity_failures += 1
        self._act("identity_failed", result, f"Identity check failed (attempt {self.call.identity_failures}).")
        if self.call.identity_failures >= MAX_IDENTITY_FAILURES:
            self.st.pop("after", None)
            return self._transfer("the identity check failed twice", key="identity_failed")
        return self.say("identity_retry")

    # ----- an ED arrival (WP9) -----

    def _arrival(self) -> str:
        """The verified patient is on the way to the ED: a registration card for the clerk, pre-filled from the call
        (suggestEhrAction; the EHR registers the arrival when the clerk confirms it, app/ehr/accepted.py)."""
        info = self.st.get("arrival") or {"complaint": "injury", "mode": "walk-in"}
        patient = self.patient or {}
        mrn = next((i.get("value") for i in patient.get("identifier") or [] if i.get("system") == C.MRN_SYSTEM), "?")
        name = next((n.get("text") for n in patient.get("name") or [] if n.get("use") == "official"), "the patient")
        relation = f"the patient's {self.call.caller_relation}" if self.call.caller_relation else "the patient"
        language = preferred_language(patient)
        result = self.run.tool("suggestEhrAction", {
            "patient_id": self.call.patient_id, "reviewer_role": "clerk",
            "summary": f"ED registration: {name}, arriving by {info['mode']} ({info['complaint']})",
            "recommendation": f"Register the ED arrival of {name} (MRN {mrn}), verified on call {self.call.id} "
                              f"(name, birth date, card digits), called in by {relation}: {info['complaint']}, "
                              f"arriving by {info['mode']}. Preferred language: {language}. Confirming registers the "
                              f"visit in the EHR (Encounter class EMER, ADT^A01) for the triage nurse.",
            "action": {"kind": "registration", "patient_id": self.call.patient_id, "complaint": info["complaint"],
                       "mode": info["mode"], "call": self.call.id},
            "priority": "urgent"})
        task = (result.output or {}).get("task_id") if result.ok else None
        self.st["registration"] = task or "failed"
        self._act("ed_registration", result, f"ED registration proposed for the registration clerk (Task {task})."
                  if task else "ED registration could not be proposed; the clerk registers by hand.", task_id=task)
        self.st["pending"] = "anything_else"
        if not result.ok:
            return self.say("transfer")
        return f"{self.say('arrival_registered', first=patient_name(self.patient, self.lang))} " \
               f"{self.say('anything_else')}"

    # ----- personal intents -----

    def _appointments(self) -> list[_Appt]:
        result = self.run.tool("getPatientAppointments", {"patient_id": self.call.patient_id})
        return [self._appt(v) for v in (result.output or {}).get("appointments", [])] if result.ok else []

    def _describe(self, a: _Appt) -> str:
        return P.say(P.CC["appointment_item"], self.lang, service=self._service_name(a), when=P.when(a.start, self.lang),
                     location=self._place(a))

    def _personal(self, intent: str, text: str) -> str:
        self.intent = intent
        if intent == "clinical":
            return self._clinical(text)
        if intent == "arrival":
            return self._arrival()
        appts = self._appointments()
        first = patient_name(self.patient, self.lang)
        if not appts:
            self.st["pending"] = "anything_else"
            return f"{self.say('no_appointments', first=first)} {self.say('anything_else')}"
        if intent == "lookup":
            self._act("lookup", summary=f"Read {len(appts)} upcoming appointment(s) to the caller.")
            self.st["pending"] = "anything_else"
            items = P.join([self._describe(a) for a in appts[:3]], self.lang)
            return f"{self.say('appointments', first=first, count=len(appts), items=items)} {self.say('anything_else')}"
        chosen = self._match(appts, text)
        if chosen is None:
            self.st.update(pending="choose", choose_for=intent, options=[a.id for a in appts[:3]])
            prompt = self.say("which", items=P.join([P.say(P.CC["which_item"], self.lang, n=i + 1,
                                                           service=self._service_name(a),
                                                           when=P.when(a.start, self.lang))
                                                     for i, a in enumerate(appts[:3])], self.lang))
            self.st["prompt"] = prompt
            return prompt
        return self._for_appointment(intent, chosen)

    def _match(self, appts: list[_Appt], text: str) -> _Appt | None:
        if len(appts) == 1:
            return appts[0]
        lowered = (text or "").lower()
        hits = [a for a in appts if any(w and (w in lowered if not w.isascii() else
                                                re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", lowered))
                                        for w in self._keywords(a))]
        return hits[0] if len(hits) == 1 else None

    @staticmethod
    def _keywords(a: _Appt) -> list[str]:
        service = P.SERVICES.get(a.service) or {}
        names = [v.lower() for v in (service.get("name") or {}).values()]
        extra = {"fracture-clinic": ["fracture", "bone", "orthop", "骨科"],
                 "anticoag-clinic": ["blood thinner", "anticoag", "inr", "warfarin clinic", "抗凝"],
                 "heart-clinic": ["cardiology", "heart clinic", "心脏"], "heart-failure-clinic": ["heart function",
                                                                                              "心功能"],
                 "lung-clinic": ["respirology", "lung", "breathing", "呼吸"], "surgery-clinic": ["surgery", "外科"],
                 "preadmission-clinic": ["pre-admission", "pre-op", "术前"], "ct-scan": ["ct", "scan", "扫描"],
                 "ultrasound": ["ultrasound", "超声"]}.get(a.service, [])
        return names + extra

    def _choose(self, text: str) -> str | None:
        options = self.st.get("options") or []
        appts = {a.id: a for a in self._appointments()}
        candidates = [appts[i] for i in options if i in appts]
        index = intents.ordinal(text)
        chosen = candidates[index] if index is not None and index < len(candidates) else self._match(candidates, text)
        if chosen is None:
            return None
        intent = self.st.pop("choose_for", "lookup")
        for key in ("pending", "options", "prompt"):
            self.st.pop(key, None)
        self.intent = intent
        return self._for_appointment(intent, chosen)

    def _for_appointment(self, intent: str, a: _Appt) -> str:
        self.st["appointment"] = a.__dict__ | {"start": a.start.isoformat()}
        if intent == "reschedule":
            result = self.run.tool("findAppointmentSlots", {"appointment_id": a.id, "count": 3})
            slots = (result.output or {}).get("slots", []) if result.ok else []
            if not slots:
                return self._transfer("no open time in the next two weeks for a reschedule", key="no_slots")
            self.st.update(pending="pick_slot", slots=slots)
            prompt = self.say("offer", service=self._service_name(a), items=P.join(
                [P.say(P.CC["offer_item"], self.lang, n=i + 1, when=P.when(parse(s["start"]), self.lang))
                 for i, s in enumerate(slots)], self.lang))
            self.st["prompt"] = prompt
            return prompt
        if intent == "cancel":
            self.st.update(pending="confirm", confirm={"action": "cancel"})
            prompt = self.say("confirm_cancel", service=self._service_name(a), when=P.when(a.start, self.lang))
            self.st["prompt"] = prompt
            return prompt
        if intent == "prep":
            return self._prep(a)
        return self._personal("lookup", "")

    def _current(self) -> _Appt:
        d = dict(self.st["appointment"])
        d["start"] = datetime.fromisoformat(d["start"])
        return _Appt(**d)

    def _pick(self, text: str) -> str | None:
        slots = self.st.get("slots") or []
        index = intents.ordinal(text)
        if index is None:
            index = next((i for i, s in enumerate(slots) if parse(s["start"]).strftime("%H:%M") in text), None)
        if index is None or index >= len(slots):
            return None
        slot = slots[index]
        a = self._current()
        self.st.update(pending="confirm", confirm={"action": "move", "slot": slot})
        prompt = self.say("confirm_move", service=self._service_name(a), old=P.when(a.start, self.lang),
                          new=P.when(parse(slot["start"]), self.lang))
        self.st["prompt"] = prompt
        return prompt

    def _confirm(self, text: str) -> str | None:
        answer = intents.yes_no(text)
        if answer == "unclear":
            return None
        confirm = self.st.get("confirm") or {}
        a = self._current()
        for key in ("pending", "confirm", "slots", "prompt", "appointment"):
            self.st.pop(key, None)
        self.st["pending"] = "anything_else"
        if answer == "no":
            return self.say("kept")
        if confirm.get("action") == "move":
            return self._move(a, confirm["slot"])
        return self._cancel(a)

    def _move(self, a: _Appt, slot: dict) -> str:
        if not a.encounter_id:
            return self._transfer("the appointment has no encounter to propose the new time on", key="no_slots")
        new_start = parse(slot["start"])
        proposed = self.run.tool("proposeAppointment", {
            "encounter_id": a.encounter_id, "service": a.service, "start": slot["start"][:16],
            "minutes": slot["minutes"], "description": f"Rescheduled by phone (call {self.call.id}) from "
                                                       f"{a.start:%Y-%m-%d %H:%M}; replaces Appointment/{a.id}"})
        if not proposed.ok:
            return self._transfer(f"the new time could not be proposed ({proposed.reason})")
        new_id = proposed.output["appointment_id"]
        request = self.run.tool("moveAppointment", {"appointment_id": new_id, "replaces": a.id})
        self._act("reschedule_requested", request,
                  f"Reschedule of the {a.display} from {a.start:%a %d %b %H:%M} to {new_start:%a %d %b %H:%M} "
                  f"requested (proposed Appointment/{new_id}; clerk approval Task {request.approval_task_id}).",
                  appointment=a.id, proposed=new_id)
        return self.say("moved", new=P.when(new_start, self.lang))

    def _cancel(self, a: _Appt) -> str:
        request = self.run.tool("cancelBookedAppointment", {
            "appointment_id": a.id, "reason": f"The patient asked to cancel by phone (call {self.call.id})"})
        self._act("cancel_requested", request,
                  f"Cancellation of the {a.display} on {a.start:%a %d %b %H:%M} requested (clerk approval Task "
                  f"{request.approval_task_id}).", appointment=a.id)
        return self.say("cancelled")

    def _prep(self, a: _Appt) -> str:
        text, english = prep_text(self.store, a.service, self.lang)
        record = self.run.tool("writeCallRecord", {
            "patient_id": self.call.patient_id, "kind": "prep-instructions", "direction": "outbound",
            "status": "completed", "language": self.lang, "about": [f"Appointment/{a.id}"],
            "text": f"Preparation instructions for the {a.display} on {a.start:%Y-%m-%d %H:%M} given by phone "
                    f"(call {self.call.id}) in {self.lang}: {english}",
            **({"encounter_id": a.encounter_id} if a.encounter_id else {})})
        self._act("prep_given", record, f"Preparation instructions for the {a.display} read out ({self.lang}).",
                  appointment=a.id)
        self.st["pending"] = "anything_else"
        return self.say("prep", service=self._service_name(a), when=P.when(a.start, self.lang), text=text)

    def _clinical(self, text: str, *, urgent: bool = False) -> str:
        encounter = self._latest_encounter()
        record = self.run.tool("writeCallRecord", {
            "patient_id": self.call.patient_id, "kind": "nurse-line", "direction": "inbound", "status": "completed",
            "language": self.lang, "text": f"Question for the nurse line (call {self.call.id}): {text}",
            **({"encounter_id": encounter} if encounter else {})})
        comm = (record.output or {}).get("communication_id")
        task = self.run.tool("createCallTask", {
            "code": "nurse-line", "performer_role": "nurse", "priority": "stat" if urgent else "routine",
            "patient_id": self.call.patient_id, "call_id": self.call.id,
            "description": f"Call the patient back: a question for the nurse line from the call centre "
                           f"(call {self.call.id}). Their words: {text}"[:1000],
            **({"encounter_id": encounter} if encounter else {}), **({"focus": f"Communication/{comm}"} if comm else {})})
        self._act("nurse_line", task, "Clinical question not answered; passed to the nurse line (Communication and "
                                      f"Task{' , stat' if urgent else ''}).", communication=comm)
        self.st["pending"] = "anything_else"
        return self.say("clinical")

    # ----- general -----

    def _faq(self, text: str, topic: str | None) -> str:
        section = kb.best(text, topic)
        self.st["pending"] = "anything_else"
        if section is None:
            self._act("faq_unanswered", summary="General question not in the knowledge base.")
            return self.say("faq_none")
        self._act("faq", summary=f"Answered from the knowledge base ({section.id}).", section=section.id)
        return self.say("faq", answer=section.answer.get(self.lang) or section.answer["en"])

    def _transfer(self, reason: str, *, urgent: bool = False, key: str = "transfer") -> str:
        args = {"code": "call-transfer", "performer_role": "clerk", "priority": "urgent" if urgent else "routine",
                "call_id": self.call.id,
                "description": f"Call {self.call.id} from the AI call centre put through to staff: {reason}."}
        if self.call.patient_id:
            args["patient_id"] = self.call.patient_id
        task = self.run.tool("createCallTask", args)
        self._act("transfer", task, f"Transfer Task for the call desk ({reason}).")
        self.call.outcome, self.call.transfer_reason = "transferred", reason
        self.ended = True
        return self.say(key)

    def _emergency(self, text: str) -> str:
        if self.call.patient_id:
            self._clinical(text, urgent=True)
            self.call.outcome, self.call.transfer_reason = "transferred", "possible emergency (911 prompt given)"
            self.ended = True
        else:
            self._transfer("possible emergency (911 prompt given)", urgent=True)
        return self.say("emergency")

    def _goodbye(self) -> str:
        self.ended = True
        return self.say("goodbye")


def prep_text(store: Store, service_code: str, lang: str) -> tuple[str, str]:
    """(what to read out in the caller's language, the English text for the record). Imaging exams use the
    imaging module's approved prep instructions (System 9); a language without an approved version is read in
    English."""
    service = P.SERVICES.get(service_code)
    if service is None:
        none = P.p("I don't have preparation instructions for this appointment; the clinic will tell you.",
                   "这个预约没有特别的准备说明，门诊会告诉您。", "這個預約沒有特別的準備說明，門診會告訴您。")
        return P.say(none, lang), none["en"]
    kind, value = service["prep"]
    if kind == "text":
        return P.say(value, lang), value["en"]
    from app.modules.prep import service as prep

    english, _, _ = prep.message_text(store, value, "en")
    text, used, _ = prep.message_text(store, value, "zh" if lang != "en" else "en")
    return text, english
