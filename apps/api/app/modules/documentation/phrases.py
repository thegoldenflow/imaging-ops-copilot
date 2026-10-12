"""Menus and phrase tables of the patient instructions (spec 7.3): one structure, three renderings.

The model never writes patient-facing sentences. It picks codes from these menus
(what a medicine is for, when to take it, cautions, follow-up departments, red
flags, daily care) for the items of the signed summary; render.py turns the
structure into English (`en`), Simplified Chinese (`zh-Hans`) and Traditional
Chinese (`zh-Hant`) from these tables. Every phrase is written by hand in all three
(short sentences for an English grade 6–8 reading level; Chinese without medical
abbreviations or Latin letters: doses in 毫克, temperatures in 度). Drug and
condition names come from the bilingual knowledge graph (terms.py); the lay names
below are the fallback and the plain-language label.
"""

from __future__ import annotations

Phrase = dict[str, str]  # {"en": ..., "zh-Hans": ..., "zh-Hant": ...}
LANGS = ("en", "zh-Hans", "zh-Hant")


def p(en: str, hans: str, hant: str) -> Phrase:
    return {"en": en, "zh-Hans": hans, "zh-Hant": hant}


TITLES: dict[str, Phrase] = {
    "document": p("Your discharge instructions", "出院指导", "出院指導"),
    "reason": p("Why you were in hospital", "您为什么住院", "您為什麼住院"),
    "medicines": p("Your medicines", "您的用药时间表", "您的用藥時間表"),
    "follow_up": p("Your follow-up appointments", "复诊安排", "複診安排"),
    "red_flags": p("When to get help right away", "出现以下情况请立即就医", "出現以下情況請立即就醫"),
    "daily_care": p("Looking after yourself at home", "居家注意事项", "居家注意事項"),
    "terms": p("Words to know", "名词解释", "名詞解釋"),
    "col_medicine": p("Medicine", "药名", "藥名"),
    "col_purpose": p("What it is for", "作用", "作用"),
    "col_when": p("When to take it", "什么时候吃", "什麼時候吃"),
    "col_notes": p("Notes", "注意事项", "注意事項"),
    "diet": p("Food and drink", "饮食", "飲食"),
    "activity": p("Activity", "活动", "活動"),
    "wound": p("Your wound", "伤口护理", "傷口護理"),
    "none": p("None.", "无。", "無。"),
    "ai_note": p("Prepared with AI help from your signed discharge summary and checked by your nurse.",
                 "本指导由人工智能根据已签署的出院小结协助起草，并经护士审核。",
                 "本指導由人工智慧根據已簽署的出院小結協助起草，並經護理師審核。"),
}

REASON: dict[str, Phrase] = {
    "stay": p("You were in hospital for {names}.", "您因{names}住院。", "您因{names}住院。"),
    "procedure": p("While you were here, you had {names}.", "住院期间，您做了{names}。", "住院期間，您做了{names}。"),
}

TIMES: dict[str, Phrase] = {
    "morning": p("Morning", "早上", "早上"),
    "noon": p("Noon", "中午", "中午"),
    "evening": p("Evening", "晚上", "晚上"),
    "bedtime": p("Bedtime", "睡前", "睡前"),
    "as_needed": p("Only when needed", "需要时才吃", "需要時才吃"),
}

PURPOSES: dict[str, Phrase] = {
    "blood_thinner": p("To prevent blood clots", "预防血栓", "預防血栓"),
    "heart_rate": p("To control your heart rate", "控制心跳", "控制心跳"),
    "blood_pressure": p("To lower your blood pressure", "降低血压", "降低血壓"),
    "water_pill": p("To remove extra fluid from your body", "帮助身体排出多余的水分", "幫助身體排出多餘的水分"),
    "heart_pump": p("To help your heart pump better", "帮助心脏更好地泵血", "幫助心臟更好地泵血"),
    "cholesterol": p("To lower your cholesterol", "降低胆固醇", "降低膽固醇"),
    "heart_protect": p("To protect your heart and blood vessels", "保护心脏和血管", "保護心臟和血管"),
    "blood_sugar": p("To control your blood sugar", "控制血糖", "控制血糖"),
    "pain": p("For pain", "止痛", "止痛"),
    "nerve_pain": p("For nerve pain", "缓解神经痛", "緩解神經痛"),
    "infection": p("To treat an infection", "治疗感染", "治療感染"),
    "breathing": p("To help your breathing", "帮助呼吸", "幫助呼吸"),
    "stomach": p("To protect your stomach", "保护胃", "保護胃"),
    "nausea": p("For an upset stomach", "缓解恶心", "緩解噁心"),
    "thyroid": p("To replace thyroid hormone", "补充甲状腺激素", "補充甲狀腺激素"),
    "mood": p("For your mood", "改善情绪", "改善情緒"),
    "sleep_anxiety": p("For worry or sleep", "缓解焦虑或帮助睡眠", "緩解焦慮或幫助睡眠"),
    "gout": p("To prevent gout attacks", "预防痛风发作", "預防痛風發作"),
    "swelling": p("To calm swelling and irritation", "减轻肿胀和刺激", "減輕腫脹和刺激"),
    "potassium": p("To keep your potassium level normal", "保持血钾正常", "保持血鉀正常"),
    "joints": p("For your joints", "治疗关节问题", "治療關節問題"),
    "other": p("As your doctor explained", "按医生的解释使用", "按醫生的解釋使用"),
}

CAUTIONS: dict[str, Phrase] = {
    "blood_test": p("Get the blood test for this medicine on the date booked.", "请按预约日期抽血检查这个药的效果。",
                    "請按預約日期抽血檢查這個藥的效果。"),
    "leafy_greens": p("Eat about the same amount of green vegetables each week.", "每周吃的绿叶蔬菜量要大致相同。",
                      "每週吃的綠葉蔬菜量要大致相同。"),
    "bleeding": p("Watch for bleeding or bruises.", "留意有没有出血或瘀青。", "留意有沒有出血或瘀青。"),
    "other_pain_pills": p("Ask your pharmacist before you take other pain pills.", "服用其他止痛药之前，先问药剂师。",
                          "服用其他止痛藥之前，先問藥師。"),
    "with_food": p("Take it with food.", "随餐服用。", "隨餐服用。"),
    "stand_slowly": p("Stand up slowly. It can make you dizzy.", "起身要慢，这个药可能让您头晕。",
                      "起身要慢，這個藥可能讓您頭暈。"),
    "drowsy": p("It can make you sleepy. Do not drive after you take it.", "可能让您想睡，服药后不要开车。",
                "可能讓您想睡，服藥後不要開車。"),
    "finish_all": p("Take all of it, even if you feel better.", "即使感觉好转，也要把药吃完。",
                    "即使感覺好轉，也要把藥吃完。"),
    "weigh_daily": p("Weigh yourself every morning.", "每天早上称体重。", "每天早上量體重。"),
    "check_sugar": p("Check your blood sugar the way you were shown.", "按照教您的方法测血糖。",
                     "按照教您的方法測血糖。"),
    "no_alcohol": p("Do not drink alcohol with it.", "服用期间不要喝酒。", "服用期間不要喝酒。"),
    "inhaler": p("Use it the way the nurse showed you.", "按照护士教您的方法使用。", "按照護理師教您的方法使用。"),
}
# Added by the renderer from the medicine's category (not chosen by the model)
CATEGORY_NOTES: dict[str, Phrase] = {
    "new": p("This is a new medicine.", "这是新药。", "這是新藥。"),
    "changed": p("Your dose has changed. Take the new dose.", "剂量改了，请按新剂量服用。", "劑量改了，請按新劑量服用。"),
    "stopped": p("Stop taking this medicine.", "请停止服用这个药。", "請停止服用這個藥。"),
    "continued": p("Keep taking it as before.", "照旧服用。", "照舊服用。"),
}

DEPARTMENTS: dict[str, Phrase] = {
    "family_doctor": p("your family doctor", "您的家庭医生", "您的家庭醫師"),
    "anticoag_clinic": p("the blood thinner clinic", "抗凝血药物门诊", "抗凝血藥物門診"),
    "blood_test": p("a blood test at the lab", "到化验室抽血", "到檢驗室抽血"),
    "heart_clinic": p("the heart clinic", "心脏科门诊", "心臟科門診"),
    "heart_failure_clinic": p("the heart failure clinic", "心力衰竭门诊", "心臟衰竭門診"),
    "lung_clinic": p("the lung clinic", "呼吸科门诊", "胸腔科門診"),
    "fracture_clinic": p("the bone and joint clinic", "骨科门诊", "骨科門診"),
    "surgery_clinic": p("the surgery clinic", "外科门诊", "外科門診"),
    "stroke_clinic": p("the stroke prevention clinic", "中风预防门诊", "中風預防門診"),
    "diabetes_clinic": p("the diabetes clinic", "糖尿病门诊", "糖尿病門診"),
    "kidney_clinic": p("the kidney clinic", "肾脏科门诊", "腎臟科門診"),
    "seniors_clinic": p("the seniors' health clinic", "老年科门诊", "老年科門診"),
    "infection_clinic": p("the infection clinic", "感染科门诊", "感染科門診"),
    "physio": p("physiotherapy", "物理治疗", "物理治療"),
    "other": p("{department}", "{department}", "{department}"),
}
FOLLOW_UP: dict[str, Phrase] = {
    "dated": p("See {department} on {date}.", "{date}看{department}。", "{date}看{department}。"),
    "undated": p("See {department}. They will call you with a date.", "看{department}，对方会打电话通知您日期。",
                 "看{department}，對方會打電話通知您日期。"),
    "where": p("Place: {location}.", "地点：{location}。", "地點：{location}。"),
}
WEEKDAYS = {"en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
            "zh-Hans": ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"),
            "zh-Hant": ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")}
MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December")

# code -> (phrase, action): emergency = call 911 now; call = call the unit or see the family doctor today
RED_FLAGS: dict[str, tuple[Phrase, str]] = {
    "chest_pain": (p("Chest pain or pressure", "胸痛或胸口发闷", "胸痛或胸口發悶"), "emergency"),
    "short_breath": (p("Trouble breathing", "呼吸困难", "呼吸困難"), "emergency"),
    "stroke_signs": (p("A drooping face, a weak arm or trouble speaking", "脸部下垂、手臂无力或说话困难",
                       "臉部下垂、手臂無力或說話困難"), "emergency"),
    "heavy_bleeding": (p("Bleeding that will not stop", "出血止不住", "出血止不住"), "emergency"),
    "fainting": (p("Fainting or a very fast heartbeat", "晕倒或心跳非常快", "暈倒或心跳非常快"), "emergency"),
    "confusion": (p("New confusion, or very hard to wake up", "突然神志不清，或很难叫醒", "突然神志不清，或很難叫醒"),
                  "emergency"),
    "bleeding_signs": (p("Blood in your pee or poop, black poop, or a nosebleed", "小便或大便带血、大便发黑，或流鼻血",
                         "小便或大便帶血、大便發黑，或流鼻血"), "call"),
    "fever": (p("A temperature of 38.5 degrees or higher", "体温达到38.5度或更高", "體溫達到38.5度或更高"), "call"),
    "wound_infection": (p("Redness, swelling, heat or pus at the wound", "伤口发红、肿胀、发热或流脓",
                          "傷口發紅、腫脹、發熱或流膿"), "call"),
    "puncture_site": (p("Bleeding or a growing lump where the tube went in", "穿刺的地方出血或肿块变大",
                        "穿刺的地方出血或腫塊變大"), "call"),
    "leg_swelling": (p("Pain or swelling in one leg", "一侧腿部疼痛或肿胀", "一側腿部疼痛或腫脹"), "call"),
    "weight_gain": (p("You gain 2 kilograms in 2 days", "2天内体重增加2公斤", "2天內體重增加2公斤"), "call"),
    "fall": (p("A fall, or you hit your head", "跌倒或撞到头", "跌倒或撞到頭"), "call"),
    "cannot_eat_drink": (p("You cannot eat or drink", "无法进食或喝水", "無法進食或喝水"), "call"),
    "worse_pain": (p("Pain that gets worse and pills do not help", "疼痛加重，吃药也不管用", "疼痛加重，吃藥也不管用"),
                   "call"),
    "low_sugar": (p("Shaking, sweating or feeling strange from low blood sugar", "低血糖引起的发抖、出汗或不舒服",
                    "低血糖引起的發抖、出汗或不舒服"), "call"),
    "urine_problems": (p("Burning when you pee, or you cannot pee", "小便时灼痛，或排不出小便", "小便時灼痛，或排不出小便"),
                       "call"),
    "breathing_worse": (p("More cough, more phlegm or more short of breath", "咳嗽、痰或气喘比之前更厉害",
                          "咳嗽、痰或氣喘比之前更厲害"), "call"),
}
ACTIONS: dict[str, Phrase] = {
    "emergency": p("Call 911 or go to the nearest emergency department right away.",
                   "立即拨打911，或马上去最近的急诊室。", "立即撥打911，或馬上去最近的急診室。"),
    "call": p("Call the unit at {phone} or see your family doctor today.",
              "请致电病房{phone}，或当天去看家庭医生。", "請致電病房{phone}，或當天去看家庭醫師。"),
}

# code -> (group, phrase)
DAILY_CARE: dict[str, tuple[str, Phrase]] = {
    "steady_greens": ("diet", p("Keep the amount of green leafy vegetables about the same each week.",
                                "每周吃的绿叶蔬菜量保持大致相同。", "每週吃的綠葉蔬菜量保持大致相同。")),
    "less_salt": ("diet", p("Eat less salt.", "少吃盐。", "少吃鹽。")),
    "fluid_limit": ("diet", p("Drink no more than 1.5 litres a day.", "每天喝水不超过1.5升。",
                              "每天喝水不超過1.5公升。")),
    "regular_meals": ("diet", p("Eat regular meals. Cut down on sweets and sugary drinks.", "按时吃饭，少吃甜食和含糖饮料。",
                                "按時吃飯，少吃甜食和含糖飲料。")),
    "no_alcohol": ("diet", p("Do not drink alcohol.", "不要喝酒。", "不要喝酒。")),
    "usual_diet": ("diet", p("Eat as usual, with fruit and vegetables.", "照常饮食，多吃蔬菜水果。", "照常飲食，多吃蔬菜水果。")),
    "drink_fluids": ("diet", p("Drink plenty of water unless you were told to limit it.", "多喝水，除非医生让您限制饮水。",
                               "多喝水，除非醫師讓您限制飲水。")),
    "walk_daily": ("activity", p("Walk a little more each day.", "每天多走一点路。", "每天多走一點路。")),
    "use_walker": ("activity", p("Use your walker or cane when you walk.", "走路时使用助行器或拐杖。",
                                 "走路時使用助行器或拐杖。")),
    "no_driving": ("activity", p("Do not drive until your doctor says it is safe.", "医生同意之前不要开车。",
                                 "醫師同意之前不要開車。")),
    "no_lifting": ("activity", p("Do not lift anything heavier than 5 kilograms for 2 weeks.", "2周内不要提超过5公斤的东西。",
                                 "2週內不要提超過5公斤的東西。")),
    "prevent_falls": ("activity", p("Keep floors clear and use a night light so you do not fall.",
                                    "保持地面通畅，晚上开小夜灯，防止跌倒。", "保持地面通暢，晚上開小夜燈，防止跌倒。")),
    "rest": ("activity", p("Rest when you feel tired.", "感到累的时候就休息。", "感到累的時候就休息。")),
    "breathing_exercises": ("activity", p("Do your breathing exercises every day.", "每天做呼吸练习。", "每天做呼吸練習。")),
    "keep_dry": ("wound", p("Keep the wound clean and dry for 2 days.", "2天内保持伤口清洁干燥。", "2天內保持傷口清潔乾燥。")),
    "check_wound": ("wound", p("Look at the wound every day.", "每天检查伤口。", "每天檢查傷口。")),
    "no_soaking": ("wound", p("Do not take a bath or swim until the wound heals. A shower is fine.",
                              "伤口愈合前不要泡澡或游泳，可以淋浴。", "傷口癒合前不要泡澡或游泳，可以淋浴。")),
    "puncture_care": ("wound", p("Keep the spot where the tube went in dry for 1 day. Do not lift heavy things for 3 days.",
                                 "穿刺的地方1天内保持干燥，3天内不要提重物。", "穿刺的地方1天內保持乾燥，3天內不要提重物。")),
    "dressing": ("wound", p("Change the dressing the way the nurse showed you.", "按照护士教您的方法换药。",
                            "按照護理師教您的方法換藥。")),
}
CARE_GROUPS = ("diet", "activity", "wound")

UNIT_NAMES: dict[str, Phrase] = {
    "MEDA": p("Medicine A unit", "内科一病房", "內科一病房"), "MEDB": p("Medicine B unit", "内科二病房", "內科二病房"),
    "SURG": p("Surgery unit", "外科病房", "外科病房"), "ORTH": p("Orthopedics unit", "骨科病房", "骨科病房"),
    "ICU": p("Intensive care unit", "重症监护病房", "加護病房"), "ED": p("Emergency department", "急诊科", "急診科"),
}
UNIT_PHONES = {"MEDA": "416-555-0141", "MEDB": "416-555-0142", "SURG": "416-555-0151", "ORTH": "416-555-0161",
               "ICU": "416-555-0171", "ED": "416-555-0100"}
DEFAULT_PHONE = "416-555-0100"

# Dose units without Latin letters in Chinese
UNITS_ZH = {"mg": ("毫克", "毫克"), "mcg": ("微克", "微克"), "g": ("克", "克"), "ml": ("毫升", "毫升"),
            "[iu]": ("单位", "單位"), "units": ("单位", "單位"), "unit": ("单位", "單位"), "meq": ("毫当量", "毫當量")}


def menus() -> dict[str, set[str]]:
    return {"purposes": set(PURPOSES), "times": set(TIMES), "cautions": set(CAUTIONS),
            "departments": set(DEPARTMENTS), "red_flags": set(RED_FLAGS), "daily_care": set(DAILY_CARE)}


def menu_text() -> str:
    """The menus as the model sees them (code: English meaning)."""
    def block(name: str, items) -> str:
        return f"{name}:\n" + "\n".join(f"- {code}: {text}" for code, text in items)

    return "\n\n".join([
        block("PURPOSES", ((k, v["en"]) for k, v in PURPOSES.items())),
        block("TIMES", ((k, v["en"]) for k, v in TIMES.items())),
        block("CAUTIONS", ((k, v["en"]) for k, v in CAUTIONS.items())),
        block("DEPARTMENTS", ((k, v["en"]) for k, v in DEPARTMENTS.items() if k != "other")) +
        "\n- other: none of these (the department is shown as recorded)",
        block("RED_FLAGS", ((k, f"{v[0]['en']} ({v[1]})") for k, v in RED_FLAGS.items())),
        block("DAILY_CARE", ((k, f"[{v[0]}] {v[1]['en']}") for k, v in DAILY_CARE.items())),
    ])
