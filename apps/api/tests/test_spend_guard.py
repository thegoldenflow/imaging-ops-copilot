"""Keeping a deployment with a real model key cheap: the idle pause (IDLE_PAUSE_MINUTES, app/core/activity.py)
and the daily AI budget (LLM_DAILY_BUDGET_USD, app/llm/budget.py)."""

import asyncio
import dataclasses
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

from app import main
from app.core import activity, config
from app.core.db import schema
from app.core.store import get_engine, get_store
from app.ehr import simulator
from app.fhir.dt import parse
from app.llm import budget
from app.llm.gateway import LlmGateway, set_gateway
from app.llm.prompts import Prompt
from app.llm.providers import MockProvider, ProviderUnavailable

T0 = datetime(2030, 1, 1, 12, 0, 0)


def _settings(monkeypatch, **overrides):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, **overrides))


@pytest.fixture(autouse=True)
def _fresh_guards():
    activity.reset()
    budget.reset_cache()
    yield
    activity.reset()
    budget.reset_cache()


# ---------- idle pause ----------


def test_a_process_starts_idle_and_a_person_wakes_it(monkeypatch):
    _settings(monkeypatch, idle_pause_minutes=15)
    assert activity.idle(T0)  # after a restart nothing runs up model calls until someone signs in
    activity.touch(T0)
    assert not activity.idle(T0 + timedelta(minutes=15))
    assert activity.idle(T0 + timedelta(minutes=15, seconds=1))
    assert activity.status(T0 + timedelta(minutes=20))["paused_since"] == T0 + timedelta(minutes=15)
    activity.touch(T0 + timedelta(minutes=40))
    assert not activity.idle(T0 + timedelta(minutes=40))
    assert activity.awake_since() == T0 + timedelta(minutes=40)
    assert activity.status(T0 + timedelta(minutes=40))["last_pause"] == {
        "from": T0 + timedelta(minutes=15), "to": T0 + timedelta(minutes=40)}


def test_zero_minutes_never_pauses(monkeypatch):
    _settings(monkeypatch, idle_pause_minutes=0)
    assert not activity.idle(T0)
    assert activity.status(T0)["paused"] is False


def test_an_open_tab_that_only_polls_does_not_keep_the_server_awake(monkeypatch):
    _settings(monkeypatch, idle_pause_minutes=15)
    activity.touch(T0)
    later = T0 + timedelta(minutes=30)
    activity.touch_from_header("1800", later)  # the page polled, but nobody touched it for 30 minutes
    assert activity.idle(later)
    activity.touch_from_header("2", later)  # the person moved the mouse: the next poll wakes it
    assert not activity.idle(later)
    activity.touch_from_header("not a number", later + timedelta(minutes=20))  # no usable header: counts as now
    assert not activity.idle(later + timedelta(minutes=20))


def test_signed_in_requests_wake_the_background_work_and_the_board_shows_the_hold(client, login, monkeypatch):
    _settings(monkeypatch, idle_pause_minutes=15)
    headers = login("U-OPS")
    assert client.get("/api/health").json()["background"]["idle_paused"]  # unauthenticated: no wake-up
    board = client.get("/api/control-tower/board", headers={**headers, "X-Idle-Seconds": "3600"}).json()
    assert board["clock"]["idle"]["paused"] is True  # a tab left open on the board
    board = client.get("/api/control-tower/board", headers={**headers, "X-Idle-Seconds": "0"}).json()
    assert board["clock"]["idle"]["paused"] is False
    assert client.get("/api/health").json()["background"]["idle_paused"] is False


def _one_pass(monkeypatch, loop, sleeps_before_stop: int) -> list[str]:
    """Run one iteration of a background loop of app/main.py and return the steps it started."""
    ran: list[str] = []
    monkeypatch.setattr(main, "_step", lambda name, fn, *args: ran.append(name))
    monkeypatch.setattr(main, "_drain_events", lambda: ran.append("event delivery"))
    sleeps = {"n": 0}
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        sleeps["n"] += 1
        if sleeps["n"] > sleeps_before_stop:
            raise asyncio.CancelledError
        await real_sleep(0)

    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(loop())
    monkeypatch.setattr(main.asyncio, "sleep", real_sleep)
    return ran


def test_while_idle_only_the_steps_that_call_the_model_wait(monkeypatch):
    _settings(monkeypatch, idle_pause_minutes=15)
    assert activity.idle()
    intake = _one_pass(monkeypatch, main._intake_loop, 1)
    assert "requisition intake" not in intake and "feedback classification" not in intake
    assert {"prior retrieval", "critical escalation", "inspection reminders", "daily demo reset"} <= set(intake)
    hospital = _one_pass(monkeypatch, main._hospital_loop, 0)
    assert "day simulator" not in hospital
    assert {"event delivery", "control tower", "medication safety intake"} <= set(hospital)
    assert _one_pass(monkeypatch, main._narrator_loop, 1) == []
    assert _one_pass(monkeypatch, main._dispatch_loop, 1) == ["dispatch"]

    activity.touch()
    assert {"requisition intake", "feedback classification"} <= set(_one_pass(monkeypatch, main._intake_loop, 1))
    assert "day simulator" in _one_pass(monkeypatch, main._hospital_loop, 0)
    assert _one_pass(monkeypatch, main._narrator_loop, 1) == [
        "control tower narration", "medication safety AI", "documentation intake"]


def test_the_clock_resumes_where_it_stopped(fresh_state):
    start = parse(fresh_state.modules["hospital_clock"]["now"])
    simulator.run(fresh_state, 60, wall_now=T0)
    simulator.tick(fresh_state, T0 + timedelta(seconds=10))  # 10 s at 1 h/min: 10 hospital minutes
    # the API skipped the ticks while idle; the person came back at +50 min and the next tick runs at +50 min 5 s
    simulator.tick(fresh_state, T0 + timedelta(minutes=50, seconds=5), awake_since=T0 + timedelta(minutes=50))
    assert parse(fresh_state.modules["hospital_clock"]["now"]) == start + timedelta(minutes=15)


# ---------- daily AI budget ----------


PROMPT = Prompt(name="t", version="t@1", system="s", template="{q}")


class PricedMock(MockProvider):
    """The mock's fixtures, priced as a real provider (the gateway charges by the Claude list prices)."""

    mode = "anthropic"


@pytest.fixture
def ledger_day(monkeypatch):
    """A day of its own in the spend ledger (the ledger commits outside the test's transaction), removed after."""
    day = date(1999, 1, 1)
    monkeypatch.setattr(budget, "_today", lambda: day)
    key = budget._key(day)
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM app_meta WHERE key = :k"), {"k": key})
    yield day
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM app_meta WHERE key = :k"), {"k": key})


def test_priced_calls_add_up_in_the_ledger_and_reach_the_cap(monkeypatch, ledger_day):
    from app.modules.feedback import service as feedback

    _settings(monkeypatch, llm_daily_budget_usd=0.002)
    set_gateway(LlmGateway(PricedMock(latency_s=0)))
    store = get_store()
    survey = next(s for s in feedback.surveys(store).values() if s.status == "sent")
    first = feedback.submit(store, survey, 5, "Quick and friendly staff", datetime.now())
    before = len(store.llm_calls)
    feedback.process_pending(store)  # this response and any other waiting for classification
    assert first.ai_status == "ok"
    spent = budget.spent_today()
    assert spent > 0 and sum(c.cost_usd for c in store.llm_calls[before:]) == pytest.approx(spent)
    budget.reset_cache()
    assert budget.spent_today() == pytest.approx(spent)  # read back from the ledger, not the process

    budget.record(0.01)  # another process's calls
    assert budget.refusal().startswith(budget.REFUSAL_PREFIX)
    second = next(s for s in feedback.surveys(store).values() if s.status == "sent" and s.id != survey.id)
    resp = feedback.submit(store, second, 1, "Waited two hours", datetime.now())
    feedback.process_pending(store)
    assert resp.ai_status == "unavailable" and resp.sentiment is None  # degraded like a provider outage
    assert any(a.response_id == resp.id for a in feedback.alerts(store).values())  # the rule still fired
    call = store.llm_calls[-1]
    assert call.outcome == "refused" and call.error.startswith(budget.REFUSAL_PREFIX) and call.cost_usd == 0


def test_over_budget_the_gateway_refuses_both_kinds_of_call(monkeypatch, ledger_day):
    from pydantic import BaseModel

    from app.agents import registry

    class Answer(BaseModel):
        answer: str

    _settings(monkeypatch, llm_daily_budget_usd=0.5)
    budget.record(0.75)
    gateway = LlmGateway(PricedMock(latency_s=0))
    with registry.temporary("t", {"kind": "embedded_agent"}):
        outcome = gateway.structured(task="t", prompt=PROMPT, variables={"q": "hi"}, schema_cls=Answer)
        assert outcome.status == "unavailable" and outcome.error == budget.USER_MESSAGE
        with pytest.raises(ProviderUnavailable, match="budget"):
            gateway.tool_turn(task="t", prompt=PROMPT, messages=[], tools=[], redacted_text="")
    assert [c.outcome for c in get_store().llm_calls[-2:]] == ["refused", "refused"]


def test_without_a_cap_nothing_is_refused(monkeypatch, ledger_day):
    _settings(monkeypatch, llm_daily_budget_usd=None)
    budget.record(1000.0)
    assert budget.refusal() is None
    assert budget.status()["reached"] is False and budget.status()["spent_usd"] == 1000.0


def test_a_new_day_starts_from_zero(monkeypatch, ledger_day):
    _settings(monkeypatch, llm_daily_budget_usd=1.0)
    budget.record(2.0)
    assert budget.refusal() is not None
    monkeypatch.setattr(budget, "_today", lambda: ledger_day + timedelta(days=1))
    try:
        assert budget.spent_today() == 0.0 and budget.refusal() is None
    finally:
        with get_engine().begin() as conn:
            conn.execute(text("DELETE FROM app_meta WHERE key = :k"), {"k": budget._key(ledger_day + timedelta(days=1))})


def test_a_demo_reset_does_not_hand_out_a_fresh_budget():
    assert "llm_calls" in schema.data_tables()  # a reset truncates the call log ...
    assert "app_meta" not in schema.data_tables()  # ... but not the ledger the budget reads


def test_ai_ops_shows_the_budget_and_non_ai_pages_work_when_it_is_reached(client, login, monkeypatch, ledger_day):
    _settings(monkeypatch, llm_daily_budget_usd=0.25)
    budget.record(0.30)
    budget.refusal()
    headers = login("U-OPS")
    shown = client.get("/api/aiops/overview", headers=headers).json()["budget"]
    assert shown["reached"] is True and shown["limit_usd"] == 0.25 and shown["spent_usd"] == pytest.approx(0.30)
    assert client.get("/api/admin/ai-usage", headers=headers).json()["budget"]["reached"] is True
    assert client.get("/api/health").json()["ai_budget"] == {"capped": True, "reached": True}
    assert client.get("/api/scheduling/dashboard", headers=headers).status_code == 200
    assert client.get("/api/control-tower/board", headers=headers).status_code == 200
