"""把「这次请求已经验证过的用户」传给工具。

为什么不把 user_id 作为工具参数：
工具参数是模型生成的。只要模型能传 user_id，用户就能在聊天里说
「帮我查 user_id=2 的待办」，或者用构造的请求体骗过模型。
所以工具签名里根本没有用户参数——身份只从服务端的登录校验结果里取。

ContextVar 而不是全局变量：它按协程隔离，两个用户并发对话时不会串。
"""

from __future__ import annotations

from contextvars import ContextVar

# 只在 agent_endpoint 的鉴权依赖里写入，工具里读。
current_agent_user: ContextVar[dict | None] = ContextVar("current_agent_user", default=None)


class AgentIdentityMissing(RuntimeError):
    """工具在没有任何登录身份的上下文里被调用。"""


def set_agent_user(user: dict) -> None:
    current_agent_user.set(user)


def get_agent_user() -> dict:
    user = current_agent_user.get()
    if user is None:
        raise AgentIdentityMissing("工具必须在已登录的请求里执行")
    return user
