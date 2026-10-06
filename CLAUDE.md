# CLAUDE.md

Read `docs/SPEC.md` in full before starting any work. It is the source of truth for scope, phases, the shared data model and acceptance criteria. Track progress in `docs/PROGRESS.md`.

## Repository layout

- `apps/web/` — React + TypeScript frontend; one folder per system under `src/features/<system>/`
- `apps/api/` — FastAPI backend: `app/core`, `app/llm`, `app/modules/<system>`, `app/integrations`, `app/workflows`
- `apps/kg-qa/` — sub-project (medical knowledge-graph Q&A), see below
- `evals/`, `data/seed/`, `infra/`, `docs/`

Folders other than `apps/kg-qa/` and `docs/` are created as phase 0 progresses.

## Workflow

1. Before starting a phase, list its tasks in `docs/PROGRESS.md`.
2. Build phases 0 → 4 in order. Do not start a phase until the previous one passes its acceptance criteria.
3. Backend code for a system goes in `apps/api/app/modules/<system>/`, frontend code in `apps/web/src/features/<system>/`.
4. A system is done when its acceptance checkboxes pass, its unit tests and at least one Playwright end-to-end test pass, and `docs/PROGRESS.md` is updated. Commit each system separately.
5. Changing the core data model or the tech stack needs a written rationale and the owner's confirmation first.

## Hard rules

- Synthetic data only. No real patient data (PHI) anywhere in the repo.
- AI output is always a suggestion or draft that a person confirms. The UI marks AI-generated content.
- Every Claude call goes through the LLM call layer (de-identification, schema validation, call logging, degradation). Model IDs come from environment variables.
- Non-AI features keep working when the Claude API is unavailable.
- External systems (RIS, PACS, OHIP, phone, SMS, email, outside image archives) are local mocks.
- UI, code, comments and commit messages are in English. Patient-facing content is multilingual.
- API keys live only in `.env`, which is never committed.
- The product name is neutral (Imaging Ops Copilot). Never use GNMI's name, logo or real clinic addresses.

## Sub-project: apps/kg-qa

Imported with its git history from the earlier AIMed repo. It has its own README (Chinese), `docker-compose.yml` and `requirements.txt`, and uses DeepSeek + Neo4j. It is not one of the 21 systems in the spec. Leave it unchanged unless a task is explicitly about it. Integrating it into the suite (for example routing its LLM calls through the Claude call layer) is a stack change and needs confirmation first.

`apps/kg-qa/data/knowledge_graph/medical_kg.jsonl` is intentionally kept out of git.

## Cloud sessions

The Docker daemon is not started automatically. Start it with `dockerd > /tmp/dockerd.log 2>&1 &` and wait until `docker info` succeeds.
