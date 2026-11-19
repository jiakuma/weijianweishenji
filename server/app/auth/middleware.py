# -*- coding: utf-8 -*-
"""统一认证中间件：SSO 触发 → extractauth ensure/user → 本地 JWT 双 cookie。

相对旧实现的修正（勿回退）：
1. 认证平台 / alche 调用带显式超时。
2. ensure/user 非 200 或 data 缺 id 直接 401，不带着 None 继续。
3. cookie 带 HttpOnly + SameSite=Lax +（HTTPS 场地）Secure。
4. 触发判据是本地 JWT 有效性，不是 already_auth 标记。
5. 401 清掉失效的 uinfo / already_auth。
6. 转发 who 时剔除自家 cookie，不把 JWT 发到第三方域。
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import settings
from .common import create_token, verify_token

logger = logging.getLogger("auth")

OWN_COOKIE_KEYS = frozenset({"uinfo", "already_auth"})
HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


def register_auth_middleware(app: FastAPI) -> None:
    """仅在 AUTH_MODE=sso 时调用；配置缺失由 config.validate() fail-fast。"""

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        start = time.time()
        path = request.url.path
        if path in settings.AUTH_EXEMPT_PATHS:
            return await call_next(request)

        uinfo = verify_token(request.cookies.get("uinfo", ""))
        if uinfo and uinfo.get("id") is not None:
            _put_state(request, {"uinfo": uinfo})
            return await call_next(request)

        if request.headers.get(settings.SSO_TRIGGER_HEADER) == "1":
            if await _ensure_user_via_sso(request):
                response = await call_next(request)
                token = (_get_state(request) or {}).get("uinfo_token")
                if token:
                    _set_session_cookies(response, token)
                    logger.info("sso ok path=%s cost=%.2fs", path, time.time() - start)
                return response
            logger.warning("sso failed path=%s", path)
            return _unauthorized()

        logger.warning("no valid session path=%s", path)
        return _unauthorized()

    return auth_middleware


def _get_state(request: Request) -> dict[str, Any] | None:
    return request.scope.get("state")


def _put_state(request: Request, data: dict[str, Any]) -> None:
    if "state" not in request.scope:
        request.scope["state"] = {}
    request.scope["state"].update(data)


def _set_session_cookies(response: JSONResponse, token: str) -> None:
    max_age = settings.SESSION_EXPIRE_SECONDS
    for key, value in (("uinfo", token), ("already_auth", "1")):
        response.set_cookie(
            key=key,
            value=value,
            max_age=max_age,
            httponly=True,
            samesite="lax",
            secure=settings.COOKIE_SECURE,
        )


def _unauthorized() -> JSONResponse:
    response = JSONResponse(
        {"error": {"code": "UNAUTHORIZED", "message": "未登录或登录已失效"}},
        status_code=401,
    )
    for key in OWN_COOKIE_KEYS:
        response.delete_cookie(key)
    return response


async def _ensure_user_via_sso(request: Request) -> bool:
    sso_host = settings.SSO_HOST
    ai_host = settings.AI_PLATFORM_HOST
    if not sso_host or not ai_host:
        logger.error("SSO_HOST / AI_PLATFORM_HOST 未配置")
        return False

    who_url = f"{sso_host.rstrip('/')}/api/security-manage-permission/user/who"
    sso_cookies = {k: v for k, v in request.cookies.items() if k not in OWN_COOKIE_KEYS}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        who_resp = await client.get(who_url, cookies=sso_cookies)
    if who_resp.status_code != 200:
        logger.error("sso who %s: %s", who_resp.status_code, who_resp.text[:200])
        return False

    who_data = who_resp.json()
    who_user = who_data.get("data", who_data) if isinstance(who_data, dict) else {}
    username = who_user.get("username")
    phone = who_user.get("phone")
    external_id = who_user.get("id")
    if not all([username, phone, external_id]):
        logger.error("sso who 缺少必要字段: %s", who_user)
        return False

    ensure_url = f"{ai_host.rstrip('/')}/api/account_management/ensure/user"
    payload = {
        "name": username,
        "phone": phone,
        "external_user_id": external_id,
        "organization": who_user.get("organization_name"),
        "department": who_user.get("department"),
    }
    headers = {"extractauth": settings.AI_PLATFORM_AUTH_KEY}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        ensure_resp = await client.post(ensure_url, json=payload, headers=headers)
    if ensure_resp.status_code != 200:
        logger.error("ensure/user %s: %s", ensure_resp.status_code, ensure_resp.text[:200])
        return False

    ensure_data = ensure_resp.json()
    user_info = ensure_data.get("data", ensure_data) if isinstance(ensure_data, dict) else None
    if not isinstance(user_info, dict) or not user_info.get("id"):
        logger.error("ensure/user 响应缺少用户 id: %s", ensure_data)
        return False

    uinfo: dict[str, Any] = {
        "id": user_info.get("id"),
        "name": user_info.get("name"),
        "status": user_info.get("status"),
    }
    _put_state(request, {"uinfo": uinfo, "uinfo_token": create_token(uinfo)})
    return True
