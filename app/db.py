"""SQLite 数据访问层。

练习项目刻意只使用标准库 sqlite3，不引入 ORM：建表语句集中在此处，
每个请求开一个连接、用完即关，配合文件型数据库即可满足一天的练习需求。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings

# 打开 WAL 数据库时，多个线程同时新建连接会在 Windows 上偶发
# "attempt to write a readonly database"（-shm 初始化竞争）。
# 建连接本身只有几十微秒，串行化最省事，也不影响后续读写的并发。
_CONNECT_LOCK = threading.Lock()

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

-- 每日总结：一条 (user_id, report_date) 只允许一行，靠唯一索引保证。
-- 状态机：pending（已占位）→ running（已有 Worker 抢到）→ success | failed
CREATE TABLE IF NOT EXISTS daily_reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    report_date       TEXT    NOT NULL,
    status            TEXT    NOT NULL DEFAULT 'pending',
    planned_count     INTEGER NOT NULL DEFAULT 0,
    completed_count   INTEGER NOT NULL DEFAULT 0,
    skipped_count     INTEGER NOT NULL DEFAULT 0,
    uncompleted_count INTEGER NOT NULL DEFAULT 0,
    -- 推迟「操作」次数，和活动数不是同一维度，可与 completed_count 重叠
    postponed_count   INTEGER NOT NULL DEFAULT 0,
    -- NULL 表示用户没有自报久坐时长（不等于 0 分钟）
    sedentary_minutes INTEGER,
    review            TEXT,
    suggestion        TEXT,
    model             TEXT,
    attempt           INTEGER NOT NULL DEFAULT 0,
    error_code        TEXT,
    error_detail      TEXT,
    locked_at         TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    generated_at      TEXT
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

-- 幂等的地基：同一个用户同一天只能有一份日报。
-- 代码里的「先查再插」挡不住并发，这里才是最终保证。
CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_user_date
    ON daily_reports(user_id, report_date);
CREATE INDEX IF NOT EXISTS idx_reports_date_status
    ON daily_reports(report_date, status);
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
        # WAL 是库文件的持久属性，在这里切一次即可；API 与 Worker 两个进程都要能同时读写。
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(TABLES)
        _migrate(conn)
        conn.executescript(INDEXES)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """每个请求一个连接。正常退出提交，异常回滚。

    每日总结的 Worker 与 API 是两个进程、同一个库文件，所以：
    - WAL 让「一写多读」不必互相阻塞（WAL 是库文件属性，只在 init_db 里切一次，
      不必每次连接都设，避免并发连接抢同一个 pragma 写锁）；
    - busy_timeout 让偶发的写冲突等一会儿再重试，而不是立刻抛 database is locked。
    写事务仍然要保持短小：绝不在 wait 模型响应期间持有写锁。
    """
    with _CONNECT_LOCK:
        conn = sqlite3.connect(_db_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
