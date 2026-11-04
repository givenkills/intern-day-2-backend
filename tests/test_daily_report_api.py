"""日报接口：登录、日期校验、状态返回、跨用户隔离、队列不可用。

重点不在"接口能不能返回 200"，而在几个容易糊弄过去的地方：
- 没登录必须 401，不能返回一份空日报；
- 别人的日期只能看到自己那份"尚未生成"；
- Redis 挂了必须 503，不能返回 queued 骗前端。
"""

from __future__ import annotations

import itertools
from datetime import date

from dr_helpers import day_before

from app.activity.timeutil import local_today
from app.daily_report import queue, store
from app.daily_report.stats import DailyStats
from app.db import connection

_phone_counter = itertools.count(1)


def intern_user_id(client, auth_headers) -> int:
    return int(client.get("/auth/me", headers=auth_headers).json()["id"])


def register_new_user(client) -> dict:
    """走真实注册流程造一个账号，顺带证明隔离不是靠"用户不存在"。"""
    phone = f"137{next(_phone_counter):08d}"
    sent = client.post("/auth/sms/send", json={"phone": phone})
    assert sent.status_code == 200, sent.text
    code = sent.json()["debug_code"]
    assert code, "练习环境应返回 debug_code"

    registered = client.post(
        "/auth/register",
        json={"phone": phone, "code": code, "password": "other123456", "display_name": "另一个人"},
    )
    assert registered.status_code == 201, registered.text
    return {"Authorization": f"Bearer {registered.json()['access_token']}"}


def insert_success_row(user_id: int, day, review: str = "当天 5 项安排完成 3 项。") -> None:
    stats = DailyStats(
        report_date=day.isoformat(),
        planned=5, completed=3, skipped=1, uncompleted=1, postponed_ops=2,
        sedentary_minutes=None, activities=(),
    )
    with connection() as conn:
        store.ensure_pending(conn, user_id, day)
        assert store.claim(conn, user_id, day, 600)
        assert store.mark_success(conn, user_id, day, stats, review, "把活动挪到上午。", "test-model")


def test_read_requires_login(client):
    day = day_before(20)
    assert client.get(f"/reports/{day.isoformat()}").status_code == 401
    assert client.post(f"/reports/{day.isoformat()}/generate").status_code == 401


def test_invalid_date_format(client, auth_headers):
    response = client.get("/reports/2026-13-40", headers=auth_headers)
    assert response.status_code == 422
    assert "YYYY-MM-DD" in response.json()["detail"]


def test_today_is_rejected(client, auth_headers):
    today = local_today().isoformat()
    response = client.get(f"/reports/{today}", headers=auth_headers)
    assert response.status_code == 422
    assert "已结束" in response.json()["detail"]


def test_not_generated_shape(client, auth_headers):
    day = day_before(21)
    response = client.get(f"/reports/{day.isoformat()}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_generated"
    assert body["stats"] is None
    assert body["review"] is None
    assert body["sedentary"] == {"recorded": False, "minutes": None}


def test_success_payload_uses_local_time(client, auth_headers):
    user_id = intern_user_id(client, auth_headers)
    day = day_before(22)
    insert_success_row(user_id, day)

    body = client.get(f"/reports/{day.isoformat()}", headers=auth_headers).json()
    assert body["status"] == "success"
    assert body["stats"] == {
        "planned": 5, "completed": 3, "skipped": 1, "uncompleted": 1, "postponed_ops": 2,
    }
    # 生成时间带 +08:00，和"日报日期"是两件事
    assert body["generated_at"].endswith("+08:00")


def test_other_user_sees_their_own_blank_report(client, auth_headers):
    user_id = intern_user_id(client, auth_headers)
    day = day_before(23)
    insert_success_row(user_id, day, review="这是 A 的日报")

    other = register_new_user(client)
    response = client.get(f"/reports/{day.isoformat()}", headers=other)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "not_generated"
    assert body["review"] is None
    assert "A 的日报" not in response.text


def test_other_user_generate_does_not_touch_first_user(client, auth_headers, monkeypatch):
    user_id = intern_user_id(client, auth_headers)
    day = day_before(24)
    insert_success_row(user_id, day, review="A 的日报")

    async def fake_enqueue(uid: int, when, generation: int = 1) -> tuple[str, bool]:
        return f"daily-report:{uid}:{when}", True

    monkeypatch.setattr(queue, "enqueue_report", fake_enqueue)

    other = register_new_user(client)
    response = client.post(f"/reports/{day.isoformat()}/generate", headers=other)
    assert response.status_code == 202

    body = client.get(f"/reports/{day.isoformat()}", headers=auth_headers).json()
    assert body["review"] == "A 的日报"


def test_generate_queues_and_returns_immediately(client, auth_headers, monkeypatch):
    day = day_before(25)
    calls: list[tuple[int, object]] = []

    async def fake_enqueue(uid: int, when, generation: int = 1) -> tuple[str, bool]:
        calls.append((uid, when))
        return f"daily-report:{uid}:{when.isoformat()}", True

    monkeypatch.setattr(queue, "enqueue_report", fake_enqueue)

    response = client.post(f"/reports/{day.isoformat()}/generate", headers=auth_headers)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["job_id"].startswith("daily-report:")
    assert len(calls) == 1

    # 入队成功后才有 pending 占位，页面据此显示"生成中"
    follow_up = client.get(f"/reports/{day.isoformat()}", headers=auth_headers).json()
    assert follow_up["status"] == "pending"


def test_generate_reports_503_when_redis_is_down(client, auth_headers, monkeypatch):
    day = day_before(26)

    async def broken_enqueue(uid: int, when, generation: int = 1) -> tuple[str, bool]:
        raise queue.QueueUnavailable("connection refused")

    monkeypatch.setattr(queue, "enqueue_report", broken_enqueue)

    response = client.post(f"/reports/{day.isoformat()}/generate", headers=auth_headers)
    assert response.status_code == 503
    assert "队列" in response.json()["detail"]

    # 关键：不能留下一条假的"生成中"
    follow_up = client.get(f"/reports/{day.isoformat()}", headers=auth_headers).json()
    assert follow_up["status"] == "not_generated"


def test_generate_reuses_success_without_requeue(client, auth_headers, monkeypatch):
    user_id = intern_user_id(client, auth_headers)
    day = day_before(27)
    insert_success_row(user_id, day, review="已有的日报")

    async def should_not_be_called(uid: int, when, generation: int = 1) -> tuple[str, bool]:
        raise AssertionError("已有成功结果时不该再入队")

    monkeypatch.setattr(queue, "enqueue_report", should_not_be_called)

    response = client.post(f"/reports/{day.isoformat()}/generate", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "already_generated"
    assert client.get(f"/reports/{day.isoformat()}", headers=auth_headers).json()["review"] == "已有的日报"


def test_generate_in_progress_when_another_worker_holds_lease(client, auth_headers, monkeypatch):
    user_id = intern_user_id(client, auth_headers)
    day = day_before(28)
    with connection() as conn:
        store.ensure_pending(conn, user_id, day)
        assert store.claim(conn, user_id, day, 600)

    async def should_not_be_called(uid: int, when, generation: int = 1) -> tuple[str, bool]:
        raise AssertionError("正在执行时不该再入队")

    monkeypatch.setattr(queue, "enqueue_report", should_not_be_called)

    response = client.post(f"/reports/{day.isoformat()}/generate", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["status"] == "in_progress"


def test_failed_report_exposes_code_but_not_detail(client, auth_headers):
    user_id = intern_user_id(client, auth_headers)
    day = day_before(29)
    with connection() as conn:
        store.ensure_pending(conn, user_id, day)
        assert store.claim(conn, user_id, day, 600)
        store.mark_failed(conn, user_id, day, "AGENT_UPSTREAM_ERROR", "Traceback: secret detail")

    body = client.get(f"/reports/{day.isoformat()}", headers=auth_headers).json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "AGENT_UPSTREAM_ERROR"
    # 内部细节留在库里，不出接口
    assert "Traceback" not in str(body)


def test_generate_503_against_a_really_closed_redis(client, auth_headers, monkeypatch):
    """不打桩，直接指向一个没人监听的端口：验证真实连接失败也是 503。"""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:6399/0")
    # 连接池是进程级缓存的，清掉才能重新连
    monkeypatch.setattr(queue, "_pool", None)

    day = day_before(30)
    response = client.post(f"/reports/{day.isoformat()}/generate", headers=auth_headers)
    assert response.status_code == 503
    assert "队列" in response.json()["detail"]


def test_job_id_is_stable_until_a_retry_is_needed():
    """同一个 job_id 才挡得住重复入队；失败补跑必须换一个，否则 ARQ 会当成已存在。"""
    from app.daily_report.queue import job_id_for

    day = date(2026, 11, 1)
    assert job_id_for(1, day) == "daily-report:1:2026-11-01"
    assert job_id_for(1, day, 1) == "daily-report:1:2026-11-01"
    assert job_id_for(1, day, 2) == "daily-report:1:2026-11-01:r2"
    assert job_id_for(1, day, 3) != job_id_for(1, day, 2)


def test_failed_report_is_requeued_with_a_new_job_id(client, auth_headers, monkeypatch):
    """失败的行再点一次，必须真的重新入队（而不是回一句「已在队列中」）。"""
    user_id = intern_user_id(client, auth_headers)
    day = day_before(31)
    with connection() as conn:
        store.ensure_pending(conn, user_id, day)
        assert store.claim(conn, user_id, day, 600)
        store.mark_failed(conn, user_id, day, "AGENT_UPSTREAM_ERROR", "boom")

    seen: list[int] = []

    async def fake_enqueue(uid: int, when, generation: int = 1) -> tuple[str, bool]:
        seen.append(generation)
        return f"daily-report:{uid}:{when}:r{generation}", True

    monkeypatch.setattr(queue, "enqueue_report", fake_enqueue)

    response = client.post(f"/reports/{day.isoformat()}/generate", headers=auth_headers)
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert seen == [2], "attempt=1 失败后应当用第 2 代 job_id 重新入队"
