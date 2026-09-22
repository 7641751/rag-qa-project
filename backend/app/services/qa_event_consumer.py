# rag_qa_project/backend/app/services/qa_event_consumer.py
# -*- coding: utf-8 -*-
"""问答事件消费端：Redis Streams → MySQL `qa_events`（P4）。

三条硬约束（P4 设计文档 §6.4）：
1. **至少一次投递**：成功后 `XACK`；失败**不 ACK**（留在 PEL 里，下一轮从 "0" 读回重投）。
2. **幂等靠 `event_id` 唯一键**：重复消费会撞唯一约束，捕获 `IntegrityError` 当成功 ——
   派生数据重复计数比丢一行更难排查。
3. **永不无限重试**：同一事件失败超过 `MAX_RETRIES` 就投进死信流并 ACK 原事件。

消费端崩了不影响任何业务请求（统计是可丢的）。
"""
import asyncio
import logging

from sqlalchemy.exc import IntegrityError

from backend.app.models import QaEvent
from config import settings

logger = logging.getLogger(__name__)

GROUP = "qa_consumers"
MAX_RETRIES = 3
CONSUMER = "worker-1"
DEAD_SUFFIX = ":dead"


async def handle_event(db, fields: dict) -> None:
    """把一条事件写进 `qa_events`。字段缺失/非法时**静默跳过**（见 docstring 末）。

    ⚠ 不抛异常的原因：抛出去会被循环当成「消费失败」而无限重投一条永远不可能成功的脏数据。
    """
    try:
        row = QaEvent(event_id=str(fields["event_id"]),
                      user_id=int(fields["user_id"]),
                      thread_id=str(fields["thread_id"]),
                      rewrites=int(fields.get("rewrites", 0)),
                      grounded=str(fields.get("grounded", "0")) in ("1", "true", "True"),
                      latency_ms=int(fields.get("latency_ms", 0)))
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("[qa-event] 事件字段非法，跳过: %s (%s)", fields, exc)
        return

    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        # 重复 event_id = 重投，属正常路径
        await db.rollback()
        logger.info("[qa-event] 重复事件，已忽略 event_id=%s", row.event_id)


async def _safe_xack(redis, stream: str, group: str, msg_id: str) -> None:
    """ACK 失败不致命：消息留在 PEL、下轮重投，幂等键兜住重复 —— 崩溃才是真事故。

    若让 XACK 的异常冒出去，一次 Redis 抖动就会把整个消费循环带走（统计永久停摆，
    正是本模块最该避免的失败模式）；留在 PEL 反而有自愈机会：重投 → 幂等键挡重复
    → 下次 XACK 成功即清掉。
    """
    try:
        await redis.xack(stream, group, msg_id)
    except Exception as exc:
        logger.warning("[qa-event] XACK 失败（消息将重投，幂等兜底）id=%s: %s", msg_id, exc)


async def consume(redis, session_factory, handler=handle_event,
                  max_retries: int = MAX_RETRIES, poll_interval: float = 1.0,
                  group: str = GROUP, consumer: str = CONSUMER) -> None:
    """后台消费循环（在 lifespan 里 `create_task` 起来，退出时 cancel）。

    **每轮先读本消费者 PEL（流 id "0"）再读新消息（">"）** —— 这是「失败不 ACK、
    留在 PEL 待重投」能真正成立的关键：">" 只投递**从未投给任何消费者**的新消息，
    失败留在 PEL 里的事件只有从 "0" 读才会重投（只读 ">" 的话，重试与死信路径
    永远不可达，docstring 的承诺就成了空话）。真实 Redis 与测试替身同此语义。

    重试计数放在**进程内存**里：重启后计数从头开始，最坏是多试几次，不会漏处理。
    """
    stream = settings.qa_stream_key
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception as exc:                       # BUSYGROUP = 组已存在，正常
        logger.debug("[qa-event] 消费组已存在或创建失败: %s", exc)

    attempts: dict[str, int] = {}
    while True:
        # ① 本消费者 PEL 的未 ACK 消息（重投）② 新消息 —— 顺序固定，先旧后新
        entries: list = []
        for read_id in ("0", ">"):
            try:
                resp = await redis.xreadgroup(group, consumer, {stream: read_id},
                                              count=10)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[qa-event] 读取失败，稍后重试: %s", exc)
                continue
            for _stream_name, batch in (resp or []):
                entries.extend(batch)

        for msg_id, fields in entries:
            try:
                async with session_factory() as db:
                    await handler(db, fields)
                    await db.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempts[msg_id] = attempts.get(msg_id, 0) + 1
                logger.warning("[qa-event] 处理失败(%d/%d) id=%s: %s",
                               attempts[msg_id], max_retries, msg_id, exc)
                if attempts[msg_id] >= max_retries:
                    await _to_dead_letter(redis, stream, msg_id, fields, exc)
                    await _safe_xack(redis, stream, group, msg_id)   # 原事件也 ACK，不再重投
                    attempts.pop(msg_id, None)
                continue
            await _safe_xack(redis, stream, group, msg_id)
            attempts.pop(msg_id, None)

        await asyncio.sleep(poll_interval)


async def _to_dead_letter(redis, stream: str, msg_id: str, fields: dict, exc) -> None:
    """投死信流：保留原始字段 + 失败原因，便于事后人工排查。"""
    try:
        payload = dict(fields)
        payload["_failed_msg_id"] = msg_id
        payload["_error"] = f"{type(exc).__name__}: {exc}"[:200]
        await redis.xadd(stream + DEAD_SUFFIX, payload, maxlen=1000, approximate=True)
        logger.error("[qa-event] 超重试上限，已投死信流 id=%s", msg_id)
    except Exception as e:
        logger.error("[qa-event] 投死信流失败 id=%s: %s", msg_id, e)


def log_consumer_crash(task: "asyncio.Task") -> None:
    """create_task 起的消费任务若意外崩溃，异常无人取 —— 直到 GC 才可能出一句 warning，
    与正常关闭无从区分。挂到 done 回调上，把「统计已停止」立刻记成 error
    （设计文档 13 节对「消费者停了」的对策之一）。正常关闭（cancel）不算崩溃、不打日志。
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("[qa-event] 消费任务意外退出（统计已停止，业务不受影响）: %s", exc)
