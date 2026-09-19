# -*- coding: utf-8 -*-
"""ORM 模型（backend/app/models.py）与 P2 spec §5 表结构的对照测试（零数据库依赖）。

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
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import mysql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

# 让 tests/ 能导入项目模块（等价于把 rag_qa_project 标记为 Sources Root）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.models import Base, Conversation, User

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


def test_both_tables_share_innodb_and_utf8mb4_collation():
    """两张表都要 InnoDB + utf8mb4_0900_ai_ci：排序规则不一致会让连接与比较行为漂移。"""
    for table in (User.__table__, Conversation.__table__):
        ddl = _mysql_ddl(table)
        assert "ENGINE=InnoDB" in ddl, f"{table.name} 缺 ENGINE=InnoDB"
        assert "utf8mb4_0900_ai_ci" in ddl, f"{table.name} 缺 utf8mb4_0900_ai_ci"
        assert "utf8mb4" in ddl, f"{table.name} 缺 charset"
