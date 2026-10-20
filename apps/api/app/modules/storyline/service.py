"""The hospital storyline (spec 8.2, WP9): reset to the start, the twelve steps' live status, the clock's beats,
and the event subscriber that plays the story.

- `reset(user, language)`: the demo reset followed by the preset (app/ehr/seed/journey.py), one click from the
  storyline page, with the patient who speaks `language` (en: Margaret Ellis, the default; zh: Wang Xiuying).
  Workflows still running from before are terminated by the reset (6.5). The scheduler's daily demo reset is this
  reset too (`user` None), with the default patient.
- `status(store, user)`: per step what the AI proposed, who signed or approved and when, what the EHR wrote and
  the audit events of those records, read through FhirGateway as the storyline (system actor, audited) plus the
  modules' own tables; the current step is the first one not done.
- `advance(store, user)`: moves the hospital clock to the current step's beat (the INR result, the admission
  decision, her bed, the first post-operative night, the discharge, 48 hours later) as a background fast-forward
  (app/ehr/simjobs.py), so the boards move and the subscribers react while it runs.
- Demo mode's "Do this step for me" and "Jump to step N" are app/modules/storyline/autoplay.py: the status shows its
  job (`autoplay`) and marks the steps it did (`automated`).
- `handle(event)`: the subscriber (consumer `storyline`). It reacts only to the storyline patient's events: the
  cards of app/modules/storyline/stubs.py, the EHR script of app/ehr/storyline.py, the scripted discharge
  reconciliation, and, with Temporal configured, the start of her InpatientJourneyWorkflow at the registration
  (journey-stay-j0001 from the ED, `JourneyInput.ed_visit`).

- `ai_step(store)`: with BACKGROUND_AI=on_demand, the AI of a step whose button a person pressed (Advance to the
  admission decision: the exception's narrative; Advance until she has a bed: the naproxen order's review and the
  admission reconciliation) is queued as soon as its item appears, as that person's action ("Storyline step N").
  Items nobody's step asked for keep waiting for someone to open them.

The page works without Temporal: every card and module step is there either way (the offline mode of 6.5); with
Temporal the journey also strings the sign-offs together, escalates them when they wait and shows the timeline.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.agents import approvals, ondemand
from app.agents import cause as causes
from app.agents import trace as traces
from app.core.audit import mrn_hash
from app.core.db.schema import audit_events
from app.core.models import StaffUser
from app.core.store import Store, ambient_store, get_store, reset_store
from app.ehr import simjobs, simulator
from app.ehr import storyline as ehr_story
from app.ehr.events import DomainEvent, bus, domain_events
from app.ehr.gateway import or_adjustments
from app.ehr.seed import journey as preset
from app.fhir.dt import parse
from app.modules.storyline import script as S
from app.modules.storyline import stubs

log = logging.getLogger("app.storyline")
CONSUMER = "storyline"
BED_REQUEST = f"bedreq-{S.VISIT}"  # the ED's bed request at the admission decision (the simulator writes it)
EVENT_TYPES = ("patient.admitted", "patient.discharged", "encounter.updated", "result.available", "task.decided",
               "exception.decided", "document.signed")
CLEAN_MAX = timedelta(minutes=102)  # the simulator's housekeeping takes 45-100 minutes


def state(store: Store) -> dict | None:
    return store.modules.get(preset.STATE)


# ---------- reset ----------


SCHEDULER = {"user_id": "system", "user_name": "Scheduler", "role": "system"}  # the daily demo reset's actor


def reset(user: StaffUser | None, language: str | None = None, *, reason: str = "storyline reset") -> dict:
    """The demo reset, then the storyline's preset with the patient who speaks `language`: the story starts at 07:00
    on the hospital day. `user` None: the scheduler's daily demo reset (app/modules/admin/service.py), audited as
    the system, so the live story is at step 1 every morning without a visitor's reset."""
    from app.workflows import bridge

    who = S.patient(language)  # an unknown language fails before anything is rebuilt
    store = reset_store()
    actor = dict(user_id=user.id, user_name=user.name, role=str(user.role)) if user else SCHEDULER
    store.audit.record(**actor, action="reset", resource_type="demo_data", resource_id="storyline",
                       reason=f"{reason} (demo reset + preset; the patient: {who.label})",
                       event_type="simulator_event", module="storyline")
    st = preset.plant(store, user.id if user else None, who.language)  # no operator: the simulator's actor
    # until a step's button is pressed, the story's AI (the imaging requisitions after triage) is the presenter's
    st["driver"] = (causes.from_request("storyline") or causes.AiCause(trigger="storyline")).model_copy(
        update={"trigger": "storyline", "note": "Storyline"}).model_dump(exclude_none=True)
    store.modules[preset.STATE] = st
    bridge.on_reset()
    from app.agents import ondemand
    from app.modules.storyline import autoplay

    ondemand.reset()  # queued AI jobs refer to the old data
    autoplay.on_reset()  # a running automation stops after its step; its marks go with the old data
    return st


# ---------- the subscriber ----------


def install() -> bool:
    if any(s.consumer == CONSUMER for s in bus.subscriptions()):
        return False
    bus.subscribe(EVENT_TYPES, handle, consumer=CONSUMER)
    return True


def _id(ref: str | None) -> str | None:
    return ref.split("/", 1)[1] if isinstance(ref, str) and "/" in ref else None


def handle(event: DomainEvent) -> None:
    store = get_store()
    st = state(store)
    if st is None:
        return
    with causes.using(_driver(store, event)):
        _handle(store, st, event)


def _handle(store: Store, st: dict, event: DomainEvent) -> None:
    t, enc, a = event.type, event.encounter, event.attrs
    if t == "patient.admitted" and enc == S.VISIT:
        stubs.ensure_triage_card(store)
        _start_journey(store, event)
    elif t == "encounter.updated" and enc == S.VISIT and a.get("change") == "triaged":
        if ehr_story.after_triage(store) is not None:
            stubs.ensure_imaging_card(store)
    elif t == "result.available" and event.refs.get("order") == f"ServiceRequest/ord-{S.VISIT}-inr":
        stubs.ensure_critical_card(store)
    elif t == "task.decided" and a.get("decision") == "accept":
        task = stubs.fhir().read("Task", _id(event.refs.get("task")) or "")
        if task is not None and stubs.card_kind(task.to_fhir()) == "imaging_order":
            stubs.confirm_imaging(store, event.actor.removeprefix("user:"))
    elif t == "exception.decided" and a.get("decision") == "approved":
        _approved(store, a.get("exception_id"), event.occurred_at)
    elif t == "patient.discharged" and enc in ((st.get("capacity") or {}).get("surgery_ready") or []):
        ehr_story.admit_after_cleaning(store, enc, _id(event.refs.get("from_location")))
    elif t == "patient.admitted" and enc == S.STAY:
        ehr_story.admission_note(store)
    elif t == "result.available" and enc == S.STAY:
        score = next((r for r in event.refs.get("results") or [] if str(r).startswith("Observation/news2-")), None)
        if score:
            stubs.news2_alert(store, _id(score))
    elif t == "patient.discharged" and enc == S.STAY:
        discharge_medrec(store)
    elif t == "document.signed" and event.refs.get("document") == f"DocumentReference/dsum-{S.STAY}" and a.get("final"):
        stubs.ensure_coding_card(store)


def _start_journey(store: Store, event: DomainEvent) -> None:
    """With Temporal configured, her journey starts at the ED registration (spec 8.2: steps 2-12 in one workflow);
    it waits for her admission through the bridge's `admitted` signal (a wait on her planned stay)."""
    from app.workflows import bridge, client

    if client.link() is None:
        return
    bridge.register_wait(f"Encounter/{S.STAY}", S.JOURNEY, "bedAssignment")
    bridge.start_journey(S.STAY, S.PATIENT, event.actor, f"{event.event_id}:storyline", ed_visit=S.VISIT)


def _approved(store: Store, exception_id: str | None, at: datetime) -> None:
    from app.modules.control_tower import exceptions as X

    exc = X.get(store, exception_id) if exception_id else None
    if exc is None or exc.key != "unit_occupancy:ORTH":
        return
    actions = [x.get("action_id") for x in (exc.decision or {}).get("actions") or []]
    if "overflow_discharge_rounds" in actions:
        ehr_story.early_rounds(store, at)


def discharge_medrec(store: Store) -> str | None:
    """Her discharge reconciliation, done off-screen by the simulator script (the 10-minute demo has no time for
    it; WP7's summary needs the signed discharge medication table): the pharmacist accepts each discrepancy with a
    reason that says it is scripted, signs, and the surgeon co-signs. Audited under their names like any MedRec."""
    from app.modules.med_safety import service as MS
    from app.modules.med_safety.reconcile import RecItem

    pharmacist, surgeon = store.staff.get(S.CAST["pharmacist"]), store.staff.get(S.CAST["surgeon"])
    if pharmacist is None or surgeon is None:
        return None
    try:
        session = MS.reconcile(store, S.STAY, "discharge")
    except MS.DecisionProblem:
        return None
    if session.status == "final":
        return session.id
    session = MS.settle(store, session.id)
    if session.status == "open":
        for item in [RecItem.model_validate(i) for i in session.items]:
            if (item.discrepancy and item.action is None) or item.indication == "pending":
                session = MS.handle_item(store, pharmacist, session.id, item.id, "accept",
                                         "Storyline script (off-screen): discharge list as prescribed by the surgeon")
        session = MS.sign(store, pharmacist, session.id)
    if session.status == "pharmacist_signed":
        session = MS.sign(store, surgeon, session.id)
    st = state(store)
    st.setdefault("marks", {})["discharge_medrec"] = {"session": session.id, "status": session.status}
    store.modules[preset.STATE] = st
    return session.id


# ---------- the clock's beats ----------


def _event_at(plan: dict, **match) -> datetime | None:
    found = [parse(e["at"]) for e in plan["events"] if all(e.get(k) == v for k, v in match.items())]
    return min(found) if found else None


def beats(store: Store) -> dict[str, str | None]:
    """When each waiting step's moment comes (hospital time), from the plan as it is now (the simulator moves her
    stay's events while she waits for a bed)."""
    st, plan = state(store) or {}, store.modules.get("hospital_plan") or {"events": []}
    marks = st.get("marks", {})
    out: dict[str, datetime | None] = {
        "inr": _event_at(plan, kind="result", order=f"ord-{S.VISIT}-inr"),
        "decision": _event_at(plan, kind="ed_decision", visit=S.VISIT),
        "night": _event_at(plan, kind="vitals", stay=S.STAY),
        "discharge": _event_at(plan, kind="discharge", stay=S.STAY),
    }
    if out["decision"]:
        out["decision"] += timedelta(minutes=2)
    offered = marks.get("bed_offered")
    if offered:
        out["admitted"] = parse(offered["at"]) + timedelta(minutes=1)
    elif marks.get("early_rounds"):
        last = max((_event_at(plan, kind="discharge", stay=sid) or parse(marks["early_rounds"]["at"])
                    for sid in marks["early_rounds"]["stays"]), default=parse(marks["early_rounds"]["at"]))
        out["admitted"] = last + CLEAN_MAX
    else:
        out["admitted"] = None
    discharged = _read("Encounter", S.STAY)
    end = parse(((discharged or {}).get("period") or {}).get("end")) if discharged else None
    base = end or out["discharge"]
    out["followup"] = base + timedelta(hours=48, minutes=6) if base else None
    for key in ("inr", "night", "discharge"):
        if out[key]:
            out[key] += timedelta(minutes=1)
    return {k: v.isoformat() if v else None for k, v in out.items()}


def patient_of(st: dict | None) -> S.Patient:
    """The storyline's patient as the reset chose her (a story planted before the choice existed was Mandarin)."""
    return S.patient((st or {}).get("language") or "zh")


def advance(store: Store, user: StaffUser) -> dict:
    """Move the clock to the current step's beat, as a background fast-forward (the page follows its progress)."""
    view = status(store, user)
    step = next((s for s in view["steps"] if s["n"] == view["current"]), None)
    if step is None or not step.get("beat"):
        raise ValueError("The current step needs no time to pass: do it first")
    target = view["beats"].get(step["beat"])
    if target is None:
        raise ValueError(f"Not yet: {step['beat_hint']}")
    target_t = parse(target)
    now = parse(simulator.clock(store)["now"])
    if target_t <= now:
        raise ValueError("Already there: the step is ready")
    if target_t > parse(view["horizon"]):
        raise ValueError("Past the hospital plan's horizon: reset the storyline")
    job = simjobs.start(store, operator=user.id, until=target_t)
    _remember_driver(store, step["n"], step["beat"])
    store.audit.record(user_id=user.id, user_name=user.name, role=str(user.role), action="fast-forward",
                       resource_type="hospital_clock", resource_id=None, outcome="allowed",
                       reason=f"storyline step {step['n']}: to {target}", event_type="simulator_event",
                       module="storyline")
    return {"job": job, "beat": step["beat"], "target": target}


# ---------- the steps' AI (app/agents/ondemand.py) ----------

# The beats whose Advance button runs a step's AI: the step key and what its AI writes
AI_BY_BEAT = {"decision": "bed", "admitted": "medrec"}
AI_TEXT = {"bed": "the AI writes the exception's narrative and recommended actions",
           "medrec": "the AI reviews her naproxen order and judges the admission reconciliation's new medications"}


def _remember_driver(store: Store, n: int, beat: str | None) -> None:
    """The person who pressed a step's button causes what the story does next (the cards, the scripted discharge
    reconciliation, and the AI of that step's items)."""
    st = state(store)
    base = causes.from_request("storyline") or causes.AiCause(trigger="storyline")
    driver = base.model_copy(update={"trigger": "storyline", "note": f"Storyline step {n}"}).model_dump(
        exclude_none=True)
    st["driver"] = driver
    if (key := AI_BY_BEAT.get(beat or "")) is not None:
        st.setdefault("ai_steps", {})[key] = {"cause": driver, "items": [], "at": datetime.now().isoformat()}
    store.modules[preset.STATE] = st


def _driver(store: Store, event: DomainEvent | None = None) -> causes.AiCause | None:
    st = state(store) or {}
    if not st.get("driver"):
        return None
    cause = causes.AiCause.model_validate(st["driver"])
    actor = (event.actor or "") if event is not None else ""
    if actor.startswith("user:") and (user := store.staff.get(actor.removeprefix("user:"))) is not None:
        cause = cause.model_copy(update={"user_id": user.id, "user_name": user.name, "role": str(user.role)})
    return cause


def _step_items(store: Store, key: str) -> list[tuple[str, str]]:
    """The items whose AI a step shows, as (kind, id), once they exist. The bed step's exception counts only once she
    boards it (the admission decision passed, and the exception high with it): the preset fills Orthopedics, so the
    Control Tower flags it (medium) from 07:00, and a narrative of that one would not mention her (it is written
    again when the severity changes). The decision is read from the plan, not her record: this runs on every pass of
    the hospital loop while the step waits, and each read of her record is audited."""
    from app.modules.control_tower import exceptions as X
    from app.modules.med_safety import service as MS

    if key == "bed":
        decision = _event_at(store.modules.get("hospital_plan") or {"events": []}, kind="ed_decision", visit=S.VISIT)
        if decision is None or decision > parse(simulator.clock(store)["now"]):
            return []
        live = sorted((e for e in X.all_exceptions(store) if e.key == "unit_occupancy:ORTH" and e.status in X.LIVE
                       and e.cleared_at is None and e.severity == "high"), key=lambda e: e.detected_at)
        return [("exception", live[-1].id)] if live else []
    if key == "medrec":
        out = []
        if (rev := MS.reviews(store).get(f"MedicationRequest/medrx-{S.STAY}-03")) is not None:
            out.append(("order_review", rev.id))
        if (ses := MS.sessions(store).get(f"{S.STAY}:admission")) is not None:
            out.append(("medrec", ses.id))
        return out
    return []


STEP_ITEMS = {"bed": 1, "medrec": 2}


def ai_step(store: Store) -> None:
    """The hospital loop's step: queue the AI of the items a pressed step's button brought, once they exist. A
    step's items are fixed when first seen (Orthopedics fills up again later in the story, and nobody pressed a
    button for that exception)."""
    st = state(store)
    wanted = (st or {}).get("ai_steps") or {}
    open_steps = {k: v for k, v in wanted.items() if len(v.get("items") or []) < STEP_ITEMS[k]}
    if not open_steps:
        return
    changed = False
    for key, entry in open_steps.items():
        known = [tuple(i) for i in entry.get("items") or []]
        for item in _step_items(store, key):
            if item in known or any(k == item[0] for k, _ in known):
                continue
            known.append(item)
            changed = True
            kind_name, item_id = item
            k = ondemand.kind(kind_name)
            if k.needs_ai(store, item_id):
                label, link = ondemand._describe(k, store, item_id)
                cause = causes.AiCause.model_validate(entry["cause"]).model_copy(
                    update={"kind": kind_name, "item_id": item_id, "label": label, "link": link})
                ondemand.request_after_commit(store, kind_name, item_id, cause)
        entry["items"] = [list(i) for i in known]
    if changed:
        store.modules[preset.STATE] = st


def ask_step_ai(store: Store, user: StaffUser, key: str) -> dict:
    """The step's Ask AI (the page's AI box): queue the AI of the step's items that exist now, as this person's
    storyline step: an item whose AI has not run (its Advance was not pressed: the clock moved by itself), or one
    whose run failed and may run again (the retry itself is decided by the run, under the item's lock). Each item
    is checked against the person (who may ask for its AI, 6.3 scope): NotAllowed when they may ask for none, else
    the refused ones are listed in `denied` ([kind, id]; the caller audits them)."""
    step = S.BY_KEY.get(key)
    if step is None or key not in AI_TEXT:
        raise ValueError("This step's AI runs from its own screen")
    st = state(store)
    if st is None:
        raise ValueError("Reset the storyline first")
    items = _step_items(store, key)
    if not items:
        raise ValueError("Nothing to ask for yet: the step's items appear when the clock gets there")
    base = (causes.from_request("storyline") or causes.AiCause(trigger="storyline")).model_copy(
        update={"trigger": "storyline", "note": f"Storyline step {step.n}"})
    asked, denied = [], []
    for kind_name, item_id in items:
        k = ondemand.kind(kind_name)
        try:
            k.check(store, user, item_id)
        except ondemand.NotAllowed:
            denied.append([kind_name, item_id])
            continue
        except LookupError:
            continue
        if k.needs_ai(store, item_id):
            retry = False
        elif ondemand.can_retry(store, kind_name, item_id):
            retry = True
        else:
            continue
        label, link = ondemand._describe(k, store, item_id)
        ondemand.request_after_commit(store, kind_name, item_id, base.model_copy(
            update={"kind": kind_name, "item_id": item_id, "label": label, "link": link}), retry=retry)
        asked.append([kind_name, item_id])
    if denied and not asked:
        raise ondemand.NotAllowed(f"You may not ask for the AI of {', '.join(i for _, i in denied)}")
    st.setdefault("ai_steps", {})[key] = {"cause": base.model_dump(exclude_none=True),
                                          "items": [list(i) for i in items], "at": datetime.now().isoformat()}
    store.modules[preset.STATE] = st
    return {"step": step.n, "items": asked, "denied": denied}


def _step_ai(store: Store, key: str, st: dict | None) -> dict:
    """What the AI does for one step: its items' AI blocks, and whether the step's button asked for it."""
    from app.modules.control_tower import exceptions as X
    from app.modules.control_tower import service as CT
    from app.modules.med_safety import service as MS
    from app.modules.med_safety.models import get_review, get_session

    asked = ((st or {}).get("ai_steps") or {}).get(key)
    pinned = [tuple(i) for i in (asked or {}).get("items") or []]
    items = []
    for kind_name, item_id in pinned or _step_items(store, key):
        if kind_name == "exception" and (exc := X.get(store, item_id)) is not None:
            items.append({"kind": kind_name, "id": item_id, "label": exc.title, "ai": CT.exception_ai(exc)})
        elif kind_name == "order_review" and (rev := get_review(store, item_id)) is not None:
            items.append({"kind": kind_name, "id": item_id, "label": "Her naproxen order's review",
                          "ai": MS.review_ai(rev)})
        elif kind_name == "medrec" and (ses := get_session(store, item_id)) is not None:
            items.append({"kind": kind_name, "id": item_id, "label": "Admission reconciliation",
                          "ai": MS.session_ai(ses)})
    return {"items": items, "text": AI_TEXT.get(key), "requested": asked is not None,
            "requested_by": ((asked or {}).get("cause") or {}).get("user_name")}


# ---------- status ----------


def _read(rtype: str, rid: str) -> dict | None:
    found = stubs.fhir().read(rtype, rid)
    return found.to_fhir() if found is not None else None


def _user(store: Store, user_id: str | None) -> dict | None:
    user = store.staff.get(user_id) if user_id else None
    return {"id": user.id, "name": user.name, "role": str(user.role), "units": list(user.unit_ids)} if user else None


def _decided(task: dict | None) -> dict | None:
    if task is None or task.get("status") not in ("completed", "rejected"):
        return None
    out = approvals.outputs(task)
    return {"by": out.get("decided_by"), "decision": out.get("decision"), "note": out.get("note"),
            "at": task.get("lastModified"), "ref": f"Task/{task['id']}", "status": task["status"]}


def _proposal(task: dict | None, label: str) -> dict | None:
    if task is None:
        return None
    run_id = traces.run_of(task)
    trace = traces.get(run_id) if run_id else None
    facts = ondemand.trace_facts(run_id) if trace and trace.llm_calls else {}
    return {"label": label, "ref": f"Task/{task['id']}", "at": task.get("authoredOn"),
            "agent": approvals._agent_of(task), "run_id": run_id, "summary": task.get("description"),
            "recommendation": approvals.inputs(task).get("recommendation"),
            "model": trace.model if trace else None, "mode": trace.mode if trace else None,
            "generated_at": facts.get("generated_at"), "duration_ms": facts.get("duration_ms")}


def _signature(doc: dict | None) -> dict | None:
    from app.ehr.signing import signatures

    if not doc:
        return None
    sigs = signatures(doc)
    if doc.get("docStatus") != "final" and not sigs:
        return None
    return {"ref": f"DocumentReference/{doc['id']}", "status": doc.get("docStatus"), "signatures": len(sigs),
            "by": ", ".join(x["name"] or x["by"] or "?" for x in sigs) or None,
            "at": sigs[-1]["at"] if sigs else doc.get("date"),
            "note": next((x["note"] for x in reversed(sigs) if x.get("note")), None)}  # the demo automation's


def _facts(store: Store, st: dict) -> dict:
    from app.modules.control_tower import exceptions as X
    from app.modules.med_safety import service as MS
    from app.modules.voice_services import followup

    gw = stubs.fhir()
    tasks = gw.search("Task", patient=S.PATIENT)
    cards = {}
    for task in sorted(tasks, key=lambda t: t.get("authoredOn") or ""):
        kind = stubs.card_kind(task)
        if kind and kind not in cards:
            cards[kind] = task
    journey_tasks = [t for t in tasks if approvals.inputs(t).get("workflow") == S.JOURNEY]
    orth = sorted((e for e in X.all_exceptions(store) if e.key == "unit_occupancy:ORTH"),
                  key=lambda e: e.detected_at)
    # her exception: the one approved with the early rounds (Orthopedics fills up again later in the story and
    # the Control Tower opens new ones), else the latest
    rounds = [e for e in orth if (e.decision or {}).get("decision") == "approved" and any(
        a.get("action_id") == "overflow_discharge_rounds" for a in (e.decision or {}).get("actions") or [])]
    orth = [*[e for e in orth if e not in rounds], *rounds[:1]]
    order_review = MS.reviews(store).get(f"MedicationRequest/medrx-{S.STAY}-03")
    medrec = MS.sessions(store).get(f"{S.STAY}:admission")
    appointment = next(iter(gw.search("Appointment", focus=f"orreq-{S.SURGERY}")), None)
    return {
        "cards": cards, "journey_tasks": journey_tasks, "visit": _read("Encounter", S.VISIT),
        "stay": _read("Encounter", S.STAY), "ctas": _read("Observation", f"ctas-{S.VISIT}"),
        "xr": _read("ServiceRequest", f"ord-{S.VISIT}-xr-hip"), "ct": _read("ServiceRequest", f"ord-{S.VISIT}-ct-hip"),
        "inr": _read("Observation", f"obs-ord-{S.VISIT}-inr"), "exception": orth[-1] if orth else None,
        "bed_request": _read("ServiceRequest", BED_REQUEST),
        "order_review": order_review, "medrec": medrec, "appointment": appointment,
        "news2": next(iter(gw.search("Flag", encounter=S.STAY, code="news2")), None),
        "summary": _read("DocumentReference", f"dsum-{S.STAY}"),
        "instructions": _read("DocumentReference", f"pinstr-{S.STAY}"),
        "followup": followup.get(followup.call_id_for(S.STAY), store),
        "requisitions": [store.requisitions.get(r) for r in S.REQUISITIONS],
        "audit": sorted([*(e for t in AUDITED for e in store.audit.query(
            patient_mrn_hash=mrn_hash(S.MRN), event_type=t, newest_first=True, limit=400)),
            # the bed manager's decision is on the hospital's exception and its Tasks, not on her record
            *(e for rid in _exception_tasks(orth[-1] if orth else None)
              for e in store.audit.query(resource_type="Task", resource_id=rid))],
            key=lambda e: e.seq, reverse=True),
    }


AUDITED = ("approve", "sign", "write", "tool_call", "privileged_call")


def _exception_tasks(exc) -> list[str]:
    """The review Task the bed manager's decision completed and the Tasks the approved actions created."""
    if exc is None:
        return []
    return [t for t in [exc.review_task_id, *((a.get("task_id") for a in (exc.decision or {}).get("actions") or []))]
            if t]

# The page polls the status every few seconds and each EHR read of hers is audited: the facts are kept while nothing
# that they show could have changed (as the Control Tower keeps its snapshot), and for a few seconds at most while
# a fast-forward runs (the clock and the job's progress are read fresh every time).
_FACTS: dict = {}
_FACTS_LOCK = threading.Lock()
FACTS_WHILE_RUNNING_S = 8.0


def _facts_key(store: Store, st: dict) -> str:
    store.save()  # the test's shared store: pending changes count (a request's own store has none)
    conn = store.conn()
    last_event = conn.execute(select(func.max(domain_events.c.seq))).scalar() or 0
    last_audit = conn.execute(select(func.max(audit_events.c.seq)).where(
        audit_events.c.event_type.in_(AUDITED))).scalar() or 0
    shared = id(store) if store is ambient_store() else 0  # tests: one store per test, rolled back after it
    return f"{shared}:{st.get('prepared_at')}:{store.version}:{simulator.clock(store)['now']}:{last_event}:{last_audit}"


def _cached(store: Store, st: dict, job: dict | None) -> tuple[dict, dict]:
    key = _facts_key(store, st)
    with _FACTS_LOCK:
        hit = _FACTS.get("last")
    running = (job or {}).get("status") == "running"
    if hit and (hit["key"] == key or (running and hit["prepared_at"] == st.get("prepared_at")
                                      and time.monotonic() - hit["at"] < FACTS_WHILE_RUNNING_S)):
        return hit["facts"], hit["beats"]
    # what is cached under the key must be read after it: the state status() read first may predate a commit the key
    # already counts (the bus handler that marks the early rounds writes only the state and the plan), and the stale
    # beats would then be served under that key until the clock moved, which only Advance does (casebook 10.10)
    store.modules.refresh(preset.STATE, "hospital_plan")
    st = state(store) or st
    facts, story_beats = _facts(store, st), beats(store)
    with _FACTS_LOCK:
        _FACTS["last"] = {"key": key, "at": time.monotonic(), "prepared_at": st.get("prepared_at"), "facts": facts,
                          "beats": story_beats}
    return facts, story_beats


def _audit(events: list, refs: list[str]) -> list[dict]:
    """The approve, sign and write events on the step's records (the hash-chained audit log)."""
    ids = {r.split("/", 1)[1] for r in refs if r and "/" in r}
    if not ids:
        return []
    out = []
    for e in events:
        if e.resource_id in ids and e.event_type in AUDITED:
            out.append({"event_type": e.event_type, "action": e.action, "resource": f"{e.resource_type}/{e.resource_id}",
                        "user": e.user_name, "role": e.role, "at": e.ts.isoformat(timespec="seconds"),
                        "outcome": e.outcome})
    return out[:6]


def _step_state(step: S.Step, f: dict, store: Store, who: S.Patient | None = None) -> dict:
    """proposal / signed / written / detail for one step, from the records; `ready`: what the step's beat waits for
    has happened (by default its proposal is there: the card, the review, the call come with that moment)."""
    c, out = f["cards"], {"proposal": None, "signed": None, "written": [], "detail": None, "done": False}
    if step.key == "call":
        card = c.get("registration")
        out.update(proposal=_proposal(card, "ED registration pre-filled from the verified call"),
                   signed=_decided(card), written=[f"Encounter/{S.VISIT}"] if f["visit"] else [])
        out["done"] = bool(f["visit"])
    elif step.key == "triage":
        card = c.get("triage")
        marks = (state(store) or {}).get("marks", {}).get("triage_suggestion") or {}
        out.update(proposal=_proposal(card, f"CTAS {marks.get('ctas', '?')} suggested"), signed=_decided(card),
                   written=[r for r in (f"Observation/ctas-{S.VISIT}" if f["ctas"] else None,
                                        f"Flag/flag-{S.VISIT}-fall-risk" if f["ctas"] else None) if r])
        if marks.get("admission"):
            out["detail"] = marks["admission"]["sentence"]
        out["done"] = bool(f["ctas"])
    elif step.key == "imaging":
        card = c.get("imaging_order")
        out.update(proposal=_proposal(card, "Urgency and protocol per exam"), signed=_decided(card),
                   written=[f"ServiceRequest/{r['id']}" for r in (f["xr"], f["ct"]) if r])
        out["done"] = bool(f["xr"] and f["ct"])
    elif step.key == "inr":
        card = c.get("critical")
        out.update(proposal=_proposal(card, "Critical value routed (roadmap stub)"), signed=_decided(card),
                   written=[f"Observation/obs-ord-{S.VISIT}-inr"] if f["inr"] else [])
        out["done"] = bool(out["signed"])
    elif step.key == "bed":
        exc = f["exception"]
        if exc is not None:
            out["proposal"] = {"label": exc.title, "ref": f"FlowException/{exc.id}", "at": exc.detected_at.isoformat(),
                               "agent": "control_tower", "summary": exc.summary,
                               "recommendation": "; ".join(a.get("action", "") for a in
                                                           (exc.narration or {}).get("recommended_actions") or [])}
            out["audit_refs"] = [f"Task/{t}" for t in _exception_tasks(exc)]
            if exc.decision:
                out["signed"] = {"by": exc.decision.get("name") or exc.decision.get("by"),
                                 "decision": exc.decision.get("decision"),
                                 "at": exc.decision.get("at"), "ref": f"FlowException/{exc.id}",
                                 "tasks": [a.get("task_id") for a in exc.decision.get("actions") or [] if a.get("task_id")]}
        stay = f["stay"]
        if stay:
            beds = [((x.get("location") or {}).get("reference")) for x in stay.get("location") or []]
            out["written"] = [f"Encounter/{S.STAY}"] + [b for b in beds if b]
            out["detail"] = f"bed {beds[-1].split('/')[-1]}" if beds and beds[-1] else None
        out["done"] = bool(stay and out["signed"])
        # her moment is the admission decision (the bed request), not the exception: the Control Tower opens that
        # as soon as Orthopedics is full, and the preset fills it at 07:00
        out["ready"] = bool(f["bed_request"])
    elif step.key == "medrec":
        rev, ses = f["order_review"], f["medrec"]
        if rev is not None:
            out["proposal"] = {"label": f"{len(rev.findings)} rule finding(s) on the naproxen order ({rev.severity})",
                               "ref": rev.order_ref, "at": rev.reviewed_at.isoformat(), "agent": "order_review",
                               "summary": "; ".join(str(fi.get("message") or fi.get("title") or fi.get("rule") or "")
                                                    for fi in rev.findings)[:300],
                               "decision": (rev.decision or {}).get("decision")}
        if ses is not None:
            out["signed"] = {"by": ", ".join(s.get("name") or s.get("by") for s in ses.signatures) or None,
                             "decision": ses.status, "ref": f"DocumentReference/{ses.document_id}",
                             "at": ses.signatures[-1]["at"] if ses.signatures else None}
            out["written"] = [f"DocumentReference/{ses.document_id}"] if ses.document_id else []
            out["detail"] = f"MedRec {ses.id}: {ses.status}"
        out["done"] = bool(ses and ses.status == "final")
    elif step.key == "or":
        appt = f["appointment"]
        signoff = next((t for t in f["journey_tasks"] if approvals.inputs(t).get("step") == "proposeSchedule"), None)
        if appt:
            out["written"] = [f"Appointment/{appt['id']}"]
            out["detail"] = f"booked {appt.get('minutesDuration')} min"
        adjusted = appt is not None and (appt.get("minutesDuration") or 0) > ehr_story.BOOKED_MINUTES
        audit = _audit(f["audit"], [f"Appointment/{appt['id']}"]) if appt else []
        if adjusted:
            # who and when from the appointment's own record of the change (hospital time); the audit event's time
            # is the wall clock
            change = (or_adjustments(appt) or [{}])[-1]
            who = next((a for a in audit if a["event_type"] == "write"), None)
            out["signed"] = {"by": change.get("name") or (who["user"] if who else None), "decision": "adjusted",
                             "at": change.get("at"), "ref": f"Appointment/{appt['id']}"}
        out["proposal"] = _proposal(signoff, "OR booking check (journey)") if signoff else (
            {"label": "Surgical duration prediction", "ref": f"Appointment/{appt['id']}", "agent": "control_tower"}
            if appt else None)
        out["done"] = adjusted
    elif step.key == "news2":
        card = c.get("news2") or next((t for t in f["journey_tasks"] if approvals.code_of(t) == "news2-response"), None)
        flag = f["news2"]
        out.update(proposal=_proposal(card, "NEWS2 flag and nurse task (roadmap stub)") if card else None,
                   signed=_decided(card), written=[f"Flag/{flag['id']}"] if flag else [])
        out["done"] = bool(out["signed"])
    elif step.key == "summary":
        doc = f["summary"]
        out.update(proposal={"label": "Discharge summary draft with sources", "ref": f"DocumentReference/{doc['id']}",
                             "agent": "discharge_summary"} if doc else None, signed=_signature(doc),
                   written=[f"DocumentReference/{doc['id']}"] if doc else [])
        out["done"] = bool(doc and doc.get("docStatus") == "final")
    elif step.key == "instructions":
        doc = f["instructions"]
        label = ("Bilingual patient instructions" if (who or S.patient()).language == "zh"
                 else "Patient instructions in her language (English)")
        out.update(proposal={"label": label, "ref": f"DocumentReference/{doc['id']}",
                             "agent": "patient_instructions"} if doc else None, signed=_signature(doc),
                   written=[f"DocumentReference/{doc['id']}"] if doc else [])
        out["done"] = bool(doc and doc.get("docStatus") == "final")
    elif step.key == "followup":
        call = f["followup"]
        if call is not None:
            out["proposal"] = {"label": f"Follow-up call {call.id} ({call.language})", "ref": f"FollowupCall/{call.id}",
                               "agent": "followup_calls", "summary": call.summary}
            handled = [a for a in call.nurse_actions if a.get("kind") in ("callback", "close", "refer")]
            if handled:
                last = handled[-1]
                out["signed"] = {"by": last.get("by"), "decision": last.get("kind"), "at": last.get("at"),
                                 "ref": f"Communication/{last.get('communication_id')}"}
            out["written"] = [r for r in ([f"Communication/{call.communication_id}"] if call.communication_id else [])
                              + [f"Task/{call.task_id}"] if r and not r.endswith("/None")]
            out["detail"] = f"{call.status}; escalated: {'yes' if call.escalated else 'no'}"
            out["done"] = bool(call.escalated and handled)
    elif step.key == "coding":
        card = c.get("coding")
        out.update(proposal=_proposal(card, "Coding suggestion (roadmap stub)"), signed=_decided(card))
        out["done"] = bool(out["signed"] and out["signed"].get("status") == "completed")
    refs = [r for r in [(out["proposal"] or {}).get("ref"), (out["signed"] or {}).get("ref"), *out["written"],
                        *out.pop("audit_refs", [])] if r]
    out["audit"] = _audit(f["audit"], refs)
    out.setdefault("ready", bool(out["proposal"]))
    return out


BEAT_HINT = {"inr": "the INR result comes back about 45 minutes after the orders",
             "decision": "the admission decision comes about 95 minutes after she was seen",
             "admitted": "approve the early rounds on Surgery first; her bed is the first one they free",
             "night": "the first night after surgery (02:00)",
             "discharge": "the discharge on the third day after surgery",
             "followup": "48 hours after the discharge"}


def status(store: Store, user: StaffUser) -> dict:
    from app.modules.storyline import autoplay
    from app.workflows import client, progress

    st = state(store)
    clock = simulator.status(store, upcoming=0)
    link = client.link()
    view: dict = {
        "prepared": st is not None, "now": clock["now"].isoformat(), "day_start": clock["day_start"].isoformat(),
        "horizon": clock["horizon"].isoformat(), "running": clock["running"], "job": simjobs.current(),
        "total_seconds": S.TOTAL_SECONDS, "you": _user(store, user.id) or {"id": user.id, "name": user.name,
                                                                           "role": str(user.role), "units": []},
        "temporal": {"configured": link is not None,
                     "status": (link.status() if link is not None else client.offline_status())},
        "patient": None, "steps": [], "current": None, "beats": {},
        "autoplay": autoplay.current(st["prepared_at"]) if st is not None else None,
        # the reset's choice of patient
        "languages": [{"code": p.language, "label": p.label} for p in S.PATIENTS.values()]}
    who = patient_of(st)
    automated = autoplay.marks(st["prepared_at"]) if st is not None else {}
    journey = progress.get(S.JOURNEY)
    view["journey"] = progress.summary(journey) if journey is not None else None
    facts, story_beats = _cached(store, st, view["job"]) if st is not None else (None, {})
    if st is not None:
        day = parse(st["prepared_at"]).date()
        call_lines, followup_lines = who.lines(who.call_lines, day), who.lines(who.followup_lines, day)
        closing = who.followup_closing
        view["patient"] = {"id": S.PATIENT, "mrn": S.MRN, "name": who.name, "name_native": who.native_name,
                           "birth_date": st["birth_date"], "age": S.AGE, "language": who.code,
                           "story_language": who.language, "call_language": who.call_language,
                           "call_lines": [text for text, _ in call_lines],
                           "followup_lines": [text for text, _ in followup_lines],
                           "followup_closing": closing[0],
                           # each scripted line in another language -> its English translation (the subtitles)
                           "translations": {text: en for text, en in [*call_lines, *followup_lines, closing]
                                            if text != en},
                           "imaging_record": S.IMAGING_PATIENT, "mpi": st.get("mpi")}
        view["beats"] = story_beats
        view["capacity"] = {"surgery_ready": (st.get("capacity") or {}).get("surgery_ready"),
                            "warnings": (st.get("capacity") or {}).get("warnings")}
    now = parse(view["now"])
    current_found = False
    for step in S.steps(who.language):
        item = {"n": step.n, "key": step.key, "title": step.title, "module": step.module, "route": step.route,
                "click_path": list(step.click_path), "say": step.say, "expect": list(step.expect), "ai": step.ai,
                "signoff": step.signoff, "writes": step.writes, "seconds": step.seconds, "stub": step.stub,
                "beat": step.beat, "beat_hint": BEAT_HINT.get(step.beat or ""), "permission": step.permission,
                "cast": _user(store, S.CAST[step.cast]), "cast_key": step.cast}
        item["automated"] = automated.get(step.key)  # done by the demo's automation: who started it, as whom
        if facts is not None:
            item.update(_step_state(step, facts, store, who))
            if step.key in AI_TEXT:
                item["step_ai"] = _step_ai(store, step.key, st)
        else:
            item.update(proposal=None, signed=None, written=[], audit=[], detail=None, done=False, ready=False)
        ready = item.pop("ready")
        if not item["done"] and not current_found:
            current_found = True
            view["current"] = step.n
            beat = step.beat
            if step.key == "bed" and item["signed"]:  # approved: then the wait for the first bed the rounds free
                beat = "admitted"
                item.update(beat=beat, beat_hint=BEAT_HINT[beat])
            beat_at = view["beats"].get(beat) if beat else None
            item["advance_ai"] = AI_TEXT.get(AI_BY_BEAT.get(beat or "", ""))
            # waiting while the beat is ahead, unless the step's moment came already (the beat is a minute or two
            # after the event it waits for; with Temporal the journey's timer schedules the follow-up call early)
            item["status"] = "waiting" if beat and (beat_at is None or parse(beat_at) > now) and \
                (not ready or beat != step.beat) else "active"
            item["beat_at"] = beat_at
        else:
            item["status"] = "done" if item["done"] else "pending"
        view["steps"].append(item)
    return view
