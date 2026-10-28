"""公共依赖：数据库连接与登录凭证校验。

所有需要登录的接口都通过 `CurrentUser` 拿到当前用户，
鉴权发生在后端，前端隐藏入口只是体验优化，不能作为安全边界。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import connection
from .security import to_iso, token_fingerprint, utcnow

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="登录接口返回的 access_token，填写时不需要再加 Bearer 前缀",
)


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_db() -> Iterator[sqlite3.Connection]:
    with connection() as conn:
        yield conn


DbConn = Annotated[sqlite3.Connection, Depends(get_db)]


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    conn: DbConn,
) -> dict:
    """校验 Bearer 令牌，返回当前用户。未携带、伪造、过期、已退出都返回 401。"""
    if credentials is None or not credentials.credentials:
        raise unauthorized("未登录或缺少登录凭证")

    fingerprint = token_fingerprint(credentials.credentials)
    row = conn.execute(
        """
        SELECT s.expires_at, u.id, u.username, u.phone, u.display_name, u.created_at
        FROM sessions AS s
        JOIN users   AS u ON u.id = s.user_id
        WHERE s.token_fingerprint = ?
        """,
        (fingerprint,),
    ).fetchone()

    if row is None:
        raise unauthorized("登录凭证无效或已退出")

    if row["expires_at"] <= to_iso(utcnow()):
        conn.execute("DELETE FROM sessions WHERE token_fingerprint = ?", (fingerprint,))
        conn.commit()
        raise unauthorized("登录已过期，请重新登录")

    return {
        "id": row["id"],
        "username": row["username"],
        "phone": row["phone"],
        "display_name": row["display_name"],
        "created_at": row["created_at"],
    }


CurrentUser = Annotated[dict, Depends(get_current_user)]
