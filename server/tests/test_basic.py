# -*- coding: utf-8 -*-
"""基础冒烟测试。"""
from __future__ import annotations


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "OK"
    assert body["ok"] is True


def test_health_alias(client):
    assert client.get("/health").json()["status"] == "OK"


def test_modules_route(client, admin_headers):
    r = client.get("/api/modules", headers=admin_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_standard_health_path_is_exempt(client):
    """健康检查不依赖鉴权，返回必须为 200。"""
    assert client.get("/health", headers={"x-access-token": "wrong"}).status_code == 200
