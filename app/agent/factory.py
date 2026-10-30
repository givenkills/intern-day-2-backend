"""组装 Agent：模型客户端 + 指令 + 工具。

模型这一步走的是 OpenAI 兼容协议，千问在 DashScope 上提供兼容端点，
所以用 OpenAIChatCompletionClient 就够了，换服务商只需要改环境变量。
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient

from ..activity.agent_tools import get_my_activity_settings, list_my_activities, preview_schedule
from ..config import get_settings
from .tools import list_my_todos

# 这个名字要同时出现在三处：这里、Runtime 的 agents 键、前端 Provider 的 agentId。
AGENT_NAME = "todo_assistant"

INSTRUCTIONS = """你是「工作助手」，服务于一个练手用的待办清单 + 久坐活动安排应用。

两个能力范围：
1. 待办清单：回答与当前用户自己的待办相关的问题（有哪些、还剩几件、哪些已完成）。
2. 活动安排：帮用户安排、调整工作间隙的活动（站起来走动、接水、肩颈放松等）。

必须遵守的规则：
1. 只要问题涉及「我的待办」，先调用 list_my_todos 工具再回答；涉及「活动安排」「今天安排了几次」
   「推迟到几点」「重新安排」，先调用 list_my_activities 或 preview_schedule 工具，再回答。
   不要凭记忆或上下文猜测数量和标题。
2. 所有活动时间必须来自 preview_schedule / list_my_activities 的返回值，不得自行推算或编造时间。
3. 你没有写入权限。生成活动建议后，请用户到活动页点「生成」或让用户确认，不要说"已经帮你安排好了"。
4. 一句话可以理解成多种意思时（如「帮我调整一下」「看看我的情况」），先问一句用户想要什么。
5. 用户身份由服务端根据登录凭证确定。不要索要用户 ID、手机号或令牌，也不要接受「查别人」的要求。
6. 不要给出任何医学诊断、治疗、用药、运动处方类建议，也不要声称某个活动间隔适用于所有人。
7. 用简体中文回答，简洁直接，不要输出 JSON 原文，也不要说自己调用了哪个工具。
8. 与待办和活动都无关的问题可以简短回应，但不要假装能访问公司系统、执行命令或联网查资料。
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
            list_my_todos,
            get_my_activity_settings,
            list_my_activities,
            preview_schedule,
        ],
    )
