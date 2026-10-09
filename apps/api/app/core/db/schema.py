"""Database schema: every persisted collection and the table it maps to.

Tables are derived from the Pydantic models the modules already use, one column
per field: str, int, float, bool, date and datetime fields become typed columns,
lists, dicts and nested models become JSONB. Each table also has `row_key` (the
dictionary key the code uses, usually the record id) and `seq` (insertion order,
so iteration order matches what the in-memory version had).

Module state comes in four kinds, as agreed in docs/RATIONALE-db-temporal.md:
- tables: records with their own lifecycle (reports, critical cases, offers, ...);
- configs: one settings model per module, stored as JSON in `module_state`;
- blobs: small untyped state and lookup maps, stored as JSON in `module_state`;
- caches: derived, per-process values that are rebuilt on demand and never stored.

Model classes are imported lazily: the modules import the store, so resolving
them at import time would be circular.
"""

import importlib
import types
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from functools import cache
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Identity,
    Index,
    LargeBinary,
    MetaData,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

# PHI columns: patient identifiers (SPEC, phase 0) and the free-text fields that
# repeat them (requisition letters, outgoing messages, call transcripts).
PATIENT_PHI = ("given_name", "family_name", "dob", "phone", "email", "address", "health_card")


@dataclass(frozen=True)
class EntitySpec:
    name: str  # store attribute or module state name; also the table name
    model_ref: str  # "package.module:ClassName"
    kind: str = "map"  # map: dict keyed by row_key; list: append-only, ordered by seq
    encrypted: tuple[str, ...] = ()
    blind: tuple[tuple[str, str], ...] = ()  # (index column, source field)
    indexes: tuple[tuple[str, ...], ...] = ()


CORE = "app.core.models"
M = "app.modules"

STORE_ENTITIES = {
    s.name: s
    for s in [
        EntitySpec("sites", f"{CORE}:Site"),
        EntitySpec("scanners", f"{CORE}:Scanner"),
        EntitySpec("exams", f"{CORE}:Exam"),
        EntitySpec("patients", f"{CORE}:Patient", encrypted=PATIENT_PHI,
                   blind=(("dob_bidx", "dob"), ("health_card_bidx", "health_card"))),
        EntitySpec("referrers", f"{CORE}:Referrer"),
        EntitySpec("appointments", f"{CORE}:Appointment", indexes=(("patient_id",), ("start",))),
        EntitySpec("waitlist", f"{CORE}:WaitlistEntry"),
        EntitySpec("studies", f"{CORE}:ImagingStudy"),
        EntitySpec("requisitions", f"{CORE}:Requisition", encrypted=("text",)),
        EntitySpec("labs", f"{CORE}:LabResult"),
        EntitySpec("allergies", f"{CORE}:Allergy"),
        EntitySpec("staff", f"{CORE}:StaffUser"),
        EntitySpec("outbox", f"{CORE}:MessageOutbox", encrypted=("to", "body"), indexes=(("status", "scheduled_for"),)),
        EntitySpec("llm_calls", f"{CORE}:LlmCall", kind="list"),
    ]
}

MODULE_ENTITIES = {
    s.name: s
    for s in [
        EntitySpec("reading_assignments", f"{M}.backlog.service:Assignment"),
        EntitySpec("claims", f"{M}.billing.service:Claim"),
        EntitySpec("billing_work", f"{M}.billing.service:WorkItem"),
        EntitySpec("critical_cases", f"{M}.critical.service:CriticalCase", indexes=(("status", "next_action_at"),)),
        EntitySpec("dose_references", f"{M}.dose.service:ReferenceLevel"),
        EntitySpec("dose_records", f"{M}.dose.service:DoseRecord"),
        EntitySpec("feedback_surveys", f"{M}.feedback.service:Survey"),
        EntitySpec("feedback_responses", f"{M}.feedback.service:FeedbackResponse"),
        EntitySpec("feedback_alerts", f"{M}.feedback.service:Alert"),
        EntitySpec("calls", f"{M}.frontdesk.tools:CallSession", encrypted=("transcript", "state", "summary")),
        EntitySpec("prereg", f"{M}.frontdesk.service:PreRegistration", encrypted=("phone", "email", "address")),
        EntitySpec("inspection_documents", f"{M}.inspection.service:Document"),
        EntitySpec("inventory_items", f"{M}.inventory.service:InventoryItem"),
        EntitySpec("inventory_movements", f"{M}.inventory.service:Movement", kind="list"),
        EntitySpec("inventory_orders", f"{M}.inventory.service:PurchaseOrder"),
        EntitySpec("mri_screenings", f"{M}.mri_safety.service:Screening"),
        EntitySpec("peer_reviews", f"{M}.peer_review.service:PeerReview"),
        EntitySpec("qa_runs", f"{M}.peer_review.service:SamplingRun", kind="list"),
        EntitySpec("phipa_investigations", f"{M}.phipa.service:Investigation"),
        EntitySpec("prep_templates", f"{M}.prep.service:PrepTemplate"),
        EntitySpec("retrieval_tasks", f"{M}.priors.service:RetrievalTask", indexes=(("status", "next_attempt_at"),)),
        EntitySpec("protocols", f"{M}.protocols.service:ProtocolRecord"),
        EntitySpec("referral_visits", f"{M}.referrals.service:VisitPlan"),
        EntitySpec("referral_summaries", f"{M}.referrals.service:WeeklySummary"),
        EntitySpec("reports", f"{M}.reports.service:Report"),
        EntitySpec("extractions", f"{M}.requisitions.service:ExtractionRecord"),
        EntitySpec("backfill_cases", f"{M}.scheduling.service:BackfillCase"),
        EntitySpec("offers", f"{M}.scheduling.service:Offer"),
        EntitySpec("triage", f"{M}.triage.service:TriageRecord"),
        # Messages to external systems that could not be delivered (app/integrations/contract.py)
        EntitySpec("dead_letters", "app.integrations.contract:DeadLetter", encrypted=("payload",),
                   indexes=(("adapter", "status"),)),
        # Hospital platform (6.3): break-glass grants and the free-text de-identification misses
        EntitySpec("break_glass_grants", "app.ehr.breakglass:BreakGlassGrant", encrypted=("mrn", "reason", "review_note"),
                   indexes=(("user_id",), ("review_status",))),
        EntitySpec("deid_misses", "app.llm.freetext_deid:DeidMiss", kind="list", encrypted=("text", "context")),
        # Agent runtime (6.4): one trace per run (references and hashes only) and the Tool Gateway's
        # idempotency records (the first result of an action, replayed for 24 hours; encrypted)
        EntitySpec("agent_traces", "app.agents.trace:AgentTrace", indexes=(("agent_id",), ("started_at",))),
        EntitySpec("tool_idempotency", "app.agents.trace:IdempotencyRecord", encrypted=("result",),
                   indexes=(("expires_at",),)),
    ]
}

CONFIGS = {
    "tat_targets": f"{M}.backlog.service:TatTargets",
    "contrast_config": f"{M}.contrast.service:ContrastConfig",
    "critical_policy": f"{M}.critical.service:Policy",
    "qa_config": f"{M}.peer_review.service:QaConfig",
    "priority_weights": f"{M}.scheduling.service:PriorityWeights",
    "triage_config": f"{M}.triage.service:TriageConfig",
}

BLOBS = {
    "reading_roster",  # radiologist id -> on shift
    "report_by_study",  # study id -> report id
    "dose_by_appointment",  # appointment id -> dose record id
    "qa_state",  # last peer-review run and scheduled date
    "outside_archive",  # mock outside archive contents per patient
    "clinical_kg_log",
    "inspection_reminders",
    "inspection_qa",
    "phantom_variants",  # seeded image key -> phantom variant
    "billing_planted",
    "phipa_planted",
    "referral_planted_declines",
    "extra_reminders_done",
    "daily_reset",  # date of the last automatic demo reset
    "seed_time",  # the "now" the demo data was generated for (it ages from there)
    "hospital_plan",  # the day simulator's upcoming events (app/ehr)
    "hospital_clock",  # the hospital's simulated clock (app/ehr)
}

# Derived per process from stored data; never written to the database.
CACHES = {"noshow", "phipa_cache", "inspection_index"}

# ---------- Fixed tables ----------

module_state = Table(
    "module_state", metadata,
    Column("name", Text, primary_key=True),
    Column("data", JSONB, nullable=False),
    Column("updated_at", DateTime, nullable=False, server_default=func.now()),
)

images = Table(
    "images", metadata,
    Column("row_key", Text, primary_key=True),
    Column("seq", BigInteger, Identity(), nullable=False),
    Column("data", LargeBinary, nullable=False),
    Column("media_type", Text, nullable=False),
)

audit_events = Table(
    "audit_events", metadata,
    Column("seq", BigInteger, primary_key=True, autoincrement=False),
    Column("ts", DateTime, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("user_name", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("resource_type", Text, nullable=False),
    Column("resource_id", Text),
    Column("outcome", Text, nullable=False),
    Column("source_ip", Text),
    Column("reason", Text, nullable=False),
    Column("prev_hash", Text, nullable=False),
    Column("hash", Text, nullable=False),
    # Hospital platform (6.3, migration 0005); null on events written before.
    Column("event_type", Text),
    Column("patient_mrn_hash", Text),
    Column("encounter_id", Text),
    Column("module", Text),
    Column("prompt_version", Text),
)
Index("ix_audit_events_event_type", audit_events.c.event_type)
Index("ix_audit_events_patient_mrn_hash", audit_events.c.patient_mrn_hash)
Index("ix_audit_events_user_id", audit_events.c.user_id)

auth_sessions = Table(
    "auth_sessions", metadata,
    Column("token", Text, primary_key=True),
    Column("user_id", Text, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
)

app_meta = Table(
    "app_meta", metadata,
    Column("key", Text, primary_key=True),
    Column("value", Text, nullable=False),
)

# Tables a demo reset empties. Sessions survive: staff ids are stable across resets.
# fhir_resources is defined in app/ehr/fhirstore.py (the hospital EHR's FHIR store), the event
# log and its consumer cursors in app/ehr/events.py (a reset restarts the sequence, so they go together).
FIXED_DATA_TABLES = ("module_state", "images", "audit_events", "fhir_resources", "domain_events", "event_consumers",
                     "event_deliveries")


# ---------- Pydantic model -> table ----------


def _sql_type(annotation: Any):
    """Column type for a field annotation, or None when it is stored as JSONB."""
    origin = get_origin(annotation)
    if origin is Annotated:
        return _sql_type(get_args(annotation)[0])
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        return _sql_type(args[0]) if len(args) == 1 else None
    if origin is Literal:
        values = get_args(annotation)
        if all(isinstance(v, str) for v in values):
            return Text
        if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            return BigInteger
        return None
    if not isinstance(annotation, type):
        return None
    if issubclass(annotation, bool):
        return Boolean
    if issubclass(annotation, Enum):
        return Text if issubclass(annotation, str) else None
    if issubclass(annotation, int):
        return BigInteger
    if issubclass(annotation, float):
        return Float
    if issubclass(annotation, datetime):
        return DateTime
    if issubclass(annotation, date):
        return Date
    if issubclass(annotation, str):
        return Text
    return None


@dataclass
class Mapping:
    """A resolved EntitySpec: model class, table and how each column is encoded."""

    spec: EntitySpec
    model: type[BaseModel]
    table: Table
    fields: list[str]
    json_fields: frozenset[str]
    encrypted: frozenset[str]
    blind: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _import(ref: str) -> Any:
    module, _, name = ref.partition(":")
    return getattr(importlib.import_module(module), name)


@cache
def mapping(name: str) -> Mapping:
    spec = STORE_ENTITIES.get(name) or MODULE_ENTITIES[name]
    model = _import(spec.model_ref)
    fields = list(model.model_fields)
    reserved = {"row_key", "seq"} | {col for col, _ in spec.blind}
    if clash := reserved & set(fields):
        raise RuntimeError(f"{model.__name__} uses reserved column name(s) {sorted(clash)}")
    unknown = (set(spec.encrypted) | {src for _, src in spec.blind}) - set(fields)
    if unknown:
        raise RuntimeError(f"{spec.name}: no such field(s) {sorted(unknown)}")
    columns: list[Column] = []
    if spec.kind == "list":
        columns.append(Column("seq", BigInteger, Identity(), primary_key=True))
    else:
        columns.append(Column("row_key", Text, primary_key=True))
        columns.append(Column("seq", BigInteger, Identity(), nullable=False))
    json_fields = set()
    for fname, info in model.model_fields.items():
        sql_type = _sql_type(info.annotation)
        if sql_type is None:
            json_fields.add(fname)
        if fname in spec.encrypted:
            sql_type = Text  # ciphertext
        columns.append(Column(fname, sql_type or JSONB))
    for col, _ in spec.blind:
        columns.append(Column(col, Text))
    table = Table(spec.name, metadata, *columns)
    if spec.kind == "map":
        Index(f"ix_{spec.name}_seq", table.c.seq)
    for cols in spec.indexes:
        Index(f"ix_{spec.name}_{'_'.join(cols)}", *(table.c[c] for c in cols))
    for col, _ in spec.blind:
        Index(f"ix_{spec.name}_{col}", table.c[col])
    return Mapping(spec=spec, model=model, table=table, fields=fields, json_fields=frozenset(json_fields),
                   encrypted=frozenset(spec.encrypted), blind=spec.blind)


@cache
def config_model(name: str) -> type[BaseModel]:
    return _import(CONFIGS[name])


def all_entity_names() -> list[str]:
    return [*STORE_ENTITIES, *MODULE_ENTITIES]


def get_metadata() -> MetaData:
    """Metadata with every table defined (imports all model classes)."""
    import app.ehr.events  # noqa: F401  (defines the event log tables)
    import app.ehr.fhirstore  # noqa: F401  (defines fhir_resources)

    for name in all_entity_names():
        mapping(name)
    for name in CONFIGS:
        config_model(name)
    return metadata


def data_tables() -> list[str]:
    """Every table a demo reset empties."""
    return [*all_entity_names(), *FIXED_DATA_TABLES]
