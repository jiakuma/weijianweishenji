"""优化版智能问答管线。

参考知识库内「RAG 与大模型检索技术」系列文档的优化路径落地：
- 查询理解：Query Rewrite（口语→标准表述）+ Multi-Query（多角度改写）+ 关键词增强
- 混合检索：多路检索后用 RRF 融合（词法腿，因当前未接入向量库）
- 重排序：LLM 对候选集精排（Cross-Encoder 的 LLM 平替）
- Agentic 自检：检索薄弱时自动换角度改写并重试一次
- 上下文处理：近似去重 + 防 Lost-in-the-Middle（最相关内容置两端）+ 带引用 Grounding Prompt

所有 LLM 步骤在「未配置大模型」时优雅降级：查询理解跳过、重排退回词法序、
生成退回本地抽取式摘要，系统仍可回答。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

import httpx

from .db import SessionLocal, get_raw_settings
from . import agent_tools
from .llm import llm_config, post_chat
from .search import (
    _citations,
    _extractive,
    build_terms,
    search_hits,
)


# ---------------------------------------------------------------- LLM 基础

def _llm_settings() -> tuple[str, str, str, float, int]:
    """保留原元组签名；配置统一由 llm.py 提供（DeepSeek 官方地址 / Key / 模型）。"""
    cfg = llm_config()
    return cfg.base, cfg.key, cfg.model, cfg.temperature, cfg.max_tokens


def _has_llm() -> bool:
    return llm_config().ready


def _llm_text(messages: list[dict], temperature: float = 0.2) -> str | None:
    cfg = llm_config()
    if not cfg.ready:
        return None
    try:
        # 知识问答允许开启官方思考模式；条款抽取等结构化任务一律关闭
        data = post_chat(
            {"model": cfg.model, "messages": messages,
             "temperature": temperature, "max_tokens": cfg.max_tokens},
            timeout=30, config=cfg, thinking=cfg.thinking,
        )
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def _llm_json(messages: list[dict], temperature: float = 0.2) -> dict | None:
    raw = _llm_text(messages, temperature)
    if not raw:
        return None
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        data = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, flags=re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except Exception:
            return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 意图识别与路由

# 双层网关第一层：关键词规则，零成本覆盖高频寒暄（文档：以低成本覆盖高频意图）
_GREET_RE = re.compile(
    r"^(hi|hello|hey|yo|hiya|good morning|good afternoon|good evening|"
    r"你好|您好|你好呀|你好啊|嗨|哈喽|哈啰|哈罗|在吗|在不在|"
    r"早上好|中午好|下午好|晚上好|早啊)"
    r"[\s!！.。,，~～、?？:：;；'\"]*$", re.I,
)
_THANKS_RE = re.compile(
    r"^(谢谢|多谢|感谢|thanks|thank you|thx|3q|辛苦了|好的|收到|嗯|嗯嗯|哦|行|ok|okay)"
    r"[\s!！.。,，~～、?？:：;；'\"]*$", re.I,
)
_BYE_RE = re.compile(
    r"^(再见|拜拜|下次见|bye|bye bye|goodbye|see you|先走了)"
    r"[\s!！.。,，~～、?？:：;；'\"]*$", re.I,
)
_META_RE = re.compile(
    r"^(你是谁|你叫什么|你能做什么|你能干嘛|你会什么|help|帮助)"
    r"[\s!！.。,，~～、?？:：;；'\"]*$", re.I,
)


def _rule_route(question: str) -> str | None:
    """规则层：命中非知识意图时返回一句直接回应，未命中返回 None。"""
    s = question.strip()
    if not s:
        return "请输入想查询的问题。"
    if not re.search(r"[\u4e00-\u9fa5a-zA-Z0-9]", s):
        return "没太看懂，直接用一句话描述你想查的问题吧。"
    if _GREET_RE.match(s):
        return "你好，有什么想查的资料？"
    if _THANKS_RE.match(s):
        return "不客气，有需要随时问。"
    if _BYE_RE.match(s):
        return "好的，再见。"
    if _META_RE.match(s):
        return "我可以基于已导入的资料回答问题，直接问就行。"
    return None


# ---------------------------------------------------------------- 查询理解

def _understand(question: str) -> dict:
    """一次调用完成「意图识别 + 查询改写」，位于检索之前。
    返回 {route, reply, rewritten, subQueries, keywords}。
    LLM 不可用或解析失败时兜底默认走知识检索（文档建议，比直接拒绝更友好）。"""
    base = {
        "route": "knowledge", "reply": "", "rewritten": question,
        "subQueries": [], "keywords": build_terms(question)[:8],
    }
    if not _has_llm():
        return base
    sys = (
        "你是 RAG 系统的意图识别与查询理解器。先判断意图类型，再输出 JSON：\n"
        "{\"route\": \"knowledge|chitchat|clarify|stats\", "
        "\"reply\": \"chitchat 时给一句简短自然的中文寒暄回应；clarify 时给一句请用户补充关键信息的问话；其余为空字符串\", "
        "\"rewritten\": \"把问题改写成适合知识库检索的标准表述（仅 knowledge 需要）\", "
        "\"subQueries\": [\"2-3 个不同检索角度的改写问题（仅 knowledge 需要）\"], "
        "\"keywords\": [\"决定性关键词，最多 8 个\"]}\n"
        "分类规则：\n"
        "- chitchat：寒暄、打招呼、致谢、告别、闲聊、情绪表达，或与知识库无关的随意输入，无需检索资料。\n"
        "- stats：询问知识库自身的状态或数量（有多少文件/资料/切片/分类、占用多少空间、导入了多少），可直接查库回答，不必检索文档内容。\n"
        "- clarify：问题过于模糊、大量指代、缺少关键信息，无法有效检索，需请用户补充。\n"
        "- knowledge：任何需要从知识库检索资料内容才能回答的实质性问题。\n"
        "只输出 JSON，不要解释。"
    )
    out = _llm_json([
        {"role": "system", "content": sys},
        {"role": "user", "content": f"用户输入：{question}"},
    ])
    if not out:
        return base
    route = str(out.get("route") or "knowledge").strip()
    if route not in ("knowledge", "chitchat", "clarify", "stats"):
        route = "knowledge"
    return {
        "route": route,
        "reply": str(out.get("reply") or "").strip()[:160],
        "rewritten": str(out.get("rewritten") or "").strip() or question,
        "subQueries": [str(x).strip() for x in (out.get("subQueries") or []) if str(x).strip()][:3],
        "keywords": [str(x).strip() for x in (out.get("keywords") or []) if str(x).strip()][:8],
    }


def _clarify(question: str, fallback: str) -> str:
    """模糊问题：轻量检索知识库子主题，生成贴合内容的澄清提示（而非通用提示）。
    检索结果只用于措辞，不作为答案依据、不产生引用。"""
    default = fallback or "问题信息有点少，能补充一下具体想查什么吗？"
    if not _has_llm():
        return default
    titles = [h["docTitle"] for h in search_hits(question, "混合", limit=5)]
    hint = "、".join(f"《{t}》" for t in dict.fromkeys(titles)) or "（暂无相关资料）"
    sys = (
        "你是知识库问答助手。用户的问题过于模糊，请结合知识库中现存的主题，"
        "生成一句简短的中文澄清问话，引导用户明确想问的方向。"
        "只输出这一句话，不要解释，不要回答问题。"
    )
    out = _llm_text([
        {"role": "system", "content": sys},
        {"role": "user", "content": f"用户问题：{question}\n知识库相关主题：{hint}"},
    ])
    return (out or "").strip()[:160] or default


def build_markdown(conclusion: str, basis: list[str] | None = None) -> str:
    """把结论与依据组装成自然语言 Markdown 正文。

    conclusion 本身可能已含 Markdown；basis 作为要点列表附在下方。
    这样前端可以用一套渲染逻辑呈现所有回答，而旧字段继续保留兼容。
    """
    parts = [str(conclusion or "").strip()]
    for item in basis or []:
        text = str(item).strip()
        if not text:
            continue
        line = text if text.startswith(("- ", "* ", "### ")) else f"- {text}"
        parts.append(line)
    return "\n\n".join(p for p in parts if p).strip()


def _direct_result(question: str, mode: str, now: str, route: str, reply: str) -> dict:
    """非知识意图的直接回应：无检索、无引用、无依据。"""
    notice = {
        "chitchat": "无需检索，直接回应。",
        "clarify": "信息不足，已请求补充，未做检索。",
    }.get(route, "直接读取知识库数据，无需检索。")
    return {
        "question": question,
        "answer": {"conclusion": reply, "basis": [], "notice": notice,
                   "markdown": build_markdown(reply)},
        "citations": [],
        "createdAt": now,
        "recallMode": mode,
        "route": route,
        "actions": [],
        "rewrittenQuery": question,
        "subQueries": [],
        "usedLLM": False,
        "retrievalCount": 0,
        "retried": False,
        "feedback": None,
    }


# ---------------------------------------------------------------- 库内统计

_STATS_QTY = re.compile(r"多少|几个|几篇|几份|几条|总数|总共|一共|数量|数目|统计|共计|多大|多少篇")
_STATS_OBJ = re.compile(r"文件|资料|文档|文章|切片|分类|类别|标签|知识库|存储|占用|空间|篇|条|个分类")
_STATS_BLOCK = re.compile(
    r"怎么|如何|为什么|原理|区别|优化|实现|方案|步骤|方法|是什么|有哪些内容|"
    r"分别|总结|对比|各找|找几|讲讲|讲了|分析|介绍|内容|观点|有哪些"
)


def _is_stats_question(question: str) -> bool:
    """判断是否为「库里有多少文件」这类可直接查库的统计问法。"""
    q = question.strip()
    if len(q) > 60 or _STATS_BLOCK.search(q):
        return False
    return bool(_STATS_QTY.search(q) and _STATS_OBJ.search(q))


def _stats_result(question: str, mode: str, now: str) -> dict:
    call = agent_tools.call_tool("count_files", {}, run_id=now)
    data = call.get("data") or {}
    if not call.get("ok") or not data:
        return _direct_result(question, mode, now, "clarify", "暂时读不到知识库统计，请稍后再试。")
    mb = round(data["totalKb"] / 1024, 1)
    conclusion = (
        f"知识库现有 {data['documents']} 篇资料（已解析 {data['parsed']} 篇），"
        f"共 {data['chunks']} 个知识切片、{data['categories']} 个分类，"
        f"原始文件合计约 {mb} MB。"
    )
    basis = [f"{x['name']} {x['count']} 篇" for x in data["byCategory"][:8]]
    action = {
        "tool": "count_files",
        "label": call.get("label") or "查库统计",
        "ok": True,
        "costMs": call.get("costMs", 0),
        "detail": data,
    }
    result = _direct_result(question, mode, now, "stats", conclusion)
    result["answer"]["basis"] = basis
    result["answer"]["notice"] = "直接读取知识库数据，未检索文档内容。"
    overview = (
        f"**知识库概况**\n\n"
        f"- 资料 {data['documents']} 篇，其中已解析 {data['parsed']} 篇\n"
        f"- 知识切片 {data['chunks']} 个，分类 {data['categories']} 个\n"
        f"- 原始文件合计约 {mb} MB\n\n"
        "**分类分布**\n\n"
        + "\n".join(f"- {x['name']}：{x['count']} 篇" for x in data["byCategory"][:8])
    )
    result["answer"]["markdown"] = overview
    result["actions"] = [action]
    return result


def _retry_query(question: str, prev: str) -> str | None:
    """检索薄弱时，换一个更可能命中书面表述的问法。无 LLM 返回 None。"""
    if not _has_llm():
        return None
    sys = (
        f"之前用「{prev}」检索没有找到相关资料。请换一个更具体、更可能命中知识库书面表述的问法"
        f"（可拆解子问题或更换关键词），只输出改写后的问题，不要解释。"
    )
    out = _llm_text([
        {"role": "system", "content": sys},
        {"role": "user", "content": f"原问题：{question}"},
    ])
    return out.strip() if out else None


# ---------------------------------------------------------------- 检索 + 融合

def _retrieve(queries: list[str], mode: str, candidate_k: int = 30) -> list[dict]:
    """对多个查询分别检索，按 RRF（Reciprocal Rank Fusion）融合去重。"""
    seen: dict[str, dict] = {}
    rrf: dict[str, float] = {}
    for q in queries:
        for rank, h in enumerate(search_hits(q, mode, limit=candidate_k), 1):
            cid = h["chunkId"]
            rrf[cid] = rrf.get(cid, 0.0) + 1.0 / (rank + 60)
            if cid not in seen:
                seen[cid] = h
    merged = list(seen.values())
    merged.sort(key=lambda h: (-rrf[h["chunkId"]], h["docId"], h["chunkId"]))
    return merged


# ---------------------------------------------------------------- 重排序

def _rerank(question: str, candidates: list[dict], top_k: int = 6) -> list[dict]:
    if not candidates:
        return []
    if not _has_llm():
        return candidates[:top_k]
    items = []
    for i, h in enumerate(candidates[:30], 1):
        snippet = re.sub(r"\s+", " ", h["text"]).strip()[:400]
        items.append(f"[{i}] 《{h['docTitle']}》第 {h['page']} 页：{snippet}")
    sys = (
        "你是 RAG 重排序器。给定用户问题和若干候选资料片段，挑选最相关、最能支撑回答的前 N 个，"
        "按相关性从高到低输出其编号列表的 JSON：{\"ranked\": [编号, ...]}。只输出 JSON。"
    )
    usr = f"用户问题：{question}\n\n候选片段：\n" + "\n".join(items)
    out = _llm_json([
        {"role": "system", "content": sys},
        {"role": "user", "content": usr},
    ])
    if not out:
        return candidates[:top_k]
    ranked = [int(x) for x in (out.get("ranked") or []) if str(x).isdigit()]
    picked: list[dict] = []
    seen_ids: set[str] = set()
    for idx in ranked:
        if 1 <= idx <= len(items):
            h = candidates[idx - 1]
            if h["chunkId"] not in seen_ids:
                seen_ids.add(h["chunkId"])
                picked.append(h)
        if len(picked) >= top_k:
            break
    # LLM 返回不足时用词法序补齐
    for h in candidates[:top_k]:
        if h["chunkId"] not in seen_ids and len(picked) < top_k:
            seen_ids.add(h["chunkId"])
            picked.append(h)
    return picked


# ---------------------------------------------------------------- 上下文处理

def _dedup(chunks: list[dict]) -> list[dict]:
    """基于归一化文本重叠的近似去重（无 embedding 时的平替）。"""
    kept: list[dict] = []
    for h in chunks:
        t = re.sub(r"\s+", "", h["text"]).lower()
        if not t:
            continue
        dup = False
        for k in kept:
            kt = re.sub(r"\s+", "", k["text"]).lower()
            if t == kt or (len(t) > 40 and t in kt) or (len(kt) > 40 and kt in t):
                dup = True
                break
        if not dup:
            kept.append(h)
    return kept


def _reorder_for_attention(chunks: list[dict]) -> list[dict]:
    """缓解 Lost-in-the-Middle：最相关的放两端（奇偶交错后反转一半）。"""
    if len(chunks) <= 2:
        return chunks
    left, right = [], []
    for i, c in enumerate(chunks):
        (left if i % 2 == 0 else right).append(c)
    return left + right[::-1]


# ---------------------------------------------------------------- 生成

def _generate(question: str, chunks: list[dict]) -> dict | None:
    if not _has_llm():
        return None
    def _flat(text: str) -> str:
        return " ".join(text.split())[:600]

    context = "\n\n".join(
        f"[{i + 1}] 《{h['docTitle']}》第 {h['page']} 页 · {h['section']}\n"
        f"{_flat(h['text'])}"
        for i, h in enumerate(chunks)
    )
    sys = (
        "你是一个只依据给定资料回答的助手。只能使用下面提供的片段，不得编造。"
        "输出严格 JSON：{\"conclusion\": \"一段结论\", \"basis\": [\"依据1\", \"依据2\"]}。"
        "每条依据必须能在片段中找到，并在句末标注 [编号]。不要输出 JSON 以外的任何内容。"
        "若资料不足以回答，conclusion 写明「根据现有资料，我无法回答该问题」，basis 为空数组。"
    )
    out = _llm_json([
        {"role": "system", "content": sys},
        {"role": "user", "content": f"资料片段：\n{context}\n\n问题：{question}"},
    ], temperature=0.2)
    if not out:
        return None
    conclusion = str(out.get("conclusion", "")).strip()
    basis = [str(x).strip() for x in (out.get("basis") or []) if str(x).strip()]
    if not conclusion:
        return None
    _, _, model, _, _ = _llm_settings()
    return {
        "conclusion": conclusion,
        "basis": basis,
        "notice": f"AI 生成（{model}），内容仅供参考；请点击引用核对原文。",
        "markdown": build_markdown(conclusion, basis),
    }


# ---------------------------------------------------------------- 编排

def answer_question(question: str, mode: str) -> dict:
    question = (question or "").strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 0) 意图识别与路由（文档：意图识别位于 Query 改写之前）
    rule_reply = _rule_route(question)
    if rule_reply is not None:
        return _direct_result(question, mode, now, "chitchat", rule_reply)
    # 规则层先拦库内统计：零 LLM 调用、直接查库给真实数字
    if _is_stats_question(question):
        return _stats_result(question, mode, now)
    ud = _understand(question)  # 有 LLM 时始终执行，用于意图分类
    route = ud.get("route", "knowledge")
    if route == "chitchat":
        return _direct_result(question, mode, now, "chitchat",
                              ud.get("reply") or "你好，有什么想查的资料？")
    if route == "stats":
        return _stats_result(question, mode, now)
    if route == "clarify":
        return _direct_result(question, mode, now, "clarify",
                              _clarify(question, ud.get("reply", "")))

    keyword_only = mode == "关键词"
    use_llm = _has_llm() and not keyword_only

    # 1) 查询理解结果 → 多路检索查询集
    queries = [question, ud["rewritten"], *ud["subQueries"]] if use_llm else [question]
    queries = [q for q in dict.fromkeys(queries) if q]  # 去重保序

    # 2) 多路检索 + RRF 融合
    candidates = _retrieve(queries, mode, candidate_k=30)
    retrieval_count = len(candidates)

    # 3) LLM 重排
    reranked = _rerank(question, candidates, top_k=6)

    # 4) Agentic 自检：候选薄弱则换角度改写重试一次
    retried = False
    if use_llm and len(reranked) < 2:
        retry_q = _retry_query(question, ud.get("rewritten", question))
        if retry_q and retry_q not in queries:
            reranked2 = _rerank(question, _retrieve([question, retry_q], mode, candidate_k=30), top_k=6)
            if len(reranked2) > len(reranked):
                reranked = reranked2
                retried = True
                queries = queries + [retry_q]

    # 5) 上下文处理：去重 + 重排（防 Lost-in-the-Middle）
    ctx = _reorder_for_attention(_dedup(reranked))[:5]

    # 6) 生成（LLM 优先，失败/缺失则抽取式兜底）
    gen = _generate(question, ctx) if use_llm else None
    if gen:
        answer = gen
        citations = _citations(ctx)
    else:
        extracted = _extractive(question, candidates, mode)
        answer = extracted["answer"]
        citations = extracted["citations"]

    return {
        "question": question,
        "answer": answer,
        "citations": citations,
        "createdAt": now,
        "recallMode": mode,
        "route": "knowledge",
        "actions": [],
        "rewrittenQuery": ud["rewritten"],
        "subQueries": ud["subQueries"],
        "usedLLM": use_llm,
        "retrievalCount": retrieval_count,
        "retried": retried,
        "feedback": None,
    }
