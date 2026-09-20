# -*- coding: utf-8 -*-
"""检索离线评估：dense / bm25 / hybrid(RRF 多权重) / hybrid+云端精排 对照。

**为什么要这个脚本**：混合检索 + 精排会带来实打实的代价——BM25 索引要常驻内存、
上传新文档后要失效重建、精排的网络往返会把延迟直接加在 SSE 首 token 之前。
上不上，应该由数据决定，而不是由"看起来更高级"决定。

跑法（在 rag_qa_project 目录，一律用 uv run，裸 python 会命中系统解释器）：
    uv run python eval_retrieval.py                    # 单组权重 0.6,0.4 + 精排
    uv run python eval_retrieval.py --sweep            # 扫六组权重，再按最优权重精排
    uv run python eval_retrieval.py --quick            # 只跑前 3 条（冒烟，验证能跑通）
    uv run python eval_retrieval.py --no-rerank        # 跳过精排（零 rerank API 调用）
    uv run python eval_retrieval.py --detail           # 打印每条 query 的各配置 top-5
    uv run python eval_retrieval.py --weights 0.7,0.3;0.8,0.2 --rerank-weight 0.7,0.3
    uv run python eval_retrieval.py --k 20 --top-n 5 --rerank-timeout 30

产出：
    stdout                  权重扫描对照表 + 总体/分类别指标 + 配置间分歧 + 冷启动开销
    data/eval_results.json  机器可读结果，便于调参后前后对比

精排走 DashScope 原生 text-rerank（模型名取自 config.settings.rerank_model），
延迟口径仍是"累计"：dense + bm25 + 融合 + 精排 RTT，即线上首 token 前的真实等待。

标注口径见 data/eval_queries.json 的 _comment：文档级命中，top-k 里任一段来自
expected_sources 之一即算命中。
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
import warnings
from collections import defaultdict
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Sequence, TypedDict

# jieba 在 Python 3.14 下会刷 3 条 invalid escape sequence 的 SyntaxWarning（无害但吵）
warnings.filterwarnings("ignore", category=SyntaxWarning)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# ⚠ 必须先导入 config：它负责 load_dotenv（DashScope key）与 setdefault HF_ENDPOINT，
#   而 HF_ENDPOINT 晚于 huggingface_hub 导入就失效了（config.py:20-21 有说明）。
from config import settings  # noqa: E402
from langchain_core.callbacks.base import Callbacks  # noqa: E402
from langchain_core.documents import BaseDocumentCompressor, Document  # noqa: E402

import jieba  # noqa: E402

# 与 langchain_classic.retrievers.EnsembleRetriever.c 的默认值一致（ensemble.py:70）
RRF_C = 60
ASCII_TOKEN = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_.\-]*)")
# 第一轮固定出现的两个单路配置；hybrid 配置按权重动态命名（单组叫 hybrid，多组叫 hybrid_w0.7）
BASE_CONFIGS = ["dense", "bm25"]
# --sweep 默认扫描的 dense/sparse 权重。1.0,0.0 是天然自检点：该权重下 BM25 独有文档
# 得分为 0 必然沉底，指标本应等于纯 dense。若不等，通常**不是**融合 bug，而是 dense 结果里
# 存在内容重复的 chunk —— RRF 对重复内容累加计分（官方 ensemble.py:325 明示
# "Duplicated contents ... scored cumulatively"），重复 chunk 会被顶到前面。
# main() 末尾会统计这种情况的 query 占比，用来区分"实现错了"和"语料脏了"。
SWEEP_WEIGHTS: list[tuple[float, float]] = [
    (0.5, 0.5), (0.6, 0.4), (0.7, 0.3), (0.8, 0.2), (0.9, 0.1), (1.0, 0.0),
]
# 精排 HTTP 超时：SDK 默认 300s，单次网络挂起会把整轮评估拖死 5 分钟
RERANK_TIMEOUT_S = 30


# ---------------------------------------------------------------- 分词
def tokenize(text: str) -> list[str]:
    """BM25 预处理：ASCII 标识符整体保留，CJK 段交给 jieba。

    实测 jieba.lcut("check_same_thread") → ['check', '_', 'same', '_', 'thread']，
    下划线变成独立 token。语料里全是这种 API 名（add_messages / stream_mode /
    check_same_thread），切碎后 '_' 成了高 DF 噪声，精度会掉，所以自己切。
    """
    out: list[str] = []
    for seg in ASCII_TOKEN.split(text):
        if not seg:
            continue
        if re.match(r"[A-Za-z0-9_]", seg):
            out.append(seg.lower())
        else:
            out.extend(w for w in jieba.lcut(seg) if w.strip())
    return out


# ---------------------------------------------------------------- 语料与各检索器
@lru_cache(maxsize=1)
def load_corpus() -> tuple[list[Document], dict]:
    """一次性取出全部 chunk。**走 get_vectorstore()**，保证评估的是线上同一个索引。"""
    from backend.app.agent.graph import get_vectorstore

    got = get_vectorstore()._collection.get(include=["documents", "metadatas"])
    docs = [Document(page_content=t, metadata=m)
            for t, m in zip(got["documents"] or [], got["metadatas"] or [])]
    stats = {
        "chunks": len(docs),
        "preset": sum(1 for d in docs if d.metadata.get("kb") == "langchain_docs"),
        "upload": sum(1 for d in docs if d.metadata.get("origin") == "upload"),
        "chars": sum(len(d.page_content) for d in docs),
    }
    return docs, stats


@lru_cache(maxsize=4)
def get_bm25(k: int):
    """BM25 索引单例。3705 段全量建索引，首次几秒、常驻几十 MB——这本身就是
    混合检索的成本之一，所以冷启动耗时单独统计并打印。"""
    from langchain_community.retrievers import BM25Retriever

    docs, _ = load_corpus()
    return BM25Retriever.from_documents(docs, k=k, preprocess_func=tokenize)


# ---------------------------------------------------------------- 云端精排（DashScope）
class DashScopeReranker(BaseDocumentCompressor):
    """DashScope 原生 text-rerank 的轻量封装。

    不用 langchain_community.document_compressors.DashScopeRerank，因为它有两处硬伤：
    ① validate_environment 在未传 client 时会**无条件把 model 覆盖成 gte-rerank**，
       用户指定的模型名被丢弃；传 client 绕过又会同时跳过 api_key 读取；
    ② 会引入 langchain-community 的 SunsetWarning。

    对外契约与原来的 CrossEncoderReranker 完全一致（compress_documents），
    所以调用点与报表层零改动。字段必须以 pydantic 字段声明（基类是 BaseModel）。
    """

    model_name: str = "qwen3.7-text-rerank"
    top_n: int = 5
    api_key: str = ""
    request_timeout: int = RERANK_TIMEOUT_S

    def compress_documents(self, documents: Sequence[Document], query: str,
                           callbacks: Callbacks | None = None) -> Sequence[Document]:
        """按 query 重排 documents，返回 top_n 条并在 metadata 写入 relevance_score。"""
        if not documents:                    # 空列表直接返回，避免无意义的 API 调用
            return []
        import dashscope

        # 空内容以 " " 占位：SDK 返回的 index 与**入参下标**一一对应，
        # 若为省事过滤掉空文档，index 会整体错位、精排结果映射到别的文档上。
        texts = [(d.page_content or "").strip() or " " for d in documents]
        resp = dashscope.TextReRank.call(
            model=self.model_name,
            query=query,
            documents=texts,
            top_n=self.top_n,
            return_documents=False,
            api_key=self.api_key,
            request_timeout=self.request_timeout,
        )
        # 失败时 output 为 None，不校验会抛出难懂的 AttributeError
        if resp.status_code != 200:
            raise RuntimeError(f"DashScope rerank 失败 [{resp.status_code}] "
                               f"{resp.code}: {resp.message}")
        out: list[Document] = []
        for res in resp.output.results:
            src = documents[res.index]
            doc = Document(src.page_content, metadata=deepcopy(src.metadata))
            doc.metadata["relevance_score"] = res.relevance_score
            out.append(doc)
        return out


@lru_cache(maxsize=4)
def get_reranker(top_n: int, timeout: int = RERANK_TIMEOUT_S) -> DashScopeReranker:
    """精排单例（lru_cache 按 (top_n, timeout) 缓存；脚本里只用一组参数）。"""
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY：.env 未加载或 key 未配置")
    return DashScopeReranker(model_name=settings.rerank_model, top_n=top_n,
                             api_key=api_key, request_timeout=timeout)


def probe_reranker(top_n: int, timeout: int = RERANK_TIMEOUT_S) -> float:
    """精排冒烟探活：一次极小的真实调用，把「模型名错 / 密钥错 / 网络不通」暴露在
    正式评估之前，返回探活耗时（秒）。

    qwen3.7-text-rerank **不在** SDK 的 TextReRank.Models 常量表里（该表只有
    gte-rerank / gte-rerank-v2 / qwen3-rerank / qwen3-vl-rerank），可用性必须实测；
    否则 24 条 query 会各自抛错，整轮评估作废且日志冗长。
    **不做静默降级**：降级会产出一行无意义的 hybrid_rerank 数据，违背本脚本的评估纪律。
    """
    docs = [Document("重置数据库连接池的正确写法"), Document("如何配置反向代理的端口")]
    t = time.perf_counter()
    try:
        ranked = get_reranker(top_n, timeout).compress_documents(docs, "怎么重置连接池")
        # 只验"不抛异常"不够：模型能调通但返回空 results 时，会到正式评估才以另一种
        # traceback 爆出来。这里断言真拿到了排序项，把问题拦在探活阶段。
        if not ranked:
            raise RuntimeError("返回空结果：模型可调用但未返回任何排序项，检查返回体结构")
    except Exception as exc:  # noqa: BLE001 — 探活阶段把所有异常翻译成排查指引
        raise RuntimeError(
            f"精排探活失败（模型 {settings.rerank_model}）：{type(exc).__name__}: {exc}\n"
            f"    ① 模型名是否可用：SDK 常量表里没有该名字，需确认拼写与开通状态\n"
            f"    ② DASHSCOPE_API_KEY 是否有效且具备 text-rerank 调用权限\n"
            f"    ③ 网络能否访问 dashscope.aliyuncs.com（原生端点，不是 compatible-mode）"
        ) from exc
    return time.perf_counter() - t


def dense_search(query: str, k: int, cache: dict) -> list[Document]:
    """向量检索，按 (query, k) 缓存：三个配置共用一份结果，省 2/3 的嵌入 API 调用。"""
    key = (query, k)
    if key not in cache:
        from backend.app.agent.graph import get_vectorstore
        cache[key] = get_vectorstore().similarity_search(query, k=k)
    return cache[key]


def rrf(doc_lists: list[list[Document]], weights: list[float], c: int = RRF_C) -> list[Document]:
    """加权 RRF，与 EnsembleRetriever.weighted_reciprocal_rank（ensemble.py:304-352）等价：
    score = Σ wᵢ/(rankᵢ + c)，rank 从 1 起，按 page_content 去重（id_key=None 的默认行为），
    分数降序。手工实现只为复用 dense 缓存，公式不敢改——改了评估的就不是要上线的东西。

    ⚠ 同一内容出现多次时分数**累加**（与官方一致，源码注释原文："Duplicated contents
    across retrievers are collapsed & scored cumulatively"）。所以语料里的重复 chunk
    一旦被同一路检索多次召回，就会叠加分数、挤掉真正的最优项 —— 见 main() 末尾统计。
    """
    score: dict[str, float] = defaultdict(float)
    for docs, w in zip(doc_lists, weights, strict=False):
        for rank, d in enumerate(docs, start=1):
            score[d.page_content] += w / (rank + c)
    seen: set[str] = set()
    out: list[Document] = []
    for docs in doc_lists:
        for d in docs:
            if d.page_content in seen:
                continue
            seen.add(d.page_content)
            out.append(d)
    out.sort(key=lambda d: score[d.page_content], reverse=True)
    return out


# ---------------------------------------------------------------- 命中判定与指标
def is_hit(src: str, exp: str) -> bool:
    """预置件按相对路径精确匹配；上传件的 source 形如 uploads/<doc_id>__<文件名>，
    取 '__' 后半段与标注的文件名比。"""
    if not src:
        return False
    if src == exp:
        return True
    if src.startswith("uploads/"):
        return src.split("__", 1)[-1] == exp
    return src.endswith("/" + exp)


def first_hit_rank(docs: list[Document], expected: list[str]) -> int | None:
    for i, d in enumerate(docs, start=1):
        src = d.metadata.get("source") or ""
        if any(is_hit(src, e) for e in expected):
            return i
    return None


def pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def summarize(ranks: list[int | None], latencies: list[float | None]) -> dict:
    """指标汇总。

    失败的 query 在 ranks 里是 None：**计入分母**（否则等于偷偷抹掉失败样本、虚高指标），
    但不计入命中。延迟相反——**整体剔除 None 再算分位**，因为把失败样本按 0ms 计入
    会伪造"变快了"的假象。
    """
    n = len(ranks) or 1
    hits = [r for r in ranks if r is not None]
    lats = [x for x in latencies if x is not None]
    return {
        "n": len(ranks),
        "recall": len(hits) / n,                       # recall@top_n（文档级，任一命中）
        "mrr": sum(1.0 / r for r in hits) / n,
        "hit1": sum(1 for r in hits if r == 1) / n,
        "p50_ms": statistics.median(lats) if lats else 0.0,
        "p95_ms": (sorted(lats)[max(0, int(len(lats) * 0.95) - 1)] if lats else 0.0),
    }


# ---------------------------------------------------------------- 单条 query
class Stash(TypedDict):
    """第一轮的原始检索结果，供第二轮精排复用（避免把 dense / bm25 再跑一遍）。

    ⚠ fusions / fuse_ms 的键**只包含第一轮实际算过的权重**，取不存在的权重就是 KeyError
    —— 所以 main() 会把 --rerank-weight 指定的权重先并入 weights_list 再开跑。
    """

    dense: list[Document]
    bm25: list[Document]
    fusions: dict[tuple[float, float], list[Document]]
    fuse_ms: dict[tuple[float, float], float]
    ms_dense: float
    ms_bm25: float


def hybrid_label(weights: tuple[float, float], multi: bool) -> str:
    """配置名：单组权重沿用历史名 hybrid；多组用 hybrid_w0.7 / hybrid_w1.0 便于扫表区分。"""
    if not multi:
        return "hybrid"
    w = round(weights[0], 3)          # 防 0.30000000000000004 这类浮点尾差
    return f"hybrid_w{w:g}" if w % 1 else f"hybrid_w{w:.1f}"


def parse_weights(spec: str) -> list[tuple[float, float]]:
    """解析权重：单组 "0.6,0.4"，多组用分号分隔 "0.7,0.3;0.8,0.2"。"""
    out: list[tuple[float, float]] = []
    for group in spec.split(";"):
        group = group.strip()
        if not group:
            continue
        parts = group.split(",")
        if len(parts) != 2:
            raise ValueError(f"每组权重必须是两个数（dense,sparse），收到 {group!r}")
        try:
            w = (float(parts[0]), float(parts[1]))
        except ValueError as exc:
            raise ValueError(f"权重不是数字：{group!r}") from exc
        if w[0] < 0 or w[1] < 0:
            raise ValueError(f"权重不能为负：{group!r}")
        out.append(w)
    if not out:
        raise ValueError("没有解析到任何权重")
    return out


def eval_query(q: str, k: int, top_n: int, weights_list: list[tuple[float, float]],
               dense_cache: dict) -> tuple[dict, Stash]:
    """第一轮：跑 dense / bm25 并做**所有**权重的 RRF 融合，返回 (out, stash)。

    out:   {config: {"docs": [...], "ms": 累计毫秒}}，config 形如 dense / bm25 / hybrid_w0.7
    stash: 原始 dense、bm25、各组融合结果（均未截断）与各段耗时，供第二轮精排复用，
           避免为了精排把 dense / bm25 重新检索一遍。

    延迟用**累计口径**：hybrid = dense + bm25 + 融合，hybrid_rerank 再加精排 RTT。
    同步 EnsembleRetriever.rank_fusion 本来就是顺序调两个 retriever（ensemble.py:239-248），
    所以累计值就是上线后的真实耗时，不是"只算融合那一下"的自欺数字。

    融合是纯 Python 字典累加（微秒级），所以扫多组权重几乎零成本；精排是付费网络调用，
    因此被隔离到第二轮，只对选定的那组权重跑一次（24 条 = 24 次调用，而非 24×N）。
    """
    multi = len(weights_list) > 1
    out: dict[str, dict] = {}

    t = time.perf_counter()
    dense = dense_search(q, k, dense_cache)
    ms_dense = (time.perf_counter() - t) * 1000
    out["dense"] = {"docs": dense[:top_n], "ms": ms_dense}

    t = time.perf_counter()
    bm25 = get_bm25(k).invoke(q)
    ms_bm25 = (time.perf_counter() - t) * 1000
    out["bm25"] = {"docs": bm25[:top_n], "ms": ms_bm25}

    fusions: dict[tuple[float, float], list[Document]] = {}
    fuse_ms: dict[tuple[float, float], float] = {}
    for w in weights_list:
        t = time.perf_counter()
        fused = rrf([dense, bm25], list(w))
        ms_fuse = (time.perf_counter() - t) * 1000
        fusions[w] = fused
        fuse_ms[w] = ms_fuse
        out[hybrid_label(w, multi)] = {"docs": fused[:top_n],
                                       "ms": ms_dense + ms_bm25 + ms_fuse}

    stash: Stash = {"dense": dense, "bm25": bm25, "fusions": fusions, "fuse_ms": fuse_ms,
                    "ms_dense": ms_dense, "ms_bm25": ms_bm25}
    return out, stash


def rerank_query(q: str, weights: tuple[float, float], top_n: int, k: int,
                 stash: Stash, reranker: DashScopeReranker) -> dict:
    """第二轮：对选定权重的融合结果做云端精排，返回 {"docs": [...], "ms": 累计毫秒}。

    精排输入是 fused[:k] —— cross-encoder 只能对**给定候选**重排，无法召回上游漏掉的
    文档，所以 recall 只会 ≤ 上游融合结果，解读报表时必须牢记这一点。
    """
    fused = stash["fusions"][weights]
    t = time.perf_counter()
    ranked = reranker.compress_documents(fused[:k], q)
    ms_rerank = (time.perf_counter() - t) * 1000
    return {"docs": list(ranked)[:top_n],
            "ms": stash["ms_dense"] + stash["ms_bm25"] + stash["fuse_ms"][weights] + ms_rerank}


# ---------------------------------------------------------------- 报表
def config_entry(docs: list[Document], ms: float, expected: list[str]) -> dict:
    """单个配置的评估明细：命中排名 + 累计耗时 + top 列表（供 --detail 与 JSON 落盘）。"""
    return {
        "first_hit_rank": first_hit_rank(docs, expected),
        "ms": round(ms, 1),
        "top": [{"source": d.metadata.get("source", "?"),
                 "title": d.metadata.get("title"),
                 "relevance": d.metadata.get("relevance_score"),
                 "snippet": d.page_content[:60].replace("\n", " ")}
                for d in docs],
    }


def print_detail(label: str, entry: dict, expected: list[str]) -> None:
    """--detail 用的单配置 top-N 打印。"""
    print(f"      ── {label}")
    for d in entry["top"]:
        mark = "✔" if any(is_hit(d["source"], e) for e in expected) else " "
        sc = f" score={d['relevance']:.3f}" if d["relevance"] is not None else ""
        print(f"       {mark} {d['source'][:52]:<52s}{sc}")


def print_table(title: str, labels: list[str], summary: dict, mark_best: bool = False) -> None:
    """通用指标表；mark_best=True 时标出 recall→MRR 最优行。"""
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)
    print(f"{'配置':<16s}{'recall@n':>10s}{'MRR':>8s}{'hit@1':>8s}"
          f"{'p50 延迟':>11s}{'p95 延迟':>11s}")
    best = max(labels, key=lambda c: (summary[c]["recall"], summary[c]["mrr"]))
    for c in labels:
        s = summary[c]
        tail = "   ← 最优" if (mark_best and c == best) else ""
        print(f"{c:<16s}{pct(s['recall']):>10s}{s['mrr']:>8.3f}{pct(s['hit1']):>8s}"
              f"{s['p50_ms']:>9.0f}ms{s['p95_ms']:>9.0f}ms{tail}")
    if mark_best:
        print(f"  （排序口径：recall 优先，其次 MRR → 最优 = {best}）")


def divergence_counts(rows: list[dict], configs: list[str]) -> None:
    """各配置相对 dense 的救回/丢失计数：权重多组时比逐条明细更易读。"""
    print(f"{'配置':<16s}{'↑救回':>8s}{'↓丢失':>8s}{'净增':>8s}")
    for c in configs:
        if c == "dense":
            continue
        gain = loss = 0
        for row in rows:
            d_rank = row["configs"]["dense"]["first_hit_rank"]
            r = row["configs"][c]["first_hit_rank"]
            if (r is None) != (d_rank is None):
                if r is not None:
                    gain += 1
                else:
                    loss += 1
        print(f"{c:<16s}{gain:>8d}{loss:>8d}{gain - loss:>+8d}")


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description="检索多配置离线对照评估（RRF 权重扫描 + 云端精排）")
    ap.add_argument("--quick", action="store_true", help="只跑前 3 条（冒烟）")
    ap.add_argument("--no-rerank", action="store_true", help="跳过精排（零 rerank API 调用）")
    ap.add_argument("--detail", action="store_true", help="打印每条 query 的各配置 top-5")
    ap.add_argument("--sweep", action="store_true", help="扫描内置六组 RRF 权重")
    ap.add_argument("--k", type=int, default=20, help="粗召回条数（默认 20）")
    ap.add_argument("--top-n", type=int, default=5, help="评估与返回条数（默认 5）")
    ap.add_argument("--weights", default="0.6,0.4",
                    help="dense,sparse 权重；多组用分号分隔，如 '0.7,0.3;0.8,0.2'")
    ap.add_argument("--rerank-weight", default="",
                    help="精排所用权重（缺省取本次权重里的最优）")
    ap.add_argument("--rerank-timeout", type=int, default=RERANK_TIMEOUT_S,
                    help=f"精排 HTTP 超时秒数（默认 {RERANK_TIMEOUT_S}）")
    ap.add_argument("--out", default=str(ROOT / "data" / "eval_results.json"))
    args = ap.parse_args()

    # ---- 权重解析 ----
    if args.sweep:
        if args.weights != ap.get_default("weights"):
            print("提示：--sweep 已启用，--weights 被忽略，改用内置六组权重")
        weights_list = list(SWEEP_WEIGHTS)
    else:
        try:
            weights_list = parse_weights(args.weights)
        except ValueError as exc:
            print(f"--weights 解析失败：{exc}")
            return 2

    rerank_pref: tuple[float, float] | None = None
    if args.rerank_weight:
        try:
            pref = parse_weights(args.rerank_weight)
        except ValueError as exc:
            print(f"--rerank-weight 解析失败：{exc}")
            return 2
        if len(pref) != 1:
            print("--rerank-weight 只能给一组权重，如 0.7,0.3")
            return 2
        rerank_pref = pref[0]

    use_rerank = not args.no_rerank
    if rerank_pref is not None:
        if not use_rerank:
            print("提示：--no-rerank 已启用，--rerank-weight 不生效")
        elif rerank_pref not in weights_list:
            # 精排要复用第一轮的融合结果（stash["fusions"]），权重不在列表里就是 KeyError。
            # 顺手把它并入：用户既不会崩，也能在扫描表里看到这组权重的纯融合指标。
            weights_list = [*weights_list, rerank_pref]
            print(f"提示：--rerank-weight {rerank_pref[0]}/{rerank_pref[1]} 不在 --weights 里，"
                  f"已并入本次评估")

    multi = len(weights_list) > 1
    hybrid_labels = [hybrid_label(w, multi) for w in weights_list]
    if len(set(hybrid_labels)) != len(hybrid_labels):
        print("权重里 dense 分量有重复，配置名会撞车，请去掉重复项")
        return 2
    label_to_weights = dict(zip(hybrid_labels, weights_list, strict=True))
    base_configs = BASE_CONFIGS + hybrid_labels
    configs = base_configs + (["hybrid_rerank"] if use_rerank else [])

    data = json.loads((ROOT / "data" / "eval_queries.json").read_text(encoding="utf-8"))
    queries = data["queries"]
    if args.quick:
        queries = queries[:3]

    print("=" * 78)
    print("检索离线评估")
    print("=" * 78)

    # ---- 冷启动开销（这些是混合检索的真实成本，必须单列）----
    t = time.perf_counter()
    docs, stats = load_corpus()
    t_corpus = time.perf_counter() - t
    print(f"语料：{stats['chunks']} 段（预置 {stats['preset']} / 上传 {stats['upload']}）"
          f"，共 {stats['chars'] / 1e6:.2f}M 字符，加载 {t_corpus:.2f}s")

    t = time.perf_counter()
    get_bm25(args.k)
    t_bm25 = time.perf_counter() - t
    print(f"BM25 索引构建（jieba+ASCII 分词，{stats['chunks']} 段）：{t_bm25:.2f}s")

    t_probe = 0.0
    if use_rerank:
        try:
            t_probe = probe_reranker(args.top_n, args.rerank_timeout)
        except RuntimeError as exc:
            print(f"\n[致命] {exc}")
            return 3
        print(f"精排探活（{settings.rerank_model}，真实 API 调用）：{t_probe:.2f}s")

    print(f"查询数：{len(queries)}　粗召回 k={args.k}　评估 top_n={args.top_n}　c={RRF_C}")
    print("RRF 权重：" + "，".join(f"{w[0]}/{w[1]}" for w in weights_list))
    print()

    # ---- 第一轮：dense + bm25 + 所有权重的融合（零精排调用）----
    dense_cache: dict = {}
    ranks: dict[str, list[int | None]] = {c: [] for c in configs}
    lats: dict[str, list[float | None]] = {c: [] for c in configs}
    per_type: dict[str, dict[str, list[int | None]]] = defaultdict(
        lambda: {c: [] for c in configs})
    rows: list[dict] = []          # 只放成功行（带 stash），供第二轮与报表复用

    for i, item in enumerate(queries, 1):
        q, exp, qtype = item["query"], item["expected_sources"], item["type"]
        try:
            res, stash = eval_query(q, args.k, args.top_n, weights_list, dense_cache)
        except Exception as exc:                  # 单条失败不该整轮报废
            print(f"[{i:2d}/{len(queries)}] ERROR {item['id']}: {type(exc).__name__}: {exc}")
            for c in configs:                     # 精排配置同样记 None，保持指标分母一致
                ranks[c].append(None)
                lats[c].append(None)              # 延迟记 None（summarize 会剔除）而非 0ms
                per_type[qtype][c].append(None)
            continue

        line = [f"[{i:2d}/{len(queries)}] {item['id']:<16s}"]
        row = {"id": item["id"], "type": qtype, "query": q,
               "expected_sources": exp, "configs": {}, "stash": stash}
        for c in base_configs:
            entry = config_entry(res[c]["docs"], res[c]["ms"], exp)
            ranks[c].append(entry["first_hit_rank"])
            lats[c].append(res[c]["ms"])
            per_type[qtype][c].append(entry["first_hit_rank"])
            row["configs"][c] = entry
            tag = f"hit@{entry['first_hit_rank']}" if entry["first_hit_rank"] else "MISS"
            line.append(f"{c}={tag}{res[c]['ms']:6.0f}ms")
        rows.append(row)
        print("  ".join(line))
        if args.detail:
            for c in base_configs:
                print_detail(c, row["configs"][c], exp)

    base_summary = {c: summarize(ranks[c], lats[c]) for c in base_configs}
    if multi:
        print_table(f"RRF 权重扫描（{len(weights_list)} 组，top_{args.top_n}）",
                    base_configs, base_summary, mark_best=True)

    # ---- 第二轮：只对选定权重做云端精排（N 条 = N 次真实调用）----
    rerank_weights: tuple[float, float] | None = None
    if use_rerank:
        if rerank_pref is not None:
            rerank_weights = rerank_pref          # 解析阶段已保证它一定在 weights_list 里
        else:
            best_hybrid = max(hybrid_labels,
                              key=lambda c: (base_summary[c]["recall"], base_summary[c]["mrr"]))
            rerank_weights = label_to_weights[best_hybrid]
        source_label = hybrid_label(rerank_weights, multi)
        print()
        print("=" * 78)
        print(f"云端精排（{settings.rerank_model}，输入 = {source_label} 的 fused[:{args.k}]）")
        print("=" * 78)
        reranker = get_reranker(args.top_n, args.rerank_timeout)
        for i, row in enumerate(rows, 1):
            exp, qtype = row["expected_sources"], row["type"]
            res = rerank_query(row["query"], rerank_weights, args.top_n, args.k,
                               row["stash"], reranker)
            entry = config_entry(res["docs"], res["ms"], exp)
            ranks["hybrid_rerank"].append(entry["first_hit_rank"])
            lats["hybrid_rerank"].append(res["ms"])
            per_type[qtype]["hybrid_rerank"].append(entry["first_hit_rank"])
            row["configs"]["hybrid_rerank"] = entry
            tag = f"hit@{entry['first_hit_rank']}" if entry["first_hit_rank"] else "MISS"
            print(f"[精排 {i:2d}/{len(rows)}] {row['id']:<16s}{tag:<8s}{res['ms']:6.0f}ms")
            if args.detail:
                print_detail("hybrid_rerank", entry, exp)

    # ---- 汇总表（总体 + 分类别）----
    summary = {c: summarize(ranks[c], lats[c]) for c in configs}
    print_table(f"总体（{len(queries)} 条，top_{args.top_n}）", configs, summary)

    for qtype in ("term_en", "semantic_zh", "upload_zh"):
        if qtype not in per_type:
            continue
        print()
        print(f"── 分类别：{qtype}（recall@{args.top_n}）" + " " * 40)
        cells = []
        for c in configs:
            rs = per_type[qtype][c]
            hit = sum(1 for r in rs if r is not None)
            cells.append(f"{c}={hit}/{len(rs)}")
        print("   " + "   ".join(cells))

    # ---- 配置间分歧（最有信息量的部分：谁救回了谁漏掉的）----
    print()
    print("=" * 78)
    print("配置间分歧（某配置命中而 dense 未命中 → 说明增量有效；反之则是噪声）")
    print("=" * 78)
    if rows:
        divergence_counts(rows, configs)
    print()
    n_diff = 0
    for row in rows:
        d_rank = row["configs"]["dense"]["first_hit_rank"]
        for c in configs[1:]:
            r = row["configs"][c]["first_hit_rank"]
            if (r is None) != (d_rank is None):
                n_diff += 1
                gain = "↑救回" if r is not None else "↓丢失"
                print(f"  {gain} [{c}] {row['id']}  {row['query'][:34]}")
                print(f"        dense={'hit@' + str(d_rank) if d_rank else 'MISS'} → "
                      f"{c}={'hit@' + str(r) if r else 'MISS'}   期望={row['expected_sources']}")
    if n_diff == 0:
        print("  （无分歧：各配置命中情况完全一致）")

    # ---- 结论提示 ----
    print()
    print("=" * 78)
    base = summary["dense"]
    best = max(configs, key=lambda c: (summary[c]["recall"], summary[c]["mrr"]))
    print("怎么读这张表：")
    print(f"  · 基线 dense：recall={pct(base['recall'])} MRR={base['mrr']:.3f} "
          f"p50={base['p50_ms']:.0f}ms")
    print(f"  · 最优配置：{best}（recall={pct(summary[best]['recall'])} "
          f"MRR={summary[best]['mrr']:.3f} p50={summary[best]['p50_ms']:.0f}ms）")
    if use_rerank and rerank_weights is not None:
        ref = hybrid_label(rerank_weights, multi)
        extra = summary["hybrid_rerank"]["p50_ms"] - summary[ref]["p50_ms"]
        d_rec = summary["hybrid_rerank"]["recall"] - summary[ref]["recall"]
        print(f"  · 精排净代价（{ref} → hybrid_rerank）：p50 +{extra:.0f}ms，"
              f"recall {d_rec * 100:+.1f} 个百分点")
        print(f"    → 这段**云端网络往返**会直接加在 SSE 首 token 之前，值不值看上面两个数。")
    dup_rows = [r["id"] for r in rows
                if len({d.page_content for d in r["stash"]["dense"]}) < len(r["stash"]["dense"])]
    if dup_rows:
        print(f"  · ⚠ dense 结果含重复内容的 query：{len(dup_rows)}/{len(rows)}"
              f"（如 {dup_rows[0]}）")
        print("    → RRF 对重复内容**累加计分**（官方 EnsembleRetriever 行为），重复 chunk 排名虚高，")
        print("      这正是 hybrid_w1.0 的 MRR 低于 dense 的原因。")
        print("    → ⚠ 但「入库去重 + 滤超短片段」**只对 hybrid 有用**：累加计分是 RRF 的行为，")
        print("      dense 是单路相似度排序、不累加。实测（2026-09-20，语料 3755 段中 311 段 <100")
        print("      字符、125 段长重复；k=4 与 k=5 各模拟一遍）：垃圾仅占 2/96 席位，滤掉后 24 条")
        print("      查询的 recall/MRR/hit@1 **一个都没变**。→ 不必为此重建向量库（3319 段 = 332 次")
        print("      嵌入请求），等真要上 hybrid 时把这一步并进那次重建即可。")
    print(f"  · 冷启动开销：语料加载 {t_corpus:.1f}s + BM25 建索引 {t_bm25:.1f}s"
          + (f" + 精排探活 {t_probe:.1f}s" if use_rerank else ""))
    print("    → BM25 索引是静态快照，**KB 上传后必须 cache_clear() 重建**，"
          "否则新文档在稀疏检索里不可见。")

    out = {
        "meta": {"queries": len(queries), "k": args.k, "top_n": args.top_n,
                 "sweep_weights": [list(w) for w in weights_list],
                 "rerank_weight": list(rerank_weights) if rerank_weights else None,
                 "rrf_c": RRF_C,
                 "reranker_model": settings.rerank_model if use_rerank else None,
                 "corpus": stats,
                 "cold_start_s": {"corpus": round(t_corpus, 2), "bm25": round(t_bm25, 2),
                                  "rerank_probe": round(t_probe, 2)}},
        "summary": summary,
        "queries": [{k: v for k, v in row.items() if k != "stash"} for row in rows],
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
