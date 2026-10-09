"""The Tool Gateway (spec 6.4): every tool call of an agent passes here.

Checks, in this order, and the first that fails refuses the call (audited, and
recorded in the run's trace):

1. the agent is registered (and the release gate lets it run in this mode);
2. the tool is registered and on the agent's allow-list; the arguments match the
   tool's input schema (closed: an argument the schema does not name is refused);
3. the caller's role is in the tool's permission_policy (the user the agent acts
   for, or "system" for a background run);
4. data scope: the resource types the tool returns are in the agent's
   data_scope, and the target (named by the argument permission_policy.target
   points at) is the run's encounter (scope encounter), a patient of the user's
   units (own_unit), or any patient the user may see (hospital);
5. consent: every consent category the agent needs is permit for the target patient;
6. the precondition of the side-effect level:
   - read: none; recommend: the result must be a draft or proposal;
   - action: the idempotency key sha256(agent_id, encounter_id, tool_id,
     canonical(args)); a repeat within 24 hours returns the first result
     without executing again;
   - privileged: a recorded human approval (a completed approval Task for exactly
     these arguments plus an `approve` or `sign` audit event by an approver role).
     A call without `approval_task_id` is a request for approval: it opens the
     approval Task and returns `approval_required`. Privileged calls run on the
     approver's authority, with 0 automatic retries and two audit events.

Authorisation looks only at the registry and the caller's identity, never at
anything the model wrote (a model that claims to be an administrator, or passes
a made-up approval, gets the same refusal). Then the tool's handler runs (domain
services: FhirGateway, the signing service, consent management and the event
bus) with the registry's retry policy; when the retries are used up the caller's
fallback (a rule-layer result) is returned, otherwise a "needs a person" Task is
opened. Output is checked against the tool's output schema.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from importlib import import_module
from typing import TYPE_CHECKING, Any, Literal

from app.agents import jsonschema, registry
from app.agents import trace as traces
from app.agents.registry import AgentSpec, ToolSpec
from app.core.audit import mrn_hash
from app.core.models import StaffUser
from app.core.store import get_store
from app.ehr import access
from app.ehr.codes import MRN_SYSTEM
from app.ehr.gateway import Actor, FhirAccessDenied, FhirConflict, FhirGateway

if TYPE_CHECKING:
    from app.agents.runtime import AgentRun

Status = Literal["ok", "replayed", "refused", "failed", "fallback", "approval_required"]
RUNTIME_MODULE = "agent_runtime"
RUNTIME_ACTOR = Actor.system("agent-runtime")


class ToolPreconditionFailed(Exception):
    """A handler's own precondition (e.g. the phone caller is not verified): refused, the message goes back to the model."""


class ToolUnavailable(Exception):
    """A transient failure of the domain service: retried per the registry."""


def _transient() -> tuple[type[BaseException], ...]:
    from app.ehr.hapi import FhirUnavailable
    from app.llm.providers import ProviderUnavailable

    return ToolUnavailable, FhirUnavailable, ProviderUnavailable, TimeoutError, ConnectionError


@dataclass
class ToolResult:
    tool_id: str
    status: Status
    output: dict | None = None
    reason: str | None = None  # refusal or failure code
    detail: str | None = None
    refs: list[str] = field(default_factory=list)
    idempotency_key: str | None = None
    approval_task_id: str | None = None
    needs_human_task_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "replayed", "fallback")

    def for_model(self) -> dict:
        """What goes back to the model: the output, or why the call did not run."""
        if self.ok:
            return self.output or {}
        return {"error": self.detail or self.reason, "status": self.status,
                **({"approval_task_id": self.approval_task_id} if self.approval_task_id else {})}


class _Refusal(Exception):
    def __init__(self, reason: str, detail: str, *, status: Status = "refused", approval_task_id: str | None = None):
        super().__init__(detail)
        self.reason, self.detail, self.status, self.approval_task_id = reason, detail, status, approval_task_id


@dataclass
class Target:
    patient_id: str | None = None
    encounter_id: str | None = None
    unit_id: str | None = None
    ref: str | None = None  # the target resource ("Encounter/stay-00012", "DocumentReference/doc-1", ...)


@dataclass
class ToolContext:
    """What a handler gets: the run, the tool, and a FhirGateway opened for the right actor."""

    run: AgentRun
    tool: ToolSpec
    fhir: FhirGateway | None
    actor: Actor
    target: Target
    approver: StaffUser | None = None

    @property
    def agent(self) -> AgentSpec:
        return self.run.spec

    @property
    def attachments(self) -> dict:
        return self.run.attachments

    def stamp(self, resource: dict) -> dict:
        return traces.stamp(resource, self.run.run_id, self.run.spec.label)

    def readable(self, resource_type: str) -> bool:
        """The agent's data scope and the actor's role both allow reading this type."""
        if resource_type not in self.run.spec.data_scope.resources:
            return False
        policy = self.fhir.policy if self.fhir is not None else None
        return policy is None or resource_type in policy.read


def runtime_fhir() -> FhirGateway:
    """The runtime's own gateway (system actor, module agent_runtime): target lookups, approvals, provenance."""
    return FhirGateway(RUNTIME_ACTOR, RUNTIME_MODULE)


def agent_actor(spec: AgentSpec, user: StaffUser | None) -> Actor:
    """The FhirGateway actor of an agent: the user it acts for (their policy) or the agent itself."""
    if user is not None:
        from app.core.context import request_context

        ctx = request_context()
        return Actor(user.id, f"{spec.agent_id} for {user.name}", str(user.role), "agent",
                     ctx.source_ip if ctx else None, tuple(user.unit_ids), user.practitioner_id)
    return Actor(f"agent:{spec.agent_id}", spec.label, access.AGENT_ROLE + spec.agent_id, "agent")


class ToolGateway:
    # ---------- the call ----------

    def call(self, run: AgentRun, tool_id: str, args: dict | None, *, fallback: dict | None = None) -> ToolResult:
        args = dict(args or {})
        started = time.monotonic()
        spec_tool = registry.tool(tool_id) if isinstance(tool_id, str) else None
        level = spec_tool.side_effect_level if spec_tool else "unknown"
        ahash = traces.args_hash(args)
        record = traces.ToolCallRecord(tool_id=str(tool_id)[:80], side_effect_level=level, args_hash=ahash,
                                       status="refused", at=datetime.now())
        target = Target()
        try:
            spec = self._agent(run)
            tool = self._tool(spec, tool_id)
            requesting = tool.side_effect_level == "privileged" and "approval_task_id" not in args
            self._arguments(tool, args, requesting)
            self._role(run, tool)
            target = self._target(run, tool, args)
            self._scope(run, spec, tool, target)
            self._consent(spec, target)
            if tool.side_effect_level == "privileged":
                result = self._privileged(run, spec, tool, args, target, requesting, record)
            else:
                result = self._execute_idempotent(run, spec, tool, args, target, record, fallback=fallback)
        except _Refusal as r:
            result = ToolResult(str(tool_id), r.status, reason=r.reason, detail=r.detail,
                                approval_task_id=r.approval_task_id,
                                output={"error": r.detail} if r.reason == "precondition" else None)
            if r.status == "approval_required":
                record.approval_task_id = r.approval_task_id
            self._audit(run, spec_tool, str(tool_id), ahash, target,
                        outcome="approval_required" if r.status == "approval_required" else "denied",
                        detail=f"{r.reason}: {r.detail}")
        record.status = result.status
        record.reason = result.reason
        record.result_ref = result.refs
        record.idempotency_key = result.idempotency_key
        record.latency_ms = int((time.monotonic() - started) * 1000)
        run.trace.tool_calls.append(record)
        run.save()
        return result

    # ---------- 1. agent ----------

    @staticmethod
    def _agent(run: AgentRun) -> AgentSpec:
        spec = registry.agent(run.spec.agent_id)
        if spec is None:
            raise _Refusal("unregistered_agent", f"agent {run.spec.agent_id} is not in the agent registry")
        if why := registry.gate(spec):
            raise _Refusal("not_deployable", why)
        return spec

    # ---------- 2. allow-list and arguments ----------

    @staticmethod
    def _tool(spec: AgentSpec, tool_id: Any) -> ToolSpec:
        tool = registry.tool(tool_id) if isinstance(tool_id, str) else None
        if tool is None:
            raise _Refusal("unknown_tool", f"{str(tool_id)[:80]!r} is not a registered tool")
        if tool_id not in spec.allowed_tools or tool.internal:
            raise _Refusal("not_on_allow_list", f"{tool_id} is not on the allow-list of {spec.label}")
        return tool

    @staticmethod
    def _arguments(tool: ToolSpec, args: dict, requesting: bool) -> None:
        schema = tool.input_schema
        if requesting:  # a request for approval: everything but the approval itself
            schema = {**schema, "required": [r for r in schema.get("required") or [] if r != "approval_task_id"]}
        if errors := jsonschema.validate(args, schema):
            raise _Refusal("invalid_arguments", "; ".join(errors[:5]))

    # ---------- 3. role ----------

    @staticmethod
    def _role(run: AgentRun, tool: ToolSpec) -> None:
        role = str(run.user.role) if run.user else "system"
        if role not in tool.permission_policy.roles:
            raise _Refusal("role_not_permitted", f"the {role} role may not cause {tool.tool_id}")

    # ---------- 4. data scope ----------

    @staticmethod
    def _target(run: AgentRun, tool: ToolSpec, args: dict) -> Target:
        spec_target = tool.permission_policy.target
        if not spec_target:
            return Target(patient_id=run.patient_id, encounter_id=run.encounter_id)
        fhir = runtime_fhir()
        (kind, arg), = spec_target.items()
        value = args.get(arg)
        if kind == "unit":
            return Target(unit_id=value, ref=f"Location/{value}")
        if kind == "patient":
            if fhir.backend.read("Patient", value) is None:
                raise _Refusal("not_found", f"no Patient/{value}")
            return Target(patient_id=value, ref=f"Patient/{value}")
        rtype = {"encounter": "Encounter", "document": "DocumentReference", "task": "Task",
                 "appointment": "Appointment", "flag": "Flag"}[kind]
        resource = fhir.backend.read(rtype, value)
        if resource is None:
            raise _Refusal("not_found", f"no {rtype}/{value}")
        return Target(patient_id=access.patient_of(resource), encounter_id=access.encounter_of(resource),
                      ref=f"{rtype}/{value}")

    @staticmethod
    def _scope(run: AgentRun, spec: AgentSpec, tool: ToolSpec, target: Target) -> None:
        scope = spec.data_scope
        outside = [t for t in tool.reads if t not in scope.resources]
        if outside:
            raise _Refusal("outside_data_scope", f"{spec.agent_id} may not read {', '.join(outside)}")
        if tool.domain == "imaging_frontdesk":  # imaging records, not the EHR: the handlers check the caller
            return
        if scope.scope == "none" and (target.patient_id or target.unit_id):
            raise _Refusal("outside_data_scope", f"{spec.agent_id} has no patient data scope")
        if scope.scope == "encounter":
            if target.unit_id is not None:
                raise _Refusal("outside_data_scope", f"{spec.agent_id} works on one encounter, not a unit")
            if run.encounter_id is None:
                raise _Refusal("outside_data_scope", f"{spec.agent_id} runs only for an encounter")
            same = target.encounter_id == run.encounter_id if target.encounter_id else \
                target.patient_id is not None and target.patient_id == run.patient_id
            if not same:
                raise _Refusal("outside_data_scope", f"{target.ref} is not part of this run's encounter "
                                                     f"({run.encounter_id})")
        if run.user is None or scope.scope == "hospital":
            return
        user_fhir = FhirGateway(agent_actor(spec, run.user), spec.agent_id)
        policy = user_fhir.policy
        if target.unit_id is not None:
            if policy.scope != "hospital" and target.unit_id not in run.user.unit_ids:
                raise _Refusal("outside_data_scope", f"unit {target.unit_id} is not one of the user's units")
        elif target.patient_id is not None and not user_fhir.is_in_scope(target.patient_id):
            raise _Refusal("outside_data_scope", "the patient is outside the user's units (agents do not use "
                                                 "break-glass)")

    # ---------- 5. consent ----------

    @staticmethod
    def _consent(spec: AgentSpec, target: Target) -> None:
        if not spec.consent_required or target.patient_id is None:
            return
        from app.ehr.consent import consent_status

        states = consent_status(runtime_fhir(), target.patient_id)
        missing = [f"{c}: {states[c]}" for c in spec.consent_required if states.get(c) != "permit"]
        if missing:
            raise _Refusal("consent_missing", f"{spec.agent_id} needs the patient's consent ({'; '.join(missing)})")

    # ---------- 6. preconditions and execution ----------

    @staticmethod
    def _idempotency():
        return get_store().module(traces.IDEMPOTENCY, dict)

    def _stored(self, key: str, status: str = "done") -> traces.IdempotencyRecord | None:
        found = self._idempotency().get(key)
        if found is None or found.status != status or found.expires_at <= datetime.now():
            return None
        return found

    def _remember(self, run: AgentRun, tool: ToolSpec, key: str, result: dict, *, status: str = "done",
                  approval_task_id: str | None = None) -> None:
        now = datetime.now().replace(microsecond=0)
        self._idempotency()[key] = traces.IdempotencyRecord(
            key=key, agent_id=run.spec.agent_id, tool_id=tool.tool_id, encounter_id=run.encounter_id,
            run_id=run.run_id, status=status, result=result, approval_task_id=approval_task_id, created_at=now,
            expires_at=now + timedelta(hours=tool.idempotency.window_hours))

    @staticmethod
    def _lock(key: str) -> None:
        """Serialise identical calls (two workers, a retried request) on the key until the transaction ends."""
        store = get_store()
        if not store.detached:
            from sqlalchemy import text

            store.conn().execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": int(key[:15], 16)})

    @staticmethod
    def _key(run: AgentRun, tool: ToolSpec, args: dict, target: Target) -> str:
        encounter = target.encounter_id or run.encounter_id or run.context_id
        return traces.idempotency_key(run.spec.agent_id, encounter, tool.tool_id, args)

    def _execute_idempotent(self, run: AgentRun, spec: AgentSpec, tool: ToolSpec, args: dict, target: Target,
                            record: traces.ToolCallRecord, *, fallback: dict | None) -> ToolResult:
        key = None
        if tool.idempotency.required:
            key = self._key(run, tool, args, target)
            self._lock(key)
            if (done := self._stored(key)) is not None:
                refs = done.result.get("_refs", [])
                self._audit(run, tool, tool.tool_id, record.args_hash, target, outcome="replayed",
                            detail=f"first result of run {done.run_id} replayed (idempotency key {key[:12]})")
                return ToolResult(tool.tool_id, "replayed", _public(done.result), refs=refs, idempotency_key=key)
        actor = agent_actor(spec, run.user)
        ctx = ToolContext(run, tool, FhirGateway(actor, spec.agent_id) if tool.domain != "imaging_frontdesk" else None,
                          actor, target)
        result = self._run_handler(ctx, args, record, fallback=fallback, retries=True)
        result.idempotency_key = key
        if key and result.status == "ok":
            self._remember(run, tool, key, {**(result.output or {}), "_refs": result.refs})
        if result.status == "ok" and tool.side_effect_level == "recommend":
            self._check_draft(ctx, result)
        self._audit(run, tool, tool.tool_id, record.args_hash, target,
                    outcome="allowed" if result.ok else result.status, detail=_refs_detail(result))
        return result

    def _privileged(self, run: AgentRun, spec: AgentSpec, tool: ToolSpec, args: dict, target: Target,
                    requesting: bool, record: traces.ToolCallRecord) -> ToolResult:
        from app.agents import approvals

        request_args = {k: v for k, v in args.items() if k != "approval_task_id"}
        request_key = self._key(run, tool, request_args, target)
        approvers = spec.required_signoff_role if tool.permission_policy.approver_roles == "signoff" \
            else tool.permission_policy.approver_roles
        if requesting:
            self._lock(request_key)
            pending = self._stored(request_key, "awaiting_approval")
            task_id = pending.approval_task_id if pending and approvals.is_open(pending.approval_task_id) else None
            if task_id is None:
                task_id = approvals.request(run, tool, request_args, traces.args_hash(request_args), target,
                                            list(approvers))
                self._remember(run, tool, request_key, {}, status="awaiting_approval", approval_task_id=task_id)
            raise _Refusal("approval_required", f"{tool.tool_id} needs a recorded approval by "
                                                f"{' or '.join(approvers)}; approval task {task_id} is open",
                           status="approval_required", approval_task_id=task_id)
        key = self._key(run, tool, args, target)
        self._lock(key)
        if (done := self._stored(key)) is not None:
            self._audit(run, tool, tool.tool_id, record.args_hash, target, outcome="replayed",
                        detail=f"first result of run {done.run_id} replayed (idempotency key {key[:12]})")
            return ToolResult(tool.tool_id, "replayed", _public(done.result), refs=done.result.get("_refs", []),
                              idempotency_key=key, approval_task_id=args["approval_task_id"])
        try:
            approver = approvals.verify(args["approval_task_id"], run, tool, traces.args_hash(request_args),
                                        list(approvers), target)
        except approvals.ApprovalInvalid as e:
            raise _Refusal("approval_invalid", str(e)) from e
        record.approval_task_id = args["approval_task_id"]
        # double audit: the approved request before execution, the result after
        self._audit(run, tool, tool.tool_id, record.args_hash, target, outcome="approved", action="privileged_call",
                    detail=f"approved by {approver.id} ({approver.role}) in task {args['approval_task_id']}")
        actor = Actor.of(approver)
        ctx = ToolContext(run, tool, FhirGateway(actor, spec.agent_id), actor, target, approver=approver)
        result = self._run_handler(ctx, request_args, record, fallback=None, retries=False)
        result.idempotency_key = key
        result.approval_task_id = args["approval_task_id"]
        if result.status == "ok":
            self._remember(run, tool, key, {**(result.output or {}), "_refs": result.refs})
        self._audit(run, tool, tool.tool_id, record.args_hash, target, action="privileged_call",
                    outcome="allowed" if result.ok else result.status, detail=_refs_detail(result))
        return result

    def _run_handler(self, ctx: ToolContext, args: dict, record: traces.ToolCallRecord, *,
                     fallback: dict | None, retries: bool) -> ToolResult:
        tool = ctx.tool
        module, _, name = tool.handler.partition(":")
        handler = getattr(import_module(module), name)
        attempts = tool.retry.max_attempts if retries else 1
        last_error = None
        for attempt in range(1, attempts + 1):
            record.attempts = attempt
            started = time.monotonic()
            try:
                output = handler(ctx, **args)
            except ToolPreconditionFailed as e:
                raise _Refusal("precondition", str(e)) from e
            except FhirAccessDenied as e:
                raise _Refusal("domain_denied", str(e)) from e
            except FhirConflict as e:
                return ToolResult(tool.tool_id, "failed", reason="conflict", detail=str(e))
            except LookupError as e:
                return ToolResult(tool.tool_id, "failed", reason="not_found", detail=str(e))
            except ValueError as e:
                return ToolResult(tool.tool_id, "failed", reason="invalid_arguments", detail=str(e))
            except _transient() as e:
                last_error = e
                if attempt < attempts:
                    time.sleep(tool.retry.backoff_ms / 1000 * 2 ** (attempt - 1))
                continue
            elapsed_ms = (time.monotonic() - started) * 1000
            if tool.side_effect_level == "read" and elapsed_ms > tool.timeout_ms and attempt < attempts:
                last_error = TimeoutError(f"{tool.tool_id} took {int(elapsed_ms)} ms (limit {tool.timeout_ms})")
                continue  # a late read is discarded and retried; writes keep their (late) result
            refs = list(output.pop("_refs", [])) if isinstance(output, dict) else []
            if errors := jsonschema.validate(output, tool.output_schema):
                return ToolResult(tool.tool_id, "failed", reason="invalid_output", detail="; ".join(errors[:5]))
            return ToolResult(tool.tool_id, "ok", output, refs=refs)
        detail = f"{tool.tool_id} failed after {attempts} attempt(s): {last_error}"
        if fallback is not None:
            return ToolResult(tool.tool_id, "fallback", fallback, reason="unavailable", detail=detail)
        task_id = self._needs_human(ctx, detail)
        return ToolResult(tool.tool_id, "failed", reason="unavailable", detail=detail, needs_human_task_id=task_id)

    @staticmethod
    def _check_draft(ctx: ToolContext, result: ToolResult) -> None:
        """Recommend tools put drafts and proposals in a review queue; anything else is a bug worth stopping on."""
        for ref in result.refs:
            rtype, _, rid = ref.partition("/")
            stored = runtime_fhir().backend.read(rtype, rid) or {}
            status = stored.get("docStatus") if rtype == "DocumentReference" else stored.get("status")
            if status not in registry.DRAFT_STATUSES or (rtype == "Task" and stored.get("intent") != "proposal"):
                raise RuntimeError(f"{ctx.tool.tool_id} produced {ref} with status {status}; recommend tools "
                                   f"produce drafts and proposals only")

    @staticmethod
    def _needs_human(ctx: ToolContext, detail: str) -> str | None:
        """6.4 fallback without a rule result: a Task asking a person to do it by hand."""
        if ctx.target.patient_id is None or ctx.tool.domain == "imaging_frontdesk":
            return None
        from app.agents import approvals

        return approvals.needs_human(ctx.run, ctx.tool, ctx.target, detail)

    # ---------- audit ----------

    @staticmethod
    def _audit(run: AgentRun, tool: ToolSpec | None, tool_id: str, ahash: str, target: Target, *, outcome: str,
               detail: str, action: str = "tool_call") -> None:
        if tool is not None and tool.audit_level == "standard" and outcome in ("allowed", "replayed") \
                and tool.side_effect_level == "read":
            detail = ""  # reads: the FhirGateway already audits what was read
        user = run.user
        mrn = None
        if target.patient_id:
            patient = runtime_fhir().backend.read("Patient", target.patient_id) or {}
            mrn = next((i.get("value") for i in patient.get("identifier") or [] if i.get("system") == MRN_SYSTEM),
                       None)
        reason = "; ".join(x for x in (f"run {run.run_id}", f"args {ahash[:16]}", detail) if x)
        get_store().audit.record(
            user_id=user.id if user else f"agent:{run.spec.agent_id}",
            user_name=f"{run.spec.agent_id} for {user.name}" if user else run.spec.label,
            role=str(user.role) if user else "system", action=action, event_type="tool_call",
            resource_type="Tool", resource_id=tool_id, outcome=outcome, reason=reason[:500],
            patient_mrn_hash=mrn_hash(mrn) if mrn else None, encounter_id=target.encounter_id,
            module=run.spec.agent_id)


def _public(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "_refs"}


def _refs_detail(result: ToolResult) -> str:
    if result.ok:
        return f"result {', '.join(result.refs)}" if result.refs else ""
    return f"{result.reason}: {result.detail}"


_gateway = ToolGateway()


def tool_gateway() -> ToolGateway:
    return _gateway
