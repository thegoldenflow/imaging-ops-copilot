# Demo: AI Ops — registries, Eval Center, release gate, observability and cost (spec 6.6)

About 4 minutes. For the technical lead (second round): "One command runs every agent's eval set; a prompt
change can't merge without an eval run attached."

## Before the demo

- API with background workers (the agents produce traces as the hospital runs), mock model:
  `cd apps/api && LLM_PROVIDER=mock uv run uvicorn app.main:app --port 9437` and the web app.
- Optional: `cd apps/api && uv run python scripts/ai_ops_demo.py --commit` adds three patient messages (one with a
  prompt injection the gateway refuses) and checks the alerts once, so the page has failures and an alert Task
  right away. Without it, open an order review or a Control Tower exception first: with `BACKGROUND_AI=on_demand`
  (the default) the AI runs only when someone opens an item or asks (`BACKGROUND_AI=eager` runs the backlog in the
  background after each reset, as before). The header's **AI activity** lists each run with what caused it.
- The eval runs are committed (`evals/runs/`): the full mock baseline and the candidate
  `patient_message_triage@2`. To run them again: `cd apps/api && uv run python -m app.aiops.evalrun` (about 5
  minutes, every agent) and `uv run python -m app.aiops.evalrun --prompt patient_message_triage@2`.

## Talk track

1. **One command** (terminal, 30 s). `uv run python -m app.aiops.evalrun` (or `make eval`): every registered agent,
   one run file in `evals/runs/`. Each agent's eval set stayed where its work package put it; the runner adapts
   them (no rewrite) and records the same six metrics for all: schema validity, grounding, task success, human
   override from the production traces, latency p50/p95, cost per case. Mock runs are baselines and say so.
2. **AI Ops → Overview** (admin). Six cards for today, hospital-wide: agent runs, cost (≈: mock calls priced at the
   model's list price, nothing billed), latency p95, failure rate, human override, adoption. Below: escalation,
   Task completion, cost per completed workflow. The agent table: version, eval status (derived from the latest
   run, "from run"), pass rate against the agent's bar, override rate, p95, cost. Every number is a trace aggregate
   (a backend test greps the page for hardcoded numbers).
3. **A failure to its trace — two clicks**. In the failure list click the patient message triage run with refused
   tool calls: the trace shows model, prompt version and its header, the inputs and outputs (references only), the
   refused `appendEncounterLocation` / `signDocumentFinal`. Switch the dimension table between model, unit and day.
4. **Eval Center — three clicks**. Eval Center → `patient_message_triage` → the failing case `authored-parking`:
   expected "other", got "administrative", and the trace captured during the eval (its writes were rolled back).
   "Cases (common format)" shows `input_refs`, `expected` and `source` (authored / human_review — the low-confidence
   workflow appends corrected cases here and runs the regression through the same runner).
5. **Compare**. `patient_message_triage@1` against the candidate `@2` (a shorter template without the rule layer's
   red flags): task success drops, and the per-case diff shows `authored-fall` regressed — "fell in the bathroom"
   filed as non-clinical. Same view for two models (`--model claude-fast`).
6. **The gates** (Registries tab). Models from `config/models.yaml` with the ids read from the environment and what
   each is approved for (the voice model: operations only). Prompts with their four header lines; `@2` is a
   candidate. Pointing the agent at `@2` fails the prompt gate (`uv run python -m app.aiops.promptgate`, also a
   backend test): its run is below the bar of 0.9. Rollback is the one line `prompt_version` in the agent's YAML.
   A run below the threshold makes the agent `failed`: the UI flag turns into **Eval failed** in demo mode, prod
   mode refuses the agent.
7. **Alerts**. Alerts tab → Check now: today's cost passes the demo threshold in `config/alerts.yaml`, and an admin
   Task opens (one per alert, scope and day, through the Tool Gateway). Resolve it as the admin.

## Honest limits

- The runs are mock baselines: the numbers prove the pipeline (context, schema, guards, evidence, gates), not a
  model's quality. A live run (`--live`) is what can make an agent `passed`.
- Five agents have no eval set yet (cxr_draft, prep_translation, voice_agent, call_summary, and followup_calls
  until WP8 is merged); their prompts are pinned to their recorded text, not judged.

## Transcript (scripts/ai_ops_demo.py)

Run 2026-10-10 on the demo database (mock model, rolled back); regenerate with `uv run python scripts/ai_ops_demo.py`.

```text
== Model registry: config/models.yaml (ids from the environment) ==
  claude-reasoning  anthropic reasoning claude-sonnet-5-5    tiers ops,documentation,clinical_ds  max $0.25/case · 11 agents
  claude-fast       anthropic fast      claude-haiku-4-5     tiers ops,documentation,clinical_ds  max $0.05/case · 9 agents
  claude-voice      anthropic voice     claude-haiku-4-5     tiers ops                            max $0.05/case · 2 agents
  gemini-reasoning  gemini    reasoning gemini-3.5-flash     tiers ops,documentation,clinical_ds  max $0.25/case · 11 agents
  gemini-fast       gemini    fast      gemini-3.5-flash     tiers ops,documentation,clinical_ds  max $0.05/case · 9 agents
  gemini-voice      gemini    voice     gemini-3.5-flash     tiers ops                            max $0.05/case · 2 agents
  order_review (clinical_ds) on the voice model -> order_review: model claude-voice is not approved for the clinical_ds tier; order_review: model gemini-voice is not approved for the clinical_ds tier

== Prompt registry and gate: every prompt a file with author, change_note, approved_by, eval_run ==
  22 prompt versions, 0 failing the gate; 4 of them for agents without an eval set (pinned to their run's text)
  point patient_message_triage at patient_message_triage@2 -> gate FAILS: run-2026-10-10T043241: task_success 0.8333 below the threshold 0.9
  rollback = prompt_version back to patient_message_triage@1 in config/agents/patient_message_triage.yaml -> gate passes

== Eval Center: the latest run per agent (evals/runs) and the release gate ==
  call_summary               no_eval_set task_success –       bar 0.8   -> eval_status pending  (run-2026-10-10T042224, not run)
  clinical_kg_answer         met         task_success 100.0%  bar 0.9   -> eval_status pending  (run-2026-10-10T042224, mock)
  clinical_kg_parse          met         task_success 100.0%  bar 0.9   -> eval_status pending  (run-2026-10-10T042224, mock)
  control_tower              met         task_success 100.0%  bar 0.9   -> eval_status pending  (run-2026-10-10T042224, mock)
  cxr_draft                  no_eval_set task_success –       bar 0.8   -> eval_status pending  (run-2026-10-10T042224, not run)
  deid_check                 met         task_success 93.5%   bar 0.9   -> eval_status passed   (run-2026-10-10T042224, mock)
  discharge_summary          met         task_success 100.0%  bar 0.9   -> eval_status pending  (run-2026-10-10T042224, mock)
  feedback_classify          met         task_success 78.3%   bar 0.75  -> eval_status pending  (run-2026-10-10T042224, mock)
  followup_calls             no_eval_set task_success –       bar 0.8   -> eval_status pending  (run-2026-10-10T042224, not run)
  medication_reconciliation  met         task_success 100.0%  bar 0.9   -> eval_status pending  (run-2026-10-10T042224, mock)
  mri_implant_extract        met         task_success 96.7%   bar 0.9   -> eval_status pending  (run-2026-10-10T042224, mock)
  nursing_handoff            met         task_success 100.0%  bar 0.9   -> eval_status pending  (run-2026-10-10T042224, mock)
  order_review               met         task_success 85.0%   bar 0.8   -> eval_status pending  (run-2026-10-10T042224, mock)
  patient_instructions       met         task_success 100.0%  bar 0.9   -> eval_status pending  (run-2026-10-10T042224, mock)
  patient_message_triage     met         task_success 91.7%   bar 0.9   -> eval_status pending  (run-2026-10-10T042224, mock)
  policy_qa                  met         task_success 85.0%   bar 0.8   -> eval_status pending  (run-2026-10-10T042224, mock)
  prep_translation           no_eval_set task_success –       bar 0.8   -> eval_status pending  (run-2026-10-10T042224, not run)
  protocol_suggest           met         task_success 95.0%   bar 0.9   -> eval_status pending  (run-2026-10-10T042224, mock)
  referral_weekly_summary    met         task_success 100.0%  bar 1.0   -> eval_status pending  (run-2026-10-10T042224, mock)
  requisition_extract        met         task_success 78.0%   bar 0.75  -> eval_status pending  (run-2026-10-10T042224, mock)
  requisition_triage         met         task_success 90.0%   bar 0.85  -> eval_status pending  (run-2026-10-10T042224, mock)
  voice_agent                no_eval_set task_success –       bar 0.8   -> eval_status pending  (run-2026-10-10T042224, not run)

== Comparison: patient_message_triage@1 against the candidate @2 ==
  task_success         0.9167     0.8333    
  schema_validity      1.0        1.0       
  latency_p95_ms       16         31        
  cost_per_case_usd    0.000521   0.000476  
  authored-fall: @1 pass {'category': 'clinical', 'urgency': 'urgent'} -> @2 fail {'category': 'other', 'urgency': 'urgent'}

== Agent runs leave traces (the AI Ops page reads nothing else) ==
  RUN-03187: scheduling/routine, 799 tokens
  RUN-03188: clinical/urgent, 808 tokens
  RUN-03189: clinical/urgent, 969 tokens, refused appendEncounterLocation, signDocumentFinal
  today: 85 runs, 70 model calls, cost ≈$0.4086, p95 2636 ms, failure rate 11.8%, override 33.3%
  failure RUN-03189 patient_message_triage: 2 tool call(s) refused by the gateway
  failure RUN-03179 order_review: outcome refused; 2 tool call(s) refused by the gateway
  failure RUN-03173 medication_reconciliation: outcome refused; 1 tool call(s) refused by the gateway

== Alerts: config/alerts.yaml (demo thresholds) -> admin Task ==
  agent_daily_cost agent control_tower: cost_usd 0.0734 > 0.01 -> open already
  agent_daily_cost agent medication_reconciliation: cost_usd 0.0337 > 0.01 -> open already
  agent_daily_cost agent order_review: cost_usd 0.2879 > 0.01 -> open already
  hospital_daily_cost hospital hospital: cost_usd 0.4086 > 0.05 -> open already
  Task/task-00740 (requested, urgent): AI Ops alert: An agent's cost today is above its daily budget. Agent medication_reconciliation: cost_usd 0.0184 USD (list-price estimate: mo...
  Task/task-00706 (requested, urgent): AI Ops alert: An agent's cost today is above its daily budget. Agent control_tower: cost_usd 0.0287 USD (list-price estimate: mock calls bil...
  Task/task-00707 (requested, urgent): AI Ops alert: An agent's cost today is above its daily budget. Agent order_review: cost_usd 0.0359 USD (list-price estimate: mock calls bill...

Rolled back (use --commit to keep).
```
