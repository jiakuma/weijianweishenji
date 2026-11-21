"""底稿审核 HTTP 接口：任务列表、详情、报告导出。主流程走智能助手工具。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ...auth.common import filter_by_owner, get_current_user
from ...db import SessionLocal
from .models import WorkpaperTask

router = APIRouter()


def _markdown(report: dict, name: str) -> str:
    """按演示报告范式导出：逐项审核结果 + 总体审核结果，不输出技术性清单。"""
    summary = report.get("summary") or {}
    items = report.get("items") or report.get("assessments") or []
    result_text = {"通过": "✅ 审核通过", "不通过": "❌ 审核不通过",
                   "无法判断": "⚠️ 无法判断"}

    lines = ["# 文档审核报告", "", "## 一、详细审核结果", ""]
    for i, item in enumerate(items, 1):
        result = str(item.get("result") or "无法判断").strip()
        if result in ("符合", "不符合"):
            result = {"符合": "通过", "不符合": "不通过"}[result]
        title = str(item.get("topic") or item.get("requirement") or "").strip()
        file_name = str(item.get("file") or "").strip()
        reason = str(item.get("reason") or "").strip()
        reason_label = ("通过原因" if result == "通过"
                        else "不通过原因" if result == "不通过" else "无法判断原因")
        lines.append(f"{item.get('index') or i}. {title}")
        if file_name:
            lines.append(f"对应文件：{file_name}")
        lines.append(f"审核结果：{result_text.get(result, result_text['无法判断'])}")
        lines.append(f"{reason_label}：{reason}")
        lines.append("")

    lines.extend([
        "## 二、总体审核结果", "",
        f"审核要点总数：{summary.get('total', len(items))}项",
        f"✅ 审核通过：{summary.get('pass', 0)}项",
        f"❌ 审核不通过：{summary.get('fail', 0)}项",
        f"总体审核结果：{summary.get('conclusion', '⚠️ 需补充材料')}",
        f"总体审核意见：{summary.get('opinion', '')}", "",
    ])
    disclaimer = report.get("disclaimer") or "本报告为文本层面的底稿复核提示，不构成审计结论。"
    lines.append(f"<small>{disclaimer}</small>")
    return "\n".join(lines)


@router.get("/tasks")
def list_tasks(user: dict = Depends(get_current_user)):
    with SessionLocal() as s:
        rows = (filter_by_owner(s.query(WorkpaperTask), WorkpaperTask, user)
                .order_by(WorkpaperTask.created_at.desc()).limit(50).all())
        return [{"id": r.id, "name": r.name, "createdAt": r.created_at, "status": r.status,
                 "unitCount": r.unit_count, "issueCount": r.issue_count} for r in rows]


@router.get("/tasks/{task_id}")
def get_task(task_id: str, user: dict = Depends(get_current_user)):
    with SessionLocal() as s:
        r = (filter_by_owner(s.query(WorkpaperTask), WorkpaperTask, user)
             .filter(WorkpaperTask.id == task_id).first())
        if not r:
            return {"found": False}
        return {"found": True, "id": r.id, "name": r.name, "createdAt": r.created_at,
                "status": r.status, "report": json.loads(r.report_json or "{}")}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, user: dict = Depends(get_current_user)):
    with SessionLocal() as s:
        r = (filter_by_owner(s.query(WorkpaperTask), WorkpaperTask, user)
             .filter(WorkpaperTask.id == task_id).first())
        if r:
            s.delete(r)
            s.commit()
    return {"ok": True}


@router.get("/tasks/{task_id}/export")
def export_report(task_id: str, user: dict = Depends(get_current_user)):
    with SessionLocal() as s:
        r = (filter_by_owner(s.query(WorkpaperTask), WorkpaperTask, user)
             .filter(WorkpaperTask.id == task_id).first())
        if not r:
            return Response("审计记录不存在", status_code=404)
        report = json.loads(r.report_json or "{}")
        content = _markdown(report, r.name or "")
    filename = f"workpaper-report-{task_id}.md"
    return Response(content.encode("utf-8"), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
