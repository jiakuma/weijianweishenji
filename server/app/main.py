"""审计工作平台后端。固定 5281 端口，与前端 5280 配对。

保留原有全部接口与数据；重构所需能力以新增接口、新增表的方式扩展。
大模型调用统一由 app/llm.py 适配：DeepSeek 官方 API，模型 deepseek-flash（V4.1-Flash）。
"""
from __future__ import annotations

import json
import queue
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from .config import settings
from .deps import success_response
from .errors import (
    AppError,
    BadRequestError,
    NotFoundError,
    UnauthorizedError,
    register_exception_handlers,
)
from . import parsers, webfetch
from .classify import classify_document
from .db import (
    ASSET_DIR,
    BACKUP_DIR,
    DATA_DIR,
    PARSED_DIR,
    UPLOAD_DIR,
    AgentRun,
    AgentStep,
    Category,
    Chunk,
    Document,
    DocumentFile,
    DocumentFulltext,
    ensure_category_chain,
    ImportTaskRow,
    NoteRow,
    QARow,
    QASession,
    QASessionItem,
    SessionLocal,
    AssistantUpload,
    TEMP_UPLOAD_DIR,
    purge_assistant_uploads,
    category_to_dict,
    doc_to_dict,
    engine,
    get_raw_settings,
    get_settings,
    init_db,
    note_to_dict,
    now_str,
    qa_to_dict,
    save_settings,
    session_to_dict,
    sha256_file,
    task_to_dict,
)
from .search import search_hits
from .qa import answer_question
from . import agent_run, agent_tools, evaluation, zhihu_login
from .llm import LLMConfig, llm_config, normalize_base_url, post_chat
from .modules import discover_modules, install_modules, list_modules

# 先发现业务模块：模块内定义的数据表须在 init_db() 之前注册到 Base.metadata
discover_modules()

MAX_UPLOAD = 200 * 1024 * 1024
IMAGE_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tif", ".tiff"}
ALLOWED_SUFFIX = {".pdf", ".pptx", ".ppt", ".docx", ".doc", ".md", ".markdown", ".txt", ".html", ".htm"} | IMAGE_SUFFIX

STAGES = [
    "原始文件保存", "文件格式识别", "文档解析", "完整文本提取", "AI 摘要生成",
    "自动分类", "关键词和标签提取", "文档切片", "建立检索索引", "更新知识库分类树",
]


def uid(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n]', "_", name)[:120] or "file"


def _update_task(task_id: str, **fields) -> None:
    with SessionLocal() as s:
        t = s.get(ImportTaskRow, task_id)
        if not t:
            return
        for key, value in fields.items():
            setattr(t, key, value)
        s.commit()


def _stage(task_id: str, index: int) -> None:
    _update_task(task_id, stage=STAGES[index], progress=(index + 1) * 10, status="解析中")
    time.sleep(0.12)


def _collect_assets(batch_dir: Path, md_path: Path, doc_id: str, text: str):
    refs = parsers.extract_markdown_images(text)
    mapping, records = {}, []
    for ref in refs:
        key = parsers.normalize_asset_ref(ref)
        candidates = [batch_dir / key, batch_dir / Path(key).name, md_path.parent / key,
                      md_path.parent / Path(key).name]
        found = next((c for c in candidates if c.is_file()), None)
        if not found:
            continue
        asset_id = uid("asset")
        target = ASSET_DIR / doc_id / found.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve() != found.resolve():
            shutil.copyfile(found, target)
        mapping[key] = f"/api/assets/{asset_id}"
        records.append(DocumentFile(
            id=asset_id, doc_id=doc_id, sha256=sha256_file(found),
            original_name=found.name, relative_path=key, stored_path=str(target),
            size_bytes=found.stat().st_size, kind="asset",
        ))
    return mapping, records


def _import_worker(task_id: str) -> None:
    with SessionLocal() as s:
        t = s.get(ImportTaskRow, task_id)
        if not t:
            return
        settings = get_raw_settings(s)
        path = Path(t.file_path)
        name, fmt, source = t.name, t.format, t.source
        size_kb, batch, relative = t.size_kb, t.batch_id, t.relative_path
        wanted_category = t.category or ""
    if not path.exists():
        _update_task(task_id, status="失败", progress=100, stage="", message="找不到已保存的原始文件")
        return

    # 断点续传时若该原件已入库（且资料仍存在），直接跳过，避免重启后重复生成资料
    digest = sha256_file(path)
    with SessionLocal() as s:
        rows = s.query(DocumentFile).filter(
            DocumentFile.sha256 == digest, DocumentFile.kind == "original").all()
        alive = [r for r in rows if r.doc_id and s.get(Document, r.doc_id)]
        for r in rows:
            if r not in alive:
                s.delete(r)  # 孤儿登记：资料已删，清掉允许重新导入
        s.commit()
    if alive:
        _update_task(task_id, status="重复跳过", progress=100, stage="", message="该原件已入库，跳过重复处理")
        return

    doc_id = uid("d")
    try:
        _stage(task_id, 0)
        _stage(task_id, 1)
        _stage(task_id, 2)
        pages, raw = parsers.parse_file(path, name)

        _stage(task_id, 3)
        fulltext = parsers.full_text_from_pages(raw)
        batch_dir = UPLOAD_DIR / batch if batch else path.parent
        _, asset_records = _collect_assets(batch_dir, path, doc_id, fulltext)

        chunks = parsers.split_into_chunks(raw)
        keywords = parsers.extract_keywords(chunks)
        _stage(task_id, 4)
        summary = parsers.extract_summary(chunks, bool(settings.get("autoSummary", True)))
        if fmt == "图片" and fulltext.startswith("图片资料："):
            summary = "图片资料：已完整保留原始文件，OCR 未能识别文字，未生成文字摘要。"
        elif "【扫描版文件】" in fulltext:
            # 扫描版 PDF：无文字层，如实标注，不生成伪摘要/伪切片
            summary = "扫描版 PDF：无文字层（每页均为图片），本地未启用 OCR；原始文件已保留，可在「原始文件」中在线预览。"
            keywords, chunks = [], []

        _stage(task_id, 5)
        # 文件夹导入：按文件夹内部结构建嵌套分类链（保留层级）。
        # relative_path 的首段即选中的根文件夹（顶层分类），其下的子目录逐级
        # 建成子分类，文件归入其所在（最深）文件夹对应的分类，不被展平。
        # 单文件导入：不做自动分类，按用户在导入时指定的分类；未指定进「未分类」。
        folder_parts = [p for p in (relative or "").split("/")[:-1] if p.strip()]
        if folder_parts:
            with SessionLocal() as s:
                leaf = ensure_category_chain(s, folder_parts)
                category = leaf.name if leaf else RESERVED_CATEGORY
        elif wanted_category:
            with SessionLocal() as s:
                segments = [p.strip() for p in wanted_category.split("/") if p.strip()]
                leaf = ensure_category_chain(s, segments)
                category = leaf.name if leaf else RESERVED_CATEGORY
        else:
            category = RESERVED_CATEGORY
        _stage(task_id, 6)
        tags = list(dict.fromkeys(keywords[:3] + [category]))[:5]
        _stage(task_id, 7)
        kept_chunks = chunks if settings.get("autoChunk", True) else []
        _stage(task_id, 8)

        # 一篇资料的全部结果在同一次提交里落库，中途失败不会留下半篇资料
        with SessionLocal() as s:
            s.add(Document(
                id=doc_id, title=name, format=fmt, source=source, category=category,
                tags=tags, status="已解析", pages=pages, size_kb=size_kb,
                imported_at=now_str(), summary=summary, keywords=keywords,
                starred=False, file_path=str(path),
            ))
            s.add(DocumentFulltext(doc_id=doc_id, text=fulltext, char_count=len(fulltext), updated_at=now_str()))
            for i, (page, section, text) in enumerate(kept_chunks, start=1):
                s.add(Chunk(id=f"{doc_id}-c{i}", doc_id=doc_id, page=page, section=section, text=text))
            s.add(DocumentFile(
                id=f"file-{doc_id}", doc_id=doc_id, sha256=digest,
                original_name=name, relative_path=relative or name, stored_path=str(path),
                size_bytes=path.stat().st_size, kind="original",
            ))
            for record in asset_records:
                s.add(record)
            (PARSED_DIR / f"{doc_id}.txt").write_text(fulltext, encoding="utf-8")
            task = s.get(ImportTaskRow, task_id)
            if task:
                task.stage, task.progress = STAGES[8], 90
                s.commit()
                task.stage, task.progress, task.status = STAGES[9], 100, "已完成"
                task.doc_id, task.message = doc_id, "解析完成"
            s.commit()
            if kept_chunks:
                from .milvus_store import upsert_document
                upsert_document(doc_id, name, [{
                    "id": f"{doc_id}-c{i}", "page": p, "section": sec, "text": txt,
                } for i, (p, sec, txt) in enumerate(kept_chunks, start=1)])
    except Exception as exc:
        _update_task(task_id, status="失败", progress=100, stage="", message=f"解析失败：{exc}"[:160])


# 有限并发：导入走线程池排队，避免几十个线程同时写 SQLite 互相锁死
_import_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="import")


def _submit_import(task_id: str) -> None:
    _import_pool.submit(_import_worker, task_id)


def resume_pending_tasks() -> None:
    """断点续传：重启后只重跑未完成任务，已完成的不重复处理。"""
    with SessionLocal() as s:
        pending = s.query(ImportTaskRow).filter(ImportTaskRow.status.in_(["等待中", "解析中"])).all()
        ids = [t.id for t in pending]
        for t in pending:
            t.status, t.progress, t.stage = "等待中", 0, ""
        s.commit()
    for task_id in ids:
        _submit_import(task_id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate()
    init_db()
    resume_pending_tasks()
    # 存量切片补进向量库，放后台线程避免模型首次加载拖慢启动
    import threading
    from . import milvus_store

    def _sync_worker() -> None:
        # 向量库只是检索增强能力：数据目录损坏/索引缺失时不得影响平台启动。
        try:
            milvus_store.sync_all()
        except Exception:
            pass

    threading.Thread(target=_sync_worker, daemon=True, name="milvus-sync").start()
    yield


app = FastAPI(title="审计工作平台后端", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5280", "http://127.0.0.1:5280",
        "http://localhost:5180", "http://127.0.0.1:5180",
    ],
    # SSO 模式依赖同域 cookie，必须允许携带凭证
    allow_credentials=settings.is_sso,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Access-Token", settings.SSO_TRIGGER_HEADER],
)

install_modules(app)
register_exception_handlers(app)

# 鉴权模式二选一：sso 用中台统一认证，local 保留本机令牌（仅开发验证）
if settings.is_sso:
    from .auth import register_auth_middleware
    register_auth_middleware(app)

_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


async def write_access_guard(request: Request, call_next):
    """本机降级鉴权：写接口需令牌；未配令牌时只允许本机写入。"""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)
    with SessionLocal() as s:
        token = str(get_raw_settings(s).get("accessToken") or "").strip()
    if token:
        if request.headers.get("x-access-token") != token:
            return JSONResponse(status_code=401, content={"detail": "需要有效的访问令牌"})
    else:
        host = request.client.host if request.client else ""
        if host not in _LOCAL_HOSTS:
            return JSONResponse(status_code=403, content={"detail": "未配置访问令牌时仅允许本机写入"})
    return await call_next(request)


if not settings.is_sso:
    app.middleware("http")(write_access_guard)


# ---------------------------------------------------------------- 基础

@app.get("/api/health")
@app.get("/health", include_in_schema=False)
def health():
    # 兼容原有前端的 ok 字段，同时满足标准健康检查契约
    return {"status": "OK", "ok": True, "service": "audit-platform",
            "time": datetime.now().isoformat()}


@app.get("/api/modules")
def modules():
    """模块清单：前端侧边栏与扩展状态以此为准。"""
    return list_modules()


class DocQuery(BaseModel):
    keyword: str = ""
    formats: list[str] = []
    sources: list[str] = []
    categories: list[str] = []
    tags: list[str] = []
    status: str = ""
    category: str = ""
    starredOnly: bool = False
    sort: str = "time"


def _category_with_descendants(session, name: str) -> list[str]:
    """分类名本身 + 其全部子孙分类名，用于点父分类时把子分类资料一并查出。"""
    cats = session.query(Category).all()
    children_of: dict[str, list[Category]] = {}
    for c in cats:
        children_of.setdefault(c.parent_id or "", []).append(c)
    out = {name}
    stack = [c.id for c in cats if c.name == name]
    while stack:
        cid = stack.pop()
        for ch in children_of.get(cid, []):
            out.add(ch.name)
            stack.append(ch.id)
    return list(out)


@app.post("/api/docs/query")
def list_docs(q: DocQuery):
    with SessionLocal() as s:
        docs = s.query(Document).all()
        kw = q.keyword.strip().lower()
        allowed_cats: set[str] | None = None
        if q.category:
            allowed_cats = set(_category_with_descendants(s, q.category))
        elif q.categories:
            allowed_cats = set()
            for cname in q.categories:
                allowed_cats.update(_category_with_descendants(s, cname))
        result = []
        for d in docs:
            if kw and kw not in f"{d.title} {d.summary} {' '.join(d.tags or [])} {' '.join(d.keywords or [])}".lower():
                continue
            if q.formats and d.format not in q.formats:
                continue
            if q.sources and d.source not in q.sources:
                continue
            if allowed_cats is not None and d.category not in allowed_cats:
                continue
            if q.tags and not any(t in (d.tags or []) for t in q.tags):
                continue
            if q.status and d.status != q.status:
                continue
            if q.starredOnly and not d.starred:
                continue
            result.append(doc_to_dict(d, []))
        if q.sort == "name":
            result.sort(key=lambda x: x["title"].lower())
        else:
            result.sort(key=lambda x: x["importedAt"], reverse=True)
        return result


@app.get("/api/docs/{doc_id}")
def get_doc(doc_id: str):
    with SessionLocal() as s:
        d = s.get(Document, doc_id)
        if not d:
            return {}
        chunks = s.query(Chunk).filter(Chunk.doc_id == doc_id).all()
        data = doc_to_dict(d, chunks)
        data["hasFulltext"] = bool(s.get(DocumentFulltext, doc_id))
        data["hasOriginal"] = bool(s.query(DocumentFile).filter(
            DocumentFile.doc_id == doc_id, DocumentFile.kind == "original").first())
        data["officePreview"] = bool(_find_soffice())
        return data


@app.get("/api/docs/{doc_id}/fulltext")
def get_fulltext(doc_id: str):
    with SessionLocal() as s:
        row = s.get(DocumentFulltext, doc_id)
        return {"docId": doc_id, "text": row.text if row else "", "charCount": row.char_count if row else 0}


@app.get("/api/docs/{doc_id}/assets")
def get_assets(doc_id: str):
    with SessionLocal() as s:
        rows = s.query(DocumentFile).filter(DocumentFile.doc_id == doc_id, DocumentFile.kind == "asset").all()
        return [{"id": r.id, "name": r.original_name, "reference": r.relative_path,
                 "url": f"/api/assets/{r.id}", "size": r.size_bytes} for r in rows]


@app.get("/api/assets/{asset_id}")
def serve_asset(asset_id: str):
    with SessionLocal() as s:
        row = s.get(DocumentFile, asset_id)
        if not row or not Path(row.stored_path).exists():
            raise NotFoundError("资源不存在")
        return FileResponse(row.stored_path)


@app.get("/api/docs/{doc_id}/file")
def download_original(doc_id: str):
    with SessionLocal() as s:
        row = s.query(DocumentFile).filter(
            DocumentFile.doc_id == doc_id, DocumentFile.kind == "original").first()
        d = s.get(Document, doc_id)
        if not row or not Path(row.stored_path).exists():
            raise NotFoundError("原始文件不存在")
        return FileResponse(row.stored_path, filename=(d.title if d else row.original_name),
                            content_disposition_type="inline")


@app.get("/api/docs/{doc_id}/preview")
def preview_original(doc_id: str):
    """原文件内联预览：PDF 直接返回；DOCX/PPTX 经 LibreOffice 转 PDF 后返回。"""
    with SessionLocal() as s:
        row = s.query(DocumentFile).filter(
            DocumentFile.doc_id == doc_id, DocumentFile.kind == "original").first()
        if not row or not Path(row.stored_path).exists():
            raise NotFoundError("原始文件不存在")
        p = Path(row.stored_path)
        if p.suffix.lower() == ".pdf":
            return FileResponse(p, content_disposition_type="inline")
        if p.suffix.lower() in (".docx", ".doc", ".pptx", ".ppt"):
            pdf = _doc_to_pdf(p)
            if pdf:
                return FileResponse(pdf, content_disposition_type="inline")
            raise BadRequestError("未安装 LibreOffice，无法内联预览 Office 文档")
        raise BadRequestError("不支持的文件格式")


def _find_soffice() -> str | None:
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return shutil.which("soffice") or shutil.which("libreoffice")


def _doc_to_pdf(src: Path) -> Path | None:
    soffice = _find_soffice()
    if not soffice:
        return None
    cache = DATA_DIR / "pdf_preview"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{src.stem}.pdf"
    if target.exists():
        return target
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(cache), str(src)],
            check=True, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    return target if target.exists() else None


@app.post("/api/docs/{doc_id}/star")
def toggle_star(doc_id: str):
    with SessionLocal() as s:
        d = s.get(Document, doc_id)
        if not d:
            return {"starred": False}
        d.starred = not d.starred
        s.commit()
        return {"starred": bool(d.starred)}


@app.post("/api/docs/{doc_id}/category")
def change_category(doc_id: str, body: dict):
    with SessionLocal() as s:
        d = s.get(Document, doc_id)
        if not d:
            return {"ok": False}
        name = str(body.get("category", d.category)).strip()
        if name and name != RESERVED_CATEGORY and name != d.category:
            segments = [p.strip() for p in name.split("/") if p.strip()]
            leaf = ensure_category_chain(s, segments)
            name = leaf.name if leaf else name
        d.category = name or RESERVED_CATEGORY
        s.commit()
        return {"ok": True, "category": d.category}


@app.post("/api/docs/ai-classify")
def ai_classify_docs(body: dict):
    """一键 AI 分类：对指定资料（或所有「未分类」资料）调用模型自动归类。"""
    with SessionLocal() as s:
        _ensure_uncategorized(s)
        enabled = bool(get_raw_settings(s).get("autoClassify", True))
        doc_ids = body.get("docIds") or []
        if doc_ids:
            docs = s.query(Document).filter(Document.id.in_(doc_ids)).all()
        else:
            docs = s.query(Document).filter(Document.category == RESERVED_CATEGORY).all()
        total = len(docs)
        classified = 0
        for d in docs:
            ft = s.get(DocumentFulltext, d.id)
            text = ft.text if ft else ""
            new_cat = classify_document(s, d.title, text, d.keywords or [], enabled)
            if new_cat and new_cat != d.category:
                d.category = new_cat
                classified += 1
            s.commit()
        pruned = _prune_empty_categories(s)
        return {"ok": True, "classified": classified, "total": total, "pruned": pruned}


@app.post("/api/docs/{doc_id}/tags")
def change_tags(doc_id: str, body: dict):
    with SessionLocal() as s:
        d = s.get(Document, doc_id)
        if not d:
            return {"ok": False}
        d.tags = [str(t).strip() for t in body.get("tags", []) if str(t).strip()][:10]
        s.commit()
        return {"ok": True, "tags": d.tags}


@app.delete("/api/docs/{doc_id}")
def delete_doc(doc_id: str, purge: bool = False):
    """默认只删知识库副本，原始文件保留；purge=true 才删原件（前端需二次确认）。"""
    with SessionLocal() as s:
        d = s.get(Document, doc_id)
        files = s.query(DocumentFile).filter(DocumentFile.doc_id == doc_id).all()
        if purge:
            for f in files:
                try:
                    Path(f.stored_path).unlink(missing_ok=True)
                except OSError:
                    pass
        # 登记一律删除：资料已删但登记还在，会让后续导入被误判“重复”而不启动解析
        s.query(DocumentFile).filter(DocumentFile.doc_id == doc_id).delete()
        s.query(Chunk).filter(Chunk.doc_id == doc_id).delete()
        s.query(NoteRow).filter(NoteRow.doc_id == doc_id).delete()
        s.query(DocumentFulltext).filter(DocumentFulltext.doc_id == doc_id).delete()
        s.query(ImportTaskRow).filter(ImportTaskRow.doc_id == doc_id).delete()
        s.query(Document).filter(Document.id == doc_id).delete()
        try:
            (PARSED_DIR / f"{doc_id}.txt").unlink(missing_ok=True)
        except OSError:
            pass
        s.commit()
    from .milvus_store import delete_document
    delete_document(doc_id)
    return {"ok": True, "originalKept": not purge}


@app.post("/api/docs/{doc_id}/reparse")
def reparse(doc_id: str):
    with SessionLocal() as s:
        row = s.query(DocumentFile).filter(
            DocumentFile.doc_id == doc_id, DocumentFile.kind == "original").first()
        d = s.get(Document, doc_id)
        if not row or not Path(row.stored_path).exists() or not d:
            return {"ok": False, "message": "找不到原始文件"}
        task = ImportTaskRow(
            id=uid("t"), name=d.title, format=d.format, source=d.source,
            size_kb=d.size_kb, progress=0, status="等待中", created_at=now_str(),
            file_path=row.stored_path, stage="", relative_path=row.relative_path,
            batch_id="", attempt=1,
        )
        s.add(task)
        s.commit()
        task_id = task.id
    threading.Thread(target=_reparse_worker, args=(task_id, doc_id), daemon=True).start()
    return {"ok": True, "taskId": task_id}


def _reparse_worker(task_id: str, doc_id: str):
    with SessionLocal() as s:
        t = s.get(ImportTaskRow, task_id)
        row = s.query(DocumentFile).filter(
            DocumentFile.doc_id == doc_id, DocumentFile.kind == "original").first()
        path = Path(row.stored_path) if row else None
    if not path or not path.exists():
        _update_task(task_id, status="失败", progress=100, message="原始文件不存在")
        return
    try:
        _stage(task_id, 2)
        pages, raw = parsers.parse_file(path, path.name)
        fulltext = parsers.full_text_from_pages(raw)
        chunks = parsers.split_into_chunks(raw)
        keywords = parsers.extract_keywords(chunks)
        _stage(task_id, 7)
        with SessionLocal() as s:
            d = s.get(Document, doc_id)
            full = s.get(DocumentFulltext, doc_id)
            if full:
                full.text, full.char_count, full.updated_at = fulltext, len(fulltext), now_str()
            else:
                s.add(DocumentFulltext(doc_id=doc_id, text=fulltext, char_count=len(fulltext), updated_at=now_str()))
            s.query(Chunk).filter(Chunk.doc_id == doc_id).delete()
            for i, (page, section, text) in enumerate(chunks, start=1):
                s.add(Chunk(id=f"{doc_id}-r{i}", doc_id=doc_id, page=page, section=section, text=text))
            if d:
                d.pages, d.status, d.keywords = pages, "已解析", keywords
                d.summary = parsers.extract_summary(chunks, get_raw_settings(s).get("autoSummary", True))
            (PARSED_DIR / f"{doc_id}.txt").write_text(fulltext, encoding="utf-8")
            s.commit()
        _update_task(task_id, status="已完成", progress=100, stage=STAGES[-1], doc_id=doc_id, message="重新解析完成")
    except Exception as exc:
        _update_task(task_id, status="失败", progress=100, message=f"解析失败：{exc}"[:160])


@app.get("/api/docs/{doc_id}/export")
def export_doc(doc_id: str):
    with SessionLocal() as s:
        d = s.get(Document, doc_id)
        if not d:
            return {"filename": "", "content": ""}
        chunks = s.query(Chunk).filter(Chunk.doc_id == doc_id).all()
    lines = [
        f"<!-- AIGC=1; provider=knowledge-web; content_id={d.id}; generated_at={datetime.now().isoformat()} -->",
        f"# {d.title}", "",
        "> 人工智能生成 / AI 生成：以下摘要由 AI 生成，内容仅供参考。", "",
        "## AI 摘要", d.summary or "", "",
        "## 标签", "、".join(d.tags or []) or "无", "",
        "## 检索切片",
    ]
    for c in chunks:
        lines += [f"### 第 {c.page} 页 · {c.section}", c.text, ""]
    lines += ["---", "文件元数据含 AIGC 标识；显式标识见本页首提示。"]
    return {"filename": f"{safe_name(d.title)}.md", "content": "\n".join(lines)}


# ---------------------------------------------------------------- 分类树 / 知识地图

RESERVED_CATEGORY = "未分类"


def _prune_empty_categories(session) -> int:
    """删除没有任何资料（含子孙资料）的空分类，保持分类树干净。常驻「未分类」不删。"""
    from collections import Counter
    cats = session.query(Category).all()
    counts = Counter(d[0] for d in session.query(Document.category).all())
    children_of: dict[str, list[Category]] = {}
    for c in cats:
        children_of.setdefault(c.parent_id or "", []).append(c)
    removed = 0
    changed = True
    while changed:
        changed = False
        for c in list(cats):
            if c.name == RESERVED_CATEGORY or c not in cats:
                continue
            if counts.get(c.name, 0) > 0:
                continue
            stack = [c.id]
            has_doc_descendant = False
            while stack:
                cid = stack.pop()
                for ch in children_of.get(cid, []):
                    if counts.get(ch.name, 0) > 0:
                        has_doc_descendant = True
                    stack.append(ch.id)
            if not has_doc_descendant:
                session.delete(c)
                cats.remove(c)
                removed += 1
                changed = True
    if removed:
        session.commit()
    return removed


def _ensure_uncategorized(session) -> None:
    """「未分类」为常驻分类：不存在则自动补建，排在分类树最前，不可改名/删除。"""
    exists = session.query(Category).filter(Category.name == RESERVED_CATEGORY).first()
    if not exists:
        session.add(Category(id=uid("cat"), name=RESERVED_CATEGORY, parent_id="",
                             order_index=-1, created_at=now_str()))
        session.commit()


@app.get("/api/categories")
def list_categories():
    with SessionLocal() as s:
        _ensure_uncategorized(s)
        cats = s.query(Category).order_by(Category.order_index, Category.id).all()
        counts = Counter(d[0] for d in s.query(Document.category).all())
        nodes = {c.id: {**category_to_dict(c), "count": counts.get(c.name, 0),
                        "directCount": counts.get(c.name, 0), "children": []} for c in cats}
        roots = []
        for c in cats:
            node = nodes[c.id]
            if c.parent_id and c.parent_id in nodes:
                nodes[c.parent_id]["children"].append(node)
            else:
                roots.append(node)

        def rollup(node):
            node["count"] += sum(rollup(ch) for ch in node["children"])
            return node["count"]

        for root in roots:
            rollup(root)
        return {"items": [category_to_dict(c) for c in cats], "tree": roots,
                "total": sum(counts.values())}


@app.post("/api/categories")
def add_category(body: dict):
    name = str(body.get("name", "")).strip()
    parent_id = str(body.get("parentId", "") or "")
    if not name:
        return {"ok": False, "message": "分类名不能为空"}
    if name == RESERVED_CATEGORY:
        return {"ok": False, "message": "「未分类」为常驻分类，无需新建"}
    with SessionLocal() as s:
        exists = s.query(Category).filter(Category.name == name, Category.parent_id == parent_id).first()
        if exists:
            return {"ok": False, "message": "同级下已有同名分类"}
        count = s.query(Category).count()
        c = Category(id=uid("cat"), name=name, parent_id=parent_id,
                     order_index=count, created_at=now_str())
        s.add(c)
        s.commit()
        return {"ok": True, "item": category_to_dict(c)}


@app.put("/api/categories/{cat_id}")
def rename_category(cat_id: str, body: dict):
    name = str(body.get("name", "")).strip()
    if not name:
        return {"ok": False, "message": "分类名不能为空"}
    if name == RESERVED_CATEGORY:
        return {"ok": False, "message": "已有「未分类」常驻分类，不可重名"}
    with SessionLocal() as s:
        c = s.get(Category, cat_id)
        if not c:
            return {"ok": False, "message": "分类不存在"}
        if c.name == RESERVED_CATEGORY:
            return {"ok": False, "message": "「未分类」为常驻分类，不可重命名"}
        old = c.name
        c.name = name
        for d in s.query(Document).filter(Document.category == old).all():
            d.category = name
        s.commit()
        return {"ok": True, "item": category_to_dict(c)}


@app.delete("/api/categories/{cat_id}")
def delete_category(cat_id: str):
    """删除分类及其全部子分类；分类下资料一并从系统删除（磁盘原始文件保留，可重新导入）。"""
    with SessionLocal() as s:
        c = s.get(Category, cat_id)
        if not c:
            return {"ok": False}
        if c.name == RESERVED_CATEGORY:
            return {"ok": False, "message": "「未分类」为常驻分类，不可删除"}
        cats = s.query(Category).all()
        children_of: dict[str, list[Category]] = {}
        for x in cats:
            children_of.setdefault(x.parent_id or "", []).append(x)
        doomed_ids = [cat_id]
        stack = [cat_id]
        while stack:
            cid = stack.pop()
            for ch in children_of.get(cid, []):
                doomed_ids.append(ch.id)
                stack.append(ch.id)
        doomed_names = {x.name for x in cats if x.id in set(doomed_ids)}
        removed = 0
        for d in s.query(Document).filter(Document.category.in_(doomed_names)).all():
            s.query(Chunk).filter(Chunk.doc_id == d.id).delete()
            s.query(NoteRow).filter(NoteRow.doc_id == d.id).delete()
            s.query(DocumentFulltext).filter(DocumentFulltext.doc_id == d.id).delete()
            s.query(ImportTaskRow).filter(ImportTaskRow.doc_id == d.id).delete()
            s.query(DocumentFile).filter(DocumentFile.doc_id == d.id).delete()
            try:
                (PARSED_DIR / f"{d.id}.txt").unlink(missing_ok=True)
            except OSError:
                pass
            s.query(Document).filter(Document.id == d.id).delete()
            removed += 1
        s.query(Category).filter(Category.id.in_(doomed_ids)).delete(synchronize_session=False)
        s.commit()
        return {"ok": True, "removedDocs": removed}


# ---------------------------------------------------------------- 导入

class LinkBody(BaseModel):
    url: str
    name: str = ""
    source: str = "网页链接"


@app.get("/api/imports")
def list_tasks():
    with SessionLocal() as s:
        return [task_to_dict(t) for t in s.query(ImportTaskRow).order_by(
            ImportTaskRow.created_at.desc(), ImportTaskRow.id.desc()).all()]


@app.delete("/api/imports")
def clear_tasks(scope: str = "finished"):
    with SessionLocal() as s:
        query = s.query(ImportTaskRow)
        if scope == "finished":
            query = query.filter(ImportTaskRow.status.in_(["已完成", "失败", "重复跳过"]))
        n = query.count()
        query.delete()
        s.commit()
        return {"ok": True, "removed": n}


@app.post("/api/imports/files")
async def upload_files(
    files: list[UploadFile] = File(...),
    source: str = Form("本地文件"),
    relative_paths: str = Form(""),
    batch_id: str = Form(""),
    only_documents: str = Form("true"),
    categories: str = Form(""),
):
    try:
        paths = json.loads(relative_paths) if relative_paths else []
    except json.JSONDecodeError:
        paths = []
    if len(paths) != len(files):
        paths = [f.filename or f"file-{i}" for i, f in enumerate(files)]
    try:
        cats = json.loads(categories) if categories else []
    except json.JSONDecodeError:
        cats = []
    if len(cats) != len(files):
        cats = [""] * len(files)
    batch = batch_id or uid("IMP_")
    root = UPLOAD_DIR / batch
    saved: list[tuple[str, Path, int, str]] = []
    for i, (f, rel) in enumerate(zip(files, paths)):
        rel = str(rel).replace("\\", "/").lstrip("/")
        parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
        rel = "/".join(parts) or (f.filename or "file")
        content = await f.read()
        if len(content) > MAX_UPLOAD:
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        saved.append((rel, target, len(content), str(cats[i] or "").strip()))

    # 找出被 Markdown 引用的图片，作为关联资源保存，不单独建资料
    referenced = set()
    for rel, path, _, _ in saved:
        if path.suffix.lower() in (".md", ".markdown"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for ref in parsers.extract_markdown_images(text):
                key = parsers.normalize_asset_ref(ref)
                referenced.add(key)
                referenced.add(Path(key).name)
                referenced.add(rel.rsplit("/", 1)[0] + "/" + key if "/" in rel else key)

    created = []
    for rel, path, size, cat in saved:
        suffix = path.suffix.lower()
        if suffix not in ALLOWED_SUFFIX:
            continue
        is_asset = suffix in IMAGE_SUFFIX and (
            rel in referenced or path.name in referenced or "assets/" in rel.lower()
        )
        if is_asset and only_documents.lower() != "false":
            continue
        digest = sha256_file(path)
        skip = False
        dup_title = ""
        with SessionLocal() as s:
            rows = s.query(DocumentFile).filter(
                DocumentFile.sha256 == digest, DocumentFile.kind == "original").all()
            alive = [r for r in rows if r.doc_id and s.get(Document, r.doc_id)]
            # 登记在但资料已删 = 孤儿登记，清掉并允许重新导入
            for r in rows:
                if r not in alive:
                    s.delete(r)
            s.commit()
            if alive:
                dup_doc = s.get(Document, alive[0].doc_id)
                dup_title = dup_doc.title if dup_doc else "已有资料"
                skip = True
            task = ImportTaskRow(
                id=uid("t"), name=path.name, format=parsers.guess_format(path.name),
                source=source, size_kb=max(1, size // 1024),
                progress=100 if skip else 0,
                status="重复跳过" if skip else "等待中",
                created_at=now_str(),
                message=f"与《{dup_title}》内容相同" if skip else "",
                file_path=str(path), stage="", relative_path=rel,
                batch_id=batch, category=cat, attempt=0,
            )
            s.add(task)
            s.commit()
            created.append(task_to_dict(task))
        if not skip:
            _submit_import(task.id)
    return created


# 智能助手附件专用格式：只做检测输入，不写入资料库 / 检索索引
ASSISTANT_UPLOAD_SUFFIX = {".pdf", ".docx", ".doc", ".txt", ".md", ".markdown", ".xlsx", ".xls"}
ASSISTANT_TEMP_TTL_HOURS = 24  # 过期临时附件在下次上传时顺带清理


@app.post("/api/assistant/uploads")
async def upload_assistant_files(
    files: list[UploadFile] = File(...),
    session_id: str = Form(""),
):
    """智能助手回形针上传：文件只落在临时区，绝不进入资料库。

    与 /api/imports/files 完全隔离：不建 ImportTask、不建 Document、
    不解析切片、不写全文，因此资料库列表与知识检索都看不到这些文件。
    返回的 id 为 tmp-xxx，随问题文本传给 detect_conflict 工具读取原始字节。
    """
    # 借每次上传顺带回收过期临时文件，避免无限堆积
    purge_assistant_uploads(max_age_hours=ASSISTANT_TEMP_TTL_HOURS)
    created = []
    for f in files:
        original = f.filename or "未命名文件"
        suffix = Path(original).suffix.lower()
        if suffix not in ASSISTANT_UPLOAD_SUFFIX:
            continue
        content = await f.read()
        if not content or len(content) > MAX_UPLOAD:
            continue
        upload_id = "tmp-" + uuid.uuid4().hex[:12]
        name = safe_name(original)
        stored = TEMP_UPLOAD_DIR / f"{upload_id}_{name}"
        stored.write_bytes(content)
        with SessionLocal() as s:
            row = AssistantUpload(
                id=upload_id,
                session_id=session_id or "",
                filename=original,
                stored_path=str(stored),
                size_bytes=len(content),
                created_at=now_str(),
            )
            s.add(row)
            s.commit()
        created.append({"id": upload_id, "name": original, "filename": original,
                        "sizeKb": max(1, len(content) // 1024)})
    return created


@app.post("/api/imports/links")
def import_link(body: LinkBody):
    url = body.url.strip()
    task_id = uid("t")
    batch = uid("IMP_")
    zhihu = webfetch.is_zhihu(url)
    fmt = "MD" if zhihu else "网页"
    with SessionLocal() as s:
        cookie = str(get_raw_settings(s).get("zhihuCookie") or "")
    fallback = safe_name(body.name or url.split("/")[-1] or "网页")[:80]
    path = UPLOAD_DIR / batch / f"{fallback}{'.md' if zhihu else '.html'}"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if zhihu:
            fetched = webfetch.fetch_zhihu(url, cookie)
            title = safe_name(fetched.title)[:80] or fallback
            path = UPLOAD_DIR / batch / f"{title}.md"
            path.write_text(fetched.markdown, encoding="utf-8")
        else:
            resp = httpx.get(url, timeout=15, follow_redirects=True,
                             headers={"User-Agent": webfetch.UA, "Accept-Language": "zh-CN,zh;q=0.9"})
            resp.raise_for_status()
            path.write_text(resp.text[:2_000_000], encoding="utf-8", errors="ignore")
    except Exception as exc:
        message = str(exc).strip() or type(exc).__name__
        if not zhihu:
            message = f"抓取失败：{type(exc).__name__} {message}"
        with SessionLocal() as s:
            t = ImportTaskRow(id=task_id, name=fallback, format=fmt, source=body.source,
                              size_kb=1, progress=100, status="失败", created_at=now_str(),
                              message=message[:160], stage="", batch_id=batch, url=url)
            s.add(t)
            s.commit()
            return task_to_dict(t)
    with SessionLocal() as s:
        t = ImportTaskRow(id=task_id, name=path.name, format=fmt, source=body.source,
                          size_kb=max(1, path.stat().st_size // 1024), progress=0,
                          status="等待中", created_at=now_str(), file_path=str(path),
                          stage="", relative_path=path.name, batch_id=batch, url=url, attempt=0)
        s.add(t)
        s.commit()
        result = task_to_dict(t)
    _submit_import(task_id)
    return result


@app.post("/api/imports/{task_id}/retry")
def retry_task(task_id: str):
    with SessionLocal() as s:
        t = s.get(ImportTaskRow, task_id)
        if not t:
            return {"ok": False}
        t.progress, t.status, t.stage, t.message = 0, "等待中", "", ""
        t.attempt = (t.attempt or 0) + 1
        s.commit()
    _submit_import(task_id)
    return {"ok": True}


@app.post("/api/imports/{task_id}/reparse")
def reparse_task(task_id: str):
    with SessionLocal() as s:
        t = s.get(ImportTaskRow, task_id)
        if not t or not t.doc_id:
            return {"ok": False, "message": "该任务没有关联资料"}
        return reparse(t.doc_id)


# ---------------------------------------------------------------- 智能体工具

@app.get("/api/agent/tools")
def list_agent_tools():
    return {"items": agent_tools.list_tools()}


# ---------------------------------------------------------------- 历史审计问题定性库

class AuditIssueImportBody(BaseModel):
    files: list[str]  # xlsx 绝对路径，必须由后端进程直接读取
    replace: bool = True


class AuditIssueSearchBody(BaseModel):
    query: str
    limit: int = 10


@app.post("/api/audit-issues/import")
def import_audit_issues(body: AuditIssueImportBody):
    """进程内导入历史审计问题：解析 xlsx → 写 AuditIssue → 写独立 Milvus collection。

    仅 local 模式开放（生产 SSO 模式禁止从服务端读取本机文件路径）。
    Milvus Lite 单进程独占锁，必须由运行中的后端进程触发，不要用外部脚本直连。
    """
    from . import audit_issues
    if settings.is_sso:
        raise BadRequestError("历史问题导入仅在本机 local 模式可用")
    return audit_issues.import_from_paths(body.files, replace=body.replace)


@app.get("/api/audit-issues/stats")
def audit_issues_stats():
    """历史问题库入库统计，便于核对解析数量与向量数量。"""
    from . import audit_issues
    return audit_issues.stats()


@app.post("/api/audit-issues/search")
def search_audit_issues(body: AuditIssueSearchBody):
    """历史审计问题语义+词法检索，返回结构化问题条目；不构成违规定性。"""
    from . import audit_issues
    return audit_issues.search(body.query, limit=max(1, min(body.limit, 50)))


@app.get("/api/audit-issues/list")
def list_audit_issues(
    page: int = 1,
    size: int = 20,
    year: str = "",
    category: str = "",
    subject: str = "",
    q: str = "",
):
    """历史问题库分页浏览：需在 /{issue_id} 之前注册，避免 list 被当成主键。"""
    from . import audit_issues
    return audit_issues.list_issues(
        page=page, size=max(1, min(size, 100)),
        year=year, category=category, subject=subject, q=q,
    )


@app.get("/api/audit-issues/{issue_id}")
def get_audit_issue(issue_id: str):
    """历史问题详情；找不到时返回空对象，便于前端统一处理。"""
    from . import audit_issues
    return audit_issues.get_issue(issue_id) or {}


# ---------------------------------------------------------------- 检索 / 问答 / 笔记（保留原接口）

@app.get("/api/search")
def search(q: str = "", mode: str = "混合"):
    return [{k: v for k, v in h.items() if k != "text"} for h in search_hits(q, mode)]


class AskBody(BaseModel):
    question: str
    mode: str = "混合"
    sessionId: str = ""
    module: str = "assistant"


_META_KEYS = ("route", "actions", "rewrittenQuery", "subQueries", "usedLLM",
              "retrievalCount", "retried", "agentRunId", "agentSteps", "nativeTools",
              "conflict", "contract", "workpaper", "status", "files")


def _file_names_from_question(question: str) -> list[str]:
    """从问题里的「待检测的文档 ID：xxx、yyy」反查原始文件名，供历史恢复展示附件标签。"""
    m = re.search(r"待检测的文档\s*ID[：:]\s*([^\n]+)", question or "")
    if not m:
        return []
    ids = [x.strip() for x in re.split(r"[、,，\s]+", m.group(1)) if x.strip()]
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


def _persist_qa(result: dict, session_id: str, qa_id: str = "") -> str:
    """问答入库 + 挂到会话。被同步问答与智能体流共用。

    传 qa_id 时更新已有占位记录（智能体流开始时先落库一条 running 占位），
    否则新建。这样长任务进行中/切页面/刷新后都能从会话历史读到状态。
    """
    meta = {k: result.get(k) for k in _META_KEYS}
    if meta.get("files") is None:
        meta["files"] = _file_names_from_question(result["question"])
    if not meta.get("conflict"):
        for action in result.get("actions") or []:
            detail = (action.get("detail") or {}).get("result") or {}
            if action.get("tool") == "get_conflict_result" and detail.get("conflicts") is not None:
                meta["conflict"] = {
                    "taskId": detail.get("taskId", ""),
                    "conflictCount": detail.get("conflictCount", 0),
                    "conflicts": detail.get("conflicts", []),
                }
            elif action.get("tool") == "detect_conflict" and detail.get("taskId") and not meta.get("conflict"):
                meta["conflict"] = {
                    "taskId": detail.get("taskId", ""),
                    "conflictCount": detail.get("conflictCount", 0),
                    "files": detail.get("files", 0), "units": detail.get("units", 0),
                    "clauses": detail.get("clauses", 0), "judgedPairs": detail.get("judgedPairs", 0),
                    "topics": detail.get("topics", []), "conflicts": [],
                }
    if not meta.get("contract"):
        for action in result.get("actions") or []:
            detail = (action.get("detail") or {}).get("result") or {}
            if action.get("tool") == "get_contract_result" and detail.get("issues") is not None:
                meta["contract"] = {
                    "taskId": detail.get("taskId", ""),
                    "contractType": detail.get("contractType", ""),
                    "mainContract": detail.get("mainContract", ""),
                    "totalIssues": detail.get("totalIssues", 0),
                    "levelCount": detail.get("levelCount", {}),
                    "issues": detail.get("issues", []),
                    "reconciliation": detail.get("reconciliation", []),
                    "disclaimer": detail.get("disclaimer", ""),
                }
            elif action.get("tool") == "audit_contract" and detail.get("taskId") and not meta.get("contract"):
                meta["contract"] = {
                    "taskId": detail.get("taskId", ""),
                    "contractType": detail.get("contractType", ""),
                    "mainContract": detail.get("mainContract", ""),
                    "totalIssues": detail.get("totalIssues", 0),
                    "issues": [], "reconciliation": [],
                }
    if not meta.get("workpaper"):
        for action in result.get("actions") or []:
            detail = (action.get("detail") or {}).get("result") or {}
            if action.get("tool") == "get_workpaper_result" and detail.get("issues") is not None:
                # 整包透传工具结果：前端卡片需要 items/summary，字段白名单会漏掉新结构。
                meta["workpaper"] = dict(detail)
            elif action.get("tool") == "audit_workpaper" and detail.get("taskId") and not meta.get("workpaper"):
                meta["workpaper"] = dict(detail)
    stored_answer = {**result["answer"], "_meta": meta}
    with SessionLocal() as s:
        row = s.get(QARow, qa_id) if qa_id else None
        if row is None:
            row = QARow(id=qa_id or uid("q"), question=result["question"], answer=stored_answer,
                        citations=result["citations"], created_at=result["createdAt"],
                        recall_mode=result["recallMode"], feedback="")
            s.add(row)
        else:
            row.question = result["question"]
            row.answer = stored_answer
            row.citations = result["citations"]
            row.created_at = result["createdAt"]
            row.recall_mode = result["recallMode"]
        session = s.get(QASession, session_id) if session_id else None
        if session:
            linked = s.query(QASessionItem).filter(QASessionItem.session_id == session.id,
                                                    QASessionItem.qa_id == row.id).first()
            if not linked:
                seq = s.query(QASessionItem).filter(QASessionItem.session_id == session.id).count()
                s.add(QASessionItem(id=uid("qi"), session_id=session.id, qa_id=row.id, seq=seq))
                if seq == 0 and not session.title:
                    session.title = result["question"][:24] or "新会话"
            session.updated_at = result["createdAt"]
        s.commit()
        return row.id


def _persist_placeholder(question: str, session_id: str, run_id: str) -> str:
    """智能体流开跑前先落一条 running 占位记录并挂到会话，返回 qa_id。"""
    created_at = now_str()
    qa_id = uid("q")
    answer = {
        "conclusion": "正在分析，完成后结果会显示在这里…",
        "basis": [],
        "_meta": {"route": "agent", "status": "running", "agentRunId": run_id,
                  "files": _file_names_from_question(question)},
    }
    with SessionLocal() as s:
        s.add(QARow(id=qa_id, question=question, answer=answer,
                    citations=[], created_at=created_at, recall_mode="混合", feedback=""))
        session = s.get(QASession, session_id) if session_id else None
        if session:
            seq = s.query(QASessionItem).filter(QASessionItem.session_id == session.id).count()
            s.add(QASessionItem(id=uid("qi"), session_id=session.id, qa_id=qa_id, seq=seq))
            if seq == 0 and not session.title:
                session.title = question[:24] or "新会话"
            session.updated_at = created_at
        s.commit()
    return qa_id


def _finalize_placeholder(qa_id: str, status: str, message: str) -> None:
    """把占位记录更新为终止态，避免切页面/刷新后永久停在 running。"""
    if not qa_id:
        return
    with SessionLocal() as s:
        row = s.get(QARow, qa_id)
        if not row or not isinstance(row.answer, dict):
            return
        answer = dict(row.answer)
        meta = dict(answer.get("_meta") or {})
        meta["status"] = status
        answer["_meta"] = meta
        if status == "stopped":
            answer["conclusion"] = "已停止，未生成完整回答。"
        elif status == "error":
            answer["conclusion"] = f"运行失败：{message}"
        answer.setdefault("basis", [])
        row.answer = answer
        s.commit()


@app.post("/api/qa/ask")
def ask_question(body: AskBody):
    result = answer_question(body.question.strip(), body.mode)
    result["id"] = _persist_qa(result, body.sessionId)
    return result


# ---------------------------------------------------------------- 智能体流（P2）

class ConfirmBody(BaseModel):
    decision: str  # approve / reject


def _emit_progress(task_ids: list[str], emit) -> None:
    """轮询后台任务并推 progress 事件直到完成或 30 秒上限。

    同时识别两类任务：
    - 导入任务（ImportTaskRow）：导入资料入口产生；
    - 互斥检测任务（ConflictTask）：detect_conflict 工具产生，id 形如 a2d...。
    两者状态字段不同，需分别映射，否则检测任务会被误报成「后台处理中 0%」。
    """
    from .modules.conflict.models import ConflictTask
    from .modules.contract.models import ContractTask
    from .modules.workpaper.models import WorkpaperTask

    pending = {t for t in task_ids if t}
    if not pending:
        return
    started = time.time()
    while pending and time.time() - started < 30:
        with SessionLocal() as s:
            import_rows = s.query(ImportTaskRow).filter(ImportTaskRow.id.in_(list(pending))).all()
            conflict_rows = s.query(ConflictTask).filter(ConflictTask.id.in_(list(pending))).all()
            contract_rows = s.query(ContractTask).filter(ContractTask.id.in_(list(pending))).all()
            workpaper_rows = s.query(WorkpaperTask).filter(WorkpaperTask.id.in_(list(pending))).all()
            snapshot = [{"id": r.id, "name": r.name, "status": r.status,
                         "progress": r.progress, "message": r.message, "kind": "import"}
                        for r in import_rows]
            for r in conflict_rows:
                done = r.status == "done"
                snapshot.append({
                    "id": r.id, "name": r.name,
                    "status": "已完成" if done else (r.stage or "处理中"),
                    "progress": 100 if done else 0,
                    "message": (f"完成 · 检出 {r.conflict_count} 处互斥" if done
                                else (r.error or "检测进行中")),
                    "kind": "conflict",
                })
            for r in contract_rows:
                done = r.status == "done"
                snapshot.append({
                    "id": r.id, "name": r.name,
                    "status": "已完成" if done else (r.stage or "处理中"),
                    "progress": 100 if done else 0,
                    "message": (f"完成 · 发现 {r.issue_count} 项待关注" if done
                                else (r.error or "审计进行中")),
                    "kind": "contract",
                })
            for r in workpaper_rows:
                done = r.status == "done"
                snapshot.append({
                    "id": r.id, "name": r.name,
                    "status": "已完成" if done else (r.stage or "处理中"),
                    "progress": 100 if done else 0,
                    "message": (f"完成 · 发现 {r.issue_count} 项待关注" if done
                                else (r.error or "审核进行中")),
                    "kind": "workpaper",
                })
        for item in snapshot:
            emit({"type": "progress", "taskId": item["id"], "taskName": item["name"],
                  "status": item["status"], "progress": item["progress"],
                  "message": item["message"]})
            if item["status"] in ("已完成", "失败", "重复跳过"):
                pending.discard(item["id"])
        if pending:
            time.sleep(1.5)
    for tid in pending:
        emit({"type": "progress", "taskId": tid, "status": "后台处理中", "progress": 0,
              "message": "仍在处理，可在任务记录里查看"})


@app.post("/api/qa/agent/stream")
def agent_stream(body: AskBody):
    """SSE：每完成一次工具调用推一条 step，写动作先推 confirm 挂起等确认，结束时推 done。"""
    run_id = uid("r")
    events: queue.Queue = queue.Queue()
    task_ids: list[str] = []
    agent_run.request_cancel(run_id)  # 先建取消标志，保证停止一定生效

    def emit(evt: dict) -> None:
        events.put(evt)

    def on_action(action: dict) -> None:
        data = (action.get("detail") or {}).get("result") or {}
        if isinstance(data, dict):
            tid = data.get("taskId")
            if tid and tid not in task_ids:
                task_ids.append(tid)
            for t in data.get("taskIds") or []:
                if t not in task_ids:
                    task_ids.append(t)
        emit({"type": "step", "runId": run_id, "action": action})

    def worker() -> None:
        ptoken = agent_tools.bind_progress(lambda p: emit({**p, "runId": run_id}))
        qa_id = ""
        try:
            # 开跑前先落 running 占位并挂会话，切页面/刷新后也能读到这条
            qa_id = _persist_placeholder(body.question.strip(), body.sessionId, run_id)
            emit({"type": "start", "runId": run_id, "qaId": qa_id})
            result = agent_run.run_agent(
                body.question.strip(), body.mode, run_id, body.sessionId,
                module=body.module,
                on_step=on_action,
                on_confirm=lambda payload: emit({"type": "confirm", "runId": run_id, **payload}),
                on_answer_delta=lambda text: emit({
                    "type": "answer_delta", "runId": run_id, "qaId": qa_id, "text": text,
                }),
            )
            # 用户中途停止：更新占位为 stopped，不给完整答案
            if agent_run.is_cancelled(run_id):
                _finalize_placeholder(qa_id, "stopped", "")
                emit({"type": "stopped", "runId": run_id, "qaId": qa_id})
                return
            _emit_progress(task_ids, emit)
            emit({"type": "status", "runId": run_id, "steps": result.get("agentSteps", 0)})
            result["status"] = "done"
            result["id"] = _persist_qa(result, body.sessionId, qa_id=qa_id)
            emit({"type": "done", "runId": run_id, "result": result})
        except Exception as exc:
            msg = f"智能体运行失败：{type(exc).__name__}"
            _finalize_placeholder(qa_id, "error", msg)
            emit({"type": "error", "runId": run_id, "message": msg, "qaId": qa_id})
        finally:
            agent_tools.reset_progress(ptoken)
            agent_run.clear_cancel(run_id)
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            evt = events.get()
            if evt is None:
                break
            yield "data: " + json.dumps(evt, ensure_ascii=False) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/qa/agent/{run_id}/stop")
def stop_agent(run_id: str):
    return {"ok": agent_run.cancel(run_id)}


@app.post("/api/qa/agent/{confirm_id}/decide")
def decide_agent_confirm(confirm_id: str, body: ConfirmBody):
    return {"ok": agent_run.decide_confirm(confirm_id, body.decision)}


@app.get("/api/qa/agent/capability")
def agent_capability():
    return {"hasLlm": agent_run.has_llm(), "nativeToolCalls": agent_run.probe_tool_calls()}


@app.get("/api/qa/agent/runs")
def list_agent_runs():
    with SessionLocal() as s:
        rows = s.query(AgentRun).order_by(AgentRun.created_at.desc()).limit(30).all()
        return [{
            "id": r.id, "sessionId": r.session_id, "question": r.question,
            "status": r.status, "stepsCount": r.steps_count, "costMs": r.cost_ms,
            "createdAt": r.created_at, "finishedAt": r.finished_at,
        } for r in rows]


@app.get("/api/qa/agent/runs/{run_id}/steps")
def list_agent_steps(run_id: str):
    with SessionLocal() as s:
        rows = (s.query(AgentStep).filter(AgentStep.run_id == run_id)
                .order_by(AgentStep.seq, AgentStep.id).all())
        return [{
            "seq": r.seq, "kind": r.kind, "tool": r.tool_name,
            "args": r.args_json or {}, "result": r.result_json or {},
            "costMs": r.cost_ms,
        } for r in rows]


# ---------------------------------------------------------------- 检索评测（P4）

class EvalBuildBody(BaseModel):
    count: int = 8
    category: str = ""


class EvalRunBody(BaseModel):
    k: int = 5
    mode: str = "混合"


@app.post("/api/qa/eval/build")
def build_eval(body: EvalBuildBody):
    return evaluation.build_eval_set(body.count, body.category)


@app.post("/api/qa/eval/run")
def run_eval(body: EvalRunBody):
    return evaluation.run_eval(body.k, body.mode)


@app.get("/api/qa/eval/queries")
def eval_queries():
    return {"items": evaluation.list_queries(), "total": evaluation.total_queries()}


@app.delete("/api/qa/eval/queries")
def clear_eval_queries():
    return {"ok": True, "removed": evaluation.clear_queries()}


@app.get("/api/qa/eval/runs")
def eval_runs():
    return evaluation.list_runs()


@app.get("/api/qa/sessions")
def list_sessions(module: str = ""):
    with SessionLocal() as s:
        out = []
        query = s.query(QASession)
        if module:
            query = query.filter(QASession.module == module)
        for session in query.order_by(QASession.updated_at.desc()).all():
            items = (s.query(QASessionItem).filter(QASessionItem.session_id == session.id)
                     .order_by(QASessionItem.seq).all())
            last = ""
            if items:
                q = s.get(QARow, items[-1].qa_id)
                last = q.question if q else ""
            out.append(session_to_dict(session, len(items), last))
        return out


class SessionBody(BaseModel):
    module: str = "assistant"


@app.post("/api/qa/sessions")
def create_session(body: SessionBody | None = None):
    module = (body.module if body else "assistant") or "assistant"
    with SessionLocal() as s:
        now = now_str()
        session = QASession(id=uid("s"), title="", created_at=now, updated_at=now, module=module)
        s.add(session)
        s.commit()
        return session_to_dict(session)


@app.delete("/api/qa/sessions/{session_id}")
def delete_session(session_id: str):
    # 会话删除时一并回收该会话的助手临时附件（报告仍留在 ConflictTask 中）
    purge_assistant_uploads(session_id=session_id)
    with SessionLocal() as s:
        s.query(QASessionItem).filter(QASessionItem.session_id == session_id).delete()
        deleted = s.query(QASession).filter(QASession.id == session_id).delete()
        s.commit()
        return {"ok": deleted > 0}


@app.get("/api/qa/sessions/{session_id}")
def get_session(session_id: str):
    with SessionLocal() as s:
        items = (s.query(QASessionItem).filter(QASessionItem.session_id == session_id)
                 .order_by(QASessionItem.seq).all())
        return [qa_to_dict(q) for q in
                (s.get(QARow, i.qa_id) for i in items) if q]


@app.get("/api/qa")
def list_qa():
    with SessionLocal() as s:
        return [qa_to_dict(q) for q in s.query(QARow).order_by(QARow.created_at.desc()).all()]


@app.post("/api/qa/{qa_id}/feedback")
def set_feedback(qa_id: str, body: dict):
    with SessionLocal() as s:
        q = s.get(QARow, qa_id)
        if q:
            q.feedback = str(body.get("feedback", ""))
            s.commit()
    return {"ok": True}


class NoteBody(BaseModel):
    docId: str
    docTitle: str = ""
    chunkId: str = ""
    page: int | None = None
    content: str


@app.get("/api/notes")
def list_notes():
    with SessionLocal() as s:
        return [note_to_dict(n) for n in s.query(NoteRow).order_by(NoteRow.created_at.desc()).all()]


@app.post("/api/notes")
def add_note(body: NoteBody):
    with SessionLocal() as s:
        n = NoteRow(id=uid("n"), doc_id=body.docId, doc_title=body.docTitle,
                    chunk_id=body.chunkId, page=body.page, content=body.content.strip(),
                    created_at=now_str())
        s.add(n)
        s.commit()
        return note_to_dict(n)


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: str):
    with SessionLocal() as s:
        s.query(NoteRow).filter(NoteRow.id == note_id).delete()
        s.commit()
    return {"ok": True}


# ---------------------------------------------------------------- 设置 / 存储

@app.get("/api/settings")
def read_settings():
    with SessionLocal() as s:
        return get_settings(s)


@app.post("/api/settings")
def update_settings(patch: dict):
    if isinstance(patch.get("zhihuCookie"), str):
        patch = {**patch, "zhihuCookie": webfetch.normalize_cookie(patch["zhihuCookie"])}
    with SessionLocal() as s:
        return save_settings(s, patch)


@app.post("/api/settings/test-model")
def test_model(body: dict):
    with SessionLocal() as s:
        cfg = llm_config(s)
    # 表单里尚未保存的临时值优先，方便「先测试连接、再保存设置」
    base = normalize_base_url(str(body.get("baseUrl") or cfg.base))
    key = str(body.get("apiKey") or "").strip() or cfg.key
    model = str(body.get("model") or "").strip() or cfg.model
    probe = LLMConfig(base, key, model, cfg.temperature, cfg.max_tokens, False)
    if not probe.ready:
        return {"ok": False, "message": "请先填写 API Key（地址与模型已有官方默认值）"}
    started = time.time()
    try:
        data = post_chat(
            {"model": model,
             "messages": [{"role": "user", "content": "回复 OK 两个字母"}],
             "max_tokens": 16, "temperature": 0},
            timeout=15, config=probe, retries=0,
        )
        choices = data.get("choices") or [{}]
        content = (choices[0].get("message", {}).get("content") or "").strip()
        if not content:
            return {"ok": False, "message": "连通但模型未返回内容，请检查模型名称、额度或代理配置"}
        return {"ok": True,
                "message": f"{model} 连接成功（{int((time.time() - started) * 1000)} ms）"}
    except Exception as exc:
        detail = str(exc)
        if "HTTP 401" in detail or "HTTP 403" in detail:
            detail = "API Key 无效或没有权限，请检查 platform.deepseek.com 上的密钥"
        elif "HTTP 402" in detail:
            detail = "账户余额不足，请到 DeepSeek 平台充值"
        elif "HTTP 429" in detail:
            detail = "请求过于频繁，请稍后重试"
        return {"ok": False, "message": f"连接失败：{detail[:160]}"}


def _db_file() -> Path:
    return Path(engine.url.database or DATA_DIR / "knowledge.db")


def _dir_size(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    files = [p for p in path.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


@app.get("/api/storage")
def storage_info():
    with SessionLocal() as s:
        st = get_raw_settings(s)
    originals = Path(st.get("originalsDir") or UPLOAD_DIR)
    parsed = Path(st.get("parsedDir") or PARSED_DIR)
    db = _db_file()
    n1, b1 = _dir_size(originals)
    n2, b2 = _dir_size(parsed)
    return {
        "originalsDir": str(originals), "parsedDir": str(parsed), "databasePath": str(db),
        "originalFiles": n1, "originalMb": round(b1 / 1024 / 1024, 2),
        "parsedMb": round(b2 / 1024 / 1024, 2),
        "databaseMb": round(db.stat().st_size / 1024 / 1024, 2) if db.exists() else 0,
        "totalMb": round((b1 + b2 + (db.stat().st_size if db.exists() else 0)) / 1024 / 1024, 2),
    }


@app.post("/api/storage/location")
def change_storage_location(body: dict):
    """复制而非移动原件，并同步更新路径记录，已有资料始终可访问。"""
    raw = str(body.get("originalsDir", "")).strip()
    if not raw:
        return {"ok": False, "message": "路径不能为空"}
    target = Path(raw)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "message": f"目录不可用：{exc}"}
    with SessionLocal() as s:
        st = get_raw_settings(s)
        current = Path(st.get("originalsDir") or UPLOAD_DIR).resolve()
        if target.resolve() == current:
            return {"ok": True, "moved": 0, "keptOriginals": True, "message": "位置未变化"}
        copied = 0
        for row in s.query(DocumentFile).filter(DocumentFile.kind == "original").all():
            src = Path(row.stored_path)
            if not src.exists():
                continue
            dest = target / src.parent.name / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                shutil.copyfile(src, dest)
            row.stored_path = str(dest)
            doc = s.get(Document, row.doc_id)
            if doc:
                doc.file_path = str(dest)
            copied += 1
        save_settings(s, {"originalsDir": str(target)})
        return {"ok": True, "moved": copied, "keptOriginals": True,
                "message": f"已复制 {copied} 个原始文件到新位置，原位置文件保留"}


@app.get("/api/storage/backups")
def list_backups():
    files = sorted(BACKUP_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "sizeMb": round(p.stat().st_size / 1024 / 1024, 2),
             "createdAt": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}
            for p in files]


@app.post("/api/storage/backup")
def create_backup():
    db = _db_file()
    target = BACKUP_DIR / f"knowledge-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(db)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return {"ok": True, "name": target.name, "sizeMb": round(target.stat().st_size / 1024 / 1024, 2)}


@app.post("/api/storage/restore")
def restore_backup(body: dict):
    name = safe_name(str(body.get("name", "")))
    source = BACKUP_DIR / name
    if not source.exists():
        return {"ok": False, "message": "备份文件不存在"}
    db = _db_file()
    engine.dispose()
    shutil.copyfile(source, db)
    engine.dispose()
    return {"ok": True, "message": "已恢复，建议重启后端以确保所有连接刷新"}


@app.get("/api/quota")
def quota():
    with SessionLocal() as s:
        docs = s.query(Document).count()
        chunks = s.query(Chunk).count()
        parsed = s.query(Document).filter(Document.status == "已解析").count()
        pending = s.query(Document).filter(Document.status.in_(["解析中", "待解析"])).count()
        total_qa = s.query(QARow).count()
        with_citation = sum(1 for q in s.query(QARow).all() if q.citations)
    size = sum(f.stat().st_size for f in UPLOAD_DIR.rglob("*") if f.is_file())
    if _db_file().exists():
        size += _db_file().stat().st_size
    return {
        "docs": docs, "chunks": chunks, "queriesThisMonth": 0, "queryLimit": 500,
        "storageMb": round(size / 1024 / 1024, 2), "storageLimitMb": 200,
        "parseSuccessRate": round(parsed / max(1, docs - pending) * 100, 1),
        "citationAccuracy": round(with_citation / max(1, total_qa) * 100, 1),
    }


# ---------------------------------------------------------------- 一键登录知乎（纯增量，不动现有链路）

@app.post("/api/zhihu/login/start")
def zhihu_login_start():
    return zhihu_login.start()


@app.get("/api/zhihu/login/status")
def zhihu_login_status():
    return zhihu_login.status()


@app.post("/api/zhihu/login/cancel")
def zhihu_login_cancel():
    return zhihu_login.cancel()


# ---------------------------------------------------------------- 智能体写动作 hook
# 复用已验证的路由实现，避免工具层重复业务逻辑或反向依赖控制器

agent_tools.register_hook("reparse_doc", lambda a: reparse(a["docId"]))
agent_tools.register_hook("retry_task", lambda a: retry_task(a["taskId"]))
agent_tools.register_hook("submit_import", lambda a: _submit_import(a["taskId"]))
agent_tools.register_hook("import_link",
                         lambda a: import_link(LinkBody(url=a["url"], name=a.get("name", ""))))
agent_tools.register_hook("export_doc", lambda a: export_doc(a["docId"]))
agent_tools.register_hook("delete_doc", lambda a: delete_doc(a["docId"], bool(a.get("purge", False))))
agent_tools.register_hook("delete_category", lambda a: delete_category(a["catId"]))
agent_tools.register_hook("detect_conflict", lambda a: _conflict_agent().detect(a))
agent_tools.register_hook("get_conflict_result", lambda a: _conflict_agent().get_result(a))
agent_tools.register_hook("audit_contract", lambda a: _contract_agent().detect(a))
agent_tools.register_hook("get_contract_result", lambda a: _contract_agent().get_result(a))
agent_tools.register_hook("audit_workpaper", lambda a: _workpaper_agent().detect(a))
agent_tools.register_hook("get_workpaper_result", lambda a: _workpaper_agent().get_result(a))


def _conflict_agent():
    from .modules.conflict import agent_tool
    return agent_tool


def _contract_agent():
    from .modules.contract import agent_tool
    return agent_tool


def _workpaper_agent():
    from .modules.workpaper import agent_tool
    return agent_tool
