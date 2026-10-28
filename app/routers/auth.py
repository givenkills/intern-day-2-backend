"""注册、登录、当前用户、退出登录。"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials

from ..deps import CurrentUser, DbConn, bearer_scheme
from ..schemas import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    SendCodeRequest,
    SendCodeResponse,
    UserOut,
)
from ..security import (
    burn_password_check,
    create_session_token,
    hash_password,
    session_expiry,
    to_iso,
    token_fingerprint,
    utcnow,
    verify_password,
)
from ..sms import consume_code, issue_code, mask_phone

router = APIRouter(prefix="/auth", tags=["认证"])

# 账号不存在与密码错误必须给出同一句提示，避免被枚举出哪些账号存在。
_INVALID_CREDENTIALS = "账号或密码不正确"
_PHONE_TAKEN = "该手机号已注册，请直接登录"


def _start_session(conn: sqlite3.Connection, user_id: int) -> LoginResponse:
    """登录与注册成功后统一在这里签发会话。"""
    now = utcnow()
    expires_at = session_expiry(now)
    token = create_session_token()

    # 顺手清理过期会话，避免会话表无限增长。
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (to_iso(now),))
    conn.execute(
        "INSERT INTO sessions (token_fingerprint, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token_fingerprint(token), user_id, to_iso(now), to_iso(expires_at)),
    )
    conn.commit()

    return LoginResponse(access_token=token, expires_at=to_iso(expires_at))


@router.post("/sms/send", response_model=SendCodeResponse, summary="发送手机验证码")
def send_code(payload: SendCodeRequest, conn: DbConn) -> SendCodeResponse:
    """给手机号发送注册验证码。

    练习环境不接真实短信通道，验证码会写到后端日志；
    开发模式下同时通过 debug_code 返回，方便自测和演示。
    """
    expires_in, resend_after, debug_code = issue_code(conn, payload.phone, "register")
    return SendCodeResponse(
        expires_in=expires_in,
        resend_after=resend_after,
        debug_code=debug_code,
        masked_phone=mask_phone(payload.phone),
    )


@router.post(
    "/register",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    summary="手机验证码注册并自动登录",
)
def register(payload: RegisterRequest, conn: DbConn) -> LoginResponse:
    """校验验证码后创建账户，用户名默认使用手机号，注册成功直接签发会话。"""
    existing = conn.execute("SELECT id FROM users WHERE phone = ?", (payload.phone,)).fetchone()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_PHONE_TAKEN)

    consume_code(conn, payload.phone, "register", payload.code)

    try:
        cursor = conn.execute(
            """INSERT INTO users (username, phone, display_name, password_hash, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                payload.phone,
                payload.phone,
                payload.display_name,
                hash_password(payload.password),
                to_iso(utcnow()),
            ),
        )
    except sqlite3.IntegrityError:
        # 并发注册同一个手机号时由唯一索引兜底
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_PHONE_TAKEN) from None

    conn.commit()
    return _start_session(conn, cursor.lastrowid)


@router.post("/login", response_model=LoginResponse, summary="账号或手机号登录")
def login(payload: LoginRequest, conn: DbConn) -> LoginResponse:
    user = conn.execute(
        "SELECT id, password_hash FROM users WHERE username = ? OR phone = ?",
        (payload.username, payload.username),
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

    return _start_session(conn, user["id"])


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
