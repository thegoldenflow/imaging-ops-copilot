"""Triage assist (spec 7.1 triage view, 8.2 step 2): a CTAS suggestion with its reasons, and the admission
model's probability with its three main factors, for the triage nurse to accept or change.

The CTAS level comes from a small table of CTAS-style modifiers over the arrival information (the ambulance's
handover vital signs, the complaint, the home medications); it is synthetic, written for this demo, and stands in
for the CTAS guideline's modifiers, which a real deployment would take from the hospital's triage system. The
admission probability is the Control Tower's flow model (`flowmodels`, trained on synthetic data) run with the
suggested level, so the nurse sees what the board will show once the triage is recorded. No language model is
involved; the suggestion is decision support (risk tier clinical_ds) and the nurse decides.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ehr import codes as C
from app.modules.control_tower import features as F
from app.modules.control_tower.flowmodels import models

ANTICOAGULANTS = {rx for rx, (_name, cls, *_rest) in C.MEDICATIONS.items() if cls == "anticoagulant"}
VITAL_NAMES = {loinc: name for name, loinc in F.VITAL_CODES.items()}
SHORT = {"8867-4": "HR", "8480-6": "SBP", "8462-4": "DBP", "9279-1": "RR", "8310-5": "T", "59408-5": "SpO₂",
         "72514-3": "pain", "9269-2": "GCS"}
UNITS = {"8310-5": " °C", "59408-5": "%", "72514-3": "/10"}


@dataclass(frozen=True)
class Modifier:
    level: int
    reason: str


def modifiers(complaint: str, vitals: dict[str, float], home_meds: list[str]) -> list[Modifier]:
    """The table's modifiers that apply, most urgent first."""
    out: list[Modifier] = []
    spo2, hr, sbp = vitals.get("59408-5"), vitals.get("8867-4"), vitals.get("8480-6")
    pain, gcs, rr = vitals.get("72514-3"), vitals.get("9269-2"), vitals.get("9279-1")
    thinners = [C.MEDICATIONS[rx][0] for rx in home_meds if rx in ANTICOAGULANTS]
    if spo2 is not None and spo2 < 86:
        out.append(Modifier(1, f"SpO₂ {spo2:g}% (severe respiratory distress)"))
    if gcs is not None and gcs < 14:
        out.append(Modifier(2, f"GCS {gcs:g} (altered level of consciousness)"))
    if sbp is not None and sbp < 90:
        out.append(Modifier(2, f"systolic BP {sbp:g} (signs of shock)"))
    if thinners and complaint in ("fall", "injury"):
        out.append(Modifier(2, f"on {thinners[0]} after a {complaint} (bleeding-risk modifier)"))
    if spo2 is not None and spo2 < 92:
        out.append(Modifier(2, f"SpO₂ {spo2:g}% on room air (moderate respiratory distress)"))
    if pain is not None and pain >= 8:
        out.append(Modifier(2, f"pain {pain:g}/10 (severe)"))
    elif pain is not None and pain >= 4:
        out.append(Modifier(3, f"pain {pain:g}/10 (moderate)"))
    if hr is not None and (hr > 110 or hr < 50):
        out.append(Modifier(3, f"heart rate {hr:g}"))
    if rr is not None and rr > 24:
        out.append(Modifier(3, f"respiratory rate {rr:g}"))
    return sorted(out, key=lambda m: m.level)


def suggest(*, age: int, complaint: str, arrival_mode: str, vitals: dict[str, float], home_meds: list[str],
            prior_admissions: int) -> dict:
    """The suggestion card's content: level, reasons, and the admission model's probability at that level."""
    found = modifiers(complaint, vitals, home_meds)
    base = 3 if complaint in ("fall", "injury", "chest pain", "shortness of breath", "abdominal pain") else 4
    level = min([base, *(m.level for m in found)])
    row = {"age": float(age), "ctas": float(level), "complaint": float(F.COMPLAINTS.index(complaint))
           if complaint in F.COMPLAINTS else float("nan"),
           "arrival_mode": float(F.ARRIVAL_MODES.index(arrival_mode)) if arrival_mode in F.ARRIVAL_MODES
           else float("nan"), "admissions_12m": float(prior_admissions)}
    for loinc, name in VITAL_NAMES.items():
        row[name] = float(vitals[loinc]) if loinc in vitals else float("nan")
    predicted = (models().predict("admission", [row]) or [None])[0]
    admission = None
    if predicted is not None:
        admission = {"value": round(predicted.value, 4), "sentence": predicted.sentence("admission"),
                     "factors": [{"feature": f, "value": v, "contribution": round(c, 4)}
                                 for f, v, c in predicted.factors]}
    return {"ctas": level, "reasons": [m.reason for m in found if m.level == level] or [f"{complaint} (base level)"],
            "other": [m.reason for m in found if m.level != level], "admission": admission,
            "source": "CTAS-style modifiers (a synthetic table) and the Control Tower's admission model"}


def card_text(s: dict, vitals: dict[str, float]) -> str:
    """The recommendation as the triage nurse reads it on the card."""
    shown = ", ".join(f"{SHORT[code]} {value:g}{UNITS.get(code, '')}" for code, value in vitals.items() if code in SHORT)
    lines = [f"CTAS {s['ctas']} suggested ({s['source']}; the nurse decides):"]
    lines += [f"- {r} -> CTAS {s['ctas']}" for r in s["reasons"]]
    lines += [f"- also: {r}" for r in s["other"]]
    if s.get("admission"):
        lines.append(f"{s['admission']['sentence']} (flow model on synthetic data).")
    lines.append(f"Handover vital signs: {shown}.")
    lines.append("Accepting records the triage in the EHR: the CTAS level, these vital signs and a fall-risk flag.")
    return "\n".join(lines)
