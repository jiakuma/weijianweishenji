"""本地检索与抽取式兜底。

检索用中文 bigram + 标题/标签/关键词扩展（字段加权），作为混合检索的词法腿；
优化版带推理的问答管线见 app/qa.py。
"""
from __future__ import annotations

import re
from datetime import datetime

from .db import Chunk, Document, SessionLocal


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
