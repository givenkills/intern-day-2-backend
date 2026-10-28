"""手机验证码：生成、限流、校验，以及短信通道抽象。

本项目**不接真实短信服务**：默认使用 `ConsoleSmsSender`，把验证码写到后端日志，
并在开发模式下通过接口返回 `debug_code` 方便自测与演示。
接入真实通道时，实现 `SmsSender` 协议并替换 `_sender` 即可，其余逻辑不用改。
"""

from __future__ import annotations

import hmac
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Protocol

from fastapi import HTTPException, status

from .config import get_settings
from .security import generate_verification_code, hash_verification_code, to_iso, utcnow

logger = logging.getLogger("app.sms")


class SmsSender(Protocol):
    """短信通道接口：真实实现里在这里调用云厂商 SDK。"""

    def send(self, phone: str, code: str, purpose: str) -> None: ...


def mask_phone(phone: str) -> str:
    """日志里不打完整手机号。"""
    return f"{phone[:3]}****{phone[-4:]}" if len(phone) == 11 else "***"


class ConsoleSmsSender:
    """练习用通道：不真正发送，只写一条日志。"""

    def send(self, phone: str, code: str, purpose: str) -> None:
        settings = get_settings()
        logger.warning(
            "[SMS][%s] 手机号 %s 验证码 %s，%s 秒内有效",
            purpose,
            mask_phone(phone),
            code,
            settings.sms_code_ttl_seconds,
        )


_sender: SmsSender = ConsoleSmsSender()


def send_sms_code(phone: str, code: str, purpose: str) -> None:
    _sender.send(phone, code, purpose)


def issue_code(conn: sqlite3.Connection, phone: str, purpose: str) -> tuple[int, int, str | None]:
    """生成并发送验证码。

    返回 (有效期秒数, 可重发间隔秒数, 是否把验证码返回给前端)。
    """
    settings = get_settings()
    now = utcnow()

    # 同一手机号同一用途的最小发送间隔
    last = conn.execute(
        """SELECT created_at FROM verification_codes
           WHERE phone = ? AND purpose = ?
           ORDER BY id DESC LIMIT 1""",
        (phone, purpose),
    ).fetchone()
    if last is not None:
        elapsed = (now - datetime.fromisoformat(last["created_at"])).total_seconds()
        if elapsed < settings.sms_resend_seconds:
            wait_seconds = int(settings.sms_resend_seconds - elapsed) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"发送过于频繁，请 {wait_seconds} 秒后重试",
            )

    # 24 小时总量上限
    day_ago = to_iso(now - timedelta(days=1))
    recent = conn.execute(
        "SELECT COUNT(*) AS total FROM verification_codes WHERE phone = ? AND created_at >= ?",
        (phone, day_ago),
    ).fetchone()["total"]
    if recent >= settings.sms_daily_limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="该手机号今日获取验证码次数过多，请明天再试",
        )

    code = generate_verification_code()
    expires_at = now + timedelta(seconds=settings.sms_code_ttl_seconds)

    conn.execute(
        """INSERT INTO verification_codes (phone, code_hash, purpose, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (phone, hash_verification_code(phone, code), purpose, to_iso(now), to_iso(expires_at)),
    )
    conn.commit()

    send_sms_code(phone, code, purpose)

    debug_code = code if settings.sms_debug_return_code else None
    return settings.sms_code_ttl_seconds, settings.sms_resend_seconds, debug_code


def consume_code(conn: sqlite3.Connection, phone: str, purpose: str, code: str) -> None:
    """校验验证码并标记为已使用。失败时按原因抛出 400 / 429。"""
    settings = get_settings()
    now = utcnow()

    row = conn.execute(
        """SELECT id, code_hash, expires_at, attempts
           FROM verification_codes
           WHERE phone = ? AND purpose = ? AND consumed_at IS NULL
           ORDER BY id DESC LIMIT 1""",
        (phone, purpose),
    ).fetchone()

    if row is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先获取验证码")

    if row["attempts"] >= settings.sms_max_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="验证码错误次数过多，请重新获取",
        )

    if row["expires_at"] <= to_iso(now):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证码已过期，请重新获取")

    if not hmac.compare_digest(row["code_hash"], hash_verification_code(phone, code)):
        conn.execute(
            "UPDATE verification_codes SET attempts = attempts + 1 WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
        remaining = settings.sms_max_attempts - row["attempts"] - 1
        detail = (
            f"验证码不正确，还可尝试 {remaining} 次" if remaining > 0 else "验证码错误次数过多，请重新获取"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    # 一次性消费：成功后立即作废，防止同一验证码重复注册
    conn.execute(
        "UPDATE verification_codes SET consumed_at = ? WHERE id = ?",
        (to_iso(now), row["id"]),
    )
    conn.commit()
