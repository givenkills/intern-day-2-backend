"""待办清单接口测试：鉴权、增删改查、越权访问。"""

import sqlite3
import uuid

from app.config import get_settings
from app.security import hash_password, to_iso, utcnow


def create_other_user() -> dict:
    """直接写库造一个别的用户，用于验证越权访问。"""
    username = f"other-{uuid.uuid4().hex[:8]}"
    conn = sqlite3.connect(get_settings().database_path)
    conn.execute(
        "INSERT INTO users (username, display_name, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (username, "其他同学", hash_password("other123456"), to_iso(utcnow())),
    )
    conn.commit()
    conn.close()
    return {"username": username, "password": "other123456"}


def test_list_requires_login(client):
    response = client.get("/todos")
    assert response.status_code == 401


def test_create_requires_login(client):
    response = client.post("/todos", json={"title": "不该被创建"})
    assert response.status_code == 401


def test_create_and_list_own_todos(client, auth_headers):
    created = client.post("/todos", json={"title": "写 PR 描述"}, headers=auth_headers)
    assert created.status_code == 201

    todo = created.json()
    assert todo["title"] == "写 PR 描述"
    assert todo["done"] is False

    listing = client.get("/todos", headers=auth_headers)
    assert listing.status_code == 200
    assert any(item["id"] == todo["id"] for item in listing.json())


def test_create_rejects_blank_title(client, auth_headers):
    response = client.post("/todos", json={"title": "   "}, headers=auth_headers)
    assert response.status_code == 422
    assert response.json()["detail"] == "请求参数校验失败"


def test_toggle_done(client, auth_headers):
    todo_id = client.post("/todos", json={"title": "勾选我"}, headers=auth_headers).json()["id"]

    response = client.patch(f"/todos/{todo_id}", json={"done": True}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["done"] is True

    response = client.patch(f"/todos/{todo_id}", json={"done": False}, headers=auth_headers)
    assert response.json()["done"] is False


def test_delete_todo(client, auth_headers):
    todo_id = client.post("/todos", json={"title": "删掉我"}, headers=auth_headers).json()["id"]

    assert client.delete(f"/todos/{todo_id}", headers=auth_headers).status_code == 204
    assert client.delete(f"/todos/{todo_id}", headers=auth_headers).status_code == 404


def test_update_missing_todo_returns_404(client, auth_headers):
    response = client.patch("/todos/999999", json={"done": True}, headers=auth_headers)
    assert response.status_code == 404


def test_cannot_read_or_change_others_todo(client, auth_headers):
    other = create_other_user()
    other_token = client.post("/auth/login", json=other).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {other_token}"}

    other_todo_id = client.post("/todos", json={"title": "别人的待办"}, headers=other_headers).json()["id"]

    # 我用别人的 id 去改、去删，都应该是 404，且不能出现在我的列表里
    assert (
        client.patch(f"/todos/{other_todo_id}", json={"done": True}, headers=auth_headers).status_code == 404
    )
    assert client.delete(f"/todos/{other_todo_id}", headers=auth_headers).status_code == 404

    my_ids = [item["id"] for item in client.get("/todos", headers=auth_headers).json()]
    assert other_todo_id not in my_ids

    # 对方的数据没有被改动
    other_list = client.get("/todos", headers=other_headers).json()
    assert any(item["id"] == other_todo_id and item["done"] is False for item in other_list)
