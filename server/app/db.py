"""数据层：SQLite + SQLAlchemy。把 DB_URL 换成 MySQL 连接串即可迁移。

现有 6 张表的字段保持不变；重构所需能力一律通过新增表扩展。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from pathlib import Path

from sqlalchemy import Boolean, Column, Integer, JSON, String, Text, create_engine, text
from sqlalchemy import text as sa_text
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
PARSED_DIR = DATA_DIR / "parsed"
ASSET_DIR = DATA_DIR / "assets"
BACKUP_DIR = DATA_DIR / "backups"
# 智能助手附件专用临时区：与资料库原件（UPLOAD_DIR）物理隔离，不参与任何检索/统计
TEMP_UPLOAD_DIR = DATA_DIR / "assistant_uploads"
for directory in (DATA_DIR, UPLOAD_DIR, PARSED_DIR, ASSET_DIR, BACKUP_DIR, TEMP_UPLOAD_DIR):
    directory.mkdir(parents=True, exist_ok=True)

from .config import settings

if settings.is_postgres:
    # 标准栈：PostgreSQL 15，所有 ORM 与原生 SQL 固定落到应用专属 schema
    DB_URL = settings.sync_database_uri
    _DB_ARGS = {
        "options": f"-csearch_path={settings.DB_SCHEMA}",
        "connect_timeout": 10,
    }
else:
    # 本地单机降级路径，仅用于开发验证
    DB_URL = settings.sqlite_uri
    _DB_ARGS = {"check_same_thread": False, "timeout": 30}
engine = create_engine(DB_URL, connect_args=_DB_ARGS, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()
if settings.is_postgres:
    Base.metadata.schema = settings.DB_SCHEMA


class Document(Base):
    __tablename__ = "documents"
    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    format = Column(String, default="PDF")
    source = Column(String, default="本地文件")
    source_url = Column(String, default="")
    category = Column(String, default="项目文档")
    tags = Column(JSON, default=list)
    status = Column(String, default="已解析")
    pages = Column(Integer, default=1)
    size_kb = Column(Integer, default=1)
    imported_at = Column(String, default="")
    summary = Column(Text, default="")
    keywords = Column(JSON, default=list)
    starred = Column(Boolean, default=False)
    file_path = Column(String, default="")


class Chunk(Base):
    __tablename__ = "chunks"
    id = Column(String, primary_key=True)
    doc_id = Column(String, index=True, nullable=False)
    page = Column(Integer, default=1)
    section = Column(String, default="正文")
    text = Column(Text, default="")


class ImportTaskRow(Base):
    __tablename__ = "import_tasks"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    format = Column(String, default="PDF")
    source = Column(String, default="本地文件")
    size_kb = Column(Integer, default=1)
    progress = Column(Integer, default=0)
    status = Column(String, default="等待中")
    created_at = Column(String, default="")
    doc_id = Column(String, default="")
    message = Column(String, default="")
    file_path = Column(String, default="")
    url = Column(String, default="")
    stage = Column(String, default="")
    relative_path = Column(String, default="")
    batch_id = Column(String, default="")
    category = Column(String, default="")
    attempt = Column(Integer, default=0)


class NoteRow(Base):
    __tablename__ = "notes"
    id = Column(String, primary_key=True)
    doc_id = Column(String, index=True, nullable=False)
    doc_title = Column(String, default="")
    chunk_id = Column(String, default="")
    page = Column(Integer, nullable=True)
    content = Column(Text, default="")
    created_at = Column(String, default="")


class QARow(Base):
    __tablename__ = "qa_history"
    id = Column(String, primary_key=True)
    question = Column(Text, nullable=False)
    answer = Column(JSON, default=dict)
    citations = Column(JSON, default=list)
    created_at = Column(String, default="")
    recall_mode = Column(String, default="混合")
    feedback = Column(String, default="")


class QASession(Base):
    """一次连续对话。不改动 qa_history，通过关联表把已有问答挂到会话上。

    module 用于隔离不同助手入口的会话：assistant / contract / conflict。
    """
    __tablename__ = "qa_sessions"
    id = Column(String, primary_key=True)
    title = Column(String, default="新会话")
    created_at = Column(String, default="")
    updated_at = Column(String, default="")
    module = Column(String, default="assistant")


class QASessionItem(Base):
    """会话与问答的关联，seq 保证同一会话内的顺序。"""
    __tablename__ = "qa_session_items"
    id = Column(String, primary_key=True)
    session_id = Column(String, index=True, nullable=False)
    qa_id = Column(String, index=True, nullable=False)
    seq = Column(Integer, default=0)


class AgentRun(Base):
    """一次智能体运行：状态、步数、耗时，用于审计与取消。"""
    __tablename__ = "agent_runs"
    id = Column(String, primary_key=True)
    session_id = Column(String, index=True, default="")
    question = Column(Text, default="")
    status = Column(String, default="running")  # running / done / stopped / failed
    steps_count = Column(Integer, default=0)
    cost_ms = Column(Integer, default=0)
    created_at = Column(String, default="")
    finished_at = Column(String, default="")


class AgentStep(Base):
    """运行内每一步：工具调用或最终答案，入参与结果均留痕。"""
    __tablename__ = "agent_steps"
    id = Column(String, primary_key=True)
    run_id = Column(String, index=True, nullable=False)
    seq = Column(Integer, default=0)
    kind = Column(String, default="tool")  # tool / answer / error
    tool_name = Column(String, default="")
    args_json = Column(JSON, default=dict)
    result_json = Column(JSON, default=dict)
    cost_ms = Column(Integer, default=0)


class EvalQuery(Base):
    """检索评估集：语料切片 + 由该切片生成的问题，标注正确答案所在 chunk。"""
    __tablename__ = "eval_queries"
    id = Column(String, primary_key=True)
    question = Column(Text, default="")
    chunk_id = Column(String, index=True, default="")
    doc_id = Column(String, default="")
    doc_title = Column(String, default="")
    category = Column(String, default="")
    difficulty = Column(String, default="easy")
    created_at = Column(String, default="")


class EvalRun(Base):
    """一次评测的指标快照，用于对比优化前后是否退化。"""
    __tablename__ = "eval_runs"
    id = Column(String, primary_key=True)
    created_at = Column(String, default="")
    total = Column(Integer, default=0)
    k = Column(Integer, default=5)
    mode = Column(String, default="混合")
    hit_rate = Column(Integer, default=0)  # 万分比，避免浮点列
    mrr = Column(Integer, default=0)       # 万分比
    cost_ms = Column(Integer, default=0)
    misses_json = Column(JSON, default=list)


class SettingRow(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(JSON, nullable=True)


# ---------------------------------------------------------------- 新增表

class Category(Base):
    __tablename__ = "categories"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    parent_id = Column(String, default="", index=True)
    order_index = Column(Integer, default=0)
    created_at = Column(String, default="")


class DocumentFulltext(Base):
    """完整解析文本，与切片相互独立，切片不替代原文。"""
    __tablename__ = "document_fulltext"
    doc_id = Column(String, primary_key=True)
    text = Column(Text, default="")
    char_count = Column(Integer, default=0)
    updated_at = Column(String, default="")


class DocumentFile(Base):
    """原始文件与 Markdown 关联资源；sha256 用于去重。"""
    __tablename__ = "document_files"
    id = Column(String, primary_key=True)
    doc_id = Column(String, index=True, default="")
    sha256 = Column(String, index=True, default="")
    original_name = Column(String, default="")
    relative_path = Column(String, default="")
    stored_path = Column(String, default="")
    size_bytes = Column(Integer, default=0)
    kind = Column(String, default="original")


class AssistantUpload(Base):
    """智能助手回形针上传的临时附件。

    与资料库完全隔离：没有 Document / Chunk / Fulltext / ImportTask 记录，
    因此不会出现在资料库列表、统计或检索结果中，仅供当次制度互斥检测读取原始字节。
    """
    __tablename__ = "assistant_uploads"
    id = Column(String, primary_key=True)          # tmp-xxxx，作为工具 docIds 传入
    session_id = Column(String, index=True, default="")  # 归属会话，删除会话时清理
    filename = Column(String, default="")          # 原始文件名，检测报告里显示
    stored_path = Column(String, default="")       # TEMP_UPLOAD_DIR 下的绝对路径
    size_bytes = Column(Integer, default=0)
    created_at = Column(String, default="")


DEFAULT_SETTINGS = {
    "displayName": "审计工作平台",
    "pageSize": 10,
    "viewMode": "list",
    "autoSummary": True,
    "autoClassify": True,
    "autoChunk": True,
    "retryCount": 2,
    "supportedFormats": ["PDF", "PPTX", "DOCX", "DOC", "MD", "TXT", "图片"],
    # DeepSeek 官方 API（OpenAI 兼容）：V4.1-Flash 的模型 ID 就是 deepseek-flash，
    # 官方地址不带 /v1。Key 不入库默认值，由系统设置或 DEEPSEEK_API_KEY 注入。
    # 需要回退原内网代理时，在系统设置把地址改回 https://dmxwg.intra.yiducloud.cn/litellm/v1
    "llmBaseUrl": "https://api.deepseek.com",
    "llmApiKey": "",
    "accessToken": "",
    "zhihuCookie": "",
    "llmModel": "deepseek-flash",
    "llmThinking": False,
    "temperature": 0.2,
    "maxTokens": 2048,
    "embeddingBaseUrl": "",
    "embeddingModel": "",
    "originalsDir": str(UPLOAD_DIR),
    "parsedDir": str(PARSED_DIR),
    "databasePath": str(DATA_DIR / "knowledge.db"),
}

MASKED = "••••••••"


def now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def purge_assistant_uploads(session_id: str = "", max_age_hours: int | None = None) -> int:
    """清理助手临时附件：按会话和/或过期时间删除登记行与磁盘文件。

    Args:
        session_id: 非空时只清理该会话的附件。
        max_age_hours: 非空时只清理创建时间早于该阈值的附件。

    Returns:
        实际删除的附件数量。
    """
    from datetime import datetime, timedelta

    removed = 0
    with SessionLocal() as s:
        query = s.query(AssistantUpload)
        if session_id:
            query = query.filter(AssistantUpload.session_id == session_id)
        if max_age_hours is not None:
            cutoff = (datetime.now() - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d %H:%M")
            query = query.filter(AssistantUpload.created_at < cutoff)
        for row in query.all():
            if row.stored_path:
                try:
                    path = Path(row.stored_path)
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass  # 文件已不在或无权删除时，仅清数据库记录
            s.delete(row)
            removed += 1
        if removed:
            s.commit()
    return removed


def get_raw_settings(session) -> dict:
    result = dict(DEFAULT_SETTINGS)
    for row in session.query(SettingRow).all():
        result[row.key] = row.value
    return result


SECRET_SETTINGS = {
    "llmApiKey": "llmApiKeySet",
    "zhihuCookie": "zhihuCookieSet",
    "accessToken": "accessTokenSet",
}


def get_settings(session) -> dict:
    result = get_raw_settings(session)
    for key, flag in SECRET_SETTINGS.items():
        if result.get(key):
            result[key] = MASKED
            result[flag] = True
        else:
            result[key] = ""
            result[flag] = False
    return result


def save_settings(session, patch: dict) -> dict:
    # 密钥类字段空值 / MASKED 表示「不修改」；accessToken 例外，显式传空表示清除令牌
    clean = {}
    for key, value in patch.items():
        if key in SECRET_SETTINGS and (not value or value == MASKED):
            if key == "accessToken" and value == "":
                clean[key] = ""
            continue
        clean[key] = value
    for key, value in clean.items():
        # 表单没加载到最新值时可能提交空串，别把已配好的代理地址 / 模型覆盖掉
        if key in ("llmBaseUrl", "llmModel") and isinstance(value, str) and not value.strip():
            value = DEFAULT_SETTINGS[key]
        row = session.get(SettingRow, key)
        if row:
            row.value = value
        else:
            session.add(SettingRow(key=key, value=value))
    session.commit()
    return get_settings(session)


def chunk_to_dict(c: Chunk) -> dict:
    return {"id": c.id, "page": c.page, "section": c.section, "text": c.text}


def doc_to_dict(d: Document, chunks: list[Chunk] | None = None) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "format": d.format,
        "source": d.source,
        "sourceUrl": d.source_url or "",
        "category": d.category,
        "tags": d.tags or [],
        "status": d.status,
        "pages": d.pages,
        "sizeKb": d.size_kb,
        "importedAt": d.imported_at,
        "summary": d.summary or "",
        "keywords": d.keywords or [],
        "starred": bool(d.starred),
        "chunks": [chunk_to_dict(c) for c in (chunks if chunks is not None else [])],
    }


# 分类层级上限：AI 自动分类与文件夹导入产生的分类链最深不超过此值
MAX_CATEGORY_DEPTH = 3

_category_lock = threading.Lock()


def ensure_category_chain(session, segments: list[str]) -> Category:
    """按层级逐级查找/创建分类链，返回最末级分类。

    串行化“查询-插入”避免并发导入时同名分类被重复创建；层级超过
    MAX_CATEGORY_DEPTH 时截断，确保分类不会无限嵌套。
    """
    segments = [seg.strip() for seg in segments if seg and seg.strip()][:MAX_CATEGORY_DEPTH]
    if not segments:
        return None
    parent_id = ""
    leaf = None
    with _category_lock:
        for seg in segments:
            leaf = session.query(Category).filter(
                Category.name == seg, Category.parent_id == parent_id).first()
            if not leaf:
                order = session.query(Category).filter(Category.parent_id == parent_id).count()
                leaf = Category(id=f"cat-{uuid.uuid4().hex[:12]}", name=seg,
                                parent_id=parent_id, order_index=order, created_at=now_str())
                session.add(leaf)
                session.commit()
            parent_id = leaf.id
    return leaf


def task_to_dict(t: ImportTaskRow) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "format": t.format,
        "source": t.source,
        "sizeKb": t.size_kb,
        "progress": t.progress,
        "status": t.status,
        "stage": t.stage or "",
        "relativePath": t.relative_path or "",
        "batchId": t.batch_id or "",
        "attempt": t.attempt or 0,
        "createdAt": t.created_at,
        "docId": t.doc_id or "",
        "message": t.message or "",
    }


def category_to_dict(c: Category) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "parentId": c.parent_id or "",
        "orderIndex": c.order_index or 0,
    }


def note_to_dict(n: NoteRow) -> dict:
    return {
        "id": n.id,
        "docId": n.doc_id,
        "docTitle": n.doc_title,
        "chunkId": n.chunk_id or "",
        "page": n.page,
        "content": n.content,
        "createdAt": n.created_at,
    }


def _qa_file_names(question: str) -> list[str]:
    """兼容旧数据：从问题里的文档 ID 反查原始文件名，供会话历史恢复附件标签。"""
    import re as _re
    m = _re.search(r"待检测的文档\s*ID[：:]\s*([^\n]+)", question or "")
    if not m:
        return []
    ids = [x.strip() for x in _re.split(r"[、,，\s]+", m.group(1)) if x.strip()]
    names: list[str] = []
    with SessionLocal() as s:
        for did in ids:
            up = s.get(AssistantUpload, did)
            if up and up.filename:
                names.append(up.filename)
                continue
            doc = s.get(Document, did)
            if doc and doc.title:
                names.append(doc.title)
    return names


def qa_to_dict(q: QARow) -> dict:
    stored = q.answer or {}
    # 过程信息随 answer 的 JSON 一起存（不新增列），读出时还原到顶层；
    # 兼容旧数据：没有 _meta 时原样返回。
    meta = stored.get("_meta") or {}
    answer = {k: v for k, v in stored.items() if k != "_meta"}
    files = meta.get("files")
    if files is None:
        files = _qa_file_names(q.question or "")
    return {
        "id": q.id,
        "question": q.question,
        "answer": answer,
        "citations": q.citations or [],
        "createdAt": q.created_at,
        "recallMode": q.recall_mode,
        "feedback": q.feedback or None,
        "files": files or [],
        **{k: v for k, v in meta.items() if v is not None},
    }


def session_to_dict(s: QASession, count: int = 0, last_question: str = "") -> dict:
    return {
        "id": s.id,
        "title": s.title or "新会话",
        "createdAt": s.created_at,
        "updatedAt": s.updated_at,
        "count": count,
        "lastQuestion": last_question,
        "module": getattr(s, "module", "") or "assistant",
    }


def init_db() -> None:
    if settings.is_postgres:
        # 标准栈：结构变更只走 Alembic，禁止 create_all
        from .run_migrations import ensure_schema_sync, run_migrations
        ensure_schema_sync()
        run_migrations()
    else:
        # 本地降级路径保留原有建表行为，仅用于开发验证
        Base.metadata.create_all(engine)
        # WAL 允许多线程读写并发，写锁等待 30s 而不是立即抛 database is locked
        with engine.connect() as conn:
            conn.execute(sa_text("PRAGMA journal_mode=WAL"))
            conn.execute(sa_text("PRAGMA busy_timeout=30000"))
        # 兼容旧库：新列以 ALTER TABLE 追加，不改已有列
        with SessionLocal() as session:
            cols = {r[1] for r in session.execute(__import__("sqlalchemy").text("PRAGMA table_info(import_tasks)"))}
            for name, ddl in (
                ("stage", "VARCHAR DEFAULT ''"),
                ("relative_path", "VARCHAR DEFAULT ''"),
                ("batch_id", "VARCHAR DEFAULT ''"),
                ("category", "VARCHAR DEFAULT ''"),
                ("attempt", "INTEGER DEFAULT 0"),
            ):
                if name not in cols:
                    session.execute(__import__("sqlalchemy").text(f"ALTER TABLE import_tasks ADD COLUMN {name} {ddl}"))
            qa_cols = {r[1] for r in session.execute(__import__("sqlalchemy").text("PRAGMA table_info(qa_sessions)"))}
            if "module" not in qa_cols:
                session.execute(__import__("sqlalchemy").text(
                    "ALTER TABLE qa_sessions ADD COLUMN module VARCHAR DEFAULT 'assistant'"))
            session.commit()
    with SessionLocal() as session:
        for key, value in DEFAULT_SETTINGS.items():
            if not session.get(SettingRow, key):
                session.add(SettingRow(key=key, value=value))

        if session.query(Document).count() == 0:
            seed_path = Path(__file__).resolve().parent / "seed.json"
            if seed_path.exists():
                for item in json.loads(seed_path.read_text(encoding="utf-8")):
                    session.add(Document(
                        id=item["id"], title=item["title"], format=item["format"],
                        source=item["source"], source_url=item.get("sourceUrl", ""),
                        category=item.get("category", "项目文档"), tags=item.get("tags", []),
                        status=item.get("status", "已解析"), pages=item.get("pages", 1),
                        size_kb=item.get("sizeKb", 1), imported_at=item.get("importedAt", now_str()),
                        summary=item.get("summary", ""), keywords=item.get("keywords", []),
                        starred=item.get("starred", False),
                    ))
                    for c in item.get("chunks", []):
                        session.add(Chunk(id=c["id"], doc_id=item["id"], page=c.get("page", 1),
                                          section=c.get("section", "正文"), text=c.get("text", "")))

        # 用现有资料的 category 值生成一级分类，不预置固定体系
        if session.query(Category).count() == 0:
            names = []
            for (name,) in session.query(Document.category).distinct():
                if name and name not in names:
                    names.append(name)
            for i, name in enumerate(names):
                session.add(Category(id=f"cat-{i + 1}", name=name, parent_id="", order_index=i, created_at=now_str()))

        # 为已有资料补齐完整原文与原件登记，不覆盖已有记录
        for d in session.query(Document).all():
            if not session.get(DocumentFulltext, d.id):
                chunks = session.query(Chunk).filter(Chunk.doc_id == d.id).order_by(Chunk.id).all()
                text = "\n\n".join(c.text for c in chunks)
                if text:
                    session.add(DocumentFulltext(doc_id=d.id, text=text, char_count=len(text), updated_at=now_str()))
            if d.file_path and Path(d.file_path).exists():
                exists = session.query(DocumentFile).filter(
                    DocumentFile.doc_id == d.id, DocumentFile.kind == "original"
                ).first()
                if not exists:
                    p = Path(d.file_path)
                    session.add(DocumentFile(
                        id=f"file-{d.id}", doc_id=d.id, sha256=sha256_file(p),
                        original_name=d.title, relative_path=d.title, stored_path=str(p),
                        size_bytes=p.stat().st_size, kind="original",
                    ))
        session.commit()
