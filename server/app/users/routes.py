# -*- coding: utf-8 -*-
"""用户管理：仅管理员可用，全部接口使用 camelCase 字段契约。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..auth.common import require_admin
from ..auth.routes import hash_password
from ..db import SessionLocal, User, now_str
from ..errors import BadRequestError, NotFoundError

router = APIRouter(prefix="/api/users", tags=["用户管理"])


class CreateUserBody(BaseModel):
    username: str = ""
    displayName: str = ""
    password: str = ""
    role: str = "user"


class UpdateUserBody(BaseModel):
    displayName: str = ""
    role: str = "user"


class ResetPasswordBody(BaseModel):
    password: str = ""


def list_dict(row: User) -> dict:
    """用户列表项：绝不返回 password_hash。"""
    return {
        "id": row.id,
        "username": row.username,
        "displayName": row.display_name or row.username,
        "role": row.role,
        "enabled": bool(row.enabled),
        "lastLoginAt": row.last_login_at or "",
    }


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise BadRequestError("密码至少 8 位")


def _other_admins(session, exclude_id: str) -> int:
    """除指定用户外仍在启用的管理员数量。"""
    return session.query(User).filter(
        User.role == "admin", User.enabled.is_(True), User.id != exclude_id
    ).count()


@router.get("")
def list_users(admin: dict = Depends(require_admin)):
    with SessionLocal() as session:
        rows = session.query(User).order_by(User.created_at, User.username).all()
        return [list_dict(row) for row in rows]


@router.post("")
def create_user(body: CreateUserBody, admin: dict = Depends(require_admin)):
    username = body.username.strip()
    if not username or len(username) > 64:
        raise BadRequestError("用户名长度应在 1-64 字符之间")
    if body.role not in {"admin", "user"}:
        raise BadRequestError("角色只能是 admin 或 user")
    _validate_password(body.password)
    with SessionLocal() as session:
        if session.query(User).filter(User.username == username).first():
            raise BadRequestError("用户名已存在")
        import uuid

        row = User(
            id=f"user-{uuid.uuid4().hex[:12]}",
            username=username,
            display_name=body.displayName.strip() or username,
            password_hash=hash_password(body.password),
            role=body.role,
            enabled=True,
            must_change_password=True,
            created_at=now_str(),
        )
        session.add(row)
        session.commit()
        return list_dict(row)


@router.put("/{user_id}")
def update_user(user_id: str, body: UpdateUserBody, admin: dict = Depends(require_admin)):
    """只改显示名与角色；username 不可变，且不能降级最后一个管理员。"""
    if body.role not in {"admin", "user"}:
        raise BadRequestError("角色只能是 admin 或 user")
    with SessionLocal() as session:
        row = session.get(User, user_id)
        if row is None:
            raise NotFoundError("用户不存在")
        if row.role == "admin" and body.role != "admin" and _other_admins(session, row.id) == 0:
            raise BadRequestError("至少保留一名启用的管理员")
        row.display_name = body.displayName.strip() or row.username
        row.role = body.role
        session.commit()
        return list_dict(row)


@router.post("/{user_id}/reset-password")
def reset_password(user_id: str, body: ResetPasswordBody,
                   admin: dict = Depends(require_admin)):
    """管理员重置密码：重置后目标用户首次登录必须改密。"""
    _validate_password(body.password)
    with SessionLocal() as session:
        row = session.get(User, user_id)
        if row is None:
            raise NotFoundError("用户不存在")
        row.password_hash = hash_password(body.password)
        row.must_change_password = True
        session.commit()
        return {"ok": True}


@router.post("/{user_id}/toggle")
def toggle_user(user_id: str, admin: dict = Depends(require_admin)):
    """启用/停用切换；不能停掉自己或最后一个管理员。"""
    with SessionLocal() as session:
        row = session.get(User, user_id)
        if row is None:
            raise NotFoundError("用户不存在")
        if row.id == admin["id"]:
            raise BadRequestError("不能停用当前登录账号")
        if row.enabled and row.role == "admin" and _other_admins(session, row.id) == 0:
            raise BadRequestError("至少保留一名启用的管理员")
        row.enabled = not row.enabled
        session.commit()
        return {"ok": True, "enabled": bool(row.enabled)}
