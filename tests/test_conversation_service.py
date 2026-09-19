# rag_qa_project/tests/test_conversation_service.py
# -*- coding: utf-8 -*-
"""会话归属服务：upsert / assert_owner / 删除索引行。全离线（SQLite 替身）。"""
import asyncio
import sys
from pathlib import Path
from backend.app.models import Conversation
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.models import Base
from backend.app.services import conversation_service as cs


@pytest.fixture
def sessionmaker_():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False})

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_setup())

    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    asyncio.run(engine.dispose())


def _run(maker, coro_factory):
    async def go():
        async with maker() as db:
            return await coro_factory(db)
    return asyncio.run(go())


def test_upsert_inserts_new_row_with_title_from_first_question(sessionmaker_):
    async def go(db):
        await cs.upsert_conversation(db, thread_id="t1", user_id=1,
                                     question="年假有几天？" * 5)
        await db.commit()
        return await db.get(Conversation, "t1")
    row = _run(sessionmaker_, go)

    assert row.user_id == 1
    assert row.title == ("年假有几天？" * 5)[:20], "title = 首问前 20 字（§3 决策 6）"