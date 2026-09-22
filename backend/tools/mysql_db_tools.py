# -*- coding: utf-8 -*-
"""MySQL 异步基础设施：引擎单例 / 会话工厂 / 会话依赖 / 建表 / 收尾释放。

本模块**只放基础设施**，不放业务模型 —— ORM 模型（User / Conversation 等）
另放 `backend/app/models.py`，理由见 `_load_base()` 的注释。

三条硬约束（都是踩过的坑）：

1. **工厂一律是同步函数。** `@lru_cache` 缓存的是「函数返回值」；加在 `async def`
   上，缓存到的就是**协程对象**本身 —— 第一次 `await` 后协程作废，第二次调用必抛
   `RuntimeError: cannot reuse already awaited coroutine`，而它还被缓存强引用着，
   永远不会被回收。好在 `create_async_engine` / `async_sessionmaker` 本身就是
   **同步构造函数**，根本不需要 `async`。
   对照实现：`backend/app/agent/graph.py` 的 `get_checkpointer()` 也是同步 `def`。

2. **不在 import 期建引擎。** 写成模块级变量等于把连接池提到进程启动，
   `.env` 没配好时整个 app 都起不来。它是 `@lru_cache` 单例，
   函数里现取的开销只是一次字典查找。

3. **收尾必须成对清缓存。** 只清引擎不清工厂，会留下「引擎已 dispose、
   工厂仍持有旧引擎」的半残状态。对称实现见 `graph.py` 的 `aclose_checkpointer()`。

口令不进源码：连接串由 `config.settings.mysql_database_url` 提供，
值来自 `config._find_env_file()` 定位到的 `.env`（独立仓库即项目根的 `.env`；
monorepo 布局下是上两级那一份）里的 `RAGQA_MYSQL_DATABASE_URL`；`.gitignore` 已覆盖 `.env`。
"""
from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings

import asyncio
from collections.abc import AsyncGenerator
from functools import lru_cache



def _mysql_url() -> str:
    """取连接串；缺失时**快速失败**并点名该配哪个环境变量。

    默认值是空串（正是为了不让口令进源码），所以这里必须显式校验：
    否则 `create_async_engine("")` 只会抛一句难以定位的 URL 解析异常，
    而 `.env` 里写了 `KEY=` 却漏填值是极常见的手误。
    """
    url = (settings.mysql_database_url or "").strip()
    if not url:
        raise RuntimeError(
            "MySQL 连接串未配置：请在 .env（rag_qa_project 的上一级，即仓库根）里设置 "
            "RAGQA_MYSQL_DATABASE_URL=mysql+aiomysql://<user>:<pwd>@<host>:3306/<db>?charset=utf8mb4"
            "（口令含 @ / : / # 等保留字符时必须 URL 编码，"
            "如 @ 写成 %40；或用 sqlalchemy.URL.create 让驱动代为转义）"
        )
    return url


# ---------------------------------------------------------------- 单例工厂
@lru_cache(maxsize=1)
def get_db_engine() -> AsyncEngine:
    """引擎单例。**同步**工厂，返回真正的 AsyncEngine 对象（不是协程）。

    各参数的理由：
    - `echo=settings.sql_echo`：默认关。本项目是 SSE 流式问答，每个请求都跑
      LLM + 检索，全量打印 SQL 会淹没应用日志并拖慢吞吐；
    - `pool_pre_ping=True`：单例引擎活到进程结束，池化连接空闲超过 MySQL 的
      `wait_timeout` 会被服务端单方面掐断 —— 不开这个，「隔夜后第一个请求」
      必报 `MySQL server has gone away (2006)`，排查成本极高；
    - `pool_recycle=3600`：按秒兜底回收，与 pre_ping 双保险；
    - 刻意不写 `future=True`：SQLAlchemy 2.0 里它是纯粹的向后兼容参数
      （官方文档：must remain at its default value of True，将于后续 2.x 废弃）。
    """
    return create_async_engine(
        _mysql_url(),
        echo=settings.sql_echo,
        pool_pre_ping=True,
        pool_recycle=3600,
    )


@lru_cache(maxsize=1)
def get_db_session_maker() -> async_sessionmaker[AsyncSession]:
    """会话工厂单例。**同步**工厂，且必须绑定 `get_db_engine()` 的引擎。

    另建一个引擎就会开出第二份连接池，池子数量与连接数都翻倍。
    `expire_on_commit=False`：提交后不过期，允许在事务提交之后继续读实体属性；
    否则响应序列化时会触发一次隐式懒加载，而那时会话可能已经关闭。
    """
    return async_sessionmaker(
        get_db_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
    )


# ---------------------------------------------------------------- 依赖注入
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：一个请求一个会话。

    消费方式（二选一）：

        # 1) 依赖注入（推荐）
        async def endpoint(session: AsyncSession = Depends(get_db_session)): ...

        # 2) 异步迭代
        async for session in get_db_session(): ...

    **不能**写成 `await get_db_session()` —— 它是异步生成器，
    `await` 拿到的是生成器对象，不是 session。

    这里不手写 `try/finally: await session.close()`：`AsyncSession` 的
    `__aexit__` 已经负责 close 并回滚未提交事务，多写一层是双重关闭。
    事务边界（commit / rollback）由调用方按业务语义决定，本函数只保证
    「无论正常结束还是异常退出，连接一定归还连接池」。
    """
    async with get_db_session_maker()() as session:
        yield session


# ---------------------------------------------------------------- 建表
def _load_base() -> type[DeclarativeBase]:
    """取出 SQLAlchemy 声明式基类 `Base`。

    刻意写成函数内局部导入，有两个理由：

    1. **注册顺序**：`Base.metadata.create_all` 只创建**已注册到该 metadata**
       的表。若模型模块从未被 import，它会静默建 0 张表、看起来还「执行成功」，
       等到第一个查询才报 `no such table`。在这里现取 Base，
       等于强制模型模块先被导入。
    2. **可测性 / 可诊断**：模型尚未建立时给出带中文指引的 `RuntimeError`，
       而不是把难懂的 `ModuleNotFoundError` 直接抛给调用方，
       同时顺带指明了 `Base` 该定义在哪里。
    """
    try:
        from backend.app.models import Base
    except ImportError as exc:
        raise RuntimeError(
            "找不到 ORM 基类 Base：请创建 backend/app/models.py，在其中定义并导出 "
            "Base（DeclarativeBase 子类）与全部 ORM 模型，再调用 init_db()。"
            "若该文件已存在，请检查它内部的导入是否出错（同样会走到这里）。"
            f"原始原因：{type(exc).__name__}: {exc}"
        ) from exc
    return Base


async def init_db() -> None:
    """创建缺失的表（幂等）。

    ⚠ 只建表、**不改表结构**：改字段仍需手工 ALTER（本项目未引入迁移框架）。
    `run_sync` 让同步 DDL 跑在线程池里，不占事件循环。
    """
    Base = _load_base()
    tables = sorted(Base.metadata.tables)
    if not tables:
        # 模型全都没注册进 metadata 时，create_all 会「成功」地什么都不建 ——
        # 这是最难查的失败模式（要等第一个查询才报 no such table），宁可在这里大声失败。
        raise RuntimeError(
            "Base.metadata 中没有任何表：请确认 backend/app/models.py 里的 ORM 模型"
            "都继承自同一个 Base。"
        )
    async with get_db_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # 启动时只跑一次；冒一行日志让「到底建了哪些表」可核对（沿用项目既有的 print 风格）
    print(f"[db] create_all 完成，表：{tables}")


# ---------------------------------------------------------------- 收尾释放
async def aclose_db() -> None:
    """进程收尾：释放连接池并**成对**清空两个单例缓存。

    与 `graph.aclose_checkpointer()` 对称：异步驱动同样持有非守护线程 / 连接句柄，
    不释放会让 uvicorn Ctrl+C 后进程卡住；而且连接绑定在创建它的那个事件循环上，
    跨事件循环复用会报隐晦的错 —— 清缓存正是为此。

    调用方（两处，都必须显式调）：
    - `api/main.py` 的 lifespan shutdown，与 `aclose_checkpointer()` 并列；
    - `scripts/migrate_kb_user_id.py` —— 它用 `asyncio.run()` 建连接，循环一结束
      连接就成了跨循环的悬空句柄，随后在 `__del__` 里 close 会抛
      `RuntimeError: Event loop is closed`。

    从未构造过单例时静默返回；重复调用安全。
    """
    if get_db_engine.cache_info().currsize:
        engine = get_db_engine()
        try:
            await engine.dispose()
        finally:
            # 即便 dispose 抛异常也照清：否则会留下「引擎半释放、两个缓存仍非空」的状态
            get_db_engine.cache_clear()
    # 工厂无条件清：它内部持有 get_db_engine() 的返回值（可能已是 dispose 掉的旧引擎）
    get_db_session_maker.cache_clear()

if __name__ == "__main__":
    # 手动建表入口：python backend/tools/mysql_db_tools.py
    async def _main() -> None:
        try:
            await init_db()
        finally:
            await aclose_db()

    asyncio.run(_main())