# rag_qa_project/tests/test_auth_service.py
# -*- coding: utf-8 -*-
"""注册与登录的业务逻辑（计划 Task 5）。全离线：SQLite 内存库替身 + 打桩 jwt_secret。

本文件是 Task 5 缺的那个测试文件。它缺席的代价是实际存在的：`register` 抛的
`409 BAD_REQUEST` 与契约要求的 `USERNAME_TAKEN` 不一致，四处文档/注释都写着后者，
却没有任何用例把这条钉住。
"""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from backend.app.models import Base, User
from backend.app.services import auth_service
from backend.tools import security as sec


@pytest.fixture
def sessionmaker_(monkeypatch):
    """SQLite 内存库 + 打桩密钥：别让用例依赖真实 .env 与真实 MySQL。"""
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret-" + "0" * 26)
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False})

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_setup())

    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    asyncio.run(engine.dispose())


def _run(maker, coro_factory):
    """开一个 session 跑一段协程工厂；工厂收 db、返回协程。"""
    async def go():
        async with maker() as db:
            return await coro_factory(db)
    return asyncio.run(go())


async def _register(db, username="hao", password="abcd1234"):
    return await auth_service.register(db, username=username, password=password)


# ============================ 1. 注册 ============================
def test_register_stores_bcrypt_hash_not_plaintext(sessionmaker_):
    """密码必须落成 bcrypt 哈希：不是明文、以 $2b$ 开头、长度固定 60。"""
    async def go(db):
        await _register(db)
        return await db.get(User, 1)
    user = _run(sessionmaker_, go)

    assert user.username == "hao"
    assert user.password_hash != "abcd1234"
    assert user.password_hash.startswith("$2b$")
    assert len(user.password_hash) == 60


def test_register_returns_id_and_username(sessionmaker_):
    au = _run(sessionmaker_, lambda db: _register(db))

    assert (au.id, au.username) == (1, "hao")


def test_register_duplicate_username_raises_409_username_taken(sessionmaker_):
    """★ 契约 §6.4：重名的 code 必须是 `USERNAME_TAKEN`。

    写成笼统的 `BAD_REQUEST` 会让前端按 `USERNAME_TAKEN` 分支渲染注册错误行的逻辑
    永远走不到（spec §8 的映射表就是那么写的）。
    """
    async def go(db):
        await _register(db)
        await _register(db, password="other9999")

    with pytest.raises(HTTPException) as ei:
        _run(sessionmaker_, go)

    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "USERNAME_TAKEN"


@pytest.mark.skip(reason="需要 MySQL 的 utf8mb4_0900_ai_ci 排序规则；SQLite 默认大小写敏感，"
                         "该行为在本地替身上不成立。留给 plan §7.7 手工验收第 3 步")
def test_register_duplicate_username_is_case_insensitive_on_mysql():
    """MySQL 上 Alice 与 alice 应撞唯一键（排序规则不区分大小写）。"""


# ============================ 2. 登录 ============================
def test_login_returns_token_and_user(sessionmaker_):
    async def go(db):
        await _register(db)
        return await auth_service.login(db, username="hao", password="abcd1234")
    res = _run(sessionmaker_, go)

    assert res.token_type == "bearer"
    assert res.user.username == "hao"
    assert res.user.id == 1
    assert sec.decode_token(res.access_token)["sub"] == "1", "sub 必须是字符串形式的主键"


def test_login_wrong_password_401_invalid_credentials(sessionmaker_):
    async def go(db):
        await _register(db)
        return await auth_service.login(db, username="hao", password="wrongpass")

    with pytest.raises(HTTPException) as ei:
        _run(sessionmaker_, go)

    assert ei.value.status_code == 401
    assert ei.value.detail["code"] == "INVALID_CREDENTIALS"


def test_login_unknown_user_has_identical_message_to_wrong_password(sessionmaker_):
    """★ 防用户名枚举（spec §6.4）：两种失败的状态码、code、message 必须逐字相同。

    只要文案有一丝差别，攻击者就能靠它判断某个用户名是否已注册。
    """
    async def wrong_password(db):
        await _register(db)
        return await auth_service.login(db, username="hao", password="wrongpass")

    with pytest.raises(HTTPException) as e1:
        _run(sessionmaker_, wrong_password)
    with pytest.raises(HTTPException) as e2:
        _run(sessionmaker_, lambda db: auth_service.login(
            db, username="nobody", password="abcd1234"))

    assert e1.value.status_code == e2.value.status_code == 401
    assert e1.value.detail["code"] == e2.value.detail["code"] == "INVALID_CREDENTIALS"
    assert e1.value.detail["message"] == e2.value.detail["message"], \
        "两种失败的文案必须完全一致，否则可被用来枚举用户名"


def test_login_token_carries_the_users_own_id(sessionmaker_):
    """第二个用户登录拿到的 sub 必须是 2 而不是 1 —— 防「登录谁都发同一个 token」。"""
    async def go(db):
        await _register(db, username="alice")
        await _register(db, username="bob", password="bobpass123")
        return await auth_service.login(db, username="bob", password="bobpass123")

    res = _run(sessionmaker_, go)
    assert sec.decode_token(res.access_token)["sub"] == "2"
