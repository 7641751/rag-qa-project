"""
chat_service 模块包含聊天相关的服务。

"""
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk
from langchain_core.runnables import RunnableConfig
import json

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.agent.schemas import ChatRequest, RAGState, HistoryResponse, HistoryMessage, ChatDeleteResponse
from backend.app.agent.graph import build_graph, get_graph
from backend.app.services import conversation_service

LABELS = {"retrieve": "检索", "grade_documents": "评分",
          "rewrite_query": "重写", "generate": "生成"}


async def stream_chat(chat_request: ChatRequest, user_id: int | None = None):
    graph = get_graph()
    # user_id 必须进 state：retrieve 节点靠它生成本轮检索的 filter（kb_filter，P3）。
    # 显式写入 None 而不是省略该键 —— 便于日后再排查「这次到底注入的是什么」，
    # 且 RAGState 是 TypedDict、两者取值行为相同，成本为零。
    state = {"question": chat_request.question, "documents": [],
             "generation": "", "rewrites": 0, "user_id": user_id}
    config = {"configurable": {"thread_id": chat_request.thread_id}}
    final_docs, rewrites, gen_step_sent, gen_text = [], 0, False, ""
    try:
        async for mode, chunk in graph.astream(
                state, config, stream_mode=["updates", "messages"]):
            if mode == "updates":
                for node, delta in chunk.items():
                    if node == "generate":
                        # 接住兜底文本：正常路径的 token 已由 messages 流逐字推完（本 update 在 token 之后，
                        # 跳过避免重复发 step）；兜底分支调用 model →
                        gen_text = delta.get("generation", "") or gen_text
                        continue
                    detail = None
                    if node == "retrieve":
                        detail = f"召回 {len(delta.get('documents', []))} 段"
                    elif node == "grade_documents":
                        final_docs = delta.get("documents", final_docs)
                        detail = f"保留 {len(final_docs)} 段相关"
                    elif node == "rewrite_query":
                        rewrites = delta.get("rewrites", rewrites)
                        detail = f"重写为: {delta.get('question', '')[:30]}"
                    yield sse("step", {"node": node, "label": LABELS.get(node, node), "detail": detail})
            elif mode == "messages":
                msg, meta = chunk
                # 关键：只放行 generate 节点的 token，过滤掉 grade/rewrite 的 JSON 结构化输出
                if (meta.get("langgraph_node") == "generate" and
                        getattr(msg, "content", "")
                        and isinstance(msg, AIMessageChunk)
                        and msg.content):
                    if not gen_step_sent:
                        yield sse("step", {"node": "generate", "label": "生成", "detail": "开始作答"})
                        gen_step_sent = True
                    yield sse("token", {"text": msg.content})
        # 兜底补发：全程没推过任何 token（gen_step_sent 为 False）却拿到了 generation，
        # 说明走的是「无文档兜底」分支——按契约补一个 step(generate) + 一帧 token，
        # 保证事件序始终是 …step → token → sources → done，前端不会收到空答案。
        if not gen_step_sent and gen_text:
            yield sse("step", {"node": "generate", "label": "生成", "detail": "开始作答"})
            yield sse("token", {"text": gen_text})
        yield sse("sources", {"sources": [_to_source(d) for d in final_docs]})
        # grounded 用 bool(final_docs) 确定性得出：grade 后还留着资料 = 有知识库依据。

        yield sse("done", {"thread_id": chat_request.thread_id, "rewrites": rewrites,
                           "grounded": bool(final_docs)})
    except Exception as exc:
        yield sse("error", {"code": "LLM_ERROR", "message": f"{type(exc).__name__}: {exc}"})


def sse(event: str, data: dict) -> str:
    # ensure_ascii=False 让中文可读；\n\n 结束一帧
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _to_source(doc) -> dict:
    md = getattr(doc, "metadata", {}) or {}
    return {"title": md.get("title") or md.get("source") or "片段",
            "snippet": (getattr(doc, "page_content", "") or "")[:80],
            "score": None}  # as_retriever 无分数，契约里 score 可为 null


async def get_chat_history(thread_id: str, user_id: int, db: AsyncSession) -> HistoryResponse:
    # 归属校验**前置**：先判「能不能看」，再去读 checkpointer。反过来会让「读失败」
    # 抛出 500 而不是 403，攻击者就能靠状态码差异判断 thread_id 是否存在。
    # 无行 = 新会话，assert_owner 会放行（spec §7.5）。
    await conversation_service.assert_owner(db, thread_id, user_id)
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    # AsyncSqliteSaver 原生异步，直接 await；不再需要 to_thread 绕线程池
    state = await graph.aget_state(config)
    messages = (state.values or {}).get("messages", [])
    out = [h for h in (_msg_to_history(m) for m in messages) if h is not None]
    return HistoryResponse(thread_id=thread_id, messages=out)


def _msg_to_history(m):
    """LangChain 消息 → 契约 {role, content, sources?, grounded?}；非 Human/AI 跳过。"""
    if isinstance(m, HumanMessage):
        return HistoryMessage(role="user", content=m.content)
    if isinstance(m, AIMessage):
        ak = getattr(m, "additional_kwargs", {}) or {}
        # grounded 由 generate 节点写入：False = 该回答无知识库依据，
        # 持久化后刷新页面仍能渲染警示标识（不存就会让无依据答案看起来和有依据的一样）
        return HistoryMessage(role="assistant", content=m.content,
                              sources=ak.get("sources"), grounded=ak.get("grounded"))
    return None  # SystemMessage 等不进历史


async def delete_chat_thread(thread_id: str, user_id: int,
                             db: AsyncSession) -> ChatDeleteResponse:
    """删除一个会话的全部服务端状态：checkpoints + writes + 归属索引行。

    幂等：不存在也返回 deleted=False。归属校验在前（非本人 → 403，不产生任何副作用）。
    """
    await conversation_service.assert_owner(db, thread_id, user_id)
    checkpointer = get_graph().checkpointer
    config = {"configurable": {"thread_id": thread_id}}

    # adelete_thread 返回 None，拿不到「是否真删了」，所以先探测存在性。
    # 用 alist(limit=1) 只取最新一条判断有无。先探测还有个附带作用：alist 内部会惰性
    # setup 建表，避免「从未写过任何会话的新库」上直接 DELETE 报 no such table。
    # 显式 aclose：只取一条就中断，挂起的生成器仍持有 saver 锁，等 GC 才释放会卡并发写入。
    agen = checkpointer.alist(config, limit=1)
    try:
        existed = await anext(agen, None) is not None
    finally:
        await agen.aclose()

    await checkpointer.adelete_thread(thread_id)
    # 索引行也要删（spec §7.6 第 3 步）：只删 checkpointer 会留下孤儿行，而 P3 的会话
    # 列表正是查这张表 —— 孤儿行会让已删会话重新出现在侧栏，点进去又是空的。
    # 顺序刻意是「先删不可逆的 checkpoint、再删可重建的索引行」：万一中途失败，结果是
    # 「列表里已消失但历史还在」（再删一次即可），比留下孤儿行好排查。
    await conversation_service.delete_conversation_row(db, thread_id)
    await db.commit()
    return ChatDeleteResponse(thread_id=thread_id, deleted=existed)

async def prepare_stream(chat_request: ChatRequest, user_id: int,
                         db: AsyncSession):
    """鉴权 + upsert 完成后，把流生成器交给路由。

    必须与 stream_chat 分开：`403` 要在返回 StreamingResponse **之前**抛出，
    一旦响应头发出就只能降级成 SSE error 帧，前端得为同一端点写两套错误处理。

    返回的是异步生成器（不是协程），路由直接交给 StreamingResponse。
    """
    await conversation_service.assert_owner(db, chat_request.thread_id, user_id)
    await conversation_service.upsert_conversation(
        db, thread_id=chat_request.thread_id, user_id=user_id,
        question=chat_request.question)
    await db.commit()
    return stream_chat(chat_request, user_id)