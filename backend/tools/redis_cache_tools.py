# rag_qa_project/backend/tools/redis_cache_tools.py
# -*- coding: utf-8 -*-
"""Redis 读缓存的通用工具（cache-aside）。纯工具层：不碰请求上下文、不碰 DB。

**定位**：缓存是**可丢的加速层**。本模块的每个函数都遵守「缓存故障不得升级为业务故障」：
任何 Redis 异常都被吞掉并记日志，调用方永远拿到可用数据。

**不要用它缓存真相源**（P4 设计文档 §2）：`conversations` 这类作为授权依据的数据永远
不会进这里 —— 一次缓存不一致就是越权。这里只放「丢了也能从 MySQL 重算」的东西。
"""
import json
import logging

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


def conv_list_key(user_id: int) -> str:
    """会话列表缓存的键。

    `:v1:` 是**结构版本位**：将来改缓存里的 JSON 结构时，把 v1 改成 v2 即可让旧键自然
    不可达、随 TTL 消失 —— 不必写迁移脚本，也不必记得发版前手动清 Redis（那一步总有人忘）。
    """
    return f"ragqa:conv:list:v1:{user_id}"


async def cached_json(redis, key: str, ttl: int, loader) -> object:
    """cache-aside 读：命中直接返回；miss / 坏值 / 任何异常都回源。

    Args:
        redis: `redis.asyncio.Redis`；**传 None 表示缓存不可用**（未配置或开关关闭），
               此时等价于直接调 loader —— 调用方因此不必到处判断开关。
        ttl: 写回时的秒级过期时间。它只作**兜底**：主策略是写路径主动删键。
        loader: 无参协程函数，负责回源（本项目里就是今天那段 MySQL 查询）。

    Returns:
        loader 的返回值，或缓存里解出来的 JSON 对象。
    """
    if redis is not None:
        try:
            raw = await redis.get(key)
        except RedisError as exc:                      # 连接类：降级，且别再尝试写
            logger.warning("[cache] 读失败，降级回源 key=%s: %s", key, exc)
            return await loader()
        except Exception as exc:                       # 代码 bug 也不该拖垮业务
            logger.error("[cache] 读异常，降级回源 key=%s: %s", key, exc)
            return await loader()

        if raw is not None:
            try:
                return json.loads(raw)
            except (ValueError, TypeError) as exc:
                # 坏值必须清掉：否则每次请求都白读一次，且永远不会自愈
                logger.warning("[cache] 值损坏，删除并回源 key=%s: %s", key, exc)
                try:
                    await redis.delete(key)
                except Exception:
                    pass                               # 删不掉也无妨，TTL 会兜底
                return await loader()
            # 命中**直接返回**：不写回、不续期。刻意不刷新 TTL —— 否则热点键永不过期，
            # 数据改了也长期不收敛（就是「缓存雪崩」笔记里那个经典反例）。

        # miss：回源并写回
        value = await loader()
        await _try_set(redis, key, value, ttl)
        return value

    # 缓存不可用：等价于无缓存
    return await loader()


async def _try_set(redis, key: str, value, ttl: int) -> None:
    """写回缓存；失败只记日志（下一次继续 miss，仅损失命中率，不影响正确性）。"""
    try:
        await redis.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
    except Exception as exc:
        logger.warning("[cache] 写回失败 key=%s: %s", key, exc)


async def invalidate(redis, key: str) -> None:
    """删键失效。**必须在 MySQL commit 之后调用**（顺序见 P4 设计文档 §4）。

    反过来（先删键再写库）会留下「库还是旧值、缓存已空」的窗口：并发读会把旧值重新
    填进缓存 —— 且这次要等 TTL 才会自愈。
    """
    if redis is None:
        return
    try:
        await redis.delete(key)
    except Exception as exc:
        logger.warning("[cache] 失效失败 key=%s: %s", key, exc)
