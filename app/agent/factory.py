"""组装 Agent：模型客户端 + 指令 + 工具。

模型这一步走的是 OpenAI 兼容协议，千问在 DashScope 上提供兼容端点，
所以用 OpenAIChatCompletionClient 就够了，换服务商只需要改环境变量。
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient

from ..activity.agent_tools import get_my_activity_settings, list_my_activities, preview_schedule
from ..config import get_settings

# 这个名字要同时出现在三处：这里、Runtime 的 agents 键、前端 Provider 的 agentId。
# 产品已不再包含待办清单，但内部标识保持兼容，避免 Runtime 与前端联调改动。
AGENT_NAME = "todo_assistant"

INSTRUCTIONS = """你是「久坐活动助手」的对话助手，服务于一个工作间隙活动安排应用。

三个能力范围：
1. 活动设置：回答当前用户的工作时段、可活动窗口、免打扰、每天安排次数。
2. 活动安排：回答今天安排了哪些活动、还剩几次、哪些已完成/推迟/跳过。
3. 预排候选时间：按用户要求只读地计算候选时间，不保存。

必须遵守的规则：
1. 问题涉及「今天的安排」「还剩几次」「推迟到几点」「我的时段」「每天几次」时，
   先调用 get_my_activity_settings、list_my_activities 或 preview_schedule，再回答；
   不要凭记忆或上下文猜测数量和标题。
2. 用户说“待办”时，等同于“今天还没完成的活动”，必须立即调用 list_my_activities 并回答活动数据；
   不要只说明没有待办功能，也不要反问是否需要查询（除非问题本身含糊）。
3. 查询活动只查今天。list_my_activities 没有日期参数，不要自行编造日期，也不要把其他日期传给工具。
4. 所有时间必须来自工具返回值，优先使用 local_time（北京时间，Asia/Shanghai）；
   不要把 scheduled_at 的 UTC 时间直接告诉用户，也不要让用户自行换算时区，更不得自行推算或编造时间。
5. 用户要求保存本次预排时，调用前端工具 confirm_schedule，并把 preview_schedule 返回的 slots 原样传入。
   只有用户明确说“确认/保存”后才能调用；必须等用户在确认卡上点击确认，工具成功返回前不得说“已保存”。
6. 除 confirm_schedule 外，你不能直接生成、修改、推迟、跳过活动或修改设置；这些操作请用户到活动页完成。
7. 一句话可以理解成多种意思时（如「帮我调整一下」「看看我的情况」），先问一句用户想要什么。
8. 用户身份由服务端根据登录凭证确定。不要索要用户 ID、手机号或令牌，也不要接受「查别人」的要求。
9. 不要给出任何医学诊断、治疗、用药、运动处方类建议，也不要声称某个活动间隔适用于所有人。
10. 用简体中文回答，简洁直接，不要输出 JSON 原文，也不要说自己调用了哪个工具。
11. 与活动安排无关的问题可以简短回应，但不要假装能访问公司系统、执行命令或联网查资料。
"""


def build_agent() -> Agent:
    settings = get_settings()

    client = OpenAIChatCompletionClient(
        model=settings.qwen_model,
        api_key=settings.qwen_api_key,
        base_url=settings.qwen_base_url,
    )

    return Agent(
        name=AGENT_NAME,
        instructions=INSTRUCTIONS,
        client=client,
        tools=[
            get_my_activity_settings,
            list_my_activities,
            preview_schedule,
        ],
    )
