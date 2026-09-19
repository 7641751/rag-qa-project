# -*- coding: utf-8 -*-
"""MySQL 基础设施模块（backend/tools/mysql_db_tools.py）离线测试（零数据库依赖）。

设计思想
--------
沿用 test_graph.py / test_kb_upload.py 的路子：MySQL **不需要在线**。

- `create_async_engine` 是**惰性**的，构造引擎本身不建立任何连接，所以
  「单例 / 收尾释放 / 空连接串」这类用例给一个合法但连不通的 URL 就够；
- 真正要「会话可用」的用例，把模块级会话工厂换成 aiosqlite 内存库替身。
  内存库每条新连接都是**独立的空库**，所以两种池各有分工：
  建表用例用 `StaticPool`（全程共用同一条连接，建出的表才看得见），
  会话用例用 `AsyncAdaptedQueuePool` —— 只有它提供 `checkedout()`，
  才能断言「连接已归还池子」。

守的四条契约（对应上一轮代码评审发现的缺陷）：
1. 工厂必须是**同步**函数且返回真对象。若写成 `@lru_cache(maxsize=1) async def`，
   lru_cache 缓存到的是**协程对象**本身：第一次 await 后协程作废，`isinstance`
   断言立刻失败，第二次 `await` 抛 RuntimeError: cannot reuse already awaited
   coroutine —— 而它还被缓存强引用着，永不回收。
2. 会话依赖在**正常结束**与**异常退出**两条路径上都必须把连接还回池子，
   判据用 `engine.pool.checkedout() == 0`（比断言 Session 状态更接近真实泄漏）。
3. 收尾函数要清掉**两个** lru_cache。只清一个会留下「引擎已释放、工厂仍持有
   旧引擎」的半残状态；且在从未构造过单例时不能报错。
4. 连接串缺失时要在构造引擎前快速失败并点名环境变量，而不是把 SQLAlchemy 的
   URL 解析异常原样抛给调用方。

运行方式（在 rag_qa_project 目录下）：
    python -m pytest tests/test_mysql_db.py -v
"""
import asyncio
import inspect
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import Integer, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine, async_sessionmaker, create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import AsyncAdaptedQueuePool, StaticPool

# 让 tests/ 能导入项目模块（等价于把 rag_qa_project 标记为 Sources Root）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.tools import mysql_db_tools as mdt


# ============================ 测试替身与夹具 ============================
# 合法但连不通的 MySQL URL：create_async_engine 惰性建连，不会真的去连。
# 口令含 @ 并已用 %40 转义，顺带覆盖「URL 里有保留字符」的写法。
_DUMMY_URL = "mysql+aiomysql://u:p%40x@127.0.0.1:3306/rag_qa_test?charset=utf8mb4"

_SINGLETON_NAMES = ("get_db_engine", "get_db_session_maker")


@pytest.fixture(autouse=True)
def _clear_singletons():
    """每个用例前后都清空单例缓存。

    lru_cache 是**进程级**的，不清会让上一个用例的引擎泄漏到下一个用例，
    表现为「单例断言偶发失败」这种极难定位的问题。

    getattr 兜住「模块还是空文件」的 RED 阶段：让用例自己报
    「没有 get_db_engine」而不是在夹具里先炸掉，失败信息才有指向性。
    """
    def _sweep():
        for name in _SINGLETON_NAMES:
            fn = getattr(mdt, name, None)
            if fn is not None and hasattr(fn, "cache_clear"):
                fn.cache_clear()

    _sweep()
    yield
    _sweep()


@pytest.fixture
def mysql_url(monkeypatch):
    """给 settings 一个合法 URL。

    `.env` 尚未配置时默认值是空串，会在构造引擎前快速失败；这里显式注入，
    让用例与「本机 .env 配没配」彻底解耦（可重复、可离线、可进 CI）。
    """
    monkeypatch.setattr(mdt.settings, "mysql_database_url", _DUMMY_URL)
    return _DUMMY_URL


@asynccontextmanager
async def _sqlite_session_factory(monkeypatch):
    """把模块级会话工厂临时换成 aiosqlite 内存库版本，退出时释放引擎。

    引擎的「创建 → 使用 → dispose」全部收在**同一个事件循环**里：aiosqlite 的
    连接与自己的线程绑定，跨事件循环 dispose 会引发 "Event loop is closed"。
    """
    # 刻意用 QueuePool 而非 StaticPool：只有前者提供 checkedout()，
    # 才能直接断言「连接已归还池子」。内存库下每条连接是一个独立空库，
    # 但 `SELECT 1` 不需要持久化，无影响。
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=AsyncAdaptedQueuePool,
        connect_args={"check_same_thread": False},
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(mdt, "get_db_session_maker", lambda: maker)
    try:
        yield maker, engine
    finally:
        await engine.dispose()


# 测试用 ORM 基类：只为验证 create_all 真的把表建出来了，不进生产代码。
class _ProbeBase(DeclarativeBase):
    """探针基类。"""


class _Probe(_ProbeBase):
    __tablename__ = "probe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)


# ============================ 1. 工厂：同步 + 单例（回归锁） ============================
def test_get_db_engine_is_sync_singleton(mysql_url):
    """★ 回归线：工厂必须是同步函数、返回真 AsyncEngine，且同进程只建一次。

    这是本次最关键的一条。若把工厂写回 `async def`，`get_db_engine()` 返回的是
    协程对象，`isinstance(..., AsyncEngine)` 立刻失败；哪怕调用方补上 await，
    第二次也会抛 RuntimeError: cannot reuse already awaited coroutine。
    """
    assert not inspect.iscoroutinefunction(mdt.get_db_engine), (
        "工厂不能是 async def：@lru_cache 缓存的是协程对象本身，"
        "第一次 await 后即作废（create_async_engine 本来就是同步函数）")

    engine = mdt.get_db_engine()
    assert isinstance(engine, AsyncEngine)
    assert mdt.get_db_engine() is engine, "重复取用必须命中同一个引擎实例"
    assert mdt.get_db_engine.cache_info().currsize == 1


def test_get_db_session_maker_is_sync_singleton(mysql_url):
    """会话工厂同样是同步单例，且必须挂在同一个引擎上（否则会开出两份连接池）。"""
    assert not inspect.iscoroutinefunction(mdt.get_db_session_maker)

    maker = mdt.get_db_session_maker()
    assert mdt.get_db_session_maker() is maker, "重复取用必须命中同一个工厂实例"
    assert maker.kw["bind"] is mdt.get_db_engine(), \
        "工厂必须绑定 get_db_engine() 返回的引擎，不能另建一个"


# ============================ 2. 连接串缺失要快速失败 ============================
def test_get_db_engine_fails_fast_on_empty_url(monkeypatch):
    """连接串缺失时在构造引擎前报错，并点名该配哪个环境变量。"""
    monkeypatch.setattr(mdt.settings, "mysql_database_url", "")

    with pytest.raises(RuntimeError) as ei:
        mdt.get_db_engine()

    assert "RAGQA_MYSQL_DATABASE_URL" in str(ei.value), \
        "错误信息必须指出该配哪个环境变量，否则排查成本极高"


def test_get_db_engine_fails_fast_on_blank_url(monkeypatch):
    """只有空白字符也算缺失：`.env` 里写了 `KEY=` 后面漏填是常见手误。"""
    monkeypatch.setattr(mdt.settings, "mysql_database_url", "   ")

    with pytest.raises(RuntimeError) as ei:
        mdt.get_db_engine()

    assert "RAGQA_MYSQL_DATABASE_URL" in str(ei.value)


# ============================ 3. 会话依赖：正常路径与异常路径都不泄漏 ============================
def test_get_db_session_yields_usable_session_and_releases_connection(monkeypatch):
    """会话依赖能 yield 出可用会话，耗尽后把连接还回池子（不再手写 close）。"""
    async def go():
        async with _sqlite_session_factory(monkeypatch) as (_, engine):
            agen = mdt.get_db_session()
            session = await anext(agen)

            assert (await session.execute(text("SELECT 1"))).scalar() == 1
            assert session.in_transaction() is True, "执行过语句后应处于事务中"
            assert engine.pool.checkedout() >= 1, "会话用着连接时应至少借出 1 个连接"

            with pytest.raises(StopAsyncIteration):
                await anext(agen)                       # 生成器收尾 → async with __aexit__ → close

            assert session.in_transaction() is False, "生成器结束后事务必须已释放"
            assert engine.pool.checkedout() == 0, "连接必须已归还连接池，否则就是泄漏"

    asyncio.run(go())


def test_get_db_session_releases_connection_on_exception(monkeypatch):
    """异常路径同样要归还连接：只照顾 happy path 就是资源泄漏。"""
    async def go():
        async with _sqlite_session_factory(monkeypatch) as (_, engine):
            agen = mdt.get_db_session()
            session = await anext(agen)
            # 必须真的执行一条语句：AsyncSession 是惰性的，不碰库就不会借连接
            await session.execute(text("SELECT 1"))
            assert engine.pool.checkedout() >= 1

            # 把异常抛进生成器的挂起点，模拟「业务代码在 yield 之后炸了」
            with pytest.raises(RuntimeError, match="boom"):
                await agen.athrow(RuntimeError("boom"))

            assert session.in_transaction() is False
            assert engine.pool.checkedout() == 0, "异常路径同样不能泄漏连接"

    asyncio.run(go())


def test_get_db_session_supports_async_for(monkeypatch):
    """契约写明可用「异步迭代」消费，这条确保 async for 写法真的成立。"""
    async def go():
        async with _sqlite_session_factory(monkeypatch) as (_, engine):
            seen = 0
            async for session in mdt.get_db_session():
                seen += 1
                assert (await session.execute(text("SELECT 1"))).scalar() == 1

            assert seen == 1, "每个请求只应产出一个会话"
            assert engine.pool.checkedout() == 0

    asyncio.run(go())


# ============================ 4. 建表 ============================
def test_init_db_creates_missing_tables(monkeypatch):
    """建表走 run_sync 建出 metadata 里的表（离线用 aiosqlite 验证真实建表行为）。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    monkeypatch.setattr(mdt, "get_db_engine", lambda: engine)
    monkeypatch.setattr(mdt, "_load_base", lambda: _ProbeBase)

    async def go():
        try:
            await mdt.init_db()
            async with engine.connect() as conn:
                names = (await conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='table'"))).scalars().all()
            assert "probe" in names, f"create_all 没建出探针表，实际表: {names}"
        finally:
            await engine.dispose()

    asyncio.run(go())


def test_init_db_creates_users_table_through_real_import_chain(monkeypatch):
    """★ 契约线：**不**替换 `_load_base`，走真实导入链，确认模型确实注册进了 metadata。

    上一条用例把 `_load_base` 整个换成了探针基类，只证明了「`run_sync(create_all)`
    能建表」；而真正要守的是「函数内局部导入保证了注册顺序」。若有人把模型从
    `models.py` 挪走、或改成模块级导入，`create_all` 会静默建 0 张表而测试仍全绿 ——
    这条用例把 `users` 表钉进 CI，堵住那个空白。
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    monkeypatch.setattr(mdt, "get_db_engine", lambda: engine)

    async def go():
        try:
            await mdt.init_db()
            async with engine.connect() as conn:
                names = (await conn.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='table'"))).scalars().all()
            assert "users" in names, f"ORM 模型未注册进 metadata，实际表: {names}"
        finally:
            await engine.dispose()

    asyncio.run(go())


def test_load_base_gives_actionable_error_when_models_missing(monkeypatch):
    """models.py 尚未建立时必须给可操作的中文指引，而不是裸 ModuleNotFoundError。

    用「往 sys.modules 里塞 None」确定性地模拟模块缺失（再 import 会抛
    ImportError: import of ... halted; None in sys.modules），
    避免依赖「models.py 此刻恰好不存在」这种会随仓库演进而失效的前提。
    """
    monkeypatch.setitem(sys.modules, "backend.app.models", None)

    with pytest.raises(RuntimeError) as ei:
        mdt._load_base()

    msg = str(ei.value)
    assert "models.py" in msg, f"错误信息应指出该建哪个文件，实际: {msg}"
    assert "Base" in msg, f"错误信息应指出要导出什么名字，实际: {msg}"


# ============================ 5. 收尾释放 ============================
def test_aclose_db_disposes_engine_and_clears_both_caches(mysql_url):
    """收尾：dispose 真的执行 + 两个 lru_cache 都清空（只清一个会半残）。

    dispose 的判据用「池对象换了新的」，而不是打桩计数：官方语义明确
    （sqlalchemy/engine/base.py 的 Engine.dispose docstring）——
    "A new connection pool is created immediately after the old one has been disposed"。
    这个判据不依赖 AsyncEngine 的内部属性是否可替换（其实它是 slots，实例级
    打桩会直接 AttributeError: object attribute 'dispose' is read-only）。
    """
    engine = mdt.get_db_engine()
    mdt.get_db_session_maker()                      # 让两个缓存都非空
    pool_before = engine.pool

    asyncio.run(mdt.aclose_db())

    assert engine.pool is not pool_before, "dispose 必须真的执行（执行后会立即换成新池）"
    assert mdt.get_db_engine.cache_info().currsize == 0, "引擎缓存必须清空"
    assert mdt.get_db_session_maker.cache_info().currsize == 0, \
        "工厂缓存也必须清空，否则它会一直持有已被 dispose 的旧引擎"


def test_aclose_db_is_noop_when_never_initialized():
    """从未构造过引擎时收尾不能报错（进程提前退出、健康检查前就崩等场景）。"""
    assert mdt.get_db_engine.cache_info().currsize == 0

    asyncio.run(mdt.aclose_db())                    # 不抛异常即通过


def test_aclose_db_is_idempotent(mysql_url):
    """连续收尾两次不能报错：lifespan 与测试夹具可能各调一次。"""
    mdt.get_db_engine()

    asyncio.run(mdt.aclose_db())
    asyncio.run(mdt.aclose_db())                    # 第二次应静默返回

    assert mdt.get_db_engine.cache_info().currsize == 0
