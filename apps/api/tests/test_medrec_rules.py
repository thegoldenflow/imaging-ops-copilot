"""WP6 (spec 7.2): the medication reconciliation rule layer (omission, dose or frequency change, duplicate therapy,
new medication and its indication) and the discharge medication table, on plain facts.

`exercise_everything` runs every case once; tests/test_med_rules.py calls it under its line-coverage check.
"""

from __future__ import annotations

from app.modules.med_safety import reconcile as R
from app.modules.med_safety.facts import ConditionFact
from tests.test_med_rules import med

AFIB = ConditionFact("Condition/c-afib", "49436004", "Atrial fibrillation")
FRACTURE = ConditionFact("Condition/c-hip", "5913000", "Fracture of neck of femur", "encounter-diagnosis")
OLD = ConditionFact("Condition/c-old", "233604007", "Pneumonia", active=False)
CONDITIONS = [AFIB, FRACTURE, OLD]


def by_id(items):
    return {i.id: i for i in items}


# ---------- admission ----------


def admission_case():
    home = [med("11289", source="home"), med("17767", source="home"), med("6918", source="home"),
            med("29046", source="home"), med("7646", source="home"), med("6809", source="home", status="stopped")]
    orders = [med("11289"),  # warfarin continued
              med("6918", dose=25),  # metoprolol dose changed
              med("29046", per_day=2),  # lisinopril frequency changed
              med("40790"),  # pantoprazole for home omeprazole (same class)
              med("67108", reasons=("5913000",)),  # enoxaparin, the order names the fracture
              med("7258"),  # naproxen, no diagnosis named
              med("161", reasons=("233604007",)),  # acetaminophen naming an inactive diagnosis
              med("1191"), med("32968"),  # aspirin and clopidogrel: duplicate antiplatelets
              med("1191"),  # the same ingredient twice: one line
              med("7052", status="cancelled")]  # a cancelled order is not on the list
    return home, orders


def test_admission_rows():
    home, orders = admission_case()
    items = by_id(R.compare_admission(home, orders, CONDITIONS))
    assert items["ok-11289"].type == "match" and not items["ok-11289"].discrepancy
    assert items["om-17767"].type == "omission" and items["om-17767"].severity == "moderate"
    assert "not ordered on admission" in items["om-17767"].explanation
    assert items["om-7646"].type == "omission" and "Pantoprazole of the same class is ordered" in items["om-7646"].explanation
    assert items["dc-6918"].type == "dose_change" and "dose changed" in items["dc-6918"].explanation
    assert "frequency changed" in items["dc-29046"].explanation
    assert items["new-67108"].type == "new" and items["new-67108"].indication == "rule"
    assert items["new-67108"].indication_ref == "Condition/c-hip"
    assert items["new-7258"].indication == "pending" and items["new-161"].indication == "pending"
    assert items["dup-antiplatelet"].type == "duplicate_therapy" and items["dup-antiplatelet"].severity == "major"
    assert "om-6809" not in items and "new-7052" not in items  # stopped home medication, cancelled order
    assert [i.id for i in R.candidates(list(items.values()))] == ["new-40790", "new-7258", "new-161", "new-1191",
                                                            "new-32968"]


def test_dose_and_frequency_both_changed():
    items = R.compare_admission([med("6918", source="home")], [med("6918", dose=25, per_day=1)], [])
    assert "dose and frequency changed" in items[0].explanation


def test_high_risk_omission_is_major():
    items = R.compare_admission([med("11289", source="home")], [], [])
    assert items[0].type == "omission" and items[0].severity == "major"


# ---------- the AI's indication judgements ----------


def test_judgements_settle_new_medications():
    home, orders = admission_case()
    items = R.compare_admission(home, orders, CONDITIONS)
    judged = {
        "new-7258": R.Judgement(item_id="new-7258", indicated=True, indication_ref="Condition/c-hip", confidence=0.9,
                                explanation="Naproxen for fracture pain."),
        "new-161": R.Judgement(item_id="new-161", indicated=True, indication_ref="Condition/c-hip", confidence=0.5,
                               explanation="Probably pain."),
    }
    settled = by_id(R.apply_judgements(items, judged, 0.7, condition_refs={c.ref for c in CONDITIONS}))
    assert settled["new-7258"].type == "new" and settled["new-7258"].source == "ai"
    assert settled["new-7258"].confidence == 0.9 and "Condition/c-hip" in settled["new-7258"].evidence_refs
    assert settled["new-161"].type == "new_without_indication" and "below the confidence threshold" in \
        settled["new-161"].explanation
    assert settled["new-67108"].indication == "rule"  # already settled by the rule


def test_judgement_not_indicated_and_made_up_reference():
    items = R.compare_admission([], [med("40790"), med("7258")], CONDITIONS)
    judged = {"new-40790": R.Judgement(item_id="new-40790", indicated=False, confidence=0.8,
                                       explanation="No acid-related diagnosis."),
              "new-7258": R.Judgement(item_id="new-7258", indicated=True, indication_ref="Condition/made-up",
                                      confidence=0.95, explanation="Pain.")}
    settled = by_id(R.apply_judgements(items, judged, 0.7, condition_refs={c.ref for c in CONDITIONS}))
    assert settled["new-40790"].type == "new_without_indication" and settled["new-40790"].confidence == 0.8
    assert settled["new-7258"].type == "new_without_indication"  # the reference is not one of the conditions
    assert settled["new-7258"].indication_ref is None
    trusting = by_id(R.apply_judgements(items, judged, 0.7))  # no condition list given: the reference stands
    assert trusting["new-7258"].type == "new" and trusting["new-7258"].indication_ref == "Condition/made-up"


def test_without_the_ai_new_medications_stay_discrepancies():
    items = R.compare_admission([], [med("40790")], [])
    settled = R.apply_judgements(items, {}, 0.7, unavailable="no ai_processing consent")
    assert settled[0].type == "new_without_indication" and settled[0].source == "rule"
    assert "no ai_processing consent" in settled[0].explanation and settled[0].confidence is None
    assert "no AI judgement" in R.apply_judgements(items, {}, 0.7)[0].explanation


def test_handling_and_severity():
    items = R.compare_admission([med("11289", source="home"), med("17767", source="home")], [], [])
    assert R.severity_of(items) == "major" and not items[0].handled
    items[0].action = "accept"
    assert items[0].handled and R.severity_of(items) == "moderate"
    items[1].action = "modify"
    assert R.severity_of(items) == "none"


# ---------- discharge ----------


def discharge_case():
    home = [med("17767", source="home"),  # amlodipine: continued
            med("4603", source="home"),  # furosemide: changed at discharge
            med("7258", source="home"),  # naproxen: stopped
            med("11289", source="home")]  # warfarin: not given in hospital, not prescribed: stopped
    inpatient = [med("17767"), med("4603"), med("7258"),
                 med("2193", route="intravenous"),  # ceftriaxone IV: hospital use
                 med("3423", prn=True),  # hydromorphone as needed: hospital use
                 med("6918"),  # metoprolol oral, not on discharge: an omission
                 med("67108", reasons=("5913000",)),  # enoxaparin, continued at discharge
                 med("26225")]  # ondansetron, dose changed at discharge
    discharge = [med("17767", source="discharge"), med("4603", dose=80, source="discharge"),
                 med("67108", reasons=("5913000",), source="discharge"),
                 med("26225", dose=8, source="discharge"),
                 med("723", source="discharge"),  # amoxicillin: new at discharge
                 med("83367", source="discharge"), med("36567", source="discharge")]  # two statins
    return home, inpatient, discharge


def test_discharge_rows():
    items = by_id(R.compare_discharge(*discharge_case(), CONDITIONS))
    assert items["ok-17767"].type == "match" and items["ok-17767"].inpatient is not None
    assert items["dc-4603"].type == "dose_change" and "at discharge" in items["dc-4603"].explanation
    assert items["dc-26225"].type == "dose_change" and items["dc-26225"].home is None
    assert "new to the patient" in items["dc-26225"].explanation
    assert items["new-67108"].indication == "rule" and "started in hospital" in items["new-67108"].explanation
    assert items["new-723"].indication == "pending" and "new at discharge" in items["new-723"].explanation
    assert items["om-7258"].type == "omission" and "not given in hospital" not in items["om-7258"].explanation
    assert "not given in hospital" in items["om-11289"].explanation and items["om-11289"].severity == "major"
    assert items["om-2193"].hospital_use and items["om-2193"].severity == "minor"
    assert items["om-3423"].hospital_use and not items["om-6918"].hospital_use
    assert items["om-6918"].severity == "major"  # beta blocker: high risk
    assert items["dup-statin"].type == "duplicate_therapy" and items["dup-statin"].severity == "moderate"
    assert "om-17767" not in items  # amlodipine is on the discharge list


def test_discharge_table_follows_the_pharmacist():
    items = R.compare_discharge(*discharge_case(), CONDITIONS)
    items = R.apply_judgements(items, {}, 0.7, unavailable="test")
    for item in items:
        if item.discrepancy:
            item.action, item.reason = "accept", "intended"
    rows = by_id(items)
    rows["om-7258"].action, rows["om-7258"].change = "accept", None  # naproxen stopped
    rows["om-11289"].action, rows["om-11289"].change = "modify", "Resume warfarin 5 mg daily; INR in 3 days"
    rows["om-6918"].action, rows["om-6918"].change = "modify", "Continue metoprolol 50 mg twice daily"
    table = R.discharge_table(items)
    names = {c: [r["name"] for r in table[c]] for c in R.CATEGORIES}
    assert names["continued"] == ["amlodipine", "warfarin"]
    assert names["changed"] == ["furosemide"]
    assert names["stopped"] == ["naproxen"]
    assert names["new"] == ["amoxicillin", "atorvastatin", "enoxaparin", "metoprolol", "ondansetron", "simvastatin"]
    warfarin = next(r for r in table["continued"] if r["name"] == "warfarin")
    assert warfarin["change_note"].startswith("Resume warfarin") and warfarin["source_refs"]
    furosemide = table["changed"][0]
    assert furosemide["previous_text"] and furosemide["dose_text"] != furosemide["previous_text"]
    assert len(furosemide["source_refs"]) == 3  # discharge prescription, home statement, inpatient order
    assert table["stopped"][0]["dose_text"] is None and table["stopped"][0]["reason"] == "intended"


def exercise_everything() -> None:
    """Every case above once (the line-coverage check in tests/test_med_rules.py runs this)."""
    for test in (test_admission_rows, test_dose_and_frequency_both_changed, test_high_risk_omission_is_major,
                 test_judgements_settle_new_medications, test_judgement_not_indicated_and_made_up_reference,
                 test_without_the_ai_new_medications_stay_discrepancies, test_handling_and_severity,
                 test_discharge_rows, test_discharge_table_follows_the_pharmacist):
        test()
