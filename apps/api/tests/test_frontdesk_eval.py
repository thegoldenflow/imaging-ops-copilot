"""System 4 phone eval (SPEC eval table: 20 scripted call scenarios, task completion and mean response latency): the
set's shape, the calls through the phone agent's own path on its mock model, and the judge (a call or a summary that
breaks the rules fails). The runner's suite is `frontdesk` (app/aiops/suites.py)."""

from __future__ import annotations

from app.agents import registry
from app.agents import trace as traces
from app.aiops import suites
from app.llm import deid
from app.llm.providers import MOCK_TOOL_FIXTURES, TextBlock
from app.modules.frontdesk import agent, mock_agent
from app.modules.frontdesk import evals as fd


def _case(case_id: str) -> dict:
    return next(c for c in fd.load_cases() if c["id"] == case_id)


def test_the_set_has_20_scripted_calls_in_three_languages_for_both_agents():
    cases = fd.load_cases()
    assert len(cases) == 20 and len({c["id"] for c in cases}) == 20
    assert {c["language"] for c in cases} == {"en", "zh", "fr"}
    assert {c["expected"]["task"] for c in cases} >= {"reschedule", "keep", "cancel", "confirm", "confirm_blocked",
                                                      "prep", "site", "transfer", "none"}
    assert {c["expected"].get("reason") for c in cases} >= {"medical", "identity", "person"}
    assert all(c["source"] == "authored" and c["input_refs"] for c in cases)
    for agent_id in ("voice_agent", "call_summary"):
        suite = suites.suite_for(registry.require_agent(agent_id))
        assert suite is not None and suite.id == "frontdesk"
        assert len([c for c in suite.cases() if c["agent_id"] == agent_id]) == 20


def test_the_calls_meet_the_bar_with_the_mock_model(fresh_state):
    report = fd.run(fresh_state, fallback=False)
    assert report["mode"] == "mock"
    assert [(r["id"], r["got"]["failures"]) for r in report["voice"] if not r["passed"]] == []
    assert [(r["id"], r["got"]) for r in report["summaries"] if not r["passed"]] == []
    assert all(g["passed"] for gates in report["gates"].values() for g in gates.values()), report["gates"]


def test_each_caller_turn_is_an_agent_run_through_the_tool_gateway(fresh_state):
    case = _case("fd-04")
    fx = fd.materialize(fresh_state, case)
    call = fd.run_call(fresh_state, case, fx, mode="mock")
    runs = [t for t in traces.table(fresh_state).values() if t.context_id == f"CallSession/{call['session'].id}"]
    assert len(runs) == len(call["replies"])
    done = [(c.tool_id, c.status) for t in runs for c in t.tool_calls]
    assert ("verifyCallerIdentity", "ok") in done and ("cancelAppointment", "ok") in done
    # The name and the English birth date reached the agent's model and the summary's as placeholders.
    assert call["raw_identifiers"] == [] and call["summary_raw_identifiers"] == []
    assert fresh_state.appointments[fx.appointment_id].status == "cancelled"


def test_a_birth_date_said_in_chinese_or_french_that_reaches_a_model_fails_both_gates(fresh_state, monkeypatch):
    monkeypatch.setattr(deid, "SPOKEN_DATE_RES", ())  # the shared patterns only: ISO and English dates
    report = fd.run(fresh_state, [_case("fd-05"), _case("fd-06")], fallback=False)
    for agent_id in ("voice_agent", "call_summary"):
        gate = report["gates"][agent_id]["calls_with_raw_identifiers_to_model"]
        assert gate["value"] == 2 and not gate["passed"], agent_id
    assert "(fd-05, fd-06)" in report["notes"]


def test_the_scripted_fallback_handles_the_english_calls(fresh_state):
    report = fd.run(fresh_state, [c for c in fd.load_cases() if c["language"] == "en"])
    assert [(r["id"], r["got"]["failures"]) for r in report["fallback"] if not r["passed"]] == []


def test_a_model_that_answers_a_medical_question_fails_the_call_and_the_transfer_gate(fresh_state, monkeypatch):
    real = MOCK_TOOL_FIXTURES["voice_agent"]

    def careless(messages, tools):
        if isinstance(messages[-1]["content"], str) and "dangerous" in messages[-1]["content"]:
            return [TextBlock("Contrast dye is generally safe for the kidneys.")], "end_turn"
        return real(messages, tools)

    monkeypatch.setitem(MOCK_TOOL_FIXTURES, "voice_agent", careless)
    report = fd.run(fresh_state, [_case("fd-15")], fallback=False)
    assert report["voice"][0]["got"]["failures"] == ["not put through to a person"]
    assert not report["gates"]["voice_agent"]["required_transfers"]["passed"]


def test_a_model_that_books_another_option_fails(fresh_state, monkeypatch):
    monkeypatch.setattr(mock_agent, "_ordinal", lambda text: 0)
    report = fd.run(fresh_state, [_case("fd-01")], fallback=False)
    assert report["voice"][0]["got"]["failures"] == ["not booked at option 2"]


def test_appointment_details_before_verification_fail_the_call(fresh_state):
    case = _case("fd-19")
    fx = fd.materialize(fresh_state, case)
    appt = fresh_state.appointments[fx.appointment_id]
    session = agent.new_session(fresh_state)
    session.outcome, session.actions = "transferred", [{"tool": "transfer_to_human", "reason": "asked"}]
    reply = f"Your {fresh_state.exams[appt.exam_code].name} is on {appt.start:%B} {appt.start.day}; transferring you."
    row = fd.judge_call(fresh_state, case, fx, {"session": session, "replies": [reply], "modes": ["mock"],
                                                "verified_after": [False]}, [])
    assert not row["passed"] and row["disclosures"] == 1


def test_a_summary_is_checked_against_the_calls_facts(fresh_state):
    session = agent.new_session(fresh_state)
    session.outcome, session.actions = "transferred", [{"tool": "transfer_to_human", "reason": "Medical question"}]

    def judge(summary: str, follow_up: bool = True) -> dict:
        return fd.judge_summary(_case("fd-15"), {"session": session, "summary": {"summary": summary,
                                                                                  "follow_up_needed": follow_up}})

    good = judge("The caller asked a medical question and was transferred to staff; nothing was cancelled.")
    assert good["passed"] and good["grounded"]
    claimed = judge("The caller cancelled the CT and was transferred.")
    assert not claimed["passed"] and claimed["got"]["unsupported_claims"] == ["cancel"]
    assert judge("The caller was transferred to staff.", follow_up=False)["passed"] is False
    assert judge("The caller asked about contrast.")["got"]["not_named"] == ["transfer_to_human"]
    failed = fd.judge_summary(_case("fd-15"), {"session": session, "summary": None})
    assert not failed["passed"] and failed["schema_valid"] is False
