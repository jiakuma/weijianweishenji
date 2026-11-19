"""制度互斥：比对多份制度文件中相互冲突（互斥）的条款。"""
from ..registry import ModuleSpec
from .router import router

MODULE = ModuleSpec(
    id="conflict",
    name="制度互斥",
    enabled=True,
    route="/conflict",
    order=20,
    router=router,
)
