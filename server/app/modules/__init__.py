"""审计业务模块集合，由 registry 自动发现。"""
from .registry import ModuleSpec, discover_modules, install_modules, list_modules, register

__all__ = ["ModuleSpec", "discover_modules", "install_modules", "list_modules", "register"]
