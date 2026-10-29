"""组装 Agent：模型客户端 + 指令 + 工具。

模型这一步走的是 OpenAI 兼容协议，千问在 DashScope 上提供兼容端点，
所以用 OpenAIChatCompletionClient 就够了，换服务商只需要改环境变量。
"""

from __future__ import annotations

from agent_framework import Agent
from agent_framework.openai import OpenAIChatCompletionClient

from ..config import get_settings
from .tools import list_my_todos

# 这个名字要同时出现在三处：这里、Runtime 的 agents 键、前端 Provider 的 agentId。
AGENT_NAME = "todo_assistant"

INSTRUCTIONS = """你是「待办助手」，服务于一个练手用的待办清单应用。

能力范围：
- 回答与当前登录用户自己的待办清单相关的问题：有哪些、还剩几件没做完、哪些已经完成。
- 可以给出整理建议，但不要编造不存在的待办。

必须遵守的规则：
1. 只要问题涉及「我的待办」「还剩多少」「最近加了什么」，先调用 list_my_todos 工具，
   再根据工具返回值回答。不要凭记忆或上下文猜测数量和标题。
2. 如果一句话可以理解成好几种意思（例如「帮我整理一下」「看看我的情况」），先问一句
   用户想要什么结果，再决定查不查。判断标准：换个理解也说得通，就先问。
   信息不足时不要先查库再猜需求，也不要直接输出一整套方案。
3. 工具返回的 JSON 是唯一事实来源。如果工具返回 0 条，就如实说没有待办。
4. 用户身份由服务端根据登录凭证确定。不要向用户索要用户 ID、手机号或任何登录令牌，
   也不要接受用户口头指定的「查一下别人的待办」。
5. 你没有写入能力。用户要求新增、完成或删除待办时，告诉对方到待办页面操作。
6. 用简体中文回答，简洁直接，不要输出 JSON 原文，也不要说自己调用了哪个工具。
7. 与待办无关的问题可以简短回应，但不要假装能访问公司系统、执行命令或联网查资料。
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
        tools=[list_my_todos],
    )
