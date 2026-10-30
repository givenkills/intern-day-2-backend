"""活动助手的业务服务层：设置读写、活动查询、生成、状态流转、周统计。

所有函数都接收 sqlite3.Connection 与 user_id，数据库写操作在此收口，
Agent 工具和路由都调这里，避免校验逻辑散落两处。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException, status

from ..security import to_iso, utcnow
from . import kinds
from .kinds import kind_cycle
from .scheduler import MIN_GAP, Slot, available_slots, next_slot, plan_slots
from .timeutil import (
    format_time,
    local_date_to_utc_iso,
    local_today,
    minutes_of_local_iso,
    parse_time,
    utc_to_local_iso,
    week_start,
)

DEFAULT_SETTINGS = {
    "work_start": "09:00",
    "work_end": "18:00",
    "morning_start": "10:00",
    "morning_end": "12:00",
    "afternoon_start": "14:00",
    "afternoon_end": "17:30",
    "daily_target": 3,
    "reminders_enabled": 1,
    "allow_in_worktime": 1,
    "postpone_policy": "next_slot",
    "timezone": "Asia/Shanghai",
}


@dataclass
class SettingsView:
    work_start: int
    work_end: int
    daily_target: int
    reminders_enabled: bool
    windows: list[Slot]
    quiet: list[Slot]


def get_agent_user_id() -> int:
    """Agent 工具里取当前登录用户 id（身份来自鉴权写入的 ContextVar）。"""
    from ..agent.context import get_agent_user

    return get_agent_user()["id"]


def _activity_out(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "activity_type": row["activity_type"],
        "title": row["title"],
        "description": row["description"],
        "plan_date": row["plan_date"],
        "scheduled_at": utc_to_local_iso(row["scheduled_at"]),
        "duration_minutes": row["duration_minutes"],
        "status": row["status"],
        "source": row["source"],
        "postponed_from_id": row["postponed_from_id"],
        "skipped_reason": row["skipped_reason"],
        "completed_at": row["completed_at"],
    }


def _load_activity(conn: sqlite3.Connection, activity_id: int, user_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM activities WHERE id = ? AND user_id = ?",
        (activity_id, user_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="活动不存在")
    return row


# ---- 设置 ----


def get_settings(conn: sqlite3.Connection, user_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM activity_settings WHERE user_id = ?", (user_id,)
    ).fetchone()
    quiet_rows = conn.execute(
        "SELECT id, start_time, end_time, label, enabled FROM quiet_periods "
        "WHERE user_id = ? ORDER BY id",
        (user_id,),
    ).fetchall()

    if row is None:
        base = dict(DEFAULT_SETTINGS)
        base["quiet_periods"] = []
        return base

    return {
        "work_start": row["work_start"],
        "work_end": row["work_end"],
        "morning_start": row["morning_start"],
        "morning_end": row["morning_end"],
        "afternoon_start": row["afternoon_start"],
        "afternoon_end": row["afternoon_end"],
        "daily_target": row["daily_target"],
        "reminders_enabled": bool(row["reminders_enabled"]),
        "allow_in_worktime": bool(row["allow_in_worktime"]),
        "postpone_policy": row["postpone_policy"],
        "timezone": row["timezone"],
        "quiet_periods": [
            {
                "id": q["id"],
                "start_time": q["start_time"],
                "end_time": q["end_time"],
                "label": q["label"],
                "enabled": bool(q["enabled"]),
            }
            for q in quiet_rows
        ],
    }


def _windows_of(settings: dict) -> list[Slot]:
    windows: list[Slot] = []
    if settings.get("morning_start") and settings.get("morning_end"):
        windows.append(Slot(parse_time(settings["morning_start"]), parse_time(settings["morning_end"])))
    if settings.get("afternoon_start") and settings.get("afternoon_end"):
        windows.append(
            Slot(parse_time(settings["afternoon_start"]), parse_time(settings["afternoon_end"]))
        )
    return windows


def _quiet_of(conn: sqlite3.Connection, user_id: int) -> list[Slot]:
    rows = conn.execute(
        "SELECT start_time, end_time FROM quiet_periods WHERE user_id = ? AND enabled = 1",
        (user_id,),
    ).fetchall()
    return [Slot(parse_time(r["start_time"]), parse_time(r["end_time"])) for r in rows]


def _settings_view(conn: sqlite3.Connection, user_id: int) -> SettingsView:
    settings = get_settings(conn, user_id)
    return SettingsView(
        work_start=parse_time(settings["work_start"]),
        work_end=parse_time(settings["work_end"]),
        daily_target=settings["daily_target"],
        reminders_enabled=settings["reminders_enabled"],
        windows=_windows_of(settings),
        quiet=_quiet_of(conn, user_id),
    )


def _occupied_of(conn: sqlite3.Connection, user_id: int, plan_date: str) -> list[Slot]:
    rows = conn.execute(
        "SELECT scheduled_at, duration_minutes FROM activities "
        "WHERE user_id = ? AND plan_date = ? AND status = 'planned'",
        (user_id, plan_date),
    ).fetchall()
    occupied: list[Slot] = []
    for r in rows:
        start = minutes_of_local_iso(r["scheduled_at"])
        occupied.append(Slot(start, start + r["duration_minutes"]))
    return occupied


def save_settings(conn: sqlite3.Connection, user_id: int, payload: dict) -> dict:
    """保存设置与免打扰。校验工作时段倒挂已在 schema 层做；这里校验窗口越界。"""
    work_start = parse_time(payload["work_start"])
    work_end = parse_time(payload["work_end"])

    # 窗口必须落在工作时段内（可为空）
    for start_key, end_key in (("morning_start", "morning_end"), ("afternoon_start", "afternoon_end")):
        start = payload.get(start_key)
        end = payload.get(end_key)
        if start and end:
            if parse_time(end) <= parse_time(start):
                raise HTTPException(status_code=422, detail="可活动时段的结束时间必须晚于开始时间")
            if parse_time(start) < work_start or parse_time(end) > work_end:
                raise HTTPException(
                    status_code=422,
                    detail=f"可活动时段（{start}-{end}）超出工作时段（{payload['work_start']}-{payload['work_end']}）",
                )
        elif bool(start) != bool(end):
            raise HTTPException(status_code=422, detail="可活动时段必须同时提供开始和结束时间")

    now = to_iso(utcnow())
    conn.execute(
        """
        INSERT INTO activity_settings (
            user_id, work_start, work_end, morning_start, morning_end,
            afternoon_start, afternoon_end, daily_target, reminders_enabled,
            allow_in_worktime, postpone_policy, timezone, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            work_start=excluded.work_start, work_end=excluded.work_end,
            morning_start=excluded.morning_start, morning_end=excluded.morning_end,
            afternoon_start=excluded.afternoon_start, afternoon_end=excluded.afternoon_end,
            daily_target=excluded.daily_target, reminders_enabled=excluded.reminders_enabled,
            allow_in_worktime=excluded.allow_in_worktime,
            postpone_policy=excluded.postpone_policy, timezone=excluded.timezone,
            updated_at=excluded.updated_at
        """,
        (
            user_id, payload["work_start"], payload["work_end"],
            payload.get("morning_start"), payload.get("morning_end"),
            payload.get("afternoon_start"), payload.get("afternoon_end"),
            payload["daily_target"], int(payload["reminders_enabled"]),
            int(payload["allow_in_worktime"]), payload["postpone_policy"],
            "Asia/Shanghai", now,
        ),
    )

    # 免打扰整表替换：先删后插，保持简单（单用户练习场景）
    conn.execute("DELETE FROM quiet_periods WHERE user_id = ?", (user_id,))
    for q in payload.get("quiet_periods", []):
        conn.execute(
            "INSERT INTO quiet_periods (user_id, start_time, end_time, label, enabled) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, q["start_time"], q["end_time"], q.get("label"), int(q["enabled"])),
        )
    conn.commit()
    return get_settings(conn, user_id)


# ---- 今日活动 ----


def list_today(conn: sqlite3.Connection, user_id: int) -> dict:
    day = local_today().isoformat()
    rows = conn.execute(
        "SELECT * FROM activities WHERE user_id = ? AND plan_date = ? ORDER BY scheduled_at",
        (user_id, day),
    ).fetchall()
    summary = _summary_of(rows)
    return {"date": day, "summary": summary, "activities": [_activity_out(r) for r in rows]}


def _summary_of(rows: list[sqlite3.Row]) -> dict:
    summary = {"total": 0, "planned": 0, "completed": 0, "postponed": 0, "skipped": 0}
    for r in rows:
        summary["total"] += 1
        summary[r["status"]] += 1
    return summary


def generate_today(conn: sqlite3.Connection, user_id: int) -> dict:
    """生成当天安排。幂等：当天已有 planned/completed 记录时直接返回，不重复生成。"""
    day = local_today().isoformat()
    existing = conn.execute(
        "SELECT * FROM activities WHERE user_id = ? AND plan_date = ? "
        "AND status IN ('planned', 'completed', 'postponed')",
        (user_id, day),
    ).fetchall()
    if existing:
        return {
            "already_generated": True,
            "requested": len(existing),
            "planned": len([r for r in existing if r["status"] == "planned"]),
            "notice": "今天已生成过安排，如需调整可对单条活动推迟或跳过",
            "activities": [_activity_out(r) for r in existing],
        }

    # 当天已跳过的类型不再安排
    skipped_types = {
        r["activity_type"]
        for r in conn.execute(
            "SELECT activity_type FROM activities WHERE user_id = ? AND plan_date = ? AND status = 'skipped'",
            (user_id, day),
        ).fetchall()
    }

    view = _settings_view(conn, user_id)
    result = plan_slots(
        view.work_start,
        view.work_end,
        view.windows,
        view.quiet,
        [],
        view.daily_target,
        5,
        MIN_GAP,
    )

    now = to_iso(utcnow())
    created: list[dict] = []
    for i, slot in enumerate(result.slots):
        kind = kinds.kind_cycle_excluding(i, skipped_types)
        scheduled = local_date_to_utc_iso(local_today(), slot.start)
        cursor = conn.execute(
            """
            INSERT INTO activities (
                user_id, activity_type, title, description, plan_date,
                scheduled_at, duration_minutes, status, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 5, 'planned', 'system', ?, ?)
            """,
            (user_id, kind.type, kind.title, kind.description, day, scheduled, now, now),
        )
        conn.execute(
            "INSERT INTO activity_events (activity_id, user_id, action, from_status, to_status, created_at) "
            "VALUES (?, ?, 'generate', NULL, 'planned', ?)",
            (cursor.lastrowid, user_id, now),
        )
        row = conn.execute(
            "SELECT * FROM activities WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        created.append(_activity_out(row))

    conn.commit()
    return {
        "already_generated": False,
        "requested": result.requested,
        "planned": result.planned,
        "notice": result.notice,
        "activities": created,
    }


# ---- 状态流转 ----


def complete_activity(conn: sqlite3.Connection, activity_id: int, user_id: int) -> dict:
    now = to_iso(utcnow())
    cursor = conn.execute(
        "UPDATE activities SET status = 'completed', completed_at = ?, updated_at = ? "
        "WHERE id = ? AND user_id = ? AND status = 'planned'",
        (now, now, activity_id, user_id),
    )
    if cursor.rowcount == 0:
        _raise_invalid_transition(conn, activity_id, user_id)
    conn.execute(
        "INSERT INTO activity_events (activity_id, user_id, action, from_status, to_status, created_at) "
        "VALUES (?, ?, 'complete', 'planned', 'completed', ?)",
        (activity_id, user_id, now),
    )
    conn.commit()
    return _activity_out(_load_activity(conn, activity_id, user_id))


def skip_activity(conn: sqlite3.Connection, activity_id: int, user_id: int, reason: str | None) -> dict:
    now = to_iso(utcnow())
    cursor = conn.execute(
        "UPDATE activities SET status = 'skipped', skipped_reason = ?, updated_at = ? "
        "WHERE id = ? AND user_id = ? AND status = 'planned'",
        (reason, now, activity_id, user_id),
    )
    if cursor.rowcount == 0:
        _raise_invalid_transition(conn, activity_id, user_id)
    conn.execute(
        "INSERT INTO activity_events "
        "(activity_id, user_id, action, from_status, to_status, note, created_at) "
        "VALUES (?, ?, 'skip', 'planned', 'skipped', ?, ?)",
        (activity_id, user_id, reason, now),
    )
    conn.commit()
    return _activity_out(_load_activity(conn, activity_id, user_id))


def postpone_options(conn: sqlite3.Connection, activity_id: int, user_id: int) -> dict:
    """返回当天剩余可推迟的候选时间点（30 分钟粒度，避开免打扰与已占用）。

    前端把这些时间点渲染成可点击按钮，代替难以操作的 select/time 控件。
    时间点从「原活动时间 + min_gap」之后开始，直到工作时段结束。
    """
    row = _load_activity(conn, activity_id, user_id)
    view = _settings_view(conn, user_id)
    occupied = _occupied_of(conn, user_id, row["plan_date"])

    # 原活动自身也算占用，避免候选点撞到原时间
    own_start = minutes_of_local_iso(row["scheduled_at"])
    occupied = occupied + [Slot(own_start, own_start + row["duration_minutes"])]

    free = available_slots(
        view.work_start,
        view.work_end,
        view.windows,
        view.quiet,
        occupied,
        row["duration_minutes"],
        MIN_GAP,
    )

    # 只保留原活动时间之后的候选点，按 30 分钟粒度采样
    after = own_start + MIN_GAP
    points: list[str] = []
    for slot in free:
        cursor = max(slot.start + MIN_GAP, after)
        while cursor + row["duration_minutes"] + MIN_GAP <= slot.end:
            points.append(format_time(cursor))
            cursor += 30

    # 去重 + 保序
    seen: set[str] = set()
    unique: list[str] = []
    for p in points:
        if p not in seen:
            seen.add(p)
            unique.append(p)

    return {"plan_date": row["plan_date"], "times": unique}


def postpone_activity(
    conn: sqlite3.Connection,
    activity_id: int,
    user_id: int,
    scheduled_at: str | None = None,
) -> dict:
    """推迟：原记录置 postponed，派生一条新的 planned。

    - 传 scheduled_at（+08:00 偏移 ISO）：推迟到用户指定的时间，校验通过才落库。
    - 不传：自动找下一个可用槽（兜底）。

    关键：先算槽/校验，通过才在同一个事务里改状态 + 插新记录；
    失败时原记录保持 planned 不变。
    """
    row = _load_activity(conn, activity_id, user_id)
    if row["status"] != "planned":
        _raise_invalid_transition(conn, activity_id, user_id)

    view = _settings_view(conn, user_id)
    now = to_iso(utcnow())

    if scheduled_at is not None:
        # 用户指定时间：校验 + 冲突检查，通过则用这个时间
        minutes = _validate_slot_time(view, scheduled_at, ignore_activity_id=activity_id)
        _validate_no_time_conflict(
            conn, user_id, row["plan_date"], minutes, row["duration_minutes"], ignore_activity_id=activity_id
        )
        scheduled = scheduled_at
    else:
        # 自动找下一槽
        occupied = _occupied_of(conn, user_id, row["plan_date"])
        after = max(minutes_of_local_iso(row["scheduled_at"]) + MIN_GAP, minutes_of_local_iso(now))
        slot = next_slot(
            view.work_start,
            view.work_end,
            view.windows,
            view.quiet,
            occupied,
            after,
            row["duration_minutes"],
            MIN_GAP,
        )
        if slot is None:
            return {
                "ok": False,
                "reason": "今日剩余时段不足，无法再推迟",
                "activity": _activity_out(row),
            }
        scheduled = local_date_to_utc_iso(local_today(), slot.start)

    conn.execute(
        "UPDATE activities SET status = 'postponed', updated_at = ? WHERE id = ? AND user_id = ?",
        (now, activity_id, user_id),
    )
    cursor = conn.execute(
        """
        INSERT INTO activities (
            user_id, activity_type, title, description, plan_date, scheduled_at,
            duration_minutes, status, source, postponed_from_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?)
        """,
        (
            user_id, row["activity_type"], row["title"], row["description"],
            row["plan_date"], scheduled, row["duration_minutes"], row["source"],
            activity_id, now, now,
        ),
    )
    new_id = cursor.lastrowid
    conn.execute(
        "INSERT INTO activity_events (activity_id, user_id, action, from_status, to_status, created_at) "
        "VALUES (?, ?, 'postpone', 'planned', 'postponed', ?)",
        (activity_id, user_id, now),
    )
    conn.execute(
        "INSERT INTO activity_events (activity_id, user_id, action, from_status, to_status, created_at) "
        "VALUES (?, ?, 'create', NULL, 'planned', ?)",
        (new_id, user_id, now),
    )
    conn.commit()

    new_row = _load_activity(conn, new_id, user_id)
    return {"ok": True, "activity": _activity_out(new_row)}


def _raise_invalid_transition(conn: sqlite3.Connection, activity_id: int, user_id: int) -> None:
    row = _load_activity(conn, activity_id, user_id)
    labels = {
        "completed": "已完成",
        "postponed": "已推迟",
        "skipped": "已跳过",
    }
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"该活动{labels.get(row['status'], row['status'])}，无法重复操作",
    )


# ---- 周统计 ----


def week_stats(conn: sqlite3.Connection, user_id: int) -> dict:
    today = local_today()
    start = week_start(today)
    days = [start + timedelta(days=i) for i in range(7)]
    end = days[-1]

    rows = conn.execute(
        """
        SELECT plan_date, status FROM activities
        WHERE user_id = ? AND plan_date >= ? AND plan_date <= ?
        """,
        (user_id, start.isoformat(), end.isoformat()),
    ).fetchall()

    day_map = {
        d.isoformat(): {
            "date": d.isoformat(), "planned": 0,
            "completed": 0, "postponed": 0, "skipped": 0,
        }
        for d in days
    }
    planned = completed = postponed = skipped = 0
    for r in rows:
        key = r["plan_date"]
        if key not in day_map:
            continue
        if r["status"] == "postponed":
            # 被推迟的原记录不计入计划数，只计一次推迟动作
            day_map[key]["postponed"] += 1
            postponed += 1
        else:
            day_map[key]["planned"] += 1
            planned += 1
            if r["status"] == "completed":
                day_map[key]["completed"] += 1
                completed += 1
            elif r["status"] == "skipped":
                day_map[key]["skipped"] += 1
                skipped += 1

    return {
        "week_start": start.isoformat(),
        "week_end": end.isoformat(),
        "planned": planned,
        "completed": completed,
        "postponed": postponed,
        "skipped": skipped,
        "days": [day_map[d.isoformat()] for d in days],
    }


# ---- Agent 建议与确认 ----


def suggest_slots(conn: sqlite3.Connection, user_id: int, daily_target: int, from_time: str | None) -> dict:
    """生成候选时间槽（只读，不落库）。from_time 为 HH:MM，可选。"""
    view = _settings_view(conn, user_id)
    occupied = _occupied_of(conn, user_id, local_today().isoformat())

    windows = view.windows
    if from_time:
        after = parse_time(from_time)
        windows = [Slot(max(w.start, after), w.end) for w in windows if w.end > after]

    result = plan_slots(
        view.work_start,
        view.work_end,
        windows,
        view.quiet,
        occupied,
        min(8, max(1, daily_target)),
        5,
        MIN_GAP,
    )

    slots = []
    for i, slot in enumerate(result.slots):
        kind = kind_cycle(i)
        scheduled = utc_to_local_iso(local_date_to_utc_iso(local_today(), slot.start))
        slots.append(
            {
                "scheduled_at": scheduled,
                "activity_type": kind.type,
                "title": kind.title,
            }
        )

    return {
        "requested": result.requested,
        "planned": result.planned,
        "slots": slots,
        "notice": result.notice,
    }


def _validate_slot_time(view: SettingsView, scheduled_utc: str, ignore_activity_id: int | None = None) -> int:
    """校验一个时间是否可排，返回该时间的本地分钟数；不合法抛 422。

    ignore_activity_id：推迟时忽略原记录自身，避免「改到同一时间」被误判为冲突。
    """
    minutes = minutes_of_local_iso(scheduled_utc)

    # 必须落在工作时段内
    if not (view.work_start <= minutes < view.work_end):
        raise HTTPException(
            status_code=422,
            detail=f"计划时间 {format_time(minutes)} 不在工作时段内",
        )
    # 必须落在某个可活动窗口内
    in_window = any(w.start <= minutes < w.end for w in view.windows)
    if not in_window:
        raise HTTPException(
            status_code=422,
            detail=f"计划时间 {format_time(minutes)} 不在可活动时段内",
        )
    # 不得落在免打扰时段内
    for q in view.quiet:
        if q.start <= minutes < q.end:
            quiet_range = f"{format_time(q.start)}-{format_time(q.end)}"
            raise HTTPException(
                status_code=422,
                detail=f"计划时间 {format_time(minutes)} 与免打扰时段（{quiet_range}）冲突",
            )
    return minutes


def _validate_no_time_conflict(
    conn: sqlite3.Connection,
    user_id: int,
    plan_date: str,
    minutes: int,
    duration: int,
    ignore_activity_id: int | None = None,
) -> None:
    """校验该时间不与当天其它 planned 活动冲突（± min_gap）。冲突抛 422。"""
    rows = conn.execute(
        "SELECT id, scheduled_at, duration_minutes FROM activities "
        "WHERE user_id = ? AND plan_date = ? AND status = 'planned'",
        (user_id, plan_date),
    ).fetchall()
    for r in rows:
        if ignore_activity_id is not None and r["id"] == ignore_activity_id:
            continue
        other_start = minutes_of_local_iso(r["scheduled_at"])
        other_end = other_start + r["duration_minutes"]
        this_end = minutes + duration
        if minutes < other_end + MIN_GAP and other_start < this_end + MIN_GAP:
            raise HTTPException(
                status_code=422,
                detail=f"计划时间 {format_time(minutes)} 与已有安排（{format_time(other_start)}）冲突",
            )


def confirm_slots(conn: sqlite3.Connection, user_id: int, slots: list[dict]) -> dict:
    """用户确认后保存 Agent 建议。每个时间都重新校验一遍，防前端/模型篡改。"""
    if not slots:
        raise HTTPException(status_code=422, detail="没有可保存的安排")

    view = _settings_view(conn, user_id)
    day = local_today()
    now = to_iso(utcnow())

    created: list[dict] = []
    for slot in slots:
        scheduled_utc = slot["scheduled_at"]
        minutes = _validate_slot_time(view, scheduled_utc)
        _validate_no_time_conflict(conn, user_id, day.isoformat(), minutes, 5)

        activity_type = slot.get("activity_type", "walk")
        if not kinds.is_valid_type(activity_type):
            raise HTTPException(status_code=422, detail=f"无效的活动类型：{activity_type}")
        kind = kinds.kind_of(activity_type)

        cursor = conn.execute(
            """
            INSERT INTO activities (
                user_id, activity_type, title, description, plan_date, scheduled_at,
                duration_minutes, status, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 5, 'planned', 'agent', ?, ?)
            """,
            (
                user_id, kind.type, kind.title, kind.description, day.isoformat(),
                scheduled_utc, now, now,
            ),
        )
        conn.execute(
            "INSERT INTO activity_events (activity_id, user_id, action, from_status, to_status, created_at) "
            "VALUES (?, ?, 'create', NULL, 'planned', ?)",
            (cursor.lastrowid, user_id, now),
        )
        row = conn.execute("SELECT * FROM activities WHERE id = ?", (cursor.lastrowid,)).fetchone()
        created.append(_activity_out(row))

    conn.commit()
    return {"planned": len(created), "activities": created}
