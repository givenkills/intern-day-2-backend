"""活动助手接口测试：设置、生成、三操作、校验、隔离、周统计。

对应用例：S01-S08、V01-V08、U01-U04、W01-W06。
"""

import sqlite3
import uuid

import pytest

from app.config import get_settings
from app.security import hash_password, to_iso, utcnow


@pytest.fixture(autouse=True)
def _clean_activities():
    """每个用例前清空活动相关表，避免用例间数据污染（共享同一 session 数据库）。"""
    conn = sqlite3.connect(get_settings().database_path)
    conn.execute("DELETE FROM activity_events")
    conn.execute("DELETE FROM activities")
    conn.execute("DELETE FROM quiet_periods")
    conn.execute("DELETE FROM activity_settings")
    conn.commit()
    conn.close()
    yield

BASE_SETTINGS = {
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


def _create_other_user() -> dict:
    username = f"other-{uuid.uuid4().hex[:8]}"
    conn = sqlite3.connect(get_settings().database_path)
    conn.execute(
        "INSERT INTO users (username, display_name, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (username, "其他同学", hash_password("other123456"), to_iso(utcnow())),
    )
    conn.commit()
    conn.close()
    return {"username": username, "password": "other123456"}


def _save_default_settings(client, headers):
    resp = client.put("/activity/settings", json=BASE_SETTINGS, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp


# ---- U 组：鉴权与隔离 ----


def test_today_requires_login(client):
    assert client.get("/activity/today").status_code == 401


def test_settings_requires_login(client):
    assert client.get("/activity/settings").status_code == 401


def test_cannot_touch_others_activity(client, auth_headers):
    other = _create_other_user()
    other_token = client.post("/auth/login", json=other).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}
    _save_default_settings(client, other_headers)
    generated = client.post("/activity/generate", headers=other_headers).json()
    other_id = generated["activities"][0]["id"]

    assert client.post(f"/activity/{other_id}/complete", headers=auth_headers).status_code == 404
    assert client.post(f"/activity/{other_id}/postpone", headers=auth_headers).status_code == 404

    my_ids = [a["id"] for a in client.get("/activity/today", headers=auth_headers).json()["activities"]]
    assert other_id not in my_ids


def test_list_only_own_activities(client, auth_headers):
    _save_default_settings(client, auth_headers)
    generated = client.post("/activity/generate", headers=auth_headers).json()
    assert len(generated["activities"]) == 3


# ---- V 组：设置校验 ----


def test_work_hours_reversed_rejected(client, auth_headers):
    bad = dict(BASE_SETTINGS, work_start="18:00", work_end="09:00")
    resp = client.put("/activity/settings", json=bad, headers=auth_headers)
    assert resp.status_code == 422


def test_window_outside_work_rejected(client, auth_headers):
    bad = dict(BASE_SETTINGS, morning_start="08:00", morning_end="12:00")
    resp = client.put("/activity/settings", json=bad, headers=auth_headers)
    assert resp.status_code == 422


def test_daily_target_out_of_range_rejected(client, auth_headers):
    for value in (0, 9, -1):
        resp = client.put(
            "/activity/settings",
            json=dict(BASE_SETTINGS, daily_target=value),
            headers=auth_headers,
        )
        assert resp.status_code == 422, f"daily_target={value} 应被拒绝"


def test_invalid_time_format_rejected(client, auth_headers):
    bad = dict(BASE_SETTINGS, work_start="25:00")
    resp = client.put("/activity/settings", json=bad, headers=auth_headers)
    assert resp.status_code == 422


def test_quiet_period_end_before_start_rejected(client, auth_headers):
    bad = dict(BASE_SETTINGS, quiet_periods=[{"start_time": "14:00", "end_time": "12:00"}])
    resp = client.put("/activity/settings", json=bad, headers=auth_headers)
    assert resp.status_code == 422


def test_settings_roundtrip(client, auth_headers):
    _save_default_settings(client, auth_headers)
    got = client.get("/activity/settings", headers=auth_headers).json()
    assert got["work_start"] == "09:00"
    assert got["daily_target"] == 3
    assert len(got["quiet_periods"]) == 1


# ---- 生成与今日 ----


def test_generate_creates_three_activities(client, auth_headers):
    _save_default_settings(client, auth_headers)
    resp = client.post("/activity/generate", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["planned"] == 3
    assert len(data["activities"]) == 3


def test_generate_idempotent(client, auth_headers):
    _save_default_settings(client, auth_headers)
    first = client.post("/activity/generate", headers=auth_headers).json()
    second = client.post("/activity/generate", headers=auth_headers).json()
    first_ids = {a["id"] for a in first["activities"]}
    second_ids = {a["id"] for a in second["activities"]}
    assert first_ids == second_ids


def test_generate_avoids_quiet_hours(client, auth_headers):
    _save_default_settings(client, auth_headers)
    data = client.post("/activity/generate", headers=auth_headers).json()
    for a in data["activities"]:
        hour = int(a["scheduled_at"][11:13])
        assert not (12 <= hour < 14), f"{a['scheduled_at']} 落在午休时段"


# ---- S 组：状态流转 ----


def _generate_one(client, headers):
    _save_default_settings(client, headers)
    data = client.post("/activity/generate", headers=headers).json()
    return data["activities"][0]


def test_complete_activity(client, auth_headers):
    activity = _generate_one(client, auth_headers)
    resp = client.post(f"/activity/{activity['id']}/complete", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["completed_at"] is not None


def test_skip_activity(client, auth_headers):
    activity = _generate_one(client, auth_headers)
    resp = client.post(f"/activity/{activity['id']}/skip", json={"reason": "开会"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"
    assert resp.json()["skipped_reason"] == "开会"


def test_postpone_creates_new_activity(client, auth_headers):
    activity = _generate_one(client, auth_headers)
    resp = client.post(f"/activity/{activity['id']}/postpone", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    new_activity = data["activity"]
    assert new_activity["postponed_from_id"] == activity["id"]
    assert new_activity["status"] == "planned"

    # 原记录已置 postponed
    today = client.get("/activity/today", headers=auth_headers).json()
    original = next(a for a in today["activities"] if a["id"] == activity["id"])
    assert original["status"] == "postponed"


def test_repeat_complete_conflict(client, auth_headers):
    activity = _generate_one(client, auth_headers)
    client.post(f"/activity/{activity['id']}/complete", headers=auth_headers)
    resp = client.post(f"/activity/{activity['id']}/complete", headers=auth_headers)
    assert resp.status_code == 409


def test_postpone_after_skip_conflict(client, auth_headers):
    activity = _generate_one(client, auth_headers)
    client.post(f"/activity/{activity['id']}/skip", json={}, headers=auth_headers)
    resp = client.post(f"/activity/{activity['id']}/postpone", headers=auth_headers)
    assert resp.status_code == 409


def test_postpone_no_slot_returns_ok_false(client, auth_headers):
    # 窗口 10:00-11:10：够生成 1 次，但推迟后剩余不足一个完整槽
    tight = dict(
        BASE_SETTINGS,
        morning_start="10:00",
        morning_end="11:10",
        afternoon_start=None,
        afternoon_end=None,
        daily_target=1,
        quiet_periods=[],
    )
    client.put("/activity/settings", json=tight, headers=auth_headers)
    data = client.post("/activity/generate", headers=auth_headers).json()
    assert data["planned"] == 1
    activity = data["activities"][0]

    resp = client.post(f"/activity/{activity['id']}/postpone", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert resp.json()["reason"]

    # 原记录仍是 planned，未被改成 postponed
    today = client.get("/activity/today", headers=auth_headers).json()
    original = next(a for a in today["activities"] if a["id"] == activity["id"])
    assert original["status"] == "planned"


def test_postpone_keeps_same_day(client, auth_headers):
    activity = _generate_one(client, auth_headers)
    resp = client.post(f"/activity/{activity['id']}/postpone", headers=auth_headers)
    assert resp.json()["activity"]["plan_date"] == activity["plan_date"]


def test_postpone_to_custom_time(client, auth_headers):
    """推迟到用户指定的时间，落在可活动窗口内应成功。"""
    activity = _generate_one(client, auth_headers)
    day = activity["plan_date"]
    resp = client.post(
        f"/activity/{activity['id']}/postpone",
        json={"scheduled_at": f"{day}T16:00:00+08:00"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["activity"]["postponed_from_id"] == activity["id"]
    assert data["activity"]["scheduled_at"] == f"{day}T16:00:00+08:00"


def test_postpone_to_quiet_hours_rejected(client, auth_headers):
    """推迟到免打扰时段应被 422 拒绝，且原记录不变。"""
    activity = _generate_one(client, auth_headers)
    day = activity["plan_date"]
    resp = client.post(
        f"/activity/{activity['id']}/postpone",
        json={"scheduled_at": f"{day}T12:30:00+08:00"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    # 原记录仍是 planned
    today = client.get("/activity/today", headers=auth_headers).json()
    original = next(a for a in today["activities"] if a["id"] == activity["id"])
    assert original["status"] == "planned"


def test_postpone_to_conflicting_time_rejected(client, auth_headers):
    """推迟到与另一条 planned 冲突的时间应被 422 拒绝。"""
    activity = _generate_one(client, auth_headers)
    # 找另一条 planned 活动的时间
    today = client.get("/activity/today", headers=auth_headers).json()
    other = next(a for a in today["activities"] if a["id"] != activity["id"] and a["status"] == "planned")
    resp = client.post(
        f"/activity/{activity['id']}/postpone",
        json={"scheduled_at": other["scheduled_at"]},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_postpone_options_returns_available_times(client, auth_headers):
    """postpone-options 返回当天剩余可推迟的时间点，且不含免打扰时段。"""
    activity = _generate_one(client, auth_headers)
    resp = client.get(f"/activity/{activity['id']}/postpone-options", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    times = data["times"]
    assert times, "应至少有一个候选时间点"
    # 候选点都在原活动时间之后
    from app.activity.timeutil import utc_to_local_iso

    original_local = utc_to_local_iso(activity["scheduled_at"])
    original_minutes = int(original_local[11:13]) * 60 + int(original_local[14:16])
    for t in times:
        hh, mm = int(t[:2]), int(t[3:5])
        assert hh * 60 + mm > original_minutes, f"{t} 应在原时间之后"
        # 不在午休 12:00-14:00 内
        assert not (12 * 60 <= hh * 60 + mm < 14 * 60), f"{t} 不应落在午休时段"


# ---- W 组：周统计 ----


def _insert_activity(user_id, plan_date, status):
    conn = sqlite3.connect(get_settings().database_path)
    cursor = conn.execute(
        """
        INSERT INTO activities (
            user_id, activity_type, title, description, plan_date, scheduled_at,
            duration_minutes, status, source, created_at, updated_at
        ) VALUES (?, 'walk', '站起来走动', '测试数据', ?, '2026-10-30T10:00:00+08:00', 5, ?, 'system', ?, ?)
        """,
        (user_id, plan_date, status, to_iso(utcnow()), to_iso(utcnow())),
    )
    conn.commit()
    conn.close()
    return cursor.lastrowid


def test_week_stats_counts(client, auth_headers):
    # 拿到当前用户 id
    from app.db import connection

    with connection() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = 'intern'").fetchone()
        user_id = row["id"]

    from app.activity.timeutil import local_today

    today = local_today().isoformat()
    # 造：完成 2、跳过 1、推迟 1（原记录 + 新记录）
    _insert_activity(user_id, today, "completed")
    _insert_activity(user_id, today, "completed")
    _insert_activity(user_id, today, "skipped")
    _insert_activity(user_id, today, "postponed")
    _insert_activity(user_id, today, "planned")  # 推迟派生的新记录

    resp = client.get("/activity/week", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["completed"] == 2
    assert data["skipped"] == 1
    assert data["postponed"] == 1
    # 计划数：completed(2) + skipped(1) + planned(1) = 4，postponed 原记录不计入
    assert data["planned"] == 4


def test_week_starts_on_monday(client, auth_headers):
    resp = client.get("/activity/week", headers=auth_headers)
    data = resp.json()
    import datetime

    monday = datetime.date.fromisoformat(data["week_start"])
    assert monday.weekday() == 0
    assert len(data["days"]) == 7
    assert data["days"][0]["date"] == data["week_start"]
