"""Localize Synthea FHIR output for the demo hospital (spec 6.2 step 3); see app/ehr/synthea.py.

    cd apps/api && uv run python scripts/localize_synthea.py IN_DIR OUT_DIR [--seed 42]

IN_DIR holds Synthea's transaction bundles (output/fhir/*.json, generated with
exporter.fhir.export=true and exporter.fhir.transaction_bundle=true). Bundles are written
to OUT_DIR under the same names, ready for a FHIR server. A small Synthea-format sample for
trying it out is in scripts/samples/synthea/.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ehr.seed.people import bed_ids  # noqa: E402
from app.ehr.reference import INPATIENT_UNITS  # noqa: E402
from app.ehr.synthea import localize_bundle  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("in_dir", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    beds = [bed for unit in INPATIENT_UNITS for bed in bed_ids(unit)]
    files = sorted(args.in_dir.glob("*.json"))
    for path in files:
        bundle = json.loads(path.read_text(encoding="utf-8"))
        (args.out_dir / path.name).write_text(json.dumps(localize_bundle(bundle, beds, args.seed), ensure_ascii=False),
                                              encoding="utf-8")
    print(f"{len(files)} bundle(s) localized into {args.out_dir}")


if __name__ == "__main__":
    main()
