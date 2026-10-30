"""活动类型库：轻量的久坐缓解动作，刻意排除医疗/运动处方类内容。"""

from __future__ import annotations

from dataclasses import dataclass

_VALID_TYPES = ("walk", "water", "stretch")


@dataclass(frozen=True)
class ActivityKind:
    type: str
    title: str
    description: str


ACTIVITY_KINDS: tuple[ActivityKind, ...] = (
    ActivityKind(
        type="walk",
        title="站起来走动",
        description="离开座位走动一会儿，活动活动腿脚，再回来继续工作。",
    ),
    ActivityKind(
        type="water",
        title="接水或去洗手间",
        description="起身去接杯水，或者去趟洗手间，顺便离开屏幕休息一下。",
    ),
    ActivityKind(
        type="stretch",
        title="肩颈放松",
        description="做几次轻柔的耸肩、转头，缓解久坐带来的肩颈紧绷。",
    ),
)

_KIND_BY_TYPE = {kind.type: kind for kind in ACTIVITY_KINDS}


def is_valid_type(activity_type: str) -> bool:
    return activity_type in _VALID_TYPES


def kind_of(activity_type: str) -> ActivityKind:
    """按类型取活动文案。类型非法抛 KeyError。"""
    return _KIND_BY_TYPE[activity_type]


def kind_cycle(index: int) -> ActivityKind:
    """按序号轮转取活动，用于把每天 N 次均匀分配到不同活动。"""
    return ACTIVITY_KINDS[index % len(ACTIVITY_KINDS)]


def kind_cycle_excluding(index: int, excluded_types: set[str]) -> ActivityKind:
    """轮转取活动，但跳过 excluded_types 里的类型。

    当天被「跳过」的活动类型不再安排；若所有类型都被排除则退回轮转（至少得安排一个）。
    """
    available = [k for k in ACTIVITY_KINDS if k.type not in excluded_types]
    pool = available if available else list(ACTIVITY_KINDS)
    return pool[index % len(pool)]
