# Imaging Ops Copilot

A demo AI toolkit for an outpatient medical imaging center: scheduling, chest X-ray report drafting, front desk automation, requisition triage, radiology operations and compliance. The full scope is 21 systems delivered in 5 phases.

> All data is synthetic. No real patient information is used anywhere. AI output is always a draft or a suggestion that a person must confirm. Nothing in this repository is a medical device or gives diagnostic advice.

## Status

Phase 0 (Backend Infrastructure) is next. See [docs/PROGRESS.md](docs/PROGRESS.md).

## Planned systems

| Phase | Systems |
| --- | --- |
| 0 Foundation | Backend Infrastructure |
| 1 Flagship | Scheduling Command Center, Report Generator, Front Desk Automation |
| 2 Requisition pipeline | Priority Triage, Protocol Assignment, Contrast & Renal Checker, MRI Safety Screening, Patient Prep Instructions, Prior Imaging Retrieval |
| 3 Radiology operations | Reporting Backlog & Turnaround, Critical Results Tracker, Peer Review / QA, CT Dose Monitoring |
| 4 Business & compliance | Inventory, Referral Analytics, Referring Physician Portal, Billing & Claims QA, Patient Feedback, PHIPA Access Monitoring, Inspection Readiness Hub |

## Tech stack

React + TypeScript (Vite, Tailwind, shadcn/ui) · Python 3.12 FastAPI · PostgreSQL · Temporal · Orthanc · Anthropic Claude API · Docker Compose

## Repository layout

```text
apps/
  web/      React frontend
  api/      FastAPI backend
  kg-qa/    Sub-project: medical knowledge-graph Q&A
evals/      Evaluation sets and results
data/seed/  Synthetic data generation and the demo storyline
infra/      docker-compose for Postgres, Temporal, Orthanc
docs/       SPEC.md, PROGRESS.md
```

## Sub-projects

- [apps/kg-qa](apps/kg-qa) — medical knowledge-graph Q&A (LangGraph agent + Neo4j + PostgreSQL), imported from an earlier project together with its history. It runs on its own; see its README (in Chinese).

## Docs

- [docs/SPEC.md](docs/SPEC.md) — full specification (in Chinese)
- [docs/PROGRESS.md](docs/PROGRESS.md) — task list and progress
