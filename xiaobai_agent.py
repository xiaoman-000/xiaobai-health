# -*- coding: utf-8 -*-
"""
小白 · 健康陪伴智能体（本地网页版引擎）

用途：在没有扣子账号 / 没有大模型 API 的情况下，把「开始 → M1 → 风险分流 → 检索 →
任务分流 → M2 → M3 → 结束 / 安全兜底」这条链路跑通，用于验证：
  1. 9 类红旗是否 100% 命中、普通症状是否误报；
  2. 章节级检索是否命中、知识库边界外是否正确降级；
  3. 儿童 / 孕妇 / 老人风险是否上调；
  4. 输出是否满足字数、免责声明、无诊断、无药名剂量等硬约束。

说明：M1/M2/M3 在真实部署中由大模型完成；本文件用规则模拟其**结构与边界**，
不模拟语言模型的措辞能力。措辞质量仍需在扣子上用真实模型验收。

用法：
    python xiaobai_agent.py "胸口像被压着，一直出汗，左胳膊也疼"
    python xiaobai_agent.py            # 交互模式
"""

import os
import re
import sys
import json
import math

BASE = os.path.dirname(os.path.abspath(__file__))
KB_ROOT = os.path.join(BASE, "知识库")

SECTIONS = ["一句话结论", "常见表现", "可能原因", "在家可以先做",
            "这些情况要就医", "不要做", "参考来源"]
DISCLAIMER = "我不是医生，这些建议不能替代面诊。"

RED_FLAGS = ["压榨性胸痛", "呼吸困难", "意识丧失或晕厥", "抽搐", "呕血或黑便",
             "卒中征象", "儿童高热惊厥", "自伤自杀念头", "严重过敏反应"]

# 安全兜底优先级：数字越小越优先
FLAG_PRIORITY = ["自伤自杀念头", "卒中征象", "压榨性胸痛", "呼吸困难",
                 "严重过敏反应", "呕血或黑便", "抽搐", "意识丧失或晕厥",
                 "儿童高热惊厥"]

# ---------------------------------------------------------------- 词表

VOCAB = [
    "发热", "发烧", "高烧", "低烧", "体温", "咳嗽", "咳痰", "咽痛", "嗓子疼", "喉咙痛",
    "鼻塞", "流鼻涕", "流涕", "打喷嚏", "头痛", "头晕", "眩晕", "腹泻", "拉肚子",
    "便秘", "呕吐", "恶心", "腹痛", "肚子疼", "胃痛", "腹胀", "肋骨", "失眠",
    "睡不着", "早醒", "胸痛", "胸闷", "心慌", "心悸", "呼吸困难", "喘不上气",
    "憋气", "气短", "晕厥", "晕倒", "昏迷", "抽搐", "惊厥", "呕血", "吐血", "黑便",
    "便血", "卒中", "中风", "嘴歪", "口角歪斜", "说话不清", "单侧无力", "麻木",
    "过敏", "皮疹", "风团", "瘙痒", "肿胀", "脱水", "乏力", "疲劳", "焦虑", "抑郁",
    "情绪低落", "自杀", "自伤", "感冒", "流感", "鼻炎", "咽炎", "支气管炎", "肺炎",
    "肠胃炎", "胃食管反流", "心梗", "心绞痛", "贫血", "低血糖", "高血压", "糖尿病",
    "偏头痛", "热性惊厥", "蜱虫", "叮咬", "姜茶", "抗生素", "退热药", "口服补液盐",
    "补液", "疫苗", "血压", "血糖", "怀孕", "孕期", "婴儿", "儿童", "老人", "宫缩",
    "作息", "睡眠", "熬夜",
    "皮疹", "声音嘶哑", "水肿", "抽动",
]

# 检索词模板：症状 -> 2-4 条规范检索词
QUERY_MAP = {
    "胸痛": ["胸痛 危险信号 就医", "胸痛 不要做 禁忌", "胸痛 家庭急救"],
    "压榨性胸痛": ["胸痛 危险信号 就医", "胸痛 不要做 禁忌", "胸痛 家庭急救"],
    "呼吸困难": ["呼吸困难 危险信号 就医", "呼吸困难 不要做", "呼吸困难 急救"],
    "意识丧失或晕厥": ["晕厥 危险信号 就医", "晕厥 不要做", "晕厥 现场处理"],
    "抽搐": ["抽搐 危险信号 就医", "抽搐 不要做", "抽搐 现场处理"],
    "呕血或黑便": ["呕血 黑便 危险信号 就医", "消化道出血 不要做", "呕血 急救"],
    "卒中征象": ["脑卒中 危险信号 就医", "脑卒中 不要做", "卒中 急救 时间"],
    "儿童高热惊厥": ["儿童高热惊厥 急救", "儿童发热 危险信号", "儿童发热 不要做"],
    "自伤自杀念头": ["心理危机 自杀念头 求助", "心理危机 陪伴 不要做"],
    "严重过敏反应": ["严重过敏 危险信号 就医", "过敏 不要做", "过敏 急救"],
    "发热": ["发热 什么时候就医", "发热 在家处理", "发热 不要做"],
    "发烧": ["发热 什么时候就医", "发热 在家处理", "发热 不要做"],
    "儿童发热": ["儿童发热 危险信号 就医", "儿童发热 在家处理", "儿童发热 不要做"],
    "头痛": ["头痛 危险信号 就医", "头痛 在家处理", "头痛 不要做"],
    "咽痛": ["咽痛 什么时候就医", "咽痛 在家处理", "咽痛 不要做"],
    "咳嗽": ["咳嗽 什么时候就医", "咳嗽 在家处理", "咳嗽 不要做"],
    "腹泻": ["腹泻 脱水 就医", "腹泻 在家处理", "腹泻 不要做"],
    "呕吐": ["呕吐 脱水 就医", "呕吐 在家处理", "呕吐 不要做"],
    "腹痛": ["腹痛 危险信号 就医", "腹痛 在家处理", "腹痛 不要做"],
    "感冒": ["普通感冒 饮食", "感冒 姜茶 有用吗", "感冒 不要做"],
    "头晕": ["头晕 危险信号 就医", "头晕 在家处理", "头晕 不要做"],
    "失眠": ["失眠 调整 作息", "失眠 什么时候就医", "失眠 不要做"],
}

FOLLOWUPS = {
    "胸痛": ["打 120 要说哪些信息", "等救护车时能做什么", "最近的胸痛中心在哪"],
    "呼吸困难": ["可能是哮喘吗", "等车时能喝水吗", "要带什么去医院"],
    "卒中征象": ["发病时间怎么算", "能自己开车去吗", "到医院挂什么科"],
    "抽搐": ["抽多久算危险", "以前没抽过怎么办", "要查脑电图吗"],
    "呕血或黑便": ["黑便一定是出血吗", "要做胃镜吗", "路上要注意什么"],
    "儿童高热惊厥": ["以后发烧还会抽吗", "要住院吗", "家里要备什么"],
    "自伤自杀念头": ["热线打不通怎么办", "怎么跟家人开口", "明天能去哪个科"],
    "严重过敏反应": ["过敏原怎么查", "以后还能吃吗", "要带什么去医院"],
    "发热": ["多少度要吃退烧药", "反复发烧正常吗", "要查血常规吗"],
    "儿童发热": ["多少度要吃退烧药", "反复发烧正常吗", "要查血常规吗"],
    "头痛": ["要做什么检查", "止痛药能吃吗", "多久能好"],
    "咽痛": ["要吃消炎药吗", "几天能好", "要不要查链球菌"],
    "咳嗽": ["要不要拍胸片", "止咳药能吃吗", "多久能好"],
    "腹泻": ["要不要吃止泻药", "多久能好", "家里人怎么防传染"],
    "呕吐": ["要不要吃止吐药", "多久能好", "反复吐怎么办"],
    "腹痛": ["要做什么检查", "能不能热敷", "多久能好"],
    "感冒": ["要不要吃消炎药", "几天能好", "会不会传染家人"],
    "头晕": ["要查什么", "能不能开车", "多久能好"],
    "失眠": ["要不要吃安眠药", "多久能改善", "睡前能看手机吗"],
    "默认": ["这种情况常见吗", "还需要注意什么", "要挂什么科"],
}

EMPATHY = {
    "症状咨询": ["听起来挺难受的，先别慌。", "这事儿确实让人心里没底，我们一点点看。",
                 "身体不舒服的时候最容易胡思乱想，先看眼下能做什么。"],
    "科普提问": ["这个问题问得挺实在。", "这个说法很多人都有疑问。", "先把这个说清楚，你心里就有数了。"],
    "用药咨询": ["用药这块确实得谨慎。", "药不能随便叠加，这点你问得对。"],
    "生活方式": ["能想到调整，已经是在照顾自己了。", "这事儿急不来，但可以一点点改。"],
    "情绪困扰": ["听起来这段时间你扛了不少。", "这种感觉挺磨人的，不是你矫情。"],
    "其他": ["我在，慢慢说。", "没关系的，我们先把情况弄明白。"],
}

# ---------------------------------------------------------------- 数据加载


class Doc:
    def __init__(self, doc_id, title, category, path, sections):
        self.id = doc_id
        self.title = title
        self.category = category
        self.path = path
        self.sections = sections

    @property
    def summary(self):
        return self.sections.get("一句话结论", "")


def parse_md(path):
    text = open(path, encoding="utf-8").read()
    title = ""
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    sections, current = {}, None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            current = m.group(1).strip()
            sections[current] = ""
        elif current:
            sections[current] += line + "\n"
    return title, {k: v.strip() for k, v in sections.items()}


def load_docs():
    docs = []
    for category in ["K1_危险信号与急救", "K2_常见症状"]:
        folder = os.path.join(KB_ROOT, category)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith(".md"):
                continue
            doc_id = int(name.split("_")[0])
            title, sections = parse_md(os.path.join(folder, name))
            docs.append(Doc(doc_id, title, category, os.path.join(folder, name), sections))
    return docs


def clean_bullets(text, label_only=False):
    """只取真正的条目行，丢掉"具体是哪种..."这类引导语。"""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("注意：") or line.startswith("具体是"):
            continue
        m = re.match(r"^(?:[-*·]|[0-9]+[.、])[ 	]*(.+)$", line)
        if not m:
            continue
        item = re.sub(r"\*\*", "", m.group(1)).strip()
        if label_only and "：" in item:
            head = item.split("：", 1)[0]
            head = re.sub(r"（[^）]*）", "", head).strip()
            if 2 <= len(head) <= 12:
                item = head
        out.append(item)
    return out


CANON = {
    "拉肚子": "腹泻", "发烧": "发热", "高烧": "发热", "低烧": "发热",
    "嗓子疼": "咽痛", "喉咙痛": "咽痛", "流鼻涕": "流涕",
    "睡不着": "失眠", "早醒": "失眠", "肚子疼": "腹痛", "胃痛": "腹痛",
    "晕倒": "晕厥", "吐血": "呕血", "声音嘶哑": "声音嘶哑",
}

# 口语变体：「头很痛」「嗓子好疼」「肚子一阵疼」这类说法词典收不全，用模式兜住
COLLO = [
    (r"头[^。！？，、]{0,4}(?<![不没])(?:痛|疼)", "头痛"),
    (r"(?:肚子|腹部|胃)[^。！？，、]{0,4}(?<![不没])(?:痛|疼)", "腹痛"),
    (r"(?:胸口|胸部|心口)[^。！？，、]{0,4}(?<![不没])(?:痛|疼)", "胸痛"),
    (r"(?:嗓子|喉咙|咽喉)[^。！？，、]{0,4}(?<![不没])(?:痛|疼)", "咽痛"),
    (r"(?:喘不上?气|上不来气|气不够用)", "呼吸困难"),
    (r"(?:拉稀|闹肚子|拉水|水样便)", "腹泻"),
    (r"(?:想吐|反胃|干呕)", "呕吐"),
    (r"头[^。！？，、]{0,3}(?<![不没])(?:晕|昏)", "头晕"),
    (r"(?:浑身|全身|身上)(?:没劲|乏力|没力气|发软)", "乏力"),
]


def symptom_pos(text, symptom):
    pos = len(text)
    for term in VOCAB:
        if CANON.get(term, term) == symptom:
            i = text.find(term)
            if i >= 0:
                pos = min(pos, i)
    for pat, canon in COLLO:
        if canon == symptom:
            m = re.search(pat, text)
            if m:
                pos = min(pos, m.start())
    return pos


def extract_terms(text):
    found = []
    for term in VOCAB:
        if term in text:
            canon = CANON.get(term, term)
            if canon not in found:
                found.append(canon)
    for pat, canon in COLLO:
        if canon not in found and re.search(pat, text):
            found.append(canon)
    return found


class Index:
    def __init__(self, docs):
        self.docs = docs
        self.chunks = []
        for d in docs:
            for sec in SECTIONS:
                body = d.sections.get(sec, "")
                if not body:
                    continue
                terms = set(extract_terms(d.title + "\n" + body))
                self.chunks.append({
                    "doc": d, "section": sec, "text": body,
                    "terms": terms, "grams": self.grams(body),
                })
        self.df = {}
        for c in self.chunks:
            for t in c["terms"]:
                self.df[t] = self.df.get(t, 0) + 1
        self.n = max(len(self.chunks), 1)

    @staticmethod
    def grams(text):
        s = re.sub(r"[\s，。、；：！？（）()《》“”\"'·\-—…*#\d]", "", text)
        return set(s[i:i + 2] for i in range(len(s) - 1))

    def idf(self, term):
        df = self.df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1 + self.n / df) + 0.2

    def retrieve(self, queries, extra_terms=(), primary=None, top_k=4, min_score=0.45, allow_k1=True):
        """返回 (命中列表, 未覆盖词, 最高分)。分数=查询词在切片中的加权覆盖率。"""
        MODIFIER = {"怀孕", "孕期", "儿童", "老人", "婴儿"}
        q_terms, uncovered = set(), set()
        for q in queries:
            for t in extract_terms(q):
                if t in MODIFIER:
                    continue
                if t in self.df:
                    q_terms.add(t)
                else:
                    uncovered.add(t)
        for t in extra_terms:
            if t in MODIFIER:
                continue
            if t not in self.df and len(t) >= 2:
                uncovered.add(t)
        if not q_terms:
            return [], sorted(uncovered), 0.0

        total_idf = sum(self.idf(t) for t in q_terms) or 1.0
        scored = []
        for c in self.chunks:
            hit = [t for t in q_terms if t in c["terms"]]
            if not hit:
                continue
            score = sum(self.idf(t) for t in hit) / total_idf
            title_hit = 1 if (primary and primary in c["doc"].title) else 0
            if title_hit:
                score = min(1.0, score * 1.25)
            if score >= min_score:
                scored.append((title_hit, score, c))
        scored.sort(key=lambda x: (-x[0], -x[1]))
        if not allow_k1:
            only_k2 = [x for x in scored if x[2]["doc"].category.startswith("K2")]
            if only_k2:
                scored = only_k2

        # 同一篇只保留最高分切片，凑够 top_k
        picked, seen = [], {}
        for _, score, c in scored:
            if c["doc"].id in seen and seen[c["doc"].id] >= 1:
                continue
            seen[c["doc"].id] = seen.get(c["doc"].id, 0) + 1
            picked.append({
                "doc_id": c["doc"].id, "title": c["doc"].title, "category": c["doc"].category,
                "section": c["section"], "text": c["text"], "score": round(score, 3),
            })
            if len(picked) >= top_k:
                break
        best = picked[0]["score"] if picked else 0.0
        return picked, sorted(uncovered), best


# ---------------------------------------------------------------- M1

def m1_extract(user_input):
    text = user_input.strip()
    flags, symptoms, queries = [], [], []

    child = bool(re.search(r"(孩子|宝宝|小孩|婴儿|儿子|女儿|\d+\s*个月|\d+\s*岁)", text))
    pregnant = bool(re.search(r"(怀孕|孕期|孕\s*\d+\s*周|胎动|孕妇)", text))
    elderly = bool(re.search(r"(爷爷|奶奶|外公|外婆|七十|八十|九十|\d{2}\s*岁)", text))

    # 1. 儿童高热惊厥（必须先判，否则会被普通抽搐吃掉）
    if child and re.search(r"(烧|发热|体温)", text) and re.search(r"(抽|惊厥|翻白眼|口吐白沫)", text):
        flags.append("儿童高热惊厥")

    # 2. 自伤自杀
    if re.search(r"(不想活|活着没意思|想死|自杀|自残|轻生|跳楼|割腕|死了算了|不如死)", text):
        flags.append("自伤自杀念头")

    # 3. 卒中
    if re.search(r"(嘴歪|口角歪斜|说话不清|说不清话|一边.{0,4}(没劲|无力|麻)|单侧.{0,4}(无力|麻)|偏瘫|突然.{0,6}看不清)", text):
        flags.append("卒中征象")

    # 4. 压榨性胸痛
    chest_pressure = re.search(r"胸(口)?[^。！？]{0,8}(压|紧|绞|压榨|像.{0,3}石头)", text)
    chest_with_sign = re.search(r"(胸痛|胸口痛|心口痛)[^。！？]{0,20}(出汗|大汗|放射|左臂|左胳膊|后背|下颌|呼吸困难)", text) or \
                      re.search(r"(胸痛|胸口痛)[^。！？]{0,10}(持续|15\s*分钟|越来越重)", text)
    if chest_pressure or chest_with_sign or "压榨性胸痛" in text:
        flags.append("压榨性胸痛")

    # 5. 呼吸困难
    if re.search(r"(喘不上气|喘不过气|呼吸困难|憋气|呼吸费力|窒息|喉咙发紧|气道.{0,4}肿)", text):
        flags.append("呼吸困难")

    # 6. 严重过敏
    allergy_sign = re.search(r"(风团|荨麻疹|皮疹|疹子|身上.{0,4}痒|过敏)", text)
    airway_sign = re.search(r"(嘴唇|眼皮|眼睑|喉头|喉咙)[^。！？]{0,6}(肿|紧)|声音[^。！？]{0,3}(哑|变)|呼吸困难|喘不上?气|憋气|休克", text)
    strong_allergy = re.search(
        r"(过敏|蜂蜇|蜂咬|食物|药物)[^。！？]{0,20}"
        r"(风团|嘴唇.{0,3}肿|眼皮.{0,3}肿|喉咙.{0,3}紧|声音.{0,3}哑|喘|休克|全身.{0,4}疹)", text)
    if strong_allergy or (allergy_sign and airway_sign) or "过敏性休克" in text:
        flags.append("严重过敏反应")

    # 7. 呕血黑便
    if re.search(r"(呕血|吐血|咖啡色[^。！？]{0,10}(吐|呕|东西|液体)|(吐|呕)[^。！？]{0,10}咖啡色|黑便|柏油.{0,4}(便|大便)|大便.{0,4}黑)", text):
        flags.append("呕血或黑便")

    # 8. 抽搐
    if re.search(r"(抽搐|抽风|惊厥|翻白眼|口吐白沫|四肢.{0,3}僵直)", text):
        flags.append("抽搐")

    # 9. 晕厥
    if re.search(r"(晕倒|昏倒|晕厥|叫不醒|意识丧失|眼前.{0,3}黑.{0,6}(摔|倒))", text):
        flags.append("意识丧失或晕厥")

    flags = [f for f in FLAG_PRIORITY if f in flags]

    # 红旗口语 -> 规范症状，保证下游拿到可读的症状名
    FLAG_SYMPTOM = {
        "压榨性胸痛": ["胸痛"], "呼吸困难": ["呼吸困难"],
        "意识丧失或晕厥": ["晕厥"], "抽搐": ["抽搐"],
        "呕血或黑便": ["呕血", "黑便"], "卒中征象": ["嘴歪", "说话不清"],
        "儿童高热惊厥": ["发热", "抽搐"], "自伤自杀念头": ["情绪低落"],
        "严重过敏反应": ["过敏", "肿胀"],
    }
    for f in flags:
        for s in FLAG_SYMPTOM.get(f, []):
            if s not in symptoms:
                symptoms.append(s)
    if "汗" in text and "胸痛" in symptoms and "出汗" not in symptoms:
        symptoms.append("出汗")

    # person
    if child:
        person = "儿童"
    elif pregnant:
        person = "孕妇"
    elif elderly:
        person = "老人"
    elif re.search(r"(我|本人)", text) and not child and not pregnant:
        person = "成人"
    else:
        person = "未知"

    # symptoms
    for term in extract_terms(text):
        if term not in symptoms:
            symptoms.append(term)
    # 按用户提到的先后排序，第一个通常就是主诉
    symptoms.sort(key=lambda x: symptom_pos(text, x))

    # duration
    m = re.search(r"(\d+|一|两|二|三|四|五|六|七|八|九|十|半)\s*(分钟|小时|天|周|个月|年)", text)
    if re.search(r"(一直|刚才|刚刚|突然)", text):
        duration = "一直"
    elif m:
        duration = m.group(1) + m.group(2)
    else:
        duration = "不详"

    # intent
    if flags and (re.search(r"(救命|快|马上|现在|急|打了?\s*120)", text) or "自伤自杀念头" in flags):
        intent = "急救求助"
    elif re.search(r"(吃什么药|能吃药|吃药|吃药吗|抗生素|布洛芬|对乙酰氨基酚|退烧药|消炎药|药物|用药|副作用|美林|泰诺林|泰诺|头孢|阿莫西林|蒙脱石|剂量|几片|一天吃几次)", text)             and not re.search(r"(能喝姜茶|能不能喝)", text):
        intent = "用药咨询"
    elif re.search(r"(焦虑|压力|难过|心情|情绪|想哭|崩溃|烦)", text):
        intent = "情绪困扰"
    elif re.search(r"(熬夜|作息|减肥|运动|饮食|吃什么好|戒烟|喝酒|早睡)", text):
        intent = "生活方式"
    elif re.search(r"(吗|能不能|可不可以|为什么|是什么|有用吗|有用|好不好|要不要|怎么办|怎么处理|如何)", text)             and duration == "不详" and not re.search(r"\d+\s*度|体温", text):
        intent = "科普提问"
    elif symptoms:
        intent = "症状咨询"
    elif re.search(r"(吗|能不能|可不可以|为什么|是什么|有用吗|有用|好不好|要不要|怎么办|怎么处理|如何)", text):
        intent = "科普提问"
    else:
        intent = "其他"
    if flags and intent == "其他":
        intent = "症状咨询"

    # search queries：红旗优先，其次症状
    if flags:
        queries = QUERY_MAP.get(flags[0], ["危险信号 就医", "禁忌 不要做"])
    elif intent == "生活方式":
        queries = ["作息 调整", "睡眠 卫生", "失眠 在家处理"]
    else:
        primary = None
        for s in symptoms:
            if s in QUERY_MAP:
                primary = s
                break
        if child and ("发热" in symptoms or "发烧" in symptoms):
            primary = "儿童发热"
        if primary:
            queries = QUERY_MAP[primary]
        elif symptoms:
            queries = [symptoms[0] + " 就医", symptoms[0] + " 在家处理", symptoms[0] + " 不要做"]
        else:
            queries = [text[:12], "什么时候需要就医"]

    return {
        "intent": intent,
        "red_flags": flags,
        "person": person,
        "symptoms": symptoms,
        "duration": duration,
        "search_queries": queries,
    }


# ---------------------------------------------------------------- M2

def up_one(level):
    order = {"低": 0, "中": 1, "高": 2}
    inv = {0: "低", 1: "中", 2: "高"}
    return inv[min(order[level] + 1, 2)]


DRUG_WORDS = ["硝酸甘油", "阿司匹林", "布洛芬", "对乙酰氨基酚", "抗生素", "头孢",
              "阿莫西林", "奥美拉唑", "蒙脱石", "氯雷他定", "肾上腺素", "地塞米松"]


def safe_items(items):
    out = []
    for it in items:
        if any(d in it for d in DRUG_WORDS) and not re.search(r"(不要|别|避免|禁止|不能|不建议)", it):
            continue
        out.append(it)
    return out


def m2_reason(m1, hits, uncovered, best_score, user_input):
    docs = {d.id: d for d in DOCS}
    if not hits:
        risk = "高" if m1["person"] in ("儿童", "孕妇", "老人") else "中"
        return {
            "risk_level": risk,
            "possible_directions": ["方向不明确"],
            "home_care": ["注意休息，别勉强活动", "少量多次喝水", "把症状变化记下来"],
            "see_doctor_if": ["症状持续不缓解或越来越重", "出现发热、呕吐、意识不清", "出现任何危险信号"],
            "avoid": ["别自己加药、换药", "别硬扛"],
            "evidence": ["未找到依据"],
            "kb_hit": False,
            "uncovered": uncovered,
        }

    top = docs[hits[0]["doc_id"]]
    directions = clean_bullets(top.sections.get("可能原因", ""), label_only=True)[:4]
    home = safe_items(clean_bullets(top.sections.get("在家可以先做", "")))[:4]
    see = safe_items(clean_bullets(top.sections.get("这些情况要就医", "")))[:3]
    avoid = safe_items(clean_bullets(top.sections.get("不要做", "")))[:3]

    base = "低"
    severe_symptoms = {"发热", "发烧", "呕吐", "腹泻", "腹痛", "头晕", "咳嗽", "咽痛", "头痛", "失眠"}
    if any(s in severe_symptoms for s in m1["symptoms"]):
        base = "中"
    if re.search(r"(\d+|两|二|三|四|五|六|七)\s*天", m1["duration"] or "") and not re.search(r"[12一二]\s*天", m1["duration"]):
        base = "中"
    if re.search(r"(越来越重|加重|反复|一直不好|好几天)", user_input):
        base = "中"
    if m1["person"] in ("儿童", "孕妇", "老人"):
        base = up_one(base)
    if m1["person"] == "儿童" and re.search(r"[1-9]\s*个月|新生儿", user_input):
        base = "高"
    if m1["person"] == "孕妇":
        base = "高"

    risk = base
    evidence = [top.title + " · " + hits[0]["section"]]

    return {
        "risk_level": risk,
        "possible_directions": directions,
        "home_care": home,
        "see_doctor_if": see,
        "avoid": avoid,
        "evidence": evidence,
        "kb_hit": True,
        "uncovered": uncovered,
        "summary": top.summary,
    }


# ---------------------------------------------------------------- M3

def trim(s, n):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def to_voice(text):
    v = text
    for a, b in [("120", "幺二零"), ("12356", "幺二三五六"), ("110", "幺幺零"),
                 ("38.5", "三十八度五"), ("39", "三十九"), ("38", "三十八")]:
        v = v.replace(a, b)
    v = re.sub(r"[，。、；：！？（）()《》“”\"'·\-—…*#\n\r\t]", " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    return v


def cut(s, n):
    """按标点截断，尽量不留省略号。"""
    s = re.sub(r"[ 	]+", " ", s).strip()
    if len(s) <= n:
        return s
    head = s[:n]
    for p in ["；", "，", "。", "、"][::-1]:
        i = head.rfind(p)
        if i >= n * 0.6:
            return head[:i].rstrip("：；，、") + "…"
    return head.rstrip("：；，、") + "…"


def lead(summary, n):
    """取结论的第一句，避免半句截断。"""
    s = re.sub(r"[ \t]+", " ", summary).strip()
    for sep in ["；", "。"]:
        i = s.find(sep)
        if 0 < i <= n:
            s = s[:i]
    if len(s) > n:
        s = cut(s, n)
    return s.rstrip("。；，、：…") + "。"


DRUG_NOTE = "具体吃什么药、吃多少，我没法替你定：年龄、体重、有没有基础病、是不是孕期，差别都很大。按说明书上的剂量表，或者医生、药师交代的来；别自己加量，也别把成分相同的药叠着吃。"
DRUG_CHILD = "用药按体重算，别把成人药掰一半给孩子吃。"


def m3_express(m1, m2, user_input):
    empathy = EMPATHY.get(m1["intent"], EMPATHY["其他"])[0]
    NL = chr(10)
    drug_note = ""
    if m1["intent"] == "用药咨询":
        drug_note = DRUG_NOTE + ("孩子这边，" + DRUG_CHILD if m1["person"] == "儿童" else "")

    if not m2["kb_hit"]:
        symptom = m1["symptoms"][0] if m1["symptoms"] else ""
        subject = "就你说的这个情况来说" if symptom in ("怀孕", "孕期", "") else "就" + symptom + "来说"
        lines = [empathy, "这个我暂时没有查到可靠资料。"]
        if drug_note:
            lines.append(drug_note)
        lines.append(subject + "，在明确原因之前，能做的只有：注意休息、别勉强活动、记录症状的变化。"
            "如果持续不缓解、越来越重，或者出现发热、呕吐、意识不清，尽快去医院让医生看。")
        lines.append("别自己对照着症状下结论，也别先吃药压着。")
        lines.append(DISCLAIMER)
        reply = NL.join(lines)
        followups = FOLLOWUPS["默认"]
    else:
        summary = m2.get("summary") or ""
        special = ("考虑到是" + m1["person"] + "，判断要更保守一点。") if m1["person"] in ("儿童", "孕妇", "老人") else ""
        direction = ""
        if m2["possible_directions"] and m2["possible_directions"][0] != "方向不明确":
            direction = "可能的方向是：" + "；".join(cut(x, 18) for x in m2["possible_directions"][:2]) + "。"
        limit = 2 if drug_note else 3
        home = m2["home_care"][:limit]
        see = m2["see_doctor_if"][:2]
        avoid = m2["avoid"][:1 if drug_note else 2]
        extras = []
        if m2["uncovered"]:
            extras.append("关于" + "、".join(m2["uncovered"][:2]) + "，知识库里没有专门讲，这块我不编。")
        if m1["intent"] == "情绪困扰":
            extras.append("如果这种状态持续两周以上，或者已经影响到吃饭、上班，建议去心理科聊聊。")
        if m2["risk_level"] == "高":
            extras.append("你这种情况，建议尽快去医院，别拖到明天。")

        def compose(use_direction=True, use_avoid=True, summary_len=80):
            parts = [empathy]
            if special:
                parts.append(special)
            if summary:
                parts.append(lead(summary, summary_len))
            if use_direction and direction:
                parts.append(direction)
            if drug_note:
                parts.append(NL + drug_note)
            if home:
                parts.append(NL + "你现在可以先做：" + NL + NL.join("· " + cut(x, 40) for x in home))
            if see:
                parts.append(NL + "出现这些情况别拖：" + NL + NL.join("· " + cut(x, 40) for x in see))
            if use_avoid and avoid:
                parts.append(NL + "这几件事先别做：" + NL + NL.join("· " + cut(x, 38) for x in avoid))
            parts.extend(extras)
            parts.append(DISCLAIMER)
            return NL.join(parts)

        reply = compose()
        if len(reply) > 400:
            reply = compose(use_direction=False)
        if len(reply) > 400:
            reply = compose(use_direction=False, use_avoid=False)
        if len(reply) > 400:
            reply = compose(use_direction=False, use_avoid=False, summary_len=48)
        if len(reply) > 400:
            body = reply[:400 - len(DISCLAIMER) - 1].rsplit(NL, 1)[0]
            reply = body + NL + DISCLAIMER
        if len(reply) > 400:
            reply = reply[:400]
        followups = FOLLOWUPS.get(m1["symptoms"][0] if m1["symptoms"] else "默认", FOLLOWUPS["默认"])

    if m2["kb_hit"] and m2["home_care"]:
        core = m1["symptoms"][0] if m1["symptoms"] else "这个情况"
        first = re.sub(r"^先", "", m2["home_care"][0])
        voice = (empathy + " 关于" + core + " 你可以试试 " + cut(first, 26) +
                 " 如果越来越重或者出现危险信号 尽快去医院 我不是医生 这些建议不能替代面诊")
    else:
        voice = "这个我暂时没有查到可靠资料 先注意休息 别勉强活动 症状加重尽快去医院 我不是医生 这些建议不能替代面诊"
    voice = to_voice(voice)
    if len(voice) > 150:
        voice = voice[:150]

    return {"reply_text": reply, "voice_text": voice, "followups": followups[:3]}


# ---------------------------------------------------------------- 安全兜底

EMERGENCY = {
    "压榨性胸痛": (
        "请立即拨打 120，现在就去打。\n胸口发紧、大汗、疼痛放射到手臂，可能是心脏在报警，越早处理越安全。"
        "打完电话后停下所有活动，坐下或半躺，把门打开，等急救人员上来。不要自己开车去医院。\n"
        "别慌，我在。\n" + DISCLAIMER,
        "请立即拨打幺二零 胸口发紧出大汗可能是心脏在报警 现在停下所有活动坐着或半躺等急救人员 不要自己开车 别慌 我在 我不是医生 这些建议不能替代面诊",
        ["打 120 要说哪些信息", "等救护车时能做什么", "最近的胸痛中心在哪"]),
    "呼吸困难": (
        "请立即拨打 120。\n喘不上气、憋气、喉咙发紧，说明氧气可能不够了，这不是忍一忍能过去的事。\n"
        "等车时保持坐位，身体稍微前倾，解开领口和腰带，不要平躺。有人陪着，别独处。\n"
        "别怕，慢慢呼吸，我在。\n" + DISCLAIMER,
        "请立即拨打幺二零 喘不上气说明氧气可能不够了 等车时坐着身体稍微前倾 解开领口不要平躺 有人陪着 慢慢呼吸 我在 我不是医生 这些建议不能替代面诊",
        ["可能是哮喘吗", "等车时能喝水吗", "要带什么去医院"]),
    "意识丧失或晕厥": (
        "先打 120，同时让他平躺、把腿垫高。\n晕倒后叫不醒、或者醒过来又迷糊，都要按急症处理。\n"
        "如果 1 分钟内没有清醒，立刻打 120；清醒后先别让他站起来，原地躺着等急救。\n"
        "你做得对，先别慌。\n" + DISCLAIMER,
        "先打幺二零 让他平躺把腿垫高 一分钟内没清醒立刻打幺二零 醒了也别马上站起来 原地躺着等急救 别慌 我不是医生 这些建议不能替代面诊",
        ["醒了能喝水吗", "晕倒常见原因有哪些", "需要查什么"]),
    "抽搐": (
        "现在打 120，同时把他侧过来躺着。\n抽搐时最危险的是摔伤和呕吐物堵住气道，侧卧能护住这两点。\n"
        "移开周围硬物，头下垫软的东西，不要往嘴里塞任何东西，不要按住他，看着表记时间。\n"
        "抽搐停了也别走开，等急救来。\n" + DISCLAIMER,
        "现在打幺二零 把他侧过来躺着 移开周围硬物 不要往嘴里塞东西 不要按住他 记一下抽了多久 停了也别走开等急救 我不是医生 这些建议不能替代面诊",
        ["抽多久算危险", "以前没抽过怎么办", "要查脑电图吗"]),
    "呕血或黑便": (
        "请马上去急诊，量多或头晕心慌就直接打 120。\n呕血和黑便都可能是消化道出血，拖久了会贫血甚至休克。\n"
        "现在禁食禁水，侧卧、头偏向一边，防止呕吐物呛进气管；把呕吐物或大便拍照带给医生。\n"
        "别自己吃胃药或止痛药。\n" + DISCLAIMER,
        "请马上去急诊 量多或者头晕心慌就直接打幺二零 现在不要吃东西不要喝水 侧卧头偏向一边 把呕吐物或大便拍照带给医生 别自己吃胃药或止痛药 我不是医生 这些建议不能替代面诊",
        ["黑便一定是出血吗", "要做胃镜吗", "路上要注意什么"]),
    "卒中征象": (
        "立刻打 120，一分钟都别等。\n嘴歪、说话不清、一边身子没劲，是脑卒中的典型信号，溶栓有时间窗，越早到医院越好。\n"
        "记下最后一次正常的时间，告诉急救人员；让他平躺、头偏向一侧，不要喂水、喂药、喂任何东西。\n"
        "别摇晃他，也别掐人中。\n" + DISCLAIMER,
        "立刻打幺二零 嘴歪说话不清一边身子没劲是脑卒中的信号 记下最后一次正常的时间告诉急救人员 让他平躺头偏向一侧 不要喂水喂药 别摇晃别掐人中 我不是医生 这些建议不能替代面诊",
        ["发病时间怎么算", "能自己开车去吗", "到医院挂什么科"]),
    "儿童高热惊厥": (
        "马上打 120，同时把孩子侧过来放平。\n孩子发烧时抽搐，多数是热性惊厥，但第一次发作必须让医生看，排除更危险的情况。\n"
        "移开周围硬物，解开衣领，不要往嘴里塞东西，不要掐人中，不要按住手脚，记录开始时间和持续多久。\n"
        "抽完也别离开，等急救。\n" + DISCLAIMER,
        "马上打幺二零 把孩子侧过来放平 移开周围硬物 解开衣领 不要往嘴里塞东西 不要掐人中 记一下抽了多久 抽完也别离开等急救 我不是医生 这些建议不能替代面诊",
        ["以后发烧还会抽吗", "要住院吗", "家里要备什么"]),
    "自伤自杀念头": (
        "我很高兴你愿意说出来，这需要很大的勇气。\n"
        "现在最重要的不是想通什么，是先让自己安全：不要一个人待着，去找一个你信得过的人陪着你，"
        "把药、刀、绳索这些先交给别人收起来。\n"
        "你可以现在就打 12356，全国心理援助热线，24 小时有人接；如果已经准备动手，立刻打 120 或 110。\n"
        "你现在很难受，但这不是你的错，也不是永远。\n" + DISCLAIMER,
        "我很高兴你愿意说出来 现在最重要的不是想通什么 是让自己安全 不要一个人待着 找信得过的人陪着你 把药和刀这些先交给别人 现在就可以打幺二三五六 全国心理援助热线二十四小时有人接 如果已经准备动手 立刻打幺二零或者幺幺零 你现在很难受 但这不是你的错 也不是永远 我不是医生 这些建议不能替代面诊",
        ["热线打不通怎么办", "怎么跟家人开口", "明天能去哪个科"]),
    "严重过敏反应": (
        "立刻打 120，这可能是过敏性休克。\n全身风团、嘴唇眼皮肿、喉咙发紧、声音变哑，说明气道可能正在肿起来，几分钟内就会变化。\n"
        "让他平躺、把腿垫高，不要站起来走动，不要自己开车；如果医生给他开过急救笔，按医生教过的用法用。\n"
        "有人陪着，等急救到场。\n" + DISCLAIMER,
        "立刻打幺二零 这可能是过敏性休克 让他平躺把腿垫高 不要站起来走动 不要自己开车 如果医生开过急救笔 按医生教过的用法用 有人陪着等急救 我不是医生 这些建议不能替代面诊",
        ["过敏原怎么查", "以后还能吃吗", "要带什么去医院"]),
}
EMERGENCY_GENERIC = (
    "请立即拨打 120。\n你描述的情况可能需要现场急救，别等、别自己开车、别先吃点药看看。\n"
    "打完电话保持电话畅通，把门打开，让急救人员能进来；有人陪着你，别独处。\n" + DISCLAIMER,
    "请立即拨打幺二零 你描述的情况可能需要现场急救 别等别自己开车 打完电话保持畅通 把门打开 有人陪着你 我不是医生 这些建议不能替代面诊",
    ["打 120 要说什么", "等车时能做什么", "要带什么证件"],
)


def safety_fallback(m1):
    flag = next((f for f in FLAG_PRIORITY if f in m1["red_flags"]), None)
    reply, voice, followups = EMERGENCY.get(flag, EMERGENCY_GENERIC)
    if flag == "自伤自杀念头":
        risk = "高"
    else:
        risk = "高"
    return {
        "reply_text": reply,
        "voice_text": voice,
        "followups": followups,
        "risk_level": risk,
        "need_emergency": True,
        "kb_hit": True,
        "evidence": [flag or "通用急救"],
    }


# ---------------------------------------------------------------- 主链路

DOCS = load_docs()
INDEX = Index(DOCS)


def run(user_input, simulate_timeout=False):
    trace = {"input": user_input}
    m1 = m1_extract(user_input)
    trace["node_1_user_input"] = user_input
    trace["node_2_m1"] = m1

    if m1["red_flags"] or m1["intent"] == "急救求助":
        trace["node_3_route"] = "安全兜底"
        out = safety_fallback(m1)
        trace["node_8_fallback"] = {"flag": out["evidence"][0]}
        trace["output"] = {k: out[k] for k in ("reply_text", "voice_text", "followups", "risk_level", "need_emergency")}
        trace["output"]["trace"] = {"node_3_route": "安全兜底", "node_2_m1": m1, "node_8_fallback": trace["node_8_fallback"]}
        return trace

    trace["node_3_route"] = "正常流程"
    if simulate_timeout:
        trace["node_6_m2"] = {"error": "timeout", "fallback": "备用模型失败，转保守模式"}
        m2 = {
            "risk_level": "中", "possible_directions": ["方向不明确"],
            "home_care": ["注意休息", "少量多次喝水"],
            "see_doctor_if": ["症状加重或持续不缓解"], "avoid": ["别硬扛"],
            "evidence": ["未找到依据"], "kb_hit": False, "uncovered": [],
        }
        hits, uncovered, best = [], [], 0.0
    else:
        primary = None
        for s_ in m1["symptoms"]:
            if s_ in QUERY_MAP:
                primary = s_
                break
        if m1["person"] == "儿童" and "发热" in m1["symptoms"]:
            primary = "儿童发热"
        hits, uncovered, best = INDEX.retrieve(m1["search_queries"], m1["symptoms"], primary, allow_k1=bool(m1["red_flags"]))
        trace["node_4_kb"] = {"hits": hits, "uncovered": uncovered, "best_score": best, "min_score": 0.45, "topK": 4}
        m2 = m2_reason(m1, hits, uncovered, best, user_input)
        trace["node_6_m2"] = m2

    trace["node_5_task_route"] = "直达温暖表达" if m1["intent"] in ("科普提问", "生活方式", "用药咨询", "情绪困扰", "其他") else "医学推理"
    out = m3_express(m1, m2, user_input)
    trace["node_7_m3"] = out
    trace["output"] = {
        "reply_text": out["reply_text"],
        "voice_text": out["voice_text"],
        "followups": out["followups"],
        "risk_level": m2["risk_level"],
        "need_emergency": False,
    }
    trace["output"]["trace"] = {
        "node_2_m1": m1,
        "node_3_route": "正常流程",
        "node_4_kb": trace.get("node_4_kb"),
        "node_5_task_route": trace["node_5_task_route"],
        "node_6_m2": m2,
    }
    return trace


def main():
    if len(sys.argv) > 1:
        print(json.dumps(run(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
        return
    print("小白 · 健康陪伴智能体（本地网页版引擎）。输入 exit 退出。")
    while True:
        try:
            text = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text in ("exit", "quit", "退出", ""):
            break
        out = run(text)["output"]
        print("\n小白：" + out["reply_text"])
        print("[voice] " + out["voice_text"])
        print("[followups] " + " / ".join(out["followups"]) + "  [risk] " + out["risk_level"])


if __name__ == "__main__":
    main()
