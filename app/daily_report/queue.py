"""Redis 连接与入队。

三件事必须在这里做对：
1. 入队要么成功、要么抛错——不能"看起来排队了，其实 Redis 没收到"。
2. `_job_id` 用「用户 + 日期」拼，重复点击/重复调度不会排两条。
3. Redis 不可用时快速失败，不让接口挂在那儿等连接超时。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from ..config import get_settings

logger = logging.getLogger(__name__)

# 建立连接最多等这么久；Redis 没起来时宁可立刻 503，也不要让请求悬着
CONNECT_TIMEOUT_SECONDS = 5.0

_pool: ArqRedis | None = None


class QueueUnavailable(RuntimeError):
    """连不上 Redis，或入队失败。接口层据此返回 503。"""


def job_id_for(user_id: int, day: date, generation: int = 1) -> str:
    """稳定的任务标识：日志、数据库、接口返回都能用同一个字符串对上。

    generation 只在「之前那次已经跑完（成功或失败）」时递增：
    ARQ 的任务 key 会保留到 keep_result 过期，同一个 id 再入队会返回 None，
    如果失败后还用原 id，补跑就永远排不进去——看起来像「已在队列中」，其实什么也没发生。
    """
    base = f"daily-report:{user_id}:{day.isoformat()}"
    return base if generation <= 1 else f"{base}:r{generation}"


async def get_pool() -> ArqRedis:
    """惰性建池。Redis 起晚了也能在下次请求时接上。"""
    global _pool
    if _pool is None:
        settings = get_settings()
        try:
            _pool = await asyncio.wait_for(
                create_pool(RedisSettings.from_dsn(settings.redis_url)),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 - 连接类异常类型不稳定
            logger.warning("连接 Redis 失败: %s", exc)
            raise QueueUnavailable(str(exc)) from exc
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def enqueue_report(user_id: int, day: date, generation: int = 1) -> tuple[str, bool]:
    """投递一条日报任务。

    返回 (job_id, 是否新入队)。返回 False 表示 Redis 里已经有同 id 的任务
    还没跑完——这不是错误，调用方按「已在队列中」回给前端即可。
    """
    pool = await get_pool()
    job_id = job_id_for(user_id, day, generation)
    try:
        job = await pool.enqueue_job(
            "generate_daily_report",
            user_id,
            day.isoformat(),
            _job_id=job_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("入队失败 user=%s date=%s: %s", user_id, day, exc)
        raise QueueUnavailable(str(exc)) from exc

    if job is None:
        logger.info("任务已在队列中，跳过重复入队 job_id=%s", job_id)
        return job_id, False

    logger.info("job_enqueued job_id=%s user=%s date=%s", job_id, user_id, day)
    return job_id, True
