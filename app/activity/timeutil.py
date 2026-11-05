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
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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


_OFFSET_PATTERN = re.compile(r"^([+-])(\d{2}):(\d{2})$")


def get_zone(name: str) -> tzinfo:
    """取时区：优先 IANA 名称，也接受设备返回的固定偏移（如 +00:00）。"""
    text = name.strip()
    offset = _OFFSET_PATTERN.fullmatch(text)
    if offset is not None:
        sign, hours_text, minutes_text = offset.groups()
        hours, minutes = int(hours_text), int(minutes_text)
        if hours > 14 or minutes > 59 or (hours == 14 and minutes != 0):
            raise ValueError(f"无效的时区偏移：{text}")
        delta = timedelta(hours=hours, minutes=minutes)
        return timezone(-delta if sign == "-" else delta, name=text)
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"未知时区：{text}") from exc


def parse_iso_datetime(value: str) -> datetime:
    """解析库里的 ISO 时间串；无偏移的旧数据按 UTC 处理。"""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def local_today_in(zone: tzinfo) -> date:
    return datetime.now(zone).date()


def week_utc_bounds(day: date, zone: tzinfo) -> tuple[datetime, datetime]:
    """某时区自然周 [周一 00:00, 下周一 00:00) 对应的 UTC 区间。"""
    start_local = datetime.combine(day, time.min, tzinfo=zone)
    end_local = datetime.combine(day + timedelta(days=7), time.min, tzinfo=zone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def local_day_utc_bounds(day: date) -> tuple[str, str]:
    """某本地自然日 [00:00, 次日 00:00) 对应的 UTC ISO 串，左闭右开。

    返回的两个串直接用于和库里的 created_at 做字符串比较（格式统一，
    字典序即时间序），这样 23:59:59 属于当天、次日 00:00:00 属于下一天。
    """
    start = datetime.combine(day, time.min, tzinfo=TZ)
    end = start + timedelta(days=1)
    return (
        start.astimezone(UTC).isoformat(timespec="seconds"),
        end.astimezone(UTC).isoformat(timespec="seconds"),
    )
