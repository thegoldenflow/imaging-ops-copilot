"""The patient instructions' structure from the signed discharge summary (spec 7.3), without the model.

- `summary_items(content)`: the signed summary as items by path (`medications.changed[0]`), the only input.
- `build(plan, items, ...)`: the model's choices (codes per item) joined with the facts copied from the summary
  (names, doses, categories, dates, locations): the model never supplies a fact. A summary medicine the model
  left out is added with a rule-based time of day and purpose "other" (and a warning), so no medicine goes
  missing from the patient's table.
- `template(items, ...)`: without AI consent (6.3) the nurse gets the structure with the summary's medicines and
  follow-ups copied in (no model, no choice made for her), everything else to fill in.
- `guard(plan, items)`: what in the model's answer is not an item or not a menu code.
- `validate_and_render(content, items, ...)`: the validator's report and the three renderings.
"""

from __future__ import annotations

from app.ehr import codes as C
from app.modules.documentation import phrases as P
from app.modules.documentation.models import (InstrFlag, InstrFollowUp, InstrMedicine, InstrReason,
                                              InstructionsContent, InstructionsPlan, ValidationReport)
from app.modules.documentation.render import dose_amount, render
from app.modules.documentation.validator import PatientInstructionsValidator

LIST_FIELDS = ("discharge_diagnoses", "procedures", "follow_up", "red_flags", "pending_results")
MED_CATEGORIES = ("new", "changed", "stopped", "continued")


def summary_items(content: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name in LIST_FIELDS:
        for i, item in enumerate(content.get(name) or []):
            out[f"{name}[{i}]"] = item
    for cat in MED_CATEGORIES:
        for i, item in enumerate((content.get("medications") or {}).get(cat) or []):
            out[f"medications.{cat}[{i}]"] = item
    return out


def model_items(items: dict[str, dict]) -> list[dict]:
    """What the model sees of each item (de-identified by the caller)."""
    out = []
    for path, item in items.items():
        row = {"item": path, "text": item.get("text") or ""}
        for key in ("name", "code", "rxcui", "dose", "note", "when", "department", "location", "test"):
            if item.get(key):
                row[key] = item[key]
        out.append(row)
    return out


EVENING_CLASSES = {"statin", "anticoagulant"}  # taken in the evening by habit when once daily


def times_from_dosage(dose_text: str, drug_class: str) -> list[str]:
    """Times of day from a dosage text ("twice daily" -> morning and evening; "as needed")."""
    t = (dose_text or "").lower()
    if "as needed" in t:
        return ["as_needed"]
    if "four times" in t:
        return ["morning", "noon", "evening", "bedtime"]
    if "three times" in t:
        return ["morning", "noon", "evening"]
    if "twice" in t:
        return ["morning", "evening"]
    return ["evening"] if drug_class in EVENING_CLASSES else ["morning"]


def default_times(dose_text: str, rxcui: str) -> list[str]:
    return times_from_dosage(dose_text, C.MEDICATIONS.get(rxcui or "", ("", ""))[1])


def guard(plan: InstructionsPlan, items: dict[str, dict]) -> list[str]:
    menus = P.menus()
    problems = []
    for m in plan.medicines:
        if not m.item.startswith("medications.") or m.item not in items:
            problems.append(f"medicine {m.item!r} is not a medication item of the summary")
        if m.purpose not in menus["purposes"]:
            problems.append(f"{m.item}: purpose {m.purpose!r} is not a PURPOSES code")
        problems += [f"{m.item}: {t!r} is not a TIMES code" for t in m.times if t not in menus["times"]]
        problems += [f"{m.item}: {c!r} is not a CAUTIONS code" for c in m.cautions if c not in menus["cautions"]]
    for f in plan.follow_ups:
        if f.item not in items or not f.item.startswith("follow_up"):
            problems.append(f"follow-up {f.item!r} is not a follow-up item of the summary")
        if f.department not in menus["departments"]:
            problems.append(f"{f.item}: {f.department!r} is not a DEPARTMENTS code")
    for r in plan.reason_items:
        if r not in items or not r.startswith(("discharge_diagnoses", "procedures")):
            problems.append(f"reason item {r!r} is not a diagnosis or procedure of the summary")
    for name, menu, choices in (("red flag", "red_flags", plan.red_flags), ("daily care", "daily_care", plan.daily_care)):
        for c in choices:
            if c.code not in menus[menu]:
                problems.append(f"{name} {c.code!r} is not a menu code")
            problems += [f"{name} {c.code}: {b!r} is not a summary item" for b in c.because if b not in items]
    return problems


def _medicine(path: str, item: dict, *, purpose: str, times: list[str], cautions: list[str]) -> InstrMedicine:
    category = path.split(".")[1].split("[")[0]
    return InstrMedicine(item=path, category=category, name=item.get("name") or "", rxcui=item.get("rxcui") or "",
                         dose=dose_amount(item.get("dose") or ""), purpose=purpose,
                         times=[] if category == "stopped" else times, cautions=[] if category == "stopped" else cautions,
                         source_refs=list(item.get("source_refs") or []))


def _follow_up(path: str, item: dict, department: str) -> InstrFollowUp:
    return InstrFollowUp(item=path, department=department, when=(item.get("when") or "")[:10],
                         location=item.get("location") or (item.get("department") if department == "other" else "")
                         or "", source_refs=list(item.get("source_refs") or []))


def _reason(path: str, item: dict) -> InstrReason:
    kind = "diagnosis" if path.startswith("discharge_diagnoses") else "procedure"
    return InstrReason(item=path, kind=kind, code=item.get("code") or "", name=item.get("name") or "",
                       source_refs=list(item.get("source_refs") or []))


def _department(item: dict) -> str:
    code = item.get("code") or ""
    return code if code in P.DEPARTMENTS and code != "other" else "other"


def build(plan: InstructionsPlan, items: dict[str, dict], *, summary_ref: str, unit: str) -> tuple[InstructionsContent,
                                                                                                    list[str]]:
    """(content, notes): the model's valid choices with the summary's facts; what the module added."""
    menus = P.menus()
    notes = []
    medicines, chosen = [], set()
    for m in plan.medicines:
        if m.item in items and m.item.startswith("medications.") and m.item not in chosen:
            chosen.add(m.item)
            medicines.append(_medicine(m.item, items[m.item], purpose=m.purpose if m.purpose in menus["purposes"]
                                       else "other", times=[t for t in m.times if t in menus["times"]],
                                       cautions=[c for c in m.cautions if c in menus["cautions"]]))
    for path, item in items.items():
        if path.startswith("medications.") and path not in chosen:
            medicines.append(_medicine(path, item, purpose="other", cautions=[],
                                       times=default_times(item.get("dose") or "", item.get("rxcui") or "")))
            notes.append(f"{item.get('name')}: added by the module (the AI left it out); check what it is for")
    follow_ups, done = [], set()
    for f in plan.follow_ups:
        if f.item in items and f.item.startswith("follow_up") and f.item not in done:
            done.add(f.item)
            follow_ups.append(_follow_up(f.item, items[f.item], f.department if f.department in menus["departments"]
                                         else "other"))
    for path, item in items.items():
        if path.startswith("follow_up") and path not in done:
            follow_ups.append(_follow_up(path, item, _department(item)))
            notes.append(f"{path}: follow-up added by the module (the AI left it out)")
    reason = [_reason(r, items[r]) for r in dict.fromkeys(plan.reason_items)
              if r in items and r.startswith(("discharge_diagnoses", "procedures"))]
    if not reason:
        reason = [_reason(p, i) for p, i in items.items() if p.startswith("discharge_diagnoses")][:2]
    flags = [InstrFlag(code=c.code, because=[b for b in c.because if b in items]) for c in plan.red_flags
             if c.code in menus["red_flags"]]
    care = [InstrFlag(code=c.code, because=[b for b in c.because if b in items]) for c in plan.daily_care
            if c.code in menus["daily_care"]]
    content = InstructionsContent(summary=summary_ref, reason=reason, medicines=medicines, follow_ups=follow_ups,
                                  red_flags=list({f.code: f for f in flags}.values()),
                                  daily_care=list({c.code: c for c in care}.values()), unit=unit)
    return content, notes


def template(items: dict[str, dict], *, summary_ref: str, unit: str) -> InstructionsContent:
    """No AI: the summary's medicines and follow-ups copied in for the nurse to complete."""
    medicines = [_medicine(p, i, purpose="other", cautions=[], times=default_times(i.get("dose") or "",
                                                                                     i.get("rxcui") or ""))
                 for p, i in items.items() if p.startswith("medications.")]
    follow_ups = [_follow_up(p, i, _department(i)) for p, i in items.items() if p.startswith("follow_up")]
    reason = [_reason(p, i) for p, i in items.items() if p.startswith("discharge_diagnoses")][:2]
    return InstructionsContent(summary=summary_ref, reason=reason, medicines=medicines, follow_ups=follow_ups,
                               unit=unit)


def validate_and_render(content: InstructionsContent, items: dict[str, dict], *, patient_conditions: list[str]
                        ) -> tuple[ValidationReport, dict[str, dict]]:
    validator = PatientInstructionsValidator(items, P.menus())
    data = content.model_dump(mode="json", by_alias=True)
    report = validator.validate(data)
    renderings = {lang: render(content, lang, patient_conditions=patient_conditions) for lang in P.LANGS}
    report.issues += validator.scan_rendered(renderings["en"]["text"])
    en = renderings["en"]["readability"]
    if not en["in_target"]:
        report.issues.append(_amber("readability", "readability_grade", str(en["grade"]),
                                    f"English reading level grade {en['grade']} (target 6-8 or easier)"))
    for lang in ("zh-Hans", "zh-Hant"):
        check = renderings[lang]["readability"]
        if check["abbreviations"]:
            report.issues.append(_amber("readability", "chinese_abbreviation", ", ".join(check["abbreviations"]),
                                        f"Latin letters in the {lang} text: {', '.join(check['abbreviations'])}"))
        if check["unexplained_terms"]:
            report.issues.append(_amber("readability", "term_unexplained", ", ".join(check["unexplained_terms"]),
                                        f"terms without an explanation in {lang}: "
                                        f"{', '.join(check['unexplained_terms'])}"))
    return report, renderings


def _amber(path: str, code: str, value: str, message: str):
    from app.modules.documentation.models import Issue

    return Issue(path=path, severity="amber", code=code, value=value, message=message)
