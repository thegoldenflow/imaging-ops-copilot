"""Voice-services demo data (spec 7.4, WP8), written at the very end of the generator.

1. Follow-up calls: stays discharged from Medicine A (the demo nurse's unit) 49 to 70 hours before the hospital day
   starts, so their follow-up call is due at 07:00, each with the WP7 documents a real stay would have by then: the
   discharge summary signed by the physician and the bilingual patient instructions signed by a nurse
   (`dsum-<stay>`, `pinstr-<stay>`, built with WP7's own builders and renderer from the stay's discharge
   prescriptions and the team's discharge plan):
   - `zh`: a Chinese-speaking patient (Simplified) after a coronary intervention (a puncture site to look after);
     in the demo they report heavy drainage from it and the call escalates (8.2 step 11);
   - `en`: an English-speaking patient whose call is clear;
   - one stay per missing condition, each of which gives a nurse the manual follow-up instead: `no_consent`
     (follow-up calls declined), `no_phone` (no number on file), `unsigned` (instructions still a draft);
   - `early`: discharged about 20 hours ago: not due yet (the 48-hour condition).
   Medicine A stays are preferred; when the window holds too few, another unit's stay is used.
2. Call-centre appointments: booked clinic and imaging appointments (`appt-vc-NN`, the service and the place on the
   Appointment, the patient's latest encounter as its `encounter` extension) for six patients: two English, two
   Simplified Chinese, one Traditional Chinese, one with nothing booked (the "no appointments" path).

Own random stream and explicit ids, after every other section, so nothing earlier changes. The cases are recorded
(ids only) in the module state `voice_demo` for the demo script, the evals and the e2e tests.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

from app.ehr import codes as C
from app.ehr.seed import documentation as doc_seed
from app.ehr.seed import medsafety
from app.ehr.seed.builder import Builder, ext
from app.ehr.seed.people import PatientInfo
from app.ehr.seed.simulate import Stay, Timeline
from app.fhir.dt import fhir_datetime, parse, ref

PCI = "415070008"
CARDIAC = {"22298006", "29857009"}
SIGNATURE = C.EXT + "signature"
SOURCE_MODULE = C.EXT + "source-module"


def _rng(b: Builder, *parts: str) -> random.Random:
    return random.Random(f"hospital:{b.seed}:voice:" + ":".join(parts))


def _unit_at(stay: Stay, at: datetime) -> str:
    bed = next((bed for bed, since, until in stay.beds if since <= at < until), stay.beds[-1][0] if stay.beds else "")
    return bed.split("-", 1)[0]


def _consents(b: Builder, patient_id: str) -> dict[str, str]:
    return doc_seed._consents(b, patient_id)


def _has_phone(b: Builder, patient_id: str) -> bool:
    patient = b.store.fhir.read("Patient", patient_id) or {}
    return any(t.get("system") == "phone" for t in patient.get("telecom") or [])


# ---------- the signed documents of a discharged stay (WP7's structures) ----------


def _rx(m: dict) -> str:
    return m["medicationCodeableConcept"]["coding"][0]["code"]


def summary_content(b: Builder, stay: Stay, p: PatientInfo) -> dict:
    """A discharge summary (discharge-summary@1) from the stay's records, every item citing its sources."""
    conds = [c for c in b.store.fhir.search("Condition", encounter=stay.id)
             if any(cc.get("code") == "encounter-diagnosis" for cat in c.get("category") or []
                    for cc in cat.get("coding") or [])]
    conds = sorted(conds, key=lambda c: (c["code"]["coding"][0]["code"] != stay.dx, c["id"]))[:3]
    procs = sorted(b.store.fhir.search("Procedure", encounter=stay.id), key=lambda r: r["id"])[:3]
    plans = sorted((m for m in b.store.fhir.search("MedicationRequest", encounter=stay.id) if m.get("intent") == "plan"),
                   key=lambda m: m["id"])
    meds = {"new": [], "changed": [], "stopped": [], "continued": []}
    for m in plans:
        rx = _rx(m)
        name = C.MEDICATIONS.get(rx, (m["medicationCodeableConcept"].get("text") or rx,))[0]
        dose = (m.get("dosageInstruction") or [{}])[0].get("text") or ""
        cat = "continued" if rx in p.home_meds else "new"
        meds[cat].append({"text": f"{name.capitalize()} {dose}".strip(), "name": name, "rxcui": rx, "dose": dose,
                          "note": "", "source_refs": [f"MedicationRequest/{m['id']}"]})
    plan = b.store.fhir.read("CarePlan", f"careplan-dc-{stay.id}")
    follow = []
    for act in (plan or {}).get("activity") or []:
        d = act.get("detail") or {}
        code = ((d.get("code") or {}).get("coding") or [{}])[0].get("code") or ""
        follow.append({"text": d.get("description") or "", "code": code,
                       "when": ((d.get("scheduledPeriod") or {}).get("start") or "")[:10],
                       "department": (d.get("code") or {}).get("text") or code,
                       "location": (d.get("location") or {}).get("display") or "",
                       "source_refs": [f"CarePlan/{plan['id']}"]})
    dx_names = [c["code"]["coding"][0].get("display") or c["code"].get("text") for c in conds]
    return {
        "reason_for_admission": {"text": f"Admitted for {dx_names[0].lower() if dx_names else 'care'}.",
                                 "source_refs": [f"Condition/{c['id']}" for c in conds[:1]]},
        "hospital_course": [{"date": fhir_datetime(stay.admit)[:10], "text": "Treated on the ward and recovered.",
                             "source_refs": [f"Encounter/{stay.id}"]}],
        "discharge_diagnoses": [{"text": n, "name": n, "code": c["code"]["coding"][0]["code"],
                                 "source_refs": [f"Condition/{c['id']}"]} for c, n in zip(conds, dx_names)],
        "procedures": [{"text": r["code"]["coding"][0].get("display") or "", "name": r["code"]["coding"][0].get("display")
                        or "", "code": r["code"]["coding"][0]["code"],
                        "date": ((r.get("performedPeriod") or {}).get("start") or "")[:10],
                        "source_refs": [f"Procedure/{r['id']}"]} for r in procs],
        "medications": meds, "pending_results": [], "follow_up": follow,
        "red_flags": [],
    }


def summary_text(content: dict) -> str:
    lines = ["DISCHARGE SUMMARY", content["reason_for_admission"]["text"], "", "Diagnoses:"]
    lines += [f"- {d['name']}" for d in content["discharge_diagnoses"]]
    if content["procedures"]:
        lines += ["", "Procedures:", *[f"- {p['name']} ({p['date']})" for p in content["procedures"]]]
    for cat in ("new", "changed", "stopped", "continued"):
        if content["medications"][cat]:
            lines += ["", f"Medications, {cat}:", *[f"- {m['text']}" for m in content["medications"][cat]]]
    if content["follow_up"]:
        lines += ["", "Follow-up:", *[f"- {f['text']} {f['when']} {f['location']}".strip() for f in content["follow_up"]]]
    return "\n".join(lines)


def _signature(role: str, practitioner: str | None, name: str, at: datetime) -> dict:
    signer = ref("Practitioner", practitioner, name) if practitioner else {"display": name}
    return {"url": SIGNATURE, "extension": [{"url": "role", "valueCode": role}, {"url": "signer", "valueReference": signer},
                                            {"url": "time", "valueDateTime": fhir_datetime(at)}]}


def _signer(b: Builder, role_code: str, unit: str) -> tuple[str | None, str]:
    """A practitioner of the role on the unit (the generated PractitionerRoles)."""
    for pr in sorted(b.store.fhir.search("PractitionerRole"), key=lambda r: r["id"]):
        codes = {c.get("code") for cc in pr.get("code") or [] for c in cc.get("coding") or []}
        units = {(loc.get("reference") or "").split("/")[-1] for loc in pr.get("location") or []}
        if role_code in codes and (unit in units or not units):
            pid = (pr.get("practitioner") or {}).get("reference", "").split("/")[-1]
            prac = b.store.fhir.read("Practitioner", pid) or {}
            return pid, ((prac.get("name") or [{}])[0]).get("text") or pid
    return None, role_code


def plant_documents(b: Builder, stay: Stay, p: PatientInfo, *, signed: bool = True, wound: bool = False) -> dict:
    """The stay's discharge summary (final) and patient instructions (final, or a draft when `signed` is false)."""
    from app.modules.documentation import document as D
    from app.modules.documentation.instructions import build, model_items, summary_items
    from app.modules.documentation.mocks import _mock_instructions
    from app.modules.documentation.models import InstructionsPlan
    from app.modules.documentation.render import display_language, render

    unit = _unit_at(stay, stay.discharge - timedelta(minutes=1))
    if not any(m.get("intent") == "plan" for m in b.store.fhir.search("MedicationRequest", encounter=stay.id)):
        medsafety.discharge_prescriptions(b, stay, p, stay.discharge)
    if not b.store.fhir.read("CarePlan", f"careplan-dc-{stay.id}"):
        warfarin = any(_rx(m) == "11289" and m.get("intent") == "plan"
                       for m in b.store.fhir.search("MedicationRequest", encounter=stay.id))
        doc_seed.care_plan(b, stay, p, stay.discharge, warfarin=warfarin)
    if wound and not any(r["code"]["coding"][0]["code"] == PCI for r in b.store.fhir.search("Procedure", encounter=stay.id)):
        done = stay.admit + min((stay.discharge - stay.admit) / 3, timedelta(hours=30))
        if stay.dx not in CARDIAC:  # the coronary intervention's reason, found during the stay
            b.condition(p.id, "22298006", done - timedelta(hours=6), enc=stay.id, category="encounter-diagnosis",
                        rid=f"cond-vc-mi-{stay.id}")
        b.procedure(p.id, stay.id, PCI, done - timedelta(minutes=70), done, performer=stay.attending,
                    rid=f"proc-vc-pci-{stay.id}")
    content = summary_content(b, stay, p)
    signed_at = stay.discharge - timedelta(hours=2)
    doctor, doctor_name = _signer(b, "doctor", unit)
    dsum_id = D.document_id("discharge_summary", stay.id)
    dsum = D.new_document("discharge_summary", dsum_id, patient_id=p.id, encounter_id=stay.id,
                          title="Discharge summary", when=fhir_datetime(signed_at - timedelta(hours=1)),
                          author="Discharge summary (seeded demo data)",
                          texts=[{"language": "en", "text": summary_text(content)}],
                          data={"schema": D.SCHEMAS["discharge_summary"], "kind": "discharge_summary",
                                "encounter_id": stay.id, "mode": "seeded", "content": content, "ai_draft": None,
                                "validation": {"validator": "DischargeSummaryValidator", "checked": 0, "traceable": 0,
                                               "issues": []}, "raw_validation": None, "sources": [], "inputs": {},
                                "edits": []}, block=None, sources=[])
    dsum["docStatus"] = "final"
    dsum["extension"] = [*dsum.get("extension", []), {"url": SOURCE_MODULE, "valueCode": "discharge_summary"},
                         _signature("physician", doctor, doctor_name, signed_at)]
    dsum["authenticator"] = ref("Practitioner", doctor, doctor_name) if doctor else {"display": doctor_name}
    b.add(dsum)

    items = summary_items(content)
    plan_data = _mock_instructions("SUMMARY_ITEMS_JSON:\n" + json.dumps(model_items(items)) + "\n", [], 0)
    instructions, _ = build(InstructionsPlan.model_validate(plan_data), items,
                            summary_ref=f"DocumentReference/{dsum_id}", unit=unit)
    lang_code = p.lang
    conditions = [i.get("code") for path, i in items.items() if path.startswith("discharge_diagnoses")]
    renderings = {lang: render(instructions, lang, patient_conditions=conditions) for lang in ("en", "zh-Hans", "zh-Hant")}
    nurse, nurse_name = _signer(b, "nurse", unit)
    pinstr_id = D.document_id("patient_instructions", stay.id)
    data = {"schema": D.SCHEMAS["patient_instructions"], "kind": "patient_instructions", "encounter_id": stay.id,
            "mode": "seeded", "content": instructions.model_dump(mode="json", by_alias=True), "ai_plan": plan_data,
            "summary": f"DocumentReference/{dsum_id}", "summary_items": items, "patient_language": lang_code,
            "default_language": display_language(lang_code),
            "validation": {"validator": "PatientInstructionsValidator", "checked": 0, "traceable": 0, "issues": []},
            "readability": {lang: r["readability"] for lang, r in renderings.items()},
            "terms": renderings["zh-Hans"]["terms"], "notes": [], "run_id": None, "prompt_version": None, "edits": []}
    pinstr = D.new_document("patient_instructions", pinstr_id, patient_id=p.id, encounter_id=stay.id,
                            title="Patient discharge instructions", when=fhir_datetime(signed_at),
                            author="Patient instructions (seeded demo data)",
                            texts=[{"language": lang, "title": r["title"], "text": r["text"][:30000]}
                                   for lang, r in renderings.items()],
                            data=data, block=None, sources=[f"DocumentReference/{dsum_id}"])
    pinstr["extension"] = [*pinstr.get("extension", []), {"url": SOURCE_MODULE, "valueCode": "patient_instructions"}]
    if signed:
        pinstr["docStatus"] = "final"
        pinstr["extension"].append(_signature("nurse", nurse, nurse_name, signed_at + timedelta(minutes=30)))
        pinstr["authenticator"] = ref("Practitioner", nurse, nurse_name) if nurse else {"display": nurse_name}
    b.add(pinstr)
    return {"stay": stay.id, "patient": p.id, "language": p.lang, "unit": unit, "instructions": pinstr_id,
            "summary": dsum_id, "signed": signed}


# ---------- call-centre appointments ----------


def _appointment(b: Builder, rid: str, p: PatientInfo, service: str, start: datetime, encounter: str | None) -> dict:
    from app.modules.voice_services.phrases import SERVICES

    s = SERVICES[service]
    end = start + timedelta(minutes=s["minutes"])
    appt = {"resourceType": "Appointment", "id": rid, "status": "booked",
            "serviceType": [C.concept(C.LOCAL, service, s["name"]["en"].capitalize())],
            "start": fhir_datetime(start), "end": fhir_datetime(end), "minutesDuration": s["minutes"],
            "participant": [{"actor": ref("Patient", p.id), "status": "accepted"},
                            {"actor": {"display": s["place"]["en"]}, "status": "accepted"}],
            "description": f"{s['name']['en'].capitalize()} (booked)"}
    if encounter:
        appt["extension"] = [ext("encounter", valueReference=ref("Encounter", encounter))]
    return b.add(appt)


def _slot(rng: random.Random, now: datetime, days: int, hours: tuple[int, int]) -> datetime:
    day = (now + timedelta(days=days)).date()
    while day.weekday() >= 5:
        day += timedelta(days=1)
    hour = rng.randint(hours[0], max(hours[0], hours[1] - 2))
    return datetime.combine(day, datetime.min.time()) + timedelta(hours=hour, minutes=rng.choice((0, 30)))


CALLERS = [  # key, language, [(service, days ahead)]
    ("en_fracture", "en", [("fracture-clinic", 5), ("ct-scan", 9)]),
    ("en_heart", "en", [("heart-clinic", 4)]),
    ("zh_anticoag", "zh-CN", [("anticoag-clinic", 3), ("heart-failure-clinic", 11)]),
    ("zh_surgery", "zh-CN", [("surgery-clinic", 6), ("ultrasound", 8)]),
    ("zh_tw_lung", "zh-TW", [("lung-clinic", 7)]),
    ("en_none", "en", []),
]


def generate(b: Builder, tl: Timeline, patients: dict[str, PatientInfo], now: datetime, used: set[str]) -> dict:
    out: dict = {"followups": {}, "callers": {}}
    rng = _rng(b, "pick")
    used = set(used)

    def discharged(lo_h: float, hi_h: float, units: tuple[str, ...] | None = ("MEDA",)) -> list[Stay]:
        return sorted((s for s in tl.stays if now - timedelta(hours=hi_h) <= s.discharge <= now - timedelta(hours=lo_h)
                       and s.disposition == "home" and s.patient not in used
                       and (units is None or _unit_at(s, s.discharge - timedelta(minutes=1)) in units)),
                      key=lambda s: s.id)

    def pick(pool: list[Stay], key) -> Stay | None:
        pool = [s for s in pool if s.patient not in used]
        if not pool:
            return None
        stay = min(pool, key=key)
        used.add(stay.patient)
        return stay

    meda = discharged(48.5, 71.5)  # the follow-up window at 07:00 (48 to 72 hours after the discharge)
    wider = discharged(48.5, 71.5, None)

    # Follow-up consent of every patient from one pass over the Consents (a search per patient scans the whole
    # in-memory store and slowed every demo reset). Patients changed below are picked (used) first, so the index is
    # never read for them after a change.
    latest: dict[str, dict] = {}
    for c in b.store.fhir.search("Consent"):
        pid = ((c.get("patient") or {}).get("reference") or "").split("/")[-1]
        cat = ((c.get("category") or [{}])[0].get("coding") or [{}])[0].get("code")
        if cat == "followup_call" and (pid not in latest or
                                       (c.get("dateTime") or "") > (latest[pid].get("dateTime") or "")):
            latest[pid] = c
    permits = {pid for pid, c in latest.items() if (c.get("provision") or {}).get("type", "deny") == "permit"}

    def ok(s: Stay) -> bool:
        return s.patient in permits and _has_phone(b, s.patient)

    # the Chinese-speaking case on Medicine A; when the window has none there, a Medicine A patient becomes one
    zh = pick([s for s in meda if patients[s.patient].lang != "en"],
              lambda s: (not ok(s), patients[s.patient].lang != "zh-CN", s.dx not in CARDIAC, s.id))
    if zh is None and (zh := pick(meda, lambda s: (s.dx not in CARDIAC, patients[s.patient].sex != "female",
                                                   abs(patients[s.patient].age - 72), s.id))):
        _make_chinese(b, patients[zh.patient], _rng(b, "zh", zh.id))
        out["converted_to_chinese"] = zh.patient
    zh = zh or pick([s for s in wider if patients[s.patient].lang != "en"], lambda s: (not ok(s), s.id))
    if zh:
        if not ok(zh):
            _make_reachable(b, patients[zh.patient], now)
        out["followups"]["zh"] = plant_documents(b, zh, patients[zh.patient], wound=True)
    en = pick([s for s in meda if patients[s.patient].lang == "en"], lambda s: (not ok(s), s.id)) or \
        pick([s for s in wider if patients[s.patient].lang == "en"], lambda s: (not ok(s), s.id))
    if en:
        if not ok(en):
            _make_reachable(b, patients[en.patient], now)
        out["followups"]["en"] = plant_documents(b, en, patients[en.patient])
    for key in ("no_consent", "no_phone", "unsigned"):
        stay = pick(meda, lambda s: (not ok(s), s.id)) or pick(wider, lambda s: (not ok(s), s.id))
        if stay is None:
            continue
        p = patients[stay.patient]
        _make_reachable(b, p, now)
        case = plant_documents(b, stay, p, signed=key != "unsigned")
        if key == "no_consent":
            b.consent(p.id, "followup_call", now - timedelta(days=3), permit=False)
        elif key == "no_phone":
            patient = b.store.fhir.read("Patient", p.id)
            patient.pop("telecom", None)
            b.store.fhir.update(patient)
        case["missing"] = {"no_consent": "consent", "no_phone": "phone", "unsigned": "instructions"}[key]
        out["followups"][key] = case
    early = pick(discharged(10, 30) or discharged(10, 30, None), lambda s: (not ok(s), s.id))
    if early:
        _make_reachable(b, patients[early.patient], now)
        out["followups"]["early"] = plant_documents(b, early, patients[early.patient])

    # call-centre appointments: patients with a recent stay or clinic visit, not used by any other demo case
    pool = sorted((p for p in patients.values() if p.id not in used and p.clinic_encounter), key=lambda p: p.id)
    for n, (key, lang, booked) in enumerate(CALLERS, start=1):
        candidates = [p for p in pool if p.lang == lang and p.id not in used]
        if not candidates:
            continue
        p = candidates[rng.randrange(len(candidates))]
        used.add(p.id)
        latest = [s for s in tl.stays if s.patient == p.id and s.discharge <= now]
        encounter = max(latest, key=lambda s: s.discharge).id if latest else p.clinic_encounter
        appts = []
        for i, (service, days) in enumerate(booked, start=1):
            from app.modules.voice_services.phrases import SERVICES

            start = _slot(_rng(b, key, service), now, days, SERVICES[service]["hours"])
            appts.append(_appointment(b, f"appt-vc-{n:02d}{i}", p, service, start, encounter)["id"])
        out["callers"][key] = {"patient": p.id, "language": lang, "appointments": appts, "encounter": encounter,
                               "services": [service for service, _ in booked]}
    b.store.modules["voice_demo"] = out
    return out


def _make_chinese(b: Builder, p: PatientInfo, rng: random.Random) -> None:
    """A Medicine A patient becomes a Mandarin speaker (Simplified Chinese), names and contact to match."""
    from app.ehr.seed.people import LANGUAGE_DISPLAY, ZH_CN_FAMILY, ZH_CN_GIVEN

    (family, family_zh), (given, given_zh) = rng.choice(ZH_CN_FAMILY), rng.choice(ZH_CN_GIVEN[p.sex])
    patient = b.store.fhir.read("Patient", p.id)
    patient["name"] = [{"use": "official", "family": family, "given": [given], "text": f"{given} {family}"},
                       {"use": "usual", "family": family_zh, "given": [given_zh], "text": family_zh + given_zh,
                        "extension": [ext("name-language", valueCode="zh-CN")]}]
    patient["communication"] = [{"language": {"coding": [{"system": "urn:ietf:bcp:47", "code": "zh-CN",
                                                           "display": LANGUAGE_DISPLAY["zh-CN"]}]}, "preferred": True}]
    for contact in patient.get("contact") or []:
        relative = rng.choice([g for g, _ in ZH_CN_GIVEN["female"]])
        contact["name"] = {"text": f"{relative} {family}"}
    b.store.fhir.update(patient)
    p.lang = "zh-CN"


def _make_reachable(b: Builder, p: PatientInfo, now: datetime) -> None:
    """Follow-up consent and a phone number for a planted case (the missing-condition cases then take one away)."""
    if _consents(b, p.id).get("followup_call") != "permit":
        b.consent(p.id, "followup_call", now - timedelta(days=5), permit=True)
    if not _has_phone(b, p.id):
        patient = b.store.fhir.read("Patient", p.id)
        patient["telecom"] = [{"system": "phone", "value": f"416-555-{int(p.id.split('-')[1]):04d}", "use": "home"}]
        b.store.fhir.update(patient)


def parse_when(value: str | None) -> datetime | None:
    return parse(value) if value else None
