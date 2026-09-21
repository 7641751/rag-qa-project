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
from backend.app.api.deps import get_current_user, get_redis_pool
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
    bad = f"{head}.{payload}.{'A' if sig[-1] != 'A' else 'B'}{sig[1:]}"

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