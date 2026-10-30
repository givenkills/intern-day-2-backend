"""活动助手接口：全部要求登录，且只能操作自己的数据。"""

from __future__ import annotations

from fastapi import APIRouter

from ..deps import CurrentUser, DbConn
from . import service
from .schemas import (
    ActivityOut,
    ActivitySettingsIn,
    ActivitySettingsOut,
    GenerateOut,
    SkipIn,
    TodayOut,
    WeekOut,
)

router = APIRouter(prefix="/activity", tags=["活动助手"])


@router.get("/settings", response_model=ActivitySettingsOut, summary="读取活动设置")
def get_settings(current_user: CurrentUser, conn: DbConn) -> dict:
    return service.get_settings(conn, current_user["id"])


@router.put("/settings", response_model=ActivitySettingsOut, summary="保存活动设置")
def save_settings(payload: ActivitySettingsIn, current_user: CurrentUser, conn: DbConn) -> dict:
    return service.save_settings(conn, current_user["id"], payload.model_dump())


@router.get("/today", response_model=TodayOut, summary="今日活动安排")
def today(current_user: CurrentUser, conn: DbConn) -> dict:
    return service.list_today(conn, current_user["id"])


@router.post("/generate", response_model=GenerateOut, summary="生成当天安排")
def generate(current_user: CurrentUser, conn: DbConn) -> dict:
    return service.generate_today(conn, current_user["id"])


@router.post("/{activity_id}/complete", response_model=ActivityOut, summary="完成活动")
def complete(activity_id: int, current_user: CurrentUser, conn: DbConn) -> dict:
    return service.complete_activity(conn, activity_id, current_user["id"])


@router.post("/{activity_id}/skip", response_model=ActivityOut, summary="今天不方便（跳过）")
def skip(activity_id: int, payload: SkipIn, current_user: CurrentUser, conn: DbConn) -> dict:
    return service.skip_activity(conn, activity_id, current_user["id"], payload.reason)


@router.post("/{activity_id}/postpone", summary="推迟活动")
def postpone(activity_id: int, current_user: CurrentUser, conn: DbConn) -> dict:
    return service.postpone_activity(conn, activity_id, current_user["id"])


@router.get("/week", response_model=WeekOut, summary="本周统计")
def week(current_user: CurrentUser, conn: DbConn) -> dict:
    return service.week_stats(conn, current_user["id"])
