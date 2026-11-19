# -*- coding: utf-8 -*-
"""异步数据库层：async engine、会话依赖、schema 隔离。

现有 OCR / PDF 等阻塞任务仍使用 db.py 的同步会话；新增 IO 代码一律使用本模块的
AsyncSession，两条路径在 PostgreSQL 模式下通过 search_path 固定到同一 schema。
"""
from __future__ import annotations

import logging
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .db import Base  # 全项目唯一 Base，确保 Alembic 能发现所有业务模型

logger = logging.getLogger("app")

engine = None
AsyncSessionLocal = None

if settings.is_postgres:
    engine = create_async_engine(
        settings.database_uri,
        echo=False,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=120,
        connect_args={"server_settings": {"search_path": settings.DB_SCHEMA}},
    )
    AsyncSessionLocal = async_sessionmaker(
        class_=AsyncSession,
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

async def ensure_schema() -> None:
    if engine is None:
        return
    async with engine.connect() as conn:
        await conn.execute(
            text(
                f'CREATE SCHEMA IF NOT EXISTS "{settings.DB_SCHEMA}" '
                f'AUTHORIZATION "{settings.DB_USER}"'
            )
        )
        await conn.commit()
    logger.info("schema [%s] 已就绪", settings.DB_SCHEMA)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入：session: AsyncSession = Depends(get_db)。"""
    if AsyncSessionLocal is None:
        raise RuntimeError("异步会话仅在 DB_BACKEND=postgres 时可用")
    async with AsyncSessionLocal() as session:
        yield session
