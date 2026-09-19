# rag_qa_project/tests/test_auth_schemas.py
# -*- coding: utf-8 -*-
"""鉴权请求/响应模型的契约校验（纯 pydantic，零依赖）。

守 spec §6.2 的字段约束 + §7.7 验收第 4 步（注册 `ab` 必须 422）。
"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.agent.schemas import Auth, AuthUser, LoginResponse


def test_username_min_length_is_3():
    """★ spec §6.2：username 是 3–32 位；§7.7 第 4 步要求 `ab` 被拒。"""
    with pytest.raises(ValidationError):
        Auth(username="ab", password="abcd1234")


def test_username_max_length_is_32():
    with pytest.raises(ValidationError):
        Auth(username="a" * 33, password="abcd1234")


def test_username_rejects_non_alnum_underscore():
    for bad in ("hao-1", "hao.1", "hao 1", "中文名"):
        with pytest.raises(ValidationError):
            Auth(username=bad, password="abcd1234")


def test_username_accepts_alnum_and_underscore():
    for good in ("hao", "hao_1", "ABC123", "a" * 32):
        assert Auth(username=good, password="abcd1234").username == good


def test_password_min_length_is_8():
    with pytest.raises(ValidationError):
        Auth(username="hao", password="a" * 7)


def test_password_over_72_utf8_bytes_is_rejected_at_schema_layer():
    """★ 30 个汉字 = 90 字节 > 72 —— 必须在 schema 层就拒掉。

    只写 max_length=72（字符数）会放过它，一路走到 bcrypt.hashpw 才 422，
    错误信息变成"密码不能超过 72 字节（当前 90 字节）"而不是契约里的字段级校验错。
    """
    han = "汉" * 30
    assert len(han) == 30 and len(han.encode("utf-8")) == 90

    with pytest.raises(ValidationError):
        Auth(username="hao", password=han)


def test_password_exactly_72_bytes_is_accepted():
    assert Auth(username="hao", password="a" * 72)


def test_auth_user_shape():
    au = AuthUser(id=1, username="hao")
    assert au.model_dump() == {"id": 1, "username": "hao"}


def test_login_response_shape():
    r = LoginResponse(access_token="t", user=AuthUser(id=1, username="hao"))
    assert r.model_dump() == {
        "access_token": "t", "token_type": "bearer",
        "user": {"id": 1, "username": "hao"}}