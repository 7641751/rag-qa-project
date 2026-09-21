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

from config import settings
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


async def get_optional_redis(request: Request) -> aioredis.Redis | None:
    """可选的 Redis（缓存用）：不可用时返回 None，由调用方降级回源。

    与 `get_redis_pool` 的分工必须分清：
      · `get_redis_pool`  —— **硬依赖**：拿不到就报可操作的错（将来真有离不开 Redis 的功能用它）
      · `get_optional_redis` —— **软依赖**：缓存而已，没有照常工作（P4 设计文档 §7 的核心原则）

    开关 `redis_cache_enabled=False` 时同样返回 None，这样「回滚开关」对调用方完全透明 ——
    调用方只有一个判断（`redis is None` ⇒ 无缓存），不必再关心开关或配置。

    ⚠ 必须把池**包成客户端**再返回，与 `get_redis_pool` 同理：`app.state.redis_pool` 是
      `redis.asyncio.connection.ConnectionPool`，它**没有任何命令方法**（已实测：无 get/set/xadd）。
      直接把它交给 `redis_cache_tools` 会让 `await redis.get(...)` 抛 AttributeError，
      再被缓存层的宽兜底吞成一条 error 日志 —— 表现为「缓存接好了但命中率恒为 0」，
      是本功能最难排查的一类假成功。包一层也让两者共用同一个池（不新建连接池）。
    """
    if not settings.redis_cache_enabled:
        return None
    pool = getattr(request.app.state, "redis_pool", None)
    if pool is None:
        # 注意与 get_redis_pool 的差别：这里**不抛异常**。Redis 不可用对缓存只是降级，
        # 调用方拿到 None 就走 MySQL 直查（P4 设计文档 §7）。
        return None
    return aioredis.Redis(connection_pool=pool)