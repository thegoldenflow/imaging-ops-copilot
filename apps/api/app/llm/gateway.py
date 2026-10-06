"""The single entry point for every Claude call.

Responsibilities: de-identify input, enforce a Pydantic output schema (one
retry, then "needs human review"), degrade on timeouts and API failures, and
log each call (task, model, prompt version, tokens, latency, cost, outcome)
without storing PHI.
"""

import hashlib
import time
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.models import LlmCall, Patient
from app.core.store import get_store
from app.llm.deid import Pseudonymizer
from app.llm.prompts import Prompt
from app.llm.providers import (
    AnthropicProvider,
    InvalidOutput,
    MockProvider,
    ProviderRefusal,
    ProviderResult,
    ProviderUnavailable,
    strict_schema,
)

# USD per million tokens (input, output).
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

Tier = Literal["reasoning", "fast", "voice"]


class LlmOutcome(BaseModel):
    status: Literal["ok", "needs_human", "unavailable"]
    data: dict | None = None
    call_id: str
    model: str
    prompt_version: str
    mode: str
    error: str | None = None


class LlmGateway:
    def __init__(self, provider=None) -> None:
        if provider is None:
            if settings.llm_mode == "anthropic" and settings.anthropic_api_key:
                provider = AnthropicProvider(settings.anthropic_api_key, settings.llm_timeout_s)
            else:
                provider = MockProvider()
        self.provider = provider

    @property
    def mode(self) -> str:
        return self.provider.mode

    @staticmethod
    def model_for(tier: Tier) -> str:
        return {"reasoning": settings.model_reasoning, "fast": settings.model_fast, "voice": settings.model_voice}[tier]

    def _log(self, *, task, model, prompt, text, started, outcome, result: ProviderResult | None = None,
             error=None, input_tokens=0, output_tokens=0) -> LlmCall:
        store = get_store()
        if result is not None:
            input_tokens += result.input_tokens
            output_tokens += result.output_tokens
        price_in, price_out = PRICES.get(model, (0.0, 0.0))
        cost = 0.0 if self.mode == "mock" else (input_tokens * price_in + output_tokens * price_out) / 1e6
        call = LlmCall(
            id=store.next_id("LLM"), ts=datetime.now(), task=task, model=model, mode=self.mode,
            prompt_version=prompt.version, input_tokens=input_tokens, output_tokens=output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000), cost_usd=round(cost, 6), outcome=outcome,
            input_hash=hashlib.sha256(text.encode()).hexdigest()[:16], error=(error or None) and error[:300],
        )
        store.llm_calls.append(call)
        return call

    def structured(
        self,
        *,
        task: str,
        prompt: Prompt,
        variables: dict,
        schema_cls: type[BaseModel],
        tier: Tier = "reasoning",
        images: list[tuple[str, str]] | None = None,  # (base64, media type)
        patients: list[Patient] | None = None,
    ) -> LlmOutcome:
        model = self.model_for(tier)
        pseudo = Pseudonymizer(patients)
        text = pseudo.redact(prompt.render(**variables))
        schema = strict_schema(schema_cls)
        started = time.monotonic()
        tokens_in = tokens_out = 0
        error = None

        for attempt in range(2):
            request_text = text if error is None else (
                f"{text}\n\nYour previous answer failed validation: {error}\nReturn JSON that matches the schema."
            )
            try:
                result = self.provider.complete_json(
                    task=task, model=model, system=prompt.system, text=request_text, schema=schema,
                    images=images, attempt=attempt,
                )
            except ProviderUnavailable as e:
                call = self._log(task=task, model=model, prompt=prompt, text=text, started=started,
                                 outcome="unavailable", error=str(e), input_tokens=tokens_in, output_tokens=tokens_out)
                return LlmOutcome(status="unavailable", call_id=call.id, model=model,
                                  prompt_version=prompt.version, mode=self.mode, error="AI is temporarily unavailable")
            except ProviderRefusal as e:
                call = self._log(task=task, model=model, prompt=prompt, text=text, started=started,
                                 outcome="needs_human", error=str(e), input_tokens=tokens_in, output_tokens=tokens_out)
                return LlmOutcome(status="needs_human", call_id=call.id, model=model,
                                  prompt_version=prompt.version, mode=self.mode, error=str(e))
            except InvalidOutput as e:
                error = f"Invalid JSON: {e}"
                continue
            tokens_in += result.input_tokens
            tokens_out += result.output_tokens
            try:
                parsed = schema_cls.model_validate(result.data)
            except ValidationError as e:
                error = str(e)[:800]
                continue
            call = self._log(task=task, model=model, prompt=prompt, text=text, started=started,
                             outcome="ok" if attempt == 0 else "retried_ok",
                             input_tokens=tokens_in, output_tokens=tokens_out)
            return LlmOutcome(status="ok", data=pseudo.restore(parsed.model_dump(mode="json")), call_id=call.id,
                              model=model, prompt_version=prompt.version, mode=self.mode)

        call = self._log(task=task, model=model, prompt=prompt, text=text, started=started, outcome="needs_human",
                         error=error, input_tokens=tokens_in, output_tokens=tokens_out)
        return LlmOutcome(status="needs_human", call_id=call.id, model=model, prompt_version=prompt.version,
                          mode=self.mode, error="AI output failed validation twice; needs human review")

    def tool_turn(self, *, task: str, prompt: Prompt, messages: list, tools: list, redacted_text: str,
                  tier: Tier = "voice") -> ProviderResult:
        """One model turn of a tool-using agent. Callers redact text before building
        `messages`; this logs the call and re-raises ProviderUnavailable/Refusal."""
        model = self.model_for(tier)
        started = time.monotonic()
        try:
            result = self.provider.tool_turn(model=model, system=prompt.system, messages=messages, tools=tools)
        except (ProviderUnavailable, ProviderRefusal) as e:
            self._log(task=task, model=model, prompt=prompt, text=redacted_text, started=started,
                      outcome="unavailable", error=str(e))
            raise
        self._log(task=task, model=model, prompt=prompt, text=redacted_text, started=started, outcome="ok",
                  result=result)
        return result


_gateway: LlmGateway | None = None


def get_gateway() -> LlmGateway:
    global _gateway
    if _gateway is None:
        _gateway = LlmGateway()
    return _gateway


def set_gateway(gateway: LlmGateway | None) -> None:
    global _gateway
    _gateway = gateway
