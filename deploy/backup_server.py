#!/usr/bin/env python3
"""审计平台自动备份：数据库 + 向量索引，保留最近 N 份。

服务器部署：
  1. 放到 /opt/audit-platform/deploy/backup_server.py
  2. crontab -e 加一行（每天凌晨 2:30）：
     30 2 * * * /opt/audit-platform/venv/bin/python /opt/audit-platform/deploy/backup_server.py >> /var/log/audit-platform/backup.log 2>&1

设计：
- knowledge.db 用 sqlite3 backup API 在线备份，WAL 模式下也能拿到一致快照，不中断服务。
- milvus.db 是嵌入式目录，用 tar 打包。
- uploads 等原始文件导入后不再变动，不在每日备份范围（首次部署的 data.tar.gz 已含全量）。
- 保留最近 KEEP 份，自动清理更旧的。
"""
import shutil
import sqlite3
import tarfile
from datetime import datetime
from pathlib import Path

SERVER = Path("/opt/audit-platform/server")
DATA = SERVER / "data"
BACKUP_ROOT = Path("/opt/audit-platform/backups")
KEEP = 7

DB = DATA / "knowledge.db"
MILVUS = DATA / "milvus.db"

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
target = BACKUP_ROOT / stamp
target.mkdir(parents=True, exist_ok=True)
print(f"[{stamp}] 开始备份 -> {target}")

# 1. 数据库在线备份（一致快照）
if DB.exists():
    dest = target / "knowledge.db"
    src = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    print(f"  数据库已备份: {dest.stat().st_size / 1024 / 1024:.1f} MB")
else:
    print("  警告: 数据库不存在")

# 2. 向量索引打包
if MILVUS.exists():
    arc = target / "milvus.tar.gz"
    with tarfile.open(arc, "w:gz") as tf:
        tf.add(MILVUS, arcname="milvus.db")
    print(f"  向量索引已备份: {arc.stat().st_size / 1024 / 1024:.1f} MB")
else:
    print("  警告: 向量索引不存在")

# 3. 清理旧备份
dirs = sorted([d for d in BACKUP_ROOT.iterdir() if d.is_dir()], reverse=True)
for old in dirs[KEEP:]:
    shutil.rmtree(old)
    print(f"  清理旧备份: {old.name}")

print(f"[{stamp}] 完成，保留 {min(len(dirs) + 1, KEEP)} 份")
