"""底稿审核核心流水线：解析 -> 角色识别 -> 逐份复核 -> 跨底稿勾稽 -> 报告。

输入 uploaded = [(filename, bytes), ...]。扫描/OCR 沿用 conflict/parser.py；
.doc 审计方案通过本机 Word COM 读取（Windows 环境），docx/txt/pdf 走统一解析器。
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
    ASSESS_SYSTEM, ASSESS_USER, REQUIREMENT_SYSTEM, REQUIREMENT_USER,
    ROLE_SYSTEM, ROLE_USER,
)

PARSE_WORKERS = 4
ROLE_EXCERPT = 900
WORKPAPER_TEXT_LIMIT = 16000
PLAN_TEXT_LIMIT = 9000
RECON_ITEM_LIMIT = 1800
_DOC_LOCK = threading.Lock()


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
    s = s.replace("审计工作底稿", "").replace("《》", "")
    s = re.sub(r"^\s*[-－—_]+", "", s).strip()
    s = re.sub(r"\s*-\s*副本$", "", s)
    return s.strip("-_— ") or s


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

    if others:
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


def _numbered(text: str, limit: int) -> str:
    lines = [re.sub(r"\s+", " ", x).strip() for x in (text or "").splitlines()]
    lines = [x for x in lines if x]
    total = len(lines)
    if total > 120:
        lines = lines[:80] + ["……（中间省略）……"] + lines[-30:]
    out = "\n".join(f"L{i + 1}: {line}" for i, line in enumerate(lines))
    return out[:limit]


def _match_plan(wp: dict, plans: list[dict]) -> dict | None:
    unit = wp.get("unit", "")
    if unit:
        for p in plans:
            if unit in p["name"] or unit in p.get("text", "")[:4000]:
                return p
    return plans[0] if plans else None


def _normalize_issues(raw, source: str) -> list[dict]:
    out = []
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict):
            continue
        out.append({
            "dimension": str(it.get("dimension") or "").strip(),
            "level": str(it.get("level") or "待核实异常").strip(),
            "title": str(it.get("title") or "待核实事项").strip(),
            "fact": str(it.get("fact") or "").strip(),
            "quote": str(it.get("quote") or "").strip(),
            "locate": str(it.get("locate") or f"{source} · L1").strip(),
            "basis": str(it.get("basis") or "").strip(),
            "analysis": str(it.get("analysis") or "").strip(),
            "impact": str(it.get("impact") or "").strip(),
            "suggestion": str(it.get("suggestion") or "").strip(),
            "source": source,
        })
    return out


def _audit_one(wp: dict, plan: dict | None, llm) -> list[dict]:
    raw = llm.chat(
        AUDIT_SYSTEM,
        AUDIT_USER.format(
            plan=_plan_excerpt(plan, wp.get("topic", "")),
            name=wp["name"],
            text=_numbered(wp.get("text", ""), WORKPAPER_TEXT_LIMIT),
        ),
    )
    issues = _normalize_issues(raw, wp["name"])
    return issues[:8]


def _audit_all(workpapers: list[dict], plans: list[dict], llm, task) -> tuple[list[dict], list[str]]:
    _update(task, stage="复核审计底稿")
    issues, errors = [], []
    for idx, wp in enumerate(workpapers):
        _update(task, done=idx)
        try:
            issues.extend(_audit_one(wp, _match_plan(wp, plans), llm))
        except Exception as exc:
            errors.append(f"{wp['name']} 底稿复核失败：{type(exc).__name__}")
    for i, it in enumerate(issues, 1):
        it["index"] = i
    return issues, errors


def _reconcile(plans: list[dict], workpapers: list[dict], llm) -> tuple[list[dict], str]:
    if not plans and len(workpapers) < 2:
        return [], "未提供审计实施方案且底稿不足两份，无法跨文件勾稽"
    plan_text = "\n\n".join(f"### {p['name']}\n{p.get('text', '')[:PLAN_TEXT_LIMIT]}"
                            for p in plans)
    items = "\n".join(
        f"- {w['name']} / 单位：{w.get('unit') or '未识别'} / 事项：{w.get('topic') or '未识别'}\n"
        f"  {w.get('text', '')[:RECON_ITEM_LIMIT]}" for w in workpapers)
    try:
        raw = llm.chat(RECON_SYSTEM, RECON_USER.format(plans=plan_text or "（无）", items=items))
    except Exception as exc:
        return [], f"跨文件勾稽失败：{type(exc).__name__}"
    out = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        out.append({
            "field": str(row.get("field") or "").strip(),
            "files": str(row.get("files") or "").strip(),
            "value_a": str(row.get("value_a") or "").strip(),
            "value_b": str(row.get("value_b") or "").strip(),
            "note": str(row.get("note") or "").strip(),
        })
    return out[:20], ""


REQUIREMENT_LIMIT = 12
ASSESS_WP_LIMIT = 3200
ASSESS_WP_COUNT = 4


def _extract_requirements(plans: list[dict], llm) -> tuple[list[dict], str]:
    """从上位文件提取需要底稿落实的要求点。"""
    if not plans:
        return [], "未提供审计实施方案，无法提取上位文件要求点"
    plan_text = "\n\n".join(
        f"### {p['name']}\n{p.get('text', '')[:PLAN_TEXT_LIMIT]}" for p in plans)
    try:
        raw = llm.chat(REQUIREMENT_SYSTEM, REQUIREMENT_USER.format(plan=plan_text))
    except Exception as exc:
        return [], f"要求点提取失败：{type(exc).__name__}"
    out = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        req = str(row.get("requirement") or "").strip()
        if not req:
            continue
        out.append({
            "id": str(row.get("id") or f"R{len(out) + 1}").strip(),
            "requirement": req,
            "scope": str(row.get("scope") or "").strip(),
            "source": str(row.get("source") or "").strip(),
        })
    return out[:REQUIREMENT_LIMIT], ""


def _assess_wp_items(req: dict, workpapers: list[dict]) -> str:
    """按要求关键词挑最相关的底稿，控制单次核对的输入量。"""
    keys = [k for k in re.split(r"[、，,；;（）()\s]+", req.get("requirement", "")) if len(k) >= 2]
    scored = []
    for w in workpapers:
        text = w.get("text", "")
        score = sum(1 for k in keys[:6] if k in text or k in w.get("topic", ""))
        scored.append((score, w))
    scored.sort(key=lambda x: -x[0])
    chunks = []
    for _, w in scored[:ASSESS_WP_COUNT]:
        chunks.append(
            f"- {w['name']}（单位：{w.get('unit') or '未识别'}，事项：{w.get('topic') or '未识别'}）\n"
            + _numbered(w.get("text", ""), ASSESS_WP_LIMIT))
    return "\n\n".join(chunks) or "（无可用底稿正文）"


def _assess_one(req: dict, workpapers: list[dict], llm) -> dict:
    raw = llm.chat(ASSESS_SYSTEM, ASSESS_USER.format(
        requirement=json.dumps(req, ensure_ascii=False),
        items=_assess_wp_items(req, workpapers),
    ))
    if not isinstance(raw, dict):
        raw = {}
    result = str(raw.get("result") or "").strip()
    if result not in ("符合", "不符合", "无法判断"):
        result = "无法判断"
    evidence = [str(x).strip() for x in (raw.get("evidence") or []) if str(x).strip()][:2]
    return {
        "id": req.get("id", ""),
        "requirement": req.get("requirement", ""),
        "scope": req.get("scope", ""),
        "source": req.get("source", ""),
        "result": result,
        "reason": str(raw.get("reason") or "").strip(),
        "evidence": evidence,
    }


def _assess_all(requirements, workpapers, llm, task) -> tuple[list[dict], list[str]]:
    out, errors = [], []
    total = len(requirements)
    for i, req in enumerate(requirements, 1):
        _update(task, stage="逐项核对底稿", done=i - 1, total=total)
        try:
            out.append(_assess_one(req, workpapers, llm))
        except Exception as exc:
            errors.append(f"{req.get('id') or '要求点'} 核对失败：{type(exc).__name__}")
            out.append({
                "id": req.get("id", ""), "requirement": req.get("requirement", ""),
                "scope": req.get("scope", ""), "source": req.get("source", ""),
                "result": "无法判断", "reason": "核对失败，需人工复核", "evidence": [],
            })
    _update(task, done=total, total=total)
    return out, errors


def _make_summary(assessments: list[dict]) -> dict:
    total = len(assessments)
    passed = [a for a in assessments if a["result"] == "符合"]
    failed = [a for a in assessments if a["result"] == "不符合"]
    unknown = [a for a in assessments if a["result"] == "无法判断"]
    if not total:
        conclusion = "未能从审计实施方案中提取到可核对的要求点。"
    elif not failed and not unknown:
        conclusion = f"实施方案提出的 {total} 项要求，现有底稿均可支撑，判定为符合。"
    else:
        conclusion = (
            f"共核对 {total} 项要求：符合 {len(passed)} 项，不符合 {len(failed)} 项，无法判断 {len(unknown)} 项。"
            "不符合及无法判断项的原因见下方逐项核对。"
        )
    return {"total": total, "pass": len(passed), "fail": len(failed),
            "unknown": len(unknown), "conclusion": conclusion}


def analyze_workpapers(uploaded, task=None, llm=None):
    """uploaded = [(filename, bytes), ...]。按上位文件要求点逐项判定底稿符合性。"""
    files = _parse_all(uploaded, task)
    errors = [f"{f['name']}：{f['error']}" for f in files if f.get("error")]

    plans, workpapers = _identify_roles(files, llm)
    if not workpapers:
        return {
            "unit_count": 0, "file_count": len(files), "plan_count": len(plans),
            "workpaper_count": 0, "file_roles": [], "requirements": [],
            "assessments": [], "summary": _make_summary([]),
            "issues": [], "issue_count": 0, "reconciliation": [], "recon_count": 0,
            "level_count": {}, "errors": errors + ["未识别到审计工作底稿"],
            "disclaimer": "本报告为文本层面的底稿复核提示，不构成审计结论。",
        }

    requirements, req_err = _extract_requirements(plans, llm)
    if req_err:
        errors.append(req_err)
    assessments, assess_errors = _assess_all(requirements, workpapers, llm, task)
    errors.extend(assess_errors)
    summary = _make_summary(assessments)

    units = sorted({w.get("unit") for w in workpapers if w.get("unit")})
    roles = [{
        "name": f["name"], "role": f.get("role", "other"),
        "unit": f.get("unit", ""), "topic": f.get("topic", ""),
        "chars": len(f.get("text", "")),
    } for f in files]

    _update(task, stage="完成", done=task.get("total", len(uploaded)) if task else len(uploaded))
    return {
        "unit_count": len(units), "file_count": len(files),
        "plan_count": len(plans), "workpaper_count": len(workpapers),
        "requirements": requirements, "assessments": assessments, "summary": summary,
        "file_roles": roles,
        # 兼容旧字段：前端与任务列表不再以问题条数作为主结论
        "issues": [], "issue_count": 0, "reconciliation": [], "recon_count": 0,
        "level_count": {}, "errors": errors,
        "disclaimer": "本报告为文本层面的符合性复核提示，不构成审计结论；是否形成问题须由审计人员结合证据复核后确定。",
    }
