# -*- coding: utf-8 -*-
"""环境变量集中读取：业务代码唯一配置入口。

设计原则：
- 所有连接串、地址、密钥从这里读取，业务代码禁止硬编码。
- 生产默认走 PostgreSQL 15 + 中台 SSO；显式切到 sqlite/local 仅供开发验证。
- 切换到 postgres/sso 时关键参数缺失即 fail-fast，避免带错配置运行。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BASE_DIR.parent
load_dotenv(ROOT_DIR / ".env")
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _default_milvus_uri() -> str:
    """向量库默认路径：LOCALAPPDATA 下的纯 ASCII 路径。

    faiss C++ 层在 Windows GBK 代码页下无法打开含中文的 UTF-8 路径，
    会导致 HNSW 索引文件写不出来，故默认目录避开项目中文路径。
    仍可用 MILVUS_URI 环境变量覆盖为独立部署的 Milvus 服务或其他路径。
    """
    local = _env("LOCALAPPDATA")
    if local:
        base = Path(local)
    elif os.name == "nt":
        base = Path.home() / "AppData" / "Local"
    else:
        base = Path.home() / ".local" / "share"
    return str(base / "audit-work-platform" / "milvus.db")


class Settings:
    APP_NAME: str = _env("APP_NAME", "audit-work-platform")
    APP_HOST: str = _env("APP_HOST", "0.0.0.0")
    APP_PORT: int = _int("APP_PORT", 5281)
    WEB_PORT: int = _int("WEB_PORT", 5280)

    DB_BACKEND: str = _env("DB_BACKEND", "sqlite").lower()
    DB_HOST: str = _env("DB_HOST", "127.0.0.1")
    DB_PORT: str = _env("DB_PORT", "5432")
    DB_USER: str = _env("DB_USER")
    DB_PASSWORD: str = _env("DB_PASSWORD")
    DB_DATABASE: str = _env("DB_DATABASE")
    DB_SCHEMA: str = _env("DB_SCHEMA", "audit_platform")
    DB_POOL_SIZE: int = _int("DB_POOL_SIZE", 5)
    DB_MAX_OVERFLOW: int = _int("DB_MAX_OVERFLOW", 10)
    SQLITE_PATH: Path = (
        Path(_env("DB_URL").replace("sqlite:///", ""))
        if _env("DB_URL").startswith("sqlite:///")
        else BASE_DIR / "data" / "knowledge.db"
    )

    AUTH_MODE: str = _env("AUTH_MODE", "local").lower()
    JWT_SECRET_KEY: str = _env("JWT_SECRET_KEY")
    SESSION_EXPIRE_SECONDS: int = _int("SESSION_EXPIRE_SECONDS", 86400)
    COOKIE_SECURE: bool = _bool("COOKIE_SECURE")
    SSO_TRIGGER_HEADER: str = _env("SSO_TRIGGER_HEADER", "eywaauth")
    SSO_HOST: str = _env("SSO_HOST")
    AI_PLATFORM_HOST: str = _env("AI_PLATFORM_HOST")
    AI_PLATFORM_AUTH_KEY: str = _env("AI_PLATFORM_AUTH_KEY")
    JWT_ALGORITHM: str = _env("JWT_ALGORITHM", "HS256")
    AUTH_EXEMPT_PATHS: tuple[str, ...] = tuple(
        p.strip()
        for p in (
            _env("AUTH_EXEMPT_PATHS", "/,/health,/api/health").replace("[", "")
            .replace("]", "").replace('"', "").split(",")
        )
        if p.strip()
    )

    LLM_BASE_URL: str = _env("LLM_BASE_URL")
    LLM_API_KEY: str = _env("LLM_API_KEY")
    LLM_MODEL: str = _env("LLM_MODEL")

    MILVUS_URI: str = _env("MILVUS_URI", _default_milvus_uri())
    EMBED_MODEL: str = _env("EMBED_MODEL", "BAAI/bge-small-zh-v1.5")

    def validate(self) -> None:
        if self.DB_BACKEND not in {"postgres", "sqlite"}:
            raise RuntimeError("DB_BACKEND 只能是 postgres 或 sqlite")
        if self.DB_BACKEND == "postgres":
            missing = [
                name
                for name, value in (
                    ("DB_USER", self.DB_USER),
                    ("DB_PASSWORD", self.DB_PASSWORD),
                    ("DB_DATABASE", self.DB_DATABASE),
                )
                if not value
            ]
            if missing:
                raise RuntimeError(
                    "DB_BACKEND=postgres 缺少必需配置: " + ", ".join(missing)
                )
            if self.DB_SCHEMA.lower() == "public":
                raise RuntimeError("DB_SCHEMA 禁止使用 public，必须配置专属 schema")
        if self.AUTH_MODE not in {"local", "sso"}:
            raise RuntimeError("AUTH_MODE 只能是 local 或 sso")
        if self.AUTH_MODE == "sso":
            missing = [
                name
                for name, value in (
                    ("JWT_SECRET_KEY", self.JWT_SECRET_KEY),
                    ("AI_PLATFORM_HOST", self.AI_PLATFORM_HOST),
                    ("AI_PLATFORM_AUTH_KEY", self.AI_PLATFORM_AUTH_KEY),
                )
                if not value
            ]
            if missing:
                raise RuntimeError(
                    "AUTH_MODE=sso 缺少必需配置: " + ", ".join(missing)
                )

    @property
    def is_postgres(self) -> bool:
        return self.DB_BACKEND == "postgres"

    @property
    def is_sso(self) -> bool:
        return self.AUTH_MODE == "sso"

    @property
    def database_uri(self) -> str:
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_DATABASE}"
        )

    @property
    def sync_database_uri(self) -> str:
        return (
            f"postgresql+psycopg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_DATABASE}"
        )

    @property
    def sqlite_uri(self) -> str:
        return f"sqlite:///{self.SQLITE_PATH}"


settings = Settings()
