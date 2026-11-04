"""给演示准备一天的活动数据（直接写库，不走接口）。

用法（容器内或本机均可）：
    python scripts/seed_demo_data.py --date 2026-11-01
    python scripts/seed_demo_data.py --date 2026-11-01 --user intern

造出来的数据覆盖四种状态 + 一次推迟：
    2 条已完成、1 条跳过、1 条未完成（planned）、1 条推迟后完成
于是统计口径应当是：
    planned=5  completed=3  skipped=1  uncompleted=1  postponed_ops=1

注意：事件时间戳必须落在**目标日期内**。如果图省事用「当前时间」，
生成昨天的日报时推迟次数会是 0——因为那条操作记录属于今天。
已有同一天的活动则直接退出，不重复造数据。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.activity.kinds import kind_of  # noqa: E402
from app.activity.timeutil import local_date_to_utc_iso  # noqa: E402
from app.db import connection, init_db  # noqa: E402
from app.security import to_iso, utcnow  # noqa: E402


def _insert(
    conn,
    user_id: int,
    day: date,
    minutes: int,
    kind_type: str,
    status: str,
    postponed_from: int | None,
) -> int:
    """插入一条活动，并补一条同时刻的 create 事件。"""
    at = local_date_to_utc_iso(day, minutes)
    kind = kind_of(kind_type)
    cursor = conn.execute(
        """
        INSERT INTO activities (
            user_id, activity_type, title, description, plan_date, scheduled_at,
            duration_minutes, status, source, postponed_from_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 5, ?, 'system', ?, ?, ?)
        """,
        (
            user_id, kind.type, kind.title, kind.description, day.isoformat(),
            at, status, postponed_from, at, at,
        ),
    )
    activity_id = int(cursor.lastrowid)
    _event(conn, activity_id, user_id, "create", None, "planned", at)
    return activity_id


def _event(conn, activity_id: int, user_id: int, action: str, frm: str | None,
           to: str | None, at: str) -> None:
    conn.execute(
        "INSERT INTO activity_events (activity_id, user_id, action, from_status, to_status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (activity_id, user_id, action, frm, to, at),
    )


def ensure_settings(conn, user_id: int, now: str) -> None:
    conn.execute(
        """
        INSERT INTO activity_settings (
            user_id, work_start, work_end, morning_start, morning_end,
            afternoon_start, afternoon_end, daily_target, reminders_enabled,
            allow_in_worktime, postpone_policy, timezone, updated_at
        ) VALUES (?, '09:00', '18:00', '10:00', '12:00', '14:00', '17:30', 3, 1, 1,
                  'next_slot', 'Asia/Shanghai', ?)
        ON CONFLICT(user_id) DO NOTHING
        """,
        (user_id, now),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="准备某一天的演示活动数据")
    parser.add_argument("--date", required=True, help="目标日期 YYYY-MM-DD（通常是昨天）")
    parser.add_argument("--user", default="intern", help="账号用户名，默认 intern")
    args = parser.parse_args()

    try:
        day = date.fromisoformat(args.date)
    except ValueError:
        print(f"日期格式不对：{args.date}")
        return 2

    init_db()
    now = to_iso(utcnow())

    with connection() as conn:
        user = conn.execute(
            "SELECT id, display_name FROM users WHERE username = ?", (args.user,)
        ).fetchone()
        if user is None:
            print(f"找不到账号 {args.user}，先启动后端让它建练习账号，或换个 --user")
            return 2
        user_id = int(user["id"])

        existing = conn.execute(
            "SELECT COUNT(*) AS total FROM activities WHERE user_id = ? AND plan_date = ?",
            (user_id, day.isoformat()),
        ).fetchone()["total"]
        if existing:
            print(f"{args.user} 在 {day} 已有 {existing} 条活动，跳过（不重复造数据）")
            return 0

        ensure_settings(conn, user_id, now)

        a1 = _insert(conn, user_id, day, 9 * 60 + 30, "walk", "completed", None)
        a2 = _insert(conn, user_id, day, 11 * 60, "water", "completed", None)
        a3 = _insert(conn, user_id, day, 14 * 60, "stretch", "skipped", None)
        _insert(conn, user_id, day, 16 * 60, "stretch", "planned", None)

        # 推迟链：原记录 postponed + 派生记录 completed，推迟操作记 1 次
        origin = _insert(conn, user_id, day, 17 * 60, "walk", "postponed", None)
        tail = _insert(conn, user_id, day, 17 * 60 + 30, "walk", "completed", origin)

        _event(conn, a1, user_id, "complete", "planned", "completed", local_date_to_utc_iso(day, 9 * 60 + 30))
        _event(conn, a2, user_id, "complete", "planned", "completed", local_date_to_utc_iso(day, 11 * 60))
        _event(conn, a3, user_id, "skip", "planned", "skipped", local_date_to_utc_iso(day, 14 * 60))
        _event(conn, origin, user_id, "postpone", "planned", "postponed",
               local_date_to_utc_iso(day, 17 * 60))
        _event(conn, tail, user_id, "complete", "planned", "completed",
               local_date_to_utc_iso(day, 17 * 60 + 30))

        for activity_id, minutes in ((a1, 9 * 60 + 30), (a2, 11 * 60), (tail, 17 * 60 + 30)):
            conn.execute(
                "UPDATE activities SET completed_at = ? WHERE id = ?",
                (local_date_to_utc_iso(day, minutes), activity_id),
            )

    print(f"已为 {args.user} 造好 {day} 的演示数据：planned=5 completed=3 skipped=1 "
          f"uncompleted=1 postponed_ops=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
