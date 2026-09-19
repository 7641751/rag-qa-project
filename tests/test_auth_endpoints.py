# rag_qa_project/tests/test_auth_endpoints.py
# -*- coding: utf-8 -*-
"""auth 三端点的 HTTP 层契约（计划 Task 6）。

分工：test_auth_service.py 覆盖业务逻辑，本文件只管「HTTP 出去长什么样」——
状态码与错误体形状，尤其是 422 必须是 {code, message} 而不是 FastAPI 默认的
detail 数组（那是本阶段专门修掉的既有契约违背）。
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from backend.app.api import auth_router
from backend.app.api.errors import register_error_handlers
from backend.app.models import Base
from backend.tools.mysql_db_tools import get_db_session


@pytest.fixture
def client(monkeypatch):
    """独立 app + SQLite 会话覆盖：不 import 真实 main（避免拉起 chroma/LLM 与真实 MySQL）。"""
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret-" + "0" * 26)

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False})

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_setup())

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with maker() as s:
            yield s

    app = FastAPI()
    register_error_handlers(app)      # 契约形状靠它：不加则 422/401 会变成 {"detail": ...}
    app.include_router(auth_router.router)
    app.dependency_overrides[get_db_session] = _override

    yield TestClient(app, raise_server_exceptions=False)
    asyncio.run(engine.dispose())


def _register(client, username="hao", password="abcd1234"):
    return client.post("/api/auth/register",
                       json={"username": username, "password": password})


def _login(client, username="hao", password="abcd1234"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


# ============================ 1. 注册 ============================
def test_register_201_returns_id_and_username(client):
    r = _register(client)

    assert r.status_code == 201
    assert r.json() == {"id": 1, "username": "hao"}


def test_register_duplicate_409_contract_shape(client):
    _register(client)
    r = _register(client)

    assert r.status_code == 409
    assert set(r.json()) == {"code", "message"}
    assert r.json()["code"] == "USERNAME_TAKEN"


def test_register_short_username_422_contract_shape(client):
    """★ 本阶段修掉的既有违背：422 必须是 {code, message}，不是 detail 数组。"""
    r = _register(client, username="ab")

    assert r.status_code == 422
    assert set(r.json()) == {"code", "message"}
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert "username" in r.json()["message"], "message 要指出是哪个字段不合格"


def test_register_password_over_72_bytes_422(client):
    """30 个汉字 = 90 字节 > bcrypt 的 72 字节硬上限。

    schema 层只卡 min_length（故意不写 max_length，让字节上限只有一处真相源），
    所以这条必须由 hash_password 拦下并回 422 —— 绝不能静默截断。
    """
    r = _register(client, password="汉" * 30)

    assert r.status_code == 422
    assert set(r.json()) == {"code", "message"}
    assert r.json()["code"] == "VALIDATION_ERROR"


def test_register_422_leaves_no_user_behind(client):
    """校验失败不能留下半条用户记录 —— 否则占着用户名却无法登录。"""
    _register(client, username="ab")

    assert _login(client, username="ab", password="abcd1234").status_code == 422


# ============================ 2. 登录与 /me ============================
def test_login_200_returns_token_and_user(client):
    _register(client)
    r = _login(client)

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"access_token", "token_type", "user"}
    assert body["token_type"] == "bearer"
    assert body["user"] == {"id": 1, "username": "hao"}


def test_login_wrong_password_401_contract_shape(client):
    _register(client)
    r = _login(client, password="wrongpass")

    assert r.status_code == 401
    assert set(r.json()) == {"code", "message"}
    assert r.json()["code"] == "INVALID_CREDENTIALS"


def test_me_without_token_401(client):
    r = client.get("/api/auth/me")

    assert r.status_code == 401
    assert set(r.json()) == {"code", "message"}
    assert r.json()["code"] == "UNAUTHORIZED"


def test_me_with_token_200(client):
    _register(client)
    token = _login(client).json()["access_token"]

    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert r.json() == {"id": 1, "username": "hao"}


def test_me_with_expired_token_401(client):
    """过期 token 与篡改 token 对客户端是同一件事：重新登录。"""
    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {"sub": "1", "username": "hao",
         "iat": now - timedelta(days=2), "exp": now - timedelta(days=1)},
        settings.jwt_secret, algorithm=settings.jwt_algorithm)

    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"
