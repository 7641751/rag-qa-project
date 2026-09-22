# -*- coding: utf-8 -*-
"""Redis 连通性自检：验证 .env 里的 redis_url 真的能连上。

与 tests/ 其余用例（全部离线、零外部依赖）不同，**这一条需要真实的 Redis 服务**，
所以它的定位是「本地/预发环境自检」，不是 CI 门禁。判据刻意分成两层：

1. **格式层**（永远跑，不需要服务）：URL 能被 redis-py 解析，scheme/host/port 取对。
   它先于连接层失败，用于立刻区分「URL 写错了」和「服务没起」——这两件事的
   排查方向完全不同。
2. **连接层**（配了才跑）：PING 通。
   · `redis_url` 未配置 → **skip**：CI 与同事的机器上不该因此变红；
   · 配了但连不上   → **fail**：配了却连不上就是配置/环境错误，
     用 skip 掩盖它恰恰会错过「我明明配了」这个最该被立刻告警的状态。

配置从哪来：`settings.redis_url`。注意 `.env` 里用的是裸 `REDIS_URL`，而 Settings 的
env_prefix 是 `RAGQA_` —— 两者都对得上靠的是该字段上的 validation_alias（见 config.py）。
本文件因此只断言**解析结果**，不钉「到底读的哪个变量名」，避免把实现细节写进测试。

运行方式（在 rag_qa_project 目录下）：
    python -m pytest tests/test_redis.py -v
"""
import asyncio
import sys
from pathlib import Path

import pytest

# 让 tests/ 能导入项目模块（等价于把 rag_qa_project 标记为 Sources Root）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402

# 放在 import 之后、用例之前：缺包时整文件跳过，而不是让每个用例各自炸一次
redis = pytest.importorskip("redis", reason="未安装 redis-py：pip install 'redis>=5.0'")

# 异步客户端（P4 在线用例用）：cached_json / invalidate / 消费端全是 async API。
# 与上面的同步 `redis` 区分开 —— 同步客户端没有 __aenter__，await 会直接炸。
aioredis = pytest.importorskip("redis.asyncio", reason="未安装 redis-py 的 asyncio 支持")

def _configured_url() -> str:
    """取配置里的 Redis 连接串；**未配置时 skip**（不是 fail）。"""
    url = (settings.redis_url or "").strip()
    if not url:
        pytest.skip("RAGQA_REDIS_URL / REDIS_URL 未配置，跳过 Redis 连通性自检")
    return url


# ============================ 1. 格式层（不需要服务） ============================
def test_configured_redis_url_is_parseable():
    """URL 必须能被 redis-py 解析，且取得到 scheme/host/port。

    这一层能独立抓住「URL 写错」这类问题：密码里的 `@` 没转义成 `%40`（会把主机名
    从中间切断）、TLS 端口上漏了 `rediss://` 的那个 s、把库号写成 `?db=1`
    （redis-py 只认路径里的 `/1`）等等 —— 全都不需要 Redis 在跑就能发现。
    """
    url = _configured_url()

    parsed = redis.connection.parse_url(url)

    # ⚠ parse_url 返回的是**连接 kwargs 字典**，**没有 scheme 键**（redis-py 8.1.0 实测）。
    #   TLS 与否、是不是 unix socket，都靠 connection_class 区分。
    conn_cls = parsed.get("connection_class")
    if conn_cls is redis.UnixDomainSocketConnection:
        assert parsed.get("path"), f"unix socket 没解析出 path：{parsed}"
    else:
        assert parsed.get("host"), f"没解析出 host（检查密码里的 @ 是否漏了 %40）：{parsed}"
        assert parsed.get("port"), f"没解析出 port（默认应为 6379）：{parsed}"
        assert isinstance(parsed.get("db", 0), int), f"db 不是整数：{parsed}"
    if url.startswith("rediss://"):
        assert conn_cls is redis.SSLConnection, \
            "rediss:// 必须解析成 SSLConnection，否则会把 TLS 握手发去明文端口"


# ============================ 2. 连接层（配了才跑） ============================
@pytest.mark.redis
def test_configured_redis_url_is_reachable():
    """PING 通即通过。

    超时压到 3 秒：连不上时要立刻报错，而不是本机防火墙默默丢包、
    把测试卡在默认的十几秒上。`with` 保证连接被关闭，输出才干净（无 ResourceWarning）。
    """
    url = _configured_url()

    with redis.from_url(url, socket_connect_timeout=3, socket_timeout=3) as client:
        assert client.ping() is True, "PING 未返回 True"


# ============================ 3. P4 在线用例：缓存 / 失效 / Streams（真 Redis）============================
async def _async_value(v):
    """把普通值包成 awaitable，供 cached_json 的 loader 参数用。"""
    return v


@pytest.mark.redis
def test_setex_ttl_is_applied():
    """真 Redis 才验得了 TTL：写回后剩余 TTL 必须落在 (0, ttl]。

    离线套件只能断到「set 时带了 ex 参数」（参数层）；真 Redis 才能证明这个 ex
    真的被服务端执行了 —— 参数对了而服务端没设 TTL 是参数层看不见的。
    """
    from backend.tools.redis_cache_tools import cached_json

    async def go():
        async with aioredis.from_url(_configured_url(), decode_responses=True,
                                     socket_connect_timeout=3, socket_timeout=3) as client:
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
    from backend.tools.redis_cache_tools import cached_json, invalidate

    calls = []

    async def loader():
        calls.append(1)
        return len(calls)

    async def go():
        async with aioredis.from_url(_configured_url(), decode_responses=True,
                                     socket_connect_timeout=3, socket_timeout=3) as client:
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
    """生产 → 消费组读 → XACK 全链路一次（真 Redis）。

    这同时是**消费端 API 用法的兼容性校验**：消费端此前只在 FakeRedis 上验过，
    这里用与 `qa_event_consumer.consume` 完全同形的调用（xgroup_create 的 id/mkstream、
    xreadgroup 的 ">"、xack 的三参形态）在真 redis-py 上走一遍 —— 参数形态错的
    话，离线替身是发现不了的。
    """
    async def go():
        async with aioredis.from_url(_configured_url(), decode_responses=True,
                                     socket_connect_timeout=3, socket_timeout=3) as client:
            key = "ragqa:test:stream"
            group = "ragqa_test_group"
            await client.delete(key)
            await client.xadd(key, {"event_id": "e2e", "user_id": "1"},
                              maxlen=100, approximate=True)
            await client.xgroup_create(key, group, id="0", mkstream=True)
            resp = await client.xreadgroup(group, "t1", {key: ">"}, count=1)
            msg_id, fields = resp[0][1][0]
            await client.xack(key, group, msg_id)
            pending = await client.xpending(key, group)
            await client.delete(key)
            return fields, pending

    fields, pending = asyncio.run(go())
    assert fields["event_id"] == "e2e" and fields["user_id"] == "1"
    assert pending["pending"] == 0, "XACK 后 PEL 必须清空"
