# Imaging Ops Copilot API. Build from the repository root:
#   docker build -f apps/api/Dockerfile .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    CLINICAL_KG_DIR=/srv/kg-data/knowledge_graph \
    EVALS_DIR=/srv/evals

WORKDIR /app

# Dependencies first so code changes don't reinstall them.
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/api/app ./app
# Agent and tool registries and other configuration the gateways read (6.4), and the
# versioned prompt files of the runtime agents.
COPY apps/api/config ./config
COPY apps/api/prompts ./prompts
# Read-only data the app loads at startup: the knowledge graph (with the bilingual
# terminology next to it) and the eval datasets/results shown on the AI evaluations page.
COPY apps/kg-qa/data/knowledge_graph /srv/kg-data/knowledge_graph
COPY apps/kg-qa/data/terminology /srv/kg-data/terminology
COPY evals /srv/evals

RUN useradd --system --uid 10001 app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# One worker is enough for the demo. Data and sessions are in PostgreSQL and background steps take
# row or advisory locks, but per-process caches (no-show model, search indexes) would be built per worker.
# Migrations run and an empty database is seeded at startup.
# --proxy-headers so the audit log records the client IP, not the reverse proxy's.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
