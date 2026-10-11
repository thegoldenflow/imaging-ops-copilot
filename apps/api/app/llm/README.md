# LLM call layer and de-identification (spec System 1, 6.3)

Every model call in the suite goes through one gateway: the prompt comes from the prompt registry, identifiers are
replaced before anything leaves the server, the output is validated against a closed Pydantic schema, every call is
logged without PHI, and an unavailable model degrades to a defined fallback. Claude is the default provider; Gemini on
Vertex AI and a mock provider are selectable with `LLM_PROVIDER`. Model ids come from the environment
(`CLAUDE_MODEL_*`, `GEMINI_MODEL_*`) through `config/models.yaml`. Web: the header's AI badge, **AI Ops → Model
calls**.

| File | What it does |
| --- | --- |
| `gateway.py` | `LlmGateway.structured(...)` (one structured call) and `tool_turn(...)` (the phone agent's tool loop); registry check, model policy, de-identification, schema validation with one retry, degradation, call log, `ai_call` audit event, one-call trace |
| `providers.py` | `AnthropicProvider`, `GeminiProvider` (Google Gen AI SDK, Vertex AI mode), `MockProvider` (built-in fixtures, list-price cost estimates) |
| `prompts.py` | `RegisteredPrompt`: each agent's prompt follows the registry's `prompt_version` pointer at every call (`apps/api/prompts/<agent>/<n>.md`) |
| `deid.py` | `Pseudonymizer`: known identifiers (patients, staff, phones, health cards, dates) to typed placeholders and back; spoken dates for the phone line |
| `fhir_deid.py` | De-identification of FHIR resources field by field (names, birth date to age, addresses, MRN to a keyed hash, free-text fields) |
| `freetext_deid.py` | Free-text clinical notes: the rule layer (dictionary from the record plus patterns) and the model second pass `deid_check` |
| `deid_eval.py` | The de-identification eval harness |

## Input resources

- A registered task (agent id) and its prompt; variables, optional images (the chest X-ray draft), a list of the
  patients whose identifiers must not leave, or a pre-built `Pseudonymizer` (the agent runtime passes FHIR-redacted
  context with the same token map).
- Free text for de-identification: the patient's and contacts' names in every spelling (Chinese included), staff,
  MRN, health card, phones, addresses, organisations; dates are kept as days from a reference date.

## Output schema

- `LlmOutcome` (`gateway.py`): `status` ok / needs_human (schema failed twice) / unavailable (timeout, API error,
  refusal, release gate in prod), `data` (the validated schema, identifiers restored), `call_id`, `model`,
  `prompt_version`, `mode`, `eval_status`, `evaluated` (false: the UI shows **Not evaluated**), `run_id`.
- The call log (`LlmCall`, AI Ops → Model calls): task, model, prompt version, tokens, latency, cost, input hash,
  outcome; no prompt text and no identifiers.
- De-identification: placeholder text (`[PERSON_1]`, `[STAFF_2]`, `[DATE_3:D-2]`, `[PHONE_1]`, ...), the token map
  kept on the server, `PhiFindings` from the second pass; misses the rule layer had are stored encrypted in
  `deid_misses` for new rules.

## Eval set and threshold

- `evals/deid/cases.jsonl`: 200 synthetic notes with 2,513 planted PHI spans (Chinese names, mixed date formats,
  phones with extensions, two-letter surnames written alone); runner `scripts/deid_eval.py` (`--live`, `--check`);
  gate recall >= 0.98 and precision >= 0.90 over the whole pipeline, run in the backend suite. README with the four
  sections in that folder.
- Call layer: `tests/test_core.py` (de-identification and re-identification of tool inputs, retry after a schema
  failure, timeout degradation, spoken dates), `tests/test_gemini.py` (31 tests, fake client), `tests/test_fhir_deid.py`
  (one test per resource type), `tests/test_freetext_deid.py`.

Release gate (6.6): `deid_check` eval_threshold 0.9 in the agent registry (`apps/api/config/agents/`); the runner
`uv run python -m app.aiops.evalrun` judges every agent against it, and the latest results of every agent are in
[evals/README.md](../../../../evals/README.md). De-identification is a pipeline eval: it is the one agent a mock run
can pass.

## Known failure modes

- Rule-layer misses on the eval notes: given names with no cue, lower-case titles, dates written in words; ambiguous
  numeric dates (05/10/2026 reads month first) get a wrong day offset although the date is redacted.
- A surname that is also an English word, written alone with no cue ("He walked to the station" for Mr. He), is not
  redacted; the second pass is the net there.
- Without an API key the second pass is a heuristic stand-in; `scripts/deid_eval.py --live` runs it on the model.
- Call-centre turns are de-identified without the second pass (latency per turn).
- Gemini accepts a subset of JSON Schema; a schema it rejects makes the call "unavailable" (the call log shows the
  error). One provider per running backend; switching needs a restart.
- Structured outputs are retried once; a second schema failure goes to a person (`needs_human`), never to a guess.
