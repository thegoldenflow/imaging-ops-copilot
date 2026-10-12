"""Training data for the Control Tower's flow models, from FHIR (spec 7.1).

    cd apps/api && uv run python scripts/build_flow_dataset.py [--out models/flow/data]

Reads the hospital EHR in DATABASE_URL through FhirGateway as the Control Tower
(registry entry `control_tower`, one audited search per resource type), as of the
hospital clock, and writes one CSV per model: admission.csv (ED visits at triage),
discharge.csv (inpatients at 07:00 and 19:00), or_duration.csv (finished OR cases),
ed_wait.csv (the ED every 30 minutes). Features, label, time and an id only: no
names, MRNs or free text. Read-only (the transaction is rolled back).
Train with scripts/train_flow_models.py.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.core.config  # noqa: E402,F401  (loads apps/api/.env)
from app.core.store import unit_of_work  # noqa: E402
from app.ehr.clock import hospital_now  # noqa: E402
from app.modules.control_tower import flowdata  # noqa: E402
from app.modules.control_tower.flowmodels import MODELS_DIR  # noqa: E402
from app.modules.control_tower.snapshot import tower_fhir  # noqa: E402

DATA_DIR = MODELS_DIR / "data"


def build() -> dict[str, flowdata.Table]:
    with unit_of_work() as store:
        try:
            return flowdata.build(tower_fhir(), hospital_now(store))
        finally:
            store.rollback()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    started = time.monotonic()
    tables = build()
    for table in tables.values():
        path = table.write(args.out)
        times = [r["time"] for r in table.rows]
        span = f"{min(times):%Y-%m-%d} .. {max(times):%Y-%m-%d}" if times else "-"
        print(f"{table.name:12s} {len(table.rows):6d} rows  {span}  -> {path}")
    print(f"built in {time.monotonic() - started:.1f} s")


if __name__ == "__main__":
    main()
