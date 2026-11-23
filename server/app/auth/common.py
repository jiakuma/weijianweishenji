# -*- coding: utf-8 -*-
"""JWT 工具、统一会话读取与本地账号鉴权依赖。

安全红线（勿回退）：
- 只认 JWT 签名，不存在 ydtoken/base64 绕过分支。
- token 有效期与 cookie max_age 共用 SESSION_EXPIRE_SECONDS。
- 本地账号只从 users 表取身份，SSO 的 uinfo 保持原有兼容语义。
"""
from __future__ import annotations

import contextvars
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, Request

from ..config import settings

logger = logging.getLogger("auth")

# 后台 SSE 线程里无法继承 request 依赖，用 contextvar 显式传递当前数据归属人。
_owner_id: contextvars.ContextVar[str] = contextvars.ContextVar("audit_owner_id", default="")


def bind_owner_id(owner_id: str) -> object:
    """把当前用户 id 绑定到本线程上下文，供智能体工具等后台逻辑使用。"""
    return _owner_id.set(owner_id or "")


def reset_owner_id(token: object) -> None:
    """恢复 contextvar，避免线程池复用导致归属人串号。"""
    _owner_id.reset(token)


def get_owner_id() -> str:
    """返回当前归属人；SSO 模式返回空串以保留历史共享行为。"""
    return "" if settings.is_sso else (_owner_id.get() or "")


def _jwt_secret() -> str:
    """SSO 必须显式配置密钥；local 模式允许留空，用固定派生值保证进程内一致。"""
    if settings.JWT_SECRET_KEY:
        return settings.JWT_SECRET_KEY
    return "audit-platform-local-jwt-secret-v1"


def create_token(data: dict[str, Any], expires_delta: int | None = None) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        seconds=expires_delta or settings.SESSION_EXPIRE_SECONDS
    )
    payload["exp"] = expire
    return jwt.encode(payload, _jwt_secret(), algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        return jwt.decode(
            token, _jwt_secret(), algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.PyJWTError:
        return None


async def get_uinfo(request: Request) -> dict[str, Any]:
    """路由层统一依赖（SSO 用）：uinfo: dict = Depends(get_uinfo)。"""
    state = request.scope.get("state") or {}
    if state.get("uinfo"):
        return state["uinfo"]
    uinfo = verify_token(request.cookies.get("uinfo", ""))
    if uinfo and uinfo.get("id") is not None:
        return uinfo
    from ..errors import UnauthorizedError

    raise UnauthorizedError()


def user_to_dict(user) -> dict[str, Any]:
    """User ORM → 前后端统一契约（camelCase，绝不含密码哈希）。"""
    # 切换入口：管理员始终可见；普通用户仅在测试开关 ALLOW_USER_SWITCH 打开时可见。
    can_switch = bool(getattr(user, "role", "") == "admin" or settings.ALLOW_USER_SWITCH)
    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name or user.username,
        "role": user.role,
        "mustChangePassword": bool(user.must_change_password),
        "enabled": bool(user.enabled),
        "canSwitchUser": can_switch,
    }


def owner_id_for(user: dict[str, Any] | None) -> str:
    """本地模式返回用户 id；SSO 或遗留会话返回空串（不做个人数据隔离）。"""
    if settings.is_sso or not user or user.get("legacy"):
        return ""
    return str(user.get("id") or "")


def filter_by_owner(query, model, user: dict[str, Any] | None):
    """按归属人收窄查询；无需隔离时原样返回。"""
    owner_id = owner_id_for(user)
    if not owner_id:
        return query
    return query.filter(model.owner_id == owner_id)


async def get_current_user(request: Request) -> dict[str, Any]:
    """路由层统一依赖：优先 X-Access-Token，其次 uinfo cookie / SSO state。"""
    from ..db import SessionLocal, User
    from ..errors import UnauthorizedError

    state = request.scope.get("state") or {}
    # 浏览器直连资源无法注入请求头，兼容 fileUrl() 附带的 token 查询参数。
    token = (request.headers.get("X-Access-Token")
             or request.cookies.get("uinfo", "")
             or request.query_params.get("token", ""))
    payload = verify_token(token) if token else None
    if not payload and state.get("uinfo"):
        payload = state["uinfo"]
    if not payload:
        raise UnauthorizedError()

    subject = payload.get("sub")
    if subject:
        with SessionLocal() as session:
            user = session.get(User, str(subject))
            if user is None or not user.enabled:
                raise UnauthorizedError()
            current = user_to_dict(user)
            # 管理员模拟切换签发的 token 带 impersonated：对外呈现为无需改密，
            # 与 switch-user 返回的 user 契约保持一致，避免刷新后被守卫踢到改密页。
            if payload.get("impersonated"):
                current["mustChangePassword"] = False
            return current

    # SSO 的 uinfo 只有 id/name，没有本地 users 记录；保持原有 SSO 行为
    if settings.is_sso and payload.get("id") is not None:
        name = str(payload.get("name") or "")
        return {
            "id": str(payload["id"]),
            "username": name,
            "displayName": name,
            "role": "user",
            "mustChangePassword": False,
            "enabled": True,
            "legacy": True,
        }
    raise UnauthorizedError()


async def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    """管理员依赖：非管理员统一返回 403 FORBIDDEN。"""
    from ..errors import ForbiddenError

    if user.get("legacy") or user.get("role") != "admin":
        raise ForbiddenError("需要管理员权限")
    return user
