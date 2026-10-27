"""测试夹具：用临时数据库启动一个独立的后端实例。

环境变量必须在导入 app 之前设置，否则配置类会先读到默认值。
"""

import os
import tempfile
from pathlib import Path

_TMP_DIR = Path(tempfile.mkdtemp(prefix="intern-day-2-backend-tests-"))
os.environ["DATABASE_PATH"] = str(_TMP_DIR / "test.db")
os.environ["SEED_ON_STARTUP"] = "true"
os.environ["SESSION_TTL_MINUTES"] = "60"
os.environ["CORS_ORIGINS"] = "http://localhost:5173"
os.environ["SEED_USERNAME"] = "intern"
os.environ["SEED_PASSWORD"] = "intern123456"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client):
    """每次调用都新建一条会话，避免退出登录的用例影响其他用例。"""
    response = client.post(
        "/auth/login",
        json={"username": "intern", "password": "intern123456"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
