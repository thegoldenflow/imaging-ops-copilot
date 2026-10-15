"""In-process harness for the storyline walk (app/modules/storyline/walk.py): what the API's background loops do,
run on demand inside the test's transaction (tests/test_storyline.py, scripts/storyline_report.py --in-process)."""

from __future__ import annotations

from datetime import timedelta

from app.core.store import get_store
from app.ehr import simulator
from app.ehr.events import bus
from app.fhir.dt import parse


def pump() -> None:
    """One pass of the background work: event delivery, the boards and the exception stream, the intakes."""
    from app.modules.control_tower import service as control_tower
    from app.modules.documentation import service as documentation
    from app.modules.med_safety import service as med_safety
    from app.agents import ondemand
    from app.modules.requisitions import service as requisitions
    from app.modules.storyline import service as storyline
    from app.modules.voice_services import followup

    store = get_store()
    for _ in range(3):
        bus.drain()
    control_tower.refresh_step(store)
    med_safety.intake_step(store)
    followup.sweep_step(store)
    control_tower.narrate_step(store)
    med_safety.ai_step(store)
    documentation.intake_step(store)
    requisitions.process_pending(store)
    storyline.ai_step(store)  # the AI a pressed step's button asked for (BACKGROUND_AI=on_demand)
    ondemand.drain(store)  # the API's AI job loop
    bus.drain()


def drain() -> None:
    for _ in range(3):
        if not bus.drain().delivered:
            return


def advance(walk, beat: str) -> None:
    """The clock to the beat: 30-minute steps when it is close, 3-hour steps across nights and days, delivering
    the events after each (the storyline's subscribers react in order); the full background pass at the end."""
    from app.modules.storyline import service as storyline

    store = get_store()
    target = storyline.beats(store).get(beat)
    if target is None:
        pump()
        target = storyline.beats(store).get(beat)
    if target is None:
        raise AssertionError(f"no time yet for {beat}")
    target_t = parse(target)
    for _ in range(400):
        now = parse(simulator.clock(store)["now"])
        if now >= target_t:
            pump()
            return
        if target_t > parse(store.modules["hospital_plan"]["horizon"]):
            raise AssertionError(f"{beat} at {target_t} is past the plan's horizon")
        step = timedelta(hours=3) if target_t - now > timedelta(hours=6) else timedelta(minutes=30)
        simulator.advance(store, min(now + step, target_t))
        drain()
        refreshed = storyline.beats(store).get(beat)  # her admission moves when a bed is offered
        if refreshed and parse(refreshed) < target_t:
            target_t = parse(refreshed)
    raise AssertionError(f"the clock did not reach {beat}")
