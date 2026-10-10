"""The three documents' structured content (spec 7.3) and the validator's report.

Every fact item carries `source_refs[]`: the FHIR resources it comes from (`Type/id`),
copied by the model from the id lists it was given. Empty strings stand for "not
recorded" (the model never fills in what the input does not hold); the providers'
structured outputs need every field present.

- DischargeSummary: the spec's schema `{reason_for_admission, hospital_course,
  discharge_diagnoses[], procedures[], medications{new[], changed[], stopped[],
  continued[]}, pending_results[], follow_up[], red_flags[]}`; hospital_course in
  dated segments, each with its sources. SegmentNotes is the first stage of the
  long-context strategy (one per chunk of resources).
- InstructionsPlan is what the model chooses for the patient instructions (codes
  from the menus in phrases.py, per item of the signed summary); InstructionsContent
  is the one structure both languages are rendered from (render.py).
- Handoff: SBAR, four sections.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Ref = str  # "Type/id"


# ---------- discharge summary ----------


class Fact(BaseModel):
    text: str = Field(max_length=600)
    source_refs: list[Ref] = Field(default_factory=list, max_length=30)


class CourseSegment(BaseModel):
    date: str = Field(max_length=40)  # the day (a date token for the model, restored to YYYY-MM-DD)
    text: str = Field(max_length=900)
    source_refs: list[Ref] = Field(default_factory=list, max_length=40)


class Diagnosis(Fact):
    name: str = Field(max_length=200)
    code: str = Field(default="", max_length=40)  # SNOMED CT, as in the Condition ("" when not given)


class ProcedureDone(Fact):
    name: str = Field(max_length=200)
    code: str = Field(default="", max_length=40)
    date: str = Field(default="", max_length=40)


class MedicationLine(Fact):
    name: str = Field(max_length=120)
    rxcui: str = Field(default="", max_length=20)
    dose: str = Field(default="", max_length=200)  # the discharge dosage text ("" for a stopped medication)
    note: str = Field(default="", max_length=300)  # what changed / why it stopped, as recorded


class Medications(BaseModel):
    new: list[MedicationLine] = Field(default_factory=list, max_length=40)
    changed: list[MedicationLine] = Field(default_factory=list, max_length=40)
    stopped: list[MedicationLine] = Field(default_factory=list, max_length=40)
    continued: list[MedicationLine] = Field(default_factory=list, max_length=40)


class PendingResult(Fact):
    test: str = Field(max_length=200)


class FollowUp(Fact):
    code: str = Field(default="", max_length=60)  # the care plan activity's code (department), "" if none
    when: str = Field(default="", max_length=40)  # date (YYYY-MM-DD after restore) or "" when not set
    department: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=200)


class DischargeSummary(BaseModel):
    reason_for_admission: Fact
    hospital_course: list[CourseSegment] = Field(default_factory=list, max_length=30)
    discharge_diagnoses: list[Diagnosis] = Field(default_factory=list, max_length=20)
    procedures: list[ProcedureDone] = Field(default_factory=list, max_length=20)
    medications: Medications = Field(default_factory=Medications)
    pending_results: list[PendingResult] = Field(default_factory=list, max_length=20)
    follow_up: list[FollowUp] = Field(default_factory=list, max_length=20)
    red_flags: list[Fact] = Field(default_factory=list, max_length=20)


NOTE_KINDS = ("reason", "diagnosis", "procedure", "medication", "observation", "result", "report", "task", "plan",
              "pending", "course")


class SegmentNote(BaseModel):
    kind: Literal["reason", "diagnosis", "procedure", "medication", "observation", "result", "report", "task",
                  "plan", "pending", "course"]
    date: str = Field(default="", max_length=40)
    name: str = Field(default="", max_length=200)
    code: str = Field(default="", max_length=40)
    text: str = Field(max_length=600)
    source_refs: list[Ref] = Field(default_factory=list, max_length=30)


class SegmentNotes(BaseModel):
    notes: list[SegmentNote] = Field(default_factory=list, max_length=80)


# ---------- patient instructions ----------


class MedicineChoice(BaseModel):
    item: str = Field(max_length=60)  # the summary item: "medications.changed[0]"
    purpose: str = Field(max_length=60)  # a PURPOSES code
    times: list[str] = Field(default_factory=list, max_length=6)  # TIMES codes
    cautions: list[str] = Field(default_factory=list, max_length=6)  # CAUTIONS codes


class FollowUpChoice(BaseModel):
    item: str = Field(max_length=60)  # "follow_up[1]"
    department: str = Field(max_length=60)  # a DEPARTMENTS code


class RedFlagChoice(BaseModel):
    code: str = Field(max_length=60)  # a RED_FLAGS code
    because: list[str] = Field(default_factory=list, max_length=6)  # summary items that call for it


class CareChoice(BaseModel):
    code: str = Field(max_length=60)  # a DAILY_CARE code
    because: list[str] = Field(default_factory=list, max_length=6)


class InstructionsPlan(BaseModel):
    reason_items: list[str] = Field(default_factory=list, max_length=6)  # diagnoses / procedures shown as the reason
    medicines: list[MedicineChoice] = Field(default_factory=list, max_length=40)
    follow_ups: list[FollowUpChoice] = Field(default_factory=list, max_length=20)
    red_flags: list[RedFlagChoice] = Field(default_factory=list, max_length=12)
    daily_care: list[CareChoice] = Field(default_factory=list, max_length=15)


class InstrReason(BaseModel):
    item: str  # summary item ("discharge_diagnoses[0]", "procedures[0]")
    kind: Literal["diagnosis", "procedure"]
    code: str = ""
    name: str = ""  # as in the signed summary
    source_refs: list[Ref] = Field(default_factory=list)


class InstrMedicine(BaseModel):
    item: str
    category: Literal["new", "changed", "stopped", "continued"]
    name: str  # as in the signed summary (English generic name)
    rxcui: str = ""
    dose: str = ""  # "5 mg"
    purpose: str
    times: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    source_refs: list[Ref] = Field(default_factory=list)


class InstrFollowUp(BaseModel):
    item: str
    department: str  # a DEPARTMENTS code
    when: str = ""  # YYYY-MM-DD or ""
    location: str = ""  # as recorded (shown as written in every language)
    source_refs: list[Ref] = Field(default_factory=list)


class InstrFlag(BaseModel):
    code: str
    because: list[str] = Field(default_factory=list)


class InstructionsContent(BaseModel):
    schema_id: str = Field(default="patient-instructions@1", alias="schema")
    summary: Ref  # the signed discharge summary it was written from
    reason: list[InstrReason] = Field(default_factory=list)
    medicines: list[InstrMedicine] = Field(default_factory=list)
    follow_ups: list[InstrFollowUp] = Field(default_factory=list)
    red_flags: list[InstrFlag] = Field(default_factory=list)
    daily_care: list[InstrFlag] = Field(default_factory=list)
    unit: str = ""  # the unit to call (phone in phrases.UNIT_PHONES)

    model_config = {"populate_by_name": True}


# ---------- SBAR handoff ----------


class Handoff(BaseModel):
    situation: Fact
    background: list[Fact] = Field(default_factory=list, max_length=12)
    assessment: list[Fact] = Field(default_factory=list, max_length=16)
    recommendation: list[Fact] = Field(default_factory=list, max_length=12)


# ---------- validation ----------


class Issue(BaseModel):
    path: str  # "medications.new[2]", "hospital_course[1]", "situation"
    severity: Literal["red", "amber"]  # red: untraceable, blocks signing; amber: a warning
    code: str  # unknown_ref, wrong_type, untraceable_drug, untraceable_diagnosis, untraceable_procedure, ...
    message: str
    value: str = ""


class ValidationReport(BaseModel):
    validator: str
    checked: int = 0  # fact items checked
    traceable: int = 0  # items without a red issue
    issues: list[Issue] = Field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return any(i.severity == "red" for i in self.issues)

    def red(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "red"]

    def summary(self) -> str:
        red = len({i.path for i in self.red()})
        amber = len([i for i in self.issues if i.severity == "amber"])
        return (f"{red} untraceable item(s) block signing" if red else "every item traces to its sources") + \
            (f"; {amber} warning(s)" if amber else "")
