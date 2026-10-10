"""Retrieval over the synthetic guideline summaries in `apps/api/knowledge/guidelines/` (spec 7.2's
`knowledge/guidelines/`): keyword scoring, no embeddings, no internet.

A section's id is `<file id>#<section id>` (e.g. `anticoagulation#nsaids-antiplatelets`); the agents may cite
only sections they were given, and `resolve` tells whether a cited id exists.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

GUIDELINES_DIR = Path(__file__).resolve().parents[3] / "knowledge" / "guidelines"
_WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
STOP = frozenset("a an and are as at be by for from in is it its of on or the to with without".split())


@dataclass(frozen=True)
class Section:
    id: str  # file#section
    title: str
    keywords: tuple[str, ...]
    text: str
    document: str  # the file's title
    source: str

    def snippet(self) -> dict:
        return {"id": self.id, "title": self.title, "text": self.text}


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in STOP]


def _parse(path: Path) -> list[Section]:
    raw = path.read_text(encoding="utf-8")
    _, head, body = raw.split("---\n", 2)
    meta = dict(line.split(":", 1) for line in head.strip().splitlines() if ":" in line)
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    out = []
    for chunk in body.split("\n## ")[1:]:
        lines = chunk.strip().splitlines()
        section_id = lines[0].strip()
        fields = {"title": "", "keywords": ""}
        text_lines = []
        for line in lines[1:]:
            key = line.split(":", 1)[0]
            if key in fields and not fields[key] and ":" in line:
                fields[key] = line.split(":", 1)[1].strip()
            elif line.strip():
                text_lines.append(line.strip())
        out.append(Section(id=f"{meta['id']}#{section_id}", title=fields["title"],
                           keywords=tuple(k.strip().lower() for k in fields["keywords"].split(",") if k.strip()),
                           text=" ".join(text_lines), document=meta.get("title", ""), source=meta.get("source", "")))
    return out


@cache
def sections(folder: Path = GUIDELINES_DIR) -> dict[str, Section]:
    found: dict[str, Section] = {}
    for path in sorted(folder.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        for s in _parse(path):
            found[s.id] = s
    return found


def resolve(section_id: str) -> Section | None:
    return sections().get(section_id)


@cache
def _idf() -> dict[str, float]:
    docs = list(sections().values())
    counts: dict[str, int] = {}
    for s in docs:
        for w in set(_words(" ".join(s.keywords) + " " + s.title + " " + s.text)):
            counts[w] = counts.get(w, 0) + 1
    return {w: math.log(1 + len(docs) / c) for w, c in counts.items()}


def search(terms: list[str], *, context: list[str] | tuple[str, ...] = (), limit: int = 4) -> list[Section]:
    """The sections that best match the query: `terms` (the order's drug, class, the rule findings) weigh three
    times as much as `context` (the patient's other medications and diagnoses). A keyword hit counts three times
    a word in the text; rarer words count more."""
    idf = _idf()
    weights: dict[str, float] = {}
    for t in context:
        for w in _words(t):
            weights[w] = max(weights.get(w, 0), 1.0)
    for t in terms:
        for w in _words(t):
            weights[w] = 3.0
    phrases = {t.lower().strip() for t in terms if " " in t.strip()}
    scored = []
    for s in sections().values():
        keyword_words = {w for k in s.keywords for w in _words(k)}
        text_words = _words(s.title + " " + s.text)
        score = sum(weight * idf.get(w, 0) * (3 * (w in keyword_words) + min(2, text_words.count(w)))
                    for w, weight in weights.items())
        score += sum(6.0 for p in phrases if p in s.keywords)
        if score > 0:
            scored.append((score, s.id, s))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s for _, _, s in scored[:limit]]
