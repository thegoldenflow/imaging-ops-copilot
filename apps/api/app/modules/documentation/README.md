# Documentation agent (spec 7.3)

Three clinical documents drafted from the FHIR record, every fact item traced to the resources it comes from, a
person signing each: the **discharge summary** (physician), the **bilingual patient instructions** (nurse) and the
**SBAR shift handoff** (nurse; confirming it is the one signature). Risk tier `documentation`. Web:
`apps/web/src/features/documentation/` (route `/documentation`); API `/api/documentation/...`.

| File | What |
| --- | --- |
| `models.py` | the three documents' schemas (every item with `source_refs[]`), the instructions plan and structure, the validator report |
| `facts.py` | the discharge summary's input: resources as facts, chunked by type, the missing-input check |
| `deid.py` | names and dates to tokens (`[DATE_n:D+k]` relative to the admission), free text through the free-text de-identifier |
| `validator.py` | `TraceabilityValidator` (base) and `DischargeSummaryValidator`, `PatientInstructionsValidator`, `HandoffValidator` |
| `lexicon.py` | drug, condition and procedure names with synonyms; fuzzy matching (difflib) |
| `summary_agent.py`, `instructions_agent.py`, `handoff_agent.py` | the runtime agents (entrypoints in config/agents) |
| `instructions.py`, `render.py`, `phrases.py`, `terms.py`, `hant.py`, `readability.py` | the instructions' structure, its three renderings, menus and phrase tables, the knowledge graph's terms and explanations, Simplified -> Traditional, Flesch-Kincaid |
| `document.py`, `tools.py` | the DocumentReference (text attachments + `documentation-data` JSON, the `sign-block` extension) and the tool handlers |
| `service.py`, `router.py`, `journey.py` | worklist, drafting, editing, signing, delivery; the journey's two steps |
| `mocks.py`, `evals.py` | mock models; the eval harness |

## Input resources

- **Discharge summary** (per Encounter, through the tools getEncounterBundle and getDischargeMedications): the
  stay's Condition, Procedure, MedicationRequest (hospital orders and discharge prescriptions) and WP6's discharge
  medication table (signed by the pharmacist and co-signed by a physician), the admission and the latest vital-sign
  panel, abnormal laboratory results (first and last per test; every INR), DiagnosticReport, open Task, CarePlan
  (the discharge plan) and pending ServiceRequests. Missing a diagnosis or the signed table: "insufficient
  information, missing X", no model call.
- **Patient instructions**: only the signed discharge summary (getSignedDischargeSummary: the encounter, the
  patient's language, the summary). Without it: insufficient information.
- **Handoff**: the last 12 hours (getHandoffContext): observations since the cut-off, open tasks, active flags, the
  stay's diagnoses, procedures and medication orders (new ones written in the window, stopped ones).
- Without the patient's `ai_processing` consent the agents are refused at the Tool Gateway and the module offers the
  template only (6.3): the empty summary or SBAR with the sources to cite, the instructions with the signed
  summary's medicines and follow-ups copied in; no model call.

## Output schema

- Discharge summary: `{reason_for_admission, hospital_course[{date, text, source_refs}], discharge_diagnoses[{name,
  code}], procedures[{name, code, date}], medications{new, changed, stopped, continued: [{name, rxcui, dose, note}]},
  pending_results[{test}], follow_up[{code, when, department, location}], red_flags[]}`, every item `{text,
  source_refs[]}`. Long-context strategy: one SEGMENT call per chunk (diagnoses and procedures; medications with the
  table; observations and reports; care plan, tasks and pending results; split above 40 facts), then one SYNTHESIS
  call over the segment notes; ids outside the chunk's list get one more try.
- Patient instructions: the model returns codes per summary item (`InstructionsPlan`: purpose, times, cautions,
  department, red flags, daily care); the module builds `patient-instructions@1` with the facts copied from the
  summary and renders the five blocks (why you were in hospital; medicine table: medicine / what it is for / when to
  take it / notes; follow-up appointments; red flags with what to do; daily care: food, activity, wound) in `en`,
  `zh-Hans` and `zh-Hant` from that one structure. Default language from `communication.language` (zh-CN ->
  Simplified, zh-TW -> Traditional).
- Handoff: `{situation, background[], assessment[], recommendation[]}`, every statement `{text, source_refs[]}`.
- Each document is a DocumentReference (docStatus preliminary until signed) with id `dsum-<encounter>`,
  `pinstr-<encounter>` or `sbar-<encounter>-<YYYYMMDD><D|N>`: text attachments (the instructions in three
  languages) and the JSON attachment `documentation-data` (content, the AI draft as written, the validator's report,
  the sources, the run). A draft with an untraceable item carries the extension `sign-block`; the signing service
  refuses it on every path.

## Eval set and threshold

`evals/documentation/` (README there): 30 synthetic stays x three documents. Gates: untraceable items after the
validator 0; validator recall 1.0 on the oracle's untraceable items; schema validity 1.0; bilingual consistency 1.0
on 10 instructions; the 5 injected hallucinations blocked; the human rating's four dimensions mean >= 4 (sheet
outstanding; automated proxies meanwhile). Monitored: the raw hallucination rate (0.0138 with the mock), the English
grade, the Chinese checks, the drafting time (5 model calls per summary). A document the model could not draft fails
that case for its agent with the reason, not the run (the instructions fail with the summary they are drafted from).
Tests: `tests/test_documentation.py`,
`tests/test_documentation_validator.py`, `tests/test_documentation_eval.py`; Playwright `e2e/documentation.spec.ts`.

Release gate (6.6): `discharge_summary` eval_threshold 0.9, `patient_instructions` eval_threshold 0.9, `nursing_handoff` eval_threshold 0.9 in the agent registry (`apps/api/config/agents/`); the runner `uv run python -m app.aiops.evalrun` judges every agent against it, and the latest results of every agent are in [evals/README.md](../../../../../evals/README.md).

## Known failure modes

- The validator checks names and sources, not every fact: a wrong dose or date next to a real, cited drug passes;
  the physician's review is the check (the handoff flags numbers missing from the cited sources as warnings).
- Negated mentions in free text ("no signs of pneumonia") of a condition outside the record are flagged red; the
  physician rewrites them. Conditions outside the EHR's list are not scanned.
- The knowledge graph's facts are noisy: explanations come from it only when they pass the selection rules (the
  patient's own diagnoses for a medicine; typical, plain signs for a disease); the common conditions have reviewed
  explanations and other medicines their class.
- Traditional Chinese comes from hand-written phrases plus a character table for the graph's terms (no conversion
  library is installed); rare characters outside the table stay Simplified.
- The generator writes "once daily" for weekly methotrexate (app/ehr/codes.py); the instructions copy the summary's
  dosage, so such a case would show daily times; a pharmacist's review of the discharge medications is the guard.
- Mock models: optimistic results; the three agents stay `eval_status: pending` until a live run and the human rating.
