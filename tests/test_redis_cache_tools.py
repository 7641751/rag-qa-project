# rag_qa_project/tests/test_redis_cache_tools.py
# -*- coding: utf-8 -*-
"""缓存工具：命中 / 回源 / 降级 / 坏值清理 / 失效。全离线（协议级假客户端，不需要 Redis）。

为什么用假客户端而不是 fakeredis：本模块的核心契约是「任何 Redis 故障都降级回源」，
要验证它必须能**按需注入异常**（连接失败、写失败、坏值）。假客户端三行就能做到这件事，
而 fakeredis 反而难以模拟「连不上」。
"""
import asyncio
import json
import sys
from pathlib import Path

from redis.exceptions import ConnectionError as RedisConnectionError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.tools.redis_cache_tools import cached_json, conv_list_key, invalidate


class FakeRedis:
    """只实现本模块用到的方法，并按调用顺序记录（顺序本身也是被断言的对象）。"""

    def __init__(self, store=None, get_exc=None, set_exc=None):
        self.store = dict(store or {})
        self.calls: list[tuple] = []
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
def test_redis_none_bypasses_cache():
    """redis=None 表示「未配置或开关关闭」，等价于无缓存。"""
    counter = []

    got = asyncio.run(cached_json(None, "k", 60, _loader({"a": 1}, counter)))

    assert got == {"a": 1}
    assert counter == [1], "没有缓存时必须回源"


def test_connection_error_degrades_to_source():
    """Redis 连不上 → 返回正确数据，且不再尝试写回（写了也必然失败）。"""
    redis, counter = FakeRedis(get_exc=RedisConnectionError("boom")), []

    got = asyncio.run(cached_json(redis, "k", 60, _loader({"a": 1}, counter)))

    assert got == {"a": 1}
    assert counter == [1]
    assert not any(c[0] == "set" for c in redis.calls), "读都失败了就别再试写"


def test_set_failure_still_returns_value():
    """写缓存失败不影响本次返回（只损失命中率，不影响正确性）。"""
    redis, counter = FakeRedis(set_exc=RedisConnectionError("boom")), []

    got = asyncio.run(cached_json(redis, "k", 60, _loader({"a": 1}, counter)))

    assert got == {"a": 1}


# ============================ 坏值清理 ============================
def test_corrupt_json_is_treated_as_miss_and_deleted():
    """坏值必须删掉：否则每次请求都白读一次坏键，且永远不会自愈。"""
    redis, counter = FakeRedis({"k": "{not json"}), []

    got = asyncio.run(cached_json(redis, "k", 60, _loader({"a": 1}, counter)))

    assert got == {"a": 1}, "坏值要当 miss"
    assert counter == [1]
    assert ("delete", "k") in redis.calls, "坏键要清掉，避免每请求都白读"


# ============================ 失效 ============================
def test_invalidate_deletes_key_and_is_noop_without_redis():
    """失效就是删键；redis=None 时静默返回（写路径在未配 Redis 时也要能跑）。"""
    redis = FakeRedis({"k": "v"})

    asyncio.run(invalidate(redis, "k"))
    assert ("delete", "k") in redis.calls
    assert "k" not in redis.store

    asyncio.run(invalidate(None, "k"))     # 不抛异常即通过
