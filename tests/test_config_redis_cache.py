# rag_qa_project/tests/test_config_redis_cache.py
# -*- coding: utf-8 -*-
"""P4 缓存配置：默认值 + 可被 RAGQA_ 环境变量覆盖。

为什么单独一条测试盯「默认值」：Redis 在本项目是**可选依赖**，三个字段都有默认值意味着
「没配 Redis 的环境也能正常启动」，而回滚开关（redis_cache_enabled）必须默认开着 ——
否则本期做的缓存等于没接。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings

# 会影响本模块断言的环境变量：三个 RAGQA_ 前缀的覆盖项，以及 redis_url 的两个别名
_ENV_KEYS = ("RAGQA_REDIS_CACHE_ENABLED", "RAGQA_CONV_CACHE_TTL",
             "RAGQA_QA_STREAM_KEY", "RAGQA_REDIS_URL", "REDIS_URL")


def _clean_env(monkeypatch):
    """把可能影响本模块的环境变量清掉。

    ⚠ `Settings(_env_file=None)` **并不能**隔离环境：config.py 在 import 期就调了
    `load_dotenv(...)`，把 .env 的内容写进了 `os.environ`，而 pydantic-settings 依旧会读
    `os.environ`（`_env_file=None` 只关掉「再读一次 .env 文件」）。

    后果很具体：操作者一旦按设计意图设了 `RAGQA_CONV_CACHE_TTL=300`（这正是本功能的正常
    用法），「默认值」那条用例就会变红。tests/test_mysql_db.py 早把这条约定写在注释里了：
    用例要与「本机 .env 配没配」彻底解耦。
    """
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_defaults_favour_safety(monkeypatch):
    """默认必须「开缓存 + 60s + 有流键」。

    只钉默认值，**不**声称钉住了「TTL 必须为正」—— 那是字段校验器的职责，而本 task 刻意
    不给可选依赖加启动期校验（缓存层的一切故障本就要求降级为无缓存行为）。
    """
    _clean_env(monkeypatch)

    s = Settings(_env_file=None)

    assert s.redis_cache_enabled is True
    assert s.conv_cache_ttl == 60
    assert s.qa_stream_key == "ragqa:stream:qa_stats"


def test_missing_redis_url_does_not_block_construction(monkeypatch):
    """★ Redis 是可选依赖：没配连接串也必须能构造配置。

    这是本期「Redis 不可用就降级」承诺的地基，所以值得一条断言，而不是靠「有默认值」推理。
    """
    _clean_env(monkeypatch)

    s = Settings(_env_file=None)

    assert s.redis_url == ""


def test_env_can_override(monkeypatch):
    """回滚开关、TTL、流键都要能通过环境变量改（RAGQA_ 前缀，与既有配置一致）。

    回滚开关能靠环境变量生效，是本期唯一的「出事时怎么办」手段。
    """
    _clean_env(monkeypatch)
    monkeypatch.setenv("RAGQA_REDIS_CACHE_ENABLED", "false")
    monkeypatch.setenv("RAGQA_CONV_CACHE_TTL", "5")
    monkeypatch.setenv("RAGQA_QA_STREAM_KEY", "ragqa:test:stream")

    s = Settings(_env_file=None)

    assert s.redis_cache_enabled is False
    assert s.conv_cache_ttl == 5
    assert s.qa_stream_key == "ragqa:test:stream"
