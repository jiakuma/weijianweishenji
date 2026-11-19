"""审计知识助手：复用底座已有的检索问答 / Agent 能力。"""
from ..registry import ModuleSpec

MODULE = ModuleSpec(
    id="assistant",
    name="审计助手",
    enabled=True,
    route="/assistant",
    order=10,
)
