"""Agent 唯一的工具：查询「当前登录用户」的待办清单（只读）。

几个刻意为之的设计：
1. 函数签名里没有 user_id。用户范围由后端根据登录凭证决定，模型无法越权。
2. 写成 async def。同步函数会被调度到线程池执行，ContextVar 里的身份就丢了。
3. 只读：没有任何新增、修改、删除的入口，也不碰外部系统。
"""

from __future__ import annotations

import json
from typing import Annotated

from agent_framework import tool

from ..config import get_settings
from ..db import connection
from .context import get_agent_user

_STATUS_FILTERS = {
    "all": "",
    "open": "AND done = 0",
    "done": "AND done = 1",
}


@tool(
    name="list_my_todos",
    description=(
        "查询当前登录用户自己的待办清单，只读。"
        "当用户问到自己有哪些待办、还剩几件没做完、最近加了什么时调用。"
        "返回 JSON，包含每条待办的内容、完成状态和创建时间，以及汇总数量。"
    ),
)
async def list_my_todos(
    status: Annotated[str, "筛选：all 全部 / open 未完成 / done 已完成，默认 all"] = "all",
    limit: Annotated[int, "最多返回多少条，1 到 20，默认 10"] = 10,
) -> str:
    # 身份来自本次请求的登录校验，不接受模型或浏览器传入的用户标识。
    user = get_agent_user()

    normalized = status if status in _STATUS_FILTERS else "all"
    safe_limit = max(1, min(int(limit or 10), get_settings().agent_tool_todo_limit))

    with connection() as conn:
        rows = conn.execute(
            f"""
            SELECT title, done, created_at
            FROM todos
            WHERE user_id = ? {_STATUS_FILTERS[normalized]}
            ORDER BY done ASC, id DESC
            LIMIT ?
            """,
            (user["id"], safe_limit),
        ).fetchall()

        summary = conn.execute(
            """
            SELECT
                COUNT(*)                                   AS total,
                COALESCE(SUM(CASE WHEN done = 0 THEN 1 ELSE 0 END), 0) AS open
            FROM todos
            WHERE user_id = ?
            """,
            (user["id"],),
        ).fetchone()

    payload = {
        "owner": user["display_name"],
        "filter": normalized,
        "total": summary["total"],
        "open": summary["open"],
        "returned": len(rows),
        "items": [
            {
                "title": row["title"],
                "done": bool(row["done"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ],
    }
    return json.dumps(payload, ensure_ascii=False)
