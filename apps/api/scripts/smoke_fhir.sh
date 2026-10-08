#!/usr/bin/env bash
# Smoke test for the hospital EHR on a FHIR server (spec 6.2 step 6).
#   scripts/smoke_fhir.sh [base url]      default http://localhost:8080/fhir
# Checks: about 1,000 patients; inpatient encounters exist; one patient has conditions,
# medication requests and observations; the Location tree has three levels.
set -euo pipefail
BASE="${1:-${FHIR_BASE_URL:-http://localhost:8080/fhir}}"
PY="${PYTHON:-python}"
fail=0

get() { curl -sf -H "Accept: application/fhir+json" "$BASE/$1"; }
total() { get "$1" | "$PY" -c "import json,sys; print(json.load(sys.stdin).get('total', 0))"; }
check() { if [ "$2" = "ok" ]; then echo "PASS  $1"; else echo "FAIL  $1 ($2)"; fail=1; fi; }

patients=$(total "Patient?_summary=count")
check "Patient count ≈ 1000 (got $patients)" "$([ "$patients" -ge 950 ] && [ "$patients" -le 1050 ] && echo ok || echo "$patients")"

inpatient=$(total "Encounter?class=IMP&_summary=count")
check "Encounter?class=IMP > 0 (got $inpatient)" "$([ "$inpatient" -gt 0 ] && echo ok || echo none)"

# A patient with an inpatient stay has conditions, medication requests and observations
pid=$(get "Encounter?class=IMP&_count=1" | "$PY" -c "import json,sys; print(json.load(sys.stdin)['entry'][0]['resource']['subject']['reference'].split('/')[1])")
for type in Condition MedicationRequest Observation; do
  n=$(total "$type?patient=$pid&_summary=count")
  check "$type for patient $pid (got $n)" "$([ "$n" -gt 0 ] && echo ok || echo none)"
done

# Location tree: bed -> room -> unit
# (R4 has no physical-type search parameter, so pick a bed from the first page of Locations.)
chain=$(get "Location?_count=500" | "$PY" -c "
import json, sys, urllib.request
base = sys.argv[1]
bed = next(e['resource'] for e in json.load(sys.stdin)['entry']
           if e['resource']['physicalType']['coding'][0]['code'] == 'bd')
def read(ref):
    req = urllib.request.Request(f'{base}/{ref}', headers={'Accept': 'application/fhir+json'})
    return json.load(urllib.request.urlopen(req))
room = read(bed['partOf']['reference'])
unit = read(room['partOf']['reference'])
codes = [r['physicalType']['coding'][0]['code'] for r in (bed, room, unit)]
print('ok' if codes == ['bd', 'ro', 'wa'] and 'partOf' not in unit else codes)
" "$BASE")
check "Location tree has three levels (bed -> room -> unit)" "$chain"

exit $fail
