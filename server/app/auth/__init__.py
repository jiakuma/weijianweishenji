# -*- coding: utf-8 -*-
"""认证模块：中台 SSO + 本地账号鉴权。"""
from .common import (
    bind_owner_id,
    create_token,
    filter_by_owner,
    get_current_user,
    get_owner_id,
    get_uinfo,
    owner_id_for,
    require_admin,
    reset_owner_id,
    user_to_dict,
    verify_token,
)
from .middleware import register_auth_middleware

__all__ = [
    "create_token", "verify_token", "get_uinfo", "register_auth_middleware",
    "get_current_user", "require_admin", "user_to_dict", "owner_id_for",
    "filter_by_owner", "bind_owner_id", "reset_owner_id", "get_owner_id",
]
