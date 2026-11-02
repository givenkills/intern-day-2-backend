"""每日总结接口。

两个接口都要求登录，用户身份只从令牌解析——路径里没有 user_id，
请求体里也不接受 user_id，所以「改 URL 看别人的日报」在协议层就不成立。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Response, status

from ..activity.timeutil import local_today
from ..config import get_settings
from ..deps import CurrentUser, DbConn
from . import queue, store
from .schemas import DailyReportOut, GenerateOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["每日总结"])


def parse_report_date(raw: str) -> date:
    """只接受 YYYY-MM-DD，且必须是已经结束的自然日。"""
    try:
        day = date.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="日期格式应为 YYYY-MM-DD，例如 2026-11-02",
        ) from exc

    if day >= local_today():
        raise HTTPException(
            status_code=422,
            detail="只能查看已结束的自然日（当天还没过完，无法总结）",
        )
    if day < local_today() - timedelta(days=365):
        raise HTTPException(
            status_code=422,
            detail="只能查看一年以内的日报",
        )
    return day


@router.get("/{report_date}", response_model=DailyReportOut, summary="查询某天的日报")
def get_daily_report(report_date: str, current_user: CurrentUser, conn: DbConn) -> dict:
    day = parse_report_date(report_date)
    row = store.get_report(conn, current_user["id"], day)
    return store.to_api(row, day)


@router.post(
    "/{report_date}/generate",
    response_model=GenerateOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="手动生成/补跑某天的日报",
)
async def generate_daily_report(
    report_date: str,
    response: Response,
    current_user: CurrentUser,
    conn: DbConn,
) -> dict:
    """只做「占位 + 入队」，立刻返回，不等模型。"""
    day = parse_report_date(report_date)
    user_id = current_user["id"]
    lease_seconds = get_settings().daily_report_lease_seconds

    row = store.get_report(conn, user_id, day)
    if row is not None:
        if row["status"] == "success":
            response.status_code = status.HTTP_200_OK
            return {
                "report_date": day.isoformat(),
                "status": "already_generated",
                "message": "该日总结已生成，直接复用已有结果",
            }
        if row["status"] == "running" and not store.lease_expired(row, lease_seconds):
            response.status_code = status.HTTP_200_OK
            return {
                "report_date": day.isoformat(),
                "status": "in_progress",
                "message": "该日总结正在生成中",
            }

    try:
        job_id, queued = await queue.enqueue_report(user_id, day)
    except queue.QueueUnavailable:
        # 队列不可用必须如实报错，不能让前端以为已经排队
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="任务队列暂不可用，总结未提交，请稍后重试",
        ) from None

    # 入队成功后再占位：失败时库里不留一条永远 pending 的假记录
    store.ensure_pending(conn, user_id, day)

    if not queued:
        response.status_code = status.HTTP_200_OK
        return {
            "report_date": day.isoformat(),
            "status": "in_progress",
            "job_id": job_id,
            "message": "该日任务已在队列中，无需重复提交",
        }

    logger.info("manual_trigger user=%s date=%s job_id=%s", user_id, day, job_id)
    return {
        "report_date": day.isoformat(),
        "status": "queued",
        "job_id": job_id,
        "message": "已提交到后台队列，稍后刷新查看",
    }
