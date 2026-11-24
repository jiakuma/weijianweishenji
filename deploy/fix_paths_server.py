#!/usr/bin/env python3
"""迁移后修复数据库里的 Windows 绝对路径为 Linux 路径。

在服务器 /opt/audit-platform 下执行：
    /opt/audit-platform/venv/bin/python /opt/audit-platform/deploy/fix_paths_server.py

做的事：
1. 备份 knowledge.db
2. documents.file_path: E:\\审计工作平台\\server\\data\\... -> /opt/audit-platform/server/data/...
3. 反斜杠统一为正斜杠
幂等：已是 Linux 路径的记录不受影响。
"""
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

DB = Path("/opt/audit-platform/server/data/knowledge.db")
WIN_PREFIX = r"E:\审计工作平台\server"
LINUX_PREFIX = "/opt/audit-platform/server"

assert DB.exists(), f"数据库不存在: {DB}"

backup = DB.with_name(f"knowledge.db.bak-pathfix-{datetime.now():%Y%m%d%H%M%S}")
shutil.copy2(DB, backup)
print(f"已备份: {backup}")

con = sqlite3.connect(DB)
try:
    rows = con.execute(
        "SELECT id, file_path FROM documents WHERE file_path IS NOT NULL AND file_path != ''"
    ).fetchall()
    changed = 0
    for doc_id, path in rows:
        new = path
        if new.startswith(WIN_PREFIX):
            new = LINUX_PREFIX + new[len(WIN_PREFIX):]
        if "\\" in new:
            new = new.replace("\\", "/")
        if new != path:
            con.execute("UPDATE documents SET file_path = ? WHERE id = ?", (new, doc_id))
            changed += 1
    con.commit()
    print(f"已转换 {changed} 条路径")

    remain = con.execute(
        "SELECT COUNT(*) FROM documents WHERE file_path LIKE 'E:%' OR file_path LIKE '%\\%'"
    ).fetchone()[0]
    print(f"剩余 Windows 路径: {remain} 条")
finally:
    con.close()
