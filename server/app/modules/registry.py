"""审计模块注册表。

扩展方式：在 `app/modules/` 下新增一个包，包内 `__init__.py` 导出
`MODULE = ModuleSpec(...)`（需要接口时再导出 `router`）。
启动时自动发现并挂载，主程序不需要改动。
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI

# registry 本身是模块而非包，遍历的是父包 app/modules/ 下的子包
_PACKAGE = __name__.rsplit(".", 1)[0]


@dataclass
class ModuleSpec:
    id: str
    name: str
    enabled: bool = True
    route: str = ""
    group: str = "审计模块"
    order: int = 100
    router: APIRouter | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "route": self.route,
            "group": self.group,
            "order": self.order,
        }


_SPECS: dict[str, ModuleSpec] = {}
_DISCOVERED = False
_SKIP = {"registry"}


def register(spec: ModuleSpec) -> None:
    if spec.id in _SPECS:
        raise ValueError(f"模块 id 重复：{spec.id}")
    _SPECS[spec.id] = spec


def discover_modules() -> list[ModuleSpec]:
    global _DISCOVERED
    if _DISCOVERED:
        return list(_SPECS.values())
    package = importlib.import_module(_PACKAGE)
    for info in pkgutil.iter_modules(package.__path__):
        if info.name in _SKIP or info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{_PACKAGE}.{info.name}")
        spec = getattr(mod, "MODULE", None)
        if isinstance(spec, ModuleSpec) and spec.id not in _SPECS:
            register(spec)
    _DISCOVERED = True
    return list(_SPECS.values())


def list_modules() -> list[dict[str, Any]]:
    return [s.to_dict() for s in sorted(discover_modules(), key=lambda x: x.order)]


def install_modules(app: FastAPI) -> None:
    for spec in discover_modules():
        if spec.router is not None:
            app.include_router(
                spec.router,
                prefix=f"/api/modules/{spec.id}",
                tags=[spec.name],
            )
