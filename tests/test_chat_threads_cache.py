# rag_qa_project/tests/test_chat_threads_cache.py
# -*- coding: utf-8 -*-
"""会话列表读缓存：命中不打 MySQL、载荷自校验、降级。全离线（SQLite 内存库 + 假 Redis）。

覆盖三层，缺一层都会留下一个「静默错误」的口子：
1. **行为**：第一次读 miss 回源并写回，第二次读命中（用 SQL 计数据证明真的没查库）；
2. **载荷**：自带 `user_id`/`total`，时间序列化成 ISO 字符串；
3. **安全兜底**：归属不符 / 结构不全（合法 JSON 但 `threads:[{}]`）→ 当 miss + 删坏键，
   绝不把它递给 `ConversationSummary(**)`（那会抛 TypeError → 500，
   恰好违反本期「缓存故障不得升级为业务故障」的核心承诺）。

运行方式（在 rag_qa_project 目录下）：
    python -m pytest tests/test_chat_threads_cache.py -v
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.models import Base
from backend.app.services import chat_service, conversation_service
from backend.tools.redis_cache_tools import conv_list_key


class FakeRedis:
    """协议级替身：只实现被测代码会用到的方法，并按调用顺序记录。

    不用 fakeredis 的原因同 test_redis_cache_tools.py：这里要能**按需注入故障**
    （连接挂掉），且调用序列本身是被断言的对象。
    """

    def __init__(self):
        self.store = {}
        self.calls = []

    async def get(self, key):
        self.calls.append(("get", key))
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.calls.append(("set", key, ex))
        self.store[key] = value

    async def delete(self, key):
        self.calls.append(("delete", key))
        return self.store.pop(key, None)

    async def xadd(self, name, fields, maxlen=None, approximate=True):
        self.calls.append(("xadd", name, dict(fields)))
        return f"{len(self.calls)}-0"


@pytest.fixture
def maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_setup())
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


def _seed(maker, thread_id="t1", user_id=1, question="年假有几天？"):
    async def go():
        async with maker() as db:
            await conversation_service.upsert_conversation(
                db, thread_id=thread_id, user_id=user_id, question=question)
            await db.commit()
    asyncio.run(go())


# ============================ 1. 命中 / 回源 ============================
def test_first_read_misses_then_second_read_hits(maker):
    """第一次 miss 必须写回，第二次才有得命中（写回是命中率的地基）。"""
    _seed(maker)
    redis = FakeRedis()

    async def go():
        async with maker() as db:
            await chat_service.list_chat_threads(1, db, redis=redis)
            n_gets_after_first = len([c for c in redis.calls if c[0] == "get"])
            await chat_service.list_chat_threads(1, db, redis=redis)
            return n_gets_after_first

    asyncio.run(go())

    gets = [c for c in redis.calls if c[0] == "get"]
    assert len(gets) == 2, "两次读各自查一次缓存"
    assert any(c[0] == "set" for c in redis.calls), "第一次必须写回缓存"


def test_hit_path_does_not_touch_mysql(maker):
    """★ 核心收益断言：命中时 MySQL 完全不被查询。

    判据用 SQLAlchemy 的 before_cursor_execute 事件计数 —— 数的是**真的发出去的 SQL**，
    比 mock 会话更接近事实（mock 会话只能证明「我没调它」，证明不了「没走某条别的路」）。
    """
    _seed(maker)
    redis = FakeRedis()
    sql_count = []

    async def go():
        async with maker() as db:
            from sqlalchemy import event
            event.listen(db.bind.sync_engine, "before_cursor_execute",
                         lambda *a, **k: sql_count.append(1))
            await chat_service.list_chat_threads(1, db, redis=redis)   # 第一次：回源
            sql_count.clear()
            await chat_service.list_chat_threads(1, db, redis=redis)   # 第二次：应命中

    asyncio.run(go())

    assert sql_count == [], f"命中缓存时不该有任何 SQL，实际执行了 {len(sql_count)} 条"


# ============================ 2. 载荷结构 ============================
def test_payload_carries_user_id_and_total(maker):
    """载荷必须自带 user_id 与 total：前者用于归属自校验，后者是「仅显示最近 50 条」的依据。"""
    _seed(maker)
    redis = FakeRedis()

    async def go():
        async with maker() as db:
            return await chat_service.list_chat_threads(1, db, redis=redis)

    r = asyncio.run(go())
    cached = json.loads(redis.store[conv_list_key(1)])

    assert cached["user_id"] == 1
    assert cached["total"] == r.total == 1
    assert cached["threads"][0]["thread_id"] == "t1"
    assert isinstance(cached["threads"][0]["created_at"], str), "时间要序列化成 ISO 字符串"


# ============================ 3. 载荷自校验（安全兜底）============================
def test_payload_user_mismatch_is_rejected(maker):
    """★ 安全兜底：缓存里的 user_id 与请求不符（键串了/被人塞了值）→ 当 miss，
    绝不能把别人的列表返回出去。"""
    _seed(maker, thread_id="t-other", user_id=2, question="别人的会话")
    redis = FakeRedis()
    redis.store[conv_list_key(1)] = json.dumps(
        {"user_id": 2, "total": 1,
         "threads": [{"thread_id": "t-other", "title": "别人的会话",
                      "created_at": "2026-09-21T00:00:00+00:00",
                      "updated_at": "2026-09-21T00:00:00+00:00"}]},
        ensure_ascii=False)

    async def go():
        async with maker() as db:
            return await chat_service.list_chat_threads(1, db, redis=redis)

    r = asyncio.run(go())

    assert r.threads == [], "user_id 不符时必须回源（此处 user 1 无会话），不能返回 user 2 的数据"
    assert ("delete", conv_list_key(1)) in redis.calls, "同时要删掉这个可疑的键"


def test_structurally_incomplete_payload_falls_back_to_mysql(maker):
    """★ 元素级校验的必要性：`{"user_id":1,"total":1,"threads":[{}]}` 是**合法 JSON、
    归属也对**，但会被 `ConversationSummary(**)` 抛 TypeError → 500。

    这正是「缓存故障升级成业务故障」的最短路径，所以必须当 miss 而不是相信它：
    ① 不抛异常 ② 回源拿到正确数据 ③ 删掉这个坏键。
    """
    _seed(maker)
    redis = FakeRedis()
    redis.store[conv_list_key(1)] = json.dumps({"user_id": 1, "total": 1, "threads": [{}]})

    async def go():
        async with maker() as db:
            return await chat_service.list_chat_threads(1, db, redis=redis)

    r = asyncio.run(go())                                          # ① 不抛异常

    assert [t.thread_id for t in r.threads] == ["t1"], "② 必须回源拿到 MySQL 里的正确数据"
    assert r.total == 1
    assert ("delete", conv_list_key(1)) in redis.calls, "③ 坏键必须被删掉"


def test_is_valid_payload_rejects_element_missing_fields():
    """元素缺字段即非法（归属与 total 都对也拦）—— 纯函数单测，不用起 DB。"""
    payload = {"user_id": 1, "total": 1,
               "threads": [{"thread_id": "t1", "title": "x"}]}      # 缺 created_at/updated_at

    assert chat_service._is_valid_payload(payload, 1) is False


def test_is_valid_payload_rejects_non_int_total():
    """`total` 必须是整数：它是前端判断「被截断」的唯一依据，字符串形式或缺失都不能放过。"""
    assert chat_service._is_valid_payload({"user_id": 1, "total": "1", "threads": []}, 1) is False
    assert chat_service._is_valid_payload({"user_id": 1, "threads": []}, 1) is False


def test_is_valid_payload_accepts_complete_payload():
    payload = {"user_id": 1, "total": 1,
               "threads": [{"thread_id": "t1", "title": "x",
                            "created_at": "2026-09-21T00:00:00+00:00",
                            "updated_at": "2026-09-21T00:00:00+00:00"}]}

    assert chat_service._is_valid_payload(payload, 1) is True
    assert chat_service._is_valid_payload(payload, 2) is False, "归属必须精确匹配请求用户"
    assert chat_service._is_valid_payload([], 1) is False, "顶层不是 dict 也不能算合法"


# ============================ 4. 降级 ============================
def test_redis_down_degrades_to_mysql(maker):
    """Redis 连不上 → 响应结构与内容不变（缓存是可选依赖，不带崩列表接口）。"""
    _seed(maker)

    class DeadRedis(FakeRedis):
        async def get(self, key):
            raise RedisConnectionError("down")

    async def go():
        async with maker() as db:
            return await chat_service.list_chat_threads(1, db, redis=DeadRedis())

    r = asyncio.run(go())

    assert [t.thread_id for t in r.threads] == ["t1"]
    assert r.total == 1


def test_redis_none_behaves_like_no_cache(maker):
    """未配置 Redis / 开关关闭时 `redis=None`，行为必须与 P3 逐字一致。"""
    _seed(maker)

    async def go():
        async with maker() as db:
            return await chat_service.list_chat_threads(1, db, redis=None)

    r = asyncio.run(go())

    assert [t.thread_id for t in r.threads] == ["t1"]
    assert r.total == 1


# ============================ 5. 端点接线 ============================
def test_endpoint_actually_passes_redis_to_service(maker, monkeypatch):
    """★ 端点必须真的把 redis 透传给服务层，否则就是「加了参数但没用上」。

    tests/test_chat_threads.py 的端点用例走的都是 `redis=None` 分支，证明不了接线；
    这里用 `dependency_overrides` 注入假 Redis（而不是塞真池 —— 那会在离线套件里真连网）。

    断言到「缓存被读、且写回了」这一层：只断言 200 的话，把端点里的 redis 去掉照样绿。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from config import settings as _settings
    from backend.app.api import chat_router
    from backend.app.api.deps import get_optional_redis
    from backend.app.api.errors import register_error_handlers
    from backend.tools import security as sec
    from backend.tools.mysql_db_tools import get_db_session

    _seed(maker)
    redis = FakeRedis()
    monkeypatch.setattr(_settings, "jwt_secret", "unit-test-secret-" + "0" * 26)

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(chat_router.router)

    async def _override_db():
        async with maker() as db:
            yield db
    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_optional_redis] = lambda: redis

    client = TestClient(app)
    client.headers.update({"Authorization": f"Bearer {sec.create_access_token(1, 'alice')}"})

    r = client.get("/api/chat/threads")

    body = r.json()
    assert r.status_code == 200
    assert set(body) == {"threads", "total"}, "契约形状不变"
    assert body["total"] == 1
    assert body["threads"][0]["thread_id"] == "t1"
    assert body["threads"][0]["title"] == "年假有几天？"      # 首问前 20 字
    assert body["threads"][0]["created_at"].endswith("Z"), "契约要带 Z 的 ISO 8601"
    assert ("get", conv_list_key(1)) in redis.calls, "端点没把 redis 交给服务层"
    assert redis.store.get(conv_list_key(1)), "服务层必须写回缓存"
