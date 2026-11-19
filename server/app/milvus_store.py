"""Milvus 向量库：知识库切片的向量化存储与语义检索。

默认用 Milvus Lite（嵌入式，数据文件 server/data/milvus.db，无需另外起服务）；
设置环境变量 MILVUS_URI 可切换到独立部署的 Milvus 服务。
向量复用 bge-small-zh-v1.5；Milvus 或模型不可用时，检索方自动降级到词法检索。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from .db import BASE_DIR, Chunk, Document, SessionLocal

COLLECTION = "knowledge_chunks"
MILVUS_URI = os.getenv("MILVUS_URI") or str(BASE_DIR / "data" / "milvus.db")

_client = None
_lock = threading.Lock()
_failed = False
_dim = 512
_dim_checked = False


def _client_or_none():
    global _client, _failed
    if _client is not None:
        return _client
    if _failed:
        return None
    with _lock:
        if _client is not None:
            return _client
        try:
            from pymilvus import MilvusClient
            Path(MILVUS_URI).parent.mkdir(parents=True, exist_ok=True)
            _client = MilvusClient(uri=MILVUS_URI)
        except Exception:
            _failed = True
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
