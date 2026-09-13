# -*- coding: utf-8 -*-
"""知识库上传链路离线测试（零 API 额度、不碰真实 Chroma 库）。

设计思想
--------
沿用 test_graph.py 的路子：用 DeterministicFakeEmbedding 替换 DashScope、
用 tmp_path 里的独立 Chroma 替换 data/chroma_db、monkeypatch settings.uploads_dir，
全程不触网、不耗额度，秒级验证契约（docs/api/README.md「知识库端点」）的三类行为：

1. 原语单元  —— embed_batch 的 metadata 落地、doc_title、落盘文件名消毒、回滚；
2. 端点契约  —— 415/413/422 走 HTTP 状态码，SSE 事件时序，GET 列表，DELETE 幂等；
3. 安全边界  —— 预置文档（无 origin 字段）删不掉也列不出，../ 穿不出项目目录。

运行方式（在 rag_qa_project 目录下）：
    python -m pytest tests/test_kb_upload.py -v
"""
import json
import math
import sys
from pathlib import Path

import pytest

# 让 tests/ 能导入项目模块（等价于把 rag_qa_project 标记为 Sources Root）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from config import settings
from backend.app import function_tools as ft
from backend.app.api import kb_router
from backend.app.services import upload_service


# ============================ 测试替身（Fakes） ============================
@pytest.fixture
def fake_vs(tmp_path, monkeypatch):
    """每个测试一份独立 Chroma（tmp_path 隔离），嵌入用假模型，原件落到 tmp_path。

    function_tools 里所有读写都通过 get_vectorstore() 现取，
    所以只要把这个名字换掉，整条链路就都跑在假库上。
    """
    vs = Chroma(collection_name="kb_test",
                embedding_function=DeterministicFakeEmbedding(size=8),
                persist_directory=str(tmp_path / "chroma"))
    monkeypatch.setattr(ft, "get_vectorstore", lambda: vs)
    monkeypatch.setattr(settings, "uploads_dir", tmp_path / "uploads")
    return vs


@pytest.fixture
def client(fake_vs):
    app = FastAPI()
    app.include_router(kb_router.router)
    return TestClient(app)


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """SSE 文本 -> [(event, data)]。按空行切帧，与前端 client.ts 的解析口径一致。"""
    frames = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        frames.append((event, data))
    return frames


def _big_markdown(blocks: int = 60) -> bytes:
    """造一份能被切成多段的 md（>EMBED_BATCH 段才会出现多批 embedding 进度帧）。"""
    text = "\n\n".join(f"## 小节 {i}\n" + "这是用于测试切分与嵌入的中文内容。" * 12
                       for i in range(blocks))
    return ("# 测试文档标题\n\n" + text).encode("utf-8")


def _upload(client, filename: str, raw: bytes):
    return client.post("/api/kb/documents",
                       files={"file": (filename, raw, "application/octet-stream")})


# ============================ 1. 原语单元 ============================
def test_embed_batch_writes_contract_metadata(fake_vs):
    """embed_batch 必须把纯文本包成 Document 并带齐契约要求的 metadata。

    直接喂 list[str] 给 add_documents 会抛 AttributeError（LangChain 要读 doc.id），
    这也是这组测试要守住的第一条回归线。
    """
    meta = {"doc_id": "d1", "filename": "a.md", "origin": "upload",
            "uploaded_at": "2026-09-13T00:00:00Z", "title": "A",
            "source": "uploads/d1__a.md", "size_bytes": 10}

    assert ft.embed_batch(["第一段", "第二段"], meta, start=0) == 2
    assert fake_vs._collection.count() == 2

    got = fake_vs._collection.get(where={"origin": "upload"}, include=["metadatas"])
    by_chunk = {int(m["chunk"]): m for m in got["metadatas"]}
    assert sorted(by_chunk) == [0, 1]                     # chunk 编号从 start 续上
    assert by_chunk[0]["doc_id"] == "d1"
    assert by_chunk[1]["filename"] == "a.md"
    # 第二批的编号要接着第一批，不能从 0 重来
    ft.embed_batch(["第三段"], meta, start=2)
    got = fake_vs._collection.get(where={"origin": "upload"}, include=["metadatas"])
    assert sorted(m["chunk"] for m in got["metadatas"]) == [0, 1, 2]


def test_doc_title_prefers_markdown_heading():
    """md 取首个 `# 标题`；txt/pdf 回退为去扩展名的文件名。"""
    assert ft.doc_title("notes.md", ["前言没有标题", "# 真正的标题\n正文"]) == "真正的标题"
    assert ft.doc_title("notes.md", ["整篇都没有标题"]) == "notes"
    assert ft.doc_title("report.pdf", ["# 不算标题"]) == "report"


def test_safe_disk_name_blocks_path_traversal(tmp_path, monkeypatch):
    """客户端文件名里的 ../ 与 Windows 非法字符必须被消毒，否则能写到项目外。"""
    monkeypatch.setattr(settings, "uploads_dir", tmp_path)
    path = ft.save_upload_copy("doc1", "../../../etc/passwd.md", b"x")
    assert path.parent == tmp_path                        # 没跑出 uploads 目录
    assert path.name == "doc1__passwd.md"

    path2 = ft.save_upload_copy("doc2", 'a<b>c:d|e?f*g.md', b"y")
    assert not any(c in path2.name for c in '<>:"|?*')

    ft.remove_upload_copies("doc1")
    assert not path.exists()
    assert path2.exists()                                 # 只删自己的，不误伤别的 doc


def test_rollback_clears_vectors_and_local_copy(fake_vs, tmp_path):
    """回滚要把向量和落盘原件一起清掉，保证「要么全成，要么库里干干净净」。"""
    meta = {"doc_id": "d9", "filename": "x.md", "origin": "upload",
            "uploaded_at": "2026-09-13T00:00:00Z"}
    ft.embed_batch(["a", "b", "c"], meta)
    ft.save_upload_copy("d9", "x.md", b"raw")

    assert upload_service._rollback("d9") == 3
    assert fake_vs._collection.count() == 0
    assert not list(settings.uploads_dir.glob("d9__*"))


def test_builtin_docs_are_invisible_and_undeletable(fake_vs):
    """预置文档没有 origin 字段：列表里看不到、按 doc_id 也删不掉（天然 403）。"""
    fake_vs.add_documents([
        Document(page_content="官方文档片段",
                 metadata={"title": "官方", "source": "langchain/x.md", "kb": ft.BUILTIN_KB}),
    ])
    assert ft.list_upload_docs() == []
    assert ft.builtin_stats() == {"docs": 1, "chunks": 1}
    assert ft.delete_upload_doc("whatever") == 0
    assert fake_vs._collection.count() == 1               # 官方文档没被误删


# ============================ 2. 端点契约 ============================
def test_pre_stream_validation_uses_http_status(client):
    """流开始前的错误必须是 HTTP 状态码 + {"code","message"}，不能降级成 SSE error。"""
    r = _upload(client, "virus.exe", b"MZ...")
    assert r.status_code == 415
    assert r.json()["code"] == "UNSUPPORTED_FILE_TYPE"

    r = _upload(client, "big.md", b"x" * (upload_service.MAX_BYTES + 1))
    assert r.status_code == 413
    assert r.json()["code"] == "FILE_TOO_LARGE"

    r = _upload(client, "empty.md", b"")
    assert r.status_code == 422
    assert r.json()["code"] == "VALIDATION_ERROR"
    assert set(r.json()) == {"code", "message"}


def test_upload_sse_event_sequence_and_done_payload(client, fake_vs):
    """成功时序：saved -> parsed -> split -> embedding × N -> done，且字段齐全。"""
    raw = _big_markdown()
    r = _upload(client, "notes.md", raw)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["x-accel-buffering"] == "no"         # 不关代理缓冲，帧会被憋住

    frames = _parse_sse(r.text)
    stages = [d["stage"] for e, d in frames if e == "progress"]
    assert stages[0] == "saved" and stages[1] == "parsed" and stages[2] == "split"
    assert set(stages[3:]) == {"embedding"}

    total = next(d["total"] for e, d in frames if e == "progress" and d["stage"] == "split")
    assert total > ft.EMBED_BATCH                         # 样本足够大，确实分了多批
    emb = [d for e, d in frames if e == "progress" and d["stage"] == "embedding"]
    assert len(emb) == math.ceil(total / ft.EMBED_BATCH)
    # current 是**累计已嵌入段数**，不是批次起点；最后一帧必须等于 total
    assert [d["current"] for d in emb] == sorted(d["current"] for d in emb)
    assert emb[-1]["current"] == total

    event, done = frames[-1]
    assert event == "done"
    assert set(done) == {"doc_id", "filename", "chunks", "replaced", "uploaded_at"}
    assert done["filename"] == "notes.md"
    assert done["chunks"] == total
    assert done["replaced"] is False
    assert done["uploaded_at"].endswith("Z")
    assert fake_vs._collection.count() == total
    # 原件必须落盘：向量库是可重建的派生数据，重建时要靠它把用户文档捞回来
    assert list(settings.uploads_dir.glob(f"{done['doc_id']}__notes.md"))


def test_upload_empty_document_emits_sse_error(client, fake_vs):
    """解析后无有效文本 -> SSE error(EMPTY_DOCUMENT)，且不留半截数据。"""
    r = _upload(client, "blank.md", b"   \n\t  ")
    frames = _parse_sse(r.text)
    assert frames[-1][0] == "error"
    assert frames[-1][1]["code"] == "EMPTY_DOCUMENT"
    assert not any(e == "done" for e, _ in frames)        # 出错后不再有 done
    assert fake_vs._collection.count() == 0
    assert list(settings.uploads_dir.glob("*")) == []     # 原件也回滚了


def test_upload_parse_error_emits_sse_error(client, fake_vs, monkeypatch):
    """解析阶段抛非 ValueError（PDF 加密/损坏）-> PARSE_ERROR，不是笼统的 INTERNAL_ERROR。"""
    def boom(raw, filename):
        raise RuntimeError("encrypted pdf")
    monkeypatch.setattr(ft, "parse_and_split", boom)
    monkeypatch.setattr(upload_service, "parse_and_split", boom)

    frames = _parse_sse(_upload(client, "locked.pdf", b"%PDF-1.7 broken").text)
    assert frames[-1][0] == "error"
    assert frames[-1][1]["code"] == "PARSE_ERROR"
    assert "encrypted pdf" in frames[-1][1]["message"]
    assert fake_vs._collection.count() == 0


def test_embedding_error_rolls_back_partial_vectors(client, fake_vs, monkeypatch):
    """嵌到一半失败 -> EMBEDDING_ERROR + 回滚，库里不能留半截向量污染检索。"""
    calls = {"n": 0}
    real = ft.embed_batch

    def flaky(chunks, meta, start=0):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("DashScope quota exceeded")
        return real(chunks, meta, start)

    monkeypatch.setattr(upload_service, "embed_batch", flaky)

    frames = _parse_sse(_upload(client, "notes.md", _big_markdown()).text)
    assert frames[-1][0] == "error"
    assert frames[-1][1]["code"] == "EMBEDDING_ERROR"
    assert fake_vs._collection.count() == 0               # 第一批写进去的也被清掉了
    assert client.get("/api/kb/documents").json()["documents"] == []


def test_same_filename_replaces_old_doc(client, fake_vs):
    """同名视为替换（契约 §4.2）：先删旧 doc_id 的向量与原件，done.replaced=true。"""
    first = _parse_sse(_upload(client, "notes.md", _big_markdown(30)).text)[-1][1]
    second = _parse_sse(_upload(client, "notes.md", _big_markdown(60)).text)[-1][1]

    assert first["replaced"] is False
    assert second["replaced"] is True
    assert second["doc_id"] != first["doc_id"]

    docs = client.get("/api/kb/documents").json()["documents"]
    assert [d["doc_id"] for d in docs] == [second["doc_id"]]   # 旧 doc 彻底消失
    assert fake_vs._collection.count() == second["chunks"]
    assert not list(settings.uploads_dir.glob(f"{first['doc_id']}__*"))


def test_list_documents_sorted_desc_with_builtin_summary(client, fake_vs):
    """GET 只返回 origin=upload，按 uploaded_at 倒序；builtin 摘要统计不到就整体省略。"""
    assert client.get("/api/kb/documents").json() == {"documents": []}   # 空库 200 不 404

    _upload(client, "older.md", _big_markdown(20))
    _upload(client, "newer.md", _big_markdown(40))

    payload = client.get("/api/kb/documents").json()
    assert "builtin" not in payload                        # 库里没有 kb=langchain_docs 的块
    assert {d["filename"] for d in payload["documents"]} == {"older.md", "newer.md"}
    stamps = [d["uploaded_at"] for d in payload["documents"]]
    assert stamps == sorted(stamps, reverse=True)
    for d in payload["documents"]:
        assert {"doc_id", "filename", "chunks", "uploaded_at"} <= set(d)
        assert d["chunks"] >= 1


def test_delete_document_is_idempotent_404(client, fake_vs):
    """DELETE 返回 deleted_chunks；重复删同一 doc_id 第二次是 404 而不是 500。"""
    done = _parse_sse(_upload(client, "notes.md", _big_markdown()).text)[-1][1]
    doc_id = done["doc_id"]

    r = client.delete(f"/api/kb/documents/{doc_id}")
    assert r.status_code == 200
    assert r.json() == {"doc_id": doc_id, "filename": "notes.md",
                        "deleted_chunks": done["chunks"]}
    assert fake_vs._collection.count() == 0
    assert not list(settings.uploads_dir.glob(f"{doc_id}__*"))

    r2 = client.delete(f"/api/kb/documents/{doc_id}")
    assert r2.status_code == 404
    assert r2.json() == {"code": "NOT_FOUND", "message": f"文档不存在: {doc_id}"}


# ============================ 3. 装配自检 ============================
def test_app_registers_kb_routes():
    """整条 import 链（backend.app.api.main）能通，且三个 kb 端点都挂上了。

    这条守住的是「多根混用」回归：function_tools / upload_service / kb_router
    必须统一用 backend.app.* 绝对导入，写成裸 services.* / agent.* 会直接 ImportError。
    """
    from backend.app.api.main import app as real_app

    paths = {p: set(m.lower() for m in ops)
             for p, ops in real_app.openapi()["paths"].items()}
    assert {"post", "get", "delete"} <= (paths.get("/api/kb/documents", set())
                                         | paths.get("/api/kb/documents/{doc_id}", set()))
    assert "post" in paths["/api/kb/documents"]
    assert "get" in paths["/api/kb/documents"]
    assert "delete" in paths["/api/kb/documents/{doc_id}"]
