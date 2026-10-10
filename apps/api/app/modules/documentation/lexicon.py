"""Names the validator recognises: drugs, conditions and procedures, with synonyms (spec 7.3, "codes first, then a
fuzzy name match").

- Drug lexicon: the EHR's medications (app/ehr/codes.py MEDICATIONS) with a few common synonyms, plus the bilingual
  knowledge graph's drug names (app/modules/clinical_kg, high confidence, single substances only: classes such as
  "macrolides" or "immunosuppressants" and food words such as vitamins are left out, so advice like "foods rich in
  vitamin K" is not taken for a prescription).
- Condition and procedure lexicons: the EHR's SNOMED CT concepts (CONDITIONS, OR_PROCEDURES, OTHER_PROCEDURES) with
  plain-language synonyms. Free text is scanned with these; the knowledge graph's thousands of disease names are not
  used for the scan (too many ordinary words), only to widen synonyms.

Matching (`same_name`): equal after normalising, one name's words inside the other's (a dose or form around the
drug name), or a difflib ratio of at least 0.85 (spelling variants). Pure functions, cached.
"""

from __future__ import annotations

import difflib
import re
from functools import lru_cache

from app.ehr import codes as C

WORD = re.compile(r"[a-z0-9]+(?:['-][a-z0-9]+)*")
FUZZY = 0.85

MED_SYNONYMS: dict[str, list[str]] = {
    "161": ["paracetamol", "tylenol"], "435": ["albuterol", "ventolin"], "11289": ["coumadin"],
    "4603": ["lasix"], "1191": ["acetylsalicylic acid", "asa"], "274783": ["lantus", "glargine"],
    "10582": ["synthroid", "l-thyroxine"], "1364430": ["eliquis"], "1114195": ["xarelto"], "1037042": ["pradaxa"],
    "67108": ["lovenox"], "8591": ["kcl"], "10180": ["co-trimoxazole", "trimethoprim-sulfamethoxazole"],
    "2193": ["rocephin"], "723": ["amoxil"], "26225": ["zofran"], "40790": ["pantoloc"],
}
CONDITION_SYNONYMS: dict[str, list[str]] = {
    "59621000": ["hypertension", "high blood pressure"],
    "44054006": ["type 2 diabetes", "diabetes", "diabetes mellitus"],
    "49436004": ["af", "afib", "a fib"],
    "42343007": ["heart failure", "chf", "cardiac failure"],
    "13645005": ["copd", "chronic obstructive pulmonary disease"],
    "433144002": ["chronic kidney disease", "ckd", "ckd stage 3"],
    "431857002": ["chronic kidney disease", "ckd", "ckd stage 4"],
    "55822004": ["high cholesterol", "dyslipidemia"],
    "35489007": ["depression"],
    "26929004": ["alzheimer disease", "dementia"],
    "233604007": ["community-acquired pneumonia", "lung infection"],
    "22298006": ["heart attack", "mi", "nstemi", "stemi"],
    "230690007": ["stroke", "cva"],
    "5913000": ["hip fracture", "fractured hip", "broken hip", "femoral neck fracture", "fracture of the femoral neck"],
    "68566005": ["urinary tract infection", "uti"],
    "195951007": ["copd exacerbation", "aecopd", "exacerbation of copd"],
    "65275009": ["cholecystitis", "gallbladder infection"],
    "128053003": ["deep vein thrombosis", "dvt", "blood clot in the leg"],
    "59282003": ["pe", "blood clot in the lung"],
    "125605004": ["fracture", "broken bone"],
    "267036007": ["shortness of breath", "breathlessness"],
    "161891005": ["back pain"],
    "386661006": ["pyrexia"],
    "1912002": ["fall at home"],
    "271737000": ["anaemia"],
    "396230008": ["skin infection"],
}
PROCEDURE_SYNONYMS: dict[str, list[str]] = {
    "52734007": ["hip replacement", "total hip arthroplasty", "tha"],
    "609588000": ["knee replacement", "total knee arthroplasty", "tka"],
    "80146002": ["appendectomy", "appendicectomy"],
    "45595009": ["lap chole", "cholecystectomy"],
    "44558001": ["hernia repair", "inguinal hernia repair"],
    "23968004": ["bowel resection"],
    "232717009": ["cabg", "bypass surgery", "coronary bypass"],
    "18286008": ["catheter ablation", "ablation", "af ablation", "cardiac ablation"],
    "415070008": ["pci", "angioplasty", "coronary stent"],
    "73761001": ["colonoscopy"],
    "16310003": ["hemiarthroplasty", "hip hemiarthroplasty"],
}
# Extra procedures the documentation demo data uses (SNOMED CT): open reduction and internal fixation of a hip
EXTRA_PROCEDURES = {"16310003": "Hemiarthroplasty of hip"}
_CLASSY = re.compile(r"(drugs|inhibitors|agents|therapy|antagonists|agonists|supplements|analogs|analogues|"
                     r"blockers|antibiotics|hormones|steroids|vaccines|antivenom|antitoxin|preparations|medications|"
                     r"products|solution|regimen)\b")
_FOOD = ("vitamin", "oxygen", "saline", "water", "alcohol", "calcium", "iron", "zinc", "fluid", "blood", "plasma",
         "glucose", "nutrition", "diet")


def norm(text: str) -> str:
    return " ".join(WORD.findall((text or "").lower().replace("’", "'")))


def tokens(text: str) -> list[str]:
    return WORD.findall((text or "").lower())


def same_name(a: str, b: str) -> bool:
    """Whether two names name the same thing (after normalising; containment; difflib >= 0.85)."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    ta, tb = set(na.split()), set(nb.split())
    significant = {t for t in ta & tb if len(t) >= 4 and not t.isdigit()}
    if significant and (ta <= tb or tb <= ta):
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= FUZZY


@lru_cache(maxsize=1)
def _kg_entries() -> list[dict]:
    try:
        from app.modules.clinical_kg.glossary import generated_entries
        return generated_entries()
    except Exception:  # the knowledge graph is optional for the validator
        return []


@lru_cache(maxsize=1)
def drug_lexicon() -> dict[str, str]:
    """Normalised drug name -> key (`rxcui:<code>` for the EHR's medications, `kg:<name>` for the graph's)."""
    out: dict[str, str] = {}
    for rxcui, (name, *_rest) in C.MEDICATIONS.items():
        out[norm(name)] = f"rxcui:{rxcui}"
        for alias in MED_SYNONYMS.get(rxcui, []):
            out.setdefault(norm(alias), f"rxcui:{rxcui}")
    for e in _kg_entries():
        if e.get("kind") != "drug" or e.get("confidence") != "high":
            continue
        for name in [e.get("en") or "", *(e.get("aliases_en") or [])]:
            n = norm(name)
            if len(n) < 5 or len(n.split()) > 3 or _CLASSY.search(n) or any(f in n for f in _FOOD):
                continue
            out.setdefault(n, f"kg:{e['name']}")
    return out


# Admission complaints the EHR codes as conditions; warning signs and courses mention them without diagnosing them,
# so free text is not scanned for them (an item naming one as a diagnosis is still matched).
SYMPTOM_CODES = {"29857009", "21522001", "267036007", "161891005", "25064002", "62315008", "386661006", "1912002"}


@lru_cache(maxsize=1)
def condition_lexicon() -> dict[str, str]:
    out: dict[str, str] = {}
    for code, (display, _chronic) in C.CONDITIONS.items():
        if code in SYMPTOM_CODES:
            continue
        out[norm(display)] = code
        for alias in CONDITION_SYNONYMS.get(code, []):
            if len(norm(alias)) >= 4:  # two- and three-letter abbreviations only for matching names, not scanning
                out.setdefault(norm(alias), code)
    return out


@lru_cache(maxsize=1)
def procedure_lexicon() -> dict[str, str]:
    out: dict[str, str] = {}
    names = {**{c: v[0] for c, v in C.OR_PROCEDURES.items()}, **C.OTHER_PROCEDURES, **EXTRA_PROCEDURES}
    for code, display in names.items():
        out[norm(display)] = code
        for alias in PROCEDURE_SYNONYMS.get(code, []):
            if len(norm(alias)) >= 5:
                out.setdefault(norm(alias), code)
    return out


def synonyms(kind: str, code: str) -> list[str]:
    """Every known name of a coded concept (for matching an item's name against an input resource)."""
    if kind == "medication":
        name = C.MEDICATIONS.get(code, (None,))[0]
        return [n for n in [name, *MED_SYNONYMS.get(code, [])] if n]
    if kind == "condition":
        display = C.CONDITIONS.get(code, (None,))[0]
        return [n for n in [display, *CONDITION_SYNONYMS.get(code, [])] if n]
    if kind == "procedure":
        display = (C.OR_PROCEDURES.get(code) or (None,))[0] or C.OTHER_PROCEDURES.get(code) or \
            EXTRA_PROCEDURES.get(code)
        return [n for n in [display, *PROCEDURE_SYNONYMS.get(code, [])] if n]
    return []


def find_mentions(text: str, lexicon: dict[str, str], *, max_words: int = 4) -> list[tuple[str, str]]:
    """(phrase, key) for every lexicon phrase in the text, longest match first, word-bounded."""
    words = tokens(text)
    out, i = [], 0
    while i < len(words):
        for n in range(min(max_words, len(words) - i), 0, -1):
            phrase = " ".join(words[i:i + n])
            if phrase in lexicon:
                out.append((phrase, lexicon[phrase]))
                i += n
                break
        else:
            i += 1
    return out
