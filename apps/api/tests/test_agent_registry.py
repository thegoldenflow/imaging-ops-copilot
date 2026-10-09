"""WP4b (spec 6.4): the agent and tool registries, the release gate (demo / prod) and the LLM gateway's use of them."""

from pathlib import Path

import pytest
from pydantic import BaseModel

from app.agents import registry
from app.agents import trace as traces
from app.agents.registry import AgentNotDeployable, UnregisteredAgent
from app.agents.runtime import runtime
from app.core.store import get_store
from app.llm.gateway import LlmGateway, get_gateway
from app.llm.prompts import Prompt
from app.llm.providers import MockProvider, ProviderResult

API = Path(__file__).resolve().parents[1]

# Every model task in the code base: the imaging systems, the clinical knowledge Q&A and the platform.
EXISTING_TASKS = {"requisition_extract", "requisition_triage", "protocol_suggest", "cxr_draft", "voice_agent",
                  "call_summary", "prep_translation", "mri_implant_extract", "feedback_classify", "policy_qa",
                  "referral_weekly_summary", "clinical_kg_parse", "clinical_kg_answer", "deid_check"}
# WP4's modules.registry.json (6.3), as it was: tier, sign-off roles, cosign, consents, writes_allowed.
WP4_ENTRIES = {
    "control_tower": ("ops", ["operations_manager"], False, [], {"Task": {"*"}, "Encounter.location": {"append"}}),
    "registration": ("ops", ["clerk"], False, [], {"Appointment": {"proposed"}, "Task": {"*"}}),
    "consent_management": ("ops", [], False, [], {"Consent": {"active"}}),
    "bedside_nursing": ("ops", ["nurse"], False, [], {"Task": {"*"}, "Flag": {"*"}, "Communication": {"*"}}),
    "discharge_summary": ("documentation", ["physician"], False, ["ai_processing"],
                          {"DocumentReference": {"preliminary", "final"}, "Task": {"*"}}),
    "patient_instructions": ("documentation", ["nurse"], False, ["ai_processing"],
                             {"DocumentReference": {"preliminary", "final"}, "Task": {"*"}}),
    "nursing_handoff": ("documentation", ["nurse"], False, ["ai_processing"],
                        {"DocumentReference": {"preliminary", "final"}}),
    "medication_reconciliation": ("clinical_ds", ["pharmacist", "physician"], True, ["ai_processing"],
                                  {"DocumentReference": {"preliminary", "final"}, "Task": {"*"}}),
    "order_review": ("clinical_ds", ["pharmacist", "physician"], False, ["ai_processing"],
                     {"DocumentReference": {"preliminary", "final"}, "Task": {"*"}, "Flag": {"*"}}),
    "followup_calls": ("ops", ["nurse"], False, ["followup_call"], {"Communication": {"*"}, "Task": {"*"}}),
    "patient_messaging": ("ops", ["clerk"], False, ["sms"], {"Communication": {"*"}, "Task": {"*"}}),
}
# Added in WP4b on purpose: booking (privileged, WP2 deferred it here) and the runtime's own entry.
WP4B_ADDITIONS = {"registration": {"Appointment": {"booked"}}}


class Out(BaseModel):
    answer: str


def _covered(writes: dict[str, set[str]]) -> dict[str, set[str]]:
    return {t: ({"*"} if "*" in s else s) for t, s in writes.items()}


def test_every_existing_agent_is_registered_with_its_prompt_version():
    from app.llm.freetext_deid import DEID_CHECK
    from app.llm.prompts import CALL_SUMMARY, CXR_DRAFT, VOICE_AGENT
    from app.modules.clinical_kg.service import ANSWER_PROMPT, PARSE_PROMPT
    from app.modules.feedback.service import CLASSIFY_PROMPT
    from app.modules.inspection.service import QA_PROMPT
    from app.modules.mri_safety.service import IMPLANT_PROMPT
    from app.modules.prep.service import TRANSLATE_PROMPT
    from app.modules.protocols.service import PROTOCOL_PROMPT
    from app.modules.referrals.service import SUMMARY_PROMPT
    from app.modules.requisitions.extraction import EXTRACT_PROMPT
    from app.modules.triage.service import TRIAGE_PROMPT

    agents = registry.agents()
    assert EXISTING_TASKS <= set(agents)
    for prompt in (DEID_CHECK, CALL_SUMMARY, CXR_DRAFT, VOICE_AGENT, ANSWER_PROMPT, PARSE_PROMPT, CLASSIFY_PROMPT,
                   QA_PROMPT, IMPLANT_PROMPT, TRANSLATE_PROMPT, PROTOCOL_PROMPT, SUMMARY_PROMPT, EXTRACT_PROMPT,
                   TRIAGE_PROMPT):
        spec = agents[prompt.name]
        assert spec.kind == "embedded_agent" and spec.prompt_version == prompt.version, prompt.name
    # the 7.1-7.4 agents are registered with their WP4 rights; their code comes in WP5-WP8
    for name in ("control_tower", "discharge_summary", "patient_instructions", "nursing_handoff",
                 "medication_reconciliation", "order_review", "followup_calls"):
        assert agents[name].kind == "runtime_agent" and agents[name].deployment_status == "dev", name
    # the one runtime agent with code
    assert agents["patient_message_triage"].entrypoint == "app.agents.library.patient_message_triage"


def test_wp4_registry_entries_moved_over_unchanged():
    agents = registry.agents()
    for name, (tier, signers, cosign, consents, writes) in WP4_ENTRIES.items():
        spec = agents[name]
        assert (spec.risk_tier, spec.required_signoff_role, spec.cosign, spec.consent_required) == (
            tier, signers, cosign, consents), name
        expected = {t: set(s) for t, s in writes.items()}
        for rtype, extra in WP4B_ADDITIONS.get(name, {}).items():
            expected[rtype] = expected.get(rtype, set()) | extra
        assert _covered(spec.writes()) == expected, name


def test_there_is_no_second_registry():
    assert not (API / "config" / "modules.registry.json").exists()
    assert not (API / "app" / "core" / "registry.py").exists()
    offenders = [p.relative_to(API).as_posix() for p in (API / "app").rglob("*.py")
                 if "modules.registry" in p.read_text(encoding="utf-8") and p.name != "registry.py"]
    assert offenders == []


def test_registry_files_are_consistent():
    tools = registry.tools()
    for spec in registry.agents().values():
        assert set(spec.allowed_tools) <= set(tools), spec.agent_id
        assert (registry.AGENTS_DIR / f"{spec.agent_id}.yaml").exists()
    for tool in tools.values():
        module, _, name = tool.handler.partition(":")
        __import__(module)
        assert hasattr(__import__(module, fromlist=[name]), name), tool.tool_id
        if tool.side_effect_level in ("action", "privileged"):
            assert tool.idempotency.required, tool.tool_id
    with pytest.raises(ValueError, match="drafts or proposals only"):
        registry.ToolSpec.model_validate({
            "tool_id": "finalDraft", "description": "x", "side_effect_level": "recommend", "handler": "x:y",
            "domain": "fhir", "fhir_writes": {"DocumentReference": ["final"]},
            "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
            "permission_policy": {"roles": ["nurse"]}, "timeout_ms": 1000, "audit_level": "detailed"})
    with pytest.raises(ValueError, match="unsupported keyword"):
        registry.ToolSpec.model_validate({
            "tool_id": "loose", "description": "x", "side_effect_level": "read", "handler": "x:y", "domain": "fhir",
            "input_schema": {"type": "object", "oneOf": []}, "output_schema": {"type": "object"},
            "permission_policy": {"roles": ["nurse"]}, "timeout_ms": 1000, "audit_level": "standard"})
    with pytest.raises(registry.RegistryError, match="not registered"):
        registry._cross_check({"x": registry.AgentSpec.model_validate(
            registry._agent_defaults("x", {"allowed_tools": ["noSuchTool"]}))}, tools)


def test_loading_an_unregistered_agent_fails():
    with pytest.raises(UnregisteredAgent):
        runtime.start("ghost_agent")
    with pytest.raises(UnregisteredAgent):
        get_gateway().structured(task="ghost_agent", prompt=Prompt("ghost_agent", "ghost_agent@1", "s", "{q}"),
                                 variables={"q": "hi"}, schema_cls=Out)


def test_demo_mode_flags_not_evaluated_output_and_prod_refuses_it(fresh_state):
    prompt = Prompt("gate_agent", "gate_agent@1", "s", "{q}")
    from app.llm.providers import mock_fixture

    mock_fixture("gate_agent")(lambda text, images, attempt: {"answer": "ok"})
    with registry.temporary("gate_agent", {"kind": "embedded_agent", "prompt_version": "gate_agent@1"}):
        demo = get_gateway().structured(task="gate_agent", prompt=prompt, variables={"q": "x"}, schema_cls=Out)
        assert demo.status == "ok" and demo.evaluated is False and demo.eval_status == "pending"
        with registry.mode("prod"):
            prod = get_gateway().structured(task="gate_agent", prompt=prompt, variables={"q": "x"}, schema_cls=Out)
            with pytest.raises(AgentNotDeployable):
                runtime.start("gate_agent")
        assert prod.status == "unavailable" and "has not passed its evaluation" in prod.error
        assert get_store().llm_calls[-1].outcome == "refused"
        assert traces.get(prod.run_id).outcome == "refused"
    passed = {"kind": "embedded_agent", "prompt_version": "gate_agent@1", "deployment_status": "prod_ready",
              "eval_status": {"status": "passed", "run_id": "run-1"}}
    with registry.temporary("gate_agent", passed), registry.mode("prod"):
        ok = get_gateway().structured(task="gate_agent", prompt=prompt, variables={"q": "x"}, schema_cls=Out)
        assert ok.status == "ok" and ok.evaluated is True
    with registry.temporary("gate_agent", {**passed, "deployment_status": "demo"}), registry.mode("prod"):
        assert "not prod_ready" in get_gateway().structured(task="gate_agent", prompt=prompt, variables={"q": "x"},
                                                            schema_cls=Out).error


def test_imaging_ai_degrades_in_prod_mode(fresh_state):
    """Not one imaging agent has passed an evaluation, so prod mode turns their AI off; the modules keep working."""
    from app.modules.feedback import service as feedback

    store = get_store()
    pending = [r.id for r in feedback.responses(store).values() if r.ai_status is None]
    assert pending  # the seed leaves two responses for the AI to classify
    with registry.mode("prod"):
        assert registry.gate(registry.agent("requisition_triage")) is not None
        assert feedback.process_pending(store) == len(pending)  # refused -> the module's own fallback
    assert {feedback.responses(store)[r].ai_status for r in pending} == {"unavailable"}
    assert store.llm_calls[-1].outcome == "refused"


def test_model_policy_and_prompt_pointer_come_from_the_registry(fresh_state):
    seen = []

    class Spy(MockProvider):
        def complete_json(self, **kwargs):
            seen.append(kwargs)
            return ProviderResult(data={"answer": "ok"}, model=kwargs["model"])

    gateway = LlmGateway(Spy(latency_s=0))
    prompt = Prompt("policy_agent", "policy_agent@1", "s", "{q}")
    with registry.temporary("policy_agent", {"kind": "embedded_agent", "prompt_version": "policy_agent@1",
                                             "model_policy": {"tier": "reasoning", "max_tokens": 1234}}):
        out = gateway.structured(task="policy_agent", prompt=prompt, variables={"q": "x"}, schema_cls=Out,
                                 tier="fast")
        assert out.status == "ok"
        assert seen[-1]["max_tokens"] == 1234 and seen[-1]["model"] == gateway.model_for("reasoning")
        drift = gateway.structured(task="policy_agent", prompt=Prompt("policy_agent", "policy_agent@2", "s", "{q}"),
                                   variables={"q": "x"}, schema_cls=Out)
    assert drift.status == "unavailable" and "not the registered prompt" in drift.error


def test_embedded_agent_calls_get_a_one_call_trace(fresh_state):
    prompt = Prompt("trace_agent", "trace_agent@1", "s", "{q}")
    from app.llm.providers import mock_fixture

    mock_fixture("trace_agent")(lambda text, images, attempt: {"answer": "ok"})
    with registry.temporary("trace_agent", {"kind": "embedded_agent"}):
        out = get_gateway().structured(task="trace_agent", prompt=prompt, variables={"q": "x"}, schema_cls=Out,
                                       input_refs=["Requisition/REQ-1"])
    trace = traces.get(out.run_id)
    assert (trace.kind, trace.agent_id, trace.outcome) == ("call", "trace_agent", "completed")
    assert trace.input_refs == ["Requisition/REQ-1"] and trace.output_ref == f"LlmCall/{out.call_id}"
    assert trace.prompt_version == "trace_agent@1" and trace.model and trace.llm_calls[0].call_id == out.call_id


def test_registry_api_lists_agents_with_their_flags_and_tools(client, login):
    headers = login("U-NURS-05")
    data = client.get("/api/agents", headers=headers).json()
    agents = {a["agent_id"]: a for a in data["agents"]}
    assert data["mode"] == "demo"
    assert agents["patient_message_triage"]["not_evaluated_flag"] is True
    assert agents["deid_check"]["not_evaluated_flag"] is False and agents["deid_check"]["evaluated"] is True
    assert agents["registration"]["kind"] == "module" and agents["registration"]["not_evaluated_flag"] is False
    tools = {t["tool_id"]: t for t in client.get("/api/agents/tools", headers=headers).json()["tools"]}
    assert {t["side_effect_level"] for t in tools.values()} == {"read", "recommend", "action", "privileged"}
    assert tools["signDocumentFinal"]["retry"]["max_attempts"] == 1
    registry_view = client.get("/api/hospital/registry", headers=headers).json()["modules"]
    assert registry_view["registration"]["writes_allowed"]["Appointment"] == ["booked", "proposed"]
