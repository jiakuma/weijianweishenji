# -*- coding: utf-8 -*-
"""Alembic 迁移环境：PostgreSQL 15 + 应用专属 schema。

所有表与 alembic_version 均落在 DB_SCHEMA 下，不使用 public。
SQLite 降级路径不经过 Alembic。
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.db import Base  # 核心表
from app.modules import discover_modules

# 新增业务模块后，在此追加模型导入，否则 autogenerate 无法发现新表
discover_modules()
from app.modules.conflict import models  # noqa: E402,F401
from app.modules.contract import models  # noqa: E402,F401
from app.modules.workpaper import models  # noqa: E402,F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

if not settings.is_postgres:
    raise RuntimeError("Alembic 仅支持 DB_BACKEND=postgres；本地降级路径由 db.init_db() 建表")

config.set_main_option("sqlalchemy.url", settings.database_uri)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=settings.DB_SCHEMA,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    connection.execute(text(f'SET search_path TO "{settings.DB_SCHEMA}"'))
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=settings.DB_SCHEMA,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"server_settings": {"search_path": settings.DB_SCHEMA}},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
