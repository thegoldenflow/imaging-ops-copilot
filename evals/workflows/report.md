# Durable workflows: acceptance report (WP4c, spec 6.5)

Run 2026-10-09T20:40:41 · temporalio 1.33.0 · Temporal's time-skipping test server and the replay of captured histories · 33 of 33 tests passed in 110.1 s.

**passed**

| Criterion | Status | Tests |
| --- | --- | --- |
| The journey with an injected failure in the MedRec activity, automatically retried, leaves exactly one MedRec document in FHIR | passed | `test_a_failed_medrec_activity_is_retried_and_leaves_exactly_one_medrec_document` passed |
| Killing and restarting the worker resumes the journey without re-running completed steps | passed | `test_a_restarted_worker_resumes_the_journey_without_running_completed_steps_again` passed |
| A sign-off timeout (24 h; 24 s in the test) produces the escalation Task, then the operations manager is told | passed | `test_a_signoff_not_given_in_24_seconds_escalates_then_tells_the_operations_manager` passed |
| The low-confidence loop: after the human correction the eval set has one more case and the regression eval runs | passed | `test_low_confidence_output_is_corrected_added_as_an_eval_case_and_the_regression_runs` passed |
| One Temporal replay test per workflow (plus the version-1 journey through patched()) | passed | `test_captured_history_replays_against_the_current_code` passed, passed, passed, passed, passed, passed<br>`test_every_workflow_has_a_replay_test` passed<br>`test_the_version_1_journey_has_no_patch_marker_and_the_current_one_has` passed |
| The whole journey from admission to the booked follow-up call | passed | `test_journey_runs_from_admission_to_the_booked_follow_up_call` passed |
| An activity calling a privileged tool is not retried; its failure becomes a Task; retry and skip by the operations manager are audited | passed | `test_a_failed_privileged_call_is_not_retried_and_waits_for_a_retry_or_skip` passed |
| Capacity exceptions: approved actions executed with idempotency keys, verified after 1 h; rejected and cleared ones record an outcome | passed | `test_capacity_workflow_executes_the_approval_and_checks_the_occupancy_an_hour_later` passed<br>`test_rejected_and_cleared_exceptions_record_their_outcome_too` passed |
| EventBus <-> Temporal bridge (insert-first outbox, order, outage), offline mode, roles and audit of retry and skip | passed | `test_without_temporal_the_bridge_is_off_and_the_view_says_offline` passed<br>`test_domain_events_map_to_workflow_starts_and_signals` passed<br>`test_the_outbox_takes_each_event_once_and_sends_in_order` passed<br>`test_commands_wait_while_temporal_is_unreachable_and_go_out_when_it_is_back` passed<br>`test_retry_and_skip_are_for_the_operations_manager_and_admin_and_audited` passed |
| Demo script demo/workflows.md (3 minutes, a deliberate failure and the recovery) | passed | `demo/workflows.md` present |

## Real server (docker dev server, a real worker process killed and restarted)

Exit code 0; MedRec documents after the injected failures: 1; resumed 16.5 s after the restart; completed steps run again: none.

```
[   0.5 s] journey journey-stay-01003 for inpatient stay-01003 (Medicine A), Temporal 127.0.0.1:7233 namespace hospital-demo
[   0.5 s] deliberate failure set: the next 2 attempts of medRecAdmission fail after their work committed
[   0.5 s] worker process 18276 started
[  14.2 s] pharmacist confirmed the order review (Task/task-00523)
[  20.7 s] MedRec step done on attempt 3 of 4; MedRec documents in FHIR for the encounter: 1 (DocumentReference/documentrefe-00009)
[  20.7 s] worker process 18276 killed while the journey waits for the MedRec co-signature
[  25.0 s] pharmacist and physician signed; the signals are stored in Temporal, no worker runs: timeline still 'waiting'
[  25.0 s] worker process 11348 started (a new process with nothing in memory)
[  41.5 s] journey resumed after the sign-off: ward monitoring reached 16.5 s after the restart
[  41.5 s] step activities before the kill: journey.context, journey.triageAssist, journey.orderReview, journey.medRecAdmission; after the restart only journey.bedAssignment, journey.predictDuration ran; completed steps run again: none
[  41.5 s] done; the journey waits on the ward (open the workflow view, or skip it there as the bed manager)
```

No model is judged in this package: the AI steps it orchestrates have their own evals (evals/control_tower, evals/patient_message_triage).
