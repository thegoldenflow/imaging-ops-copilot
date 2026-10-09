"""WP4 (spec 6.3): free-text de-identification: the rule layer, typed tokens and the
server-side map, the second pass through the LLM gateway, the gateway's pre-redacted
input (the hook WP2 left open), and the eval gate on 200 synthetic notes."""

from datetime import date

from sqlalchemy import text

from app.agents import registry
from app.llm.deid_eval import PRECISION_MIN, RECALL_MIN, evaluate, load_cases
from app.llm.fhir_deid import FhirDeidentifier
from app.llm.freetext_deid import FreeTextDeidentifier, PhiFindings, deidentify
from app.llm.gateway import LlmGateway, get_gateway, set_gateway
from app.llm.prompts import Prompt
from app.llm.providers import MockProvider, ProviderUnavailable, mock_fixture

REF = date(2026, 10, 8)
NOTE = ("Nursing note. Margaret Tremblay (王芳), MRN 40012345, HCN 1234-567-890-AB. Seen by Dr. Alicia Moreau on "
        "Oct 5, 2026; follow-up 10/15 with Dr. Okonjo at Parkdale Medical Centre. Daughter Emily Santos can be "
        "reached at (416) 555-0123 ext. 234. Lives at 45 Sample Ave, Toronto, ON M4C 1A1. 患者王芳，2026年10月6日"
        "入院。家属李娜（女儿），电话 905-555-0101 分机 302。王女士今日精神好转。Signed: R. Salazar, RN. "
        "Parkinson's disease stable; Foley removed at 06:00; pain 5/10; BP 132/84; GCS 15/15.")


def _deid() -> FreeTextDeidentifier:
    deid = FreeTextDeidentifier(reference=REF)
    deid.add_person(["Margaret Tremblay", "王芳"])
    deid.add_known("40012345", "MRN")
    deid.add_person(["Dr. Alicia Moreau"], "STAFF")
    deid.add_person(["Rosa Salazar"], "STAFF")
    return deid


def test_rule_layer_replaces_every_kind_with_typed_tokens():
    deid = _deid()
    out = deid.redact(NOTE)
    for phi in ("Margaret", "Tremblay", "王芳", "40012345", "1234-567-890-AB", "Alicia Moreau", "Okonjo", "Parkdale",
                "Emily Santos", "555-0123", "ext. 234", "45 Sample Ave", "M4C 1A1", "李娜", "905-555-0101", "302",
                "Salazar", "Oct 5", "10/15", "2026年10月6日"):
        assert phi not in out, phi
    assert out.count("[PERSON_1]") == 3  # one token for the patient, whichever spelling
    assert "Seen by [STAFF_1] on [DATE_1:D-3]" in out and "follow-up [DATE_2:D+7]" in out  # relative days kept
    assert "[DATE_3:D-2]" in out  # the Chinese date
    for kind in ("MRN", "HEALTH_CARD", "PHONE", "ADDRESS", "ORG"):
        assert f"[{kind}_1]" in out, kind
    for clinical in ("Parkinson's disease", "Foley removed at 06:00", "pain 5/10", "BP 132/84", "GCS 15/15"):
        assert clinical in out, clinical
    # The server-side map restores every token; one person is one token, so it comes back in its first spelling.
    assert deid.restore(out) == NOTE.replace("王芳", "Margaret Tremblay")


def test_tokens_already_in_the_text_are_left_alone():
    deid = _deid()
    once = deid.redact(NOTE)
    assert deid.redact(once) == once


def test_second_pass_catches_what_the_rules_miss_and_records_it(fresh_state):
    deid = _deid()
    note = "Hannah Novak will drive the patient home. Seen by Dr. Alicia Moreau."
    result = deidentify(note, deid)
    assert result.second_pass == "ok" and "Hannah Novak" not in result.text
    assert [(m.kind, m.source) for m in result.misses] == [("PERSON", "model")]
    miss = fresh_state.module("deid_misses", list)[-1]
    assert (miss.kind, miss.text, miss.task) == ("PERSON", "Hannah Novak", "deid_check")
    fresh_state.flush()
    row = fresh_state.conn().execute(text("SELECT text FROM deid_misses ORDER BY seq DESC LIMIT 1")).first()
    assert "Hannah" not in row.text  # encrypted at rest
    call = fresh_state.llm_calls[-1]
    assert (call.task, call.prompt_version) == ("deid_check", "deid_check@1")


def test_identifiers_learned_from_fhir_and_the_keyed_mrn_token_survive_the_second_pass(fresh_state):
    """A patient's MRN becomes FHIR de-identification's keyed-hash token [MRN_<hex>]; the second pass must
    treat it as a placeholder (it once re-redacted the digits inside the hash)."""
    patient = {"resourceType": "Patient", "id": "p1", "name": [{"family": "Prescott", "given": ["Ruth"]}],
               "identifier": [{"system": "urn:demo-hospital:mrn", "value": "40000388"}],
               "telecom": [{"system": "phone", "value": "647-555-0144"}], "address": [{"line": ["45 Sample Ave"]}]}
    deid = FreeTextDeidentifier(reference=REF)
    deid.learn_fhir([patient])
    result = deidentify("Mrs. Prescott (MRN 40000388), 647-555-0144, 45 Sample Ave. Ruth slept well.", deid)
    token = next(t for t in result.text.split() if t.startswith("[MRN_"))
    assert token.rstrip("),") == deid.pseudo.token("MRN", "40000388") and "[ID_" not in result.text
    assert "Prescott" not in result.text and "Ruth" not in result.text and "647-555-0144" not in result.text
    assert result.misses == []


def test_second_pass_unavailable_keeps_the_rule_result(fresh_state):
    class Down(MockProvider):
        def complete_json(self, **kwargs):
            raise ProviderUnavailable("timeout")

    previous = get_gateway()
    set_gateway(LlmGateway(Down(latency_s=0)))
    try:
        result = deidentify(NOTE, _deid())
    finally:
        set_gateway(previous)
    assert result.second_pass == "unavailable" and result.text == _deid().redact(NOTE)


def test_the_model_only_sees_redacted_text(fresh_state):
    seen = []

    @mock_fixture("deid_spy")
    def _spy(text, images, attempt):
        seen.append(text)
        return {"findings": []}

    with registry.temporary("deid_spy", {"kind": "embedded_agent"}):
        deidentify(NOTE, _deid(), task="deid_spy")
    assert seen and "Tremblay" not in seen[0] and "王芳" not in seen[0] and "[PERSON_1]" in seen[0]
    assert PhiFindings.model_validate({"findings": []}).findings == []


def test_gateway_takes_fhir_redacted_input_without_retokenising_dates(fresh_state):
    """The WP2 hook: resources de-identified by FhirDeidentifier keep their clinical dates in the prompt,
    and the model's output is re-identified with the same map."""
    patient = {"resourceType": "Patient", "id": "p1", "name": [{"family": "Tremblay", "given": ["Margaret"]}],
               "birthDate": "1948-03-12", "identifier": [{"system": "urn:demo-hospital:mrn", "value": "40012345"}]}
    obs = {"resourceType": "Observation", "id": "o1", "status": "final", "code": {"text": "Note"},
           "subject": {"reference": "Patient/p1", "display": "Margaret Tremblay"},
           "effectiveDateTime": "2026-10-07T08:00:00-04:00", "valueString": "Margaret Tremblay walked 20 m"}
    fhir_deid = FhirDeidentifier(on=REF)
    redacted = fhir_deid.redact_all([patient, obs])
    seen = []

    @mock_fixture("hook_test")
    def _echo(text, images, attempt):
        seen.append(text)
        return {"summary": "[PERSON_1] walked on 2026-10-07"}

    from pydantic import BaseModel

    class Out(BaseModel):
        summary: str

    import json

    prompt = Prompt("hook_test", "hook_test@1", "Summarise.", "{resources}")
    with registry.temporary("hook_test", {"kind": "embedded_agent"}):
        outcome = get_gateway().structured(task="hook_test", prompt=prompt,
                                           variables={"resources": json.dumps(redacted)}, schema_cls=Out,
                                           pseudonymizer=fhir_deid.pseudo)
    assert "2026-10-07T08:00:00-04:00" in seen[0] and "[DATE_" not in seen[0]  # clinical times stay
    assert "Tremblay" not in seen[0] and "40012345" not in seen[0]
    assert outcome.data["summary"] == "Margaret Tremblay walked on 2026-10-07"


def test_eval_meets_the_thresholds(fresh_state):
    """The release gate of 6.3: recall >= 0.98 and precision >= 0.90 on the 200 synthetic notes."""
    cases = load_cases()
    assert len(cases) == 200 and sum(len(c["phi"]) for c in cases) > 2000
    report = evaluate(cases)
    assert report["passed"], report["pipeline"]
    assert report["pipeline"]["recall"] >= RECALL_MIN and report["pipeline"]["precision"] >= PRECISION_MIN
    assert report["rule_layer"]["recall"] >= RECALL_MIN  # the rules alone pass too, without any model
    assert report["second_pass"]["outcomes"] == {"ok": 200}
