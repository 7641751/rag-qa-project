# -*- coding: utf-8 -*-
"""rag_qa_project 核心工作流（CRAG 简化版，LangGraph 实现）。

流程：
    START -> retrieve -> decide_after_retrieve
        decide_after_retrieve: 有召回          -> grade_documents -> decide_to_generate
                               无召回且可改写   -> rewrite_query -> retrieve（循环）
                               无召回且改写用尽 -> generate（兜底）
    decide_to_generate: 有相关文档    -> generate -> END
                        无相关且可重写 -> rewrite_query -> retrieve（循环）
                        重写次数用尽   -> generate（无资料：用模型自有知识作答并自我声明）

设计要点：
- build_graph(model=None, vectorstore=None, checkpointer=None) 支持依赖注入，
  测试时可传入 FakeChatModel / 内存向量库，零 API 额度跑通全图。
- 每个节点是纯函数：输入状态 -> 输出增量更新。
- **改写只产出 `search_query`，绝不覆写 `question`**：前者是检索手段，后者是用户原话。
  覆写 question 会同时污染两处 —— 历史里记的用户提问变成英文改写查询（刷新后可见），
  且第 2 次改写的「原始问题」会变成第 1 次的输出（关键词漂移叠加）。
- **空召回不走 grade**：0 条候选时它的提示词会退化成「编号 1..0 / 输出 0 个布尔值」，
  结构化输出拿不到合法 JSON，整轮以报错结束，用户反而拿不到兜底答案。
- **改写的触发门槛由 `settings.grade_strict` 决定**：False（默认）时「保留数不到召回数
  一半」就改写；True 时只有「一条都没留下」才改写。grade 的判定标准是「宁可多留、
  不可误删」，所以在 True 之下改写几乎永不触发，纠错回路形同虚设。
"""
import logging
from functools import lru_cache

import aiosqlite
from langchain_chroma import Chroma
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from backend.app.agent.schemas import RAGState, GradeDocuments, RewrittenQuery
from config import settings

logger = logging.getLogger(__name__)


class RetrievalError(RuntimeError):
    """检索层失败（向量库读不出来、Chroma 打不开、嵌入服务不可用…）。

    存在的唯一目的：让上层能把「检索失败」与「LLM 失败」分开报。
    两者都归成 LLM_ERROR 时，用户会去查模型与额度，而真正坏的是向量库。
    """


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

# grade 的判定标准：稳定不变。**提到模块级**而不是埋在节点函数体里 ——
# 这是整套图里最常被调的判据（它直接决定改写触发与否），集中后才便于对比与复用。
_GRADE_RULES = """你是检索质量评审员。知识库是 LangChain / LangGraph 的英文官方文档和上传的文档。

判定标准（宁可多留、不可误删）：
- True：能直接回答问题；或仅部分相关；或需与其他片段组合才能回答；或含问题所涉及的关键概念 / API / 术语；
- False：讨论的是完全无关的主题。
片段已被截断，看不到全貌时请倾向判 True。"""

# rewrite 的改写规则：同样提到模块级。
_REWRITE_RULES = """你是检索查询改写器。知识库是 LangChain / LangGraph 的**英文**官方文档和上传的文档，向量检索对英文查询命中更准。

上一轮检索没有找到足够相关的文档。请把问题改写成更适合向量检索的查询：
1. 输出**英文**查询；专有名词保持原样（如 checkpointer / StateGraph / tool calling）；若原问题是中文，先译成英文再补关键词；
2. 展开缩写与口语表达，补上同义术语与上位概念，提高召回；
3. 结合最近对话补全指代，使查询自包含（脱离对话也能看懂）；
4. 保持疑问语义，不要变成陈述句，不要添加无关词；
5. 只输出改写后的查询本身，不要解释。"""


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
        # 有改写就用改写后的查询（向量检索享受改写红利），否则用用户原话。
        query = state.get("search_query") or state["question"]
        try:
            docs = vectorstore.similarity_search(query, k=settings.top_k,
                                                 filter=kb_filter(state.get("user_id")))
        except Exception as exc:
            # 包成 RetrievalError，让上层能把它与 LLM 失败分开报（否则用户看到
            # 「LLM 错误」会去查模型和额度，而真正坏的是向量库）。
            raise RetrievalError(f"{type(exc).__name__}: {exc}") from exc
        print(f"    [retrieve] 命中 {len(docs)} 段: "
              f"{[d.metadata.get('title', '?') for d in docs]}")
        # retrieved_count 单独记：documents 会被 grade 覆盖成「保留」的子集，
        # 覆盖后就无从判断「召回了几条、留下几条」——而那正是 grade_strict 的门槛依据。
        return {"documents": docs, "retrieved_count": len(docs)}

    def grade_documents(state: RAGState):
        docs = state["documents"]
        # 0 条候选直接短路，**不去构造那份退化提示词**：向模型要「0 个布尔值」时
        # 结构化输出不可靠（实测会拿到非 JSON → JSONDecodeError → 异常冒泡成 SSE
        # error），结果反而是用户拿不到本该有的「未命中知识库」兜底答案。
        # 条件边已保证正常路径不会走到这里；节点自保一层，免得日后改拓扑的人重踩。
        if not docs:
            return {"documents": []}

        grader = model.with_structured_output(GradeDocuments)
        n = len(docs)
        preview = settings.grade_preview_chars
        # 编号从 1 开始（对模型比 0-based 友好）；过滤靠 zip 的位置对应，与编号基准无关
        numbered = "\n".join(
            f"[{i + 1}] {d.page_content[:preview]}" for i, d in enumerate(docs))
        # 判据用**用户原话**（该不该留下，最终由用户的需求决定）；发生过改写时把
        # 检索查询一并给出，让判官知道「这些片段是凭什么被召回的」。
        search = state.get("search_query")
        query_block = (f"问题：{state['question']}\n检索查询：{search}"
                       if search else f"问题：{state['question']}")
        result = grader.invoke(
            f"{_GRADE_RULES}\n\n{query_block}\n\n"
            f"候选文档共 {n} 条（编号 1..{n}，每条已截断到前 {preview} 字符）：\n"
            f"{numbered}\n\n"
            f"请按编号顺序输出 {n} 个布尔值，第 i 个对应编号为 i 的文档，不要多也不要少。")

        # ⚠ 判定数必须与候选数对齐后再配对。直接用 zip 会**按短的截断** ——
        # 模型少返一个判定，末尾文档就被当成「不相关」静默丢掉，而日志只显示
        # 「k/n 段相关」，看不出是判为不相关还是模型少返了。取向与提示词的
        # 「宁可多留、不可误删」一致：缺失的判定补 True（保守保留）。
        flags = list(result.relevance)
        if len(flags) != n:
            logger.warning("[grade] 判定数 %d != 候选数 %d，按「宁可多留」补齐/截断为 %d",
                           len(flags), n, n)
            flags = (flags + [True] * n)[:n]
        kept = [d for d, ok in zip(docs, flags) if ok]
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
            f"{_REWRITE_RULES}{history_block}\n\n"
            f"原始问题：{state['question']}\n\n"
            f"这是第 {attempt} 次改写（最多 {settings.max_rewrites} 次），"
            f"请比上一次更宽泛、更关键词化。")
        print(f"    [rewrite] 第 {attempt} 次重写: {result.query}")
        # ⚠ 只写 search_query，**不碰 question**。question 是用户原话，它有两个下游
        # 用途：① generate 拿它作为本轮 HumanMessage 存进 messages（覆写会让历史里
        # 的提问变成英文改写查询，刷新后用户可见）；② 本函数下一次改写的「原始问题」
        # （覆写会让第 2 次拿到第 1 次的输出，关键词越滚越偏）。
        return {"search_query": result.query, "rewrites": attempt}

    # ── 两条作答路径各自成函数（⑧ 的落地方式，见下方说明）─────────────────
    # 原先把它们都塞在 `generate` 里、靠 `if not state["documents"]` 分叉。
    # ⚠ 为什么**不拆成两个 LangGraph 节点**：
    #   ① `chat_service.py:55` 用 `meta["langgraph_node"] == "generate"` 过滤流式 token，
    #      拆节点会让兜底路径的 token 被整条丢掉（除非同时改那处过滤）；
    #   ② 拆分原本的收益是「兜底话术可以单独调」—— 但两份提示词**早已是模块级常量**
    #      （_GENERATE_RULES / _UNGROUNDED_RULES），这条收益并不存在。
    #   所以：提成命名函数拿可读性，不付「新增用户可见节点名」的契约代价。
    def _answer_without_kb(question: str, history: list):
        """无资料兜底：不硬拒答，真调 model 用自有知识作答，并自我声明未经验证。

        三个好处：① 答案有实质内容；② streaming=True 下是真流式，前端逐字看到输出；
        ③ grounded=False 写进 additional_kwargs，刷新后从 history 读回来仍能渲染警示标识。
        """
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

    def _answer_from_context(documents: list, question: str, history: list):
        """正常 RAG 路径：把资料拼成带编号的上下文，要求模型标注出处。"""
        # 资料编号用【资料N】：既能要求模型标注出处（契约里 sources 是有的），
        # 又不会与 grade 提示词的 [N] 编号格式混淆
        context = "\n\n".join(
            f"【资料{i + 1}·{d.metadata.get('title', '片段')}】{d.page_content}"
            for i, d in enumerate(documents)
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

    def generate(state: RAGState):
        """按「有没有留下资料」分发到两条作答路径。"""
        question = state["question"]
        history = state.get("messages", [])  # 之前轮次的 Human/AI 消息（首轮为 []）
        if not state["documents"]:
            return _answer_without_kb(question, history)
        return _answer_from_context(state["documents"], question, history)

    return retrieve, grade_documents, rewrite_query, generate


# ---------- 路由 ----------
def _decide_after_retrieve(state: RAGState):
    """retrieve 之后的分流：有召回才值得花一次 LLM 去评；一条都没召回就直接改写。

    这省下的不只是一次调用（有改写时最多省 max_rewrites 次）：0 条候选时 grade 会
    构造一份退化提示词（「候选文档共 0 条（编号 1..0）…请输出 0 个布尔值」），
    with_structured_output 拿不到合法 JSON，整轮以 SSE error 结束 ——
    **用户于是拿不到本该有的「未命中知识库」兜底答案**。
    """
    if state["documents"]:
        return "grade_documents"
    return "rewrite_query" if state["rewrites"] < settings.max_rewrites else "generate"


def _decide_to_generate(state: RAGState):
    """grade 之后：留下的够不够用来回答？不够且还能改写就再改写一轮。

    「够不够」的判据由 `settings.grade_strict` 决定（config.py 里写好的语义）：
    - `True` ：只要留下**一条**就算够。改编写几乎永不触发 —— grade 的判定标准是
      「宁可多留、不可误删」，两者叠加的结果是「召回质量差但非零」时没有任何
      自我纠正的机会。
    - `False`（默认）：保留数**不到召回数一半**就不够，值得再改写一轮。

    两种模式下「一条都没留下」都必须改写 —— 那是原实现就有的、且必须保留的行为。
    """
    kept = len(state["documents"])
    if kept == 0:
        enough = False
    elif settings.grade_strict:
        enough = True
    else:
        enough = kept * 2 >= (state.get("retrieved_count") or 0)

    if enough:
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
    # retrieve 之后不是无条件进 grade：空召回直接改写，省掉一次注定失败的 LLM 调用
    b.add_conditional_edges(
        "retrieve", _decide_after_retrieve,
        {"grade_documents": "grade_documents",
         "rewrite_query": "rewrite_query",
         "generate": "generate"},
    )
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
