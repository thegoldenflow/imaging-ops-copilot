"""Render the patient instructions from their one structure (spec 7.3: both languages from the same content).

`render(content, lang, terms)` -> the five blocks in `en`, `zh-Hans` or `zh-Hant`:
why you were in hospital, the medicine table (medicine / what it is for / when to
take it / notes), follow-up appointments, red flags with what to do, daily care
(food, activity, wound). Every line carries the key of the structure item it
renders (`med:medications.changed[0]`, `flag:bleeding_signs`, ...), so the two
languages can be compared item by item (evals: bilingual consistency). Chinese
gets each medical term's explanation the first time it appears; English lists them
under "Words to know". Strings recorded in the EHR as written (a clinic's location,
an unmapped department) are shown as written in every language and listed in
`verbatim`, which the Chinese abbreviation check skips.
"""

from __future__ import annotations

import re
from datetime import date

from app.modules.documentation import phrases as P
from app.modules.documentation import terms as T
from app.modules.documentation.models import InstructionsContent
from app.modules.documentation.readability import chinese_checks, fk_grade

DOSE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|mL|\[iU\]|units?|meq)\b", re.IGNORECASE)
LANG_FOR = {"en": "en", "zh-CN": "zh-Hans", "zh-TW": "zh-Hant", "zh": "zh-Hans", "zh-Hans": "zh-Hans",
            "zh-Hant": "zh-Hant"}


def display_language(code: str | None) -> str:
    """The rendering for a patient's communication.language (zh-CN -> Simplified, zh-TW -> Traditional)."""
    return LANG_FOR.get(code or "en", "en")


def dose_amount(text: str) -> str:
    m = DOSE.search(text or "")
    return f"{m.group(1)} {m.group(2)}" if m else ""


def _dose(amount: str, lang: str) -> str:
    m = DOSE.search(amount or "")
    if not m:
        return ""
    if lang == "en":
        return f"{m.group(1)} {m.group(2)}"
    hans, hant = P.UNITS_ZH.get(m.group(2).lower(), (m.group(2), m.group(2)))
    return f"{m.group(1)}{hans if lang == 'zh-Hans' else hant}"


def _date(iso: str, lang: str) -> str:
    try:
        d = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    weekday = P.WEEKDAYS[lang][d.weekday()]
    if lang == "en":
        return f"{weekday}, {P.MONTHS_EN[d.month - 1]} {d.day}"
    return f"{d.month}月{d.day}日（{weekday}）"


def _join(items: list[str], lang: str) -> str:
    if not items:
        return ""
    if lang == "en":
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
    return "、".join(items)


class _Terms:
    """Terms in order of first appearance; Chinese explains a term the first time."""

    def __init__(self, lang: str) -> None:
        self.lang = lang
        self.seen: dict[str, dict] = {}

    def name(self, term: T.Term) -> str:
        text = term.name[self.lang]
        if term.key not in self.seen:
            explained = term.explanation is not None or term.plain
            self.seen[term.key] = {"key": term.key, "name": term.name[self.lang], "source": term.source,
                                   "explanation": term.explanation[self.lang] if term.explanation else None,
                                   "explained": explained, "facts": term.facts}
            if self.lang != "en" and term.explanation is not None:
                return f"{text}（{term.explanation[self.lang]}）"
        return text


def terms_for(content: InstructionsContent, patient_conditions: list[str]) -> dict[str, T.Term]:
    out: dict[str, T.Term] = {}
    for r in content.reason:
        out[r.item] = T.condition_term(r.code, r.name) if r.kind == "diagnosis" else T.procedure_term(r.code, r.name)
    for m in content.medicines:
        out[m.item] = T.drug_term(m.rxcui, m.name, patient_conditions)
    return out


def render(content: InstructionsContent, lang: str, *, patient_conditions: list[str] | None = None) -> dict:
    lang = display_language(lang) if lang not in P.LANGS else lang
    tm = terms_for(content, patient_conditions or [r.code for r in content.reason if r.kind == "diagnosis"])
    seen = _Terms(lang)
    verbatim: list[str] = []
    blocks: list[dict] = []
    phone = P.UNIT_PHONES.get(content.unit, P.DEFAULT_PHONE)

    # 1. why you were in hospital
    lines = []
    diagnoses = [seen.name(tm[r.item]) for r in content.reason if r.kind == "diagnosis"]
    procedures = [seen.name(tm[r.item]) for r in content.reason if r.kind == "procedure"]
    if diagnoses:
        lines.append({"key": "reason:diagnoses", "text": P.REASON["stay"][lang].format(names=_join(diagnoses, lang))})
    if procedures:
        lines.append({"key": "reason:procedures",
                      "text": P.REASON["procedure"][lang].format(names=_join(procedures, lang))})
    blocks.append({"key": "reason", "title": P.TITLES["reason"][lang], "lines": lines})

    # 2. medicines
    rows = []
    for m in content.medicines:
        name = seen.name(tm[m.item])
        dose = _dose(m.dose, lang)
        when = P.TITLES["none"][lang] if not m.times else _join([P.TIMES[t][lang] for t in m.times if t in P.TIMES],
                                                                  lang)
        if m.category == "stopped":
            when = {"en": "Do not take", "zh-Hans": "不要再吃", "zh-Hant": "不要再吃"}[lang]
        notes = [P.CATEGORY_NOTES[m.category][lang], *(P.CAUTIONS[c][lang] for c in m.cautions if c in P.CAUTIONS)]
        rows.append({"key": f"med:{m.item}", "category": m.category,
                     "cells": [f"{name} {dose}".strip() if lang == "en" else f"{name}{dose}",
                               P.PURPOSES.get(m.purpose, P.PURPOSES["other"])[lang], when,
                               " ".join(notes) if lang == "en" else "".join(notes)]})
    blocks.append({"key": "medicines", "title": P.TITLES["medicines"][lang],
                   "columns": [P.TITLES[c][lang] for c in ("col_medicine", "col_purpose", "col_when", "col_notes")],
                   "rows": rows})

    # 3. follow-up
    lines = []
    for f in content.follow_ups:
        if f.department == "other":
            dept = P.DEPARTMENTS["other"][lang].format(department=f.location or "?")
            verbatim.append(f.location or "")
        else:
            dept = P.DEPARTMENTS.get(f.department, P.DEPARTMENTS["family_doctor"])[lang]
        text = (P.FOLLOW_UP["dated"][lang].format(department=dept, date=_date(f.when, lang)) if f.when
                else P.FOLLOW_UP["undated"][lang].format(department=dept))
        if f.location and f.department != "other":
            text += (" " if lang == "en" else "") + P.FOLLOW_UP["where"][lang].format(location=f.location)
            verbatim.append(f.location)
        lines.append({"key": f"followup:{f.item}", "text": text})
    blocks.append({"key": "follow_up", "title": P.TITLES["follow_up"][lang], "lines": lines})

    # 4. red flags, emergencies first
    groups = []
    for action in ("emergency", "call"):
        items = [{"key": f"flag:{f.code}", "text": P.RED_FLAGS[f.code][0][lang]} for f in content.red_flags
                 if f.code in P.RED_FLAGS and P.RED_FLAGS[f.code][1] == action]
        if items:
            groups.append({"action": action, "text": P.ACTIONS[action][lang].format(phone=phone), "items": items})
    blocks.append({"key": "red_flags", "title": P.TITLES["red_flags"][lang], "groups": groups})

    # 5. daily care
    groups = []
    for group in P.CARE_GROUPS:
        items = [{"key": f"care:{c.code}", "text": P.DAILY_CARE[c.code][1][lang]} for c in content.daily_care
                 if c.code in P.DAILY_CARE and P.DAILY_CARE[c.code][0] == group]
        if items:
            groups.append({"group": group, "title": P.TITLES[group][lang], "items": items})
    blocks.append({"key": "daily_care", "title": P.TITLES["daily_care"][lang], "groups": groups})

    terms = list(seen.seen.values())
    if lang == "en":
        blocks.append({"key": "terms", "title": P.TITLES["terms"][lang],
                       "lines": [{"key": f"term:{t['key']}", "text": f"{t['name'][:1].upper()}{t['name'][1:]}: "
                                                                    f"{t['explanation']}."}
                                 for t in terms if t["explanation"]]})
    text = to_text(blocks, lang)
    out = {"lang": lang, "title": P.TITLES["document"][lang], "blocks": blocks, "text": text, "terms": terms,
           "verbatim": [v for v in verbatim if v], "phone": phone,
           "unit": (P.UNIT_NAMES.get(content.unit) or P.p(content.unit, content.unit, content.unit))[lang],
           "ai_note": P.TITLES["ai_note"][lang]}
    if lang == "en":
        out["readability"] = fk_grade(prose(blocks))
    else:
        check = text
        for v in out["verbatim"]:
            check = check.replace(v, "")
        out["readability"] = chinese_checks(check, terms)
    return out


def to_text(blocks: list[dict], lang: str) -> str:
    """Plain text (print preview, SMS link page, readability)."""
    out = []
    end = "." if lang == "en" else "。"
    for b in blocks:
        out.append(b["title"])
        for line in b.get("lines") or []:
            out.append(line["text"])
        for row in b.get("rows") or []:
            cells = [c for c in row["cells"] if c]
            out.append((" - ".join(cells) if lang == "en" else "；".join(cells)).rstrip(".。") + end)
        for g in b.get("groups") or []:
            if "action" in g:
                out.extend(i["text"].rstrip(".。") + end for i in g["items"])
                out.append(g["text"])
            else:
                out.append(g["title"])
                out.extend(i["text"] for i in g["items"])
        if not (b.get("lines") or b.get("rows") or b.get("groups")):
            out.append(P.TITLES["none"][lang])
    return "\n".join(out)


def prose(blocks: list[dict]) -> str:
    """The sentences the patient reads (no headings, no column titles): what the reading level is measured on."""
    out = []
    for b in blocks:
        out += [line["text"] for line in b.get("lines") or []]
        for row in b.get("rows") or []:
            name, purpose, when, notes = row["cells"]
            out.append(f"Take {name} {when.lower()}. {purpose}. {notes}".strip())
        for g in b.get("groups") or []:
            out += [i["text"].rstrip(".") + "." for i in g["items"]]
            if "action" in g:
                out.append(g["text"])
    return "\n".join(out)


def item_keys(rendered: dict) -> list[str]:
    """Every fact item key of a rendering (for the bilingual consistency check)."""
    keys = []
    for b in rendered["blocks"]:
        if b["key"] == "terms":
            continue
        keys += [line["key"] for line in b.get("lines") or []]
        keys += [row["key"] for row in b.get("rows") or []]
        keys += [i["key"] for g in b.get("groups") or [] for i in g["items"]]
    return keys
