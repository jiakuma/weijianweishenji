"""合同审计 HTTP 接口：任务列表、详情、报告导出。检测主流程走智能助手工具。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ...auth.common import filter_by_owner, get_current_user
from ...db import SessionLocal
from .models import ContractTask

router = APIRouter()


def _markdown(report: dict, name: str) -> str:
    lines = ["# 合同审计报告", ""]
    lines.append(f"- 主合同：{report.get('main_contract', '')}")
    lines.append(f"- 合同类型：{report.get('contract_type', '')}")
    lines.append(f"- 审计问题：{report.get('issue_count', 0)} 项；跨文件差异：{report.get('recon_count', 0)} 项")
    lines.append("")

    lines.append("## 一、审计问题（文本审核）")
    lines.append("")
    issues = report.get("issues", [])
    if not issues:
        lines.append("本次未检出合同文本问题。")
    for it in issues:
        lines.append(f"### {it.get('index', '')}. {it.get('title', '')}")
        lines.append(f"- 检查维度：{it.get('dimension', '')}　风险分层：{it.get('level', '')}")
        lines.append(f"- 位置：{it.get('locate', '')}")
        lines.append(f"- 事实：{it.get('fact', '')}")
        if it.get("quote"):
            lines.append(f"- 原文：> {it.get('quote', '')}")
        lines.append(f"- 分析：{it.get('analysis', '')}")
        lines.append(f"- 影响：{it.get('impact', '')}")
        lines.append(f"- 建议：{it.get('suggestion', '')}")
        lines.append("")

    lines.append("## 二、跨文件勾稽差异")
    lines.append("")
    recon = report.get("reconciliation", [])
    if not recon:
        lines.append("未提供相关附件或核对后未发现差异。")
    for i, d in enumerate(recon, 1):
        lines.append(f"### {i}. {d.get('field', '')}")
        lines.append(f"- 涉及文件：{d.get('files', '')}")
        lines.append(f"- {d.get('value_a', '')}  ↔  {d.get('value_b', '')}")
        lines.append(f"- 说明：{d.get('note', '')}")
        lines.append("")

    lines.append("## 三、文件清单与使用说明")
    lines.append("")
    for f in report.get("file_roles", []):
        lines.append(f"- {f.get('name', '')}：{f.get('role', '')} / {f.get('type', '')}")
    errs = report.get("errors", [])
    if errs:
        lines.append("- 处理提示：" + "；".join(errs))
    lines.append("")
    lines.append("> " + report.get("disclaimer", "本报告为文本层面的风险提示，不构成违规定性结论。"))
    return "\n".join(lines)


@router.get("/tasks")
def list_tasks(user: dict = Depends(get_current_user)):
    with SessionLocal() as s:
        rows = (filter_by_owner(s.query(ContractTask), ContractTask, user)
                .order_by(ContractTask.created_at.desc()).limit(50).all())
        return [{"id": r.id, "name": r.name, "createdAt": r.created_at, "status": r.status,
                 "contractType": r.contract_type, "issueCount": r.issue_count} for r in rows]


@router.get("/tasks/{task_id}")
def get_task(task_id: str, user: dict = Depends(get_current_user)):
    with SessionLocal() as s:
        r = (filter_by_owner(s.query(ContractTask), ContractTask, user)
             .filter(ContractTask.id == task_id).first())
        if not r:
            return {"found": False}
        return {"found": True, "id": r.id, "name": r.name, "createdAt": r.created_at,
                "status": r.status, "contractType": r.contract_type,
                "report": json.loads(r.report_json or "{}")}


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str, user: dict = Depends(get_current_user)):
    with SessionLocal() as s:
        r = (filter_by_owner(s.query(ContractTask), ContractTask, user)
             .filter(ContractTask.id == task_id).first())
        if r:
            s.delete(r)
            s.commit()
    return {"ok": True}


@router.get("/tasks/{task_id}/export")
def export_report(task_id: str, user: dict = Depends(get_current_user)):
    with SessionLocal() as s:
        r = (filter_by_owner(s.query(ContractTask), ContractTask, user)
             .filter(ContractTask.id == task_id).first())
        if not r:
            return Response("审计记录不存在", status_code=404)
        report = json.loads(r.report_json or "{}")
        content = _markdown(report, r.name or "")
    filename = f"contract-report-{task_id}.md"
    return Response(content.encode("utf-8"), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
