"""本地检索与抽取式兜底。

检索用中文 bigram + 标题/标签/关键词扩展（字段加权），作为混合检索的词法腿；
优化版带推理的问答管线见 app/qa.py。
"""
from __future__ import annotations

import re
from datetime import datetime

from .db import Category, Chunk, Document, SessionLocal


def build_terms(q: str) -> list[str]:
    base = [w.lower() for w in re.split(r"[\s,，、；;]+", q.strip()) if w]
    terms = set(base)
    for word in base:
        if len(word) >= 3 and re.search(r"[\u4e00-\u9fa5]", word):
            for i in range(len(word) - 1):
                terms.add(word[i:i + 2])
    return [t for t in terms if t]


def first_sentence(text: str, limit: int = 110) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    m = re.search(r"[。！？；]", text)
    return (text[: m.end()] if m and m.end() <= limit else text[:limit]).strip()


def snippet_of(text: str, terms: list[str]) -> str:
    lower = text.lower()
    positions = [lower.find(t) for t in terms if lower.find(t) >= 0]
    at = min(positions) if positions else 0
    start = max(0, at - 30)
    piece = text[start:start + 180]
    return ("…" if start > 0 else "") + piece


def search_hits(q: str, mode: str = "混合", limit: int = 20) -> list[dict]:
    """知识库检索入口：优先 Milvus 向量检索，不可用时降级到词法检索。

    mode="关键词" 保持原有词法行为；其余模式先走向量库，向量库没有可信命中
    才回退词法（包括扫描件按标题定位的兜底）。
    """
    if mode != "关键词":
        try:
            from .milvus_store import search as vector_search
            hits = vector_search(q, limit)
            if hits:
                return hits
        except Exception:
            pass
    return _lexical_search(q, mode, limit)


def _lexical_search(q: str, mode: str = "混合", limit: int = 20) -> list[dict]:
    """字段加权的词法检索：分块文本 / 标题 / 关键词 / 标签分别加权，
    整句短语命中给强信号，命中词项占比（召回度）给小加成。
    作为混合检索的「词法腿」，被 /api/search 与问答管线共用。"""
    terms = build_terms(q)
    if not terms:
        return []
    raw = q.strip().lower()
    keyword_only = mode == "关键词"
    hits: list[dict] = []
    with SessionLocal() as session:
        docs = {d.id: d for d in session.query(Document).all()}
        for chunk in session.query(Chunk).all():
            doc = docs.get(chunk.doc_id)
            if not doc or doc.status != "已解析":
                continue
            text = chunk.text or ""
            lower = text.lower()
            title_lower = doc.title.lower()
            matched = 0
            score = 0
            for term in terms:
                in_text = term in lower
                in_title = term in title_lower
                in_kw = (not keyword_only) and any(term in k.lower() for k in (doc.keywords or []))
                in_tag = (not keyword_only) and any(term in t.lower() for t in (doc.tags or []))
                if in_text:
                    matched += 1
                    score += 10
                if in_title:
                    score += 8
                if in_kw:
                    score += 5
                if in_tag:
                    score += 3
            # 整句短语命中：强信号（文档里整段复述了用户问句）
            if raw and raw in lower:
                score += 15
                matched = max(matched, 1)
            # 召回度加成：命中的不同词项越多越相关
            if matched:
                score += matched * 2
            if score:
                hits.append({
                    "docId": doc.id,
                    "docTitle": doc.title,
                    "chunkId": chunk.id,
                    "page": chunk.page,
                    "section": chunk.section,
                    "snippet": snippet_of(text, terms),
                    "score": score,
                    "format": doc.format,
                    "text": text,
                })
    # 文档级兜底：对「没有任何可检索切片」的已解析文档（典型为无文字层的扫描件），
    # 仍按标题/分类/关键词/标签参与匹配，使按文件名或标题即可定位到它，避免「库里有却搜不到」。
    hit_doc_ids = {h["docId"] for h in hits}
    for doc in docs.values():
        if doc.status != "已解析" or doc.id in hit_doc_ids:
            continue
        title_lower = doc.title.lower()
        score = 0
        matched = 0
        for term in terms:
            in_title = term in title_lower
            in_cat = term in (doc.category or "").lower()
            in_kw = any(term in k.lower() for k in (doc.keywords or []))
            in_tag = any(term in t.lower() for t in (doc.tags or []))
            if in_title:
                score += 8
            if in_cat:
                score += 4
            if in_kw:
                score += 5
            if in_tag:
                score += 3
            if in_title or in_cat or in_kw or in_tag:
                matched += 1
        if raw and raw in title_lower:
            score += 15
            matched = max(matched, 1)
        if matched:
            score += matched * 2
        if score:
            note = f"{doc.title}（扫描件/无文字层，可在「原始文件」中预览）"
            hits.append({
                "docId": doc.id,
                "docTitle": doc.title,
                "chunkId": "",
                "page": 0,
                "section": "扫描件/无文字层",
                "snippet": doc.title,
                "score": score,
                "format": doc.format,
                "text": note,
            })
    hits.sort(key=lambda h: (-h["score"], h["docId"], h["chunkId"]))
    return hits[:limit]


def _citations(top: list[dict]) -> list[dict]:
    return [{
        "docId": h["docId"],
        "docTitle": h["docTitle"],
        "chunkId": h["chunkId"],
        "page": h["page"],
        "section": h["section"],
        "quote": re.sub(r"\s+", " ", h["text"]).strip()[:120],
    } for h in top]


def _extractive(question: str, hits: list[dict], mode: str) -> dict:
    notice = "AI 生成，内容仅供参考；请点击引用核对原文。"
    if not hits:
        return {
            "question": question,
            "answer": {
                "conclusion": "当前知识库里没有检索到能回答这个问题的资料。",
                "basis": ["可以换个说法，或先把相关资料导入资料库。"],
                "notice": notice,
            },
            "citations": [],
            "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "recallMode": mode,
            "feedback": None,
        }
    top = hits[:3]
    return {
        "question": question,
        "answer": {
            "conclusion": f"知识库中有 {len(hits)} 处相关片段，主要来自《{top[0]['docTitle']}》等资料。综合来看：{first_sentence(top[0]['text'])}",
            "basis": [f"《{h['docTitle']}》第 {h['page']} 页：{first_sentence(h['text'])}" for h in top],
            "notice": notice,
        },
        "citations": _citations(top),
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "recallMode": mode,
        "feedback": None,
    }


# ---------------------------------------------------------------- 制度全量召回（文档级）

RECALL_NOTICE = "助手仅召回制度清单供参考，制度适用与定性须由审计人员复核。"


def recall_regulations(query: str, limit: int = 20) -> dict:
    """文档级制度召回：用户想列举/盘点某类制度时，返回完整制度清单而非碎片切片。

    候选集限定为制度库已解析文档（历史审计问题存于 AuditIssue 表，天然不在其中）。
    分类树命中给强加分；同时并入向量命中与词法命中，阈值从宽，宁可多召回。
    返回项的 hits 结构与 search_hits 一致，可直接进入 Agent 的引用池。

    Args:
        query: 用户的制度主题问句。
        limit: 最多返回篇数，调用方需裁剪到 1~50。

    Returns:
        {'query','total','items','hits','limitNote','notice'}。
    """
    question = (query or "").strip()
    if not question:
        return {"query": question, "total": 0, "items": [], "hits": [],
            "limitNote": "", "notice": RECALL_NOTICE}
    cap = max(1, min(int(limit or 20), 50))
    terms = build_terms(question)
    raw = question.lower()

    # 1) 向量腿：取较宽的候选，按 docId 聚合
    vector_hits: list[dict] = []
    try:
        from .milvus_store import search as vector_search
        vector_hits = vector_search(question, 50)
    except Exception:
        vector_hits = []
    vector_by_doc: dict[str, dict] = {}
    for hit in vector_hits:
        vector_by_doc.setdefault(hit["docId"], hit)

    with SessionLocal() as session:
        # 2) 候选文档：只取制度库已解析文档；防御性排除来源标注为历史问题的记录
        docs = [d for d in session.query(Document).all()
                if d.status == "已解析" and "历史问题" not in (d.source or "")]
        if not docs:
            return {"query": question, "total": 0, "items": [], "hits": [],
                    "limitNote": "制度库暂无已解析资料。", "notice": RECALL_NOTICE}

        # 3) 分类树：问题词命中分类名时，把该分类及其所有子孙纳入优先分类集合
        cats = session.query(Category).all()
        children_of: dict[str, list[Category]] = {}
        for cat in cats:
            children_of.setdefault(cat.parent_id or "", []).append(cat)
        meaningful = [t for t in terms if len(t) >= 2]
        cat_names: set[str] = set()
        for cat in cats:
            name = cat.name or ""
            if any(term in name.lower() for term in meaningful):
                cat_names.add(name)
        stack = [cat.id for cat in cats if cat.name in cat_names]
        while stack:
            cid = stack.pop()
            for child in children_of.get(cid, []):
                if child.name not in cat_names:
                    cat_names.add(child.name)
                stack.append(child.id)

        # 4) 每篇文档的前若干切片，供打分与命中片段使用
        excerpts: dict[str, str] = {}
        lead_chunk: dict[str, Any] = {}
        for chunk in session.query(Chunk).order_by(Chunk.id).all():
            bucket = lead_chunk.setdefault(chunk.doc_id, [])
            if len(bucket) < 3:
                bucket.append(chunk)

    for doc in docs:
        chunks = lead_chunk.get(doc.id, [])
        text = " ".join((c.text or "")[:600] for c in chunks)
        excerpts[doc.id] = text

    scored: list[dict] = []
    for doc in docs:
        title = (doc.title or "").lower()
        category = doc.category or ""
        category_lower = category.lower()
        keyword_text = " ".join(str(k) for k in (doc.keywords or [])).lower()
        tag_text = " ".join(str(t) for t in (doc.tags or [])).lower()
        summary = (doc.summary or "").lower()
        excerpt = (excerpts.get(doc.id) or "").lower()
        score = 0.0
        title_hits: list[str] = []
        for term in terms:
            if term in title:
                score += 8
                title_hits.append(term)
            if term in category_lower:
                score += 4
            if term in keyword_text:
                score += 5
            if term in tag_text:
                score += 3
            if term in summary:
                score += 3
            if term in excerpt:
                score += 6
        if category in cat_names:
            score += 12  # 分类树命中是强信号
        if raw and (raw in title or raw in excerpt):
            score += 15
        vector_hit = vector_by_doc.get(doc.id)
        if vector_hit:
            score += max(10.0, float(vector_hit.get("score") or 0) * 0.3)

        if score < 8:  # 阈值从宽：单个标题 bigram 命中即可入选
            continue

        chunk = None
        for candidate in lead_chunk.get(doc.id, []):
            if any(term in (candidate.text or "").lower() for term in terms):
                chunk = candidate
                break
        if chunk is None:
            chunk = lead_chunk.get(doc.id, [None])[0] if lead_chunk.get(doc.id) else None
        snippet = (vector_hit or {}).get("snippet") or snippet_of(excerpts.get(doc.id) or "", terms)
        quote_text = re.sub(r"\s+", " ", (chunk.text if chunk else "") or snippet).strip()[:120]

        reasons: list[str] = []
        if category in cat_names:
            reasons.append(f"分类命中「{category}」")
        if title_hits:
            reasons.append("标题含「" + "、".join(sorted(set(title_hits))[:3]) + "」")
        if vector_hit:
            reasons.append("语义相关")
        if not reasons:
            reasons.append("正文或摘要命中关键词")

        scored.append({
            "docId": doc.id,
            "docTitle": doc.title,
            "title": doc.title,
            "category": category,
            "score": round(score, 1),
            "reason": "；".join(reasons),
            "chunkId": chunk.id if chunk else "",
            "page": chunk.page if chunk else 0,
            "section": chunk.section if chunk else "摘要",
            "snippet": snippet,
            "quote": quote_text,
        })

    scored.sort(key=lambda item: (-item["score"], item["docId"]))
    items = scored[:cap]
    note = f"最多返回 {cap} 篇制度（上限 50）。"
    if len(scored) > cap:
        note += f"另有 {len(scored) - cap} 篇相关制度未列出，可缩小主题后再次召回。"
    return {
        "query": question,
        "total": len(items),
        "matched": len(scored),
        "items": items,
        "hits": items,  # 与 search_docs 对齐，供 Agent 引用池直接消费
        "limitNote": note,
        "notice": RECALL_NOTICE,
    }
