"""LLM providers behind the gateway: the Anthropic API, the Gemini API and a mock.

The mock returns canned, schema-shaped outputs so every AI feature can be
demoed without an API key. The gateway decides which provider to use.

Callers speak one format: Anthropic-style messages and tool definitions, and
content blocks with `type`, `text`, `id`, `name` and `input`. The Gemini
provider converts to and from Gemini's contents and function calls.
"""

import base64
import json
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
    """Output was not valid JSON."""


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


class MockProvider:
    mode = "mock"

    def __init__(self, latency_s: float = 0.6) -> None:
        self.latency_s = latency_s
        self.attempts: dict[str, int] = {}

    def complete_json(self, *, task, model, system, text, schema, images=None, attempt=0) -> ProviderResult:
        time.sleep(self.latency_s)
        fixture = MOCK_FIXTURES.get(task)
        if fixture is None:
            raise ProviderUnavailable(f"No mock fixture for task {task}")
        data = fixture(text, images or [], attempt)
        return ProviderResult(
            data=data, model=f"mock:{model}",
            input_tokens=len(text) // 4 + 1500 * len(images or []), output_tokens=len(json.dumps(data)) // 4,
        )

    def tool_turn(self, **_) -> ProviderResult:
        raise ProviderUnavailable("The mock provider has no tool-use agent; use the scripted agent")


class AnthropicProvider:
    mode = "anthropic"
    # Sonnet 5.5: opt into server-side refusal fallbacks so a declined request
    # is retried on a fallback model instead of failing outright.
    FALLBACK_MODELS = ("claude-sonnet-5-5",)

    def __init__(self, api_key: str, timeout_s: float) -> None:
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=1)

    def _create(self, **kwargs):
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

    def complete_json(self, *, task, model, system, text, schema, images=None, attempt=0) -> ProviderResult:
        content: list[dict] = [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
            for b64, media_type in (images or [])
        ]
        content.append({"type": "text", "text": text})
        resp = self._create(
            model=model,
            max_tokens=8000,
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

    def tool_turn(self, *, model, system, messages, tools) -> ProviderResult:
        resp = self._create(model=model, max_tokens=1024, system=system, messages=messages, tools=tools)
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


class GeminiProvider:
    """Google Gemini through the Agent Platform / Vertex AI google-genai route."""

    mode = "gemini"
    REFUSAL_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY",
                       "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION"}

    def __init__(self, api_key: str, timeout_s: float, client=None) -> None:
        self.client = client or genai.Client(
            vertexai=True,
            api_key=api_key,
            http_options=genai_types.HttpOptions(
                api_version="v1",
                timeout=int(timeout_s * 1000),
                retry_options=genai_types.HttpRetryOptions(attempts=2),
            ),
        )

    def _generate(self, *, model, contents, config):
        try:
            return self.client.models.generate_content(model=model, contents=contents, config=config)
        except genai_errors.APIError as e:  # 4xx incl. 429 rate limit, 5xx
            code = int(e.code or 0)
            if code in (401, 403):
                hint = "authentication or permission error: check the API key and API access"
            elif code == 404:
                hint = "model or endpoint not found: check the model and Vertex endpoint"
            elif code == 429:
                hint = "rate or quota limit reached"
            elif code == 402:
                hint = "payment required: check billing on the Google Cloud project"
            else:
                hint = "request failed"
            # Keep Google's own message: it names the actual cause.
            raise ProviderUnavailable(f"Agent Platform {code} {hint}. Google: {e.message or e.status}") from e
        except (httpx.HTTPError, OSError) as e:  # timeouts and network failures
            raise ProviderUnavailable(f"Connection error: {type(e).__name__}: {e}") from e

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

    def complete_json(self, *, task, model, system, text, schema, images=None, attempt=0) -> ProviderResult:
        contents: list = [
            genai_types.Part.from_bytes(data=base64.b64decode(b64), mime_type=media_type)
            for b64, media_type in (images or [])
        ]
        contents.append(text)
        resp = self._generate(model=model, contents=contents, config=genai_types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=8000,
            response_mime_type="application/json",
            response_json_schema=schema,
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
        ))
        parts = self._parts(resp)
        body = "".join(p.text for p in parts if p.text and not p.thought)
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            raise InvalidOutput(str(e)) from e
        tokens_in, tokens_out = self._usage(resp)
        return ProviderResult(data=data, model=resp.model_version or model,
                              input_tokens=tokens_in, output_tokens=tokens_out)

    def tool_turn(self, *, model, system, messages, tools) -> ProviderResult:
        resp = self._generate(model=model, contents=gemini_contents(messages), config=genai_types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=1024,
            tools=gemini_tools(tools),
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
        ))
        blocks = blocks_from_gemini(self._parts(resp))
        reason = _get(resp.candidates[0].finish_reason, "name", None)
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else (
            "max_tokens" if reason == "MAX_TOKENS" else "end_turn")
        tokens_in, tokens_out = self._usage(resp)
        return ProviderResult(data=None, model=resp.model_version or model, content=blocks, stop_reason=stop,
                              input_tokens=tokens_in, output_tokens=tokens_out)
