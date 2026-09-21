# rag_qa_project/tests/test_redis_cache_tools.py
# -*- coding: utf-8 -*-
"""缓存工具：命中 / 回源 / 降级 / 坏值清理 / 失效。全离线（协议级假客户端，不需要 Redis）。

为什么用假客户端而不是 fakeredis：本模块的核心契约是「任何 Redis 故障都降级回源」，
要验证它必须能**按需注入异常**（连接失败、命令失败、序列化失败、业务异常）。
假客户端几行就能做到这件事，而 fakeredis 反而难以模拟「连不上」。

覆盖口径（被质量审查的变异测试逼出来的）：模块里 5 个吞异常点都有对应用例 ——
读 RedisError / 读非 RedisError / 坏值删除失败 / 写回失败 / 值无法序列化，
外加一条**反向契约**：loader 的业务异常必须原样上抛（缓存层不替业务吞错）。
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.tools.redis_cache_tools import cached_json, conv_list_key, invalidate

LOGGER_NAME = "backend.tools.redis_cache_tools"


class FakeRedis:
    """只实现本模块用到的方法，并按调用顺序记录（顺序本身也是被断言的对象）。"""

    def __init__(self, store=None, get_exc=None, set_exc=None, delete_exc=None):
        self.store = dict(store or {})
        self.calls: list[tuple] = []
        self._get_exc = get_exc
        self._set_exc = set_exc
        self._delete_exc = delete_exc

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
        if self._delete_exc:
            raise self._delete_exc
        return self.store.pop(key, None)


def _loader(value, counter):
    """返回一个「被调用就计数」的 loader（协程函数，不是协程对象）。"""
    async def load():
        counter.append(1)
        return value
    return load


# ============================ 键设计 ============================
def test_conv_list_key_is_namespaced_by_user_and_versioned():
    """键要带用户 id（隔离）与版本位（改结构时不必写迁移脚本）。"""
    assert conv_list_key(7) == "ragqa:conv:list:v1:7"
    assert conv_list_key(7) != conv_list_key(8)


# ============================ 命中 / 回源 ============================
def test_miss_calls_loader_and_writes_back_with_ttl():
    redis, counter = FakeRedis(), []

    got = asyncio.run(cached_json(redis, "k", 60, _loader({"a": 1}, counter)))

    assert got == {"a": 1}
    assert counter == [1], "miss 必须回源"
    assert ("set", "k", 60) in redis.calls, "写回必须带 ex=ttl，否则键永不过期"
    assert json.loads(redis.store["k"]) == {"a": 1}, \
        "写回去的必须是 JSON 文本（不序列化的话真 Redis 会抛 DataError，然后被吞成 warning，" \
        "表现为「缓存永远不填充」却毫无告警）"


def test_hit_does_not_call_loader():
    """★ 这是本期唯一的收益来源：命中时绝不回源。"""
    redis, counter = FakeRedis({"k": json.dumps({"a": 1})}), []

    got = asyncio.run(cached_json(redis, "k", 60, _loader({"a": 2}, counter)))

    assert got == {"a": 1}, "必须返回缓存里的值"
    assert counter == [], "命中时绝不能调 loader"


def test_hit_does_not_refresh_ttl():
    """命中时不续期：否则热点键永不过期，数据改了也长期不收敛（缓存雪崩笔记里的经典反例）。"""
    redis, counter = FakeRedis({"k": json.dumps({"a": 1})}), []

    asyncio.run(cached_json(redis, "k", 60, _loader({"a": 1}, counter)))

    assert not any(c[0] == "set" for c in redis.calls), "命中路径不该有任何写操作"


# ============================ 降级 ============================
def test_redis_none_bypasses_cache(caplog):
    """redis=None 表示「未配置或开关关闭」，等价于无缓存，且**静默**（不刷告警）。"""
    counter = []

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        got = asyncio.run(cached_json(None, "k", 60, _loader({"a": 1}, counter)))

    assert got == {"a": 1}
    assert counter == [1], "没有缓存时必须回源"
    assert [r for r in caplog.records if r.name == LOGGER_NAME] == [], \
        "开关关闭是正常状态，不该产生告警（Task 9 验收要看日志干净）"


def test_connection_error_degrades_to_source(caplog):
    """Redis 连不上 → 返回正确数据，且不再尝试写回（写了也必然失败）。"""
    redis, counter = FakeRedis(get_exc=RedisConnectionError("boom")), []

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        got = asyncio.run(cached_json(redis, "k", 60, _loader({"a": 1}, counter)))

    assert got == {"a": 1}
    assert counter == [1]
    assert not any(c[0] == "set" for c in redis.calls), "读都失败了就别再试写"
    assert [r.levelno for r in caplog.records] == [logging.WARNING], \
        "连接抖动是常态，记 warning 就够（记 error 会把日志刷满）"


def test_non_redis_exception_also_degrades(caplog):
    """★ 缓存层不得把「客户端/我们自己的 bug」升级成业务故障 —— 这条与异常类型无关。

    质量审查的变异测试指出：本分支原先没有任何用例守着，把它删掉套件依然全绿。
    """
    redis, counter = FakeRedis(get_exc=RuntimeError("bug in client")), []

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        got = asyncio.run(cached_json(redis, "k", 60, _loader({"a": 1}, counter)))

    assert got == {"a": 1}
    assert counter == [1]
    assert not any(c[0] == "set" for c in redis.calls)
    assert [r.levelno for r in caplog.records] == [logging.ERROR], \
        "非 Redis 侧异常是代码 bug，要记 error 才能被看见"


def test_set_failure_still_returns_value():
    """写缓存失败不影响本次返回（只损失命中率，不影响正确性）。"""
    redis, counter = FakeRedis(set_exc=RedisConnectionError("boom")), []

    got = asyncio.run(cached_json(redis, "k", 60, _loader({"a": 1}, counter)))

    assert got == {"a": 1}


def test_unserializable_value_is_dropped_without_raising(caplog):
    """loader 返回不可序列化对象（代码 bug）→ 记 error、放弃写回，但**值照常返回**。

    这条守的是「序列化失败」与「Redis 写失败」被混成一条 warning 的旧写法：
    那样「缓存永远不填充」会伪装成环境抖动，长期无人发现。
    """
    sentinel = object()                       # 用同一实例比对，避免 isinstance(_, object) 这种恒真断言
    redis, counter = FakeRedis(), []

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        got = asyncio.run(cached_json(redis, "k", 60, _loader(sentinel, counter)))

    assert got is sentinel, "序列化失败也不能改变返回值"
    assert counter == [1]
    assert not any(c[0] == "set" for c in redis.calls), "序列化都失败了，不该调 set"
    assert [r.levelno for r in caplog.records if r.name == LOGGER_NAME] == [logging.ERROR]


def test_loader_business_error_propagates():
    """★ 反向契约：loader 的业务异常必须原样上抛。

    缓存层只兜自己的错。若这里被写成「什么都吞」，MySQL 故障会被伪装成「缓存降级成功」，
    用户拿到的是空列表而不是 500 —— 那是最难排查的一类故障。
    """
    counter = []

    async def failing_loader():
        counter.append(1)
        raise RuntimeError("mysql down")

    with pytest.raises(RuntimeError, match="mysql down"):
        asyncio.run(cached_json(FakeRedis(), "k", 60, failing_loader))

    assert counter == [1]


# ============================ 坏值清理 ============================
def test_corrupt_json_is_treated_as_miss_and_deleted():
    """坏值必须删掉：否则每次请求都白读一次坏键，且永远不会自愈。"""
    redis, counter = FakeRedis({"k": "{not json"}), []

    got = asyncio.run(cached_json(redis, "k", 60, _loader({"a": 1}, counter)))

    assert got == {"a": 1}, "坏值要当 miss"
    assert counter == [1]
    assert ("delete", "k") in redis.calls, "坏键要清掉，避免每请求都白读"


def test_corrupt_value_delete_failure_still_misses():
    """删坏键失败（连不上）也不能把业务带崩 —— 兜底是 TTL 到期后键自己消失。"""
    redis, counter = FakeRedis({"k": "{not json"},
                              delete_exc=RedisConnectionError("boom")), []

    got = asyncio.run(cached_json(redis, "k", 60, _loader({"a": 1}, counter)))

    assert got == {"a": 1}
    assert counter == [1]


# ============================ 失效 ============================
def test_invalidate_deletes_key_and_is_noop_without_redis():
    """失效就是删键；redis=None 时静默返回（写路径在未配 Redis 时也要能跑）。"""
    redis = FakeRedis({"k": "v"})

    asyncio.run(invalidate(redis, "k"))
    assert ("delete", "k") in redis.calls
    assert "k" not in redis.store

    asyncio.run(invalidate(None, "k"))     # 不抛异常即通过


def test_invalidate_failure_does_not_raise():
    """失效失败只记日志（best-effort）：TTL 会兜底，不该让用户的「改名」请求失败。"""
    redis = FakeRedis({"k": "v"}, delete_exc=RedisConnectionError("boom"))

    asyncio.run(invalidate(redis, "k"))    # 不抛异常即通过
    assert "k" in redis.store, "没删掉就还在 —— 靠 TTL 收敛"
