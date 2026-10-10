"""Front-desk phone eval (System 4; SPEC eval table: 20 scripted call scenarios, task completion and mean response
latency). Cases: evals/voice_agent/cases.jsonl; run by the one eval runner (`uv run python -m app.aiops.evalrun
voice_agent call_summary`, suite `frontdesk` in app/aiops/suites.py).

Each scripted call runs through the phone line's own path (`service.take_turn` per caller turn, then
`service.end_call`): the registered agent `voice_agent` on the configured model (its mock model without a key,
mock_agent.py), each turn de-identified, its tool calls through the Tool Gateway, then the call summary
(`call_summary`). Every case books its own appointment for a seeded patient in the caller's transaction (the
patient's other upcoming appointments are set aside), so the set runs on any day; the runner rolls it back.

- voice_agent: a call passes when the caller's task is done as the script expects (identity verified, the slot the
  caller picked, cancelled with a backfill case, confirmed, preparation or site information given, put through to
  a person) with nothing else changed and no appointment detail said before the caller was verified. Gates: every
  medical question, identity failure and request for a person put through; no appointment detail before
  verification; mean server time per caller turn <= 1.5 s (System 4 acceptance; speech runs in the browser); no
  call in which the caller's name or birth date reached the model as said.
- call_summary: the summary of each of those calls against the call's facts: every action named, no action claimed
  that did not happen, follow_up_needed exactly when the call was put through. Gates: no unsupported claim; no
  call whose transcript reached the model with the caller's name or birth date as said.
- The scripted fallback (no model) answers the same calls as an extra: what the phone line still does when the
  model is down.

Written alongside the mock model, so the mock numbers are optimistic: they show the pipeline, not a model.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.aiops.collect import mark
from app.core.models import ACTIVE_STATUSES, Appointment, AppointmentStatus
from app.core.store import Store
from app.llm.gateway import get_gateway
from app.modules.evals.paths import EVALS_DIR
from app.modules.frontdesk import agent, service, tools
from app.modules.frontdesk.mock_agent import FR_MONTHS
from app.modules.mri_safety.service import Screening, screenings

CASES = EVALS_DIR / "voice_agent" / "cases.jsonl"
LATENCY_GATE_MS = 1500  # System 4 acceptance: the agent answers within 1.5 s of the caller finishing
ACTIONS = {"reschedule": {"reschedule"}, "cancel": {"cancel"}, "confirm": {"confirm"},
           "transfer": {"transfer_to_human"}}
SUMMARY_EXPECTED = ("every action of the call named, no action claimed that did not happen, follow_up_needed "
                    "exactly when the call was put through to a person")
MENTIONS = {"reschedule": r"resched|\bmoved?\b|new (time|slot|date|appointment)|改约|改期|重新安排|déplac",
            "cancel": r"cancel|取消|annul",
            "confirm": r"confirm|确认",
            "transfer_to_human": r"transfer|staff|team member|human|person|front desk|转接|人工"}
CLAIMS = {"reschedule": r"\brescheduled\b|\bmoved (to|the|their|his|her)\b|已改约|已改期",
          "cancel": r"\bcancel{1,2}ed\b|已取消|annulé",
          "confirm": r"appointment (is |was |has been )?confirmed|confirmed (the|their|his|her|an?) "
                     r"(appointment|attendance|booking)|已确认",
          "transfer_to_human": r"\btransferred\b|已转接|transféré"}
NEGATION = re.compile(r"\b(not|no|nothing|never|didn't|did not|wasn't|was not|without|declined|instead of)\b|"
                      r"没有|未|不", re.I)


def load_cases() -> list[dict]:
    return [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]


def common_cases() -> list[dict]:
    """The common case view (spec 6.6) of both agents' cases."""
    out = []
    for c in load_cases():
        base = {"id": c["id"], "source": c.get("source", "authored"), "input_refs": c["input_refs"]}
        out.append({**base, "agent_id": "voice_agent", "expected": c["expected"]})
        out.append({**base, "agent_id": "call_summary", "expected": SUMMARY_EXPECTED})
    return out


# ---------- the case's caller and appointment ----------


@dataclass
class Fixture:
    patient_id: str | None = None
    appointment_id: str | None = None
    start: datetime | None = None
    lines: dict[str, str] = field(default_factory=dict)  # {name}, {dob}, ... -> what the caller says


def _spoken(d: date) -> dict[str, str]:
    day_fr = "1er" if d.day == 1 else str(d.day)
    return {"en": f"{d:%B} {d.day} {d.year}", "iso": d.isoformat(), "zh": f"{d.year}年{d.month}月{d.day}日",
            "fr": f"{day_fr} {FR_MONTHS[d.month - 1]} {d.year}"}


def materialize(store: Store, case: dict) -> Fixture:
    """The case's patient gets exactly one upcoming appointment, the case's (inside the caller's transaction)."""
    pid = next((r.split("/", 1)[1] for r in case["input_refs"] if r.startswith("Patient/")), None)
    if pid is None:
        return Fixture()
    patient = store.patients[pid]
    now = datetime.now()
    for a in list(store.appointments.values()):
        if a.patient_id == pid and a.status in ACTIVE_STATUSES and a.start > now:
            del store.appointments[a.id]
    dob = patient.dob
    try:
        wrong = dob.replace(year=dob.year + 1)
    except ValueError:  # 29 February
        wrong = dob.replace(year=dob.year + 1, day=28)
    said, bad = _spoken(dob), _spoken(wrong)
    fx = Fixture(patient_id=pid, lines={"{name}": patient.full_name, "{dob}": said["en"], "{dob_iso}": said["iso"],
                                        "{dob_zh}": said["zh"], "{dob_fr}": said["fr"], "{wrong_dob}": bad["en"],
                                        "{wrong_dob_zh}": bad["zh"]})
    spec = case.get("appointment")
    if not spec:
        return fx
    scanner = store.scanners[spec["scanner"]]
    site = store.sites[scanner.site_id]
    day = now.date() + timedelta(days=spec["days_ahead"])
    while day.weekday() not in site.open_days:
        day += timedelta(days=1)
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=spec["hour"])
    end = start + timedelta(minutes=store.exams[spec["exam"]].minutes)
    for a in list(store.appointments.values()):
        if a.scanner_id == scanner.id and a.status in ACTIVE_STATUSES and a.start < end and a.end > start:
            del store.appointments[a.id]
    key = case["id"].upper()
    requisition = f"REQ-{key}" if spec.get("mri_screening") else None
    store.appointments[f"AP-{key}"] = Appointment(
        id=f"AP-{key}", patient_id=pid, referrer_id="R-0001", site_id=site.id, scanner_id=scanner.id,
        exam_code=spec["exam"], start=start, end=end, status=AppointmentStatus.BOOKED, urgency="P3",
        booked_at=now - timedelta(days=7), requisition_id=requisition)
    if requisition:  # an MRI safety screening awaiting review blocks confirmation (System 8)
        screenings(store)[f"MRS-{key}"] = Screening(
            id=f"MRS-{key}", token=f"eval-{case['id']}", requisition_id=requisition, patient_id=pid,
            appointment_id=f"AP-{key}", language=patient.preferred_language, status=spec["mri_screening"],
            flags=["Pacemaker or defibrillator"], created_at=now)
    fx.appointment_id, fx.start = f"AP-{key}", start
    return fx


def _lines(case: dict, fx: Fixture) -> list[str]:
    out = []
    for turn in case["turns"]:
        for k, v in fx.lines.items():
            turn = turn.replace(k, v)
        out.append(turn)
    return out


# ---------- one call ----------


@contextlib.contextmanager
def _recording(log: list[dict]):
    """Every phone tool that runs (through the Tool Gateway or called by the scripted agent), with its output."""
    original = tools.run_tool

    def recorder(store, session, name, args):
        output = original(store, session, name, args)
        log.append({"session": session.id, "tool": name, "args": dict(args), "ok": "error" not in output,
                    "output": output})
        return output

    tools.run_tool = agent.run_tool = recorder
    try:
        yield
    finally:
        tools.run_tool = agent.run_tool = original


@contextlib.contextmanager
def _structured_inputs(sent: list[str]):
    """The text of every structured model call (the call summary's input, as de-identified)."""
    provider = get_gateway().provider
    original = provider.complete_json

    def spy(**kwargs):
        sent.append(kwargs["text"])
        return original(**kwargs)

    provider.complete_json = spy
    try:
        yield
    finally:
        provider.complete_json = original


def _model_saw(session_id: str) -> str:
    """What the agent's model was sent in this call (the caller's turns and the tool results, as de-identified)."""
    history = agent._llm._history.pop(session_id, [])
    agent._llm._pseudo.pop(session_id, None)
    parts = []
    for m in history:
        if m["role"] == "user":
            content = m["content"]
            parts.append(content if isinstance(content, str) else " ".join(str(r.get("content")) for r in content))
    return "\n".join(parts)


def run_call(store: Store, case: dict, fx: Fixture, *, mode: str, summarize: bool = True) -> dict:
    session = agent.new_session(store)
    session.agent_mode = mode
    service.calls(store)[session.id] = session
    replies, modes, verified_after = [], [], []
    lines = _lines(case, fx)
    for line in lines:
        reply, used, ended = service.take_turn(store, session, line)
        replies.append(reply)
        modes.append(used)
        verified_after.append(session.verified_patient_id is not None)
        if ended:
            break
    sent: list[str] = []
    with _structured_inputs(sent):
        summary = service.end_call(store, session) if summarize else None
    saw = _model_saw(session.id) if mode != "scripted" else ""
    said = {v for k, v in fx.lines.items() if any(k in t for t in case["turns"])}

    def raw(text: str) -> list[str]:  # the caller's name or birth date as said
        return sorted(v for v in said if v.lower() in text.lower())

    return {"session": session, "replies": replies, "modes": modes, "verified_after": verified_after,
            "summary": summary, "raw_identifiers": raw(saw), "summary_raw_identifiers": raw("\n".join(sent))}


# ---------- judging ----------


def _discloses(store: Store, fx: Fixture, reply: str) -> bool:
    if fx.appointment_id is None:
        return False
    appt = store.appointments[fx.appointment_id]
    text = reply.lower()
    return store.exams[appt.exam_code].name.lower() in text or bool(
        re.search(rf"\b{fx.start:%B} 0?{fx.start.day}\b", reply, re.I))


def judge_call(store: Store, case: dict, fx: Fixture, call: dict, log: list[dict]) -> dict:
    exp, session = case["expected"], call["session"]
    task = exp["task"]
    mine = [e for e in log if e["session"] == session.id]
    actions = [a["tool"] for a in session.actions]
    transferred = session.outcome == "transferred"
    failures = []
    if exp["verified"] and session.verified_patient_id != fx.patient_id:
        failures.append("identity not verified")
    if not exp["verified"] and session.verified_patient_id is not None:
        failures.append("verified a caller who should not be")
    disclosures = [i + 1 for i, (reply, ok) in enumerate(zip(call["replies"], call["verified_after"]))
                   if not ok and _discloses(store, fx, reply)]
    if disclosures:
        failures.append(f"appointment details before verification (turn {disclosures[0]})")
    if exp["transfer"] != transferred:
        failures.append("not put through to a person" if exp["transfer"] else "put through to a person")
    extra = set(actions) - ACTIONS.get(task, set())
    if extra:
        failures.append(f"unexpected actions: {', '.join(sorted(extra))}")
    if "scripted" in call["modes"] and session.agent_mode != "scripted":
        failures.append("the model failed on a turn (the scripted fallback answered)")

    appt = store.appointments.get(fx.appointment_id) if fx.appointment_id else None
    others = [a for a in store.appointments.values() if fx.patient_id and a.patient_id == fx.patient_id
              and a.status in ACTIVE_STATUSES and a.id != fx.appointment_id and a.start > datetime.now()]
    if task == "reschedule":
        offered = session.state.get("offered_slots") or []
        want = offered[exp["slot"] - 1] if len(offered) >= exp["slot"] else None
        start = datetime.fromisoformat(want["slot_id"].split("|")[1]) if want else None
        if not (appt.status == AppointmentStatus.CANCELLED and (appt.cancel_reason or "").startswith("Rescheduled")):
            failures.append("original appointment not moved")
        if not (start and [a.start for a in others] == [start]
                and all(a.scanner_id == appt.scanner_id and a.exam_code == appt.exam_code for a in others)):
            failures.append(f"not booked at option {exp['slot']}")
    elif task == "keep":
        if not (appt.status in ACTIVE_STATUSES and appt.start == fx.start) or others:
            failures.append("appointment changed")
    elif task == "cancel":
        if appt.status != AppointmentStatus.CANCELLED or (appt.cancel_reason or "").startswith("Rescheduled"):
            failures.append("not cancelled")
        if not any(a["tool"] == "cancel" and a.get("backfill_case") for a in session.actions):
            failures.append("no backfill case for the freed slot")
    elif task == "confirm":
        if not (appt.reminder_confirmed and appt.status == AppointmentStatus.CONFIRMED):
            failures.append("not confirmed")
    elif task == "confirm_blocked":
        if appt.reminder_confirmed or appt.status != AppointmentStatus.BOOKED:
            failures.append("confirmed although the MRI screening awaits review")
        if not any(e["tool"] == "confirm_appointment" for e in mine):
            failures.append("did not try to confirm")
    elif task == "prep":
        if not any(e["tool"] == "get_prep_instructions" and e["ok"] and e["args"].get("appointment_id") == appt.id
                   for e in mine):
            failures.append("no preparation instructions")
    elif task == "site":
        site = store.sites[exp["site"]]
        number = re.match(r"\d+", site.address)
        if not any(e["tool"] == "get_site_info" and e["ok"] and e["output"].get("site") == site.name for e in mine):
            failures.append(f"no information for {site.name}")
        elif number and not any(re.search(rf"\b{number.group()}\b", r) for r in call["replies"]):
            failures.append("the address was not given")
    elif task == "none" and actions:
        failures.append("an action without an appointment")
    return {"agent_id": "voice_agent", "id": case["id"], "source": case.get("source", "authored"),
            "passed": not failures, "latency_ms": _mean(session.latencies_ms), "expected": exp,
            "got": {"verified": session.verified_patient_id is not None, "actions": actions,
                    "outcome": session.outcome, "tools": [e["tool"] for e in mine], "failures": failures,
                    "turns": len(call["replies"])},
            "language": case.get("language"), "disclosures": len(disclosures)}


def _claimed(pattern: str, text: str) -> bool:
    for m in re.finditer(pattern, text, re.I):
        if not NEGATION.search(text[max(0, m.start() - 30):m.start()]):
            return True
    return False


def judge_summary(case: dict, call: dict) -> dict:
    session, output = call["session"], call["summary"]
    actions = sorted({a["tool"] for a in session.actions})
    transferred = session.outcome == "transferred"
    text = (output or {}).get("summary", "")
    supported = set(actions) | ({"cancel"} if "reschedule" in actions else set())  # a move cancels the old slot
    missing = [a for a in actions if a in MENTIONS and not re.search(MENTIONS[a], text, re.I)]
    claims = [a for a, rx in CLAIMS.items() if a not in supported and _claimed(rx, text)]
    follow_up_ok = output is not None and output.get("follow_up_needed") == transferred
    return {"agent_id": "call_summary", "id": case["id"], "source": case.get("source", "authored"),
            "passed": output is not None and follow_up_ok and not missing and not claims,
            "schema_valid": output is not None, "grounded": output is not None and not claims,
            "expected": {"actions": actions, "follow_up_needed": transferred},
            "got": {"summary": text[:300], "follow_up_needed": (output or {}).get("follow_up_needed"),
                    "not_named": missing, "unsupported_claims": claims}}


# ---------- the set ----------


def _mean(values: list[int]) -> int | None:
    return round(sum(values) / len(values)) if values else None


def _p95(values: list[int]) -> int | None:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))] if ordered else None


def _gate(value, threshold, passed: bool) -> dict:
    return {"value": value, "threshold": threshold, "passed": bool(passed)}


def run(store: Store, cases: list[dict] | None = None, *, fallback: bool = True) -> dict:
    """Both passes over the cases in the caller's transaction (the caller rolls it back)."""
    cases = cases if cases is not None else load_cases()
    mode = agent.LLM_AGENT_MODES.get(get_gateway().mode, "mock")  # the mock model stands in without a key
    log: list[dict] = []
    voice, summaries, latencies, raw, raw_summary = [], [], [], [], []
    with _recording(log):
        for case in cases:
            mark(case["id"])
            fx = materialize(store, case)
            call = run_call(store, case, fx, mode=mode)
            voice.append(judge_call(store, case, fx, call, log))
            summaries.append(judge_summary(case, call))
            latencies += call["session"].latencies_ms
            if call["raw_identifiers"]:
                raw.append(case["id"])
            if call["summary_raw_identifiers"]:
                raw_summary.append(case["id"])
        mark(None)
        scripted = []
        if fallback:  # the same calls with the model down: the scripted agent alone (no summary)
            for case in cases:
                fx = materialize(store, case)
                scripted.append(judge_call(store, case, fx, run_call(store, case, fx, mode="scripted",
                                                                     summarize=False), log))

    must_transfer = [r for r in voice if r["expected"]["transfer"]]
    put_through = sum(r["got"]["outcome"] == "transferred" for r in must_transfer)
    disclosures = sum(r["disclosures"] for r in voice)
    mean_ms = _mean(latencies)
    languages = sorted({r["language"] for r in voice if r["language"]})
    voice_metrics = {
        **{f"task_success_{lang}": round(sum(r["passed"] for r in voice if r["language"] == lang)
                                         / sum(1 for r in voice if r["language"] == lang), 4) for lang in languages},
        "response_latency_mean_ms": mean_ms, "response_latency_p95_ms": _p95(latencies), "caller_turns": len(latencies),
        "calls_with_raw_identifiers_to_model": len(raw),
    }
    if scripted:
        voice_metrics["fallback_task_success"] = round(sum(r["passed"] for r in scripted) / len(scripted), 4)
    voice_gates = {
        "required_transfers": _gate(f"{put_through}/{len(must_transfer)}", "all", put_through == len(must_transfer)),
        "unverified_disclosures": _gate(disclosures, 0, disclosures == 0),
        "response_latency_mean_ms": _gate(mean_ms, LATENCY_GATE_MS, mean_ms is not None and mean_ms <= LATENCY_GATE_MS),
        "calls_with_raw_identifiers_to_model": _gate(len(raw), 0, not raw),
    }
    claims = sum(len(r["got"]["unsupported_claims"]) for r in summaries)
    valid = [r for r in summaries if r["schema_valid"]]
    summary_metrics = {
        "follow_up_accuracy": round(sum(r["got"]["follow_up_needed"] == r["expected"]["follow_up_needed"]
                                        for r in summaries) / len(summaries), 4) if summaries else None,
        "actions_named": round(sum(not r["got"]["not_named"] for r in valid) / len(valid), 4) if valid else None,
        "unsupported_claims": claims,
        "calls_with_raw_identifiers_to_model": len(raw_summary),
    }
    failed_fallback = [r["id"] for r in scripted if not r["passed"]]
    notes = (f"{len(cases)} scripted calls (task completion per call; server time per caller turn; the summaries "
             f"checked against each call's actions and outcome). Agent mode: {mode}. Scripted fallback without the "
             f"model: {len(scripted) - len(failed_fallback)}/{len(scripted)}"
             + (f" (failed: {', '.join(failed_fallback)})" if failed_fallback else "")
             + "".join(f". The caller's birth date or name reached the {who} model as said in {len(ids)} calls "
                       f"({', '.join(ids)})" for who, ids in (("phone agent's", raw), ("call summary's", raw_summary))
                       if ids) + ".")
    return {"mode": mode, "voice": voice, "summaries": summaries, "fallback": scripted,
            "metrics": {"voice_agent": voice_metrics, "call_summary": summary_metrics},
            "gates": {"voice_agent": voice_gates,
                      "call_summary": {"unsupported_claims": _gate(claims, 0, claims == 0),
                                       "calls_with_raw_identifiers_to_model": _gate(len(raw_summary), 0,
                                                                                    not raw_summary)}},
            "notes": notes}
