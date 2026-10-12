"""Train the Control Tower's four flow models and write their report (spec 7.1).

    cd apps/api && uv run python scripts/train_flow_models.py [--rebuild] [--check]

Reads the CSVs that scripts/build_flow_dataset.py wrote to models/flow/data/
(`--rebuild`, or no CSVs yet: builds them from FHIR first), trains each model with
a time split (the last 20% of the dates held out), compares it with the spec's
baseline and writes models/flow/<model>.joblib, report.json and report.md.
`--check` exits with 1 when a model misses its gate. The metrics come from
synthetic data: they show the pipeline works, nothing more (models/flow/README.md).
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.core.config  # noqa: E402,F401  (loads apps/api/.env)
from app.modules.control_tower import features as F  # noqa: E402
from app.modules.control_tower import flowdata, flowmodels  # noqa: E402
from app.modules.control_tower.flowmodels import MODELS_DIR, SPECS  # noqa: E402

DATA_DIR = MODELS_DIR / "data"


def load_tables(rebuild: bool) -> dict[str, flowdata.Table]:
    paths = {name: DATA_DIR / f"{name}.csv" for name in flowmodels.NAMES}
    if rebuild or not all(p.exists() for p in paths.values()):
        from build_flow_dataset import build  # noqa: E402  (same folder)

        tables = build()
        for table in tables.values():
            table.write(DATA_DIR)
        return tables
    return {name: flowdata.read_table(path) for name, path in paths.items()}


def example(bundle: dict, table: flowdata.Table) -> str:
    rows = [r for r in table.rows if r["time"] == max(x["time"] for x in table.rows)][:1] or table.rows[-1:]
    models = flowmodels.FlowModels(MODELS_DIR)
    pred = models.predict(table.name, [{c: r[c] for c in table.columns} for r in rows])
    if not pred:
        return ""
    booked = rows[0].get("booked_minutes") if table.name == "or_duration" else None
    return pred[0].sentence(table.name, booked=booked)


def markdown(report: dict) -> str:
    lines = ["# Flow models: training report", "",
             f"Trained {report['trained_at']} on data as of {report['data_as_of']} (scikit-learn "
             f"{report['sklearn']}, HistGradientBoosting). **Synthetic data**: these numbers show that the pipeline "
             "works (data from FHIR, time split, baseline, gate); they say nothing about clinical or operational "
             "performance on a real hospital.", "",
             "| Model | Metric | Model | Baseline | Gate | Passed | Train / test rows | Test dates |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for name, m in report["models"].items():
        metric = m["metric"].upper()
        gate = SPECS[name].gate
        lines.append(f"| {SPECS[name].title} | {metric} | {m['model']} | {m['baseline']} | {gate} | "
                     f"{'yes' if m['passed'] else '**no**'} | {m['n_train']} / {m['n_test']} | "
                     f"{m['test_dates'][0]} .. {m['test_dates'][1]} |")
    lines += ["", "## Per model", ""]
    for name, m in report["models"].items():
        lines += [f"### {SPECS[name].title} (`{name}`)", "",
                  f"- Baseline: {SPECS[name].baseline}",
                  f"- Features: {', '.join(f.name for f in F.FEATURES[name])}",
                  "- Largest mean contributions on the test dates: "
                  + ", ".join(f"{k} {v}" for k, v in list(m["importance"].items())[:5]),
                  f"- Example explanation: \"{m['example']}\"", ""]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rebuild", action="store_true", help="rebuild the training data from FHIR first")
    parser.add_argument("--check", action="store_true", help="exit 1 when a model misses its gate")
    args = parser.parse_args()
    started = time.monotonic()
    tables = load_tables(args.rebuild)
    report = {"trained_at": datetime.now().isoformat(timespec="seconds"), "sklearn": flowmodels.sklearn.__version__,
              "data_as_of": max(r["time"] for t in tables.values() for r in t.rows).isoformat(timespec="minutes"),
              "test_share": flowmodels.TEST_SHARE, "models": {}}
    for name in flowmodels.NAMES:
        bundle, metrics = flowmodels.train(tables[name])
        flowmodels.save(bundle)
        metrics["importance"] = bundle["importance"]
        metrics["example"] = example(bundle, tables[name])
        report["models"][name] = metrics
        verdict = "passed" if metrics["passed"] else "FAILED"
        print(f"{name:12s} {metrics['metric']} model {metrics['model']} baseline {metrics['baseline']}  {verdict}")
    report["passed"] = all(m["passed"] for m in report["models"].values())
    (MODELS_DIR / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (MODELS_DIR / "report.md").write_text(markdown(report), encoding="utf-8")
    print(f"report: {MODELS_DIR / 'report.md'} ({time.monotonic() - started:.1f} s)")
    if args.check and not report["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
