# -*- coding: utf-8 -*-
"""认证模块：中台 SSO + 本地 JWT 双 cookie。"""
from .common import create_token, get_uinfo, verify_token
from .middleware import register_auth_middleware

__all__ = ["create_token", "verify_token", "get_uinfo", "register_auth_middleware"]
