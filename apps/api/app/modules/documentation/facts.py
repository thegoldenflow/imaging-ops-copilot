"""The discharge summary's input (spec 7.3): an encounter's resources as plain facts, chunked by type.

`build(resources, table)` turns what the agent's read tools returned (the encounter
bundle and WP6's discharge medication table) into:

- `sources`: every resource a summary may cite, by reference, with its names and
  codes (what the validator matches against);
- `chunks`: the facts fed to the model, grouped by type (diagnoses and procedures;
  medications with the discharge medication table; observations and reports; care
  plan, open tasks and pending results), each with its id list, and split when a
  chunk is long (`MAX_FACTS`): the long-context strategy (segment summaries per
  chunk, then a synthesis);
- `missing`: key input that is not there (no diagnosis; no signed discharge
  medication table). The module then answers "insufficient information, missing X"
  instead of drafting.

Key observations are the admission and the latest vital-sign panel and the abnormal
laboratory results (first and last value per test; every INR); normal results are
left out. Dates go to the model as tokens relative to the admission
(`[DATE_n:D+2]`, restored after drafting) and free text through the free-text
de-identifier (deid.py). Pure functions: no FHIR access here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.ehr import codes as C
from app.fhir.dt import parse, ref_id

MAX_FACTS = 40  # facts per chunk before it is split
LAB_SHORT = {"33914-3": "eGFR", "2160-0": "Creatinine", "6301-6": "INR", "2823-3": "Potassium", "2951-2": "Sodium",
             "718-7": "Hemoglobin", "6690-2": "White blood cells", "777-3": "Platelets", "2345-7": "Glucose",
             "1988-5": "C-reactive protein", "10839-9": "Troponin I", "33762-6": "NT-proBNP", "2524-7": "Lactate",
             "600-7": "Blood culture"}
VITAL_SHORT = {"8867-4": "heart rate", "8480-6": "systolic BP", "8462-4": "diastolic BP", "9279-1": "respiratory rate",
               "8310-5": "temperature", "59408-5": "SpO2", "72514-3": "pain score", "9269-2": "GCS"}
OPEN_TASK = ("draft", "requested", "received", "accepted", "ready", "in-progress", "on-hold")
NOT_PENDING_CATEGORIES = ("bed-request", "surgery")
CHUNK_TITLES = {"diagnoses_procedures": "Diagnoses, reason for admission and procedures",
                "medications": "Medications in hospital and the signed discharge medication table",
                "observations_reports": "Key observations (vital signs, abnormal laboratory results) and reports",
                "care_plan": "Care plan, follow-up, open tasks and pending results"}


@dataclass
class Source:
    ref: str
    rtype: str
    kind: str  # encounter, diagnosis, procedure, medication, observation, result, report, task, plan, pending, medrec
    name: str = ""
    code: str = ""
    names: list[str] = field(default_factory=list)  # every name the validator accepts for it
    date: str = ""  # YYYY-MM-DD
    detail: str = ""  # one line for people (the source panel)
    category: str = ""  # Condition category, MedicationRequest intent, table category


@dataclass
class Chunk:
    name: str
    title: str
    facts: list[dict]

    @property
    def refs(self) -> list[str]:
        out: list[str] = []
        for f in self.facts:
            for r in f.get("refs") or [f["id"]]:
                if r not in out:
                    out.append(r)
        return out


@dataclass
class Inputs:
    encounter_id: str
    patient_id: str | None
    admitted: datetime | None
    discharged: datetime | None
    unit: str | None
    language: str
    disposition: str
    sources: dict[str, Source]
    chunks: list[Chunk]
    table: dict | None
    missing: list[str]

    def ids(self) -> list[str]:
        return list(self.sources)


def _code(concept: dict | None) -> str:
    return ((concept or {}).get("coding") or [{}])[0].get("code") or ""


def _display(concept: dict | None) -> str:
    concept = concept or {}
    return concept.get("text") or ((concept.get("coding") or [{}])[0].get("display")) or ""


def _day(value: str | None) -> str:
    when = parse(value) if value else None
    return when.date().isoformat() if when else ""


def _hhmm(value: str | None) -> str:
    when = parse(value) if value else None
    return when.strftime("%H:%M") if when else ""


def _ref(r: dict) -> str:
    return f"{r['resourceType']}/{r['id']}"


def unit_of(encounter: dict) -> str | None:
    entries = encounter.get("location") or []
    active = [e for e in entries if e.get("status") == "active"] or entries[-1:]
    bed = ref_id((active[-1].get("location") if active else None)) if active else None
    return bed.split("-")[0] if bed else None


def language_of(patient: dict | None) -> str:
    for c in (patient or {}).get("communication") or []:
        code = _code(c.get("language"))
        if code:
            return code
    return "en"


def _dose_text(med: dict) -> str:
    dosage = (med.get("dosageInstruction") or [{}])[0]
    return dosage.get("text") or ""


def _note(r: dict) -> str:
    return " ".join(n.get("text", "") for n in r.get("note") or [] if n.get("text"))


def _interpretation(o: dict) -> str:
    return _code((o.get("interpretation") or [None])[0])


def _abnormal(o: dict) -> bool:
    flag = _interpretation(o)
    if flag in ("H", "L", "HH", "LL", "A", "AA"):
        return True
    code, value = _code(o.get("code")), (o.get("valueQuantity") or {}).get("value")
    low_high = C.LABS.get(code, (None, None, None))[2]
    return value is not None and low_high is not None and not low_high[0] <= value <= low_high[1]


def _vitals(o: dict) -> dict[str, str]:
    out = {}
    for comp in o.get("component") or []:
        code = _code(comp.get("code"))
        q = comp.get("valueQuantity") or {}
        if code in VITAL_SHORT and q.get("value") is not None:
            unit = {"Cel": "°C", "mm[Hg]": "mmHg", "{score}": ""}.get(q.get("unit") or "", q.get("unit") or "")
            out[VITAL_SHORT[code]] = f"{q['value']:g} {unit}".strip()
    return out


def vitals_line(o: dict) -> str:
    v = _vitals(o)
    bp = f"BP {v.pop('systolic BP', '?').split()[0]}/{v.pop('diastolic BP', '?').split()[0]} mmHg" \
        if "systolic BP" in v else None
    return ", ".join(x for x in [bp, *(f"{k} {val}" for k, val in v.items())] if x)


def lab_value(o: dict) -> str:
    q = o.get("valueQuantity") or {}
    if q.get("value") is None:
        return o.get("valueString") or ""
    unit = (q.get("unit") or "").replace("{INR}", "").replace("{1.73_m2}", "1.73m2")
    return f"{q['value']:g} {unit}".strip()


def _is_vital_panel(o: dict) -> bool:
    return _code(o.get("code")) == C.VITAL_PANEL[0] or bool(o.get("component"))


def build(resources: list[dict], table: dict | None, *, medrec_ref: str | None = None) -> Inputs:
    """The encounter's facts. `resources`: the bundle as returned (re-identified, for matching); `table`: the
    discharge medication table (WP6 interface, as JSON) or None."""
    encounter = next((r for r in resources if r["resourceType"] == "Encounter"), None)
    if encounter is None:
        raise LookupError("no Encounter in the input")
    patient = next((r for r in resources if r["resourceType"] == "Patient"), None)
    period = encounter.get("period") or {}
    admitted, discharged = parse(period.get("start")), parse(period.get("end"))
    sources: dict[str, Source] = {}

    def add(s: Source) -> Source:
        sources[s.ref] = s
        return s

    enc_ref = _ref(encounter)
    reason = [_display(c) for c in encounter.get("reasonCode") or []]
    disposition = _display((encounter.get("hospitalization") or {}).get("dischargeDisposition"))
    add(Source(enc_ref, "Encounter", "encounter", name=", ".join(reason), code=_code((encounter.get("reasonCode")
               or [None])[0]), date=_day(period.get("start")),
               detail=f"Stay {encounter['id']}: admitted {_day(period.get('start'))}"
                      + (f", discharged {_day(period.get('end'))} ({disposition or 'disposition not recorded'})"
                         if discharged else ", still in hospital") + (f"; reason: {', '.join(reason)}" if reason else "")))
    by_type: dict[str, list[dict]] = {}
    for r in resources:
        by_type.setdefault(r["resourceType"], []).append(r)
    from app.modules.documentation.lexicon import synonyms

    # ----- A: diagnoses, reason, procedures -----
    chunk_a = [{"id": enc_ref, "kind": "reason", "reason_for_admission": reason, "admitted": _day(period.get("start")),
                "discharged": _day(period.get("end")), "disposition": disposition}]
    conditions = [c for c in by_type.get("Condition", []) if (c.get("verificationStatus") or {}).get("coding", [{}])[0]
                  .get("code") != "entered-in-error"]
    for c in sorted(conditions, key=lambda c: (_code((c.get("category") or [None])[0]) != "encounter-diagnosis", c["id"])):
        code, name = _code(c.get("code")), _display(c.get("code"))
        category = _code((c.get("category") or [None])[0])
        s = add(Source(_ref(c), "Condition", "diagnosis", name=name, code=code, names=[name, *synonyms("condition", code)],
                       date=_day(c.get("onsetDateTime") or c.get("recordedDate")), category=category,
                       detail=f"{name} ({category or 'condition'}, {_code(c.get('clinicalStatus')) or 'status ?'})"))
        chunk_a.append({"id": s.ref, "kind": "diagnosis", "name": name, "code": code, "category": category,
                        "status": _code(c.get("clinicalStatus")), "onset": s.date, "note": _note(c)})
    for p in sorted(by_type.get("Procedure", []), key=lambda p: p["id"]):
        code, name = _code(p.get("code")), _display(p.get("code"))
        when = (p.get("performedPeriod") or {}).get("start") or p.get("performedDateTime")
        s = add(Source(_ref(p), "Procedure", "procedure", name=name, code=code, names=[name, *synonyms("procedure", code)],
                       date=_day(when), detail=f"{name} on {_day(when) or '?'} ({p.get('status')})"))
        chunk_a.append({"id": s.ref, "kind": "procedure", "name": name, "code": code, "date": s.date,
                        "status": p.get("status"), "note": _note(p)})

    # ----- B: medications -----
    chunk_b = []
    for m in sorted(by_type.get("MedicationRequest", []), key=lambda m: (m.get("intent") != "order", m["id"])):
        code, name = _code(m.get("medicationCodeableConcept")), _display(m.get("medicationCodeableConcept"))
        intent = m.get("intent") or ""
        label = "discharge prescription" if intent == "plan" else "hospital order"
        s = add(Source(_ref(m), "MedicationRequest", "medication", name=name, code=code,
                       names=[name, *synonyms("medication", code)], date=_day(m.get("authoredOn")), category=intent,
                       detail=f"{name} {_dose_text(m)} ({label}, {m.get('status')})"))
        chunk_b.append({"id": s.ref, "kind": "medication", "name": name, "rxcui": code, "intent": intent,
                        "status": m.get("status"), "dose": _dose_text(m), "authored": s.date,
                        "reason": ", ".join(_display(r) for r in m.get("reasonCode") or []), "note": _note(m)})
    if table is not None:
        doc_ref = table.get("document") or medrec_ref
        if doc_ref:
            add(Source(doc_ref, "DocumentReference", "medrec", name="Discharge medication table",
                       detail=f"Discharge medication reconciliation ({table.get('status')}), session "
                              f"{table.get('session_id') or '?'}"))
        for category in ("new", "changed", "stopped", "continued"):
            for line in table.get(category) or []:
                refs = [r for r in line.get("source_refs") or [] if r]
                for r in refs:  # a home medication (MedicationStatement) is only known through the table
                    if r not in sources:
                        add(Source(r, r.split("/")[0], "medication", name=line.get("name") or "",
                                   code=line.get("rxcui") or "",
                                   names=[line.get("name") or "", *synonyms("medication", line.get("rxcui") or "")],
                                   detail=f"{line.get('name')} {line.get('previous_text') or ''} (home medication)"
                                   if r.startswith("MedicationStatement/") else f"{line.get('name')}"))
                chunk_b.append({"id": doc_ref or (refs[0] if refs else ""), "refs": ([doc_ref] if doc_ref else []) + refs,
                                "kind": "discharge_table", "category": category, "name": line.get("name"),
                                "rxcui": line.get("rxcui"), "dose": line.get("dose_text") or "",
                                "previous": line.get("previous_text") or "", "change_note": line.get("change_note") or "",
                                "pharmacist_reason": line.get("reason") or ""})

    # ----- C: observations and reports -----
    chunk_c = []
    observations = sorted(by_type.get("Observation", []), key=lambda o: o.get("effectiveDateTime") or "")
    panels = [o for o in observations if _is_vital_panel(o)]
    for label, o in ([("admission", panels[0])] if panels else []) + \
                    ([("latest", panels[-1])] if len(panels) > 1 else []):
        s = add(Source(_ref(o), "Observation", "observation", name="Vital signs", code=C.VITAL_PANEL[0],
                       names=["vital signs", "vitals"], date=_day(o.get("effectiveDateTime")),
                       detail=f"Vital signs ({label}) {_day(o.get('effectiveDateTime'))} "
                              f"{_hhmm(o.get('effectiveDateTime'))}: {vitals_line(o)}"))
        chunk_c.append({"id": s.ref, "kind": "observation", "label": label, "date": s.date,
                        "time": _hhmm(o.get("effectiveDateTime")), "values": _vitals(o)})
    labs: dict[str, list[dict]] = {}
    for o in observations:
        if not _is_vital_panel(o) and _code((o.get("category") or [None])[0]) in ("laboratory", ""):
            labs.setdefault(_code(o.get("code")), []).append(o)
    for code, series in labs.items():
        pick = series if code == "6301-6" else ([series[0], series[-1]] if any(map(_abnormal, series)) else [])
        for obs in {o["id"]: o for o in pick}.values():
            name = LAB_SHORT.get(code) or _display(obs.get("code"))
            s = add(Source(_ref(obs), "Observation", "result", name=name, code=code,
                           names=[name, _display(obs.get("code"))], date=_day(obs.get("effectiveDateTime")),
                           detail=f"{name} {lab_value(obs)} ({_interpretation(obs) or 'N'}) on "
                                  f"{_day(obs.get('effectiveDateTime'))}"))
            chunk_c.append({"id": s.ref, "kind": "result", "name": name, "code": code, "date": s.date,
                            "value": lab_value(obs), "flag": _interpretation(obs) or "N"})
    for d in sorted(by_type.get("DiagnosticReport", []), key=lambda d: d.get("effectiveDateTime") or d["id"]):
        name = _display(d.get("code"))
        s = add(Source(_ref(d), "DiagnosticReport", "report", name=name, code=_code(d.get("code")), names=[name],
                       date=_day(d.get("effectiveDateTime") or d.get("issued")),
                       detail=f"{name}: {d.get('conclusion') or 'no conclusion'}"))
        chunk_c.append({"id": s.ref, "kind": "report", "name": name, "date": s.date,
                        "conclusion": d.get("conclusion") or ""})

    # ----- D: care plan, open tasks, pending results -----
    chunk_d = []
    for cp in sorted(by_type.get("CarePlan", []), key=lambda c: c["id"]):
        activities = []
        for a in cp.get("activity") or []:
            det = a.get("detail") or {}
            when = (det.get("scheduledPeriod") or {}).get("start") or det.get("scheduledString") or ""
            activities.append({"code": _code(det.get("code")), "what": _display(det.get("code")),
                               "when": _day(when) if when and when[:2] in ("19", "20") else when,
                               "location": (det.get("location") or {}).get("display") or "",
                               "description": det.get("description") or ""})
        s = add(Source(_ref(cp), "CarePlan", "plan", name=cp.get("title") or "Care plan",
                       names=[cp.get("title") or "care plan"], date=_day((cp.get("period") or {}).get("start")),
                       detail=(cp.get("title") or "Care plan") + ": " + "; ".join(
                           f"{a['what']} {a['when']} {a['location']}".strip() for a in activities)))
        chunk_d.append({"id": s.ref, "kind": "plan", "title": cp.get("title") or "", "activities": activities})
    for t in sorted(by_type.get("Task", []), key=lambda t: t["id"]):
        if t.get("status") not in OPEN_TASK:
            continue
        name = _display(t.get("code"))
        s = add(Source(_ref(t), "Task", "task", name=name, code=_code(t.get("code")), names=[name],
                       date=_day(t.get("authoredOn")), detail=f"Open task: {t.get('description') or name}"))
        chunk_d.append({"id": s.ref, "kind": "task", "name": name, "status": t.get("status"),
                        "description": t.get("description") or ""})
    results_for = {ref_id(b) for o in observations for b in o.get("basedOn") or []}
    results_for |= {ref_id(b) for d in by_type.get("DiagnosticReport", []) for b in d.get("basedOn") or []}
    for sr in sorted(by_type.get("ServiceRequest", []), key=lambda s: s["id"]):
        category = _code((sr.get("category") or [None])[0])
        if sr.get("status") != "active" or category in NOT_PENDING_CATEGORIES or sr["id"] in results_for:
            continue
        name = LAB_SHORT.get(_code(sr.get("code"))) or _display(sr.get("code"))
        s = add(Source(_ref(sr), "ServiceRequest", "pending", name=name, code=_code(sr.get("code")),
                       names=[name, _display(sr.get("code"))], date=_day(sr.get("authoredOn")),
                       detail=f"Pending: {name} ({category}, ordered {_day(sr.get('authoredOn'))})"))
        chunk_d.append({"id": s.ref, "kind": "pending", "name": name, "category": category, "ordered": s.date})

    chunks: list[Chunk] = []
    for name, facts in (("diagnoses_procedures", chunk_a), ("medications", chunk_b),
                        ("observations_reports", chunk_c), ("care_plan", chunk_d)):
        if not facts:
            continue
        parts = [facts[i:i + MAX_FACTS] for i in range(0, len(facts), MAX_FACTS)]
        for n, part in enumerate(parts, start=1):
            suffix = f" (part {n} of {len(parts)})" if len(parts) > 1 else ""
            chunks.append(Chunk(name if len(parts) == 1 else f"{name}_{n}", CHUNK_TITLES[name] + suffix, part))

    missing = []
    if not conditions:
        missing.append("a diagnosis (no Condition recorded on the encounter)")
    if table is None:
        missing.append("the signed discharge medication table (the discharge medication reconciliation in "
                       "Medication safety, signed by the pharmacist and co-signed by a physician)")
    elif table.get("status") != "final":
        missing.append(f"a final discharge medication table (it is {str(table.get('status')).replace('_', ' ')})")
    return Inputs(encounter_id=encounter["id"], patient_id=ref_id(encounter.get("subject")), admitted=admitted,
                  discharged=discharged, unit=unit_of(encounter), language=language_of(patient),
                  disposition=disposition, sources=sources, chunks=chunks, table=table, missing=missing)


def table_lines(table: dict | None) -> list[dict]:
    """The discharge medication table as the synthesis sees it: one line per medication with its sources."""
    out = []
    for category in ("new", "changed", "stopped", "continued"):
        for line in (table or {}).get(category) or []:
            out.append({"category": category, "name": line.get("name"), "rxcui": line.get("rxcui"),
                        "dose": line.get("dose_text") or "", "previous": line.get("previous_text") or "",
                        "change_note": line.get("change_note") or "", "pharmacist_reason": line.get("reason") or "",
                        "refs": [r for r in [(table or {}).get("document"), *(line.get("source_refs") or [])] if r]})
    return out


def stay_days(inputs: Inputs) -> int:
    if not inputs.admitted:
        return 0
    end = inputs.discharged or inputs.admitted
    return max(0, (end.date() - inputs.admitted.date()).days)


def day_of(inputs: Inputs, iso: str) -> int | None:
    """Hospital day (0 = admission day) of a YYYY-MM-DD date."""
    if not iso or not inputs.admitted:
        return None
    try:
        return (date.fromisoformat(iso[:10]) - inputs.admitted.date()).days
    except ValueError:
        return None
