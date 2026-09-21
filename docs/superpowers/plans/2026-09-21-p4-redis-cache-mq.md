# 会话列表 Redis 读缓存 + MQ 派生数据落库 实现计划（P4）

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 给 `GET /api/chat/threads` 加上 Redis 读缓存（cache-aside + 写时删键 + 降级），并把每轮问答的统计经 Redis Streams 异步写入 MySQL 的 `qa_events` 表。

**Architecture:** MySQL 始终是真相源（同步写、授权只走 MySQL）；Redis 只做「可丢的读缓存」与「可丢的事件流」。缓存层的任何故障都降级回源，不升级为业务故障。契约零变化。

**Tech Stack:** FastAPI / SQLAlchemy(async) / Redis 8.1（`redis.asyncio`，含 Streams）/ pytest（离线 + `-m redis` 在线分层）

**设计文档：** `docs/superpowers/specs/2026-09-21-redis-cache-and-mq-design.md`（本计划的每一项约束都源自那里，冲突时以设计文档为准）

---

## 开工前必须知道的 5 件事

1. **本期不追求性能收益**（实测列表 1ms、授权 0.01ms）。验收看「命中率与 MySQL 查询计数」，不看延迟。
2. **`assert_owner` 一个字符都不改**。授权永不走缓存。
3. **写路径只删键，绝不写缓存值**。且删键必须在 **MySQL commit 之后**。
4. **项目没有 pip**（`python -m pip` 报 `No module named pip`），装包用 **`uv`**：
   `uv pip install <pkg> --python .venv/Scripts/python.exe`
   跑测试统一用：`d:\PycharmProjects\my_langchain_demo\.venv\Scripts\python.exe -m pytest …`
5. **默认套件必须保持全绿**（离线、零外部依赖）。需要真 Redis 的用例一律打 `@pytest.mark.redis`，`pytest.ini` 已配 `addopts = -m "not redis"` 默认排除。

---

## Task 0：前置阻塞项 —— 修 `.env` 的 Redis 认证

**为什么必须先做**：`.env` 第 30 行是 `redis://root:<密码>@192.168.88.130:6379`，而服务端不存在 `root` 用户。实测：

```
TCP 探测 192.168.88.130:6379 ... TCP 可建立 ✓（不是网络问题）
ping 失败: AuthenticationError: invalid username-password pair or user is disabled.
去掉用户名 / 改 default → PING 通 ✓
```

不修的话，后面每个任务都跑在降级路径上 —— 缓存「看起来接好了」但永远命中不了，你会白排查。

**Step 1：改 `.env`（文件在仓库外：`d:\PycharmProjects\my_langchain_demo\.env` 第 30 行）**

```diff
- REDIS_URL=redis://root:<密码>@192.168.88.130:6379
+ REDIS_URL=redis://:<密码>@192.168.88.130:6379
```

（也可写 `redis://default:<密码>@…`；两种都实测通过。只删 `root:` 这一小段，密码不动。）

**Step 2：验证**

Run: `cd advanced_tutorial/rag_qa_project && <上面那个 python.exe> -m pytest -m redis -q --no-header`
Expected: `2 passed`（`test_redis.py` 的连接层 + 另一条；之前是 `1 failed` 的 AuthenticationError）

**Step 3：确认缓存开关的默认值可用**

Run: `<python.exe> -X utf8 -c "from config import settings; print(bool(settings.redis_url))"`
Expected: `True`

---

## Task 1：配置项

**Files:**
- Modify: `rag_qa_project/config.py`（`Settings` 类内，「---- Redis ----」段之后）
- Test: `rag_qa_project/tests/test_config_redis_cache.py`（新建）

**Step 1：写失败测试**

```python
# rag_qa_project/tests/test_config_redis_cache.py
# -*- coding: utf-8 -*-
"""P4 缓存配置：默认值 + 可被 RAGQA_ 环境变量覆盖。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings

_ENV_KEYS = ("RAGQA_REDIS_CACHE_ENABLED", "RAGQA_CONV_CACHE_TTL",
             "RAGQA_QA_STREAM_KEY", "RAGQA_REDIS_URL", "REDIS_URL")


def _clean_env(monkeypatch):
    """清掉影响本模块的环境变量。

    ⚠ `Settings(_env_file=None)` **不能**隔离环境：config.py import 期就 load_dotenv()
    写进了 os.environ，而 pydantic-settings 仍会读 os.environ。不清的话，操作者一设
    `RAGQA_CONV_CACHE_TTL=300`（本功能的正常用法）「默认值」用例就变红 —— 与
    tests/test_mysql_db.py 记下的约定冲突。
    """
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_defaults_favour_safety(monkeypatch):
    """默认必须「开缓存 + 60s + 有流键」。只钉默认值，不声称钉住 TTL 为正（那需要字段校验器，
    而本 task 刻意不给可选依赖加启动期校验）。"""
    _clean_env(monkeypatch)

    s = Settings(_env_file=None)

    assert s.redis_cache_enabled is True
    assert s.conv_cache_ttl == 60
    assert s.qa_stream_key == "ragqa:stream:qa_stats"


def test_missing_redis_url_does_not_block_construction(monkeypatch):
    """★ Redis 是可选依赖：没配连接串也必须能构造配置（「不可用就降级」承诺的地基）。"""
    _clean_env(monkeypatch)

    s = Settings(_env_file=None)

    assert s.redis_url == ""


def test_env_can_override(monkeypatch):
    """回滚开关、TTL、流键都要能通过环境变量改（RAGQA_ 前缀，与既有配置一致）。"""
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAGQA_REDIS_CACHE_ENABLED", "false")
    monkeypatch.setenv("RAGQA_CONV_CACHE_TTL", "5")
    monkeypatch.setenv("RAGQA_QA_STREAM_KEY", "ragqa:test:stream")

    s = Settings(_env_file=None)

    assert s.redis_cache_enabled is False
    assert s.conv_cache_ttl == 5
    assert s.qa_stream_key == "ragqa:test:stream"
```

**Step 2：跑测试确认失败**

Run: `<python.exe> -m pytest tests/test_config_redis_cache.py -q --no-header`
Expected: FAIL —— `AttributeError: 'Settings' object has no attribute 'redis_cache_enabled'`

**Step 3：实现（加在 `config.py` 的「---- Redis ----」段之后）**

```python
    # ---- Redis 读缓存（P4）----
    # ⚠ 三个都必须有默认值：Redis 是**可选依赖**，缺它不该拦启动（与 mysql_database_url
    #   的「空串即快速失败」不同 —— 那条是必需依赖，这条不是）。
    redis_cache_enabled: bool = True   # 一键回退到「无缓存」行为
    conv_cache_ttl: int = 60           # 列表缓存 TTL 秒；只作兜底（主策略是写时删键）
    qa_stream_key: str = "ragqa:stream:qa_stats"   # 问答统计事件流
```

**Step 4：跑测试确认通过**

Run: `<python.exe> -m pytest tests/test_config_redis_cache.py -q --no-header`
Expected: `2 passed`

**Step 5：提交**

```bash
git add rag_qa_project/config.py rag_qa_project/tests/test_config_redis_cache.py
git commit -m "feat(p4): Redis 读缓存配置项（开关 / TTL / 事件流键）"
```

---

## Task 2：缓存工具 `redis_cache_tools.py`

**Files:**
- Create: `rag_qa_project/backend/tools/redis_cache_tools.py`
- Test: `rag_qa_project/tests/test_redis_cache_tools.py`（新建）

**设计要点（写代码前读一遍）**
- `redis=None` ⇒ 直接回源。这样「未配置 / 开关关闭」在调用方看来是同一件事，不需要到处传 flag。
- 三种异常要分开处理：**连接类**（`RedisError`）→ 降级；**解码类**（`JSONDecodeError`/`TypeError`）→ 当 miss 并删坏键；**其它**（代码 bug）→ 也要降级（缓存不能把业务带崩），但日志级别更高。

**Step 1：写失败测试**

```python
# rag_qa_project/tests/test_redis_cache_tools.py
# -*- coding: utf-8 -*-
"""缓存工具：命中 / 回源 / 降级 / 坏值清理。全离线（协议级假客户端，不需要 Redis）。"""
import sys
from pathlib import Path

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.tools.redis_cache_tools import cached_json, conv_list_key


class FakeRedis:
    """只实现本模块用到的方法，并按调用顺序记录。"""

    def __init__(self, store=None, get_exc=None, set_exc=None):
        self.store = dict(store or {})
        self.calls = []                    # [("get", key), ("set", key, ex), ("delete", key)...]
        self._get_exc = get_exc
        self._set_exc = set_exc

    async def get(self, key):
        self.calls.append(("get", key))
        if self._get_exc:
            raise self._get_exc
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.calls.append(("set", key, ex))
        if self._set_exc:
            raise self._set_exc
        self.store[key] = value

    async def delete(self, key):
        self.calls.append(("delete", key))
        return self.store.pop(key, None)


async def _loader(value, counter):
    counter.append(1)
    return value


def _run(coro_factory):
    import asyncio
    return asyncio.run(coro_factory())


def test_key_namespaced_by_user_and_versioned():
    """键必须带用户 id（隔离）与版本位（改结构时不必写迁移脚本）。"""
    assert conv_list_key(7) == "ragqa:conv:list:v1:7"
    assert conv_list_key(7) != conv_list_key(8)


def test_miss_calls_loader_and_writes_with_ttl():
    import asyncio
    redis, counter = FakeRedis(), []

    async def go():
        return await cached_json(redis, "k", 60, lambda: _loader({"a": 1}, counter))
    got = asyncio.run(go())

    assert got == {"a": 1}
    assert counter == [1], "miss 时必须回源"
    assert ("set", "k", 60) in redis.calls, "写回必须带 ex=ttl（否则键永不过期）"


def test_hit_does_not_call_loader():
    import asyncio
    redis = FakeRedis({"k": '{"a": 1}'})
    counter = []

    async def go():
        return await cached_json(redis, "k", 60, lambda: _loader({"a": 2}, counter))
    got = asyncio.run(go())

    assert got == {"a": 1}
    assert counter == [], "命中时绝不能回源（这是本期唯一的收益来源）"


def test_redis_none_bypasses_cache():
    import asyncio
    counter = []

    async def go():
        return await cached_json(None, "k", 60, lambda: _loader({"a": 1}, counter))
    got = asyncio.run(go())

    assert got == {"a": 1}
    assert counter == [1], "redis=None 表示「未配置或已关闭」，必须回源"


def test_connection_error_degrades_to_source():
    """Redis 连不上 → 返回正确数据 + 不再写回（写了也会失败）。"""
    import asyncio
    redis = FakeRedis(get_exc=RedisConnectionError("boom"))
    counter = []

    async def go():
        return await cached_json(redis, "k", 60, lambda: _loader({"a": 1}, counter))
    got = asyncio.run(go())

    assert got == {"a": 1}
    assert counter == [1]
    assert not any(c[0] == "set" for c in redis.calls), "读都失败就别再试写"


def test_corrupt_json_is_treated_as_miss_and_deleted():
    """坏值必须删掉：否则每次请求都白读一次坏键，且永远修不好。"""
    import asyncio
    redis = FakeRedis({"k": "{not json"})
    counter = []

    async def go():
        return await cached_json(redis, "k", 60, lambda: _loader({"a": 1}, counter))
    got = asyncio.run(go())

    assert got == {"a": 1}
    assert counter == [1]
    assert ("delete", "k") in redis.calls


def test_set_failure_still_returns_value():
    """写缓存失败不影响本次返回（下一次继续 miss，仅损失命中率）。"""
    import asyncio
    redis = FakeRedis(set_exc=RedisConnectionError("boom"))
    counter = []

    async def go():
        return await cached_json(redis, "k", 60, lambda: _loader({"a": 1}, counter))
    assert await go() == {"a": 1}
```

**Step 2：跑测试确认失败**

Run: `<python.exe> -m pytest tests/test_redis_cache_tools.py -q --no-header`
Expected: FAIL —— `ModuleNotFoundError: No module named 'backend.tools.redis_cache_tools'`

**Step 3：实现**

```python
# rag_qa_project/backend/tools/redis_cache_tools.py
# -*- coding: utf-8 -*-
"""Redis 读缓存的通用工具（cache-aside）。纯工具层：不碰请求上下文、不碰 DB。

**定位**：缓存是**可丢的加速层**。本模块的每个函数都遵守「缓存故障不得升级为业务故障」：
任何 Redis 异常都被吞掉并记日志，调用方永远拿到可用数据。

**不要用它缓存真相源**（P4 设计文档 §2）：`conversations` 这类作为授权依据的数据
永远不会进这里 —— 一次不一致就是越权。这里放的只能是「丢了也能从 MySQL 重算」的东西。
"""
import json
import logging

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


def conv_list_key(user_id: int) -> str:
    """会话列表缓存的键。

    `:v1:` 是**结构版本位**：将来改缓存里的 JSON 结构时，把 v1 改成 v2 即可让旧键
    自然不可达、随 TTL 消失 —— 不必写迁移脚本，也不必记得发版前手动清 Redis。
    """
    return f"ragqa:conv:list:v1:{user_id}"


async def cached_json(redis, key: str, ttl: int, loader) -> object:
    """cache-aside 读：命中直接返回；miss / 坏值 / 任何异常都回源。

    Args:
        redis: `redis.asyncio.Redis`；**传 None 表示缓存不可用**（未配置或开关关闭），
               此时等价于直接调 loader —— 调用方因此不需要到处判断开关。
        ttl: 写回时的秒级过期时间，只作兜底（主策略是写路径主动删键）。
        loader: 无参协程函数，负责回源（本项目里就是今天那段 MySQL 查询）。

    Returns:
        loader 的返回值，或缓存里解出来的 JSON 对象。
    """
    if redis is not None:
        raw = None
        try:
            raw = await redis.get(key)
        except RedisError as exc:                     # 连接类：降级，且别再尝试写
            logger.warning("[cache] 读失败，降级回源 key=%s: %s", key, exc)
            return await loader()
        except Exception as exc:                      # 代码 bug 也不该拖垮业务
            logger.error("[cache] 读异常，降级回源 key=%s: %s", key, exc)
            return await loader()

        if raw is not None:
            try:
                return json.loads(raw)
            except (ValueError, TypeError) as exc:
                # 坏值必须清掉：否则每次请求都白读一次，且永远不会自愈
                logger.warning("[cache] 值损坏，删除并回源 key=%s: %s", key, exc)
                try:
                    await redis.delete(key)
                except Exception:
                    pass                              # 删不掉也无妨，TTL 会兜底
                return await loader()
            # 命中直接返回（不写回、不续期）
            # 说明：这里**不刷新 TTL**，避免「被反复读的键永不过期」把陈旧数据留成永久状态
            # （缓存雪崩一节里的经典反例：热点键续期 → 数据修改后长期不收敛）
        # miss：回源并写回
        value = await loader()
        await _try_set(redis, key, value, ttl)
        return value

    # 缓存不可用：等价于无缓存
    return await loader()


async def _try_set(redis, key: str, value, ttl: int) -> None:
    """写回缓存；失败只记日志（下一次继续 miss，仅损失命中率）。"""
    try:
        await redis.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
    except Exception as exc:
        logger.warning("[cache] 写回失败 key=%s: %s", key, exc)


async def invalidate(redis, key: str) -> None:
    """删键失效。**必须在 MySQL commit 之后调用**（顺序见 P4 设计文档 §4）。

    反过来（先删键再写库）会留下「库还是旧值、缓存已空」的窗口，
    并发读会把旧值重新填进缓存 —— 且这次要等 TTL 才会自愈。
    """
    if redis is None:
        return
    try:
        await redis.delete(key)
    except Exception as exc:
        logger.warning("[cache] 失效失败 key=%s: %s", key, exc)
```

**Step 4：跑测试确认通过**

Run: `<python.exe> -m pytest tests/test_redis_cache_tools.py -q --no-header`
Expected: `14 passed`

> 实现时在原稿 7 条之外补了 7 条（共 14 条）。前 2 条是实现时主动补的：
> `test_hit_does_not_refresh_ttl`（命中不续期，防热点键永不过期导致数据长期不收敛）与
> `test_invalidate_deletes_key_and_is_noop_without_redis`（给 `invalidate` 直接覆盖）。
>
> 后 5 条是**质量审查用变异测试**逼出来的 —— 它逐条改实现再跑既有用例，证明有两类契约
> **没有任何测试守着**（删掉宽兜底分支、甚至让缓存层吞掉 loader 的业务异常，套件依然全绿）：
> `test_non_redis_exception_also_degrades`（非 RedisError 也要降级且记 error）、
> `test_loader_business_error_propagates`（★ 反向契约：业务异常必须原样上抛，否则 MySQL 故障
> 会被伪装成「降级成功」）、`test_unserializable_value_is_dropped_without_raising`
> （序列化失败要记 error 且不调 set）、`test_corrupt_value_delete_failure_still_misses`、
> `test_invalidate_failure_does_not_raise`。同时给 `FakeRedis` 加了 `delete_exc`、
> 给 miss 用例加了「写回去的必须是 JSON 文本」的断言。

**Step 5：提交**

```bash
git add rag_qa_project/backend/tools/redis_cache_tools.py rag_qa_project/tests/test_redis_cache_tools.py
git commit -m "feat(p4): Redis cache-aside 工具（命中/回源/降级/坏值清理）"
```

---

## Task 3：可选 Redis 依赖 + 读路径接缓存

**Files:**
- Modify: `rag_qa_project/backend/app/api/deps.py`（加 `get_optional_redis`，**不动** `get_redis_pool`）
- Modify: `rag_qa_project/backend/app/services/chat_service.py:151-162`（`list_chat_threads`）
- Modify: `rag_qa_project/backend/app/api/chat_router.py:42-49`（注入依赖）
- Test: `rag_qa_project/tests/test_deps.py`（追加）+ `rag_qa_project/tests/test_chat_threads_cache.py`（新建）

**Step 1：写失败测试（依赖部分）**

追加到 `tests/test_deps.py`：

```python
def test_get_optional_redis_returns_none_when_pool_missing():
    """★ 与 get_redis_pool 的关键区别：池不存在时返回 None（降级），**不抛异常**。

    缓存是可选依赖：Redis 没配或挂了，列表接口必须照常返回数据。
    get_redis_pool 保持「抛 RuntimeError」是因为它的定位是硬依赖。
    """
    from backend.app.api.deps import get_optional_redis

    app = FastAPI()

    assert asyncio.run(get_optional_redis(_request_for(app))) is None


def test_get_optional_redis_returns_none_when_switch_off(monkeypatch):
    """开关关闭 ⇒ 即使池在也返回 None（让回滚开关对调用方完全透明）。"""
    from backend.app.api.deps import get_optional_redis

    monkeypatch.setattr(settings, "redis_cache_enabled", False)
    app = FastAPI()
    app.state.redis_pool = object()

    assert asyncio.run(get_optional_redis(_request_for(app))) is None
```

**Step 2：写失败测试（读路径部分）**

```python
# rag_qa_project/tests/test_chat_threads_cache.py
# -*- coding: utf-8 -*-
"""会话列表读缓存：命中不打 MySQL、写时删键、降级。全离线（SQLite 内存库 + 假 Redis）。"""
import asyncio
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
    """记录调用顺序的协议级替身（含 Streams 方法，Task 7 复用）。"""

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


def test_first_read_misses_then_second_read_hits(maker):
    _seed(maker)
    redis = FakeRedis()

    async def go():
        async with maker() as db:
            await chat_service.list_chat_threads(1, db, redis=redis)
            n_calls_after_first = len([c for c in redis.calls if c[0] == "get"])
            await chat_service.list_chat_threads(1, db, redis=redis)
            return n_calls_after_first

    asyncio.run(go())

    gets = [c for c in redis.calls if c[0] == "get"]
    assert len(gets) == 2, "两次读各自查一次缓存"
    assert any(c[0] == "set" for c in redis.calls), "第一次必须写回缓存"


def test_hit_path_does_not_touch_mysql(maker):
    """★ 核心收益断言：命中时 MySQL 完全不被查询。

    判据用 SQLAlchemy 的 before_cursor_execute 事件计数，比 mock 会话更接近真实。
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
            return None

    asyncio.run(go())

    assert sql_count == [], f"命中缓存时不该有任何 SQL，实际执行了 {len(sql_count)} 条"


def test_payload_carries_user_id_and_total(maker):
    """缓存载荷必须自带 user_id 与 total：前者防串键泄露，后者是「仅显示最近 50 条」的依据。"""
    import json
    _seed(maker)
    redis = FakeRedis()

    async def go():
        async with maker() as db:
            r = await chat_service.list_chat_threads(1, db, redis=redis)
            return r

    r = asyncio.run(go())
    cached = json.loads(redis.store[conv_list_key(1)])

    assert cached["user_id"] == 1
    assert cached["total"] == r.total == 1
    assert cached["threads"][0]["thread_id"] == "t1"
    assert isinstance(cached["threads"][0]["created_at"], str), "时间要序列化成 ISO 字符串"


def test_payload_user_mismatch_is_rejected(maker):
    """★ 安全兜底：若缓存里的 user_id 与请求不符（键串了/被人塞了值），当 miss，
    绝不能把别人的列表返回出去。"""
    import json
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


def test_redis_down_degrades_to_mysql(maker):
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
```

**Step 3：跑测试确认失败**

Run: `<python.exe> -m pytest tests/test_chat_threads_cache.py tests/test_deps.py -q --no-header`
Expected:
- `test_deps.py`：`ImportError: cannot import name 'get_optional_redis'`
- `test_chat_threads_cache.py`：`TypeError: list_chat_threads() got an unexpected keyword argument 'redis'`

**Step 4：实现依赖（`deps.py` 末尾追加，**不要改** `get_redis_pool`）**

```python
async def get_optional_redis(request: Request):
    """可选的 Redis（缓存用）：不可用时返回 None，由调用方降级回源。

    与 `get_redis_pool` 的分工必须分清：
      · `get_redis_pool`  —— **硬依赖**：拿不到就报可操作的错（将来真有离不开 Redis 的功能用它）
      · `get_optional_redis` —— **软依赖**：缓存而已，没有照常工作（P4 设计文档 §7 的核心原则）

    开关 `redis_cache_enabled=False` 时同样返回 None，这样「回滚开关」对调用方完全透明。
    """
    if not settings.redis_cache_enabled:
        return None
    return getattr(request.app.state, "redis_pool", None)
```

并在文件顶部补 `from config import settings`。

**Step 5：实现读路径（`chat_service.py` 的 `list_chat_threads`）**

```python
async def list_chat_threads(user_id: int, db: AsyncSession, redis=None) -> ThreadListResponse:
    """当前用户的会话列表（对齐契约 §7.1），带 Redis 读缓存。

    缓存载荷结构 {"user_id": N, "total": M, "threads": [...]}：
      · `user_id` 用于**自校验** —— 键串了/被塞了值时当 miss，绝不返回别人的数据；
      · `total` 必须一起缓存，它是前端「仅显示最近 50 条」的唯一依据（P3 §7.1）。

    `redis=None` ⇒ 无缓存路径，行为与 P3 完全一致（未配置 Redis 或开关关闭时就是这样）。
    """
    async def _load() -> dict:
        rows, total = await conversation_service.list_conversations(db, user_id)
        return {"user_id": user_id, "total": total,
                "threads": [{"thread_id": r.thread_id, "title": r.title,
                             "created_at": r.created_at.isoformat(),
                             "updated_at": r.updated_at.isoformat()} for r in rows]}

    key = conv_list_key(user_id)
    payload = await cached_json(redis, key, settings.conv_cache_ttl, _load)

    # 自校验：归属不符 / 结构不符 / 单个元素字段不全 → 一律当 miss 并清掉坏键
    if not _is_valid_payload(payload, user_id):
        logger.error("[cache] 载荷归属或结构异常，丢弃 key=%s", key)
        await invalidate(redis, key)
        payload = await _load()

    return ThreadListResponse(
        threads=[ConversationSummary(**t) for t in payload["threads"]],
        total=payload["total"])


_REQUIRED_THREAD_FIELDS = ("thread_id", "title", "created_at", "updated_at")


def _is_valid_payload(payload, user_id: int) -> bool:
    """缓存载荷自校验：归属正确 + `total` 是整数 + `threads` 每项四个必填字段齐全。

    ⚠ **元素级校验不能省**（质量审查发现）：`{"threads":[{}]}` 能通过「是 list」这一层，
    但会让 `ConversationSummary(**t)` 抛 `TypeError` → 500 —— 那恰好违反本期的核心承诺
    「缓存故障不得升级为业务故障」。写成纯函数是为了能单测它。

    `total` 单独校验的原因同 P3：它是前端「仅显示最近 50 条」的唯一依据，缺失或非整数时
    前端会把「被截断」当成「一共就这么多」。
    """
    if not isinstance(payload, dict) or payload.get("user_id") != user_id:
        return False
    if not isinstance(payload.get("total"), int):
        return False
    threads = payload.get("threads")
    if not isinstance(threads, list):
        return False
    return all(isinstance(t, dict) and all(f in t for f in _REQUIRED_THREAD_FIELDS)
               for t in threads)
```

`chat_service.py` 顶部补：

```python
import logging

from config import settings
from backend.tools.redis_cache_tools import cached_json, conv_list_key, invalidate

logger = logging.getLogger(__name__)
```

**Step 6：实现路由注入（`chat_router.py`）**

```python
from redis import asyncio as aioredis

from backend.app.api.deps import get_current_user, get_optional_redis

RedisOpt = Annotated[aioredis.Redis | None, Depends(get_optional_redis)]


@router.get("/threads", response_model=ThreadListResponse)
async def list_chat_threads(user: CurrentUser, db: DbSession, redis: RedisOpt):
    """当前用户的会话列表，按 `updated_at` 倒序、最多 50 条（P3 §7.1）。

    结果走 Redis 读缓存（P4）；Redis 不可用时自动降级直查 MySQL，响应结构不变。
    """
    return await chat_service.list_chat_threads(user.id, db, redis)
```

**Step 7：跑测试确认通过**

Run: `<python.exe> -m pytest tests/test_chat_threads_cache.py tests/test_deps.py -q --no-header`
Expected: `6 passed`（缓存用例）+ `9 passed`（deps）

**Step 8：跑全量确认没破坏既有契约**

Run: `<python.exe> -m pytest tests -q --no-header`
Expected: 全绿（`test_chat_threads.py` 里既有的列表用例必须仍通过 —— 它们没传 `redis`，走 None 分支）

**Step 9：提交**

```bash
git add rag_qa_project/backend/app/api/deps.py rag_qa_project/backend/app/api/chat_router.py \
        rag_qa_project/backend/app/services/chat_service.py \
        rag_qa_project/tests/test_deps.py rag_qa_project/tests/test_chat_threads_cache.py
git commit -m "feat(p4): 会话列表读缓存 + 可选 Redis 依赖（含归属自校验与降级）"
```

---

## Task 4：三处写路径失效（**顺序是契约**）

**Files:**
- Modify: `rag_qa_project/backend/app/services/chat_service.py`（`prepare_stream` / `rename_chat_thread` / `delete_chat_thread`）
- Modify: `rag_qa_project/backend/app/api/chat_router.py`（三个端点加 `redis` 注入）
- Test: `rag_qa_project/tests/test_chat_threads_cache.py`（追加）

**Step 1：写失败测试**

```python
def test_write_paths_invalidate_after_commit(maker):
    """★ 顺序契约：必须「先 MySQL commit、后 DEL」—— 顺序反了会把旧值重新填回缓存。

    判据用 SQLAlchemy 的 after_commit 事件与假 Redis 的调用记录**打进同一个列表**，
    直接断言序列，而不是各自断言「都发生过」。
    """
    from sqlalchemy import event

    redis = FakeRedis()
    order = []

    async def go():
        async with maker() as db:
            event.listen(db.bind.sync_engine, "after_commit", lambda *a, **k: order.append("commit"))
            redis.calls = order            # 让两边写进同一个序列

            # ① 提问路径（含 upsert + commit）
            await chat_service.prepare_stream(
                ChatRequest(thread_id="t9", question="新问题"), 1, db, redis=redis)

            # ② 改名路径（rename 内部自己 commit）
            await chat_service.rename_chat_thread("t9", 1, "新标题", db, redis=redis)

            # ③ 删除路径（delete 内部 commit）
            await chat_service.delete_chat_thread("t9", 1, db, redis=redis)

    asyncio.run(go())

    seq = [x if isinstance(x, str) else x[0] for x in order]
    # 每个路径都必须是 commit 紧邻其后的 delete
    assert seq.count("commit") == 3
    for i, item in enumerate(seq):
        if item == "commit":
            assert seq[i + 1] == "delete", f"commit 之后必须紧跟 delete，实际: {seq}"
    assert all(x[1] == conv_list_key(1) for x in order if not isinstance(x, str))


def test_invalidate_is_noop_without_redis(maker):
    """redis=None（未配置/开关关闭）时，写路径照常工作、不报错。"""
    async def go():
        async with maker() as db:
            await chat_service.prepare_stream(
                ChatRequest(thread_id="t8", question="问题"), 1, db, redis=None)
            await chat_service.rename_chat_thread("t8", 1, "标题", db, redis=None)
            await chat_service.delete_chat_thread("t8", 1, db, redis=None)

    asyncio.run(go())      # 不抛异常即通过
```

（在文件顶部 import 里加 `from backend.app.agent.schemas import ChatRequest`）

**Step 2：跑测试确认失败**

Run: `<python.exe> -m pytest tests/test_chat_threads_cache.py -q --no-header -k invalidate`
Expected: FAIL —— `TypeError: prepare_stream() got an unexpected keyword argument 'redis'`

**Step 3：实现（三个函数各加一个 `redis=None` 形参 + 在 commit 之后删键）**

`prepare_stream`：

```python
async def prepare_stream(chat_request: ChatRequest, user_id: int,
                         db: AsyncSession, redis=None):
    """鉴权 + upsert 完成后，把流生成器交给路由。……（原 docstring 保留）"""
    await conversation_service.assert_owner(db, chat_request.thread_id, user_id)
    await conversation_service.upsert_conversation(
        db, thread_id=chat_request.thread_id, user_id=user_id,
        question=chat_request.question)
    await db.commit()
    # ⚠ 删键必须在 commit **之后**：先删后写会留下「库旧、缓存空」的窗口被并发读回填旧值
    await invalidate(redis, conv_list_key(user_id))
    return stream_chat(chat_request, user_id, redis=redis,
                       started_at=time.perf_counter())   # Task 7 用
```

`rename_chat_thread`：

```python
async def rename_chat_thread(thread_id: str, user_id: int, title: str,
                             db: AsyncSession, redis=None) -> RenameResponse:
    row = await conversation_service.rename_conversation(db, thread_id, user_id, title)
    # rename_conversation 内部已 commit（conversation_service.py:93），此处删键是安全的
    await invalidate(redis, conv_list_key(user_id))
    return RenameResponse(thread_id=row.thread_id, title=row.title)
```

`delete_chat_thread`：

```python
    await conversation_service.delete_conversation_row(db, thread_id)
    await db.commit()
    await invalidate(redis, conv_list_key(user_id))     # ← 在 return 之前
    return ChatDeleteResponse(thread_id=thread_id, deleted=existed)
```

**Step 4：实现路由注入（三个端点加 `redis: RedisOpt` 并透传）**

```python
@router.post("/stream")
async def stream_chat(chat_request: ChatRequest, user: CurrentUser, db: DbSession,
                      redis: RedisOpt):
    agen = await chat_service.prepare_stream(chat_request, user.id, db, redis)
    ...


@router.patch("/threads/{thread_id}", response_model=RenameResponse)
async def rename_chat_thread(thread_id: str, body: RenameRequest,
                             user: CurrentUser, db: DbSession, redis: RedisOpt):
    return await chat_service.rename_chat_thread(thread_id, user.id, body.title, db, redis)


@router.delete("/threads/{thread_id}", response_model=ChatDeleteResponse)
async def delete_chat(thread_id: str, user: CurrentUser, db: DbSession, redis: RedisOpt):
    return await chat_service.delete_chat_thread(thread_id, user.id, db, redis)
```

**Step 5：跑测试确认通过**

Run: `<python.exe> -m pytest tests/test_chat_threads_cache.py -q --no-header`
Expected: `8 passed`

**Step 6：跑全量（既有的 `test_chat_threads.py` / `test_chat_stream.py` 必须仍通过）**

Run: `<python.exe> -m pytest tests -q --no-header`
Expected: 全绿

**Step 7：提交**

```bash
git add rag_qa_project/backend/app/services/chat_service.py rag_qa_project/backend/app/api/chat_router.py \
        rag_qa_project/tests/test_chat_threads_cache.py
git commit -m "feat(p4): 三处写路径在 commit 之后失效缓存（顺序有测试钉住）"
```

---

## Task 5：`qa_events` 表

**Files:**
- Modify: `rag_qa_project/backend/app/models.py`（追加模型）
- Test: `rag_qa_project/tests/test_models.py`（追加一条）

**Step 1：写失败测试**

```python
def test_qa_events_table_has_idempotency_key():
    """★ `event_id` 必须是 UNIQUE —— Redis Streams 是**至少一次**投递，
    重复消费靠这个唯一键挡住，否则统计会重复计数。"""
    from backend.app.models import QaEvent

    cols = {c.name for c in QaEvent.__table__.columns}
    assert {"id", "event_id", "user_id", "thread_id",
            "rewrites", "grounded", "latency_ms", "created_at"} <= cols

    uniques = [c for c in QaEvent.__table__.constraints
               if c.__class__.__name__ == "UniqueConstraint"]
    assert any([col.name for col in u.columns] == ["event_id"] for u in uniques), \
        "event_id 必须有 UNIQUE 约束（幂等键）"
```

**Step 2：跑测试确认失败**

Run: `<python.exe> -m pytest tests/test_models.py -q --no-header -k qa_events`
Expected: FAIL —— `ImportError: cannot import name 'QaEvent'`

**Step 3：实现（追加到 `models.py`，不动 `User` / `Conversation`）**

```python
class QaEvent(Base):
    """问答事件（派生数据，P4）。

    **刻意不建外键**：统计不该因为用户被删而级联消失（派生数据要能独立留存），
    也避免 CASCADE 在删用户时把事件一并抹掉。
    """
    __tablename__ = "qa_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_qa_events_event_id"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True).with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True)
    # 幂等键：Redis Streams 至少一次投递，重复消费靠它挡住
    event_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    thread_id: Mapped[str] = mapped_column(String(36))
    rewrites: Mapped[int] = mapped_column(Integer)
    grounded: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=_utcnow)
```

`models.py` 顶部 import 补 `Boolean`：

```python
from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, text
```

**Step 4：跑测试确认通过**

Run: `<python.exe> -m pytest tests/test_models.py -q --no-header`
Expected: 全绿（含新用例）

**Step 5：真库建表（`create_all` 自动建，无需迁移脚本）**

Run:
```bash
<python.exe> -X utf8 -c "
import asyncio
from backend.tools.mysql_db_tools import init_db
asyncio.run(init_db())
"
```
Expected: 输出里出现 `qa_events`（与 `users` / `conversations` 并列）

**Step 6：提交**

```bash
git add rag_qa_project/backend/app/models.py rag_qa_project/tests/test_models.py
git commit -m "feat(p4): qa_events 派生数据表（event_id 唯一键做幂等）"
```

---

## Task 6：MQ 生产端（含新增计时）

**Files:**
- Modify: `rag_qa_project/backend/app/services/chat_service.py`（`stream_chat` 签名 + `done` 分支）
- Test: `rag_qa_project/tests/test_qa_events_stream.py`（新建）

**Step 1：写失败测试**

**Step 0：先给既有的假图辅助函数加两个可选参数**

⚠ **不要新写一套假图装配** —— `tests/test_chat_stream.py:54-70` 已有 `_run_stream_with_vs`
（返回 `(frames, vs)`，内部已把 `chat_service.get_graph` 换成假件编译的图，并固定用
`thread_id="t-stream"`）。只需透传两个新参数：

```diff
 def _run_stream_with_vs(monkeypatch, relevance_mode: str,
                         question: str = "LangGraph 怎么做持久化？",
-                        user_id: int | None = None, docs=None):
+                        user_id: int | None = None, docs=None,
+                        redis=None, started_at: float | None = None):
@@
     async def go():
-        return [frame async for frame in chat_service.stream_chat(req, user_id=user_id)]
+        return [frame async for frame in chat_service.stream_chat(
+            req, user_id=user_id, redis=redis, started_at=started_at)]
```

既有调用方一处都不用改（两个新参数都有默认值）。

**Step 1：写失败测试**

```python
# rag_qa_project/tests/test_qa_events_stream.py
# -*- coding: utf-8 -*-
"""问答事件的产生：字段完整、失败不影响 SSE 流。全离线（复用 test_chat_stream 的假图）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_chat_stream import _run_stream_with_vs      # 同层 import，与 test_graph 的用法一致


class FakeRedis:
    """只实现生产端用到的 xadd；记录 (流名, 字段, maxlen)。"""

    def __init__(self, xadd_exc=None):
        self.calls = []
        self._exc = xadd_exc

    async def xadd(self, name, fields, maxlen=None, approximate=True):
        self.calls.append((name, dict(fields), maxlen))
        if self._exc:
            raise self._exc
        return "1-0"


def test_qa_event_fields_are_complete(monkeypatch):
    redis = FakeRedis()

    frames, _vs = _run_stream_with_vs(monkeypatch, relevance_mode="all",
                                      user_id=7, redis=redis, started_at=0.0)

    assert [e for e, _ in frames][-1] == "done", "事件在 done 之后才发，不能打乱帧序"

    assert len(redis.calls) == 1, "一轮问答恰好一条事件"
    stream_key, fields, maxlen = redis.calls[0]
    assert stream_key == "ragqa:stream:qa_stats"
    assert fields["user_id"] == "7"
    assert fields["thread_id"] == "t-stream"        # 由 _run_stream_with_vs 固定
    assert fields["grounded"] == "1"                # relevance_mode="all" ⇒ 有资料
    assert int(fields["latency_ms"]) > 0            # started_at=0.0 ⇒ 耗时必为正
    assert len(fields["event_id"]) == 36            # uuid4 字符串长度
    assert maxlen == 10000, "必须带 MAXLEN，否则流会无限增长"


def test_xadd_failure_does_not_break_stream(monkeypatch):
    redis = FakeRedis(xadd_exc=RuntimeError("stream down"))

    frames, _vs = _run_stream_with_vs(monkeypatch, relevance_mode="all",
                                      user_id=7, redis=redis, started_at=0.0)

    assert [e for e, _ in frames][-1] == "done", "XADD 失败绝不能影响 SSE 流（统计是可丢的）"
    assert not [d for e, d in frames if e == "error"]


def test_no_redis_means_no_event_and_no_error(monkeypatch):
    """redis=None（未配置 / 开关关闭）：不产生事件，也不报错。"""
    frames, _vs = _run_stream_with_vs(monkeypatch, relevance_mode="all", user_id=7)

    assert [e for e, _ in frames][-1] == "done"
```

**Step 2：跑测试确认失败**

Run: `<python.exe> -m pytest tests/test_qa_events_stream.py -q --no-header`
Expected: FAIL —— `TypeError: stream_chat() got an unexpected keyword argument 'redis'`

**Step 3：实现**

`stream_chat` 签名与 `done` 分支：

```python
async def stream_chat(chat_request: ChatRequest, user_id: int | None = None,
                      redis=None, started_at: float | None = None):
    ...
        yield sse("done", {"thread_id": chat_request.thread_id, "rewrites": rewrites,
                           "grounded": bool(final_docs)})
        # 事件在生产端**只在问答成功后**发；失败只记 warning（统计是可丢的派生数据）
        await _emit_qa_event(redis, user_id=user_id, thread_id=chat_request.thread_id,
                             rewrites=rewrites, grounded=bool(final_docs),
                             started_at=started_at)
```

新增工具函数：

```python
async def _emit_qa_event(redis, *, user_id, thread_id, rewrites, grounded,
                         started_at) -> None:
    """把本轮问答的结果写进 Redis Stream（派生数据，可丢）。

    ⚠ 这里**不能抛异常**：它在 SSE 流的收尾处，抛出去会把已经成功的回答变成 error 帧。
    """
    if redis is None:
        return
    latency_ms = int((time.perf_counter() - started_at) * 1000) if started_at else 0
    try:
        await redis.xadd(
            settings.qa_stream_key,
            {"event_id": str(uuid.uuid4()), "user_id": str(user_id),
             "thread_id": thread_id, "rewrites": str(rewrites),
             "grounded": "1" if grounded else "0", "latency_ms": str(latency_ms)},
            maxlen=10000, approximate=True)
    except Exception as exc:
        logger.warning("[qa-event] 写入事件流失败（不影响问答）：%s", exc)
```

`chat_service.py` 顶部补 `import time`、`import uuid`。

**Step 4：跑测试确认通过**

Run: `<python.exe> -m pytest tests/test_qa_events_stream.py -q --no-header`
Expected: `2 passed`

**Step 5：提交**

```bash
git add rag_qa_project/backend/app/services/chat_service.py rag_qa_project/tests/test_qa_events_stream.py
git commit -m "feat(p4): 问答事件生产端（新增 latency 计时 + XADD，失败不影响 SSE）"
```

---

## Task 7：MQ 消费端（消费组 + 幂等 + 死信）

**Files:**
- Create: `rag_qa_project/backend/app/services/qa_event_consumer.py`
- Modify: `rag_qa_project/backend/app/api/main.py`（lifespan 起停后台任务）
- Test: `rag_qa_project/tests/test_qa_event_consumer.py`（新建）

**Step 1：写失败测试**

```python
# rag_qa_project/tests/test_qa_event_consumer.py
# -*- coding: utf-8 -*-
"""消费端：字段映射、幂等（event_id 唯一键）、失败不 ACK、超次进死信。全离线。"""
import asyncio
import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.models import Base, QaEvent
from backend.app.services import qa_event_consumer as consumer


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


EVENT = {"event_id": "e-1", "user_id": "7", "thread_id": "t1",
         "rewrites": "1", "grounded": "1", "latency_ms": "1234"}


def test_handle_event_writes_row(maker):
    async def go():
        async with maker() as db:
            await consumer.handle_event(db, EVENT)
            await db.commit()
            return (await db.get(QaEvent, 1))

    row = asyncio.run(go())

    assert row.event_id == "e-1"
    assert row.user_id == 7 and row.thread_id == "t1"
    assert row.rewrites == 1 and row.grounded is True and row.latency_ms == 1234


def test_duplicate_event_is_idempotent(maker):
    """★ 至少一次投递：同一 event_id 再来一次不能重复插、也不能把消费端搞崩。"""
    async def go():
        async with maker() as db:
            await consumer.handle_event(db, EVENT)
            await db.commit()
            await consumer.handle_event(db, EVENT)     # 重投
            await db.commit()
            from sqlalchemy import func, select
            return await db.scalar(select(func.count()).select_from(QaEvent))

    assert asyncio.run(go()) == 1


def test_bad_payload_does_not_raise(maker):
    """字段缺失/类型不对：不能抛（否则会被当成「消费失败」无限重试）。"""
    async def go():
        async with maker() as db:
            await consumer.handle_event(db, {"event_id": "e-2"})   # 缺字段
            await db.commit()

    asyncio.run(go())      # 不抛即通过


def test_consumer_loop_acks_on_success_and_dead_letters_after_max_retries():
    """循环：成功 → XACK；连续失败超 MAX_RETRIES → 进死信流 + XACK 原事件（不无限重投）。"""
    class FakeRedis:
        def __init__(self):
            self.acked, self.dead = [], []
            self.batches = [                            # 第一次给 1 条，第二次没有
                [("1-0", {"event_id": "e-1", "user_id": "7", "thread_id": "t1",
                          "rewrites": "0", "grounded": "1", "latency_ms": "10"})],
                [],
            ]
            self.idx = 0

        async def xgroup_create(self, *a, **k):
            raise Exception("BUSYGROUP")               # 已存在：必须被忽略

        async def xreadgroup(self, *a, **k):
            batch = self.batches[min(self.idx, len(self.batches) - 1)]
            self.idx += 1
            return [("ragqa:stream:qa_stats", batch)] if batch else []

        async def xack(self, stream, group, msg_id):
            self.acked.append(msg_id)

        async def xadd(self, name, fields, maxlen=None, approximate=True):
            self.dead.append((name, dict(fields)))
            return "2-0"

    redis = FakeRedis()
    calls = {"n": 0}

    async def flaky_handler(db, fields):
        calls["n"] += 1
        raise RuntimeError("db down")

    async def go():
        task = asyncio.create_task(consumer.consume(
            redis, session_factory=None, handler=flaky_handler,
            max_retries=2, poll_interval=0.01))
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(go())

    assert calls["n"] >= 2, "失败必须重试"
    assert redis.dead, "超过 max_retries 必须进死信流"
    assert redis.dead[0][0].endswith(":dead")
```

**Step 2：跑测试确认失败**

Run: `<python.exe> -m pytest tests/test_qa_event_consumer.py -q --no-header`
Expected: FAIL —— `ModuleNotFoundError: No module named 'backend.app.services.qa_event_consumer'`

**Step 3：实现**

```python
# rag_qa_project/backend/app/services/qa_event_consumer.py
# -*- coding: utf-8 -*-
"""问答事件消费端：Redis Streams → MySQL `qa_events`（P4）。

三条硬约束（P4 设计文档 §6.4）：
1. **至少一次投递**：成功后 `XACK`；失败**不 ACK**（留在 PEL 里待重投）。
2. **幂等靠 `event_id` 唯一键**：重复消费会撞唯一约束，捕获 `IntegrityError` 当成功 ——
   派生数据重复计数比丢一行更难排查。
3. **永不无限重试**：同一事件失败超过 `MAX_RETRIES` 就投进死信流并 ACK 原事件。

消费端崩了不影响任何业务请求（统计是可丢的）。
"""
import asyncio
import json
import logging

from sqlalchemy.exc import IntegrityError

from backend.app.models import QaEvent
from config import settings

logger = logging.getLogger(__name__)

GROUP = "qa_consumers"
MAX_RETRIES = 3
CONSUMER = "worker-1"
DEAD_SUFFIX = ":dead"


async def handle_event(db, fields: dict) -> None:
    """把一条事件写进 `qa_events`。字段缺失/非法时**静默跳过**（见 docstring 末）。

    ⚠ 不抛异常的原因：抛出去会被循环当成「消费失败」而无限重投一条永远不可能成功的脏数据。
    """
    try:
        row = QaEvent(event_id=str(fields["event_id"]),
                      user_id=int(fields["user_id"]),
                      thread_id=str(fields["thread_id"]),
                      rewrites=int(fields.get("rewrites", 0)),
                      grounded=str(fields.get("grounded", "0")) in ("1", "true", "True"),
                      latency_ms=int(fields.get("latency_ms", 0)))
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("[qa-event] 事件字段非法，跳过: %s (%s)", fields, exc)
        return

    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        # 重复 event_id = 重投，属正常路径
        await db.rollback()
        logger.info("[qa-event] 重复事件，已忽略 event_id=%s", row.event_id)


async def consume(redis, session_factory, handler=handle_event,
                  max_retries: int = MAX_RETRIES, poll_interval: float = 1.0,
                  group: str = GROUP, consumer: str = CONSUMER) -> None:
    """后台消费循环（在 lifespan 里 `create_task` 起来，退出时 cancel）。

    重试计数放在**进程内存**里：重启后 PEL 会重投，计数从头开始 —— 这是可接受的
    （最坏是重试次数略多于 max_retries，不会漏处理）。
    """
    stream = settings.qa_stream_key
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception as exc:                       # BUSYGROUP = 组已存在，正常
        logger.debug("[qa-event] 消费组已存在或创建失败: %s", exc)

    attempts: dict[str, int] = {}
    while True:
        try:
            resp = await redis.xreadgroup(group, consumer, {stream: ">"}, count=10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[qa-event] 读取失败，稍后重试: %s", exc)
            await asyncio.sleep(poll_interval)
            continue

        for _stream_name, entries in (resp or []):
            for msg_id, fields in entries:
                try:
                    async with session_factory() as db:
                        await handler(db, fields)
                        await db.commit()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    attempts[msg_id] = attempts.get(msg_id, 0) + 1
                    logger.warning("[qa-event] 处理失败(%d/%d) id=%s: %s",
                                   attempts[msg_id], max_retries, msg_id, exc)
                    if attempts[msg_id] >= max_retries:
                        await _to_dead_letter(redis, stream, msg_id, fields, exc)
                        await redis.xack(stream, group, msg_id)   # 原事件也 ACK，不再重投
                        attempts.pop(msg_id, None)
                    continue
                await redis.xack(stream, group, msg_id)
                attempts.pop(msg_id, None)

        await asyncio.sleep(poll_interval)


async def _to_dead_letter(redis, stream: str, msg_id: str, fields: dict, exc) -> None:
    """投死信流：保留原始字段 + 失败原因，便于事后人工排查。"""
    try:
        payload = dict(fields)
        payload["_failed_msg_id"] = msg_id
        payload["_error"] = f"{type(exc).__name__}: {exc}"[:200]
        await redis.xadd(stream + DEAD_SUFFIX, payload, maxlen=1000, approximate=True)
        logger.error("[qa-event] 超重试上限，已投死信流 id=%s", msg_id)
    except Exception as e:
        logger.error("[qa-event] 投死信流失败 id=%s: %s", msg_id, e)
```

**Step 4：在 `main.py` 的 lifespan 里起停（**插在 `yield` 前后，不要动既有清理顺序**）**

```python
    # 问答事件消费端：派生数据，起不来/挂掉都不影响业务请求
    consumer_task = None
    if app.state.redis_pool is not None:
        from backend.app.services.qa_event_consumer import consume
        from backend.tools.mysql_db_tools import get_db_session_maker
        consumer_task = asyncio.create_task(
            consume(aioredis.Redis(connection_pool=app.state.redis_pool),
                    get_db_session_maker()))
    yield
    if consumer_task is not None:
        consumer_task.cancel()
        with suppress(asyncio.CancelledError):
            await consumer_task
    ...
```

`main.py` 顶部补 `import asyncio` 与 `from contextlib import asynccontextmanager, suppress`。

**Step 5：跑测试确认通过**

Run: `<python.exe> -m pytest tests/test_qa_event_consumer.py -q --no-header`
Expected: `4 passed`

**Step 6：跑全量**

Run: `<python.exe> -m pytest tests -q --no-header`
Expected: 全绿

**Step 7：提交**

```bash
git add rag_qa_project/backend/app/services/qa_event_consumer.py rag_qa_project/backend/app/api/main.py \
        rag_qa_project/tests/test_qa_event_consumer.py
git commit -m "feat(p4): 问答事件消费端（消费组 / event_id 幂等 / 死信流）"
```

---

## Task 8：在线用例（真 Redis，`-m redis`）

**Files:**
- Modify: `rag_qa_project/tests/test_redis.py`（追加 3 条，沿用该文件已有的 `_configured_url()` 与 marker）

**Step 1：写测试**

```python
@pytest.mark.redis
def test_setex_ttl_is_applied():
    """真 Redis 才验得了 TTL：写回后剩余 TTL 必须落在 (0, ttl]。"""
    import asyncio
    from backend.tools.redis_cache_tools import cached_json

    url = _configured_url()

    async def go():
        async with redis.from_url(url, decode_responses=True) as client:
            key = "ragqa:test:ttl"
            await client.delete(key)
            await cached_json(client, key, 30, lambda: _async_value({"a": 1}))
            ttl = await client.ttl(key)
            await client.delete(key)
            return ttl

    ttl = asyncio.run(go())
    assert 0 < ttl <= 30


@pytest.mark.redis
def test_invalidate_makes_next_read_miss():
    """删键后下一次读必须回源（这是「改名后立刻生效」的机制）。"""
    import asyncio
    from backend.tools.redis_cache_tools import cached_json, invalidate

    url = _configured_url()
    calls = []

    async def loader():
        calls.append(1)
        return len(calls)

    async def go():
        async with redis.from_url(url, decode_responses=True) as client:
            key = "ragqa:test:invalidate"
            await client.delete(key)
            first = await cached_json(client, key, 30, loader)
            await invalidate(client, key)
            second = await cached_json(client, key, 30, loader)
            await client.delete(key)
            return first, second

    first, second = asyncio.run(go())
    assert first == 1 and second == 2, "删键后必须重新回源"


@pytest.mark.redis
def test_stream_produce_and_consume_roundtrip():
    """生产→消费→XACK 全链路一次（含 MAXLEN 截断）。"""
    import asyncio

    url = _configured_url()

    async def go():
        async with redis.from_url(url, decode_responses=True) as client:
            key = "ragqa:test:stream"
            await client.delete(key)
            await client.xadd(key, {"event_id": "e2e", "user_id": "1"}, maxlen=100, approximate=True)
            resp = await client.xread({key: "0"}, count=1)
            await client.delete(key)
            return resp

    resp = asyncio.run(go())
    assert resp and resp[0][1][0][1]["event_id"] == "e2e"
```

（`_async_value` 是个小 helper：`async def _async_value(v): return v`，加在文件里。）

**Step 2：跑在线用例**

Run: `<python.exe> -m pytest -m redis -q --no-header`
Expected: `5 passed`（此前 2 条 + 新增 3 条）

**Step 3：确认默认套件不受影响**

Run: `<python.exe> -m pytest tests -q --no-header`
Expected: 仍全绿，且显示 `N deselected`（在线用例被排除）

**Step 4：提交**

```bash
git add rag_qa_project/tests/test_redis.py
git commit -m "test(p4): 在线用例 3 条（TTL / 删键回源 / Streams 往返）"
```

---

## Task 9：验收（逐条对照设计文档 §12）

**Step 1：契约零变化** —— 用改动前的响应做对照

```bash
# 改动前先录一份（在 Task 1 之前做最好；若已改动，用 git stash 录）
curl -s -H "Authorization: Bearer <token>" localhost:8000/api/chat/threads > /tmp/before.json
# 改动后再录一份
curl -s -H "Authorization: Bearer <token>" localhost:8000/api/chat/threads > /tmp/after.json
diff <(python -m json.tool /tmp/before.json) <(python -m json.tool /tmp/after.json)
```
Expected: 无差异（结构、字段名、时间格式、`total` 语义全不变）

**Step 2：降级成立（不需要真 Redis 也能验）**

```bash
RAGQA_REDIS_CACHE_ENABLED=false uvicorn backend.app.api.main:app --port 8001
curl -s localhost:8001/api/health
curl -s localhost:8001/api/chat/threads -H "Authorization: Bearer <token>"
```
Expected: 均正常返回；日志里**没有** Redis 相关 warning（因为压根没走缓存）

**Step 3：命中确实生效**

```bash
# 连点两次列表，第二次日志不应出现 MySQL 查询（或直接看 test_hit_path_does_not_touch_mysql）
for i in 1 2; do curl -s -o /dev/null -w "%{time_total}\n" localhost:8000/api/chat/threads -H "Authorization: Bearer <token>"; done
redis-cli -h 192.168.88.130 TTL ragqa:conv:list:v1:1
```
Expected: TTL 落在 (0, 60]

**Step 4：失效即时**

改名一次后立刻拉列表，标题必须是新值（不是 TTL 过期后的值）。

**Step 5：MQ 闭环**

```bash
# 发一轮问答，然后
redis-cli -h 192.168.88.130 XRANGE ragqa:stream:qa_stats - + COUNT 1
# 查库
<python.exe> -c "import asyncio; from sqlalchemy import select, func; ..."   # 或直接 SQL 客户端
```
Expected: `qa_events` 多一行，`latency_ms` > 0

**Step 6：全量套件 + 在线用例**

Run: `<python.exe> -m pytest tests -q --no-header && <python.exe> -m pytest -m redis -q --no-header`
Expected: 都全绿

**Step 7：更新 README**

在 `rag_qa_project/README.md` 的「会话列表（侧栏）」章节后追加一小节「缓存与事件流（P4）」，写明：三点定位（缓存可丢 / MySQL 是真相源 / 授权不走缓存）、回滚开关、以及**本期无性能收益**这句诚实声明。

**Step 8：提交**

```bash
git add rag_qa_project/README.md
git commit -m "docs(p4): README 补充缓存与事件流说明（含回滚开关与收益说明）"
```

---

## 完成定义（Definition of Done）

- [ ] Task 0 的 `.env` 已修，`pytest -m redis` 全绿
- [ ] `pytest tests` 全绿（离线基线零外部依赖）
- [ ] 设计文档 §12 的 9 条验收逐条有证据
- [ ] `assert_owner` 的调用链上没有 Redis（用代码搜索确认：`grep -rn "assert_owner" backend/` 里没有任何 redis 参数）
- [ ] 契约零变化（Step 1 的 diff 为空）
- [ ] README 写了回滚开关与「本期无性能收益」
