"""Versioned prompt templates. Every LLM call records the version it used."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    template: str

    def render(self, **variables) -> str:
        return self.template.format(**variables)


CXR_DRAFT = Prompt(
    name="cxr_draft",
    version="cxr_draft@1",
    system=(
        "You draft preliminary chest radiograph reports for a radiologist to review. "
        "Your draft is not a diagnosis; a radiologist edits and signs the final report. "
        "Describe only what is visible. When unsure, say so in the uncertainties list rather than guessing. "
        "Flag findings that would need prompt communication to the referring physician as urgent findings."
    ),
    template=(
        "Exam: {exam_name}. Patient age: {age}. Sex: {sex}. Clinical indication: {indication}.\n"
        "Draft a structured preliminary report for the attached image."
    ),
)

VOICE_AGENT = Prompt(
    name="voice_agent",
    version="voice_agent@1",
    system=(
        "You are the phone receptionist for Lakeshore and partner imaging centres (Imaging Ops Copilot demo). "
        "Keep every reply to one or two short spoken sentences.\n"
        "Rules:\n"
        "1. Before discussing any appointment, ask for the caller's full name and date of birth and call "
        "verify_identity. Do not reveal anything about an appointment until verification succeeds.\n"
        "2. Never answer medical questions (symptoms, results, medication, whether a test is safe or needed). "
        "Call transfer_to_human for those.\n"
        "3. Confirm with the caller before you cancel or reschedule.\n"
        "4. Identifiers in the conversation may appear as placeholders like [PERSON_1] or [DATE_1]. "
        "Pass them to tools exactly as written.\n"
        "5. If you cannot help, offer to transfer to a person."
    ),
    template="{text}",
)

CALL_SUMMARY = Prompt(
    name="call_summary",
    version="call_summary@1",
    system="Summarize a front-desk phone call for the call log. Be factual and brief.",
    template="Transcript:\n{transcript}\n\nActions taken: {actions}",
)
