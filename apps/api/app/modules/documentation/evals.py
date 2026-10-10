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


def _handoff_proxies(view: dict) -> dict:
    c = view["content"]
    sections = [bool(c["situation"]["text"]), bool(c["background"]), bool(c["assessment"]), bool(c["recommendation"])]
    facts = view.get("facts") or {}
    tasks = {t["id"] for t in facts.get("tasks") or []}
    flags = {f["id"] for f in facts.get("flags") or []}
    cited = {r for item in c["recommendation"] for r in item["source_refs"]}
    covered = [t in cited for t in tasks | flags]
    vitals = any(r.startswith("Observation/") for item in c["assessment"] for r in item["source_refs"])
    red = len([i for i in view["validation"]["issues"] if i["severity"] == "red"])
    amber = len([i for i in view["validation"]["issues"] if i["severity"] == "amber"])
    words = [len(item["text"].split()) for item in [c["situation"], *c["background"], *c["assessment"],
                                                    *c["recommendation"]]]
    return {"completeness": _scale(_ratio(sum(sections) + sum(covered) + vitals, 4 + len(covered) + 1)),
            "accuracy": 5.0 if red == 0 and amber == 0 else 4.0 if red == 0 else 2.0,
            "readability": 5.0 if max(words or [0]) <= 45 else 4.0,
            "format": 5.0 if all(item["source_refs"] for item in [c["situation"], *c["background"], *c["assessment"],
                                                                 *c["recommendation"]]) else 3.0}


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


def run(store: Store, cases: list[dict]) -> dict:
    now = hospital_now(store)
    physician, nurse, pharmacist = (_user(store, r) for r in (Role.PHYSICIAN, Role.NURSE, Role.PHARMACIST))
    rows, documents, consistency, injections = [], [], [], None
    for i, case in enumerate(cases):
        made = doccases.materialize(store, case, now, index=i)
        enc = made["encounter"]
        sign_medrec(store, enc, pharmacist, physician)
        started = time.monotonic()
        view = docs.draft_summary(store, physician, enc)
        seconds = time.monotonic() - started
        if view.get("status") == "insufficient":
            rows.append({"id": case["id"], "title": case["title"], "summary_status": "insufficient",
                         "missing": view["missing"]})
            continue
        raw = view["raw_validation"]
        content = view["content"]
        model_calls, chunks = view.get("model_calls"), len((view.get("inputs") or {}).get("chunks") or [])
        raw_red = [i for i in raw["issues"] if i["severity"] == "red"]
        raw_oracle = oracle(case, content)
        data = D.read_data(_doc(view["id"])) or {}
        table = (data.get("inputs") or {}).get("table") or {}
        if injections is None:
            injections = injected(summary_agent.sources_from(data["sources"]), table, content)
        fixed = copy.deepcopy(content)
        for path in sorted({x["path"] for x in raw_red if "[" in x["path"]}, reverse=True):
            section, _, index = path.rstrip("]").rpartition("[")
            group = fixed
            for part in section.split("."):
                group = group[part]
            del group[int(index)]
        if raw_red:
            view = docs.save(store, physician, view["id"], fixed)
        signed = docs.sign(store, physician, view["id"])
        after = oracle(case, signed["content"])
        flagged_values = {x["value"].lower() for x in raw_red}
        missed = [o for o in raw_oracle if not any(v and v in o.lower() for v in flagged_values)]
        summary_proxy = _summary_proxies(case, signed["content"], table, signed["validation"])

        inst = docs.draft_instructions(store, nurse, enc)
        items = inst.get("summary_items") or {}
        inst_proxy = _instruction_proxies(inst, items)
        if case["patient"]["language"] != "en" and len(consistency) < 10:
            consistency.append({"id": case["id"], "language": case["patient"]["language"], **bilingual(inst)})
        inst_red = [x for x in inst["validation"]["issues"] if x["severity"] == "red"]
        inst_signed = docs.sign(store, nurse, inst["id"]) if not inst_red else inst

        hand = docs.draft_handoff(store, nurse, enc)
        hand_proxy = _handoff_proxies(hand)
        hand_red = [x for x in hand["validation"]["issues"] if x["severity"] == "red"]
        if not hand_red:
            docs.sign(store, nurse, hand["id"])
        r = inst["renderings"]
        row = {
            "id": case["id"], "title": case["title"], "summary_status": "signed",
            "summary_seconds": round(seconds, 3), "model_calls": model_calls, "chunks": chunks,
            "items": raw["checked"], "raw_red": len(raw_red), "raw_red_codes": sorted({x["code"] for x in raw_red}),
            "raw_oracle": raw_oracle, "validator_missed": missed, "untraceable_after": after,
            "summary_proxy": summary_proxy, "instructions_proxy": inst_proxy, "handoff_proxy": hand_proxy,
            "instructions_red": len(inst_red), "instructions_signed": inst_signed.get("doc_status") == "final",
            "handoff_red": len(hand_red), "language": inst.get("default_language"),
            "fk_grade": r["en"]["readability"]["grade"], "fk_band": r["en"]["readability"].get("band"),
            "zh_no_abbreviations": r["zh-Hans"]["readability"]["no_abbreviations"] and
            r["zh-Hant"]["readability"]["no_abbreviations"],
            "zh_terms_explained": r["zh-Hans"]["readability"]["terms_explained"],
            "terms": r["zh-Hans"]["readability"]["terms"], "terms_from_kg": r["zh-Hans"]["readability"]["from_knowledge_graph"],
            "tempting_drug": case["expected"]["tempting_drug"],
        }
        rows.append(row)
        documents.append({"id": case["id"], "title": case["title"],
                          "summary": D.read_texts(_doc(signed["id"]))["en"],
                          "instructions": {lang: r[lang]["text"] for lang in P.LANGS},
                          "handoff": D.read_texts(_doc(hand["id"]))["en"]})
    done = [r for r in rows if r["summary_status"] == "signed"]
    items = sum(r["items"] for r in done)
    raw_red = sum(r["raw_red"] for r in done)
    proxies = {}
    for kind in ("summary_proxy", "instructions_proxy", "handoff_proxy"):
        proxies[kind.replace("_proxy", "")] = {dim: round(sum(r[kind][dim] for r in done) / len(done), 2)
                                               for dim in ("completeness", "accuracy", "readability", "format")}
    seconds = sorted(r["summary_seconds"] for r in done)
    calls = Counter(r["model_calls"] for r in done)
    metrics = {
        "cases": len(rows), "summaries_signed": len(done),
        "schema_validity": _ratio(len(done), len(rows)),
        "fact_items_checked": items, "raw_untraceable_items": raw_red,
        "raw_hallucination_rate": _ratio(raw_red, items),
        "raw_oracle_items": sum(len(r["raw_oracle"]) for r in done),
        "validator_recall": _ratio(sum(len(r["raw_oracle"]) - len(r["validator_missed"]) for r in done),
                                   sum(len(r["raw_oracle"]) for r in done)),
        "untraceable_after_validator": sum(len(r["untraceable_after"]) for r in done),
        "instructions_red": sum(r["instructions_red"] for r in done),
        "handoff_red": sum(r["handoff_red"] for r in done),
        "fk_grade_mean": round(sum(r["fk_grade"] for r in done) / len(done), 2),
        "fk_grade_max": max(r["fk_grade"] for r in done),
        "fk_within_or_below_8": _ratio(sum(r["fk_grade"] <= 8 for r in done), len(done)),
        "zh_no_abbreviations": _ratio(sum(r["zh_no_abbreviations"] for r in done), len(done)),
        "zh_terms_explained": _ratio(sum(r["zh_terms_explained"] for r in done), len(done)),
        "terms_from_knowledge_graph": _ratio(sum(r["terms_from_kg"] for r in done), sum(r["terms"] for r in done)),
        "bilingual_documents": len(consistency),
        "bilingual_consistency": _ratio(sum(c["consistent"] for c in consistency), len(consistency)),
        "injected_blocked": sum(x["blocked"] for x in injections or []),
        "summary_seconds_p50": seconds[len(seconds) // 2] if seconds else 0,
        "summary_seconds_max": seconds[-1] if seconds else 0,
        "model_calls_per_summary": dict(sorted(calls.items())),
        "automated_proxies": proxies,
    }
    gates = {
        "untraceable_after_validator": metrics["untraceable_after_validator"] == 0,
        "validator_recall": metrics["validator_recall"] >= GATES["validator_recall"],
        "schema_validity": metrics["schema_validity"] >= GATES["schema_validity"],
        "bilingual_consistency": metrics["bilingual_consistency"] >= GATES["bilingual_consistency"]
        and len(consistency) >= 10,
        "injected_blocked": metrics["injected_blocked"] == GATES["injected_blocked"],
        "automated_proxies_mean_ge_4": all(v >= 4 for p in proxies.values() for v in p.values()),
    }
    return {"metrics": metrics, "gates": gates, "passed": all(gates.values()), "rows": rows,
            "consistency": consistency, "injected": injections or [], "documents": documents}
