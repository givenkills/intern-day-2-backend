"""用 Agent 写日报文字，但数字一律不让模型碰。

分工：
- 统计数字由 `stats.build_stats` 用 SQL 算好，作为事实塞进 prompt；
- 模型只负责把事实写成两句人话；
- 写回来的文字要过 5 道校验，任何一道不过就重试一次，再不过就判失败。

为什么校验这么严：模型改数字是高频且静默的错误——
「完成 3 项」写成「完成 4 项」，页面看起来完全正常，但数据是假的。
让模型"注意别编"不可靠，正则抽数字比祈祷可靠。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient

from ..config import get_settings
from .stats import DailyStats

REPORT_AGENT_NAME = "daily_report_writer"

# 正文里允许出现这些词，出现就说明模型越界了
FORBIDDEN_WORDS = ("诊断", "治疗", "处方", "用药", "药物", "疗程", "医嘱", "医学标准", "康复方案")

# 没有自报久坐数据时，不允许出现「久坐 30 分钟」这类定量描述
SEDENTARY_CLAIM = re.compile(r"久坐[^。；，\n]{0,8}?\d+\s*(小时|分钟|分)")

# 只用来判断"当天无记录"的表述是否到位
NO_DATA_PHRASES = ("无记录", "没有记录", "未记录", "没有活动", "数据不足", "无法判断", "无可依据")

# 先把日期、时间这些「不是统计值」的数字整段抠掉，再检查剩下的阿拉伯数字
_DATE_TOKEN = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}\s*月\s*\d{1,2}\s*日")
_TIME_TOKEN = re.compile(r"\d{1,2}\s*[:：]\s*\d{2}")
_NUMBER = re.compile(r"\d+")

REVIEW_LIMIT = 200
SUGGESTION_LIMIT = 120

INSTRUCTIONS = """你是「每日活动总结」的撰写者。用户会给你某一天的活动记录统计（JSON）。

你要写两段文字：
1. review：120 字以内的简短回顾，说明这一天计划了几项、完成几项、跳过几项、
   未完成几项。
2. suggestion：80 字以内、具体可执行的一条调整建议，例如把某类活动挪到某个时段；
   不要写「保持良好习惯」这种空话。

必须遵守：
1. 只能使用输入里给出的数字。不要自己加总、推算或编造数字，也不要写「大约」「左右」这类估算。
2. 不要写日期，页面已经显示了。
3. 如果 sedentary.recorded 为 false：绝不允许出现「久坐 X 分钟/小时」这类定量描述，
   只能说明未记录久坐时长、无法判断实际久坐情况。
4. 如果 has_records 为 false（当天没有任何记录）：只能说明数据不足，
   不能写「今天做得不错」，也不能写「久坐 0 分钟」或「完成 0 项」——
   没有记录不等于没有久坐。
5. 不要给出疾病诊断、治疗方案、用药建议，也不要说某个活动间隔适用于所有人。
6. 只输出一个 JSON 对象，不要 Markdown、不要代码块、不要解释。格式：
{"review": "……", "suggestion": "……", "data_notes": ["……"]}
"""


class ReportAgentError(Exception):
    """带错误码的 Agent 失败，service 层据此决定是否重试。"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ReportDraft:
    review: str
    suggestion: str
    data_notes: tuple[str, ...] = ()


def build_report_agent() -> Agent:
    """日报专用 Agent：**不挂任何工具**。

    不给工具就没有"模型自己取数"的可能性，也就不存在越权读取路径；
    它拿到的全部事实都来自 prompt 里那段由后端算好的 JSON。
    """
    settings = get_settings()
    client = OpenAIChatCompletionClient(
        model=settings.qwen_model,
        api_key=settings.qwen_api_key,
        base_url=settings.qwen_base_url,
    )
    return Agent(name=REPORT_AGENT_NAME, instructions=INSTRUCTIONS, client=client)


def build_prompt(stats: DailyStats, strict: bool = False) -> str:
    payload = stats.as_payload()
    payload["has_records"] = stats.has_records
    lines = [
        "以下是这一天的统计，请据此写回顾与建议。",
        json.dumps(payload, ensure_ascii=False, indent=2),
    ]
    if strict:
        lines.append(
            "注意：上一次的输出没有通过校验。请只使用上面 stats 里的数字，"
            "不要在正文里出现任何其它数字，也不要在没有记录时说久坐时长。"
        )
    return "\n".join(lines)


async def generate_review(stats: DailyStats) -> ReportDraft:
    """调模型写总结；输出不合规则就地重写一次，仍不合格才抛错。"""
    last_error: ReportAgentError | None = None
    for attempt in (1, 2):
        text = await _call_model(stats, strict=attempt > 1)
        try:
            return parse_and_validate(text, stats)
        except ReportAgentError as exc:
            # 只有"写错了"才值得再给一次机会；超时/上游故障由上面直接抛出
            last_error = exc
    assert last_error is not None
    raise last_error


async def _call_model(stats: DailyStats, strict: bool) -> str:
    settings = get_settings()
    agent = build_report_agent()
    try:
        response = await asyncio.wait_for(
            agent.run(build_prompt(stats, strict=strict)),
            timeout=settings.daily_report_agent_timeout,
        )
    except TimeoutError as exc:
        raise ReportAgentError(
            "AGENT_TIMEOUT", f"模型 {settings.daily_report_agent_timeout}s 未返回"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 上游异常类型不稳定，统一按可重试处理
        raise ReportAgentError("AGENT_UPSTREAM_ERROR", f"{type(exc).__name__}: {exc}") from exc

    text = (getattr(response, "text", "") or "").strip()
    if not text:
        raise ReportAgentError("AGENT_OUTPUT_INVALID", "模型返回空内容")
    return text


def parse_and_validate(text: str, stats: DailyStats) -> ReportDraft:
    """解析 + 5 道校验。任何一道不过都抛 ReportAgentError。"""
    draft = _parse(text)
    _check_length(draft)
    _check_numbers(draft, stats)
    _check_sedentary(draft, stats)
    _check_forbidden(draft)
    _check_no_data_wording(draft, stats)
    return draft


def _parse(text: str) -> ReportDraft:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ReportAgentError("AGENT_OUTPUT_INVALID", f"输出里找不到 JSON 对象: {text[:120]}")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ReportAgentError("AGENT_OUTPUT_INVALID", f"JSON 解析失败: {exc}") from exc

    if not isinstance(data, dict):
        raise ReportAgentError("AGENT_OUTPUT_INVALID", "JSON 顶层不是对象")

    review = data.get("review")
    suggestion = data.get("suggestion")
    if not isinstance(review, str) or not review.strip():
        raise ReportAgentError("AGENT_OUTPUT_INVALID", "缺少 review")
    if not isinstance(suggestion, str) or not suggestion.strip():
        raise ReportAgentError("AGENT_OUTPUT_INVALID", "缺少 suggestion")

    notes = data.get("data_notes")
    data_notes = tuple(str(n) for n in notes) if isinstance(notes, list) else ()

    return ReportDraft(review=review.strip(), suggestion=suggestion.strip(), data_notes=data_notes)


def _check_length(draft: ReportDraft) -> None:
    if len(draft.review) > REVIEW_LIMIT:
        raise ReportAgentError("AGENT_OUTPUT_INVALID", f"review 过长: {len(draft.review)} 字")
    if len(draft.suggestion) > SUGGESTION_LIMIT:
        raise ReportAgentError("AGENT_OUTPUT_INVALID", f"suggestion 过长: {len(draft.suggestion)} 字")


def _allowed_numbers(stats: DailyStats) -> set[int]:
    return {
        stats.planned,
        stats.completed,
        stats.skipped,
        stats.uncompleted,
        stats.postponed_ops,
    }


def _check_numbers(draft: ReportDraft, stats: DailyStats) -> None:
    """正文里出现的每个数字都必须是统计里真实存在的值。"""
    allowed = _allowed_numbers(stats)
    for field, content in (("review", draft.review), ("suggestion", draft.suggestion)):
        stripped = _TIME_TOKEN.sub(" ", _DATE_TOKEN.sub(" ", content))
        for token in _NUMBER.findall(stripped):
            if int(token) not in allowed:
                raise ReportAgentError(
                    "AGENT_GROUNDING_FAILED",
                    f"{field} 出现未经授权的数字 {token}（允许值 {sorted(allowed)}）",
                )


def _check_sedentary(draft: ReportDraft, stats: DailyStats) -> None:
    if stats.sedentary_minutes is not None:
        return
    for field, content in (("review", draft.review), ("suggestion", draft.suggestion)):
        match = SEDENTARY_CLAIM.search(content)
        if match:
            raise ReportAgentError(
                "AGENT_GROUNDING_FAILED",
                f"{field} 在没有自报数据时描述久坐时长: {match.group(0)}",
            )


def _check_forbidden(draft: ReportDraft) -> None:
    text = draft.review + draft.suggestion + "".join(draft.data_notes)
    for word in FORBIDDEN_WORDS:
        if word in text:
            raise ReportAgentError("AGENT_GROUNDING_FAILED", f"出现医疗相关表述: {word}")


def _check_no_data_wording(draft: ReportDraft, stats: DailyStats) -> None:
    if stats.has_records:
        return
    text = draft.review + draft.suggestion
    if not any(phrase in text for phrase in NO_DATA_PHRASES):
        raise ReportAgentError(
            "AGENT_GROUNDING_FAILED", "当天没有记录，但正文没有说明数据不足"
        )
