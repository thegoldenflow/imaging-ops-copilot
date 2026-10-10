"""Medication-safety evals (spec 7.2): the AI commentary on 60 labelled orders, the reconciliation on 20 cases.

Each case is written into the EHR as a synthetic patient (app/ehr/seed/medcases.py) and run through the real path:
the rules, the agent through the runtime and the Tool Gateway, the guards, the evidence check, the queue Task. Run
inside a transaction that is rolled back (scripts/med_safety_eval.py, tests/test_med_safety_eval.py).

Order review (evals/order_review): schema validity of the commentary, evidence that resolves (grounding), and the
precision and recall of the flagged concern types against the labels (gates 0.80 / 0.85); the "new without
indication" judgement is scored separately on the orders that are new medications (the reconciliation of the same
admission). Reconciliation (evals/medrec): every row's discrepancy type against the labels (accuracy gate 0.90).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from app.core.store import Store
from app.ehr.clock import hospital_now
from app.ehr.seed import medcases
from app.modules.med_safety import review_agent
from app.modules.med_safety import service as S
from app.modules.med_safety.reconcile import RecItem

EVALS = Path(__file__).resolve().parents[5] / "evals"
GATES = {"precision": 0.80, "recall": 0.85, "schema_validity": 1.0, "grounding_rate": 1.0, "medrec_accuracy": 0.90}


def load_cases(name: str) -> list[dict]:
    path = EVALS / name / "cases.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _ratio(a: int, b: int) -> float:
    return round(a / b, 4) if b else 1.0


def run_order_review(store: Store, cases: list[dict]) -> dict:
    now = hospital_now(store)
    rows = []
    for i, case in enumerate(cases):
        made = medcases.materialize(store, case, now, index=i)
        started = time.monotonic()
        review = S.review_order(store, made["order_ref"])
        review = S.comment(store, review.id)
        latency = int((time.monotonic() - started) * 1000)
        c = review.commentary or {}
        valid = review.commentary_status == "done"
        if valid:
            try:
                review_agent.Commentary.model_validate({k: c.get(k) for k in ("concerns", "alternatives",
                                                                             "guideline_refs")})
            except ValueError:
                valid = False
        bad_refs = list((c.get("unresolved_refs") or [])) + list(c.get("dropped_refs") or [])
        predicted = sorted({x["type"] for x in c.get("concerns") or []})
        expected = case["expected"]["concerns"]
        row = {"id": case["id"], "title": case["title"], "kind": review.kind, "status": review.commentary_status,
               "schema_valid": valid, "grounded": valid and not bad_refs, "bad_refs": bad_refs,
               "rules": sorted({f["rule"] for f in review.findings}), "expected": expected, "predicted": predicted,
               "true_positive": sorted(set(expected) & set(predicted)),
               "false_positive": sorted(set(predicted) - set(expected)),
               "false_negative": sorted(set(expected) - set(predicted)), "latency_ms": latency,
               "evidence_refs": sum(len(x["evidence_refs"]) for x in c.get("concerns") or [])}
        label = case["expected"].get("indicated")
        rxcui = (case.get("order") or {}).get("rxcui")
        if label is not None and rxcui and not any(h["rxcui"] == rxcui for h in case.get("home") or []):
            session = S.settle(store, S.reconcile(store, made["encounter"], "admission").id)
            item = next((RecItem.model_validate(x) for x in session.items if x["id"] == f"new-{rxcui}"), None)
            if item is not None:
                row["indication"] = {"label": label, "predicted": item.type == "new", "type": item.type,
                                     "decided_by": item.indication, "confidence": item.confidence}
        rows.append(row)
    tp = sum(len(r["true_positive"]) for r in rows)
    fp = sum(len(r["false_positive"]) for r in rows)
    fn = sum(len(r["false_negative"]) for r in rows)
    per_type: dict[str, dict] = {}
    for kind in sorted({t for r in rows for t in r["expected"] + r["predicted"]}):
        t_tp = sum(kind in r["true_positive"] for r in rows)
        t_fp = sum(kind in r["false_positive"] for r in rows)
        t_fn = sum(kind in r["false_negative"] for r in rows)
        per_type[kind] = {"tp": t_tp, "fp": t_fp, "fn": t_fn, "precision": _ratio(t_tp, t_tp + t_fp),
                          "recall": _ratio(t_tp, t_tp + t_fn)}
    judged = [r["indication"] for r in rows if "indication" in r]
    flagged = [j for j in judged if not j["predicted"]]  # "new without indication"
    really = [j for j in judged if not j["label"]]
    hit = [j for j in flagged if not j["label"]]
    latencies = sorted(r["latency_ms"] for r in rows)
    metrics = {
        "cases": len(rows), "schema_validity": _ratio(sum(r["schema_valid"] for r in rows), len(rows)),
        "grounding_rate": _ratio(sum(r["grounded"] for r in rows), len(rows)),
        "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn), "tp": tp, "fp": fp, "fn": fn,
        "latency_p50_ms": latencies[len(latencies) // 2] if latencies else 0,
        "latency_p95_ms": latencies[int(len(latencies) * 0.95) - 1] if latencies else 0,
        "new_without_indication": {
            "cases": len(judged), "accuracy": _ratio(sum(j["predicted"] == j["label"] for j in judged), len(judged)),
            "precision": _ratio(len(hit), len(flagged)), "recall": _ratio(len(hit), len(really)),
            "flagged": len(flagged), "labelled_without_indication": len(really),
            "decided_by": dict(Counter(j["decided_by"] for j in judged))},
    }
    passed = all(metrics[k] >= GATES[k] for k in ("precision", "recall", "schema_validity", "grounding_rate"))
    return {"metrics": metrics, "per_type": per_type, "gates": {k: GATES[k] for k in ("precision", "recall",
                                                                                       "schema_validity",
                                                                                       "grounding_rate")},
            "passed": passed, "results": rows}


def run_medrec(store: Store, cases: list[dict]) -> dict:
    now = hospital_now(store)
    rows = []
    confusion: Counter = Counter()
    for i, case in enumerate(cases):
        made = medcases.materialize(store, case, now, index=100 + i)
        session = S.settle(store, S.reconcile(store, made["encounter"], case["kind"]).id)
        predicted = {x["id"]: x["type"] for x in session.items}
        expected = case["expected"]["items"]
        ids = sorted(set(predicted) | set(expected))
        wrong = {k: {"expected": expected.get(k), "predicted": predicted.get(k)} for k in ids
                 if expected.get(k) != predicted.get(k)}
        for k in ids:
            confusion[(expected.get(k) or "(none)", predicted.get(k) or "(none)")] += 1
        rows.append({"id": case["id"], "title": case["title"], "kind": case["kind"], "rows": len(ids),
                     "correct": len(ids) - len(wrong), "wrong": wrong, "ai_status": session.ai_status,
                     "document": session.document_id})
    total = sum(r["rows"] for r in rows)
    correct = sum(r["correct"] for r in rows)
    accuracy = _ratio(correct, total)
    return {"metrics": {"cases": len(rows), "rows": total, "correct": correct, "accuracy": accuracy},
            "confusion": [{"expected": e, "predicted": p, "count": n} for (e, p), n in sorted(confusion.items())],
            "gates": {"accuracy": GATES["medrec_accuracy"]}, "passed": accuracy >= GATES["medrec_accuracy"],
            "results": rows}
