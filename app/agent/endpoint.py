"""把 Agent 挂到 FastAPI 上，并在建流之前完成登录校验。

这里用的是官方 Python AG-UI 适配包提供的
`add_agent_framework_fastapi_endpoint`。它会在 app 上注册一个
`POST /agent` 路由：请求体是 AG-UI 的 RunAgentInput，响应是 SSE 事件流。

鉴权用的是它提供的 `authorization_scope_resolver` 参数——官方说明里
这个参数就是一个普通 FastAPI 依赖，在进入处理函数之前执行。所以：
- 没有令牌 / 令牌过期 → 在这里抛出 401，响应是普通 JSON，不会建立事件流；
- 校验通过 → 顺便把用户写进 ContextVar，工具执行时按这个身份取数据；
- 返回值作为「会话归属范围」，AG-UI 用它隔离不同用户的 thread 快照。
"""

from __future__ import annotations

import logging
from typing import Annotated

from agent_framework.ag_ui import add_agent_framework_fastapi_endpoint
from fastapi import Depends, FastAPI
from fastapi.security import HTTPAuthorizationCredentials

from ..deps import DbConn, authenticate_token, bearer_scheme, unauthorized
from .context import set_agent_user
from .factory import build_agent

logger = logging.getLogger(__name__)

# 前端 Runtime 里配置的 AGENT_URL 要指向这个路径。
AGENT_PATH = "/agent"


async def resolve_authorization_scope(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    conn: DbConn,
) -> str:
    """校验登录凭证，返回本次请求的用户作用域。

    和 /todos 用的是同一个 authenticate_token，避免两套鉴权规则慢慢跑偏。
    """
    if credentials is None or not credentials.credentials:
        raise unauthorized("未登录或缺少登录凭证")

    user = authenticate_token(credentials.credentials, conn)

    # 工具从这里读身份；不接受任何来自请求体或模型参数的用户标识。
    set_agent_user(user)

    return f"user:{user['id']}"


def register_agent_endpoint(app: FastAPI) -> None:
    agent = build_agent()

    add_agent_framework_fastapi_endpoint(
        app,
        agent,
        AGENT_PATH,
        authorization_scope_resolver=resolve_authorization_scope,
        tags=["Agent"],
    )

    logger.info("已注册 AG-UI 入口: POST %s (agent=%s)", AGENT_PATH, agent.name)
