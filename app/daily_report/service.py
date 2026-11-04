"""一条日报任务的执行编排：占位 → 抢占 → 统计 → 模型 → 校验 → 落库。

这里刻意把流程切成三段，中间那一段不持有任何数据库连接：

    [短事务] 占位 + 抢占 + 读活动，结束即关连接
    [无连接] 等模型（最长 45s × 2 次），此时库里没有本任务的写锁
    [短事务] 校验通过后一次写入

Worker 与 API 是两个进程共用一个 SQLite 文件，只要有一段代码"拿着事务等模型"，
另一个进程的写请求就会卡到 busy_timeout 之后失败。
"""

from __future__ import annotations

import logging
from datetime import date

from ..config import get_settings
from ..db import connection
from . import agent, stats, store
from .agent import ReportAgentError

logger = logging.getLogger(__name__)


async def run_generate(user_id: int, day: date, job_try: int = 1) -> dict:
    """执行一条日报任务。返回结果字典，不抛业务异常（重试决策交给调用方）。"""
    settings = get_settings()

    # ---- 第一段：占位、抢占、算统计 ----
    with connection() as conn:
        store.ensure_pending(conn, user_id, day)
        if not store.claim(conn, user_id, day, settings.daily_report_lease_seconds):
            row = store.get_report(conn, user_id, day)
            current = row["status"] if row else "unknown"
            logger.info(
                "job_skipped user=%s date=%s reason=already_%s", user_id, day, current
            )
            return {
                "status": "skipped",
                "reason": f"already_{current}",
                "transient": False,
            }

        try:
            day_stats = stats.build_stats(conn, user_id, day)
        except Exception as exc:  # noqa: BLE001
            logger.exception("统计失败 user=%s date=%s", user_id, day)
            store.mark_failed(conn, user_id, day, "STATS_FAILED", str(exc))
            return {"status": "failed", "code": "STATS_FAILED", "transient": False}

    logger.info(
        "job_started user=%s date=%s attempt=%s "
        "planned=%s completed=%s skipped=%s uncompleted=%s postponed_ops=%s",
        user_id, day, job_try,
        day_stats.planned, day_stats.completed, day_stats.skipped,
        day_stats.uncompleted, day_stats.postponed_ops,
    )

    # 没有密钥属于配置缺失，重试多少次都一样，直接判失败
    if not settings.agent_ready:
        with connection() as conn:
            store.mark_failed(conn, user_id, day, "CONFIG_MISSING", "QWEN_API_KEY 为空")
        logger.error("未配置 QWEN_API_KEY，无法生成日报 user=%s date=%s", user_id, day)
        return {"status": "failed", "code": "CONFIG_MISSING", "transient": False}

    # ---- 第二段：调模型（不持有数据库连接）----
    try:
        draft = await agent.generate_review(day_stats)
    except ReportAgentError as exc:
        with connection() as conn:
            store.mark_failed(conn, user_id, day, exc.code, exc.detail)
        return {
            "status": "failed",
            "code": exc.code,
            "transient": exc.code in store.TRANSIENT_CODES,
            "detail": exc.detail,
        }

    # ---- 第三段：写入结果 ----
    with connection() as conn:
        saved = store.mark_success(
            conn, user_id, day, day_stats, draft.review, draft.suggestion, settings.qwen_model
        )

    if not saved:
        # 抢到 running 之后被别人接管并写完了（租约过期场景），不覆盖已有结果
        logger.warning("结果未写入：记录已不是 running 状态 user=%s date=%s", user_id, day)
        return {"status": "skipped", "reason": "claim_lost", "transient": False}

    logger.info(
        "job_succeeded user=%s date=%s attempt=%s review_len=%s",
        user_id, day, job_try, len(draft.review),
    )
    return {
        "status": "success",
        "report_date": day.isoformat(),
        "planned": day_stats.planned,
        "completed": day_stats.completed,
        "postponed_ops": day_stats.postponed_ops,
        "transient": False,
    }
