"""智能体工具箱。

控制器不写业务逻辑；工具集中注册、统一白名单与参数校验后执行，
模型输出（工具名与入参）一律不信任，禁止透传未知字段。
P3 起写入类工具全部标 needs_confirm，由人在回路确认后才执行。
涉及后台任务的动作通过 hook 复用 main.py 现有路由函数，不改主流程。
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .db import (
    Category,
    Chunk,
    Document,
    DocumentFulltext,
    ImportTaskRow,
    NoteRow,
    SessionLocal,
    get_raw_settings,
    now_str,
)
from .search import recall_regulations, search_hits

log = logging.getLogger("knowledge.agent")

RESERVED_CATEGORY = "未分类"  # 与 main.py 保持一致


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    description: str
    parameters: dict
    readonly: bool
    handler: Callable[[dict], Any]
    needs_confirm: bool = False
    risk: str = "low"  # low / high


_REGISTRY: dict[str, ToolSpec] = {}
_HOOKS: dict[str, Callable[[dict], Any]] = {}  # 后台任务等动作，由 main.py 注入
_progress = contextvars.ContextVar("agent_progress", default=None)  # 长任务进度发射器


def bind_progress(fn) -> object:
    """把当前 SSE 流的进度发射器绑定到本线程上下文，工具内部可实时推事件。"""
    return _progress.set(fn)


def reset_progress(token) -> None:
    _progress.reset(token)


def get_progress():
    return _progress.get()


def emit_progress(payload: dict) -> None:
    fn = _progress.get()
    if fn:
        try:
            fn(payload)
        except Exception:
            pass


def register(spec: ToolSpec) -> None:
    if spec.name in _REGISTRY:
        raise ValueError(f"工具重复注册：{spec.name}")
    _REGISTRY[spec.name] = spec


def register_hook(name: str, fn: Callable[[dict], Any]) -> None:
    """注入依赖控制器的动作（提交后台任务等），避免工具层反向依赖 main。"""
    _HOOKS[name] = fn


def get_spec(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def list_tools() -> list[dict]:
    return [{
        "name": s.name,
        "label": s.label,
        "description": s.description,
        "parameters": s.parameters,
        "readonly": s.readonly,
        "needsConfirm": s.needs_confirm,
        "risk": s.risk,
    } for s in _REGISTRY.values()]


def tools_schema() -> list[dict]:
    """转成 OpenAI 兼容的 tools 参数，供原生 function calling 使用。"""
    return [{
        "type": "function",
        "function": {
            "name": s.name,
            "description": f"{s.label}：{s.description}",
            "parameters": s.parameters,
        },
    } for s in _REGISTRY.values()]


def _validate(spec: ToolSpec, args: dict) -> tuple[dict, str | None]:
    """按 JSON schema 的 required / type / enum 做边界校验，剔除未知字段。"""
    schema = spec.parameters or {}
    props = schema.get("properties", {})
    clean: dict = {}
    for key in schema.get("required", []):
        if key not in args or args[key] in (None, ""):
            return {}, f"缺少参数 {key}"
    for key, value in args.items():
        rule = props.get(key)
        if not rule:  # 未知字段直接丢弃，不透传给 handler
            continue
        expected = rule.get("type")
        if expected == "string" and not isinstance(value, str):
            return {}, f"参数 {key} 应为字符串"
        if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            return {}, f"参数 {key} 应为整数"
        if expected == "number" and not isinstance(value, (int, float)):
            return {}, f"参数 {key} 应为数字"
        if expected == "boolean" and not isinstance(value, bool):
            return {}, f"参数 {key} 应为布尔值"
        if expected == "array" and not isinstance(value, list):
            return {}, f"参数 {key} 应为数组"
        if "enum" in rule and value not in rule["enum"]:
            return {}, f"参数 {key} 取值不合法"
        clean[key] = value
    for key, rule in props.items():
        if "minimum" in rule and key in clean and clean[key] < rule["minimum"]:
            return {}, f"参数 {key} 小于最小值"
        if "maximum" in rule and key in clean and clean[key] > rule["maximum"]:
            return {}, f"参数 {key} 超过最大值"
        if rule.get("type") == "string" and "maxLength" in rule and key in clean:
            clean[key] = clean[key][: rule["maxLength"]]
    return clean, None


def call_tool(name: str, args: dict | None = None, run_id: str = "") -> dict:
    """执行工具：白名单 → 参数校验 → handler → 结构化结果（含耗时）。"""
    started = time.time()
    args = args or {}
    spec = _REGISTRY.get(name)
    if not spec:
        return {"ok": False, "tool": name, "error": "未注册的工具", "costMs": 0}
    clean, err = _validate(spec, args)
    if err:
        return {"ok": False, "tool": name, "error": err, "costMs": 0}
    try:
        data = spec.handler(clean)
        result = {"ok": True, "tool": name, "label": spec.label, "data": data,
                  "costMs": int((time.time() - started) * 1000)}
    except Exception as exc:  # 不向客户端抛堆栈
        log.error(json.dumps({"runId": run_id, "tool": name, "event": "tool_failed",
                              "error": type(exc).__name__}, ensure_ascii=False))
        return {"ok": False, "tool": name, "error": f"工具执行失败：{type(exc).__name__}",
                "costMs": int((time.time() - started) * 1000)}
    log.info(json.dumps({"runId": run_id, "tool": name, "event": "tool_done",
                         "costMs": result["costMs"]}, ensure_ascii=False))
    return result


# ---------------------------------------------------------------- 只读工具

def _list_categories(_: dict) -> dict:
    """列出知识库全部分类，供改分类时选择目标分类名。"""
    with SessionLocal() as s:
        cats = s.query(Category).order_by(Category.name).all()
        return {"total": len(cats), "categories": [c.name for c in cats]}


def _count_files(_: dict) -> dict:
    """库内统计：直接查库，不检索文档内容。"""
    with SessionLocal() as s:
        docs = s.query(Document).all()
        by_cat: dict[str, int] = {}
        for d in docs:
            name = d.category or "未分类"
            by_cat[name] = by_cat.get(name, 0) + 1
        return {
            "documents": len(docs),
            "parsed": sum(1 for d in docs if d.status == "已解析"),
            "chunks": s.query(Chunk).count(),
            "categories": s.query(Category).count(),
            "totalKb": sum(d.size_kb or 0 for d in docs),
            "byCategory": sorted(
                [{"name": k, "count": v} for k, v in by_cat.items()],
                key=lambda x: -x["count"],
            ),
        }


def _search_docs(args: dict) -> dict:
    q = args["query"]
    mode = args.get("mode", "混合")
    limit = args.get("limit", 5)
    hits = search_hits(q, mode, limit=min(limit, 20))
    return {
        "query": q,
        "total": len(hits),
        "hits": [{
            "docId": h["docId"], "docTitle": h["docTitle"], "chunkId": h["chunkId"],
            "page": h["page"], "section": h["section"], "snippet": h["snippet"],
            "score": h["score"],
            "quote": re.sub(r"\s+", " ", h.get("text") or "").strip()[:120],
        } for h in hits],
    }


def _recall_regulations(args: dict) -> dict:
    """制度全量召回：返回文档级制度清单，只查制度库，不含历史审计问题条目。"""
    limit = int(args.get("limit") or 20)
    return recall_regulations(args["query"], limit=min(limit, 50))


def _read_fulltext(args: dict) -> dict:
    with SessionLocal() as s:
        row = s.get(DocumentFulltext, args["docId"])
        doc = s.get(Document, args["docId"])
        if not row:
            return {"found": False, "docId": args["docId"]}
        return {
            "found": True,
            "docId": args["docId"],
            "title": doc.title if doc else "",
            "charCount": row.char_count,
            "text": (row.text or "")[:4000],
        }


def _list_tasks(_: dict) -> dict:
    with SessionLocal() as s:
        rows = s.query(ImportTaskRow).order_by(ImportTaskRow.created_at.desc()).limit(20).all()
        return {"total": len(rows), "items": [
            {"id": r.id, "name": r.name, "status": r.status, "progress": r.progress,
             "message": r.message} for r in rows]}


def _get_settings(_: dict) -> dict:
    with SessionLocal() as s:
        st = get_raw_settings(s)
    keys = ("displayName", "pageSize", "autoSummary", "autoClassify", "autoChunk",
            "llmModel", "embeddingModel", "temperature", "maxTokens")
    return {k: st.get(k) for k in keys}


register(ToolSpec(
    name="count_files", label="查库统计",
    description="统计知识库现有资料数、已解析数、知识切片数、分类数、文件总大小及按分类的分布。用于回答「库里有多少文件/资料/切片/分类」等问题。",
    parameters={"type": "object", "properties": {}},
    readonly=True, handler=_count_files,
))

register(ToolSpec(
    name="search_docs", label="搜资料",
    description="按关键词检索知识库切片，返回标题、页码、章节、摘要与相关度。",
    parameters={"type": "object", "properties": {
        "query": {"type": "string", "maxLength": 200},
        "mode": {"type": "string", "enum": ["混合", "关键词", "语义"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
    }, "required": ["query"]},
    readonly=True, handler=_search_docs,
))

register(ToolSpec(
    name="recall_regulations", label="召回制度清单",
    description=(
        "当用户想找某类制度、要列举/盘点/召回库里所有相关制度或规定时使用"
        "（如「差旅费有哪些制度」「预算管理相关的制度都有什么」「费用报销方面有哪些规定」）。"
        "返回文档级制度清单（docId、标题、分类、相关度、命中理由），最多 50 篇；"
        "与 search_docs 的区别是：search_docs 返回相关切片，本工具返回完整制度清单。"
        "只召回制度库已解析文档，不含历史审计问题条目；"
        "助手仅做检索与提示，制度适用与定性须由审计人员复核。"
    ),
    parameters={"type": "object", "properties": {
        "query": {"type": "string", "maxLength": 200},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
    }, "required": ["query"]},
    readonly=True, handler=_recall_regulations,
))

register(ToolSpec(
    name="read_fulltext", label="看全文",
    description="读取某篇资料的完整解析文本（最多 4000 字）。",
    parameters={"type": "object", "properties": {
        "docId": {"type": "string", "maxLength": 64},
    }, "required": ["docId"]},
    readonly=True, handler=_read_fulltext,
))

register(ToolSpec(
    name="list_tasks", label="看导入任务",
    description="查看最近 20 条导入/解析任务的状态与进度。",
    parameters={"type": "object", "properties": {}},
    readonly=True, handler=_list_tasks,
))

register(ToolSpec(
    name="get_settings", label="看设置",
    description="读取知识库当前配置（模型、自动处理开关等），不返回密钥。",
    parameters={"type": "object", "properties": {}},
    readonly=True, handler=_get_settings,
))

register(ToolSpec(
    name="list_categories", label="列分类",
    description="列出知识库全部分类名称，改分类时先调用本工具确认目标分类名。",
    parameters={"type": "object", "properties": {}},
    readonly=True, handler=_list_categories,
))


# ---------------------------------------------------------------- 制度互斥检测

def _detect_conflict(args: dict) -> dict:
    """对指定资料做制度互斥检测。docIds 来自 search_docs / 用户上传后返回的资料。"""
    fn = _HOOKS.get("detect_conflict")
    if not fn:
        return {"ok": False, "error": "制度互斥检测未启用"}
    return fn(args)


register(ToolSpec(
    name="detect_conflict", label="制度互斥检测",
    description=(
        "对指定的多份制度/规定文件做互斥（冲突）检测：解析条款、语义召回、逐对比对，"
        "输出相互冲突的条款及原文定位。当用户要求「查制度冲突/互斥/有没有打架的规定」时使用。"
        "docIds 为资料 ID 列表（可先用 search_docs 找到库内资料，或直接使用用户"
        "在智能助手里上传附件后提供的 tmp- 开头的临时文件 ID）；"
        "focus 为可选的关注点（如「采购审批权限」）以缩小检测范围。"
    ),
    parameters={"type": "object", "properties": {
        "docIds": {"type": "array", "maxItems": 20},
        "focus": {"type": "string", "maxLength": 100},
    }, "required": ["docIds"]},
    readonly=True, handler=_detect_conflict,
))


def _get_conflict_result(args: dict) -> dict:
    fn = _HOOKS.get("get_conflict_result")
    if not fn:
        return {"ok": False, "error": "制度互斥检测未启用"}
    return fn(args)


register(ToolSpec(
    name="get_conflict_result", label="查互斥检测结果",
    description=(
        "读取最近一次制度互斥检测的冲突明细（含每条冲突的说明、甲乙双方原文与定位）。"
        "当用户追问「刚才检测的详情/第几条冲突的依据/导出报告」时使用。"
        "传入 taskId 读取指定检测，留空则读取最近一次。"
    ),
    parameters={"type": "object", "properties": {
        "taskId": {"type": "string", "maxLength": 64},
    }},
    readonly=True, handler=_get_conflict_result,
))


# ---------------------------------------------------------------- 合同审计

def _audit_contract(args: dict) -> dict:
    """对上传的合同及附件做文本审核与跨文件勾稽。"""
    fn = _HOOKS.get("audit_contract")
    if not fn:
        return {"ok": False, "error": "合同审计未启用"}
    return fn(args)


register(ToolSpec(
    name="audit_contract", label="合同审计",
    description=(
        "对用户上传的合同文件做审计：自动识别主合同与附件、识别合同类型，"
        "按十条维度检查合同文本问题，并对招标/中标/验收/付款等附件做跨文件勾稽，"
        "输出带原文定位的风险提示与待核实事项（不做违规定性）。"
        "当用户要求「审合同/看合同是否违规/合同有没有问题/核对合同和附件」时使用。"
        "docIds 为智能助手上传附件后提供的 tmp- 开头临时文件 ID 列表。"
    ),
    parameters={"type": "object", "properties": {
        "docIds": {"type": "array", "maxItems": 20},
    }, "required": ["docIds"]},
    readonly=True, handler=_audit_contract,
))


def _get_contract_result(args: dict) -> dict:
    fn = _HOOKS.get("get_contract_result")
    if not fn:
        return {"ok": False, "error": "合同审计未启用"}
    return fn(args)


register(ToolSpec(
    name="get_contract_result", label="查合同审计结果",
    description=(
        "读取最近一次（或指定 taskId 的）合同审计的明细：审计问题（事实/原文/位置/依据/建议）、"
        "跨文件勾稽差异与三级风险分层。当用户追问「刚才审计的详情/第几条问题/导出报告」时使用。"
    ),
    parameters={"type": "object", "properties": {
        "taskId": {"type": "string", "maxLength": 64},
    }},
    readonly=True, handler=_get_contract_result,
))


def _audit_workpaper(args: dict) -> dict:
    """对上传的审计实施方案与审计工作底稿做复核。"""
    fn = _HOOKS.get("audit_workpaper")
    if not fn:
        return {"ok": False, "error": "底稿审核未启用"}
    return fn(args)


register(ToolSpec(
    name="audit_workpaper", label="底稿审核",
    description=(
        "对用户上传的审计实施方案和审计工作底稿做复核：自动识别方案/底稿角色与被审计单位，"
        "按完整性、程序一致性、证据充分性、事实结论、上位文件依据、数据勾稽和表述规范检查底稿，"
        "并核对方案要求与已提交底稿的覆盖关系，输出带原文定位和补正建议的复核提示（不做违规定性）。"
        "当用户要求「审底稿/复核审计工作底稿/检查底稿是否规范/看底稿能不能支撑结论」时使用。"
        "docIds 为智能助手上传附件后提供的 tmp- 开头临时文件 ID 列表。"
    ),
    parameters={"type": "object", "properties": {
        "docIds": {"type": "array", "maxItems": 30},
    }, "required": ["docIds"]},
    readonly=True, handler=_audit_workpaper,
))


def _get_workpaper_result(args: dict) -> dict:
    fn = _HOOKS.get("get_workpaper_result")
    if not fn:
        return {"ok": False, "error": "底稿审核未启用"}
    return fn(args)


register(ToolSpec(
    name="get_workpaper_result", label="查底稿审核结果",
    description=(
        "读取最近一次（或指定 taskId 的）底稿审核明细：复核问题（事实/原文/上位文件依据/建议）、"
        "跨文件勾稽差异与风险分层。当用户追问「刚才底稿审核的详情/第几条问题/导出报告」时使用。"
    ),
    parameters={"type": "object", "properties": {
        "taskId": {"type": "string", "maxLength": 64},
    }},
    readonly=True, handler=_get_workpaper_result,
))


# ---------------------------------------------------------------- 写入工具（P3，全部需确认）

def _via_hook(hook: str):
    """复用 main.py 已验证的动作实现，返回结果里统一补上 taskId（若有）。"""
    def run(args: dict) -> dict:
        fn = _HOOKS.get(hook)
        if not fn:
            return {"ok": False, "error": "动作未注册"}
        out = fn(args) or {}
        if not isinstance(out, dict):
            return {"ok": False, "error": "动作返回异常"}
        return out
    return run


def _set_tags(args: dict) -> dict:
    with SessionLocal() as s:
        d = s.get(Document, args["docId"])
        if not d:
            return {"ok": False, "error": "资料不存在"}
        tags = [str(t).strip() for t in args["tags"] if str(t).strip()][:10]
        d.tags = tags
        s.commit()
        return {"ok": True, "docTitle": d.title, "tags": tags}


def _set_category(args: dict) -> dict:
    with SessionLocal() as s:
        d = s.get(Document, args["docId"])
        if not d:
            return {"ok": False, "error": "资料不存在"}
        cat = str(args.get("category") or "").strip()
        if not cat:
            return {"ok": False, "error": "分类不能为空"}
        if cat != RESERVED_CATEGORY:
            exists = s.query(Category).filter(Category.name == cat).first()
            if not exists:
                return {"ok": False, "error": f"分类「{cat}」不存在，请先用 list_categories 查看可选分类"}
        d.category = cat
        s.commit()
        return {"ok": True, "docTitle": d.title, "category": cat}


def _set_star(args: dict) -> dict:
    with SessionLocal() as s:
        d = s.get(Document, args["docId"])
        if not d:
            return {"ok": False, "error": "资料不存在"}
        d.starred = bool(args["starred"])
        s.commit()
        return {"ok": True, "docTitle": d.title, "starred": bool(d.starred)}


def _add_note(args: dict) -> dict:
    with SessionLocal() as s:
        d = s.get(Document, args["docId"])
        if not d:
            return {"ok": False, "error": "资料不存在"}
        n = NoteRow(id=f"n-{uuid.uuid4().hex[:12]}", doc_id=d.id, doc_title=d.title,
                    content=args["content"].strip(), created_at=now_str())
        s.add(n)
        s.commit()
        return {"ok": True, "noteId": n.id, "docTitle": d.title}


def _reparse_doc(args: dict) -> dict:
    out = _via_hook("reparse_doc")(args)
    if out.get("ok"):
        return {"ok": True, "taskId": out.get("taskId"), "docId": args["docId"]}
    return {"ok": False, "error": out.get("message") or "重新解析失败"}


def _retry_task(args: dict) -> dict:
    out = _via_hook("retry_task")(args)
    return {"ok": bool(out.get("ok")), "taskId": args["taskId"] if out.get("ok") else None,
            "error": None if out.get("ok") else "任务不存在或无法重试"}


def _retry_failed_tasks(_: dict) -> dict:
    with SessionLocal() as s:
        rows = s.query(ImportTaskRow).filter(ImportTaskRow.status.in_(["失败", "解析失败"])).all()
        ids = []
        for t in rows:
            t.progress, t.status, t.stage, t.message = 0, "等待中", "", ""
            t.attempt = (t.attempt or 0) + 1
            ids.append(t.id)
        s.commit()
    submit = _HOOKS.get("submit_import")
    for tid in ids:
        if submit:
            submit({"taskId": tid})
    return {"ok": True, "count": len(ids), "taskIds": ids}


def _import_link(args: dict) -> dict:
    out = _via_hook("import_link")(args)
    tid = out.get("id") or out.get("taskId")
    if out.get("status") == "失败":
        return {"ok": False, "error": out.get("message") or "导入失败"}
    return {"ok": True, "taskId": tid, "name": out.get("name") or args.get("name") or args["url"]}


def _export_doc(args: dict) -> dict:
    out = _via_hook("export_doc")(args)
    content = str(out.get("content") or "")
    if not content:
        return {"ok": False, "error": "资料不存在或无可导出内容"}
    return {"ok": True, "filename": out.get("filename"), "charCount": len(content),
            "preview": content[:200]}


def _delete_doc(args: dict) -> dict:
    out = _via_hook("delete_doc")(args)
    return {"ok": bool(out.get("ok")), "originalKept": out.get("originalKept"),
            "error": None if out.get("ok") else "资料不存在"}


def _delete_category(args: dict) -> dict:
    out = _via_hook("delete_category")(args)
    return {"ok": bool(out.get("ok")), "removedDocs": out.get("removedDocs", 0),
            "error": out.get("message") or (None if out.get("ok") else "分类不存在")}


_WRITE_TOOLS: list[tuple[str, str, str, dict, Callable[[dict], Any], str]] = [
    ("set_tags", "改标签", "给指定资料设置标签（最多 10 个），会覆盖原有标签。改分类请用 set_category。", {
        "docId": {"type": "string", "maxLength": 64},
        "tags": {"type": "array"},
    }, _set_tags, "low"),
    ("set_category", "改分类", "把指定资料移动到某个已存在的分类（先 list_categories 确认名称；'未分类'为常驻分类可直接用）。", {
        "docId": {"type": "string", "maxLength": 64},
        "category": {"type": "string", "maxLength": 40},
    }, _set_category, "low"),
    ("set_star", "设收藏", "设置指定资料的收藏状态。", {
        "docId": {"type": "string", "maxLength": 64},
        "starred": {"type": "boolean"},
    }, _set_star, "low"),
    ("add_note", "写笔记", "给指定资料追加一条笔记。", {
        "docId": {"type": "string", "maxLength": 64},
        "content": {"type": "string", "maxLength": 1000},
    }, _add_note, "low"),
    ("reparse_doc", "重新解析", "按原始文件重新解析指定资料，返回后台任务 ID。", {
        "docId": {"type": "string", "maxLength": 64},
    }, _reparse_doc, "low"),
    ("retry_import_task", "重试导入任务", "重试一个导入/解析任务，返回后台任务 ID。", {
        "taskId": {"type": "string", "maxLength": 64},
    }, _retry_task, "low"),
    ("retry_failed_tasks", "重试所有失败任务", "把所有状态为失败/解析失败的导入任务重新排队。", {},
     _retry_failed_tasks, "low"),
    ("import_link", "导入网页链接", "抓取一个网页或知乎链接并导入知识库，返回后台任务 ID。", {
        "url": {"type": "string", "maxLength": 500},
        "name": {"type": "string", "maxLength": 80},
    }, _import_link, "low"),
    ("export_doc", "导出资料", "生成指定资料的 Markdown 导出内容，返回文件名、字数与开头预览。", {
        "docId": {"type": "string", "maxLength": 64},
    }, _export_doc, "low"),
    ("delete_doc", "删资料", "删除知识库中的资料副本；purge=true 时连磁盘原始文件一起永久删除，不可恢复。", {
        "docId": {"type": "string", "maxLength": 64},
        "purge": {"type": "boolean"},
    }, _delete_doc, "high"),
    ("delete_category", "删分类", "删除分类及其全部子分类，分类下的资料也会一并从知识库删除（原始文件保留）。", {
        "catId": {"type": "string", "maxLength": 64},
    }, _delete_category, "high"),
]

_REQUIRED_ARGS = {
    "set_tags": ["docId", "tags"], "set_category": ["docId", "category"],
    "set_star": ["docId", "starred"],
    "add_note": ["docId", "content"], "reparse_doc": ["docId"],
    "retry_import_task": ["taskId"], "retry_failed_tasks": [],
    "import_link": ["url"], "export_doc": ["docId"],
    "delete_doc": ["docId"], "delete_category": ["catId"],
}

for _name, _label, _desc, _props, _handler, _risk in _WRITE_TOOLS:
    register(ToolSpec(
        name=_name, label=_label, description=_desc,
        parameters={"type": "object", "properties": _props, "required": _REQUIRED_ARGS[_name]},
        readonly=False, handler=_handler, needs_confirm=True, risk=_risk,
    ))
