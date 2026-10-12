# Demo scripts

Each script gives the setup, the click path or the terminal commands, what to say and what the screen shows. Start the
app first ([README.md](../README.md#run-it)); no API key is needed (the mock model stands in). The hospital scripts
assume the hospital's demo data (press **Reset demo** after an upgrade); the storyline has its own reset.

| Script | What it shows | Spec | Time | Screen or terminal |
| --- | --- | --- | --- | --- |
| [hospital_journey.md](hospital_journey.md) | **Start here.** One synthetic patient through the whole hospital in 12 steps: AI proposes, a person signs, the audit trail records it; with or without Temporal | 8.2 | under 10 min | **Hospital storyline** |
| [flow_control_tower.md](flow_control_tower.md) | ED, bed and OR boards on the simulated clock, an exception narrated by the AI, approved into Tasks | 7.1 | 5 min | **Control Tower** |
| [med_safety.md](med_safety.md) | Rules first, AI commentary second; admission reconciliation signed by the pharmacist and co-signed | 7.2 | 3 min | **Medication safety** |
| [documentation.md](documentation.md) | A discharge summary that cites its sources and a blocked untraceable medicine; bilingual instructions; SBAR | 7.3 | 4 min | **Documentation** |
| [voice_services.md](voice_services.md) | Call centre with a clerk's one-click confirmation; a Chinese follow-up call that escalates | 7.4 | 3 min | **Call centre**, **Follow-up calls** |
| [workflows.md](workflows.md) | A deliberate failure retried without a second side effect; a worker killed and restarted | 6.5 | 3 min | **Workflows** + terminal |
| [ai_ops.md](ai_ops.md) | One command runs every eval; release gate, a failing case's trace, a prompt comparison, a cost alert | 6.6 | 4 min | **AI Ops** + terminal |
| [agent_runtime.md](agent_runtime.md) | Registered agents and tools, the gateway's refusals of an injected request, trace and Provenance | 6.4 | 5 min | **Patients** (patient message), **AI agents** |
| [platform_governance.md](platform_governance.md) | Access by role and unit, break-glass, consent degradations, de-identification, the audit chain | 6.3 | 6 min | **Patients**, **Break-glass review**, **Audit log** |
| [event_bus_simulator.md](event_bus_simulator.md) | The day simulator sending HL7 v2 messages that become domain events | 6.2 | 3 min | terminal |
| [fhir_gateway.md](fhir_gateway.md) | One gateway for every read, drafts only for writes, the same answers from PostgreSQL and HAPI FHIR | 6.1–6.2 | 3 min | terminal |

The imaging centre's storylines are on the Home page of each imaging role (front desk, technologist, radiologist,
operations manager, medical director, administrator, referrer), with one-click role switches.

For the interview: [docs/interview-narrative.md](../docs/interview-narrative.md) pairs each talk-track sentence with the
screen above that proves it; the architecture pages are **Safety tiers** and **Roadmap**.
