# -*- coding: utf-8 -*-
"""统一响应构造。"""
from __future__ import annotations

from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def success_response(data: Any) -> JSONResponse:
    """标准成功响应：{"data": ...}。"""
    return JSONResponse(status_code=200, content={"data": jsonable_encoder(data)})


def page_response(total: int, data: Any) -> JSONResponse:
    return success_response({"total": total, "data": data})


def raw_response(data: Any, status_code: int = 200) -> JSONResponse:
    """兼容尚未收敛的旧接口，逐步废弃。"""
    return JSONResponse(status_code=status_code, content=jsonable_encoder(data))
