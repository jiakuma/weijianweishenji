# -*- coding: utf-8 -*-
"""业务异常定义与全局异常处理器。"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """服务端错误（500）。"""

    def __init__(self, message: str, detail: Any = None, wrap_error: Exception | None = None):
        self.message = message
        self.detail = detail
        self.wrap_error = wrap_error
        super().__init__(message)


class BadRequestError(Exception):
    """请求参数或业务规则错误（400）。"""

    def __init__(self, message: str, detail: Any = None):
        self.message = message
        self.detail = detail
        super().__init__(message)


class NotFoundError(Exception):
    """资源不存在（404）。"""

    def __init__(self, message: str = "资源不存在", detail: Any = None):
        self.message = message
        self.detail = detail
        super().__init__(message)


class UnauthorizedError(Exception):
    """未认证（401）。"""

    def __init__(self, message: str = "未登录或登录已失效", detail: Any = None):
        self.message = message
        self.detail = detail
        super().__init__(message)


def _payload(code: str, message: str, detail: Any = None) -> dict:
    body: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        body["detail"] = detail
    return {"error": body}


def register_exception_handlers(app: FastAPI) -> None:
    """在 main.py 中调用一次，统一兜底所有异常。"""

    @app.exception_handler(BadRequestError)
    async def _bad_request(_: Request, exc: BadRequestError):
        return JSONResponse(400, _payload("BAD_REQUEST", exc.message, exc.detail))

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError):
        return JSONResponse(404, _payload("NOT_FOUND", exc.message, exc.detail))

    @app.exception_handler(UnauthorizedError)
    async def _unauthorized(_: Request, exc: UnauthorizedError):
        return JSONResponse(401, _payload("UNAUTHORIZED", exc.message, exc.detail))

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(500, _payload("INTERNAL_ERROR", exc.message, exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse(422, _payload("VALIDATION_ERROR", "请求参数不合法", exc.errors()))

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND"}.get(
            exc.status_code, "HTTP_ERROR"
        )
        return JSONResponse(
            exc.status_code, _payload(code, str(exc.detail)), headers=exc.headers
        )
