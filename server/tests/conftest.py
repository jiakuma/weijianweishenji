# -*- coding: utf-8 -*-
"""测试骨架：保证重构过程中每一步可验证。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("AUTH_MODE", "local")
os.environ.setdefault("APP_PORT", "5281")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
