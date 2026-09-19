# rag_qa_project/tests/test_config_jwt.py
# -*- coding: utf-8 -*-
"""jwt_secret 的启动期校验：为空必须当场失败，绝不退化成空串签发。"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings


def test_empty_jwt_secret_is_rejected_at_construction():
    """★ §7.2：空密钥必须让「构造 Settings」这一步就失败（= uvicorn 起不来）。"""
    with pytest.raises(ValidationError) as ei:
        Settings(jwt_secret="")
    assert "RAGQA_JWT_SECRET" in str(ei.value)


def test_whitespace_jwt_secret_is_rejected():
    """`.env` 里写了 KEY= 却漏填是常见手误，纯空白同样算未配置。"""
    with pytest.raises(ValidationError):
        Settings(jwt_secret="   ")


def test_short_jwt_secret_is_rejected():
    """HS256 的密钥不足 32 字节会被 PyJWT 警告（RFC 7518 §3.2），提前拦掉。"""
    with pytest.raises(ValidationError) as ei:
        Settings(jwt_secret="short")
    assert "32" in str(ei.value)


def test_valid_jwt_secret_passes():
    s = Settings(jwt_secret="a" * 64)
    assert s.jwt_secret == "a" * 64