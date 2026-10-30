"""活动助手的请求与响应模型。时间格式、范围、倒挂等校验都在这一层做。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .timeutil import is_valid_time, parse_time

# 每天安排次数范围
DAILY_TARGET_MIN = 1
DAILY_TARGET_MAX = 8

# 活动状态枚举
STATUS_PLANNED = "planned"
STATUS_COMPLETED = "completed"
STATUS_POSTPONED = "postponed"
STATUS_SKIPPED = "skipped"
VALID_STATUSES = (STATUS_PLANNED, STATUS_COMPLETED, STATUS_POSTPONED, STATUS_SKIPPED)


def _check_time(value: str) -> str:
    if not is_valid_time(value):
        raise ValueError(f"时间格式应为 HH:MM，收到「{value}」")
    return value


class QuietPeriodIn(BaseModel):
    start_time: str
    end_time: str
    label: str | None = Field(default=None, max_length=40)
    enabled: bool = True

    @field_validator("start_time", "end_time")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        return _check_time(value)

    @field_validator("end_time")
    @classmethod
    def _end_after_start(cls, value: str, info) -> str:
        start = info.data.get("start_time")
        if start is not None and parse_time(value) <= parse_time(start):
            raise ValueError("免打扰的结束时间必须晚于开始时间")
        return value


class ActivitySettingsIn(BaseModel):
    work_start: str
    work_end: str
    morning_start: str | None = None
    morning_end: str | None = None
    afternoon_start: str | None = None
    afternoon_end: str | None = None
    daily_target: int = Field(default=3, ge=DAILY_TARGET_MIN, le=DAILY_TARGET_MAX)
    reminders_enabled: bool = True
    allow_in_worktime: bool = True
    postpone_policy: str = "next_slot"
    quiet_periods: list[QuietPeriodIn] = Field(default_factory=list)

    @field_validator(
        "work_start", "work_end", "morning_start", "morning_end",
        "afternoon_start", "afternoon_end",
    )
    @classmethod
    def _valid_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _check_time(value)

    @field_validator("work_end")
    @classmethod
    def _work_ordered(cls, value: str, info) -> str:
        start = info.data.get("work_start")
        if start is not None and parse_time(value) <= parse_time(start):
            raise ValueError("工作结束时间必须晚于开始时间")
        return value

    @field_validator("postpone_policy")
    @classmethod
    def _policy(cls, value: str) -> str:
        if value not in ("next_slot",):
            raise ValueError("postpone_policy 只支持 next_slot")
        return value


class QuietPeriodOut(BaseModel):
    id: int
    start_time: str
    end_time: str
    label: str | None = None
    enabled: bool


class ActivitySettingsOut(BaseModel):
    work_start: str
    work_end: str
    morning_start: str | None = None
    morning_end: str | None = None
    afternoon_start: str | None = None
    afternoon_end: str | None = None
    daily_target: int
    reminders_enabled: bool
    allow_in_worktime: bool
    postpone_policy: str
    timezone: str
    quiet_periods: list[QuietPeriodOut]


class ActivityOut(BaseModel):
    id: int
    activity_type: str
    title: str
    description: str
    plan_date: str
    scheduled_at: str
    duration_minutes: int
    status: str
    source: str
    postponed_from_id: int | None = None
    skipped_reason: str | None = None
    completed_at: str | None = None


class TodaySummary(BaseModel):
    total: int
    planned: int
    completed: int
    postponed: int
    skipped: int


class TodayOut(BaseModel):
    date: str
    summary: TodaySummary
    activities: list[ActivityOut]


class GenerateOut(BaseModel):
    already_generated: bool = False
    requested: int
    planned: int
    notice: str | None = None
    activities: list[ActivityOut]


class SkipIn(BaseModel):
    reason: str | None = Field(default=None, max_length=120)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class PostponeIn(BaseModel):
    """推迟请求：可带 scheduled_at 指定新时间（+08:00 偏移的 ISO），不传则由后端自动找下一槽。"""

    scheduled_at: str | None = None


class WeekDayStat(BaseModel):
    date: str
    planned: int
    completed: int
    postponed: int
    skipped: int


class WeekOut(BaseModel):
    week_start: str
    week_end: str
    planned: int
    completed: int
    postponed: int
    skipped: int
    days: list[WeekDayStat]


class SuggestSlot(BaseModel):
    scheduled_at: str
    activity_type: str
    title: str


class AgentSuggestOut(BaseModel):
    requested: int
    planned: int
    slots: list[SuggestSlot]
    notice: str | None = None


class AgentConfirmIn(BaseModel):
    slots: list[SuggestSlot]

    @field_validator("slots")
    @classmethod
    def _non_empty(cls, value: list[SuggestSlot]) -> list[SuggestSlot]:
        if not value:
            raise ValueError("至少要提供一条安排")
        return value


class AgentConfirmOut(BaseModel):
    planned: int
    activities: list[ActivityOut]
