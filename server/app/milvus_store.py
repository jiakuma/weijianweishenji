"""Milvus 向量库：知识库切片的向量化存储与语义检索。

默认用 Milvus Lite（嵌入式，数据文件 server/data/milvus.db，无需另外起服务）；
设置环境变量 MILVUS_URI 可切换到独立部署的 Milvus 服务。
向量复用 bge-small-zh-v1.5；Milvus 或模型不可用时，检索方自动降级到词法检索。
"""
from __future__ import annotations

import threading
from pathlib import Path

from .config import settings
from .db import Chunk, Document, SessionLocal

COLLECTION = "knowledge_chunks"
MILVUS_URI = settings.MILVUS_URI

_client = None
_lock = threading.Lock()
_failed = False
_dim = 512
_dim_checked = False


def _mark_failed() -> None:
    """熔断向量库：数据目录或索引损坏时，避免每条请求重复触发底层异常。"""
    global _client, _failed
    _failed = True
    stale, _client = _client, None
    if stale is not None:
        try:
            stale.close()
        except Exception:
            pass


def _client_or_none():
    global _client, _failed
    if _client is not None:
        return _client
    if _failed:
        return None
    with _lock:
        if _client is not None:
            return _client
        if _failed:
            return None
        client = None
        try:
            from pymilvus import MilvusClient
            Path(MILVUS_URI).parent.mkdir(parents=True, exist_ok=True)
            client = MilvusClient(uri=MILVUS_URI)
            # 健康探活：已存在的 collection 在 load 时会校验索引文件。
            # Milvus Lite 的 HNSW 索引由 faiss 写入，Windows 中文路径下 faiss
            # 用窄字符 fopen 无法写入，索引文件缺失会在这一步抛 MilvusException。
            # 就地熔断，避免异常散落到导入、检索、后台同步各条链路。
            if client.has_collection(COLLECTION):
                client.load_collection(COLLECTION)
            _client = client
        except Exception:
            _failed = True
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            return None
    return _client


def _vector_dim() -> int:
    global _dim, _dim_checked
    if _dim_checked:
        return _dim
    from .modules.conflict.embed import encode
    try:
        vecs = encode(["向量维度探针"])
        if vecs:
            _dim = len(vecs[0])
    except Exception:
        pass
    _dim_checked = True
    return _dim


def available() -> bool:
    if _client_or_none() is None:
        return False
    from .modules.conflict.embed import available as model_available
    return model_available()


def _ensure_collection(client) -> None:
    if client.has_collection(COLLECTION):
        try:
            client.load_collection(COLLECTION)
        except Exception:
            pass
        return
    from pymilvus import DataType
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("chunk_id", DataType.VARCHAR, max_length=128, is_primary=True)
    schema.add_field("doc_id", DataType.VARCHAR, max_length=128)
    schema.add_field("doc_title", DataType.VARCHAR, max_length=512)
    schema.add_field("chunk_text", DataType.VARCHAR, max_length=8192)
    schema.add_field("page", DataType.INT64)
    schema.add_field("section", DataType.VARCHAR, max_length=256)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=_vector_dim())
    client.create_collection(collection_name=COLLECTION, schema=schema)
    idx = client.prepare_index_params()
    idx.add_index(field_name="vector", index_type="HNSW", metric_type="COSINE",
                  params={"M": 16, "efConstruction": 200})
    client.create_index(collection_name=COLLECTION, index_params=idx)
    client.load_collection(COLLECTION)


def upsert_document(doc_id: str, doc_title: str, chunks: list[dict]) -> bool:
    """chunks = [{'id','page','section','text'}, ...]。标题拼进向量文本，便于按文件名检索。"""
    client = _client_or_none()
    if client is None or not chunks:
        return False
    from .modules.conflict.embed import encode
    texts = [f"{doc_title or ''}\n{c.get('text') or ''}" for c in chunks]
    vecs = encode(texts)
    if not vecs:
        return False
    try:
        with _lock:
            _ensure_collection(client)
            rows = [{
                "chunk_id": str(c.get("id") or f"{doc_id}-c{i}"),
                "doc_id": str(doc_id),
                "doc_title": str(doc_title or ""),
                "chunk_text": str(c.get("text") or ""),
                "page": int(c.get("page") or 1),
                "section": str(c.get("section") or "正文"),
                "vector": v,
            } for i, (c, v) in enumerate(zip(chunks, vecs), 1)]
            client.upsert(collection_name=COLLECTION, data=rows)
        return True
    except Exception:
        _mark_failed()
        return False


def delete_document(doc_id: str) -> None:
    client = _client_or_none()
    if client is None:
        return
    try:
        with _lock:
            if client.has_collection(COLLECTION):
                client.delete(collection_name=COLLECTION,
                              filter=f'doc_id == "{doc_id}"')
    except Exception:
        pass


def _snippet(text: str, query: str, width: int = 180) -> str:
    at = (text or "").lower().find((query or "").strip().lower()[:8])
    start = max(0, at - 30) if at >= 0 else 0
    piece = (text or "")[start:start + width]
    return ("…" if start > 0 else "") + piece


def search(query: str, limit: int = 20) -> list[dict]:
    """语义检索，返回与 search_hits 一致的 hit 结构；失败返回空列表让调用方降级。"""
    client = _client_or_none()
    if client is None or not (query or "").strip():
        return []
    from .modules.conflict.embed import encode
    vecs = encode([query])
    if not vecs:
        return []
    try:
        res = client.search(
            collection_name=COLLECTION, data=vecs, anns_field="vector",
            limit=limit,
            output_fields=["chunk_id", "doc_id", "doc_title", "chunk_text", "page", "section"],
            search_params={"metric_type": "COSINE", "params": {"ef": 64}},
        )
    except Exception:
        return []
    hits = []
    for row in (res[0] if res else []):
        # 余弦相似度太低视为不相关，交给词法检索兜底（尤其是扫描件按文件名定位）
        if float(row.get("distance") or 0) < 0.58:
            continue
        ent = row.get("entity") or {}
        text = str(ent.get("chunk_text") or "")
        hits.append({
            "docId": str(ent.get("doc_id") or ""),
            "docTitle": str(ent.get("doc_title") or ""),
            "chunkId": str(ent.get("chunk_id") or ""),
            "page": int(ent.get("page") or 1),
            "section": str(ent.get("section") or "正文"),
            "snippet": _snippet(text, query),
            "score": round(float(row.get("distance") or 0) * 100, 1),
            "format": "",
            "text": text,
        })
    return hits


def sync_all() -> int:
    """把 SQLite 存量切片补进 Milvus，返回本次同步的资料数。"""
    client = _client_or_none()
    if client is None:
        return 0
    with _lock:
        try:
            _ensure_collection(client)
            rows = client.query(collection_name=COLLECTION, filter='doc_id != ""',
                                output_fields=['chunk_id'], limit=16384)
            existing = {str(r.get("chunk_id") or "") for r in rows}
        except Exception:
            _mark_failed()
            existing = set()
    done = 0
    with SessionLocal() as s:
        for doc in s.query(Document).filter(Document.status == "已解析").all():
            chunks = [{
                "id": c.id, "page": c.page, "section": c.section, "text": c.text,
            } for c in s.query(Chunk).filter(Chunk.doc_id == doc.id).all()
                if c.id not in existing]
            if chunks and upsert_document(doc.id, doc.title, chunks):
                existing.update(str(c["id"]) for c in chunks)
                done += 1
    return done


# ---------------------------------------------------------------- 历史问题独立 collection
# 与制度库 knowledge_chunks 严格隔离：历史审计问题只写下面的 collection。
# 复用上面的同一个 MilvusClient（Milvus Lite 单进程独占文件锁），不另建客户端。

AUDIT_COLLECTION = "audit_issues"
_AUDIT_TEXT_MAX = 8000


def _ensure_audit_collection(client) -> None:
    """确保历史问题 collection 存在并已加载；schema 与制度库互不共享。"""
    if client.has_collection(AUDIT_COLLECTION):
        client.load_collection(AUDIT_COLLECTION)
        return
    from pymilvus import DataType
    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field("issue_id", DataType.VARCHAR, max_length=128, is_primary=True)
    schema.add_field("source_year", DataType.INT64)
    schema.add_field("cat_l4", DataType.VARCHAR, max_length=512)
    schema.add_field("problem_desc", DataType.VARCHAR, max_length=8192)
    schema.add_field("search_text", DataType.VARCHAR, max_length=8192)
    schema.add_field("vector", DataType.FLOAT_VECTOR, dim=_vector_dim())
    client.create_collection(collection_name=AUDIT_COLLECTION, schema=schema)
    idx = client.prepare_index_params()
    idx.add_index(field_name="vector", index_type="HNSW", metric_type="COSINE",
                  params={"M": 16, "efConstruction": 200})
    client.create_index(collection_name=AUDIT_COLLECTION, index_params=idx)
    client.load_collection(AUDIT_COLLECTION)


def upsert_audit_issues(rows: list[dict]) -> bool:
    """批量写入历史问题向量。

    Args:
        rows: [{'issue_id','source_year','cat_l4','problem_desc','search_text'}, ...]
    Returns:
        是否全部写入成功；失败只返回 False，由调用方按词法兜底，不熔断制度库检索。
    """
    import logging

    client = _client_or_none()
    if client is None or not rows:
        return False
    from .modules.conflict.embed import encode
    vecs = encode([str(r.get("search_text") or "")[:_AUDIT_TEXT_MAX] for r in rows])
    if not vecs or len(vecs) != len(rows):
        return False
    try:
        with _lock:
            _ensure_audit_collection(client)
            data = [{
                "issue_id": str(r.get("issue_id") or ""),
                "source_year": int(r.get("source_year") or 0),
                "cat_l4": str(r.get("cat_l4") or "")[:500],
                "problem_desc": str(r.get("problem_desc") or "")[:_AUDIT_TEXT_MAX],
                "search_text": str(r.get("search_text") or "")[:_AUDIT_TEXT_MAX],
                "vector": v,
            } for r, v in zip(rows, vecs)]
            # Milvus 单次 upsert 有条目上限，分批提交
            for start in range(0, len(data), 100):
                client.upsert(collection_name=AUDIT_COLLECTION, data=data[start:start + 100])
        return True
    except Exception as exc:
        logging.getLogger("knowledge.audit").warning("历史问题向量写入失败：%s", exc)
        return False


def search_audit_issues(query: str, limit: int = 20) -> list[dict]:
    """历史问题语义检索；不可用时返回空列表，交给调用方词法兜底。"""
    client = _client_or_none()
    if client is None or not (query or "").strip():
        return []
    from .modules.conflict.embed import encode
    vecs = encode([query])
    if not vecs:
        return []
    try:
        with _lock:
            if not client.has_collection(AUDIT_COLLECTION):
                return []
            client.load_collection(AUDIT_COLLECTION)
            res = client.search(
                collection_name=AUDIT_COLLECTION, data=vecs, anns_field="vector",
                limit=limit,
                output_fields=["issue_id", "source_year", "cat_l4", "problem_desc", "search_text"],
                search_params={"metric_type": "COSINE", "params": {"ef": 64}},
            )
    except Exception:
        return []
    hits: list[dict] = []
    for row in (res[0] if res else []):
        # 阈值略放宽：宁可多召回，最终排序由 RRF 融合决定
        distance = float(row.get("distance") or 0)
        if distance < 0.55:
            continue
        ent = row.get("entity") or {}
        text = str(ent.get("search_text") or "")
        hits.append({
            "issueId": str(ent.get("issue_id") or ""),
            "sourceYear": int(ent.get("source_year") or 0),
            "catL4": str(ent.get("cat_l4") or ""),
            "problemDesc": str(ent.get("problem_desc") or ""),
            "snippet": _snippet(text, query),
            "score": round(distance * 100, 1),
        })
    return hits


def reset_audit_collection() -> bool:
    """重导入前清空独立 collection；失败返回 False，调用方据此提示向量可能残留。"""
    client = _client_or_none()
    if client is None:
        return False
    try:
        with _lock:
            if client.has_collection(AUDIT_COLLECTION):
                client.drop_collection(AUDIT_COLLECTION)
        return True
    except Exception:
        return False


def audit_collection_count() -> int:
    """独立 collection 的条目数；向量库不可用时返回 -1 表示未知。"""
    client = _client_or_none()
    if client is None:
        return -1
    try:
        with _lock:
            if not client.has_collection(AUDIT_COLLECTION):
                return 0
            client.load_collection(AUDIT_COLLECTION)
            stats = client.get_collection_stats(collection_name=AUDIT_COLLECTION) or {}
            return int(stats.get("row_count") or 0)
    except Exception:
        return -1
