"""English terminology for the knowledge graph's Chinese node names.

    uv run python -m app.modules.clinical_kg.terms export        # data/terms_to_translate.jsonl
    uv run python -m app.modules.clinical_kg.terms export --all  # also nodes used only once
    uv run python -m app.modules.clinical_kg.terms check         # validate data/terminology_en.jsonl

Export lists the names still missing from data/terminology_en.jsonl, most useful
first (every disease, then symptoms, tests, drugs and treatments by how many
diseases use them), each with a little graph context for the translator. The
translations go into data/terminology_en.jsonl (one JSON object per line):

    {"name": "肺栓塞", "kind": "disease", "en": "pulmonary embolism",
     "aliases_en": ["pulmonary thromboembolism"], "confidence": "high"}

glossary.py merges that file into English matching and display.
"""

import json
import re
import sys
from collections import Counter

from app.modules.clinical_kg.glossary import GENERATED_FILE, generated_entries, matching_aliases
from app.modules.clinical_kg.graph import NODE_KINDS, Graph, get_graph

EXPORT_FILE = GENERATED_FILE.parent / "terms_to_translate.jsonl"
MAX_NAME_LENGTH = 12  # longer values are descriptive phrases, not terms anyone types


def _uses(graph: Graph) -> Counter:
    uses: Counter = Counter()
    for (relation, value), ids in graph.by_target.items():
        uses[("disease" if relation == "acompany" else relation, value)] += len(ids)
    return uses


def _context(graph: Graph, kind: str, name: str) -> str:
    if kind == "disease":
        facts = graph.disease_facts(name, ("symptom", "check"))[:4]
        return "; ".join(f"{f.relation}: {f.value}" for f in facts)
    relation = "acompany" if kind == "disease" else kind
    return "used by: " + ", ".join(graph.diseases_with(relation, name)[:3])


def export(include_all: bool = False) -> int:
    graph, uses = get_graph(), _uses(get_graph())
    done = {e["name"] for e in generated_entries()}
    rows = []
    for kind in NODE_KINDS:
        for name in graph.nodes.get(kind, ()):
            if name in done or len(name) > MAX_NAME_LENGTH or not re.search(r"[一-鿿]", name):
                continue
            count = uses[(kind, name)] + (len(graph.disease_facts(name)) if kind == "disease" else 0)
            priority = 1 if kind == "disease" or uses[(kind, name)] >= 2 else 2
            if priority == 2 and not include_all:
                continue
            rows.append({"name": name, "kind": kind, "priority": priority, "uses": count,
                         "context": _context(graph, kind, name)})
    seen: set[str] = set()
    rows = [r for r in sorted(rows, key=lambda r: (r["priority"], NODE_KINDS.index(r["kind"]), -r["uses"], r["name"]))
            if not (r["name"] in seen or seen.add(r["name"]))]  # a name that is two kinds is listed once
    EXPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_FILE.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"Wrote {len(rows)} names to {EXPORT_FILE} ({len(done)} already translated)")
    return len(rows)


def check(path=None) -> list[str]:
    """Problems in the generated terminology; an empty list means it is usable."""
    graph = get_graph()
    nodes = set().union(*graph.nodes.values())
    problems, seen, claims = [], set(), {}
    for i, e in enumerate(generated_entries(path), 1):
        name = e.get("name")
        if name not in nodes:
            problems.append(f"line {i}: {name!r} is not a graph node")
        if name in seen:
            problems.append(f"line {i}: {name!r} appears twice")
        seen.add(name)
        en = e.get("en") or ""
        if not en.strip():
            problems.append(f"line {i}: {name!r} has no English name")
        for text in [en, *e.get("aliases_en", [])]:
            if re.search(r"[一-鿿]", text):
                problems.append(f"line {i}: {name!r} English text contains Chinese: {text!r}")
        if e.get("confidence") not in ("high", "medium", "low"):
            problems.append(f"line {i}: {name!r} confidence must be high, medium or low")
        for alias in matching_aliases(e):
            claims.setdefault(alias, set()).add(name)
    for alias, names in sorted(claims.items()):
        if len(names) > 1:
            problems.append(f"alias {alias!r} is claimed by {sorted(names)} (it will not be matched)")
    return problems


def main(args: list[str]) -> None:
    if args[:1] == ["export"]:
        export(include_all="--all" in args)
    elif args[:1] == ["check"]:
        entries = generated_entries()
        problems = check()
        print("\n".join(problems[:200]))
        print(f"{len(entries)} entries, {len(problems)} problems")
        sys.exit(1 if any("alias" not in p for p in problems) else 0)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
