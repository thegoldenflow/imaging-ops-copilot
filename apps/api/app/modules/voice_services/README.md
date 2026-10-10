# Patient voice services (spec 7.4)

The hospital-wide **call centre** (inbound calls: appointments, general information, never clinical advice) and the
**follow-up call** 48 hours after discharge (scripted from the signed patient instructions, red-flag rules first, a
nurse queue for what needs a person). Calls are text-simulated: the browser turns speech into text (Web Speech) and
reads the replies aloud; only text reaches the server and no audio is recorded. Agents `call_center` (risk tier
`ops`, model tier `voice`, signer clerk) and `followup_calls` (risk tier `documentation`, model tier `reasoning`,
signer nurse); module `voice_services` (the
rules-only fallback and the nurse's actions). Web: `apps/web/src/features/voice/` (routes `/call-centre`,
`/follow-up`); API `/api/voice/...`.

| File | What |
| --- | --- |
| `models.py` | `HospitalCall` (module table `voice_calls`), `FollowupCall` (`followup_calls`), `Turn`; transcripts encrypted at rest |
| `callcenter.py` | the inbound call's dialog: safety rules, pending steps (identity, which appointment, which time, yes/no), intents |
| `intents.py`, `call_agent.py` | keyword rules (emergency, distress, a person, the six intents, yes/no, ordinals) and the model's intent recognition |
| `identity.py` | parsing name, birth date, last four card digits and the caller's relation (English and Chinese) |
| `kb.py` | the hospital FAQ (`knowledge/hospital_faq/*.md`: sections with keywords and answers in three languages) |
| `followup.py` | the four trigger conditions, scheduling (sweep and journey), the scripted call, escalation and write-back |
| `redflags.py`, `followup_agent.py` | the nine red-flag rules (negation, temperatures) and the escalation classifier |
| `phrases.py` | every sentence the agents say, in `en`, `zh-Hans`, `zh-Hant`; the service names |
| `queue.py` | the nurse follow-up queue and its actions (call-back note, close, refer) |
| `tools.py` | the tool handlers (config/tools: findCaller, getPatientAppointments, findAppointmentSlots, writeCallRecord, createCallTask, recordPatientReportedObservation, moveAppointment, cancelBookedAppointment) |
| `service.py`, `router.py`, `journey.py` | views, clerk requests, demo cases; the API; the journey's `scheduleFollowupCall` step |
| `evals.py` | the eval harness (call centre, follow-up, bilingual) |

## Input resources

- **Call centre**, per caller turn: the utterance (de-identified, in an untrusted block; identity turns never go to
  the model); after the identity check (`findCaller`: Patient by name + birthDate + the health card's last four
  digits) the patient's Appointments (`getPatientAppointments`), open slots of the same service
  (`findAppointmentSlots`, the next two weeks), the FAQ sections and the services' prep text. A relative may call for
  the patient ("my mother"): the identity is the patient's.
- **Follow-up call**: the stay (Encounter, discharge time), the patient (telecom, `communication.language`), the
  `followup_call` consent (6.3) and the **signed** patient instructions (WP7's `pinstr-<encounter>`,
  `patient-instructions@1`): the script asks how the patient feels, about the medicines and their times, the
  follow-up appointments, the patient's own red flags (as yes/no questions), daily care, anything else. The
  classifier reads the de-identified transcript (`getEncounterContext`: diagnoses, procedures; untrusted block).
- **Trigger conditions** (all four, else a manual nurse Task with the reason, no call): a discharge time, consent to
  follow-up calls, a phone number, signed instructions. Due 48 h after discharge (`FOLLOWUP_CALL_DELAY_H`); the
  sweep takes stays discharged 48–72 h ago (`FOLLOWUP_CALL_WINDOW_H`) that no journey workflow follows; a journey
  schedules its own through `journey.scheduleFollowupCall`.

## Output schema

- Intent (`IntentOutput`): `{intent: reschedule|cancel|lookup|faq|prep|clinical|transfer|goodbye|other, confidence,
  faq_topic, language: en|zh, distress, emergency}`. Rules win over the model for emergency, distress and a request
  for a person; below 0.5 confidence the turn counts as unrecognised.
- Classifier (`ClassifierOutput`): `{escalate, reason, confidence, summary, symptoms[{code, quote}], quotes[]}`;
  `escalate` true → urgent nurse Task (`model`); confidence < 0.7 → escalated as a routine nurse review Task
  (`low_confidence`); model unavailable → the same (`model_unavailable`). The rules decide first and the model never
  overrides them.
- Red-flag hit: `{code, label, emergency, quote, how: keyword|temperature|answer, temperature_c, question}`; codes
  chest_pain, short_breath, heavy_bleeding, altered_consciousness (emergency: the 911 prompt, stat Task, the call
  ends), fever (≥ 38.5 °C, or a reported fever without a reading), wound_drainage, fall, cannot_eat_drink,
  wants_person (urgent Task, the nurse calls back within 30 min, `FOLLOWUP_CALLBACK_MINUTES`); a yes to the patient's
  own red-flag question counts as a hit.
- Write-back through the gateway: Communication (category `urn:demo-hospital:call-kind`: call-centre,
  prep-instructions, nurse-line, followup-call, nurse-callback, nurse-referral, nurse-close; the call's summary, the
  nurse-line question or the nurse's note as text; the transcript stays in the module's encrypted table), Observation (`patient-reported`, status preliminary: temperature as LOINC 8310-5,
  symptoms as text), Task (call-transfer, nurse-line, followup-escalation, followup-manual, followup-referral), a
  proposed Appointment and the clerk's approval Task for a reschedule or cancellation. Domain events
  `followup.escalated`, `followup.handled`.
- Patient-facing sentences come from `phrases.py`, never from the model.

## Evaluation and thresholds

`evals/call_center/`, `evals/followup/`, `evals/voice_bilingual/` (READMEs there; runner
`apps/api/scripts/voice_eval.py`, cases from `scripts/gen_voice_eval.py`); the prompt-injection extension in
`evals/injection/` (inj-31..38). Gates: call-centre intent accuracy ≥ 0.90 on 40 scripts, identity-failure scripts
put through to a person 100%; follow-up red-flag recall ≥ 0.95 on 50 scripts (15 with red flags), and all 15 by the
rules alone with the model down; bilingual 10 English + 10 Chinese with the same outcome per pair; injection 0
unauthorised actions. Over-escalation is recorded, not gated. Tests: `tests/test_voice_services.py`,
`tests/test_voice_eval.py`; Playwright `e2e/voice-services.spec.ts`.

## Known failure modes

- Keyword rules miss paraphrases ("my dressing is wet through" is caught, "it's oozing a bit" is not); the
  classifier is the second net and escalates when unsure, which costs over-escalation (0.26 with the mock).
- Negation is clause-local ("no chest pain, but …"); a double negative or a negation far from the symptom can be
  misread either way. A short unclear answer to a yes/no question is asked again once, then recorded as said and
  left to the classifier (which escalates below 0.7 confidence).
- Identity is name + birth date + four digits against the EHR; a caller who knows those passes. Speech recognition
  of names (especially Chinese names in English mode) fails often; the console's typed input is the fallback.
- The call centre offers only times of the same service in the next two weeks; anything else goes to a person.
- Stays without signed WP7 instructions (most generated stays) get a manual follow-up Task, not a call.
- The script is built when the call is scheduled; instructions re-signed afterwards are not picked up.
- Mock models: the eval numbers show the pipeline works, not a model's quality; both agents stay
  `eval_status: pending` until a live run.
