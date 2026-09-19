# rag_qa_project/tests/test_kb_requires_login.py
# -*- coding: utf-8 -*-
"""KB 三端点的鉴权门（401）与归属隔离（404）。

**P3 已翻转本文件的第二条断言**：原先 `test_kb_endpoints_require_login_but_not_ownership`
断言「用户 B 能删掉用户 A 上传的文档且返回 200」，那是 P2 有意留下的已知缺口，
写成断言是为了让它一旦变化就立刻红灯。P3 补上归属隔离后它如期变红，现改名为
`test_delete_other_users_doc_returns_404` 并断言 404 —— 这个「红灯提醒」机制本身
起了作用，值得在此留档。

设计思想沿用 test_kb_upload.py：假嵌入 + tmp_path 独立 Chroma，全程不触网、零 API 额度。
"""
import json
import sys
from pathlib import Path

import pytest

# 让 tests/ 能导入项目模块（等价于把 rag_qa_project 标记为 Sources Root）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_chroma import Chroma
from langchain_core.embeddings import DeterministicFakeEmbedding

from config import settings
from backend.tools import security as sec
from backend.tools import upload_function_tools as ft
from backend.app.api import kb_router
from backend.app.api.errors import register_error_handlers


# ============================ 测试替身（Fakes） ============================
@pytest.fixture
def fake_vs(tmp_path, monkeypatch):
    """每个测试一份独立 Chroma（tmp_path 隔离），嵌入用假模型，原件落到 tmp_path。"""
    vs = Chroma(collection_name="kb_auth_test",
                embedding_function=DeterministicFakeEmbedding(size=8),
                persist_directory=str(tmp_path / "chroma"))
    monkeypatch.setattr(ft, "get_vectorstore", lambda: vs)
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    return vs


@pytest.fixture
def kb_client(fake_vs, monkeypatch):
    """只挂 kb_router 的测试应用，逐个端点验证鉴权门。

    必须调 register_error_handlers：get_current_user 走的是 api_error(401, ...)，
    即 HTTPException(detail={"code","message"})。不注册处理器的话响应体会被包成
    {"detail": {...}}，与契约 docs/api/README.md 的 {"code","message"} 不符 ——
    那样测试就对不上真实 main.py 的行为（它在启动时就注册了处理器）。
    """
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret-" + "0" * 26)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(kb_router.router)
    return TestClient(app)


def _token(user_id: int, username: str) -> dict:
    """造一个已登录用户的 Authorization 头。"""
    return {"Authorization": f"Bearer {sec.create_access_token(user_id, username)}"}


def _upload(client, headers: dict | None = None, filename: str = "notes.md"):
    """走真实上传端点（SSE 流），用于把一份文档放进假向量库。"""
    raw = ("# 鉴权测试文档\n\n" + "用于鉴权测试的正文内容。" * 20).encode("utf-8")
    return client.post("/api/kb/documents",
                       files={"file": (filename, raw, "application/octet-stream")},
                       headers=headers or {})


def _done_frame(body: str) -> dict:
    """从 SSE 响应体里取出 done 帧的 data（只有上传成功才会有）。"""
    for block in body.split("\n\n"):
        if "event: done" in block:
            for line in block.splitlines():
                if line.startswith("data: "):
                    return json.loads(line[len("data: "):])
    raise AssertionError(f"SSE 响应里没有 done 帧，实际内容：\n{body}")


# ============================ 1. 三个端点都要求登录 ============================
def test_upload_without_token_401(kb_client):
    """上传：不带 token 必须被拒，且响应体是契约形状。

    刻意带上合法的文件体：否则 422（缺 file 字段）会与 401 混淆，
    这条用例就证明不了"是鉴权拦下的"。
    """
    r = _upload(kb_client)

    assert r.status_code == 401
    assert set(r.json()) == {"code", "message"}
    assert r.json()["code"] == "UNAUTHORIZED"


def test_list_without_token_401(kb_client):
    r = kb_client.get("/api/kb/documents")

    assert r.status_code == 401
    assert set(r.json()) == {"code", "message"}
    assert r.json()["code"] == "UNAUTHORIZED"


def test_delete_without_token_401(kb_client):
    r = kb_client.delete("/api/kb/documents/d1")

    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def test_valid_token_opens_the_gate(kb_client):
    """带合法 token 时 401 必须消失。

    这条是上面三条的对照组：没有它，前面三条也可能因为别的原因（路由 404 被
    异常处理器改写成 401 之类）假通过，而我们并不知道"门到底开不开"。
    """
    r = kb_client.get("/api/kb/documents", headers=_token(1, "hao"))

    assert r.status_code == 200
    assert r.json()["documents"] == []


# ============================ 2. 归属隔离（P3 起生效） ============================
def test_delete_other_users_doc_returns_404(kb_client, fake_vs):
    """★ P3 **翻转**了这条：原先断言「B 删 A 的文档返回 200」，那是刻意保留的已知缺口。

    现在 metadata 带 user_id、删除按归属过滤 → 命中 0 条 → `404 NOT_FOUND`。
    **刻意不是 403**：403 会泄露「该 doc_id 存在但不属于你」，而 404 与「这 id 根本
    不存在」不可区分，零额外代码且不泄露存在性（spec 决策 6）。
    """
    a_header = _token(1, "alice")
    a = _upload(kb_client, headers=a_header)
    assert a.status_code == 200, "A 上传本身应当成功"
    doc_id = _done_frame(a.text)["doc_id"]
    before = fake_vs._collection.count()
    assert before > 0, "上传后库里应当有向量"

    b = kb_client.delete(f"/api/kb/documents/{doc_id}", headers=_token(2, "bob"))

    assert b.status_code == 404, "P3 起删别人的文档 → 404"
    assert b.json()["code"] == "NOT_FOUND"
    assert fake_vs._collection.count() == before, "A 的向量必须一段都没少"

    # 对照组：A 自己删得掉。没有它，上面那条「404」也可能因为删除功能整体坏掉而假通过
    a2 = kb_client.delete(f"/api/kb/documents/{doc_id}", headers=a_header)
    assert a2.status_code == 200, "本人删除必须仍然成功"
    assert fake_vs._collection.count() == 0


# ============================ 3. /api/health 保持公开 ============================
def test_health_stays_public(fake_vs, monkeypatch):
    """探活不带 token 也必须能用：网关与容器健康检查不会带 token。"""
    from backend.app.agent import graph as graph_mod
    from backend.app.api.main import app

    # health_check 内部是 `from backend.app.agent.graph import get_vectorstore`
    # 的局部导入，所以换掉模块属性即可生效，避免探活去开真实的 data/chroma_db
    monkeypatch.setattr(graph_mod, "get_vectorstore", lambda: fake_vs)

    # 刻意不用 `with TestClient(app)`：那会触发 lifespan → init_db() 打真实 MySQL。
    # 探活不依赖 lifespan，而 Starlette 只在上下文管理器里才跑 lifespan。
    r = TestClient(app).get("/api/health")

    assert r.status_code == 200
    assert r.json()["status"] == "ok"
