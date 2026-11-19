# -*- coding: utf-8 -*-
"""JWT 工具与统一会话读取。

安全红线（勿回退）：
- 只认 JWT 签名，不存在 ydtoken/base64 绕过分支。
- token 有效期与 cookie max_age 共用 SESSION_EXPIRE_SECONDS。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Request

from ..config import settings

logger = logging.getLogger("auth")


def create_token(data: dict[str, Any], expires_delta: int | None = None) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        seconds=expires_delta or settings.SESSION_EXPIRE_SECONDS
    )
    payload["exp"] = expire
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        return jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except jwt.PyJWTError:
        return None


async def get_uinfo(request: Request) -> dict[str, Any]:
    """路由层统一依赖：uinfo: dict = Depends(get_uinfo)。"""
    state = request.scope.get("state") or {}
    if state.get("uinfo"):
        return state["uinfo"]
    uinfo = verify_token(request.cookies.get("uinfo", ""))
    if uinfo and uinfo.get("id") is not None:
        return uinfo
    from ..errors import UnauthorizedError

    raise UnauthorizedError()
