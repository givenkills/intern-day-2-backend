"""日报测试用的公共夹具函数（不是 pytest 插件，直接 import）。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.activity.timeutil import TZ, local_date_to_utc_iso
from app.db import connection
from app.security import to_iso, utcnow


def local_moment(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def add_activity(
    user_id: int,
    day: date,
    *,
    minute_of_day: int = 9 * 60 + 30,
    status: str = "planned",
    title: str = "站起来走动",
    activity_type: str = "walk",
    postponed_from_id: int | None = None,
) -> int:
    """直接往库里写一条活动，绕过排期算法（测试只关心统计）。"""
    now = to_iso(utcnow())
    with connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO activities (
                user_id, activity_type, title, description, plan_date, scheduled_at,
                duration_minutes, status, source, postponed_from_id, created_at, updated_at
            ) VALUES (?, ?, ?, '测试数据', ?, ?, 5, ?, 'system', ?, ?, ?)
            """,
            (
                user_id, activity_type, title, day.isoformat(),
                local_date_to_utc_iso(day, minute_of_day),
                status, postponed_from_id, now, now,
            ),
        )
        return int(cursor.lastrowid)


def add_event(user_id: int, activity_id: int, action: str, at: datetime) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO activity_events (activity_id, user_id, action, from_status, to_status, created_at) "
            "VALUES (?, ?, ?, NULL, NULL, ?)",
            (activity_id, user_id, action, to_iso(at)),
        )


def add_postpone(user_id: int, activity_id: int, at: datetime) -> None:
    add_event(user_id, activity_id, "postpone", at)


def make_postpone_chain(user_id: int, day: date, times: list[int]) -> list[int]:
    """造一条推迟链：前 n-1 条是 postponed，最后一条是 planned。

    times 是每次推迟后的本地分钟数，长度即条目数，第 1 条为最初的时间。
    """
    ids: list[int] = []
    previous: int | None = None
    for index, minutes in enumerate(times):
        is_last = index == len(times) - 1
        new_id = add_activity(
            user_id,
            day,
            minute_of_day=minutes,
            status="planned" if is_last else "postponed",
            postponed_from_id=previous,
        )
        if previous is not None:
            add_postpone(user_id, previous, local_moment(day, minutes // 60, minutes % 60))
        previous = new_id
        ids.append(new_id)
    return ids


def day_before(days: int) -> date:
    """取一个「已结束的自然日」，用于接口测试（必须在一年以内）。"""
    return (utcnow().astimezone(TZ).date() - timedelta(days=days))
