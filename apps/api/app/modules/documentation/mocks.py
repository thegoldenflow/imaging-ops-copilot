"""Mock models for the three documentation agents, so the demo and the evals run without an API key.

Heuristics over the prompt's own JSON sections, written by the same author as the
eval cases, so the mock eval results are optimistic: they show that the pipeline
works (chunks, segment notes, synthesis, citations, validator, rendering), not what a
model would write.

- discharge_summary: SEGMENT turns each fact into a note citing it; SYNTHESIS builds
  the summary from the notes and the discharge medication table. One failure mode
  is deliberately kept: a medicine named in a clinical note (for example "metoprolol
  was discussed, not started") is taken for a new prescription, as language models
  are known to do. The validator catches it (demo case and eval).
- patient_instructions: picks purposes, times of day and cautions by drug class and
  dosage text, departments by the care plan's code, red flags and daily care by
  diagnosis, procedure and medication.
- nursing_handoff: SBAR from the 12-hour facts: latest vital signs and flags,
  diagnoses and procedures, the trend between the first and the last vital signs of
  the window, open tasks and new orders.
"""

from __future__ import annotations

import json
import re

from app.ehr import codes as C
from app.llm.providers import mock_fixture
from app.modules.documentation import lexicon as L
from app.modules.documentation.instructions import times_from_dosage


def _json_after(text: str, marker: str):
    rest = text.split(marker + "\n", 1)
    if len(rest) < 2:
        return None
    return json.loads(rest[1].split("\n", 1)[0])


def _line_json(text: str, prefix: str) -> dict:
    for line in text.splitlines():
        if line.startswith(prefix):
            try:
                return json.loads(line[len(prefix):].strip())
            except ValueError:
                return {}
    return {}


def _offset(token: str) -> int:
    m = re.search(r":D([+-]\d+)\]", token or "")
    return int(m.group(1)) if m else 0


# ---------- discharge summary ----------


def _segment(facts: list[dict]) -> dict:
    notes = []
    drugs = L.drug_lexicon()
    for f in facts:
        kind, ref = f.get("kind"), f.get("id")
        if kind == "reason":
            reasons = ", ".join(f.get("reason_for_admission") or []) or "the admission diagnosis"
            notes.append({"kind": "reason", "date": f.get("admitted") or "", "name": reasons, "code": "",
                          "text": f"Admitted for {reasons}.", "source_refs": [ref]})
        elif kind == "diagnosis":
            notes.append({"kind": "diagnosis", "date": f.get("onset") or "", "name": f["name"], "code": f.get("code") or "",
                          "text": f"{f['name']} ({(f.get('category') or 'condition').replace('-', ' ')}).",
                          "source_refs": [ref]})
            for phrase, _key in L.find_mentions(f.get("note") or "", drugs):  # the deliberate failure mode
                notes.append({"kind": "medication", "date": "", "name": phrase, "code": "",
                              "text": f"{phrase.capitalize()} mentioned in the notes.", "source_refs": [ref]})
        elif kind == "procedure":
            notes.append({"kind": "procedure", "date": f.get("date") or "", "name": f["name"], "code": f.get("code") or "",
                          "text": f"{f['name']}.", "source_refs": [ref]})
        elif kind == "medication":
            label = "discharge prescription" if f.get("intent") == "plan" else "started in hospital"
            notes.append({"kind": "medication", "date": f.get("authored") or "", "name": f["name"],
                          "code": f.get("rxcui") or "", "text": f"{f['name']} {f.get('dose') or ''} ({label}).",
                          "source_refs": [ref]})
        elif kind == "discharge_table":
            extra = f"; previously {f['previous']}" if f.get("category") == "changed" and f.get("previous") else ""
            notes.append({"kind": "medication", "date": "", "name": f["name"], "code": f.get("rxcui") or "",
                          "text": f"{f['category']}: {f['name']} {f.get('dose') or ''}{extra}.",
                          "source_refs": f.get("refs") or [ref]})
        elif kind == "observation":
            values = f.get("values") or {}
            bp = f"BP {values.get('systolic BP', '?').split()[0]}/{values.get('diastolic BP', '?').split()[0]}" \
                if "systolic BP" in values else ""
            rest = ", ".join(f"{k} {v}" for k, v in values.items() if k not in ("systolic BP", "diastolic BP", "GCS"))
            notes.append({"kind": "observation", "date": f.get("date") or "", "name": "vital signs", "code": "",
                          "text": f"Vital signs ({f.get('label')}): {', '.join(x for x in (bp, rest) if x)}.",
                          "source_refs": [ref]})
        elif kind == "result":
            flag = {"H": "high", "L": "low", "HH": "very high", "LL": "very low"}.get(f.get("flag") or "", "")
            notes.append({"kind": "result", "date": f.get("date") or "", "name": f["name"], "code": f.get("code") or "",
                          "text": f"{f['name']} {f.get('value')}" + (f" ({flag})" if flag else "") + ".",
                          "source_refs": [ref]})
        elif kind == "report":
            notes.append({"kind": "report", "date": f.get("date") or "", "name": f["name"], "code": "",
                          "text": (f.get("conclusion") or f"{f['name']} reported.").rstrip(".") + ".",
                          "source_refs": [ref]})
        elif kind == "plan":
            for a in f.get("activities") or []:
                notes.append({"kind": "plan", "date": a.get("when") or "", "name": a.get("what") or "follow-up",
                              "code": a.get("code") or "",
                              "text": (a.get("location") or ""), "source_refs": [ref]})
        elif kind == "task":
            notes.append({"kind": "task", "date": "", "name": f.get("name") or "task", "code": "",
                          "text": f"Open task: {f.get('description') or f.get('name')}.", "source_refs": [ref]})
        elif kind == "pending":
            notes.append({"kind": "pending", "date": f.get("ordered") or "", "name": f["name"], "code": "",
                          "text": f"{f['name']} ordered, result pending.", "source_refs": [ref]})
    return {"notes": notes}


FLAG_RULES = [  # (diagnosis codes, text)
    ({"42343007"}, "Weight gain of more than 2 kg in 2 days, more swelling of the legs or more shortness of breath."),
    ({"233604007", "195951007", "13645005"}, "Worse cough or shortness of breath, or a fever."),
    ({"22298006", "29857009"}, "Chest pain or pressure that does not settle with rest."),
    ({"230690007"}, "New weakness, a drooping face or trouble speaking: call 911."),
    ({"5913000", "125605004"}, "Hip or leg pain that gets worse, or a new fall."),
    ({"91302008", "68566005"}, "Fever, chills or new confusion."),
    ({"49436004"}, "A very fast or irregular heartbeat with dizziness or fainting."),
]


def _synthesis(text: str) -> dict:
    segments = _json_after(text, "SEGMENTS_JSON:") or []
    table = _json_after(text, "TABLE_JSON:") or []
    notes = [n for s in segments for n in s.get("notes") or []]
    by_kind: dict[str, list[dict]] = {}
    for n in notes:
        by_kind.setdefault(n["kind"], []).append(n)
    diagnoses = by_kind.get("diagnosis", [])
    reason = (by_kind.get("reason") or [{}])[0]
    main = diagnoses[0] if diagnoses else None
    out: dict = {"reason_for_admission": {
        "text": f"Admitted with {main['name'].lower()}." if main else reason.get("text", "Reason not recorded."),
        "source_refs": sorted(set((reason.get("source_refs") or []) + (main.get("source_refs") if main else [])))}}
    course: dict[str, dict] = {}
    for n in notes:
        if n["kind"] not in ("procedure", "observation", "result", "report") or not n.get("date"):
            continue
        seg = course.setdefault(n["date"], {"date": n["date"], "parts": [], "source_refs": []})
        seg["parts"].append(n["text"])
        seg["source_refs"] += [r for r in n["source_refs"] if r not in seg["source_refs"]]
    if reason.get("date") and reason["date"] not in course:
        course[reason["date"]] = {"date": reason["date"], "parts": [reason.get("text", "")],
                                  "source_refs": list(reason.get("source_refs") or [])}
    out["hospital_course"] = [{"date": s["date"], "text": " ".join(s["parts"])[:880], "source_refs": s["source_refs"][:40]}
                              for s in sorted(course.values(), key=lambda s: _offset(s["date"]))][:30]
    out["discharge_diagnoses"] = [{"name": d["name"], "code": d.get("code") or "", "text": d["name"],
                                   "source_refs": d["source_refs"]} for d in diagnoses]
    out["procedures"] = [{"name": p["name"], "code": p.get("code") or "", "date": p.get("date") or "",
                          "text": f"{p['name']} ({p.get('date') or 'date not recorded'}).", "source_refs": p["source_refs"]}
                         for p in by_kind.get("procedure", [])]
    meds: dict[str, list[dict]] = {"new": [], "changed": [], "stopped": [], "continued": []}
    for line in table:
        note = line.get("change_note") or (f"was {line['previous']}" if line["category"] == "changed" and line.get("previous")
                                           else line.get("pharmacist_reason") if line["category"] == "stopped" else "")
        meds[line["category"]].append({"name": line["name"], "rxcui": line.get("rxcui") or "",
                                       "dose": line.get("dose") or "", "note": note or "",
                                       "text": f"{line['name']} {line.get('dose') or ''}".strip(),
                                       "source_refs": line.get("refs") or []})
    listed = {L.norm(line["name"]) for line in table}
    for n in by_kind.get("medication", []):  # a medicine "mentioned" in a note: the deliberate failure mode
        if any(r.startswith("Condition/") for r in n["source_refs"]) and L.norm(n["name"]) not in listed:
            meds["new"].append({"name": n["name"], "rxcui": "", "dose": "", "note": "", "text": n["name"],
                                "source_refs": n["source_refs"]})
            listed.add(L.norm(n["name"]))
    out["medications"] = meds
    out["pending_results"] = [{"test": n["name"], "text": n["text"], "source_refs": n["source_refs"]}
                              for n in by_kind.get("pending", [])]
    out["follow_up"] = [{"code": n.get("code") or "", "when": n.get("date") or "", "department": n["name"],
                         "location": n.get("text") or "",
                         "text": f"{n['name']}" + (f" on {n['date']}" if n.get("date") else "")
                                 + (f" at {n['text']}" if n.get("text") else "") + ".",
                         "source_refs": n["source_refs"]} for n in by_kind.get("plan", [])]
    flags = []
    for wanted, flag_text in FLAG_RULES:
        hits = [d for d in diagnoses if d.get("code") in wanted]
        if hits:
            flags.append({"text": flag_text, "source_refs": hits[0]["source_refs"]})
    thinners = [line for line in table if C.MEDICATIONS.get(line.get("rxcui") or "", ("", ""))[1] == "anticoagulant"
                and line["category"] != "stopped"]
    if thinners:
        flags.append({"text": "Bleeding that does not stop, blood in the urine or black stools.",
                      "source_refs": thinners[0].get("refs") or []})
    if by_kind.get("procedure"):
        flags.append({"text": "Redness, swelling, discharge or bleeding at the wound or puncture site, or a fever.",
                      "source_refs": by_kind["procedure"][0]["source_refs"]})
    if not flags and diagnoses:
        flags.append({"text": "Fever, new or worse pain, or feeling much worse.", "source_refs": diagnoses[0]["source_refs"]})
    out["red_flags"] = flags
    return out


@mock_fixture("discharge_summary")
def _mock_summary(text: str, images: list, attempt: int) -> dict:
    if "Stage: SEGMENT" in text:
        return _segment(_json_after(text, "CHUNK_JSON:") or [])
    return _synthesis(text)


# ---------- patient instructions ----------

PURPOSE_BY_CLASS = {
    "anticoagulant": "blood_thinner", "antiplatelet": "heart_protect", "statin": "cholesterol",
    "ace inhibitor": "blood_pressure", "calcium channel blocker": "blood_pressure", "thiazide diuretic": "blood_pressure",
    "beta blocker": "heart_rate", "loop diuretic": "water_pill", "potassium-sparing diuretic": "heart_pump",
    "cardiac glycoside": "heart_rate", "antiarrhythmic": "heart_rate", "biguanide": "blood_sugar",
    "dpp4 inhibitor": "blood_sugar", "sulfonylurea": "blood_sugar", "insulin": "blood_sugar", "nsaid": "pain",
    "analgesic": "pain", "opioid": "pain", "gabapentinoid": "nerve_pain", "cephalosporin": "infection",
    "penicillin": "infection", "glycopeptide": "infection", "fluoroquinolone": "infection", "macrolide": "infection",
    "sulfonamide": "infection", "nitrofuran": "infection", "nitroimidazole": "infection",
    "azole antifungal": "infection", "beta agonist": "breathing", "anticholinergic": "breathing",
    "corticosteroid": "swelling", "proton pump inhibitor": "stomach", "antiemetic": "nausea",
    "thyroid hormone": "thyroid", "ssri": "mood", "antipsychotic": "mood", "mood stabilizer": "mood",
    "benzodiazepine": "sleep_anxiety", "xanthine oxidase inhibitor": "gout", "anti-gout": "gout",
    "electrolyte": "potassium", "antimetabolite": "joints",
}
ANTIBIOTICS = {"cephalosporin", "penicillin", "glycopeptide", "fluoroquinolone", "macrolide", "sulfonamide",
               "nitrofuran", "nitroimidazole", "azole antifungal"}
DEPT_WORDS = [("anticoag", "anticoag_clinic"), ("inr", "anticoag_clinic"), ("blood thinner", "anticoag_clinic"),
              ("family", "family_doctor"), ("heart failure", "heart_failure_clinic"), ("cardio", "heart_clinic"),
              ("heart", "heart_clinic"), ("fracture", "fracture_clinic"), ("ortho", "fracture_clinic"),
              ("respir", "lung_clinic"), ("lung", "lung_clinic"), ("stroke", "stroke_clinic"),
              ("diabetes", "diabetes_clinic"), ("kidney", "kidney_clinic"), ("surg", "surgery_clinic"),
              ("physio", "physio"), ("lab", "blood_test"), ("blood test", "blood_test"), ("geriatric", "seniors_clinic"),
              ("infect", "infection_clinic")]
SURGICAL = set(C.OR_PROCEDURES) | {"16310003"}
PUNCTURE = {"18286008", "415070008"}


@mock_fixture("patient_instructions")
def _mock_instructions(text: str, images: list, attempt: int) -> dict:
    items = _json_after(text, "SUMMARY_ITEMS_JSON:") or []
    diagnoses = [i for i in items if i["item"].startswith("discharge_diagnoses")]
    procedures = [i for i in items if i["item"].startswith("procedures")]
    meds = [i for i in items if i["item"].startswith("medications.")]
    follow = [i for i in items if i["item"].startswith("follow_up")]
    dx = {i.get("code") for i in diagnoses}
    px = {i.get("code") for i in procedures}
    classes = {}
    out_meds = []
    for m in meds:
        drug_class = C.MEDICATIONS.get(m.get("rxcui") or "", ("", ""))[1]
        classes[m["item"]] = drug_class
        cautions = []
        if m.get("rxcui") == "11289":
            cautions += ["blood_test", "leafy_greens"]
        if drug_class == "anticoagulant":
            cautions += ["bleeding", "other_pain_pills"]
        if drug_class == "nsaid":
            cautions.append("with_food")
        if drug_class in ("opioid", "benzodiazepine", "gabapentinoid"):
            cautions.append("drowsy")
        if drug_class in ANTIBIOTICS:
            cautions.append("finish_all")
        if drug_class in ("loop diuretic",) and "42343007" in dx:
            cautions.append("weigh_daily")
        if drug_class in ("ace inhibitor", "calcium channel blocker", "thiazide diuretic", "beta blocker"):
            cautions.append("stand_slowly")
        if drug_class in ("insulin", "sulfonylurea"):
            cautions.append("check_sugar")
        if (C.MEDICATIONS.get(m.get("rxcui") or "", ("",) * 6)[5]) == "inhaled":
            cautions.append("inhaler")
        stopped = m["item"].startswith("medications.stopped")
        out_meds.append({"item": m["item"], "purpose": PURPOSE_BY_CLASS.get(drug_class, "other"),
                         "times": [] if stopped else times_from_dosage(m.get("dose") or "", drug_class),
                         "cautions": [] if stopped else cautions[:6]})
    out_follow = []
    for f in follow:
        code = f.get("code") or ""
        if code not in {c for _, c in DEPT_WORDS} | {"family_doctor"}:
            words = f"{f.get('department') or ''} {f.get('text') or ''}".lower()
            code = next((c for w, c in DEPT_WORDS if w in words), "other")
        out_follow.append({"item": f["item"], "department": code})
    thinner = [i for i, c in classes.items() if c == "anticoagulant" and not i.startswith("medications.stopped")]
    flags: list[tuple[str, list[str]]] = []
    if thinner:
        flags += [("heavy_bleeding", thinner[:1]), ("bleeding_signs", thinner[:1])]
    for d in diagnoses:
        code = d.get("code")
        rules = {"42343007": ["short_breath", "weight_gain"], "233604007": ["short_breath", "breathing_worse", "fever"],
                 "195951007": ["short_breath", "breathing_worse"], "22298006": ["chest_pain"], "29857009": ["chest_pain"],
                 "230690007": ["stroke_signs"], "5913000": ["worse_pain", "fall", "leg_swelling"],
                 "125605004": ["worse_pain"], "91302008": ["fever", "confusion"],
                 "68566005": ["fever", "urine_problems"], "49436004": ["fainting"], "128053003": ["leg_swelling",
                                                                                               "short_breath"]}
        flags += [(c, [d["item"]]) for c in rules.get(code, [])]
    for p in procedures:
        if p.get("code") in PUNCTURE:
            flags.append(("puncture_site", [p["item"]]))
        elif p.get("code") in SURGICAL:
            flags += [("wound_infection", [p["item"]]), ("fever", [p["item"]])]
    if any(c in ("insulin", "sulfonylurea") for c in classes.values()):
        flags.append(("low_sugar", [i for i, c in classes.items() if c in ("insulin", "sulfonylurea")][:1]))
    if not flags:
        flags = [("fever", [diagnoses[0]["item"]] if diagnoses else []), ("chest_pain", [])]
    seen, red = set(), []
    for code, because in flags:
        if code not in seen:
            seen.add(code)
            red.append({"code": code, "because": because})
    care: list[tuple[str, list[str]]] = []
    if any(m.get("rxcui") == "11289" for m in meds):
        care.append(("steady_greens", [m["item"] for m in meds if m.get("rxcui") == "11289"][:1]))
    if "42343007" in dx:
        care += [("less_salt", []), ("fluid_limit", [])]
    if "44054006" in dx or any(c in ("insulin", "sulfonylurea", "biguanide", "dpp4 inhibitor") for c in classes.values()):
        care.append(("regular_meals", []))
    if px & SURGICAL:
        care += [("keep_dry", []), ("check_wound", []), ("no_soaking", []), ("dressing", [])]
    if px & PUNCTURE:
        care.append(("puncture_care", []))
    if dx & {"5913000", "125605004"} or px & {"52734007", "609588000", "16310003"}:
        care += [("use_walker", []), ("prevent_falls", []), ("no_driving", [])]
    elif px & {"80146002", "45595009", "44558001", "23968004"}:
        care.append(("no_lifting", []))
    if not any(c[0] in ("less_salt", "regular_meals", "steady_greens") for c in care):
        care.append(("usual_diet", []))
    care += [("walk_daily", []), ("rest", [])]
    return {"reason_items": [i["item"] for i in diagnoses[:2] + procedures[:1]], "medicines": out_meds,
            "follow_ups": out_follow, "red_flags": red[:12],
            "daily_care": [{"code": c, "because": b} for c, b in dict(care).items()][:15]}


# ---------- nursing handoff ----------


@mock_fixture("nursing_handoff")
def _mock_handoff(text: str, images: list, attempt: int) -> dict:
    h = _json_after(text, "HANDOFF_JSON:") or {}
    vitals = h.get("vital_signs") or []
    labs = h.get("results") or []
    flags = h.get("flags") or []
    latest = vitals[-1] if vitals else None
    unit = h.get("unit_name") or h.get("unit") or "the unit"
    situation_refs = [h["encounter"]] + ([latest["id"]] if latest else []) + [f["id"] for f in flags]
    sit = f"Day {h.get('stay_day', '?')} of the stay on {unit}."
    if latest:
        sit += f" Latest vital signs ({latest.get('time')}): {latest.get('line')}."
    if flags:
        sit += " Active flags: " + ", ".join(f["name"].lower() for f in flags) + "."
    background = [{"text": f"Admitted with {d['name'].lower()}.", "source_refs": [d["id"]]}
                  for d in (h.get("diagnoses") or [])[:3]]
    background += [{"text": f"{p['name']} on {p.get('date') or 'a recorded date'}.", "source_refs": [p["id"]]}
                   for p in (h.get("procedures") or [])[:2]]
    meds = (h.get("medications") or [])[:5]
    if meds:
        background.append({"text": "Current medications include " + ", ".join(m["name"] for m in meds) + ".",
                           "source_refs": [m["id"] for m in meds]})
    assessment = []
    if len(vitals) >= 2:
        first, last = vitals[0], vitals[-1]
        parts = []
        for key, label in (("heart rate", "Heart rate"), ("SpO2", "SpO2"), ("temperature", "Temperature"),
                           ("respiratory rate", "Respiratory rate")):
            a, b = (first.get("values") or {}).get(key), (last.get("values") or {}).get(key)
            if a and b:
                parts.append(f"{label} {a} then {b}")
        fa, la = first.get("values") or {}, last.get("values") or {}
        if fa.get("systolic BP") and la.get("systolic BP"):
            parts.append(f"BP {fa['systolic BP'].split()[0]}/{fa.get('diastolic BP', '?').split()[0]} then "
                         f"{la['systolic BP'].split()[0]}/{la.get('diastolic BP', '?').split()[0]}")
        assessment.append({"text": "Over this shift: " + "; ".join(parts) + ".",
                           "source_refs": [first["id"], last["id"]]})
    elif latest:
        assessment.append({"text": f"One set of vital signs this shift: {latest.get('line')}.",
                           "source_refs": [latest["id"]]})
    pains = [v for v in vitals if (v.get("values") or {}).get("pain score")]
    if pains:
        assessment.append({"text": f"Pain score {pains[0]['values']['pain score']} then "
                                   f"{pains[-1]['values']['pain score']} out of 10.",
                           "source_refs": [pains[0]["id"], pains[-1]["id"]]})
    for lab in labs[:4]:
        assessment.append({"text": f"{lab['name']} {lab['value']}" + (f" ({lab['flag']})" if lab.get("flag") not in
                                                                       ("", "N") else "") + ".",
                           "source_refs": [lab["id"]]})
    assessment.append({"text": "Intake and output: not recorded this shift.",
                       "source_refs": [v["id"] for v in vitals][:5] or [h["encounter"]]})
    recommendation = [{"text": f"Open task: {t['description'].rstrip('.')}.", "source_refs": [t["id"]]}
                      for t in (h.get("tasks") or [])[:6]]
    recommendation += [{"text": f"Keep {f['name'].lower()} precautions in place.", "source_refs": [f["id"]]}
                       for f in flags]
    recommendation += [{"text": f"New order: {o['name']} {o.get('dose') or ''}".strip() + ".", "source_refs": [o["id"]]}
                       for o in (h.get("new_orders") or [])[:4]]
    recommendation += [{"text": f"Stopped: {o['name']}.", "source_refs": [o["id"]]} for o in (h.get("stopped_orders") or [])[:4]]
    if not recommendation:
        recommendation.append({"text": "No open tasks or flags; continue routine observations.",
                               "source_refs": [h["encounter"]]})
    return {"situation": {"text": sit, "source_refs": situation_refs[:30]}, "background": background[:12],
            "assessment": assessment[:16], "recommendation": recommendation[:12]}
