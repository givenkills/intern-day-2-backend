"""自定义活动模板接口测试。

覆盖：鉴权、CRUD、归档过滤、跨用户隔离、从模板排期（成功与各类非法输入）、
归档/改名不影响历史活动与统计。对应用例 T01-T16。
"""

import sqlite3
import uuid
from datetime import timedelta

import pytest

from app.activity.timeutil import local_today
from app.config import get_settings
from app.security import hash_password, to_iso, utcnow

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

TEMPLATE = {
    "name": "肩颈拉伸",
    "description": "坐直，缓慢绕肩十次",
    "duration_minutes": 6,
}


def _clean_activity_tables() -> None:
    conn = sqlite3.connect(get_settings().database_path)
    conn.execute("DELETE FROM activity_events")
    conn.execute("DELETE FROM activities")
    conn.execute("DELETE FROM activity_templates")
    conn.execute("DELETE FROM quiet_periods")
    conn.execute("DELETE FROM activity_settings")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _isolate_activity_tables():
    """用例前后都清空活动与模板，避免共享会话数据库互相污染。"""
    _clean_activity_tables()
    yield
    _clean_activity_tables()


def _save_default_settings(client, headers):
    resp = client.put("/activity/settings", json=BASE_SETTINGS, headers=headers)
    assert resp.status_code == 200, resp.text


def _create_template(client, headers, **overrides):
    payload = dict(TEMPLATE, **overrides)
    resp = client.post("/activity/templates", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


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


def _other_headers(client) -> dict:
    other = _create_other_user()
    token = client.post("/auth/login", json=other).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _activity_count() -> int:
    conn = sqlite3.connect(get_settings().database_path)
    row = conn.execute("SELECT COUNT(*) FROM activities").fetchone()
    conn.close()
    return row[0]


# ---- T01-T02：鉴权 ----


def test_templates_require_login(client):
    assert client.get("/activity/templates").status_code == 401
    assert client.post("/activity/templates", json=TEMPLATE).status_code == 401


def test_schedule_and_archive_require_login(client, auth_headers):
    template = _create_template(client, auth_headers)
    assert client.post(f"/activity/templates/{template['id']}/archive").status_code == 401
    assert (
        client.post(
            f"/activity/templates/{template['id']}/schedule",
            json={"plan_date": local_today().isoformat(), "time": "15:00"},
        ).status_code
        == 401
    )


# ---- T03-T07：CRUD 与归档 ----


def test_create_and_list_template(client, auth_headers):
    created = _create_template(client, auth_headers)
    assert created["name"] == TEMPLATE["name"]
    assert created["description"] == TEMPLATE["description"]
    assert created["duration_minutes"] == 6
    assert created["archived"] is False
    assert created["archived_at"] is None

    listed = client.get("/activity/templates", headers=auth_headers).json()
    assert [t["id"] for t in listed] == [created["id"]]


def test_create_strips_whitespace(client, auth_headers):
    created = _create_template(client, auth_headers, name="  喝水  ", description="  倒一杯水  ")
    assert created["name"] == "喝水"
    assert created["description"] == "倒一杯水"


def test_create_rejects_blank_and_bad_duration(client, auth_headers):
    bad_cases = [
        dict(TEMPLATE, name="   "),
        dict(TEMPLATE, description=""),
        dict(TEMPLATE, duration_minutes=0),
        dict(TEMPLATE, duration_minutes=121),
        dict(TEMPLATE, name="x" * 41),
    ]
    for payload in bad_cases:
        resp = client.post("/activity/templates", json=payload, headers=auth_headers)
        assert resp.status_code == 422, f"{payload} 应被拒绝"


def test_update_template(client, auth_headers):
    created = _create_template(client, auth_headers)
    resp = client.put(
        f"/activity/templates/{created['id']}",
        json={"name": "眼保健操", "description": "看远处二十秒", "duration_minutes": 3},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["name"] == "眼保健操"
    assert updated["duration_minutes"] == 3

    listed = client.get("/activity/templates", headers=auth_headers).json()
    assert listed[0]["name"] == "眼保健操"


def test_archive_hides_from_default_list_and_is_idempotent(client, auth_headers):
    created = _create_template(client, auth_headers)
    first = client.post(f"/activity/templates/{created['id']}/archive", headers=auth_headers)
    assert first.status_code == 200
    assert first.json()["archived"] is True
    archived_at = first.json()["archived_at"]

    second = client.post(f"/activity/templates/{created['id']}/archive", headers=auth_headers)
    assert second.status_code == 200
    assert second.json()["archived_at"] == archived_at

    assert client.get("/activity/templates", headers=auth_headers).json() == []
    with_archived = client.get(
        "/activity/templates?include_archived=true", headers=auth_headers
    ).json()
    assert [t["id"] for t in with_archived] == [created["id"]]
    assert with_archived[0]["archived"] is True


def test_edit_and_schedule_archived_template_rejected(client, auth_headers):
    _save_default_settings(client, auth_headers)
    created = _create_template(client, auth_headers)
    client.post(f"/activity/templates/{created['id']}/archive", headers=auth_headers)

    edit = client.put(
        f"/activity/templates/{created['id']}",
        json=TEMPLATE,
        headers=auth_headers,
    )
    assert edit.status_code == 409

    schedule = client.post(
        f"/activity/templates/{created['id']}/schedule",
        json={"plan_date": local_today().isoformat(), "time": "15:00"},
        headers=auth_headers,
    )
    assert schedule.status_code == 409
    assert _activity_count() == 0


# ---- T08-T10：跨用户隔离 ----


def test_cannot_see_or_touch_others_template(client, auth_headers):
    other_headers = _other_headers(client)
    _save_default_settings(client, other_headers)
    other_template = _create_template(client, other_headers)

    assert client.get("/activity/templates", headers=auth_headers).json() == []
    assert (
        client.put(
            f"/activity/templates/{other_template['id']}",
            json=TEMPLATE,
            headers=auth_headers,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/activity/templates/{other_template['id']}/archive", headers=auth_headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/activity/templates/{other_template['id']}/schedule",
            json={"plan_date": local_today().isoformat(), "time": "15:00"},
            headers=auth_headers,
        ).status_code
        == 404
    )
    # 别人自己的列表不受影响
    assert [t["id"] for t in client.get("/activity/templates", headers=other_headers).json()] == [
        other_template["id"]
    ]


def test_body_user_id_is_ignored(client, auth_headers):
    other = _create_other_user()
    conn = sqlite3.connect(get_settings().database_path)
    other_id = conn.execute("SELECT id FROM users WHERE username = ?", (other["username"],)).fetchone()[0]
    conn.close()

    created = client.post(
        "/activity/templates",
        json=dict(TEMPLATE, user_id=other_id),
        headers=auth_headers,
    )
    assert created.status_code == 200

    mine = client.get("/activity/templates", headers=auth_headers).json()
    assert [t["id"] for t in mine] == [created.json()["id"]]

    other_token = client.post("/auth/login", json=other).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}
    assert client.get("/activity/templates", headers=other_headers).json() == []


def test_template_missing_returns_404(client, auth_headers):
    assert (
        client.put("/activity/templates/999999", json=TEMPLATE, headers=auth_headers).status_code
        == 404
    )
    assert client.post("/activity/templates/999999/archive", headers=auth_headers).status_code == 404
    assert (
        client.post(
            "/activity/templates/999999/schedule",
            json={"plan_date": local_today().isoformat(), "time": "15:00"},
            headers=auth_headers,
        ).status_code
        == 404
    )


# ---- T11-T14：从模板排期 ----


def test_schedule_creates_activity_today(client, auth_headers):
    _save_default_settings(client, auth_headers)
    template = _create_template(client, auth_headers)
    day = local_today().isoformat()

    resp = client.post(
        f"/activity/templates/{template['id']}/schedule",
        json={"plan_date": day, "time": "15:00"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    activity = resp.json()
    assert activity["title"] == TEMPLATE["name"]
    assert activity["description"] == TEMPLATE["description"]
    assert activity["activity_type"] == "custom"
    assert activity["source"] == "template"
    assert activity["duration_minutes"] == 6
    assert activity["status"] == "planned"
    assert activity["plan_date"] == day
    assert activity["scheduled_at"][11:16] == "15:00"
    assert activity["scheduled_at"].endswith("+08:00")

    today = client.get("/activity/today", headers=auth_headers).json()
    assert [a["id"] for a in today["activities"]] == [activity["id"]]

    # 生成的模板活动走既有状态流转
    completed = client.post(f"/activity/{activity['id']}/complete", headers=auth_headers)
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"


def test_schedule_with_adjusted_duration(client, auth_headers):
    _save_default_settings(client, auth_headers)
    template = _create_template(client, auth_headers)
    resp = client.post(
        f"/activity/templates/{template['id']}/schedule",
        json={"plan_date": local_today().isoformat(), "time": "16:00", "duration_minutes": 20},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["duration_minutes"] == 20


def test_schedule_future_date(client, auth_headers):
    _save_default_settings(client, auth_headers)
    template = _create_template(client, auth_headers)
    tomorrow = (local_today() + timedelta(days=1)).isoformat()
    resp = client.post(
        f"/activity/templates/{template['id']}/schedule",
        json={"plan_date": tomorrow, "time": "10:00"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["plan_date"] == tomorrow
    # 不属于今天，所以今日列表里不出现
    today = client.get("/activity/today", headers=auth_headers).json()
    assert today["activities"] == []


def test_schedule_rejects_invalid_time_and_date(client, auth_headers):
    _save_default_settings(client, auth_headers)
    template = _create_template(client, auth_headers)
    day = local_today().isoformat()
    yesterday = (local_today() - timedelta(days=1)).isoformat()

    bad_payloads = [
        {"plan_date": day, "time": "25:00"},
        {"plan_date": day, "time": "9:00"},
        {"plan_date": day, "time": "12:30"},  # 午休
        {"plan_date": day, "time": "08:30"},  # 工作时段之外
        {"plan_date": "2026/11/04", "time": "15:00"},
        {"plan_date": "20261104", "time": "15:00"},
        {"plan_date": yesterday, "time": "15:00"},
        {"plan_date": day, "time": "15:00", "duration_minutes": 0},
        {"plan_date": day, "time": "15:00", "duration_minutes": 121},
    ]
    for payload in bad_payloads:
        resp = client.post(
            f"/activity/templates/{template['id']}/schedule",
            json=payload,
            headers=auth_headers,
        )
        assert resp.status_code == 422, f"{payload} 应被拒绝"
    assert _activity_count() == 0


def test_schedule_duration_must_fit_window(client, auth_headers):
    _save_default_settings(client, auth_headers)
    template = _create_template(client, auth_headers)
    # 17:20 + 20 分钟超出下午窗口 17:30
    resp = client.post(
        f"/activity/templates/{template['id']}/schedule",
        json={"plan_date": local_today().isoformat(), "time": "17:20", "duration_minutes": 20},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert _activity_count() == 0


def test_schedule_conflict_creates_no_partial_activity(client, auth_headers):
    _save_default_settings(client, auth_headers)
    template = _create_template(client, auth_headers)
    generated = client.post("/activity/generate", headers=auth_headers).json()
    assert generated["planned"] == 3
    before = _activity_count()

    # 撞到已经生成的第一条活动
    existing = generated["activities"][0]
    resp = client.post(
        f"/activity/templates/{template['id']}/schedule",
        json={"plan_date": existing["plan_date"], "time": existing["scheduled_at"][11:16]},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert _activity_count() == before


# ---- T15-T16：历史保留与统计 ----


def test_edit_and_archive_do_not_rewrite_history(client, auth_headers):
    _save_default_settings(client, auth_headers)
    template = _create_template(client, auth_headers)
    activity = client.post(
        f"/activity/templates/{template['id']}/schedule",
        json={"plan_date": local_today().isoformat(), "time": "15:00"},
        headers=auth_headers,
    ).json()
    client.post(f"/activity/{activity['id']}/complete", headers=auth_headers)

    client.put(
        f"/activity/templates/{template['id']}",
        json={"name": "改名后的模板", "description": "新描述", "duration_minutes": 30},
        headers=auth_headers,
    )
    client.post(f"/activity/templates/{template['id']}/archive", headers=auth_headers)

    # 已生成的活动保持原样，且仍然存在、已完成
    today = client.get("/activity/today", headers=auth_headers).json()
    row = next(a for a in today["activities"] if a["id"] == activity["id"])
    assert row["title"] == TEMPLATE["name"]
    assert row["description"] == TEMPLATE["description"]
    assert row["duration_minutes"] == 6
    assert row["status"] == "completed"

    week = client.get("/activity/week", headers=auth_headers).json()
    assert week["completed"] == 1
    assert week["planned"] == 1


def test_schedule_does_not_require_generate(client, auth_headers):
    """没点“生成今日安排”也能直接排模板活动。"""
    _save_default_settings(client, auth_headers)
    template = _create_template(client, auth_headers)
    resp = client.post(
        f"/activity/templates/{template['id']}/schedule",
        json={"plan_date": local_today().isoformat(), "time": "10:30"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    today = client.get("/activity/today", headers=auth_headers).json()
    assert today["summary"]["planned"] == 1
