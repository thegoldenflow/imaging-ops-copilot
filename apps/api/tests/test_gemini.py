"""GeminiProvider against a fake google-genai client (no real API calls)."""

import base64
import json

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from app.core import config
from app.core.store import get_store
from app.llm import gateway as gateway_module
from app.llm.gateway import LlmGateway, set_gateway
from app.llm.prompts import Prompt
from app.llm.providers import (
    GeminiProvider,
    MockProvider,
    ProviderRefusal,
    ProviderUnavailable,
    TextBlock,
    ToolUseBlock,
    gemini_contents,
)
from app.modules.frontdesk import agent as frontdesk_agent
from app.modules.frontdesk.tools import TOOL_DEFINITIONS

PROMPT = Prompt(name="t", version="t-v1", system="sys", template="Q: {q}")


class Answer(BaseModel):
    answer: str


class FakeModels:
    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeClient:
    def __init__(self, replies: list) -> None:
        self.models = FakeModels(replies)


def response(parts: list, finish="STOP", prompt_tokens=100, out_tokens=20, thoughts=5, block=None):
    return types.GenerateContentResponse(
        candidates=[] if block else [types.Candidate(content=types.Content(role="model", parts=parts),
                                                     finish_reason=finish)],
        prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=block) if block else None,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens, candidates_token_count=out_tokens, thoughts_token_count=thoughts),
        model_version="gemini-3.5-flash",
    )


def json_reply(data) -> types.GenerateContentResponse:
    return response([types.Part(text=json.dumps(data))])


def gemini_gateway(replies: list) -> tuple[LlmGateway, FakeClient]:
    client = FakeClient(replies)
    return LlmGateway(GeminiProvider("test-key", 5, client=client)), client


def api_error(code: int, status: str) -> genai_errors.APIError:
    cls = genai_errors.ClientError if code < 500 else genai_errors.ServerError
    return cls(code, {"error": {"code": code, "status": status, "message": status.lower()}})


# --- structured JSON output ---------------------------------------------------------------------------------


def test_complete_json_sends_image_schema_and_parses_output():
    client = FakeClient([json_reply({"answer": "clear lungs"})])
    provider = GeminiProvider("test-key", 5, client=client)
    png = base64.b64encode(b"\x89PNG fake").decode()
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"],
              "additionalProperties": False}

    result = provider.complete_json(task="chest_xray_draft", model="gemini-2.5-flash", system="sys",
                                    text="Describe", schema=schema, images=[(png, "image/png")])

    assert result.data == {"answer": "clear lungs"}
    assert result.input_tokens == 100 and result.output_tokens == 25  # thinking tokens count as output
    call = client.models.calls[0]
    image, text = call["contents"]
    assert image.inline_data.data == b"\x89PNG fake" and image.inline_data.mime_type == "image/png"
    assert text == "Describe"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_json_schema == schema
    assert call["config"].system_instruction == "sys"


def test_gateway_on_gemini_logs_vendor_model_and_cost():
    gw, client = gemini_gateway([json_reply({"answer": "ok"})])
    outcome = gw.structured(task="t", prompt=PROMPT, variables={"q": "x"}, schema_cls=Answer)
    assert outcome.status == "ok" and outcome.mode == "gemini"
    assert outcome.model == config.settings.gemini_model_reasoning == client.models.calls[0]["model"]
    call = get_store().llm_calls[-1]
    assert call.mode == "gemini" and call.model == "gemini-3.5-flash"
    assert call.cost_usd == round((100 * 1.50 + 25 * 9.00) / 1e6, 6)


def test_gateway_retries_once_after_schema_failure_on_gemini():
    gw, client = gemini_gateway([json_reply({"wrong": 1}), json_reply({"answer": "ok"})])
    outcome = gw.structured(task="t", prompt=PROMPT, variables={"q": "x"}, schema_cls=Answer)
    assert outcome.status == "ok" and outcome.data == {"answer": "ok"}
    assert get_store().llm_calls[-1].outcome == "retried_ok"
    assert "failed validation" in client.models.calls[1]["contents"][-1]


def test_gateway_retries_after_invalid_json_then_needs_human():
    bad = response([types.Part(text="not json")])
    gw, _ = gemini_gateway([bad, json_reply({"wrong": 1})])
    outcome = gw.structured(task="t", prompt=PROMPT, variables={"q": "x"}, schema_cls=Answer)
    assert outcome.status == "needs_human"


# --- degradation and refusals -------------------------------------------------------------------------------


@pytest.mark.parametrize("error", [
    httpx.ReadTimeout("timed out"),
    httpx.ConnectError("no route"),
    api_error(429, "RESOURCE_EXHAUSTED"),
    api_error(503, "UNAVAILABLE"),
])
def test_timeouts_rate_limits_and_network_errors_degrade(error):
    gw, _ = gemini_gateway([error])
    outcome = gw.structured(task="t", prompt=PROMPT, variables={"q": "x"}, schema_cls=Answer)
    assert outcome.status == "unavailable" and outcome.error == "AI is temporarily unavailable"
    assert get_store().llm_calls[-1].outcome == "unavailable"


def test_provider_raises_unavailable_on_timeout():
    provider = GeminiProvider("k", 5, client=FakeClient([httpx.ReadTimeout("timed out")]))
    with pytest.raises(ProviderUnavailable):
        provider.complete_json(task="t", model="m", system="s", text="x", schema={})


@pytest.mark.parametrize("reply", [
    response([], finish="SAFETY"),
    response([], block="PROHIBITED_CONTENT"),
])
def test_refusals_route_to_a_person(reply):
    provider = GeminiProvider("k", 5, client=FakeClient([reply]))
    with pytest.raises(ProviderRefusal):
        provider.complete_json(task="t", model="m", system="s", text="x", schema={})
    gw, _ = gemini_gateway([reply])
    assert gw.structured(task="t", prompt=PROMPT, variables={"q": "x"}, schema_cls=Answer).status == "needs_human"


# --- tool use format conversion -----------------------------------------------------------------------------


def test_history_converts_to_gemini_contents():
    history = [
        {"role": "user", "content": "I want to cancel"},
        {"role": "assistant", "content": [
            TextBlock(text="Let me check.", signature=b"sig-text"),
            ToolUseBlock(id="call_1", name="lookup_appointment", input={}, call_id="g-1", signature=b"sig-call"),
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": json.dumps({"appointments": []})},
        ]},
    ]
    contents = gemini_contents(history)
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[0].parts[0].text == "I want to cancel"
    text, call = contents[1].parts
    assert text.text == "Let me check." and text.thought_signature == b"sig-text"
    assert call.function_call.name == "lookup_appointment" and call.function_call.id == "g-1"
    assert call.thought_signature == b"sig-call"  # Gemini needs the signature back with the call
    result = contents[2].parts[0].function_response
    assert result.name == "lookup_appointment" and result.id == "g-1"
    assert result.response == {"output": {"appointments": []}}


def test_tool_turn_returns_anthropic_style_blocks():
    reply = response([
        types.Part(text="thinking...", thought=True),
        types.Part(text="One moment."),
        types.Part(function_call=types.FunctionCall(name="get_site_info", args={"site": "Lakeshore"}),
                   thought_signature=b"s1"),
    ])
    client = FakeClient([reply])
    provider = GeminiProvider("k", 5, client=client)
    result = provider.tool_turn(model="gemini-2.5-flash", system="sys",
                                messages=[{"role": "user", "content": "Where are you?"}], tools=TOOL_DEFINITIONS)

    assert result.stop_reason == "tool_use"
    text, tool = result.content
    assert text.type == "text" and text.text == "One moment."
    assert tool.type == "tool_use" and tool.name == "get_site_info" and tool.input == {"site": "Lakeshore"}
    assert tool.id.startswith("call_") and tool.call_id is None and tool.signature == b"s1"
    config_ = client.models.calls[0]["config"]
    declarations = config_.tools[0].function_declarations
    assert [d.name for d in declarations] == [t["name"] for t in TOOL_DEFINITIONS]
    assert declarations[0].parameters_json_schema == TOOL_DEFINITIONS[0]["input_schema"]
    assert config_.automatic_function_calling.disable is True


def test_phone_agent_runs_tool_loop_on_gemini():
    store = get_store()
    tool_call = response([types.Part(function_call=types.FunctionCall(id="g-7", name="get_site_info", args={"site": ""}))])
    answer = response([types.Part(text="We are open today.")])
    gw, client = gemini_gateway([tool_call, answer])
    set_gateway(gw)

    session = frontdesk_agent.new_session(store)
    assert session.agent_mode == "gemini"
    reply, mode = frontdesk_agent.agent_reply(store, session, "What are your hours?")

    assert (reply, mode) == ("We are open today.", "gemini")
    second = client.models.calls[1]["contents"]
    assert [c.role for c in second] == ["user", "model", "user"]
    fr = second[2].parts[0].function_response
    assert fr.name == "get_site_info" and fr.id == "g-7" and "address" in fr.response["output"]
    assert [c.mode for c in store.llm_calls[-2:]] == ["gemini", "gemini"]


def test_phone_agent_falls_back_to_script_when_gemini_is_down():
    store = get_store()
    gw, _ = gemini_gateway([httpx.ReadTimeout("timed out")])
    set_gateway(gw)
    session = frontdesk_agent.new_session(store)
    reply, mode = frontdesk_agent.agent_reply(store, session, "What are your hours?")
    assert mode == "scripted" and "open" in reply


# --- configuration ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("env, expected", [
    ({}, "mock"),
    ({"ANTHROPIC_API_KEY": "a"}, "anthropic"),
    ({"ANTHROPIC_API_KEY": "a", "LLM_PROVIDER": "gemini"}, "gemini"),
    ({"LLM_MODE": "mock", "ANTHROPIC_API_KEY": "a"}, "mock"),
    ({"LLM_PROVIDER": "Gemini"}, "gemini"),
])
def test_llm_provider_setting(monkeypatch, env, expected):
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_AGENT_PLATFORM_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER", "LLM_MODE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert config.load_settings().llm_provider == expected


def test_invalid_llm_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(ValueError):
        config.load_settings()


def test_provider_selection(monkeypatch):
    def with_settings(**overrides):
        monkeypatch.setattr(gateway_module, "settings", config.Settings(**{**vars(config.settings), **overrides}))
        return gateway_module.make_provider()

    assert isinstance(with_settings(llm_provider="gemini", google_agent_platform_api_key="g"), GeminiProvider)
    assert isinstance(with_settings(llm_provider="gemini", google_agent_platform_api_key=None), MockProvider)
    assert isinstance(with_settings(llm_provider="mock", google_agent_platform_api_key="g", anthropic_api_key="a"), MockProvider)
    gw = LlmGateway(with_settings(llm_provider="gemini", google_agent_platform_api_key="g", gemini_model_fast="gemini-x"))
    assert gw.mode == "gemini" and gw.model_for("fast") == "gemini-x"
    assert LlmGateway(MockProvider()).model_for("fast") == config.settings.model_fast


def test_meta_reports_gemini_mode(client, login):
    set_gateway(gemini_gateway([])[0])
    headers = login("U-ADMIN")
    assert client.get("/api/meta", headers=headers).json()["llm_mode"] == "gemini"
    assert client.get("/api/admin/ai-usage", headers=headers).json()["mode"] == "gemini"


def test_eval_result_names_vendor_and_model(monkeypatch, tmp_path):
    from app.modules.evals import run

    monkeypatch.setattr(run, "RESULTS", tmp_path)
    gw, _ = gemini_gateway([json_reply({"answer": "ok"})])
    import time
    started = time.monotonic()
    gw.structured(task="t", prompt=PROMPT, variables={"q": "x"}, schema_cls=Answer)
    result = run._write("t", gw, {"m": 1}, 1, [], started, "")
    assert result["mode"] == "gemini" and result["model"] == "Gemini: gemini-3.5-flash"
    mock = run._write("t", LlmGateway(MockProvider(latency_s=0)), {}, 0, [], started, "")
    assert mock["model"] == "rule-based baseline"


def test_vertex_client_uses_agent_platform_configuration(monkeypatch):
    captured = {}

    def make_client(**kwargs):
        captured.update(kwargs)
        return FakeClient([])

    monkeypatch.setattr("app.llm.providers.genai.Client", make_client)
    GeminiProvider("agent-platform-key", 5)

    assert captured["vertexai"] is True
    assert captured["api_key"] == "agent-platform-key"
    assert captured["http_options"].api_version == "v1"


@pytest.mark.parametrize("code, expected", [
    (401, "authentication or permission"),
    (403, "authentication or permission"),
    (404, "model or endpoint not found"),
    (429, "rate or quota limit"),
    (402, "Gemini Developer API payment route"),
])
def test_vertex_api_errors_have_actionable_messages(code, expected):
    provider = GeminiProvider("k", 5, client=FakeClient([api_error(code, "ERROR")]))
    with pytest.raises(ProviderUnavailable, match=expected):
        provider.complete_json(task="t", model="gemini-2.5-flash", system="s", text="x", schema={})
