"""Runtime settings, read once from environment variables."""

import logging
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env support: KEY=VALUE lines; real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


_load_dotenv(Path(__file__).resolve().parents[2] / ".env")


# Verified with this Google Cloud project through the Agent Platform endpoint
# after a successful gemini-2.5-flash smoke test. Used for every tier unless overridden.
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash"


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    google_agent_platform_api_key: str | None
    # anthropic, gemini or mock, from LLM_PROVIDER (LLM_MODE is the older name).
    # Unset: "anthropic" when ANTHROPIC_API_KEY is present, otherwise "mock".
    # A provider without its key also runs as mock.
    llm_provider: str
    model_reasoning: str  # vision and reasoning tasks (Sonnet tier)
    model_fast: str  # simple extraction / classification (Haiku tier)
    model_voice: str  # latency-sensitive voice agent
    gemini_model_reasoning: str
    gemini_model_fast: str
    gemini_model_voice: str
    llm_timeout_s: float
    seed: int
    # When set, demo login requires this passcode (for public deployments).
    demo_passcode: str | None
    cors_origins: list[str]
    database_url: str
    # Run the background workers (message dispatch, intake pipeline, escalations). Tests turn them off.
    background_workers: bool
    # Hour of day (0-23) at which the demo data is regenerated once a day; unset: never.
    demo_daily_reset_hour: int | None
    # Hospital EHR behind FhirGateway: "local" (FHIR store in PostgreSQL) or "hapi" (a FHIR server).
    fhir_backend: str
    fhir_base_url: str
    # "none" (local HAPI) or "smart_backend" (SMART Backend Services, defined but not implemented).
    fhir_auth_mode: str
    fhir_client_id: str | None
    fhir_token_url: str | None


def load_settings() -> Settings:
    if os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_AGENT_PLATFORM_API_KEY"):
        # A Google AI Studio key does not work on the Vertex AI endpoint; say so instead of a bare 401.
        logging.getLogger(__name__).warning(
            "GEMINI_API_KEY is ignored: Gemini runs through Vertex AI and needs GOOGLE_AGENT_PLATFORM_API_KEY")
    key = os.getenv("ANTHROPIC_API_KEY") or None
    provider = (os.getenv("LLM_PROVIDER") or os.getenv("LLM_MODE") or ("anthropic" if key else "mock")).strip().lower()
    if provider not in ("anthropic", "gemini", "mock"):
        raise ValueError(f"LLM_PROVIDER must be anthropic, gemini or mock, not {provider!r}")
    fhir_backend = os.getenv("FHIR_BACKEND", "local").strip().lower()
    if fhir_backend not in ("local", "hapi"):
        raise ValueError(f"FHIR_BACKEND must be local or hapi, not {fhir_backend!r}")
    fhir_auth_mode = os.getenv("FHIR_AUTH_MODE", "none").strip().lower()
    if fhir_auth_mode not in ("none", "smart_backend"):
        raise ValueError(f"FHIR_AUTH_MODE must be none or smart_backend, not {fhir_auth_mode!r}")
    return Settings(
        anthropic_api_key=key,
        google_agent_platform_api_key=os.getenv("GOOGLE_AGENT_PLATFORM_API_KEY") or None,
        llm_provider=provider,
        model_reasoning=os.getenv("CLAUDE_MODEL_REASONING", "claude-sonnet-5-5"),
        model_fast=os.getenv("CLAUDE_MODEL_FAST", "claude-haiku-4-5"),
        model_voice=os.getenv("CLAUDE_MODEL_VOICE", "claude-haiku-4-5"),
        gemini_model_reasoning=os.getenv("GEMINI_MODEL_REASONING", GEMINI_DEFAULT_MODEL),
        gemini_model_fast=os.getenv("GEMINI_MODEL_FAST", GEMINI_DEFAULT_MODEL),
        gemini_model_voice=os.getenv("GEMINI_MODEL_VOICE", GEMINI_DEFAULT_MODEL),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "30")),
        seed=int(os.getenv("SEED", "42")),
        demo_passcode=os.getenv("DEMO_PASSCODE") or None,
        cors_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
        database_url=os.getenv("DATABASE_URL", "postgresql+psycopg://ioc:ioc@127.0.0.1:5433/ioc"),
        background_workers=os.getenv("BACKGROUND_WORKERS", "1") != "0",
        demo_daily_reset_hour=int(h) if (h := os.getenv("DEMO_DAILY_RESET_HOUR", "").strip()) else None,
        fhir_backend=fhir_backend,
        fhir_base_url=os.getenv("FHIR_BASE_URL", "http://localhost:8080/fhir").rstrip("/"),
        fhir_auth_mode=fhir_auth_mode,
        fhir_client_id=os.getenv("FHIR_CLIENT_ID") or None,
        fhir_token_url=os.getenv("FHIR_TOKEN_URL") or None,
    )


settings = load_settings()
