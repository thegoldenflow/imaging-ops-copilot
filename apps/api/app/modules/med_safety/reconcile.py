"""The rule layer of medication reconciliation (spec 7.2, MedRec): compare two medication lists.

Admission: the home list (MedicationStatement, the provincial drug history stand-in)
against the admission orders (MedicationRequest, intent order). Discharge: the
discharge prescriptions (MedicationRequest, intent plan) against the home list and
the inpatient orders. Matching is by RxNorm ingredient. One row per medication:

| Row | When | Discrepancy |
| --- | --- | --- |
| match | on both lists with the same dose and frequency | no |
| omission | at home (or, at discharge, in hospital) but not on the new list | yes |
| dose_change | on both lists with another dose or frequency | yes |
| new | only on the new list, with an indication: the order names a diagnosis on the patient's list, or the AI found one with enough confidence | no |
| new_without_indication | only on the new list and no indication found (judged by the AI, with its confidence) | yes |

plus one `duplicate_therapy` row (a discrepancy) per therapeutic class with two
different ingredients on the new list. Which new medications have an indication is
the AI's judgement (agent `medication_reconciliation`); `apply_judgements` takes it
in, and without the AI a new medication without a named diagnosis stays a
discrepancy for the pharmacist. The pharmacist handles every discrepancy (accept,
modify or reject, with a reason); `discharge_table` turns a discharge
reconciliation into the discharge medication table (new / changed / stopped /
continued) that the discharge summary (7.3) reads.

Pure functions over facts.py records; no EHR, no model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.modules.med_safety.facts import ConditionFact, Med
from app.modules.med_safety.rules import SEVERITY_RANK, Tables, load_tables

ItemType = Literal["match", "omission", "dose_change", "duplicate_therapy", "new", "new_without_indication"]
DISCREPANCIES = ("omission", "dose_change", "duplicate_therapy", "new_without_indication")
TYPE_NAMES = {"match": "No discrepancy", "omission": "Omission", "dose_change": "Dose or frequency change",
              "duplicate_therapy": "Duplicate therapy", "new": "New (indicated)",
              "new_without_indication": "New without indication"}
CLOSED_STATUSES = ("cancelled", "entered-in-error", "stopped")


class MedLine(BaseModel):
    ref: str
    rxcui: str
    name: str
    drug_class: str = ""
    text: str = ""
    dose: float | None = None
    unit: str | None = None
    per_day: float | None = None
    route: str | None = None
    prn: bool = False


class RecItem(BaseModel):
    id: str
    type: ItemType
    severity: str = "none"
    home: MedLine | None = None
    inpatient: MedLine | None = None
    order: MedLine | None = None  # admission: the inpatient order; discharge: the discharge prescription
    others: list[MedLine] = Field(default_factory=list)  # duplicate therapy: the other ingredients of the class
    source: Literal["rule", "ai"] = "rule"
    confidence: float | None = None  # AI judgements only
    explanation: str = ""
    ai_note: str | None = None  # the AI's plain-language explanation of a discrepancy
    evidence_refs: list[str] = Field(default_factory=list)
    hospital_use: bool = False  # discharge: an inpatient-only order (intravenous, subcutaneous, as needed)
    indication: Literal["rule", "pending", "ai", "none"] | None = None  # how a new medication's indication was decided
    indication_ref: str | None = None
    # the pharmacist's handling
    action: Literal["accept", "modify", "reject"] | None = None
    reason: str | None = None
    change: str | None = None
    by: str | None = None
    by_name: str | None = None
    at: str | None = None

    @property
    def discrepancy(self) -> bool:
        return self.type in DISCREPANCIES

    @property
    def handled(self) -> bool:
        return not self.discrepancy or self.action is not None


def _line(med: Med | None) -> MedLine | None:
    return MedLine(**med.line()) if med else None


def _open(meds: list[Med]) -> list[Med]:
    """Active lists: closed orders out, one entry per ingredient (the first)."""
    out: dict[str, Med] = {}
    for m in meds:
        if m.status not in CLOSED_STATUSES and m.rxcui not in out:
            out[m.rxcui] = m
    return list(out.values())


def _change_words(old: Med, new: Med) -> str:
    dose = old.dose != new.dose or old.unit != new.unit
    freq = old.per_day != new.per_day
    return "dose and frequency changed" if dose and freq else ("dose changed" if dose else "frequency changed")


def _severity(kind: str, med: Med, tables: Tables, *, hospital_use: bool = False) -> str:
    """The severity of a discrepancy row (omission, dose change, duplicate therapy)."""
    if hospital_use:
        return "minor"
    high = med.drug_class in tables.medrec.high_risk_classes
    if kind in ("omission", "duplicate_therapy"):
        return "major" if high else "moderate"
    return "moderate"


def _indication(med: Med, conditions: list[ConditionFact]) -> ConditionFact | None:
    """The diagnosis the order names (reasonCode), when it is on the patient's active list."""
    active = {c.code: c for c in conditions if c.active}
    return next((active[c] for c in med.reason_codes if c in active), None)


def _new_item(item_id: str, med: Med, conditions: list[ConditionFact], tables: Tables, *,
              inpatient: Med | None = None, where: str) -> RecItem:
    named = _indication(med, conditions)
    if named is not None:
        return RecItem(id=item_id, type="new", order=_line(med), inpatient=_line(inpatient), indication="rule",
                       indication_ref=named.ref, evidence_refs=[med.ref, named.ref],
                       explanation=f"{med.name.capitalize()} is {where}; the order names {named.display}, on the "
                                   f"diagnosis list.")
    return RecItem(id=item_id, type="new", order=_line(med), inpatient=_line(inpatient), indication="pending",
                   evidence_refs=[med.ref],
                   explanation=f"{med.name.capitalize()} is {where}; its indication is to be checked against the "
                               f"diagnosis list.")


def _duplicates(meds: list[Med], tables: Tables, listed: str) -> list[RecItem]:
    out = []
    by_class: dict[str, list[Med]] = {}
    for m in meds:
        if m.drug_class in tables.medrec.duplicate_therapy.classes:
            by_class.setdefault(m.drug_class, []).append(m)
    for drug_class, group in sorted(by_class.items()):
        if len(group) < 2:
            continue
        first, rest = group[0], group[1:]
        names = " and ".join(m.name for m in group)
        out.append(RecItem(
            id=f"dup-{drug_class.replace(' ', '-')}", type="duplicate_therapy",
            severity=_severity("duplicate_therapy", first, tables), order=_line(first),
            others=[_line(m) for m in rest], evidence_refs=[m.ref for m in group],
            explanation=f"Duplicate therapy ({drug_class}): {names} are both {listed}."))
    return out


def compare_admission(home: list[Med], orders: list[Med], conditions: list[ConditionFact],
                      tables: Tables | None = None) -> list[RecItem]:
    """Home medications against the admission orders."""
    tables = tables or load_tables()
    home, orders = _open(home), _open(orders)
    by_rx = {o.rxcui: o for o in orders}
    items: list[RecItem] = []
    for h in home:
        o = by_rx.get(h.rxcui)
        if o is None:
            same_class = [x for x in orders if x.drug_class and x.drug_class == h.drug_class]
            hint = f" {same_class[0].name.capitalize()} of the same class is ordered (a substitution?)." \
                if same_class else ""
            items.append(RecItem(
                id=f"om-{h.rxcui}", type="omission", severity=_severity("omission", h, tables), home=_line(h),
                evidence_refs=[h.ref] + [x.ref for x in same_class[:1]],
                explanation=f"{h.name.capitalize()} ({h.text}) is on the home medication list but not ordered on "
                            f"admission.{hint}"))
        elif h.same_regimen(o):
            items.append(RecItem(id=f"ok-{h.rxcui}", type="match", home=_line(h), order=_line(o),
                                 evidence_refs=[h.ref, o.ref], explanation="Continued at the home dose."))
        else:
            items.append(RecItem(
                id=f"dc-{h.rxcui}", type="dose_change", severity=_severity("dose_change", h, tables), home=_line(h),
                order=_line(o), evidence_refs=[h.ref, o.ref],
                explanation=f"{h.name.capitalize()}: home {h.text}; ordered {o.text} ({_change_words(h, o)})."))
    home_rx = {h.rxcui for h in home}
    for o in orders:
        if o.rxcui not in home_rx:
            items.append(_new_item(f"new-{o.rxcui}", o, conditions, tables, where="new in hospital (not on the home "
                                                                                    "list)"))
    return items + _duplicates(orders, tables, "ordered")


def compare_discharge(home: list[Med], inpatient: list[Med], discharge: list[Med], conditions: list[ConditionFact],
                      tables: Tables | None = None) -> list[RecItem]:
    """The discharge prescriptions against the home list and the inpatient orders."""
    tables = tables or load_tables()
    home, inpatient, discharge = _open(home), _open(inpatient), _open(discharge)
    home_rx = {h.rxcui: h for h in home}
    inpatient_rx = {i.rxcui: i for i in inpatient}
    discharge_rx = {d.rxcui for d in discharge}
    items: list[RecItem] = []
    for d in discharge:
        h, i = home_rx.get(d.rxcui), inpatient_rx.get(d.rxcui)
        if h is not None:
            if h.same_regimen(d):
                items.append(RecItem(id=f"ok-{d.rxcui}", type="match", home=_line(h), inpatient=_line(i),
                                     order=_line(d), evidence_refs=[h.ref, d.ref],
                                     explanation="Continued at the home dose."))
            else:
                items.append(RecItem(
                    id=f"dc-{d.rxcui}", type="dose_change", severity=_severity("dose_change", d, tables),
                    home=_line(h), inpatient=_line(i), order=_line(d), evidence_refs=[h.ref, d.ref],
                    explanation=f"{d.name.capitalize()}: home {h.text}; at discharge {d.text} "
                                f"({_change_words(h, d)})."))
        elif i is not None and not i.same_regimen(d):
            items.append(RecItem(
                id=f"dc-{d.rxcui}", type="dose_change", severity=_severity("dose_change", d, tables),
                inpatient=_line(i), order=_line(d), evidence_refs=[i.ref, d.ref],
                explanation=f"{d.name.capitalize()}: in hospital {i.text}; at discharge {d.text} "
                            f"({_change_words(i, d)}); new to the patient."))
        else:
            where = "started in hospital and continued at discharge" if i is not None else "new at discharge"
            items.append(_new_item(f"new-{d.rxcui}", d, conditions, tables, inpatient=i, where=where))
    for h in home:
        if h.rxcui not in discharge_rx:
            given = "" if h.rxcui in inpatient_rx else " and was not given in hospital"
            items.append(RecItem(
                id=f"om-{h.rxcui}", type="omission", severity=_severity("omission", h, tables), home=_line(h),
                inpatient=_line(inpatient_rx.get(h.rxcui)), evidence_refs=[h.ref],
                explanation=f"{h.name.capitalize()} ({h.text}) was taken at home but is not on the discharge "
                            f"prescriptions{given}: stopped unless it is resumed."))
    for i in inpatient:
        if i.rxcui in discharge_rx or i.rxcui in home_rx:
            continue
        hospital_use = i.prn or (i.route or "") in tables.medrec.hospital_use_routes
        note = (" It is a hospital-use order (intravenous, subcutaneous or as needed): stopping it at discharge is "
                "usually intended.") if hospital_use else ""
        items.append(RecItem(
            id=f"om-{i.rxcui}", type="omission", severity=_severity("omission", i, tables, hospital_use=hospital_use),
            inpatient=_line(i), evidence_refs=[i.ref], hospital_use=hospital_use,
            explanation=f"{i.name.capitalize()} ({i.text}) was given in hospital but is not on the discharge "
                        f"prescriptions.{note}"))
    return items + _duplicates(discharge, tables, "prescribed at discharge")


# ---------- the AI's indication judgements ----------


class Judgement(BaseModel):
    """The medication_reconciliation agent's verdict on one new medication."""

    item_id: str
    indicated: bool
    indication_ref: str | None = None
    confidence: float = Field(ge=0, le=1)
    explanation: str


def candidates(items: list[RecItem]) -> list[RecItem]:
    """New medications whose indication the AI has to judge."""
    return [i for i in items if i.type == "new" and i.indication == "pending"]


def apply_judgements(items: list[RecItem], judgements: dict[str, Judgement], threshold: float, *,
                     unavailable: str | None = None, condition_refs: set[str] | None = None) -> list[RecItem]:
    """Settle every pending new medication: indicated with enough confidence stays `new`; otherwise it becomes
    `new_without_indication` (the AI's call with its confidence, or, without a judgement, the rule fallback).
    An indication reference must be one of the patient's conditions (`condition_refs`), else it is dropped."""
    out = []
    for item in items:
        if not (item.type == "new" and item.indication == "pending"):
            out.append(item)
            continue
        j = judgements.get(item.id)
        if j is None:
            why = unavailable or "no AI judgement"
            out.append(item.model_copy(update=dict(
                type="new_without_indication", severity="moderate", indication="none", source="rule",
                explanation=f"{item.explanation.split(';')[0]}; the order names no diagnosis on the list and the "
                            f"indication was not checked by the AI ({why}). Check the indication.")))
            continue
        ref = j.indication_ref if j.indication_ref and (condition_refs is None or j.indication_ref in condition_refs) \
            else None
        if j.indicated and ref and j.confidence >= threshold:
            out.append(item.model_copy(update=dict(
                indication="ai", indication_ref=ref, source="ai", confidence=j.confidence, explanation=j.explanation,
                evidence_refs=list(dict.fromkeys([*item.evidence_refs, ref])))))
        else:
            unsure = " (below the confidence threshold)" if j.indicated and j.confidence < threshold else ""
            out.append(item.model_copy(update=dict(
                type="new_without_indication", severity="moderate", indication="ai", source="ai",
                confidence=j.confidence, explanation=j.explanation + unsure,
                indication_ref=ref, evidence_refs=list(dict.fromkeys([*item.evidence_refs, *([ref] if ref else [])])))))
    return out


def severity_of(items: list[RecItem]) -> str:
    """The most severe discrepancy still waiting for the pharmacist ("none" when every one is handled)."""
    open_ = [i.severity for i in items if i.discrepancy and i.action is None]
    return max(open_, key=lambda s: SEVERITY_RANK[s], default="none")


# ---------- the discharge medication table ----------


class DischargeMedication(BaseModel):
    """One line of the discharge medication table (spec 7.2 step 5; read by the discharge summary, 7.3)."""

    rxcui: str
    name: str
    category: Literal["new", "changed", "stopped", "continued"]
    dose_text: str | None = None  # the discharge prescription's dosage (None when stopped)
    previous_text: str | None = None  # the home dosage (changed, continued, stopped)
    change_note: str | None = None  # the pharmacist's change (an item handled with "modify")
    reason: str | None = None  # the pharmacist's reason for the line's item
    source_refs: list[str] = Field(default_factory=list)  # discharge prescription, home statement, inpatient order


CATEGORIES = ("new", "changed", "stopped", "continued")


def discharge_table(items: list[RecItem]) -> dict[str, list[dict]]:
    """{new, changed, stopped, continued} from a discharge reconciliation, as the pharmacist settled it: a home
    medication omitted at discharge is stopped, or continued when the pharmacist resumed it (modify); an
    inpatient-only order resumed by the pharmacist becomes new; every line keeps its sources."""
    table: dict[str, list[dict]] = {c: [] for c in CATEGORIES}

    def add(category: str, line: MedLine, item: RecItem, *, dose: MedLine | None, previous: MedLine | None) -> None:
        refs = [x.ref for x in (item.order, item.home, item.inpatient) if x is not None]
        table[category].append(DischargeMedication(
            rxcui=line.rxcui, name=line.name, category=category, dose_text=dose.text if dose else None,
            previous_text=previous.text if previous else None,
            change_note=item.change if item.action == "modify" else None, reason=item.reason,
            source_refs=list(dict.fromkeys(refs))).model_dump())

    for item in items:
        if item.type == "duplicate_therapy":
            continue
        if item.order is not None:  # on the discharge prescriptions
            if item.type == "match":
                add("continued", item.order, item, dose=item.order, previous=item.home)
            elif item.type == "dose_change" and item.home is not None:
                add("changed", item.order, item, dose=item.order, previous=item.home)
            else:
                add("new", item.order, item, dose=item.order, previous=None)
        elif item.home is not None:  # a home medication not prescribed at discharge
            resumed = item.action == "modify"
            add("continued" if resumed else "stopped", item.home, item, dose=item.home if resumed else None,
                previous=item.home)
        elif item.inpatient is not None and item.action == "modify":  # an inpatient order the pharmacist resumes
            add("new", item.inpatient, item, dose=item.inpatient, previous=None)
    for rows in table.values():
        rows.sort(key=lambda r: r["name"])
    return table
