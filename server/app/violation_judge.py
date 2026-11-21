"""现象违规判定辅助：历史定性匹配 → 法条制度反查 → 结构化提示。

本模块只做检索与提示，不构成违规定性；是否违规及适用条款须由审计人员复核确认。
数据隔离：历史问题来自 AuditIssue 表 / audit_issues 向量集合，制度原文来自制度库。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("knowledge.violation_judge")

NOTICE = (
    "助手仅依据历史审计问题与制度库做相似性提示，不构成违规定性；"
    "是否违规及适用条款须由审计人员复核确认。"
)

MAX_REFERENCES = 6
MAX_REGULATIONS = 10


def _basis_query(item: dict) -> str:
    """从历史条目提取适合检索制度条款的查询串。

    四级分类是「现象 → 制度条款」的桥梁，优先使用；法条字段常含整段原文，
    截断到首个句读，避免长文本把向量查询稀释。
    """
    cat = (item.get("catL4") or item.get("catL3") or "").strip()
    basis = (item.get("legalBasis") or "").strip()
    basis = re.split(r"[。；;\n]", basis, maxsplit=1)[0].strip()
    parts = [p for p in (cat, basis[:80]) if p]
    return " ".join(parts)


def judge_violation(phenomenon: str, limit: int = 6) -> dict:
    """按「现象描述」返回历史定性参考与制度条款依据。

    Args:
        phenomenon: 用户的现象描述问句，如「差旅费报销超标准是否违规」。
        limit: 最多返回的历史相似条目数，调用方裁剪到 1~10。

    Returns:
        dict: status / references（历史问题）/ hits（制度切片引用）/ summary / notice。
              hits 结构与 search_docs 一致，可直接进入 Agent 的引用池。
    """
    question = (phenomenon or "").strip()
    if not question:
        return {"phenomenon": "", "status": "empty", "references": [], "hits": [],
                "total": 0, "summary": "", "notice": NOTICE}

    limit = max(1, min(int(limit or 6), 10))

    # 第一段：历史审计问题定性匹配
    try:
        from .audit_issues import search as search_issues
        hist = search_issues(question, limit=limit)
    except Exception as exc:
        log.warning("历史问题检索失败：%s", exc)
        hist = {"items": [], "total": 0}

    references: list[dict] = []
    for item in hist.get("items", []):
        references.append({
            "issueId": item.get("id"),
            "sourceYear": item.get("sourceYear"),
            "sourceFile": item.get("sourceFile"),
            "auditSubject": item.get("auditSubject"),
            "auditProject": item.get("auditProject"),
            "category": item.get("category"),
            "catL1": item.get("catL1"),
            "catL2": item.get("catL2"),
            "catL3": item.get("catL3"),
            "catL4": item.get("catL4"),
            "manifestation": item.get("manifestation"),
            "problemDesc": item.get("problemDesc"),
            "legalBasis": item.get("legalBasis"),
            "remediation": item.get("remediation"),
            "score": item.get("score", 0),
        })

    # 第二段：法条 / 制度原文反查
    from .search import search_hits

    # 用户原始现象优先，确保「问差旅费」先命中差旅费制度；
    # 历史条目的分类/法条作为补充，覆盖历史定性引用的其他条款。
    queries: list[str] = [question]
    for item in references[:4]:
        q = _basis_query(item)
        if q and q not in queries:
            queries.append(q)

    hits: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for q in queries:
        try:
            found = search_hits(q, "混合", 8)
        except Exception as exc:
            log.warning("制度条款反查失败：%s", exc)
            found = []
        for h in found:
            key = (h.get("docId", ""), h.get("chunkId", ""))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            hits.append({
                "docId": h.get("docId"),
                "docTitle": h.get("docTitle", ""),
                "chunkId": h.get("chunkId"),
                "page": h.get("page"),
                "section": h.get("section"),
                "snippet": h.get("snippet", ""),
                "quote": re.sub(r"\s+", " ", h.get("text") or "").strip()[:120],
                "score": h.get("score", 0),
            })
            if len(hits) >= MAX_REGULATIONS:
                break
        if len(hits) >= MAX_REGULATIONS:
            break

    # 第三段：结构化汇总（提示式，不做定性）
    if references:
        top = references[0]
        summary = (
            f"检索到 {len(references)} 条相似历史审计问题；最接近的一类为"
            f"「{top.get('catL4') or top.get('category') or '未分类'}」，"
            f"历史定性依据为：{(top.get('legalBasis') or '未记录')[:120]}。"
            f"同时反查到 {len(hits)} 处制度条款可供核对。"
        )
    else:
        summary = (
            f"未在历史审计问题库中检索到与「{question[:40]}」高度相似的记录；"
            f"仅从制度库反查到 {len(hits)} 处可能相关条款，供人工判断。"
        )

    return {
        "phenomenon": question,
        "status": "matched" if references else "no_reference",
        "references": references[:MAX_REFERENCES],
        "hits": hits,
        "total": len(hits),
        "summary": summary,
        "notice": NOTICE,
    }
