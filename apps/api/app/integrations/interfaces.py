"""Real adapters for non-FHIR systems: interfaces only (spec 6.2).

Each would replace a mock in app/integrations/mocks.py without changing its
callers: same adapter name, same operations, same contract (`Adapter.call`:
correlation id, timeout and retry, circuit breaker, dead letters). Only `_send`
differs. None is implemented in the demo; each docstring lists the operations
and what the transport would be.
"""

from __future__ import annotations

from app.integrations.contract import Adapter


class _Interface(Adapter):
    def _send(self, operation: str, payload: dict, *, correlation_id: str, timeout_s: float) -> dict:
        raise NotImplementedError(f"{type(self).__name__} is an interface only; the demo uses the mock adapter")


class Hl7v2InterfaceEngine(_Interface):
    """HL7 v2 over MLLP to the hospital's interface engine (e.g. Rhapsody, Cloverleaf).

    Operations: `send` (ORM^O01 orders, ORU^R01 results) and inbound ADT^A01/A02/A03/A08,
    ORU^R01, SIU^S12 through `receive`, mapped to domain events by the event bus (WP3).
    The MSH-10 message control id is the correlation id; an AE/AR acknowledgement raises
    AdapterError / AdapterRejected."""


class PacsAdapter(_Interface):
    """DICOM to the PACS / vendor-neutral archive: `query` (C-FIND or QIDO-RS), `retrieve`
    (C-MOVE or WADO-RS), `store` (STOW-RS). Outside archives use the same interface (`fetch`)."""


class TelephonyAdapter(_Interface):
    """Voice calls through a telephony provider (e.g. Twilio Programmable Voice): `call`
    (place an outbound call with a spoken message), inbound calls through `receive`."""


class SmsGatewayAdapter(_Interface):
    """SMS and email through a messaging provider: `send`; delivery receipts and patient
    replies arrive through `receive` under the outbound message's id."""


class FaxAdapter(_Interface):
    """Outbound fax to referring offices through an e-fax service: `send`."""


class BillingAdapter(_Interface):
    """Claims to the provincial plan (OHIP MC EDT) and private insurers: `submit_claim`,
    `claim_status`, `remittance` (remittance advice files); eligibility checks: `validate`
    (health card) and `verify` (private policy)."""
