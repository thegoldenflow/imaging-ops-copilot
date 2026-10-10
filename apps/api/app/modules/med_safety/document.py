"""The medication reconciliation record's content (spec 7.2 step 5): a readable text and the structured data.

The DocumentReference (type medrec, category admission or discharge) carries two
attachments: `text/plain` for people, and `application/json` (title `medrec-data`)
with every item, its source (rule or AI, with the AI's confidence), the
pharmacist's action and reason, and for a discharge the discharge medication table.
The discharge summary (7.3) reads the JSON (app/modules/med_safety/discharge.py).
Pure functions, used by the agent (draft) and the service (the pharmacist's final
version, written before the signature).
"""

from __future__ import annotations

import json

from app.modules.med_safety.reconcile import CATEGORIES, TYPE_NAMES, RecItem, discharge_table

SCHEMA = "medrec@1"


def _line(item: RecItem) -> str:
    home = item.home.text if item.home else "-"
    home = f"{item.home.name} {home}" if item.home else "-"
    order = f"{item.order.name} {item.order.text}" if item.order else (
        f"(in hospital: {item.inpatient.name} {item.inpatient.text})" if item.inpatient else "-")
    if item.type == "duplicate_therapy":
        order = " + ".join(f"{m.name}" for m in [item.order, *item.others] if m)
    source = "AI" + (f", confidence {item.confidence:.2f}" if item.confidence is not None else "") \
        if item.source == "ai" else "rule"
    out = f"- {TYPE_NAMES[item.type]} [{source}]: home {home} | {order}. {item.explanation}"
    if item.ai_note:
        out += f" (AI note: {item.ai_note})"
    if item.action:
        out += f"\n    Pharmacist: {item.action}" + (f" - {item.change}" if item.change else "") + \
               (f"; reason: {item.reason}" if item.reason else "")
    return out


def render(session: dict, items: list[RecItem], *, final: bool = False) -> tuple[str, str]:
    """(text, data) of the record. `session`: id, kind, encounter_id, signatures (when signed)."""
    kind = session["kind"]
    table = discharge_table(items) if kind == "discharge" else None
    lines = [f"MEDICATION RECONCILIATION ({kind.upper()})" + ("" if final else " - DRAFT"),
             f"Encounter {session['encounter_id']}; reconciliation {session['id']}.",
             "Compared: " + ("home medications (provincial drug history) with the admission orders."
                             if kind == "admission" else
                             "discharge prescriptions with the home medications and the inpatient orders."),
             "", "Items:"]
    lines += [_line(i) for i in items] or ["- (no medications on either list)"]
    if table is not None:
        lines += ["", "Discharge medication table:"]
        for category in CATEGORIES:
            rows = table[category]
            lines.append(f"  {category.capitalize()}: " + ("; ".join(
                f"{r['name']} {r['dose_text'] or ''}".strip() + (f" (was {r['previous_text']})"
                                                                 if category == "changed" else "")
                + (f" [pharmacist: {r['change_note']}]" if r["change_note"] else "") for r in rows) or "none"))
    if session.get("signatures"):
        lines += ["", "Signatures: " + "; ".join(f"{s['role']} {s.get('name') or s['by']} at {s['at']}"
                                                  for s in session["signatures"])]
    lines += ["", "Rule tables and AI judgements are decision support for the pharmacist (demo; synthetic data)."]
    data = {"schema": SCHEMA, "session_id": session["id"], "kind": kind, "encounter_id": session["encounter_id"],
            "items": [i.model_dump(mode="json") for i in items], "discharge_medications": table}
    return "\n".join(lines), json.dumps(data, ensure_ascii=False)
