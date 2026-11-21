"""制度互斥检测任务表。新增表，不改动底座原有 6 张表。"""
from __future__ import annotations

from sqlalchemy import Column, Integer, String, Text

from ...db import Base


class ConflictTask(Base):
    __tablename__ = "audit_conflict_tasks"

    id = Column(String, primary_key=True)
    name = Column(String, default="")
    created_at = Column(String, default="")
    status = Column(String, default="pending")
    stage = Column(String, default="")
    mode = Column(String, default="")
    total = Column(Integer, default=0)
    done = Column(Integer, default=0)
    compare_total = Column(Integer, default=0)
    compare_done = Column(Integer, default=0)
    conflict_count = Column(Integer, default=0)
    files_json = Column(Text, default="[]")
    report_json = Column(Text, default="{}")
    error = Column(Text, default="")
    owner_id = Column(String, index=True, default="")


class ConflictSession(Base):
    """一次连续的互斥检测会话，持有若干任务；任务表不动，兼容历史数据。"""
    __tablename__ = "audit_conflict_sessions"

    id = Column(String, primary_key=True)
    title = Column(String, default="")
    task_ids_json = Column(Text, default="[]")
    created_at = Column(String, default="")
    updated_at = Column(String, default="")
    owner_id = Column(String, index=True, default="")
