"""活动助手的 Agent 只读工具。

和待办工具一样，函数签名里没有 user_id：身份来自登录校验写入的 ContextVar。
时间一律由后端算好返回，模型只负责理解用户意图、挑选和解释，不自己推算时间。

三个工具：
- get_my_activity_settings  读工作时段、可活动窗口、免打扰、每天次数
- list_my_activities         读某天的活动安排与汇总
- preview_schedule           按参数预排候选时间槽（只读，不落库）
"""

from __future__ import annotations

import json
from typing import Annotated

from agent_framework import tool

from ..db import connection
from . import service
from .kinds import kind_cycle
from .scheduler import MIN_GAP, Slot, plan_slots
from .timeutil import format_time, local_date_to_utc_iso, local_today, parse_time

_DATE_HINT = "YYYY-MM-DD，例如 2026-10-30；不传默认今天"


@tool(
    name="get_my_activity_settings",
    description=(
        "查询当前用户的活动设置：工作时段、上午/下午可活动窗口、免打扰时段、每天安排次数。"
        "当用户问起「我的工作时段」「我设的免打扰」「每天安排几次」时调用。只读。"
    ),
)
async def get_my_activity_settings() -> str:
    user = service.get_agent_user_id()
    with connection() as conn:
        settings = service.get_settings(conn, user)
    return json.dumps(settings, ensure_ascii=False)


@tool(
    name="list_my_activities",
    description=(
        "查询当前用户某一天的活动安排与状态汇总。"
        "当用户问起「今天/某天安排了几次」「还剩几次没做」「推迟到几点」时调用。只读。"
        "返回 JSON，含每条活动的时间、状态、来源，以及 planned/completed/postponed/skipped 计数。"
    ),
)
async def list_my_activities(
    date: Annotated[str, _DATE_HINT] = "",
) -> str:
    user = service.get_agent_user_id()
    day = date or local_today().isoformat()
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM activities WHERE user_id = ? AND plan_date = ? ORDER BY scheduled_at",
            (user, day),
        ).fetchall()
    payload = {
        "date": day,
        "summary": {
            "total": len(rows),
            "planned": sum(1 for r in rows if r["status"] == "planned"),
            "completed": sum(1 for r in rows if r["status"] == "completed"),
            "postponed": sum(1 for r in rows if r["status"] == "postponed"),
            "skipped": sum(1 for r in rows if r["status"] == "skipped"),
        },
        "activities": [
            {
                "id": r["id"],
                "title": r["title"],
                "scheduled_at": r["scheduled_at"],
                "status": r["status"],
                "source": r["source"],
                "postponed_from_id": r["postponed_from_id"],
            }
            for r in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


@tool(
    name="preview_schedule",
    description=(
        "按给定参数预排候选活动时间，只读、不保存。"
        "当用户想重新安排活动、调整次数，或说「下午要开会，帮我重新排」时调用。"
        "返回后端计算好的候选时间槽，以及因落在免打扰/工作时段外而不可用的原因。"
        "所有时间都是后端算的，模型不得自行推算或编造时间。"
    ),
)
async def preview_schedule(
    daily_target: Annotated[int, "想安排几次，1 到 8"] = 3,
    from_time: Annotated[str, "只考虑这个时间之后的时段，HH:MM 格式；不传则从最早开始"] = "",
) -> str:
    user = service.get_agent_user_id()
    with connection() as conn:
        view = service._settings_view(conn, user)
        occupied = service._occupied_of(conn, user, local_today().isoformat())

    # 从 from_time 之后开始：把早于它的可用区间裁掉
    windows = view.windows
    if from_time:
        try:
            after = parse_time(from_time)
        except ValueError:
            return json.dumps(
                {"error": "invalid_time", "hint": "from_time 必须是 HH:MM 格式"}, ensure_ascii=False
            )
        windows = [Slot(max(w.start, after), w.end) for w in windows if w.end > after]

    result = plan_slots(
        view.work_start,
        view.work_end,
        windows,
        view.quiet,
        occupied,
        min(8, max(1, int(daily_target))),
        5,
        MIN_GAP,
    )

    slots = []
    for i, slot in enumerate(result.slots):
        kind = kind_cycle(i)
        scheduled = local_date_to_utc_iso(local_today(), slot.start)
        slots.append(
            {
                "scheduled_at": scheduled,
                "local_time": format_time(slot.start),
                "activity_type": kind.type,
                "title": kind.title,
            }
        )

    payload = {
        "requested": result.requested,
        "planned": result.planned,
        "slots": slots,
        "notice": result.notice,
        "quiet_periods": [f"{format_time(q.start)}-{format_time(q.end)}" for q in view.quiet],
    }
    return json.dumps(payload, ensure_ascii=False)
