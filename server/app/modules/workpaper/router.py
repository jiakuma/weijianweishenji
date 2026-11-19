"""底稿审核 HTTP 接口：任务列表、详情、报告导出。主流程走智能助手工具。"""
from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import Response

from ...db import SessionLocal
from .models import WorkpaperTask

router = APIRouter()


def _markdown(report: dict, name: str) -> str:
    summary = report.get("summary") or {}
    lines = ["# 底稿审核报告", ""]
    lines.append(f"- 被审计单位：{report.get('unit_count', 0)} 家")
    lines.append(f"- 上位文件：{report.get('plan_count', 0)} 份；审计工作底稿：{report.get('workpaper_count', 0)} 份")
    lines.append("")

    lines.append("## 一、汇总结论")
    lines.append("")
    lines.append(summary.get("conclusion") or "未能从上位文件中提取到可核对的要求点。")
    lines.append("")

    lines.append("## 二、逐项核对")
    lines.append("")
    assessments = report.get("assessments", [])
    if not assessments:
        lines.append("无可核对的要求点。")
    for a in assessments:
        lines.append(f"### {a.get('id', '')} {a.get('requirement', '')}")
        lines.append(f"- 判定：{a.get('result', '无法判断')}")
        if a.get("scope"):
            lines.append(f"- 适用范围：{a.get('scope', '')}")
        if a.get("source"):
            lines.append(f"- 上位文件：{a.get('source', '')}")
        lines.append(f"- 说明：{a.get('reason', '')}")
        for ev in a.get("evidence", []) or []:
            lines.append(f"- 证据：{ev}")
        lines.append("")

    lines.append("## 三、文件清单与使用说明")
    lines.append("")
    for f in report.get("file_roles", []):
        unit = f.get("unit") or "未识别单位"
        topic = f.get("topic") or ""
        lines.append(f"- {f.get('name', '')}：{f.get('role', '')} / {unit} / {topic}")
    errs = report.get("errors", [])
    if errs:
        lines.append("- 处理提示：" + "；".join(errs))
    lines.append("")
    lines.append("> " + report.get("disclaimer", "本报告为文本层面的底稿复核提示，不构成审计结论。"))
    return "\n".join(lines)


@router.get("/tasks")
def list_tasks():
    with SessionLocal() as s:
        rows = (s.query(WorkpaperTask).order_by(WorkpaperTask.created_at.desc()).limit(50).all())
        return [{"id": r.id, "name": r.name, "createdAt": r.created_at, "status": r.status,
                 "unitCount": r.unit_count, "issueCount": r.issue_count} for r in rows]


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    with SessionLocal() as s:
        r = s.get(WorkpaperTask, task_id)
        if not r:
            return {"found": False}
        return {"found": True, "id": r.id, "name": r.name, "createdAt": r.created_at,
                "status": r.status, "report": json.loads(r.report_json or "{}")}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str):
    with SessionLocal() as s:
        r = s.get(WorkpaperTask, task_id)
        if r:
            s.delete(r)
            s.commit()
    return {"ok": True}


@router.get("/tasks/{task_id}/export")
def export_report(task_id: str):
    with SessionLocal() as s:
        r = s.get(WorkpaperTask, task_id)
        if not r:
            return Response("审计记录不存在", status_code=404)
        report = json.loads(r.report_json or "{}")
        content = _markdown(report, r.name or "")
    filename = f"workpaper-report-{task_id}.md"
    return Response(content.encode("utf-8"), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
