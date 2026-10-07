"""In-memory medical knowledge graph for clinical knowledge Q&A.

Read-only from the apps/kg-qa sub-project's data (that project is not changed):
the committed annotation graph `medical_kg_from_annotation.jsonl`, plus the
larger `medical_kg.jsonl` when it has been placed there locally. Both use the
same line format: one disease per line with relation lists (`symptom`,
`check`, `drug`, `treat`, `acompany`) and short text properties (`cause`,
`people`, ...). A disease can appear on many lines; they are merged.

Every relation edge becomes a fact with a stable id, so answers can cite the
exact edges they rely on.
"""

import hashlib
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

KG_DIR = Path(os.getenv("CLINICAL_KG_DIR") or Path(__file__).resolve().parents[4] / "kg-qa" / "data" / "knowledge_graph")
KG_FILES = ("medical_kg_from_annotation.jsonl", "medical_kg.jsonl")

# Relation lists: disease -> target nodes. "acompany" (sic, the source field name) is complications.
LIST_RELATIONS = ("symptom", "check", "drug", "treat", "acompany")
# Short text properties shown as facts too.
TEXT_RELATIONS = ("cause", "people")
RELATIONS = LIST_RELATIONS + TEXT_RELATIONS
NODE_KINDS = ("disease", "symptom", "check", "drug", "treat")

RELATION_LABELS = {
    "symptom": "symptom", "check": "test or exam", "drug": "drug", "treat": "treatment",
    "acompany": "complication", "cause": "cause or risk factor", "people": "affected group",
}


@dataclass(frozen=True)
class Fact:
    id: str
    disease: str
    relation: str
    value: str

    def as_dict(self) -> dict:
        return {"id": self.id, "disease": self.disease, "relation": self.relation,
                "relation_label": RELATION_LABELS[self.relation], "value": self.value}


def fact_id(disease: str, relation: str, value: str) -> str:
    return "F-" + hashlib.sha1(f"{disease}|{relation}|{value}".encode()).hexdigest()[:8]


@dataclass
class Graph:
    sources: list[str] = field(default_factory=list)
    facts: dict[str, Fact] = field(default_factory=dict)
    # disease -> relation -> ordered fact ids
    by_disease: dict[str, dict[str, list[str]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(list)))
    # (relation, target value) -> fact ids, for reverse lookups such as symptom -> diseases
    by_target: dict[tuple[str, str], list[str]] = field(default_factory=lambda: defaultdict(list))
    # node kind -> names
    nodes: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add(self, disease: str, relation: str, value: str) -> None:
        value = value.strip()
        if not value or value == disease:
            return
        fid = fact_id(disease, relation, value)
        if fid in self.facts:
            return
        self.facts[fid] = Fact(fid, disease, relation, value)
        self.by_disease[disease][relation].append(fid)
        if relation in LIST_RELATIONS:
            self.by_target[(relation, value)].append(fid)
            kind = "disease" if relation == "acompany" else relation
            self.nodes[kind].add(value)

    def disease_facts(self, disease: str, relations: tuple[str, ...] = RELATIONS) -> list[Fact]:
        rels = self.by_disease.get(disease, {})
        return [self.facts[f] for r in relations for f in rels.get(r, [])]

    def diseases_with(self, relation: str, value: str) -> list[str]:
        return [self.facts[f].disease for f in self.by_target.get((relation, value), [])]

    def stats(self) -> dict:
        return {"sources": self.sources, "diseases": len(self.by_disease), "facts": len(self.facts),
                **{f"{k}_nodes": len(v) for k, v in self.nodes.items() if k != "disease"}}


def load(directory: Path = KG_DIR) -> Graph:
    graph = Graph()
    for name in KG_FILES:
        path = directory / name
        if not path.exists():
            continue
        graph.sources.append(name)
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                disease = str(row.get("name", "")).strip()
                if not disease:
                    continue
                graph.nodes["disease"].add(disease)
                for relation in RELATIONS:
                    value = row.get(relation)
                    for v in (value if isinstance(value, list) else [value] if value else []):
                        graph.add(disease, relation, str(v))
    return graph


@lru_cache(maxsize=1)
def get_graph() -> Graph:
    return load()
