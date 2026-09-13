"""
chat_service 模块包含聊天相关的服务。

"""
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk
from langchain_core.runnables import RunnableConfig
import json
from backend.app.agent.schemas import ChatRequest, RAGState, HistoryResponse, HistoryMessage
from backend.app.agent.graph import build_graph, get_graph

LABELS = {"retrieve": "检索", "grade_documents": "评分",
          "rewrite_query": "重写", "generate": "生成"}


async def stream_chat(chat_request: ChatRequest):
    graph = get_graph()
    state = {"question": chat_request.question, "documents": [],
             "generation": "", "rewrites": 0}
    config = {"configurable": {"thread_id": chat_request.thread_id}}
    final_docs, rewrites, gen_step_sent, gen_text = [], 0, False, ""
    try:
        async for mode, chunk in graph.astream(
                state, config, stream_mode=["updates", "messages"]):
            if mode == "updates":
                for node, delta in chunk.items():
                    if node == "generate":
                        # 接住兜底文本：正常路径的 token 已由 messages 流逐字推完（本 update 在 token 之后，
                        # 跳过避免重复发 step）；但兜底分支不调用 model → 没有 AIMessageChunk →
                        # 一帧 token 都不会发，前端答案气泡会是空白的。先存下来，循环结束后按需补发。
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
                    yield sse("step", {"node": node, "label": LABELS[node], "detail": detail})
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
        # 不靠模型自述——模型不会可靠地告诉你它哪句是编的。
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


def get_chat_history(thread_id: str) -> HistoryResponse:
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    state = graph.get_state(config)
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
