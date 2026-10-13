"""LLM providers behind the gateway: the Anthropic API, the Gemini API and a mock.

The mock returns canned, schema-shaped outputs so every AI feature can be
demoed without an API key. The gateway decides which provider to use.

Callers speak one format: Anthropic-style messages and tool definitions, and
content blocks with `type`, `text`, `id`, `name` and `input`. The Gemini
provider converts to and from Gemini's contents and function calls.
"""

import base64
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic
import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types


class ProviderUnavailable(Exception):
    """Timeout, rate limit, network or server failure: degrade gracefully."""


class ProviderRefusal(Exception):
    """The model declined; route to a person."""


class InvalidOutput(Exception):
    """Output was not valid JSON. Carries the tokens the failed attempt still billed, so the gateway can
    account for them when it retries."""

    def __init__(self, message: str, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class OutputTruncated(InvalidOutput):
    """The model stopped at its output-token limit (Gemini finish reason MAX_TOKENS), often because thinking ate
    the budget. Its own error so the gateway can retry it differently from malformed JSON — telling the model to
    answer concisely and capping its thinking on the retry — instead of repeating the identical request and
    truncating again."""


@dataclass
class ProviderResult:
    data: Any
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    content: list = field(default_factory=list)
    stop_reason: str | None = None


def strict_schema(model_cls) -> dict:
    """Pydantic JSON schema -> structured-outputs schema: refs inlined,
    every object closed (additionalProperties false) and every field required."""
    raw = model_cls.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].split("/")[-1]])
            node = {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            return node
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(raw)


# Mock fixtures: task name -> function(text, images, attempt) -> output dict.
MOCK_FIXTURES: dict[str, Callable[[str, list, int], Any]] = {}


def mock_fixture(task: str):
    def register(fn):
        MOCK_FIXTURES[task] = fn
        return fn

    return register


# Mock tool-use fixtures: task name -> function(messages, tools) -> (content blocks, stop reason). A tool-using
# agent with one runs its loop on the mock (its eval); without one the mock has no tool-use agent.
MOCK_TOOL_FIXTURES: dict[str, Callable[[list, list], tuple[list, str]]] = {}


def mock_tool_fixture(task: str):
    def register(fn):
        MOCK_TOOL_FIXTURES[task] = fn
        return fn

    return register


class MockProvider:
    mode = "mock"

    def __init__(self, latency_s: float = 0.6) -> None:
        self.latency_s = latency_s
        self.attempts: dict[str, int] = {}

    def complete_json(self, *, task, model, system, text, schema, images=None, attempt=0,
                      max_tokens=8000, tier=None, thinking_level=None, timeout_s=None) -> ProviderResult:
        time.sleep(self.latency_s)
        fixture = MOCK_FIXTURES.get(task)
        if fixture is None:
            raise ProviderUnavailable(f"No mock fixture for task {task}")
        data = fixture(text, images or [], attempt)
        return ProviderResult(
            data=data, model=f"mock:{model}",
            input_tokens=len(text) // 4 + 1500 * len(images or []), output_tokens=len(json.dumps(data)) // 4,
        )

    def tool_turn(self, *, task=None, model="", messages=(), tools=(), **_) -> ProviderResult:
        fixture = MOCK_TOOL_FIXTURES.get(task)
        if fixture is None:
            raise ProviderUnavailable("The mock provider has no tool-use agent; use the scripted agent")
        time.sleep(self.latency_s)
        content, stop_reason = fixture(list(messages), list(tools))
        return ProviderResult(
            data=None, model=f"mock:{model}", content=content, stop_reason=stop_reason,
            input_tokens=len(json.dumps(list(messages), default=str)) // 4,
            output_tokens=len(json.dumps(content, default=str)) // 4,
        )


class AnthropicProvider:
    mode = "anthropic"
    # Sonnet 5.5: opt into server-side refusal fallbacks so a declined request
    # is retried on a fallback model instead of failing outright.
    FALLBACK_MODELS = ("claude-sonnet-5-5",)

    def __init__(self, api_key: str, timeout_s: float) -> None:
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=1)

    def _create(self, timeout_s=None, **kwargs):
        if timeout_s is not None:  # the agent's own timeout (model_policy.timeout_s) instead of the client's
            kwargs["timeout"] = timeout_s
        try:
            if kwargs["model"] in self.FALLBACK_MODELS:
                return self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            return self.client.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            raise ProviderUnavailable(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:  # includes timeouts
            raise ProviderUnavailable(f"Connection error: {e}") from e

    def complete_json(self, *, task, model, system, text, schema, images=None, attempt=0,
                      max_tokens=8000, tier=None, thinking_level=None, timeout_s=None) -> ProviderResult:
        content: list[dict] = [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
            for b64, media_type in (images or [])
        ]
        content.append({"type": "text", "text": text})
        resp = self._create(
            timeout_s=timeout_s,
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if resp.stop_reason == "refusal":
            raise ProviderRefusal("Model declined the request")
        body = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise InvalidOutput(str(e)) from e
        return ProviderResult(
            data=data, model=resp.model,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
        )

    def tool_turn(self, *, model, system, messages, tools, max_tokens=1024, task=None, tier=None,
                  timeout_s=None) -> ProviderResult:
        resp = self._create(timeout_s=timeout_s, model=model, max_tokens=max_tokens, system=system,
                            messages=messages, tools=tools)
        if resp.stop_reason == "refusal":
            raise ProviderRefusal("Model declined the request")
        return ProviderResult(
            data=None, model=resp.model, content=list(resp.content),
            stop_reason=resp.stop_reason,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
        )


@dataclass
class TextBlock:
    text: str
    type: str = "text"
    signature: bytes | None = None  # Gemini thought signature, sent back with the turn


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"
    call_id: str | None = None  # Gemini's own function-call id, if it gave one
    signature: bytes | None = None


def _get(block, key, default=None):
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def gemini_tools(tools: list[dict]) -> list:
    """Anthropic tool definitions -> one Gemini tool with function declarations."""
    return [genai_types.Tool(function_declarations=[
        genai_types.FunctionDeclaration(name=t["name"], description=t.get("description", ""),
                                        parameters_json_schema=t["input_schema"])
        for t in tools
    ])]


def gemini_contents(messages: list[dict]) -> list:
    """Anthropic-style history -> Gemini contents. Tool results become function
    responses with the name and id of the call they answer."""
    calls: dict[str, ToolUseBlock | Any] = {}
    contents = []
    for message in messages:
        role = "model" if message["role"] == "assistant" else "user"
        content = message["content"]
        if isinstance(content, str):
            contents.append(genai_types.Content(role=role, parts=[genai_types.Part(text=content)]))
            continue
        parts = []
        for block in content:
            kind = _get(block, "type")
            if kind == "text":
                if _get(block, "text"):
                    parts.append(genai_types.Part(text=_get(block, "text"), thought_signature=_get(block, "signature")))
            elif kind == "tool_use":
                calls[_get(block, "id")] = block
                parts.append(genai_types.Part(
                    function_call=genai_types.FunctionCall(id=_get(block, "call_id"), name=_get(block, "name"),
                                                           args=dict(_get(block, "input") or {})),
                    thought_signature=_get(block, "signature"),
                ))
            elif kind == "tool_result":
                call = calls.get(_get(block, "tool_use_id"))
                raw = _get(block, "content")
                try:
                    output = json.loads(raw) if isinstance(raw, str) else raw
                except json.JSONDecodeError:
                    output = raw
                parts.append(genai_types.Part(function_response=genai_types.FunctionResponse(
                    id=_get(call, "call_id") if call is not None else None,
                    name=_get(call, "name", "") if call is not None else "",
                    response={"output": output},
                )))
        if parts:
            contents.append(genai_types.Content(role=role, parts=parts))
    return contents


def blocks_from_gemini(parts: list) -> list:
    """Gemini response parts -> text and tool_use blocks (thoughts dropped)."""
    blocks: list = []
    for part in parts or []:
        if part.thought:
            continue
        if part.function_call is not None:
            fc = part.function_call
            blocks.append(ToolUseBlock(id=fc.id or f"call_{uuid.uuid4().hex[:12]}", name=fc.name,
                                       input=dict(fc.args or {}), call_id=fc.id, signature=part.thought_signature))
        elif part.text:
            blocks.append(TextBlock(text=part.text, signature=part.thought_signature))
    return blocks


def gemini_schema(node):
    """The structured-output schema as Gemini accepts it: without `maxItems`. Vertex AI rejects a schema whose
    arrays of objects carry large item limits (400 INVALID_ARGUMENT; seen with maxItems 40 on medication
    reconciliation and the documentation agents, while 10 passed). The gateway still validates every answer against
    the full Pydantic model, so a list that runs over its limit fails validation and is retried there."""
    if isinstance(node, dict):
        return {k: gemini_schema(v) for k, v in node.items() if k != "maxItems"}
    if isinstance(node, list):
        return [gemini_schema(v) for v in node]
    return node


class GeminiProvider:
    """Google Gemini through the Agent Platform / Vertex AI google-genai route."""

    mode = "gemini"
    REFUSAL_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY",
                       "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION"}
    # Retry rate limits and transient server/connection failures with exponential backoff, within the call's own
    # timeout (its budget: LLM_TIMEOUT_S or the agent's model_policy.timeout_s). The 2026-10-12 live run hit 429s
    # under load; a short backoff let the call ride them out. It also hit Vertex stalls: an attempt that returns
    # nothing until its deadline, then 504 DEADLINE_EXCEEDED. The SDK sends an attempt's timeout as the server
    # deadline (X-Server-Timeout), so one attempt may use at most ATTEMPT_SHARE of the budget: a stalled attempt
    # then leaves room for a full retry instead of eating the whole budget (with one 30 s attempt, the 504 came at
    # about 27.5 s and the retry never fired). The call as a whole never takes longer than its budget.
    MAX_ATTEMPTS = 4
    RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504})
    RETRY_JITTER_S = 0.5
    ATTEMPT_SHARE = 0.5  # the most of the budget one attempt may use
    MIN_ATTEMPT_S = 5.0  # don't start another attempt with less than this left of the budget

    def __init__(self, api_key: str, timeout_s: float, client=None, thinking: dict | None = None, clock=None) -> None:
        self.timeout_s = timeout_s
        # tier ("reasoning"/"fast"/"voice") -> Gemini thinking level name; missing or "DEFAULT" sends none, so the
        # model keeps its own default. Gemini bills thinking as output and thinks by default (3), which truncated
        # the documentation agents and slowed the phone agent in the 2026-10-12 live run.
        self.thinking = thinking or {}
        self._clock = clock or time  # monotonic() + sleep(); injectable so retries are testable without real waits
        self.client = client or genai.Client(
            vertexai=True,
            api_key=api_key,
            http_options=genai_types.HttpOptions(
                api_version="v1",
                timeout=int(timeout_s * 1000),
                retry_options=None,  # this provider retries itself, bounded by the call's timeout (see _generate)
            ),
        )

    def _thinking_config(self, model, tier, override=None):
        """The ThinkingConfig for a model and tier, or None to leave the model on its own default (no tier, level
        DEFAULT, or none configured). `override` forces a level (the gateway's concise truncation retry uses it).
        Gemini 3.x takes a thinking_level; 2.x takes an integer thinking_budget, so a GEMINI_MODEL_* set to
        gemini-2.5-flash does not receive an unsupported field (PROGRESS.md lists it as a priced option)."""
        level = override or (self.thinking.get(tier) if tier is not None else None)
        if not level or level == "DEFAULT":
            return None
        if "gemini-2." in str(model or "").lower():
            budget = {"MINIMAL": 0, "LOW": 512, "MEDIUM": 4096, "HIGH": 16384}[level]
            return genai_types.ThinkingConfig(thinking_budget=budget)
        return genai_types.ThinkingConfig(thinking_level=getattr(genai_types.ThinkingLevel, level))

    @staticmethod
    def _api_error(e: genai_errors.APIError) -> tuple[str, str, bool]:
        """(message prefix, Google's own detail, retryable?) for an API error. The prefix and detail are kept
        apart so an "(after N attempts)" note can sit between them."""
        code = int(e.code or 0)
        hint = {
            401: "authentication or permission error: check the API key and API access",
            403: "authentication or permission error: check the API key and API access",
            404: "model or endpoint not found: check the model and Vertex endpoint",
            429: "rate or quota limit reached",
            402: "payment required: check billing on the Google Cloud project",
        }.get(code, "request failed")
        # Keep Google's own message: it names the actual cause.
        return (f"Agent Platform {code} {hint}", f". Google: {e.message or e.status}",
                code in GeminiProvider.RETRYABLE_CODES)

    def _generate(self, *, model, contents, config, timeout_s=None):
        """Call Gemini, retrying rate limits and transient server/connection failures with exponential backoff.
        `timeout_s` is the call's budget (None: the provider's LLM_TIMEOUT_S). Each attempt gets at most
        ATTEMPT_SHARE of it and never more than is left; the loop stops once too little remains to start another
        attempt."""
        budget = timeout_s or self.timeout_s
        start = self._clock.monotonic()
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            remaining = budget - (self._clock.monotonic() - start)
            deadline = min(budget * self.ATTEMPT_SHARE, remaining)
            cfg = config.model_copy(update={
                "http_options": genai_types.HttpOptions(timeout=int(max(deadline, 0.0) * 1000))})
            try:
                return self.client.models.generate_content(model=model, contents=contents, config=cfg)
            except genai_errors.APIError as e:  # 4xx incl. 429 rate limit, 5xx
                prefix, detail, retryable, cause = (*self._api_error(e), e)
            except (httpx.HTTPError, OSError) as e:  # timeouts and network failures
                prefix, detail, retryable, cause = f"Connection error: {type(e).__name__}: {e}", "", True, e
            if retryable and attempt < self.MAX_ATTEMPTS:
                backoff = 2 ** attempt + random.uniform(0, self.RETRY_JITTER_S)
                left = budget - (self._clock.monotonic() - start)
                if left - backoff >= self.MIN_ATTEMPT_S:
                    self._clock.sleep(backoff)
                    continue
            suffix = f" (after {attempt} attempts)" if attempt > 1 else ""
            raise ProviderUnavailable(f"{prefix}{suffix}{detail}") from cause

    def _parts(self, resp) -> list:
        feedback = resp.prompt_feedback
        if feedback is not None and feedback.block_reason:
            raise ProviderRefusal(f"Prompt blocked: {_get(feedback.block_reason, 'name', feedback.block_reason)}")
        if not resp.candidates:
            raise ProviderRefusal("Model returned no candidates")
        candidate = resp.candidates[0]
        reason = _get(candidate.finish_reason, "name", candidate.finish_reason)
        if reason in self.REFUSAL_REASONS:
            raise ProviderRefusal(f"Model declined the request ({reason})")
        return (candidate.content.parts if candidate.content else None) or []

    @staticmethod
    def _usage(resp) -> tuple[int, int]:
        u = resp.usage_metadata
        if u is None:
            return 0, 0
        # Thinking tokens are billed as output.
        return u.prompt_token_count or 0, (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)

    def complete_json(self, *, task, model, system, text, schema, images=None, attempt=0,
                      max_tokens=8000, tier=None, thinking_level=None, timeout_s=None) -> ProviderResult:
        contents: list = [
            genai_types.Part.from_bytes(data=base64.b64decode(b64), mime_type=media_type)
            for b64, media_type in (images or [])
        ]
        contents.append(text)
        resp = self._generate(model=model, contents=contents, config=genai_types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
            response_json_schema=gemini_schema(schema),
            thinking_config=self._thinking_config(model, tier, thinking_level),
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
        ), timeout_s=timeout_s)
        parts = self._parts(resp)
        tokens_in, tokens_out = self._usage(resp)
        if _get(resp.candidates[0].finish_reason, "name", resp.candidates[0].finish_reason) == "MAX_TOKENS":
            u = resp.usage_metadata
            thoughts = (u.thoughts_token_count or 0) if u is not None else 0
            raise OutputTruncated(
                f"stopped at the output limit of {max_tokens} tokens (finish reason MAX_TOKENS; "
                f"{thoughts} of {tokens_out} output tokens were thinking)",
                input_tokens=tokens_in, output_tokens=tokens_out)
        body = "".join(p.text for p in parts if p.text and not p.thought)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise InvalidOutput(str(e), input_tokens=tokens_in, output_tokens=tokens_out) from e
        return ProviderResult(data=data, model=resp.model_version or model,
                              input_tokens=tokens_in, output_tokens=tokens_out)

    def tool_turn(self, *, model, system, messages, tools, max_tokens=1024, task=None, tier=None,
                  timeout_s=None) -> ProviderResult:
        resp = self._generate(model=model, contents=gemini_contents(messages), config=genai_types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens,
            tools=gemini_tools(tools),
            thinking_config=self._thinking_config(model, tier),
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
        ), timeout_s=timeout_s)
        blocks = blocks_from_gemini(self._parts(resp))
        reason = _get(resp.candidates[0].finish_reason, "name", None)
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else (
            "max_tokens" if reason == "MAX_TOKENS" else "end_turn")
        tokens_in, tokens_out = self._usage(resp)
        return ProviderResult(data=None, model=resp.model_version or model, content=blocks, stop_reason=stop,
                              input_tokens=tokens_in, output_tokens=tokens_out)
