"""daily_reports 的读写：幂等靠唯一索引 + 条件更新，不靠「先查再插」。

状态机：pending（已占位）→ running（某个 Worker 抢到了）→ success | failed

三条不变量：
1. 一个 (user_id, report_date) 永远只有一行 —— `idx_reports_user_date` 保证。
2. 只有 `status='running'` 的行能被写成功/写失败 —— 防止覆盖别人已经做好的结果。
3. 抢占成功才继续执行，抢不到就直接退出 —— 并发、重跑、进程重启都收敛到一份。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from ..activity.timeutil import utc_to_local_iso
from ..security import to_iso, utcnow
from .stats import DailyStats

# 错误码：前三个可以重试，后两个是永久错误，不该浪费重试次数
TRANSIENT_CODES = ("AGENT_TIMEOUT", "AGENT_UPSTREAM_ERROR", "DB_ERROR")
PERMANENT_CODES = ("CONFIG_MISSING", "AGENT_OUTPUT_INVALID", "AGENT_GROUNDING_FAILED", "STATS_FAILED")

# 页面只展示这一句，详细原因留在 error_detail 里给开发看
ERROR_MESSAGES = {
    "AGENT_TIMEOUT": "模型响应超时，总结未生成",
    "AGENT_UPSTREAM_ERROR": "模型服务暂时不可用，总结未生成",
    "AGENT_OUTPUT_INVALID": "模型输出不符合要求，总结未生成",
    "AGENT_GROUNDING_FAILED": "总结与记录不一致，已拒绝保存",
    "CONFIG_MISSING": "未配置模型密钥，无法生成总结",
    "DB_ERROR": "数据库写入失败",
    "STATS_FAILED": "统计活动记录失败",
}


def ensure_pending(conn: sqlite3.Connection, user_id: int, day: date) -> None:
    """占位：没有这一行就插一条 pending，有则什么都不做。

    必须是 INSERT ... ON CONFLICT DO NOTHING：先 SELECT 再 INSERT 在并发下会撞
    唯一索引，或者更糟——两个请求都"以为"自己是第一个。
    """
    now = to_iso(utcnow())
    conn.execute(
        "INSERT INTO daily_reports (user_id, report_date, status, created_at, updated_at) "
        "VALUES (?, ?, 'pending', ?, ?) "
        "ON CONFLICT(user_id, report_date) DO NOTHING",
        (user_id, day.isoformat(), now, now),
    )
    conn.commit()


def claim(conn: sqlite3.Connection, user_id: int, day: date, lease_seconds: int) -> bool:
    """抢占执行权，返回是否抢到。

    能拿到的情况只有三种：还没人做过（pending）、上次失败了（failed）、
    上次的 Worker 死了且租约已过期（running 且 locked_at 太旧）。
    已有成功结果时 rowcount 为 0，调用方直接复用，不重算。
    """
    now = utcnow()
    now_iso = to_iso(now)
    cutoff = to_iso(now - timedelta(seconds=lease_seconds))
    cursor = conn.execute(
        """
        UPDATE daily_reports
           SET status = 'running',
               attempt = attempt + 1,
               locked_at = ?,
               updated_at = ?,
               error_code = NULL,
               error_detail = NULL
         WHERE user_id = ? AND report_date = ?
           AND (
                 status IN ('pending', 'failed')
                 OR (status = 'running' AND (locked_at IS NULL OR locked_at < ?))
               )
        """,
        (now_iso, now_iso, user_id, day.isoformat(), cutoff),
    )
    conn.commit()
    return cursor.rowcount > 0


def mark_success(
    conn: sqlite3.Connection,
    user_id: int,
    day: date,
    stats: DailyStats,
    review: str,
    suggestion: str,
    model: str,
) -> bool:
    """写入统计与总结。只允许改自己抢到的 running 行。"""
    now_iso = to_iso(utcnow())

    # 统计值再判一次恒等式，写库前最后一道闸
    if stats.planned != stats.completed + stats.skipped + stats.uncompleted:
        raise ValueError("统计口径不一致：planned != completed + skipped + uncompleted")

    cursor = conn.execute(
        """
        UPDATE daily_reports
           SET status = 'success',
               planned_count = ?, completed_count = ?, skipped_count = ?,
               uncompleted_count = ?, postponed_count = ?, sedentary_minutes = ?,
               review = ?, suggestion = ?, model = ?,
               generated_at = ?, locked_at = NULL, updated_at = ?,
               error_code = NULL, error_detail = NULL
         WHERE user_id = ? AND report_date = ? AND status = 'running'
        """,
        (
            stats.planned, stats.completed, stats.skipped, stats.uncompleted,
            stats.postponed_ops, stats.sedentary_minutes,
            review, suggestion, model,
            now_iso, now_iso, user_id, day.isoformat(),
        ),
    )
    conn.commit()
    return cursor.rowcount > 0


def mark_failed(
    conn: sqlite3.Connection,
    user_id: int,
    day: date,
    code: str,
    detail: str | None = None,
) -> bool:
    """标记失败。detail 只进库，不出接口——里面可能有上游返回的原始报错。"""
    now_iso = to_iso(utcnow())
    cursor = conn.execute(
        """
        UPDATE daily_reports
           SET status = 'failed', error_code = ?, error_detail = ?,
               locked_at = NULL, updated_at = ?
         WHERE user_id = ? AND report_date = ? AND status = 'running'
        """,
        (code, (detail or "")[:500], now_iso, user_id, day.isoformat()),
    )
    conn.commit()
    return cursor.rowcount > 0


def get_report(conn: sqlite3.Connection, user_id: int, day: date) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM daily_reports WHERE user_id = ? AND report_date = ?",
        (user_id, day.isoformat()),
    ).fetchone()


def lease_expired(row: sqlite3.Row, lease_seconds: int) -> bool:
    """租约是否已过期。和 claim 里的 SQL 判据保持一致，供接口层复用。"""
    locked_at = row["locked_at"]
    if not locked_at:
        return True
    cutoff = to_iso(utcnow() - timedelta(seconds=lease_seconds))
    return locked_at < cutoff


def users_for_date(conn: sqlite3.Connection, day: date) -> list[int]:
    """需要为这一天生成日报的用户。

    取并集而不是只取「当天有活动的人」：没有活动的用户也该拿到一份
    「当日无记录」的总结，否则页面会永远停在「尚未生成」。
    """
    rows = conn.execute(
        """
        SELECT user_id FROM activity_settings
        UNION
        SELECT user_id FROM activities WHERE plan_date = ?
        UNION
        SELECT user_id FROM daily_reports WHERE report_date = ?
        """,
        (day.isoformat(), day.isoformat()),
    ).fetchall()
    return sorted({r["user_id"] for r in rows})


def to_api(row: sqlite3.Row | None, day: date) -> dict:
    """把一行日报整理成接口返回结构。没有行时返回「尚未生成」。"""
    if row is None:
        return {
            "report_date": day.isoformat(),
            "status": "not_generated",
            "stats": None,
            "review": None,
            "suggestion": None,
            "sedentary": {"recorded": False, "minutes": None},
            "generated_at": None,
            "attempt": 0,
            "error": None,
        }

    has_stats = row["status"] == "success"
    stats = (
        {
            "planned": row["planned_count"],
            "completed": row["completed_count"],
            "skipped": row["skipped_count"],
            "uncompleted": row["uncompleted_count"],
            "postponed_ops": row["postponed_count"],
        }
        if has_stats
        else None
    )

    error = None
    if row["status"] == "failed" and row["error_code"]:
        error = {
            "code": row["error_code"],
            "message": ERROR_MESSAGES.get(row["error_code"], "总结生成失败，可稍后重试"),
        }

    return {
        "report_date": row["report_date"],
        "status": row["status"],
        "stats": stats,
        "review": row["review"],
        "suggestion": row["suggestion"],
        "sedentary": {
            "recorded": row["sedentary_minutes"] is not None,
            "minutes": row["sedentary_minutes"],
        },
        "generated_at": utc_to_local_iso(row["generated_at"]) if row["generated_at"] else None,
        "attempt": row["attempt"],
        "error": error,
    }
