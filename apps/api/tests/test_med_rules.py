"""WP6 (spec 7.2): the order review's rule layer, at least five positive and five negative cases per rule.

The rules are pure functions over plain facts (app/modules/med_safety/facts.py), so
these tests build the facts directly; no EHR, no model. The last test measures line
coverage of the rule layer (rules.py and reconcile.py) with the standard library's
sys.monitoring and fails below 100%.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta

import pytest

from app.ehr import codes as C
from app.modules.med_safety import rules
from app.modules.med_safety.facts import AllergyFact, Measurement, Med, PatientFacts, ServiceFact
from app.modules.med_safety.rules import RULES_DIR, evaluate, load_tables

NOW = datetime(2026, 10, 9, 7, 0)
T = load_tables()
_seq = iter(range(1, 10_000))


def med(rxcui: str, *, dose: float | None = None, per_day: float | None = None, source: str = "order",
        unit: str | None = None, prn: bool = False, route: str | None = None, ref: str | None = None,
        reasons: tuple[str, ...] = (), status: str = "active") -> Med:
    name, cls, typical, typical_unit, freq, default_route = C.MEDICATIONS[rxcui]
    n = next(_seq)
    rtype = "MedicationStatement" if source == "home" else "MedicationRequest"
    dose = typical if dose is None else dose
    per_day = freq if per_day is None else per_day
    return Med(ref=ref or f"{rtype}/m{n}", rxcui=rxcui, name=name, drug_class=cls, dose=dose,
               unit=unit or typical_unit, per_day=per_day, route=route or default_route, prn=prn,
               text=f"{dose:g} {typical_unit} x{per_day:g}/d", source=source, status=status, authored=NOW,
               reason_codes=reasons)


def facts(*, age: int | None = 60, weight: float | None = None, egfr: float | None = None, allergies=(), meds=(),
          services=(), conditions=()) -> PatientFacts:
    return PatientFacts(
        patient_ref="Patient/p1", age=age, sex="female",
        weight=Measurement("Observation/w1", weight, "kg", NOW - timedelta(days=30)) if weight is not None else None,
        egfr=Measurement("Observation/e1", egfr, "mL/min/{1.73_m2}", NOW - timedelta(days=10)) if egfr is not None
        else None, allergies=tuple(allergies), meds=tuple(meds), services=tuple(services), conditions=tuple(conditions))


def allergy(code: str, display: str, drug_class: str = "", criticality: str = "high", reaction: str | None = "Hives"):
    return AllergyFact(ref=f"AllergyIntolerance/a{next(_seq)}", code=code, display=display, drug_class=drug_class,
                       criticality=criticality, reaction=reaction)


def service(code: str, *, hours_ago: float, resulted_after: float | None = 2.0, category: str = "lab",
            ref: str | None = None, at: datetime = NOW) -> ServiceFact:
    authored = at - timedelta(hours=hours_ago)
    n = next(_seq)
    return ServiceFact(ref=ref or f"ServiceRequest/s{n}", code=code, display=f"Test {code}", category=category,
                       status="completed" if resulted_after is not None else "active", authored=authored,
                       result_ref=f"Observation/r{n}" if resulted_after is not None else None,
                       result_at=authored + timedelta(hours=resulted_after) if resulted_after is not None else None)


def new_order(code: str, category: str = "lab") -> ServiceFact:
    return ServiceFact(ref="ServiceRequest/new", code=code, display=f"Test {code}", category=category,
                       status="active", authored=NOW)


# ---------- the tables ----------


def test_tables_have_the_specified_size_and_every_entry_a_source():
    assert len(T.renal) == 15 and T.renal_threshold == 30
    assert len(T.pairs) == 25 and len({p.id for p in T.pairs}) == 25
    assert len(T.dose) >= 10
    for entry in [*T.renal.values(), *T.pairs, *T.dose.values()]:
        assert len(entry.source) >= 10, entry
    assert T.medrec.duplicate_test.window_hours == 72 and T.medrec.duplicate_test.source
    for rxcui in [*T.renal, *T.dose]:
        assert rxcui in C.MEDICATIONS  # the generator's vocabulary
    classes = {m[1] for m in C.MEDICATIONS.values()}
    for pair in T.pairs:
        for side in (pair.a, pair.b):
            codes = side.rxcui if isinstance(side.rxcui, list) else [side.rxcui]
            assert (side.drug_class in classes) if side.drug_class else all(c in C.MEDICATIONS for c in codes)
    readme = (RULES_DIR / "README.md").read_text(encoding="utf-8")
    assert "not clinical guidance" in readme and "commercial drug knowledge base" in readme


def test_a_pair_side_names_an_ingredient_or_a_class_not_both():
    with pytest.raises(ValueError):
        rules.PairSide.model_validate({"name": "x"})
    with pytest.raises(ValueError):
        rules.PairSide.model_validate({"name": "x", "rxcui": "1", "class": "nsaid"})


# ---------- 1. duplicate test ----------

DUPLICATE_POSITIVE = [
    ("CBC resulted yesterday", new_order("718-7"), [service("718-7", hours_ago=24)]),
    ("chest X-ray two days ago", new_order("XR_CHEST", "imaging"), [service("XR_CHEST", hours_ago=48, category="imaging")]),
    ("edge of the 72-hour window", new_order("2160-0"), [service("2160-0", hours_ago=71.5)]),
    ("troponin resulted an hour ago", new_order("10839-9"), [service("10839-9", hours_ago=3, resulted_after=2)]),
    ("result time unknown", new_order("2823-3"), [ServiceFact("ServiceRequest/x1", "2823-3", "Potassium", "lab", "completed",
                                                              NOW - timedelta(hours=5), "Observation/x1", None)]),
    ("an unrelated test does not hide it", new_order("6690-2"), [service("718-7", hours_ago=10), service("6690-2", hours_ago=30)]),
]
DUPLICATE_NEGATIVE = [
    ("prior not resulted yet", new_order("718-7"), [service("718-7", hours_ago=5, resulted_after=None)]),
    ("prior outside 72 hours", new_order("718-7"), [service("718-7", hours_ago=73)]),
    ("another test", new_order("718-7"), [service("2951-2", hours_ago=12)]),
    ("consults are not tests", new_order("physio", "consult"), [service("physio", hours_ago=12, category="consult")]),
    ("the order itself", new_order("718-7"), [service("718-7", hours_ago=0, ref="ServiceRequest/new")]),
    ("result came after this order", new_order("718-7"), [service("718-7", hours_ago=1, resulted_after=3)]),
    ("prior placed after this order", new_order("718-7"), [service("718-7", hours_ago=-2)]),
]


@pytest.mark.parametrize("name,order,services", DUPLICATE_POSITIVE, ids=[c[0] for c in DUPLICATE_POSITIVE])
def test_duplicate_test_positive(name, order, services):
    found = rules.duplicate_test(order, facts(services=services), T)
    assert len(found) == 1 and found[0].severity == "info" and not found[0].blocking
    prior = max((s for s in services if s.code == order.code), key=lambda s: s.authored)
    assert found[0].evidence_refs == (prior.ref, prior.result_ref)
    assert "already resulted" in found[0].title


@pytest.mark.parametrize("name,order,services", DUPLICATE_NEGATIVE, ids=[c[0] for c in DUPLICATE_NEGATIVE])
def test_duplicate_test_negative(name, order, services):
    assert rules.duplicate_test(order, facts(services=services), T) == []


def test_duplicate_test_needs_the_order_time():
    order = ServiceFact("ServiceRequest/new", "718-7", "CBC", "lab", "active", None)
    assert rules.duplicate_test(order, facts(services=[service("718-7", hours_ago=5)]), T) == []


def test_duplicate_test_cites_the_most_recent_result():
    older, newer = service("718-7", hours_ago=40), service("718-7", hours_ago=12)
    found = rules.duplicate_test(new_order("718-7"), facts(services=[older, newer]), T)
    assert found[0].evidence_refs[0] == newer.ref and "12 h before" in found[0].detail


# ---------- 2. renal dosing ----------

RENAL_POSITIVE = [
    ("metformin, eGFR 22", med("6809"), 22, "major"),
    ("enoxaparin, eGFR 25", med("67108"), 25, "moderate"),
    ("naproxen, eGFR 29.9", med("7258"), 29.9, "major"),
    ("dabigatran, eGFR 15", med("1037042"), 15, "major"),
    ("spironolactone, eGFR 10", med("9997"), 10, "major"),
    ("gabapentin, eGFR 18", med("25480"), 18, "moderate"),
]
RENAL_NEGATIVE = [
    ("metformin at the threshold", med("6809"), 30),
    ("metformin, eGFR 45", med("6809"), 45),
    ("no eGFR on record", med("6809"), None),
    ("amlodipine is not on the list", med("17767"), 20),
    ("acetaminophen is not on the list", med("161"), 12),
    ("ketorolac, normal eGFR", med("35827"), 95),
]


@pytest.mark.parametrize("name,order,egfr,severity", RENAL_POSITIVE, ids=[c[0] for c in RENAL_POSITIVE])
def test_renal_dosing_positive(name, order, egfr, severity):
    found = rules.renal_dosing(order, facts(egfr=egfr), T)
    assert len(found) == 1 and found[0].severity == severity
    assert found[0].recommendation == T.renal[order.rxcui].suggested
    assert found[0].evidence_refs == ("Observation/e1",) and f"eGFR {egfr:g}" in found[0].title


@pytest.mark.parametrize("name,order,egfr", RENAL_NEGATIVE, ids=[c[0] for c in RENAL_NEGATIVE])
def test_renal_dosing_negative(name, order, egfr):
    assert rules.renal_dosing(order, facts(egfr=egfr), T) == []


def test_renal_dosing_without_a_date_still_fires():
    f = PatientFacts("Patient/p1", egfr=Measurement("Observation/e9", 20, None, None))
    found = rules.renal_dosing(med("6809"), f, T)
    assert found and " on " not in found[0].detail


# ---------- 3. interactions ----------

WARFARIN_ORDER = med("11289")
INTERACTION_POSITIVE = [
    ("warfarin order, naproxen at home", WARFARIN_ORDER, [med("7258", source="home")], "warfarin-nsaid", "major"),
    ("naproxen order, warfarin ordered", med("7258"), [med("11289")], "warfarin-nsaid", "major"),
    ("apixaban with ibuprofen (ingredient list)", med("1364430"), [med("5640")], "doac-nsaid", "major"),
    ("tramadol with sertraline (class)", med("10689"), [med("36437", source="home")], "tramadol-ssri", "major"),
    ("potassium with spironolactone", med("8591"), [med("9997")], "spironolactone-potassium", "major"),
    ("warfarin with ciprofloxacin", med("2551"), [med("11289", source="home")], "warfarin-fluoroquinolone", "moderate"),
    ("hydromorphone with lorazepam", med("3423"), [med("6470")], "opioid-benzodiazepine", "major"),
]
INTERACTION_NEGATIVE = [
    ("warfarin with acetaminophen", WARFARIN_ORDER, [med("161")]),
    ("the same ingredient at home", WARFARIN_ORDER, [med("11289", source="home")]),
    ("clopidogrel with pantoprazole", med("32968"), [med("40790")]),
    ("aspirin with clopidogrel", med("1191"), [med("32968")]),
    ("the order itself", WARFARIN_ORDER, [WARFARIN_ORDER]),
    ("nothing else active", med("7258"), []),
]


@pytest.mark.parametrize("name,order,others,pair,severity", INTERACTION_POSITIVE, ids=[c[0] for c in INTERACTION_POSITIVE])
def test_interaction_positive(name, order, others, pair, severity):
    found = rules.interactions(order, facts(meds=others), T)
    assert [f.entry for f in found] == [f"interactions:{pair}"]
    entry = next(p for p in T.pairs if p.id == pair)
    assert found[0].severity == severity and entry.mechanism in found[0].detail
    assert found[0].evidence_refs == tuple(o.ref for o in others)


@pytest.mark.parametrize("name,order,others", INTERACTION_NEGATIVE, ids=[c[0] for c in INTERACTION_NEGATIVE])
def test_interaction_negative(name, order, others):
    assert rules.interactions(order, facts(meds=others), T) == []


def test_interaction_partner_on_both_lists_is_one_finding_citing_the_order_first():
    home, ordered = med("7258", source="home"), med("7258")
    found = rules.interactions(WARFARIN_ORDER, facts(meds=[home, ordered]), T)
    assert len(found) == 1 and found[0].evidence_refs == (ordered.ref, home.ref)
    assert "an active order and the home medication list" in found[0].detail


def test_interactions_with_two_partners_are_two_findings():
    found = rules.interactions(WARFARIN_ORDER, facts(meds=[med("1191"), med("32968")]), T)
    assert sorted(f.entry for f in found) == ["interactions:warfarin-aspirin", "interactions:warfarin-clopidogrel"]
    moderate = rules.interactions(med("29046"), facts(meds=[med("8591")]), T)
    assert moderate[0].severity == "moderate" and moderate[0].recommendation.startswith("Monitor")


# ---------- 4. allergy ----------

PENICILLIN = ("764146007", "Penicillin", "penicillin")
ALLERGY_POSITIVE = [
    ("piperacillin, penicillin allergy", med("8339"), allergy(*PENICILLIN), "class"),
    ("ibuprofen, aspirin allergy (NSAID class)", med("5640"), allergy("1191", "aspirin", "nsaid"), "class"),
    ("aspirin, aspirin allergy (same drug)", med("1191"), allergy("1191", "aspirin", "nsaid"), "code"),
    ("hydromorphone, morphine allergy (opioid class)", med("3423"), allergy("7052", "morphine", "opioid"), "class"),
    ("cefazolin, ceftriaxone allergy (cephalosporin class)", med("2180"), allergy("2193", "ceftriaxone", "cephalosporin"), "class"),
    ("sulfamethoxazole, sulfonamide allergy", med("10180"), allergy("387406002", "Sulfonamide", "sulfonamide"), "class"),
]
ALLERGY_NEGATIVE = [
    ("ceftriaxone, penicillin allergy (no cross-class rule)", med("2193"), [allergy(*PENICILLIN)]),
    ("peanut allergy", med("8339"), [allergy("91935009", "Peanut")]),
    ("latex allergy", med("161"), [allergy("111088007", "Latex")]),
    ("no allergies", med("8339"), []),
    ("acetaminophen, morphine allergy", med("161"), [allergy("7052", "morphine", "opioid")]),
    ("amoxicillin, sulfonamide allergy", med("723"), [allergy("387406002", "Sulfonamide", "sulfonamide")]),
]


@pytest.mark.parametrize("name,order,found_allergy,match", ALLERGY_POSITIVE, ids=[c[0] for c in ALLERGY_POSITIVE])
def test_allergy_positive(name, order, found_allergy, match):
    found = rules.allergy(order, facts(allergies=[found_allergy]), T)
    assert len(found) == 1 and found[0].severity == "blocking" and found[0].blocking
    assert found[0].entry == f"allergy:{match}" and found[0].evidence_refs == (found_allergy.ref,)
    assert "pharmacist" in found[0].recommendation


@pytest.mark.parametrize("name,order,allergies", ALLERGY_NEGATIVE, ids=[c[0] for c in ALLERGY_NEGATIVE])
def test_allergy_negative(name, order, allergies):
    assert rules.allergy(order, facts(allergies=allergies), T) == []


def test_allergy_without_reaction_or_criticality():
    found = rules.allergy(med("8339"), facts(allergies=[allergy(*PENICILLIN, criticality=None, reaction=None)]), T)
    assert "criticality unknown" in found[0].detail and "reaction" not in found[0].detail


# ---------- 5. dose bounds ----------

DOSE_POSITIVE = [
    ("ketorolac 30 mg q6h at 70", med("35827", dose=30, per_day=4), {"age": 70}, "major", ["above 15", "above 60"]),
    ("acetaminophen 1 g qid at 45 kg", med("161", dose=1000, per_day=4), {"weight": 45}, "major", ["above 675", "above 3375"]),
    ("methotrexate 15 mg daily", med("6851", dose=15, per_day=1), {}, "major", ["a week is above 25"]),
    ("vancomycin 1 g for 120 kg", med("11124", dose=1000, per_day=2), {"weight": 120}, "moderate", ["below 1200"]),
    ("enoxaparin 120 mg at 60 kg", med("67108", dose=120, per_day=1), {"weight": 60}, "major", ["above 90"]),
    ("lorazepam 2 mg bid at 80", med("6470", dose=2, per_day=2), {"age": 80}, "moderate", ["above 1", "above 2"]),
    ("digoxin 0.25 mg at 75", med("3407", dose=0.25, per_day=1), {"age": 75}, "major", ["0.25 mg a day is above 0.125"]),
    ("colchicine 0.6 mg tid", med("2683", dose=0.6, per_day=3), {}, "major", ["1.8 mg a day is above 1.2"]),
]
DOSE_NEGATIVE = [
    ("ketorolac 15 mg qid at 50", med("35827", dose=15, per_day=4), {"age": 50}),
    ("acetaminophen 1 g qid at 70 kg", med("161", dose=1000, per_day=4), {"weight": 70}),
    ("methotrexate 15 mg weekly", med("6851", dose=15, per_day=1 / 7), {}),
    ("vancomycin 1.5 g for 100 kg", med("11124", dose=1500, per_day=2), {"weight": 100}),
    ("heparin is not in the table", med("5224"), {}),
    ("amlodipine is not in the table", med("17767", dose=50), {}),
    ("another unit", med("35827", dose=300, unit="ug"), {}),
    ("enoxaparin 40 mg, no weight", med("67108", dose=40), {}),
]


@pytest.mark.parametrize("name,order,patient,severity,texts", DOSE_POSITIVE, ids=[c[0] for c in DOSE_POSITIVE])
def test_dose_bounds_positive(name, order, patient, severity, texts):
    found = rules.dose_bounds(order, facts(**{"age": 60, **patient}), T)
    assert len(found) == 1 and found[0].severity == severity
    for text in texts:
        assert text in found[0].detail
    assert found[0].recommendation.startswith("Keep within")


@pytest.mark.parametrize("name,order,patient", DOSE_NEGATIVE, ids=[c[0] for c in DOSE_NEGATIVE])
def test_dose_bounds_negative(name, order, patient):
    assert rules.dose_bounds(order, facts(**{"age": 60, **patient}), T) == []


def test_dose_bounds_evidence_and_ranges():
    no_dose = Med("MedicationRequest/x", "35827", "ketorolac", "nsaid", None, "mg", 4)
    assert rules.dose_bounds(no_dose, facts(), T) == []
    once = Med("MedicationRequest/y", "35827", "ketorolac", "nsaid", 60, "mg", None)
    found = rules.dose_bounds(once, facts(age=None), T)  # no frequency: the single dose only
    assert found and "a day" not in found[0].detail and found[0].evidence_refs == ("MedicationRequest/y",)
    older = rules.dose_bounds(med("35827", dose=30, per_day=4), facts(age=70, weight=45), T)
    assert older[0].evidence_refs == ("Observation/w1", "Patient/p1")
    vanc = rules.bounds_for(T.dose["11124"], 50, 80)
    assert rules.range_text(vanc, "mg") == "800-1600 mg per dose"
    enox = rules.bounds_for(T.dose["67108"], 50, None)
    assert rules.range_text(enox, "mg") == "at least 20 mg per dose"
    assert rules.range_text(rules.bounds_for(T.dose["6851"], 50, None), "mg") == "up to 25 mg a week"
    assert rules.range_text(rules.bounds_for(T.dose["35827"], 70, None), "mg") == \
        "up to 15 mg per dose, up to 60 mg a day"


# ---------- all rules ----------


def test_evaluate_runs_every_rule_most_severe_first():
    order = med("7258")  # naproxen
    f = facts(egfr=20, allergies=[allergy("1191", "aspirin", "nsaid")], meds=[med("11289", source="home")])
    found = evaluate(order, f)
    assert [x.rule for x in found] == ["allergy", "interaction", "renal_dosing"]
    assert [x.severity for x in found] == ["blocking", "major", "major"]
    as_dict = found[0].as_dict()
    assert as_dict["blocking"] and as_dict["rule_name"] == "Allergy" and as_dict["origin"] == "rule"
    assert evaluate(new_order("718-7"), facts(services=[service("718-7", hours_ago=4)]))[0].rule == "duplicate_test"
    assert evaluate(med("161"), facts()) == []
    assert rules.most_severe([]) == "none" and rules.most_severe(["info", "major", "minor"]) == "major"
    assert rules._ago(None, NOW) == "earlier" and rules._ago(NOW - timedelta(minutes=5), NOW) == "5 min before"


# ---------- coverage of the rule layer ----------


def _function_lines(module: types.ModuleType) -> dict[str, set[int]]:
    """Executable lines inside the module's functions (module and class bodies run at import); the def line itself
    (function entry bookkeeping) does not count."""
    import ast
    import dis

    source = open(module.__file__, encoding="utf-8").read()
    tree = ast.parse(source)
    classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    code = compile(source, module.__file__, "exec")
    lines: dict[str, set[int]] = {}

    def walk(co: types.CodeType) -> None:
        for const in co.co_consts:
            if isinstance(const, types.CodeType):
                if const.co_name not in classes and const.co_name != "<module>":
                    body = {i.positions.lineno for i in dis.get_instructions(const)
                            if i.positions and i.positions.lineno and i.opname not in ("RESUME", "NOP", "CACHE")
                            and i.positions.lineno != const.co_firstlineno}
                    lines.setdefault(const.co_qualname, set()).update(body)
                walk(const)

    walk(code)
    return lines


def test_rule_layer_has_full_line_coverage():
    """Every line of the rule layer's functions runs in this module's cases (sys.monitoring, no extra tool)."""
    from app.modules.med_safety import reconcile
    from tests import test_medrec_rules

    targets = {rules.__file__: rules, reconcile.__file__: reconcile}
    hit: dict[str, set[int]] = {path: set() for path in targets}
    tool = sys.monitoring.COVERAGE_ID
    sys.monitoring.use_tool_id(tool, "wp6-rules")

    def on_line(code: types.CodeType, line: int):
        if code.co_filename in hit:
            hit[code.co_filename].add(line)
        return sys.monitoring.DISABLE  # each location once is enough

    sys.monitoring.register_callback(tool, sys.monitoring.events.LINE, on_line)
    sys.monitoring.set_events(tool, sys.monitoring.events.LINE)
    try:
        rules.load_tables.__wrapped__(RULES_DIR)
        test_a_pair_side_names_an_ingredient_or_a_class_not_both()
        for case in DUPLICATE_POSITIVE:
            test_duplicate_test_positive(*case)
        for case in DUPLICATE_NEGATIVE:
            test_duplicate_test_negative(*case)
        test_duplicate_test_needs_the_order_time()
        test_duplicate_test_cites_the_most_recent_result()
        for case in RENAL_POSITIVE:
            test_renal_dosing_positive(*case)
        for case in RENAL_NEGATIVE:
            test_renal_dosing_negative(*case)
        test_renal_dosing_without_a_date_still_fires()
        for case in INTERACTION_POSITIVE:
            test_interaction_positive(*case)
        for case in INTERACTION_NEGATIVE:
            test_interaction_negative(*case)
        test_interaction_partner_on_both_lists_is_one_finding_citing_the_order_first()
        test_interactions_with_two_partners_are_two_findings()
        for case in ALLERGY_POSITIVE:
            test_allergy_positive(*case)
        for case in ALLERGY_NEGATIVE:
            test_allergy_negative(*case)
        test_allergy_without_reaction_or_criticality()
        for case in DOSE_POSITIVE:
            test_dose_bounds_positive(*case)
        for case in DOSE_NEGATIVE:
            test_dose_bounds_negative(*case)
        test_dose_bounds_evidence_and_ranges()
        test_evaluate_runs_every_rule_most_severe_first()
        test_medrec_rules.exercise_everything()
    finally:
        sys.monitoring.set_events(tool, 0)
        sys.monitoring.register_callback(tool, sys.monitoring.events.LINE, None)
        sys.monitoring.free_tool_id(tool)
    missing = {}
    for path, module in targets.items():
        for name, lines in _function_lines(module).items():
            if lines - hit[path]:
                missing[f"{module.__name__}.{name}"] = sorted(lines - hit[path])
    assert missing == {}
