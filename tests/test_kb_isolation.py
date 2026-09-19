# -*- coding: utf-8 -*-
"""P3 知识库用户隔离离线测试（零 API 额度、临时 Chroma）。

设计思想
--------
沿用 test_kb_upload.py 的搭法（tmp_path 独立 Chroma + DeterministicFakeEmbedding），
但重心不同：那个文件守**契约**（SSE 时序 / 415 / 413 / 422 / 幂等），
本文件守**隔离** —— 「谁的文档谁能看见、能删、能被同名替换命中」这件事，
在 P2 之前完全没有测试覆盖，而其中的同名替换缺陷是**数据丢失级**的。

为什么用**真 Chroma** 而不是手写一个 where 求值器：隔离的成败恰恰取决于 Chroma 对
`$and` / `$or` / 等值条件的真实语义（比如 `$or` 至少要两个子条件，否则抛 ValueError）。
自己模拟一遍等于把被测逻辑重写一遍，测不出真问题。

运行方式（在 rag_qa_project 目录下）：
    python -m pytest tests/test_kb_isolation.py -v
"""
import sys
from pathlib import Path

import pytest

# 让 tests/ 能导入项目模块（等价于把 rag_qa_project 标记为 Sources Root）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

from config import settings
from backend.tools import upload_function_tools as ft
from backend.app.agent.graph import kb_filter

USER_A, USER_B = 1, 2


# ============================ 测试替身（Fakes） ============================
@pytest.fixture
def vs(tmp_path, monkeypatch):
    """每个测试一份独立 Chroma。upload_function_tools 全部读写都经 get_vectorstore()
    现取，所以换掉这个名字，整条链路就跑在假库上。

    刻意**不**抽到 conftest.py 与 test_kb_upload.py 共用：那个文件的 fake_vs 还
    monkeypatch 了 uploads_dir，本文件的用例不碰落盘原件，两边的夹具需求不同，
    强行合并会让任一边的改动波及另一边。
    """
    store = Chroma(collection_name="kb_iso_test",
                   embedding_function=DeterministicFakeEmbedding(size=8),
                   persist_directory=str(tmp_path / "chroma"))
    monkeypatch.setattr(ft, "get_vectorstore", lambda: store)
    return store


def _seed_upload(store, doc_id, *, filename, user_id, chunks=2,
                 uploaded_at="2026-09-19T00:00:00Z"):
    """造一份「某人上传的文档」：一个 doc_id 对应 chunks 段向量（metadata 与真实上传一致）。"""
    store.add_documents([
        Document(page_content=f"{filename} 第 {i} 段",
                 metadata={"doc_id": doc_id, "filename": filename, "origin": "upload",
                           "user_id": user_id, "uploaded_at": uploaded_at,
                           "title": filename, "size_bytes": 100,
                           "source": f"uploads/{doc_id}__{filename}", "chunk": i})
        for i in range(chunks)])


def _seed_builtin(store, n=3):
    """造预置官方文档：**只有 kb 字段**，没有 origin、没有 user_id（spec §5.1 事实表）。"""
    store.add_documents([
        Document(page_content=f"LangGraph 官方文档第 {i} 篇，讲 checkpointer 与持久化。",
                 metadata={"kb": ft.BUILTIN_KB, "title": f"official{i}",
                           "source": f"langchain/{i}.md"})
        for i in range(n)])


# ============================ 1. 同名替换：数据丢失级回归 ============================
def test_find_upload_doc_ids_only_matches_own(vs):
    """★ P3 §1 问题 2 的回归测试：A 的 `笔记.md` 不能被 B 的同名上传命中。

    修复前 find_upload_doc_ids 只按 filename + origin 找旧件，于是 A 再传一次
    `笔记.md` 时会把 **B 的文档向量与落盘原件一起删掉**（静默数据丢失）。
    """
    _seed_upload(vs, "doc-a", filename="笔记.md", user_id=USER_A)
    _seed_upload(vs, "doc-b", filename="笔记.md", user_id=USER_B)

    assert ft.find_upload_doc_ids("笔记.md", USER_A) == ["doc-a"]
    assert ft.find_upload_doc_ids("笔记.md", USER_B) == ["doc-b"]
    # 关键：两个 doc_id 都在，谁都没被对方的同名文件带走
    assert len(ft.list_upload_docs(USER_A)) == 1
    assert len(ft.list_upload_docs(USER_B)) == 1


def test_find_upload_doc_ids_empty_for_unrelated_user(vs):
    """没有任何上传件的第三个用户 → 空列表（而不是匹配到别人的）。"""
    _seed_upload(vs, "doc-a", filename="笔记.md", user_id=USER_A)

    assert ft.find_upload_doc_ids("笔记.md", 999) == []


# ============================ 2. 列表 / 元信息 ============================
def test_list_docs_only_own(vs):
    """列表只含本人的上传件；预置文档不进列表。"""
    _seed_upload(vs, "doc-a", filename="A.md", user_id=USER_A)
    _seed_upload(vs, "doc-b", filename="B.md", user_id=USER_B)
    _seed_builtin(vs)

    assert [d["doc_id"] for d in ft.list_upload_docs(USER_A)] == ["doc-a"]
    assert [d["doc_id"] for d in ft.list_upload_docs(USER_B)] == ["doc-b"]


def test_list_docs_groups_chunks_into_one_entry(vs):
    """一个 doc_id 的 N 段向量只算一条文档，chunks 计数正确。

    这条同时守着「$and 两条件」这个改动没有把分组搞坏（旧实现对 origin 单条件分组）。
    """
    _seed_upload(vs, "doc-a", filename="A.md", user_id=USER_A, chunks=5)

    docs = ft.list_upload_docs(USER_A)

    assert len(docs) == 1
    assert docs[0]["chunks"] == 5


def test_get_upload_doc_other_user_returns_none(vs):
    """取别人文档的元信息 → None（上层据此返回 404，不泄露存在性）。"""
    _seed_upload(vs, "doc-b", filename="B.md", user_id=USER_B)

    assert ft.get_upload_doc("doc-b", USER_B) is not None
    assert ft.get_upload_doc("doc-b", USER_A) is None


# ============================ 3. 删除 ============================
def test_delete_other_users_doc_deletes_nothing(vs):
    """★ 删别人的文档命中 0 条 → 上层 404（**不是 403**，不泄露存在性）。"""
    _seed_upload(vs, "doc-b", filename="B.md", user_id=USER_B, chunks=3)

    gone = ft.delete_upload_doc("doc-b", USER_A)

    assert gone == 0
    assert len(ft.list_upload_docs(USER_B)) == 1        # B 的文档毫发无损
    assert ft.get_upload_doc("doc-b", USER_B) is not None


def test_delete_own_doc_removes_all_chunks(vs):
    """本人删除 → 该 doc_id 的全部向量块都没了。"""
    _seed_upload(vs, "doc-a", filename="A.md", user_id=USER_A, chunks=3)

    gone = ft.delete_upload_doc("doc-a", USER_A)

    assert gone == 3
    assert ft.list_upload_docs(USER_A) == []


def test_delete_builtin_is_impossible(vs):
    """预置文档（无 origin）在任何 user_id 下都删不掉 —— 老防线不能被 P3 改坏。"""
    _seed_builtin(vs)

    for uid in (USER_A, USER_B):
        assert ft.delete_upload_doc("official0", uid) == 0


# ============================ 4. 预置文档统计是全局的 ============================
def test_builtin_stats_is_global(vs):
    """builtin 摘要是全局统计，不随调用方变化（spec §6「不改的」第 1 条）。"""
    _seed_builtin(vs, n=4)
    _seed_upload(vs, "doc-a", filename="A.md", user_id=USER_A)

    assert ft.builtin_stats() == {"docs": 4, "chunks": 4}


# ============================ 5. 检索隔离（端到端，走真 Chroma 的 where） ============================
def test_similarity_search_filter_isolates_users(vs):
    """★ 检索隔离的端到端验证：A 只拿得到「预置 + 自己」，拿不到 B 的。

    这条直接对真 Chroma 用 kb_filter 跑 similarity_search，验证的是**真实 where 语义**，
    而不是我们自己对过滤条件的理解 —— 88 篇官方文档会不会被 $or 意外滤掉，只有这里测得到。
    """
    _seed_builtin(vs, n=3)
    _seed_upload(vs, "doc-a", filename="A.md", user_id=USER_A)
    _seed_upload(vs, "doc-b", filename="B.md", user_id=USER_B)

    def titles(uid) -> set[str]:
        """命中的标题集合。用集合而非列表：similarity_search 返回的是**向量块**，
        一份文档有 N 段就出现 N 次（k=20 足够装下本例的 5 段）。
        """
        hits = vs.similarity_search("checkpointer 持久化", k=20, filter=kb_filter(uid))
        return {h.metadata.get("title") or h.metadata.get("filename") for h in hits}

    a, b = titles(USER_A), titles(USER_B)

    # ★ 隔离的核心断言：自己的 + 预置都在，对方的**一个都不在**
    assert a == {"A.md", "official0", "official1", "official2"}
    assert b == {"B.md", "official0", "official1", "official2"}
    assert "B.md" not in a
    assert "A.md" not in b
    # 无 user_id（CLI）只见预置 —— 且必须**真的**见到 3 篇，而不是被 $or 单条件抛错
    assert titles(None) == {"official0", "official1", "official2"}
