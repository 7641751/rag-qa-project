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


def build_redis_pool(redis_url: str | None) -> aioredis.ConnectionPool | None:
    """建 Redis 连接池；未配置返回 None，**超时是必填项、不是调优项**。

    · 未配置**不能**无条件 from_url("")：会抛
      ValueError: Redis URL must specify one of the following schemes (redis://, ...)
      那会让整个后端起不来。Redis 是可选依赖，缺它不该拖垮启动 ——
      未配置就返回 None，把可操作的报错留给真正用到它的 get_redis_pool。
    · 两个超时必须显式给（实测）：Redis 主机黑洞（DROP/消失）而客户端无超时时，
      **每次连接尝试要白等 5010ms** —— 每个列表请求都被拖 5 秒才降级回源，
      「缓存故障不得升级为业务故障」的承诺就落空了。3s 封顶（实测 3008ms）与
      tests/test_redis.py 的取值一致；命令级错误（如认证失败）本就是毫秒级。
    """
    url = (redis_url or "").strip()
    if not url:
        return None
    return aioredis.ConnectionPool.from_url(url, decode_responses=True,
                                            socket_connect_timeout=3, socket_timeout=3)


def should_start_consumer(pool, cache_enabled: bool) -> bool:
    """消费端启停判据：配了 Redis **且**回滚开关开着。

    ⚠ 开关关闭 = 回滚到 P4 之前的行为（设计文档 9.2 #7「完全不碰 Redis」、12 节 #8
    「行为回到今天」）：生产端此时本就不产事件（开关关闭 ⇒ get_optional_redis 返回
    None ⇒ 不 XADD），消费端再轮询只是白碰 Redis —— 缓存与事件流必须一起停，才谈得上
    「完全」。（8 区实测用 Redis commandstats 差量验证：开关关闭 + Redis 在跑时，
    xgroup_create/xreadgroup/get/set/xadd 调用数零增长。）
    """
    return pool is not None and cache_enabled


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动：异步 checkpointer 依赖事件循环，首个请求时惰性构造，无需预热
    await init_db()
    app.state.redis_pool = build_redis_pool(settings.redis_url)
    # 问答事件消费端（P4 Task 7）：统计是可丢的派生数据 —— 只配了 Redis 才起，
    # 起不来/挂掉都不影响任何业务请求（失败由它自己吞、只记日志）。
    consumer_task = None
    if should_start_consumer(app.state.redis_pool, settings.redis_cache_enabled):
        from backend.app.services.qa_event_consumer import consume, log_consumer_crash
        from backend.tools.mysql_db_tools import get_db_session_maker
        consumer_task = asyncio.create_task(
            consume(aioredis.Redis(connection_pool=app.state.redis_pool),
                    get_db_session_maker()))
        # 意外崩溃要立刻可见：任务异常无人取时直到 GC 才可能出声，与正常关闭无从区分。
        consumer_task.add_done_callback(log_consumer_crash)
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