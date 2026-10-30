"""SQLite 数据访问层。

练习项目刻意只使用标准库 sqlite3，不引入 ORM：建表语句集中在此处，
每个请求开一个连接、用完即关，配合文件型数据库即可满足一天的练习需求。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings

TABLES = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    phone         TEXT,
    display_name  TEXT    NOT NULL,
    password_hash TEXT    NOT NULL,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_fingerprint TEXT    PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at        TEXT    NOT NULL,
    expires_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title      TEXT    NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone       TEXT    NOT NULL,
    code_hash   TEXT    NOT NULL,
    purpose     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL,
    expires_at  TEXT    NOT NULL,
    consumed_at TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS activity_settings (
    user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    work_start        TEXT    NOT NULL,
    work_end          TEXT    NOT NULL,
    morning_start     TEXT,
    morning_end       TEXT,
    afternoon_start   TEXT,
    afternoon_end     TEXT,
    daily_target      INTEGER NOT NULL DEFAULT 3,
    reminders_enabled INTEGER NOT NULL DEFAULT 1,
    allow_in_worktime INTEGER NOT NULL DEFAULT 1,
    postpone_policy   TEXT    NOT NULL DEFAULT 'next_slot',
    timezone          TEXT    NOT NULL DEFAULT 'Asia/Shanghai',
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS quiet_periods (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    start_time TEXT    NOT NULL,
    end_time   TEXT    NOT NULL,
    label      TEXT,
    enabled    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS activities (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    activity_type     TEXT    NOT NULL,
    title             TEXT    NOT NULL,
    description       TEXT    NOT NULL,
    plan_date         TEXT    NOT NULL,
    scheduled_at      TEXT    NOT NULL,
    duration_minutes  INTEGER NOT NULL DEFAULT 5,
    status            TEXT    NOT NULL DEFAULT 'planned',
    source            TEXT    NOT NULL DEFAULT 'system',
    postponed_from_id INTEGER REFERENCES activities(id),
    skipped_reason    TEXT,
    completed_at      TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL,
    action      TEXT    NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    note        TEXT,
    created_at  TEXT    NOT NULL
);
"""

# 部分索引：phone 为 NULL 的账号（例如初始化脚本建的练习账号）不参与唯一性约束。
INDEXES = """
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_todos_user    ON todos(user_id);
CREATE INDEX IF NOT EXISTS idx_codes_phone   ON verification_codes(phone, purpose);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone
    ON users(phone) WHERE phone IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_activities_user_day
    ON activities(user_id, plan_date, status);
CREATE INDEX IF NOT EXISTS idx_activities_postponed
    ON activities(postponed_from_id);
CREATE INDEX IF NOT EXISTS idx_events_user_time
    ON activity_events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_quiet_user
    ON quiet_periods(user_id, enabled);
"""


def _db_path() -> Path:
    path = Path(get_settings().database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：老库补 users.phone 列。

    练习项目不引入 Alembic，这里只处理「表已存在但缺列」的情况；
    列加上之后再建唯一索引，所以索引必须放在迁移之后执行。
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "phone" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN phone TEXT")


def init_db() -> None:
    """建库建表，可重复执行。"""
    conn = sqlite3.connect(_db_path())
    try:
        conn.executescript(TABLES)
        _migrate(conn)
        conn.executescript(INDEXES)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """每个请求一个连接。正常退出提交，异常回滚。"""
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
