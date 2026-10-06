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


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    # "anthropic" when a key is present, otherwise "mock". Can be forced with LLM_MODE.
    llm_mode: str
    model_reasoning: str  # vision and reasoning tasks (Sonnet tier)
    model_fast: str  # simple extraction / classification (Haiku tier)
    model_voice: str  # latency-sensitive voice agent
    llm_timeout_s: float
    seed: int
    # When set, demo login requires this passcode (for public deployments).
    demo_passcode: str | None
    cors_origins: list[str]


def load_settings() -> Settings:
    key = os.getenv("ANTHROPIC_API_KEY") or None
    mode = os.getenv("LLM_MODE") or ("anthropic" if key else "mock")
    return Settings(
        anthropic_api_key=key,
        llm_mode=mode,
        model_reasoning=os.getenv("CLAUDE_MODEL_REASONING", "claude-sonnet-5-5"),
        model_fast=os.getenv("CLAUDE_MODEL_FAST", "claude-haiku-4-5"),
        model_voice=os.getenv("CLAUDE_MODEL_VOICE", "claude-haiku-4-5"),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "30")),
        seed=int(os.getenv("SEED", "42")),
        demo_passcode=os.getenv("DEMO_PASSCODE") or None,
        cors_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","),
    )


settings = load_settings()
