# -*- coding: utf-8 -*-
"""rag_qa_project 离线测试（零 API 额度）。

设计思想
--------
用「假 LLM」替换 DeepSeek、用「内存检索器」替换 Chroma，全程不触网、不耗额度，
秒级验证 CRAG 工作流的三层行为：

1. 图结构    —— 能编译、四个节点齐全；
2. 端到端路由 —— 全部相关直答 / 部分相关过滤 / 改写后命中 / 改写耗尽兜底；
3. 节点单元  —— retrieve / grade_documents / rewrite_query / generate 各自的输入输出，
              以及路由函数 _decide_to_generate 的三条分支。

运行方式（在 rag_qa_project 目录下）：
    python -m pytest tests/ -v
"""
import json
import re
import sys
from pathlib import Path

import pytest

# 让 tests/ 能导入项目模块（等价于把 rag_qa_project 标记为 Sources Root）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from config import settings
from backend.app.agent.graph import _decide_to_generate, _make_nodes, build_graph, kb_filter


# ============================ 测试替身（Fakes） ============================
def _count_numbered(prompt_text: str) -> int:
    """统计 grade prompt 中以 [i] 编号开头的候选文档行数。"""
    return len(re.findall(r"^\[\d+\]", prompt_text, flags=re.M))


class FakeRAGModel(FakeMessagesListChatModel):
    """按 prompt 内容路由到 grade / rewrite / generate 三种行为的假 LLM。

    relevance_mode 控制「文档相关性」判定，用于驱动不同的路由分支：
        "all"           全部相关           -> 检索一次即直答
        "none"          全部不相关         -> 改写直到上限后兜底
        "first"         仅第一篇相关       -> 验证 grade 的过滤
        "after_rewrite" 仅改写后才判为相关 -> 验证「改写 -> 命中」恢复路径
    """

    relevance_mode: str = "all"
    rewrite_to: str = "LangGraph checkpointer 持久化 状态 原理"
    answer_text: str = "（模拟回答）LangGraph 通过 checkpointer 持久化图状态。"

    def with_structured_output(self, schema, **kwargs):
        """把 _generate 返回的 JSON 文本解析成 schema 实例，模拟结构化输出。"""
        from langchain_core.runnables import RunnableLambda

        def _call(prompt):
            msg = self.invoke(prompt)
            data = json.loads(msg.content)
            return schema(**data) if isinstance(schema, type) else data

        return RunnableLambda(_call)

    def _relevance_flags(self, n: int, prompt_text: str):
        """根据 relevance_mode 生成长度为 n 的相关性布尔列表。"""
        mode = self.relevance_mode
        if mode == "none":
            return [False] * n
        if mode == "first":
            return [True] + [False] * (n - 1)
        if mode == "after_rewrite":
            hit = self.rewrite_to in prompt_text   # 改写后的问题才会出现在 prompt 里
            return [hit] * n
        return [True] * n   # "all"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        text = messages[-1].content if messages else ""

        def reply(content: str) -> ChatResult:
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=content))])

        n = _count_numbered(text)
        if n:                              # grade_documents 调用
            return reply(json.dumps({"relevance": self._relevance_flags(n, text)}))
        if "改写" in text:                  # rewrite_query 调用
            return reply(json.dumps({"query": self.rewrite_to}))
        return reply(self.answer_text)     # generate 调用


class FakeVectorStore:
    """内存向量库替身：返回固定的 langchain 文档片段，并**记录每次调用收到的 filter**。

    P3 起 `retrieve` 节点直接调 vectorstore.similarity_search（不再是 retriever.invoke），
    这不是换汤不换药 —— 旧的 FakeRetriever 只有 invoke(query)，**根本看不见 filter**，
    所以「按用户隔离」这件事在 P1/P2 时期完全无法测试。calls 记下 filter 之后，
    才能断言 {"$or":[{"kb":...},{"user_id":7}]} 这种隔离语义。
    """

    def __init__(self, docs=None):
        self.docs = list(docs) if docs is not None else [
            Document(
                page_content="LangGraph 用 checkpointer 在每个 super-step 后持久化状态，"
                             "从而支持断点续跑与时间旅行。",
                metadata={"title": "Persistence", "source": "langgraph/persistence.md"}),
            Document(
                page_content="LangChain 消息通过 add_messages reducer 累加，"
                             "并按消息 id 自动去重/覆盖。",
                metadata={"title": "Messages", "source": "langchain/messages.md"}),
        ]
        self.calls: list[dict] = []

    def similarity_search(self, query: str, k: int = 4, filter=None, **kwargs):
        self.calls.append({"query": query, "k": k, "filter": filter})
        return list(self.docs)[:k]


@pytest.fixture
def base_state():
    """一个标准的初始 RAGState。"""
    return {"question": "LangGraph 怎么做持久化？",
            "documents": [], "generation": "", "rewrites": 0}


def _model(**kwargs) -> FakeRAGModel:
    """构造 FakeRAGModel；responses=[] 是父类必填项，实际逻辑走重写的 _generate。"""
    return FakeRAGModel(responses=[], **kwargs)


# ============================ 1. 图结构 ============================
def test_graph_compiles_with_all_nodes():
    """图能成功编译，且包含 retrieve / grade / rewrite / generate 四个节点。"""
    app = build_graph(model=_model(), vectorstore=FakeVectorStore(), checkpointer=InMemorySaver())
    assert app is not None
    node_names = set(app.get_graph().nodes)
    assert {"retrieve", "grade_documents", "rewrite_query", "generate"} <= node_names


# ============================ 2. 端到端路由 ============================
def test_happy_path_all_relevant(base_state):
    """全部相关 -> 检索一次即直答，不触发改写。"""
    vs = FakeVectorStore()
    app = build_graph(model=_model(relevance_mode="all"), vectorstore=vs, checkpointer=InMemorySaver())
    result = app.invoke(base_state, config={"configurable": {"thread_id": "test"}, "recursion_limit": 20})

    assert result["rewrites"] == 0
    assert len(result["documents"]) == 2
    assert "模拟回答" in result["generation"]
    # calls 现在记的是 {query,k,filter} 字典，取 query 比对
    assert [c["query"] for c in vs.calls] == [base_state["question"]]   # 只检索了一次


def test_partial_relevance_keeps_only_relevant(base_state):
    """部分相关 -> grade 只保留判为相关的文档，仍然直答。"""
    app = build_graph(model=_model(relevance_mode="first"), vectorstore=FakeVectorStore(), checkpointer=InMemorySaver())
    result = app.invoke(base_state, config={"configurable": {"thread_id": "test"}, "recursion_limit": 20})

    assert [d.metadata["title"] for d in result["documents"]] == ["Persistence"]
    assert result["rewrites"] == 0
    assert "模拟回答" in result["generation"]


def test_rewrite_then_hit(base_state):
    """先判不相关 -> 改写 -> 再检索命中 -> 正常作答（恢复路径）。"""
    vs = FakeVectorStore()
    app = build_graph(model=_model(relevance_mode="after_rewrite"), vectorstore=vs, checkpointer=InMemorySaver())
    result = app.invoke(base_state, config={"configurable": {"thread_id": "test"}, "recursion_limit": 20})

    assert result["rewrites"] == 1
    assert len(result["documents"]) == 2
    assert "模拟回答" in result["generation"]
    assert len(vs.calls) == 2   # 初次 + 改写后各一次


def test_rewrite_exhausted_falls_back(base_state):
    """始终不相关 -> 改写到 max_rewrites 上限 -> 兜底改为用模型自有知识作答（不再硬拒答）。"""
    vs = FakeVectorStore()
    app = build_graph(model=_model(relevance_mode="none"), vectorstore=vs, checkpointer=InMemorySaver())
    result = app.invoke(base_state, config={"configurable": {"thread_id": "test"}, "recursion_limit": 20})

    assert result["rewrites"] == settings.max_rewrites
    assert result["documents"] == []
    assert len(vs.calls) == settings.max_rewrites + 1
    # 兜底现在会真调用 model：generation 是模型产出，不再是硬编码的「未找到」
    assert "模拟回答" in result["generation"]
    # 且必须把 grounded=False 持久化到 AIMessage，否则刷新后警示标识丢失
    assert any(getattr(m, "additional_kwargs", {}).get("grounded") is False
               for m in result["messages"]), "兜底回答必须带 grounded=False"


# ============================ 3. 节点单元 ============================
def test_node_retrieve(base_state):
    """retrieve：把检索结果写入 documents。"""
    retrieve, _, _, _ = _make_nodes(_model(), FakeVectorStore())
    out = retrieve(base_state)

    assert len(out["documents"]) == 2
    assert out["documents"][0].metadata["title"] == "Persistence"


def test_node_grade_filters():
    """grade_documents：按相关性列表过滤文档，只留下判为相关的。"""
    docs = [Document(page_content="a", metadata={"title": "A"}),
            Document(page_content="b", metadata={"title": "B"})]
    _, grade, _, _ = _make_nodes(_model(relevance_mode="first"), FakeVectorStore())
    out = grade({"question": "q", "documents": docs, "generation": "", "rewrites": 0})

    assert [d.metadata["title"] for d in out["documents"]] == ["A"]


def test_node_rewrite_updates_state():
    """rewrite_query：产出 search_query 并使 rewrites 计数 +1。

    ⚠ 本用例原先断言 `out["question"] == model.rewrite_to`，即「改写覆写 question」——
    那等于把 P0-① 的缺陷当成期望行为钉进了测试。改写是**检索**手段，用户原话不该被动：
    覆写 question 会让历史里的提问变成英文改写查询（刷新后用户可见），
    还会让第 2 次改写的「原始问题」变成第 1 次的输出。
    """
    model = _model()
    _, _, rewrite, _ = _make_nodes(model, FakeVectorStore())
    out = rewrite({"question": "原始问题", "documents": [], "generation": "", "rewrites": 0})

    assert out["search_query"] == model.rewrite_to
    assert "question" not in out, "改写不得覆写用户原话（P0-①）"
    assert out["rewrites"] == 1


def test_node_generate_empty_documents(base_state):
    """generate：documents 为空时调用 LLM 用自有知识作答，并标记 grounded=False。

    旧行为是返回硬编码的「未找到」且不调用 model——那会让 stream_mode="messages"
    拿不到任何 AIMessageChunk，前端收到 0 帧 token、答案气泡空白。
    """
    _, _, _, generate = _make_nodes(_model(), FakeVectorStore())
    out = generate({**base_state, "documents": []})

    assert "模拟回答" in out["generation"]                 # 真的调用了 model
    human, ai = out["messages"]
    assert isinstance(human, HumanMessage)
    assert ai.additional_kwargs["grounded"] is False        # 刷新后靠它渲染警示标识


def test_node_generate_uses_context(base_state):
    """generate：有文档时拼接上下文并调用 LLM 产出答案。"""
    docs = [Document(page_content="LangGraph 持久化状态。", metadata={"title": "Persistence"})]
    _, _, _, generate = _make_nodes(_model(), FakeVectorStore())
    out = generate({**base_state, "documents": docs})

    assert "模拟回答" in out["generation"]
    assert out["messages"][1].additional_kwargs["grounded"] is True   # 有资料 -> grounded


# ============================ 4. 路由函数单元 ============================
def test_decide_to_generate_branches():
    """_decide_to_generate 的三条分支：有文档 / 可改写 / 改写耗尽。"""
    doc = Document(page_content="x")
    assert _decide_to_generate({"documents": [doc], "rewrites": 0}) == "generate"
    assert _decide_to_generate({"documents": [], "rewrites": 0}) == "rewrite"
    assert _decide_to_generate(
        {"documents": [], "rewrites": settings.max_rewrites}) == "generate"


# ============================ 5. 检索按用户过滤（P3） ============================
def test_retrieve_filter_with_user_id():
    """P3 §5.2：有 user_id → 预置文档 + 本人上传件。"""
    vs = FakeVectorStore()
    retrieve, _, _, _ = _make_nodes(_model(), vs)

    retrieve({"question": "q", "documents": [], "generation": "", "rewrites": 0, "user_id": 7})

    assert vs.calls[0]["filter"] == {"$or": [{"kb": "langchain_docs"}, {"user_id": 7}]}
    assert vs.calls[0]["k"] == settings.top_k


def test_retrieve_filter_without_user_id():
    """P3 §5.2：无 user_id（CLI / 未登录）→ 只见预置文档。

    断言的是**裸条件**而不是 {"$or": [{"kb": ...}]} —— 后者会让 Chroma 抛
    ValueError（$or 至少要两个子条件，spec §5.2 与 upload_function_tools.py:17-18）。
    """
    vs = FakeVectorStore()
    retrieve, _, _, _ = _make_nodes(_model(), vs)

    retrieve({"question": "q", "documents": [], "generation": "", "rewrites": 0})

    assert vs.calls[0]["filter"] == {"kb": "langchain_docs"}
    assert "$or" not in vs.calls[0]["filter"]


def test_kb_filter_never_uses_origin_builtin():
    """钉住 spec §5.1 的纠错：预置文档**没有 `origin` 字段**。

    设计初稿写的是 {"$or":[{"origin":"builtin"},{"user_id":X}]}。若按那样实现，
    88 篇官方文档会全部从检索结果里消失，**且不报任何错** —— 问答质量断崖下跌却无人
    察觉（这正是它值得一条专门测试的原因）。正确的标记字段是 `kb`。
    """
    for uid in (None, 7):
        cond = json.dumps(kb_filter(uid))
        assert "origin" not in cond, (
            f"过滤条件里出现了 origin={cond}；预置文档没有 origin 字段，"
            "用它过滤会让 88 篇官方文档全部消失，应该用 kb")
        assert "langchain_docs" in cond


# ============================ 6. P0 修复：改写不改用户原话 + 空检索短路 ============================
class RecordingRAGModel(FakeRAGModel):
    """在 FakeRAGModel 上记录每次 LLM 调用的提示词，用于断言「哪些节点真的被调用了」。

    ⚠ 不能只断言「没抛异常」来证明「grade 被跳过了」—— 那只说明这次侥幸没炸，
    数不清调用次数。`seen` 存完整提示词，`kinds()` 按提示词开头分类。
    """

    seen: list = Field(default_factory=list)

    def _generate(self, messages, *a, **kw):
        self.seen.append(((messages[-1].content if messages else "") or "").lstrip())
        return super()._generate(messages, *a, **kw)

    def kinds(self) -> list[str]:
        def classify(s: str) -> str:
            if s.startswith("你是检索质量评审员"):
                return "grade"
            if s.startswith("你是检索查询改写器"):
                return "rewrite"
            return "generate"
        return [classify(s) for s in self.seen]


# ---------- P0-① 改写不得覆写用户原话 ----------
def test_rewrite_writes_search_query_and_leaves_question_alone(base_state):
    """★ P0-①：改写结果落到 `search_query`，**不得**再返回 `question`。

    实测过的缺陷：`rewrite_query` 返回 {"question": <英文改写>} 覆写了 state["question"]，
    而 `generate` 又拿 state["question"] 当本轮 HumanMessage 存进 messages ——
    于是历史（以及刷新后的气泡）里显示的是 'LangGraph checkpointer 持久化 状态 原理'，
    而不是用户说的「LangGraph 怎么做持久化？」。
    额外好处：第 2 次改写时「原始问题」不会再被第 1 次的输出顶掉（关键词漂移叠加）。
    """
    _, _, rewrite, _ = _make_nodes(_model(), FakeVectorStore())

    out = rewrite({**base_state, "documents": [], "rewrites": 0})

    assert out["search_query"], "改写结果必须落到 search_query，供 retrieve 使用"
    assert "question" not in out, "改写不得再碰 question——那是用户的原话"


def test_retrieve_searches_with_search_query_when_present(base_state):
    """retrieve 用改写后的 search_query 检索（向量检索仍享受改写红利）。"""
    vs = FakeVectorStore()
    retrieve, _, _, _ = _make_nodes(_model(), vs)

    retrieve({**base_state, "search_query": "LangGraph checkpointer 原理"})

    assert vs.calls[0]["query"] == "LangGraph checkpointer 原理"


def test_retrieve_falls_back_to_question_without_search_query(base_state):
    """未改写（首轮 / CLI）时没有 search_query，检索用用户原话。"""
    vs = FakeVectorStore()
    retrieve, _, _, _ = _make_nodes(_model(), vs)

    retrieve(base_state)

    assert vs.calls[0]["query"] == base_state["question"]


def test_history_keeps_original_question_after_rewrite(base_state):
    """★ P0-① 端到端：真发生改写后，messages 里的用户提问仍必须是原话。"""
    app = build_graph(model=_model(relevance_mode="after_rewrite"),
                      vectorstore=FakeVectorStore(), checkpointer=InMemorySaver())

    result = app.invoke(base_state, config={"configurable": {"thread_id": "t-orig"},
                                            "recursion_limit": 20})

    assert result["rewrites"] == 1, "本用例需要真的发生改写，否则测不到这条路径"
    humans = [m.content for m in result["messages"] if isinstance(m, HumanMessage)]
    assert humans == [base_state["question"]], f"历史里的用户提问被改写了：{humans}"


def test_second_rewrite_still_gets_original_question(base_state):
    """★ 同源缺陷：连续改写时，「原始问题」不能被上一次的改写结果顶掉。

    旧实现里 rewrite 覆写 question，于是第 2 次改写拿到的是第 1 次的**输出** ——
    关键词越滚越偏（本例的假模型固定返回同一串，真实模型下这就是质量漂移）。
    """
    model = RecordingRAGModel(responses=[], relevance_mode="none")
    app = build_graph(model=model, vectorstore=FakeVectorStore(),
                      checkpointer=InMemorySaver())
    model.seen.clear()

    app.invoke(base_state, config={"configurable": {"thread_id": "t-drift"},
                                   "recursion_limit": 25})

    prompts = [p for p in model.seen if p.startswith("你是检索查询改写器")]
    assert len(prompts) == settings.max_rewrites, "应当发生 max_rewrites 次改写"
    for p in prompts:
        assert f"原始问题：{base_state['question']}" in p, (
            "每次改写的「原始问题」都必须是用户原话，而不是上一次的改写结果")


# ---------- P0-② 空召回不能走 grade ----------
def test_retrieve_empty_skips_grade(base_state):
    """★ P0-②：一次都没召回时**不该调 grade**。

    实测过的缺陷：0 条候选时 grade 的提示词退化成「候选文档共 0 条（编号 1..0）…
    请按编号顺序输出 0 个布尔值」，`with_structured_output` 拿不到合法 JSON
    （实测抛 JSONDecodeError）→ 异常冒泡成 SSE error 帧 →
    **用户拿不到本该有的「未命中知识库」兜底答案**。
    """
    model = RecordingRAGModel(responses=[], relevance_mode="all")
    app = build_graph(model=model, vectorstore=FakeVectorStore(docs=[]),
                      checkpointer=InMemorySaver())
    model.seen.clear()

    result = app.invoke(base_state, config={"configurable": {"thread_id": "t-empty"},
                                            "recursion_limit": 25})

    assert "grade" not in model.kinds(), f"空召回不该调 grade：{model.kinds()}"
    assert result["documents"] == []
    assert "模拟回答" in result["generation"], "兜底路径必须真的产出答案，而不是让整轮报错"


def test_retrieve_empty_rewrites_then_falls_back(base_state):
    """空召回 → 直接改写；改写用尽 → 兜底作答。LLM 只花在改写与生成上。"""
    model = RecordingRAGModel(responses=[], relevance_mode="all")
    vs = FakeVectorStore(docs=[])
    app = build_graph(model=model, vectorstore=vs, checkpointer=InMemorySaver())
    model.seen.clear()

    app.invoke(base_state, config={"configurable": {"thread_id": "t-empty2"},
                                   "recursion_limit": 25})

    # 首轮 1 次 + 每次改写后各 1 次
    assert len(vs.calls) == settings.max_rewrites + 1
    assert sorted(model.kinds()) == ["generate"] + ["rewrite"] * settings.max_rewrites


def test_grade_node_with_empty_documents_is_a_noop(base_state):
    """纵深防御：即便将来拓扑变了把 0 条喂给 grade，它也必须安全返回。

    单靠「retrieve 的条件边不会放空列表进来」是不够的 —— 节点本身必须自保，
    否则改拓扑的人会重新踩进同一个坑。
    """
    model = RecordingRAGModel(responses=[], relevance_mode="all")
    _, grade, _, _ = _make_nodes(model, FakeVectorStore())
    model.seen.clear()

    out = grade({**base_state, "documents": []})

    assert out == {"documents": []}
    assert model.seen == [], "0 条候选时必须短路，不能构造退化提示词去调模型"
