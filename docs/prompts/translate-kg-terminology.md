# Prompt: English terminology for the clinical knowledge graph

Give this to a local coding agent (Claude Code or similar) on branch `feat/gemini-provider`.

---

You are adding English terminology to the clinical knowledge graph in this repository, so that English questions match Chinese graph nodes and answers show English names. Do not translate or change the graph data in `apps/kg-qa/` (that sub-project must stay unchanged). You only write one new file: `apps/api/app/modules/clinical_kg/data/terminology_en.jsonl`.

## Steps

1. Read `apps/api/app/modules/clinical_kg/terms.py` and `glossary.py` to see how the file is used.
2. Export the names to translate:
   `cd apps/api && uv run python -m app.modules.clinical_kg.terms export`
   This writes `apps/api/app/modules/clinical_kg/data/terms_to_translate.jsonl`, one name per line, most useful first. Each line has `name` (Chinese graph node), `kind` (disease, symptom, check, drug or treat), `uses` and `context` (a few related graph facts, to help you pick the right meaning). Names already in `terminology_en.jsonl` are left out, so you can stop and resume.
3. Translate in batches of about 150 lines, in file order. After each batch, append the results to `terminology_en.jsonl` (create it on the first batch), then run
   `uv run python -m app.modules.clinical_kg.terms check`
   and fix every problem it reports before the next batch. Alias conflicts are reported but are not errors: if two different names claim the same alias, remove the alias from the entry it fits less well.
4. Re-run `export` from time to time; it lists only what is still missing. Priority 1 (all diseases plus symptoms, tests, drugs and treatments used by two or more diseases) is the goal; `export --all` adds the rest if there is time.
5. When done: run `uv run pytest -q tests/test_clinical_kg.py` and `uv run python -m app.modules.evals.run clinical_kg`, then commit `terminology_en.jsonl` alone with a message like `data(clinical-kg): English terminology for N graph nodes`. Do not commit `terms_to_translate.jsonl` (it is git-ignored).

## Output format

One JSON object per line, UTF-8, no trailing commas, Chinese kept as is (`ensure_ascii=False`):

```json
{"name": "肺栓塞", "kind": "disease", "en": "pulmonary embolism", "aliases_en": ["pulmonary thromboembolism"], "confidence": "high"}
{"name": "呼吸困难", "kind": "symptom", "en": "dyspnea", "aliases_en": ["shortness of breath", "breathlessness", "dyspnoea", "difficulty breathing"], "confidence": "high"}
{"name": "胸部增强CT", "kind": "check", "en": "contrast-enhanced chest CT", "aliases_en": ["CT chest with contrast", "chest CT with IV contrast"], "confidence": "high"}
{"name": "继发性GHD", "kind": "disease", "en": "secondary growth hormone deficiency", "aliases_en": ["secondary GHD"], "confidence": "medium"}
{"name": "肠道机械压缩", "kind": "symptom", "en": "mechanical compression of the bowel", "aliases_en": [], "confidence": "low"}
```

- `name`: copied exactly from the export, character for character (including full-width punctuation and spaces). Never edit it.
- `kind`: copied from the export.
- `en`: the standard English medical term, lower case except proper names and abbreviations (Goodpasture syndrome, CT, MRI, HIV). Prefer the preferred term a clinician would write in a requisition or report (MeSH / SNOMED CT style), American spelling.
- `aliases_en`: other ways a clinician or requisition would write the same thing: common synonyms, the British spelling, lay terms clinicians use (heart attack), well-known abbreviations when they are unambiguous in an imaging clinic (COPD, DVT, ACL). Up to 5. Leave it empty rather than padding it.
- `confidence`:
  - `high`: a standard term with a clear English equivalent.
  - `medium`: the meaning is clear but the wording is your best rendering.
  - `low`: a descriptive phrase, a lab value, an unclear or garbled name. Low entries are shown as English labels but never used for matching, so use `low` whenever you are unsure.

## Rules

- Translate the meaning in medical English, not word by word; use the `context` to decide between meanings (e.g. 胸片 is "chest X-ray", 超声 is "ultrasound", 增强 is "contrast-enhanced").
- Do not invent facts, add explanations or merge two names into one. One output line per exported name.
- Never use as an alias: two-letter abbreviations (they are ignored anyway), abbreviations with several common meanings in radiology (PE, MS, AS, CA, MI is fine only as an alias of myocardial infarction), or a broader term (do not give "cancer" as an alias of 肺癌).
- Generic or vague names (疼痛 "pain", 感染 "infection", 体格检查 "physical examination") get an accurate `en` and no aliases.
- Drug names: the generic (INN) name, lower case; brand names only as aliases if they are well known. Treatments and procedures: the usual procedure name.
- Names that are lab results or thresholds (for example containing ＜, ＞, % or units) get `confidence: low`.
- If a name is not a medical term at all (noise from the source data), still output it with your best `en` and `confidence: low`.
- No patient data is involved; everything here is a public research graph (CMeIE). Do not send the repository anywhere else.

## Check before you finish

- `terms check` reports 0 problems other than alias conflicts you chose to keep.
- Spot-check 20 random `high` lines against a reliable source; downgrade anything you are not sure of.
- In the app (Clinical knowledge page), English questions such as "What tests are used for acute pancreatitis?" and "What could cause fever, rash and joint pain?" now align their terms, and facts show English names next to the Chinese.
