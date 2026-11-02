"""幂等与并发：同一用户同一天只能有一份有效日报。

这一组是任务说明里点名要自动化测试的部分。
核心断言只有一个：不管跑几次、几个人同时抢，库里始终只有一行 success。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timedelta

import pytest
from dr_helpers import day_before

from app.activity.timeutil import TZ
from app.daily_report import store
from app.daily_report.stats import DailyStats
from app.db import connection
from app.security import to_iso

LEASE_SECONDS = 600


def intern_user_id() -> int:
    with connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = 'intern'").fetchone()
    return int(row["id"])


def empty_stats(day: date) -> DailyStats:
    return DailyStats(
        report_date=day.isoformat(),
        planned=0,
        completed=0,
        skipped=0,
        uncompleted=0,
        postponed_ops=0,
        sedentary_minutes=None,
        activities=(),
    )


def count_rows(user_id: int, day: date) -> int:
    with connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total FROM daily_reports WHERE user_id = ? AND report_date = ?",
            (user_id, day.isoformat()),
        ).fetchone()
    return int(row["total"])


def test_ensure_pending_is_idempotent():
    user_id = intern_user_id()
    day = day_before(60)

    with connection() as conn:
        store.ensure_pending(conn, user_id, day)
        store.ensure_pending(conn, user_id, day)
        store.ensure_pending(conn, user_id, day)

    assert count_rows(user_id, day) == 1


def test_unique_index_blocks_second_row():
    """唯一索引是最后一道防线：绕过 store 直接插也会被拒。"""
    user_id = intern_user_id()
    day = day_before(61)

    with connection() as conn:
        store.ensure_pending(conn, user_id, day)

    with pytest.raises(sqlite3.IntegrityError):
        with connection() as conn:
            conn.execute(
                "INSERT INTO daily_reports (user_id, report_date, status, created_at, updated_at) "
                "VALUES (?, ?, 'pending', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
                (user_id, day.isoformat()),
            )


def test_claim_can_only_be_taken_once():
    user_id = intern_user_id()
    day = day_before(62)

    with connection() as conn:
        store.ensure_pending(conn, user_id, day)
        assert store.claim(conn, user_id, day, LEASE_SECONDS) is True
        assert store.claim(conn, user_id, day, LEASE_SECONDS) is False


def test_success_is_not_regenerated():
    user_id = intern_user_id()
    day = day_before(63)

    with connection() as conn:
        store.ensure_pending(conn, user_id, day)
        assert store.claim(conn, user_id, day, LEASE_SECONDS)
        assert store.mark_success(
            conn, user_id, day, empty_stats(day), "回顾一", "建议一", "test-model"
        )
        # 再跑一次：抢不到，已有结果不被覆盖
        assert store.claim(conn, user_id, day, LEASE_SECONDS) is False
        row = store.get_report(conn, user_id, day)

    assert row["status"] == "success"
    assert row["review"] == "回顾一"
    assert row["attempt"] == 1


def test_failed_report_can_be_retried():
    user_id = intern_user_id()
    day = day_before(64)

    with connection() as conn:
        store.ensure_pending(conn, user_id, day)
        assert store.claim(conn, user_id, day, LEASE_SECONDS)
        assert store.mark_failed(conn, user_id, day, "AGENT_TIMEOUT", "上游超时")
        assert store.claim(conn, user_id, day, LEASE_SECONDS) is True
        row = store.get_report(conn, user_id, day)

    assert row["status"] == "running"
    assert row["attempt"] == 2
    # 重试时把上一次的错误清掉，避免页面同时显示"正在生成"和旧错误
    assert row["error_code"] is None


def test_stale_running_is_taken_over():
    """Worker 被杀后，租约过期的 running 允许被接管。"""
    user_id = intern_user_id()
    day = day_before(65)

    with connection() as conn:
        store.ensure_pending(conn, user_id, day)
        assert store.claim(conn, user_id, day, LEASE_SECONDS)
        # 模拟「十分钟前就卡住了」
        stale = to_iso(datetime.now(TZ) - timedelta(seconds=LEASE_SECONDS + 60))
        conn.execute(
            "UPDATE daily_reports SET locked_at = ? WHERE user_id = ? AND report_date = ?",
            (stale, user_id, day.isoformat()),
        )
        conn.commit()

        assert store.claim(conn, user_id, day, LEASE_SECONDS) is True
        row = store.get_report(conn, user_id, day)

    assert row["status"] == "running"
    assert row["attempt"] == 2


def test_mark_success_requires_running_state():
    """没有抢占成功就写不进去——防止覆盖别人已经做好的日报。"""
    user_id = intern_user_id()
    day = day_before(66)

    with connection() as conn:
        store.ensure_pending(conn, user_id, day)
        assert store.mark_success(
            conn, user_id, day, empty_stats(day), "x", "y", "m"
        ) is False


def test_concurrent_claim_only_one_wins():
    """四个线程同时抢同一行，只有一个能拿到。"""
    user_id = intern_user_id()
    day = day_before(67)

    with connection() as conn:
        store.ensure_pending(conn, user_id, day)

    results: list[bool] = []
    errors: list[str] = []
    lock = threading.Lock()

    def contend() -> None:
        try:
            with connection() as conn:
                won = store.claim(conn, user_id, day, LEASE_SECONDS)
            with lock:
                results.append(won)
        except Exception as exc:  # noqa: BLE001 - 线程里的异常不会自动让用例失败
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=contend) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == [], f"并发抢占不该抛异常：{errors}"
    assert results.count(True) == 1, f"应当只有一次抢占成功，实际 {results}"
    assert count_rows(user_id, day) == 1


def test_repeated_success_writes_keep_single_row_and_latest_content():
    """哪怕多次调用 mark_success，也只有一行、且只保留第一次写入的回顾。"""
    user_id = intern_user_id()
    day = day_before(68)

    with connection() as conn:
        store.ensure_pending(conn, user_id, day)
        assert store.claim(conn, user_id, day, LEASE_SECONDS)
        assert store.mark_success(conn, user_id, day, empty_stats(day), "第一次", "建议", "m")
        assert store.mark_success(conn, user_id, day, empty_stats(day), "第二次", "建议", "m") is False
        row = store.get_report(conn, user_id, day)

    assert count_rows(user_id, day) == 1
    assert row["review"] == "第一次"
