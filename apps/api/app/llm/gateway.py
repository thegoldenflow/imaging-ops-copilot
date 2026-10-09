"""The single entry point for every LLM call (Claude, Gemini or the mock).

Responsibilities: de-identify input, enforce a Pydantic output schema (one
retry, then "needs human review"), degrade on timeouts and API failures, and
log each call (task, model, prompt version, tokens, latency, cost, outcome)
without storing PHI. Each call is also an `ai_call` audit event (spec 6.3) with
the task as module and the prompt version.
"""

import hashlib
import time
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.context import request_context
from app.core.models import LlmCall, Patient
from app.core.store import get_store
from app.llm.deid import Pseudonymizer
from app.llm.prompts import Prompt
from app.llm.providers import (
    AnthropicProvider,
    GeminiProvider,
    InvalidOutput,
    MockProvider,
    ProviderRefusal,
    ProviderResult,
    ProviderUnavailable,
    strict_schema,
)

# USD per million tokens (input, output), standard tier.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    # Vertex AI global standard pricing, USD per million tokens, checked 2026-10-07.
    # Output includes reasoning tokens.
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3.5-flash": (1.50, 9.00),
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


def make_provider():
    """Provider from LLM_PROVIDER; a provider whose API key is missing runs as mock."""
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(settings.anthropic_api_key, settings.llm_timeout_s)
    if settings.llm_provider == "gemini" and settings.google_agent_platform_api_key:
        return GeminiProvider(settings.google_agent_platform_api_key, settings.llm_timeout_s)
    return MockProvider()


class LlmGateway:
    def __init__(self, provider=None) -> None:
        if provider is None:
            provider = make_provider()
        self.provider = provider

    @property
    def mode(self) -> str:
        """The vendor actually used: anthropic, gemini or mock."""
        return self.provider.mode

    def model_for(self, tier: Tier) -> str:
        if self.mode == "gemini":
            return {"reasoning": settings.gemini_model_reasoning, "fast": settings.gemini_model_fast,
                    "voice": settings.gemini_model_voice}[tier]
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
        self._audit(store, call)
        return call

    @staticmethod
    def _audit(store, call: LlmCall) -> None:
        """An `ai_call` audit event (6.3) for the person whose request made the call, or the system."""
        ctx = request_context()
        user = ctx.user if ctx else None
        store.audit.record(
            user_id=user.id if user else "system", user_name=user.name if user else "system",
            role=str(user.role) if user else "system", action="ai_call", event_type="ai_call",
            resource_type="LlmCall", resource_id=call.id, outcome=call.outcome,
            source_ip=ctx.source_ip if ctx else None, reason=f"{call.mode}:{call.model}", module=call.task,
            prompt_version=call.prompt_version)

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
        pseudonymizer: Pseudonymizer | None = None,
    ) -> LlmOutcome:
        """`pseudonymizer`: the variables were de-identified by the caller with it (FHIR resources through
        app/llm/fhir_deid.py, notes through app/llm/freetext_deid.py). The gateway then repeats only the
        replacement of the identifiers it knows, not its own patterns (which would also tokenise the clinical
        dates of a resource), and re-identifies the output with the same map."""
        model = self.model_for(tier)
        if pseudonymizer is not None:
            pseudo = pseudonymizer
            text = pseudo.redact_known(prompt.render(**variables))
        else:
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
