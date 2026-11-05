"""活动助手接口：全部要求登录，且只能操作自己的数据。"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Query

from ..deps import CurrentUser, DbConn
from . import service
from .schemas import (
    ActivityOut,
    ActivitySettingsIn,
    ActivitySettingsOut,
    AgentConfirmIn,
    AgentConfirmOut,
    AgentSuggestOut,
    GenerateOut,
    PostponeIn,
    SkipIn,
    TemplateIn,
    TemplateOut,
    TemplateScheduleIn,
    TodayOut,
    WeekOut,
)
from .timeutil import get_zone, local_today_in
from .timeutil import week_start as start_of_week

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


# ---- 自定义活动模板 ----
# 静态路径段要放在 /{activity_id} 之前，避免被动态路由抢先匹配。


@router.get(
    "/templates",
    response_model=list[TemplateOut],
    summary="我的活动模板（默认不含已归档）",
)
def list_templates(
    current_user: CurrentUser,
    conn: DbConn,
    include_archived: bool = Query(default=False),
) -> list[dict]:
    return service.list_templates(conn, current_user["id"], include_archived)


@router.post("/templates", response_model=TemplateOut, summary="新建活动模板")
def create_template(payload: TemplateIn, current_user: CurrentUser, conn: DbConn) -> dict:
    return service.create_template(conn, current_user["id"], payload.model_dump())


@router.put("/templates/{template_id}", response_model=TemplateOut, summary="编辑活动模板")
def update_template(
    template_id: int,
    payload: TemplateIn,
    current_user: CurrentUser,
    conn: DbConn,
) -> dict:
    return service.update_template(conn, template_id, current_user["id"], payload.model_dump())


@router.post("/templates/{template_id}/archive", response_model=TemplateOut, summary="归档活动模板")
def archive_template(template_id: int, current_user: CurrentUser, conn: DbConn) -> dict:
    return service.archive_template(conn, template_id, current_user["id"])


@router.post(
    "/templates/{template_id}/schedule",
    response_model=ActivityOut,
    summary="把模板加入某天的安排",
)
def schedule_template(
    template_id: int,
    payload: TemplateScheduleIn,
    current_user: CurrentUser,
    conn: DbConn,
) -> dict:
    return service.schedule_template(conn, template_id, current_user["id"], payload.model_dump())


@router.post("/{activity_id}/complete", response_model=ActivityOut, summary="完成活动")
def complete(activity_id: int, current_user: CurrentUser, conn: DbConn) -> dict:
    return service.complete_activity(conn, activity_id, current_user["id"])


@router.post("/{activity_id}/skip", response_model=ActivityOut, summary="今天不方便（跳过）")
def skip(activity_id: int, payload: SkipIn, current_user: CurrentUser, conn: DbConn) -> dict:
    return service.skip_activity(conn, activity_id, current_user["id"], payload.reason)


@router.get("/{activity_id}/postpone-options", summary="可推迟的候选时间点")
def postpone_options(activity_id: int, current_user: CurrentUser, conn: DbConn) -> dict:
    return service.postpone_options(conn, activity_id, current_user["id"])


@router.post("/{activity_id}/postpone", summary="推迟活动")
def postpone(
    activity_id: int,
    current_user: CurrentUser,
    conn: DbConn,
    payload: Annotated[PostponeIn | None, Body()] = None,
) -> dict:
    scheduled_at = payload.scheduled_at if payload else None
    return service.postpone_activity(conn, activity_id, current_user["id"], scheduled_at)


@router.get("/week", response_model=WeekOut, summary="周度复盘统计")
def week(
    current_user: CurrentUser,
    conn: DbConn,
    week_start: str | None = Query(
        default=None,
        description="周一的日期 YYYY-MM-DD；不传时返回当前周",
    ),
    timezone: str = Query(default="Asia/Shanghai", description="IANA 时区名"),
) -> dict:
    try:
        zone = get_zone(timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    current_start = start_of_week(local_today_in(zone))
    if week_start is None:
        target = current_start
    else:
        try:
            target = date.fromisoformat(week_start)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="week_start 格式应为 YYYY-MM-DD，例如 2026-10-26",
            ) from exc
        if target.weekday() != 0:
            raise HTTPException(status_code=422, detail="week_start 必须是周一")
        if target > current_start:
            raise HTTPException(status_code=422, detail="不能查看未来周")

    return service.week_stats(conn, current_user["id"], target, zone)


@router.post("/agent/suggest", response_model=AgentSuggestOut, summary="生成安排建议（不落库）")
def agent_suggest(
    current_user: CurrentUser,
    conn: DbConn,
    daily_target: int = Query(default=3, ge=1, le=8),
    from_time: str | None = Query(default=None, pattern=r"^([01]\d|2[0-3]):([0-5]\d)$"),
) -> dict:
    return service.suggest_slots(conn, current_user["id"], daily_target, from_time)


@router.post("/agent/confirm", response_model=AgentConfirmOut, summary="确认并保存 Agent 建议")
def agent_confirm(
    payload: AgentConfirmIn,
    current_user: CurrentUser,
    conn: DbConn,
) -> dict:
    return service.confirm_slots(conn, current_user["id"], [s.model_dump() for s in payload.slots])
