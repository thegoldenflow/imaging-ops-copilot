"""LLM providers behind the gateway: the real Anthropic API and a mock.

The mock returns canned, schema-shaped outputs so every AI feature can be
demoed without an API key. The gateway decides which provider to use.
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic


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
