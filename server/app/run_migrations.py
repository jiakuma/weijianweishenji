# -*- coding: utf-8 -*-
"""数据库结构只由 Alembic 管理：启动时 upgrade head。"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from .config import BASE_DIR, settings

logger = logging.getLogger("app")

ALEMBIC_INI = BASE_DIR / "alembic.ini"


def ensure_schema_sync() -> None:
    """PostgreSQL 模式下确保应用 schema 存在（先于迁移执行）。"""
    if not settings.is_postgres:
        return
    from .db import engine

    with engine.begin() as conn:
        conn.execute(
            text(
                f'CREATE SCHEMA IF NOT EXISTS "{settings.DB_SCHEMA}" '
                f'AUTHORIZATION "{settings.DB_USER}"'
            )
        )
    logger.info("schema [%s] 已就绪", settings.DB_SCHEMA)


def run_migrations() -> None:
    """执行版本化迁移。SQLite 降级路径不跑 Alembic。"""
    if not settings.is_postgres:
        return
    if not ALEMBIC_INI.exists():
        raise RuntimeError(f"缺少 Alembic 配置: {ALEMBIC_INI}")
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    command.upgrade(cfg, "head")
    logger.info("数据库迁移完成")


def create_initial_revision(message: str = "initial schema") -> Path:
    """首次接入 Postgres 时生成初始迁移脚本，仅由引导脚本调用。"""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    command.revision(cfg, message=message, autogenerate=True)
    versions = BASE_DIR / "migrations" / "versions"
    files = sorted(versions.glob(f"*_{message.replace(' ', '_')}.py"))
    if not files:
        files = sorted(versions.glob("*.py"))
    return files[-1]
