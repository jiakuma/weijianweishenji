"""底稿审核：智能体工具桥接层。

docIds 来自助手临时上传区 tmp-xxx 或资料库原文档；复用 service.analyze_workpapers。
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from ... import agent_tools
from ...db import AssistantUpload, Document, DocumentFile, SessionLocal, now_str
from ...llm import llm_config
from .models import WorkpaperTask
from .service import LLMClient, analyze_workpapers

_LAST_LOCK = threading.Lock()
_LAST_RESULT: dict = {}
_PROGRESS_KEYS = {"stage", "done", "total"}


def _load_uploaded(doc_ids: list[str]) -> tuple[list[tuple[str, bytes]], list[str]]:
    uploaded, missing = [], []
    with SessionLocal() as s:
        for did in doc_ids:
            tmp = s.get(AssistantUpload, did)
            if tmp:
                p = Path(tmp.stored_path) if tmp.stored_path else None
                if not p or not p.exists():
                    missing.append(did)
                    continue
                uploaded.append((tmp.filename or p.name, p.read_bytes()))
                continue
            doc = s.get(Document, did)
            row = (s.query(DocumentFile)
                   .filter(DocumentFile.doc_id == did, DocumentFile.kind == "original")
                   .first())
            p = Path(row.stored_path) if row and row.stored_path else None
            if not doc or not p or not p.exists():
                missing.append(did)
                continue
            uploaded.append((doc.title or p.name, p.read_bytes()))
    return uploaded, missing


def _new_task(uploaded) -> dict:
    return {
        "task_id": uuid.uuid4().hex[:12],
        "name": "、".join(fn for fn, _ in uploaded[:3]),
        "created_at": now_str(),
        "status": "running",
        "stage": "排队中",
        "total": len(uploaded),
        "done": 0,
    }


def _watch(task, stop, emit):
    last = None
    while not stop.is_set():
        snap = {k: task.get(k) for k in _PROGRESS_KEYS}
        if snap != last:
            if emit:
                try:
                    emit({"kind": "workpaper", "taskId": task["task_id"],
                          "stage": snap["stage"] or "", "done": snap["done"] or 0,
                          "total": snap["total"] or 0})
                except Exception:
                    pass
            last = snap
        stop.wait(0.5)


def _persist(task, report):
    try:
        with SessionLocal() as s:
            row = s.get(WorkpaperTask, task["task_id"])
            if row is None:
                row = WorkpaperTask(id=task["task_id"])
                s.add(row)
            row.name = task["name"]
            row.created_at = task["created_at"]
            row.status = "done"
            row.stage = "完成"
            row.total = task["total"]
            row.done = task["done"]
            summary = report.get("summary") or {}
            row.issue_count = int(summary.get("fail", 0))
            row.recon_count = int(summary.get("unknown", 0))
            row.unit_count = report.get("unit_count", 0)
            row.file_count = report.get("file_count", 0)
            row.files_json = json.dumps(report.get("file_roles", []), ensure_ascii=False)
            row.report_json = json.dumps(report, ensure_ascii=False)
            row.error = "；".join(report.get("errors", []))[:500]
            s.commit()
    except Exception:
        pass


def detect(args: dict) -> dict:
    doc_ids = [str(x) for x in (args.get("docIds") or []) if x][:30]
    if not doc_ids:
        return {"ok": False, "error": "未提供要审核的底稿文件"}

    uploaded, missing = _load_uploaded(doc_ids)
    if not uploaded:
        return {"ok": False, "error": "找不到这些文件的原始内容"
                + (f"（缺失 {len(missing)} 个）" if missing else "")}

    cfg = llm_config()
    if not cfg.ready:
        return {"ok": False, "error": "未配置大模型，请到系统设置填写接口地址与 API Key"}
    llm = LLMClient(cfg.base, cfg.key, cfg.model)

    task = _new_task(uploaded)
    stop = threading.Event()
    emit = agent_tools.get_progress()
    watcher = threading.Thread(target=_watch, args=(task, stop, emit), daemon=True)
    watcher.start()
    try:
        report = analyze_workpapers(uploaded, task=task, llm=llm)
    except Exception as exc:
        stop.set()
        watcher.join(timeout=2)
        return {"ok": False, "error": f"底稿审核失败：{type(exc).__name__}: {exc}"}
    finally:
        stop.set()
        watcher.join(timeout=2)

    _persist(task, report)
    summary = report.get("summary") or {}
    total_issues = int(summary.get("fail", 0)) + int(summary.get("unknown", 0))
    with _LAST_LOCK:
        _LAST_RESULT["task_id"] = task["task_id"]
        _LAST_RESULT["report"] = report
        _LAST_RESULT["name"] = task["name"]
    if emit:
        try:
            emit({"kind": "workpaper_done", "taskId": task["task_id"], "totalIssues": total_issues})
        except Exception:
            pass
    return {"ok": True, "taskId": task["task_id"], "unitCount": report.get("unit_count", 0),
            "files": len(uploaded), "totalIssues": total_issues,
            "issueCount": int(summary.get("fail", 0)), "reconCount": int(summary.get("unknown", 0))}


def get_result(args: dict) -> dict:
    task_id = str(args.get("taskId") or "").strip()
    with _LAST_LOCK:
        cached = dict(_LAST_RESULT)
    report, name = None, ""
    if task_id:
        with SessionLocal() as s:
            row = s.get(WorkpaperTask, task_id)
            if row:
                report = json.loads(row.report_json or "{}")
                name = row.name
        if report is None and cached.get("task_id") == task_id:
            report, name = cached.get("report"), cached.get("name", "")
        if report is None:
            return {"ok": False, "error": "找不到该底稿审核记录"}
    elif cached.get("report"):
        report, name = cached.get("report"), cached.get("name", "")
    else:
        with SessionLocal() as s:
            row = (s.query(WorkpaperTask).filter(WorkpaperTask.status == "done")
                   .order_by(WorkpaperTask.created_at.desc()).first())
            if not row:
                return {"ok": False, "error": "还没有做过底稿审核"}
            report, name = json.loads(row.report_json or "{}"), row.name
    return _result_payload(task_id or cached.get("task_id", ""), name, report)


def _result_payload(task_id, name, report):
    return {
        "ok": True, "taskId": task_id, "name": name,
        "unitCount": report.get("unit_count", 0),
        "fileCount": report.get("file_count", 0),
        "workpaperCount": report.get("workpaper_count", 0),
        "issueCount": report.get("issue_count", 0),
        "reconCount": report.get("recon_count", 0),
        "totalIssues": report.get("issue_count", 0) + report.get("recon_count", 0),
        "levelCount": report.get("level_count", {}),
        "requirements": report.get("requirements", []),
        "assessments": report.get("assessments", []),
        "summary": report.get("summary", {}),
        "issues": report.get("issues", []),
        "reconciliation": report.get("reconciliation", []),
        "fileRoles": report.get("file_roles", []),
        "disclaimer": report.get("disclaimer", ""),
    }
