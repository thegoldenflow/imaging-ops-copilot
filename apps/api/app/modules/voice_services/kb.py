"""The call centre's FAQ knowledge base (spec 7.4: visiting hours, locations, parking) and its retrieval.

Files in `apps/api/knowledge/hospital_faq/` (format in its README): sections with a topic, English and Chinese
keywords and the answer in English, Simplified and Traditional Chinese. Retrieval scores the sections by the
keywords found in the caller's words (whole words in English, substrings in Chinese), favouring the topic the
intent step named; the answer is read out as written. No embeddings, no internet; the model never writes the
answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

FAQ_DIR = Path(__file__).resolve().parents[3] / "knowledge" / "hospital_faq"
TOPICS = ("visiting_hours", "location", "parking", "general")
LANGS = ("en", "zh-Hans", "zh-Hant")
CJK = re.compile(r"[一-鿿]")


@dataclass(frozen=True)
class Section:
    id: str  # file#section
    topic: str
    keywords: tuple[str, ...]
    answer: dict[str, str]  # lang -> text

    def snippet(self, lang: str) -> dict:
        return {"id": self.id, "topic": self.topic, "answer": self.answer.get(lang) or self.answer["en"]}


def _parse(path: Path) -> list[Section]:
    _, head, body = path.read_text(encoding="utf-8").split("---\n", 2)
    meta = {k.strip(): v.strip() for k, v in (line.split(":", 1) for line in head.strip().splitlines() if ":" in line)}
    out = []
    for chunk in body.split("\n## ")[1:]:
        lines = chunk.strip().splitlines()
        fields = {k.strip(): v.strip() for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)}
        answer = {lang: fields[lang] for lang in LANGS if fields.get(lang)}
        if fields.get("topic") not in TOPICS or "en" not in answer:
            raise ValueError(f"{path.name} {lines[0]}: needs a known topic and an English answer")
        out.append(Section(f"{meta['id']}#{lines[0].strip()}", fields["topic"],
                           tuple(k.strip().lower() for k in fields.get("keywords", "").split(",") if k.strip()),
                           answer))
    return out


@cache
def sections(folder: Path = FAQ_DIR) -> dict[str, Section]:
    found: dict[str, Section] = {}
    for path in sorted(folder.glob("*.md")):
        if path.name.lower() != "readme.md":
            found.update({s.id: s for s in _parse(path)})
    return found


def _matches(keyword: str, text: str) -> bool:
    if CJK.search(keyword):
        return keyword in text
    return re.search(r"(?<![a-z])" + re.escape(keyword) + r"(?![a-z])", text) is not None


def search(text: str, topic: str | None = None, *, limit: int = 3) -> list[tuple[Section, float]]:
    """Sections whose keywords appear in the caller's words, best first; the named topic breaks ties upward."""
    lowered = (text or "").lower()
    scored = []
    for s in sections().values():
        score = sum(1 + len(k) / 10 for k in s.keywords if _matches(k, lowered))
        if score and topic == s.topic:
            score += 1.5
        if score:
            scored.append((s, round(score, 2)))
    if not scored and topic in TOPICS:  # the model named a topic but no keyword matched: its first section
        first = next((s for s in sections().values() if s.topic == topic), None)
        scored = [(first, 0.5)] if first else []
    return sorted(scored, key=lambda x: (-x[1], x[0].id))[:limit]


def best(text: str, topic: str | None = None) -> Section | None:
    found = search(text, topic, limit=1)
    return found[0][0] if found else None
