"""Voice-services evals (spec 7.4): the scripts in evals/call_center, evals/followup and evals/voice_bilingual run
through the real path on the seeded hospital (the dialogs, the agents through the runtime and the Tool Gateway, the
rules, the write-back), each case in its own savepoint that is rolled back.

- Call centre: intent accuracy (the first intent the call recognised against the script's label; gate 0.90) and the
  identity-failure scripts put through to a person (gate 100%); per-turn server latency.
- Follow-up: red-flag recall over the 15 red-flag scripts (escalated; gate 0.95), the rules alone (the same scripts
  with the model down: every one escalated by the rules, the 7.4 acceptance), over-escalation on the 35 others
  (recorded, not gated), and the rule codes against the labels.
- Bilingual: 10 scenarios in English and Chinese: every agent sentence in the caller's language, the same outcome in
  both languages, the write-back present.

Written alongside the mock models, so the mock numbers are optimistic: they show the pipeline, not a model.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from app.core.store import Store
from app.ehr.codes import HCN_SYSTEM
from app.llm.gateway import LlmGateway, get_gateway, set_gateway
from app.llm.providers import ProviderUnavailable
from app.modules.evals.paths import EVALS_DIR
from app.modules.voice_services import callcenter, followup

EVALS = EVALS_DIR
INTENT_GATE, IDENTITY_GATE, RECALL_GATE = 0.90, 1.0, 0.95
CJK = re.compile(r"[一-鿿]")
DEFAULTS = {"en": {"flag": "no", "med": "yes", "med_problems": "no", "followup": "yes, I know",
                   "wound": "It looks fine.", "care": "yes", "open": "I'm okay.", "other": "no, that's all"},
            "zh": {"flag": "没有", "med": "有，按时吃", "med_problems": "没有", "followup": "知道",
                   "wound": "伤口挺好的", "care": "能做到", "open": "还可以", "other": "没有了"}}


class Down:
    """The model provider down: the rules have to escalate on their own."""

    mode = "anthropic"

    def complete_json(self, **_):
        raise ProviderUnavailable("down for the eval")

    def tool_turn(self, **_):
        raise ProviderUnavailable("down for the eval")


def load(name: str) -> list[dict]:
    path = EVALS / name / "cases.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _isolated(store: Store, fn):
    """One case: its own savepoint, rolled back afterwards (the next case starts from the seeded state)."""
    try:
        return fn()
    finally:
        store.rollback()


# ---------- identity lines of the planted callers ----------


def _patient(patient_id: str) -> dict:
    """The planted patient, read through the gateway as the system (the eval plays the caller)."""
    return callcenter.system_fhir().read("Patient", patient_id).to_fhir()


def identity_lines(store: Store, patient_id: str) -> dict[str, str]:
    p = _patient(patient_id)
    official = next(n["text"] for n in p["name"] if n.get("use") == "official")
    usual = next((n["text"] for n in p["name"] if n.get("use") == "usual"), official)
    card = next(i["value"] for i in p["identifier"] if i["system"] == HCN_SYSTEM)[-4:]
    y, m, d = p["birthDate"].split("-")
    return {"{id}": f"This is {official}, born {p['birthDate']}, card ending {card}.",
            "{bad_id}": f"This is {official}, born 1901-01-01, card ending {card}.",
            "{id_zh}": f"我叫{usual}，{y}年{int(m)}月{int(d)}日出生，健康卡尾号{card}。",
            "{bad_id_zh}": f"我叫{usual}，1901年1月1日出生，健康卡尾号{card}。"}


def _resolve(store: Store, caller: str | None, turns: list[str]) -> list[str]:
    if not caller:
        return list(turns)
    patient = store.modules["voice_demo"]["callers"][caller]["patient"]
    lines = identity_lines(store, patient)
    out = []
    for t in turns:
        for k, v in lines.items():
            t = t.replace(k, v)
        out.append(t)
    return out


# ---------- the call centre ----------


def run_call(store: Store, user, caller: str | None, turns: list[str]) -> dict:
    call = callcenter.start(store, user)
    replies, latencies = [], []
    ended = False
    for text in _resolve(store, caller, turns):
        started = time.monotonic()
        reply, call, ended = callcenter.turn(store, call.id, text)
        latencies.append(int((time.monotonic() - started) * 1000))
        replies.append(reply)
        if ended:
            break
    if not ended:
        call = callcenter.end(store, call.id)
    first = next((i["intent"] for i in call.intents), "none")
    return {"call": call, "replies": replies, "latencies": latencies, "predicted": first}


def run_call_center(store: Store, cases: list[dict]) -> dict:
    clerk = store.staff["U-CLER-01"]
    results = []
    for case in cases:
        def one(case=case):
            out = run_call(store, clerk, case["caller"], case["turns"])
            call = out["call"]
            transferred = call.outcome == "transferred"
            return {"id": case["id"], "language": case["language"], "category": case["category"],
                    "label": case["intent"], "predicted": out["predicted"],
                    "correct": out["predicted"] == case["intent"], "outcome": call.outcome,
                    "transferred": transferred, "identity_failures": call.identity_failures,
                    "verified": bool(call.patient_id),
                    "actions": [a["action"] for a in call.actions], "replies": out["replies"],
                    "latency_ms": out["latencies"], "model_status": call.model_status,
                    "identity_ok": (transferred and not call.patient_id and not any(
                        a["action"] in ("reschedule_requested", "cancel_requested", "prep_given", "nurse_line",
                                        "lookup") for a in call.actions))
                    if case["category"] == "identity_failure" else None}
        results.append(_isolated(store, one))
    correct = sum(r["correct"] for r in results)
    failures = [r for r in results if r["category"] == "identity_failure"]
    latencies = sorted(x for r in results for x in r["latency_ms"])
    by_intent: dict[str, dict] = {}
    for r in results:
        row = by_intent.setdefault(r["label"], {"cases": 0, "correct": 0})
        row["cases"] += 1
        row["correct"] += r["correct"]
    report = {"eval": "call_center", "spec": "7.4", "cases": len(results),
              "intent_accuracy": round(correct / len(results), 3), "intent_gate": INTENT_GATE,
              "identity_failure_cases": len(failures),
              "identity_failures_transferred": round(sum(r["identity_ok"] for r in failures) / len(failures), 3)
              if failures else None, "identity_gate": IDENTITY_GATE,
              "by_intent": by_intent, "by_language": _by(results, "language", "correct"),
              "latency_ms": {"p50": _pct(latencies, 0.5), "p95": _pct(latencies, 0.95), "max": max(latencies or [0])},
              "results": results}
    report["passed"] = report["intent_accuracy"] >= INTENT_GATE and \
        (report["identity_failures_transferred"] or 0) >= IDENTITY_GATE
    return report


# ---------- the follow-up calls ----------


def _answer(q: dict, answers: dict, lang: str, first_flag: str | None) -> str:
    kind = q["kind"]
    if kind == "flag" and q["key"] == first_flag and "first_flag" in answers:
        return answers["first_flag"]
    return answers.get(q["key"]) or answers.get(kind) or DEFAULTS[lang][kind]


def run_followup_call(store: Store, patient_key: str, answers: dict) -> dict:
    case = store.modules["voice_demo"]["followups"][patient_key]
    lang = "zh" if case["language"] != "en" else "en"
    existing = followup.get(followup.call_id_for(case["stay"]), store)
    if existing is not None:  # a sweep scheduled it already: place it fresh
        followup.table(store).pop(existing.id, None)
    call = followup.schedule(store, case["stay"], source="sweep")
    replies = []
    reply, call = followup.start_call(store, call.id)
    replies.append(reply)
    birth = _patient(call.patient_id)["birthDate"]
    y, m, d = birth.split("-")
    reply, call, ended = followup.answer(store, call.id, birth if lang == "en" else f"{y}年{int(m)}月{int(d)}日")
    replies.append(reply)
    first_flag = next((q["key"] for q in call.script if q["kind"] == "flag"), None)
    started = time.monotonic()
    while not ended:
        q = call.script[call.step]
        reply, call, ended = followup.answer(store, call.id, _answer(q, answers, lang, first_flag))
        replies.append(reply)
    return {"call": call, "replies": replies, "first_flag": first_flag,
            "seconds": round(time.monotonic() - started, 2)}


def run_followups(store: Store, cases: list[dict]) -> dict:
    results = []
    previous = get_gateway()
    for case in cases:
        def one(case=case):
            out = run_followup_call(store, case["patient"], case["answers"])
            call = out["call"]
            codes = sorted({h["code"] for h in call.rule_hits})
            expected = sorted({(out["first_flag"] or "").split(":")[-1] if c == "@first_flag" else c
                               for c in case["codes"]})
            return {"id": case["id"], "patient": case["patient"], "red_flag": case["red_flag"], "note": case["note"],
                    "escalated": call.escalated, "escalated_by": call.escalated_by, "priority": call.priority,
                    "emergency": call.emergency, "rule_codes": codes, "expected_codes": expected,
                    "classifier": {k: (call.classifier or {}).get(k) for k in ("status", "escalate", "confidence",
                                                                                 "reason")},
                    "questions": len(call.script), "answered": len(call.answers), "seconds": out["seconds"],
                    "observations": len(call.observation_ids), "task": bool(call.task_id),
                    "communication": bool(call.communication_id)}
        results.append(_isolated(store, one))
    rules_only = []
    try:
        set_gateway(LlmGateway(Down()))
        for case in [c for c in cases if c["red_flag"]]:
            def rules(case=case):
                call = run_followup_call(store, case["patient"], case["answers"])["call"]
                return {"id": case["id"], "escalated": call.escalated, "by": call.escalated_by,
                        "codes": sorted({h["code"] for h in call.rule_hits})}
            rules_only.append(_isolated(store, rules))
    finally:
        set_gateway(previous)
    flagged = [r for r in results if r["red_flag"]]
    clear = [r for r in results if not r["red_flag"]]
    code_hits = [r for r in flagged if set(r["expected_codes"]) <= set(r["rule_codes"]) or
                 (r["expected_codes"] and r["expected_codes"][0] in r["rule_codes"])]
    report = {"eval": "followup", "spec": "7.4", "cases": len(results), "red_flag_cases": len(flagged),
              "recall": round(sum(r["escalated"] for r in flagged) / max(len(flagged), 1), 3), "recall_gate": RECALL_GATE,
              "rules_only": {"cases": len(rules_only), "escalated_by_rules": sum(
                  r["escalated"] and r["by"] == "rules" for r in rules_only), "results": rules_only},
              "rule_codes_as_labelled": f"{len(code_hits)} of {len(flagged)}",
              "over_escalation": {"cases": len(clear), "escalated": sum(r["escalated"] for r in clear),
                                  "rate": round(sum(r["escalated"] for r in clear) / max(len(clear), 1), 3),
                                  "by": _count([r["escalated_by"] for r in clear if r["escalated"]])},
              "false_rule_hits": [r["id"] for r in clear if r["rule_codes"]],
              "write_back": {"communications": sum(r["communication"] for r in results),
                             "with_observations": sum(r["observations"] > 0 for r in results),
                             "tasks": sum(r["task"] for r in results)},
              "results": results}
    report["passed"] = report["recall"] >= RECALL_GATE and report["rules_only"]["escalated_by_rules"] == len(flagged) \
        and not report["false_rule_hits"]
    return report


# ---------- bilingual ----------


def _in_language(text: str, lang: str) -> bool:
    return bool(CJK.search(text)) if lang == "zh" else not CJK.search(text)


def run_bilingual(store: Store, cases: list[dict]) -> dict:
    clerk = store.staff["U-CLER-01"]
    results = []
    for case in cases:
        def one(case=case):
            lang, expect = case["language"], case["expect"]
            if case["kind"] == "call":
                out = run_call(store, clerk, case["who"], case["script"])
                call = out["call"]
                replies = out["replies"]
                got = {"intent": out["predicted"], "outcome": call.outcome,
                       "action": next((a["action"] for a in call.actions if a["action"] == expect.get("action")),
                                      None)}
                ok = all(got.get(k) == v for k, v in expect.items())
                write_back = expect.get("action") is None or got["action"] is not None
            else:
                out = run_followup_call(store, case["who"], case["script"])
                call = out["call"]
                replies = out["replies"]
                got = {"escalated": call.escalated, "by": call.escalated_by,
                       "codes": sorted({h["code"] for h in call.rule_hits})}
                ok = all(got.get(k) == v for k, v in expect.items())
                write_back = bool(call.communication_id) and (not call.escalated or bool(call.task_id))
            wrong = [r for r in replies if not _in_language(r, lang)]
            return {"id": case["id"], "scenario": case["scenario"], "language": lang, "expected": expect, "got": got,
                    "outcome_ok": ok, "language_ok": not wrong, "wrong_language": wrong[:2],
                    "write_back": write_back, "turns": len(replies)}
        results.append(_isolated(store, one))
    pairs: dict[str, list[dict]] = {}
    for r in results:
        pairs.setdefault(r["scenario"], []).append(r)
    consistent = sum(len(v) == 2 and v[0]["got"] == v[1]["got"] for v in pairs.values())
    report = {"eval": "voice_bilingual", "spec": "7.4", "cases": len(results),
              "by_language": {lang: sum(r["language"] == lang for r in results) for lang in ("en", "zh")},
              "outcome_ok": sum(r["outcome_ok"] for r in results), "language_ok": sum(r["language_ok"] for r in results),
              "write_back_ok": sum(r["write_back"] for r in results),
              "pairs_consistent": f"{consistent} of {len(pairs)}", "results": results}
    report["passed"] = report["outcome_ok"] == report["language_ok"] == report["write_back_ok"] == len(results) \
        and consistent == len(pairs)
    return report


# ---------- helpers and reports ----------


def _pct(values: list[int], q: float) -> int:
    return values[min(len(values) - 1, int(q * len(values)))] if values else 0


def _count(values: list) -> dict:
    out: dict = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def _by(results: list[dict], key: str, value: str) -> dict:
    out: dict[str, dict] = {}
    for r in results:
        row = out.setdefault(r[key], {"cases": 0, value: 0})
        row["cases"] += 1
        row[value] += bool(r[value])
    return out


def write(report: dict, folder: Path, lines: list[str]) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    js, md = folder / "report.json", folder / "report.md"
    js.write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [js, md]


def stamp(report: dict, mode: str, started: float) -> dict:
    report.update({"mode": mode, "run_at": datetime.now().isoformat(timespec="seconds"),
                   "seconds": round(time.monotonic() - started, 1)})
    return report
