# -*- coding: utf-8 -*-
"""历史审计问题定性库：xlsx 解析、SQLite 入库、独立向量库与检索。

定性口径：本模块只做检索与提示，不构成违规定性，结论须由审计人员复核。
数据隔离：记录写入 AuditIssue 表，向量写入 Milvus 的 audit_issues collection，
与制度库 Document/Chunk 及 knowledge_chunks 物理隔离。
Milvus Lite 单进程独占文件锁，导入只能由运行中的后端进程内触发（见 main.py 的 API），
禁止外部脚本直连向量库。
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel
from sqlalchemy import or_

from . import milvus_store
from .db import AuditIssue, SessionLocal, now_str
from .search import build_terms, snippet_of

log = logging.getLogger("knowledge.audit_issues")

NOTICE = "助手仅提供检索与提示，不构成违规定性；定性结论须由审计人员复核。"
BATCH_SIZE = 50  # 写库 / 写向量分批大小
_AUDIT_TEXT_MAX = 8000  # 与 milvus_store.VARCHAR 上限保持一致


# ---------------------------------------------------------------- xlsx 解析

def _norm_header(value: Any) -> str:
    """表头规范化：去掉单元格里的换行与空格，统一全角括号，便于按关键词定位列。"""
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", "", text)
    return text.replace("（", "(").replace("）", ")")


def _clean(value: Any) -> str:
    """单元格转干净文本：None 转空串，日期转 ISO，连续空白压成单个空格。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return re.sub(r"[\u3000\s]+", " ", str(value)).strip()


def _deadline(value: Any) -> str:
    """整改时限兼容三种形态：日期对象、Excel 日期序列号（如 46477）、日期文本。"""
    if value is None or value == "":
        return ""
    if isinstance(value, (datetime, date)):
        return _clean(value)
    text = str(value).strip()
    number: float | None = None
    try:
        number = float(text)
    except (TypeError, ValueError):
        number = None  # 非数字文本，按原样返回
    # 20000~80000 覆盖 1954~2119 年，避免把普通数字误当日期
    if number is not None and 20000 <= number <= 80000:
        return from_excel(number).strftime("%Y-%m-%d")
    return _clean(value)


def _year_from_name(name: str) -> int:
    match = re.search(r"(20\d{2})", name or "")
    return int(match.group(1)) if match else 0


def _cell(row: tuple, index: int | None) -> Any:
    """安全取单元格：列不存在或行短于表头时返回空。"""
    if index is None or index < 0 or index >= len(row):
        return None
    return row[index]


def _find_col(headers: list[str], *keywords: str) -> int | None:
    """按关键词定位列号；需要全部关键词同时出现在规范化表头中。0 是合法列号。"""
    for i, header in enumerate(headers):
        if all(word in header for word in keywords):
            return i
    return None


def _build_search_text(item: dict) -> str:
    """拼接可检索文本；四级分类常与三级、表现形式重复，按内容去重后再拼接。"""
    parts = (
        item["cat_l1"], item["cat_l2"], item["cat_l3"], item["cat_l4"],
        item["audit_subject"], item["audit_project"], item["manifestation"],
        item["problem_desc"], item["legal_basis"], item["remediation"],
        item["remediation_status"],
    )
    seen: set[str] = set()
    kept: list[str] = []
    for part in parts:
        text = (part or "").strip()
        if text and text not in seen:
            seen.add(text)
            kept.append(text)
    return "\n".join(kept)[:_AUDIT_TEXT_MAX]


def _issue_id(year: int, file_name: str, seq: str) -> str:
    """稳定 ID：同一文件重复导入时覆盖更新，不产生重复条目。"""
    tag = hashlib.md5(Path(file_name).name.encode("utf-8")).hexdigest()[:6]
    return f"ai-{year}-{tag}-{seq}"


def parse_workbook(path: str | Path, year_hint: int | None = None) -> list[dict]:
    """解析一张问题明细表，返回标准化问题条目列表。

    2024 表与 2025 表前 10 列一致；2024 表第 11 列为「适用法条」，
    2025 表第 11 列为「定性依据」，统一映射到 legal_basis。
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"问题明细表不存在：{file_path}")
    if file_path.suffix.lower() not in (".xlsx", ".xlsm"):
        raise ValueError(f"仅支持 .xlsx 文件：{file_path}")
    year_default = year_hint or _year_from_name(file_path.name)
    book = load_workbook(file_path, read_only=True, data_only=True)
    try:
        sheet = book.active
        rows = sheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            return []
        headers = [_norm_header(v) for v in header]
        cols = {
            "seq": _find_col(headers, "序号"),
            "year": _find_col(headers, "年度"),
            "subject": _find_col(headers, "审计主体"),
            "project": _find_col(headers, "审计项目"),
            "cat_l1": _find_col(headers, "问题分类", "一级"),
            "cat_l2": _find_col(headers, "问题分类", "二级"),
            "cat_l3": _find_col(headers, "问题分类", "三级"),
            "cat_l4": _find_col(headers, "问题分类", "四级"),
            "manifestation": _find_col(headers, "常见问题表现形式"),
            "problem": _find_col(headers, "问题表述"),
            "legal": (_find_col(headers, "适用法条")
                      if _find_col(headers, "适用法条") is not None
                      else _find_col(headers, "定性依据")),
            "remediation": _find_col(headers, "整改要求"),
            "status": _find_col(headers, "整改情况"),
            "rem_type": _find_col(headers, "整改类型"),
            "deadline": _find_col(headers, "整改时限"),
            "disclosure": _find_col(headers, "披露形式"),
            "audit_type": _find_col(headers, "审计类型"),
        }
        if cols["problem"] is None and cols["manifestation"] is None:
            raise ValueError(f"表头无法识别问题列：{file_path.name}")

        items: list[dict] = []
        last_year = ""
        for line_no, row in enumerate(rows, 2):
            if not any(v not in (None, "") for v in row):
                continue
            seq = _clean(_cell(row, cols["seq"]))
            problem_desc = _clean(_cell(row, cols["problem"]))
            if not seq and not problem_desc:
                continue
            # 年度列常只在分组首行填写，后续空行沿用上一个非空值；
            # 全空时回退到文件名里的年度（2024 表即此情形）。
            year_text = _clean(_cell(row, cols["year"])) or last_year
            if year_text:
                last_year = year_text
            year = int(year_text) if year_text.isdigit() else year_default or 0

            item = {
                "source_year": year,
                "source_file": file_path.name,
                "audit_subject": _clean(_cell(row, cols["subject"])),
                "audit_project": _clean(_cell(row, cols["project"])),
                "cat_l1": _clean(_cell(row, cols["cat_l1"])),
                "cat_l2": _clean(_cell(row, cols["cat_l2"])),
                "cat_l3": _clean(_cell(row, cols["cat_l3"])),
                "cat_l4": _clean(_cell(row, cols["cat_l4"])),
                "manifestation": _clean(_cell(row, cols["manifestation"])),
                "problem_desc": problem_desc,
                "legal_basis": _clean(_cell(row, cols["legal"])),
                "remediation": _clean(_cell(row, cols["remediation"])),
                "remediation_status": _clean(_cell(row, cols["status"])),
                "remediation_type": _clean(_cell(row, cols["rem_type"])),
                "remediation_deadline": _deadline(_cell(row, cols["deadline"])),
                "disclosure": _clean(_cell(row, cols["disclosure"])),
                "audit_type": _clean(_cell(row, cols["audit_type"])),
                "created_at": now_str(),
            }
            item["id"] = _issue_id(year, file_path.name, seq or f"r{line_no}")
            item["search_text"] = _build_search_text(item)
            items.append(item)
        return items
    finally:
        book.close()


# ---------------------------------------------------------------- 入库

def import_from_paths(
    paths: list[str],
    replace: bool = True,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """解析并导入多个 xlsx；由后端 API 在进程内调用。

    Args:
        paths: xlsx 绝对路径列表。
        replace: True 时先清空历史问题表与独立 collection，保证重复导入结果一致。
        progress: 可选进度回调，接收 {'phase','done','total'}。

    Returns:
        含解析数、入库数、向量数、失败明细与分文件统计的字典。
    """
    files = [str(p) for p in (paths or []) if str(p).strip()]
    if not files:
        raise ValueError("请提供至少一个 xlsx 绝对路径")

    per_file: list[dict] = []
    all_items: list[dict] = []
    for file_path in files:
        items = parse_workbook(file_path)
        per_file.append({"file": file_path, "parsed": len(items)})
        all_items.extend(items)
    total = len(all_items)
    if progress:
        progress({"phase": "解析完成", "done": 0, "total": total})

    db_cleared = False
    vector_cleared = False
    if replace:
        with SessionLocal() as session:
            session.query(AuditIssue).delete()
            session.commit()
        db_cleared = True
        vector_cleared = milvus_store.reset_audit_collection()

    db_ok = 0
    vector_ok = 0
    failed: list[dict] = []

    with SessionLocal() as session:
        for start in range(0, total, BATCH_SIZE):
            batch = all_items[start:start + BATCH_SIZE]
            try:
                for item in batch:
                    row = session.get(AuditIssue, item["id"])
                    if row:
                        for key, value in item.items():
                            setattr(row, key, value)
                    else:
                        session.add(AuditIssue(**item))
                session.commit()
                db_ok += len(batch)  # 仅在提交成功后计入，避免回滚导致统计虚高
            except Exception as exc:
                session.rollback()
                log.warning("历史问题写库失败（批次 %s）：%s", start, exc)
                failed.append({"phase": "db", "range": f"{start}-{start + len(batch)}",
                               "error": type(exc).__name__})
            if progress:
                progress({"phase": "写库", "done": min(start + BATCH_SIZE, total), "total": total})

    for start in range(0, total, BATCH_SIZE):
        batch = all_items[start:start + BATCH_SIZE]
        rows = [{
            "issue_id": item["id"],
            "source_year": item["source_year"],
            "cat_l4": item["cat_l4"],
            "problem_desc": item["problem_desc"],
            "search_text": item["search_text"],
        } for item in batch]
        if milvus_store.upsert_audit_issues(rows):
            vector_ok += len(batch)
        else:
            failed.append({"phase": "vector", "range": f"{start}-{start + len(batch)}",
                           "error": "向量写入失败，检索将用词法兜底"})
        if progress:
            progress({"phase": "写向量", "done": min(start + BATCH_SIZE, total), "total": total})

    result = {
        "ok": total > 0 and not any(f["phase"] == "db" for f in failed),
        "total": total,
        "parsed": total,
        "dbOk": db_ok,
        "vectorOk": vector_ok,
        "replaced": replace,
        "dbCleared": db_cleared,
        "vectorCleared": vector_cleared,
        "failed": failed,
        "perFile": per_file,
        "notice": NOTICE,
    }
    log.info("历史问题导入完成：%s", {k: result[k] for k in ("total", "dbOk", "vectorOk")})
    return result


def stats() -> dict:
    """入库统计：SQLite 记录数、按年度/主体分布，以及独立 collection 的条目数。"""
    with SessionLocal() as session:
        total = session.query(AuditIssue).count()
        by_year: dict[str, int] = {}
        by_subject: dict[str, int] = {}
        for year, subject in session.query(AuditIssue.source_year, AuditIssue.audit_subject).all():
            ykey = str(year or 0)
            by_year[ykey] = by_year.get(ykey, 0) + 1
            skey = subject or "未标注"
            by_subject[skey] = by_subject.get(skey, 0) + 1
    return {
        "dbTotal": total,
        "vectorTotal": milvus_store.audit_collection_count(),
        "byYear": dict(sorted(by_year.items())),
        "bySubject": dict(sorted(by_subject.items(), key=lambda x: -x[1])),
        "notice": NOTICE,
    }


# ---------------------------------------------------------------- 检索

def _row_to_dict(row: AuditIssue, snippet: str = "", score: float = 0.0) -> dict:
    """ORM 行转结构化条目；分类同时给出扁平字段与可读链，便于工具/前端消费。"""
    cats = [c for c in (row.cat_l1, row.cat_l2, row.cat_l3, row.cat_l4) if c]
    return {
        "id": row.id,
        "issueId": row.id,
        "sourceYear": row.source_year,
        "sourceFile": row.source_file,
        "auditSubject": row.audit_subject,
        "auditProject": row.audit_project,
        "catL1": row.cat_l1,
        "catL2": row.cat_l2,
        "catL3": row.cat_l3,
        "catL4": row.cat_l4,
        "category": " → ".join(cats),
        "manifestation": row.manifestation,
        "problemDesc": row.problem_desc,
        "legalBasis": row.legal_basis,
        "remediation": row.remediation,
        "remediationStatus": row.remediation_status,
        "remediationType": row.remediation_type,
        "remediationDeadline": row.remediation_deadline,
        "disclosure": row.disclosure,
        "auditType": row.audit_type,
        "snippet": snippet,
        "score": round(score, 1),
        "notice": NOTICE,
    }


def list_issues(
    page: int = 1,
    size: int = 20,
    year: str = "",
    category: str = "",
    subject: str = "",
    q: str = "",
) -> dict:
    """历史问题库分页列表：先应用全部筛选条件再统计总数。

    Args:
        page: 1 起的页码，小于 1 时按 1 处理。
        size: 每页条数，裁剪到 1~100。
        year: source_year 精确匹配（非数字年份返回空结果）。
        category: cat_l1~cat_l4 任一字段 LIKE 匹配。
        subject: audit_subject LIKE 匹配。
        q: search_text LIKE 匹配（覆盖分类、表现、问题、依据、整改等字段）。

    Returns:
        dict: {"items": [...], "total": int, "page": int, "size": int}。
    """
    page = max(1, int(page or 1))
    size = max(1, min(int(size or 20), 100))

    with SessionLocal() as session:
        query = session.query(AuditIssue)
        year_text = str(year or "").strip()
        if year_text:
            try:
                year_value = int(year_text)
            except (TypeError, ValueError):
                year_value = -1  # 非法年份不可能命中任何记录
            query = query.filter(AuditIssue.source_year == year_value)
        category_text = str(category or "").strip()
        if category_text:
            pattern = f"%{category_text}%"
            query = query.filter(or_(
                AuditIssue.cat_l1.like(pattern),
                AuditIssue.cat_l2.like(pattern),
                AuditIssue.cat_l3.like(pattern),
                AuditIssue.cat_l4.like(pattern),
            ))
        subject_text = str(subject or "").strip()
        if subject_text:
            query = query.filter(AuditIssue.audit_subject.like(f"%{subject_text}%"))
        keyword = str(q or "").strip()
        if keyword:
            query = query.filter(AuditIssue.search_text.like(f"%{keyword}%"))

        total = query.count()
        rows = (query
                .order_by(AuditIssue.source_year.desc(), AuditIssue.id.asc())
                .offset((page - 1) * size)
                .limit(size)
                .all())
        items = [_row_to_dict(row) for row in rows]
    return {"items": items, "total": total, "page": page, "size": size}


def get_issue(issue_id: str) -> dict | None:
    """按主键取一条历史审计问题；不存在时返回 None。"""
    key = str(issue_id or "").strip()
    if not key:
        return None
    with SessionLocal() as session:
        row = session.get(AuditIssue, key)
        return _row_to_dict(row) if row else None


def _lexical_search(query: str, limit: int) -> list[dict]:
    """词法兜底：中文 bigram + 四级分类加权，历史问题库仅数百条，全表扫描即可。"""
    terms = build_terms(query)
    if not terms:
        return []
    raw = query.strip().lower()
    hits: list[dict] = []
    with SessionLocal() as session:
        for row in session.query(AuditIssue).all():
            text = row.search_text or ""
            lower = text.lower()
            score = 0
            matched = 0
            for term in terms:
                if term in lower:
                    matched += 1
                    score += 10
                if term in (row.cat_l4 or "").lower():
                    score += 6
            if raw and raw in lower:
                score += 15
                matched = max(matched, 1)
            if matched:
                score += matched * 2
            if score:
                hits.append({
                    "issueId": row.id,
                    "score": score,
                    "snippet": snippet_of(text, terms),
                })
    hits.sort(key=lambda h: (-h["score"], h["issueId"]))
    return hits[:limit]


def search(query: str, limit: int = 10) -> dict:
    """历史审计问题检索：向量语义与词法两腿 RRF 融合，返回结构化问题条目。"""
    question = (query or "").strip()
    if not question:
        return {"query": question, "total": 0, "items": [], "notice": NOTICE}
    fetch = max(limit * 2, 20)
    vector_hits = milvus_store.search_audit_issues(question, limit=fetch)
    lexical_hits = _lexical_search(question, fetch)

    # Reciprocal Rank Fusion：两腿名次倒数相加，避免两腿分数量纲不一致
    fused: dict[str, dict] = {}
    for rank, hit in enumerate(vector_hits, 1):
        key = hit["issueId"]
        acc = fused.setdefault(key, {"rrf": 0.0, "snippet": "", "vector": 0.0, "lexical": 0})
        acc["rrf"] += 1.0 / (60 + rank)
        acc["snippet"] = acc["snippet"] or hit.get("snippet", "")
        acc["vector"] = hit.get("score", 0.0)
    for rank, hit in enumerate(lexical_hits, 1):
        key = hit["issueId"]
        acc = fused.setdefault(key, {"rrf": 0.0, "snippet": "", "vector": 0.0, "lexical": 0})
        acc["rrf"] += 1.0 / (60 + rank)
        acc["snippet"] = acc["snippet"] or hit.get("snippet", "")
        acc["lexical"] = hit.get("score", 0)

    items: list[dict] = []
    if fused:
        with SessionLocal() as session:
            rows = {row.id: row for row in session.query(AuditIssue).filter(
                AuditIssue.id.in_(list(fused.keys()))).all()}
        for issue_id, acc in fused.items():
            row = rows.get(issue_id)
            if row:
                items.append(_row_to_dict(row, acc["snippet"], acc["rrf"] * 3000))
    items.sort(key=lambda item: (-item["score"], item["id"]))
    items = items[:limit]
    return {
        "query": question,
        "total": len(items),
        "items": items,
        "vectorUsed": bool(vector_hits),
        "notice": NOTICE,
    }
