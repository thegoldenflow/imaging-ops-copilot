"""Load the synthetic hospital into an external FHIR server (HAPI), spec 6.2 step 5.

    docker compose --profile ehr up -d hapi
    cd apps/api && uv run python scripts/load_fhir.py [--base FHIR_BASE_URL] [--limit N]

Reads the resources from the app's FHIR store (run the API or `python -m app.seed` first), then
POSTs transaction bundles: organization and practitioners, the Location tree, one bundle per
patient. A failed bundle is retried once; bundles that still fail are written to load_errors.log.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.core.store import unit_of_work  # noqa: E402
from app.ehr.bundles import patient_bundles, shared_bundles  # noqa: E402
from app.ehr.hapi import HapiClient, auth_from_settings  # noqa: E402

ERRORS = Path(__file__).resolve().parents[1] / "load_errors.log"


def post(client: HapiClient, name: str, bundle: dict) -> str | None:
    """Returns None on success, otherwise the error text (after one retry)."""
    error = client.post_transaction(bundle, attempts=2)
    return f"{name}: {error}" if error else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base", default=settings.fhir_base_url)
    parser.add_argument("--limit", type=int, default=None, help="load only the first N patients")
    args = parser.parse_args()
    failures: list[str] = []
    started = time.monotonic()
    client = HapiClient(args.base, auth_from_settings(), timeout_s=120)
    with unit_of_work() as store:
        for name, bundle in shared_bundles(store):
            if err := post(client, name, bundle):
                failures.append(err)
            print(f"{name}: {len(bundle['entry'])} resources")
        ids = store.fhir.ids("Patient")[: args.limit]
        for i, (pid, bundle) in enumerate(patient_bundles(store, ids), start=1):
            if err := post(client, pid, bundle):
                failures.append(err)
            store.fhir.expunge()
            if i % 50 == 0:
                print(f"{i}/{len(ids)} patients, {time.monotonic() - started:.0f} s")
    client.close()
    ERRORS.write_text("\n".join(failures) + ("\n" if failures else ""), encoding="utf-8")
    print(f"done in {time.monotonic() - started:.0f} s; {len(failures)} failed bundle(s), see {ERRORS.name}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
