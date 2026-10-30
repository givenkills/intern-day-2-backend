"""Agent 建议与确认接口测试。

对应用例：G02（from_time 排除会议时段）、G03（确认时被篡改被拒）。
"""

from app.activity.timeutil import local_today, utc_to_local_iso
from tests.test_activity import BASE_SETTINGS, _save_default_settings


def _suggest(client, headers, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return client.post(f"/activity/agent/suggest?{query}", headers=headers)


def test_suggest_returns_slots_without_saving(client, auth_headers):
    _save_default_settings(client, auth_headers)
    resp = _suggest(client, auth_headers, daily_target=3)
    assert resp.status_code == 200
    data = resp.json()
    assert data["planned"] == 3
    assert len(data["slots"]) == 3
    # 只是建议，不落库
    today = client.get("/activity/today", headers=auth_headers).json()
    assert today["summary"]["total"] == 0


def test_suggest_excludes_from_time(client, auth_headers):
    _save_default_settings(client, auth_headers)
    # 从 15:00 之后安排，结果不得落在 15:00 之前
    resp = _suggest(client, auth_headers, daily_target=3, from_time="15:00")
    assert resp.status_code == 200
    data = resp.json()
    for slot in data["slots"]:
        iso = slot["scheduled_at"]
        local = utc_to_local_iso(iso)
        hour = int(local[11:13])
        minute = int(local[14:16])
        assert hour * 60 + minute >= 15 * 60, f"{local} 早于 15:00"


def test_confirm_saves_agent_activities(client, auth_headers):
    _save_default_settings(client, auth_headers)
    suggested = _suggest(client, auth_headers, daily_target=2).json()

    resp = client.post(
        "/activity/agent/confirm",
        json={"slots": suggested["slots"]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["planned"] == 2
    assert all(a["source"] == "agent" for a in data["activities"])


def test_confirm_rejects_quiet_hours_tamper(client, auth_headers):
    _save_default_settings(client, auth_headers)
    # 伪造一个落在午休 12:30 的时间（同时也在上午窗口之外）
    day = local_today().isoformat()
    tampered = {
        "slots": [
            {
                "scheduled_at": f"{day}T12:30:00+08:00",
                "activity_type": "walk",
                "title": "站起来走动",
            }
        ]
    }
    resp = client.post("/activity/agent/confirm", json=tampered, headers=auth_headers)
    assert resp.status_code == 422


def test_confirm_rejects_time_conflict_with_existing(client, auth_headers):
    """confirm 落库时与已有 planned 时间冲突应被 422 拒绝。"""
    _save_default_settings(client, auth_headers)
    # 先系统生成一批，拿到一条已有活动的时间
    generated = client.post("/activity/generate", headers=auth_headers).json()
    existing_time = generated["activities"][0]["scheduled_at"]

    resp = client.post(
        "/activity/agent/confirm",
        json={
            "slots": [
                {
                    "scheduled_at": existing_time,
                    "activity_type": "walk",
                    "title": "站起来走动",
                }
            ]
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422
    assert "冲突" in resp.json()["detail"]


def test_confirm_rejects_time_inside_quiet_only(client, auth_headers):
    # 在下午窗口内加一段免打扰 15:00-16:00，伪造 15:30 应被免打扰拦截
    settings = dict(BASE_SETTINGS)
    settings["quiet_periods"] = [
        {"start_time": "12:00", "end_time": "14:00", "label": "午休", "enabled": True},
        {"start_time": "15:00", "end_time": "16:00", "label": "会议", "enabled": True},
    ]
    client.put("/activity/settings", json=settings, headers=auth_headers)

    day = local_today().isoformat()
    tampered = {
        "slots": [
            {
                "scheduled_at": f"{day}T15:30:00+08:00",
                "activity_type": "walk",
                "title": "站起来走动",
            }
        ]
    }
    resp = client.post("/activity/agent/confirm", json=tampered, headers=auth_headers)
    assert resp.status_code == 422
    assert "免打扰" in resp.json()["detail"]


def test_confirm_rejects_outside_window(client, auth_headers):
    _save_default_settings(client, auth_headers)
    day = local_today().isoformat()
    tampered = {
        "slots": [
            {
                "scheduled_at": f"{day}T08:30:00+08:00",
                "activity_type": "walk",
                "title": "站起来走动",
            }
        ]
    }
    resp = client.post("/activity/agent/confirm", json=tampered, headers=auth_headers)
    assert resp.status_code == 422


def test_confirm_rejects_empty_slots(client, auth_headers):
    _save_default_settings(client, auth_headers)
    resp = client.post("/activity/agent/confirm", json={"slots": []}, headers=auth_headers)
    assert resp.status_code == 422


def test_confirm_requires_login(client):
    resp = client.post("/activity/agent/confirm", json={"slots": []})
    assert resp.status_code == 401
