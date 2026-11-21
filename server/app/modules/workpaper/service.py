"""底稿审核核心流水线：解析 -> 角色识别 -> 提取审计事项 -> 逐项匹配底稿并判定 -> 报告。

输入 uploaded = [(filename, bytes), ...]。扫描/OCR 沿用 conflict/parser.py；
.doc 审计方案通过本机 Word COM 读取（Windows 环境），docx/txt/pdf 走统一解析器。

报告以审计方案「审计内容和重点」中的一级事项为审核要点，一项对应一份底稿，
逐项给出「通过 / 不通过 / 无法判断」及一段连贯的原因说明，最后汇总总体结论。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ...llm import LLMConfig, OFFICIAL_MODEL, chat_url, normalize_base_url, post_chat
from ..conflict.parser import parse_document
from .prompts import (
    REPORT_ASSESS_SYSTEM, REPORT_ASSESS_USER, ROLE_SYSTEM, ROLE_USER,
    TOPIC_SYSTEM, TOPIC_USER,
)

PARSE_WORKERS = 4
ROLE_EXCERPT = 900
WORKPAPER_TEXT_LIMIT = 16000
PLAN_TEXT_LIMIT = 9000
TOPIC_PLAN_TEXT_LIMIT = 16000
TOPIC_PLAN_EXCERPT_LIMIT = 3000
TOPIC_LIMIT = 12
_DOC_LOCK = threading.Lock()
DISCLAIMER = ("本报告为文本层面的底稿复核提示，不构成审计结论；"
              "是否形成问题须由审计人员结合证据复核后确定。")


class LLMClient:
    """复用系统设置的 OpenAI 兼容接口。"""

    def __init__(self, base_url: str, api_key: str, model: str):
        base = normalize_base_url(base_url)
        self.url = chat_url(base)
        self.model = model or OFFICIAL_MODEL
        self.cfg = LLMConfig(base, api_key, self.model, 0.0, 4096, False)

    def chat(self, system: str, user: str):
        import httpx

        data = post_chat(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0,
            },
            timeout=120.0, config=self.cfg,
            client=httpx.Client(timeout=120.0),
        )
        choices = data.get("choices") or [{}]
        content = (choices[0].get("message", {}).get("content") or "").strip()
        if not content:
            raise RuntimeError("模型返回空内容")
        return _extract_json(content)


def _extract_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "[{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                return obj
            except Exception:
                continue
    raise ValueError("模型未返回可解析的 JSON")


def _update(task, stage=None, done=None, total=None):
    if not task:
        return
    if stage is not None:
        task["stage"] = stage
    if done is not None:
        task["done"] = done
    if total is not None:
        task["total"] = total


def _parse_doc_com(raw: bytes) -> str:
    """用本机 Word 读取 .doc；COM 非线程安全，进程内串行并初始化。"""
    import pythoncom
    import win32com.client as win32

    fd, path = tempfile.mkstemp(suffix=".doc")
    os.close(fd)
    try:
        with open(path, "wb") as f:
            f.write(raw)
        last = None
        for _ in range(3):
            try:
                with _DOC_LOCK:
                    pythoncom.CoInitialize()
                    app = None
                    doc = None
                    try:
                        app = win32.Dispatch("Word.Application")
                        app.Visible = False
                        app.DisplayAlerts = 0
                        doc = app.Documents.Open(path, ReadOnly=True, AddToRecentFiles=False)
                        return (doc.Content.Text or "").strip()
                    finally:
                        if doc is not None:
                            try:
                                doc.Close(False)
                            except Exception:
                                pass
                        if app is not None:
                            try:
                                app.Quit()
                            except Exception:
                                pass
                        pythoncom.CoUninitialize()
            except Exception as exc:
                last = exc
                time.sleep(0.6)
        raise last
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _parse_one(name: str, raw: bytes) -> dict:
    base = os.path.basename(name)
    if base.startswith("._"):
        return {"name": base, "text": "", "skip": True}
    ext = base.lower().rsplit(".", 1)[-1] if "." in base else ""
    try:
        if ext == "doc":
            text = _parse_doc_com(raw)
        else:
            text = parse_document(base, raw).text
        return {"name": base, "text": (text or "").strip(), "error": ""}
    except Exception as exc:
        return {"name": base, "text": "", "error": f"{type(exc).__name__}: {exc}"}


def _parse_all(uploaded, task) -> list[dict]:
    _update(task, stage="解析文件")
    out = []
    with ThreadPoolExecutor(max_workers=PARSE_WORKERS) as pool:
        futures = [pool.submit(_parse_one, n, b) for n, b in uploaded]
        for fut in as_completed(futures):
            out.append(fut.result())
    return [f for f in out if not f.get("skip")]


def _unit_of(name: str, text: str) -> str:
    m = re.search(r"被审计单位[：:]\s*([^\s\n\r]{2,40})", text or "")
    if m:
        return m.group(1).strip("。，,；;）)")
    m = re.search(r"(北京[^\\\n\r]{0,20}(?:研究所|医院|中心|学院|管理局|委员会))", name)
    if m:
        return m.group(1)
    return ""


def _topic_of(name: str) -> str:
    s = os.path.basename(name)
    s = re.sub(r"\.(docx?|pdf|txt|md)$", "", s, flags=re.I)
    s = re.sub(r"^\s*\d+[\.、]?\s*", "", s)
    s = s.replace("《》", "")
    s = s.replace("审计工作底稿", "")
    s = re.sub(r"^\s*[-－—_]+", "", s).strip()
    s = re.sub(r"\s*-\s*副本$", "", s)
    return s.strip("-_— ") or s


def _file_sort_key(name: str) -> tuple:
    m = re.match(r"\s*(\d+)", os.path.basename(name))
    return (0, int(m.group(1)), name) if m else (1, 0, name)


def _identify_roles(files: list[dict], llm) -> tuple[list[dict], list[dict]]:
    """先用文件名与正文特征分类，歧义文件再交给模型。"""
    plans, workpapers, others = [], [], []
    for f in files:
        name, text = f["name"], f.get("text", "")
        head = text[:ROLE_EXCERPT]
        if "审计实施方案" in name or "审计方案" in name or "审计通知书" in name:
            plans.append(f)
        elif "审计工作底稿" in name or "审计工作底稿" in head:
            workpapers.append(f)
        else:
            others.append(f)

    if others and llm is not None:
        items = "\n".join(
            f"- {f['name']}：{f.get('text', '')[:ROLE_EXCERPT]}" for f in others)
        try:
            raw = llm.chat(ROLE_SYSTEM, ROLE_USER.format(items=items))
            for row in raw if isinstance(raw, list) else []:
                if not isinstance(row, dict):
                    continue
                target = next((x for x in others if x["name"] == str(row.get("name") or "")), None)
                if not target:
                    continue
                role = str(row.get("role") or "other")
                if role == "plan":
                    plans.append(target)
                elif role == "workpaper":
                    workpapers.append(target)
        except Exception:
            pass

    for f in plans:
        f["role"] = "plan"
        f["unit"] = _unit_of(f["name"], f.get("text", ""))
        f["topic"] = ""
    for f in workpapers:
        f["role"] = "workpaper"
        f["unit"] = _unit_of(f["name"], f.get("text", ""))
        f["topic"] = _topic_of(f["name"])
    workpapers.sort(key=lambda w: _file_sort_key(w["name"]))
    return plans, workpapers


def _plan_excerpt(plan: dict | None, topic: str, limit: int = PLAN_TEXT_LIMIT) -> str:
    text = re.sub(r"\n{3,}", "\n\n", (plan or {}).get("text", "").strip())
    if not text:
        return "（未提供对应审计实施方案）"
    if len(text) <= limit:
        return text
    if topic:
        keys = [k for k in re.split(r"[、，,；;（）()\s]+", topic) if len(k) >= 2]
        for k in keys[:4]:
            at = text.find(k)
            if at >= 0:
                start = max(0, at - limit // 3)
                return text[start:start + limit]
    return text[:limit]


def _topic_plan_excerpt(plan: dict | None, topic: str, limit: int = TOPIC_PLAN_EXCERPT_LIMIT) -> str:
    """截取方案「审计内容和重点」中该事项的正文，避免混入后续审计程序章节。"""
    text = re.sub(r"\r\n?", "\n", (plan or {}).get("text", "") or "")
    if not text.strip():
        return "（未提供对应审计实施方案）"
    body = text
    heading = re.search(r"(?:^|\n)\s*审计内容和重点\s*\n", text)
    if heading:
        body = text[heading.start():]
    next_chapter = re.search(
        r"\n\s*(?:审计程序及措施|审计程序|审计策略|审计工作组织与人员安排)\s*\n", body)
    if next_chapter:
        body = body[:next_chapter.start()]

    title = str(topic or "").strip()
    start = body.find(title) if title else -1
    if start < 0:
        # 方案表述与底稿标题略有出入时，按字符重合度定位最接近的一行。
        chars = {c for c in re.sub(r"[\s、，,；;：:（）()《》“”]", "", title) if c.strip()}
        best_pos, best_hit, pos = -1, 0, 0
        for line in body.split("\n"):
            hit = sum(1 for c in chars if c in line)
            if hit > best_hit:
                best_pos, best_hit = pos, hit
            pos += len(line) + 1
        if chars and best_hit >= max(2, len(chars) // 2):
            start = best_pos
    if start < 0:
        return _plan_excerpt(plan, topic, limit)

    segment = body[start:]
    tail = segment[len(title):] if title else segment
    next_topic = re.search(r"\n\s*（[一二三四五六七八九十百]+）", tail)
    if next_topic:
        segment = segment[:len(title) + next_topic.start()]
    return segment[:limit].strip()


def _numbered(text: str, limit: int) -> str:
    lines = [re.sub(r"\s+", " ", x).strip() for x in (text or "").splitlines()]
    lines = [x for x in lines if x]
    total = len(lines)
    if total > 120:
        lines = lines[:80] + ["……（中间省略）……"] + lines[-30:]
    out = "\n".join(f"L{i + 1}: {line}" for i, line in enumerate(lines))
    return out[:limit]


def _match_plan(wp: dict, plans: list[dict]) -> dict | None:
    unit = (wp or {}).get("unit", "")
    if unit:
        for p in plans:
            if unit in p["name"] or unit in p.get("text", "")[:4000]:
                return p
    return plans[0] if plans else None


def _extract_topics(plans: list[dict], llm, workpapers: list[dict] | None = None) -> tuple[list[dict], str]:
    """提取审核事项骨架；无上位文件或提取失败时退化为按底稿事项生成。"""
    workpapers = workpapers or []
    error = ""
    topics: list[dict] = []
    if plans:
        plan_text = "\n\n".join(
            f"### {p['name']}\n{p.get('text', '')[:TOPIC_PLAN_TEXT_LIMIT]}" for p in plans)
        try:
            raw = llm.chat(TOPIC_SYSTEM, TOPIC_USER.format(plan=plan_text))
        except Exception as exc:
            error = f"审计事项提取失败：{type(exc).__name__}"
        else:
            for row in raw if isinstance(raw, list) else []:
                if not isinstance(row, dict):
                    continue
                title = str(row.get("topic") or "").strip()
                if not title:
                    continue
                topics.append({
                    "index": len(topics) + 1,
                    "topic": re.sub(r"^[（(][一二三四五六七八九十百]+[）)]\s*", "", title),
                    "source": str(row.get("source") or "").strip(),
                })
    if not topics and workpapers:
        for wp in sorted(workpapers, key=lambda w: _file_sort_key(w["name"])):
            title = wp.get("topic") or _topic_of(wp["name"])
            if title:
                topics.append({"index": len(topics) + 1, "topic": title, "source": ""})
    if not topics:
        error = error or "未能提取到审计事项，且未识别到可用底稿"
    return topics[:TOPIC_LIMIT], error


_PUNCT = r"[\s、，,；;：:（）()《》“”\"'—\-\.]+"


def _topic_keys(topic: str) -> list[str]:
    """用二元组做事项名与底稿文件名的模糊匹配，避免长短语无法命中。"""
    clean = re.sub(_PUNCT, "", str(topic or ""))
    if len(clean) < 2:
        return [clean] if clean else []
    keys = {clean[i:i + 2] for i in range(len(clean) - 1)}
    return sorted(keys)


def _match_workpaper(topic: dict, workpapers: list[dict]) -> dict | None:
    """按事项名与底稿事项/文件名/正文的关键词重合度选最相关的 1 份底稿。"""
    title = str((topic or {}).get("topic") or "")
    keys = _topic_keys(title)
    if not keys or not workpapers:
        return None
    whole = re.sub(_PUNCT, "", title)
    best, best_score = None, 0.0
    for wp in workpapers:
        name = re.sub(_PUNCT, "", wp.get("name", ""))
        wp_topic = re.sub(_PUNCT, "", wp.get("topic", ""))
        head = (wp.get("text", "") or "")[:4000]
        name_hits = sum(1 for k in keys if k in name)
        topic_hits = sum(1 for k in keys if k in wp_topic)
        text_hits = sum(1 for k in keys if k in head)
        score = (3.0 * topic_hits + 2.0 * name_hits + 0.3 * text_hits) / len(keys)
        if whole and (whole in wp_topic or whole in name):
            score += 1.0
        if score > best_score:
            best, best_score = wp, score
    return best if best_score >= 0.35 else None


def _clean_reason(text) -> str:
    """压缩为一段不超过 100 字的完整表述，避免截断在半句话上。"""
    reason = re.sub(r"\s+", "", str(text or "").strip())
    if len(reason) <= 100:
        return reason
    head = reason[:100]
    boundary = max(head.rfind("。"), head.rfind("；"), head.rfind("，"))
    if boundary >= 40:
        return head[:boundary].rstrip("，；。") + "。"
    return head.rstrip("，；。") + "。"


def _assess_topic(topic: dict, wp: dict | None, plan: dict | None, llm) -> dict:
    """单事项单底稿判定；异常与材料不足都退化为无法判断，不中断整体。"""
    item = {
        "index": int((topic or {}).get("index", 0) or 0),
        "topic": str((topic or {}).get("topic") or "").strip(),
        "file": (wp or {}).get("name", ""),
        "result": "无法判断",
        "reason": "",
        "source": str((topic or {}).get("source") or "").strip(),
    }
    if wp is None:
        item["reason"] = "未提供对应审计事项的工作底稿，需人工核实"
        return item
    try:
        raw = llm.chat(REPORT_ASSESS_SYSTEM, REPORT_ASSESS_USER.format(
            topic=item["topic"],
            plan=_topic_plan_excerpt(plan, item["topic"]),
            text=_numbered(wp.get("text", ""), WORKPAPER_TEXT_LIMIT),
        ))
        if not isinstance(raw, dict):
            raw = {}
        result = str(raw.get("result") or "").strip()
        item["result"] = result if result in ("通过", "不通过", "无法判断") else "无法判断"
        item["reason"] = _clean_reason(raw.get("reason", "")) or "底稿材料不足，需人工核实"
    except Exception:
        item["result"] = "无法判断"
        item["reason"] = "核对失败，需人工核实"
    return item


def _make_report_summary(items: list[dict]) -> dict:
    total = len(items)
    passed = sum(1 for it in items if it.get("result") == "通过")
    failed = sum(1 for it in items if it.get("result") == "不通过")
    unknown = sum(1 for it in items if it.get("result") == "无法判断")
    if failed:
        conclusion = "❌ 审核不通过"
    elif total and not unknown:
        conclusion = "✅ 审核通过"
    else:
        conclusion = "⚠️ 需补充材料"
    if total and passed == total:
        opinion = "所有审核要点均通过，工作底稿完整覆盖审计要求，审计程序执行充分有效。"
    else:
        opinion = (f"共 {total} 项审核要点，通过 {passed} 项、不通过 {failed} 项、"
                   f"无法判断 {unknown} 项。不通过及无法判断项的原因见详细审核结果。")
    return {"total": total, "pass": passed, "fail": failed, "unknown": unknown,
            "conclusion": conclusion, "opinion": opinion}


def _make_assessments(items: list[dict]) -> list[dict]:
    """兼容字段：把新的 items 映射成旧的 assessments 结构。"""
    return [{
        "id": f"T{it.get('index', i)}",
        "requirement": it.get("topic", ""),
        "result": it.get("result", "无法判断"),
        "reason": it.get("reason", ""),
        "source": it.get("source", ""),
        "scope": "",
        "evidence": [],
    } for i, it in enumerate(items, 1)]


def _build_report(files, plans, workpapers, items, errors) -> dict:
    summary = _make_report_summary(items)
    units = sorted({w.get("unit") for w in workpapers if w.get("unit")})
    roles = [{
        "name": f["name"], "role": f.get("role", "other"),
        "unit": f.get("unit", ""), "topic": f.get("topic", ""),
        "chars": len(f.get("text", "")),
    } for f in files]
    return {
        "items": items,
        "summary": summary,
        "unit_count": len(units),
        "plan_count": len(plans),
        "workpaper_count": len(workpapers),
        "file_count": len(files),
        "file_roles": roles,
        # 兼容字段：主链路不再以问题条数作为结论。
        "requirements": [],
        "assessments": _make_assessments(items),
        "issues": [], "issue_count": 0,
        "reconciliation": [], "recon_count": 0,
        "level_count": {},
        "errors": errors,
        "disclaimer": DISCLAIMER,
    }


def analyze_workpapers(uploaded, task=None, llm=None):
    """uploaded = [(filename, bytes), ...]。按审计事项逐项匹配底稿并判定。"""
    files = _parse_all(uploaded, task)
    errors = [f"{f['name']}：{f['error']}" for f in files if f.get("error")]
    plans, workpapers = _identify_roles(files, llm)
    if not workpapers:
        errors.append("未识别到审计工作底稿")
        return _build_report(files, plans, workpapers, [], errors)

    _update(task, stage="提取审核要点")
    topics, topic_err = _extract_topics(plans, llm, workpapers=workpapers)
    if topic_err:
        errors.append(topic_err)
    total = len(topics)
    _update(task, total=total, done=0)

    items = []
    for i, topic in enumerate(topics, 1):
        _update(task, stage="逐项审核底稿", done=i - 1, total=total)
        wp = _match_workpaper(topic, workpapers)
        plan = _match_plan(wp, plans) if wp else (plans[0] if plans else None)
        items.append(_assess_topic(topic, wp, plan, llm))
    _update(task, stage="完成", done=total, total=total)
    return _build_report(files, plans, workpapers, items, errors)
