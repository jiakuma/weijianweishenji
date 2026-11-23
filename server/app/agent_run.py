"""智能体循环调度器（P2：只读工具）。

服务层：不依赖 HTTP 请求/响应对象，由 main.py 的 SSE 控制器驱动。
模型输出一律不信任：工具名白名单与参数校验交给 agent_tools，
步数/超时/重复调用/取消在这里兜底，工具入参与结果落 agent_steps 留痕。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

import httpx

from . import agent_tools, qa
from .llm import llm_config, post_chat
from .db import (
    AgentRun,
    AgentStep,
    QARow,
    QASessionItem,
    SessionLocal,
    get_raw_settings,
    now_str,
)

log = logging.getLogger("knowledge.agent")

MAX_STEPS = 6                 # 最多几轮「决定 + 调工具」
MAX_CALLS_PER_STEP = 3        # 单轮最多几个工具调用
STEP_TIMEOUT = 30             # 单次模型调用超时（秒）
TOTAL_TIMEOUT = 120           # 整体超时（秒）
MAX_POOL = 12                 # 引用候选池上限

_CAPABILITY: dict = {"value": None, "checked_at": 0.0}
_CAPABILITY_TTL = 3600
_CANCEL: dict[str, threading.Event] = {}
_CANCEL_LOCK = threading.Lock()

CONFIRM_TIMEOUT = 60  # 秒，超时一律按拒绝处理
_CONFIRM: dict[str, dict] = {}
_CONFIRM_LOCK = threading.Lock()


def _open_confirm(run_id: str, tool: str, label: str, args: dict, risk: str) -> str:
    cid = f"c-{uuid.uuid4().hex[:10]}"
    with _CONFIRM_LOCK:
        _CONFIRM[cid] = {"runId": run_id, "tool": tool, "label": label,
                         "args": args, "risk": risk,
                         "event": threading.Event(), "decision": None}
    return cid


def decide_confirm(confirm_id: str, decision: str) -> bool:
    if decision not in ("approve", "reject"):
        return False
    with _CONFIRM_LOCK:
        item = _CONFIRM.get(confirm_id)
        if not item or item["event"].is_set():
            return False
        item["decision"] = decision
        item["event"].set()
    return True


def _await_confirm(confirm_id: str, timeout: int | None = None) -> str:
    with _CONFIRM_LOCK:
        item = _CONFIRM.get(confirm_id)
    if not item:
        return "reject"
    approved = item["event"].wait(timeout if timeout is not None else CONFIRM_TIMEOUT)
    decision = item["decision"] if approved else "timeout"
    with _CONFIRM_LOCK:
        _CONFIRM.pop(confirm_id, None)
    return decision or "timeout"


# ---------------------------------------------------------------- 配置与能力探测

def _conf() -> tuple[str, str, str, float, int]:
    """保留原元组签名；配置统一由 llm.py 提供（DeepSeek 官方 API）。"""
    cfg = llm_config()
    return cfg.base, cfg.key, cfg.model, cfg.temperature, cfg.max_tokens


def has_llm() -> bool:
    base, key, model, _, _ = _conf()
    return bool(base and key and model)


def probe_tool_calls(force: bool = False) -> bool:
    """探测上游是否接受 tools / tool_choice；结果缓存 1 小时，失败一律按不支持处理。

    DeepSeek-V4.1-Flash 官方 API 原生支持 tool_calls；探测不重试、不开思考，
    避免拖慢 Agent 首响。
    """
    now = time.time()
    if not force and _CAPABILITY["value"] is not None and now - _CAPABILITY["checked_at"] < _CAPABILITY_TTL:
        return bool(_CAPABILITY["value"])
    cfg = llm_config()
    ok = False
    if cfg.ready:
        try:
            data = post_chat(
                {"model": cfg.model,
                 "messages": [{"role": "user", "content": "ping"}],
                 "tools": agent_tools.tools_schema()[:1],
                 "tool_choice": "auto", "max_tokens": 16, "temperature": 0},
                timeout=10, config=cfg, retries=0,
            )
            msg = data["choices"][0]["message"]
            ok = isinstance(msg, dict)
        except Exception as exc:
            log.info(json.dumps({"event": "probe_failed", "error": type(exc).__name__}, ensure_ascii=False))
    _CAPABILITY["value"] = ok
    _CAPABILITY["checked_at"] = now
    log.info(json.dumps({"event": "probe_tool_calls", "supported": ok}, ensure_ascii=False))
    return ok


def request_cancel(run_id: str) -> threading.Event:
    with _CANCEL_LOCK:
        ev = _CANCEL.setdefault(run_id, threading.Event())
    return ev


def cancel(run_id: str) -> bool:
    with _CANCEL_LOCK:
        ev = _CANCEL.get(run_id)
        if ev:
            ev.set()
    # 该运行下挂起的确认立即按拒绝处理，避免连接断开后线程空等
    with _CONFIRM_LOCK:
        for item in _CONFIRM.values():
            if item.get("runId") == run_id and not item["event"].is_set():
                item["decision"] = "reject"
                item["event"].set()
    return bool(ev)


def is_cancelled(run_id: str) -> bool:
    with _CANCEL_LOCK:
        ev = _CANCEL.get(run_id)
        return bool(ev and ev.is_set())


def clear_cancel(run_id: str) -> None:
    with _CANCEL_LOCK:
        _CANCEL.pop(run_id, None)


# ---------------------------------------------------------------- 模型决策

@dataclass
class Call:
    name: str
    args: dict
    call_id: str = ""


@dataclass
class Decision:
    calls: list[Call] = field(default_factory=list)
    final: dict | None = None
    assistant_message: dict = field(default_factory=dict)
    native: bool = False


_TOOL_LIST = "\n".join(
    f"- {t['name']}（{t['label']}）：{t['description']}"
    for t in agent_tools.list_tools()
)

_SYSTEM_NATIVE = (
    "你是知识库智能体。可以用给定工具查资料，自己决定查几次、要不要换关键词重查、什么时候回答。\n"
    "规则：\n"
    "1. 只依据工具返回的资料回答，不得编造；资料不足就明确说明。\n"
    "2. 需要查资料时调用工具；已经够了就直接输出最终答案。\n"
    "3. 最终答案输出严格 JSON：{\"conclusion\": \"正文\", \"basis\": [\"依据1\", \"依据2\"]}。\n"
    "   conclusion 用自然语言 Markdown 写：先给一句核心结论，再按需用「**小标题**」分段、"
    "- 列表列要点，把关键数字和判断写清楚；不要写成机械的一句话，也不要复述工具调用过程。\n"
    "   禁止在答案里写检索过程统计或技术术语：不要出现「检索命中 N 篇文档 / N 条切片 / 主要分类为…」"
    "「召回」「切片」「向量检索」「相关度」等字样；直接给出制度名称、文号和要点，并保留「须由审计人员复核」的提示即可。\n"
    "4. 引用编号 [数字] 直接内联在相关句子末尾，编号取自工具结果里的「可用引用」；basis 放次要依据。\n"
    "5. 若问题含「它 / 那个 / 上面 / 刚才 / 再 / 这几步」等指代，必须先用「最近对话」补全成完整问题"
    "再检索，不要直接要求用户补充；只有最近对话里确实找不到指代对象时才请用户补充。\n"
    "6. 某次检索没有命中时，换关键词或换角度再查（不要重复同一查询）；连续两次无结果就如实说明没查到。\n"
    "7. 用户上传文件后，问题末尾会出现「待检测的文档 ID：tmp-xxx、tmp-yyy」，这些 tmp- 开头的就是本次要处理的文件。"
    "当用户要求审合同/看合同有没有问题/查违规时，必须直接调用 audit_contract 并把全部 tmp- ID 作为 docIds 传入；"
    "当用户要求审底稿/复核审计工作底稿/检查底稿是否规范时，必须直接调用 audit_workpaper 并把全部 tmp- ID 作为 docIds 传入；"
    "当用户要求查制度冲突/互斥时，必须直接调用 detect_conflict。不要先查导入任务，也不要回答「没收到文件」。\n"
    "8. 判定类问题最高优先级：当用户描述某种现象并问「是否违规 / 违反什么制度 / 有什么依据 / "
    "有没有法条 / 以前有没有类似审计问题」时，必须优先调用 judge_violation，而不是 recall_regulations "
    "或 search_docs。judge_violation 一次返回历史相似问题及其定性依据、反查到的制度条款原文和可点击引用；"
    "不要只用 recall_regulations / search_docs，也不要凭空判断。"
    "正文提到历史问题时，必须用其对应的 [编号] 标注（编号取自「可用引用」中标为〔历史问题〕的条目），"
    "用户点击该角标可查看历史问题详情。回答时明确说明这是相似性参考，不构成违规定性，最终定性须由审计人员复核。\n"
    "9. 只有单纯找某类制度、询问库里有哪些相关制度/规定（如「差旅费有哪些制度」「预算管理相关的制度都有什么」）时，"
    "才调用 recall_regulations 而不是 search_docs，它返回文档级制度清单；search_docs 只返回碎片切片。"
    "助手只做检索与提示，制度适用与定性须由审计人员复核。\n"
    "10. 不要输出 JSON 以外的任何内容。"
)

_FINAL_SYSTEM = (
    "你是知识库问答助手。只依据已知信息回答，不得编造。"
    "输出严格 JSON：{\"conclusion\": \"正文\", \"basis\": [\"依据1\"]}。"
    "conclusion 用自然语言 Markdown 写：先给核心结论，再按需用 **小标题** 分段、- 列表列要点，"
    "把关键数字和判断写清楚，不要只写一句干巴巴的话。"
    "禁止写检索过程统计或技术术语：不要出现「检索命中 N 篇文档 / N 条切片 / 主要分类为…」「召回」「切片」「向量检索」「相关度」等字样，"
    "直接给出制度名称、文号和要点，并保留「须由审计人员复核」的提示。"
    "引用编号 [数字] 内联在相关句末，basis 放次要依据。不要输出 JSON 以外的任何内容。"
)

_SYSTEM_JSON = (
    "你是知识库智能体。可以用给定工具查资料，自己决定查几次、要不要换关键词重查、什么时候回答。\n"
    "可用工具：\n" + _TOOL_LIST + "\n\n"
    "每一步只输出严格 JSON，二选一：\n"
    "{\"tool_calls\": [{\"name\": \"工具名\", \"args\": {...}}]}   ← 还要查资料\n"
    "{\"answer\": {\"conclusion\": \"正文\", \"basis\": [\"依据1\"]}}   ← 可以回答了\n"
    "conclusion 用自然语言 Markdown：先核心结论，再按需用 **小标题** 分段、- 列表列要点，关键数字写清楚，不要只写一句话。\n"
    "禁止写检索过程统计或技术术语：不要出现「检索命中 N 篇文档 / N 条切片 / 主要分类为…」「召回」「切片」「向量检索」「相关度」等字样，"
    "直接给出制度名称、文号和要点，并保留「须由审计人员复核」的提示。\n"
    "规则：只依据工具返回的资料回答，不得编造；资料不足就明确说明；"
    "引用编号 [数字] 内联在相关句末，编号取自工具结果里的「可用引用」；basis 放次要依据；"
    "问题含指代时必须先用「最近对话」补全成完整问题再检索，只有历史里也找不到对象时才请用户补充；"
    "检索没命中就换关键词再查，连续两次无结果如实说明；"
    "用户上传文件后问题末尾会出现「待检测的文档 ID：tmp-xxx、tmp-yyy」，这些 tmp- 开头的就是本次要处理的文件。"
    "当用户要求审合同/看合同有没有问题/查违规时，必须直接调用 audit_contract 并把全部 tmp- ID 作为 docIds 传入；"
    "当用户要求审底稿/复核审计工作底稿/检查底稿是否规范时，必须直接调用 audit_workpaper 并把全部 tmp- ID 作为 docIds 传入；"
    "当用户要求查制度冲突/互斥时，必须直接调用 detect_conflict。不要先查导入任务，也不要回答「没收到文件」。"
    "判定类问题最高优先级：当用户描述某种现象并问「是否违规 / 违反什么制度 / 有什么依据 / "
    "有没有法条 / 以前有没有类似审计问题」时，必须优先调用 judge_violation，而不是 recall_regulations "
    "或 search_docs。judge_violation 同时返回历史相似问题及定性依据、制度条款原文和可点击引用；"
    "正文提到历史问题时必须用对应的 [编号] 标注（编号取自「可用引用」中标为〔历史问题〕的条目），"
    "用户点击角标可查看历史问题详情；回答时说明这是相似性参考，不构成违规定性，须由审计人员复核。"
    "只有单纯找某类制度、询问库里有哪些相关制度/规定时才调用 recall_regulations 而不是 search_docs，"
    "它返回文档级制度清单；助手只做检索与提示，制度适用与定性须由审计人员复核。"
    "不要输出 JSON 以外的任何内容。"
)


def _parse_final(raw: str) -> dict | None:
    if not raw:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    obj = data.get("answer") if isinstance(data.get("answer"), dict) else data
    conclusion = str(obj.get("conclusion") or "").strip()
    if not conclusion:
        return None
    basis = [str(x).strip() for x in (obj.get("basis") or []) if str(x).strip()]
    from .qa import build_markdown
    return {"conclusion": conclusion, "basis": basis,
            "markdown": build_markdown(conclusion, basis)}


# 模型偶尔会把工具返回里的检索统计当成答案输出（如「检索命中 50 篇相关文档、
# 约 110 条切片，主要分类为…」）。这类过程细节对审计师无意义，行级删除兜底。
_RE_RETRIEVAL_STAT = re.compile(
    r"(检索命中|共命中|命中\s*\d+|召回\s*\d+|检索到\s*\d+).{0,40}(篇|条|切片|文档|分类)"
    r"|(约|共)?\s*\d+\s*条切片"
    r"|主要分类为"
    r"|(\d+\s*条|\d+\s*篇).{0,12}相关(片段|文档|资料)"
)


def _strip_retrieval_stats(text: str) -> str:
    """删除检索过程统计/技术术语所在的行，保留制度名称、文号等实质内容。"""
    if not text:
        return text
    kept = [line for line in text.splitlines()
            if not _RE_RETRIEVAL_STAT.search(line.strip())]
    return "\n".join(kept).strip()


def _post(payload: dict, timeout: int) -> dict:
    return post_chat(payload, timeout=timeout, config=llm_config())


def _finalize(messages: list[dict], pool: list[dict]) -> dict | None:
    """不带 tools 的收尾请求：把已有工具结果压成纯文本，避免残留 tool 消息结构。"""
    _, _, model, _, max_tokens = _conf()
    lines: list[str] = []
    for m in messages[1:]:
        content = m.get("content") or ""
        if m.get("role") == "tool":
            lines.append(f"工具结果：{content[:1200]}")
        elif m.get("tool_calls"):
            lines.append("助手决定查资料")
        elif content:
            lines.append(content[:1500])
    prompt = (
        "用户问题：" + (messages[1].get("content") if len(messages) > 1 else "") +
        "\n\n已知信息：\n" + "\n".join(lines[-8:]) +
        "\n\n可用引用：\n" + _citation_lines(pool) +
        "\n\n请直接输出最终答案 JSON。"
    )
    try:
        data = _post({"model": model,
                      "messages": [{"role": "system", "content": _FINAL_SYSTEM},
                                   {"role": "user", "content": prompt}],
                      "temperature": 0.2, "max_tokens": max_tokens}, STEP_TIMEOUT)
        return _parse_final(data["choices"][0]["message"].get("content") or "")
    except Exception as exc:
        log.error(json.dumps({"event": "finalize_failed", "error": type(exc).__name__},
                             ensure_ascii=False))
        return None


def _decide(messages: list[dict], native: bool) -> Decision:
    _, _, model, temperature, max_tokens = _conf()
    common = {"model": model, "messages": messages, "temperature": min(temperature, 0.3),
              "max_tokens": max_tokens}
    if native:
        payload = {**common, "tools": agent_tools.tools_schema(), "tool_choice": "auto"}
        data = _post(payload, STEP_TIMEOUT)
        msg = data["choices"][0]["message"]
        raw_calls = msg.get("tool_calls") or []
        if raw_calls:
            calls = []
            for tc in raw_calls[:MAX_CALLS_PER_STEP]:
                fn = tc.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                calls.append(Call(name=str(fn.get("name") or ""), args=args,
                                  call_id=str(tc.get("id") or "")))
            return Decision(calls=calls, assistant_message=msg, native=True)
        return Decision(final=_parse_final(msg.get("content") or ""),
                        assistant_message=msg, native=True)

    data = _post(common, STEP_TIMEOUT)
    content = data["choices"][0]["message"].get("content") or ""
    assistant_message = {"role": "assistant", "content": content}
    text = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.M).strip()
    try:
        parsed = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return Decision(final=_parse_final(content), assistant_message=assistant_message)
        try:
            parsed = json.loads(m.group(0))
        except Exception:
            return Decision(final=_parse_final(content), assistant_message=assistant_message)
    if not isinstance(parsed, dict):
        return Decision(assistant_message=assistant_message)
    if isinstance(parsed.get("answer"), dict):
        return Decision(final=_parse_final(json.dumps(parsed["answer"], ensure_ascii=False)),
                        assistant_message=assistant_message)
    raw_calls = parsed.get("tool_calls") or []
    calls = []
    for c in raw_calls[:MAX_CALLS_PER_STEP]:
        if not isinstance(c, dict):
            continue
        args = c.get("args") if isinstance(c.get("args"), dict) else {}
        calls.append(Call(name=str(c.get("name") or ""), args=args))
    return Decision(calls=calls, assistant_message=assistant_message)


# ---------------------------------------------------------------- 运行记录

def _open_run(run_id: str, session_id: str, question: str) -> None:
    with SessionLocal() as s:
        s.add(AgentRun(id=run_id, session_id=session_id, question=question,
                       status="running", created_at=now_str()))
        s.commit()


def _close_run(run_id: str, status: str, steps: int, cost_ms: int) -> None:
    with SessionLocal() as s:
        row = s.get(AgentRun, run_id)
        if row:
            row.status, row.steps_count, row.cost_ms = status, steps, cost_ms
            row.finished_at = now_str()
            s.commit()


def _record_step(run_id: str, seq: int, kind: str, tool_name: str,
                 args: dict, result: dict, cost_ms: int) -> None:
    with SessionLocal() as s:
        s.add(AgentStep(id=f"as-{run_id}-{seq}-{int(time.time() * 1000) % 100000}",
                        run_id=run_id, seq=seq, kind=kind, tool_name=tool_name,
                        args_json=args, result_json=result, cost_ms=cost_ms))
        s.commit()


# ---------------------------------------------------------------- 编排

def _session_context(session_id: str, limit: int = 6) -> str:
    """取同一会话最近几轮问答，供模型补全「它 / 那个 / 刚才」等指代。"""
    if not session_id:
        return ""
    with SessionLocal() as s:
        items = (s.query(QASessionItem)
                 .filter(QASessionItem.session_id == session_id)
                 .order_by(QASessionItem.seq.desc()).limit(limit).all())
        lines: list[str] = []
        for item in reversed(items):
            row = s.get(QARow, item.qa_id)
            if not row:
                continue
            answer = row.answer or {}
            conclusion = str(answer.get("conclusion") or "").strip()[:200]
            lines.append(f"问：{row.question}\n答：{conclusion}")
    if not lines:
        return ""
    return "最近对话（仅用于补全指代，不是新问题的资料依据）：\n" + "\n".join(lines)


def _citation_lines(pool: list[dict]) -> str:
    """给模型看的「可用引用」清单：历史问题与制度条款用不同前缀区分。"""
    if not pool:
        return "（暂无可引用资料）"
    lines: list[str] = []
    for i, h in enumerate(pool):
        if h.get("kind") == "issue":
            label = h.get("catL4") or h.get("category") or "未分类"
            detail = h.get("manifestation") or (h.get("problemDesc") or "")[:80]
            lines.append(
                f"[{i + 1}] 〔历史问题·{h.get('sourceYear', '')}〕{label} · {detail}"
            )
        else:
            lines.append(
                f"[{i + 1}] 《{h['docTitle']}》第 {h['page']} 页 · {h.get('snippet', '')[:80]}"
            )
    return "\n".join(lines)


def _citations_from(pool: list[dict], basis: list[str], markdown: str = "") -> list[dict]:
    """从 basis 和正文里出现的 [n] 抽取引用，保留两类资料的完整可点击字段。"""
    used: set[int] = set()
    for text in list(basis or []) + [markdown or ""]:
        for n in re.findall(r"\[(\d+)\]", text):
            idx = int(n)
            if 1 <= idx <= len(pool):
                used.add(idx - 1)
    if not used:
        used = set(range(min(3, len(pool))))
    citations: list[dict] = []
    for i in sorted(used):
        h = pool[i]
        if h.get("kind") == "issue":
            citations.append({
                "kind": "issue", "refNo": i + 1,
                "issueId": h.get("issueId", ""),
                "sourceYear": h.get("sourceYear", ""),
                "sourceFile": h.get("sourceFile", ""),
                "auditSubject": h.get("auditSubject", ""),
                "auditProject": h.get("auditProject", ""),
                "category": h.get("category", ""),
                "catL1": h.get("catL1", ""), "catL2": h.get("catL2", ""),
                "catL3": h.get("catL3", ""), "catL4": h.get("catL4", ""),
                "manifestation": h.get("manifestation", ""),
                "problemDesc": h.get("problemDesc", ""),
                "legalBasis": h.get("legalBasis", ""),
                "remediation": h.get("remediation", ""),
                "docTitle": h.get("docTitle", ""),
                "docId": "", "chunkId": "", "section": "历史问题", "page": 0,
                "quote": h.get("quote") or h.get("snippet") or "",
            })
        else:
            citations.append({
                "kind": "doc", "refNo": i + 1,
                "docId": h.get("docId", ""), "docTitle": h.get("docTitle", ""),
                "chunkId": h.get("chunkId", ""), "page": h.get("page", 0),
                "section": h.get("section", "正文"), "quote": h.get("quote", ""),
            })
    return citations


# 模块入口决定默认审核工具：前端四个入口的提问文本高度相似，
# 仅靠问题措辞猜工具会把制度互斥/底稿审核都做成合同审计。
_MODULE_TOOL_HINT = {
    "conflict": (
        "当前处于「制度互斥」模块：本次上传的是制度文件，"
        "必须直接调用 detect_conflict 做制度互斥检测，禁止调用 audit_contract 或 audit_workpaper。"
    ),
    "contract": (
        "当前处于「合同审计」模块：本次上传的是合同及附件，"
        "必须直接调用 audit_contract 做合同审计，禁止调用 detect_conflict 或 audit_workpaper。"
    ),
    "workpaper": (
        "当前处于「底稿审核」模块：本次上传的是审计实施方案与审计工作底稿，"
        "必须直接调用 audit_workpaper 做底稿复核，禁止调用 audit_contract 或 detect_conflict。"
    ),
}


def _answer_chunks(text: str, size: int = 18):
    """把最终 Markdown 切成小段推送，前端逐字显示。

    模型给出的最终答案本身已生成完毕，这里只负责呈现节奏：按字/词切分并保留
    换行边界，避免一次性整段出现。审计长报告不参与（由专门的结果卡片承载）。
    """
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch == "\n" or len(buf) >= size:
            yield "".join(buf)
            buf = []
    if buf:
        yield "".join(buf)


def _push_answer(markdown: str, on_answer_delta=None) -> None:
    if not markdown or on_answer_delta is None:
        return
    for chunk in _answer_chunks(markdown):
        try:
            on_answer_delta(chunk)
        except Exception:
            return
        time.sleep(0.012)


def run_agent(question: str, mode: str, run_id: str, session_id: str = "",
              module: str = "assistant",
              on_step=None, on_status=None, on_confirm=None,
              on_answer_delta=None) -> dict:
    """返回 QAResult 结构。快速通道不进循环；LLM 不可用时退回固定管线。
    写工具经 on_confirm 挂起等待用户决定，超时或拒绝一律不执行。"""
    question = (question or "").strip()
    now = now_str()

    # 快速通道：寒暄 / 库内统计 / 无 LLM —— 沿用 qa.py 已验证的行为，不多花一次调用
    if (not has_llm()) or qa._rule_route(question) is not None or qa._is_stats_question(question):
        quick = qa.answer_question(question, mode)
        answer = quick.get("answer") or {}
        _push_answer(answer.get("markdown") or answer.get("conclusion") or "", on_answer_delta)
        return quick

    cancel_ev = request_cancel(run_id)
    native = probe_tool_calls()
    _open_run(run_id, session_id, question)
    started = time.time()
    # 长工具（如制度互斥检测，可能耗时数分钟）的执行时间不计入总超时。
    # TOTAL_TIMEOUT 只约束模型决策/等待确认等「空转」时间，避免工具成功落库后
    # 因工具自身耗时被误判为整体超时、丢弃已得结果。
    tool_time = 0.0
    system = _SYSTEM_NATIVE if native else _SYSTEM_JSON
    hint = _MODULE_TOOL_HINT.get((module or "").strip().lower(), "")
    if hint:
        system += (
            "\n\n【当前模块任务 · 最高优先级】\n" + hint +
            "\n若用户问题里明确要求了另一种任务（明确说查制度互斥 / 审合同 / 审底稿），以用户明确表述为准。"
            "问题末尾「待检测的文档 ID：tmp-...」中的全部 tmp- 文件必须作为 docIds 传入对应工具。"
        )
    history = _session_context(session_id)
    if history:
        system += (
            "\n\n" + history +
            "\n当前问题若含「它 / 那个 / 上面 / 刚才 / 再 / 这几步」等指代，"
            "必须用最近一轮的问答补全成完整问题（例如「它主要讲了哪几步」→「上一轮讨论的 RAG 召回率优化主要讲了哪几步」），"
            "然后直接检索作答，不要要求用户重新说明。"
        )
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"用户问题：{question}"},
    ]
    pool: list[dict] = []
    actions: list[dict] = []
    seen_signatures: set[str] = set()
    empty_streak = 0      # 连续检索空结果次数
    failed_streak = 0     # 连续工具失败次数
    decision: Decision | None = None
    steps = 0
    status = "done"

    try:
        for step in range(MAX_STEPS):
            if cancel_ev.is_set():
                status = "stopped"
                break
            if time.time() - started - tool_time > TOTAL_TIMEOUT:
                status = "failed"
                break
            steps = step + 1
            try:
                decision = _decide(messages, native)
            except Exception as exc:
                log.error(json.dumps({"runId": run_id, "step": steps,
                                      "event": "decide_failed", "error": type(exc).__name__},
                                     ensure_ascii=False))
                status = "failed"
                break

            if cancel_ev.is_set():
                status = "stopped"
                break
            if decision.final:
                break
            if not decision.calls:
                # 模型既没调工具也没给答案：基于已有资料强制收尾一次
                tail = _finalize(messages, pool)
                if tail:
                    decision = Decision(final=tail)
                    break
                status = "failed"
                break

            messages.append(decision.assistant_message if decision.assistant_message
                            else {"role": "assistant", "content": ""})
            followups: list[dict] = []
            hints: list[str] = []
            for call in decision.calls:
                spec = agent_tools.get_spec(call.name)
                sig = json.dumps([call.name, call.args], sort_keys=True, ensure_ascii=False)
                confirmed = "n/a"
                if sig in seen_signatures:
                    result = {"ok": False, "tool": call.name, "error": "重复调用已跳过", "costMs": 0}
                elif spec and spec.needs_confirm:
                    # 写动作：挂起等用户点确认，超时/拒绝一律不执行
                    cid = _open_confirm(run_id, call.name, spec.label, call.args, spec.risk)
                    if on_confirm:
                        on_confirm({"confirmId": cid, "tool": call.name, "label": spec.label,
                                    "args": call.args, "risk": spec.risk})
                    # 等待时间受总超时约束，避免多次挂起累加突破上限
                    left = TOTAL_TIMEOUT - (time.time() - started - tool_time)
                    confirmed = _await_confirm(cid, timeout=max(5, min(CONFIRM_TIMEOUT, int(left))))
                    if confirmed == "approve":
                        log.info(json.dumps({"runId": run_id, "tool": call.name,
                                             "event": "write_approved"}, ensure_ascii=False))
                        result = agent_tools.call_tool(call.name, call.args, run_id=run_id)
                        tool_time += (result.get("costMs") or 0) / 1000.0
                    else:
                        reason = "用户拒绝执行" if confirmed == "reject" else "确认超时，未执行"
                        log.info(json.dumps({"runId": run_id, "tool": call.name,
                                             "event": "write_blocked", "reason": confirmed},
                                            ensure_ascii=False))
                        result = {"ok": False, "tool": call.name, "error": reason, "costMs": 0}
                else:
                    seen_signatures.add(sig)
                    result = agent_tools.call_tool(call.name, call.args, run_id=run_id)
                    # 工具执行耗时从总超时预算中扣除：制度互斥检测等长任务不应
                    # 因自身耗时（可能 > TOTAL_TIMEOUT）拖垮整个 agent 的判定。
                    tool_time += (result.get("costMs") or 0) / 1000.0
                    if result.get("ok") and call.name in ("search_docs", "recall_regulations", "judge_violation"):
                        data = result.get("data") or {}
                        # 历史问题与制度条款进同一个引用池，正文里的 [n] 才能同时点到两类详情。
                        if call.name == "judge_violation":
                            for ref in data.get("references") or []:
                                if len(pool) >= MAX_POOL:
                                    break
                                issue_id = ref.get("issueId") or ""
                                if not issue_id or any(
                                    p.get("kind") == "issue" and p.get("issueId") == issue_id
                                    for p in pool
                                ):
                                    continue
                                label = ref.get("catL4") or ref.get("category") or "未分类"
                                quote = ref.get("problemDesc") or ref.get("manifestation") or ""
                                pool.append({
                                    "kind": "issue",
                                    "issueId": issue_id,
                                    "docTitle": f"历史问题·{ref.get('sourceYear', '')}·{label}",
                                    "page": 0,
                                    "snippet": quote[:120],
                                    "quote": quote[:120],
                                    "sourceYear": ref.get("sourceYear", ""),
                                    "sourceFile": ref.get("sourceFile", ""),
                                    "auditSubject": ref.get("auditSubject", ""),
                                    "auditProject": ref.get("auditProject", ""),
                                    "category": ref.get("category", ""),
                                    "catL1": ref.get("catL1", ""),
                                    "catL2": ref.get("catL2", ""),
                                    "catL3": ref.get("catL3", ""),
                                    "catL4": ref.get("catL4", ""),
                                    "manifestation": ref.get("manifestation", ""),
                                    "problemDesc": ref.get("problemDesc", ""),
                                    "legalBasis": ref.get("legalBasis", ""),
                                    "remediation": ref.get("remediation", ""),
                                })
                        for h in data.get("hits") or []:
                            if len(pool) >= MAX_POOL:
                                break
                            if not h.get("docId"):
                                continue
                            if any(
                                p.get("kind") != "issue" and p.get("docId") == h.get("docId")
                                and p.get("chunkId") == h.get("chunkId")
                                for p in pool
                            ):
                                continue
                            item = dict(h)
                            item.setdefault("kind", "doc")
                            pool.append(item)
                # 失败换路：空结果/异常时给模型明确的重试提示，连续两次则收尾
                if confirmed != "n/a" and not result.get("ok"):
                    hints.append("该写操作未获用户同意，不要重复尝试同类操作，直接说明情况。")
                elif result.get("ok") and call.name in ("search_docs", "recall_regulations", "judge_violation"):
                    data = result.get("data") or {}
                    # judge_violation 的 total 只统计制度 hits，历史问题也要算进命中数。
                    total = (len(data.get("references") or []) + len(data.get("hits") or [])
                             if call.name == "judge_violation"
                             else data.get("total", 0))
                    if total == 0:
                        empty_streak += 1
                        failed_streak = 0
                        hints.append("这次检索没有命中任何资料，请换一组关键词或换角度再查，不要重复同一查询。")
                    else:
                        empty_streak = 0
                        failed_streak = 0
                elif not result.get("ok"):
                    failed_streak += 1
                    hints.append(f"工具 {call.name} 未成功（{result.get('error')}），请换个工具或调整参数再试。")
                else:
                    failed_streak = 0
                action = {
                    "tool": call.name,
                    "label": result.get("label") or (spec.label if spec else call.name),
                    "ok": bool(result.get("ok")),
                    "costMs": result.get("costMs", 0),
                    "writable": bool(spec and not spec.readonly),
                    "confirmed": confirmed,
                    "detail": {"args": call.args, "result": result.get("data") or result.get("error")},
                }
                actions.append(action)
                _record_step(run_id, steps, "tool", call.name, call.args,
                             {**result, "confirmed": confirmed}, result.get("costMs", 0))
                if on_step:
                    on_step(action)
                brief = json.dumps(result.get("data") if result.get("ok") else
                                   {"error": result.get("error")}, ensure_ascii=False)[:2500]
                if native:
                    followups.append({"role": "tool", "tool_call_id": call.call_id,
                                      "content": brief})
                else:
                    followups.append({"role": "user",
                                      "content": f"工具 {call.name} 的结果：{brief}"})
            if native:
                # tool 消息条数必须与 tool_calls 一致，否则上游会拒绝
                messages[-1] = {
                    "role": "assistant",
                    "content": decision.assistant_message.get("content") or "",
                    "tool_calls": [{
                        "id": c.call_id or f"call-{steps}-{i}",
                        "type": "function",
                        "function": {"name": c.name,
                                     "arguments": json.dumps(c.args, ensure_ascii=False)},
                    } for i, c in enumerate(decision.calls)],
                }
                for i, fu in enumerate(followups):
                    fu["tool_call_id"] = decision.calls[i].call_id or f"call-{steps}-{i}"
            messages.extend(followups)
            for hint in hints:
                messages.append({"role": "user", "content": hint})
            if empty_streak >= 2 or failed_streak >= 2:
                # 连续失败不再空耗步数：基于已有资料收尾，如实说明没查到
                tail = _finalize(messages, pool)
                if tail:
                    decision = Decision(final=tail)
                    break
                status = "failed"
                break
            if pool:
                messages.append({"role": "system" if not native else "user",
                                 "content": "可用引用（basis 句末标注编号）：\n" + _citation_lines(pool)})
            if on_status:
                on_status(steps)
        else:
            status = "failed"  # 用尽步数

        if status == "failed" and not (decision and decision.final) and pool:
            tail = _finalize(messages, pool)
            if tail:
                decision = Decision(final=tail)

        # 兜底：制度互斥检测等长工具已成功执行并落库，但因超时/步数耗尽未能
        # 正常收尾时，主动读取检测结果生成结论，避免把成功的结果误报成「没查到」。
        conflict_detail = None
        has_conflict_result = any(
            a.get("tool") == "get_conflict_result" and a.get("ok")
            and (a.get("detail") or {}).get("result", {}).get("conflicts") is not None
            for a in actions
        )
        if not has_conflict_result:
            for act in actions:
                if act.get("tool") == "detect_conflict" and act.get("ok"):
                    detail = (act.get("detail") or {}).get("result") or {}
                    if detail.get("taskId"):
                        conflict_detail = detail
                        break
        if conflict_detail:
            task_id = conflict_detail.get("taskId", "")
            get_res = agent_tools.call_tool("get_conflict_result", {"taskId": task_id})
            get_data = (get_res.get("data") or {}) if get_res.get("ok") else {}
            conflicts = get_data.get("conflicts") or []
            count = get_data.get("conflictCount", conflict_detail.get("conflictCount", 0))
            # 补一条 get_conflict_result action：前端据此渲染冲突卡片，
            # 后端 _persist_qa 据此把完整 conflict 写入 _meta 供刷新恢复。
            if conflicts:
                gact = {
                    "tool": "get_conflict_result", "label": "查互斥检测结果",
                    "ok": True, "costMs": get_res.get("costMs", 0),
                    "writable": False, "confirmed": "n/a",
                    "detail": {"args": {"taskId": task_id}, "result": get_data},
                }
                actions.append(gact)
                if on_step:
                    on_step(gact)
            final = {
                "conclusion": (f"已完成制度互斥检测（任务 ID {task_id}）：共检出 {count} 处互斥条款。"
                               if conflicts else
                               f"已完成制度互斥检测（任务 ID {task_id}），未发现相互冲突的条款。"),
                "basis": [],
            }
            decision = Decision(final=final)

        # 底稿审核同理：长工具成功后即使模型没主动查详情，也补齐结果供前端渲染与持久化。
        workpaper_detail = None
        has_workpaper_result = any(
            a.get("tool") == "get_workpaper_result" and a.get("ok")
            and (a.get("detail") or {}).get("result", {}).get("issues") is not None
            for a in actions
        )
        if not has_workpaper_result:
            for act in actions:
                if act.get("tool") == "audit_workpaper" and act.get("ok"):
                    detail = (act.get("detail") or {}).get("result") or {}
                    if detail.get("taskId"):
                        workpaper_detail = detail
                        break
        if workpaper_detail:
            task_id = workpaper_detail.get("taskId", "")
            get_res = agent_tools.call_tool("get_workpaper_result", {"taskId": task_id})
            get_data = (get_res.get("data") or {}) if get_res.get("ok") else {}
            if get_data.get("issues") is not None:
                gact = {
                    "tool": "get_workpaper_result", "label": "查底稿审核结果",
                    "ok": True, "costMs": get_res.get("costMs", 0),
                    "writable": False, "confirmed": "n/a",
                    "detail": {"args": {"taskId": task_id}, "result": get_data},
                }
                actions.append(gact)
                if on_step:
                    on_step(gact)
            wp_summary = get_data.get("summary") or {}
            total = int(wp_summary.get("total", 0))
            pass_count = int(wp_summary.get("pass", 0))
            fail_count = int(wp_summary.get("fail", 0))
            final = {
                "conclusion": (f"已完成底稿审核：审核要点 {total} 项，通过 {pass_count} 项、"
                               f"不通过 {fail_count} 项。"),
                "basis": [],
            }
            decision = Decision(final=final)

        final = (decision.final if decision else None) or {}
        if not final.get("conclusion"):
            if pool:
                final = {
                    "conclusion": "已查到相关资料，但未能在限定步数内生成完整结论，以下为供参考的制度条款。",
                    "basis": [f"《{pool[i]['docTitle']}》第 {pool[i]['page']} 页 [{i + 1}]"
                              for i in range(min(3, len(pool)))],
                }
            else:
                final = {"conclusion": "没有查到能回答这个问题的资料，换个说法或先导入相关资料。",
                         "basis": []}
        # 兜底：模型偶尔把检索统计写进答案，行级删除后重算 markdown，保证展示与结论一致。
        cleaned = _strip_retrieval_stats(final.get("conclusion", ""))
        if cleaned and cleaned != final.get("conclusion"):
            final["conclusion"] = cleaned
            final.pop("markdown", None)  # 让下方 build_markdown 用清洗后的结论重算
        final["basis"] = [b for b in (_strip_retrieval_stats(x) for x in final.get("basis", [])) if b]
        notice = {
            "done": f"智能体自主检索完成（{steps} 步 · 原生工具调用：{'是' if native else '否'}），内容仅供参考；请点击引用核对原文。",
            "stopped": "已停止，返回已查到的结果。",
            "failed": "达到步数/超时上限或调用失败，返回已查到的结果。",
        }[status]
        from .qa import build_markdown
        markdown = final.get("markdown") or build_markdown(
            final["conclusion"], final.get("basis", []))
        _push_answer(markdown, on_answer_delta)
        result = {
            "question": question,
            "answer": {"conclusion": final["conclusion"],
                       "basis": final.get("basis", []), "notice": notice,
                       "markdown": markdown},
            "citations": _citations_from(pool, final.get("basis", []), markdown),
            "createdAt": now,
            "recallMode": mode,
            "route": "agent",
            "actions": actions,
            "rewrittenQuery": question,
            "subQueries": [],
            "usedLLM": True,
            "retrievalCount": len(pool),
            "retried": False,
            "agentRunId": run_id,
            "agentSteps": steps,
            "nativeTools": native,
            "feedback": None,
        }
        _record_step(run_id, steps + 1, "answer", "", {},
                     {"conclusion": final["conclusion"]}, 0)
        return result
    finally:
        _close_run(run_id, status, steps, int((time.time() - started) * 1000))
        # 取消标志由控制器在 SSE 收尾时清理，这里保留以便控制器判断是否写库
