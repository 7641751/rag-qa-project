# rag_qa_project/tests/test_deps.py
# -*- coding: utf-8 -*-
"""get_current_user 依赖：正常路径 + 三种失败路径都必须回契约格式错误。"""
import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from redis import asyncio as aioredis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from backend.app.api.deps import get_current_user, get_optional_redis, get_redis_pool
from backend.app.api.errors import register_error_handlers
from backend.app.agent.schemas import AuthUser
from backend.tools import security as sec


@pytest.fixture
def client(monkeypatch):
    """只挂一个探针端点，不 import 真实的 main（避免拉起 chroma/LLM 导入链）。"""
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret-" + "0" * 26)
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/probe")
    async def probe(user: AuthUser = Depends(get_current_user)):
        return {"id": user.id, "username": user.username}

    return TestClient(app)


def test_valid_token_returns_user(client):
    token = sec.create_access_token(7, "hao")
    r = client.get("/probe", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert r.json() == {"id": 7, "username": "hao"}


def test_missing_header_gives_401_contract_body(client):
    """★ 契约：401 且响应体是 {"code","message"}，不是 FastAPI 默认的 {"detail"}。"""
    r = client.get("/probe")

    assert r.status_code == 401
    assert set(r.json()) == {"code", "message"}
    assert r.json()["code"] == "UNAUTHORIZED"


def test_wrong_scheme_gives_401(client):
    """`Basic xxx` 不是 Bearer，必须拒。"""
    r = client.get("/probe", headers={"Authorization": "Basic aGFvOmhhbw=="})
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def test_tampered_token_gives_401(client):
    token = sec.create_access_token(1, "hao")
    head, payload, sig = token.split(".")

    # ⚠ 与 test_security.py 里同一处 flaky 修正（两处曾各有一份）：判据读 `sig[-1]`、
    #   却替换 `sig[0]` → 当 `sig[-1] != 'A'` 且 `sig[0] == 'A'` 时 bad 与原 token 相同，
    #   压根没篡改 → 实测 1.38% 概率返回 200 而不是 401。
    pos = 0 if sig[0] != "A" else 1
    bad = (f"{head}.{payload}.{sig[:pos]}"
           f"{'A' if sig[pos] != 'A' else 'B'}{sig[pos + 1:]}")
    assert bad != token, "构造失败：篡改后的串与原串相同，这条用例已失去意义"

    r = client.get("/probe", headers={"Authorization": f"Bearer {bad}"})

    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def test_sub_must_be_int_like(client):
    """token 的 sub 是字符串（RFC 7519），依赖要把它还原成 int 主键。"""
    token = sec.create_access_token(42, "hao")
    r = client.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert r.json()["id"] == 42


# ============================ get_redis_pool ============================
def _request_for(app: FastAPI) -> Request:
    """造一个最小 scope 的 Request：只为让 request.app 指向给定 app。

    Starlette 的 `Request.app` 就是 `scope["app"]`，所以不需要真实服务或 TestClient。
    这样调用依赖就能完全离线（Redis 客户端从池里构造时**不会**建连，已实测）。
    """
    return Request({"type": "http", "app": app, "headers": []})


def test_get_redis_pool_returns_client_bound_to_app_state_pool():
    """★ 契约：必须返回**挂在 app.state 那个池**上的客户端，而不是另建一个池。

    另建池的后果是每个请求各开一份连接池 —— 连接数随请求数线性增长，
    而 Redis 服务端的 maxclients 是有限的，表现为「压测一会儿就开始连接超时」。

    顺带钉住 decode_responses：main.py 建池时传了它，客户端必须继承 ——
    少了它就是返回 bytes，业务里做字符串比较会静默失配。
    """
    app = FastAPI()
    pool = aioredis.ConnectionPool.from_url("redis://127.0.0.1:6399/0", decode_responses=True)
    app.state.redis_pool = pool

    client = asyncio.run(get_redis_pool(_request_for(app)))

    assert isinstance(client, aioredis.Redis)
    assert client.connection_pool is pool, "必须复用 state 上的池，不能另建"
    assert client.connection_pool.connection_kwargs["decode_responses"] is True


def test_get_redis_pool_fails_fast_when_unconfigured():
    """Redis 未配置时要立刻报错，并点名该配哪个环境变量。

    main.py 在未配置时把 redis_pool 置为 None（而不是无条件建池 —— from_url("")
    会抛 ValueError 让整个后端起不来）。此时依赖必须给出可操作的提示，
    否则报错点会漂到第一次真的发命令时，排查成本高得多。
    """
    app = FastAPI()                       # 刻意不设 app.state.redis_pool

    with pytest.raises(RuntimeError) as ei:
        asyncio.run(get_redis_pool(_request_for(app)))

    msg = str(ei.value)
    assert "RAGQA_REDIS_URL" in msg and "REDIS_URL" in msg, \
        f"错误信息必须指出该配哪个环境变量，实际: {msg}"


# ============================ get_optional_redis ============================
def test_get_optional_redis_returns_none_when_pool_missing():
    """★ 与 get_redis_pool 的关键区别：池不存在时返回 None（降级），**不抛异常**。

    缓存是可选依赖：Redis 没配或挂了，列表接口必须照常返回数据。
    get_redis_pool 保持「抛 RuntimeError」是因为它的定位是硬依赖 —— 两者分工不能混。
    """
    app = FastAPI()                       # 刻意不设 app.state.redis_pool

    assert asyncio.run(get_optional_redis(_request_for(app))) is None


def test_get_optional_redis_returns_none_when_switch_off(monkeypatch):
    """开关关闭 ⇒ 即使池在也返回 None（让回滚开关对调用方完全透明）。"""
    monkeypatch.setattr(settings, "redis_cache_enabled", False)
    app = FastAPI()
    app.state.redis_pool = object()

    assert asyncio.run(get_optional_redis(_request_for(app))) is None


def test_get_optional_redis_returns_command_usable_client():
    """★ 开关开着且池存在 ⇒ 必须返回**能真的发命令**的客户端，且复用 state 上的池。

    这条挡的是一个「看起来接好了、命中率恒为 0」的静默失效：
    `app.state.redis_pool` 是 ConnectionPool，**它没有 get/set/xadd**（已实测）。
    若依赖直接返回池，`cached_json` 里 `await redis.get(...)` 会抛 AttributeError，
    被缓存层的宽兜底吞成一条 error 日志 —— 接口照常 200，但缓存永远不命中。
    所以这里既要断言协议可用，也要断言不新建池（每请求新建池会线性涨连接数）。
    """
    app = FastAPI()
    pool = aioredis.ConnectionPool.from_url("redis://127.0.0.1:6399/0", decode_responses=True)
    app.state.redis_pool = pool

    client = asyncio.run(get_optional_redis(_request_for(app)))

    assert isinstance(client, aioredis.Redis)
    for method in ("get", "set", "delete", "xadd"):   # xadd 供 Task 7 的消费端复用
        assert callable(getattr(client, method, None)), f"缺少 {method}：缓存工具会静默降级"
    assert client.connection_pool is pool, "必须复用 state 上的池，不能另建"


# ============================ main 的建池契约（P4 复审：超时是必填项）============================
def test_build_redis_pool_has_timeouts_and_none_when_unconfigured():
    """★ 池必须带 3s 超时；未配置必须返回 None（而不是抛）。

    超时缺失的代价是实测出来的：Redis 主机黑洞（DROP）且客户端无超时时，每次连接
    尝试白等 5010ms —— 每个列表请求都被拖 5 秒才降级回源；加 3s 超时后封顶 3008ms。
    所以这条断言的实质是「降级承诺的时间上限」，不是参数存在性检查。

    （这里才 import 真实的 main：本文件其余用例靠探针 app 保持无 chroma/LLM 导入链，
    见文件中部 fixture 的注释；main 的导入链已被 test_kb_requires_login 等覆盖，
    惰性 import 只是让本文件单独跑时不必付这份成本。）
    """
    from backend.app.api.main import build_redis_pool

    pool = build_redis_pool("redis://127.0.0.1:6399/0")
    kw = pool.connection_kwargs
    assert kw["socket_connect_timeout"] == 3 and kw["socket_timeout"] == 3, \
        "无超时 ⇒ 黑洞时每请求白等 5 秒（实测 5010ms），降级承诺落空"
    assert kw["decode_responses"] is True, "少了它就是 bytes，业务比较会静默失配"

    assert build_redis_pool("") is None, "未配置必须返回 None（走旁路），而不是抛"
    assert build_redis_pool(None) is None
    assert build_redis_pool("   ") is None, "空白串也按未配置处理"