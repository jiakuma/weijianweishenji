# -*- coding: utf-8 -*-
"""本地账号鉴权接口：登录、当前用户、改密、登出。"""
from __future__ import annotations

import bcrypt
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..db import SessionLocal, User, now_str
from ..errors import BadRequestError, UnauthorizedError
from .common import create_token, get_current_user, user_to_dict

router = APIRouter(prefix="/api/auth", tags=["本地账号鉴权"])

MAX_PASSWORD_BYTES = 72  # bcrypt 的有效输入上限


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""


class ChangePasswordBody(BaseModel):
    oldPassword: str = ""
    newPassword: str = ""


def hash_password(password: str) -> str:
    """返回 bcrypt 哈希字符串；数据库里绝不出现明文。"""
    raw = password.encode("utf-8")
    if len(raw) > MAX_PASSWORD_BYTES:
        raise BadRequestError(f"密码不能超过 {MAX_PASSWORD_BYTES} 字节")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """校验 bcrypt 密码，损坏的哈希按校验失败处理。"""
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(
            password.encode("utf-8")[:MAX_PASSWORD_BYTES],
            password_hash.encode("ascii"),
        )
    except (ValueError, TypeError):
        return False


@router.post("/login")
def login(body: LoginBody):
    """校验账号密码并签发 JWT；不区分用户不存在与密码错误，防止用户枚举。"""
    username = body.username.strip()
    with SessionLocal() as session:
        user = session.query(User).filter(User.username == username).first()
        if user is None or not user.enabled or not verify_password(body.password, user.password_hash):
            raise UnauthorizedError("用户名或密码错误")
        user.last_login_at = now_str()
        session.commit()
        payload = {
            "sub": user.id,
            "username": user.username,
            "role": user.role,
        }
        token = create_token(payload)
        current = user_to_dict(user)
    return {"token": token, "user": current}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    """返回当前用户；mustChangePassword 供前端引导改密页。"""
    return user


@router.post("/change-password")
def change_password(body: ChangePasswordBody, user: dict = Depends(get_current_user)):
    """修改自己的密码，成功后清除强制改密标记。"""
    new_password = body.newPassword
    if len(new_password) < 8:
        raise BadRequestError("新密码至少 8 位")
    with SessionLocal() as session:
        row = session.get(User, user["id"])
        if row is None or not row.enabled:
            raise UnauthorizedError()
        if not verify_password(body.oldPassword, row.password_hash):
            raise BadRequestError("原密码不正确")
        row.password_hash = hash_password(new_password)
        row.must_change_password = False
        session.commit()
    return {"ok": True}


@router.post("/logout")
def logout():
    """JWT 无状态：仅提供语义完整的接口，前端负责清除本地 token。"""
    return {"ok": True}
