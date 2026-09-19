# -*- coding: utf-8 -*-
"""P3 迁移脚本离线测试（临时 Chroma，零 API 额度）。

设计思想
--------
迁移脚本是本阶段**唯一不可逆**的动作（它直接改现有向量库）。三条最贵的错法各有一条
测试盯着：
1. 用 add_documents → 2885 段全量重嵌（真实额度成本）→ 用计数嵌入函数断言**零**调用；
2. update 整体替换 metadata 却只传 {"user_id": uid} → doc_id/filename 全丢，文档变孤儿；
3. 不幂等 → 重复跑出乱子。

运行方式（在 rag_qa_project 目录下）：
    python -m pytest tests/test_migrate_kb.py -v
"""
import sys
from pathlib import Path

import pytest
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from backend.tools import upload_function_tools as ft
from scripts.migrate_kb_user_id import migrate, collect_targets

USER_A = 7


@pytest.fixture
def vs(tmp_path, monkeypatch):
    store = Chroma(collection_name="migrate_test",
                   embedding_function=DeterministicFakeEmbedding(size=8),
                   persist_directory=str(tmp_path / "chroma"))
    monkeypatch.setattr(ft, "get_vectorstore", lambda: store)
    return store


def _seed_legacy(store, doc_id, *, filename, chunks=2,
                 uploaded_at="2026-09-01T00:00:00Z"):
    """造 P3 之前的「老」上传件：metadata **没有 user_id**。"""
    store.add_documents([
        Document(page_content=f"{filename} 第 {i} 段",
                 metadata={"doc_id": doc_id, "filename": filename, "origin": "upload",
                           "uploaded_at": uploaded_at, "title": filename,
                           "size_bytes": 100, "chunk": i})
        for i in range(chunks)])


def _seed_builtin(store, n=2):
    """预置官方文档：只有 kb 字段（迁移不该碰它们）。"""
    store.add_documents([
        Document(page_content=f"官方文档 {i}",
                 metadata={"kb": ft.BUILTIN_KB, "source": f"langchain/{i}.md"})
        for i in range(n)])


def _uploads(store) -> list[dict]:
    return [md for md in (store._collection.get(include=["metadatas"])["metadatas"] or [])
            if md.get("origin") == "upload"]


# ============================ 1. 补齐 + 保 metadata ============================
def test_migrate_backfills_user_id_and_preserves_other_metadata(vs):
    """★ 补上 user_id，且**其余 metadata 一个都不能丢**。

    update 是整体替换语义：只传 {"user_id": uid} 的话，doc_id/filename/origin/
    uploaded_at 会全被抹掉，文档立刻变成既列不出也删不掉的孤儿 —— 那比不迁移更糟。
    """
    _seed_legacy(vs, "old-1", filename="老笔记.md", chunks=3)
    _seed_builtin(vs, n=2)

    r = migrate(vs, USER_A)

    assert r == {"chunks": 3, "docs": 1, "user_id": USER_A, "written": 3}

    rows = _uploads(vs)
    assert len(rows) == 3
    assert all(md["user_id"] == USER_A for md in rows)
    # 关键：原有字段原封不动
    assert rows[0]["doc_id"] == "old-1"
    assert rows[0]["filename"] == "老笔记.md"
    assert rows[0]["origin"] == "upload"
    assert rows[0]["uploaded_at"] == "2026-09-01T00:00:00Z"
    assert {md["chunk"] for md in rows} == {0, 1, 2}


def test_migrate_does_not_touch_builtin_docs(vs):
    """预置文档没有 origin，不在迁移范围内 —— 别给它们加 user_id。"""
    _seed_builtin(vs, n=3)
    _seed_legacy(vs, "old-1", filename="老笔记.md", chunks=1)

    migrate(vs, USER_A)

    builtin = [md for md in (vs._collection.get(include=["metadatas"])["metadatas"] or [])
               if md.get("kb") == ft.BUILTIN_KB]
    assert len(builtin) == 3
    assert all("user_id" not in md for md in builtin)


def test_migrate_makes_docs_visible_to_owner_and_filter(vs):
    """迁移的**目的**：迁完这份文档才检索得到（这是「必须跑」的原因）。"""
    from backend.app.agent.graph import kb_filter

    _seed_legacy(vs, "old-1", filename="老笔记.md", chunks=2)
    # 迁移前：检索不到自己上传的（没有 user_id，两个子条件都不匹配）
    assert vs.similarity_search("老笔记", k=10, filter=kb_filter(USER_A)) == []

    migrate(vs, USER_A)

    hits = vs.similarity_search("老笔记", k=10, filter=kb_filter(USER_A))
    assert len(hits) == 2
    assert {h.metadata["doc_id"] for h in hits} == {"old-1"}


# ============================ 2. 幂等 ============================
def test_migrate_is_idempotent(vs):
    """★ 第二次跑应为 0 段待迁移，且不产生任何写入。"""
    _seed_legacy(vs, "old-1", filename="老笔记.md", chunks=3)

    first = migrate(vs, USER_A)
    second = migrate(vs, USER_A)

    assert first["written"] == 3
    assert second == {"chunks": 0, "docs": 0, "user_id": USER_A, "written": 0}
    assert len(_uploads(vs)) == 3, "重复跑不得改变向量总数"
    assert all(md["filename"] == "老笔记.md" for md in _uploads(vs))


def test_dry_run_writes_nothing(vs):
    """★ `--dry-run` 是关键安全阀：必须只统计、零写入。"""
    _seed_legacy(vs, "old-1", filename="老笔记.md", chunks=2)

    r = migrate(vs, USER_A, dry_run=True)

    assert r == {"chunks": 2, "docs": 1, "user_id": USER_A, "written": 0}
    assert all(md.get("user_id") is None for md in _uploads(vs)), "dry-run 不得写入"


def test_collect_targets_skips_already_migrated(vs):
    """collect_targets 只看「缺 user_id」的段，已迁过的直接跳过。"""
    _seed_legacy(vs, "old-1", filename="老笔记.md", chunks=2)
    migrate(vs, USER_A)

    assert collect_targets(vs) == []


# ============================ 3. 不重算向量（成本防线） ============================
def test_migrate_does_not_recompute_embeddings(tmp_path, monkeypatch):
    """★ 迁移期间嵌入调用次数必须为 **0**。

    这是本脚本最贵的一条约束：预置 2885 段 + 上传件若走 add_documents，
    等于把整库重嵌一遍（真实的 DashScope 额度消耗）。走 raw collection 的 update
    且不传 documents 时 Chroma 会跳过嵌入。

    ⚠ 不能用「迁移前后向量是否相同」来判断 —— DeterministicFakeEmbedding 是确定性的，
    重嵌也会得到一模一样的向量，那种断言是**空转**的。必须数调用次数。
    """
    calls: list[int] = []

    class CountingEmbeddings(DeterministicFakeEmbedding):
        def embed_documents(self, texts):
            calls.append(len(texts))
            return super().embed_documents(texts)

    store = Chroma(collection_name="mig_count",
                   embedding_function=CountingEmbeddings(size=8),
                   persist_directory=str(tmp_path / "chroma"))
    monkeypatch.setattr(ft, "get_vectorstore", lambda: store)

    _seed_legacy(store, "old-1", filename="老笔记.md", chunks=3)
    calls.clear()                      # 种子阶段的嵌入不计入

    migrate(store, USER_A)

    assert calls == [], f"迁移期间发生了嵌入调用 {calls}（应为 0 次，只改 metadata）"
