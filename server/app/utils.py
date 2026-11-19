# -*- coding: utf-8 -*-
"""通用工具函数。"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def now_tz() -> datetime:
    """带本地时区的当前时间，供数据库时间字段统一写入。"""
    return datetime.now(_LOCAL_TZ)


def now_str() -> str:
    """项目现有字符串时间格式，兼容存量数据。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
