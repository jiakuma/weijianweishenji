"""制度互斥检测：供智能体工具调用的桥接层。

把 docId 还原成原始文件字节，复用 analyze_files 跑检测，
并把后端各阶段进度通过 agent_tools.emit_progress 实时推给当前 SSE 流。

docId 有两类来源：
1. tmp-xxx —— 智能助手回形针上传的临时附件（assistant_uploads 表，不入库资料库）；
2. 资料库已有资料 ID —— 兼容 search_docs 后直接指定库内文档的场景。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from ... import agent_tools
from ...db import AssistantUpload, Document, DocumentFile, SessionLocal, now_str
from ...llm import llm_config
from .models import ConflictTask
from .service import LLMClient, analyze_files

_LAST_LOCK = threading.Lock()
_LAST_RESULT: dict = {}
_PROGRESS_KEYS = {"stage", "done", "total", "compare_done", "compare_total"}


def _load_uploaded(doc_ids: list[str]) -> tuple[list[tuple[str, bytes]], list[str]]:
    """按 docId 取原始文件字节：优先助手临时附件，其次资料库原件。

    临时附件只存在于 assistant_uploads 表 + 独立目录，不写资料库任何表；
    库内文档仍按原逻辑读取，保证「先 search_docs 再检测」的用法不退化。
    """
    uploaded: list[tuple[str, bytes]] = []
    missing: list[str] = []
    with SessionLocal() as s:
        for did in doc_ids:
            # 1) 智能助手临时附件（本次会话上传，未入库）
            tmp = s.get(AssistantUpload, did)
            if tmp:
                tmp_path = Path(tmp.stored_path) if tmp.stored_path else None
                if not tmp_path or not tmp_path.exists():
                    missing.append(did)
                    continue
                uploaded.append((tmp.filename or tmp_path.name, tmp_path.read_bytes()))
                continue
            # 2) 资料库已有资料（原有行为，保持不变）
            doc = s.get(Document, did)
            row = (s.query(DocumentFile)
                   .filter(DocumentFile.doc_id == did, DocumentFile.kind == "original")
                   .first())
            path = Path(row.stored_path) if row and row.stored_path else None
            if not doc or not path or not path.exists():
                missing.append(did)
                continue
            uploaded.append((doc.title or path.name, path.read_bytes()))
    return uploaded, missing


def _new_task_state(uploaded, focus: str) -> dict:
    return {
        "task_id": uuid.uuid4().hex[:12],
        "name": "、".join(fn for fn, _ in uploaded[:3]),
        "created_at": now_str(),
        "status": "running",
        "stage": "排队中",
        "mode": "real",
        "files": [
            {"filename": fn, "status": "pending", "chars": None, "note": None,
             "error": None, "units": 0, "clauses": 0}
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
        "focus": focus,
    }


def _watch_progress(task: dict, stop: threading.Event, emit) -> None:
    """轮询 task 字典，把阶段与计数推给当前 SSE 流；工具结束时停止。"""
    last = None
    while not stop.is_set():
        snap = {k: task.get(k) for k in _PROGRESS_KEYS}
        if snap != last:
            if emit:
                try:
                    emit({
                        "kind": "conflict",
                        "taskId": task["task_id"],
                        "stage": snap["stage"] or "",
                        "done": snap["done"] or 0,
                        "total": snap["total"] or 0,
                        "compareDone": snap["compare_done"] or 0,
                        "compareTotal": snap["compare_total"] or 0,
                    })
                except Exception:
                    pass
            last = snap
        stop.wait(0.5)


def _persist(task: dict, report: dict) -> None:
    """把结果落库，便于导出与历史追溯。"""
    try:
        with SessionLocal() as s:
            row = s.get(ConflictTask, task["task_id"])
            if row is None:
                row = ConflictTask(id=task["task_id"])
                s.add(row)
            row.name = task["name"]
            row.created_at = task["created_at"]
            row.status = "done"
            row.stage = "完成"
            row.mode = "real"
            row.total = task["total"]
            row.done = task["done"]
            row.compare_total = task.get("compare_total", 0)
            row.compare_done = task.get("compare_done", 0)
            row.conflict_count = len(report.get("conflicts", []))
            row.files_json = json.dumps(task["files"], ensure_ascii=False)
            row.report_json = json.dumps(report, ensure_ascii=False)
            row.error = ""
            s.commit()
    except Exception:
        pass


def detect(args: dict) -> dict:
    """agent 工具 handler：检测指定 docIds 的制度互斥。"""
    doc_ids = [str(x) for x in (args.get("docIds") or []) if x][:20]
    focus = str(args.get("focus") or "").strip()[:100]
    if not doc_ids:
        return {"ok": False, "error": "未提供要检测的资料"}

    uploaded, missing = _load_uploaded(doc_ids)
    if not uploaded:
        return {"ok": False, "error": "找不到这些资料的原始文件" + (f"（缺失 {len(missing)} 个）" if missing else "")}

    cfg = llm_config()
    if not cfg.ready:
        return {"ok": False, "error": "未配置大模型，请到系统设置填写接口地址与 API Key"}
    llm = LLMClient(cfg.base, cfg.key, cfg.model)

    task = _new_task_state(uploaded, focus)
    stop = threading.Event()
    emit = agent_tools.get_progress()  # contextvar 不跨线程，显式捕获后传给轮询线程
    watcher = threading.Thread(target=_watch_progress, args=(task, stop, emit), daemon=True)
    watcher.start()
    try:
        report = analyze_files(uploaded, task=task, llm=llm, mock=False)
    except Exception as exc:
        stop.set()
        return {"ok": False, "error": f"检测失败：{type(exc).__name__}: {exc}"}
    finally:
        stop.set()
        watcher.join(timeout=2)

    _persist(task, report)
    conflicts = report.get("conflicts", [])
    cov = report.get("coverage", {})
    summary = {
        "taskId": task["task_id"],
        "conflictCount": len(conflicts),
        "files": len(uploaded),
        "units": len(report.get("units", [])),
        "clauses": report.get("clause_count", 0),
        "judgedPairs": cov.get("judged_pairs", cov.get("compared_pairs", 0)),
        "focus": focus or None,
    }
    with _LAST_LOCK:
        _LAST_RESULT["task_id"] = task["task_id"]
        _LAST_RESULT["report"] = report
        _LAST_RESULT["name"] = task["name"]
    if emit:
        try:
            emit({
                "kind": "conflict_done", "taskId": task["task_id"],
                "stage": "完成", "conflictCount": len(conflicts),
            })
        except Exception:
            pass
    return {"ok": True, **summary,
            "topics": list(dict.fromkeys(c.get("topic", "") for c in conflicts))[:10]}


def get_result(args: dict) -> dict:
    """agent 工具 handler：读取最近一次（或指定 taskId 的）检测结果明细。"""
    task_id = str(args.get("taskId") or "").strip()
    with _LAST_LOCK:
        cached = dict(_LAST_RESULT)
    if task_id:
        with SessionLocal() as s:
            row = s.get(ConflictTask, task_id)
            if row:
                return _result_payload(task_id, row.name, json.loads(row.report_json or "{}"))
        if cached.get("task_id") == task_id and cached.get("report"):
            return _result_payload(task_id, cached.get("name", ""), cached["report"])
        return {"ok": False, "error": "找不到该检测记录"}
    if cached.get("report"):
        return _result_payload(cached.get("task_id", ""), cached.get("name", ""), cached["report"])
    with SessionLocal() as s:
        row = (s.query(ConflictTask).filter(ConflictTask.status == "done")
               .order_by(ConflictTask.created_at.desc()).first())
        if not row:
            return {"ok": False, "error": "还没有做过制度互斥检测"}
        return _result_payload(row.id, row.name, json.loads(row.report_json or "{}"))


def _result_payload(task_id: str, name: str, report: dict) -> dict:
    conflicts = []
    for i, c in enumerate(report.get("conflicts", []), 1):
        conflicts.append({
            "index": i,
            "topic": c.get("topic", ""),
            "type": c.get("conflict_type", ""),
            "severity": c.get("severity", ""),
            "explanation": c.get("explanation", ""),
            "a": {"text": c.get("quote_a", ""), "locate": c.get("locate_a") or c.get("file_a", "")},
            "b": {"text": c.get("quote_b", ""), "locate": c.get("locate_b") or c.get("file_b", "")},
        })
    return {"ok": True, "taskId": task_id, "name": name,
            "conflictCount": len(conflicts), "conflicts": conflicts}
