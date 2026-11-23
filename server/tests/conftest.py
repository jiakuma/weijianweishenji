# -*- coding: utf-8 -*-
"""测试骨架：保证重构过程中每一步可验证。"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import bcrypt
import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("AUTH_MODE", "local")
os.environ.setdefault("APP_PORT", "5281")
# 测试必须跑在独立库上：conftest 会改写 admin 密码、创建临时账号，
# 若复用 data/knowledge.db 会污染用户真实数据（历史上已发生多次）。
TEST_DB = Path(__file__).resolve().parent / ".test_knowledge.db"
os.environ["DB_URL"] = f"sqlite:///{TEST_DB}"

TEST_PASSWORD = "TestPass123!"


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username: str, password: str = TEST_PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


@pytest.fixture(scope="session")
def admin_headers(client):
    """把 admin 密码固定为测试密码并返回可用的登录请求头。"""
    from app.db import SessionLocal, User

    with SessionLocal() as session:
        admin = session.query(User).filter(User.username == "admin").first()
        assert admin is not None, "init_db 未创建 admin"
        admin.password_hash = bcrypt.hashpw(
            TEST_PASSWORD.encode("utf-8"), bcrypt.gensalt()
        ).decode("ascii")
        admin.enabled = True
        admin.must_change_password = False
        session.commit()

    response = _login(client, "admin")
    assert response.status_code == 200, response.text
    return {"X-Access-Token": response.json()["token"]}


@pytest.fixture(scope="session")
def normal_user(client, admin_headers):
    """通过管理接口创建普通用户，返回请求头与用户 id。"""
    from app.db import SessionLocal, User

    username = f"tester-{uuid.uuid4().hex[:8]}"
    created = client.post(
        "/api/users",
        json={
            "username": username,
            "displayName": "普通测试用户",
            "password": TEST_PASSWORD,
            "role": "user",
        },
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["id"]

    with SessionLocal() as session:
        row = session.get(User, user_id)
        row.must_change_password = False
        session.commit()

    response = _login(client, username)
    assert response.status_code == 200, response.text
    return {
        "id": user_id,
        "username": username,
        "headers": {"X-Access-Token": response.json()["token"]},
    }
