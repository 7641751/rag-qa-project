# -*- coding: utf-8 -*-
"""P3 会话列表与重命名：服务层 + 端点契约。全离线（SQLite 替身 + TestClient）。

设计思想
--------
分两层守：
1. **服务层**（`list_conversations` / `rename_conversation`）—— 排序、50 条上限、
   total 口径、只返回本人、403/404 的分工、以及「重命名不动 updated_at」；
2. **端点契约** —— 状态码、响应体形状、以及 `created_at` 的 ISO 8601 **必须带 Z**
   （不带的话 JS `new Date()` 会按本地时区解析，相对时间整体偏掉一个时区）。

运行方式（在 rag_qa_project 目录下）：
    python -m pytest tests/test_chat_threads.py -v
"""
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from backend.app.api import chat_router
from backend.app.api.errors import register_error_handlers
from backend.app.models import Base, Conversation
from backend.app.services import conversation_service as cs
from backend.tools import security as sec
from backend.tools.mysql_db_tools import get_db_session

ALICE, BOB = 1, 2
BASE = datetime(2026, 9, 19, 6, 0, tzinfo=UTC)


def _t(minutes: int) -> datetime:
    return BASE + timedelta(minutes=minutes)


# ============================ 夹具 ============================
@pytest.fixture
def maker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False})

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_setup())

    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


def _seed(maker, rows):
    """rows: [(thread_id, user_id, title, updated_at)]"""
    async def go():
        async with maker() as db:
            for tid, uid, title, upd in rows:
                db.add(Conversation(thread_id=tid, user_id=uid, title=title,
                                    created_at=upd, updated_at=upd))
            await db.commit()
    asyncio.run(go())


def _run(maker, factory):
    async def go():
        async with maker() as db:
            return await factory(db)
    return asyncio.run(go())


@pytest.fixture
def client(maker, monkeypatch):
    """挂 chat_router 的测试应用，DB 会话换成内存 SQLite。

    默认身份是 alice(1)；要换用户就传 headers。
    """
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret-" + "0" * 26)
    app = FastAPI()
    register_error_handlers(app)          # 否则 403/404 会变成 FastAPI 默认的 {"detail": ...}
    app.include_router(chat_router.router)

    async def _override():
        async with maker() as db:
            yield db
    app.dependency_overrides[get_db_session] = _override

    c = TestClient(app, raise_server_exceptions=False)
    c.headers.update({"Authorization": f"Bearer {sec.create_access_token(ALICE, 'alice')}"})
    return c


def _token(uid: int, name: str) -> dict:
    return {"Authorization": f"Bearer {sec.create_access_token(uid, name)}"}


# ============================ 1. 服务层：列表 ============================
def test_list_only_own_and_ordered_by_updated_desc(maker):
    """只返回本人的会话，按 updated_at **倒序**（最近提问的在最前）。"""
    _seed(maker, [
        ("t-a-old", ALICE, "A 的旧会话", _t(0)),
        ("t-a-new", ALICE, "A 的新会话", _t(30)),
        ("t-b", BOB, "B 的会话", _t(60)),
    ])

    rows, total = _run(maker, lambda db: cs.list_conversations(db, ALICE))

    assert [r.thread_id for r in rows] == ["t-a-new", "t-a-old"]   # 倒序
    assert total == 2, "total 必须是本人的真实总数，不含别人的"
    assert all(r.user_id == ALICE for r in rows)


def test_list_empty_returns_empty_list_and_zero(maker):
    """无会话 → ([], 0)，不是抛异常/None（空列表是 200 而不是 404）。"""
    rows, total = _run(maker, lambda db: cs.list_conversations(db, ALICE))

    assert rows == []
    assert total == 0


def test_list_caps_at_50_but_total_is_real(maker):
    """★ 造 55 条 → 返回 50 条、total=55。

    total 是前端显示「仅显示最近 50 条」的**唯一依据**：只回 50 条而不给 total，
    前端无从判断是被截断还是真的只有这么多。
    """
    _seed(maker, [(f"t{i:02d}", ALICE, f"会话 {i}", _t(i)) for i in range(55)])

    rows, total = _run(maker, lambda db: cs.list_conversations(db, ALICE))

    assert len(rows) == 50
    assert total == 55
    # 截断的是**最旧**的 5 条：留下的应是 updated_at 最大的 50 个
    assert rows[0].thread_id == "t54"
    assert "t00" not in {r.thread_id for r in rows}


# ============================ 2. 服务层：重命名 ============================
def test_rename_updates_title(maker):
    _seed(maker, [("t1", ALICE, "旧标题", _t(0))])

    row = _run(maker, lambda db: cs.rename_conversation(db, "t1", ALICE, "新标题"))

    assert row.title == "新标题"
    again = _run(maker, lambda db: db.get(Conversation, "t1"))
    assert again.title == "新标题", "必须真的落库"


def test_rename_does_not_touch_updated_at(maker):
    """重命名**不改** updated_at —— 它不是新活动，不该把会话顶到列表最前。

    模型上挂着 onupdate=_utcnow，所以这条断言不是多余的：只要这行有别的脏属性，
    SQLAlchemy 就会发出 UPDATE，onupdate 会把 updated_at 一起刷新掉。
    """
    _seed(maker, [("t1", ALICE, "旧标题", _t(0))])

    _run(maker, lambda db: cs.rename_conversation(db, "t1", ALICE, "新标题"))

    after = _run(maker, lambda db: db.get(Conversation, "t1"))
    # 库往返会剥掉 tzinfo（MySQL DATETIME 不带时区，写入的是 UTC 裸值），所以与 naive 值比。
    # 断言的是**精确等于种下的旧时间**：一旦 onupdate 生效，这里会变成"现在"而立刻红。
    assert after.updated_at == _t(0).replace(tzinfo=None), \
        "updated_at 被 onupdate 刷新了，列表排序语义会被破坏"


def test_rename_other_users_thread_403(maker):
    """非本人 → 403 FORBIDDEN（契约 §7.1）。

    ⚠ 刻意**不是 404**：会话 id 由前端生成并存在于 URL 里，用户本来就知道它；
    这里的 403 只是告诉他自己没有权限，不构成额外的信息泄露。
    这与「删别人的文档 → 404」的选择不同，因为那个 404 是为了不泄露**存在性**。
    """
    _seed(maker, [("t-b", BOB, "B 的会话", _t(0))])

    with pytest.raises(Exception) as ei:
        _run(maker, lambda db: cs.rename_conversation(db, "t-b", ALICE, "改掉"))
    assert getattr(ei.value, "status_code", None) == 403

    after = _run(maker, lambda db: db.get(Conversation, "t-b"))
    assert after.title == "B 的会话", "403 不得产生任何副作用"


def test_rename_missing_thread_404(maker):
    """不存在的会话 → 404（与「非本人 403」区分开）。"""
    with pytest.raises(Exception) as ei:
        _run(maker, lambda db: cs.rename_conversation(db, "nope", ALICE, "x"))

    assert getattr(ei.value, "status_code", None) == 404


# ============================ 3. 端点：GET /api/chat/threads ============================
def test_get_threads_requires_token(client):
    r = TestClient(client.app).get("/api/chat/threads")
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def test_get_threads_contract(client, maker):
    """响应体形状 + 只含本人 + 按倒序。"""
    _seed(maker, [("t-a", ALICE, "A 的会话", _t(0)), ("t-b", BOB, "B 的会话", _t(30))])

    r = client.get("/api/chat/threads")

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"threads", "total"}
    assert body["total"] == 1
    assert body["threads"] == [{
        "thread_id": "t-a", "title": "A 的会话",
        "created_at": "2026-09-19T06:00:00Z", "updated_at": "2026-09-19T06:00:00Z",
    }]


def test_get_threads_empty_returns_200_not_404(client):
    """空列表是 200 + 空数组（前端新用户进来就是这个状态，不能当异常）。"""
    r = client.get("/api/chat/threads")

    assert r.status_code == 200
    assert r.json() == {"threads": [], "total": 0}


# ============================ 4. 端点：PATCH 重命名 ============================
def test_patch_rename_own_thread(client, maker):
    _seed(maker, [("t-a", ALICE, "旧标题", _t(0))])

    r = client.patch("/api/chat/threads/t-a", json={"title": "新标题"})

    assert r.status_code == 200
    assert r.json() == {"thread_id": "t-a", "title": "新标题"}


def test_patch_rename_other_users_thread_403(client, maker):
    _seed(maker, [("t-b", BOB, "B 的会话", _t(0))])

    r = client.patch("/api/chat/threads/t-b", json={"title": "改掉"})

    assert r.status_code == 403
    assert r.json()["code"] == "FORBIDDEN"


def test_patch_rename_missing_404(client):
    r = client.patch("/api/chat/threads/nope", json={"title": "x"})

    assert r.status_code == 404
    assert r.json()["code"] == "NOT_FOUND"


@pytest.mark.parametrize("bad", ["", "   ", "字" * 61])
def test_patch_rename_invalid_title_422(client, maker, bad):
    """空标题 / 纯空格 / 超 60 字 → 422，且错误体是 {code,message} 而非 {detail:[...]}。

    纯空格必须被拦下：`min_length=1` 只挡空串，"   " 能通过 schema 校验，
    但存进去就是一个**看起来空白**的会话标题。
    """
    _seed(maker, [("t-a", ALICE, "旧标题", _t(0))])

    r = client.patch("/api/chat/threads/t-a", json={"title": bad})

    assert r.status_code == 422
    assert set(r.json()) == {"code", "message"}
    after = _run(maker, lambda db: db.get(Conversation, "t-a"))
    assert after.title == "旧标题", "422 不得产生任何副作用"


def test_patch_rename_trims_whitespace(client, maker):
    """标题两端的空白要被去掉 —— 否则库里存的是 "  x  "，列表里看着像有缩进。"""
    _seed(maker, [("t-a", ALICE, "旧标题", _t(0))])

    r = client.patch("/api/chat/threads/t-a", json={"title": "  新标题  "})

    assert r.status_code == 200
    assert r.json()["title"] == "新标题"
