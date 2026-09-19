# rag_qa_project/tests/test_deps.py
# -*- coding: utf-8 -*-
"""get_current_user 依赖：正常路径 + 三种失败路径都必须回契约格式错误。"""
import sys
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from backend.app.api.deps import get_current_user
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