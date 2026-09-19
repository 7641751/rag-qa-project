# rag_qa_project/backend/app/api/deps.py
"""鉴权依赖：从 Authorization 头解出当前用户。

`HTTPBearer(auto_error=False)` 是刻意的（spec §7.4 item 1）：默认 auto_error=True 会
自己抛 403 且响应体是 {"detail": ...}，与契约的 {code,message} 冲突。
这里自己判断 creds is None，走统一的 api_error。
"""
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.agent.schemas import AuthUser
from backend.tools.http_tools import api_error
from backend.tools.security import decode_token

# auto_error=False：缺头/格式错时返回 None 而不是自己抛 403+{"detail"}
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AuthUser:
    """解析 Bearer token → AuthUser。

    失败一律 401 UNAUTHORIZED（decode_token 内部的过期/篡改/垃圾串都归这一句），
    文案不区分具体原因 —— 对客户端都是"重新登录"。
    """
    if creds is None or (creds.scheme or "").lower() != "bearer":
        api_error(401, "UNAUTHORIZED", "缺少或无效的 Authorization 头")

    payload = decode_token(creds.credentials)
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        api_error(401, "UNAUTHORIZED", "token 无效或已过期")
    return AuthUser(id=user_id, username=payload.get("username", ""))