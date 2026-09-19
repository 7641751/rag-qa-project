# -*- coding: utf-8 -*-
"""rag_qa_project 核心工作流（CRAG 简化版，LangGraph 实现）。

流程：
    START -> retrieve -> grade_documents -> decide
        decide: 有相关文档    -> generate -> END
                无相关且可重写 -> rewrite_query -> retrieve（循环）
                重写次数用尽   -> generate（无资料：放开自由度，用模型自有知识作答并自我声明）

设计要点：
- build_graph(model=None, vectorstore=None, checkpointer=None) 支持依赖注入，
  测试时可传入 FakeChatModel / 内存向量库，零 API 额度跑通全图。
- 每个节点是纯函数：输入状态 -> 输出增量更新。
"""
from functools import lru_cache

import aiosqlite
from langchain_chroma import Chroma
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.app.agent.schemas import RAGState, GradeDocuments, RewrittenQuery
from config import settings


# ---------- 工厂 ----------
def get_model():
    from langchain_deepseek import ChatDeepSeek
    return ChatDeepSeek(model=settings.deepseek_model,
                        temperature=settings.temperature,
                        streaming=True)


@lru_cache(maxsize=1)
def get_vectorstore():
    """向量库单例：Chroma + DashScope 嵌入只加载一次，全进程复用。

    注意：不能把 embeddings 对象作为参数——DashScopeEmbeddings 不可哈希，
    会让 @lru_cache 抛 TypeError；改为内部构建、用无参缓存键。
    """
    from ingest import build_embeddings

    return Chroma(
        collection_name=settings.collection_name,
        embedding_function=build_embeddings(),
        persist_directory=str(settings.chroma_dir),
    )


@lru_cache(maxsize=1)
def get_checkpointer() -> AsyncSqliteSaver:
    """AsyncSqliteSaver 单例（异步调用面专用）。三条约束缺一即出问题：
    ① 不用 `from_conn_string` 的 async with —— 退出即关连接，而单例要活到进程结束；
       手动 aiosqlite.connect 建连接，由 lru_cache 单例持有。
    ② 必须在运行中的事件循环里构造 —— __init__ 会捕获 get_running_loop() 存 self.loop，
       所以只能由异步上下文首次触发（uvicorn 请求期 / CLI 的 asyncio.run）；
       模块顶层或纯同步上下文构造会 RuntimeError。
    ③ 建表是惰性的：aget_tuple / alist 首次调用会 await setup()（幂等），无需手动 setup()。
    另注意：它的同步桥接方法（get_tuple/delete_thread 等）只允许跨线程调用，
    在事件循环线程上直接调会抛 InvalidStateError —— 服务层一律 await 异步版。
    """
    settings.checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
    return AsyncSqliteSaver(aiosqlite.connect(str(settings.checkpoint_db)))


async def aclose_checkpointer() -> None:
    """关闭 checkpointer 单例的连接（进程收尾专用）。

    aiosqlite 的工作线程是**非 daemon** 的：只有 close() 才会入队退出哨兵，
    否则解释器退出时会一直等这个线程 —— CLI 跑完进程不退出、uvicorn Ctrl+C 不退出。
    close 后顺手清掉 lru_cache：同一进程若再起新的 asyncio.run（新事件循环），
    下次建图会重建全新连接，避免旧连接跨事件循环复用。
    调用时机：run.py 的 asyncio.run 收尾、api/main.py 的 lifespan shutdown。
    """
    if get_checkpointer.cache_info().currsize:
        await get_checkpointer().conn.close()
        get_checkpointer.cache_clear()


def kb_filter(user_id: int | None) -> dict | None:
    """检索范围：预置官方文档全局共享 + 本人上传件。

    预置文档在 ingest.py 里统一打了 `kb="langchain_docs"`，**没有 `origin` 字段**
    （`upload_function_tools.py:12-16` 与 `docs/api/README.md` 都明写了这一点）。
    用 `kb` 而不是 `origin` 区分两类文档是本设计的正确做法——若写成
    `{"$or":[{"origin":"builtin"}, ...]}`，88 篇官方文档会全部从检索结果里消失，
    且**不报任何错**（问答质量断崖下跌但无人察觉）。
    tests/test_graph.py::test_kb_filter_never_uses_origin_builtin 钉住了这一点。

    ⚠ Chroma 的 $and / $or **至少要两个子条件**，单条件包一层会抛 ValueError
      （upload_function_tools.py:17-18、项目 README:438）。所以无 user_id 时
      直接返回裸条件，不能写 {"$or": [{"kb": BUILTIN_KB}]}。

    ⚠ BUILTIN_KB 必须**函数内 import**：upload_function_tools 反过来 import 本模块的
      get_vectorstore（它 :29），模块顶层 import 会立刻成环——实测后果是
      「cannot import name 'get_vectorstore' from partially initialized module」，
      5 个测试文件连收集都失败。
    """
    from backend.tools.upload_function_tools import BUILTIN_KB

    if user_id is None:  # CLI run.py 等无登录态的调用方：只检索预置文档
        return {"kb": BUILTIN_KB}
    return {"$or": [{"kb": BUILTIN_KB}, {"user_id": user_id}]}


# ---------- 提示词 ----------
# generate 的角色与规则：**稳定不变**（不掺本轮资料），这样 system 部分跨轮一致；
# 资料改放到当前轮 HumanMessage 里——离问题更近，注意力不会被历史对话稀释。
_GENERATE_RULES = """你是 LangChain / LangGraph 官方文档以及上传文档的问答助手。

回答规则：
1. **优先**依据本轮「资料」作答，引用时用【资料N】标注编号（N 为资料前的编号），便于用户核对来源。
2. 资料不足以完整回答时，**允许**用你自己的通用知识补充，但必须做到：
   - 先给出资料能支撑的部分；
   - 再单独用一行「以下基于通用知识，未经本知识库验证：」明确分隔，之后才写补充内容；
   - 绠不给补充内容编造【资料N】，也不要把补充伪装成来自资料。
3. 资料里可能混有与问题无关的片段，需自行甄别，不要强行引用。
4. 完全没把握时直说不确定，不要编造 API、参数名或版本号。
5. 用中文回答；代码、API 名称、类名、参数名保持英文原样。
6. 优先用短段落和列表组织答案；涉及用法时给出可直接运行的最小代码示例。
7. 资料之间若有冲突，指出冲突并分别标注出处编号。"""

# 无资料兜底：知识库没命中时不再硬拒答，而是放开自由度让模型用自有知识回答，
# 但必须自我声明未经验证。前端另根据 done.grounded=false 渲染结构化警示标识（双保险）。
_UNGROUNDED_RULES = """你是 LangChain / LangGraph 以及用户上传文档的问答助手。
本轮知识库**没有检索到任何相关资料**，请依据你自己的通用知识回答，并严格遵守：

1. **第一句必须是这句声明**：“⚠ 本回答未命中知识库，基于模型通用知识，可能过时或有误，请自行核实。”
2. 声明之后空一行再开始正文，正文用 Markdown 组织。
3. 不确定的地方直说不确定，不要编造 API、参数名、版本号或配置项。
4. 用中文回答；代码、API 名称、类名、参数名保持英文原样。
5. 结尾可建议用户：换更具体的术语重试，或把相关文档上传到知识库后再问。
6. 如果问题完全超出你的能力范围，就只输出声明加上“这个问题我无法可靠回答”，不要硬答。"""


def _recent_dialogue(state: RAGState, limit: int = 4, chars: int = 200) -> str:
    """取最近 limit 条对话（每条截断到 chars），供改写时补全指代。无历史返回空串。"""
    msgs = state.get("messages") or []
    lines = []
    for m in msgs[-limit:]:
        role = "用户" if isinstance(m, HumanMessage) else "助手"
        lines.append(f"{role}: {(getattr(m, 'content', '') or '')[:chars]}")
    return "\n".join(lines)


# ---------- 节点 ----------
def _make_nodes(model, vectorstore):
    """闭包工厂：把 model / vectorstore 注入节点函数。"""

    def retrieve(state: RAGState):
        docs = vectorstore.similarity_search(state["question"], k=settings.top_k,
                                             filter=kb_filter(state.get("user_id")))
        print(f"    [retrieve] 命中 {len(docs)} 段: "
              f"{[d.metadata.get('title', '?') for d in docs]}")
        return {"documents": docs}

    def grade_documents(state: RAGState):
        grader = model.with_structured_output(GradeDocuments)
        docs = state["documents"]
        n = len(docs)
        preview = settings.grade_preview_chars
        # 编号从 1 开始（对模型比 0-based 友好）；过滤靠 zip 的位置对应，与编号基准无关
        numbered = "\n".join(
            f"[{i + 1}] {d.page_content[:preview]}" for i, d in enumerate(docs))
        result = grader.invoke(
            f"你是检索质量评审员。知识库是 LangChain / LangGraph 的英文官方文档和上传的文档。\n\n"
            f"问题：{state['question']}\n\n"
            f"候选文档共 {n} 条（编号 1..{n}，每条已截断到前 {preview} 字符）：\n"
            f"{numbered}\n\n"
            f"判定标准（宁可多留、不可误删）：\n"
            f"- True：能直接回答问题；或仅部分相关；或需与其他片段组合才能回答；"
            f"或含问题所涉及的关键概念 / API / 术语；\n"
            f"- False：讨论的是完全无关的主题。\n"
            f"片段已被截断，看不到全貌时请倾向判 True。\n\n"
            f"请按编号顺序输出 {n} 个布尔值，第 i 个对应编号为 i 的文档，不要多也不要少。")
        kept = [d for d, ok in zip(docs, result.relevance) if ok]
        print(f"    [grade] {len(kept)}/{n} 段判定为相关")
        return {"documents": kept}

    def rewrite_query(state: RAGState):
        rewriter = model.with_structured_output(RewrittenQuery)
        # 带上最近对话：多轮场景下“它怎么工作？”这类指代必须靠历史才能补全
        dialogue = _recent_dialogue(state)
        history_block = (f"\n\n最近对话（用于补全指代，如“它”“这个”）：\n{dialogue}"
                         if dialogue else "")
        attempt = state["rewrites"] + 1
        result = rewriter.invoke(
            f"你是检索查询改写器。知识库是 LangChain / LangGraph 的**英文**官方文档和上传的文档，"
            f"向量检索对英文查询命中更准。{history_block}\n\n"
            f"原始问题：{state['question']}\n\n"
            f"上一轮检索没有命中任何相关文档。请把问题改写成更适合向量检索的查询：\n"
            f"1. 输出**英文**查询；专有名词保持原样（如 checkpointer / StateGraph / tool calling）；"
            f"若原问题是中文，先译成英文再补关键词；\n"
            f"2. 展开缩写与口语表达，补上同义术语与上位概念，提高召回；\n"
            f"3. 结合最近对话补全指代，使查询自包含（脱离对话也能看懂）；\n"
            f"4. 保持疑问语义，不要变成陈述句，不要添加无关词；\n"
            f"5. 只输出改写后的查询本身，不要解释。\n\n"
            f"这是第 {attempt} 次改写（最多 {settings.max_rewrites} 次），"
            f"请比上一次更宽泛、更关键词化。")
        print(f"    [rewrite] 第 {attempt} 次重写: {result.query}")
        return {"question": result.query, "rewrites": attempt}

    def generate(state: RAGState):
        question = state["question"]
        history = state.get("messages", [])  # 之前轮次的 Human/AI 消息（首轮为 []）

        # ── 无文档兜底 ──────────────────────────────────────────────
        if not state["documents"]:
            # 不再硬拒答：真调用 model 用自有知识作答。三个好处：
            # ① 答案有实质内容；② streaming=True 下是真流式，前端逐字看到输出；
            # ③ grounded=False 写进 additional_kwargs，刷新后从 history 读回来仍能渲染警示标识。
            prompt = [SystemMessage(content=_UNGROUNDED_RULES), *history,
                      HumanMessage(content=question)]
            answer = model.invoke(prompt)
            return {
                "generation": answer.content,
                # add_messages reducer 会把这两条追加到 state["messages"]
                "messages": [HumanMessage(content=question),
                             AIMessage(content=answer.content,
                                       additional_kwargs={"grounded": False})],
            }

        # ── 正常 RAG 路径 ───────────────────────────────────────────
        # 资料编号用【资料N】：既能要求模型标注出处（契约里 sources 是有的），
        # 又不会与 grade 提示词的 [N] 编号格式混淆
        context = "\n\n".join(
            f"【资料{i + 1}·{d.metadata.get('title', '片段')}】{d.page_content}"
            for i, d in enumerate(state["documents"])
        )

        # system 只放稳定规则；资料随本轮问题一起放最后——离问题最近，且 system 跨轮不变
        prompt = [SystemMessage(content=_GENERATE_RULES), *history,
                  HumanMessage(content=f"资料：\n{context}\n\n问题：{question}")]

        answer = model.invoke(prompt)  # streaming=True → LangGraph messages-mode 自动逐 token 推送

        return {
            "generation": answer.content,
            "messages": [HumanMessage(content=question),
                         AIMessage(content=answer.content,
                                   additional_kwargs={"grounded": True})],
        }

    return retrieve, grade_documents, rewrite_query, generate


# ---------- 路由 ----------
def _decide_to_generate(state: RAGState):
    if state["documents"]:
        return "generate"
    if state["rewrites"] < settings.max_rewrites:
        return "rewrite"
    return "generate"  # 兜底：generate 无资料时会用模型自有知识作答（grounded=False）


def build_graph(model=None, vectorstore=None, checkpointer=None):
    """构建并编译 RAG 工作流。model/vectorstore/checkpointer 可注入用于测试。"""
    from langgraph.graph import END, START, StateGraph

    model = model or get_model()
    vectorstore = vectorstore or get_vectorstore()
    retrieve, grade, rewrite, generate = _make_nodes(model, vectorstore)

    b = StateGraph(RAGState)
    b.add_node("retrieve", retrieve)
    b.add_node("grade_documents", grade)
    b.add_node("rewrite_query", rewrite)
    b.add_node("generate", generate)
    b.add_edge(START, "retrieve")
    b.add_edge("retrieve", "grade_documents")
    b.add_conditional_edges(
        "grade_documents", _decide_to_generate,
        {"generate": "generate", "rewrite": "rewrite_query"},
    )
    b.add_edge("rewrite_query", "retrieve")
    b.add_edge("generate", END)
    # 默认走 AsyncSqliteSaver 单例；测试必须显式传 InMemorySaver，避免写入真实 db
    return b.compile(checkpointer=checkpointer or get_checkpointer())


def print_ascii_graph():
    """打印工作流结构图（需要 grandalf 依赖）。这里是同步上下文，
    AsyncSqliteSaver 单例构造需要运行中的事件循环，用 InMemorySaver 占位即可。"""
    graph = build_graph(checkpointer=InMemorySaver())
    print(graph.get_graph().draw_ascii())


@lru_cache(maxsize=1)
def get_graph():
    return build_graph()  # 只编译一次；checkpointer 单例跨请求保留 thread 状态


if __name__ == "__main__":
    print_ascii_graph()
