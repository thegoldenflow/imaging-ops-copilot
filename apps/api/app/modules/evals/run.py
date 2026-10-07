"""Runs the phase 2 evaluation sets and writes evals/results/<task>.json.

    uv run python -m app.modules.evals.run            # all tasks
    uv run python -m app.modules.evals.run triage     # one task

Uses Claude when ANTHROPIC_API_KEY is set, otherwise the rule-based baselines
behind the mock provider. Every call goes through the same LLM gateway as the
app."""

import json
import re
import sys
import time
from datetime import datetime

from app.core.config import settings
from app.core.models import Patient
from app.llm.gateway import LlmGateway
from app.llm.providers import MockProvider
from app.modules.evals.paths import DATASETS, RESULTS
from app.modules.mri_safety.service import IMPLANT_PROMPT, DeviceExtraction
from app.modules.protocols import service as protocols
from app.modules.protocols.library import BY_ID, detect_modality, retrieve
from app.modules.requisitions.extraction import EXTRACT_PROMPT, Extraction
from app.modules.requisitions.service import values_only, clinical_text
from app.modules.triage import service as triage

CONTRAST = re.compile(r"contrast|iodin|gadolin|\bdye\b", re.I)


def _gateway() -> LlmGateway:
    return LlmGateway(MockProvider(latency_s=0)) if settings.llm_mode == "mock" else LlmGateway()


def _load(name: str) -> list[dict]:
    return json.loads((DATASETS / f"{name}.json").read_text())


def _write(task: str, gateway: LlmGateway, metrics: dict, n: int, errors: list, started: float, notes: str) -> dict:
    RESULTS.mkdir(parents=True, exist_ok=True)
    result = {
        "task": task, "run_at": datetime.now().isoformat(timespec="seconds"), "mode": gateway.mode,
        "model": "rule-based baseline" if gateway.mode == "mock" else "claude (see llm_calls)",
        "n": n, "metrics": metrics, "errors": errors[:12], "seconds": round(time.monotonic() - started, 1), "notes": notes,
    }
    (RESULTS / f"{task}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    print(f"{task}: {json.dumps(metrics)}")
    return result


def _extract(gateway: LlmGateway, text: str) -> dict | None:
    outcome = gateway.structured(task="requisition_extract", prompt=EXTRACT_PROMPT, variables={"text": text},
                                 schema_cls=Extraction, tier="fast")
    return outcome.data if outcome.status == "ok" else None


def eval_extraction(gateway: LlmGateway) -> dict:
    started, items = time.monotonic(), _load("requisitions")
    checks = {"requested_modality": 0, "renal_or_diabetes": 0, "contrast_allergy": 0, "implant_hints": 0,
              "prior_imaging": 0, "stated_urgency": 0}
    errors, failed = [], 0
    for item in items:
        labels, fields = item["labels"], _extract(gateway, item["text"])
        if fields is None:
            failed += 1
            continue
        got = {
            "requested_modality": str(detect_modality(fields["requested_exam"]["value"])) == labels["modality"],
            "renal_or_diabetes": bool(fields["renal_or_diabetes"]) == labels["renal_risk"],
            "contrast_allergy": any(CONTRAST.search(a["value"]) for a in fields["allergies"]) == bool(labels["contrast_allergy"]),
            "implant_hints": bool(fields["implant_hints"]) == bool(labels["implant"]),
            "prior_imaging": bool(fields["prior_imaging"]) == bool(labels["prior_facility"]),
            "stated_urgency": fields["stated_urgency"]["value"] == labels["stated_urgency"],
        }
        for key, ok in got.items():
            checks[key] += ok
            if not ok:
                errors.append({"id": item["id"], "field": key, "style": labels["style"]})
    n = len(items)
    metrics = {f"{k}_accuracy": round(v / n, 3) for k, v in checks.items()}
    metrics["field_accuracy"] = round(sum(checks.values()) / (n * len(checks)), 3)
    metrics["failed_calls"] = failed
    return _write("extraction", gateway, metrics, n, errors, started,
                  "Field-level accuracy against generator labels. 30% of requisitions are free-text letters.")


def eval_triage(gateway: LlmGateway) -> dict:
    started, items = time.monotonic(), _load("requisitions")
    exact = within_one = over = under = failed = 0
    errors = []
    for item in items:
        fields = _extract(gateway, item["text"])
        if fields is None:
            failed += 1
            continue
        outcome = gateway.structured(task="requisition_triage", prompt=triage.TRIAGE_PROMPT,
                                     variables={"fields": json.dumps(values_only(fields))},
                                     schema_cls=triage.TriageOutput, tier="reasoning")
        if outcome.status != "ok":
            failed += 1
            continue
        got, want = outcome.data["priority"], item["labels"]["priority"]
        exact += got == want
        within_one += abs(int(got[1]) - int(want[1])) <= 1
        over += got < want
        under += got > want
        if got != want:
            errors.append({"id": item["id"], "expected": want, "got": got, "rationale": outcome.data["rationale"]})
    n = len(items)
    return _write("triage", gateway, {
        "agreement": round(exact / n, 3), "within_one_tier": round(within_one / n, 3),
        "over_triage_rate": round(over / n, 3), "under_triage_rate": round(under / n, 3), "failed_calls": failed,
    }, n, errors, started, "Agreement with the labelled priority (P1-P4). Under-triage is the safety-relevant error.")


def eval_protocol(gateway: LlmGateway) -> dict:
    started, items = time.monotonic(), _load("protocols")
    top1 = top3 = failed = 0
    errors = []
    for item in items:
        candidates = retrieve(item["requested_exam"], item["clinical"])
        outcome = gateway.structured(
            task="protocol_suggest", prompt=protocols.PROTOCOL_PROMPT,
            variables={"exam": item["requested_exam"], "clinical": item["clinical"],
                       "candidates": protocols.format_candidates(candidates)},
            schema_cls=protocols.ProtocolOutput, tier="reasoning")
        if outcome.status != "ok":
            failed += 1
            continue
        ranked = [outcome.data["primary_protocol_id"], *outcome.data["alternative_protocol_ids"]][:3]
        top1 += ranked[0] == item["protocol_id"]
        top3 += item["protocol_id"] in ranked
        if ranked[0] != item["protocol_id"]:
            errors.append({"id": item["id"], "expected": item["protocol_id"], "got": ranked,
                           "clinical": item["clinical"]})
    n = len(items)
    return _write("protocol", gateway, {"top1": round(top1 / n, 3), "top3": round(top3 / n, 3), "failed_calls": failed},
                  n, errors, started, "Top-1 and top-3 hit rate over retrieval candidates from the synthetic protocol library.")


def eval_implants(gateway: LlmGateway) -> dict:
    started, items = time.monotonic(), _load("implants")
    correct = failed = 0
    errors = []
    by_language: dict[str, list[int]] = {}
    for item in items:
        outcome = gateway.structured(task="mri_implant_extract", prompt=IMPLANT_PROMPT, variables={"text": item["text"]},
                                     schema_cls=DeviceExtraction, tier="fast")
        if outcome.status != "ok":
            failed += 1
            continue
        got = sorted({d["category"] for d in outcome.data["devices"]})
        ok = got == sorted(item["categories"])
        correct += ok
        by_language.setdefault(item["language"], [0, 0])
        by_language[item["language"]][0] += ok
        by_language[item["language"]][1] += 1
        if not ok:
            errors.append({"id": item["id"], "language": item["language"], "text": item["text"],
                           "expected": item["categories"], "got": got})
    n = len(items)
    metrics = {"device_accuracy": round(correct / n, 3), "failed_calls": failed}
    metrics |= {f"accuracy_{lang}": round(a / b, 3) for lang, (a, b) in sorted(by_language.items())}
    return _write("implants", gateway, metrics, n, errors, started,
                  "Exact match of device categories per answer, across English, French, Chinese and Punjabi.")


def eval_feedback(gateway: LlmGateway) -> dict:
    from app.modules.feedback.service import CLASSIFY_PROMPT, Classification

    started, items = time.monotonic(), _load("feedback")
    sentiment_ok = themes_exact = tp = fp = fn = failed = 0
    errors = []
    by_language: dict[str, list[int]] = {}
    for item in items:
        outcome = gateway.structured(task="feedback_classify", prompt=CLASSIFY_PROMPT,
                                     variables={"rating": item["rating"], "comment": item["comment"]},
                                     schema_cls=Classification, tier="fast")
        if outcome.status != "ok":
            failed += 1
            continue
        got, want = set(outcome.data["themes"]), set(item["themes"])
        s_ok = outcome.data["sentiment"] == item["sentiment"]
        sentiment_ok += s_ok
        themes_exact += got == want
        tp, fp, fn = tp + len(got & want), fp + len(got - want), fn + len(want - got)
        lang = by_language.setdefault(item["language"], [0, 0])
        lang[0] += s_ok and got == want
        lang[1] += 1
        if not s_ok or got != want:
            errors.append({"id": item["id"], "language": item["language"], "comment": item["comment"],
                           "expected": {"sentiment": item["sentiment"], "themes": sorted(want)},
                           "got": {"sentiment": outcome.data["sentiment"], "themes": sorted(got)}})
    n = len(items)
    precision = tp / (tp + fp) if tp + fp else 0
    recall = tp / (tp + fn) if tp + fn else 0
    metrics = {"sentiment_accuracy": round(sentiment_ok / n, 3), "theme_exact_match": round(themes_exact / n, 3),
               "theme_f1": round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0,
               "failed_calls": failed}
    metrics |= {f"both_correct_{lang}": round(a / b, 3) for lang, (a, b) in sorted(by_language.items())}
    return _write("feedback", gateway, metrics, n, errors, started,
                  "60 comments, 15 per language (EN, FR, ZH, PA), with star rating. Sentiment accuracy, exact theme-set "
                  "match and micro-F1 over themes.")


TASKS = {"extraction": eval_extraction, "triage": eval_triage, "protocol": eval_protocol, "implants": eval_implants,
         "feedback": eval_feedback}


def main(names: list[str]) -> None:
    gateway = _gateway()
    for name in names or list(TASKS):
        TASKS[name](gateway)


if __name__ == "__main__":
    main(sys.argv[1:])
