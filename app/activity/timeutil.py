"""时间处理工具：数据库统一存 UTC，业务按 Asia/Shanghai 本地时间工作。

关键约定：
- 数据库的 scheduled_at / created_at 等一律是 UTC 的 ISO 字符串（沿用 security.to_iso）。
- 用户的「工作时段」「可活动窗口」「免打扰」都是墙钟时间（HH:MM），属于本地日，
  不参与时区换算——它们只是当天的一条时间轴。
- 「今天」「本周」按 Asia/Shanghai 的本地日期判断，存进 plan_date 字段，避免用
  UTC 时间去反推本地日期造成跨时区错位。
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta, timezone

# 固定 +08:00 时区。不用 zoneinfo("Asia/Shanghai")，因为 Windows 裸机与精简容器
# 可能没有系统时区数据库（zoneinfo 需要 tzdata），改用 timezone 常量最稳。
TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

# 严格 HH:MM，允许 00:00-23:59
_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

_MINUTES_PER_DAY = 24 * 60


def is_valid_time(text: str) -> bool:
    return _TIME_PATTERN.fullmatch(text) is not None


def parse_time(text: str) -> int:
    """把 HH:MM 转成当天第几分钟（0-1439）。格式非法抛 ValueError。"""
    match = _TIME_PATTERN.fullmatch(text)
    if match is None:
        raise ValueError(f"无效时间格式：{text}")
    return int(match.group(1)) * 60 + int(match.group(2))


def format_time(minutes: int) -> str:
    """把当天第几分钟转回 HH:MM。越界时先按模一天处理（仅用于显示）。"""
    minutes %= _MINUTES_PER_DAY
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def local_now() -> datetime:
    return datetime.now(TZ)


def local_today() -> date:
    return local_now().date()


def to_local(dt_utc: datetime) -> datetime:
    """UTC 的 datetime 转成 Asia/Shanghai 的墙钟时间（用于展示）。"""
    return dt_utc.astimezone(TZ)


def utc_to_local_iso(iso_utc: str) -> str:
    """把库里的 UTC ISO 串转成带 +08:00 偏移的 ISO 串，供前端展示。"""
    dt = datetime.fromisoformat(iso_utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def local_date_to_utc_iso(day: date, minutes: int) -> str:
    """把「本地日期 + 当天第几分钟」合成 UTC 的 ISO 串，用于写库。"""
    local_dt = datetime.combine(day, time.min, tzinfo=TZ) + timedelta(minutes=minutes)
    return local_dt.astimezone(UTC).isoformat(timespec="seconds")


def minutes_of_local_iso(iso_utc: str) -> int:
    """库里的 UTC ISO 串 → 本地当天第几分钟（用于和时间窗口比较）。"""
    dt = datetime.fromisoformat(iso_utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    local = dt.astimezone(TZ)
    return local.hour * 60 + local.minute


def plan_date_of_iso(iso_utc: str) -> str:
    """库里的 UTC ISO 串 → 本地日期 YYYY-MM-DD。"""
    dt = datetime.fromisoformat(iso_utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TZ).date().isoformat()


def week_start(day: date) -> date:
    """返回 day 所在周的周一。"""
    return day - timedelta(days=day.weekday())
