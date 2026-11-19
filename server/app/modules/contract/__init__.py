"""合同审核：上传合同及附件，识别角色与类型，做文本审核和跨文件勾稽。"""
from ..registry import ModuleSpec
from .router import router  # 同时完成 models 注册，保证建表在 init_db 前

MODULE = ModuleSpec(
    id="contract",
    name="合同审计",
    enabled=True,
    route="/contract",
    order=30,
    router=router,
)
