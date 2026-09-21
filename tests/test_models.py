# -*- coding: utf-8 -*-
"""ORM 模型（backend/app/models.py）与设计文档表结构的对照测试（零数据库依赖）。

覆盖面：P2 的 `users` / P3 的 `conversations`（§5）+ P4 的 `qa_events`（§10）。

设计思想
--------
**不需要连 MySQL**：用 MySQL 方言把 `CreateTable` 编译成 DDL 字符串再断言，
就能把 §5 的建表语句钉死 —— 这是「模型与设计文档是否一致」最直接的证据，
比连库跑 `SHOW CREATE TABLE` 快几个数量级，也不依赖任何环境。

守的四条契约：
1. 列与 §5 逐字一致：`id BIGINT UNSIGNED` / `username VARCHAR(32)` /
   `password_hash VARCHAR(60)` / `created_at DATETIME(6)`，且都 NOT NULL；
2. 唯一键名 `uq_users_username`、排序规则 `utf8mb4_0900_ai_ci`
   （**大小写不敏感** —— §5 明确「`Alice` 与 `alice` 撞唯一键」是想要的行为）、引擎 InnoDB；
3. 列名必须是 `password_hash`，**不得叫 `password`**（列名本身就是一种文档，
   叫 password 会持续诱导后来者往里存明文）；
4. 模型必须**跨方言可用**：测试跑 SQLite 内存库（§7.4 约定），所以 MySQL 专属类型
   （`BIGINT(unsigned=True)` / `DATETIME(fsp=6)`）不能在这里炸，且**主键仍要能自增**。

运行方式（在 rag_qa_project 目录下）：
    python -m pytest tests/test_models.py -v
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import UniqueConstraint, create_engine, select
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

# 让 tests/ 能导入项目模块（等价于把 rag_qa_project 标记为 Sources Root）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.models import Base, Conversation, QaEvent, User

# 一个合法的 bcrypt 形状（$2b$ + 12 轮 + 22 盐 + 31 哈希 = 60 字符），避免在用例里真算哈希
_FAKE_HASH = "$2b$12$" + "x" * 53
assert len(_FAKE_HASH) == 60


def _mysql_ddl(table) -> str:
    """把建表语句编译成 MySQL 方言的 DDL（纯编译，不连库）。

    去掉 MySQL 的反引号标识符引用，让断言只关心「列 / 约束长什么样」。
    """
    ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
    return ddl.replace("`", "")


# ============================ 1. 列定义对齐 §5 ============================
def test_users_columns_match_spec_section_5():
    """★ 逐条对照 spec §5 的 CREATE TABLE users。"""
    ddl = _mysql_ddl(User.__table__)

    assert "id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT" in ddl
    assert "username VARCHAR(32) NOT NULL" in ddl
    assert "password_hash VARCHAR(60) NOT NULL" in ddl
    assert "created_at DATETIME(6) NOT NULL" in ddl
    assert "PRIMARY KEY (id)" in ddl


def test_users_has_no_plaintext_password_column():
    """★ 回归线：列名只能叫 password_hash。"""
    assert "password_hash" in User.__table__.columns
    assert "password" not in User.__table__.columns


def test_password_hash_width_is_exactly_bcrypt_output_length():
    """bcrypt 输出固定 60 字符；宽度给大了会让人误以为能存下别的东西。"""
    assert User.__table__.columns["password_hash"].type.length == 60


# ============================ 2. 约束与表选项 ============================
def test_username_unique_key_is_named_and_case_insensitive():
    """唯一键要有名字（重复键报错里会出现它，将来 DROP 也要用），且大小写不敏感。"""
    ddl = _mysql_ddl(User.__table__)

    assert "uq_users_username" in ddl, "唯一键必须显式命名（§5 的 uq_users_username）"
    assert "UNIQUE (username)" in ddl
    assert "utf8mb4_0900_ai_ci" in ddl, \
        "ci 排序规则才让 Alice 与 alice 撞唯一键 —— §5 明确要这个行为"


def test_table_options_match_spec():
    ddl = _mysql_ddl(User.__table__)

    assert "ENGINE=InnoDB" in ddl
    assert "utf8mb4" in ddl


# ============================ 3. 跨方言：SQLite 上要能建表、能自增 ============================
def test_sqlite_can_create_and_autoincrement():
    """★ 测试库是 SQLite（§7.4 约定），模型必须在这里也成立。

    两个易踩点：
    ① 主键若渲染成 `BIGINT`，SQLite **不会自增** —— 只有类型名恰好是 `"INTEGER"` 的
       单列主键才是 rowid 别名（sqlalchemy/dialects/sqlite/base.py:86-104 明确写了，
       并给出 `BigInteger().with_variant(Integer, "sqlite")` 这个官方解法）。
       渲染成 BIGINT 时插入会因 NOT NULL 直接失败。
    ② `created_at` 的默认值必须落成 **naive UTC**（MySQL DATETIME 不存时区）。
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as s:
            s.add(User(username="hao", password_hash=_FAKE_HASH))
            s.commit()
            row = s.execute(select(User)).scalar_one()

            assert row.id == 1, "SQLite 上主键必须能自增（BIGINT 不行，必须是 INTEGER）"
            assert row.created_at.tzinfo is None, "DATETIME 不存时区，统一按 naive UTC 存"
            drift = abs((datetime.now(timezone.utc).replace(tzinfo=None)
                         - row.created_at).total_seconds())
            assert drift < 60, f"created_at 应由 Python 默认值填成当前 UTC，偏差 {drift}s"
    finally:
        engine.dispose()


def test_username_uniqueness_is_enforced_by_the_database():
    """唯一性不能只写在注释里：真插两条同名记录，必须被数据库拒掉。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as s:
            s.add(User(username="hao", password_hash=_FAKE_HASH))
            s.commit()

        with Session(engine) as s:
            s.add(User(username="hao", password_hash="$2b$12$" + "y" * 53))
            with pytest.raises(IntegrityError):
                s.commit()
    finally:
        engine.dispose()


# ============================ 4. conversations 对齐 §5 ============================
def test_conversations_columns_match_spec_section_5():
    """★ §5 的 CREATE TABLE conversations：thread_id 是主键，user_id 是 BIGINT UNSIGNED。"""
    ddl = _mysql_ddl(Conversation.__table__)

    assert "thread_id VARCHAR(36) NOT NULL" in ddl
    assert "user_id BIGINT UNSIGNED NOT NULL" in ddl
    assert "title VARCHAR(60) NOT NULL" in ddl
    assert "created_at DATETIME(6) NOT NULL" in ddl
    assert "updated_at DATETIME(6) NOT NULL" in ddl
    assert "PRIMARY KEY (thread_id)" in ddl


def test_conversations_foreign_key_cascades_to_users():
    """删 user 要级联删 conversations 行（§5）。

    ⚠ 级联**不会**碰 checkpointer 里的 thread 数据（§5 明确记了这条局限），
    所以将来真做「删用户」功能时必须先遍历该用户的 thread_id 调 delete_thread。
    """
    fks = list(Conversation.__table__.foreign_keys)

    assert len(fks) == 1, "只应有 user_id → users.id 这一条外键"
    assert fks[0].target_fullname == "users.id"
    assert fks[0].constraint.name == "fk_conversations_user"
    assert fks[0].ondelete == "CASCADE"


def test_conversations_user_updated_index_is_declared():
    """§5 的 `ix_conversations_user_updated (user_id, updated_at DESC)` —— P3 列表查询靠它。

    注意 Index 是**独立 DDL**（`CREATE INDEX`），不会出现在 `CreateTable` 里，
    所以这里用 Table 的内省而不是编译建表语句。
    """
    indexes = {i.name: i for i in Conversation.__table__.indexes}

    assert "ix_conversations_user_updated" in indexes, \
        f"缺少 §5 声明的索引，实际: {sorted(indexes)}"
    idx = indexes["ix_conversations_user_updated"]
    assert [c.name for c in idx.columns] == ["user_id"], "索引第一列必须是 user_id"
    assert "updated_at DESC" in str(idx.expressions[1]), \
        "第二列必须带 DESC（P3 是按 updated_at 倒序取最近 50 条）"


def test_all_tables_share_innodb_and_utf8mb4_collation():
    """**每张表**都要 InnoDB + utf8mb4_0900_ai_ci：排序规则不一致会让连接与比较行为漂移。

    ⚠ 新增表时必须把它加进下面这个元组。这条用例曾叫 `test_both_tables_...` 且只遍历
    `User`/`Conversation` —— qa_events 落地时没被纳入，于是评审用变异证明：**改掉新表的
    charset/引擎，全套件依然全绿**。所以名字也一并改成 all_tables，让"漏加一张表"可被察觉。
    """
    for table in (User.__table__, Conversation.__table__, QaEvent.__table__):
        ddl = _mysql_ddl(table)
        assert "ENGINE=InnoDB" in ddl, f"{table.name} 缺 ENGINE=InnoDB"
        assert "utf8mb4_0900_ai_ci" in ddl, f"{table.name} 缺 utf8mb4_0900_ai_ci"
        assert "utf8mb4" in ddl, f"{table.name} 缺 charset"


# ============================ 5. qa_events 对齐 §10 ============================
def test_qa_events_columns_match_spec_section_10():
    """★ §10 的 CREATE TABLE qa_events：八个字段一个都不能少，**且逐列类型/长度/可空性一致**。

    只比列名集合是不够的 —— 评审用变异证明过：把 `event_id` 改成 `VARCHAR(255)`、`grounded`
    改成字符串、或直接多加一列，集合断言照样全绿。所以这里改成断言 SQLAlchemy **实际编译出的
    DDL 片段**，与文件既有的 `test_password_hash_width_is_exactly_bcrypt_output_length`
    同一做法：把「只能靠手工 SHOW CREATE TABLE 看」的事实固化成回归线。
    """
    ddl = _mysql_ddl(QaEvent.__table__)

    assert set(QaEvent.__table__.columns.keys()) == {
        "id", "event_id", "user_id", "thread_id",
        "rewrites", "grounded", "latency_ms", "created_at"}, \
        "列集合必须与 §10 完全一致（多一列或漏一列都要发现）"

    for fragment in (
        "id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT",
        "event_id VARCHAR(36) NOT NULL",
        "user_id BIGINT UNSIGNED NOT NULL",
        "thread_id VARCHAR(36) NOT NULL",
        "rewrites INTEGER NOT NULL",
        "grounded BOOL NOT NULL",
        "latency_ms INTEGER NOT NULL",
        "created_at DATETIME(6) NOT NULL",
        "PRIMARY KEY (id)",
        "CONSTRAINT uq_qa_events_event_id UNIQUE (event_id)",
    ):
        assert fragment in ddl, f"§10 要求 DDL 含「{fragment}」，实际：\n{ddl}"


def test_qa_events_event_id_has_unique_constraint():
    """★ event_id 必须有 UNIQUE 约束 —— 这是这张表存在的理由。

    Redis Streams 是**至少一次**投递：同一事件可能被重复消费。唯一的幂等闸门就是
    这个唯一键（消费端撞约束后当成功处理）。去掉它，统计会重复计数且无从发现。
    """
    uniques = [c for c in QaEvent.__table__.constraints
               if isinstance(c, UniqueConstraint)]

    assert any([col.name for col in u.columns] == ["event_id"] for u in uniques), \
        "event_id 必须有 UNIQUE 约束（幂等键）"


def test_qa_events_has_no_foreign_key_to_users():
    """★ 刻意不建外键：派生数据不该因为用户被删而级联消失（统计要能独立留存），
    也避免 CASCADE 在删用户时把事件一并抹掉。

    回归线：将来若有人「顺手」给 user_id 补 ForeignKey，这条用例会红。
    """
    assert list(QaEvent.__table__.foreign_keys) == [], \
        "qa_events 不应有任何外键（尤其不能指向 users.id）"
