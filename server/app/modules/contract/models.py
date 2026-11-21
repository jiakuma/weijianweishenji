"""合同审计：任务与报告数据表。

报告体整体放 report_json，沿用制度互斥模块的经验——新增字段不改表结构，
避免 SQLite ALTER TABLE 迁移风险。
"""
from ...db import Base
from sqlalchemy import Column, String, Integer, Text


class ContractTask(Base):
    __tablename__ = "audit_contract_tasks"

    id = Column(String, primary_key=True)
    name = Column(String, default="")
    created_at = Column(String, default="")
    status = Column(String, default="running")
    stage = Column(String, default="")
    total = Column(Integer, default=0)
    done = Column(Integer, default=0)
    issue_count = Column(Integer, default=0)
    contract_type = Column(String, default="")
    files_json = Column(Text, default="[]")
    report_json = Column(Text, default="{}")
    error = Column(String, default="")
    owner_id = Column(String, index=True, default="")
