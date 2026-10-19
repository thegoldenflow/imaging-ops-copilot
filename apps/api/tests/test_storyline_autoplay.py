"""Demo mode's automation for the hospital storyline (spec 8.2, WP9): "Do this step for me" for each of the twelve
steps, "Jump to step N", a step the presenter began by hand finished from where it stands, the presenter's audit
event, and the marks on everything the automation signed (Temporal off).

The API runs the job in a thread against itself; here the background workers are off, so the test runs the job in
its own transaction with the in-process harness (`autoplay.run`), through the same HTTP API."""

from __future__ import annotations

import pytest

from app.ehr import accepted
from app.ehr.seed import journey
from app.ehr.signing import signatures
from app.modules.storyline import autoplay, service
from app.modules.storyline import script as S
from app.modules.storyline.walk import Walk
from tests import storyline_harness as H

PRESENTER = "U-ADMIN"


@pytest.fixture
def story(fresh_state, monkeypatch):
    accepted.install()
    service.install()
    journey.plant(fresh_state, "U-ADMIN")
    monkeypatch.setattr(autoplay, "_job", None)  # the job and its marks are per process: none from an earlier test
    monkeypatch.setattr(autoplay, "_marks", {})
    return fresh_state


def play(client, headers: dict, path: str) -> dict:
    r = client.post(path, headers=headers)
    assert r.status_code == 200, r.text
    started = r.json()
    assert started["status"] == "running"
    ended = autoplay.run(started["id"], client=client, pump=H.pump, advance=H.advance, timeout_s=60)
    assert ended is not None and ended["status"] == "done", ended
    return ended


def story_view(client, headers: dict) -> dict:
    return client.get("/api/storyline", headers=headers).json()


def by_hand(client) -> Walk:
    """The presenter clicking through part of a step themselves (no automation)."""
    return Walk(client, pump=H.pump, advance=H.advance, timeout_s=60)


def marked(text: str | None, user_id: str, store, presenter: str = "Casey Brooks") -> bool:
    user = store.staff[user_id]
    return (text or "").startswith(f"Demo automation on behalf of {user.name} ({str(user.role).replace('_', ' ')}), "
                                   f"started by {presenter}")


def test_do_this_step_for_me_does_each_step_as_the_person_who_plays_it(story, client, login):
    store, presenter = story, login(PRESENTER)
    for step in S.STEPS:
        job = play(client, presenter, f"/api/storyline/steps/{step.key}/autoplay")
        assert job["keys"] == [step.key] and job["done"] == [step.key] and job["mode"] == "step"
        assert job["results"][0]["ok"], job["results"]
        view = story_view(client, presenter)
        assert [s["done"] for s in view["steps"]] == [s.n <= step.n for s in S.STEPS], step.key
        assert view["current"] == (step.n + 1 if step.n < len(S.STEPS) else None)
        assert view["autoplay"]["id"] == job["id"] and view["autoplay"]["status"] == "done"
        done = view["steps"][step.n - 1]
        assert done["automated"]["by"] == "Casey Brooks"
        assert done["automated"]["as"] == store.staff[S.CAST[step.cast]].name

    steps = {s["key"]: s for s in story_view(client, presenter)["steps"]}
    # the cards' decisions say whose automation it was and on whose behalf
    for key in ("call", "triage", "imaging", "inr", "news2", "coding"):
        assert marked(steps[key]["signed"]["note"], S.CAST[S.BY_KEY[key].cast], store), (key, steps[key]["signed"])
    # every signature it added says so on the record
    dsum = store.fhir.read("DocumentReference", f"dsum-{S.STAY}")
    assert dsum["docStatus"] == "final" and marked(signatures(dsum)[-1]["note"], S.CAST["surgeon"], store)
    assert marked(steps["summary"]["signed"]["note"], S.CAST["surgeon"], store)
    medrec = store.fhir.read("DocumentReference", steps["medrec"]["written"][0].split("/")[1])
    assert [s["role"] for s in signatures(medrec)] == ["pharmacist", "physician"]
    assert marked(signatures(medrec)[0]["note"], S.CAST["pharmacist"], store)
    assert marked(signatures(medrec)[1]["note"], S.CAST["surgeon"], store)
    instructions = store.fhir.read("DocumentReference", f"pinstr-{S.STAY}")
    assert marked(signatures(instructions)[-1]["note"], S.CAST["ward_nurse"], store)
    # the audit trail: the presenter's demo action for each step, and the cast's signatures and approvals marked
    presses = [e for e in store.audit.query(user_id=PRESENTER, action="autoplay")]
    assert [e.resource_id for e in presses] == [s.key for s in S.STEPS]
    assert all(e.event_type == "simulator_event" and "done by the demo's automation" in e.reason for e in presses)
    # (the story's own off-screen script, e.g. the discharge reconciliation, is not the automation: only the steps'
    # records are checked)
    cast = set(S.CAST.values()) - {PRESENTER}
    ids = {ref.split("/", 1)[-1] for s in steps.values()  # the bed's approval: its Tasks, as bare ids
           for ref in [(s["proposal"] or {}).get("ref"), (s["signed"] or {}).get("ref"), *s["written"],
                       *((s["signed"] or {}).get("tasks") or [])] if ref}
    signed = [e for t in ("sign", "approve") for e in store.audit.query(event_type=t)
              if e.user_id in cast and e.resource_id in ids]
    assert signed and all("demo automation on behalf of" in e.reason.lower() for e in signed), \
        [(e.user_id, e.action, e.resource_id, e.reason) for e in signed if "demo automation" not in e.reason.lower()]
    assert {e.resource_id for e in signed if e.event_type == "sign"} >= {f"dsum-{S.STAY}", f"pinstr-{S.STAY}"}


def test_jump_leaves_step_n_current_with_the_earlier_steps_done(story, client, login):
    """Jumps from wherever the story stands; on the way, steps the presenter began by hand (the bed manager's
    approval, the summary's removal of the untraceable medicine, the first answer of the follow-up call) are finished
    from where they stand. (With the test above, every N from 2 to 12 is reached.)"""
    store = story
    presenter = login(S.CAST["triage_nurse"])  # the guide's browser is signed in as whoever played the last step
    hand = by_hand(client)

    def jump(n: int) -> dict:
        before = story_view(client, presenter)["current"]
        job = play(client, presenter, f"/api/storyline/steps/{S.STEPS[n - 1].key}/jump")
        assert job["mode"] == "jump" and job["target"] == n and job["steps"] == list(range(before, n))
        view = story_view(client, presenter)
        assert view["current"] == n
        assert [s["done"] for s in view["steps"]] == [s.n < n for s in S.STEPS], n
        assert all(s["automated"] for s in view["steps"] if before <= s["n"] < n)
        return view

    jump(5)
    # step 5 by hand: the bed manager approves the early rounds, then the presenter loses the thread
    ops = S.CAST["bed_manager"]
    hand.advance("decision")
    exc = hand.wait("the Orthopedics exception", lambda: next(
        (e for e in hand.get("/api/control-tower/exceptions", ops)["exceptions"]
         if e["key"] == "unit_occupancy:ORTH" and e["status"] == "open" and e["severity"] == "high"), None))
    hand.open_ai(f"/api/control-tower/exceptions/{exc['id']}", "narrate", ops)
    hand.wait("the narrative", lambda: hand.get(f"/api/control-tower/exceptions/{exc['id']}", ops).get("narration"))
    hand.post(f"/api/control-tower/exceptions/{exc['id']}/approve", ops,
              {"action_ids": ["overflow_discharge_rounds"], "note": "Round early on Surgery"})
    view = jump(9)
    bed = view["steps"][4]
    assert bed["signed"]["by"] == store.staff[ops].name and bed["automated"]  # her approval, the automation's wait
    assert "approved before" in next(r for r in autoplay.current()["results"] if r["key"] == "bed")["result"]

    # step 9 by hand: the surgeon removes the medicine the validator blocked, but does not sign
    surgeon = S.CAST["surgeon"]
    hand.advance("discharge")
    draft = hand.wait("the draft", lambda: hand._draft_summary(surgeon))
    red = {(i.get("value") or "").lower() for i in draft["validation"]["issues"] if i.get("severity") == "red"}
    assert "lisinopril" in red
    content = draft["content"]
    content["medications"]["new"] = [m for m in content["medications"]["new"] if (m.get("name") or "").lower() not in red]
    assert hand.put(f"/api/documentation/documents/{draft['id']}", surgeon, {"content": content})["can_sign"]
    jump(11)
    sigs = signatures(store.fhir.read("DocumentReference", f"dsum-{S.STAY}"))
    assert len(sigs) == 1 and marked(sigs[0]["note"], surgeon, store, store.staff[S.CAST["triage_nurse"]].name)

    # step 11 by hand: the nurse places the call and she gives her birth date, then the presenter stops
    nurse, call_id = S.CAST["ward_nurse"], f"FU-{S.STAY}"
    hand.advance("followup")
    hand.wait("the follow-up call", lambda: client.get(f"/api/voice/followups/{call_id}",
                                                      headers=hand.headers(nurse)).status_code == 200)
    hand.post(f"/api/voice/followups/{call_id}/start", nurse)
    hand.post(f"/api/voice/followups/{call_id}/answer", nurse,
              {"text": hand.status()["patient"]["followup_lines"][0]})
    jump(12)
    call = hand.get(f"/api/voice/followups/{call_id}", nurse)
    caller = [t["text"] for t in call["transcript"] if t["role"] == "caller"]
    assert caller.count(hand.status()["patient"]["followup_lines"][0]) == 1  # her birth date only once
    assert call["escalated"] and hand.status()["patient"]["followup_lines"][1] in caller  # the wound keeps leaking

    job = play(client, presenter, f"/api/storyline/steps/{S.STEPS[-1].key}/autoplay")
    assert job["done"] == ["coding"] and story_view(client, presenter)["current"] is None


def test_a_call_the_presenter_made_is_confirmed_without_calling_again(story, client, login):
    """Step 1 failed live after the call verified her: the automation confirms that call's registration."""
    store, clerk = story, S.CAST["clerk"]
    hand = by_hand(client)
    lines = hand.status(clerk)["patient"]["call_lines"]
    call = hand.post("/api/voice/calls", clerk)
    for line in lines[:2]:
        hand.post(f"/api/voice/calls/{call['id']}/turn", clerk, {"text": line})
    request = hand.wait("the registration request", lambda: hand._registration(clerk))
    job = play(client, login(PRESENTER), "/api/storyline/steps/call/autoplay")
    assert job["results"][0]["result"].startswith("the presenter's call")
    assert store.fhir.read("Encounter", S.VISIT)["status"] == "arrived"
    task = store.fhir.read("Task", request["task_id"])
    assert task["status"] == "completed"


def test_the_automation_is_refused_unless_it_continues_the_story(story, client, login):
    store, presenter = story, login(PRESENTER)
    r = client.post("/api/storyline/steps/triage/autoplay", headers=presenter)
    assert r.status_code == 409 and "current step is 1" in r.json()["detail"]
    r = client.post("/api/storyline/steps/call/jump", headers=presenter)
    assert r.status_code == 409 and "already the current step" in r.json()["detail"]
    assert client.post("/api/storyline/steps/nope/autoplay", headers=presenter).status_code == 404

    # a jump to step 5 from the start: steps 1-4, audited as the presenter's demo action with the people it acts as
    r = client.post("/api/storyline/steps/bed/jump", headers=presenter)
    assert r.status_code == 200
    job = r.json()
    assert job["keys"] == ["call", "triage", "imaging", "inr"] and job["target"] == 5
    assert job["started_by"]["name"] == "Casey Brooks" and "people" not in job
    event = store.audit.query(user_id=PRESENTER, action="autoplay")[-1]
    assert event.resource_id == "call..inr" and event.event_type == "simulator_event"
    for user_id in (S.CAST["clerk"], S.CAST["triage_nurse"], S.CAST["ed_physician"]):
        assert store.staff[user_id].name in event.reason
    view = story_view(client, presenter)
    assert view["autoplay"]["status"] == "running" and view["autoplay"]["current_n"] == 1
    assert view["autoplay"]["current_cast"] == store.staff[S.CAST["clerk"]].name
    # one job at a time
    r = client.post("/api/storyline/steps/call/autoplay", headers=presenter)
    assert r.status_code == 409 and "still doing step 1" in r.json()["detail"]
    # a reset stops it after its step and forgets its marks
    autoplay.on_reset()
    ended = autoplay.run(job["id"], client=client, pump=H.pump, advance=H.advance, timeout_s=60)
    assert ended["status"] == "failed" and "reset" in ended["error"] and ended["done"] == []


def test_the_storyline_must_be_set_up_first(fresh_state, client, login, monkeypatch):
    monkeypatch.setattr(autoplay, "_job", None)
    r = client.post("/api/storyline/steps/call/autoplay", headers=login(PRESENTER))
    assert r.status_code == 409 and "Reset the storyline first" in r.json()["detail"]
