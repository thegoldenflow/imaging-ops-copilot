"""Write the hospital's bed master data as a FHIR transaction bundle (spec 6.2 step 4).

    cd apps/api && uv run python scripts/gen_locations.py [--out locations.json]

ED 30 beds, Medicine A/B 32 each, Surgery 32, Orthopedics 24, ICU 12, plus 4 operating rooms;
three levels Unit -> Room -> Bed, every bed with an operationalStatus. The demo seed writes the
same tree into the FHIR store; this script is for loading it on its own (e.g. into HAPI).
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.store import Store, use_store  # noqa: E402
from app.ehr.bundles import transaction  # noqa: E402
from app.ehr.seed.builder import Builder  # noqa: E402
from app.ehr.seed.people import build_locations  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default="-", help="output file (default: stdout)")
    args = parser.parse_args()
    store = Store()
    with use_store(store):
        build_locations(Builder(store, 0, datetime.now()))
    resources = [r for (rtype, _), (r, _) in store.fhir._mem.items() if rtype in ("Organization", "Location")]
    text = json.dumps(transaction(resources), indent=1, ensure_ascii=False)
    if args.out == "-":
        print(text)
    else:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{len(resources)} resources written to {args.out}")


if __name__ == "__main__":
    main()
