"""The agent runtime (spec 6.4): load a registered agent, run it, leave a trace and a Provenance.

    with runtime.start("patient_message_triage", user=nurse, encounter_id="stay-00042") as run:
        ctx = run.gather(("getEncounterContext", {"encounter_id": run.encounter_id}))
        outcome = run.model(PROMPT, {"context": ctx.json(), ...}, Output, context=ctx,
                            untrusted={"message": text})
        run.tool("createTask", {...})

1. `start` loads the agent from the registry (unregistered: `UnregisteredAgent`;
   refused by the release gate: `AgentNotDeployable`) and checks that the run's
   encounter is the caller's to act on. It opens a trace.
2. Context comes only through the agent's read tools (`gather`); the resources
   are de-identified (app/llm/fhir_deid.py) and the same token map learns their
   identifiers for the free text (app/llm/freetext_deid.py).
3. `model` sends the prompt through the LLM gateway with that map (the hook WP2
   and WP4 prepared: pre-redacted input), untrusted text in its own delimited
   block; the gateway attaches the call (model, prompt version, tokens, cost) to
   the run's trace.
4. Every tool call goes through the Tool Gateway (app/agents/gateway.py).
   Resources a read returns become the run's input_refs; resources a write
   produces become its output_refs.
5. `finish` closes the trace and, when the run wrote FHIR resources, records the
   Provenance `prov-<run_id>` (targets, the agent as Device with its version, the
   user it acted for, the inputs, prompt version, model and run id).

A privileged call waits for a person: the run ends with the approval Task open,
and `execute_approved` resumes it once the approval is recorded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel

from app.agents import registry, untrusted as untrusted_text
from app.agents import trace as traces
from app.agents.gateway import ToolResult, agent_actor, runtime_fhir, tool_gateway
from app.agents.registry import AgentSpec
from app.core.context import request_context
from app.core.models import StaffUser
from app.core.store import get_store
from app.ehr import access
from app.ehr.clock import hospital_now
from app.ehr.gateway import FhirGateway
from app.fhir.dt import fhir_datetime, ref
from app.llm.deid import Pseudonymizer
from app.llm.fhir_deid import FhirDeidentifier
from app.llm.freetext_deid import FreeTextDeidentifier, deidentify
from app.llm.gateway import LlmOutcome, get_gateway
from app.llm.prompts import Prompt


class RunRefused(PermissionError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


@dataclass
class Context:
    """What an agent knows: de-identified resources from its read tools, and the token map."""

    resources: list[dict]
    results: list[ToolResult]
    pseudo: Pseudonymizer
    freetext: FreeTextDeidentifier
    refs: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def refused(self) -> list[ToolResult]:
        return [r for r in self.results if not r.ok]

    def json(self) -> str:
        return json.dumps(self.resources, ensure_ascii=False, separators=(",", ":"))


def _is_fhir_ref(value: str) -> bool:
    rtype, _, rid = value.partition("/")
    return bool(rid) and rtype[:1].isupper() and rtype not in ("LlmCall", "CallSession", "Tool")


class AgentRun:
    def __init__(self, spec: AgentSpec, trace: traces.AgentTrace, *, user: StaffUser | None, encounter_id: str | None,
                 patient_id: str | None, context_id: str | None = None, attachments: dict | None = None) -> None:
        self.spec = spec
        self.trace = trace
        self.user = user
        self.encounter_id = encounter_id
        self.patient_id = patient_id
        self.context_id = context_id
        self.attachments = attachments or {}
        self.last_model: dict | None = None  # the last model call's de-identified input and output
        self._token = None

    @property
    def run_id(self) -> str:
        return self.trace.run_id

    def __enter__(self) -> AgentRun:
        self._token = traces._active.set(self.trace)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.trace.finished_at is None:
                self.finish(outcome="failed" if exc_type else None)
        finally:
            traces._active.reset(self._token)

    def save(self) -> None:
        traces.save(self.trace)

    # ----- tools -----

    def tool(self, tool_id: str, args: dict | None = None, *, fallback: dict | None = None) -> ToolResult:
        result = tool_gateway().call(self, tool_id, args, fallback=fallback)
        spec = registry.tool(tool_id) if isinstance(tool_id, str) else None
        if result.ok and spec is not None:
            into = self.trace.input_refs if spec.side_effect_level == "read" else self.trace.output_refs
            into.extend(r for r in result.refs if r not in into)
            self.save()
        return result

    def gather(self, *calls: tuple[str, dict]) -> Context:
        """Run read tools and de-identify what they return (6.4 step 1)."""
        results = [self.tool(tool_id, args) for tool_id, args in calls]
        resources = [r for res in results if res.ok for r in (res.output or {}).get("resources", [])]
        pseudo = Pseudonymizer()
        redacted = FhirDeidentifier(pseudo).redact_all(resources)
        freetext = FreeTextDeidentifier(pseudo)
        freetext.learn_fhir(resources)
        return Context(redacted, results, pseudo, freetext, [f"{r['resourceType']}/{r['id']}" for r in resources])

    # ----- the model -----

    def model(self, prompt: Prompt, variables: dict, schema_cls: type[BaseModel], *, context: Context | None = None,
              untrusted: dict[str, str] | None = None, second_pass: bool = True) -> LlmOutcome:
        """One structured call through the LLM gateway; untrusted text is de-identified and goes into its own block."""
        if untrusted and not untrusted_text.has_rule(prompt.system):
            raise ValueError(f"{prompt.version} takes untrusted text but its system prompt lacks the untrusted rule")
        pseudo = context.pseudo if context else Pseudonymizer()
        freetext = context.freetext if context else FreeTextDeidentifier(pseudo)
        values = dict(variables)
        for name, text in (untrusted or {}).items():
            redacted = deidentify(text, freetext, second_pass=second_pass).text
            values[name] = untrusted_text.block(name, redacted)
        token = traces._active.set(self.trace)
        try:
            outcome = get_gateway().structured(task=self.spec.agent_id, prompt=prompt, variables=values,
                                               schema_cls=schema_cls, pseudonymizer=pseudo)
        finally:
            traces._active.reset(token)
        if outcome.status == "ok":  # kept for a low-confidence review (6.5): as the model saw and answered it
            self.last_model = {"prompt_version": prompt.version, "variables": values,
                               "output": pseudo.retokenize(outcome.data)}
        return outcome

    # ----- the end -----

    def finish(self, *, outcome: str | None = None, confidence: float | None = None) -> traces.AgentTrace:
        trace = self.trace
        if outcome:
            trace.outcome = outcome
        elif trace.outcome == "running":
            trace.outcome = "completed"
        if confidence is not None:
            trace.confidence = confidence
        trace.finished_at = datetime.now()
        if any(_is_fhir_ref(r) for r in trace.output_refs):
            on_behalf = ref("Practitioner", self.user.practitioner_id, self.user.name) \
                if self.user and self.user.practitioner_id else None
            prov = traces.provenance(trace, recorded=fhir_datetime(hospital_now(get_store())),
                                     on_behalf_of=on_behalf)
            fhir = runtime_fhir()
            existing = fhir.backend.read("Provenance", prov["id"])
            if existing is not None:  # a resumed run (an approved privileged call): keep the signers
                prov["agent"] += [a for a in existing.get("agent") or []
                                  if any(c.get("code") == "verifier" for c in (a.get("type") or {}).get("coding") or [])]
            fhir.record_provenance(prov, replace=True)
            trace.provenance_id = prov["id"]
        self.save()
        if confidence is not None:  # below the registry threshold: a person reviews it (6.5)
            from app.agents import reviews

            reviews.maybe_open(self, confidence)
        return trace


class AgentRuntime:
    def start(self, agent_id: str, *, user: StaffUser | None = None, encounter_id: str | None = None,
              context_id: str | None = None, input_refs: tuple[str, ...] = (),
              attachments: dict | None = None) -> AgentRun:
        """Load a registered agent and open its run (UnregisteredAgent / AgentNotDeployable / RunRefused)."""
        spec = registry.require_deployable(agent_id)
        if user is None:
            ctx = request_context()
            user = ctx.user if ctx else None
        patient_id = None
        if encounter_id is not None:
            encounter = runtime_fhir().backend.read("Encounter", encounter_id)
            if encounter is None:
                raise LookupError(f"Encounter/{encounter_id} not found")
            patient_id = access.patient_of(encounter)
            if user is not None and spec.data_scope.scope != "hospital" and \
                    not FhirGateway(agent_actor(spec, user), spec.agent_id).is_in_scope(patient_id):
                raise RunRefused("outside_data_scope", "The patient is outside your units; an agent cannot act "
                                                       "for you there (break-glass is personal)")
        store = get_store()
        trace = traces.AgentTrace(
            run_id=store.next_id("RUN"), agent_id=spec.agent_id, agent_version=spec.version, kind="run",
            mode=get_gateway().mode, eval_status=spec.eval_status.status, evaluated=spec.evaluated,
            actor_id=user.id if user else "system", actor_role=str(user.role) if user else "system",
            encounter_id=encounter_id, context_id=context_id, input_refs=list(input_refs),
            started_at=datetime.now())
        traces.save(trace)
        return AgentRun(spec, trace, user=user, encounter_id=encounter_id, patient_id=patient_id,
                        context_id=context_id, attachments=attachments)

    def resume(self, run_id: str) -> AgentRun:
        trace = traces.get(run_id)
        if trace is None:
            raise LookupError(f"No agent run {run_id}")
        spec = registry.require_deployable(trace.agent_id)
        user = get_store().staff.get(trace.actor_id) if trace.actor_id not in (None, "system") else None
        patient_id = None
        if trace.encounter_id:
            patient_id = access.patient_of(runtime_fhir().backend.read("Encounter", trace.encounter_id))
        trace.finished_at = None
        return AgentRun(spec, trace, user=user, encounter_id=trace.encounter_id, patient_id=patient_id,
                        context_id=trace.context_id)

    def execute_approved(self, decision: dict) -> ToolResult | None:
        """After a person approved a privileged call (approvals.decide), run it on the original run."""
        if decision.get("kind") != "approval-request" or decision.get("decision") != "approve":
            return None
        with self.resume(decision["run_id"]) as run:
            result = run.tool(decision["tool_id"], {**(decision.get("args") or {}),
                                                    "approval_task_id": decision["task_id"]})
            run.finish(outcome="completed" if result.ok else "needs_human")
        return result


def record_signature(document: dict, signer: dict, role: str, user_id: str) -> None:
    """The signing service tells the runtime: the signer joins the Provenance and the trace records the action."""
    run_id = traces.run_of(document)
    if not run_id:
        return
    fhir = runtime_fhir()
    prov = fhir.backend.read("Provenance", traces.provenance_id(run_id))
    if prov is not None:
        fhir.record_provenance(traces.add_signer(prov, signer, role), replace=True)
    trace = traces.get(run_id)
    if trace is not None:
        trace.human_action = {"role": role, "user_id": user_id, "decision": "sign",
                              "at": datetime.now().isoformat(timespec="seconds"),
                              "document": f"DocumentReference/{document.get('id')}"}
        traces.save(trace)


def lineage(resource_ref: str) -> dict:
    """From an output back to its run: the trace (inputs, model, prompt version, tool calls) and the Provenance."""
    rtype, _, rid = resource_ref.partition("/")
    fhir = runtime_fhir()
    resource = fhir.backend.read(rtype, rid)
    if resource is None:
        raise LookupError(f"{resource_ref} not found")
    run_id = traces.run_of(resource)
    if run_id is None:
        provenances = fhir.backend.search("Provenance", focus=resource_ref)
        run_id = traces.provenance_fields(provenances[0]).get("run_id") if provenances else None
    if run_id is None:
        return {"resource": resource_ref, "run": None, "trace": None, "provenance": None}
    trace = traces.get(run_id)
    prov = fhir.backend.read("Provenance", traces.provenance_id(run_id))
    return {"resource": resource_ref, "run": run_id, "trace": trace.model_dump(mode="json") if trace else None,
            "provenance": prov, **traces.provenance_fields(prov)}


runtime = AgentRuntime()
