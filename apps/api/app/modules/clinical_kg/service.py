"""Clinical knowledge Q&A over the in-memory medical knowledge graph.

Three steps, each usable without a model:
1. Understand: intent, relations and medical entities. The model maps terms to
   Chinese graph names (task `clinical_kg_parse`); the rule-based parser
   (glossary plus dictionary scan) is its mock fixture and its fallback.
2. Retrieve: fixed query templates over the graph, never model-written queries:
   facts about a disease, or diseases that list a set of symptoms.
3. Answer: the model writes statements that each cite graph fact ids (task
   `clinical_kg_answer`); the server rejects unknown ids. No facts -> a "not in
   the graph" reply without a model call; AI unavailable -> the facts are listed.

Staff-facing reference only, never a diagnosis; the phone agent still refuses
medical questions.
"""

import difflib
import re
from contextvars import ContextVar
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, model_validator

from app.core.models import Patient
from app.core.store import Store
from app.llm.gateway import get_gateway
from app.llm.prompts import Prompt
from app.llm.providers import mock_fixture
from app.modules.clinical_kg.glossary import english_names, glossary
from app.modules.clinical_kg.graph import LIST_RELATIONS, RELATION_LABELS, RELATIONS, Fact, Graph, get_graph

NOTICE = ("Reference only, not a diagnosis. Facts come from a research knowledge graph built from annotated Chinese "
          "medical literature (CMeIE); it is incomplete and not a clinical guideline.")
NOT_FOUND = "The knowledge graph has no facts for this question."
MAX_FACTS_PER_RELATION = 12
MAX_DISEASES = 8
CJK = re.compile(r"[一-鿿]")

Intent = Literal["disease_facts", "symptoms_to_diseases", "unsupported"]
Relation = Literal["symptom", "check", "drug", "treat", "acompany", "cause", "people"]
Kind = Literal["disease", "symptom", "check", "drug", "treat"]


def log(store: Store) -> list[dict]:
    return store.module("clinical_kg_log", list)


# ---------- Alignment ----------

def _all_names(graph: Graph) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {}
    for kind in ("disease", "symptom", "check", "drug", "treat"):
        for n in graph.nodes.get(kind, ()):
            names.setdefault(n, []).append(kind)
    return names


_NAMES: dict[int, dict[str, list[str]]] = {}


def names(graph: Graph) -> dict[str, list[str]]:
    key = id(graph)
    if key not in _NAMES:
        _NAMES.clear()
        _NAMES[key] = _all_names(graph)
    return _NAMES[key]


def align(graph: Graph, term: str) -> dict:
    """Term (English or Chinese) -> graph node: exact, glossary alias, then fuzzy."""
    index = names(graph)
    raw = term.strip()
    candidate = glossary().get(raw.lower(), raw)
    match = "exact" if candidate == raw else "glossary"
    if candidate in index:
        return {"text": raw, "name": candidate, "kinds": index[candidate], "match": match}
    if CJK.search(candidate) and len(candidate) >= 2:
        near = [n for n in index if len(n) >= 2 and (candidate in n or n in candidate)]
        near = sorted(near, key=lambda n: -difflib.SequenceMatcher(None, candidate, n).ratio())
        if near and difflib.SequenceMatcher(None, candidate, near[0]).ratio() >= 0.6:
            return {"text": raw, "name": near[0], "kinds": index[near[0]], "match": "fuzzy"}
    return {"text": raw, "name": None, "kinds": [], "match": "none"}


# ---------- Step 1: understand ----------

S2D = re.compile(r"\b(what could|which (disease|condition)s?|possible (disease|condition|cause)s?|differential|"
                 r"could (this|these|it) be|what (disease|condition)s?)\b|可能|什么病|哪些(疾病|病)|鉴别", re.I)
REL_WORDS = {
    "symptom": r"\bsymptoms?\b|\bpresentation\b|\bsigns?\b|症状|表现",
    "check": r"\b(tests?|exams?|examinations?|imaging|scans?|investigations?|work-?up|diagnos\w*)\b|检查|影像|诊断",
    "drug": r"\b(drugs?|medications?|medicines?)\b|药",
    "treat": r"\b(treat\w*|therap\w*|management|manage)\b|治疗",
    "acompany": r"\bcomplications?\b|并发",
    "cause": r"\b(causes?|risk factors?|etiology|aetiology)\b|病因|诱因|原因",
    "people": r"\b(who gets|population|age group)\b|人群",
}


def _scan_english(text: str) -> list[str]:
    found, lowered = [], text.lower()
    for term in sorted(glossary(), key=len, reverse=True):
        m = re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", lowered)
        if m:
            found.append(text[m.start():m.end()])
            lowered = lowered[:m.start()] + " " * len(term) + lowered[m.end():]
    return found


def _scan_chinese(graph: Graph, text: str) -> list[str]:
    index, found = names(graph), []
    for segment in re.findall(r"[一-鿿A-Za-z0-9-]+", text):
        if not CJK.search(segment):
            continue
        i = 0
        while i < len(segment):
            for size in range(min(12, len(segment) - i), 1, -1):
                if segment[i:i + size] in index:
                    found.append(segment[i:i + size])
                    i += size
                    break
            else:
                i += 1
    return found


def rule_parse(graph: Graph, question: str) -> dict:
    language = "zh" if CJK.search(question) else "en"
    terms = _scan_chinese(graph, question) + _scan_english(question)
    entities = []
    for term in dict.fromkeys(terms):
        a = align(graph, term)
        if a["name"]:
            kind = "disease" if "disease" in a["kinds"] else a["kinds"][0]
            if kind == "disease" and "symptom" in a["kinds"] and S2D.search(question):
                kind = "symptom"
            entities.append({"text": term, "name_zh": a["name"], "kind": kind})
    relations = [r for r, pattern in REL_WORDS.items() if re.search(pattern, question, re.I)]
    symptoms = [e for e in entities if e["kind"] == "symptom"]
    diseases = [e for e in entities if e["kind"] == "disease"]
    if S2D.search(question) and symptoms:
        intent = "symptoms_to_diseases"
    elif diseases:
        intent = "disease_facts"
    elif symptoms:
        intent = "symptoms_to_diseases"
    else:
        intent = "unsupported"
    return {"language": language, "intent": intent, "relations": relations, "entities": entities}


class Entity(BaseModel):
    text: str
    name_zh: str
    kind: Kind


class ParseOutput(BaseModel):
    language: Literal["en", "zh"]
    intent: Intent
    relations: list[Relation]
    entities: list[Entity]


PARSE_PROMPT = Prompt(
    name="clinical_kg_parse",
    version="clinical_kg_parse@1",
    system=(
        "You turn a clinician's question into a lookup in a Chinese medical knowledge graph. The graph links diseases "
        "to symptoms (symptom), tests and exams (check), drugs (drug), treatments (treat), complications (acompany), "
        "causes (cause) and affected groups (people). List every medical term in the question as an entity with its "
        "standard Simplified Chinese medical name and kind (disease, symptom, check, drug or treat). intent is "
        "disease_facts when the question asks about named diseases, symptoms_to_diseases when it gives symptoms or "
        "findings and asks which diseases fit, otherwise unsupported. relations are the relations the question asks "
        "for (empty for all). language is the language of the question. Do not answer the question."
    ),
    template="Question: {question}",
)


@mock_fixture("clinical_kg_parse")
def _mock_parse(text: str, images: list, attempt: int) -> dict:
    return rule_parse(get_graph(), text.split("Question: ", 1)[1].strip())


# ---------- Step 2: retrieve ----------

def retrieve(graph: Graph, parsed: dict) -> tuple[list[Fact], list[dict]]:
    """Returns (facts, aligned entities). Diseases from symptoms are ranked by symptoms matched."""
    aligned = []
    for e in parsed["entities"]:
        a = align(graph, e["name_zh"])
        if a["name"] is None and e["text"] != e["name_zh"]:
            a = align(graph, e["text"])
        aligned.append({"text": e["text"], "kind": e["kind"], "name": a["name"], "match": a["match"]})
    facts: list[Fact] = []
    if parsed["intent"] == "disease_facts":
        relations = tuple(r for r in RELATIONS if r in parsed["relations"]) or RELATIONS
        for a in [a for a in aligned if a["name"] and a["kind"] == "disease"][:3]:
            for relation in relations:
                facts += graph.disease_facts(a["name"], (relation,))[:MAX_FACTS_PER_RELATION]
    elif parsed["intent"] == "symptoms_to_diseases":
        symptoms = list(dict.fromkeys(a["name"] for a in aligned if a["name"] and a["kind"] == "symptom"))
        scores: dict[str, set[str]] = {}
        for s in symptoms:
            for disease in graph.diseases_with("symptom", s):
                scores.setdefault(disease, set()).add(s)
        # More matched symptoms first; among equals, the share of the disease's listed symptoms that matched
        # (at least 5 assumed, so a disease with one or two listed symptoms does not win), then better-described
        # diseases (more facts).
        def rank(d: str) -> tuple:
            listed = max(len(graph.by_disease[d].get("symptom", [])), 5)
            return -len(scores[d]), -len(scores[d]) / listed, -sum(map(len, graph.by_disease[d].values())), d

        ranked = sorted(scores, key=rank)
        for disease in ranked[:MAX_DISEASES]:
            facts += [f for f in graph.disease_facts(disease, ("symptom",)) if f.value in scores[disease]]
            facts += graph.disease_facts(disease, ("check",))[:3]
    return facts, aligned


# ---------- Step 3: answer ----------

_RETRIEVED: ContextVar[set[str]] = ContextVar("clinical_kg_retrieved", default=set())


class Statement(BaseModel):
    text: str
    fact_ids: list[str]


class AnswerOutput(BaseModel):
    found: bool
    statements: list[Statement]

    @model_validator(mode="after")
    def grounded(self):
        allowed = _RETRIEVED.get()
        for s in self.statements:
            if not s.fact_ids:
                raise ValueError("Every statement must cite at least one fact id")
            unknown = [f for f in s.fact_ids if f not in allowed]
            if unknown:
                raise ValueError(f"Unknown fact ids: {unknown}")
        if self.found and not self.statements:
            raise ValueError("An answer needs at least one statement")
        return self


ANSWER_PROMPT = Prompt(
    name="clinical_kg_answer",
    version="clinical_kg_answer@1",
    system=(
        "You answer a clinician's question using only the knowledge-graph facts provided. Each fact line is "
        "[fact id] disease | relation | value, in Chinese. Write short statements in the answer language; every "
        "statement lists the ids of the facts it relies on. Add nothing that is not in the facts: no doses, no "
        "advice, no ranking by likelihood beyond the number of matching symptoms given. In English, write each "
        "Chinese term in English followed by the Chinese in parentheses. If the facts do not answer the question, set "
        "found to false and give no statements. This is reference material for staff, not a diagnosis."
    ),
    template="Question: {question}\nAnswer language: {language}\nLookup: {lookup}\n\nFacts:\n{facts}",
)


def _en(name: str) -> str | None:
    return None if not CJK.search(name) else english_names().get(name)


def _term(name: str, language: str) -> str:
    en = _en(name)
    return f"{name} ({en})" if language == "en" and en else name


@mock_fixture("clinical_kg_answer")
def _mock_answer(text: str, images: list, attempt: int) -> dict:
    """Template baseline: one statement per disease and relation (per disease for symptom lookups)."""
    language = re.search(r"^Answer language: (\w+)", text, re.M).group(1)
    groups: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for fid, disease, relation, value in re.findall(r"^\[(F-\w+)\] (.+?) \| (\w+) \| (.+)$", text, re.M):
        groups.setdefault((disease, relation), []).append((fid, value))
    if text.split("Lookup: ", 1)[1].startswith("diseases"):
        return _mock_ranked(groups, language)
    statements = []
    for (disease, relation), items in groups.items():
        values = [v for _, v in items]
        if language == "zh":
            label = {"symptom": "症状", "check": "检查", "drug": "药物", "treat": "治疗", "acompany": "并发症",
                     "cause": "病因或诱因", "people": "多发人群"}[relation]
            line = f"图谱中{disease}的{label}：{'、'.join(values)}。"
        else:
            line = (f"The graph lists {PLURALS[relation]} of {_term(disease, 'en')}: "
                    f"{', '.join(_term(v, 'en') for v in values)}.")
        statements.append({"text": line, "fact_ids": [fid for fid, _ in items]})
    return {"found": bool(statements), "statements": statements}


PLURALS = {"symptom": "symptoms", "check": "tests and exams", "drug": "drugs", "treat": "treatments",
           "acompany": "complications", "cause": "causes or risk factors", "people": "affected groups"}


def _mock_ranked(groups: dict, language: str) -> dict:
    statements = []
    for disease in dict.fromkeys(d for d, _ in groups):
        symptoms, checks = groups.get((disease, "symptom"), []), groups.get((disease, "check"), [])
        if language == "zh":
            line = f"{disease}：列有症状{'、'.join(v for _, v in symptoms)}"
            line += f"；检查：{'、'.join(v for _, v in checks)}。" if checks else "。"
        else:
            line = f"{_term(disease, 'en')} lists {', '.join(_term(v, 'en') for _, v in symptoms)}"
            line += f"; tests listed: {', '.join(_term(v, 'en') for _, v in checks)}." if checks else "."
        statements.append({"text": line, "fact_ids": [fid for fid, _ in symptoms + checks]})
    return {"found": bool(statements), "statements": statements}


def _fact_view(f: Fact) -> dict:
    return f.as_dict() | {"disease_en": _en(f.disease), "value_en": _en(f.value)}


def ask(store: Store, question: str, by: str, now: datetime, patient: Patient | None = None,
        requisition_id: str | None = None) -> dict:
    graph = get_graph()
    gateway = get_gateway()
    patients = [patient] if patient else None
    question = question.strip()
    entry = {"id": store.next_id("KGQ"), "question": question, "asked_by": by, "requisition_id": requisition_id,
             "asked_at": now.isoformat(timespec="minutes"), "notice": NOTICE, "llm_call_ids": []}

    parsed_outcome = gateway.structured(task="clinical_kg_parse", prompt=PARSE_PROMPT, variables={"question": question},
                                        schema_cls=ParseOutput, tier="fast", patients=patients)
    entry["llm_call_ids"].append(parsed_outcome.call_id)
    if parsed_outcome.status == "ok":
        parsed, entry["parse_source"] = parsed_outcome.data, "ai"
    else:
        parsed, entry["parse_source"] = rule_parse(graph, question), "rules"
    facts, aligned = retrieve(graph, parsed)
    symptoms = sorted({a["name"] for a in aligned if a["name"] and a["kind"] == "symptom"})
    if parsed["intent"] == "symptoms_to_diseases":
        lookup = f"diseases ranked by how many of these symptoms they list: {', '.join(symptoms)}"
    else:
        lookup = f"facts about {', '.join(a['name'] for a in aligned if a['name'] and a['kind'] == 'disease')}"
    entry["notes"] = (["Only one symptom matched the graph, so the list of diseases is broad. Add more findings to "
                       "narrow it."] if parsed["intent"] == "symptoms_to_diseases" and len(symptoms) == 1 else [])
    entry |= {"language": parsed["language"], "intent": parsed["intent"], "relations": parsed["relations"],
              "entities": aligned, "facts": [_fact_view(f) for f in facts]}

    if not facts:
        entry |= {"found": False, "answer_status": "no_match", "statements": [], "answer": NOT_FOUND,
                  "model": None, "prompt_version": None}
    else:
        lines = "\n".join(f"[{f.id}] {f.disease} | {f.relation} | {f.value}" for f in facts)
        token = _RETRIEVED.set({f.id for f in facts})
        try:
            outcome = gateway.structured(task="clinical_kg_answer", prompt=ANSWER_PROMPT,
                                         variables={"question": question, "language": parsed["language"],
                                                    "lookup": lookup, "facts": lines},
                                         schema_cls=AnswerOutput, tier="reasoning", patients=patients)
        finally:
            _RETRIEVED.reset(token)
        entry["llm_call_ids"].append(outcome.call_id)
        entry |= {"answer_status": outcome.status, "model": outcome.model, "prompt_version": outcome.prompt_version}
        if outcome.status == "ok" and outcome.data["found"]:
            entry |= {"found": True, "statements": outcome.data["statements"], "answer": None}
        elif outcome.status == "ok":
            entry |= {"found": False, "statements": [], "answer": NOT_FOUND}
        else:
            entry |= {"found": True, "statements": [],
                      "answer": "AI is unavailable or its answer could not be checked against the graph. "
                                "The matching graph facts are listed below."}
    entry["mode"] = gateway.mode
    log(store).append(entry)
    store.touch()
    return entry


def stats() -> dict:
    return get_graph().stats() | {"notice": NOTICE, "relations": RELATION_LABELS, "list_relations": list(LIST_RELATIONS)}
