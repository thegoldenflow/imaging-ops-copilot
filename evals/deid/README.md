# Free-text de-identification (spec 6.3)

Code: `apps/api/app/llm/freetext_deid.py` (rule layer, second pass), `apps/api/app/llm/deid_eval.py` (scoring). Prompt: `deid_check@1` (in the code, version recorded on every call).

## Input resources

Clinical free text (notes, report conclusions, handoffs, call transcripts) plus the identifiers the record already holds: Patient (names in every spelling including Chinese, MRN, health card, phone, address, postal code, contacts), Practitioner (staff names), Organization. `FreeTextDeidentifier.learn_fhir(resources)` reads them from FHIR; the eval passes them per note (`known`).

## Output schema

- Rule layer: the text with typed placeholders from the shared token map: `[PERSON_n]`, `[STAFF_n]`, `[DATE_n:D-2]` (days from the note's reference date), `[ADDRESS_n]`, `[PHONE_n]`, `[EMAIL_n]`, `[HEALTH_CARD_n]`, `[MRN_<keyed hash>]` / `[MRN_n]`, `[ORG_n]`, `[ID_n]`. The map stays on the server; `restore()` puts the identifiers back into model output (one person = one token, restored in its first spelling).
- Second pass (`deid_check@1` through the LLM gateway): `{"findings": [{"text": str, "kind": PERSON | STAFF | DATE | ADDRESS | PHONE | EMAIL | HEALTH_CARD | MRN | ORG | ID}]}`, validated against the schema. Each finding is replaced too and stored, encrypted, in `deid_misses` as a sample the rules missed. If the model is unavailable the rule layer's result stands.

## Eval set and thresholds

- `cases.jsonl`: 200 synthetic notes (`apps/api/scripts/gen_deid_eval.py`, seed 6300) with 2,513 planted PHI spans: English, pinyin and Chinese names, relatives and outside clinicians the record does not know, mixed date formats (ISO, both slash orders, dots, month names, Chinese), phones with extensions, health cards with version codes, MRNs, addresses and postal codes, organisations, emails; plus traps (eponyms, drug names, scores, fractions, surnames that are also words) and, in about a third of the notes, one harder variant no pattern targets on purpose. The 24 notes whose patient has a two-letter surname (Li, Wu, Xu, Ma, Hu, He) end with it written alone ("Hu's son called", "ID band checked: XU, Jun", "Wu walked to the nursing station"; 31 spans, added 2026-10-09 from a random stream of their own, so the other notes did not change), and male patients' notes among them with a sentence starting with "He" (a trap).
- Run: `cd apps/api && uv run python scripts/deid_eval.py [--check] [--live]` writes `report.json` and `report.md`. `--live` runs the second pass on the configured provider; without it the mock provider's heuristic detector stands in.
- Gate: recall >= 0.98 and precision >= 0.90 (pipeline). Recall counts a span only when all of it is redacted. The backend suite fails below the gate (`tests/test_freetext_deid.py::test_eval_meets_the_thresholds`).
- Result 2026-10-08: rule layer 0.9887 / 0.9992; with the second pass (mock) 0.9932 / 0.9992; date offsets right for 93% of caught dates.
- Result 2026-10-09 (with the two-letter surnames): rule layer 0.9889 / 0.9992 (all 31 new spans caught, no "He" trap redacted: the leaks and the two false positives are the earlier ones; without the two-letter rule all 24 surnames leak and the rule layer falls to recall 0.9793, below the gate); with the second pass (mock) 0.9932 / 0.9992.

## Known failure modes

- Names with no cue: given names alone ("Peter and Maria visited"), a nickname not introduced as such. A real model's second pass is the intended net; the mock only knows a list of common surnames.
- Lower-case titles ("dr. yamada") and dates in words ("the second of November").
- Ambiguous numeric dates: 05/10/2026 is read month first, so its token can be a few months off when the writer meant 5 October (dates after the 12th are unambiguous). The date itself is still redacted.
- Dictionary hits on surnames that are also words ("Young adult daughter" for a patient named Young) are redacted: safe, but it removes words.
- A two-letter surname that is also a word or symbol (He, Ma, Li; `SHORT_WORDS` in the code) is taken for a name only in a name context (possessive, title, relation, next to the person's other name). Written alone with no cue ("He walked to the station" for Mr. He) it is not redacted; a two-letter surname that is no word (Hu, Wu, Xu) is, wherever it is capitalised.
- The notes are synthetic and were written alongside the rules: the numbers show the pipeline works, not its performance on real clinical text.
