# -*- coding: utf-8 -*-
"""认证相关测试：JWT 工具、配置 fail-fast、本地账号与数据隔离。"""
from __future__ import annotations

import uuid

import bcrypt
import pytest

from conftest import TEST_PASSWORD


def test_jwt_roundtrip(monkeypatch):
    from app import config
    from app.auth import create_token, verify_token

    monkeypatch.setattr(config.settings, "JWT_SECRET_KEY", "test-secret-not-for-prod")
    token = create_token({"id": "u1", "name": "tester"})
    assert verify_token(token)["id"] == "u1"
    assert verify_token(token + "tampered") is None


def test_jwt_expired(monkeypatch):
    from app import config
    from app.auth import create_token, verify_token

    monkeypatch.setattr(config.settings, "JWT_SECRET_KEY", "test-secret-not-for-prod")
    token = create_token({"id": "u1"}, expires_delta=-1)
    assert verify_token(token) is None


def test_sso_config_fail_fast(monkeypatch):
    from app import config

    monkeypatch.setattr(config.settings, "AUTH_MODE", "sso")
    monkeypatch.setattr(config.settings, "JWT_SECRET_KEY", "")
    monkeypatch.setattr(config.settings, "AI_PLATFORM_HOST", "")
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        config.settings.validate()


def test_local_health_still_open(client):
    assert client.get("/api/health").status_code == 200


# ---------------------------------------------------------------- 本地账号

def test_login_success(client, admin_headers):
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": TEST_PASSWORD}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"]
    assert body["user"]["username"] == "admin"
    assert body["user"]["role"] == "admin"
    assert set(body["user"]) >= {
        "id", "username", "displayName", "role", "mustChangePassword"
    }
    assert "passwordHash" not in body["user"]
    assert "password_hash" not in body["user"]


def test_login_failures_do_not_leak_user_existence(client):
    wrong_password = client.post(
        "/api/auth/login", json={"username": "admin", "password": "definitely-wrong"}
    )
    missing_user = client.post(
        "/api/auth/login", json={"username": "nobody-xyz", "password": "definitely-wrong"}
    )
    assert wrong_password.status_code == 401
    assert missing_user.status_code == 401
    assert wrong_password.json() == missing_user.json()
    assert wrong_password.json()["error"]["code"] == "UNAUTHORIZED"


def test_disabled_user_cannot_login(client):
    from app.auth.routes import hash_password
    from app.db import SessionLocal, User, now_str

    username = f"disabled-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        session.add(User(
            id=f"user-{uuid.uuid4().hex[:12]}", username=username,
            display_name="已停用", password_hash=hash_password(TEST_PASSWORD),
            role="user", enabled=False, must_change_password=False,
            created_at=now_str(),
        ))
        session.commit()
    response = client.post(
        "/api/auth/login", json={"username": username, "password": TEST_PASSWORD}
    )
    assert response.status_code == 401


def test_password_is_bcrypt_hash(client, admin_headers):
    from app.db import SessionLocal, User

    with SessionLocal() as session:
        admin = session.query(User).filter(User.username == "admin").first()
        assert admin.password_hash != TEST_PASSWORD
        assert admin.password_hash.startswith("$2")
        assert bcrypt.checkpw(TEST_PASSWORD.encode("utf-8"), admin.password_hash.encode("ascii"))


def test_missing_token_is_rejected(client):
    response = client.get("/api/qa/sessions")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_require_admin_returns_403(client, normal_user):
    response = client.get("/api/users", headers=normal_user["headers"])
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_user_management_contract(client, admin_headers):
    username = f"managed-{uuid.uuid4().hex[:8]}"
    created = client.post(
        "/api/users",
        json={"username": username, "displayName": "被管理用户",
              "password": TEST_PASSWORD, "role": "user"},
        headers=admin_headers,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["username"] == username
    assert body["displayName"] == "被管理用户"
    assert body["enabled"] is True
    assert "passwordHash" not in body

    listed = client.get("/api/users", headers=admin_headers).json()
    row = next(item for item in listed if item["username"] == username)
    assert "lastLoginAt" in row and "passwordHash" not in row

    updated = client.put(
        f"/api/users/{body['id']}",
        json={"displayName": "改名用户", "role": "user"}, headers=admin_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["displayName"] == "改名用户"

    assert client.post(
        f"/api/users/{body['id']}/reset-password",
        json={"password": "ResetPass123!"}, headers=admin_headers,
    ).status_code == 200
    assert client.post(
        f"/api/users/{body['id']}/toggle", headers=admin_headers
    ).status_code == 200

    # 不提供 DELETE
    assert client.delete(f"/api/users/{body['id']}", headers=admin_headers).status_code == 405

    # 不能停掉自己
    me = client.get("/api/auth/me", headers=admin_headers).json()
    self_toggle = client.post(f"/api/users/{me['id']}/toggle", headers=admin_headers)
    assert self_toggle.status_code == 400


def test_must_change_password_guard(client):
    from app.auth.routes import hash_password
    from app.db import SessionLocal, User, now_str

    username = f"must-change-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        session.add(User(
            id=f"user-{uuid.uuid4().hex[:12]}", username=username,
            display_name="待改密", password_hash=hash_password(TEST_PASSWORD),
            role="user", enabled=True, must_change_password=True, created_at=now_str(),
        ))
        session.commit()
    login = client.post(
        "/api/auth/login", json={"username": username, "password": TEST_PASSWORD}
    )
    assert login.status_code == 200
    headers = {"X-Access-Token": login.json()["token"]}
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    blocked = client.get("/api/qa/sessions", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "MUST_CHANGE_PASSWORD"

    assert client.post(
        "/api/auth/change-password",
        json={"oldPassword": TEST_PASSWORD, "newPassword": "NewPass123!"},
        headers=headers,
    ).status_code == 200
    assert client.get("/api/qa/sessions", headers=headers).status_code == 200


# ---------------------------------------------------------------- 数据隔离

def test_qa_session_owner_isolation(client, admin_headers, normal_user):
    created = client.post(
        "/api/qa/sessions", json={"module": "assistant"}, headers=admin_headers
    )
    assert created.status_code == 200
    session_id = created.json()["id"]

    admin_ids = [s["id"] for s in client.get("/api/qa/sessions", headers=admin_headers).json()]
    assert session_id in admin_ids
    user_ids = [s["id"] for s in client.get(
        "/api/qa/sessions", headers=normal_user["headers"]
    ).json()]
    assert session_id not in user_ids

    foreign_delete = client.delete(
        f"/api/qa/sessions/{session_id}", headers=normal_user["headers"]
    )
    assert foreign_delete.status_code == 404
    foreign_read = client.get(
        f"/api/qa/sessions/{session_id}", headers=normal_user["headers"]
    )
    assert foreign_read.status_code == 404

    assert client.delete(
        f"/api/qa/sessions/{session_id}", headers=admin_headers
    ).status_code == 200


def test_assistant_upload_owner_isolation(client, admin_headers, normal_user):
    from app.auth.common import bind_owner_id, reset_owner_id
    from app.db import AssistantUpload, SessionLocal
    from app.modules.conflict.agent_tool import _load_uploaded

    response = client.post(
        "/api/assistant/uploads",
        files=[("files", ("隔离测试.txt", b"owner-isolation", "text/plain"))],
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    upload_id = response.json()[0]["id"]
    admin_id = client.get("/api/auth/me", headers=admin_headers).json()["id"]
    with SessionLocal() as session:
        row = session.get(AssistantUpload, upload_id)
        assert row.owner_id == admin_id

    token = bind_owner_id(normal_user["id"])
    try:
        uploaded, missing = _load_uploaded([upload_id])
    finally:
        reset_owner_id(token)
    assert uploaded == [] and upload_id in missing

    token = bind_owner_id(admin_id)
    try:
        uploaded, missing = _load_uploaded([upload_id])
    finally:
        reset_owner_id(token)
    assert len(uploaded) == 1 and not missing


def test_report_tasks_owner_isolation(client, admin_headers, normal_user):
    from app.db import SessionLocal, now_str
    from app.modules.contract.models import ContractTask
    from app.modules.workpaper.models import WorkpaperTask

    admin_id = client.get("/api/auth/me", headers=admin_headers).json()["id"]
    task_id = f"iso-{uuid.uuid4().hex[:10]}"
    with SessionLocal() as session:
        session.add(ContractTask(
            id=task_id, name="他人合同报告", created_at=now_str(), status="done",
            report_json='{"issues": []}', owner_id=admin_id,
        ))
        session.add(WorkpaperTask(
            id=task_id, name="他人底稿报告", created_at=now_str(), status="done",
            report_json='{"items": []}', owner_id=admin_id,
        ))
        session.commit()

    for module in ("contract", "workpaper"):
        rows = client.get(f"/api/modules/{module}/tasks", headers=normal_user["headers"]).json()
        assert all(row["id"] != task_id for row in rows)
        detail = client.get(
            f"/api/modules/{module}/tasks/{task_id}", headers=normal_user["headers"]
        ).json()
        assert detail.get("found") is False
        exported = client.get(
            f"/api/modules/{module}/tasks/{task_id}/export",
            headers=normal_user["headers"],
        )
        assert exported.status_code == 404

    own = client.get("/api/modules/contract/tasks", headers=admin_headers).json()
    assert any(row["id"] == task_id for row in own)


# ---------------------------------------------------------------- 导入权限与切换账号

def test_import_endpoints_require_admin(client, normal_user, admin_headers):
    """资料库导入/改写接口仅管理员可用；只读任务列表对所有登录用户开放。"""
    headers = normal_user["headers"]
    assert client.get("/api/imports", headers=headers).status_code == 200
    assert client.post(
        "/api/imports/files",
        files=[("files", ("权限测试.txt", b"hello", "text/plain"))],
        headers=headers,
    ).status_code == 403
    assert client.post(
        "/api/imports/links",
        json={"url": "https://example.com/nope"},
        headers=headers,
    ).status_code == 403
    assert client.post("/api/imports/not-exist/retry", headers=headers).status_code == 403
    assert client.post("/api/imports/not-exist/reparse", headers=headers).status_code == 403
    assert client.delete("/api/imports", headers=headers).status_code == 403

    # 管理员调用同一个空操作接口不受影响
    assert client.post(
        "/api/imports/not-exist/retry", headers=admin_headers
    ).json() == {"ok": False}
    assert client.delete("/api/imports", headers=admin_headers).status_code == 200


def test_switch_user_requires_admin(client, normal_user):
    response = client.post(
        "/api/auth/switch-user",
        json={"userId": normal_user["id"]},
        headers=normal_user["headers"],
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_user_switch_disabled_by_default(client, normal_user):
    """测试开关默认关闭：普通用户既拿不到列表也不能切换，契约里 canSwitchUser=false。"""
    me = client.get("/api/auth/me", headers=normal_user["headers"])
    assert me.json()["canSwitchUser"] is False
    assert client.get(
        "/api/auth/switchable-users", headers=normal_user["headers"]
    ).status_code == 403
    assert client.post(
        "/api/auth/switch-user", json={"userId": normal_user["id"]},
        headers=normal_user["headers"],
    ).status_code == 403


def test_user_switch_enabled_for_normal_user(client, normal_user, monkeypatch):
    """开启 ALLOW_USER_SWITCH 后：普通用户可列账号并切换，me 契约可切换标志为 true。"""
    from app.config import settings

    monkeypatch.setattr(settings, "ALLOW_USER_SWITCH", True)

    listing = client.get("/api/auth/switchable-users", headers=normal_user["headers"])
    assert listing.status_code == 200, listing.text
    ids = {u["id"] for u in listing.json()}
    assert normal_user["id"] in ids

    me = client.get("/api/auth/me", headers=normal_user["headers"])
    assert me.json()["canSwitchUser"] is True

    resp = client.post(
        "/api/auth/switch-user", json={"userId": normal_user["id"]},
        headers=normal_user["headers"],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["canSwitchUser"] is True


def test_switch_user_success_and_target_contract(client, normal_user, admin_headers):
    response = client.post(
        "/api/auth/switch-user",
        json={"userId": normal_user["id"]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"]
    assert body["user"]["id"] == normal_user["id"]
    assert body["user"]["role"] == "user"
    assert set(body["user"]) >= {
        "id", "username", "displayName", "role", "mustChangePassword"
    }
    assert "passwordHash" not in body["user"]
    me = client.get("/api/auth/me", headers={"X-Access-Token": body["token"]})
    assert me.status_code == 200
    assert me.json()["id"] == normal_user["id"]


def test_switch_user_bypasses_must_change_without_mutating_target(client, admin_headers):
    """管理员模拟切到“需改密”账号：切换后接口畅通，但目标库的改密标志不变。"""
    from app.auth.routes import hash_password
    from app.db import SessionLocal, User, now_str

    username = f"sw-mustchange-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        target_id = f"user-{uuid.uuid4().hex[:12]}"
        session.add(User(
            id=target_id, username=username,
            display_name="待改密切换目标", password_hash=hash_password(TEST_PASSWORD),
            role="user", enabled=True, must_change_password=True, created_at=now_str(),
        ))
        session.commit()

    resp = client.post(
        "/api/auth/switch-user", json={"userId": target_id}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["mustChangePassword"] is False
    impersonated_headers = {"X-Access-Token": body["token"]}
    # 整页重载后 /me 与业务接口都不应再被强制改密拦截
    me = client.get("/api/auth/me", headers=impersonated_headers)
    assert me.status_code == 200
    assert me.json()["mustChangePassword"] is False
    assert client.get("/api/qa/sessions", headers=impersonated_headers).status_code == 200
    # 目标账号数据库标志未被模拟切换改动：它自己正常登录仍须改密
    with SessionLocal() as session:
        assert session.get(User, target_id).must_change_password is True
    own_login = client.post(
        "/api/auth/login", json={"username": username, "password": TEST_PASSWORD}
    )
    assert client.get(
        "/api/qa/sessions",
        headers={"X-Access-Token": own_login.json()["token"]},
    ).status_code == 403


def test_switch_user_rejects_unknown_and_disabled(client, admin_headers):
    assert client.post(
        "/api/auth/switch-user", json={"userId": "nonexistent-user"},
        headers=admin_headers,
    ).status_code == 404

    from app.auth.routes import hash_password
    from app.db import SessionLocal, User, now_str

    username = f"sw-disabled-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        session.add(User(
            id=f"user-{uuid.uuid4().hex[:12]}", username=username,
            display_name="停用切换目标", password_hash=hash_password(TEST_PASSWORD),
            role="user", enabled=False, must_change_password=False,
            created_at=now_str(),
        ))
        session.commit()
    with SessionLocal() as session:
        disabled_id = session.query(User).filter(User.username == username).first().id
    assert client.post(
        "/api/auth/switch-user", json={"userId": disabled_id},
        headers=admin_headers,
    ).status_code == 404


def test_switch_user_disabled_in_sso(client, admin_headers, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "AUTH_MODE", "sso")
    assert settings.is_sso is True
    response = client.post(
        "/api/auth/switch-user", json={"userId": "anything"},
        headers=admin_headers,
    )
    assert response.status_code == 403
