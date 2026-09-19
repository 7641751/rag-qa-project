# -*- coding: utf-8 -*-
"""chat_service.stream_chat 的 SSE 事件契约离线测试（零 API 额度、不触网）。

设计思想
--------
沿用 test_graph.py 的假 LLM + 内存检索器，再用 monkeypatch 把 chat_service.get_graph
换成用假件编译的图。守的是 docs/api/README.md「SSE 事件协议」的两条硬约束：

1. 事件序始终是 …step → token → sources → done；
2. **只要图产出了 generation，前端就必然收到至少一帧 token**。
   兜底分支（documents 为空）不调用 model，stream_mode="messages" 就没有
   AIMessageChunk 可推 —— 修复前 token 帧数恒为 0，前端答案气泡是空白的。

运行方式（在 rag_qa_project 目录下）：
    python -m pytest tests/test_chat_stream.py -v
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# 让 tests/ 能导入项目模块（等价于把 rag_qa_project 标记为 Sources Root）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from backend.app.agent.graph import build_graph
from backend.app.agent.schemas import ChatRequest
from backend.app.models import Base
from backend.app.services import chat_service
from langgraph.checkpoint.memory import InMemorySaver

from test_graph import FakeRAGModel, FakeRetriever      # 复用同一套假件，避免重复实现


# ============================ 工具 ============================
def _parse_sse(frames: list[str]) -> list[tuple[str, dict]]:
    """SSE 帧文本 -> [(event, data)]，口径与前端 client.ts / test_kb_upload 一致。"""
    out = []
    for block in frames:
        event, data = None, None
        for line in block.strip().splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        out.append((event, data))
    return out


def _run_stream(monkeypatch, relevance_mode: str,
                question: str = "LangGraph 怎么做持久化？") -> list[tuple[str, dict]]:
    """把 chat_service.get_graph 换成假件编译的图，跑完 stream_chat 并解析成事件列表。"""
    app = build_graph(model=FakeRAGModel(responses=[], relevance_mode=relevance_mode),
                      retriever=FakeRetriever(), checkpointer=InMemorySaver())
    monkeypatch.setattr(chat_service, "get_graph", lambda: app)
    req = ChatRequest(question=question, thread_id="t-stream")

    async def go():
        return [frame async for frame in chat_service.stream_chat(req)]

    return _parse_sse(asyncio.run(go()))


@pytest.fixture
def db_maker():
    """SQLite 内存库替身。

    `get_chat_history` 现在要求归属校验，所以必须给一个真会话。而 "t-stream" 在
    conversations 里没有行 → assert_owner 按「新会话」放行（spec §7.5），
    这两个持久化用例因此不必先播种归属。
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False})

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_setup())

    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    asyncio.run(engine.dispose())


def _history(maker, thread_id: str = "t-stream"):
    """按现签名读历史：get_chat_history(thread_id, user_id, db)。"""
    async def go():
        async with maker() as db:
            return await chat_service.get_chat_history(thread_id, 1, db)
    return asyncio.run(go())


# ============================ 1. 兜底不能空答案 ============================
def test_fallback_path_emits_token_frame_and_marks_ungrounded(monkeypatch):
    """★ 核心回归线：始终不相关 -> 改写耗尽 -> 兜底分支，仍必须发 token 帧且 grounded=False。

    修复前这里 token 帧数为 0，前端只收到 step/sources/done，答案气泡空白。
    """
    frames = _run_stream(monkeypatch, relevance_mode="none")
    events = [e for e, _ in frames]

    tokens = [d["text"] for e, d in frames if e == "token"]
    # 假模型返回 AIMessage 而非 AIMessageChunk，所以这里走的是 chat_service 的补发分支（恰好 1 帧）；
    # 真实流式模型下兜底会真调用 model、逐字多帧。两种情况都满足「至少一帧」这条契约。
    assert len(tokens) >= 1, f"兜底路径必须至少发一帧 token，实际 {len(tokens)} 帧"
    assert "模拟回答" in tokens[0]

    done = [d for e, d in frames if e == "done"][0]
    assert done["grounded"] is False           # ★ 无资料 → 前端据此渲染未验证标识

    assert events[-2:] == ["sources", "done"]               # 收尾两帧顺序不变
    assert events.index("token") < events.index("sources")   # token 必须在 sources 之前
    # 补发 token 前要先给一个 step(generate)，前端才知道「开始作答」
    gen_step_at = [i for i, (e, d) in enumerate(frames)
                   if e == "step" and d["node"] == "generate"]
    assert gen_step_at and gen_step_at[0] < events.index("token")


def test_fallback_done_reports_rewrite_count(monkeypatch):
    """兜底路径的 done 仍要带 thread_id 与已用尽的 rewrites 次数。"""
    frames = _run_stream(monkeypatch, relevance_mode="none")
    done = [d for e, d in frames if e == "done"][0]

    assert done["thread_id"] == "t-stream"
    assert done["rewrites"] == settings.max_rewrites      # 改写次数用尽才会落到兜底


# ============================ 2. 正常路径不受修复影响 ============================
def test_happy_path_emits_generation_and_sources(monkeypatch):
    """全部相关 -> 直答：generation 落到 token 帧，sources 带上检索到的两段。"""
    frames = _run_stream(monkeypatch, relevance_mode="all")
    by_event: dict[str, list] = {}
    for e, d in frames:
        by_event.setdefault(e, []).append(d)

    assert "模拟回答" in "".join(t["text"] for t in by_event["token"])
    assert [s["title"] for s in by_event["sources"][0]["sources"]] == ["Persistence", "Messages"]
    assert by_event["done"][0] == {"thread_id": "t-stream", "rewrites": 0, "grounded": True}


def test_partial_relevance_keeps_only_relevant_in_sources(monkeypatch):
    """部分相关 -> sources 只含判为相关的那一段（grade 的过滤结果）。"""
    frames = _run_stream(monkeypatch, relevance_mode="first")
    sources = [d for e, d in frames if e == "sources"][0]["sources"]

    assert [s["title"] for s in sources] == ["Persistence"]


def test_rewrite_loop_reports_rewrites_in_done(monkeypatch):
    """改写后命中：done.rewrites = 1，step 里能看到 rewrite_query，且同样不空答案。"""
    frames = _run_stream(monkeypatch, relevance_mode="after_rewrite")
    nodes = [d["node"] for e, d in frames if e == "step"]
    done = [d for e, d in frames if e == "done"][0]

    assert "rewrite_query" in nodes
    assert nodes.count("retrieve") == 2                     # 初次 + 改写后各一次
    assert done["rewrites"] == 1
    assert [d["text"] for e, d in frames if e == "token"]    # 恢复路径也不会空答案


# ============================ 3. grounded 持久化（刷新后标识不丢） ============================
def test_ungrounded_answer_is_persisted_in_history(monkeypatch, db_maker):
    """grounded=False 必须随 AIMessage 进 checkpointer：刷新后从 history 读回仍带标识。

    不持久化就是个安全风险：用户刷新页面后，一条无依据的答案看上去和有依据的一样。
    """
    _run_stream(monkeypatch, relevance_mode="none")
    hist = _history(db_maker)

    assistants = [m for m in hist.messages if m.role == "assistant"]
    assert assistants, "兜底回答也应进历史"
    assert assistants[-1].grounded is False


def test_grounded_answer_is_persisted_as_true(monkeypatch, db_maker):
    """有资料时 grounded=True 同样持久化，前端不会误报未验证。"""
    _run_stream(monkeypatch, relevance_mode="all")
    hist = _history(db_maker)

    assistants = [m for m in hist.messages if m.role == "assistant"]
    assert assistants[-1].grounded is True
