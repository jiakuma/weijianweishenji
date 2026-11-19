"""底稿审核：上传审计实施方案与审计工作底稿，逐份复核并做跨文件勾稽。"""
from ..registry import ModuleSpec
from .router import router  # 同时完成 models 注册

MODULE = ModuleSpec(
    id="workpaper",
    name="底稿审核",
    enabled=True,
    route="/workpaper",
    order=40,
    router=router,
)
