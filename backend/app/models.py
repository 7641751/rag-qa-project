# -*- coding: utf-8 -*-
"""SQLAlchemy ORM 模型（MySQL 持久化层）。
"""
from datetime import UTC, datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    """当前 UTC 时间（带时区）。
    """
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """全部 ORM 模型的声明式基类（`init_db()` 依赖它的 metadata）。"""


class User(Base):
    """用户表。
    P2（账号鉴权）落地时还需要补：密码哈希校验流程、以及
    `conversations`（`thread_id → user_id` 归属映射）等表。
    """
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    # sqlite 测试库需 INTEGER 主键才有 rowid 自增（BIGINT 主键插 NULL 会违反 NOT NULL）
    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True).with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    # 登录按用户名查；P2 契约要求「用户名已存在 → 409 USERNAME_TAKEN」，
    # 唯一性由 uq_users_username 唯一约束保证，不能只靠应用层先查后插（并发下会漏）。
    username: Mapped[str] = mapped_column(String(32))
    # 存的是 bcrypt 哈希（bcrypt 输出固定 60 字符）
    password_hash: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=_utcnow)


class Conversation(Base):
    """会话表。
    """
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_updated", "user_id", text("updated_at DESC")),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )
    title: Mapped[str] = mapped_column(String(60))
    thread_id: Mapped[str] = mapped_column(String(36), primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", name="fk_conversations_user", ondelete="CASCADE"),
    )
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=_utcnow, onupdate=_utcnow)


class QaEvent(Base):
    """问答事件表（派生数据，P4）。

    由消费端从 Redis Streams（`ragqa:stream:qa_stats`）异步落库，只作统计用，
    不参与任何业务判断 —— 消费端挂了不影响任何请求。

    **刻意不建外键**（`user_id` 不指向 `users.id`）：统计是派生数据，不该因为
    用户被删而级联消失，要能独立留存；也避免 CASCADE 在删用户时把事件一并抹掉。
    """
    __tablename__ = "qa_events"
    __table_args__ = (
        # 幂等键：Redis Streams 是**至少一次**投递，重复消费靠这个唯一约束挡住，
        # 否则统计会重复计数（且重复计数比丢一行更难排查）。
        UniqueConstraint("event_id", name="uq_qa_events_event_id"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
        },
    )

    # sqlite 测试库需 INTEGER 主键才有 rowid 自增（BIGINT 主键插 NULL 会违反 NOT NULL）
    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True).with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    # 由生产端生成的 uuid4 字符串（36 字符），重复消费时撞 uq_qa_events_event_id
    event_id: Mapped[str] = mapped_column(String(36))
    user_id: Mapped[int] = mapped_column(BIGINT(unsigned=True))
    thread_id: Mapped[str] = mapped_column(String(36))
    rewrites: Mapped[int] = mapped_column(Integer)
    grounded: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=_utcnow)
