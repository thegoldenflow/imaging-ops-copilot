"""Writes the authored cases of the patient-message triage eval: evals/patient_message_triage/cases.jsonl.

    uv run python scripts/build_triage_eval.py           (rewrites the authored cases, keeps human_review ones)

Twelve synthetic messages (no real patients), labelled by the prompt's own definitions (category: clinical,
scheduling, administrative, other; urgency after the rule layer). Each case holds the prompt variables exactly as
the agent sends them (app/agents/library/patient_message_triage.py): a small de-identified encounter context, the
channel, the rule layer's red flags and the message in its untrusted block. Cases from people reviewing
low-confidence outputs (source human_review) are appended by the LowConfidenceReviewWorkflow (spec 6.5) and kept
when this script runs again. Run the regression: `uv run python scripts/triage_regression.py`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents import evalsets  # noqa: E402
from app.agents import untrusted  # noqa: E402
from app.agents.library.patient_message_triage import red_flags  # noqa: E402
from app.ehr.codes import EXT  # noqa: E402

AGENT = "patient_message_triage"
# (id, message, channel, category, urgency) — labels as a ward nurse would give them under the prompt's definitions
MESSAGES = [
    ("chest-pain", "My mother has had chest pain since this morning, after coming home yesterday.", "portal",
     "clinical", "urgent"),
    ("reschedule", "Can we move his follow-up appointment to next Tuesday? Monday does not work for us.", "portal",
     "scheduling", "routine"),
    ("work-form", "I need a form for my employer that shows the dates I was in hospital.", "portal",
     "administrative", "routine"),
    ("wound", "The wound is leaking yellow fluid and it smells bad.", "voicemail", "clinical", "urgent"),
    ("visiting", "What are the visiting hours on the orthopaedic ward this weekend?", "portal", "scheduling",
     "routine"),
    ("fall", "He fell in the bathroom last night but says he feels fine.", "voicemail", "clinical", "urgent"),
    ("parking", "Is there parking for family members near the main entrance?", "portal", "other", "routine"),
    ("fever-zh", "我妈妈出院以后一直发烧，现在应该怎么办？", "voicemail", "clinical", "urgent"),
    ("thanks", "Thank you to all the nurses on the ward for the great care last week.", "portal", "other",
     "routine"),
    ("bill", "Could someone call me about the bill I received in the mail?", "portal", "administrative", "routine"),
    ("pills", "He is taking two blood pressure pills now. Should he still take the old one?", "portal", "clinical",
     "soon"),
    ("followup-zh", "我想预约下周的复诊时间。", "portal", "scheduling", "routine"),
]


def context(case_id: str) -> str:
    resources = [{"resourceType": "Encounter", "id": f"stay-eval-{case_id}", "status": "in-progress",
                  "class": {"code": "IMP"}, "subject": {"reference": "Patient/pat-eval"}},
                 {"resourceType": "Patient", "id": "pat-eval", "gender": "female",
                  "extension": [{"url": EXT + "age-years", "valueInteger": 72}], "name": [{"text": "[PERSON_1]"}]}]
    return json.dumps(resources, ensure_ascii=False, separators=(",", ":"))


def build() -> list[dict]:
    cases = []
    for case_id, message, channel, category, urgency in MESSAGES:
        cases.append({"id": f"authored-{case_id}", "source": "authored", "agent_id": AGENT,
                      "input": {"context": context(case_id), "channel": channel,
                                "rule_flags": ", ".join(red_flags(message)) or "none",
                                "message": untrusted.block("message", message)},
                      "expected": {"category": category, "urgency": urgency}})
    return cases


def main() -> None:
    path = evalsets.cases_path(AGENT)
    kept = [c for c in evalsets.load(AGENT) if c.get("source") != "authored"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for case in [*build(), *kept]:
            f.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"{path}: {len(MESSAGES)} authored cases, {len(kept)} kept from human review")


if __name__ == "__main__":
    main()
