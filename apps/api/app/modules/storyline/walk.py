"""Walk the storyline's twelve steps through the HTTP API, as the people who play them (spec 8.2, WP9).

The backend test (tests/test_storyline.py), the storyline report (scripts/storyline_report.py) and anyone checking
a running API use the same walk. Each step does what the presenter does on the page (the same endpoints the web app
calls), signed in as that step's person, then waits until the storyline's status shows the step done, and records
how long it took.

Two things depend on where the API runs, so the caller passes them in:
- `pump()`: one pass of the background work (event delivery, the medication-safety intake, the Control Tower's
  refresh and narration, the documentation intake, the follow-up sweep). In process (tests) the caller runs those
  steps; against a running API it just waits a moment, the API's own loops do it.
- `advance(beat)`: moving the hospital clock to the step's beat. Against a running API this is
  POST /api/storyline/advance and waiting for the fast-forward job (`api_advance`); in process the caller steps the
  simulator.

The demo's "Do this step for me" and "Jump to step N" (autoplay.py) run the same walk inside the API, as the cast
with sign-in tokens marked as the automation's (`automation`: who started it). Every note the walk writes then says
"Demo automation on behalf of <name> (<role>), started by <presenter>". A step the presenter began by hand is
finished from where it stands: a decided card, an approved exception, a handled discrepancy or a signature already
there is not done again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.modules.storyline import script as S

# The discharge summary is drafted in one synchronous request: a model call per chunk of the stay's facts (four for
# the storyline's stay) and the synthesis, each within the summary agent's budget. A Vertex stall uses up a call's
# whole budget (casebook 3.15), so a draft that gets past its stalls can take this many budgets.
SUMMARY_CALLS = 5


def summary_draft_s() -> float:
    """The longest the storyline's discharge summary draft can take: SUMMARY_CALLS x the summary agent's
    model_policy.timeout_s (LLM_TIMEOUT_S when it has none)."""
    from app.agents import registry
    from app.core.config import settings

    return SUMMARY_CALLS * (registry.require_agent("discharge_summary").model_policy.timeout_s
                            or settings.llm_timeout_s)


class WalkFailed(AssertionError):
    pass


@dataclass
class StepRun:
    n: int
    key: str
    title: str
    user: str
    seconds: float = 0.0
    ok: bool = False
    result: str = ""
    refs: list[str] = field(default_factory=list)


class Walk:
    def __init__(self, client: Any, *, pump: Callable[[], None], advance: Callable[["Walk", str], None],
                 log: Callable[[str], None] = lambda _m: None, timeout_s: float = 120,
                 summary_timeout_s: float | None = None, automation: str | None = None,
                 people: dict[str, tuple[str, str]] | None = None) -> None:
        self.client, self.pump, self._advance, self.log, self.timeout_s = client, pump, advance, log, timeout_s
        # Step 9 waits for the discharge summary draft: against a live model longer than for other background work
        # (scripts/storyline_report.py passes timeout_s + summary_draft_s()); None: timeout_s.
        self.summary_timeout_s = summary_timeout_s
        # The demo automation (autoplay.py): who started it, and the cast's names and roles for its notes
        self.automation, self.people = automation, people or {}
        self.tokens: dict[str, str] = {}
        self.runs: list[StepRun] = []

    def note(self, text: str, user: str) -> str:
        """A note or reason the walk writes as `user`: as it is, or marked as the demo automation's."""
        if not self.automation:
            return text
        name, role = self.people.get(user, (user, "?"))
        return f"Demo automation on behalf of {name} ({role}), started by {self.automation}: {text}"

    # ----- HTTP as a member of staff -----

    def headers(self, user_id: str) -> dict:
        if user_id not in self.tokens:
            r = self.client.post("/api/auth/login", json={"user_id": user_id})
            self._ok(r, f"login {user_id}")
            self.tokens[user_id] = r.json()["token"]
        return {"Authorization": f"Bearer {self.tokens[user_id]}"}

    def _ok(self, r, what: str):
        if r.status_code >= 400:
            raise WalkFailed(f"{what}: HTTP {r.status_code} {r.text[:400]}")
        return r

    def get(self, path: str, user: str, **kw) -> Any:
        return self._ok(self.client.get(path, headers=self.headers(user), **kw), f"GET {path} as {user}").json()

    def post(self, path: str, user: str, json: dict | None = None) -> Any:
        return self._ok(self.client.post(path, json=json or {}, headers=self.headers(user)),
                        f"POST {path} as {user}").json()

    def put(self, path: str, user: str, json: dict) -> Any:
        return self._ok(self.client.put(path, json=json, headers=self.headers(user)), f"PUT {path} as {user}").json()

    def status(self, user: str = S.CAST["admin"]) -> dict:
        return self.get("/api/storyline", user)

    def step(self, key: str, user: str | None = None) -> dict:
        n = S.BY_KEY[key].n
        return next(s for s in self.status(user or S.CAST["admin"])["steps"] if s["n"] == n)

    def wait(self, what: str, check: Callable[[], Any], timeout_s: float | None = None) -> Any:
        deadline = time.monotonic() + (timeout_s or self.timeout_s)
        while True:
            self.pump()
            value = check()
            if value:
                return value
            if time.monotonic() > deadline:
                raise WalkFailed(f"timed out waiting for {what}")
            time.sleep(0.2)

    def done(self, key: str, timeout_s: float | None = None) -> dict:
        return self.wait(f"step {key} done", lambda: (s := self.step(key))["done"] and s, timeout_s)

    def decide(self, task_ref: str, user: str, decision: str = "approve", note: str = "") -> dict:
        return self.post(f"/api/agents/tasks/{task_ref.split('/', 1)[1]}/decision", user,
                         {"decision": decision, "note": self.note(note, user)})

    def decide_card(self, key: str, user: str, note: str) -> dict:
        """Wait for the step's card and decide it, unless it is decided already (the presenter clicked it)."""
        card = self.proposal(key, user)
        if not self.step(key, user).get("signed"):
            self.decide(card["ref"], user, note=note)
        return card

    def proposal(self, key: str, user: str | None = None) -> dict:
        return self.wait(f"the AI proposal of step {key}", lambda: self.step(key, user).get("proposal"))

    def advance(self, beat: str) -> None:
        self._advance(self, beat)

    def open_ai(self, path: str, action: str, user: str) -> dict:
        """Open an item as the web app does: when its AI has not run (BACKGROUND_AI=on_demand, or still queued),
        opening asks for it (`POST <path>/<action>?trigger=opened`; a run the step's button started is waited
        for, not repeated)."""
        view = self.get(path, user)
        ai = view.get("ai") or {}
        if ai.get("auto") and ai.get("state") in ("not_requested", "queued"):
            view = self.post(f"{path}/{action}?trigger=opened", user)
        return view

    # ----- the steps -----

    def run(self, keys: list[str] | None = None) -> list[StepRun]:
        for st in S.STEPS:
            if keys and st.key not in keys:
                continue
            user = S.CAST[st.cast]
            run = StepRun(st.n, st.key, st.title, user)
            started = time.monotonic()
            try:
                run.result, run.refs = getattr(self, f"_{st.key}")(user)
                run.ok = True
            finally:
                run.seconds = round(time.monotonic() - started, 2)
                self.runs.append(run)
                self.log(f"[{run.seconds:6.1f} s] step {st.n:2d} {st.key}: {'ok' if run.ok else 'FAILED'} {run.result}")
        return self.runs

    def _registration(self, user: str) -> dict | None:
        return next((r for r in self.get("/api/voice/requests", user)["requests"]
                     if r.get("kind") == "registration" and r.get("patient_id") == S.PATIENT), None)

    def _call(self, user: str):
        request = self._registration(user)  # the presenter's call already verified her: only the confirmation left
        what = "the presenter's call"
        if request is None:
            lines = self.status(user)["patient"]["call_lines"]
            call = self.post("/api/voice/calls", user)
            for line in lines:
                reply = self.post(f"/api/voice/calls/{call['id']}/turn", user, {"text": line})
                if reply.get("ended"):
                    break
            call = self.get(f"/api/voice/calls/{call['id']}", user)
            if not call.get("ended_at"):
                call = self.post(f"/api/voice/calls/{call['id']}/end", user)
            if call.get("language") != "zh-Hans" or call.get("patient_id") != S.PATIENT:
                raise WalkFailed(f"call not verified in Chinese: {call.get('language')} {call.get('patient_id')}")
            request = self.wait("the registration request", lambda: self._registration(user))
            what = f"call {call['id']} ({call['caller_relation']})"
        self.decide(f"Task/{request['task_id']}", user, note="Registered at the ED desk")
        step = self.done("call")
        return f"{what}, registration Task {request['task_id']}", step["written"]

    def _triage(self, user: str):
        card = self.decide_card("triage", user, "CTAS 2 confirmed at triage")
        step = self.done("triage")
        if "0.82" not in (step.get("detail") or ""):
            raise WalkFailed(f"admission probability: {step.get('detail')}")
        return f"{card['label']}; {step['detail']}", step["written"]

    def _imaging(self, user: str):
        card = self.decide_card("imaging", user, "Order both")
        step = self.done("imaging")
        return card["summary"], step["written"]

    def _inr(self, user: str):
        self.advance("inr")
        card = self.decide_card("inr", user, "Seen: vitamin K given, anaesthesia informed")
        step = self.done("inr")
        return card["summary"], step["written"]

    def _bed(self, user: str):
        approved = (self.step("bed", user).get("signed") or {}).get("ref")
        if approved:  # the presenter approved the rounds: only the wait for her bed is left
            self.advance("admitted")
            step = self.done("bed")
            return f"{approved} approved before; admitted", step["written"]
        self.advance("decision")
        exc = self.wait("the Orthopedics exception (high, she is boarding)", lambda: next(
            (e for e in self.get("/api/control-tower/exceptions", user)["exceptions"]
             if e["key"] == "unit_occupancy:ORTH" and e["status"] == "open" and e["severity"] == "high"), None))
        self.open_ai(f"/api/control-tower/exceptions/{exc['id']}", "narrate", user)
        detail = self.wait("the narrated exception", lambda: (d := self.get(
            f"/api/control-tower/exceptions/{exc['id']}", user)).get("narration") and d)
        if not any(m["action_id"] == "overflow_discharge_rounds" for m in detail.get("menu") or []):
            raise WalkFailed(f"no overflow rounds on the menu: {[m['action_id'] for m in detail.get('menu') or []]}")
        actions = [m["action_id"] for m in detail["menu"] if m["action_id"] in ("overflow_discharge_rounds",
                                                                                "confirm_discharges")]
        self.post(f"/api/control-tower/exceptions/{exc['id']}/approve", user,
                  {"action_ids": actions, "note": self.note("Round early on Surgery", user)})
        self.advance("admitted")
        step = self.done("bed")
        return f"{exc['title']} ({exc['id']}) approved: {', '.join(actions)}", step["written"]

    def _medrec(self, user: str):
        surgeon = S.CAST["surgeon"]
        review = self.wait("the naproxen order review", lambda: next(
            (i for i in self.get("/api/med-safety/queue?show=all", user)["items"]
             if i.get("order_ref") == f"MedicationRequest/medrx-{S.STAY}-03"), None))
        view = self.open_ai(f"/api/med-safety/reviews/{review['id']}", "commentary", user)
        if not any("warfarin" in str(f).lower() for f in view.get("findings") or []):
            raise WalkFailed("no warfarin interaction finding on the naproxen order")
        if view.get("can_decide"):
            self.post(f"/api/med-safety/reviews/{review['id']}/decision", user,
                      {"decision": "request_change", "reason": self.note("Stop naproxen: warfarin at home, INR 3.8",
                                                                         user)})
        session = self.wait("the admission reconciliation", lambda: next(
            (m for m in self.get(f"/api/med-safety/encounters/{S.STAY}", user).get("medrec") or []
             if m.get("kind") == "admission"), None))
        sid = session.get("id") or session.get("session_id")
        self.open_ai(f"/api/med-safety/medrec/{sid}", "ai", user)
        view = self.wait("the reconciliation's AI step", lambda: (v := self.get(f"/api/med-safety/medrec/{sid}", user))
                         ["ai_status"] not in ("pending", "on_open") and v)
        def name(item: dict) -> str:
            return ((item.get("home") or item.get("order") or {}).get("name") or "").lower()

        omissions = [i for i in view["items"] if i.get("type") == "omission"]
        if not any("amlodipine" in name(i) for i in omissions):
            raise WalkFailed(f"no amlodipine omission: {[name(i) for i in omissions]}")
        for item in view["items"]:
            if item["discrepancy"] and not item.get("action"):
                name_ = name(item)
                if "amlodipine" in name_:
                    body = {"action": "modify", "reason": "Home antihypertensive left out on admission",
                            "change": "Resume amlodipine 5 mg once daily"}
                elif "warfarin" in name_:
                    body = {"action": "accept", "reason": "Held on purpose: INR 3.8 before surgery"}
                else:
                    body = {"action": "accept", "reason": "Reviewed with the orthopaedic team"}
                body["reason"] = self.note(body["reason"], user)
                self.post(f"/api/med-safety/medrec/{sid}/items/{item['id']}", user, body)
        if self.get(f"/api/med-safety/medrec/{sid}", user)["status"] == "open":  # not signed by hand already
            self.post(f"/api/med-safety/medrec/{sid}/sign", user)
        if self.get(f"/api/med-safety/medrec/{sid}", surgeon)["status"] == "pharmacist_signed":
            self.post(f"/api/med-safety/medrec/{sid}/sign", surgeon)
        step = self.done("medrec")
        return f"{len(omissions)} omission(s) incl. amlodipine; MedRec {sid} final", step["written"]

    def _or(self, user: str):
        board = self.wait("her OR case on the board", lambda: next(
            (c for c in self.get("/api/control-tower/board", user)["or_cases"]
             if c.get("appointment_id") and c.get("mrn") == S.MRN), None))
        if not board.get("predicted_minutes") or board["predicted_minutes"] - board["booked_minutes"] < 15:
            raise WalkFailed(f"no overrun predicted: {board}")
        adjusted = self.post(f"/api/control-tower/or-cases/{board['appointment_id']}/adjust", user,
                             {"note": self.note("Adjusted to the predicted duration; theatre staff told", user)})
        step = self.done("or")
        return f"booked {adjusted['previous_minutes']} -> {adjusted['minutes']} min (predicted " \
               f"{adjusted['predicted_minutes']})", step["written"]

    def _news2(self, user: str):
        self.advance("night")
        card = self.decide_card("news2", user, "Assessed: fluids, analgesia review, physician informed")
        step = self.done("news2")
        return card.get("summary") or card["label"], step["written"]

    def _summary(self, user: str):
        self.advance("discharge")
        view = self.wait("the discharge summary draft", lambda: self._draft_summary(user), self.summary_timeout_s)
        red = [i for i in (view.get("validation") or {}).get("issues") or [] if i.get("severity") == "red"]
        names = {(i.get("value") or "").lower() for i in red}
        if self.automation and view.get("can_sign") and view.get("edits"):
            # the presenter already removed the untraceable medicine: only the signature is left
            self.post(f"/api/documentation/documents/{view['id']}/sign", user)
            step = self.done("summary")
            return "edited by hand before; signed", step["written"]
        if not red or view.get("can_sign"):
            raise WalkFailed(f"the validator blocked nothing: {red}")
        content = view["content"]
        content["medications"]["new"] = [m for m in content["medications"]["new"]
                                         if (m.get("name") or "").lower() not in names]
        edited = self.put(f"/api/documentation/documents/{view['id']}", user, {"content": content})
        if not edited.get("can_sign"):
            raise WalkFailed(f"still blocked after the removal: {(edited.get('validation') or {}).get('issues')}")
        self.post(f"/api/documentation/documents/{view['id']}/sign", user)
        step = self.done("summary")
        return f"blocked {sorted(names)}; removed and signed", step["written"]

    def _draft_summary(self, user: str):
        r = self.client.post(f"/api/documentation/encounters/{S.STAY}/discharge-summary", json={},
                             headers=self.headers(user))
        if r.status_code >= 400:
            return None
        view = r.json()
        return view if view.get("status") != "insufficient" and view.get("content") else None

    def _instructions(self, user: str):
        view = self.wait("the patient instructions draft", lambda: (r := self.client.post(
            f"/api/documentation/encounters/{S.STAY}/patient-instructions", json={},
            headers=self.headers(user))).status_code < 400 and r.json().get("content") and r.json())
        text = str(view.get("rendered") or view.get("texts") or view)
        if "绿叶蔬菜" not in text:
            raise WalkFailed("no warfarin diet advice in the Chinese instructions")
        self.post(f"/api/documentation/documents/{view['id']}/sign", user)
        step = self.done("instructions")
        return f"{view['id']} ({view.get('default_language')}) signed", step["written"]

    def _followup(self, user: str):
        self.advance("followup")
        call_id = S.FOLLOWUP_CALL
        self.wait("the scheduled follow-up call", lambda: (r := self.client.get(
            f"/api/voice/followups/{call_id}", headers=self.headers(user))).status_code == 200 and
            r.json().get("status") in ("scheduled", "in_progress", "completed"), timeout_s=self.timeout_s * 2)
        patient = self.status(user)["patient"]
        lines, closing = patient["followup_lines"], patient["followup_closing"]
        call = self.get(f"/api/voice/followups/{call_id}", user)
        if call["status"] == "scheduled":
            self.post(f"/api/voice/followups/{call_id}/start", user)
            call = self.get(f"/api/voice/followups/{call_id}", user)
        if call["status"] == "in_progress" and not call.get("escalated"):
            # answered from the question the call is at: her birth date only while it asks who she is (a call the
            # presenter placed may be past that)
            asked = next((t.get("question") for t in reversed(call.get("transcript") or []) if t["role"] == "agent"),
                         None)
            kind = next((q["kind"] for q in call.get("script") or [] if q["key"] == asked), "identity")
            for text in [*(lines if kind == "identity" else lines[1:]), *[closing] * 6]:
                answer = self.post(f"/api/voice/followups/{call_id}/answer", user, {"text": text})
                if answer.get("ended"):
                    break
        call = self.get(f"/api/voice/followups/{call_id}", user)
        if not call.get("escalated"):
            raise WalkFailed(f"no escalation: {call.get('status')} {call.get('rule_hits')}")
        if not any(a.get("kind") in ("callback", "close", "refer") for a in call.get("nurse_actions") or []):
            self.post(f"/api/voice/followups/{call_id}/actions", user, {
                "kind": "callback", "note": self.note("Called back: wound reviewed tomorrow at the fracture clinic",
                                                      user)})
        step = self.done("followup")
        return f"{call_id}: escalated ({call.get('priority')}), call-back recorded", step["written"]

    def _coding(self, user: str):
        card = self.decide_card("coding", user, "Codes confirmed")
        self.done("coding")
        return card["summary"], [card["ref"]]


def api_advance(client: Any, *, user: str, deadline_s: float = 900, poll_s: float = 1.5) -> Callable[[Walk, str], None]:
    """`advance` against a running API: POST /api/storyline/advance (as `user`) whenever no fast-forward runs, until
    the clock is at the beat; the API's job and loops do the rest (the storyline report, the demo automation)."""

    def advance(walk: Walk, beat: str) -> None:
        deadline = time.monotonic() + deadline_s
        while True:
            status = walk.status()
            target = status["beats"].get(beat)
            if target and status["now"] >= target:
                return
            job = status.get("job") or {}
            if job.get("status") != "running":
                r = client.post("/api/storyline/advance", headers=walk.headers(user))
                if r.status_code >= 400 and "Already there" not in r.text and "Not yet" not in r.text:
                    raise WalkFailed(f"advance {beat}: {r.status_code} {r.text[:300]}")
            if time.monotonic() > deadline:
                raise WalkFailed(f"the clock did not reach {beat}")
            time.sleep(poll_s)

    return advance
