from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.errors import register_error_handlers
from backend.tools.mysql_db_tools import init_db, aclose_db

from config import settings
from backend.app.agent.schemas import Health
from backend.app.api import auth_router, chat_router, kb_router

@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动：异步 checkpointer 依赖事件循环，首个请求时惰性构造，无需预热
    await init_db()
    yield
    from backend.app.agent.graph import aclose_checkpointer
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