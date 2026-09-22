# rag_qa_project/tests/test_chat_threads_cache.py
# -*- coding: utf-8 -*-
"""会话列表读缓存：命中不打 MySQL、载荷自校验、降级。全离线（SQLite 内存库 + 假 Redis）。

覆盖三层，缺一层都会留下一个「静默错误」的口子：
1. **行为**：第一次读 miss 回源并写回，第二次读命中（用 SQL 计数据证明真的没查库）；
2. **载荷**：自带 `user_id`/`total`，时间序列化成 ISO 字符串；
3. **安全兜底**：归属不符 / 结构不全（合法 JSON 但 `threads:[{}]`）/ **字段齐全但类型
   或可解析性不符**（`created_at:"not-a-date"`）→ 一律当 miss + 删坏键，绝不把它递给
   `ConversationSummary(**)`（那会抛 ValidationError → 落到默认 500，且坏键不被删，
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

from backend.app.agent.schemas import ChatRequest
from backend.app.models import Base, Conversation
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

    # xadd 本文件当前无人调用，保留它是给 Task 7（事件流写 Redis Stream）复用 —— 别删。
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
            await chat_service.list_chat_threads(1, db, redis=redis)

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
    归属也对**，只有元素缺字段 —— 若放行，它会直捣 `ConversationSummary(**)`。

    这正是「缓存故障升级成业务故障」的最短路径，所以必须当 miss 而不是相信它：
    ① 不抛异常 ② 回源拿到正确数据 ③ 删掉这个坏键。

    ⚠ 更正：这条被**第一层**（`_is_valid_payload` 的元素级检查）拦下，**根本到不了 pydantic**；
    真正会抵达 pydantic 并抛异常的是「字段齐全但类型不符」那类 —— 见下面
    `test_payload_with_unparseable_timestamp_falls_back_to_mysql`（实测抛的是
    `ValidationError` 而非 `TypeError`）。
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
    # I1：这条路刻意**不回填**（回填要多一条写路径、多一处可能重新写坏的代码），
    # 代价仅是「下一次请求 miss 一次、回源 1ms」。
    assert not any(c[0] == "set" for c in redis.calls), "自校验失败路径不得回填缓存"


def test_payload_with_unparseable_timestamp_falls_back_to_mysql(maker):
    """★ 字段齐全但**类型/可解析性**不符的载荷也必须当 miss（C1，质量审查实测发现）。

    与上一个用例（缺字段）的关键差别：`_is_valid_payload` 只查「四个字段在不在」，
    所以 `created_at: "not-a-date"` 会被它判**合法** → 直接递给 `ConversationSummary(**)`
    → pydantic 抛 ValidationError（MRO: ValidationError → ValueError），而 errors.py 的
    两个 handler 都不接 ValueError → 落到 Starlette 默认 500。

    危害不止一次 500：该键不会被删 ⇒ 若它来自外部 SET（redis-cli 调试 / 多实例 / 另一份
    代码）且**没带 TTL**，这个用户的列表接口会**永久 500** 直到人工清理。所以必须断言
    「坏键被删」—— 那是自愈的唯一保证。
    """
    _seed(maker)
    redis = FakeRedis()
    redis.store[conv_list_key(1)] = json.dumps(
        {"user_id": 1, "total": 1,
         "threads": [{"thread_id": "t1", "title": "年假有几天？",
                      "created_at": "not-a-date",                 # 合法 JSON、字段齐全，但解析不出来
                      "updated_at": "2026-09-21T00:00:00+00:00"}]},
        ensure_ascii=False)

    async def go():
        async with maker() as db:
            return await chat_service.list_chat_threads(1, db, redis=redis)

    r = asyncio.run(go())                                          # ① 不抛异常

    assert [t.thread_id for t in r.threads] == ["t1"], "② 必须回源拿到 MySQL 里的正确数据"
    assert r.total == 1
    assert ("delete", conv_list_key(1)) in redis.calls, "③ 坏键必须被删掉（自愈的唯一保证）"
    # I1：这条路刻意**不回填**（回填要多一条写路径、多一处可能重新写坏的代码）；
    # 代价仅是「下一次请求 miss 一次、回源 1ms」，换来的是这里永远不会再写坏一次。
    assert not any(c[0] == "set" for c in redis.calls), "自校验失败路径不得回填缓存"
    assert conv_list_key(1) not in redis.store


def test_payload_with_wrong_element_type_falls_back_to_mysql(maker):
    """★ 另一条实测路径：字段齐全但**类型不符**（`title` 是 dict 而不是 str）—— 同样必须当 miss。

    与时间戳用例是同一根因（`_is_valid_payload` 只看字段「在不在」，不看「对不对」），
    但走的是不同的 pydantic 校验分支（string_type vs datetime_from_date_parsing），
    一起覆盖才能证明第二层 try/except 真的以 pydantic 为准、而非只为某一个字段打的补丁。
    """
    _seed(maker)
    redis = FakeRedis()
    redis.store[conv_list_key(1)] = json.dumps(
        {"user_id": 1, "total": 1,
         "threads": [{"thread_id": "t1", "title": {"a": 1},             # 类型不符
                      "created_at": "2026-09-21T00:00:00+00:00",
                      "updated_at": "2026-09-21T00:00:00+00:00"}]},
        ensure_ascii=False)

    async def go():
        async with maker() as db:
            return await chat_service.list_chat_threads(1, db, redis=redis)

    r = asyncio.run(go())

    assert [t.thread_id for t in r.threads] == ["t1"], "必须回源拿到 MySQL 里的正确数据"
    assert r.threads[0].title == "年假有几天？"
    assert ("delete", conv_list_key(1)) in redis.calls, "坏键必须被删掉（自愈）"


def test_is_valid_payload_rejects_element_missing_fields():
    """元素缺字段即非法（归属与 total 都对也拦）—— 纯函数单测，不用起 DB。"""
    payload = {"user_id": 1, "total": 1,
               "threads": [{"thread_id": "t1", "title": "x"}]}      # 缺 created_at/updated_at

    assert chat_service._is_valid_payload(payload, 1) is False


def test_is_valid_payload_rejects_non_int_total():
    """`total` 必须是**真 int**：它是前端判断「被截断」的唯一依据，字符串形式或缺失都不能放过。

    `True` 这一条专门盯 `isinstance(True, int) is True` 这个陷阱 —— 用 `isinstance` 写会把
    bool 放行，只有 `type(x) is int` 关得掉。
    """
    assert chat_service._is_valid_payload({"user_id": 1, "total": "1", "threads": []}, 1) is False
    assert chat_service._is_valid_payload({"user_id": 1, "threads": []}, 1) is False
    assert chat_service._is_valid_payload({"user_id": 1, "total": True, "threads": []}, 1) is False


def test_is_valid_payload_accepts_complete_payload():
    payload = {"user_id": 1, "total": 1,
               "threads": [{"thread_id": "t1", "title": "x",
                            "created_at": "2026-09-21T00:00:00+00:00",
                            "updated_at": "2026-09-21T00:00:00+00:00"}]}

    assert chat_service._is_valid_payload(payload, 1) is True
    assert chat_service._is_valid_payload(payload, 2) is False, "归属必须精确匹配请求用户"
    assert chat_service._is_valid_payload([], 1) is False, "顶层不是 dict 也不能算合法"


def test_required_fields_match_schema():
    """I2：`_REQUIRED_THREAD_FIELDS` 与 `ConversationSummary` 之间没有类型层面的同步机制。

    将来给 schema 加一个必填字段时，这份手写清单会**悄悄落后** —— 落后本身不报错，
    代价是「新字段缺失的缓存载荷」又能穿过去、直捣 `ConversationSummary(**)` 拿 500。
    这条断言把「同步」从口头约定变成会红的测试。
    """
    from backend.app.agent.schemas import ConversationSummary
    # ⚠ 只比对**必填**字段：`model_fields` 含带默认值的可选字段。若拿全部字段比对，
    # 将来给 schema 加一个 `extra: X | None = None` 时这条会红，并把开发者推去把 optional
    # 塞进「必填清单」—— 那会让第一层校验拒绝**所有**合法载荷，缓存直接形同虚设。
    required = {name for name, f in ConversationSummary.model_fields.items() if f.is_required()}
    assert set(chat_service._REQUIRED_THREAD_FIELDS) == required


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


# ============================ 6. 写路径失效（顺序是契约）============================
@pytest.fixture
def fake_graph(monkeypatch):
    """把 `chat_service.get_graph` 换成只有 checkpointer 的替身。

    本节的焦点是「MySQL commit 与 Redis DEL 的先后」，`delete_chat_thread` 里的
    checkpoint 删除只是必经的一段噪声。不换的话会走到真实的 `AsyncSqliteSaver`
    （默认写 `data/checkpoints.db`）—— 离线套件不该碰真实文件（与
    tests/test_chat_ownership.py 的 fake_graph 同一出发点）。
    InMemorySaver 是纯内存实现，不需要编译整张图，因此这里只造 graph.checkpointer 这一处。
    """
    from langgraph.checkpoint.memory import InMemorySaver

    class _StubGraph:
        checkpointer = InMemorySaver()

    monkeypatch.setattr(chat_service, "get_graph", lambda: _StubGraph())


def _assert_commit_immediately_followed_by_delete(order, key, paths: int = 3):
    """断言序列里每个 `commit` 的**紧邻下一个**动作都是 `delete`。

    为什么用「同一序列」而不是「两边各自断言都发生过」：后者在「先 DEL 后 commit」
    的实现下**照样绿**（两种动作都发生过），而那种顺序会把旧值留在库里、缓存已空，
    并发读会把旧值回填缓存 —— 那是本期最贵的一种错（要等满 TTL 才自愈）。
    把两个来源拷进同一个 list，才断言得了「谁紧挨着谁」。
    """
    seq = [x if isinstance(x, str) else x[0] for x in order]
    assert seq.count("commit") == paths, f"应恰好 {paths} 次 commit，实际序列: {seq}"
    for i, item in enumerate(seq):
        if item == "commit":
            assert i + 1 < len(seq) and seq[i + 1] == "delete", \
                f"commit 之后必须紧跟 delete（先删后写会留旧值回填窗口），实际序列: {seq}"
    deletes = [x for x in order if not isinstance(x, str)]
    assert len(deletes) == paths and all(x[1] == key for x in deletes), \
        f"三处写路径都必须失效同一个键，实际: {deletes}"


def test_order_assertion_detects_reversed_write():
    """变异自检：把「先 DEL 后 commit」的序列喂给上面那条断言，它必须报错。

    没有这条，「顺序契约被钉住」可能只是错觉 —— 一个写得过宽的断言（比如只数个数）
    在错误实现下也全绿。这里用错误序列反证断言真的有判别力。
    """
    key = conv_list_key(1)
    wrong_order = [
        ("delete", key), "commit",          # ① 提问路径写反了
        "commit", ("delete", key),          # ② 正确
        "commit", ("delete", key),          # ③ 正确
    ]

    with pytest.raises(AssertionError):
        _assert_commit_immediately_followed_by_delete(wrong_order, key)


def test_write_paths_invalidate_after_commit(maker, fake_graph):
    """★ 顺序契约：三处写路径都必须「先 MySQL commit、后 DEL」。

    判据把 SQLAlchemy 的 `after_commit` 事件与假 Redis 的调用记录打进**同一个 list**，
    再断言相邻关系。

    ⚠ 事件挂在 `db.sync_session` 上，不是计划里写的 `db.bind.sync_engine`：
    `after_commit` 是 `SessionEvents` 的事件，挂在 Engine 上会直接
    `AttributeError: after_commit`（实测），根本注册不上、更不会触发。
    AsyncSession 支持透传 SessionEvents，所以目标换成它底下的 sync Session。
    """
    from sqlalchemy import event

    redis = FakeRedis()
    order = []

    async def go():
        async with maker() as db:
            event.listen(db.sync_session, "after_commit",
                         lambda *a, **k: order.append("commit"))
            redis.calls = order            # 让两个来源写进同一个序列

            # ① 提问路径（prepare_stream 自己 commit）
            await chat_service.prepare_stream(
                ChatRequest(thread_id="t9", question="新问题"), 1, db, redis=redis)
            # ② 改名路径（rename_conversation **内部**已 commit）
            await chat_service.rename_chat_thread("t9", 1, "新标题", db, redis=redis)
            # ③ 删除路径（delete_chat_thread 自己 commit）
            await chat_service.delete_chat_thread("t9", 1, db, redis=redis)

    asyncio.run(go())

    _assert_commit_immediately_followed_by_delete(order, conv_list_key(1))


def test_invalidate_is_noop_without_redis(maker, fake_graph):
    """`redis=None`（未配置 / 开关关闭）时，三处写路径照常跑完、不抛异常。

    ⚠ 只断言「不抛异常」太弱：把 `invalidate` 写成 `return` 之前意外 `raise`、
    或干脆把整段业务逻辑跳过的实现，也可能不抛。所以顺带断言业务真的生效
    （upsert 落库、rename 生效、delete 真的删掉行）。
    """
    async def go():
        async with maker() as db:
            await chat_service.prepare_stream(
                ChatRequest(thread_id="t8", question="问题"), 1, db, redis=None)
            assert (await db.get(Conversation, "t8")).title == "问题", "upsert 必须照常落库"

            renamed = await chat_service.rename_chat_thread("t8", 1, "标题", db, redis=None)
            assert renamed.title == "标题", "rename 必须照常生效"

            deleted = await chat_service.delete_chat_thread("t8", 1, db, redis=None)
            assert deleted.thread_id == "t8"
            assert await db.get(Conversation, "t8") is None, "delete 必须照常删掉索引行"

    asyncio.run(go())


# ============================ 7. 三个写端点的接线 ============================
def test_write_endpoints_forward_redis(maker, fake_graph, monkeypatch):
    """★ 三个写端点必须真的把 `RedisOpt` 传进服务层。

    服务层对了、端点忘了透传，是这类改动最常见的漏法：接口照常 200、单测照常绿，
    线上却是「写路径从不失效」——列表陈旧到 TTL 才收敛，且没人会怀疑到缓存头上。
    tests/test_chat_threads.py 的既有端点用例走的都是 `redis=None`，证明不了接线。

    `POST /stream` 用 spy 顶掉 `prepare_stream`：这里要验的是**路由的转发**，
    不是流图本身（流图在 test_chat_stream.py）。PATCH/DELETE 则真跑服务层，
    验「缓存键真的被删掉」这一最终效果。
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

    # POST /stream：记录路由下发进来的 redis，不真跑图
    seen = {}

    async def _spy_prepare(chat_request, user_id, db, redis=None):
        seen["redis"] = redis

        async def _empty_stream():
            yield "event: done\ndata: {}\n\n"
        return _empty_stream()

    monkeypatch.setattr(chat_service, "prepare_stream", _spy_prepare)

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

    r_stream = client.post("/api/chat/stream",
                           json={"thread_id": "t1", "question": "问一句"})
    assert r_stream.status_code == 200
    assert seen.get("redis") is redis, "POST /stream 没把 redis 交给 prepare_stream"

    r_patch = client.patch("/api/chat/threads/t1", json={"title": "新标题"})
    assert r_patch.status_code == 200, r_patch.text
    assert ("delete", conv_list_key(1)) in redis.calls, "PATCH 改名后没失效缓存"

    r_delete = client.delete("/api/chat/threads/t1")
    assert r_delete.status_code == 200, r_delete.text
    deletes = [c for c in redis.calls if c == ("delete", conv_list_key(1))]
    assert len(deletes) == 2, f"PATCH 与 DELETE 各应删一次键，实际 {len(deletes)} 次"


# ============================ 8. prepare_stream 接线（P4 Task 7）============================
def test_prepare_stream_hands_redis_and_started_at_to_stream(maker, monkeypatch):
    """★ prepare_stream 必须把 redis 与 started_at 交给 stream_chat（P4 Task 7 接线）。

    不接线的后果是**静默**的：生产端能力全部就位，但真实 API 路径永远传 redis=None ——
    qa_stats 流一条真实数据都不会有；而 Task 6 的用例直连 stream_chat，证明不了这处转发
    （接口照常 200、单测照常绿，与本文件 7 区挡的是同一类漏法）。
    """
    redis = FakeRedis()
    seen = {}

    def _spy_stream_chat(chat_request, user_id=None, redis=None, started_at=None):
        seen["redis"], seen["started_at"] = redis, started_at

        async def _empty():
            yield "event: done\ndata: {}\n\n"
        return _empty()

    monkeypatch.setattr(chat_service, "stream_chat", _spy_stream_chat)

    async def go():
        async with maker() as db:
            gen = await chat_service.prepare_stream(
                ChatRequest(question="接线测试", thread_id="t-wire"), 1, db,
                redis=redis)
            return [frame async for frame in gen]

    frames = asyncio.run(go())

    assert seen["redis"] is redis, "redis 必须原样透传（生产端据此发事件）"
    assert isinstance(seen["started_at"], float), \
        "started_at 必须是 perf_counter 读数（None 会让 latency_ms 恒为 0）"
    assert frames == ["event: done\ndata: {}\n\n"], "接线不得改变流的消费方式"
