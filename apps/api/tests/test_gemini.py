"""GeminiProvider against a fake google-genai client (no real API calls)."""

import base64
import json

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field

from app.agents import registry
from app.core import config
from app.core.store import get_store
from app.llm import gateway as gateway_module
from app.llm.gateway import LlmGateway, set_gateway
from app.llm.prompts import Prompt
from app.llm.providers import (
    GeminiProvider,
    MockProvider,
    OutputTruncated,
    ProviderRefusal,
    ProviderUnavailable,
    TextBlock,
    ToolUseBlock,
    gemini_contents,
)
from app.modules.frontdesk import agent as frontdesk_agent
from app.modules.frontdesk.tools import TOOL_DEFINITIONS

PROMPT = Prompt(name="t", version="t-v1", system="sys", template="Q: {q}")


@pytest.fixture(autouse=True)
def _registered_test_agent():
    """The LLM gateway refuses tasks without an agent-registry entry (6.4); "t" is this file's test agent."""
    with registry.temporary("t", {"kind": "embedded_agent"}):
        yield


class Answer(BaseModel):
    answer: str


class FakeModels:
    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        reply = self.replies.pop(0)
        if callable(reply):  # e.g. one that lets time pass on a FakeClock first
            reply = reply()
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


def gemini_gateway(replies: list, thinking: dict | None = None) -> tuple[LlmGateway, FakeClient]:
    client = FakeClient(replies)
    return LlmGateway(GeminiProvider("test-key", 5, client=client, thinking=thinking)), client


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


class Answers(BaseModel):
    items: list[Answer] = Field(max_length=2)


def test_schema_sent_to_gemini_has_no_item_limits_and_the_gateway_still_enforces_them():
    # Vertex AI answers 400 INVALID_ARGUMENT to large maxItems on arrays of objects (live run, 2026-10-12).
    three = {"items": [{"answer": "a"}, {"answer": "b"}, {"answer": "c"}]}
    gw, client = gemini_gateway([json_reply(three), json_reply(three)])
    outcome = gw.structured(task="t", prompt=PROMPT, variables={"q": "x"}, schema_cls=Answers)
    sent = client.models.calls[0]["config"].response_json_schema
    assert "maxItems" not in json.dumps(sent)
    assert sent["properties"]["items"]["items"]["required"] == ["answer"]
    assert outcome.status == "needs_human"  # three items break the limit of two: refused twice by validation


# --- thinking level and output limit -------------------------------------------------------------------------

THINKING = {"reasoning": "HIGH", "fast": "LOW", "voice": "MINIMAL"}


def test_thinking_level_is_sent_per_tier():
    client = FakeClient([json_reply({"answer": "a"}), json_reply({"answer": "b"}), json_reply({"answer": "c"}),
                         response([types.Part(text="Hello.")])])
    provider = GeminiProvider("k", 5, client=client, thinking={**THINKING, "fast": "DEFAULT"})
    for tier in ("reasoning", "fast", None):
        provider.complete_json(task="t", model="m", system="s", text="x", schema={}, tier=tier)
    provider.tool_turn(model="m", system="s", messages=[{"role": "user", "content": "Hi"}], tools=TOOL_DEFINITIONS,
                       tier="voice")
    sent = [c["config"].thinking_config for c in client.models.calls]
    assert sent[0].thinking_level == types.ThinkingLevel.HIGH
    assert sent[1] is None and sent[2] is None  # DEFAULT, or no tier: the model's own default
    assert sent[3].thinking_level == types.ThinkingLevel.MINIMAL


def test_thinking_is_a_level_for_gemini_3_and_a_budget_for_gemini_2_5():
    # Gemini 3.x takes thinking_level; 2.x takes an integer thinking_budget (PROGRESS.md lists gemini-2.5-flash as a
    # priced option). Sending a thinking_level to a 2.5 model would be an unsupported field.
    client = FakeClient([json_reply({"answer": "a"}), json_reply({"answer": "b"})])
    provider = GeminiProvider("k", 5, client=client, thinking={"reasoning": "LOW"})
    provider.complete_json(task="t", model="gemini-3.5-flash", system="s", text="x", schema={}, tier="reasoning")
    provider.complete_json(task="t", model="gemini-2.5-flash", system="s", text="x", schema={}, tier="reasoning")
    g3, g25 = (c["config"].thinking_config for c in client.models.calls)
    assert g3.thinking_level == types.ThinkingLevel.LOW and g3.thinking_budget is None
    assert g25.thinking_budget == 512 and g25.thinking_level is None


def test_gateway_sends_the_thinking_level_of_the_agents_model_tier():
    pinned_prompt = Prompt(name="t_pinned", version="t-v1", system="s", template="{q}")
    with registry.temporary("t_pinned", {"kind": "embedded_agent", "model_policy": {
            "tier": "fast", "model": "gemini-reasoning", "max_tokens": 900}}):
        gw, client = gemini_gateway([json_reply({"answer": "a"}), json_reply({"answer": "b"})], thinking=THINKING)
        gw.structured(task="t", prompt=PROMPT, variables={"q": "x"}, schema_cls=Answer)  # the fast tier
        gw.structured(task="t_pinned", prompt=pinned_prompt, variables={"q": "x"}, schema_cls=Answer)
    fast, pinned = (c["config"] for c in client.models.calls)
    assert fast.thinking_config.thinking_level == types.ThinkingLevel.LOW and fast.max_output_tokens == 2000
    # A policy pinned to a registered model gets that model's tier.
    assert pinned.thinking_config.thinking_level == types.ThinkingLevel.HIGH and pinned.max_output_tokens == 900


def test_phone_agent_turns_use_the_voice_thinking_level():
    gw, client = gemini_gateway([response([types.Part(text="We are open today.")])], thinking=THINKING)
    set_gateway(gw)
    store = get_store()
    frontdesk_agent.agent_reply(store, frontdesk_agent.new_session(store), "What are your hours?")
    assert client.models.calls[0]["config"].thinking_config.thinking_level == types.ThinkingLevel.MINIMAL


def test_thinking_levels_come_from_the_environment(monkeypatch):
    for tier in ("REASONING", "FAST", "VOICE"):
        monkeypatch.delenv(f"GEMINI_THINKING_{tier}", raising=False)
    defaults = config.load_settings()
    assert (defaults.gemini_thinking_reasoning, defaults.gemini_thinking_fast, defaults.gemini_thinking_voice) == (
        "LOW", "LOW", "MINIMAL")
    monkeypatch.setenv("GEMINI_THINKING_REASONING", "medium")
    monkeypatch.setenv("GEMINI_THINKING_VOICE", "default")
    custom = config.load_settings()
    assert (custom.gemini_thinking_reasoning, custom.gemini_thinking_voice) == ("MEDIUM", "DEFAULT")
    monkeypatch.setattr(gateway_module, "settings", config.Settings(**{
        **vars(custom), "llm_provider": "gemini", "google_agent_platform_api_key": "g"}))
    monkeypatch.setattr("app.llm.providers.genai.Client", lambda **_: FakeClient([]))
    assert gateway_module.make_provider().thinking == {"reasoning": "MEDIUM", "fast": "LOW", "voice": "DEFAULT"}
    monkeypatch.setenv("GEMINI_THINKING_FAST", "none")
    with pytest.raises(ValueError, match="GEMINI_THINKING_FAST"):
        config.load_settings()


def truncated(thoughts=7676, answer=309):
    """The 2026-10-12 live run's documentation synthesis: thinking used up the 8,000-token output limit."""
    return response([types.Part(text='{"answer": "the discharge summary sta')], finish="MAX_TOKENS",
                    prompt_tokens=3000, out_tokens=answer, thoughts=thoughts)


def test_max_tokens_finish_is_its_own_error():
    provider = GeminiProvider("k", 5, client=FakeClient([truncated()]))
    with pytest.raises(OutputTruncated, match="output limit of 8000 tokens") as raised:
        provider.complete_json(task="t", model="m", system="s", text="x", schema={}, max_tokens=8000)
    assert "MAX_TOKENS; 7676 of 7985 output tokens were thinking" in str(raised.value)
    assert (raised.value.input_tokens, raised.value.output_tokens) == (3000, 7985)


def test_gateway_retries_a_truncated_answer_once_and_logs_why_and_its_tokens():
    gw, client = gemini_gateway([truncated(), json_reply({"answer": "ok"})])
    outcome = gw.structured(task="t", prompt=PROMPT, variables={"q": "x"}, schema_cls=Answer)
    assert outcome.status == "ok"
    first, retry = client.models.calls
    # The retry is meaningful, not a copy of the request: a be-concise hint, and thinking capped at MINIMAL to free
    # the output budget (the first call sent no thinking config for this gateway).
    assert first["config"].thinking_config is None
    assert "Answer more concisely" in retry["contents"][-1]
    assert "cut off at the model's output limit" in retry["contents"][-1]
    assert retry["config"].thinking_config.thinking_level == types.ThinkingLevel.MINIMAL
    call = get_store().llm_calls[-1]
    assert call.outcome == "retried_ok" and call.output_tokens == 7985 + 25  # the truncated attempt is billed too

    gw, _ = gemini_gateway([truncated(), truncated()])
    assert gw.structured(task="t", prompt=PROMPT, variables={"q": "x"}, schema_cls=Answer).status == "needs_human"
    call = get_store().llm_calls[-1]
    assert call.outcome == "needs_human" and "finish reason MAX_TOKENS" in call.error
    assert (call.input_tokens, call.output_tokens) == (6000, 2 * 7985)


# --- retries --------------------------------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def after(self, seconds: float, outcome):
        """A reply that arrives `seconds` after its request."""
        def reply():
            self.now += seconds
            return outcome
        return reply


def retrying_provider(replies: list, timeout_s: float):
    """Replies may be functions of the clock (FakeClock.after)."""
    clock = FakeClock()
    client = FakeClient([r(clock) if callable(r) else r for r in replies])
    provider = GeminiProvider("k", timeout_s, client=client, clock=clock)
    provider.RETRY_JITTER_S = 0
    return provider, client, clock


def test_rate_limits_and_gateway_timeouts_are_retried_with_backoff():
    provider, client, clock = retrying_provider([
        lambda c: c.after(1, api_error(429, "RESOURCE_EXHAUSTED")),
        lambda c: c.after(3, api_error(504, "DEADLINE_EXCEEDED")),
        lambda c: c.after(2, json_reply({"answer": "ok"})),
    ], timeout_s=30)
    result = provider.complete_json(task="t", model="m", system="s", text="x", schema={})
    assert result.data == {"answer": "ok"}
    assert clock.sleeps == [2.0, 4.0]
    # Each attempt may use what is left of the call's 30 s: 30, then 30 - 1 - 2, then 30 - 1 - 2 - 3 - 4.
    assert [c["config"].http_options.timeout for c in client.models.calls] == [30000, 27000, 20000]


def test_retries_stop_after_four_attempts():
    provider, client, clock = retrying_provider([api_error(429, "RESOURCE_EXHAUSTED")] * 4, timeout_s=60)
    with pytest.raises(ProviderUnavailable, match=r"rate or quota limit reached \(after 4 attempts\)"):
        provider.complete_json(task="t", model="m", system="s", text="x", schema={})
    assert len(client.models.calls) == 4 and clock.sleeps == [2.0, 4.0, 8.0]


def test_retries_stay_inside_the_call_timeout():
    # Each 504 comes 10 s after its request: after the second (at 22 s) a third attempt would have under 5 s left.
    provider, client, clock = retrying_provider(
        [lambda c: c.after(10, api_error(504, "DEADLINE_EXCEEDED"))] * 3, timeout_s=30)
    with pytest.raises(ProviderUnavailable, match=r"\(after 2 attempts\)"):
        provider.complete_json(task="t", model="m", system="s", text="x", schema={})
    assert len(client.models.calls) == 2 and clock.sleeps == [2.0] and clock.now == 22


@pytest.mark.parametrize("error", [api_error(400, "INVALID_ARGUMENT"), api_error(403, "PERMISSION_DENIED")])
def test_request_errors_are_not_retried(error):
    provider, client, clock = retrying_provider([error], timeout_s=60)
    with pytest.raises(ProviderUnavailable):
        provider.complete_json(task="t", model="m", system="s", text="x", schema={})
    assert len(client.models.calls) == 1 and clock.sleeps == []


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
    assert captured["http_options"].timeout == 5000
    assert captured["http_options"].retry_options is None  # the provider retries, inside the call's timeout


@pytest.mark.parametrize("code, expected", [
    (401, "authentication or permission"),
    (403, "authentication or permission"),
    (404, "model or endpoint not found"),
    (429, "rate or quota limit"),
    (402, "payment required"),
])
def test_vertex_api_errors_have_actionable_messages(code, expected):
    provider = GeminiProvider("k", 5, client=FakeClient([api_error(code, "ERROR")]))
    with pytest.raises(ProviderUnavailable, match=expected) as raised:
        provider.complete_json(task="t", model="gemini-3.5-flash", system="s", text="x", schema={})
    assert "Google: error" in str(raised.value)  # Google's own message is kept


def test_ai_studio_key_is_not_used_for_vertex(monkeypatch, caplog):
    monkeypatch.delenv("GOOGLE_AGENT_PLATFORM_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "ai-studio-key")
    assert config.load_settings().google_agent_platform_api_key is None
    assert "GEMINI_API_KEY is ignored" in caplog.text
