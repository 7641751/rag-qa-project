"""
chat_service 模块包含聊天相关的服务。

"""
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk
from langchain_core.runnables import RunnableConfig
import json
import logging

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from backend.app.agent.schemas import (
    ChatRequest, RAGState, HistoryResponse, HistoryMessage, ChatDeleteResponse,
    ConversationSummary, ThreadListResponse, RenameResponse,
)
from backend.app.agent.graph import RetrievalError, build_graph, get_graph
from backend.app.services import conversation_service
from backend.tools.redis_cache_tools import cached_json, conv_list_key, invalidate

logger = logging.getLogger(__name__)

LABELS = {"retrieve": "检索", "grade_documents": "评分",
          "rewrite_query": "重写", "generate": "生成"}

# 缓存载荷里每个会话项必须具备的字段，与 ConversationSummary 的必填字段一一对应。
# ⚠ 两者没有类型层面的同步机制：给 ConversationSummary 加必填字段时这份清单会**悄悄落后**
# （落后不报错，代价是「新字段缺失的载荷」又能穿过去直捣 `ConversationSummary(**)` 拿 500）。
# 兜底机制是 tests/test_chat_threads_cache.py::test_required_fields_match_schema —— 加字段时它会红。
_REQUIRED_THREAD_FIELDS = ("thread_id", "title", "created_at", "updated_at")


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
                        # 改写只产出 search_query（不再覆写 question），见 graph.py 的说明
                        detail = f"重写为: {delta.get('search_query', '')[:30]}"
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
    except RetrievalError as exc:
        # 检索层失败 ≠ LLM 失败。两者都报 LLM_ERROR 会把排查方向带偏 ——
        # 用户看到「LLM 错误」会去查模型与额度，而真正坏的是向量库。
        # 零契约变更：INTERNAL_ERROR 本就在错误码表里，前端按 message 展示即可。
        yield sse("error", {"code": "INTERNAL_ERROR", "message": f"检索失败：{exc}"})
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

async def list_chat_threads(user_id: int, db: AsyncSession,
                            redis=None) -> ThreadListResponse:
    """当前用户的会话列表（对齐契约 §7.1），带 Redis 读缓存（P4）。

    排序 / 上限 / total 口径仍在 `conversation_service.list_conversations` 里，
    本层只加缓存与「ORM/JSON → 契约模型」的搬运，不改查询本身。

    缓存载荷结构 `{"user_id": N, "total": M, "threads": [...]}`：
      · `user_id` 用于**自校验** —— 键串了/被塞了值时当 miss，绝不返回别人的数据；
      · `total` 必须一起缓存，它是前端「仅显示最近 50 条」的唯一依据（P3 §7.1）。
      · 时间转成 ISO 字符串存：`datetime` 进不了 JSON，且契约侧本来就要求 ISO 8601。

    `redis=None` ⇒ 无缓存路径，行为与 P3 逐字一致（未配置 Redis 或开关关闭时就是这样）。
    授权不在这里：`assert_owner` 的调用链上没有任何 redis 参数（P4 设计文档 §7）。
    """
    async def _load() -> dict:
        # 回源就是 P3 那段原封不动的查询 + 搬运，缓存只在它外面套一层
        rows, total = await conversation_service.list_conversations(db, user_id)
        return {"user_id": user_id, "total": total,
                "threads": [{"thread_id": r.thread_id, "title": r.title,
                             "created_at": r.created_at.isoformat(),
                             "updated_at": r.updated_at.isoformat()} for r in rows]}

    key = conv_list_key(user_id)
    # 注意：`_load` 里的 MySQL 异常由 cached_json 原样上抛（缓存层只兜自己的错），
    # 行为与 P3 一致 —— 库挂了就是 500，不会被伪装成「降级成功」而返回空列表。
    payload = await cached_json(redis, key, settings.conv_cache_ttl, _load)

    # ── 第一层（廉价前置）：归属 / 顶层结构 / 元素字段齐不齐 ──
    # 它守的是 pydantic **守不住**的那两条设计意图（见 `_is_valid_payload` docstring）：
    # `user_id` 归属精确匹配、`total` 必须是「真 int」。所以这一层不能换成 try/except。
    if not _is_valid_payload(payload, user_id):
        logger.error("[cache] 载荷归属或结构异常，丢弃 key=%s", key)
        await invalidate(redis, key)
        # 刻意**不回填**：回填要多一条写路径、多一处可能把坏值重新写进去的代码；
        # 代价仅是「下一次请求 miss 一次、回源 1ms」（P4 §2 实测，索引扫描）。
        return _to_response(await _load())

    # ── 第二层（权威兜底）：形状以 pydantic 为准 ──
    # 字段齐全但**类型/可解析性**不符（实测 `created_at:"not-a-date"`、`title:{"a":1}`）会
    # 通过上面的廉价前置，却在 `_to_response` 里抛 ValidationError —— 它继承 ValueError，
    # errors.py 的两个 handler 都不接，于是落到 Starlette 默认 500，且**坏键不被删**
    # （无 TTL 时该用户的列表接口会永久 500 直到人工清理）。元素级校验替代不了这一层：
    # pydantic v2 宽松模式会把 "1"/True 强转成 int(1)，判据宽严不同、互补而非重复。
    try:
        return _to_response(payload)
    except (ValidationError, TypeError, KeyError):
        # ⚠ 只包 `_to_response(payload)`，**不包**下面的回源：回源结果若也不合法，那是我们
        # 自己的 bug，应该照常 500 炸出来，不能被这里伪装成「缓存故障」而静默。
        logger.error("[cache] 载荷无法构成契约响应，丢弃 key=%s", key)
        await invalidate(redis, key)
        return _to_response(await _load())


def _to_response(payload: dict) -> ThreadListResponse:
    """缓存载荷 → 契约响应。缓存里的坏值在这里暴露成 pydantic 校验错误，由调用方兜底。"""
    return ThreadListResponse(
        threads=[ConversationSummary(**t) for t in payload["threads"]],
        total=payload["total"])


def _is_valid_payload(payload, user_id: int) -> bool:
    """缓存载荷自校验：归属正确 + `total` 是**真 int** + `threads` 每项四个必填字段齐全。

    写成纯函数是为了能脱离 DB/Redis 单测它 —— 这条判据一旦写漏，就得靠线上 500 才发现。
    （调用方还有一层 try/except 以 pydantic 为准兜底，但那一层兜不住本函数守的两条意图，
    见下。）

    它**不可**被「交给 pydantic 兜」替代，两处设计意图 pydantic 宽松模式都不执行：
      · 归属：schema 里根本没有 `user_id` 字段，pydantic 无从判「这是不是本人的数据」；
      · `total` 必须是真 int：pydantic 会把 `"1"`、`True` 强转成 1，而它是前端判断
        「结果被截断」的唯一依据（P3 §7.1），被强转就等于把「截断了」说成「就这么少」。
    所以 `isinstance(x, int)` 在这里**不够**（`isinstance(True, int) is True`），
    必须用 `type(x) is int`。这也是为什么第二层 try/except 不能取代本函数。
    """
    if not isinstance(payload, dict) or payload.get("user_id") != user_id:
        return False
    if type(payload.get("total")) is not int:
        return False
    threads = payload.get("threads")
    if not isinstance(threads, list):
        return False
    return all(isinstance(t, dict) and all(f in t for f in _REQUIRED_THREAD_FIELDS)
               for t in threads)


async def rename_chat_thread(thread_id: str, user_id: int, title: str,
                             db: AsyncSession) -> RenameResponse:
    """重命名会话。403（非本人）与 404（不存在）由服务层 api_error 抛出，
    路由不加 try —— 异常经 errors.py 的处理器统一转成契约错误体。"""
    row = await conversation_service.rename_conversation(db, thread_id, user_id, title)
    return RenameResponse(thread_id=row.thread_id, title=row.title)


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