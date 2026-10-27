"""练习账号初始化。

两种用法：
1. 服务启动时（SEED_ON_STARTUP=true）若库里一个用户都没有，自动创建练习账号；
2. 手动执行 `uv run python -m app.seed`，适合在已有数据的环境里补一个账号。

账号密码来自环境变量，默认值只用于本地练习，不写入源码仓库。
"""

from __future__ import annotations

from .config import get_settings
from .db import connection, init_db
from .security import hash_password, to_iso, utcnow

_SAMPLE_TODOS = (
    "看完 FastAPI 的 /docs 页面，确认每个接口的请求体",
    "验证不登录时直接访问功能页会被弹回登录页",
    "退出登录后，用旧令牌再请求一次 /todos 应该拿到 401",
)


def seed_demo_user() -> bool:
    """用户表为空时创建练习账号，返回是否真的创建了。"""
    settings = get_settings()

    with connection() as conn:
        exists = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
        if exists:
            return False

        now = to_iso(utcnow())
        cursor = conn.execute(
            """
            INSERT INTO users (username, display_name, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                settings.seed_username,
                settings.seed_display_name,
                hash_password(settings.seed_password),
                now,
            ),
        )
        user_id = cursor.lastrowid

        if settings.seed_sample_todos:
            conn.executemany(
                """
                INSERT INTO todos (user_id, title, done, created_at, updated_at)
                VALUES (?, ?, 0, ?, ?)
                """,
                [(user_id, title, now, now) for title in _SAMPLE_TODOS],
            )

    return True


def main() -> None:
    init_db()
    if seed_demo_user():
        settings = get_settings()
        print(f"已创建练习账号：{settings.seed_username} / {settings.seed_password}")
    else:
        print("用户表已有数据，未做改动。")


if __name__ == "__main__":
    main()
