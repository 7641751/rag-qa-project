# -*- coding: utf-8 -*-
"""鉴权原语（backend/tools/security.py）离线测试（零数据库、零网络）。

设计思想
--------
沿用 test_graph.py / test_mysql_db.py 的路子：本项目未装 pytest-asyncio，
而本模块全是同步函数，所以用例就是普通同步函数，不需要事件循环。

守的四条契约：
1. 哈希是 bcrypt `$2b$` 且固定 60 字符（正合 `VARCHAR(60)`）；同一口令两次哈希不同（加盐）；
2. **>72 字节必须 422，不能变成 500** —— bcrypt 5.0.0 起 `hashpw` 对超长口令抛
   `ValueError`（changelog 原文：longer than 72 bytes now raises a ValueError），
   少了前置校验就是 500。阈值按 **UTF-8 字节**算，不是字符数；
3. `verify_password` 永不抛异常：脏哈希 / 超长口令都要安静地回 False，
   否则一条脏数据行就能把登录接口打成 500；
4. `decode_token` 对过期 / 篡改 / 垃圾串一律 401，且**不区分原因**。

运行方式（在 rag_qa_project 目录下）：
    python -m pytest tests/test_security.py -v
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
import pytest
from fastapi import HTTPException

# 让 tests/ 能导入项目模块（等价于把 rag_qa_project 标记为 Sources Root）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings
from backend.tools import security as sec


# ============================ 夹具 ============================
@pytest.fixture
def jwt_secret(monkeypatch):
    """注入固定密钥：让用例与「本机 .env 配没配 jwt_secret」彻底解耦。

    顺带覆盖 settings.jwt_secret 为空时本模块会快速失败这条分支（见最后一个用例）。

    ⚠ 密钥必须 ≥32 字节（这里是 40）：PyJWT 2.13 对 HS256 的 HMAC 密钥做最小长度校验
    （jwt/algorithms.py 的 HMACAlgorithm.check_key_length，依据 RFC 7518 §3.2），
    不足 32 字节时**每次 encode/decode** 都会发 InsecureKeyLengthWarning，
    测试输出就不再干净。生产密钥用 `openssl rand -hex 32` 得到 64 个字符，天然达标。
    """
    monkeypatch.setattr(settings, "jwt_secret", "unit-test-secret-not-for-prod-0123456789")
    return settings.jwt_secret


def _assert_contract_error(exc: HTTPException, status: int, code: str) -> str:
    """错误体必须是契约形状 {"code","message"}，返回 message 供进一步断言。"""
    assert exc.status_code == status
    assert isinstance(exc.detail, dict), f"错误体应是 dict，实际 {type(exc.detail)}"
    assert set(exc.detail) == {"code", "message"}, f"错误体字段不对: {exc.detail}"
    assert exc.detail["code"] == code
    assert exc.detail["message"], "message 不能为空"
    return exc.detail["message"]


# ============================ 1. 密码哈希 ============================
def test_hash_password_returns_60_char_bcrypt_hash():
    """★ `$2b$` 前缀 + 固定 60 字符 —— 正好等于 spec §5 的 VARCHAR(60)。"""
    h = sec.hash_password("abcd1234")

    assert h.startswith("$2b$")
    assert len(h) == 60, f"bcrypt 输出应固定 60 字符（含盐与算法前缀），实际 {len(h)}"
    assert "abcd1234" not in h, "哈希里不得出现明文"
    assert sec.verify_password("abcd1234", h) is True


def test_hash_password_salts_are_random():
    """同一口令两次哈希必须不同（每次 gensalt），否则彩虹表可用。"""
    a = sec.hash_password("abcd1234")
    b = sec.hash_password("abcd1234")

    assert a != b
    assert sec.verify_password("abcd1234", a) and sec.verify_password("abcd1234", b)


def test_hash_password_accepts_exactly_72_bytes():
    """边界：正好 72 字节是允许的（bcrypt 上限是"超过"，不是"达到"）。"""
    h = sec.hash_password("a" * 72)

    assert sec.verify_password("a" * 72, h) is True


def test_hash_password_rejects_73_bytes_with_422():
    """★ 回归线：73 字节必须是 422 VALIDATION_ERROR。

    若把前置校验删掉，bcrypt 5.0.0 会抛 ValueError 直接把接口打成 500
    （changelog: "Passing hashpw a password longer than 72 bytes now raises a ValueError"）。
    """
    with pytest.raises(HTTPException) as ei:
        sec.hash_password("a" * 73)

    _assert_contract_error(ei.value, 422, "VALIDATION_ERROR")


def test_hash_password_counts_utf8_bytes_not_characters():
    """★ 阈值是 **UTF-8 字节**不是字符数：30 个汉字 = 90 字节，必须 422。

    只看 len(str) 就会漏判 —— 这是"中文密码偶尔 500"这类问题的根源。
    """
    assert len("汉" * 30) == 30
    assert len(("汉" * 30).encode("utf-8")) == 90

    with pytest.raises(HTTPException) as ei:
        sec.hash_password("汉" * 30)

    _assert_contract_error(ei.value, 422, "VALIDATION_ERROR")

    # 24 个汉字 = 72 字节，正好在边界内，应当通过
    assert sec.hash_password("汉" * 24)


# ============================ 2. 密码校验 ============================
def test_verify_password_false_for_wrong_password():
    h = sec.hash_password("abcd1234")

    assert sec.verify_password("abcd1235", h) is False
    assert sec.verify_password("", h) is False


def test_verify_password_never_raises_on_dirty_hash():
    """★ 脏数据不得把登录接口打成 500：空串 / 非 bcrypt 串 / 非 ASCII 都回 False。"""
    assert sec.verify_password("abcd1234", "") is False
    assert sec.verify_password("abcd1234", "not-a-bcrypt-hash") is False
    assert sec.verify_password("abcd1234", "$2b$12$短哈希") is False


def test_verify_password_false_for_over_72_bytes():
    """超长口令同样只回 False —— 绝不因为"密码太长"而抛异常。"""
    h = sec.hash_password("a" * 72)

    assert sec.verify_password("a" * 73, h) is False


# ============================ 3. JWT 签发 ============================
def test_create_access_token_payload_matches_contract(jwt_secret):
    """payload 与 spec §6.2 逐字一致：{sub, username, exp, iat}，sub 是**字符串**。"""
    token = sec.create_access_token(7, "hao")
    payload = jwt.decode(token, jwt_secret, algorithms=[settings.jwt_algorithm])

    assert payload["sub"] == "7", "JWT 的 sub 必须是字符串（RFC 7519 StringOrURI）"
    assert isinstance(payload["sub"], str)
    assert int(payload["sub"]) == 7
    assert payload["username"] == "hao"
    assert set(payload) >= {"sub", "username", "exp", "iat"}


def test_create_access_token_expires_after_configured_days(jwt_secret):
    """有效期读 settings.jwt_expire_days（默认 7 天，spec §7.2）。"""
    payload = jwt.decode(sec.create_access_token(1, "hao"), jwt_secret,
                         algorithms=[settings.jwt_algorithm])

    delta = payload["exp"] - payload["iat"]
    assert delta == settings.jwt_expire_days * 86400, \
        f"exp - iat 应为 {settings.jwt_expire_days} 天，实际 {delta} 秒"


def test_decode_token_roundtrip(jwt_secret):
    assert sec.decode_token(sec.create_access_token(42, "hao"))["sub"] == "42"


# ============================ 4. JWT 解析失败一律 401 ============================
def test_decode_token_rejects_tampered_signature(jwt_secret):
    """★ spec §7.7 第 13 步：手改 token 一个字符 → 401 UNAUTHORIZED。"""
    token = sec.create_access_token(1, "hao")
    head, payload, sig = token.split(".")

    # ⚠ 翻转的位置必须与取判据的位置**一致**。原写法判据读 `sig[-1]`、却替换 `sig[0]`，
    #   于是当 `sig[-1] != 'A'` 且 `sig[0] == 'A'` 时 tampered 与原 token **完全相同** ——
    #   压根没篡改，自然也不会 401。实测触发率 1.38%（5000 个样本 69 次），
    #   表现为「这条用例偶发红灯」，曾让人怀疑签名实现而不是用例本身。
    #   末尾的断言让用例自证有效：构造没篡改成功就立刻炸，而不是悄悄变成一条假绿。
    pos = 0 if sig[0] != "A" else 1
    tampered = (f"{head}.{payload}.{sig[:pos]}"
                f"{'A' if sig[pos] != 'A' else 'B'}{sig[pos + 1:]}")
    assert tampered != token, "构造失败：篡改后的串与原串相同，这条用例已失去意义"

    with pytest.raises(HTTPException) as ei:
        sec.decode_token(tampered)

    _assert_contract_error(ei.value, 401, "UNAUTHORIZED")


def test_decode_token_rejects_expired(jwt_secret):
    """★ spec §7.7 第 14 步：exp 已过去的 token → 401。"""
    past = datetime.now(timezone.utc) - timedelta(seconds=10)
    expired = jwt.encode(
        {"sub": "1", "username": "hao", "iat": past - timedelta(days=8), "exp": past},
        jwt_secret, algorithm=settings.jwt_algorithm)

    with pytest.raises(HTTPException) as ei:
        sec.decode_token(expired)

    _assert_contract_error(ei.value, 401, "UNAUTHORIZED")


def test_decode_token_rejects_token_without_exp(jwt_secret):
    """缺 exp 的 token 必须拒 —— 否则签发端一个 bug 就产生永不过期的凭证。"""
    forever = jwt.encode({"sub": "1", "username": "hao"}, jwt_secret,
                         algorithm=settings.jwt_algorithm)

    with pytest.raises(HTTPException) as ei:
        sec.decode_token(forever)

    _assert_contract_error(ei.value, 401, "UNAUTHORIZED")


@pytest.mark.parametrize("garbage", ["", "abc", "a.b", "a.b.c", "not.a.jwt"])
def test_decode_token_rejects_garbage(jwt_secret, garbage):
    with pytest.raises(HTTPException) as ei:
        sec.decode_token(garbage)

    _assert_contract_error(ei.value, 401, "UNAUTHORIZED")


def test_decode_token_rejects_token_signed_with_other_secret(jwt_secret):
    """用别的密钥签的 token（等价于伪造）必须拒。"""
    # 这个"别的密钥"同样要 ≥32 字节，否则它自己就会触发 InsecureKeyLengthWarning，
    # 把本用例想守的「签名不匹配」淹在噪音里
    forged = jwt.encode(
        {"sub": "1", "username": "hao",
         "exp": datetime.now(timezone.utc) + timedelta(days=1)},
        "forged-secret-not-the-real-one-0123456789", algorithm=settings.jwt_algorithm)

    with pytest.raises(HTTPException) as ei:
        sec.decode_token(forged)

    _assert_contract_error(ei.value, 401, "UNAUTHORIZED")


# ============================ 5. 密钥未配置 ============================
def test_missing_jwt_secret_fails_fast(monkeypatch):
    """★ 空密钥必须快速失败并点名环境变量，绝不退化成空串签发。

    （真正的启动期校验应由 config.py 的 model_validator 承担，尚未加；
    在此之前本模块保证"一旦用到就失败"。）
    """
    monkeypatch.setattr(settings, "jwt_secret", "")

    with pytest.raises(RuntimeError) as ei:
        sec.create_access_token(1, "hao")
    assert "RAGQA_JWT_SECRET" in str(ei.value)

    with pytest.raises(RuntimeError) as ei2:
        sec.decode_token("a.b.c")
    assert "RAGQA_JWT_SECRET" in str(ei2.value)

    # 纯空白同样算未配置（.env 里写了 KEY= 却漏填是常见手误）
    monkeypatch.setattr(settings, "jwt_secret", "   ")
    with pytest.raises(RuntimeError):
        sec.create_access_token(1, "hao")
