"""注册流程与多账户数据隔离测试。"""

import sqlite3
import uuid
from datetime import timedelta

from app.config import get_settings
from app.security import to_iso, utcnow

PASSWORD = "pass123456"


def unique_phone() -> str:
    """生成 138 开头的 11 位虚构手机号，避免用例之间互相影响。"""
    return "138" + str(uuid.uuid4().int)[:8]


def send_code(client, phone: str) -> str:
    response = client.post("/auth/sms/send", json={"phone": phone})
    assert response.status_code == 200, response.text
    return response.json()["debug_code"]


def register(client, phone: str | None = None, name: str = "新同学"):
    phone = phone or unique_phone()
    code = send_code(client, phone)
    return client.post(
        "/auth/register",
        json={"phone": phone, "code": code, "password": PASSWORD, "display_name": name},
    )


def auth_of(response) -> dict:
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ---------- 发送验证码 ----------


def test_send_code_rejects_invalid_phone(client):
    for bad in ["12345", "23800138000", "1380013800a", ""]:
        response = client.post("/auth/sms/send", json={"phone": bad})
        assert response.status_code == 422, f"{bad} 应该被拒绝"


def test_send_code_returns_debug_code_and_masked_phone(client):
    phone = unique_phone()
    response = client.post("/auth/sms/send", json={"phone": phone})

    assert response.status_code == 200
    body = response.json()
    assert len(body["debug_code"]) == 6 and body["debug_code"].isdigit()
    assert body["masked_phone"] == f"{phone[:3]}****{phone[-4:]}"
    assert body["expires_in"] > 0
    # 校验码不能出现在掩码手机号里
    assert phone not in body["masked_phone"]


def test_send_code_is_rate_limited(client):
    phone = unique_phone()
    assert client.post("/auth/sms/send", json={"phone": phone}).status_code == 200
    second = client.post("/auth/sms/send", json={"phone": phone})
    assert second.status_code == 429
    assert "秒后重试" in second.json()["detail"]


# ---------- 注册 ----------


def test_register_creates_account_and_signs_in(client):
    phone = unique_phone()
    response = register(client, phone, name="张三")

    assert response.status_code == 201, response.text
    assert response.json()["access_token"]

    me = client.get("/auth/me", headers=auth_of(response))
    assert me.status_code == 200
    assert me.json()["display_name"] == "张三"
    assert me.json()["phone"] == phone
    assert me.json()["username"] == phone
    assert "password" not in me.json()


def test_register_rejects_wrong_code(client):
    phone = unique_phone()
    send_code(client, phone)

    response = client.post(
        "/auth/register",
        json={"phone": phone, "code": "000000", "password": PASSWORD, "display_name": "张三"},
    )
    assert response.status_code == 400
    assert "验证码不正确" in response.json()["detail"]


def test_register_locks_code_after_too_many_attempts(client):
    phone = unique_phone()
    send_code(client, phone)

    for _ in range(5):
        client.post(
            "/auth/register",
            json={"phone": phone, "code": "000000", "password": PASSWORD, "display_name": "张三"},
        )

    response = client.post(
        "/auth/register",
        json={"phone": phone, "code": "000000", "password": PASSWORD, "display_name": "张三"},
    )
    assert response.status_code == 429
    assert "次数过多" in response.json()["detail"]


def test_register_rejects_expired_code(client):
    phone = unique_phone()
    send_code(client, phone)

    # 把验证码改成已过期
    conn = sqlite3.connect(get_settings().database_path)
    conn.execute(
        "UPDATE verification_codes SET expires_at = ? WHERE phone = ?",
        (to_iso(utcnow() - timedelta(seconds=1)), phone),
    )
    conn.commit()
    conn.close()

    response = client.post(
        "/auth/register",
        json={"phone": phone, "code": "000000", "password": PASSWORD, "display_name": "张三"},
    )
    assert response.status_code == 400
    assert "已过期" in response.json()["detail"]


def test_register_marks_code_as_consumed(client):
    phone = unique_phone()
    code = send_code(client, phone)
    response = client.post(
        "/auth/register",
        json={"phone": phone, "code": code, "password": PASSWORD, "display_name": "张三"},
    )
    assert response.status_code == 201

    conn = sqlite3.connect(get_settings().database_path)
    row = conn.execute(
        "SELECT consumed_at FROM verification_codes WHERE phone = ? ORDER BY id DESC LIMIT 1",
        (phone,),
    ).fetchone()
    conn.close()
    assert row is not None and row[0] is not None


def test_register_rejects_duplicate_phone(client):
    phone = unique_phone()
    assert register(client, phone).status_code == 201

    # 手机号已存在时，接口在校验验证码之前就返回 409
    second = client.post(
        "/auth/register",
        json={"phone": phone, "code": "123456", "password": PASSWORD, "display_name": "另一个人"},
    )
    assert second.status_code == 409
    assert "已注册" in second.json()["detail"]


def test_register_rejects_short_password(client):
    phone = unique_phone()
    code = send_code(client, phone)

    response = client.post(
        "/auth/register",
        json={"phone": phone, "code": code, "password": "123", "display_name": "张三"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "请求参数校验失败"


def test_register_rejects_blank_display_name(client):
    phone = unique_phone()
    code = send_code(client, phone)

    response = client.post(
        "/auth/register",
        json={"phone": phone, "code": code, "password": PASSWORD, "display_name": "   "},
    )
    assert response.status_code == 422


# ---------- 手机号登录 ----------


def test_login_with_phone_after_register(client):
    phone = unique_phone()
    register(client, phone)

    response = client.post("/auth/login", json={"username": phone, "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["access_token"]


# ---------- 多账户数据隔离 ----------


def test_two_accounts_do_not_see_each_other_todos(client):
    phone_a, phone_b = unique_phone(), unique_phone()
    headers_a = auth_of(register(client, phone_a, name="甲"))
    headers_b = auth_of(register(client, phone_b, name="乙"))

    # 新注册的账户看到的应该是空列表
    assert client.get("/todos", headers=headers_a).json() == []
    assert client.get("/todos", headers=headers_b).json() == []

    created = client.post("/todos", json={"title": "甲的秘密待办"}, headers=headers_a)
    assert created.status_code == 201
    todo_id = created.json()["id"]

    # 乙看不到甲的待办
    assert client.get("/todos", headers=headers_b).json() == []
    # 乙也改不动、删不掉
    assert client.patch(f"/todos/{todo_id}", json={"done": True}, headers=headers_b).status_code == 404
    assert client.delete(f"/todos/{todo_id}", headers=headers_b).status_code == 404

    # 甲自己的数据没有被影响
    mine = client.get("/todos", headers=headers_a).json()
    assert len(mine) == 1 and mine[0]["title"] == "甲的秘密待办" and mine[0]["done"] is False

    # 乙建自己的待办，两边互不干扰
    client.post("/todos", json={"title": "乙的待办"}, headers=headers_b)
    assert len(client.get("/todos", headers=headers_a).json()) == 1
    assert len(client.get("/todos", headers=headers_b).json()) == 1


def test_same_display_name_can_be_registered_twice(client):
    name = "同名同学"
    assert register(client, unique_phone(), name=name).status_code == 201
    assert register(client, unique_phone(), name=name).status_code == 201
