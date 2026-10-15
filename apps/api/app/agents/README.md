# Agent runtime and Tool Gateway (spec 6.4)

Every AI function runs as a registered agent calling registered tools. The registry declares what an agent may read
and write, the Tool Gateway checks every call (agent registered and deployable, tool on the allow-list, the caller's
role, data scope, consent, the side-effect level's precondition), and every run leaves a trace and, for runtime
agents, a FHIR Provenance. Web: **AI agents** (admin: registries, tools, recent runs with their traces) and the
review queue (`/reviews`). API: `/api/agents/...` (`router.py`).

| File | What it does |
| --- | --- |
| `registry.py` | Loads `config/agents/*.yaml` (35 entries) and `config/tools/*.yaml` (53 tools), refuses inconsistent entries; the release gate's effective `eval_status` (`app/aiops/release.py`); `APP_MODE` demo / prod |
| `gateway.py`, `handlers.py` | The Tool Gateway: checks in the spec's order, idempotency key, retries, fallback, approvals for privileged tools; handlers bind tools to FhirGateway, signing, consent and the event bus |
| `runtime.py` | `runtime.start(agent, user, encounter)`: context through read tools only, de-identified (`fhir_deid` + `freetext_deid`), the model through the LLM gateway, trace and Provenance |
| `approvals.py`, `reviews.py` | Approval and review Tasks (`approval-request`, `ai-review`, `workflow-signoff`), decisions with an `approve` audit event; low-confidence reviews (6.5) |
| `untrusted.py` | The `<untrusted>` block for patient and outside text |
| `trace.py` | `AgentTrace` (table `agent_traces`), idempotency records |
| `library/patient_message_triage.py` | The runtime's reference agent; `library/journey_stubs.py` the journey's remaining placeholders |
| `injection_eval.py`, `evalsets.py` | The prompt-injection harness; per-agent eval sets and regression for the low-confidence loop |
| `ondemand.py`, `cause.py`, `activity.py`, `ondemand_router.py` | AI on demand (`BACKGROUND_AI`): the kinds of item that get AI output, their shared AI state, the job loop's queue and batches, who caused each run (`cause` on the trace), and "What the AI did in this demo" (`/api/ai/...`) |

AI on demand: with `BACKGROUND_AI=on_demand` (the demo's default) no model call is made for work nobody asked for.
An item's AI runs when a person opens it or presses Ask AI (in the request, `ondemand.run_now`), or at once for new
work their action created and for a batch they asked for (`ondemand.request`, run by the API's AI job loop, two at a
time). One run per item at a time: a second caller waits for the first in this process, and the kinds' own locks
cover other processes. Each run's trace records its cause: the browser's demo session (`X-Demo-Session`), the person,
the trigger (opened, asked, action, batch, storyline) and the item, also when the agent writes as itself.

## Input resources

- The two registries: an agent's `allowed_tools`, `data_scope` (resource types, scope encounter / own unit /
  hospital), `consent_required`, `model_policy.tier` (model ids come from the environment through
  `config/models.yaml`), `prompt_version`, `required_signoff_role`, `confidence_threshold`, `eval_threshold`. A
  tool's `side_effect_level`, input and output JSON Schema, `permission_policy` (caller roles, approver roles,
  target argument), idempotency, timeout, retry, audit level.
- The caller: the signed-in user (role, units, never their break-glass grant) or the agent acting as itself.
- FHIR through the read tools only (getEncounterContext, getBedBoard, getOrders, getMedicationContext, ...);
  agent code may not import FhirGateway, the FHIR store, the database or an HTTP client (grep test).
- Patient and outside text (messages, call transcripts, uploaded notes) as untrusted text.

## Output schema

- `ToolResult` (`gateway.py`): `status` ok / replayed / refused / failed / fallback / approval_required, `output`
  (validated against the tool's output schema), `reason`, `refs`, `idempotency_key`, `approval_task_id`,
  `needs_human_task_id`.
- `AgentTrace` (`trace.py`): run id, agent and version, model, prompt version, eval status, actor, encounter,
  input refs, LLM calls (tokens, cost, latency, status), tool calls (level, args hash, status, attempts, result refs,
  idempotency key, approval Task), output refs, confidence, human action, outcome, cost, Provenance id.
- FHIR `Provenance` `prov-<run_id>` on a runtime agent's output: target, the agent as a Device reference with its
  version, on behalf of the user's Practitioner, inputs as entities, prompt version / model / run id extensions;
  signers added as verifiers. `GET /api/agents/lineage?ref=` goes from any output back to inputs, model and prompt.
- The reference agent's model output: `TriageOutput` in `library/patient_message_triage.py` (summary, category,
  urgency, reply draft, confidence, tool requests); the red flags come from the rule layer before the model.
- An item's AI block (`ondemand.view`), the same for every kind: `state` not_requested / queued / running / done /
  unavailable / skipped, `auto` (opening runs it), `typical_s`, the running `step` of `steps`, `generated_at`,
  `duration_ms`, `cause`, `error`. `GET /api/ai/activity?scope=session|all`: the AI actions (runs grouped by the
  cause's action id) with what, item, trigger, person, time, duration, calls and cost (mock: the list-price estimate),
  what is running and queued, totals. `POST /api/ai/batch`, `GET /api/ai/kinds`.

## Eval set and threshold

- Prompt injection: `evals/injection/cases.jsonl` (38 cases over the runtime agents, the voice agents included),
  harness `injection_eval.py`, runner `scripts/injection_eval.py`, README in that folder. Gate: no attack call
  executed (the backend suite runs it; the eval runner adds an `injection_refused` gate to each agent it covers).
- Reference agent: `evals/patient_message_triage/cases.jsonl` (12 authored cases plus any added by the
  low-confidence loop), judged on category and urgency.
- Gateway behaviour: `tests/test_tool_gateway.py` (one positive and one refusal per side-effect level, idempotency,
  privileged approvals, retries, fallback), `tests/test_agent_runtime.py`, `tests/test_agent_registry.py`.
- AI on demand: `tests/test_ai_on_demand.py` (no call until something is opened, one run per item, a submitted
  requisition and a survey answer run at once as the person's action, batches, retry, the cause through
  `acting_as_system`, the activity of a session and of everyone) and `tests/test_background_ai.py`; Playwright
  `ai-on-demand.spec.ts`. Bar: a fresh data set makes 0 model calls while people only browse lists.

Release gate (6.6): `patient_message_triage` eval_threshold 0.9 in the agent registry (`apps/api/config/agents/`);
the runner `uv run python -m app.aiops.evalrun` judges every agent against it, and the latest results of every agent
are in [evals/README.md](../../../../evals/README.md).

## Known failure modes

- The imaging modules' AI calls are embedded agents: registered, gated and traced (one-call traces), but their
  output is a module record, not a FHIR resource, so they get no Provenance, and the grep rule does not cover them
  (they use the store directly).
- The idempotency window is 24 hours on the wall clock and keeps only successful results: a refused call is decided
  again next time.
- `timeout_ms` is checked after the fact for reads (a late read is discarded and retried); a write keeps a late
  result, since in-process work is not cancelled.
- Every tool call writes one or two audit events (short transactions, about 2 ms each locally) on top of
  FhirGateway's own.
- The injection eval assumes a fully compromised model and checks that the gateway refuses what it asks for; it does
  not measure how often a real model falls for an injection. The triage mock is written to fall for injections, so
  the refusals are visible in the demo.
- The low-confidence loop learns for `patient_message_triage` only (the only agent with a confidence and a
  threshold); its regression judges category and urgency.
- AI on demand keeps its running and queued jobs, its measured typical times and the causes of follow-up work (a
  signed summary's instructions) in process memory: a restart drops queued jobs (the items then wait for someone to
  open them) and the API must run one worker (docs/DEPLOY.md). A reconciliation whose AI was unavailable keeps the
  rules' fallback; Ask AI does not run it again.
