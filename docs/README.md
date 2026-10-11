# Docs

| Document | What it is |
| --- | --- |
| [../README.md](../README.md) | Start here: what the demo contains, how to run it, logins, storylines, evals, tests, configuration, troubleshooting |
| [safety-tiers.md](safety-tiers.md) | The three risk tiers with every registered agent and module, the tool side-effect levels, the Level 0–4 mapping, guardrails, honest boundaries and the 14 roadmap modules (generated from the registries; the app's **Safety tiers** and **Roadmap** pages show the same) |
| [interview-narrative.md](interview-narrative.md) | Talk tracks for a medical director, an operations director and a technical lead, each sentence with the screen that proves it; honest boundaries; likely questions |
| [data-model.md](data-model.md) | The hospital's FHIR model: resources, ids, local codes and extensions, the gateway, events, access, signing, agent runtime, workflows, AI Ops, the storyline |
| [PROGRESS.md](PROGRESS.md) | What is built, per phase and work package, with the deviations from the specs, test results and known limits |
| [DEPLOY.md](DEPLOY.md) | Server deployment with Docker Hub images behind Caddy, backups, keys, the optional Temporal services (in Chinese) |
| [SPEC.md](SPEC.md) | The imaging centre's specification: 21 systems in phases 0–4 (in Chinese) |
| [SPEC-hospital.md](SPEC-hospital.md) | The hospital platform extension: phases 6–8, work packages WP0–WP10 (in Chinese) |
| [audit-baseline.md](audit-baseline.md) | WP0's reuse audit, the owner's decisions and the spec-name → code-name map |
| [engineering-notes.md](engineering-notes.md) | Concurrency and database-layer problems found while running the platform (thread-safe table mappings, cancelled queries, lock scope, process caches), each with its cause, fix, evidence and code; open optimizations |
| [RATIONALE-db-temporal.md](RATIONALE-db-temporal.md) | Why PostgreSQL and (later) Temporal, and the options the owner chose from |
| [prompts/translate-kg-terminology.md](prompts/translate-kg-terminology.md) | The prompt for translating knowledge-graph terms to English |

Elsewhere:

- [../demo/README.md](../demo/README.md): the demo scripts, one per module, and the hospital storyline.
- [../evals/README.md](../evals/README.md): every agent's eval set, metric, threshold and latest result (generated).
- Module READMEs, each with the four fixed sections (input resources, output schema, eval set and threshold, known
  failure modes): [agent runtime](../apps/api/app/agents/README.md), [AI Ops](../apps/api/app/aiops/README.md),
  [EHR platform](../apps/api/app/ehr/README.md), [LLM call layer](../apps/api/app/llm/README.md),
  [workflows](../apps/api/app/workflows/README.md), [Control Tower](../apps/api/app/modules/control_tower/README.md)
  and its [flow models](../apps/api/models/flow/README.md), [medication safety](../apps/api/app/modules/med_safety/README.md),
  [documentation](../apps/api/app/modules/documentation/README.md),
  [voice services](../apps/api/app/modules/voice_services/README.md),
  [storyline](../apps/api/app/modules/storyline/README.md), [roadmap and stubs](../apps/api/app/modules/roadmap/README.md),
  [imaging-centre agents](../apps/api/app/modules/README.md).
