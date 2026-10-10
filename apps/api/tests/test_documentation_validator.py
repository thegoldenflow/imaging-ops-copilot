"""WP7 (spec 7.3): the validators, the bilingual rendering and the readability checks, without the database.

The acceptance test of 7.3: five hallucinations injected into a draft are each blocked by the
DischargeSummaryValidator (an invented medication, a made-up source id, a diagnosis and a procedure that are not in
the record, an unprescribed drug in the hospital course). Also: one validator base class with per-document rules,
category warnings against the discharge medication table, the patient instructions validator against the signed
summary, English and Chinese rendered from one structure (same item keys and numbers), Traditional Chinese from
the character table, Flesch-Kincaid.
"""

from __future__ import annotations

import copy

import pytest

from app.modules.documentation import facts as F
from app.modules.documentation import instructions as I
from app.modules.documentation import phrases as P
from app.modules.documentation.evals import bilingual
from app.modules.documentation.hant import to_hant
from app.modules.documentation.models import InstructionsPlan
from app.modules.documentation.readability import chinese_checks, fk_grade, syllables
from app.modules.documentation.render import item_keys, render
from app.modules.documentation.validator import (DischargeSummaryValidator, HandoffValidator,
                                                 PatientInstructionsValidator, TraceabilityValidator)


def _src(ref, kind, name="", code="", names=None, detail=""):
    return F.Source(ref=ref, rtype=ref.split("/")[0], kind=kind, name=name, code=code, names=names or [name],
                    detail=detail or name)


SOURCES = {s.ref: s for s in [
    _src("Encounter/e1", "encounter", "Fracture of neck of femur", "5913000"),
    _src("Condition/c1", "diagnosis", "Fracture of neck of femur", "5913000", ["Fracture of neck of femur", "hip fracture"]),
    _src("Condition/c2", "diagnosis", "Atrial fibrillation", "49436004", ["Atrial fibrillation", "af"]),
    _src("Procedure/p1", "procedure", "Total replacement of hip", "52734007", ["Total replacement of hip",
                                                                              "hip replacement"]),
    _src("MedicationRequest/m1", "medication", "warfarin", "11289"),
    _src("MedicationRequest/m2", "medication", "acetaminophen", "161", ["acetaminophen", "paracetamol"]),
    _src("MedicationStatement/s1", "medication", "warfarin", "11289"),
    _src("DocumentReference/medrec-e1-discharge", "medrec", "Discharge medication table"),
    _src("Observation/o1", "observation", "vital signs", detail="Vital signs 08:00: BP 130/80 mmHg, heart rate 78 /min"),
    _src("CarePlan/cp1", "plan", "Discharge plan"),
]}
TABLE = {"new": [{"name": "acetaminophen", "rxcui": "161"}], "changed": [{"name": "warfarin", "rxcui": "11289"}],
         "stopped": [], "continued": []}


def _draft() -> dict:
    return {
        "reason_for_admission": {"text": "Admitted with a fracture of the neck of the femur after a fall.",
                                 "source_refs": ["Encounter/e1", "Condition/c1"]},
        "hospital_course": [{"date": "2026-10-05", "text": "Total replacement of hip. Warfarin held, then restarted.",
                             "source_refs": ["Procedure/p1", "MedicationRequest/m1"]}],
        "discharge_diagnoses": [{"name": "Hip fracture", "code": "", "text": "Hip fracture", "source_refs": ["Condition/c1"]},
                                {"name": "Atrial fibrillation", "code": "49436004", "text": "Atrial fibrillation",
                                 "source_refs": ["Condition/c2"]}],
        "procedures": [{"name": "Hip replacement", "code": "52734007", "date": "2026-10-05", "text": "Hip replacement",
                        "source_refs": ["Procedure/p1"]}],
        "medications": {
            "new": [{"name": "acetaminophen", "rxcui": "161", "dose": "1000 mg oral four times daily", "note": "",
                     "text": "acetaminophen 1000 mg", "source_refs": ["DocumentReference/medrec-e1-discharge",
                                                                     "MedicationRequest/m2"]}],
            "changed": [{"name": "warfarin", "rxcui": "11289", "dose": "3 mg oral once daily", "note": "was 5 mg",
                         "text": "warfarin 3 mg", "source_refs": ["MedicationRequest/m1", "MedicationStatement/s1"]}],
            "stopped": [], "continued": []},
        "pending_results": [],
        "follow_up": [{"code": "family_doctor", "when": "2026-10-16", "department": "Family doctor follow-up",
                       "location": "Family doctor's office", "text": "Family doctor in one week.",
                       "source_refs": ["CarePlan/cp1"]}],
        "red_flags": [{"text": "Bleeding that does not stop, or black stools.", "source_refs": ["MedicationRequest/m1"]}],
    }


def test_a_clean_draft_traces_every_item():
    report = DischargeSummaryValidator(SOURCES, TABLE).validate(_draft())
    assert not report.blocking, report.issues
    assert report.checked == report.traceable == 9


INJECTED = [
    ("invented medication citing another drug's order", "medications", "new",
     {"name": "rivaroxaban", "rxcui": "1114195", "dose": "20 mg", "note": "", "text": "rivaroxaban 20 mg",
      "source_refs": ["MedicationRequest/m1"]}, {"untraceable_medication"}),
    ("medication citing a made-up id", "medications", "continued",
     {"name": "digoxin", "rxcui": "3407", "dose": "0.125 mg", "note": "", "text": "digoxin",
      "source_refs": ["MedicationRequest/fake-1"]}, {"unknown_ref", "untraceable_medication"}),
    ("diagnosis not in the record", "discharge_diagnoses", None,
     {"name": "Pulmonary embolism", "code": "59282003", "text": "Pulmonary embolism", "source_refs": ["Condition/c1"]},
     {"untraceable_diagnosis"}),
    ("procedure without a source", "procedures", None,
     {"name": "Coronary artery bypass grafting", "code": "232717009", "date": "", "text": "CABG", "source_refs": []},
     {"no_source", "untraceable_procedure"}),
    ("unprescribed drug in the course", "hospital_course", None,
     {"date": "2026-10-06", "text": "Metronidazole was started for a wound infection.", "source_refs": ["Observation/o1"]},
     {"untraceable_drug"}),
]


@pytest.mark.parametrize("title,section,category,item,codes", INJECTED, ids=[x[0] for x in INJECTED])
def test_the_validator_blocks_each_injected_hallucination(title, section, category, item, codes):
    draft = _draft()
    group = draft[section][category] if category else draft[section]
    group.append(copy.deepcopy(item))
    path = f"{section}.{category}[{len(group) - 1}]" if category else f"{section}[{len(group) - 1}]"
    report = DischargeSummaryValidator(SOURCES, TABLE).validate(draft)
    hits = {i.code for i in report.issues if i.path == path and i.severity == "red"}
    assert report.blocking and codes <= hits, (title, report.issues)


def test_all_five_injected_hallucinations_are_blocked_together():
    draft = _draft()
    for _title, section, category, item, _codes in INJECTED:
        (draft[section][category] if category else draft[section]).append(copy.deepcopy(item))
    report = DischargeSummaryValidator(SOURCES, TABLE).validate(draft)
    red_paths = {i.path for i in report.red()}
    assert len(red_paths) == 5 and report.traceable == report.checked - 5


def test_a_medication_citing_a_condition_is_the_wrong_source_type():
    draft = _draft()
    draft["medications"]["new"].append({"name": "metoprolol", "rxcui": "", "dose": "", "note": "", "text": "metoprolol",
                                        "source_refs": ["Condition/c2"]})
    codes = {i.code for i in DischargeSummaryValidator(SOURCES, TABLE).validate(draft).red()}
    assert {"wrong_source_type", "untraceable_medication"} <= codes


def test_category_against_the_discharge_medication_table_is_a_warning():
    draft = _draft()
    draft["medications"]["continued"] = draft["medications"]["changed"]
    draft["medications"]["changed"] = []
    report = DischargeSummaryValidator(SOURCES, TABLE).validate(draft)
    assert not report.blocking
    assert any(i.code == "category_differs" and i.severity == "amber" for i in report.issues)


def test_advice_about_vitamin_k_is_not_a_prescription():
    draft = _draft()
    draft["red_flags"].append({"text": "Keep foods rich in vitamin K about the same each week.",
                               "source_refs": ["MedicationRequest/m1"]})
    assert not DischargeSummaryValidator(SOURCES, TABLE).validate(draft).blocking


def test_subclasses_share_the_base_class():
    for cls in (DischargeSummaryValidator, PatientInstructionsValidator, HandoffValidator):
        assert issubclass(cls, TraceabilityValidator) and cls.RULES
    handoff = {"situation": {"text": "Heart rate 78, BP 130/80.", "source_refs": ["Observation/o1"]},
               "background": [{"text": "On warfarin.", "source_refs": ["MedicationRequest/m1"]}],
               "assessment": [{"text": "Heart rate 99.", "source_refs": ["Observation/o1"]}],
               "recommendation": [{"text": "Start digoxin.", "source_refs": ["Encounter/e1"]}]}
    report = HandoffValidator(SOURCES).validate(handoff)
    assert any(i.code == "number_not_in_source" and i.value == "99" for i in report.issues)
    assert any(i.code == "untraceable_drug" for i in report.red())


# ---------- patient instructions ----------


def _content():
    items = I.summary_items(_draft())
    plan = InstructionsPlan.model_validate({
        "reason_items": ["discharge_diagnoses[0]", "procedures[0]"],
        "medicines": [{"item": "medications.changed[0]", "purpose": "blood_thinner", "times": ["evening"],
                       "cautions": ["blood_test", "leafy_greens"]},
                      {"item": "medications.new[0]", "purpose": "pain", "times": ["morning", "noon", "evening",
                                                                                  "bedtime"], "cautions": []}],
        "follow_ups": [{"item": "follow_up[0]", "department": "family_doctor"}],
        "red_flags": [{"code": "bleeding_signs", "because": ["medications.changed[0]"]},
                      {"code": "wound_infection", "because": ["procedures[0]"]}],
        "daily_care": [{"code": "steady_greens", "because": []}, {"code": "use_walker", "because": []}]})
    content, notes = I.build(plan, items, summary_ref="DocumentReference/dsum-e1", unit="MEDA")
    return content, items, notes


def test_instructions_copy_the_facts_from_the_summary_and_render_three_languages_alike():
    content, items, notes = _content()
    assert not notes
    warfarin = next(m for m in content.medicines if m.name == "warfarin")
    assert warfarin.dose == "3 mg" and warfarin.category == "changed"
    report, renderings = I.validate_and_render(content, items, patient_conditions=["5913000"])
    assert not report.blocking, report.issues
    assert item_keys(renderings["en"]) == item_keys(renderings["zh-Hans"]) == item_keys(renderings["zh-Hant"])
    assert bilingual({"renderings": renderings})["consistent"]
    zh = renderings["zh-Hans"]["text"]
    assert "华法林" in zh and "3毫克" in zh and "10月16日" in zh
    assert "華法林" in renderings["zh-Hant"]["text"]
    assert renderings["zh-Hans"]["readability"]["no_abbreviations"]
    assert renderings["zh-Hans"]["readability"]["terms_explained"]


def test_instructions_validator_refuses_what_the_summary_does_not_say():
    content, items, _ = _content()
    data = content.model_dump(mode="json", by_alias=True)
    data["medicines"][0]["name"] = "apixaban"
    data["medicines"].append({"item": "medications.new[5]", "category": "new", "name": "x", "purpose": "pain",
                              "times": [], "cautions": [], "source_refs": []})
    data["follow_ups"][0]["when"] = "2026-12-01"
    data["red_flags"].append({"code": "made_up", "because": []})
    report = PatientInstructionsValidator(items, P.menus()).validate(data)
    codes = {i.code for i in report.red()}
    assert {"untraceable_medication", "unknown_item", "date_differs", "unknown_code"} <= codes


def test_a_medicine_the_model_left_out_is_added_by_rule():
    items = I.summary_items(_draft())
    plan = InstructionsPlan.model_validate({"reason_items": [], "medicines": [], "follow_ups": [], "red_flags": [],
                                            "daily_care": []})
    content, notes = I.build(plan, items, summary_ref="DocumentReference/dsum-e1", unit="MEDA")
    assert {m.name for m in content.medicines} == {"acetaminophen", "warfarin"} and len(notes) == 3
    assert next(m for m in content.medicines if m.name == "acetaminophen").times == ["morning", "noon", "evening",
                                                                                     "bedtime"]
    assert I.guard(InstructionsPlan.model_validate({"medicines": [{"item": "medications.new[9]", "purpose": "magic"}]}),
                   items)


# ---------- language tools ----------


def test_traditional_chinese_from_the_character_table():
    assert to_hant("华法林") == "華法林"
    assert to_hant("心房颤动") == "心房顫動"
    assert to_hant("复诊安排") == "複診安排"
    assert to_hant("一种药物，可用于心房颤动等") == "一種藥物，可用於心房顫動等"
    assert to_hant("头发干燥") == "頭髮乾燥"


def test_flesch_kincaid():
    assert syllables("medicine") == 3 and syllables("take") == 1 and syllables("bleeding") == 2
    easy = fk_grade("Take one pill each day. Drink water. Call us if you feel sick.")
    hard = fk_grade("Anticoagulation necessitates meticulous international normalized ratio monitoring "
                    "considering pharmacokinetic interactions.")
    assert easy["grade"] < 6 and easy["in_target"] and hard["grade"] > 8 and not hard["in_target"]


def test_chinese_checks_flag_latin_letters_and_unexplained_terms():
    out = chinese_checks("请检查INR，每天5 mg。", [{"key": "drug:1", "explained": False}])
    assert out["abbreviations"] == ["INR", "mg"] and out["unexplained_terms"] == ["drug:1"]


def test_every_phrase_exists_in_three_languages_without_latin_letters_in_chinese():
    import re

    tables = [P.TITLES, P.REASON, P.TIMES, P.PURPOSES, P.CAUTIONS, P.CATEGORY_NOTES, P.DEPARTMENTS, P.FOLLOW_UP,
              P.ACTIONS, P.UNIT_NAMES, {k: v[0] for k, v in P.RED_FLAGS.items()},
              {k: v[1] for k, v in P.DAILY_CARE.items()}]
    for table in tables:
        for key, phrase in table.items():
            assert set(phrase) == set(P.LANGS), key
            for lang in ("zh-Hans", "zh-Hant"):
                text = re.sub(r"\{\w+\}", "", phrase[lang])
                assert not re.search(r"[A-Za-z]", text), (key, lang, text)
    sample = render(_content()[0], "en")
    assert [b["key"] for b in sample["blocks"]][:5] == ["reason", "medicines", "follow_up", "red_flags", "daily_care"]
