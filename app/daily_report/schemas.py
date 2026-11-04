"""每日总结的请求与响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class StatsOut(BaseModel):
    """全部由后端 SQL 算出，模型不参与。"""

    planned: int = Field(description="当日计划活动数（推迟链上的中间记录不计入）")
    completed: int = Field(description="已完成")
    skipped: int = Field(description="已跳过")
    uncompleted: int = Field(description="未完成 = planned - completed - skipped")
    postponed_ops: int = Field(description="推迟操作次数，与活动数不同维度，可与 completed 重叠")


class SedentaryOut(BaseModel):
    """用户自报的久坐情况。目前没有录入入口，恒为未记录。"""

    recorded: bool
    minutes: int | None


class ReportErrorOut(BaseModel):
    code: str
    message: str


class DailyReportOut(BaseModel):
    report_date: str
    status: Literal["not_generated", "pending", "running", "success", "failed"]
    stats: StatsOut | None
    review: str | None
    suggestion: str | None
    sedentary: SedentaryOut
    generated_at: str | None = Field(default=None, description="生成时间（+08:00），与日报日期分开保存")
    attempt: int = Field(default=0, description="已经执行过几次")
    error: ReportErrorOut | None


class GenerateOut(BaseModel):
    report_date: str
    status: Literal["queued", "in_progress", "already_generated"]
    job_id: str | None = None
    message: str | None = None
