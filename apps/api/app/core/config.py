"""Runtime settings, read once from environment variables."""

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


# Verified with this Google Cloud project through the Agent Platform endpoint.
# Used for every tier unless overridden.
GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"


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


def load_settings() -> Settings:
    key = os.getenv("ANTHROPIC_API_KEY") or None
    provider = (os.getenv("LLM_PROVIDER") or os.getenv("LLM_MODE") or ("anthropic" if key else "mock")).strip().lower()
    if provider not in ("anthropic", "gemini", "mock"):
        raise ValueError(f"LLM_PROVIDER must be anthropic, gemini or mock, not {provider!r}")
    return Settings(
        anthropic_api_key=key,
        # GEMINI_API_KEY remains a temporary local migration fallback only. Every
        # Gemini request is still constructed as a Vertex / Agent Platform request.
        google_agent_platform_api_key=(
            os.getenv("GOOGLE_AGENT_PLATFORM_API_KEY") or os.getenv("GEMINI_API_KEY") or None
        ),
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
    )


settings = load_settings()
