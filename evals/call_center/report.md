# Call-centre eval (spec 7.4)

Run 2026-10-10T04:37:02 (mock model): **passed**. 40 scripts; intent accuracy **1.0** (gate 0.9); identity-failure scripts put through to a person **1.0** of 6 (gate 1.0). Server time per caller turn: p50 328 ms, p95 2078 ms (text only; speech runs in the browser).

Synthetic scripts written alongside the mock models: with the mock the numbers show that the pipeline works (dialog, identity check, rules, Tool Gateway, write-back), not a model's quality. Run with --live on the configured model before trusting them.

## By intent

| Intent | Scripts | Correct |
| --- | --- | --- |
| cancel | 6 | 6 |
| clinical | 6 | 6 |
| faq | 7 | 7 |
| lookup | 6 | 6 |
| prep | 5 | 5 |
| reschedule | 7 | 7 |
| transfer | 3 | 3 |

## By language

| Language | Scripts | Correct |
| --- | --- | --- |
| en | 26 | 26 |
| zh | 14 | 14 |

## Scripts

| Script | Lang | Kind | Label | Recognised | Correct | Outcome | Actions |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cc-01 | en | intent | reschedule | reschedule | yes | requested | identity_verified, reschedule_requested |
| cc-02 | en | identity_failure | reschedule | reschedule | yes | transferred | identity_failed, identity_failed, transfer |
| cc-03 | en | intent | reschedule | reschedule | yes | requested | identity_verified, reschedule_requested |
| cc-04 | en | intent | reschedule | reschedule | yes | requested | identity_verified, reschedule_requested |
| cc-05 | en | intent | reschedule | reschedule | yes | requested | identity_verified, reschedule_requested |
| cc-06 | zh | intent | reschedule | reschedule | yes | requested | identity_verified, reschedule_requested |
| cc-07 | zh | intent | reschedule | reschedule | yes | requested | identity_verified, reschedule_requested |
| cc-08 | en | intent | cancel | cancel | yes | requested | identity_verified, cancel_requested |
| cc-09 | en | intent | cancel | cancel | yes | requested | identity_verified, cancel_requested |
| cc-10 | en | intent | cancel | cancel | yes | requested | identity_verified, cancel_requested |
| cc-11 | en | identity_failure | cancel | cancel | yes | transferred | identity_failed, identity_failed, transfer |
| cc-12 | zh | intent | cancel | cancel | yes | requested | identity_verified, cancel_requested |
| cc-13 | zh | identity_failure | cancel | cancel | yes | transferred | identity_failed, identity_failed, transfer |
| cc-14 | en | intent | lookup | lookup | yes | resolved | identity_verified, lookup |
| cc-15 | en | identity_failure | lookup | lookup | yes | transferred | identity_failed, identity_failed, transfer |
| cc-16 | en | intent | lookup | lookup | yes | resolved | identity_verified |
| cc-17 | en | intent | lookup | lookup | yes | resolved | identity_verified, lookup |
| cc-18 | zh | intent | lookup | lookup | yes | resolved | identity_verified, lookup |
| cc-19 | zh | intent | lookup | lookup | yes | resolved | identity_verified, lookup |
| cc-20 | en | intent | faq | faq | yes | resolved | faq |
| cc-21 | en | intent | faq | faq | yes | resolved | faq |
| cc-22 | en | intent | faq | faq | yes | resolved | faq |
| cc-23 | en | intent | faq | faq | yes | resolved | faq |
| cc-24 | zh | intent | faq | faq | yes | resolved | faq |
| cc-25 | zh | intent | faq | faq | yes | resolved | faq |
| cc-26 | zh | intent | faq | faq | yes | resolved | faq |
| cc-27 | en | intent | prep | prep | yes | resolved | identity_verified, prep_given |
| cc-28 | en | intent | prep | prep | yes | resolved | identity_verified, prep_given |
| cc-29 | zh | identity_failure | prep | prep | yes | transferred | identity_failed, identity_failed, transfer |
| cc-30 | zh | intent | prep | prep | yes | resolved | identity_verified, prep_given |
| cc-31 | en | intent | prep | prep | yes | resolved | identity_verified, prep_given |
| cc-32 | en | intent | clinical | clinical | yes | resolved | identity_verified, nurse_line |
| cc-33 | en | intent | clinical | clinical | yes | resolved | identity_verified, nurse_line |
| cc-34 | zh | intent | clinical | clinical | yes | resolved | identity_verified, nurse_line |
| cc-35 | zh | intent | clinical | clinical | yes | resolved | identity_verified, nurse_line |
| cc-36 | en | identity_failure | clinical | clinical | yes | transferred | identity_failed, identity_failed, transfer |
| cc-37 | en | intent | clinical | clinical | yes | resolved | identity_verified, nurse_line |
| cc-38 | en | intent | transfer | transfer | yes | transferred | transfer |
| cc-39 | zh | intent | transfer | transfer | yes | transferred | transfer |
| cc-40 | en | intent | transfer | transfer | yes | transferred | transfer |
