# Guideline summaries for the order-review commentary (spec 7.2)

The spec's `knowledge/guidelines/` is `apps/api/knowledge/guidelines/` here (copied
into the API image). The `order_review` and `medication_reconciliation` agents
retrieve sections from these files (`app/modules/med_safety/knowledge.py`: keyword
scoring, no embeddings, no internet) and may cite them as `guideline_refs`
(`<file id>#<section id>`); a reference that is not a retrieved section is dropped.

**Synthetic summaries written for this demo, not clinical guidance.** Each file
paraphrases general pharmacology in our own words and names the public material it
leans on (mostly FDA prescribing information, which is in the public domain). They
are short and incomplete on purpose. A real deployment would load the hospital's
approved guidelines and pharmacy policies instead (the 8.1 "knowledge retrieval
service" roadmap card).

Format: a header block (`id`, `title`, `source`) between `---` lines, then one
`## <section id>` per section with a `title:` line, a `keywords:` line and the text.
