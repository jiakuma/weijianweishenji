"""制度互斥模块接口：上传分析、进度轮询、历史记录、详情、删除、导出。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response

from ...db import SessionLocal, get_raw_settings, now_str
from ...llm import llm_config
from .models import ConflictSession, ConflictTask
from .service import DATA_DIR, DEFAULT_BUDGET, LLMClient, analyze_files

router = APIRouter()

# 进行中的任务放内存，保证轮询拿到的进度是实时的；完成后已落库，重启仍可查历史。
TASKS: dict[str, dict] = {}
_exec = ThreadPoolExecutor(max_workers=2, thread_name_prefix="conflict")
# .xlsx 为「制度清单」，只提供权威制度名，不参与正文比对
ALLOWED_SUFFIX = {".pdf", ".docx", ".doc", ".txt", ".md", ".xlsx", ".xls"}
BUDGET_CHOICES = {"快速": 30, "标准": DEFAULT_BUDGET, "深度": 150}


def _mock() -> bool:
    return os.getenv("MOCK_MODE") == "1"


def _llm() -> LLMClient | None:
    cfg = llm_config()
    if not cfg.ready:
        return None
    return LLMClient(cfg.base, cfg.key, cfg.model)


def _task_dict(t: dict, full: bool = False) -> dict:
    d = {
        "task_id": t["task_id"],
        "name": t["name"],
        "created_at": t["created_at"],
        "status": t["status"],
        "stage": t["stage"],
        "mode": t["mode"],
        "total": t["total"],
        "done": t["done"],
        "compare_total": t.get("compare_total", 0),
        "compare_done": t.get("compare_done", 0),
        "conflict_count": t.get("conflict_count", 0),
        "error": t.get("error") or "",
    }
    if full:
        d["files"] = t["files"]
        d["report"] = t.get("report") or {}
        d["conflicts"] = d["report"].get("conflicts", [])
        d["units"] = d["report"].get("units", [])
        d["coverage"] = d["report"].get("coverage", {})
        d["clause_count"] = d["report"].get("clause_count", 0)
    return d


def _row_dict(r: ConflictTask, full: bool = False) -> dict:
    d = {
        "task_id": r.id,
        "name": r.name,
        "created_at": r.created_at,
        "status": r.status,
        "stage": r.stage,
        "mode": r.mode,
        "total": r.total,
        "done": r.done,
        "compare_total": r.compare_total,
        "compare_done": r.compare_done,
        "conflict_count": r.conflict_count,
        "error": r.error or "",
    }
    if full:
        d["files"] = json.loads(r.files_json or "[]")
        d["report"] = json.loads(r.report_json or "{}")
        d["conflicts"] = d["report"].get("conflicts", [])
        d["units"] = d["report"].get("units", [])
        d["coverage"] = d["report"].get("coverage", {})
        d["clause_count"] = d["report"].get("clause_count", 0)
    return d


def _save(t: dict) -> None:
    with SessionLocal() as s:
        row = s.get(ConflictTask, t["task_id"])
        if row is None:
            row = ConflictTask(id=t["task_id"])
            s.add(row)
        row.name = t["name"]
        row.created_at = t["created_at"]
        row.status = t["status"]
        row.stage = t["stage"]
        row.mode = t["mode"]
        row.total = t["total"]
        row.done = t["done"]
        row.compare_total = t.get("compare_total", 0)
        row.compare_done = t.get("compare_done", 0)
        row.conflict_count = t.get("conflict_count", 0)
        row.files_json = json.dumps(t["files"], ensure_ascii=False)
        row.report_json = json.dumps(t.get("report") or {}, ensure_ascii=False)
        row.error = t.get("error") or ""
        s.commit()


def _process(task_id: str, uploaded, llm, mock: bool, budget: int) -> None:
    t = TASKS.get(task_id)
    if not t:
        return
    t["status"] = "running"
    _save(t)
    try:
        analyze_files(uploaded, task=t, llm=llm, mock=mock, budget=budget)
        t["status"] = "done"
        t["stage"] = "完成"
    except Exception as e:
        t["status"] = "error"
        t["stage"] = "失败"
        t["error"] = str(e)
    _save(t)


def _session_dict(s: ConflictSession) -> dict:
    ids = json.loads(s.task_ids_json or "[]")
    return {
        "id": s.id,
        "title": s.title,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
        "task_count": len(ids),
        "task_ids": ids,
    }


def _ensure_legacy_session(s) -> ConflictSession | None:
    """历史库没有任何会话但已有任务时，建一个默认会话收编，避免旧数据不可见。"""
    if s.query(ConflictSession).count() > 0:
        return None
    task_ids = [r.id for r in s.query(ConflictTask).order_by(ConflictTask.created_at).all()]
    if not task_ids:
        return None
    now = now_str()
    session = ConflictSession(
        id=uuid.uuid4().hex[:12],
        title="历史检测",
        task_ids_json=json.dumps(task_ids, ensure_ascii=False),
        created_at=now,
        updated_at=now,
    )
    s.add(session)
    s.commit()
    return session


@router.get("/sessions")
def list_sessions():
    with SessionLocal() as s:
        _ensure_legacy_session(s)
        rows = s.query(ConflictSession).order_by(ConflictSession.updated_at.desc()).all()
        return [_session_dict(r) for r in rows]


@router.post("/sessions")
def create_session():
    with SessionLocal() as s:
        now = now_str()
        session = ConflictSession(
            id=uuid.uuid4().hex[:12], title="新会话",
            task_ids_json="[]", created_at=now, updated_at=now,
        )
        s.add(session)
        s.commit()
        return _session_dict(session)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    removed = []
    with SessionLocal() as s:
        session = s.get(ConflictSession, session_id)
        if session:
            removed = json.loads(session.task_ids_json or "[]")
            s.delete(session)
            s.commit()
    for tid in removed:
        TASKS.pop(tid, None)
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tid):
            shutil.rmtree(DATA_DIR / tid, ignore_errors=True)
    return {"ok": True, "deleted_tasks": removed}


@router.get("/config")
def config():
    mock = _mock()
    ready = _llm() is not None
    return {
        "mock": mock,
        "llmReady": ready,
        "mode": "演示" if mock else ("真实模型" if ready else "未配置"),
        "budgetDefault": DEFAULT_BUDGET,
        "budgetChoices": BUDGET_CHOICES,
        "acceptManifest": True,
    }


@router.post("/analyze")
async def analyze(
    files: list[UploadFile] = File(...),
    budget: int = Form(DEFAULT_BUDGET),
    session_id: str = Form(""),
):
    try:
        budget = int(budget)
    except (TypeError, ValueError):
        budget = DEFAULT_BUDGET
    budget = max(10, min(500, budget))
    uploaded = []
    bad = []
    for f in files:
        name = f.filename or "未命名文件"
        if os.path.splitext(name)[1].lower() not in ALLOWED_SUFFIX:
            bad.append(name)
            continue
        raw = await f.read()
        if raw:
            uploaded.append((name, raw))
    if bad:
        return JSONResponse({"error": "不支持的文件格式：" + "、".join(bad)}, status_code=400)
    if not uploaded:
        return JSONResponse({"error": "未收到任何文件"}, status_code=400)

    mock = _mock()
    llm = None if mock else _llm()
    if not mock and llm is None:
        return JSONResponse(
            {"error": "未配置大模型，请到系统设置填写接口地址与 API Key"}, status_code=400
        )

    task_id = uuid.uuid4().hex[:12]
    t = {
        "task_id": task_id,
        "name": "、".join(fn for fn, _ in uploaded[:3]) + (f" 等 {len(uploaded)} 个文件" if len(uploaded) > 3 else ""),
        "created_at": now_str(),
        "status": "pending",
        "stage": "排队中",
        "mode": "mock" if mock else "real",
        "files": [
            {"filename": fn, "status": "pending", "chars": None, "note": None, "error": None}
            for fn, _ in uploaded
        ],
        "total": len(uploaded),
        "done": 0,
        "compare_total": 0,
        "compare_done": 0,
        "conflict_count": 0,
        "conflicts": [],
        "report": None,
        "error": None,
    }
    TASKS[task_id] = t
    _save(t)
    if session_id:
        with SessionLocal() as s:
            session = s.get(ConflictSession, session_id)
            if session:
                ids = json.loads(session.task_ids_json or "[]")
                ids.append(task_id)
                session.task_ids_json = json.dumps(ids, ensure_ascii=False)
                if not session.title or session.title == "新会话":
                    session.title = os.path.splitext(uploaded[0][0])[0][:24]
                session.updated_at = now_str()
                s.commit()
    loop = asyncio.get_running_loop()
    loop.run_in_executor(_exec, _process, task_id, uploaded, llm, mock, budget)
    return {"task_id": task_id, "mode": t["mode"], "budget": budget}


@router.get("/tasks")
def list_tasks():
    with SessionLocal() as s:
        rows = (
            s.query(ConflictTask)
            .order_by(ConflictTask.created_at.desc(), ConflictTask.id.desc())
            .limit(100)
            .all()
        )
        items = [_row_dict(r) for r in rows]
    index = {x["task_id"]: x for x in items}
    for tid, t in TASKS.items():
        live = _task_dict(t)
        if tid in index:
            index[tid].update(live)
        else:
            index[tid] = live
    return sorted(index.values(), key=lambda x: x["created_at"], reverse=True)


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    t = TASKS.get(task_id)
    if t:
        return _task_dict(t, full=True)
    with SessionLocal() as s:
        row = s.get(ConflictTask, task_id)
        if row is None:
            return JSONResponse({"error": "任务不存在或已过期"}, status_code=404)
        return _row_dict(row, full=True)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    TASKS.pop(task_id, None)
    with SessionLocal() as s:
        row = s.get(ConflictTask, task_id)
        if row:
            s.delete(row)
        for session in s.query(ConflictSession).all():
            ids = json.loads(session.task_ids_json or "[]")
            if task_id in ids:
                ids.remove(task_id)
                session.task_ids_json = json.dumps(ids, ensure_ascii=False)
        s.commit()
    # 同步清理归一化中间产物（units/clauses/candidates/coverage）
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", task_id):
        shutil.rmtree(DATA_DIR / task_id, ignore_errors=True)
    return {"ok": True}


def _full_task(task_id: str) -> dict | None:
    t = TASKS.get(task_id)
    if t:
        return _task_dict(t, full=True)
    with SessionLocal() as s:
        row = s.get(ConflictTask, task_id)
        return _row_dict(row, full=True) if row else None


def _md_cell(value) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")


def _markdown(d: dict) -> str:
    """导出报告：以冲突结果为主体，范围与限制放在末尾作底稿交代。"""
    report = d.get("report") or {}
    cov = report.get("coverage") or {}
    units = report.get("units") or []
    conflicts = report.get("conflicts", [])
    judged = cov.get("judged_pairs", cov.get("compared_pairs", 0))
    lines = [
        "# 制度互斥检测报告",
        "",
        f"- 任务：{d['name']}",
        f"- 检测时间：{d['created_at']}",
        f"- 模式：{'演示数据' if d['mode'] == 'mock' else '真实模型'}",
        f"- 上传文件：{d['total']} 个，拆分制度单元 {len(units)} 个、条款 "
        f"{report.get('clause_count') or cov.get('clauses', 0)} 条",
        f"- **检出互斥：{d['conflict_count']} 处**",
        "",
        "## 一、检出互斥条款",
        "",
    ]
    for i, c in enumerate(conflicts, 1):
        lines += [
            f"### {i}. [{c.get('severity', '')}] {c.get('conflict_type', '')}｜{c.get('topic', '')}",
            "",
            c.get("explanation", ""),
            "",
            f"> **甲** {_md_cell(c.get('locate_a') or c.get('file_a', ''))}",
            ">",
            f"> {_md_cell(c.get('quote_a', ''))}",
            "",
            f"> **乙** {_md_cell(c.get('locate_b') or c.get('file_b', ''))}",
            ">",
            f"> {_md_cell(c.get('quote_b', ''))}",
            "",
        ]
    if not conflicts:
        lines += ["在本次比对范围内未发现互斥条款。", ""]

    lines += ["## 二、检测范围与使用限制", ""]
    status_text = {
        "done": "已解析", "skipped": "跳过", "error": "失败",
        "parsing": "解析中", "pending": "等待中",
    }
    file_lines = [
        f"- {_md_cell(f['filename'])}：{status_text.get(f['status'], f['status'])}"
        + (f"，制度单元 {f.get('units', 0)} 个、条款 {f.get('clauses', 0)} 条" if f.get("status") == "done" and not f.get("is_manifest") else "")
        + (f"，{_md_cell(f.get('note') or f.get('error') or '')}" if f.get("note") or f.get("error") else "")
        for f in d.get("files", [])
    ]
    lines += file_lines
    lines += [
        "",
        f"- 召回候选 {cov.get('candidate_pairs', 0)} 对，精判 {judged} 对"
        f"（覆盖 {cov.get('unit_pairs_compared', 0)} 对制度组合），"
        f"未送模型 {cov.get('skipped_pairs', 0)} 对，失败 {cov.get('failed_pairs', 0)} 对",
        f"- 召回方式：{cov.get('skip_reason', '')}",
        "",
        "**使用限制**：本报告结论仅代表已精判的比对范围，「未发现互斥」不代表制度体系不存在冲突；"
        "引用原文均取自制度文本，冲突认定由大模型作出，重要事项请人工复核后使用。",
    ]
    if cov.get("failed_pairs"):
        lines += ["", f"> ⚠️ 有 {cov['failed_pairs']} 对精判失败，首个错误：{cov.get('first_error', '')}，结果可能不完整。"]
    return "\n".join(lines)


@router.get("/tasks/{task_id}/export")
def export_task(task_id: str):
    d = _full_task(task_id)
    if d is None:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    content = _markdown(d)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="conflict-report-{task_id}.md"'},
    )
