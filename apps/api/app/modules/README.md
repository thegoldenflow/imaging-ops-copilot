# Imaging-centre AI agents (phases 1–4)

The outpatient imaging centre's thirteen AI steps are embedded agents: each is one structured call through the LLM
gateway (`app/llm/README.md`) inside its module, registered in `apps/api/config/agents/` with a risk tier and a
signer, gated by the release gate and traced as a one-call trace. Their output is a module record a person confirms,
not a FHIR resource. This page gives the four fixed sections for all of them in one place; the hospital modules have
their own READMEs (`control_tower/`, `med_safety/`, `documentation/`, `voice_services/`, `storyline/`, `roadmap/`,
`app/workflows/`, `app/agents/`, `app/aiops/`, `app/ehr/`, `app/llm/`). The imaging modules without a model (systems
2, 7, 10–15, 17, 18, 20 and the rest of 21) have no README of their own: their behaviour and limits are in
`docs/PROGRESS.md`, phases 1–4.

## Input resources

| Agent | Module | Input |
| --- | --- | --- |
| `requisition_extract` | `requisitions/` (System 5 intake) | Requisition text: a structured form or a free-text letter (30% of the generated set) |
| `requisition_triage` | `triage/` (System 5) | The extraction and the requisition text |
| `protocol_suggest` | `protocols/` (System 6) | The extraction and the protocols retrieved from the library (22 protocols) |
| `cxr_draft` | `reports/` (System 3) | A chest X-ray image (synthetic PNG phantom or an upload) and the exam |
| `mri_implant_extract` | `mri_safety/` (System 8) | The questionnaire's free-text implant description (4 languages) |
| `prep_translation` | `prep/` (System 9) | The approved English preparation text and the target language |
| `voice_agent`, `call_summary` | `frontdesk/` (System 4) | Caller turns, de-identified (names and spoken dates as placeholders); the call's actions for the summary |
| `feedback_classify` | `feedback/` (System 19) | A survey comment (English, French, Punjabi, Chinese) |
| `policy_qa` | `inspection/` (System 21) | A staff question and the policy sections retrieved by keyword |
| `referral_weekly_summary` | `referrals/` (System 16) | The week's computed facts (no raw records) |
| `clinical_kg_parse`, `clinical_kg_answer` | `clinical_kg/` | A question (English or Chinese) and the knowledge-graph facts the fixed templates return |

## Output schema

| Agent | Schema (Pydantic) | Confirmed by |
| --- | --- | --- |
| `requisition_extract` | `Extraction` (`requisitions/extraction.py`): each field with its source quote and confidence | intake staff (corrections audited) |
| `requisition_triage` | `TriageOutput` (`triage/service.py`): P1–P4, rationale, red flags | radiologist (override needs a reason) |
| `protocol_suggest` | `ProtocolOutput` (`protocols/service.py`): a primary and two alternatives from the library | radiologist |
| `cxr_draft` | `CxrDraft` (`reports/service.py`): fixed report sections | radiologist, section by section, then sign-off |
| `mri_implant_extract` | `DeviceExtraction` (`mri_safety/service.py`) | technologist (flags block confirmation until reviewed) |
| `prep_translation` | `TranslationOutput` (`prep/service.py`) | medical director or radiologist approves before any text is sent |
| `voice_agent` | tool calls through the Tool Gateway (8 front-desk tools) and replies | staff take transfers; actions are logged |
| `call_summary` | `CallSummary` (`frontdesk/service.py`) | front desk reads the call log |
| `feedback_classify` | `Classification` (`feedback/service.py`): sentiment and themes | site manager confirms |
| `policy_qa` | `QaOutput` (`inspection/service.py`): answer with citations checked verbatim by the server | staff |
| `referral_weekly_summary` | `SummaryOutput` (`referrals/service.py`): sentences with fact keys; the server fills every number | operations manager |
| `clinical_kg_parse`, `clinical_kg_answer` | `ParseOutput`, `AnswerOutput` (`clinical_kg/service.py`): statements citing fact ids, unknown ids rejected | reference only |

## Eval set and threshold

| Agent | Eval set | Main metric |
| --- | --- | --- |
| `requisition_extract` | `evals/datasets/requisitions.json` (50) | all six fields right per case |
| `requisition_triage` | `evals/datasets/requisitions.json` (50) | agreement with the label |
| `protocol_suggest` | `evals/datasets/protocols.json` (40) | top-1 protocol |
| `mri_implant_extract` | `evals/datasets/implants.json` (30) | device extracted right |
| `voice_agent`, `call_summary` | `evals/voice_agent/cases.jsonl` (20 scripted calls, README there) | call completed; summary matches the actions |
| `feedback_classify` | `evals/datasets/feedback.json` (60) | sentiment and the exact theme set |
| `policy_qa` | `evals/datasets/policy_qa.json` (20, 4 not covered) | right citation or a correct "not found" |
| `referral_weekly_summary` | `evals/datasets/referral_summary.json` (3 data sets) | every number traceable |
| `clinical_kg_parse`, `clinical_kg_answer` | `evals/datasets/clinical_kg.json` (30) | expected fact cited, correct "not found" |
| `cxr_draft`, `prep_translation` | none (the draft needs images; translations are approved by a person) | |

Run them with `uv run python -m app.aiops.evalrun <agent>` (the older `uv run python -m app.modules.evals.run` still
writes `evals/results/<task>.json` for the AI Ops "Imaging reports" tab).

Release gate (6.6): `requisition_extract` eval_threshold 0.75, `requisition_triage` eval_threshold 0.85,
`protocol_suggest` eval_threshold 0.9, `cxr_draft` eval_threshold 0.8, `mri_implant_extract` eval_threshold 0.9,
`prep_translation` eval_threshold 0.8, `voice_agent` eval_threshold 0.9, `call_summary` eval_threshold 0.9,
`feedback_classify` eval_threshold 0.75, `policy_qa` eval_threshold 0.8, `referral_weekly_summary` eval_threshold 1,
`clinical_kg_parse` eval_threshold 0.9, `clinical_kg_answer` eval_threshold 0.9 in the agent registry
(`apps/api/config/agents/`, the reason for each in a comment); the latest results of every agent are in
[evals/README.md](../../../../evals/README.md).

## Known failure modes

- Every number is a mock baseline: the mock outputs and the eval labels come from the same generator, so the rates are
  optimistic and show the pipeline, not a model's quality. A run with a real key is outstanding for all of them.
- Extraction: the prior-imaging field is the weakest (0.86 on the mock); free-text letters are harder than forms.
- Triage: over-triage 8% and under-triage 2% on the mock; the protocol library has 22 protocols, so an exam outside
  it gets the closest one (a radiologist must change it).
- Chest X-ray drafts run on synthetic phantom images; no eval set, so the agent stays "Not evaluated".
- Feedback: the exact theme set is the hard part (English comments are the weakest); policy Q&A says "not found"
  correctly in 3 of 4 uncovered questions on the mock; retrieval is keyword scoring, no embeddings.
- Phone line: the language is found by keyword, numeric dates with both numbers <= 12 read month first, and the model
  agent's French replies are read out with the English voice; speech recognition is the browser's.
- Clinical knowledge: the graph comes from annotated Chinese literature with uneven coverage; it is reference
  material, not a guideline, and the UI says so on every answer.
