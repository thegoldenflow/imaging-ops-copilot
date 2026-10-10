"""Mock models for the two medication-safety agents, so the demo and the evals run without an API key.

They are heuristics over the prompt's own sections (the de-identified context, the
rule findings, the retrieved guideline sections), written by the same author as the
eval labels, so the mock eval results are optimistic: they show that the pipeline
works (context, schema, guards, evidence, queue), not what a model would score.
`scripts/med_safety_eval.py --live` runs the configured model through the same path.

- order_review: restates the rule findings as concerns (with their evidence) and
  adds what the rules do not check: duplicate therapy, age-related caution, monitoring
  (INR, potassium, levels), mild renal impairment, a missing indication.
- medication_reconciliation: looks the new medication's class up in an indication
  map against the diagnosis list (and the reason for admission for symptom relief
  and prophylaxis), with a confidence; a plain sentence per discrepancy.
"""

from __future__ import annotations

import json

from app.ehr import codes as C
from app.llm.providers import mock_fixture
from app.modules.med_safety.rules import load_tables

AGE_YEARS = C.EXT + "age-years"
RULE_TYPES = {"duplicate_test": "duplicate_test", "renal_dosing": "renal", "interaction": "interaction",
              "allergy": "allergy", "dose_bounds": "dose"}
RULE_SEVERITY = {"blocking": "major", "info": "minor"}
OLDER_ADULT_CAUTION = {"6470": "moderate", "51272": "moderate", "4815": "moderate", "35827": "minor", "5640": "minor",
                       "7258": "minor", "140587": "minor"}  # lorazepam, quetiapine, glyburide, NSAIDs
LEVEL_DRUGS = {"3407": "digoxin level", "6448": "lithium level", "11124": "vancomycin trough or AUC"}
POTASSIUM_DRUGS = {"29046", "9997", "8591"}

FRACTURES = {"5913000", "125605004"}
PAIN = FRACTURES | {"161891005", "396275006", "69896004", "74400008", "65275009", "21522001", "22298006", "25064002",
                    "1912002", "29857009"}
INFECTIONS = {"233604007", "91302008", "68566005", "396230008", "74400008", "65275009", "10509002", "195951007",
              "444814009"}
# therapeutic class (app/ehr/codes.py) -> diagnoses (SNOMED CT) that indicate it
INDICATIONS: dict[str, set[str]] = {
    "anticoagulant": {"49436004", "128053003", "59282003"},
    "antiplatelet": {"22298006", "230690007"},
    "statin": {"55822004", "22298006", "230690007", "44054006"},
    "ace inhibitor": {"59621000", "42343007", "44054006", "22298006"},
    "calcium channel blocker": {"59621000"},
    "thiazide diuretic": {"59621000"},
    "beta blocker": {"59621000", "49436004", "42343007", "22298006"},
    "loop diuretic": {"42343007"},
    "potassium-sparing diuretic": {"42343007"},
    "biguanide": {"44054006"}, "dpp4 inhibitor": {"44054006"}, "sulfonylurea": {"44054006"}, "insulin": {"44054006"},
    "beta agonist": {"13645005", "195967001", "195951007"},
    "anticholinergic": {"13645005", "195951007"},
    "corticosteroid": {"195951007", "13645005", "195967001", "69896004"},
    "cephalosporin": INFECTIONS, "penicillin": INFECTIONS, "glycopeptide": INFECTIONS, "macrolide": INFECTIONS,
    "fluoroquinolone": INFECTIONS, "nitroimidazole": INFECTIONS, "sulfonamide": INFECTIONS, "nitrofuran": {"68566005"},
    "analgesic": PAIN, "opioid": PAIN, "nsaid": PAIN, "antiemetic": {"74400008", "65275009", "21522001"},
    "thyroid hormone": {"40930008"}, "xanthine oxidase inhibitor": {"90560007"}, "anti-gout": {"90560007"},
    "ssri": {"35489007"}, "antimetabolite": {"69896004"}, "cardiac glycoside": {"49436004", "42343007"},
    "antiarrhythmic": {"49436004"},
}
# below the registry's threshold on purpose: a person should look
UNSURE = {"antipsychotic": ({"26929004"}, 0.6)}
PROPHYLAXIS = {"67108", "5224"}  # enoxaparin, heparin: VTE prophylaxis for an admitted patient
GASTROPROTECTION = {"nsaid", "anticoagulant", "antiplatelet", "corticosteroid"}  # a reason for a PPI, not a diagnosis


def _between(text: str, start: str, end: str | None) -> str:
    rest = text.split(start, 1)[-1]
    return rest.split(end, 1)[0] if end else rest


def _code(concept: dict | None) -> str:
    return ((concept or {}).get("coding") or [{}])[0].get("code") or ""


def _ref(r: dict) -> str:
    return f"{r['resourceType']}/{r['id']}"


def _latest(observations: list[dict], code: str) -> dict | None:
    found = [o for o in observations if _code(o.get("code")) == code and (o.get("valueQuantity") or {}).get("value")
             is not None]
    return max(found, key=lambda o: o.get("effectiveDateTime") or "") if found else None


# ---------- order_review ----------


def _alternatives(concerns: list[dict], findings: list[dict], order_name: str, drug_class: str) -> list[dict]:
    out = []
    types = {c["type"] for c in concerns}
    if "interaction" in types and drug_class == "nsaid":
        out.append({"suggestion": "Scheduled acetaminophen 650-1000 mg every 6 hours instead of the NSAID (no more "
                                  "than 3 g a day in an older adult)",
                    "rationale": "Pain relief without adding to the anticoagulant's bleeding risk."})
    for f in findings:
        if f["rule"] in ("renal_dosing", "dose_bounds"):
            out.append({"suggestion": f["recommendation"][:300],
                        "rationale": f"The {f['rule'].replace('_', ' ')} rule's limit for this patient."})
    if "allergy" in types:
        out.append({"suggestion": f"An alternative to {order_name} from a class the patient is not allergic to, "
                                  "agreed with the prescriber",
                    "rationale": "The documented reaction makes this order unsafe as written."})
    if "duplicate_therapy" in types:
        out.append({"suggestion": f"Keep one {drug_class} and stop the other",
                    "rationale": "Two drugs of the same class add side effects without added benefit."})
    if "age_related" in types and drug_class in ("benzodiazepine", "antipsychotic"):
        out.append({"suggestion": "Non-drug measures for sleep or agitation; if a drug is needed, the lowest dose for "
                                  "the shortest time",
                    "rationale": "Sedatives raise the risk of delirium and falls in older adults."})
    if "duplicate_test" in types:
        out.append({"suggestion": "Use the existing result and cancel the repeat unless the clinical picture changed",
                    "rationale": "A repeat within 72 hours rarely changes management."})
    return out[:3]


@mock_fixture("order_review")
def _order_review(text: str, images: list, attempt: int) -> dict:
    order = json.loads(_between(text, "):\n", "\n\nRule findings"))
    findings = json.loads(_between(text, "(deterministic; they stand):\n", "\n\nGuideline sections"))
    guidelines = json.loads(_between(text, "Guideline sections you may cite:\n", "\n\nContext"))
    resources = json.loads(_between(text, "de-identified):\n", "\n"))
    concerns: list[dict] = []
    for f in findings:
        concerns.append({"type": RULE_TYPES[f["rule"]], "severity": RULE_SEVERITY.get(f["severity"], f["severity"]),
                         "explanation": f"{f['title']}. {f['detail']}"[:800],
                         "evidence_refs": list(f["evidence_refs"])[:12], "confidence": 0.92})
    order_name, drug_class, rxcui = "", "", ""
    if order.get("resourceType") == "MedicationRequest":
        rxcui = _code(order.get("medicationCodeableConcept"))
        order_name, drug_class = (C.MEDICATIONS.get(rxcui) or ("", ""))[:2]
        concerns += _medication_concerns(order, rxcui, order_name, drug_class, resources,
                                         {c["type"] for c in concerns})
    refs = {_ref(r) for r in resources}
    for c in concerns:
        c["evidence_refs"] = [r for r in c["evidence_refs"] if r in refs] or c["evidence_refs"][:0]
    cited = [g["id"] for g in guidelines[:2]] if concerns else []
    return {"concerns": concerns[:8], "alternatives": _alternatives(concerns, findings, order_name, drug_class),
            "guideline_refs": cited}


def _medication_concerns(order: dict, rxcui: str, name: str, drug_class: str, resources: list[dict],
                         covered: set[str]) -> list[dict]:
    out: list[dict] = []
    order_ref = _ref(order)
    patient = next((r for r in resources if r["resourceType"] == "Patient"), {})
    age = next((e.get("valueInteger") for e in patient.get("extension") or [] if e.get("url") == AGE_YEARS), None)
    others = [r for r in resources if r["resourceType"] in ("MedicationRequest", "MedicationStatement")
              and _ref(r) != order_ref and r.get("status") == "active"
              and (r["resourceType"] == "MedicationStatement" or r.get("intent") == "order")]
    observations = [r for r in resources if r["resourceType"] == "Observation"]
    conditions = {_code(r.get("code")) for r in resources if r["resourceType"] == "Condition"}
    tables = load_tables()
    if "duplicate_therapy" not in covered and drug_class in tables.medrec.duplicate_therapy.classes:
        same = [r for r in others if (C.MEDICATIONS.get(_code(r.get("medicationCodeableConcept"))) or ("", ""))[1]
                == drug_class and _code(r.get("medicationCodeableConcept")) != rxcui]
        if same:
            other = (C.MEDICATIONS.get(_code(same[0].get("medicationCodeableConcept"))) or ("another drug",))[0]
            out.append({"type": "duplicate_therapy",
                        "severity": "major" if drug_class in tables.medrec.high_risk_classes else "moderate",
                        "explanation": f"{name.capitalize()} and {other} are both {drug_class}s; one is usually enough.",
                        "evidence_refs": [order_ref, _ref(same[0])], "confidence": 0.8})
    if age is not None and age >= 65 and rxcui in OLDER_ADULT_CAUTION:
        out.append({"type": "age_related", "severity": OLDER_ADULT_CAUTION[rxcui],
                    "explanation": f"At {age}, {name} carries more risk (falls, delirium, bleeding or kidney injury); "
                                   "use the lowest dose for the shortest time.",
                    "evidence_refs": [_ref(patient)] if patient else [], "confidence": 0.75})
    on_warfarin = rxcui == "11289" or any(_code(r.get("medicationCodeableConcept")) == "11289" for r in others)
    inr = _latest(observations, "6301-6")
    if on_warfarin and "monitoring" not in covered:
        if inr is not None and inr["valueQuantity"]["value"] > 3.0:
            out.append({"type": "monitoring", "severity": "major" if drug_class == "nsaid" else "moderate",
                        "explanation": f"The INR is {inr['valueQuantity']['value']:g}, above the usual range; "
                                       "review warfarin and any added bleeding risk before giving this.",
                        "evidence_refs": [_ref(inr)], "confidence": 0.85})
        elif inr is None and rxcui == "11289":
            out.append({"type": "monitoring", "severity": "minor",
                        "explanation": "No recent INR in the record; check it before the next warfarin dose.",
                        "evidence_refs": [order_ref], "confidence": 0.7})
    potassium = _latest(observations, "2823-3")
    if rxcui in POTASSIUM_DRUGS and potassium is not None and potassium["valueQuantity"]["value"] > 5.0:
        out.append({"type": "monitoring", "severity": "moderate",
                    "explanation": f"Potassium is {potassium['valueQuantity']['value']:g}; this drug raises it further.",
                    "evidence_refs": [_ref(potassium)], "confidence": 0.8})
    if rxcui in LEVEL_DRUGS:
        out.append({"type": "monitoring", "severity": "minor",
                    "explanation": f"Plan a {LEVEL_DRUGS[rxcui]} after the dose is started or changed.",
                    "evidence_refs": [order_ref], "confidence": 0.7})
    egfr = _latest(observations, "33914-3")
    if "renal" not in covered and egfr is not None and 30 <= egfr["valueQuantity"]["value"] < 60 \
            and rxcui in tables.renal:
        out.append({"type": "renal", "severity": "minor",
                    "explanation": f"eGFR {egfr['valueQuantity']['value']:g} is above the rule's threshold but "
                                   f"reduced; monitor kidney function on {name}.",
                    "evidence_refs": [_ref(egfr)], "confidence": 0.65})
    named = {_code(r) for r in order.get("reasonCode") or []}
    if drug_class == "proton pump inhibitor":
        protects = any((C.MEDICATIONS.get(_code(r.get("medicationCodeableConcept"))) or ("", ""))[1] in GASTROPROTECTION
                       for r in others)
        if not protects and "indication" not in covered:
            out.append({"type": "indication", "severity": "minor",
                        "explanation": f"Nothing on the record explains {name} (no NSAID, anticoagulant, antiplatelet "
                                       "or steroid to protect against, no acid-related diagnosis); confirm the "
                                       "indication or stop it.", "evidence_refs": [order_ref], "confidence": 0.65})
        return out
    wanted = INDICATIONS.get(drug_class)
    if wanted is not None and not (named & conditions) and not (wanted & conditions) and rxcui not in PROPHYLAXIS \
            and drug_class not in ("analgesic", "opioid", "antiemetic", "nsaid"):
        out.append({"type": "indication", "severity": "minor",
                    "explanation": f"No diagnosis on the list explains {name}; confirm the indication.",
                    "evidence_refs": [order_ref], "confidence": 0.6})
    return out


# ---------- medication_reconciliation ----------


def judge_indication(candidate: dict, conditions: list[dict], reason_code: str) -> dict:
    """The mock's indication verdict for one new medication (also used by the eval's labels check)."""
    rxcui, drug_class, name = candidate["rxcui"], candidate["class"], candidate["medication"]
    by_code = {c["code"]: c for c in conditions if c.get("status") == "active"}
    encounter_dx = by_code.get(reason_code)
    wanted = INDICATIONS.get(drug_class, set())
    match = next((by_code[c] for c in sorted(wanted) if c in by_code), None)
    if match is not None:
        return {"indicated": True, "indication_ref": match["ref"], "confidence": 0.85,
                "explanation": f"{name.capitalize()} is indicated by {match['display']}."}
    if drug_class in UNSURE:
        codes, confidence = UNSURE[drug_class]
        hit = next((by_code[c] for c in sorted(codes) if c in by_code), None)
        if hit is not None:
            return {"indicated": True, "indication_ref": hit["ref"], "confidence": confidence,
                    "explanation": f"{name.capitalize()} may be for {hit['display']}, but that is uncertain."}
    if encounter_dx is not None and (rxcui in PROPHYLAXIS or (drug_class in ("analgesic", "opioid", "antiemetic", "nsaid")
                                                              and reason_code in PAIN | INFECTIONS)):
        purpose = "clot prophylaxis" if rxcui in PROPHYLAXIS else "symptom relief"
        return {"indicated": True, "indication_ref": encounter_dx["ref"], "confidence": 0.78,
                "explanation": f"{name.capitalize()} for {purpose} during the admission for {encounter_dx['display']}."}
    return {"indicated": False, "indication_ref": "", "confidence": 0.8,
            "explanation": f"No diagnosis on the list explains {name}."}


@mock_fixture("medication_reconciliation")
def _medication_reconciliation(text: str, images: list, attempt: int) -> dict:
    candidates = json.loads(_between(text, "(item_id, medication, class, dose):\n", "\n\nDiscrepancies"))
    discrepancy_lines = _between(text, "(item_id: description):\n", "\n\nDiagnosis list").strip().splitlines()
    conditions = json.loads(_between(text, "(reference, code, display, status):\n", "\n\nEncounter"))
    encounter = json.loads(_between(text, "(reason for admission):\n", "\n\nGuideline sections"))
    reason_code = _code((encounter.get("reasonCode") or [{}])[0])
    judgements = [{"item_id": c["item_id"], **judge_indication(c, conditions, reason_code)} for c in candidates]
    notes = []
    for line in discrepancy_lines:
        item_id, _, description = line.partition(": ")
        if not description:
            continue
        kind, _, key = item_id.partition("-")
        name = (C.MEDICATIONS.get(key) or (key.replace("-", " "),))[0]
        text = {"om": f"{name.capitalize()} is on the patient's list but not on the new one; ask whether stopping it "
                      "was intended.",
                "dc": f"{name.capitalize()} is ordered at another dose or frequency than before; confirm the change "
                      "is intended.",
                "dup": f"Two {name} medicines are ordered at the same time; usually one is enough.",
                "new": f"No diagnosis on the list explains {name}; ask the prescriber what it is for."}.get(kind)
        if text:
            notes.append({"item_id": item_id, "explanation": text[:500]})
    return {"judgements": judgements, "notes": notes}
