"""登录流程相关的测试：正常登录、错误密码、未登录、伪造令牌、过期、退出。"""

import sqlite3
from datetime import timedelta

from app.config import get_settings
from app.security import to_iso, token_fingerprint, utcnow

CREDENTIALS = {"username": "intern", "password": "intern123456"}


def login(client, username: str = "intern", password: str = "intern123456") -> str:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_health_does_not_require_login(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_returns_opaque_token(client):
    response = client.post("/auth/login", json=CREDENTIALS)
    assert response.status_code == 200

    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_at"]
    # 令牌不应包含任何账号信息，只是随机串
    assert CREDENTIALS["username"] not in body["access_token"]
    assert CREDENTIALS["password"] not in body["access_token"]


def test_login_with_wrong_password_uses_generic_message(client):
    response = client.post("/auth/login", json={"username": "intern", "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json()["detail"] == "账号或密码不正确"


def test_login_with_unknown_user_uses_same_message(client):
    response = client.post("/auth/login", json={"username": "nobody", "password": "whatever"})
    assert response.status_code == 401
    assert response.json()["detail"] == "账号或密码不正确"


def test_login_rejects_empty_fields(client):
    response = client.post("/auth/login", json={"username": "intern", "password": ""})
    assert response.status_code == 422
    assert response.json()["detail"] == "请求参数校验失败"
    assert response.json()["errors"][0]["field"] == "password"


def test_login_rejects_blank_username(client):
    response = client.post("/auth/login", json={"username": "   ", "password": "intern123456"})
    assert response.status_code == 422


def test_me_requires_token(client):
    response = client.get("/auth/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_forged_token(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer forged-token-value"})
    assert response.status_code == 401
    assert response.json()["detail"] == "登录凭证无效或已退出"


def test_me_returns_current_user(client):
    token = login(client)
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "intern"
    assert body["display_name"] == "练习同学"
    assert "password" not in body
    assert "password_hash" not in body


def test_logout_invalidates_old_token(client):
    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/auth/me", headers=headers).status_code == 200
    assert client.post("/auth/logout", headers=headers).status_code == 204

    # 同一个令牌退出后必须失效
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.get("/todos", headers=headers).status_code == 401


def test_expired_token_is_rejected(client):
    token = login(client)
    headers = {"Authorization": f"Bearer {token}"}

    # 直接把会话改成已过期，模拟等待过期的效果
    conn = sqlite3.connect(get_settings().database_path)
    conn.execute(
        "UPDATE sessions SET expires_at = ? WHERE token_fingerprint = ?",
        (to_iso(utcnow() - timedelta(minutes=1)), token_fingerprint(token)),
    )
    conn.commit()
    conn.close()

    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 401
    assert "过期" in response.json()["detail"]
