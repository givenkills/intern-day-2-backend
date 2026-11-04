"""Agent 输出校验：模型可以写得平淡，但不能写假。

每条用例对应一条具体的事实约束——
数字不许编、没记录不许说久坐时长、不许给医疗建议、没数据必须说明数据不足。
"""

from __future__ import annotations

import json

import pytest

from app.daily_report.agent import ReportAgentError, parse_and_validate
from app.daily_report.stats import ActivityBrief, DailyStats


def make_stats(**overrides) -> DailyStats:
    base = {
        "report_date": "2026-11-02",
        "planned": 5,
        "completed": 3,
        "skipped": 1,
        "uncompleted": 1,
        "postponed_ops": 2,
        "sedentary_minutes": None,
        "activities": (
            ActivityBrief(title="站起来走动", time="09:30", status="completed", postponed=False),
            ActivityBrief(title="肩颈放松", time="14:00", status="planned", postponed=True),
        ),
    }
    base.update(overrides)
    return DailyStats(**base)


def as_text(review: str, suggestion: str = "把 14:00 的活动挪到午饭前，冲突更少。") -> str:
    return json.dumps({"review": review, "suggestion": suggestion, "data_notes": []}, ensure_ascii=False)


def test_valid_output_passes():
    result = parse_and_validate(
        as_text("当天 5 项安排完成 3 项，跳过 1 项，还有 1 项没执行，推迟 2 次。"),
        make_stats(),
    )
    assert result.review.startswith("当天 5 项")


def test_fabricated_number_is_rejected():
    """完成 4 项是模型编的（真实是 3），必须拒绝。"""
    with pytest.raises(ReportAgentError) as excinfo:
        parse_and_validate(as_text("当天 5 项安排完成 4 项，状态不错。"), make_stats())
    assert excinfo.value.code == "AGENT_GROUNDING_FAILED"
    assert "4" in excinfo.value.detail


def test_time_tokens_are_not_mistaken_for_counts():
    """14:00 / 16:30 是时间不是统计值，不该被判成编造。"""
    result = parse_and_validate(
        as_text("5 项安排完成 3 项。可以把 16:30 那条挪到 14:00 前后。"),
        make_stats(),
    )
    assert "16:30" in result.review


def test_sedentary_minutes_rejected_when_not_recorded():
    """没有自报数据时出现「久坐 30 分钟」——数字 30 恰好在允许集合里，
    这条用例专门验证「久坐定量描述」这一条独立规则。"""
    stats = make_stats(planned=30, completed=3, skipped=1, uncompleted=26, postponed_ops=2)
    with pytest.raises(ReportAgentError) as excinfo:
        parse_and_validate(
            as_text("今天久坐 30 分钟，活动执行得还可以。", suggestion="每小时起来一次。"),
            stats,
        )
    assert excinfo.value.code == "AGENT_GROUNDING_FAILED"
    assert "久坐" in excinfo.value.detail


def test_medical_advice_is_rejected():
    with pytest.raises(ReportAgentError) as excinfo:
        parse_and_validate(
            as_text("当天 5 项安排完成 3 项。", suggestion="建议去医院治疗腰椎问题。"),
            make_stats(),
        )
    assert excinfo.value.code == "AGENT_GROUNDING_FAILED"


def test_no_records_must_say_data_insufficient():
    """没有记录时不能写成「完成 0 项，继续保持」。"""
    stats = make_stats(planned=0, completed=0, skipped=0, uncompleted=0, postponed_ops=0, activities=())
    with pytest.raises(ReportAgentError) as excinfo:
        parse_and_validate(
            as_text("今天完成 0 项，久坐控制得不错。", suggestion="明天继续加油。"),
            stats,
        )
    assert excinfo.value.code in {"AGENT_GROUNDING_FAILED", "AGENT_OUTPUT_INVALID"}


def test_no_records_with_correct_wording_passes():
    stats = make_stats(planned=0, completed=0, skipped=0, uncompleted=0, postponed_ops=0, activities=())
    result = parse_and_validate(
        as_text(
            "当天没有活动记录，无法判断实际执行情况，也未记录久坐时长。",
            suggestion="明天先安排一次短活动。",
        ),
        stats,
    )
    assert "无记录" in result.review or "没有活动记录" in result.review


def test_missing_json_is_rejected():
    with pytest.raises(ReportAgentError) as excinfo:
        parse_and_validate("好的，我帮你写一段总结：今天还不错。", make_stats())
    assert excinfo.value.code == "AGENT_OUTPUT_INVALID"


def test_too_long_review_is_rejected():
    with pytest.raises(ReportAgentError) as excinfo:
        parse_and_validate(as_text("完成 3 项。" * 40), make_stats())
    assert excinfo.value.code == "AGENT_OUTPUT_INVALID"


def test_json_inside_code_fence_is_tolerated():
    """模型爱包 ```json，能解析出来就不该白扔一次。"""
    fenced = "```json\n" + as_text("当天 5 项安排完成 3 项，跳过 1 项。") + "\n```"
    result = parse_and_validate(fenced, make_stats())
    assert "5 项" in result.review
