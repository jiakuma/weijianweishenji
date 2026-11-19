"""制度归一化层：Document -> Unit -> Clause，并生成可比对的候选条款对。

三种上传形态在这里合流，不再区分「大文件 / 多小文件 / 混合」：

- 单个大汇编：靠目录页 + 制度清单 + 正文页首标题拆成多个 Unit；
- 多个小文件：每个文件是一个 Unit；
- 大小混合：所有 Document 的 Unit 汇总到同一个 Clause 池，跨 Unit 统一比对。

设计要点（针对旧链路的缺陷）：
1. 不做字符截断、不做固定长度滑窗，条款按「第X条 /（一）」结构边界切分；
2. 每个 Clause 全程携带 source_file / unit_name / page / article，冲突可回溯到原文位置；
3. 候选对由规则倒排召回（关键词 + 数值 + 主体 + 主题），不交给 LLM 预筛，
   避免旧版「预筛返回空数组 -> 0 冲突」的静默失败；
4. 全程统计覆盖率，报告里能区分「真的没有冲突」和「根本没比到」。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from .parser import Page, ParsedDocument

# ------------------------------------------------------------------ 正则 / 词表

_CN_NUM = "一二三四五六七八九十百零〇两0-9"
ARTICLE_RE = re.compile(rf"第[{_CN_NUM}]+条")
CHAPTER_RE = re.compile(rf"第[{_CN_NUM}]+[章节]")
SUBITEM_RE = re.compile(r"^[（(][{_CN_NUM}]+[）)]".format(_CN_NUM=_CN_NUM))
GROUP_RE = re.compile(rf"^\s*第[{_CN_NUM}]+部分")
TOC_ENTRY_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9（）()《》〈〉、，,：:·\-\s]{2,60}?)[…⋯]{2,}(\d{1,4})")
DOTS_RE = re.compile(r"[…⋯]")

# 制度名称常见后缀，用于在没有清单/目录时识别正文页首标题
_NAME_SUFFIX = ("制度", "办法", "规定", "细则", "规范", "规程", "章程", "方案", "标准", "流程", "准则", "要求")
_NAME_END_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9（）()《》]{2,40}?(?:" + "|".join(_NAME_SUFFIX) + r")$")

NUM_UNIT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:万元|元|%|％|天|日|个?工作日|个月|年|次|人|份|小时|周岁|级|倍)"
)
CN_NUM_RE = re.compile(rf"[{_CN_NUM}]+\s*(?:万元|元|天|日|个月|年|次|人|小时)")

ACTORS = [
    "院长", "副院长", "分管领导", "院领导", "所长", "书记", "主任", "副主任",
    "财务部门", "财务科", "财务处", "会计室", "会计人员", "出纳", "经办人", "申请人",
    "部门负责人", "科室负责人", "科室", "各部门", "职能科室", "业务部门", "办公室",
    "院长办公室", "医务办公室", "审计部门", "审计科", "纪检", "监察", "人事部门",
    "采购部门", "采购小组", "资产管理部", "资产管理员", "档案管理员", "保管人",
    "委员会", "领导小组", "领导班子", "主管部门", "上级", "归口管理部门", "使用人",
]

OBLIGATIONS = [
    "应当", "必须", "不得", "禁止", "严禁", "须经", "须由", "需经", "须报", "报经",
    "经……批准", "经批准", "审批后", "事前审批", "先审批", "备案", "审核", "复核",
    "原则上", "一律", "统一", "负责解释", "由……负责", "不得超过", "不低于", "不高于",
]

TOPIC_RULES: list[tuple[str, list[str]]] = [
    ("审批权限", ["审批", "批准", "核准", "授权", "权限", "签批", "会签", "决策", "议事", "集体研究"]),
    ("报销标准", ["报销", "差旅", "补助", "补贴", "费用标准", "限额", "额度", "伙食", "住宿", "交通费", "公务卡"]),
    ("采购流程", ["采购", "招标", "投标", "询价", "比价", "供应商", "验收", "合同签", "遴选"]),
    ("岗位职责", ["职责", "岗位", "分工", "负责", "牵头", "归口", "交接", "离岗", "轮岗"]),
    ("合同管理", ["合同", "协议", "履约", "盖章", "用印", "法务"]),
    ("资产管理", ["资产", "固定资产", "设备", "物资", "盘点", "报废", "处置", "台账", "低值易耗"]),
    ("人事管理", ["招聘", "聘用", "考勤", "请假", "休假", "薪酬", "工资", "绩效", "考核", "培训", "进修", "职称"]),
    ("财务核算", ["预算", "决算", "核算", "账务", "凭证", "票据", "发票", "会计档案", "资金", "收款", "付款", "结算"]),
    ("档案管理", ["档案", "归档", "立卷", "保管期限", "借阅", "销毁"]),
    ("信息安全", ["信息系统", "网络", "口令", "密码", "数据", "保密", "涉密", "权限申请", "日志"]),
]

STOP_CHARS = set("的了和与及或在是为对由并经等关于根据本我院所内其该此项各不有无进行以及或者一个")
KEY_PHRASE_RE = re.compile(
    r"[\u4e00-\u9fa5]{2,8}?(?:管理|审批|报销|采购|验收|登记|备案|审核|拨付|结算|考核|保管|处置|使用|申请|报告|检查|监督|制度|办法|费用|标准|权限|职责|档案|合同|资产)"
)


# ------------------------------------------------------------------ 数据结构

@dataclass
class Clause:
    clause_id: str
    unit_id: str
    unit_name: str
    source_file: str
    page: int | None = None
    article: str = ""
    chapter: str = ""
    text: str = ""
    keywords: list[str] = field(default_factory=list)
    actors: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    obligations: list[str] = field(default_factory=list)
    topic: str = "其他"

    def to_dict(self) -> dict:
        return asdict(self)

    def locate(self) -> str:
        """给报告/前端用的一行定位。"""
        parts = [self.source_file]
        if self.unit_name and self.unit_name != self.source_file:
            parts.append(self.unit_name)
        if self.page:
            parts.append(f"第{self.page}页")
        if self.article:
            parts.append(self.article)
        return " · ".join(parts)


@dataclass
class Unit:
    unit_id: str
    name: str
    source_file: str
    start_page: int | None = None
    end_page: int | None = None
    group: str = ""
    origin: str = "single"
    text: str = ""
    clauses: list[Clause] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["clauses"] = [c.to_dict() for c in self.clauses]
        return d

    def summary(self) -> dict:
        return {
            "unit_id": self.unit_id,
            "name": self.name,
            "source_file": self.source_file,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "group": self.group,
            "origin": self.origin,
            "chars": len(self.text),
            "clause_count": len(self.clauses),
            "note": self.note,
        }


@dataclass
class Corpus:
    units: list[Unit] = field(default_factory=list)
    clauses: list[Clause] = field(default_factory=list)
    manifest_items: list[dict] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)

    def add(self, unit: Unit) -> None:
        self.units.append(unit)
        self.clauses.extend(unit.clauses)

    def units_summary(self) -> list[dict]:
        return [u.summary() for u in self.units]


# ------------------------------------------------------------------ 目录 / 页眉

def is_toc_page(text: str) -> bool:
    """目录页特征：大量点线引导符（每页通常 800+ 个）。"""
    if not text:
        return False
    return len(DOTS_RE.findall(text)) >= 100


def infer_header(filename: str) -> str:
    """从大汇编文件名推断页眉，如 综合管理制度2025.pdf -> 综合管理制度。"""
    stem = filename.rsplit(".", 1)[0]
    stem = re.sub(r"20\d{2}$", "", stem).strip()
    return stem if 2 <= len(stem) <= 12 else ""


def strip_header(line: str, header: str) -> str:
    if header and line.startswith(header):
        return line[len(header):].strip()
    return line.strip()


def parse_toc(pages: list[Page], header: str = "") -> list[dict]:
    """解析「制度名 ……… 页码」目录，返回 [{name, toc_page, group}]（不含章节行）。"""
    entries: list[dict] = []
    group = ""
    for page in pages:
        if not is_toc_page(page.text):
            continue
        # 按行解析：目录条目靠引导符连接，常多个挤在同一视觉行；
        # 若跨行匹配会把页眉/上一行文字粘进制度名。
        for line in page.text.split("\n"):
            for raw_name, raw_no in TOC_ENTRY_RE.findall(line):
                name = re.sub(r"\s+", "", strip_header(raw_name, header))
                name = name.strip("　 ·:：")
                if not name or len(name) > 45:
                    continue
                if GROUP_RE.match(name):
                    group = name
                    continue
                if CHAPTER_RE.match(name) or name.endswith("目录"):
                    continue
                entries.append({"name": name, "toc_page": int(raw_no), "group": group})
    # 同名去重（目录里偶尔重复列一次），保留首次
    seen: set[str] = set()
    out = []
    for e in entries:
        if e["name"] in seen:
            continue
        seen.add(e["name"])
        out.append(e)
    return out


def _heading_from_page(page: Page, header: str) -> str:
    """正文页首行的「页眉+制度名」-> 制度名；不是标题页则返回空。"""
    if not page.text:
        return ""
    first = page.text.split("\n", 1)[0].strip()
    first = strip_header(first, header)
    if not first or len(first) > 42:
        return ""
    if any(ch in first for ch in "。；，,;:："):
        return ""
    if DOTS_RE.search(first) or ARTICLE_RE.search(first) or CHAPTER_RE.match(first):
        return ""
    if _NAME_END_RE.match(first):
        return first
    return ""


def infer_offset(toc: list[dict], body_pages: list[Page], header: str) -> tuple[int, int]:
    """自动推断「目录书页 -> PDF 真实页」偏移，返回 (offset, 命中样本数)。

    做法：把目录前若干制度名与正文页首标题对齐，取 pdf_page - toc_page 的众数。
    旧脚本把 OFFSET=14 写死，换一本汇编就全错，这里改成数据驱动。
    """
    diffs: list[int] = []
    headings: dict[int, str] = {}
    for p in body_pages:
        h = _heading_from_page(p, header)
        if h:
            headings[p.number or -1] = h
    for e in toc[:60]:
        name = e["name"]
        for pno, h in headings.items():
            if h == name or h.endswith(name) or name.endswith(h):
                diffs.append(pno - e["toc_page"])
                break
    if not diffs:
        return 0, 0
    offset, hits = Counter(diffs).most_common(1)[0]
    return offset, hits


# ------------------------------------------------------------------ 特征抽取

def _clean_article_prefix(line: str) -> str:
    """汇编 PDF 常见「第一条第一条为…」「第三条第三条…」，折叠重复编号。"""
    m = ARTICLE_RE.match(line)
    if not m:
        return line
    tag = m.group(0)
    rest = line[m.end():]
    while rest.startswith(tag):
        rest = rest[len(tag):]
    return tag + rest.lstrip()


def extract_features(text: str, context: str = "") -> dict:
    """规则特征：context 传「制度名 + 章名」，用于给条款补主题与关键词。"""
    numbers = NUM_UNIT_RE.findall(text) + CN_NUM_RE.findall(text)
    numbers = [re.sub(r"\s+", "", n) for n in numbers if n.strip()]
    actors = [a for a in ACTORS if a in text]
    obligations = [o for o in OBLIGATIONS if o in text or (o.startswith("经") and "批准" in text)]

    scores: dict[str, int] = {}
    for topic, words in TOPIC_RULES:
        scores[topic] = sum(1 for w in words if w in text)
        # 条款本身没写主题词时，用所属制度/章节名兜底（权重较低）
        if scores[topic] == 0 and context and any(w in context for w in words[:6]):
            scores[topic] = 1
    topic = max(scores, key=lambda k: scores[k]) if max(scores.values(), default=0) > 0 else "其他"

    phrases = [
        p for p in KEY_PHRASE_RE.findall(text)
        if 3 <= len(p) <= 10 and not ARTICLE_RE.match(p) and not CHAPTER_RE.match(p)
    ]
    context_words = [
        w for _, words in TOPIC_RULES for w in words if context and w in context
    ]
    keywords = list(dict.fromkeys(
        [p for p in phrases if not any(c in STOP_CHARS for c in p[:1])]
        + [w for _, words in TOPIC_RULES for w in words if w in text]
        + context_words[:6]
        + numbers[:6]
        + actors[:6]
    ))
    return {
        "keywords": keywords[:24],
        "actors": list(dict.fromkeys(actors))[:8],
        "numbers": list(dict.fromkeys(numbers))[:8],
        "obligations": list(dict.fromkeys(obligations))[:8],
        "topic": topic,
    }


# ------------------------------------------------------------------ 条款切分

def split_clauses(unit: Unit) -> list[Clause]:
    """按「第X条」切；无条号时退化为「（一）/ 一、/ 段落」切分。"""
    lines = [ln.strip() for ln in unit.text.split("\n") if ln.strip()]
    blocks: list[dict] = []
    chapter = ""
    cur: dict | None = None
    has_article = any(ARTICLE_RE.match(_clean_article_prefix(ln)) for ln in lines)

    for raw in lines:
        line = _clean_article_prefix(raw)
        if CHAPTER_RE.match(line) and len(line) <= 24:
            chapter = line
            continue
        if GROUP_RE.match(line) and len(line) <= 30:
            continue
        am = ARTICLE_RE.match(line) if has_article else None
        sub = bool(SUBITEM_RE.match(line)) if not has_article else False
        if am or sub:
            if cur and cur["lines"]:
                blocks.append(cur)
            cur = {
                "article": am.group(0) if am else line[:6],
                "chapter": chapter,
                "page": None,
                "lines": [line],
            }
        elif cur is not None:
            cur["lines"].append(line)
        else:
            # 条号出现前的引言/制定依据，也作为一个可比对条款
            cur = {"article": "", "chapter": chapter, "page": None, "lines": [line]}
    if cur and cur["lines"]:
        blocks.append(cur)

    # 页码：用条款原文在 Unit 各页里首次出现的位置定位（页级精度足够）
    page_of_line = _line_page_index(unit)

    clauses: list[Clause] = []
    for k, b in enumerate(blocks):
        body = " ".join(b["lines"]).strip()
        body = re.sub(r"\s+", " ", body)
        if len(body) < 12:
            continue
        if len(body) > 1200:
            body = body[:1200]
        page = None
        probe = b["lines"][0][:18]
        if probe:
            page = page_of_line.get(probe)
        core = re.sub(r"(?:制度|办法|规定|细则|规范|规程|章程|方案|标准|流程|准则|要求)$", "", unit.name)
        chapter_clean = re.sub(rf"^第[{_CN_NUM}]+[章节]\s*", "", b["chapter"] or "")
        feats = extract_features(body, context=f"{core} {unit.name} {chapter_clean}")
        # 制度核心词/章名是强特征，置顶保证不被 [:24] 截断
        extra = [x for x in (core, chapter_clean) if 2 <= len(x) <= 12]
        feats["keywords"] = list(dict.fromkeys(extra + feats["keywords"]))[:24]
        clauses.append(Clause(
            clause_id=f"{unit.unit_id}-C{k + 1:03d}",
            unit_id=unit.unit_id,
            unit_name=unit.name,
            source_file=unit.source_file,
            page=page,
            article=b["article"],
            chapter=b["chapter"],
            text=body,
            **feats,
        ))
    return clauses


def _line_page_index(unit: Unit) -> dict[str, int]:
    """Unit 构造时把每页首 18 字映射回页码，供条款快速定位。"""
    idx: dict[str, int] = {}
    for pno, line in getattr(unit, "_page_lines", []):
        key = line[:18]
        if key and key not in idx:
            idx[key] = pno
    return idx


# ------------------------------------------------------------------ 汇编拆分

def _build_unit(
    uid: str, name: str, doc: ParsedDocument, pages: list[Page],
    group: str, origin: str, header: str,
) -> Unit:
    kept: list[str] = []
    page_lines: list[tuple[int, str]] = []
    for p in pages:
        for ln in p.text.split("\n"):
            ln = ln.strip()
            if not ln:
                continue
            if header and ln == header:
                continue  # 单独成行的页眉
            ln = strip_header(ln, header)
            ln = _clean_article_prefix(ln)
            if not ln:
                continue
            kept.append(ln)
            page_lines.append((p.number or 0, ln))
    text = "\n".join(kept)
    u = Unit(
        unit_id=uid,
        name=name,
        source_file=doc.filename,
        start_page=pages[0].number if pages else None,
        end_page=pages[-1].number if pages else None,
        group=group,
        origin=origin,
        text=text,
    )
    setattr(u, "_page_lines", page_lines)
    u.clauses = split_clauses(u)
    return u


def _split_compendium(doc: ParsedDocument, toc: list[dict], manifest: list[dict], corpus: Corpus) -> int:
    """把一份汇编 PDF 拆成多个 Unit；返回识别到的 Unit 数。"""
    header = infer_header(doc.filename)
    toc_page_nos = {p.number for p in doc.pages if is_toc_page(p.text)} if doc.pages else set()
    body = [p for p in doc.pages if p.number not in toc_page_nos and p.text.strip()]
    if not body:
        return 0

    offset, offset_hits = infer_offset(toc, body, header) if toc else (0, 0)
    # 目录失效时，用「最后一个目录页」当正文起点反推偏移
    if offset_hits < 3 and toc and body:
        first_toc_no = min(e["toc_page"] for e in toc)
        offset = (body[0].number or 1) - first_toc_no
        offset_hits = 0

    # 1) 标题锚点：正文页首的「页眉+制度名」，最可靠
    anchors: dict[int, dict] = {}
    for p in body:
        h = _heading_from_page(p, header)
        if h:
            anchors[p.number] = {"name": h, "origin": "heading", "group": ""}

    # 2) 清单 / 目录锚点：补上标题识别漏掉的制度，并校正名字与分组
    known = manifest or [{"name": e["name"], "group": e["group"]} for e in toc]
    known_origin = "manifest" if manifest else "toc"
    group_by_name = {re.sub(r"\s+", "", x["name"]): x.get("group", "") for x in known}
    for e in toc:
        pdf_page = e["toc_page"] + offset
        if pdf_page < (body[0].number or 1) or pdf_page > (body[-1].number or 10 ** 6):
            continue
        if pdf_page not in anchors:
            anchors[pdf_page] = {"name": e["name"], "origin": "toc", "group": e["group"]}
        else:
            anchors[pdf_page]["group"] = anchors[pdf_page].get("group") or e["group"]
            if e["name"] and e["name"] != anchors[pdf_page]["name"]:
                anchors[pdf_page]["name"] = e["name"]
                anchors[pdf_page]["origin"] = "toc"

    # 2b) 清单里未被目录/标题命中的制度名，在正文页首做一次保守匹配。
    #     目录可能漏列、页首标题也可能 OCR 失真，清单是最权威的制度名单。
    matched = {re.sub(r"\s+", "", a["name"]) for a in anchors.values()}
    for item in known:
        name = re.sub(r"\s+", "", item["name"])
        if not name or name in matched:
            continue
        for p in body:
            head = re.sub(r"\s+", "", strip_header(p.text.split("\n", 1)[0], header))
            if head == name or (head.endswith(name) and 0 < len(head) - len(name) <= 12):
                anchors.setdefault(p.number, {
                    "name": name, "origin": known_origin, "group": item.get("group", ""),
                })
                matched.add(name)
                break

    # 3) 无目录无清单时，用「第一条」重置点兜底
    if len(anchors) < 2:
        for p in body:
            if ARTICLE_RE.match(_clean_article_prefix(p.text.split("\n", 1)[0])) or any(
                ARTICLE_RE.match(_clean_article_prefix(ln)) and ln.startswith("第一条")
                for ln in p.text.split("\n")[:3]
            ):
                anchors.setdefault(p.number, {
                    "name": _heading_from_page(p, header) or f"{doc.filename} 第{p.number}页起",
                    "origin": "article-reset", "group": "",
                })

    # 4) 清单名字校正：页首标题若在清单里，补全分组并标记为权威
    for pno, a in anchors.items():
        key = re.sub(r"\s+", "", a["name"])
        if key in group_by_name:
            a["group"] = a["group"] or group_by_name[key]
            if a["origin"] == "heading":
                a["origin"] = known_origin

    if not anchors:
        return 0

    by_number: dict[int, Page] = {p.number: p for p in doc.pages}
    ordered = sorted(anchors)
    first_body, last_body = body[0].number, body[-1].number
    count = 0
    for i, start in enumerate(ordered):
        end = (ordered[i + 1] - 1) if i + 1 < len(ordered) else last_body
        if end < start:
            continue
        seg = [by_number[n] for n in range(start, end + 1) if n in by_number and n not in toc_page_nos]
        if not seg:
            continue
        a = anchors[start]
        uid = f"U{len(corpus.units) + 1:04d}"
        u = _build_unit(uid, a["name"], doc, seg, a["group"], a["origin"], header)
        if not u.clauses:
            u.note = "未切出条款"
        corpus.add(u)
        count += 1
    return count


def _split_single(doc: ParsedDocument, corpus: Corpus) -> None:
    """小文件：整个文件视为一个 Unit，制度名从首行标题或文件名推断。"""
    header = ""
    first_line = ""
    for p in doc.pages:
        for ln in p.text.split("\n"):
            ln = ln.strip()
            if len(ln) >= 4:
                first_line = ln
                break
        if first_line:
            break
    # 公文小文件名通常是「单位名+〔年〕序号+号 制度名.pdf」，文号之后才是制度名；
    # 若直接从正文首行猜，会把单位名和文号一起吞进去（旧结果里出现过
    # 「001号北京市神经外科研究所财务电子信息…」这种名字）。
    stem = re.sub(r"\s+", "", doc.filename.rsplit(".", 1)[0])
    m = re.search(r"\d+号([\u4e00-\u9fa5A-Za-z0-9（）()《》]{4,40})$", stem)
    name = m.group(1) if m else ""
    if not name:
        head = re.sub(r"\s+", "", first_line)[-30:]
        m = re.search(
            r"([\u4e00-\u9fa5]{3,20}(?:制度|办法|规定|细则|规范|规程|章程|方案|标准|流程|准则|要求))$",
            head,
        )
        name = m.group(1) if m else (stem or doc.filename)
    uid = f"U{len(corpus.units) + 1:04d}"
    u = _build_unit(uid, name, doc, doc.pages, "", "single-file", header)
    if u.clauses:
        # 第一页里若同时出现多个制度标题，说明其实是小汇编，仍按单 Unit 处理并记录
        u.note = ""
    corpus.add(u)


# ------------------------------------------------------------------ 主入口

def normalize(
    parsed_docs: list[ParsedDocument],
    manifest_items: list[dict] | None = None,
    max_compendium_pages: int = 25,
) -> Corpus:
    """把若干物理文件归一化成 Unit/Clause 池。

    parsed_docs: parse_document() 的结果（xlsx 应已在调用方过滤，仅把 read_manifest 结果传进来）
    manifest_items: read_manifest() 的返回值，作为权威制度名清单
    """
    corpus = Corpus(manifest_items=list(manifest_items or []))
    docs = [d for d in parsed_docs if d.pages and not d.error]
    good_ids = {id(d) for d in docs}
    skipped_docs = [
        {"filename": d.filename, "reason": d.error or d.note or "无正文"}
        for d in parsed_docs if id(d) not in good_ids
    ]

    toc_by_doc: dict[str, list[dict]] = {}
    for d in docs:
        toc = parse_toc(d.pages, infer_header(d.filename))
        toc_by_doc[d.filename] = toc
        is_big = len(d.pages) >= max_compendium_pages
        n = _split_compendium(d, toc, corpus.manifest_items, corpus) if is_big else 0
        if n == 0:
            _split_single(d, corpus)

    # ---------- 覆盖率统计 ----------
    all_pages = sum(len(d.pages) for d in parsed_docs)
    toc_pages = sum(
        1 for d in parsed_docs for p in d.pages if is_toc_page(p.text)
    )
    covered_pages = sum(
        (u.end_page - u.start_page + 1)
        for u in corpus.units if u.start_page and u.end_page
    )
    empty_units = [u.unit_id for u in corpus.units if not u.clauses]
    origin_count = Counter(u.origin for u in corpus.units)
    corpus.coverage = {
        "documents": len(parsed_docs),
        "documents_parsed": len(docs),
        "documents_skipped": skipped_docs,
        "manifest_items": len(corpus.manifest_items),
        "toc_entries": sum(len(v) for v in toc_by_doc.values()),
        "toc_pages": toc_pages,
        "units": len(corpus.units),
        "units_by_origin": dict(origin_count),
        "clauses": len(corpus.clauses),
        "clauses_by_unit": {u.unit_id: len(u.clauses) for u in corpus.units},
        "units_without_clause": empty_units,
        "pages_total": all_pages,
        "pages_covered": covered_pages,
        "pages_unassigned": max(0, all_pages - toc_pages - covered_pages),
        "page_coverage_rate": round(min(1.0, covered_pages / max(1, all_pages - toc_pages)), 3),
        "candidate_pairs": 0,
        "compared_pairs": 0,
        "skipped_pairs": {},
    }
    return corpus


# ------------------------------------------------------------------ 候选对召回

def build_candidates(
    clauses: list[Clause],
    max_pairs: int = 300,
    top_k_per_clause: int = 8,
    min_shared: int = 1,
    per_unit_pair: int = 3,
    sim_threshold: float = 0.62,
    unit_pair_filter: set | None = None,
) -> tuple[list[tuple[int, int, float, list[str]]], int, bool]:
    """语义 + 关键词混合召回：跨制度条款对。

    - 语义通道：本地 bge 向量余弦相似度 >= sim_threshold 才进入候选池
      （解决「差旅费 vs 出差费用」换词漏检），阈值让候选池自然收敛；
    - 关键词通道：共享关键词/数值/主体（兜底，语义模型不可用时独立工作）；
    - 语义分主导排序，关键词分经饱和函数折算后叠加；
    - unit_pair_filter：两阶段粗判产出的「管辖重叠」制度对集合，
      传入后只在该范围内召回；
    - 返回 (pairs, total_candidates, semantic_used)。
    不做 LLM 预筛 —— 旧版预筛失败会静默返回 0 对，导致「0 冲突」假象。
    """
    n = len(clauses)
    if n < 2:
        return [], 0, False

    # 1) 关键词倒排
    index: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(clauses):
        for kw in set(c.keywords):
            if len(kw) >= 2:
                index[kw].append(i)
    # 过长倒排（如「管理」）区分度低且组合爆炸，直接丢弃；其余按 df 降权
    kw_scores: dict[tuple[int, int], tuple[float, list[str]]] = {}
    for kw, ids in index.items():
        df = len(ids)
        if df < 2 or df > 200 or df > n * 0.35:
            continue
        weight = 1.0 / (1.0 + 0.05 * max(0, df - 3))
        if NUM_UNIT_RE.fullmatch(kw) or CN_NUM_RE.fullmatch(kw):
            weight *= 1.6
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                i, j = ids[x], ids[y]
                if clauses[i].unit_id == clauses[j].unit_id:
                    continue  # 同一制度内部不自比
                key = (i, j)
                prev = kw_scores.get(key)
                if prev is None:
                    kw_scores[key] = (weight, [kw])
                else:
                    kw_scores[key] = (prev[0] + weight, prev[1] + [kw])

    # 2) 语义通道
    vecs = None
    if n <= 10000:  # 相似度矩阵内存保护
        try:
            from .embed import available, encode

            if available():
                vecs = encode([c.text for c in clauses])
        except Exception:
            vecs = None

    def _sat(score: float) -> float:
        """关键词分饱和折算到 0~6，避免压过语义主导分。"""
        return 6.0 * score / (score + 6.0)

    merged: dict[tuple[int, int], tuple[float, list[str]]] = {}
    for (i, j), (s, shared) in kw_scores.items():
        merged[(i, j)] = (_sat(s), list(shared))
    semantic_used = False
    if vecs is not None:
        import numpy as np

        m = np.asarray(vecs, dtype=np.float32)
        norm = np.linalg.norm(m, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        m = m / norm
        sim = m @ m.T
        iu = np.triu_indices(n, 1)
        pair_sims = sim[iu]
        cand = np.where(pair_sims >= sim_threshold)[0]
        if len(cand) > 60000:  # 极端情况只保留相似度最高的
            cand = cand[np.argsort(-pair_sims[cand])][:60000]
        semantic_used = bool(len(cand))
        for idx in cand:
            i, j = int(iu[0][idx]), int(iu[1][idx])
            if clauses[i].unit_id == clauses[j].unit_id:
                continue
            s = float(pair_sims[idx])
            tag = f"语义相似 {s:.2f}"
            prev = merged.get((i, j))
            if prev is None:
                merged[(i, j)] = (s * 6.0, [tag])
            else:
                merged[(i, j)] = (prev[0] + s * 6.0, [tag] + prev[1])

    # 3) 组装排序：语义/关键词基础分 + 规则加分
    ranked: list[tuple[int, int, float, list[str]]] = []
    for (i, j), (base, shared) in merged.items():
        if unit_pair_filter is not None:
            upair = frozenset((clauses[i].unit_id, clauses[j].unit_id))
            if upair not in unit_pair_filter:
                continue
        a, b = clauses[i], clauses[j]
        if len(shared) < min_shared:
            continue
        score = base + len(shared) * 0.35
        if a.topic == b.topic and a.topic != "其他":
            score += 1.2
        if a.numbers and b.numbers:
            score += 1.0
        if a.actors and b.actors and set(a.actors) != set(b.actors):
            score += 0.8
        if a.obligations and b.obligations:
            score += 0.5
        # 同一物理文件但不同制度（汇编内互比）是重点场景，轻微提权
        if a.source_file == b.source_file:
            score += 0.3
        ranked.append((i, j, round(score, 3), shared[:8]))

    ranked.sort(key=lambda x: x[2], reverse=True)

    # 4) 配额截断：每个条款最多贡献 top_k 个对手；同一对制度的配额默认 3 条，
    #    但制度组合数少时（如多个小文件只有 10 种组合）自动放宽，
    #    保证 max_pairs 预算能被现有组合用满。
    upair_seen = {tuple(sorted((clauses[i].unit_id, clauses[j].unit_id))) for i, j, _, _ in ranked}
    pair_limit = max(per_unit_pair, -(-max_pairs // max(1, len(upair_seen))))
    used: Counter = Counter()
    unit_pair_used: Counter = Counter()
    capped = []
    for item in ranked:
        i, j = item[0], item[1]
        if used[i] >= top_k_per_clause or used[j] >= top_k_per_clause:
            continue
        upair = (clauses[i].unit_id, clauses[j].unit_id)
        if upair[0] > upair[1]:
            upair = (upair[1], upair[0])
        if unit_pair_used[upair] >= pair_limit:
            continue
        used[i] += 1
        used[j] += 1
        unit_pair_used[upair] += 1
        capped.append(item)
        if len(capped) >= max_pairs:
            break
    return capped, len(ranked), semantic_used
