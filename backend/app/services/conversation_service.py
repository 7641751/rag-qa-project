# rag_qa_project/backend/app/services/conversation_service.py
"""会话归属：thread_id → user_id 的映射与校验。

这张表是 P2 归属校验的**唯一依据**（spec §3 决策 5）——thread_id 由前端生成，
checkpointer 里只有图状态、没有归属信息。
"""
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Conversation
from backend.tools.http_tools import api_error

TITLE_MAX_CHARS = 20

# 列表硬上限（spec §7.1）。超出的部分不回，但 total 返回真实总数，
# 前端据此显示「仅显示最近 50 条」——只回 50 条而不给 total 是看不出被截断的。
LIST_LIMIT = 50


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


async def list_conversations(db: AsyncSession, user_id: int) -> tuple[list[Conversation], int]:
    """当前用户的会话，按 `updated_at` 倒序，最多 `LIST_LIMIT` 条。返回 `(rows, total)`。

    **total 必须单独 count**：它是前端显示「仅显示最近 50 条」的唯一依据。
    若图省事返回 `len(rows)`，被截断时 total == 50，前端会以为一共有 50 条。

    查询与索引 `ix_conversations_user_updated (user_id, updated_at DESC)` 完全对齐，
    所以 where + order_by + limit 是走索引的直接扫描，不需要额外排序。
    """
    total = await db.scalar(
        select(func.count()).select_from(Conversation)
        .where(Conversation.user_id == user_id))
    rows = (await db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(LIST_LIMIT))).all()
    return list(rows), int(total or 0)


async def rename_conversation(db: AsyncSession, thread_id: str,
                              user_id: int, title: str) -> Conversation:
    """重命名会话。非本人 → 403，不存在 → 404（spec §11 错误矩阵）。

    **为什么用 Core update 而不是 `row.title = title`**：模型上挂着
    `onupdate=_utcnow`（models.py:69），而任何带脏属性的 UPDATE 都会顺带刷新
    `updated_at` —— 于是「重命名」会把会话顶到列表最前，破坏「列表按最近提问排序」
    的语义。Core update 里**显式给出** `updated_at=<原值>` 可以抑制该列的 onupdate
    （SQLAlchemy 的规则：列一旦出现在 SET 里，onupdate 就不再覆盖它）。

    403 而非 404：会话 id 由前端生成、就写在 URL 里，用户本来就知道它存在，
    403 不构成额外信息泄露。这与「删别人的文档 → 404」的选择不同 ——
    那个 404 是为了不泄露**存在性**，两者的出发点不一样。
    """
    row = await db.get(Conversation, thread_id)
    if row is None:
        api_error(404, "NOT_FOUND", f"会话不存在: {thread_id}")
    if row.user_id != user_id:
        api_error(403, "FORBIDDEN", "该会话不属于当前用户")

    await db.execute(
        update(Conversation)
        .where(Conversation.thread_id == thread_id)
        .values(title=title, updated_at=row.updated_at))
    await db.commit()
    await db.refresh(row)     # Core update 绕过了 ORM，刷新才能让返回值反映库里的真值
    return row


async def delete_conversation_row(db: AsyncSession, thread_id: str) -> None:
    """删索引行。幂等：不存在也不报错（删除顺序见 spec §7.6 第 3 步）。"""
    row = await db.get(Conversation, thread_id)
    if row is not None:
        await db.delete(row)
