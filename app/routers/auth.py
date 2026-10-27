"""登录、当前用户、退出登录。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials

from ..deps import CurrentUser, DbConn, bearer_scheme
from ..schemas import LoginRequest, LoginResponse, UserOut
from ..security import (
    burn_password_check,
    create_session_token,
    session_expiry,
    to_iso,
    token_fingerprint,
    utcnow,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["认证"])

# 账号不存在与密码错误必须给出同一句提示，避免被枚举出哪些账号存在。
_INVALID_CREDENTIALS = "账号或密码不正确"


@router.post("/login", response_model=LoginResponse, summary="账号密码登录")
def login(payload: LoginRequest, conn: DbConn) -> LoginResponse:
    user = conn.execute(
        "SELECT id, password_hash FROM users WHERE username = ?",
        (payload.username,),
    ).fetchone()

    if user is None:
        burn_password_check(payload.password)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )

    now = utcnow()
    expires_at = session_expiry(now)
    token = create_session_token()

    # 顺手清理过期会话，避免会话表无限增长。
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (to_iso(now),))
    conn.execute(
        "INSERT INTO sessions (token_fingerprint, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token_fingerprint(token), user["id"], to_iso(now), to_iso(expires_at)),
    )
    conn.commit()

    return LoginResponse(access_token=token, expires_at=to_iso(expires_at))


@router.get("/me", response_model=UserOut, summary="获取当前登录用户")
def read_current_user(current_user: CurrentUser) -> UserOut:
    return UserOut(**current_user)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="退出登录",
    response_class=Response,
)
def logout(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    current_user: CurrentUser,
    conn: DbConn,
) -> Response:
    """删除服务端会话，退出后再用旧令牌访问受保护接口会返回 401。"""
    assert credentials is not None  # 已通过 get_current_user 校验
    conn.execute(
        "DELETE FROM sessions WHERE token_fingerprint = ? AND user_id = ?",
        (token_fingerprint(credentials.credentials), current_user["id"]),
    )
    conn.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
