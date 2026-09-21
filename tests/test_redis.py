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
import sys
from pathlib import Path

import pytest

# 让 tests/ 能导入项目模块（等价于把 rag_qa_project 标记为 Sources Root）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402

# 放在 import 之后、用例之前：缺包时整文件跳过，而不是让每个用例各自炸一次
redis = pytest.importorskip("redis", reason="未安装 redis-py：pip install 'redis>=5.0'")

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
