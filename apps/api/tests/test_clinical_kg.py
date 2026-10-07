"""Clinical knowledge Q&A over the in-memory knowledge graph."""

from datetime import datetime

import pytest

from app.core.store import get_store
from app.llm.gateway import LlmGateway, set_gateway
from app.llm.providers import MOCK_FIXTURES, ProviderResult, ProviderUnavailable
from app.modules.clinical_kg import service
from app.modules.clinical_kg.glossary import GLOSSARY
from app.modules.clinical_kg.graph import fact_id, get_graph, load


class TaskProvider:
    """Queued outputs per task (an exception instance is raised); other tasks use the mock fixtures."""

    mode = "mock"

    def __init__(self, outputs: dict[str, list]):
        self.outputs = outputs
        self.seen: dict[str, list[str]] = {}

    def complete_json(self, *, task, text, attempt=0, **_):
        self.seen.setdefault(task, []).append(text)
        queue = self.outputs.get(task)
        out = queue.pop(0) if queue else MOCK_FIXTURES[task](text, [], attempt)
        if isinstance(out, Exception):
            raise out
        return ProviderResult(data=out, model="m", input_tokens=10, output_tokens=5)


def ask(question: str, **kwargs) -> dict:
    return service.ask(get_store(), question, "Dr. Test", datetime.now(), **kwargs)


# --- graph and glossary -------------------------------------------------------------------------------------


def test_graph_loads_and_merges_duplicate_disease_lines(tmp_path):
    (tmp_path / "medical_kg_from_annotation.jsonl").write_text(
        '{"name": "甲病", "symptom": ["发热"]}\n{"name": "甲病", "symptom": ["发热", "咳嗽"], "check": ["胸片"]}\n'
        '{"name": "乙病", "symptom": ["咳嗽"], "cause": "受凉"}\n', encoding="utf-8")
    graph = load(tmp_path)
    assert graph.sources == ["medical_kg_from_annotation.jsonl"]
    assert [f.value for f in graph.disease_facts("甲病", ("symptom",))] == ["发热", "咳嗽"]
    assert sorted(graph.diseases_with("symptom", "咳嗽")) == ["乙病", "甲病"]
    assert graph.disease_facts("乙病", ("cause",))[0].value == "受凉"
    assert graph.disease_facts("甲病", ("check",))[0].id == fact_id("甲病", "check", "胸片")  # stable ids


def test_committed_graph_is_loaded():
    stats = get_graph().stats()
    assert stats["sources"][0] == "medical_kg_from_annotation.jsonl"
    assert stats["diseases"] > 3000 and stats["facts"] > 25000


def test_every_glossary_target_is_a_graph_node():
    nodes = set().union(*get_graph().nodes.values())
    assert [(en, zh) for en, zh in GLOSSARY.items() if zh not in nodes] == []


@pytest.mark.parametrize("term, name, match", [
    ("肺栓塞", "肺栓塞", "exact"),
    ("Pulmonary Embolism", "肺栓塞", "glossary"),
    ("多发性硬化", "多发性硬化症", "fuzzy"),
    ("interstitial lung disease", None, "none"),
])
def test_alignment(term, name, match):
    a = service.align(get_graph(), term)
    assert (a["name"], a["match"]) == (name, match)


# --- understanding and retrieval ----------------------------------------------------------------------------


def test_rule_parser_reads_disease_questions_in_both_languages():
    en = service.rule_parse(get_graph(), "What tests are used for pulmonary embolism?")
    zh = service.rule_parse(get_graph(), "肺栓塞需要做哪些检查？")
    for parsed, language in ((en, "en"), (zh, "zh")):
        assert parsed["language"] == language and parsed["intent"] == "disease_facts"
        assert parsed["relations"] == ["check"]
        assert [e["name_zh"] for e in parsed["entities"]] == ["肺栓塞"]


def test_symptom_question_ranks_diseases_by_matching_symptoms():
    graph = get_graph()
    parsed = service.rule_parse(graph, "What could cause fever, cough and dyspnea?")
    assert parsed["intent"] == "symptoms_to_diseases"
    facts, _ = service.retrieve(graph, parsed)
    first = facts[0].disease
    matched = {f.value for f in facts if f.disease == first and f.relation == "symptom"}
    assert matched == {"发热", "咳嗽", "呼吸困难"}
    assert len({f.disease for f in facts}) <= service.MAX_DISEASES


# --- answers ------------------------------------------------------------------------------------------------


def test_answer_cites_graph_facts():
    entry = ask("What tests are used for pulmonary embolism?")
    assert entry["found"] and entry["answer_status"] == "ok"
    fact_ids = {f["id"] for f in entry["facts"]}
    assert {f["value"] for f in entry["facts"]} >= {"胸部增强CT"}
    assert all(set(s["fact_ids"]) <= fact_ids for s in entry["statements"])
    assert "pulmonary embolism" in entry["statements"][0]["text"]
    assert service.log(get_store())[-1]["id"] == entry["id"]


def test_answer_with_unknown_fact_id_is_retried_then_falls_back_to_the_facts():
    bad = {"found": True, "statements": [{"text": "Invented.", "fact_ids": ["F-00000000"]}]}
    set_gateway(LlmGateway(TaskProvider({"clinical_kg_answer": [bad, bad]})))
    entry = ask("Symptoms of pneumonia")
    assert entry["answer_status"] == "needs_human" and entry["statements"] == []
    assert entry["facts"] and "could not be checked" in entry["answer"]


def test_ai_unavailable_still_answers_from_the_graph():
    down = ProviderUnavailable("timeout")
    set_gateway(LlmGateway(TaskProvider({"clinical_kg_parse": [down], "clinical_kg_answer": [down]})))
    entry = ask("肺栓塞需要做哪些检查")
    assert entry["parse_source"] == "rules" and entry["answer_status"] == "unavailable"
    assert {f["value"] for f in entry["facts"]} >= {"胸部增强CT"}


def test_nothing_in_graph_says_so_without_an_answer_call():
    provider = TaskProvider({})
    set_gateway(LlmGateway(provider))
    entry = ask("What is the weather today?")
    assert not entry["found"] and entry["answer"] == service.NOT_FOUND
    assert "clinical_kg_answer" not in provider.seen


# --- API ----------------------------------------------------------------------------------------------------


def test_front_desk_and_referrers_cannot_use_it(client, login):
    for user in ("U-FD", "U-REF"):
        r = client.post("/api/clinical-kg/ask", headers=login(user), json={"question": "Symptoms of pneumonia"})
        assert r.status_code == 403


def test_requisition_question_is_audited_and_redacted(client, login):
    provider = TaskProvider({})
    set_gateway(LlmGateway(provider))
    store = get_store()
    req = next(iter(store.requisitions.values()))
    patient = store.patients[req.patient_id]
    question = f"{patient.full_name}: chest pain and dyspnea, what could this be?"
    r = client.post("/api/clinical-kg/ask", headers=login("U-RAD"), json={"question": question, "requisition_id": req.id})
    assert r.status_code == 200 and r.json()["requisition_id"] == req.id
    assert all(patient.family_name not in t for texts in provider.seen.values() for t in texts)
    event = store.audit.events()[-1]
    assert (event.action, event.resource_id) == ("knowledge_query", req.id)
    assert client.get("/api/clinical-kg/history", headers=login("U-RAD")).json()["entries"][0]["question"] == question


def test_symptom_lookup_answers_per_disease_and_flags_a_single_broad_symptom():
    entry = ask("What could cause fever, cough and dyspnea?")
    assert entry["notes"] == []
    assert entry["statements"][0]["text"].count("lists") == 1  # one statement per disease
    single = ask("Bilateral leg weakness, what could this be?")
    assert single["intent"] == "symptoms_to_diseases" and "Only one symptom" in single["notes"][0]
