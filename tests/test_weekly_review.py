"""周度复盘接口测试：时区、周边界、完成率、推迟归属与权限。

口径：
- 一周从用户时区的周一 00:00 开始，到下一个周一 00:00 结束；
- planned 只数最终记录（status != postponed）；
- postponed_activities 按活动只数一次，postponed_ops 是操作次数，两者独立；
- planned=0 时 completion_rate 为 None，不伪造 0%。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.activity.timeutil import TZ, local_today
from app.config import get_settings
from app.security import hash_password, to_iso, utcnow

WEEK_START = date(2026, 10, 26)
WEEK_END = date(2026, 11, 1)


@pytest.fixture(autouse=True)
def _clean_activities():
    conn = sqlite3.connect(get_settings().database_path)
    conn.execute("DELETE FROM activity_events")
    conn.execute("DELETE FROM activities")
    conn.execute("DELETE FROM quiet_periods")
    conn.execute("DELETE FROM activity_settings")
    conn.commit()
    conn.close()
    yield


def _user_id(username: str = "intern") -> int:
    conn = sqlite3.connect(get_settings().database_path)
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    assert row is not None, f"missing test user {username}"
    return int(row[0])


def _create_other_user() -> int:
    username = f"other-{uuid.uuid4().hex[:8]}"
    conn = sqlite3.connect(get_settings().database_path)
    cursor = conn.execute(
        "INSERT INTO users (username, display_name, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (username, "其他同学", hash_password("other123456"), to_iso(utcnow())),
    )
    conn.commit()
    user_id = int(cursor.lastrowid)
    conn.close()
    return user_id


def _insert_activity(
    user_id: int,
    *,
    scheduled_at_utc: datetime,
    status: str = "planned",
    title: str = "远眺",
    source: str = "system",
    postponed_from_id: int | None = None,
    plan_date: str | None = None,
) -> int:
    if scheduled_at_utc.tzinfo is None:
        scheduled_at_utc = scheduled_at_utc.replace(tzinfo=UTC)
    local_day = scheduled_at_utc.astimezone(TZ).date().isoformat()
    now = to_iso(utcnow())
    conn = sqlite3.connect(get_settings().database_path)
    cursor = conn.execute(
        """
        INSERT INTO activities (
            user_id, activity_type, title, description, plan_date, scheduled_at,
            duration_minutes, status, source, postponed_from_id, created_at, updated_at
        ) VALUES (?, 'stretch', ?, '', ?, ?, 5, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            title,
            plan_date or local_day,
            scheduled_at_utc.astimezone(UTC).isoformat(timespec="seconds"),
            status,
            source,
            postponed_from_id,
            now,
            now,
        ),
    )
    conn.commit()
    activity_id = int(cursor.lastrowid)
    conn.close()
    return activity_id


def _insert_postpone_event(user_id: int, activity_id: int, created_at_utc: datetime) -> None:
    conn = sqlite3.connect(get_settings().database_path)
    conn.execute(
        "INSERT INTO activity_events (activity_id, user_id, action, from_status, to_status, created_at) "
        "VALUES (?, ?, 'postpone', 'planned', 'postponed', ?)",
        (activity_id, user_id, created_at_utc.astimezone(UTC).isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()


def _get_week(client, headers, **params):
    return client.get("/activity/week", params=params, headers=headers)


def test_week_review_returns_days_rate_and_empty_days(client, auth_headers):
    user_id = _user_id()
    _insert_activity(
        user_id,
        scheduled_at_utc=datetime(2026, 10, 26, 2, 0, tzinfo=UTC),  # 周一 10:00 +08
        status="completed",
    )
    _insert_activity(
        user_id,
        scheduled_at_utc=datetime(2026, 10, 27, 2, 0, tzinfo=UTC),
        status="planned",
    )
    _insert_activity(
        user_id,
        scheduled_at_utc=datetime(2026, 10, 28, 2, 0, tzinfo=UTC),
        status="skipped",
    )

    resp = _get_week(
        client,
        auth_headers,
        week_start=WEEK_START.isoformat(),
        timezone="Asia/Shanghai",
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["week_start"] == WEEK_START.isoformat()
    assert data["week_end"] == WEEK_END.isoformat()
    assert data["timezone"] == "Asia/Shanghai"
    assert data["planned"] == 3
    assert data["completed"] == 1
    assert data["skipped"] == 1
    assert data["pending"] == 1
    assert data["denominator"] == 3
    assert data["completion_rate"] == pytest.approx(1 / 3)
    assert data["is_complete"] is True
    assert data["is_current"] is False

    days = {day["date"]: day for day in data["days"]}
    assert list(days) == [
        "2026-10-26",
        "2026-10-27",
        "2026-10-28",
        "2026-10-29",
        "2026-10-30",
        "2026-10-31",
        "2026-11-01",
    ]
    assert days["2026-10-26"]["has_activities"] is True
    assert days["2026-10-26"]["completion_rate"] == 1.0
    assert days["2026-10-29"]["has_activities"] is False
    assert days["2026-10-29"]["completion_rate"] is None
    assert days["2026-10-29"]["planned"] == 0


def test_week_review_uses_requested_timezone_for_boundaries(client, auth_headers):
    user_id = _user_id()
    # 2026-10-25T20:00Z：在 UTC+14 是周一 10-26 10:00，在 UTC-10 仍是周日 10-25 10:00。
    _insert_activity(
        user_id,
        scheduled_at_utc=datetime(2026, 10, 25, 20, 0, tzinfo=UTC),
        status="completed",
    )

    east = _get_week(
        client,
        auth_headers,
        week_start=WEEK_START.isoformat(),
        timezone="Pacific/Kiritimati",
    )
    west = _get_week(
        client,
        auth_headers,
        week_start=WEEK_START.isoformat(),
        timezone="Pacific/Honolulu",
    )

    assert east.status_code == 200, east.text
    assert west.status_code == 200, west.text
    assert east.json()["planned"] == 1
    assert east.json()["days"][0]["date"] == "2026-10-26"
    assert west.json()["planned"] == 0


def test_week_review_accepts_fixed_offset_timezone(client, auth_headers):
    user_id = _user_id()
    # 2026-10-25T20:00Z 在 +14:00 是周一 10-26 10:00。
    _insert_activity(
        user_id,
        scheduled_at_utc=datetime(2026, 10, 25, 20, 0, tzinfo=UTC),
        status="completed",
    )

    resp = _get_week(
        client,
        auth_headers,
        week_start=WEEK_START.isoformat(),
        timezone="+14:00",
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["timezone"] == "+14:00"
    assert data["planned"] == 1
    assert data["days"][0]["date"] == "2026-10-26"


def test_week_review_postpone_chain_counts_once_and_tracks_ops(client, auth_headers):
    user_id = _user_id()
    original = _insert_activity(
        user_id,
        scheduled_at_utc=datetime(2026, 10, 26, 2, 0, tzinfo=UTC),
        status="postponed",
    )
    middle = _insert_activity(
        user_id,
        scheduled_at_utc=datetime(2026, 10, 26, 3, 0, tzinfo=UTC),
        status="postponed",
        postponed_from_id=original,
    )
    _insert_activity(
        user_id,
        scheduled_at_utc=datetime(2026, 10, 26, 4, 0, tzinfo=UTC),
        status="completed",
        postponed_from_id=middle,
    )
    _insert_postpone_event(user_id, original, datetime(2026, 10, 26, 2, 5, tzinfo=UTC))
    _insert_postpone_event(user_id, middle, datetime(2026, 10, 26, 3, 5, tzinfo=UTC))

    resp = _get_week(
        client,
        auth_headers,
        week_start=WEEK_START.isoformat(),
        timezone="Asia/Shanghai",
    )
    data = resp.json()

    assert data["planned"] == 1
    assert data["completed"] == 1
    assert data["postponed_activities"] == 1
    assert data["postponed_ops"] == 2
    assert data["days"][0]["planned"] == 1


def test_week_review_counts_template_activity_as_normal(client, auth_headers):
    user_id = _user_id()
    _insert_activity(
        user_id,
        scheduled_at_utc=datetime(2026, 10, 26, 2, 0, tzinfo=UTC),
        status="planned",
        source="template",
        title="模板活动",
    )

    data = _get_week(
        client,
        auth_headers,
        week_start=WEEK_START.isoformat(),
        timezone="Asia/Shanghai",
    ).json()

    assert data["planned"] == 1
    assert data["pending"] == 1
    assert data["days"][0]["activities"][0]["source"] == "template"


def test_week_review_empty_week_has_no_fake_rate(client, auth_headers):
    data = _get_week(
        client,
        auth_headers,
        week_start=WEEK_START.isoformat(),
        timezone="Asia/Shanghai",
    ).json()

    assert data["planned"] == 0
    assert data["denominator"] == 0
    assert data["completion_rate"] is None
    assert all(day["completion_rate"] is None for day in data["days"])


def test_week_review_isolates_users(client, auth_headers):
    other_id = _create_other_user()
    _insert_activity(
        other_id,
        scheduled_at_utc=datetime(2026, 10, 26, 2, 0, tzinfo=UTC),
        status="completed",
    )

    data = _get_week(
        client,
        auth_headers,
        week_start=WEEK_START.isoformat(),
        timezone="Asia/Shanghai",
    ).json()

    assert data["planned"] == 0


@pytest.mark.parametrize(
    "params",
    [
        {"week_start": "2026-10-27"},  # 非周一
        {"week_start": "2026-11-09"},  # 未来周
        {"week_start": "2026-10-26", "timezone": "Mars/Olympus"},
        {"week_start": "2026-10-26", "timezone": "+25:00"},
        {"week_start": "2026-10-26", "timezone": "+08:99"},
        {"week_start": "not-a-date"},
    ],
)
def test_week_review_rejects_invalid_queries(client, auth_headers, params, monkeypatch):
    from app.activity import router as activity_router

    # 固定“今天”为 2026-11-01，避免硬编码的 2026-11-09 在跨天后变成当前周
    monkeypatch.setattr(activity_router, "local_today_in", lambda zone: date(2026, 11, 1))

    resp = _get_week(client, auth_headers, **params)
    assert resp.status_code == 422
    assert resp.json()["detail"]


def test_week_review_requires_login(client):
    assert _get_week(client, {}).status_code == 401


def test_postpone_rejects_cross_day_scheduled_at(client, auth_headers):
    settings = {
        "work_start": "09:00",
        "work_end": "18:00",
        "morning_start": "10:00",
        "morning_end": "12:00",
        "afternoon_start": "14:00",
        "afternoon_end": "17:30",
        "daily_target": 3,
        "reminders_enabled": True,
        "allow_in_worktime": True,
        "postpone_policy": "next_slot",
        "quiet_periods": [{"start_time": "12:00", "end_time": "14:00", "label": "午休", "enabled": True}],
    }
    assert client.put("/activity/settings", json=settings, headers=auth_headers).status_code == 200

    today = local_today()
    start = datetime.combine(today, time(10, 0), tzinfo=TZ)
    activity_id = _insert_activity(
        _user_id(),
        scheduled_at_utc=start.astimezone(UTC),
        status="planned",
    )

    tomorrow = start + timedelta(days=1)
    resp = client.post(
        f"/activity/{activity_id}/postpone",
        json={"scheduled_at": tomorrow.isoformat(timespec="seconds")},
        headers=auth_headers,
    )

    assert resp.status_code == 422
    assert "同一天" in resp.json()["detail"]
