"""密码哈希、会话令牌与时间工具。

设计要点：
1. 密码只保存 Argon2 哈希，接口与日志里都不会出现明文。
2. 登录凭证是随机的不透明字符串，由后端生成；数据库里只存它的
   SHA-256 指纹，即使数据库被读取也无法还原出可用的令牌。
3. 服务端保存会话与过期时间，退出登录直接删除会话，旧凭证立即失效。
4. 手机验证码用 HMAC-SHA256(密钥, 手机号:验证码) 存储，密钥不落库。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from pwdlib import PasswordHash

from .config import get_settings

_password_hash = PasswordHash.recommended()

# 账号不存在时也跑一次校验，避免通过响应耗时判断账号是否存在。
_DUMMY_HASH = _password_hash.hash("dummy-password-for-timing")


def hash_password(raw_password: str) -> str:
    return _password_hash.hash(raw_password)


def verify_password(raw_password: str, stored_hash: str) -> bool:
    try:
        return _password_hash.verify(raw_password, stored_hash)
    except Exception:  # 哈希格式损坏等情况一律视为校验失败
        return False


def burn_password_check(raw_password: str) -> None:
    """账号不存在时调用，让两条失败分支的耗时接近。"""
    verify_password(raw_password, _DUMMY_HASH)


def create_session_token() -> str:
    """32 字节随机令牌，URL 安全，不含任何用户信息。"""
    return secrets.token_urlsafe(32)


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_verification_code() -> str:
    """6 位数字验证码，用 secrets 保证不可预测。"""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_verification_code(phone: str, code: str) -> str:
    """用服务端密钥做 HMAC，库里不存验证码原文。"""
    key = get_settings().app_secret.encode("utf-8")
    message = f"{phone}:{code}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_iso(moment: datetime) -> str:
    """统一成 UTC 的 ISO 字符串，便于按字符串比较大小。"""
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def session_expiry(now: datetime | None = None) -> datetime:
    base = now or utcnow()
    return base + timedelta(minutes=get_settings().session_ttl_minutes)
