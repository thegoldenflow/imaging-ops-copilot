"""Medical terms in the patient instructions and their explanations from the bilingual knowledge graph (spec 7.3).

- Names: a condition or procedure gets its plain-language name in every language
  (CONDITION_LAY / PROCEDURE_LAY); a medicine its English generic name and its
  Chinese name from the knowledge graph's terminology (app/modules/clinical_kg:
  English -> Chinese node, 6,382 nodes), else from DRUG_ZH.
- Explanations (the first time a term appears in Chinese, and in the English "Words
  to know"): built from the graph's facts when they describe the term plainly. A
  medicine: the patient's own diagnoses it is used for, per the graph; a disease: its
  typical signs (plain signs among the graph's first facts for it, at least one of
  them a cardinal sign such as fever, cough or chest pain). Only facts with an
  English name and a plain Chinese name (no Latin letters, short) are used, so both
  languages say the same thing. The graph's facts are noisy (hypertension lists
  "abdominal pain", sepsis "vomiting", amoxicillin "COPD"), so the common conditions
  have reviewed plain-language explanations in CURATED that take precedence, and a
  medicine the graph does not link to the patient's diagnoses is explained by its
  class (both marked source "curated"); the eval reports how many came from the graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.ehr import codes as C
from app.modules.documentation.hant import to_hant
from app.modules.documentation.lexicon import CONDITION_SYNONYMS, MED_SYNONYMS, norm
from app.modules.documentation.phrases import Phrase, p

CONDITION_LAY: dict[str, Phrase] = {
    "59621000": p("high blood pressure", "高血压", "高血壓"),
    "44054006": p("type 2 diabetes", "2型糖尿病", "第2型糖尿病"),
    "49436004": p("an irregular heartbeat (atrial fibrillation)", "心房颤动", "心房顫動"),
    "42343007": p("heart failure", "心力衰竭", "心臟衰竭"),
    "13645005": p("a long-term lung disease (COPD)", "慢性阻塞性肺疾病", "慢性阻塞性肺病"),
    "433144002": p("kidney disease (stage 3)", "慢性肾脏病（3期）", "慢性腎臟病（3期）"),
    "431857002": p("kidney disease (stage 4)", "慢性肾脏病（4期）", "慢性腎臟病（4期）"),
    "195967001": p("asthma", "哮喘", "氣喘"),
    "55822004": p("high cholesterol", "高脂血症", "高血脂症"),
    "35489007": p("depression", "抑郁症", "憂鬱症"),
    "64859006": p("weak bones (osteoporosis)", "骨质疏松", "骨質疏鬆"),
    "396275006": p("worn joints (osteoarthritis)", "骨关节炎", "退化性關節炎"),
    "69896004": p("rheumatoid arthritis", "类风湿关节炎", "類風濕性關節炎"),
    "26929004": p("Alzheimer's disease", "阿尔茨海默病", "阿茲海默症"),
    "40930008": p("a slow thyroid", "甲状腺功能减退", "甲狀腺功能低下"),
    "90560007": p("gout", "痛风", "痛風"),
    "233604007": p("a lung infection (pneumonia)", "肺炎", "肺炎"),
    "22298006": p("a heart attack", "心肌梗死", "心肌梗塞"),
    "230690007": p("a stroke", "中风", "中風"),
    "5913000": p("a broken hip", "髋部骨折", "髖部骨折"),
    "68566005": p("a bladder or kidney infection", "尿路感染", "泌尿道感染"),
    "91302008": p("a severe infection (sepsis)", "脓毒症", "敗血症"),
    "195951007": p("a flare-up of your lung disease", "慢性阻塞性肺疾病急性加重", "慢性阻塞性肺病急性惡化"),
    "74400008": p("appendicitis", "阑尾炎", "闌尾炎"),
    "65275009": p("a swollen gallbladder", "急性胆囊炎", "急性膽囊炎"),
    "128053003": p("a blood clot in a leg vein", "深静脉血栓", "深部靜脈血栓"),
    "59282003": p("a blood clot in the lung", "肺栓塞", "肺栓塞"),
    "29857009": p("chest pain", "胸痛", "胸痛"),
    "21522001": p("belly pain", "腹痛", "腹痛"),
    "267036007": p("shortness of breath", "呼吸困难", "呼吸困難"),
    "161891005": p("back pain", "背痛", "背痛"),
    "25064002": p("headache", "头痛", "頭痛"),
    "125605004": p("a broken bone", "骨折", "骨折"),
    "10509002": p("a chest cold (bronchitis)", "急性支气管炎", "急性支氣管炎"),
    "444814009": p("a sinus infection", "病毒性鼻窦炎", "病毒性鼻竇炎"),
    "62315008": p("diarrhea", "腹泻", "腹瀉"),
    "386661006": p("fever", "发热", "發燒"),
    "1912002": p("a fall", "跌倒", "跌倒"),
    "271737000": p("a low blood count (anemia)", "贫血", "貧血"),
    "396230008": p("a skin infection (cellulitis)", "蜂窝织炎", "蜂窩性組織炎"),
    "44558001": p("a groin hernia", "腹股沟疝", "腹股溝疝氣"),
    "396331005": p("celiac disease", "乳糜泻", "乳糜瀉"),
}
PROCEDURE_LAY: dict[str, Phrase] = {
    "52734007": p("a hip replacement", "髋关节置换术", "髖關節置換術"),
    "609588000": p("a knee replacement", "膝关节置换术", "膝關節置換術"),
    "55705006": p("back surgery (spinal fusion)", "脊柱融合术", "脊椎融合術"),
    "80146002": p("surgery to take out your appendix", "阑尾切除术", "闌尾切除術"),
    "45595009": p("surgery to take out your gallbladder", "腹腔镜胆囊切除术", "腹腔鏡膽囊切除術"),
    "44558001": p("a hernia repair", "疝修补术", "疝氣修補術"),
    "23968004": p("bowel surgery", "结肠切除术", "結腸切除術"),
    "232717009": p("heart bypass surgery", "冠状动脉搭桥术", "冠狀動脈繞道手術"),
    "236886002": p("surgery to take out your womb", "子宫切除术", "子宮切除術"),
    "73761001": p("a camera test of your bowel", "结肠镜检查", "大腸鏡檢查"),
    "18286008": p("a heart rhythm procedure (catheter ablation)", "导管消融术", "導管消融術"),
    "415070008": p("a procedure to open a heart artery", "冠状动脉介入治疗", "冠狀動脈介入治療"),
    "312681000": p("a bone density scan", "骨密度检查", "骨密度檢查"),
    "385763009": p("hospice care", "安宁疗护", "安寧療護"),
    "16310003": p("a partial hip replacement", "半髋关节置换术", "半髖關節置換術"),
}
DRUG_ZH: dict[str, str] = {
    "11289": "华法林", "1364430": "阿哌沙班", "1114195": "利伐沙班", "1037042": "达比加群", "67108": "依诺肝素",
    "5224": "肝素", "6809": "二甲双胍", "593411": "西格列汀", "4815": "格列本脲", "274783": "甘精胰岛素",
    "29046": "赖诺普利", "17767": "氨氯地平", "5487": "氢氯噻嗪", "6918": "美托洛尔", "4603": "呋塞米",
    "9997": "螺内酯", "83367": "阿托伐他汀", "36567": "辛伐他汀", "3407": "地高辛", "703": "胺碘酮",
    "1191": "阿司匹林", "32968": "氯吡格雷", "5640": "布洛芬", "7258": "萘普生", "35827": "酮咯酸",
    "140587": "塞来昔布", "161": "对乙酰氨基酚", "7052": "吗啡", "3423": "氢吗啡酮", "10689": "曲马多",
    "25480": "加巴喷丁", "187832": "普瑞巴林", "519": "别嘌醇", "2683": "秋水仙碱", "6448": "碳酸锂",
    "36437": "舍曲林", "4493": "氟西汀", "2556": "西酞普兰", "51272": "喹硫平", "6470": "劳拉西泮",
    "10582": "左甲状腺素", "8640": "泼尼松", "435": "沙丁胺醇", "69120": "噻托溴铵", "40790": "泮托拉唑",
    "7646": "奥美拉唑", "26225": "昂丹司琼", "8591": "氯化钾", "2193": "头孢曲松", "2180": "头孢唑林",
    "723": "阿莫西林", "8339": "哌拉西林", "11124": "万古霉素", "2551": "环丙沙星", "82122": "左氧氟沙星",
    "21212": "克拉霉素", "10180": "复方磺胺甲噁唑", "7454": "呋喃妥因", "4450": "氟康唑", "6922": "甲硝唑",
    "6851": "甲氨蝶呤",
}
# Plain-language explanations when the graph has nothing usable
CURATED: dict[str, Phrase] = {
    "condition:49436004": p("the heart beats in an uneven way", "心跳不规则", "心跳不規則"),
    "condition:5913000": p("a break near the top of the thigh bone", "大腿骨靠近髋关节的地方断了", "大腿骨靠近髖關節的地方斷了"),
    "condition:42343007": p("the heart does not pump as well as it should", "心脏泵血的能力变弱", "心臟泵血的能力變弱"),
    "condition:22298006": p("part of the heart muscle did not get enough blood", "部分心肌缺血受损", "部分心肌缺血受損"),
    "condition:230690007": p("part of the brain did not get enough blood", "部分脑组织缺血受损", "部分腦組織缺血受損"),
    "condition:128053003": p("a clot that blocks a vein in the leg", "腿部静脉里的血块", "腿部靜脈裡的血塊"),
    "condition:91302008": p("the body's strong reaction to an infection", "身体对感染的严重反应", "身體對感染的嚴重反應"),
    "condition:59621000": p("blood pressure that stays too high", "血压长期偏高", "血壓長期偏高"),
    "condition:44054006": p("blood sugar that stays too high", "血糖长期偏高", "血糖長期偏高"),
    "condition:13645005": p("the airways stay narrow, so breathing is hard", "气道长期变窄，呼吸困难",
                            "氣道長期變窄，呼吸困難"),
    "condition:195951007": p("your lung disease suddenly got worse", "慢性肺病突然加重", "慢性肺病突然加重"),
    "condition:59282003": p("a clot that blocks a blood vessel in the lung", "肺部血管被血块堵住", "肺部血管被血塊堵住"),
    "condition:125605004": p("a bone is broken", "骨头断了", "骨頭斷了"),
    "condition:68566005": p("an infection of the bladder or kidneys", "膀胱或肾脏感染", "膀胱或腎臟感染"),
    "condition:433144002": p("the kidneys work less well than they should", "肾脏功能下降", "腎臟功能下降"),
    "condition:431857002": p("the kidneys work much less well than they should", "肾脏功能明显下降", "腎臟功能明顯下降"),
    "condition:195967001": p("the airways tighten, which causes wheezing", "气道收缩，引起喘息", "氣道收縮，引起喘息"),
    "condition:55822004": p("too much fat in the blood", "血液中的脂肪过高", "血液中的脂肪過高"),
    "condition:35489007": p("low mood that lasts a long time", "长期情绪低落", "長期情緒低落"),
    "condition:64859006": p("bones that break easily", "骨头变脆，容易骨折", "骨頭變脆，容易骨折"),
    "condition:396275006": p("worn, stiff and painful joints", "关节磨损、僵硬、疼痛", "關節磨損、僵硬、疼痛"),
    "condition:69896004": p("the body attacks its own joints", "身体攻击自己的关节", "身體攻擊自己的關節"),
    "condition:26929004": p("memory and thinking get worse over time", "记忆力和思考能力逐渐下降", "記憶力和思考能力逐漸下降"),
    "condition:40930008": p("the thyroid makes too little hormone", "甲状腺激素分泌太少", "甲狀腺激素分泌太少"),
    "condition:90560007": p("painful swollen joints from uric acid", "尿酸引起的关节红肿疼痛", "尿酸引起的關節紅腫疼痛"),
    "condition:74400008": p("an inflamed appendix", "阑尾发炎", "闌尾發炎"),
    "condition:65275009": p("an inflamed gallbladder", "胆囊发炎", "膽囊發炎"),
    "condition:10509002": p("swollen airways after a cold", "感冒后气管发炎", "感冒後氣管發炎"),
    "condition:444814009": p("a virus in the sinuses", "病毒引起的鼻窦发炎", "病毒引起的鼻竇發炎"),
    "condition:271737000": p("too few red blood cells", "红细胞太少", "紅血球太少"),
    "condition:44558001": p("a bulge in the groin", "腹股沟处鼓出一块", "腹股溝處鼓出一塊"),
    "condition:396331005": p("the gut reacts to gluten in food", "肠道对食物中的麸质过敏", "腸道對食物中的麩質過敏"),
    "procedure:415070008": p("a thin tube opens a narrowed heart artery", "用细导管打通狭窄的心脏血管", "用細導管打通狹窄的心臟血管"),
    "procedure:45595009": p("the gallbladder is taken out through small cuts", "通过小切口切除胆囊", "通過小切口切除膽囊"),
    "procedure:80146002": p("the appendix is taken out", "切除阑尾", "切除闌尾"),
    "procedure:609588000": p("the knee joint is replaced with a new one", "用人工关节替换膝关节", "用人工關節替換膝關節"),
    "procedure:44558001": p("the bulge in the groin is fixed", "修补腹股沟的鼓包", "修補腹股溝的鼓包"),
    "procedure:23968004": p("part of the bowel is taken out", "切除部分肠子", "切除部分腸子"),
    "procedure:232717009": p("new paths are made for blood to reach the heart", "为心脏搭建新的血管通路", "為心臟搭建新的血管通路"),
    "procedure:236886002": p("the womb is taken out", "切除子宫", "切除子宮"),
    "procedure:73761001": p("a camera looks inside the bowel", "用镜头检查肠道内部", "用鏡頭檢查腸道內部"),
    "procedure:55705006": p("bones in the back are joined together", "把背部的骨头固定在一起", "把背部的骨頭固定在一起"),
    "procedure:312681000": p("a scan that checks how strong the bones are", "检查骨头强度的扫描", "檢查骨頭強度的掃描"),
    "procedure:385763009": p("care for comfort near the end of life", "临终时的舒缓照护", "臨終時的舒緩照護"),
    "procedure:18286008": p("a thin tube is passed to the heart to treat an uneven heartbeat",
                            "把细导管送到心脏，治疗心跳不规则", "把細導管送到心臟，治療心跳不規則"),
    "procedure:52734007": p("the hip joint is replaced with a new one", "用人工关节替换髋关节", "用人工關節替換髖關節"),
    "procedure:16310003": p("part of the hip joint is replaced", "替换部分髋关节", "替換部分髖關節"),
    "drug": p("a medicine", "一种药物", "一種藥物"),
}
# Plain signs, most telling first (the explanation of a disease uses up to three of the graph's, in this order)
PLAIN_SYMPTOMS = ("发热", "咳嗽", "呼吸困难", "胸痛", "心悸", "水肿", "头晕", "疼痛", "有痰", "气促", "乏力", "腹痛",
                  "恶心", "呕吐", "腹泻", "便秘", "头痛", "晕厥", "排尿困难", "尿频", "尿急", "血尿", "关节痛", "骨痛",
                  "肿胀", "跛行", "皮肤红斑", "皮疹", "胸闷", "疲劳", "发冷", "寒战", "食欲不振", "体重减轻", "麻木",
                  "无力", "黄疸", "咯血", "喘息", "盗汗", "咽痛", "腰痛", "压痛")
# The graph's English names of those signs are clinical ("dyspnea"); the patient reads these plain ones
SYMPTOMS_EN = {"发热": "fever", "咳嗽": "cough", "呼吸困难": "trouble breathing", "胸痛": "chest pain",
               "心悸": "a pounding heart", "水肿": "swelling", "头晕": "dizziness", "疼痛": "pain", "有痰": "phlegm",
               "气促": "fast breathing", "乏力": "tiredness", "腹痛": "belly pain", "恶心": "feeling sick",
               "呕吐": "throwing up", "腹泻": "loose stools", "便秘": "constipation", "头痛": "headache",
               "晕厥": "fainting", "排尿困难": "trouble peeing", "尿频": "peeing often", "尿急": "a sudden need to pee",
               "血尿": "blood in the pee", "关节痛": "joint pain", "骨痛": "bone pain", "肿胀": "swelling",
               "跛行": "limping", "皮肤红斑": "red skin", "皮疹": "a rash", "胸闷": "a tight chest",
               "疲劳": "tiredness", "发冷": "chills", "寒战": "shaking chills", "食欲不振": "poor appetite",
               "体重减轻": "weight loss", "麻木": "numbness", "无力": "weakness", "黄疸": "yellow skin",
               "咯血": "coughing up blood", "喘息": "wheezing", "盗汗": "night sweats", "咽痛": "sore throat",
               "腰痛": "back pain", "压痛": "tenderness"}
TYPICAL = 8  # the graph lists a disease's signs roughly most typical first
CARDINAL = {"发热", "咳嗽", "呼吸困难", "胸痛", "心悸", "水肿", "头晕", "疼痛", "腹痛", "头痛", "排尿困难", "尿频",
            "皮肤红斑", "肿胀", "有痰", "气促", "腹泻", "呕吐"}
# A medicine the graph says nothing usable about: its therapeutic class (app/ehr/codes.py MEDICATIONS), curated
CLASS_EXPLAIN: dict[str, Phrase] = {
    "statin": p("a medicine that lowers cholesterol", "一种降胆固醇的药物", "一種降膽固醇的藥物"),
    "thiazide diuretic": p("a water pill that also lowers blood pressure", "一种利尿降压的药物", "一種利尿降壓的藥物"),
    "loop diuretic": p("a water pill", "一种利尿的药物", "一種利尿的藥物"),
    "ace inhibitor": p("a medicine that lowers blood pressure", "一种降血压的药物", "一種降血壓的藥物"),
    "calcium channel blocker": p("a medicine that lowers blood pressure", "一种降血压的药物", "一種降血壓的藥物"),
    "beta blocker": p("a medicine that slows the heart", "一种减慢心跳的药物", "一種減慢心跳的藥物"),
    "anticoagulant": p("a blood thinner", "一种抗凝血的药物", "一種抗凝血的藥物"),
    "antiplatelet": p("a medicine that keeps blood clots from forming", "一种预防血栓的药物", "一種預防血栓的藥物"),
    "analgesic": p("a pain reliever", "一种止痛药", "一種止痛藥"),
    "opioid": p("a strong pain reliever", "一种强效止痛药", "一種強效止痛藥"),
    "nsaid": p("a pain and swelling reliever", "一种消炎止痛药", "一種消炎止痛藥"),
    "proton pump inhibitor": p("a medicine that lowers stomach acid", "一种减少胃酸的药物", "一種減少胃酸的藥物"),
    "penicillin": p("an antibiotic", "一种抗生素", "一種抗生素"),
    "cephalosporin": p("an antibiotic", "一种抗生素", "一種抗生素"),
    "biguanide": p("a medicine that lowers blood sugar", "一种降血糖的药物", "一種降血糖的藥物"),
    "insulin": p("a medicine that lowers blood sugar", "一种降血糖的药物", "一種降血糖的藥物"),
    "dpp4 inhibitor": p("a medicine that lowers blood sugar", "一种降血糖的药物", "一種降血糖的藥物"),
    "sulfonylurea": p("a medicine that lowers blood sugar", "一种降血糖的药物", "一種降血糖的藥物"),
    "potassium-sparing diuretic": p("a water pill that keeps potassium", "一种保钾利尿的药物", "一種保鉀利尿的藥物"),
    "cardiac glycoside": p("a medicine that helps the heart beat strongly", "一种增强心跳的药物", "一種增強心跳的藥物"),
    "antiarrhythmic": p("a medicine that keeps the heartbeat steady", "一种稳定心律的药物", "一種穩定心律的藥物"),
    "gabapentinoid": p("a medicine for nerve pain", "一种治疗神经痛的药物", "一種治療神經痛的藥物"),
    "xanthine oxidase inhibitor": p("a medicine that lowers uric acid", "一种降尿酸的药物", "一種降尿酸的藥物"),
    "anti-gout": p("a medicine that calms gout attacks", "一种缓解痛风发作的药物", "一種緩解痛風發作的藥物"),
    "mood stabilizer": p("a medicine that keeps the mood steady", "一种稳定情绪的药物", "一種穩定情緒的藥物"),
    "ssri": p("a medicine for low mood or worry", "一种治疗情绪低落或焦虑的药物", "一種治療情緒低落或焦慮的藥物"),
    "antipsychotic": p("a medicine that calms the mind", "一种安定精神的药物", "一種安定精神的藥物"),
    "benzodiazepine": p("a medicine for worry or sleep", "一种缓解焦虑或帮助睡眠的药物", "一種緩解焦慮或幫助睡眠的藥物"),
    "thyroid hormone": p("a thyroid hormone pill", "一种甲状腺激素药物", "一種甲狀腺激素藥物"),
    "corticosteroid": p("a medicine that calms swelling and irritation", "一种消炎消肿的药物", "一種消炎消腫的藥物"),
    "beta agonist": p("an inhaler that opens the airways", "一种扩张气道的吸入药", "一種擴張氣道的吸入藥"),
    "anticholinergic": p("an inhaler that keeps the airways open", "一种保持气道通畅的吸入药", "一種保持氣道通暢的吸入藥"),
    "antiemetic": p("a medicine for an upset stomach", "一种止吐的药物", "一種止吐的藥物"),
    "electrolyte": p("a potassium supplement", "一种补钾的药物", "一種補鉀的藥物"),
    "glycopeptide": p("an antibiotic", "一种抗生素", "一種抗生素"),
    "fluoroquinolone": p("an antibiotic", "一种抗生素", "一種抗生素"),
    "macrolide": p("an antibiotic", "一种抗生素", "一種抗生素"),
    "sulfonamide": p("an antibiotic", "一种抗生素", "一種抗生素"),
    "nitrofuran": p("an antibiotic for bladder infections", "一种治疗膀胱感染的抗生素", "一種治療膀胱感染的抗生素"),
    "nitroimidazole": p("an antibiotic", "一种抗生素", "一種抗生素"),
    "azole antifungal": p("a medicine against fungus", "一种抗真菌的药物", "一種抗真菌的藥物"),
    "antimetabolite": p("a medicine that calms an overactive immune system", "一种调节免疫的药物", "一種調節免疫的藥物"),
}
# Everyday words (admission complaints): no explanation needed
PLAIN_TERMS = {"29857009", "21522001", "267036007", "161891005", "25064002", "62315008", "386661006", "1912002"}
_LATIN = re.compile(r"[A-Za-z0-9＜＞<>≥≤%]")


@dataclass
class Term:
    key: str  # "drug:11289", "condition:5913000", "procedure:18286008"
    name: Phrase  # the term in each language
    explanation: Phrase | None = None
    plain: bool = False  # an everyday word (chest pain, a fall): needs no explanation
    source: str = ""  # knowledge_graph | curated | ""
    kg_node: str = ""
    facts: list[str] = field(default_factory=list)  # KG fact values used (Chinese)


@lru_cache(maxsize=1)
def _kg():
    try:
        from app.modules.clinical_kg.glossary import english_names, glossary
        from app.modules.clinical_kg.graph import get_graph
        return glossary(), english_names(), get_graph()
    except Exception:  # the graph is optional: names fall back to the tables
        return {}, {}, None


def _plain(zh: str, en_names: dict[str, str]) -> bool:
    return bool(zh) and len(zh) <= 10 and not _LATIN.search(zh) and bool(en_names.get(zh))


def kg_node(*english: str) -> str | None:
    gl, _, _ = _kg()
    for name in english:
        if name and (node := gl.get(norm(name))):
            return node
    return None


def _join(items: list[str], lang: str) -> str:
    if lang == "en":
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
    return "、".join(items)


def drug_term(rxcui: str, name: str, patient_conditions: list[str]) -> Term:
    gl, en_names, graph = _kg()
    english = (C.MEDICATIONS.get(rxcui) or (name,))[0] or name
    node = kg_node(english, name, *MED_SYNONYMS.get(rxcui, []))
    zh = node if node and not _LATIN.search(node) else DRUG_ZH.get(rxcui, "")
    term = Term(f"drug:{rxcui or norm(name)}", p(english, zh or english, to_hant(zh) if zh else english))
    if node and graph is not None:
        used_for = graph.diseases_with("drug", node)
        mine = [c for c in (kg_node(*_condition_names(code)) for code in patient_conditions) if c]
        picked = [d for d in dict.fromkeys(d for d in used_for if d in mine) if _plain(d, en_names)][:2]
        if picked:
            term.facts = picked
            term.source, term.kg_node = "knowledge_graph", node
            term.explanation = p(f"a medicine used for {_join([en_names[d] for d in picked], 'en')}",
                                 f"一种药物，可用于{_join(picked, 'zh')}等", to_hant(f"一种药物，可用于{_join(picked, 'zh')}等"))
    if term.explanation is None:
        drug_class = (C.MEDICATIONS.get(rxcui) or ("", ""))[1]
        term.explanation, term.source = CLASS_EXPLAIN.get(drug_class, CURATED["drug"]), "curated"
    return term


def _condition_names(code: str) -> list[str]:
    display = C.CONDITIONS.get(code, ("",))[0]
    return [n for n in [display, *CONDITION_SYNONYMS.get(code, [])] if n]


def code_for(kind: str, name: str) -> str:
    """The EHR's code of a condition or procedure named without one (an edited item), or ""."""
    from app.modules.documentation.lexicon import condition_lexicon, procedure_lexicon, same_name

    lexicon = condition_lexicon() if kind == "condition" else procedure_lexicon()
    if (found := lexicon.get(norm(name))) is not None:
        return found
    return next((code for phrase, code in lexicon.items() if same_name(name, phrase)), "")


def _graph_name(name: str) -> Phrase:
    """A term outside the tables: its Chinese name from the knowledge graph, else as written."""
    node = kg_node(name)
    zh = node if node and not _LATIN.search(node) else name
    return p(name, zh, to_hant(zh) if zh != name else name)


def condition_term(code: str, name: str) -> Term:
    _, en_names, graph = _kg()
    code = code or code_for("condition", name)
    lay = CONDITION_LAY.get(code) or _graph_name(name)
    term = Term(f"condition:{code or norm(name)}", lay, plain=code in PLAIN_TERMS)
    node = kg_node(*_condition_names(code), name)
    if curated := CURATED.get(f"condition:{code}"):  # reviewed plain words win over the graph's noisier facts
        term.explanation, term.source, term.kg_node = curated, "curated", node or ""
        return term
    if node and graph is not None:
        signs = {f.value for f in graph.disease_facts(node, ("symptom",))[:TYPICAL]}
        picked = [s for s in PLAIN_SYMPTOMS if s in signs and _plain(s, en_names)][:3]
        if len(picked) >= 2 and any(s in CARDINAL for s in picked):
            term.facts = picked
            term.source, term.kg_node = "knowledge_graph", node
            term.explanation = p(f"common signs are {_join([SYMPTOMS_EN.get(s) or en_names[s] for s in picked], 'en')}",
                                 f"常见表现有{_join(picked, 'zh')}", to_hant(f"常见表现有{_join(picked, 'zh')}"))
    return term


def procedure_term(code: str, name: str) -> Term:
    """Procedures have no usable graph facts (the graph's "treat" nodes are disease-centred): curated only."""
    code = code or code_for("procedure", name)
    lay = PROCEDURE_LAY.get(code) or _graph_name(name)
    term = Term(f"procedure:{code or norm(name)}", lay)
    if curated := CURATED.get(f"procedure:{code}"):
        term.explanation, term.source = curated, "curated"
    return term
