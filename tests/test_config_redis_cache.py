# rag_qa_project/tests/test_config_redis_cache.py
# -*- coding: utf-8 -*-
"""P4 缓存配置：默认值 + 可被 RAGQA_ 环境变量覆盖。

为什么单独一条测试盯「默认值」：Redis 在本项目是**可选依赖**，三个字段都有默认值意味着
「没配 Redis 的环境也能正常启动」，而回滚开关（redis_cache_enabled）必须默认开着 ——
否则本期做的缓存等于没接。TTL 为 0 会让 SETEX 直接报错，所以也顺带钉一下它是正数。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings


def test_defaults_favour_safety():
    """默认必须「开缓存 + 60s + 有流键」，且 TTL 是正数（0 会让 SETEX 直接报错）。"""
    s = Settings(_env_file=None)

    assert s.redis_cache_enabled is True
    assert s.conv_cache_ttl == 60
    assert s.conv_cache_ttl > 0
    assert s.qa_stream_key == "ragqa:stream:qa_stats"


def test_env_can_override(monkeypatch):
    """回滚开关与 TTL 都要能通过环境变量改（RAGQA_ 前缀，与既有配置一致）。

    回滚开关不改代码就能生效，是本期唯一的「出事时怎么办」手段，所以它必须可被环境变量覆盖。
    """
    monkeypatch.setenv("RAGQA_REDIS_CACHE_ENABLED", "false")
    monkeypatch.setenv("RAGQA_CONV_CACHE_TTL", "5")

    s = Settings(_env_file=None)

    assert s.redis_cache_enabled is False
    assert s.conv_cache_ttl == 5
