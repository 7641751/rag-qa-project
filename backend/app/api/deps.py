# rag_qa_project/backend/app/api/deps.py
"""鉴权依赖：从 Authorization 头解出当前用户。

`HTTPBearer(auto_error=False)` 是刻意的（spec §7.4 item 1）：默认 auto_error=True 会
自己抛 403 且响应体是 {"detail": ...}，与契约的 {code,message} 冲突。
这里自己判断 creds is None，走统一的 api_error。
"""
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis import asyncio as aioredis

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


async def get_redis_pool(request: Request) -> aioredis.Redis:
    """依赖注入：从 app.state 上的连接池取一个客户端。

    ⚠ 必须经 `request.app.state`，**不能**引用 main 里的全局 `app`：
      · main.py 在模块顶层 import 本模块所在的路由，deps 反过来 import main 就是
        循环导入（会以 partially initialized module 的形式炸）；
      · 全局单例在单测里也不可得 —— 每个用例各自新建一个 FastAPI 实例。
      这是 FastAPI 共享 lifespan 资源的官方写法。

    ⚠ None 判断不能省：Redis 未配置时 main.py 会把 redis_pool 置为 None（见那里的
      说明）。直接构造会得到「connection_pool=None」的隐式行为，报错点漂移到第一次
      真的发命令时，排查成本高得多 —— 这里就地给可操作的中文提示。
    """
    pool = getattr(request.app.state, "redis_pool", None)
    if pool is None:
        raise RuntimeError(
            "Redis 未配置：请在仓库根 .env 里设置 RAGQA_REDIS_URL（或 REDIS_URL）后重启")
    return aioredis.Redis(connection_pool=pool)