# rag_qa_project/tests/test_qa_event_consumer.py
# -*- coding: utf-8 -*-
"""消费端：字段映射、幂等（event_id 唯一键）、成功 ACK、失败重投、超次进死信。全离线。

FakeRedis 的 PEL 语义按**真实 Redis** 建模（交付即进 pending、只有 XACK 才移除）——
否则「失败不 ACK → 下轮从 "0" 读回重投」的路径在这个替身上根本不可达，
重试/死信用例会变成假的绿（原计划的替身与实现叠加恰好构成这种不可达，见提交说明）。
"""
import asyncio
import logging
import sys
from contextlib import suppress
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.models import Base, QaEvent
from backend.app.services import qa_event_consumer as consumer


@pytest.fixture
def maker(tmp_path):
    # ⚠ 用**文件库**而不是 :memory:：本文件的用例会在多个 asyncio.run（多个 event loop）
    # 之间复用同一引擎，且 cancel 可能打断 aiosqlite 的在途操作、使物理连接被重建 ——
    # 内存库的「新连接 = 空库」会让重建后查不到表（实测偶发 no such table，6/10 次）。
    # 文件库下重连看到的还是同一个库，与 event loop 怎么切无关。
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path.as_posix()}/test.db",
        poolclass=StaticPool, connect_args={"check_same_thread": False})

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_setup())
    yield async_sessionmaker(engine, expire_on_commit=False)
    asyncio.run(engine.dispose())


EVENT = {"event_id": "e-1", "user_id": "7", "thread_id": "t1",
         "rewrites": "1", "grounded": "1", "latency_ms": "1234"}


class FakeRedis:
    """协议级替身：只实现消费端会用到的方法，PEL 按真实 Redis 语义建模。"""

    def __init__(self, batches=None):
        self.acked, self.dead = [], []
        self.pending = {}                      # msg_id -> fields（未 ACK，待重投）
        self.batches = batches if batches is not None else []
        self.idx = 0

    async def xgroup_create(self, *a, **k):
        raise Exception("BUSYGROUP")           # 组已存在：必须被忽略

    async def xreadgroup(self, group, consumer, streams, count=None):
        stream, read_id = next(iter(streams.items()))
        if read_id == ">":                     # 新消息：按脚本投递，交付即进 PEL
            batch = (self.batches[min(self.idx, len(self.batches) - 1)]
                     if self.batches else [])
            self.idx += 1
            self.pending.update(dict(batch))
        else:                                  # "0"：本消费者 PEL 重投
            batch = list(self.pending.items())
        return [(stream, batch)] if batch else []

    async def xack(self, stream, group, msg_id):
        self.acked.append(msg_id)
        self.pending.pop(msg_id, None)

    async def xadd(self, name, fields, maxlen=None, approximate=True):
        self.dead.append((name, dict(fields)))
        return "2-0"


def _run_consumer(redis, maker, handler, max_retries=3, seconds=0.1):
    """起消费任务、跑一小段、然后取消（poll_interval 很小，几次迭代足够）。"""
    async def go():
        task = asyncio.create_task(consumer.consume(
            redis, session_factory=maker, handler=handler,
            max_retries=max_retries, poll_interval=0.01))
        await asyncio.sleep(seconds)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    asyncio.run(go())


def test_handle_event_writes_row(maker):
    async def go():
        async with maker() as db:
            await consumer.handle_event(db, EVENT)
            await db.commit()
            return (await db.get(QaEvent, 1))

    row = asyncio.run(go())

    assert row.event_id == "e-1"
    assert row.user_id == 7 and row.thread_id == "t1"
    assert row.rewrites == 1 and row.grounded is True and row.latency_ms == 1234


def test_duplicate_event_is_idempotent(maker):
    """★ 至少一次投递：同一 event_id 再来一次不能重复插、也不能把消费端搞崩。"""
    async def go():
        async with maker() as db:
            await consumer.handle_event(db, EVENT)
            await db.commit()
            await consumer.handle_event(db, EVENT)     # 重投
            await db.commit()
            return await db.scalar(select(func.count()).select_from(QaEvent))

    assert asyncio.run(go()) == 1


def test_bad_payload_does_not_raise(maker):
    """字段缺失/类型不对：不能抛（否则会被当成「消费失败」无限重投一条脏数据）。"""
    async def go():
        async with maker() as db:
            await consumer.handle_event(db, {"event_id": "e-2"})   # 缺字段
            await db.commit()

    asyncio.run(go())      # 不抛即通过


def test_consumer_loop_acks_on_success(maker):
    """★ 硬约束 1 正面：成功 → XACK + 落库；ACK 过的消息不得被再次投递。"""
    redis = FakeRedis(batches=[[("1-0", dict(EVENT))], []])
    calls = {"n": 0}

    async def counting_handler(db, fields):
        calls["n"] += 1
        await consumer.handle_event(db, fields)

    _run_consumer(redis, maker, counting_handler)

    assert redis.acked == ["1-0"], "成功必须 XACK"
    assert not redis.dead
    assert calls["n"] == 1, "ACK 过的消息不得被再次投递（否则重复计数）"

    async def _count():
        async with maker() as db:
            return await db.scalar(select(func.count()).select_from(QaEvent))

    assert asyncio.run(_count()) == 1, "成功路径必须真的落库"


def test_consumer_loop_retries_then_dead_letters(maker):
    """★ 失败 → 不 ACK → 下轮从 PEL 重投 → 超 max_retries 进死信流 + ACK 原事件。"""
    redis = FakeRedis(batches=[[("1-0", dict(EVENT))], []])
    calls = {"n": 0}

    async def flaky_handler(db, fields):
        calls["n"] += 1
        raise RuntimeError("db down")

    _run_consumer(redis, maker, flaky_handler, max_retries=2)

    assert calls["n"] >= 2, "失败必须重投（不 ACK → 下轮从 PEL 读回）"
    assert redis.dead, "超过 max_retries 必须进死信流"
    assert redis.dead[0][0].endswith(":dead")
    assert redis.acked == ["1-0"], "只有进死信时才 ACK 原事件（不无限重投）"


def test_xack_failure_does_not_kill_consumer(maker):
    """★ XACK 失败（Redis 瞬时抖动）不能把消费循环炸死 —— 消息留在 PEL 下轮重投，
    幂等键兜住重复；循环崩掉才是真事故（统计永久停摆，正是本模块最该避免的失败模式）。
    """
    class FlakyAckRedis(FakeRedis):
        async def xack(self, stream, group, msg_id):
            raise RuntimeError("redis blip")       # 永远 ACK 不成功

    redis = FlakyAckRedis(batches=[[("1-0", dict(EVENT))], []])
    calls = {"n": 0}

    async def counting_handler(db, fields):
        calls["n"] += 1
        await consumer.handle_event(db, fields)

    _run_consumer(redis, maker, counting_handler)

    assert calls["n"] >= 2, "XACK 失败后消息应被重投，而不是把任务炸崩、循环停止"

    async def _count():
        async with maker() as db:
            return await db.scalar(select(func.count()).select_from(QaEvent))

    assert asyncio.run(_count()) == 1, "重投不得造成重复计数（幂等键必须兜住）"


def test_consumer_crash_is_logged_not_silent(caplog):
    """★ 消费任务意外崩溃必须立刻留下 error 日志（统计停摆不能无声无息）。

    设计文档 13 节对「消费者停了」的对策之一就是 error 日志；而 create_task 的异常
    无人取时，直到 GC 才可能出一句 warning，与正常关闭无从区分。
    """
    async def boom():
        raise RuntimeError("unexpected crash")

    async def go():
        task = asyncio.create_task(boom())
        task.add_done_callback(consumer.log_consumer_crash)
        with suppress(RuntimeError):
            await task      # 取回异常，验证的是回调留下的日志

    with caplog.at_level(logging.ERROR):
        asyncio.run(go())

    assert [r for r in caplog.records if r.levelno == logging.ERROR], \
        "崩溃必须留下 error 日志"


def test_consumer_cancel_does_not_log_crash(caplog):
    """正常关闭（cancel）不是崩溃：不许打 error 日志（否则每次重启都误报事故）。"""
    async def go():
        task = asyncio.create_task(asyncio.sleep(10))
        task.add_done_callback(consumer.log_consumer_crash)
        await asyncio.sleep(0.01)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    with caplog.at_level(logging.ERROR):
        asyncio.run(go())

    assert not [r for r in caplog.records if r.levelno == logging.ERROR]
