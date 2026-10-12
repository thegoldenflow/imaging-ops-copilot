# Bilingual voice eval (spec 7.4)

Run 2026-10-10T04:40:08 (mock model): **passed**. 20 scripts (10 English, 10 Chinese) through the whole flow: expected outcome 20, every agent sentence in the caller's language 20, write-back 20; the same outcome in both languages for 10 of 10 scenarios.

Synthetic scripts written alongside the mock models: with the mock the numbers show that the pipeline works (dialog, identity check, rules, Tool Gateway, write-back), not a model's quality. Run with --live on the configured model before trusting them.

| Script | Lang | Outcome | Language | Write-back | Agent turns |
| --- | --- | --- | --- | --- | --- |
| bi-faq-en | en | yes | yes | yes | 2 |
| bi-faq-zh | zh | yes | yes | yes | 2 |
| bi-lookup-en | en | yes | yes | yes | 3 |
| bi-lookup-zh | zh | yes | yes | yes | 3 |
| bi-prep-en | en | yes | yes | yes | 3 |
| bi-prep-zh | zh | yes | yes | yes | 3 |
| bi-clinical-en | en | yes | yes | yes | 3 |
| bi-clinical-zh | zh | yes | yes | yes | 3 |
| bi-transfer-en | en | yes | yes | yes | 1 |
| bi-transfer-zh | zh | yes | yes | yes | 1 |
| bi-clear-en | en | yes | yes | yes | 17 |
| bi-clear-zh | zh | yes | yes | yes | 17 |
| bi-fever-en | en | yes | yes | yes | 4 |
| bi-fever-zh | zh | yes | yes | yes | 4 |
| bi-wound-en | en | yes | yes | yes | 4 |
| bi-wound-zh | zh | yes | yes | yes | 4 |
| bi-missed_medicine-en | en | yes | yes | yes | 17 |
| bi-missed_medicine-zh | zh | yes | yes | yes | 17 |
| bi-person-en | en | yes | yes | yes | 17 |
| bi-person-zh | zh | yes | yes | yes | 17 |
