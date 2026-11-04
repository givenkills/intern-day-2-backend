"""统计口径测试：日期边界、推迟链、推迟次数与恒等式。

这些用例的价值在于把「口径」写成可执行的断言——
数值算错不会报错，只会让页面上出现一个看起来很正常但不对的数字。
"""

from __future__ import annotations

from datetime import date, timedelta

from dr_helpers import add_activity, add_postpone, local_moment, make_postpone_chain

from app.activity.timeutil import local_day_utc_bounds
from app.daily_report.stats import build_stats
from app.db import connection


def intern_user_id() -> int:
    with connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = 'intern'").fetchone()
    return int(row["id"])


def stats_of(user_id: int, day: date):
    with connection() as conn:
        return build_stats(conn, user_id, day)


def test_date_boundary_is_left_closed_right_open():
    """23:59 属于当天，次日 00:00 属于下一天——左闭右开。"""
    user_id = intern_user_id()
    day = date(2026, 7, 10)

    add_activity(user_id, day, minute_of_day=23 * 60 + 59, title="23:59 的活动")
    add_activity(user_id, day + timedelta(days=1), minute_of_day=0, title="00:00 的活动")

    assert stats_of(user_id, day).planned == 1
    assert stats_of(user_id, day + timedelta(days=1)).planned == 1


def test_utc_bounds_match_shanghai_midnight():
    """本地 00:00 对应的 UTC 是前一天 16:00，跨月跨年都不能偏。"""
    start, end = local_day_utc_bounds(date(2026, 11, 2))
    assert start == "2026-11-01T16:00:00+00:00"
    assert end == "2026-11-02T16:00:00+00:00"

    start, end = local_day_utc_bounds(date(2026, 1, 1))
    assert start == "2025-12-31T16:00:00+00:00"
    assert end == "2026-01-01T16:00:00+00:00"


def test_month_and_year_boundary_activities():
    user_id = intern_user_id()
    last_day = date(2025, 12, 31)
    new_year = date(2026, 1, 1)

    add_activity(user_id, last_day, minute_of_day=23 * 60 + 30, title="跨年前")
    add_activity(user_id, new_year, minute_of_day=30, title="跨年后")

    assert stats_of(user_id, last_day).planned == 1
    assert stats_of(user_id, new_year).planned == 1


def test_counts_completed_skipped_and_uncompleted():
    user_id = intern_user_id()
    day = date(2026, 9, 11)

    for i in range(3):
        add_activity(user_id, day, minute_of_day=600 + i * 60, status="completed")
    add_activity(user_id, day, minute_of_day=900, status="skipped")
    for i in range(2):
        add_activity(user_id, day, minute_of_day=1000 + i * 60, status="planned")

    stats = stats_of(user_id, day)
    assert (stats.planned, stats.completed, stats.skipped, stats.uncompleted) == (6, 3, 1, 2)
    assert stats.planned == stats.completed + stats.skipped + stats.uncompleted


def test_postpone_chain_counts_as_one_activity():
    """推迟两次 = 3 条记录，但只算 1 项计划、2 次推迟操作。"""
    user_id = intern_user_id()
    day = date(2026, 8, 12)

    make_postpone_chain(user_id, day, [9 * 60, 10 * 60, 11 * 60])

    stats = stats_of(user_id, day)
    assert stats.planned == 1, "推迟链中间记录不该被数成多条活动"
    assert stats.uncompleted == 1
    assert stats.postponed_ops == 2


def test_postponed_ops_counts_by_operation_date():
    """推迟次数按「操作发生日」归集，不按活动归属日。"""
    user_id = intern_user_id()
    day = date(2026, 6, 13)

    activity_id = add_activity(user_id, day, minute_of_day=9 * 60)
    add_postpone(user_id, activity_id, local_moment(day, 9, 0))
    add_postpone(user_id, activity_id, local_moment(day, 23, 59))

    next_day = day + timedelta(days=1)
    add_postpone(user_id, activity_id, local_moment(next_day, 0, 0))

    assert stats_of(user_id, day).postponed_ops == 2
    assert stats_of(user_id, next_day).postponed_ops == 1


def test_postponed_then_completed_overlaps_but_does_not_double_count():
    """一条活动推迟后又完成：完成 +1、推迟 +1，计划仍然只 +1。"""
    user_id = intern_user_id()
    day = date(2026, 5, 14)

    original = add_activity(user_id, day, minute_of_day=9 * 60, status="postponed")
    add_postpone(user_id, original, local_moment(day, 9, 0))
    add_activity(user_id, day, minute_of_day=16 * 60, status="completed", postponed_from_id=original)

    stats = stats_of(user_id, day)
    assert stats.planned == 1
    assert stats.completed == 1
    assert stats.postponed_ops == 1
    # 推迟次数和完成数会同时出现，相加就会得到 2 > 计划数 1 的荒谬结论
    assert stats.completed + stats.postponed_ops == 2
    assert stats.planned == 1


def test_invariant_holds_for_mixed_records():
    user_id = intern_user_id()
    day = date(2026, 4, 15)

    make_postpone_chain(user_id, day, [9 * 60, 10 * 60])
    add_activity(user_id, day, minute_of_day=11 * 60, status="completed")
    add_activity(user_id, day, minute_of_day=12 * 60, status="skipped")
    add_activity(user_id, day, minute_of_day=13 * 60, status="planned")

    stats = stats_of(user_id, day)
    assert stats.planned == 4
    assert stats.planned == stats.completed + stats.skipped + stats.uncompleted


def test_day_without_records_is_zero_not_fabricated():
    user_id = intern_user_id()
    day = date(2026, 3, 16)

    stats = stats_of(user_id, day)
    assert (stats.planned, stats.completed, stats.skipped, stats.uncompleted) == (0, 0, 0, 0)
    assert stats.postponed_ops == 0
    assert stats.has_records is False
    # 没有记录 ≠ 久坐 0 分钟
    assert stats.sedentary_minutes is None


def test_stats_are_scoped_to_one_user():
    """另一个用户的同日记录不能混进来。"""
    user_id = intern_user_id()
    day = date(2026, 2, 17)

    with connection() as conn:
        other = conn.execute(
            "INSERT INTO users (username, phone, display_name, password_hash, created_at) "
            "VALUES ('stats-other', NULL, '他人', 'x', '2026-02-17T00:00:00+00:00')"
        ).lastrowid

    add_activity(int(other), day, minute_of_day=9 * 60, status="completed")
    add_activity(user_id, day, minute_of_day=10 * 60, status="completed")

    assert stats_of(user_id, day).planned == 1
    assert stats_of(int(other), day).planned == 1
