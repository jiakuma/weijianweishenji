"""底稿审核任务与报告数据表。

报告体整体放 report_json，新增字段不需要改表结构。
"""
from ...db import Base
from sqlalchemy import Column, String, Integer, Text


class WorkpaperTask(Base):
    __tablename__ = "audit_workpaper_tasks"

    id = Column(String, primary_key=True)
    name = Column(String, default="")
    created_at = Column(String, default="")
    status = Column(String, default="running")
    stage = Column(String, default="")
    total = Column(Integer, default=0)
    done = Column(Integer, default=0)
    issue_count = Column(Integer, default=0)
    recon_count = Column(Integer, default=0)
    unit_count = Column(Integer, default=0)
    file_count = Column(Integer, default=0)
    files_json = Column(Text, default="[]")
    report_json = Column(Text, default="{}")
    error = Column(String, default="")
