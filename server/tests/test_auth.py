# -*- coding: utf-8 -*-
"""认证相关测试：JWT 工具、配置 fail-fast、本地降级行为。"""
from __future__ import annotations

import pytest


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
