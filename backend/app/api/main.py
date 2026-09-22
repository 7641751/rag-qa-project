import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.errors import register_error_handlers
from backend.tools.mysql_db_tools import init_db, aclose_db

from config import settings
from backend.app.agent.schemas import Health
from backend.app.api import auth_router, chat_router, kb_router
from redis import asyncio as aioredis

@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动：异步 checkpointer 依赖事件循环，首个请求时惰性构造，无需预热
    await init_db()
    # ⚠ 未配置时**不能**无条件建池：from_url("") 会抛
    #   ValueError: Redis URL must specify one of the following schemes (redis://, ...)
    #   那会让整个后端起不来。Redis 目前是可选依赖，缺它不该拖垮启动 ——
    #   未配置就置 None，把可操作的报错留给真正用到它的 get_redis_pool。
    redis_url = (settings.redis_url or "").strip()
    app.state.redis_pool = (
        aioredis.ConnectionPool.from_url(redis_url, decode_responses=True)
        if redis_url else None
    )
    # 问答事件消费端（P4 Task 7）：统计是可丢的派生数据 —— 只配了 Redis 才起，
    # 起不来/挂掉都不影响任何业务请求（失败由它自己吞、只记日志）。
    consumer_task = None
    if app.state.redis_pool is not None:
        from backend.app.services.qa_event_consumer import consume
        from backend.tools.mysql_db_tools import get_db_session_maker
        consumer_task = asyncio.create_task(
            consume(aioredis.Redis(connection_pool=app.state.redis_pool),
                    get_db_session_maker()))
    yield
    # 先停消费端、再关池（它在用池）—— 既有清理顺序（池 → 引擎 → checkpointer）不动。
    if consumer_task is not None:
        consumer_task.cancel()
        with suppress(asyncio.CancelledError):
            await consumer_task
    from backend.app.agent.graph import aclose_checkpointer
    # 与 init_db/aclose_db 成对：池也要显式关，否则那些连接会一直留在服务端，
    # 直到 Redis 的 timeout 才回收（重启几次就把 maxclients 占满）。
    if app.state.redis_pool is not None:
        await app.state.redis_pool.aclose()
    await aclose_db()  # 引擎 dispose + **成对**清空两个 lru_cache
    await aclose_checkpointer()


app = FastAPI(
    title="LangChain Knowledge Helper Backend",
    description="MVP backend for the intelligent knowledge helper.",
    version="0.1.0",
    lifespan=lifespan,
)
register_error_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:80",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", response_model=Health,
         response_model_exclude_none=True, tags=["system"])
def health_check():
    """健康检查（契约 /api/health）：轻量探活。

    kb_count 尽力读取知识库向量条数；知识库未就绪时省略该字段，
    而不是让探活失败（status 仍为 ok）。
    """
    kb_count = None
    try:
        from backend.app.agent.graph import get_vectorstore
        kb_count = get_vectorstore()._collection.count()
    except Exception:
        pass  # 知识库未入库/未就绪不应拖垮探活
    return Health(status="ok", kb_count=kb_count, model=settings.embed_model)




app.include_router(chat_router.router)
app.include_router(kb_router.router)
app.include_router(auth_router.router)