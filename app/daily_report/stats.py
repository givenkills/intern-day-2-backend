"""日报统计口径：所有数字都在这里由 SQL 算出来，模型只负责写文字。

口径（与 README「日报日期与统计口径」一节一一对应）：

1. 一条活动归属哪个日期，只看 `activities.plan_date`（本地自然日，Asia/Shanghai）。
   推迟不跨日——`postpone_activity` 要求新时间落在当天工作时段内，派生行的
   `plan_date` 与原记录相同，所以「一条活动只属于一个日期」天然成立。

2. `planned` = 当天 `status != 'postponed'` 的记录数。
   推迟链上的中间记录（`postponed`）不计入，只有链条末端那条算一次计划；
   否则一条活动推迟两次就会被数成三条活动。

3. `uncompleted` 用减法而不是再查一次 `status='planned'`：
   即使以后多出别的状态值，也不会出现「计划 5 条、完成 1、跳过 1、未完成 2」这种
   加起来对不上的情况。

4. `postponed_ops` 是「操作次数」，按 `activity_events.created_at` 落在当天
   [00:00, 次日 00:00) 区间统计，和活动数不是一个维度：
   一条活动可以既被推迟过、最后又完成了，两个数字会同时 +1，不能相加。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

from ..activity.timeutil import format_time, local_day_utc_bounds, minutes_of_local_iso

# 交给模型的明细条数上限，避免把整天的安排都塞进上下文。
MAX_BRIEF_ACTIVITIES = 8


@dataclass(frozen=True)
class ActivityBrief:
    """给模型看的活动摘要：只有标题、计划时刻、状态。"""

    title: str
    time: str
    status: str
    postponed: bool

    def as_payload(self) -> dict:
        return {
            "title": self.title,
            "planned_time": self.time,
            "status": self.status,
            "postponed": self.postponed,
        }


@dataclass(frozen=True)
class DailyStats:
    report_date: str
    planned: int
    completed: int
    skipped: int
    uncompleted: int
    postponed_ops: int
    sedentary_minutes: int | None
    activities: tuple[ActivityBrief, ...]

    @property
    def has_records(self) -> bool:
        """当天有没有可依据的记录。没有记录 ≠ 久坐 0 分钟。"""
        return self.planned > 0 or self.postponed_ops > 0

    def as_payload(self, max_activities: int = MAX_BRIEF_ACTIVITIES) -> dict:
        return {
            "report_date": self.report_date,
            "stats": {
                "planned": self.planned,
                "completed": self.completed,
                "skipped": self.skipped,
                "uncompleted": self.uncompleted,
                "postponed_ops": self.postponed_ops,
            },
            "sedentary": {
                "recorded": self.sedentary_minutes is not None,
                "minutes": self.sedentary_minutes,
            },
            "activities": [a.as_payload() for a in self.activities[:max_activities]],
        }


def build_stats(
    conn: sqlite3.Connection,
    user_id: int,
    report_date: date,
    sedentary_minutes: int | None = None,
) -> DailyStats:
    """按上面的口径算出一天的统计。纯读取，不写库。"""
    day = report_date.isoformat()

    rows = conn.execute(
        "SELECT title, scheduled_at, status, postponed_from_id FROM activities "
        "WHERE user_id = ? AND plan_date = ? ORDER BY scheduled_at",
        (user_id, day),
    ).fetchall()

    # 推迟链上的中间记录不算计划数，只有链条末端那条算
    active = [r for r in rows if r["status"] != "postponed"]
    completed = sum(1 for r in active if r["status"] == "completed")
    skipped = sum(1 for r in active if r["status"] == "skipped")
    planned = len(active)
    uncompleted = max(0, planned - completed - skipped)

    start_iso, end_iso = local_day_utc_bounds(report_date)
    postponed_ops = conn.execute(
        "SELECT COUNT(*) AS total FROM activity_events "
        "WHERE user_id = ? AND action = 'postpone' AND created_at >= ? AND created_at < ?",
        (user_id, start_iso, end_iso),
    ).fetchone()["total"]

    briefs = tuple(
        ActivityBrief(
            title=r["title"],
            time=format_time(minutes_of_local_iso(r["scheduled_at"])),
            status=r["status"],
            postponed=r["postponed_from_id"] is not None,
        )
        for r in active[:MAX_BRIEF_ACTIVITIES]
    )

    return DailyStats(
        report_date=day,
        planned=planned,
        completed=completed,
        skipped=skipped,
        uncompleted=uncompleted,
        postponed_ops=postponed_ops,
        sedentary_minutes=sedentary_minutes,
        activities=briefs,
    )
