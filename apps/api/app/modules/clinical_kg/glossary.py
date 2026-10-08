"""English -> Chinese names for common clinical terms in requisitions.

The graph is in Chinese. Every target here must be a node in the graph
(checked by tests/test_clinical_kg.py), so the rule-based path can align
English questions without a model. The kg-qa sub-project's reviewable
terminology sample is merged in at load time, and so is the generated
English terminology for graph nodes (data/terminology_en.jsonl, see terms.py)
when it exists. The hand-written entries win on conflicts.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.modules.clinical_kg.graph import KG_DIR

GLOSSARY: dict[str, str] = {
    # symptoms and signs
    "headache": "头痛", "headaches": "头痛", "cough": "咳嗽", "fever": "发热", "dyspnea": "呼吸困难", "dyspnoea": "呼吸困难",
    "shortness of breath": "呼吸困难", "breathlessness": "呼吸困难", "weight loss": "体重减轻", "hematuria": "血尿",
    "haematuria": "血尿", "renal colic": "肾绞痛", "memory decline": "记忆力减退", "memory loss": "记忆力减退",
    "vertigo": "眩晕", "dizziness": "头晕", "sciatica": "坐骨神经痛", "low back pain": "腰痛", "back pain": "腰痛",
    "numbness": "麻木", "blurred vision": "视物模糊", "chest pain": "胸痛", "abdominal pain": "腹痛", "nausea": "恶心",
    "vomiting": "呕吐", "fatigue": "乏力", "jaundice": "黄疸", "edema": "水肿", "oedema": "水肿", "swelling": "水肿",
    "hemoptysis": "咯血", "syncope": "晕厥", "palpitations": "心悸", "lymphadenopathy": "淋巴结肿大",
    "hepatomegaly": "肝大", "splenomegaly": "脾大", "weakness": "无力", "confusion": "意识障碍", "coma": "昏迷",
    "diarrhea": "腹泻", "diarrhoea": "腹泻", "constipation": "便秘", "rash": "皮疹", "joint pain": "关节痛",
    "bone pain": "骨痛", "hearing loss": "听力下降", "sore throat": "咽痛", "wheezing": "喘息", "cyanosis": "发绀",
    "night sweats": "盗汗", "pleural effusion": "胸腔积液", "ascites": "腹水",
    "crackles": "肺部啰音", "rales": "肺部啰音", "sputum": "有痰", "productive cough": "有痰性咳嗽", "flank pain": "腰痛",
    # diseases
    "iron deficiency anemia": "缺铁性贫血", "anemia": "贫血", "anaemia": "贫血", "kidney stone": "肾结石",
    "kidney stones": "肾结石", "renal stone": "肾结石", "seizure": "癫痫", "seizures": "癫痫", "epilepsy": "癫痫",
    "optic neuritis": "视神经炎", "dementia": "痴呆", "gallstones": "胆石症", "gallstone": "胆石症", "pneumonia": "肺炎",
    "osteoarthritis": "骨关节炎", "goitre": "甲状腺肿", "goiter": "甲状腺肿", "deep vein thrombosis": "深静脉血栓",
    "dvt": "深静脉血栓", "pulmonary embolism": "肺栓塞", "hypertension": "高血压", "asthma": "哮喘", "lung cancer": "肺癌",
    "breast cancer": "乳腺癌", "melanoma": "黑色素瘤", "endometrial cancer": "子宫内膜癌", "pulmonary fibrosis": "肺纤维化",
    "fibrosis": "肺纤维化", "alzheimer's disease": "阿尔茨海默病", "alzheimer disease": "阿尔茨海默病", "diabetes": "糖尿病",
    "type 2 diabetes": "糖尿病", "stroke": "卒中", "heart failure": "心力衰竭", "copd": "慢性阻塞性肺疾病",
    "tuberculosis": "肺结核", "appendicitis": "阑尾炎", "pancreatitis": "急性胰腺炎", "cirrhosis": "肝硬化",
    "chronic kidney disease": "慢性肾脏病", "multiple sclerosis": "多发性硬化症", "hepatitis b": "乙型肝炎",
    "myocardial infarction": "心肌梗死", "heart attack": "心肌梗死", "atrial fibrillation": "心房颤动", "lymphoma": "淋巴瘤",
    "leukemia": "白血病", "leukaemia": "白血病", "liver cancer": "肝癌", "gastric cancer": "胃癌", "stomach cancer": "胃癌",
    "thyroid cancer": "甲状腺癌", "prostate cancer": "前列腺癌", "rectal cancer": "直肠癌",
    "urinary tract infection": "尿路感染", "sepsis": "脓毒症", "hypothyroidism": "甲状腺功能减退症",
    "hyperthyroidism": "甲状腺功能亢进症", "bronchitis": "支气管炎", "sinusitis": "鼻窦炎", "osteoporosis": "骨质疏松",
    "fracture": "骨折", "gout": "痛风", "rheumatoid arthritis": "类风湿关节炎", "lupus": "系统性红斑狼疮", "migraine": "偏头痛",
    "meningitis": "脑膜炎", "encephalitis": "脑炎", "hydrocephalus": "脑积水", "cerebral hemorrhage": "脑出血",
    "brain hemorrhage": "脑出血", "subarachnoid hemorrhage": "蛛网膜下腔出血", "pneumothorax": "气胸",
    # tests and exams
    "ct": "CT", "ct scan": "CT", "mri": "MRI", "ultrasound": "超声", "x-ray": "X线", "xray": "X线", "chest x-ray": "胸部X线",
    "cxr": "胸部X线", "chest ct": "胸部CT", "head ct": "头颅CT", "ct head": "头颅CT", "abdominal ultrasound": "腹部超声",
    "blood test": "血常规", "complete blood count": "血常规", "biopsy": "活检", "ecg": "心电图", "ekg": "心电图",
    "echocardiography": "超声心动图", "echocardiogram": "超声心动图", "bone scan": "骨扫描", "pet": "PET",
    "mammography": "乳腺X线", "mammogram": "乳腺X线", "colonoscopy": "结肠镜检查", "endoscopy": "胃镜",
    "lumbar puncture": "腰椎穿刺", "eeg": "脑电图", "angiography": "血管造影", "ct angiography": "CT血管造影",
}

TERMINOLOGY_FILE = KG_DIR.parent / "terminology" / "bilingual_medical_sample.json"
# One JSON object per line: {"name": zh node, "kind", "en", "aliases_en": [...], "confidence": high|medium|low}
GENERATED_FILE = Path(__file__).resolve().parent / "data" / "terminology_en.jsonl"


def norm(term: str) -> str:
    return " ".join(term.lower().replace("’", "'").split())


def generated_entries(path: Path | None = None) -> list[dict]:
    path = path or GENERATED_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def matching_aliases(entry: dict) -> list[str]:
    """English strings that may match this node: the preferred name and aliases. Low-confidence entries are
    display-only, and aliases shorter than 3 letters (ambiguous abbreviations) are never matched."""
    if entry.get("confidence") == "low" or not entry.get("en"):
        return []
    return [norm(a) for a in [entry["en"], *entry.get("aliases_en", [])] if len(re.sub(r"[^a-z]", "", a.lower())) >= 3]


@lru_cache(maxsize=1)
def glossary() -> dict[str, str]:
    """English (normalized lower case) -> Chinese graph name."""
    terms = {norm(k): v for k, v in GLOSSARY.items()}
    if TERMINOLOGY_FILE.exists():
        for entry in json.loads(TERMINOLOGY_FILE.read_text(encoding="utf-8")):
            for alias in entry.get("aliases_en", []):
                terms.setdefault(norm(alias), entry["canonical_name"])
    claims: dict[str, set[str]] = {}
    for entry in generated_entries():
        for alias in matching_aliases(entry):
            claims.setdefault(alias, set()).add(entry["name"])
    for alias, names in claims.items():
        if len(names) == 1:  # an alias claimed by two different nodes is ambiguous: skip it
            terms.setdefault(alias, next(iter(names)))
    return terms


@lru_cache(maxsize=1)
def english_names() -> dict[str, str]:
    """Chinese graph name -> one English name, for display next to the Chinese term."""
    out = {e["name"]: e["en"] for e in generated_entries() if e.get("en")}
    for en, zh in GLOSSARY.items():
        out.setdefault(zh, en)
    return out
