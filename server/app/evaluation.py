"""检索评测（P4）。

参考知识库内「RAG系统评测」「构建 RAG 的评估测试集」「67%→92%」三篇：
- 评估集：从语料切片抽样，让 LLM 生成「只能由该切片回答」的问题，标注正确 chunk_id
- 指标：Hit Rate@K（top-K 是否命中正确 chunk）、MRR（首个正确结果排名倒数的均值）
- 保留 misses 明细与历史快照，用于对比优化前后是否退化

服务层不依赖 HTTP 类型；所有输入在控制器边界校验后才传入。
"""
from __future__ import annotations

import json
import os
import random
import re
import time
import uuid

import httpx

from .db import (
    Chunk,
    Document,
    EvalQuery,
    EvalRun,
    SessionLocal,
    get_raw_settings,
    now_str,
)
from .search import search_hits
from .llm import llm_config, post_chat


def _llm(messages: list[dict], timeout: int = 45) -> str | None:
    cfg = llm_config()
    if not cfg.ready:
        return None
    try:
        data = post_chat(
            {"model": cfg.model, "messages": messages,
             "temperature": 0.4, "max_tokens": 1200},
            timeout=timeout, config=cfg,
        )
        return data["choices"][0]["message"].get("content") or ""
    except Exception:
        return None


def has_llm() -> bool:
    return llm_config().ready


def _parse_items(raw: str) -> list[dict]:
    text = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.M).strip()
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except Exception:
            return []
    items = data.get("items") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


def _sample(count: int, category: str) -> list[tuple[Chunk, Document]]:
    with SessionLocal() as s:
        q = s.query(Chunk, Document).join(Document, Chunk.doc_id == Document.id)
        if category:
            q = q.filter(Document.category == category)
        rows = q.all()
    candidates = [(c, d) for c, d in rows if 80 <= len(c.text or "") <= 2000
                  and (d.status == "已解析")]
    random.shuffle(candidates)
    # 优先覆盖不同资料，避免同一篇占满评估集
    picked: list[tuple[Chunk, Document]] = []
    used_docs: set[str] = set()
    for pair in candidates:
        if pair[0].doc_id not in used_docs:
            picked.append(pair)
            used_docs.add(pair[0].doc_id)
        if len(picked) >= count:
            break
    for pair in candidates:
        if len(picked) >= count:
            break
        if pair not in picked:
            picked.append(pair)
    return picked[:count]


def build_eval_set(count: int = 8, category: str = "") -> dict:
    """抽样切片并生成评估问题；同一批最多 5 个切片，减少 LLM 调用次数。"""
    if not has_llm():
        return {"ok": False, "message": "未配置大模型，无法生成评估问题"}
    samples = _sample(max(1, min(count, 30)), category.strip())
    if not samples:
        return {"ok": False, "message": "没有可用的资料切片"}
    system = (
        "你是 RAG 评估集构建器。我会给若干编号的资料片段，请为每个片段生成一条"
        "「只能依据该片段回答」的自然语言问题。\n"
        "要求：问题要像真实用户提问，不要出现片段编号或文档标题，不要过于宽泛；"
        "顺带标注难度 easy（片段直接给出答案）或 hard（需要综合片段多处信息）。\n"
        "输出严格 JSON：{\"items\": [{\"index\": 1, \"question\": \"...\", \"difficulty\": \"easy\"}]}。"
        "不要输出 JSON 以外的内容。"
    )
    created = 0
    for start in range(0, len(samples), 5):
        batch = samples[start:start + 5]
        context = "\n\n".join(
            f"[{i + 1}] {c.text[:600]}" for i, (c, _) in enumerate(batch)
        )
        raw = _llm([
            {"role": "system", "content": system},
            {"role": "user", "content": f"资料片段：\n{context}"},
        ])
        items = _parse_items(raw or "")
        with SessionLocal() as s:
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    idx = int(item.get("index"))
                except (TypeError, ValueError):
                    continue
                if not (1 <= idx <= len(batch)):
                    continue
                question = str(item.get("question") or "").strip()
                if len(question) < 6:
                    continue
                chunk, doc = batch[idx - 1]
                s.add(EvalQuery(
                    id=f"eq-{uuid.uuid4().hex[:10]}", question=question[:200],
                    chunk_id=chunk.id, doc_id=doc.id, doc_title=doc.title,
                    category=doc.category or "", difficulty=str(item.get("difficulty") or "easy"),
                    created_at=now_str(),
                ))
                created += 1
            s.commit()
    return {"ok": True, "created": created, "total": total_queries()}


def total_queries() -> int:
    with SessionLocal() as s:
        return s.query(EvalQuery).count()


def list_queries(limit: int = 100) -> list[dict]:
    with SessionLocal() as s:
        rows = s.query(EvalQuery).order_by(EvalQuery.created_at.desc()).limit(limit).all()
        return [{
            "id": r.id, "question": r.question, "chunkId": r.chunk_id,
            "docId": r.doc_id, "docTitle": r.doc_title, "category": r.category,
            "difficulty": r.difficulty, "createdAt": r.created_at,
        } for r in rows]


def clear_queries() -> int:
    with SessionLocal() as s:
        removed = s.query(EvalQuery).delete()
        s.commit()
        return removed


def run_eval(k: int = 5, mode: str = "混合") -> dict:
    """对评估集跑词法检索，计算 Hit Rate@K 与 MRR，保存一次指标快照。"""
    with SessionLocal() as s:
        queries = s.query(EvalQuery).all()
        snapshot = [(r.id, r.question, r.chunk_id, r.doc_title) for r in queries]
    if not snapshot:
        return {"ok": False, "message": "评估集为空，请先生成评估集"}
    k = max(1, min(k, 20))
    started = time.time()
    hits, rr_sum, misses = 0, 0.0, []
    for qid, question, chunk_id, doc_title in snapshot:
        results = search_hits(question, mode, limit=max(k, 10))
        ids = [h["chunkId"] for h in results]
        if chunk_id in ids[:k]:
            hits += 1
            rank = ids.index(chunk_id) + 1
            rr_sum += 1.0 / rank
        elif len(misses) < 10:
            misses.append({"question": question, "expectedDoc": doc_title,
                           "topChunk": results[0]["docTitle"] if results else "无结果"})
    total = len(snapshot)
    hit_rate = hits / total
    mrr = rr_sum / total
    cost_ms = int((time.time() - started) * 1000)
    run_id = f"ev-{uuid.uuid4().hex[:10]}"
    with SessionLocal() as s:
        s.add(EvalRun(
            id=run_id, created_at=now_str(), total=total, k=k, mode=mode,
            hit_rate=round(hit_rate * 10000), mrr=round(mrr * 10000),
            cost_ms=cost_ms, misses_json=misses,
        ))
        s.commit()
    return {
        "ok": True, "runId": run_id, "total": total, "k": k, "mode": mode,
        "hitRate": round(hit_rate, 4), "mrr": round(mrr, 4),
        "costMs": cost_ms, "misses": misses, "createdAt": now_str(),
    }


def list_runs(limit: int = 20) -> list[dict]:
    with SessionLocal() as s:
        rows = s.query(EvalRun).order_by(EvalRun.created_at.desc(), EvalRun.id.desc()).limit(limit).all()
        return [{
            "id": r.id, "createdAt": r.created_at, "total": r.total, "k": r.k,
            "mode": r.mode, "hitRate": round((r.hit_rate or 0) / 10000, 4),
            "mrr": round((r.mrr or 0) / 10000, 4), "costMs": r.cost_ms,
            "misses": r.misses_json or [],
        } for r in rows]
