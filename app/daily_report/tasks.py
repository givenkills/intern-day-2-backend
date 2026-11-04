"""ARQ 任务：调度任务 + 按用户执行的任务，外加一个连通性自检。

两个任务分开的理由：
- 调度任务只负责「算出目标日期、找出该给谁做、逐个投递」，跑得快、失败面小；
- 总结任务只负责「一个用户 + 一个日期」，慢、会调模型、会失败。
一个用户失败不该影响别人，所以拆开、在循环里逐个处理。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from arq import Retry

from ..activity.timeutil import TZ
from ..config import get_settings
from ..db import connection
from ..security import to_iso, utcnow
from . import service, store
from .queue import job_id_for

logger = logging.getLogger(__name__)


def resolve_target_date(now=None) -> date:
    """算出这次要总结哪一天。

    默认「本地时间的昨天」：00:05 触发时，昨天已经完整结束，
    前一天最后几分钟的活动也能被覆盖到。
    DAILY_REPORT_TARGET_DATE 只用于演示和补跑，正常运行时留空。
    """
    settings = get_settings()
    if settings.daily_report_target_date.strip():
        raw = settings.daily_report_target_date.strip()
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"DAILY_REPORT_TARGET_DATE 不是合法日期：{raw}") from exc

    local_now = (now or utcnow()).astimezone(TZ)
    return (local_now - timedelta(days=1)).date()


async def ping(ctx) -> dict:
    """连通性自检：能跑通就说明 Worker 连上了 Redis、也能调起任务函数。"""
    return {"pong": True, "at": to_iso(utcnow())}


async def schedule_daily_reports(ctx) -> dict:
    """定时任务：到点后为每个需要日报的用户投递一条总结任务。"""
    day = resolve_target_date()
    with connection() as conn:
        users = store.users_for_date(conn, day)

    redis = ctx["redis"]
    enqueued = duplicates = failed = 0
    for user_id in users:
        try:
            job = await redis.enqueue_job(
                "generate_daily_report",
                user_id,
                day.isoformat(),
                _job_id=job_id_for(user_id, day),
            )
        except Exception:  # noqa: BLE001 - 单个用户失败不能拖住其他用户
            failed += 1
            logger.exception("投递失败 user=%s date=%s", user_id, day)
            continue

        if job is None:
            duplicates += 1
        else:
            enqueued += 1
            logger.info(
                "job_enqueued job_id=%s user=%s date=%s",
                job_id_for(user_id, day), user_id, day,
            )

    logger.info(
        "schedule_fired date=%s users=%d enqueued=%d duplicates=%d failed=%d",
        day, len(users), enqueued, duplicates, failed,
    )
    return {
        "date": day.isoformat(),
        "users": len(users),
        "enqueued": enqueued,
        "duplicates": duplicates,
        "failed": failed,
    }


async def generate_daily_report(ctx, user_id: int, report_date: str) -> dict:
    """执行任务：参数只有用户和日期，其余全部从数据库现读。"""
    try:
        day = date.fromisoformat(report_date)
    except (TypeError, ValueError):
        logger.error("参数错误 user=%s report_date=%r", user_id, report_date)
        return {"status": "failed", "code": "INVALID_DATE", "report_date": report_date}

    job_try = int(ctx.get("job_try", 1))
    result = await service.run_generate(user_id, day, job_try)

    # 只有"临时性"失败才重试；永久错误重试多少次都是同一个结果
    if result.get("status") == "failed" and result.get("transient"):
        max_tries = get_settings().daily_report_max_tries
        if job_try < max_tries:
            delay = 30 * job_try
            logger.warning(
                "job_retry user=%s date=%s attempt=%s code=%s defer=%ss",
                user_id, day, job_try, result.get("code"), delay,
            )
            raise Retry(defer=delay)
        logger.error(
            "job_failed_final user=%s date=%s attempt=%s code=%s",
            user_id, day, job_try, result.get("code"),
        )

    return result
