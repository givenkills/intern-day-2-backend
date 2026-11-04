"""排期算法单元测试：纯函数，不碰数据库。

对应用例：A01-A08。
"""

from app.activity.scheduler import MIN_GAP, Slot, next_slot, plan_slots
from app.activity.timeutil import format_time, parse_time


def _win(*pairs):
    return [Slot(parse_time(a), parse_time(b)) for a, b in pairs]


def _quiet(*pairs):
    return [Slot(parse_time(a), parse_time(b)) for a, b in pairs]


def _start_times(result):
    return [format_time(s.start) for s in result.slots]


WORK = (parse_time("09:00"), parse_time("18:00"))


def test_a01_standard_three_slots_evenly_distributed():
    result = plan_slots(
        WORK[0], WORK[1], _win(("10:00", "12:00"), ("14:00", "17:30")), [], [], 3, 5, MIN_GAP
    )
    assert result.planned == 3
    assert _start_times(result) == ["10:55", "14:45", "16:35"]


def test_a02_skips_lunch_and_meeting():
    result = plan_slots(
        WORK[0],
        WORK[1],
        _win(("10:00", "12:00"), ("14:00", "17:30")),
        _quiet(("12:00", "14:00"), ("15:00", "16:00")),
        [],
        3,
        5,
        MIN_GAP,
    )
    times = _start_times(result)
    assert result.planned == 3
    # 三条都不得落在免打扰时段内
    for t in times:
        m = parse_time(t)
        assert not (12 * 60 <= m < 14 * 60)
        assert not (15 * 60 <= m < 16 * 60)
    # 有效区间 = 10:00-12:00（120 分钟）+ 16:00-17:30（90 分钟）
    assert times == ["10:35", "11:45", "16:55"]


def test_a03_all_covered_returns_empty():
    result = plan_slots(
        WORK[0], WORK[1], _win(("14:00", "17:30")), _quiet(("14:00", "17:30")), [], 3, 5, MIN_GAP
    )
    assert result.planned == 0
    assert result.slots == []
    assert result.notice is not None


def test_a04_insufficient_capacity():
    # 窗口 10:00-11:30（90 分钟），最多放 2 次（每次 5 分钟 + 30 分钟间隔）
    result = plan_slots(
        WORK[0], WORK[1], _win(("10:00", "11:30")), [], [], 3, 5, MIN_GAP
    )
    assert result.planned == 2
    assert result.requested == 3
    assert result.notice is not None


def test_a05_single_slot_at_midpoint():
    result = plan_slots(WORK[0], WORK[1], _win(("10:00", "12:00")), [], [], 1, 5, MIN_GAP)
    assert result.planned == 1
    assert _start_times(result) == ["11:00"]


def test_a06_window_clipped_to_work_hours():
    result = plan_slots(
        WORK[0], WORK[1], _win(("08:00", "12:00")), [], [], 1, 5, MIN_GAP
    )
    # 只裁剪到 09:00-12:00，中点 10:30
    assert _start_times(result) == ["10:30"]


def test_a07_snapping_to_five_minutes():
    # 构造一个会算出 10:57 的输入：窗口 10:00-10:34，时长 5，间隔 30 -> 仅够 1 次，
    # 但有效时长远小于间隔需求，会退化。改用更直接的窗口验证吸附：
    # 窗口 10:00-11:15，单次，中点 10:37.5 -> 吸附 10:40
    result = plan_slots(WORK[0], WORK[1], _win(("10:00", "11:15")), [], [], 1, 5, MIN_GAP)
    assert _start_times(result) == ["10:40"]


def test_a08_eight_slots_no_overlap():
    result = plan_slots(
        WORK[0], WORK[1], _win(("10:00", "12:00"), ("14:00", "17:30")), [], [], 8, 5, MIN_GAP
    )
    assert result.planned == 8
    starts = [s.start for s in result.slots]
    for i in range(1, len(starts)):
        assert starts[i] - starts[i - 1] >= MIN_GAP


def test_next_slot_finds_first_gap_after():
    slot = next_slot(
        WORK[0],
        WORK[1],
        _win(("14:00", "17:30")),
        _quiet(("15:00", "16:00")),
        [],
        parse_time("14:10"),
        5,
        MIN_GAP,
    )
    assert slot is not None
    assert slot.start == parse_time("16:00")


def test_next_slot_uses_after_directly_when_free():
    slot = next_slot(
        WORK[0],
        WORK[1],
        _win(("14:00", "17:30")),
        [],
        [],
        parse_time("14:10"),
        5,
        MIN_GAP,
    )
    assert slot is not None
    assert slot.start == parse_time("14:10")


def test_next_slot_none_when_no_gap():
    slot = next_slot(
        WORK[0],
        WORK[1],
        _win(("14:00", "14:30")),
        [],
        [],
        parse_time("14:10"),
        5,
        MIN_GAP,
    )
    # 只剩 14:00-14:30，放不下 5 分钟 + 30 分钟间隔
    assert slot is None
