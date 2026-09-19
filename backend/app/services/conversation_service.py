# rag_qa_project/backend/app/services/conversation_service.py
"""会话归属：thread_id → user_id 的映射与校验。

这张表是 P2 归属校验的**唯一依据**（spec §3 决策 5）——thread_id 由前端生成，
checkpointer 里只有图状态、没有归属信息。
"""
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Conversation
from backend.tools.http_tools import api_error

TITLE_MAX_CHARS = 20


async def upsert_conversation(db: AsyncSession, *, thread_id: str,
                              user_id: int, question: str) -> Conversation:
    """新行插入（title = 首问前 20 字）；已有行只更新 updated_at。

    title 只在首次提问时写（spec §3 决策 6）：P2 的 upsert 现场正好握着 question，
    留到 P3 再补就要回填历史行。
    """
    row = await db.get(Conversation, thread_id)
    now = datetime.now(UTC)
    if row is None:
        row = Conversation(thread_id=thread_id, user_id=user_id,
                           title=question[:TITLE_MAX_CHARS],
                           created_at=now, updated_at=now)
        db.add(row)
    else:
        row.updated_at = now
    await db.flush()
    return row


async def assert_owner(db: AsyncSession, thread_id: str, user_id: int) -> None:
    """校验会话归属：thread_id → user_id 的映射。"""
    row = await db.get(Conversation, thread_id)
    if row is not None and row.user_id != user_id:
        api_error(403, "FORBIDDEN", "该会话不属于当前用户")


async def delete_conversation_row(db: AsyncSession, thread_id: str) -> None:
    """删索引行。幂等：不存在也不报错（删除顺序见 spec §7.6 第 3 步）。"""
    row = await db.get(Conversation, thread_id)
    if row is not None:
        await db.delete(row)
