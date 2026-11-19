# -*- coding: utf-8 -*-
"""PostgreSQL 15 首次接入引导：建 schema → 生成初始迁移 → 升级 → 迁移存量数据。

用法（在项目根 .env 中填好 Postgres 参数并设 DB_BACKEND=postgres 后）：

    cd server
    python scripts/bootstrap_postgres.py --with-data

说明：
- 只在 migrations/versions/ 为空时生成初始迁移，重复执行不会覆盖。
- --with-data 从 server/data/knowledge.db 读取存量数据，按主键幂等写入新库。
- 不加 --with-data 时只建结构，适合空库全新部署。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))
os.chdir(SERVER_DIR)

os.environ["DB_BACKEND"] = "postgres"  # 引导脚本强制走标准栈，忽略 .env 中的本地降级开关

TABLES = [
    "settings", "documents", "chunks", "document_files", "document_fulltext",
    "categories", "import_tasks", "notes", "qa_history", "qa_sessions",
    "qa_session_items", "agent_runs", "agent_steps", "assistant_uploads",
    "eval_queries", "eval_runs", "audit_conflict_tasks", "audit_conflict_sessions",
    "audit_contract_tasks", "audit_workpaper_tasks",
]

SQLITE_DB = SERVER_DIR / "data" / "knowledge.db"


def bootstrap() -> None:
    from app.config import settings
    from app.run_migrations import create_initial_revision, ensure_schema_sync, run_migrations

    settings.validate()
    if not settings.is_postgres:
        raise RuntimeError("请先设置 DB_BACKEND=postgres")

    ensure_schema_sync()
    versions = SERVER_DIR / "migrations" / "versions"
    if not list(versions.glob("*.py")):
        rev = create_initial_revision("initial schema")
        print(f"已生成初始迁移: {rev.name}")
    run_migrations()
    print("数据库结构已就绪")


def migrate_data() -> None:
    from sqlalchemy import create_engine, inspect, text

    from app.config import settings

    if not SQLITE_DB.exists():
        print(f"存量库不存在，跳过数据迁移: {SQLITE_DB}")
        return

    src = create_engine(f"sqlite:///{SQLITE_DB}")
    dst = create_engine(
        settings.sync_database_uri,
        connect_args={"options": f"-csearch_path={settings.DB_SCHEMA}"},
    )
    quoted = f'"{settings.DB_SCHEMA}"'
    existing = set(inspect(dst).get_table_names(schema=settings.DB_SCHEMA))

    with src.connect() as srcconn, dst.begin() as dstconn:
        for table in TABLES:
            if table not in existing:
                print(f"跳过（目标表不存在）: {table}")
                continue
            rows = srcconn.execute(text(f'SELECT * FROM "{table}"')).mappings().all()
            if not rows:
                continue
            cols = list(rows[0].keys())
            col_sql = ", ".join(f'"{c}"' for c in cols)
            params = ", ".join(f":{c}" for c in cols)
            update = ", ".join(f'"{c}"=EXCLUDED."{c}"' for c in cols if c != "id")
            sql = text(
                f'INSERT INTO {quoted}."{table}" ({col_sql}) VALUES ({params}) '
                f'ON CONFLICT ("id") DO UPDATE SET {update}' if update and "id" in cols
                else f'INSERT INTO {quoted}."{table}" ({col_sql}) VALUES ({params})'
            )
            dstconn.execute(sql, [dict(r) for r in rows])
            print(f"迁移 {table}: {len(rows)} 行")

    src.dispose()
    dst.dispose()
    print("存量数据迁移完成")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-data", action="store_true", help="同时迁移 SQLite 存量数据")
    args = parser.parse_args()
    bootstrap()
    if args.with_data:
        migrate_data()
