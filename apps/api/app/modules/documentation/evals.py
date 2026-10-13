"""Documentation evals (spec 7.3): 30 synthetic stays x three documents, run through the real path.

Each case is written into the EHR as a synthetic patient (app/ehr/seed/doccases.py); the discharge medication
reconciliation is signed through WP6's workbench service, then the documentation service drafts, validates and signs
the discharge summary (as the demo physician), the bilingual patient instructions and the SBAR handoff (as the demo
nurse), with the agents behind the runtime and the Tool Gateway. Run inside a transaction that is rolled back
(scripts/documentation_eval.py).

Measured:
- raw (pre-validator) hallucination rate: fact items of the AI draft the validator marks untraceable, over all items
  (a monitoring metric, not a gate), and an independent oracle count of medications, diagnoses and procedures the
  draft names that the case does not have;
- untraceable items after the validator: the oracle count on what is signed (red items fixed or removed, as the
  physician must) - gate 0; every oracle-found item must also be red (validator recall 1.0);
- automated proxies for the human rating's four dimensions (completeness, accuracy, readability, format; 1-5 each,
  per document type; the human rating sheet is filled in by a person, gate mean >= 4);
- bilingual consistency on 10 instructions: the same fact items, one to one, in English, Simplified and Traditional
  Chinese, with the same numbers (doses, dates, phone, temperatures);
- the 5 injected hallucinations: each blocked by the validator;
- the summary's drafting time and model calls per encounter.
A document the model could not draft (unavailable, refused, output invalid twice) fails that case for that agent,
not the run: the instructions, drafted from the signed summary, fail with the summary; the handoff is drafted
anyway. The metrics are over the documents drafted, schema validity counts the failures.
The mock model was written by the author of the cases: the mock numbers are optimistic and show the pipeline.
"""

from __future__ import annotations

import copy
import json
import re
import time
from collections import Counter
from pathlib import Path

from app.core.models import Role
from app.core.store import Store
from app.ehr import codes as C
from app.ehr.clock import hospital_now
from app.ehr.seed import doccases
from app.modules.documentation import document as D
from app.modules.documentation import lexicon as L
from app.modules.documentation import phrases as P
from app.modules.documentation import service as docs
from app.modules.documentation import summary_agent
from app.modules.documentation.readability import fk_grade
from app.modules.documentation.render import item_keys
from app.modules.documentation.validator import DischargeSummaryValidator
from app.modules.med_safety import service as S
from app.modules.med_safety.reconcile import RecItem

EVALS = Path(__file__).resolve().parents[5] / "evals"
GATES = {"untraceable_after_validator": 0, "validator_recall": 1.0, "schema_validity": 1.0,
         "bilingual_consistency": 1.0, "injected_blocked": 5, "human_rating_mean": 4.0}
MIN_BILINGUAL_DOCUMENTS = 10  # the bilingual gate counts only over at least this many non-English instructions
MONTH = {m: i + 1 for i, m in enumerate(P.MONTHS_EN)}


def load_cases() -> list[dict]:
    path = EVALS / "documentation" / "cases.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _ratio(a: float, b: float) -> float:
    return round(a / b, 4) if b else 1.0


def _scale(fraction: float) -> float:
    return round(1 + 4 * max(0.0, min(1.0, fraction)), 2)


def _user(store: Store, role: Role):
    return next(u for u in store.staff.values() if u.role == role and u.demo_login)


def sign_medrec(store: Store, encounter_id: str, pharmacist, physician) -> None:
    session = S.settle(store, S.reconcile(store, encounter_id, "discharge").id)
    for item in [RecItem.model_validate(i) for i in session.items]:
        if (item.discrepancy and item.action is None) or item.indication == "pending":
            session = S.handle_item(store, pharmacist, session.id, item.id, "accept", "Intended by the team (eval)")
    S.sign(store, pharmacist, session.id)
    S.sign(store, physician, session.id)


def _items(content: dict) -> list[tuple[str, dict]]:
    out = [("reason_for_admission", content["reason_for_admission"])]
    for key in ("hospital_course", "discharge_diagnoses", "procedures", "pending_results", "follow_up", "red_flags"):
        out += [(f"{key}[{i}]", x) for i, x in enumerate(content.get(key) or [])]
    for cat in ("new", "changed", "stopped", "continued"):
        out += [(f"medications.{cat}[{i}]", x) for i, x in enumerate(content["medications"].get(cat) or [])]
    return out


def oracle(case: dict, content: dict) -> list[str]:
    """Names in the summary that the case does not have (independent of the validator's matching)."""
    meds = {C.MEDICATIONS[e["rxcui"]][0] for key in ("home", "inpatient", "discharge") for e in case.get(key) or []}
    conditions = {case["dx"], *case.get("secondary", [])}
    procedures = {case["procedure"]["code"]} if case.get("procedure") else set()
    bad = []
    for cat in ("new", "changed", "stopped", "continued"):
        for m in content["medications"].get(cat) or []:
            if not any(L.same_name(m.get("name") or "", n) for n in meds):
                bad.append(f"medication {m.get('name')}")
    for d in content.get("discharge_diagnoses") or []:
        if d.get("code") not in conditions and not any(
                L.same_name(d.get("name") or "", n) for code in conditions for n in L.synonyms("condition", code)):
            bad.append(f"diagnosis {d.get('name')}")
    for p in content.get("procedures") or []:
        if p.get("code") not in procedures:
            bad.append(f"procedure {p.get('name')}")
    for path, item in _items(content):  # drugs named in free text
        for phrase, key in L.find_mentions(str(item.get("text") or ""), L.drug_lexicon()):
            if not any(L.same_name(phrase, n) for n in meds):
                bad.append(f"drug in text {phrase} ({path})")
    return bad


def _summary_proxies(case: dict, content: dict, table: dict, report: dict) -> dict:
    exp = case["expected"]
    present = [code in {d.get("code") for d in content["discharge_diagnoses"]} for code in exp["diagnoses"]]
    present += [code in {p.get("code") for p in content["procedures"]} for code in exp["procedures"]]
    present += [code in {f.get("code") for f in content["follow_up"]} for code in exp["follow_up"]]
    present += [bool(content["pending_results"]) for _ in exp["pending"]]
    lines = {(cat, line["rxcui"]) for cat in ("new", "changed", "stopped", "continued") for line in table.get(cat) or []}
    listed = {(cat, m.get("rxcui")) for cat in ("new", "changed", "stopped", "continued")
              for m in content["medications"].get(cat) or []}
    present += [line in listed for line in lines]
    correct = len(lines & listed) + sum(present[:len(exp["diagnoses"]) + len(exp["procedures"])])
    total = len(lines) + len(exp["diagnoses"]) + len(exp["procedures"])
    course = " ".join(s["text"] for s in content["hospital_course"])
    grade = fk_grade(course)["grade"] if course else 0
    dated = all(s.get("date") for s in content["hospital_course"]) and \
        [s["date"] for s in content["hospital_course"]] == sorted(s["date"] for s in content["hospital_course"])
    cited = all(item.get("source_refs") for _, item in _items(content))
    return {"completeness": _scale(_ratio(sum(present), len(present))),
            "accuracy": _scale(_ratio(correct, total) - 0.25 * len([i for i in report["issues"] if i["severity"] == "red"])),
            "readability": 5.0 if grade <= 12 else 4.0 if grade <= 14 else 3.0,
            "format": 5.0 - (0 if dated else 1) - (0 if cited else 2)}


def _instruction_proxies(view: dict, summary_items: dict) -> dict:
    content, r = view["content"], view["renderings"]
    meds = [p for p in summary_items if p.startswith("medications.")]
    follows = [p for p in summary_items if p.startswith("follow_up")]
    listed = {m["item"] for m in content["medicines"]}
    present = [p in listed for p in meds] + [any(f["item"] == p for f in content["follow_ups"]) for p in follows]
    present += [bool(content["red_flags"]), bool(content["daily_care"]), bool(content["reason"])]
    red = len([i for i in view["validation"]["issues"] if i["severity"] == "red"])
    en, zh, zht = r["en"]["readability"], r["zh-Hans"]["readability"], r["zh-Hant"]["readability"]
    readability = 5.0 - (0 if en["in_target"] else 1) - (0 if zh["no_abbreviations"] and zht["no_abbreviations"] else 1) \
        - (0 if zh["terms_explained"] else 1)
    blocks = all(len([b for b in r[lang]["blocks"] if b["key"] != "terms"]) == 5 for lang in P.LANGS)
    same = item_keys(r["en"]) == item_keys(r["zh-Hans"]) == item_keys(r["zh-Hant"])
    return {"completeness": _scale(_ratio(sum(present), len(present))), "accuracy": 5.0 if red == 0 else 2.0,
            "readability": readability, "format": 5.0 - (0 if blocks else 2) - (0 if same else 2)}


ABSENCE = re.compile(r"\b(?:not|no|none|never)\b[^.;]*?\b(?:recorded|documented|charted|available|measured)\b", re.I)
SENTENCE_END = re.compile(r"[.!?](?:\s+(?=[A-Z\[])|\s*$)")


def _handoff_statements(c: dict) -> list[tuple[str, dict]]:
    return [("situation", c["situation"])] + [(f"{key}[{i}]", item) for key in ("background", "assessment",
                                                                              "recommendation")
                                              for i, item in enumerate(c.get(key) or [])]


def _handoff_proxies(view: dict) -> dict:
    """Automated proxies for the SBAR's human rating, each measuring one thing (a defect lowers one proxy):

    - completeness: what the next shift needs is there: the four sections, every open task and flag in the
      recommendation, a vital-sign trend citing observations, and pain and intake/output each addressed (a statement
      or a `not_recorded` declaration);
    - accuracy: the share of claims (statements and `not_recorded` declarations) with no validator issue: cited, every
      citation an input, names and numbers in the cited sources, a declared gap really absent; 1 + 4 x share;
    - readability: statement length (the longest at most 45 words);
    - format: the share of SBAR template checks passed: the situation is one sentence, and per statement, it does not
      state an absence (that goes in `not_recorded`); 1 + 4 x share.
    An uncited statement is an accuracy defect only (it used to cost accuracy, as a red, and format too). Version 1's
    uncited "Intake and output were not recorded." is two defects, untraceable (accuracy) and misplaced (format); the
    same sentence with a token citation is a format defect only."""
    c = view["content"]
    statements = _handoff_statements(c)
    gaps = list(c.get("not_recorded") or [])
    sections = [bool(c["situation"]["text"]), bool(c["background"]), bool(c["assessment"]), bool(c["recommendation"])]
    facts = view.get("facts") or {}
    tasks = {t["id"] for t in facts.get("tasks") or []}
    flags = {f["id"] for f in facts.get("flags") or []}
    cited = {r for item in c["recommendation"] for r in item["source_refs"]}
    covered = [t in cited for t in tasks | flags]
    vitals = any(r.startswith("Observation/") for item in c["assessment"] for r in item["source_refs"])
    text = " ".join(item["text"] for _, item in statements)
    addressed = ["pain" in gaps or bool(re.search(r"\bpain\b", text, re.I)),
                 "intake_output" in gaps or bool(re.search(r"\b(?:intake|output)\b", text, re.I))]
    flawed = {i["path"] for i in view["validation"]["issues"]}
    claims = [path for path, _ in statements] + [f"not_recorded[{i}]" for i in range(len(gaps))]
    in_place = [len(SENTENCE_END.findall(c["situation"]["text"].strip())) <= 1] + \
        [not ABSENCE.search(item["text"]) for _, item in statements]
    words = [len(item["text"].split()) for _, item in statements]
    return {"completeness": _scale(_ratio(sum(sections) + sum(covered) + vitals + sum(addressed),
                                          4 + len(covered) + 1 + len(addressed))),
            "accuracy": _scale(_ratio(sum(p not in flawed for p in claims), len(claims))),
            "readability": 5.0 if max(words or [0]) <= 45 else 4.0,
            "format": _scale(_ratio(sum(in_place), len(in_place)))}


def numbers(text: str) -> list[str]:
    """Digits of a rendered line, with English month names as their numbers (dates compare across languages)."""
    for name, n in MONTH.items():
        text = re.sub(rf"\b{name} (\d+)", rf"{n} \1", text)
    return sorted(re.findall(r"\d+(?:\.\d+)?", text))


def bilingual(view: dict) -> dict:
    r = view["renderings"]
    rows = {}
    for lang in P.LANGS:
        out = {}
        for b in r[lang]["blocks"]:
            if b["key"] == "terms":
                continue
            for line in b.get("lines") or []:
                out[line["key"]] = numbers(line["text"])
            for row in b.get("rows") or []:
                out[row["key"]] = numbers(" ".join(row["cells"][:1] + row["cells"][2:3]))
            for g in b.get("groups") or []:
                for i in g["items"]:
                    out[i["key"]] = numbers(i["text"])
        rows[lang] = out
    keys_match = set(rows["en"]) == set(rows["zh-Hans"]) == set(rows["zh-Hant"])
    mismatched = [k for k in rows["en"] if not (rows["en"][k] == rows["zh-Hans"].get(k) == rows["zh-Hant"].get(k))]
    return {"items": len(rows["en"]), "keys_match": keys_match, "number_mismatches": mismatched,
            "consistent": keys_match and not mismatched}


def injected(sources: dict, table: dict | None, content: dict) -> list[dict]:
    """Five hallucinations injected into a real draft, each checked by the validator on its own."""
    med_refs = [ref for ref, s in sources.items() if s.rtype == "MedicationRequest"]
    cond_refs = [ref for ref, s in sources.items() if s.rtype == "Condition"]
    obs_refs = [ref for ref, s in sources.items() if s.rtype == "Observation"]
    samples = [
        ("invented medication citing another drug's order", "medications.new",
         {"name": "rivaroxaban", "rxcui": "1114195", "dose": "20 mg oral once daily", "note": "",
          "text": "rivaroxaban 20 mg oral once daily", "source_refs": med_refs[:1]}),
        ("medication citing a made-up resource id", "medications.continued",
         {"name": "digoxin", "rxcui": "3407", "dose": "0.125 mg oral once daily", "note": "",
          "text": "digoxin 0.125 mg oral once daily", "source_refs": ["MedicationRequest/ev-fabricated-1"]}),
        ("diagnosis that is not in the record", "discharge_diagnoses",
         {"name": "Pulmonary embolism", "code": "59282003", "text": "Pulmonary embolism",
          "source_refs": cond_refs[:1]}),
        ("procedure with no source", "procedures",
         {"name": "Coronary artery bypass grafting", "code": "232717009", "date": "", "text": "CABG",
          "source_refs": []}),
        ("unprescribed drug named in the hospital course", "hospital_course",
         {"date": "", "text": "Metronidazole was started for the infection and stopped after two days.",
          "source_refs": obs_refs[:1]}),
    ]
    out = []
    validator = DischargeSummaryValidator(sources, table)
    for title, where, item in samples:
        draft = copy.deepcopy(content)
        if where.startswith("medications."):
            group = draft["medications"][where.split(".")[1]]
        else:
            group = draft[where]
        group.append(item)
        path = f"{where}[{len(group) - 1}]"
        report = validator.validate(draft)
        hits = [i for i in report.issues if i.path == path and i.severity == "red"]
        out.append({"sample": title, "path": path, "blocked": bool(hits) and report.blocking,
                    "issues": [f"{i.code}: {i.message}" for i in hits]})
    return out


def _doc(doc_id: str) -> dict:
    return docs._system(docs.VIEW).read("DocumentReference", doc_id).to_fhir()


def _summary_step(store: Store, physician, case: dict, encounter_id: str, on_draft) -> tuple[dict, str | None]:
    """Draft the summary, remove its red items (as the physician must) and sign it: (the row's summary fields, the
    document text). `on_draft(sources, table, content)` gets the AI draft before it is signed (the injected samples).

    A DocProblem from draft_summary (model unavailable / refused) propagates and the case is a failed draft (`_step`).
    A DocProblem from signing ("Untraceable items block signing") is different: the model DID produce a draft and the
    validator is doing its job, so the case is recorded `unsigned` and keeps its pre-validator metrics (raw red and
    oracle items feed the raw hallucination rate and the validator recall); it just does not count as signed."""
    started = time.monotonic()
    view = docs.draft_summary(store, physician, encounter_id)
    seconds = time.monotonic() - started
    if view.get("status") == "insufficient":
        return {"summary_status": "insufficient", "missing": view["missing"]}, None
    raw = view["raw_validation"]
    content = view["content"]
    model_calls, chunks = view.get("model_calls"), len((view.get("inputs") or {}).get("chunks") or [])
    raw_red = [i for i in raw["issues"] if i["severity"] == "red"]
    raw_oracle = oracle(case, content)
    data = D.read_data(_doc(view["id"])) or {}
    table = (data.get("inputs") or {}).get("table") or {}
    on_draft(summary_agent.sources_from(data["sources"]), table, content)
    flagged_values = {x["value"].lower() for x in raw_red}
    missed = [o for o in raw_oracle if not any(v and v in o.lower() for v in flagged_values)]
    # fields known once the draft exists; they stay in the metrics whether or not it is signed
    base = {"summary_seconds": round(seconds, 3), "model_calls": model_calls, "chunks": chunks,
            "items": raw["checked"], "raw_red": len(raw_red), "raw_red_codes": sorted({x["code"] for x in raw_red}),
            "raw_oracle": raw_oracle, "validator_missed": missed}
    fixed = copy.deepcopy(content)
    for path in sorted({x["path"] for x in raw_red if "[" in x["path"]}, reverse=True):
        section, _, index = path.rstrip("]").rpartition("[")
        group = fixed
        for part in section.split("."):
            group = group[part]
        del group[int(index)]
    if raw_red:
        view = docs.save(store, physician, view["id"], fixed)
    try:
        signed = docs.sign(store, physician, view["id"])
    except docs.DocProblem as e:  # the validator blocked signing: a drafted case, not a model failure
        return {**base, "summary_status": "unsigned", "summary_error": str(e)}, D.read_texts(_doc(view["id"]))["en"]
    after = oracle(case, signed["content"])
    fields = {**base, "summary_status": "signed", "untraceable_after": after,
              "summary_proxy": _summary_proxies(case, signed["content"], table, signed["validation"])}
    return fields, D.read_texts(_doc(signed["id"]))["en"]


def _instructions_step(store: Store, nurse, encounter_id: str) -> tuple[dict, dict]:
    """Draft the patient instructions from the signed summary, sign them when no item is red: (fields, the view)."""
    inst = docs.draft_instructions(store, nurse, encounter_id)
    proxy = _instruction_proxies(inst, inst.get("summary_items") or {})
    red = [x for x in inst["validation"]["issues"] if x["severity"] == "red"]
    signed = docs.sign(store, nurse, inst["id"]) if not red else inst
    r = inst["renderings"]
    return {"instructions_status": "drafted", "instructions_proxy": proxy, "instructions_red": len(red),
            "instructions_signed": signed.get("doc_status") == "final", "language": inst.get("default_language"),
            "fk_grade": r["en"]["readability"]["grade"], "fk_band": r["en"]["readability"].get("band"),
            "zh_no_abbreviations": r["zh-Hans"]["readability"]["no_abbreviations"] and
            r["zh-Hant"]["readability"]["no_abbreviations"],
            "zh_terms_explained": r["zh-Hans"]["readability"]["terms_explained"],
            "terms": r["zh-Hans"]["readability"]["terms"],
            "terms_from_kg": r["zh-Hans"]["readability"]["from_knowledge_graph"]}, inst


def _handoff_step(store: Store, nurse, encounter_id: str) -> tuple[dict, str]:
    """Draft the SBAR handoff, sign it when no item is red: (fields, the text)."""
    hand = docs.draft_handoff(store, nurse, encounter_id)
    red = [x for x in hand["validation"]["issues"] if x["severity"] == "red"]
    if not red:
        docs.sign(store, nurse, hand["id"])
    return ({"handoff_status": "drafted", "handoff_proxy": _handoff_proxies(hand), "handoff_red": len(red)},
            D.read_texts(_doc(hand["id"]))["en"])


def _step(name: str, step, *args) -> tuple[dict, object]:
    """One document of a case. A draft the model could not write (unavailable, refused, output invalid twice) or
    one that cannot be signed raises DocProblem: that document of this case fails, the run goes on."""
    try:
        return step(*args)
    except docs.DocProblem as e:
        return {f"{name}_status": "failed", f"{name}_error": str(e)}, None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def run(store: Store, cases: list[dict]) -> dict:
    now = hospital_now(store)
    physician, nurse, pharmacist = (_user(store, r) for r in (Role.PHYSICIAN, Role.NURSE, Role.PHARMACIST))
    rows, documents, consistency, injections = [], [], [], []

    def first_draft(sources, table, content) -> None:  # the five injected samples go into the first AI draft
        if not injections:
            injections.extend(injected(sources, table, content))

    for i, case in enumerate(cases):
        made = doccases.materialize(store, case, now, index=i)
        enc = made["encounter"]
        sign_medrec(store, enc, pharmacist, physician)
        row = {"id": case["id"], "title": case["title"], "tempting_drug": case["expected"]["tempting_drug"]}
        document = {"id": case["id"], "title": case["title"], "summary": None, "instructions": None, "handoff": None}
        fields, document["summary"] = _step("summary", _summary_step, store, physician, case, enc, first_draft)
        row.update(fields)
        if row["summary_status"] == "signed":
            fields, inst = _step("instructions", _instructions_step, store, nurse, enc)
        else:  # the instructions are drafted from the signed summary
            fields, inst = {"instructions_status": "skipped",
                            "instructions_error": f"no signed discharge summary ({row['summary_status']})"}, None
        row.update(fields)
        if inst is not None:
            document["instructions"] = {lang: inst["renderings"][lang]["text"] for lang in P.LANGS}
            if case["patient"]["language"] != "en" and len(consistency) < 10:
                consistency.append({"id": case["id"], "language": case["patient"]["language"], **bilingual(inst)})
        fields, document["handoff"] = _step("handoff", _handoff_step, store, nurse, enc)
        row.update(fields)
        rows.append(row)
        documents.append(document)
    # a summary the model drafted (signed, or drafted but blocked at signing) keeps its pre-validator metrics;
    # the signed-only metrics (untraceable after the validator, schema validity, the proxies) count only `signed`.
    signed = [r for r in rows if r["summary_status"] == "signed"]
    drafted = [r for r in rows if r["summary_status"] in ("signed", "unsigned")]
    inst_done = [r for r in rows if r["instructions_status"] == "drafted"]
    hand_done = [r for r in rows if r["handoff_status"] == "drafted"]
    items = sum(r["items"] for r in drafted)
    raw_red = sum(r["raw_red"] for r in drafted)
    oracle_total = sum(len(r["raw_oracle"]) for r in drafted)
    oracle_caught = sum(len(r["raw_oracle"]) - len(r["validator_missed"]) for r in drafted)
    proxies = {kind: {dim: _mean([r[f"{kind}_proxy"][dim] for r in have])
                      for dim in ("completeness", "accuracy", "readability", "format")}
               for kind, have in (("summary", signed), ("instructions", inst_done), ("handoff", hand_done))}
    seconds = sorted(r["summary_seconds"] for r in drafted)
    calls = Counter(r["model_calls"] for r in drafted)
    metrics = {
        "cases": len(rows), "summaries_signed": len(signed), "summaries_drafted": len(drafted),
        "instructions_drafted": len(inst_done), "handoffs_drafted": len(hand_done),
        "schema_validity": _ratio(len(signed), len(rows)) if rows else None,
        "fact_items_checked": items, "raw_untraceable_items": raw_red,
        "raw_hallucination_rate": _ratio(raw_red, items) if drafted else None,
        "raw_oracle_items": oracle_total,
        "validator_recall": _ratio(oracle_caught, oracle_total) if drafted else None,
        "untraceable_after_validator": sum(len(r["untraceable_after"]) for r in signed),
        "instructions_red": sum(r["instructions_red"] for r in inst_done),
        "handoff_red": sum(r["handoff_red"] for r in hand_done),
        "fk_grade_mean": _mean([r["fk_grade"] for r in inst_done]),
        "fk_grade_max": max((r["fk_grade"] for r in inst_done), default=None),
        "fk_within_or_below_8": _ratio(sum(r["fk_grade"] <= 8 for r in inst_done), len(inst_done)) if inst_done else None,
        "zh_no_abbreviations": _ratio(sum(r["zh_no_abbreviations"] for r in inst_done), len(inst_done)) if inst_done else None,
        "zh_terms_explained": _ratio(sum(r["zh_terms_explained"] for r in inst_done), len(inst_done)) if inst_done else None,
        "terms_from_knowledge_graph": _ratio(sum(r["terms_from_kg"] for r in inst_done),
                                             sum(r["terms"] for r in inst_done)) if inst_done else None,
        "bilingual_documents": len(consistency),
        "bilingual_consistency": _ratio(sum(c["consistent"] for c in consistency), len(consistency)) if consistency else None,
        "injected_blocked": sum(x["blocked"] for x in injections),
        "summary_seconds_p50": seconds[len(seconds) // 2] if seconds else 0,
        "summary_seconds_max": seconds[-1] if seconds else 0,
        "model_calls_per_summary": dict(sorted(calls.items())),
        "automated_proxies": proxies,
    }

    def _proxies_ok(kind: str) -> bool:  # every dimension's mean present and >= 4 (an empty population gives None)
        return all(v is not None and v >= 4 for v in proxies[kind].values())

    gates = {
        "untraceable_after_validator": bool(signed) and metrics["untraceable_after_validator"] == 0,
        "validator_recall": metrics["validator_recall"] is not None
        and metrics["validator_recall"] >= GATES["validator_recall"],
        "schema_validity": metrics["schema_validity"] is not None
        and metrics["schema_validity"] >= GATES["schema_validity"],
        "bilingual_consistency": metrics["bilingual_consistency"] is not None
        and metrics["bilingual_consistency"] >= GATES["bilingual_consistency"]
        and len(consistency) >= MIN_BILINGUAL_DOCUMENTS,
        "injected_blocked": metrics["injected_blocked"] == GATES["injected_blocked"],
        "handoff_proxies_mean_ge_4": _proxies_ok("handoff"),
        "automated_proxies_mean_ge_4": all(_proxies_ok(k) for k in ("summary", "instructions", "handoff")),
    }
    return {"metrics": metrics, "gates": gates, "passed": all(gates.values()), "rows": rows,
            "consistency": consistency, "injected": injections, "documents": documents}
