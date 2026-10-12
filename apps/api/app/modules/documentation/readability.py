"""Readability checks of the patient instructions (spec 7.3).

- English: Flesch-Kincaid grade level, 0.39 * words/sentence + 11.8 * syllables/word - 15.59, with a syllable
  heuristic (vowel groups, a silent final "e", "-le" endings). Target grade 6–8: the check passes at grade 8 or
  below (easier text than grade 6 is fine for patients, and reported as "below"); above 8 is a warning. No
  readability library is installed; the standard library does it.
- Chinese: no medical abbreviations (no Latin letters at all in the Chinese text: INR, NSAID, mg are written out),
  and every medical term explained the first time it appears (render.py records which terms got an explanation).
"""

from __future__ import annotations

import re

SENTENCE = re.compile(r"[^.!?]+[.!?]")
WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
VOWELS = re.compile(r"[aeiouy]+")
LATIN = re.compile(r"[A-Za-z]+")
TARGET = (6.0, 8.0)


def syllables(word: str) -> int:
    w = word.lower().strip("'")
    if len(w) <= 3:
        return 1
    if w.endswith("e") and not w.endswith(("le", "ee", "ye")):
        w = w[:-1]
    count = len(VOWELS.findall(w))
    if w.endswith("ed") and not w.endswith(("ted", "ded")):
        count -= 1
    return max(1, count)


def fk_grade(text: str) -> dict:
    """Flesch-Kincaid grade of English text (sentences end with . ! or ?)."""
    text = re.sub(r"(?<![.!?:])\n", ". ", text.strip() + "\n").replace("\n", " ")  # a heading is a sentence too
    sentences = [s for s in SENTENCE.findall(text) if WORD.search(s)]
    words = [w for s in sentences for w in WORD.findall(s)]
    if not sentences or not words:
        return {"grade": 0.0, "sentences": 0, "words": 0, "syllables": 0, "in_target": False}
    sylls = sum(syllables(w) for w in words)
    grade = 0.39 * len(words) / len(sentences) + 11.8 * sylls / len(words) - 15.59
    hardest = sorted(sentences, key=lambda s: -sum(syllables(w) for w in WORD.findall(s)) / max(1, len(WORD.findall(s))))
    band = "below" if grade < TARGET[0] else "within" if grade <= TARGET[1] else "above"
    return {"grade": round(grade, 1), "sentences": len(sentences), "words": len(words), "syllables": sylls,
            "in_target": grade <= TARGET[1], "band": band, "target": list(TARGET),
            "hardest": [s.strip() for s in hardest[:3]]}


def chinese_checks(text: str, terms: list[dict]) -> dict:
    """Latin letters (abbreviations, units) in the Chinese text, and terms without an explanation at first use."""
    latin = sorted(set(LATIN.findall(text)))
    unexplained = [t["key"] for t in terms if not t.get("explained")]
    return {"abbreviations": latin, "no_abbreviations": not latin, "terms": len(terms),
            "unexplained_terms": unexplained, "terms_explained": not unexplained,
            "from_knowledge_graph": sum(1 for t in terms if t.get("source") == "knowledge_graph")}
