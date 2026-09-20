# -*- coding: utf-8 -*-
"""评估集与语料的一致性校验（零 API 额度，不需要向量库）。

守的是这一类**静默退化**：标注指向的文档已经被删掉。
删文档（例如 P3 迁移清理「只有向量、无落盘原件」的孤儿）既不报错也不变红，
那条 query 只是**永远不可能命中**，于是静默把 recall 拉低几个百分点。

upload_zh_06 就是这么烂掉的（2026-09-20 本校验上线当天即抓到）：期望件 `可重入锁.md`
被 P3 迁移删了（7 段，与迁移记录「820/21 → 813/20」逐段吻合，备份留在
data/orphan-backup-5230cdcc.json），而标注一直留在评估集里 —— 让人误以为
「极小文档召回不行」，其实是文档没了。按校验给出的结论，该条已从评估集移除。

注意本文件**不**校验「真实评估集的标注是否都在语料里」—— 那需要向量库，所以由
eval_retrieval.py 在加载语料后运行时校验（missing_expected）。这里只管数据本身是否规范。

运行：python -m pytest tests/test_eval_retrieval.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.documents import Document  # noqa: E402

from eval_retrieval import missing_expected  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _doc(source: str) -> Document:
    return Document(page_content="x", metadata={"source": source})


def _q(qid: str, *expected: str) -> dict:
    return {"id": qid, "expected_sources": list(expected)}


# ============================ 存在的情形（不该误报） ============================
def test_preset_source_exact_path_ok():
    docs = [_doc("langgraph/checkpointers.md")]
    assert missing_expected([_q("a", "langgraph/checkpointers.md")], docs) == []


def test_preset_source_tail_only_ok():
    # is_hit 对预置件用的是 src.endswith("/" + exp)，所以标注只写末段也算命中
    docs = [_doc("langgraph/checkpointers.md")]
    assert missing_expected([_q("a", "checkpointers.md")], docs) == []


def test_upload_source_filename_ok():
    # 上传件的 source 形如 uploads/<doc_id>__<文件名>，而标注只写文件名
    docs = [_doc("uploads/5230cdcc-510b-43a4-8034-bf5886f1e348__可重入锁.md")]
    assert missing_expected([_q("a", "可重入锁.md")], docs) == []


def test_empty_expected_is_ignored():
    assert missing_expected([_q("a")], [_doc("langgraph/checkpointers.md")]) == []


def test_doc_without_source_metadata_does_not_crash():
    docs = [Document(page_content="x", metadata={})]
    assert missing_expected([_q("a", "任意.md")], docs) == [("a", "任意.md")]


# ============================ 缺失的情形（必须报出） ============================
def test_missing_source_is_reported():
    docs = [_doc("langgraph/checkpointers.md")]
    assert missing_expected([_q("x_01", "已被删掉的.md")], docs) == [("x_01", "已被删掉的.md")]


def test_partial_expected_still_reports_the_missing_one():
    # 一条 query 标了两个源、只有一个还在 → 仍要报（缺一个就注定 MISS，
    # 只是 MISS 会被误读成「检索不行」而不是「标注烂了」）
    docs = [_doc("langgraph/interrupts.md")]
    got = missing_expected(
        [_q("s_03", "langchain/human-in-the-loop.md", "langgraph/interrupts.md")], docs)
    assert got == [("s_03", "langchain/human-in-the-loop.md")]


# ============================ 校验不误伤：真实评估集的上传件标注 ============================
def test_real_eval_set_upload_annotations_are_not_false_positives():
    """用真实评估集 + 一份由「标注里的上传件名字」自造出来的语料。

    语料不含预置件，所以预置标注会全部报缺失（合成语料的必然结果，不是误报）。
    真正要钉的是：**所有上传件标注都不该被报出** —— 即这个校验不会误伤正常标注。
    （upload_zh_06 曾在这一步被报出，正是它该被报出的证明；该条已于 2026-09-20 移除。）
    """
    queries = json.loads(
        (ROOT / "data" / "eval_queries.json").read_text(encoding="utf-8"))["queries"]

    upload_names = {e for q in queries for e in q["expected_sources"] if "/" not in e}
    docs = [_doc(f"uploads/fake-{i}__{n}")
            for i, n in enumerate(sorted(upload_names))]

    got = missing_expected(queries, docs)

    assert [pair for pair in got if "/" not in pair[1]] == []


# ============================ 真实评估集的结构不变式 ============================
def test_real_eval_set_integrity():
    """数据本身是否规范（不需要向量库）。

    ⚠ 刻意**不**断言「三类各 8 条」：移除 upload_zh_06 后三类是 8/8/7，
    而 id 空位是故意保留的（避免历史报表里的 id 指向漂移）。
    """
    queries = json.loads(
        (ROOT / "data" / "eval_queries.json").read_text(encoding="utf-8"))["queries"]

    ids = [q["id"] for q in queries]
    assert len(ids) == len(set(ids)), "id 必须唯一"
    assert all(q["expected_sources"] for q in queries), "每条都要有标注"
    assert {q["type"] for q in queries} == {"term_en", "semantic_zh", "upload_zh"}
    for q in queries:
        assert q["id"].startswith(q["type"]), f"{q['id']} 与 type={q['type']} 不一致"
        for e in q["expected_sources"]:
            assert e.endswith((".md", ".pdf")), f"{q['id']} 的标注不像是文档标识：{e}"
