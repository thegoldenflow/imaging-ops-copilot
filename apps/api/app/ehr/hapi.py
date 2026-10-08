"""The FHIR server backend of FhirGateway (HAPI FHIR here; a real EHR's FHIR API later).

This is the only place in the code base that talks HTTP to a FHIR server: the
gateway's `hapi` backend and the loader script (`scripts/load_fhir.py`) both use
`HapiClient`. A test greps for any other FHIR HTTP call.

Searches keep the local store's semantics. Parameters that map exactly (or to a
superset) onto a FHIR search parameter narrow the server query; every result is
then matched against the same index columns the PostgreSQL store searches on
(`fhirstore.index_columns`), so both backends return the same resources.

Authentication: `none` for the local HAPI. `smart_backend` (SMART Backend
Services: a signed JWT client assertion exchanged for an access token with the
client-credentials grant) is the mode a real EHR needs; it is defined here but
not implemented in the demo.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx

from app.ehr.codes import HCN_SYSTEM, MRN_SYSTEM
from app.ehr.fhirstore import blind_params, index_columns, matches, order_rows
from app.fhir.dt import fhir_datetime, parse

FHIR_JSON = "application/fhir+json"
PAGE_SIZE = 200
MAX_RESULTS = 20_000  # a guard against unscoped searches pulling the whole server

# Resource types that support each FHIR search parameter (R4), for the parameters translated.
_PATIENT = {"Encounter", "Observation", "Condition", "MedicationRequest", "MedicationStatement", "AllergyIntolerance",
            "Procedure", "DiagnosticReport", "ServiceRequest", "Appointment", "Task", "Flag", "Communication",
            "DocumentReference", "CarePlan", "Consent", "EpisodeOfCare"}
_ENCOUNTER = {"Observation", "Condition", "MedicationRequest", "Procedure", "DiagnosticReport", "ServiceRequest",
              "Task", "Flag", "Communication", "DocumentReference", "CarePlan"}
_STATUS = {"Encounter", "Observation", "MedicationRequest", "MedicationStatement", "ServiceRequest", "Procedure",
           "DiagnosticReport", "Appointment", "Task", "Flag", "Communication", "CarePlan", "Consent", "EpisodeOfCare",
           "Slot"}
_CLINICAL_STATUS = {"Condition", "AllergyIntolerance"}
_CODE = {"Observation", "Condition", "ServiceRequest", "Procedure", "DiagnosticReport", "MedicationRequest",
         "MedicationStatement", "Task"}
_DATE = {"Encounter", "Observation"}  # date=ge/lt matches a superset of "start within the range"


class FhirUnavailable(RuntimeError):
    """The FHIR server could not be reached or returned a server error."""


class FhirServerError(RuntimeError):
    """The FHIR server refused a request (4xx other than not found)."""


# ---------- authentication ----------


class NoAuth:
    def headers(self) -> dict[str, str]:
        return {}


class SmartBackendAuth:
    """SMART Backend Services (client credentials with a signed JWT assertion).

    A real deployment would: build a JWT (iss = sub = client id, aud = token URL, jti,
    exp <= 5 minutes) signed with the client's registered private key (RS384 or ES384),
    POST it to the token URL as `client_assertion` with
    `grant_type=client_credentials` and `client_assertion_type=
    urn:ietf:params:oauth:client-assertion-type:jwt-bearer`, cache the access token
    until shortly before it expires and send it as a Bearer token."""

    def __init__(self, client_id: str | None, token_url: str | None) -> None:
        self.client_id, self.token_url = client_id, token_url

    def headers(self) -> dict[str, str]:
        raise NotImplementedError(
            "FHIR_AUTH_MODE=smart_backend is defined but not implemented in the demo; "
            "use FHIR_AUTH_MODE=none with the local HAPI server")


def auth_from_settings():
    from app.core.config import settings

    if settings.fhir_auth_mode == "smart_backend":
        return SmartBackendAuth(settings.fhir_client_id, settings.fhir_token_url)
    return NoAuth()


# ---------- HTTP client ----------


class HapiClient:
    """Plain FHIR REST calls: read, search (all pages), create, update, transaction."""

    def __init__(self, base_url: str, auth=None, *, timeout_s: float = 30.0, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth or NoAuth()
        self._client = client or httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, url: str, *, params=None, json: dict | None = None,
                 headers: dict | None = None) -> httpx.Response:
        all_headers = {"Accept": FHIR_JSON, **self.auth.headers(), **(headers or {})}
        if json is not None:
            all_headers["Content-Type"] = FHIR_JSON
        try:
            res = self._client.request(method, url, params=params, json=json, headers=all_headers)
        except httpx.HTTPError as e:
            raise FhirUnavailable(f"FHIR server not reachable: {type(e).__name__}: {e}") from e
        if res.status_code >= 500:
            raise FhirUnavailable(f"FHIR server error {res.status_code}: {res.text[:300]}")
        return res

    @staticmethod
    def _check(res: httpx.Response) -> dict:
        if res.status_code >= 400:
            raise FhirServerError(f"HTTP {res.status_code}: {res.text[:500]}")
        return res.json() if res.content else {}

    def read(self, resource_type: str, resource_id: str) -> dict | None:
        res = self._request("GET", f"{self.base_url}/{resource_type}/{resource_id}")
        if res.status_code in (404, 410):
            return None
        return self._check(res)

    def search(self, resource_type: str, params: list[tuple[str, str]]) -> Iterator[dict]:
        url: str | None = f"{self.base_url}/{resource_type}"
        query: list[tuple[str, str]] | None = [*params, ("_count", str(PAGE_SIZE))]
        seen = 0
        while url:
            # HAPI reuses the result of an identical search for 60 s by default, so without this a
            # bed board would miss a patient admitted a moment ago.
            bundle = self._check(self._request("GET", url, params=query, headers={"Cache-Control": "no-cache"}))
            for entry in bundle.get("entry") or []:
                if (entry.get("search") or {}).get("mode", "match") == "match":
                    seen += 1
                    if seen > MAX_RESULTS:
                        raise FhirServerError(f"{resource_type} search returned more than {MAX_RESULTS} resources")
                    yield entry["resource"]
            url = next((link["url"] for link in bundle.get("link") or [] if link.get("relation") == "next"), None)
            query = None  # the next link carries the query

    def create(self, resource: dict) -> dict:
        """POST (server-assigned id) or, when the resource has an id, PUT after checking it is new."""
        body = {k: v for k, v in resource.items() if k != "meta"}
        rtype = resource["resourceType"]
        if resource.get("id"):
            if self.read(rtype, resource["id"]) is not None:
                raise FhirServerError(f"{rtype}/{resource['id']} already exists")
            res = self._request("PUT", f"{self.base_url}/{rtype}/{resource['id']}", json=body,
                                headers={"Prefer": "return=representation"})
        else:
            res = self._request("POST", f"{self.base_url}/{rtype}", json=body,
                                headers={"Prefer": "return=representation"})
        return self._check(res)

    def update(self, resource: dict) -> dict:
        body = {k: v for k, v in resource.items() if k != "meta"}
        res = self._request("PUT", f"{self.base_url}/{resource['resourceType']}/{resource['id']}", json=body,
                            headers={"Prefer": "return=representation"})
        return self._check(res)

    def delete(self, resource_type: str, resource_id: str) -> None:
        res = self._request("DELETE", f"{self.base_url}/{resource_type}/{resource_id}")
        if res.status_code not in (404, 410):
            self._check(res)

    def transaction(self, bundle: dict) -> dict:
        return self._check(self._request("POST", self.base_url, json=bundle))

    def post_transaction(self, bundle: dict, *, attempts: int = 2, pause_s: float = 1.0) -> str | None:
        """A transaction bundle with retries; None on success, otherwise the last error text."""
        error = None
        for attempt in range(attempts):
            try:
                self.transaction(bundle)
                return None
            except (FhirUnavailable, FhirServerError) as e:
                error = str(e)
            if attempt + 1 < attempts:
                time.sleep(pause_s)
        return error


# ---------- gateway backend ----------


def _join(value: Any) -> str:
    return ",".join(value) if isinstance(value, (list, tuple, set)) else str(value)


def fhir_query(resource_type: str, params: dict[str, Any], ids=None) -> list[tuple[str, str]]:
    """FHIR search parameters that select a superset of what the local store would return."""
    q: list[tuple[str, str]] = []
    if ids is not None:
        q.append(("_id", _join(ids)))
    for key, value in params.items():
        if value is None:
            continue
        if key == "patient":
            if resource_type == "Patient":
                q.append(("_id", _join(value)))
            elif resource_type in _PATIENT:
                q.append(("patient", _join(value)))
        elif key == "encounter":
            if resource_type == "MedicationStatement":
                q.append(("context", _join(value)))
            elif resource_type in _ENCOUNTER:
                q.append(("encounter", _join(value)))
        elif key in ("mrn", "hcn") and resource_type == "Patient":
            q.append(("identifier", f"{MRN_SYSTEM if key == 'mrn' else HCN_SYSTEM}|{value}"))
        elif key == "status":
            if resource_type in _STATUS:
                q.append(("status", _join(value)))
            elif resource_type in _CLINICAL_STATUS:
                q.append(("clinical-status", _join(value)))
        elif key == "cls" and resource_type == "Encounter":
            q.append(("class", _join(value)))
        elif key == "code" and resource_type in _CODE:
            q.append(("code", _join(value)))
        elif key == "location":
            if resource_type == "Location":
                q.append(("partof", _join([f"Location/{v}" for v in _as_list(value)])))
            elif resource_type == "Encounter":  # any location in the history; the current one is checked below
                q.append(("location", _join([f"Location/{v}" for v in _as_list(value)])))
        elif key in ("date_from", "date_to") and resource_type in _DATE:
            q.append(("date", ("ge" if key == "date_from" else "lt") + fhir_datetime(value)))
    return q


def _as_list(value) -> list:
    return list(value) if isinstance(value, (list, tuple, set)) else [value]


class HapiBackend:
    """FhirGateway backend on a FHIR server, with the local store's search semantics."""

    name = "hapi"

    def __init__(self, client: HapiClient | None = None) -> None:
        if client is None:
            from app.core.config import settings

            client = HapiClient(settings.fhir_base_url, auth_from_settings())
        self.client = client

    def read(self, resource_type: str, resource_id: str) -> dict | None:
        return self.client.read(resource_type, resource_id)

    def search(self, resource_type: str, *, ids=None, order: str | None = None, limit: int | None = None,
               **params: Any) -> list[dict]:
        rows = []
        wanted = set(ids) if ids is not None else None
        blind = blind_params(params)  # mrn / hcn -> blind index values, as stored
        for resource in self.client.search(resource_type, fhir_query(resource_type, params, ids)):
            if wanted is not None and resource["id"] not in wanted:
                continue
            cols = index_columns(resource)
            if all(matches(cols, k, v) for k, v in blind.items() if v is not None):
                rows.append((resource, cols))
        if order not in ("date", "-date"):  # the local store keeps insertion order
            rows.sort(key=lambda rc: parse((rc[0].get("meta") or {}).get("lastUpdated")) or datetime.min)
        out = [r for r, _ in order_rows(rows, order)]
        return out[:limit] if limit else out

    def create(self, resource: dict) -> dict:
        return self.client.create(resource)

    def update(self, resource: dict) -> dict:
        return self.client.update(resource)
