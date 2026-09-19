# rag_qa_project/tests/test_chat_ownership.py
# -*- coding: utf-8 -*-
"""chat 三端点的鉴权与归属校验（计划 Task 7）。

本文件是 Task 7 缺的那个测试文件，而它缺席的代价已经实际发生了两次：
`get_chat_history` 的 service 签名没跟着路由更新（路由传 3 个参数、service 收 1 个，
线上 500），以及删除会话漏删 `conversations` 索引行。两者都在「没有 HTTP 层用例」
的盲区里 —— 既有的 test_chat_stream.py 直连 service 层，签名对得上所以照常通过。

所以本文件一律走 **HTTP 路由**，不直连 service：这是唯一能发现「路由与 service
签名不一致」的层次。
"""
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from backend.app.agent.graph import build_graph
from backend.app.api import chat_router
from backend.app.api.errors import register_error_handlers
from backend.app.models import Base, Conversation
from backend.app.services import chat_service
from backend.tools import security as sec
from backend.tools.mysql_db_tools import get_db_session
from test_graph import FakeRAGModel, FakeRetriever      # 复用既有假件，避免重复实现


# ============================ 测试替身（Fakes） ============================
@pytest.fixture
def db_maker(monkeypatch):
    """SQLite 内存库替身 + 打桩密钥。用于覆盖 get_db_session 与直接播种会话索引行。"""
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


@pytest.fixture
def fake_graph(monkeypatch):
    """把 chat_service.get_graph 换成假件编译的真图。

    目的有三：不触网/不耗额度、不落真实 data/checkpoints.db、以及让「图状态」在
    每个用例里都是干净的（InMemorySaver 随 fixture 重建）。
    """
    graph = build_graph(model=FakeRAGModel(responses=[], relevance_mode="all"),
                        retriever=FakeRetriever(), checkpointer=InMemorySaver())
    monkeypatch.setattr(chat_service, "get_graph", lambda: graph)
    return graph


@pytest.fixture
def client(db_maker, fake_graph):
    """只挂 chat_router 的测试 app：走真实路由，才能抓到路由与 service 的契约错配。"""
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(chat_router.router)

    async def _override():
        async with db_maker() as s:
            yield s
    app.dependency_overrides[get_db_session] = _override

    return TestClient(app, raise_server_exceptions=False)


def _headers(user_id: int = 1, username: str = "alice") -> dict:
    return {"Authorization": f"Bearer {sec.create_access_token(user_id, username)}"}


def _seed_thread(maker, thread_id: str, user_id: int, title: str = "首问") -> None:
    """直接在 conversations 里插一行，模拟「该会话已属于某人」。"""
    async def go():
        async with maker() as db:
            now = datetime.now(UTC)
            db.add(Conversation(thread_id=thread_id, user_id=user_id, title=title,
                                created_at=now, updated_at=now))
            await db.commit()
    asyncio.run(go())


def _conversation(maker, thread_id: str):
    async def go():
        async with maker() as db:
            return await db.get(Conversation, thread_id)
    return asyncio.run(go())


def _stream(client, thread_id: str, question: str = "LangGraph 怎么做持久化？",
            headers: dict | None = None):
    return client.post("/api/chat/stream",
                       json={"question": question, "thread_id": thread_id},
                       headers=headers or _headers())


# ============================ 1. 三个端点都要求登录 ============================
def test_stream_without_token_401(client):
    r = client.post("/api/chat/stream", json={"question": "q", "thread_id": "t1"})

    assert r.status_code == 401
    assert set(r.json()) == {"code", "message"}
    assert r.json()["code"] == "UNAUTHORIZED"


def test_history_without_token_401(client):
    r = client.get("/api/chat/history", params={"thread_id": "t1"})

    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def test_delete_without_token_401(client):
    r = client.delete("/api/chat/threads/t1")

    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


# ============================ 2. 归属校验：403 ============================
def test_stream_other_users_thread_403_before_stream(client, db_maker):
    """★ 403 必须在流开始**之前**抛出。

    响应头一旦发出就改不了状态码，只能降级成 SSE error 帧，前端就得为同一个端点
    写两套错误处理。用「Content-Type 不是 text/event-stream」来证明这一点。
    """
    _seed_thread(db_maker, "t-alice", user_id=1)

    r = _stream(client, "t-alice", headers=_headers(2, "bob"))

    assert r.status_code == 403
    assert r.json()["code"] == "FORBIDDEN"
    assert "text/event-stream" not in r.headers.get("content-type", ""), \
        "403 若发生在流内，就说明鉴权被推迟到了 StreamingResponse 之后"


def test_history_of_other_users_thread_403(client, db_maker):
    _seed_thread(db_maker, "t-alice", user_id=1)

    r = client.get("/api/chat/history", params={"thread_id": "t-alice"},
                   headers=_headers(2, "bob"))

    assert r.status_code == 403
    assert r.json()["code"] == "FORBIDDEN"


def test_history_403_does_not_depend_on_checkpointer_readability(client, db_maker, monkeypatch):
    """★ 归属校验必须先于读 checkpoint。

    顺序反了会怎样：读 checkpoint 失败（库损坏、saver 不可用）时抛出的是 500 而不是
    403，于是攻击者能靠**状态码差异**判断某个 thread_id 是否存在 —— 403 说明「存在但
    不是你的」，500 说明「存在但你查不到」。这类存在性泄漏正是 403/404 要抹平的。

    用「让 get_graph 直接抛异常」来验证顺序：403 若先发生，这个异常永远不会被触发。
    """
    _seed_thread(db_maker, "t-alice", user_id=1)

    def boom():
        raise RuntimeError("checkpointer 不可用")
    monkeypatch.setattr(chat_service, "get_graph", boom)

    r = client.get("/api/chat/history", params={"thread_id": "t-alice"},
                   headers=_headers(2, "bob"))

    assert r.status_code == 403, "越权判定不该受 checkpointer 是否可读影响"
    assert r.json()["code"] == "FORBIDDEN"


def test_history_of_unknown_thread_returns_empty_200(client):
    """★ 守既有约定：库里没有这一行 = 新会话，必须 200 + 空数组。

    若这里改判 403，前端用本地生成的 uuid 第一次打开会话时就永远打不开 ——
    归属校验只对「已有行且不是本人」才拒绝。
    """
    r = client.get("/api/chat/history", params={"thread_id": "brand-new"},
                   headers=_headers(1, "alice"))

    assert r.status_code == 200
    assert r.json()["thread_id"] == "brand-new"
    assert r.json()["messages"] == []


def test_delete_other_users_thread_403_and_leaves_data_intact(client, db_maker):
    """★ 越权请求不得产生任何副作用：索引行必须原样还在。"""
    _seed_thread(db_maker, "t-alice", user_id=1)

    r = client.delete("/api/chat/threads/t-alice", headers=_headers(2, "bob"))

    assert r.status_code == 403
    assert r.json()["code"] == "FORBIDDEN"
    assert _conversation(db_maker, "t-alice") is not None, \
        "403 之后不能改动数据"


# ============================ 3. 会话索引行的写入与清理 ============================
def test_stream_creates_conversation_row_with_first_question_as_title(client, db_maker):
    r = _stream(client, "t-new", question="年假有几天？请详细说明", headers=_headers(7, "alice"))

    assert r.status_code == 200, r.text[:200]
    row = _conversation(db_maker, "t-new")
    assert row is not None, "首次提问必须把归属落库，否则后续无从校验"
    assert row.user_id == 7
    assert row.title == "年假有几天？请详细说明"[:20]


def test_stream_second_question_keeps_first_title(client, db_maker):
    """title 只在首问写入（§3 决策 6）：避免标题随对话内容漂移。"""
    _stream(client, "t-2", question="第一个问题", headers=_headers(7, "alice"))
    _stream(client, "t-2", question="第二个完全不同的问题", headers=_headers(7, "alice"))

    row = _conversation(db_maker, "t-2")
    assert row.title == "第一个问题"


def test_delete_own_thread_removes_conversation_row(client, db_maker):
    """★ 删除必须连索引行一起清掉。

    漏了就留孤儿行 —— P3 的会话列表正是查这张表，孤儿行会让**已删会话重新出现在
    侧栏**，点进去又是空的。这条断言就是为这个场景写的。
    """
    _stream(client, "t-del", headers=_headers(7, "alice"))
    assert _conversation(db_maker, "t-del") is not None, "前置条件：先有索引行"

    r = client.delete("/api/chat/threads/t-del", headers=_headers(7, "alice"))

    assert r.status_code == 200
    assert r.json() == {"thread_id": "t-del", "deleted": True}
    assert _conversation(db_maker, "t-del") is None, "索引行必须一起删掉"


def test_delete_is_idempotent_for_unknown_thread(client):
    """未知 thread_id 返回 deleted=false 而非 404（契约如此），也不得抛异常。"""
    r = client.delete("/api/chat/threads/never-existed", headers=_headers(1, "alice"))

    assert r.status_code == 200
    assert r.json() == {"thread_id": "never-existed", "deleted": False}
