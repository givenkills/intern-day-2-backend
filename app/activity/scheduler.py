"""排期算法：把「每天 N 次」均匀铺进有效可活动时段。

有效时段 = Σ(每个可活动窗口 ∩ 工作时段) − 免打扰 − 已占用区间。

名额分配用「累积位置法」：把有效区间首尾相接看成一条虚拟时间轴，
第 i 个点取 (i + 0.5) * L / N，再沿虚拟轴还原成真实时间。这样跨午休、
跨会议的空洞会被自然跳过，不需要逐个判断某个点是否落在免打扰里。

本模块是纯函数：输入时段参数与占用，输出时间列表，不碰数据库。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    start: int  # 当天第几分钟
    end: int  # 当天第几分钟


@dataclass(frozen=True)
class PlanResult:
    slots: list[Slot]
    requested: int
    planned: int
    notice: str | None


MIN_GAP = 30  # 两次活动的最小间隔（分钟）


def _merge(slots: list[Slot]) -> list[Slot]:
    """合并重叠或首尾相接的区间，按 start 升序返回。"""
    if not slots:
        return []
    ordered = sorted(slots, key=lambda s: s.start)
    merged: list[Slot] = [ordered[0]]
    for slot in ordered[1:]:
        last = merged[-1]
        if slot.start <= last.end:
            merged[-1] = Slot(last.start, max(last.end, slot.end))
        else:
            merged.append(slot)
    return merged


def _subtract(intervals: list[Slot], cuts: list[Slot]) -> list[Slot]:
    """从 intervals 里挖掉 cuts（免打扰 / 已占用）。"""
    result: list[Slot] = []
    for iv in intervals:
        cursor = iv.start
        for cut in sorted(cuts, key=lambda c: c.start):
            if cut.end <= cursor or cut.start >= iv.end:
                continue
            if cut.start > cursor:
                result.append(Slot(cursor, min(cut.start, iv.end)))
            cursor = max(cursor, cut.end)
            if cursor >= iv.end:
                break
        if cursor < iv.end:
            result.append(Slot(cursor, iv.end))
    return result


def available_slots(
    work_start: int,
    work_end: int,
    windows: list[Slot],
    quiet: list[Slot],
    occupied: list[Slot],
    duration: int,
    min_gap: int = MIN_GAP,
) -> list[Slot]:
    """计算所有能放下一个 activity（含最小间隔）的可用区间。"""
    base: list[Slot] = []
    for win in windows:
        start = max(win.start, work_start)
        end = min(win.end, work_end)
        if end > start:
            base.append(Slot(start, end))

    base = _merge(base)
    free = _subtract(base, _merge(quiet))
    free = _subtract(free, _merge(occupied))

    # 过滤掉长度不足以容纳 activity + 两侧间隔的碎片
    return [Slot(s.start, s.end) for s in free if (s.end - s.start) >= duration + 2 * min_gap]


def plan_slots(
    work_start: int,
    work_end: int,
    windows: list[Slot],
    quiet: list[Slot],
    occupied: list[Slot],
    count: int,
    duration: int,
    min_gap: int = MIN_GAP,
) -> PlanResult:
    """生成一天 N 个活动的时间点。"""
    free = available_slots(work_start, work_end, windows, quiet, occupied, duration, min_gap)
    total = sum(s.end - s.start for s in free)

    planned = min(count, max(0, total // (duration + min_gap)))
    notice = None
    if planned < count:
        notice = f"今日可活动时段只够安排 {planned} 次" if planned else "今日没有可用的活动时段"

    slots: list[Slot] = []
    for i in range(planned):
        pos = (i + 0.5) * total / planned
        # 沿虚拟轴还原真实时间
        acc = 0
        for s in free:
            if pos < acc + (s.end - s.start):
                raw = s.start + (pos - acc)
                # 吸附到 5 分钟整数倍（向上取整）
                snapped = ((int(raw) + 4) // 5) * 5
                # 不越过区间末端，且给活动留出时长
                snapped = min(snapped, s.end - duration)
                slots.append(Slot(snapped, snapped + duration))
                break
            acc += s.end - s.start

    return PlanResult(slots=slots, requested=count, planned=planned, notice=notice)


def next_slot(
    work_start: int,
    work_end: int,
    windows: list[Slot],
    quiet: list[Slot],
    occupied: list[Slot],
    after: int,
    duration: int,
    min_gap: int = MIN_GAP,
) -> Slot | None:
    """从 after（当天第几分钟）起，找第一个能放下 activity 的位置。"""
    free = available_slots(work_start, work_end, windows, quiet, occupied, duration, min_gap)
    for s in free:
        candidate = max(s.start, after)
        if candidate + duration <= s.end:
            snapped = ((candidate + 4) // 5) * 5
            snapped = max(snapped, candidate)
            if snapped + duration <= s.end:
                return Slot(snapped, snapped + duration)
    return None
