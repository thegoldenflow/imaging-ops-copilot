"""Hospital flow Control Tower (docs/SPEC-hospital.md 7.1): ED -> beds -> OR on one screen.

- `snapshot.py`: the three boards from FHIR (through FhirGateway, under the
  `control_tower` registry entry's data scope), with the flow models' predictions.
- `flowmodels.py` / `features.py` / `flowdata.py`: the four prediction models
  (admission from triage, discharge within 24 h, surgical duration, ED wait),
  trained by `scripts/train_flow_models.py` on data `scripts/build_flow_dataset.py`
  reads from FHIR; files and reports in `apps/api/models/flow/`.
- `rules.py`: the rule engine (unit occupancy, ED boarders, OR overrun, pre-op
  gaps) with engine-built facts, evidence and action menus.
- `exceptions.py`: the exception stream (open, deferred, decided, cleared).
- `agent.py`: the narrator, a runtime agent (6.4): narrative and recommended
  actions in the spec's schema, guarded (actions from the menu, numbers from the
  engine, evidence resolvable in FHIR); it never executes anything.
- `service.py` / `router.py`: board views per role, the action drawer
  (approve -> Task for the owner role, reject with a reason, defer with a
  reminder), the drill-down patient card (RBAC through FhirGateway).
"""
