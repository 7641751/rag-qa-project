from datetime import datetime, timedelta, timezone
from typing import Any, NoReturn

import bcrypt
import jwt
from fastapi import HTTPException
from config import settings
from backend.tools.http_tools import api_error

BCRYPT_MAX_BYTES = 72



def _jwt_secret() -> str:
    """取 JWT 密钥；未配置时**快速失败**。

    不能退化成空串或默认值——那样任何人都能自签一个"合法" token（spec §7.2 明确要求
    「为空必须启动失败」）。真正的**启动期**校验应由 `config.py` 的
    `@model_validator(mode="after")` 承担（当前还没加）；在它落地之前，
    本函数保证「一旦用到就失败」，绝不会静默拿空密钥去签发。
    """
    secret = (settings.jwt_secret or "").strip()
    if not secret:
        raise RuntimeError(
            "RAGQA_JWT_SECRET 未配置：请在 .env 里加一行 "
            "RAGQA_JWT_SECRET=<openssl rand -hex 32 的输出>；"
            "留空会让任何人都能伪造 token。"
        )
    return secret


def hash_password(password: str):
    """明文转哈希"""
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        api_error(422, "VALIDATION_ERROR",
                   f"密码不能超过 {BCRYPT_MAX_BYTES} 字节（当前 {len(raw)} 字节）")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    """明文与哈希比对"""
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES or not password_hash:
        return False
    try:
        return bcrypt.checkpw(raw, password_hash.encode("ascii"))
    except ValueError:
        # 非法 bcrypt 串 / 非 ASCII 脏哈希：统一当作"不匹配"
        return False



def create_access_token(user_id: int, username: str) -> str:
    """生成访问令牌"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_expire_days),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=settings.jwt_algorithm)



def decode_token(token: str)-> Any | None:
    """ 校验并解析 token，返回 payload。"""
    try:
        return jwt.decode(
            token,
            _jwt_secret(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError:
        api_error(401, "UNAUTHORIZED", "token 无效或已过期")

