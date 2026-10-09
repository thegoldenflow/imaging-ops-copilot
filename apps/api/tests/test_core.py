"""Phase 0 acceptance: auth, audit, de-identification and the LLM call layer."""

from datetime import date

import pytest

from pydantic import BaseModel

from app.agents import registry
from app.core.models import Patient
from app.core.store import Store, get_store
from app.llm.deid import Pseudonymizer
from app.llm.gateway import LlmGateway
from app.llm.prompts import Prompt
from app.llm.providers import ProviderResult, ProviderUnavailable


def test_unauthenticated_request_is_rejected(client):
    assert client.get("/api/scheduling/dashboard").status_code == 401


def test_forbidden_role_gets_403_and_audit_record(client, login):
    headers = login("U-FD")
    assert client.get("/api/reports/worklist", headers=headers).status_code == 403
    denied = [e for e in get_store().audit.events() if e.outcome == "denied"]
    assert denied and denied[-1].user_id == "U-FD" and denied[-1].resource_type == "/api/reports/worklist"


def test_site_scoped_user_cannot_cancel_other_site(client, login):
    store = get_store()
    other = next(a for a in store.appointments.values() if a.site_id != "LKS" and a.status in ("booked", "confirmed"))
    r = client.post(f"/api/scheduling/appointments/{other.id}/cancel", headers=login("U-FD"), json={})
    assert r.status_code == 403
    assert store.audit.events()[-1].resource_id == other.id


def test_audit_chain_detects_tampering():
    log = Store().audit  # detached store: the chain logic without the database
    for i in range(3):
        log.record(user_id="u", user_name="U", role="admin", action="read", resource_type="patient",
                   resource_id=str(i), reason="test")
    assert log.verify() == (True, None)
    log._pending[1].resource_id = "changed"
    assert log.verify() == (False, 2)


def _patient() -> Patient:
    return Patient(id="p1", given_name="Robert", family_name="Taylor", dob=date(1968, 4, 12), sex="M",
                   phone="+1-416-555-0177", email="robert.taylor@example.com", address="15 Sample Ave, Demo City, ON",
                   health_card="7310594826", health_card_version="RT", preferred_language="en")


def test_deidentification_replaces_identifiers_and_restores_them():
    pseudo = Pseudonymizer([_patient()])
    text = ("This is robert taylor, born 1968-04-12, card 7310594826, phone +1-416-555-0177, "
            "email robert.taylor@example.com, living at 15 Sample Ave, Demo City, ON")
    redacted = pseudo.redact(text)
    for secret in ("Taylor", "taylor", "1968-04-12", "7310594826", "555-0177", "example.com", "Sample Ave"):
        assert secret not in redacted
    assert "[PERSON_1]" in redacted
    assert pseudo.restore({"name": "[PERSON_1]"}) == {"name": "Robert Taylor"}


class Echo(BaseModel):
    answer: str


PROMPT = Prompt(name="t", version="t@1", system="s", template="{q}")


@pytest.fixture(autouse=True)
def _registered_test_agent():
    """The LLM gateway refuses tasks without an agent-registry entry (6.4); "t" is this file's test agent."""
    with registry.temporary("t", {"kind": "embedded_agent"}):
        yield


class ScriptedProvider:
    """Returns the queued outputs in order; an exception instance is raised."""

    mode = "mock"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.seen: list[str] = []

    def complete_json(self, *, text, **_):
        self.seen.append(text)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return ProviderResult(data=out, model="m", input_tokens=10, output_tokens=5)


def test_gateway_retries_once_after_schema_failure():
    provider = ScriptedProvider([{"wrong": 1}, {"answer": "ok"}])
    outcome = LlmGateway(provider).structured(task="t", prompt=PROMPT, variables={"q": "hi"}, schema_cls=Echo)
    assert outcome.status == "ok" and outcome.data == {"answer": "ok"}
    assert "failed validation" in provider.seen[1]
    assert get_store().llm_calls[-1].outcome == "retried_ok"


def test_gateway_needs_human_after_two_failures():
    provider = ScriptedProvider([{"wrong": 1}, {"wrong": 2}])
    outcome = LlmGateway(provider).structured(task="t", prompt=PROMPT, variables={"q": "hi"}, schema_cls=Echo)
    assert outcome.status == "needs_human" and outcome.data is None


def test_gateway_degrades_on_timeout():
    provider = ScriptedProvider([ProviderUnavailable("timeout")])
    outcome = LlmGateway(provider).structured(task="t", prompt=PROMPT, variables={"q": "hi"}, schema_cls=Echo)
    assert outcome.status == "unavailable"
    assert get_store().llm_calls[-1].outcome == "unavailable"


def test_gateway_sends_redacted_text_and_logs_no_phi():
    provider = ScriptedProvider([{"answer": "ok"}])
    LlmGateway(provider).structured(task="t", prompt=PROMPT, variables={"q": "Robert Taylor 7310594826"},
                                    schema_cls=Echo, patients=[_patient()])
    assert "Taylor" not in provider.seen[0] and "7310594826" not in provider.seen[0]
    logged = get_store().llm_calls[-1].model_dump_json()
    assert "Taylor" not in logged and "7310594826" not in logged


def test_reset_rebuilds_demo_data(client, login):
    headers = login("U-OPS")
    get_store().appointments.clear()
    client.post("/api/demo/reset", headers=headers)
    assert len(get_store().appointments) > 10000
    # Login sessions are not part of the demo data: the same token still works.
    assert client.get("/api/scheduling/dashboard", headers=headers).status_code == 200
