"""请求与响应模型：字段缺失、空值、格式错误都在这一层被拦下。"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 中国大陆手机号：1 开头，第二位 3-9，共 11 位
_PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


def normalize_phone(raw: str) -> str:
    """去掉用户可能输入的空格和短横线，再校验格式。"""
    value = raw.strip().replace(" ", "").replace("-", "")
    if not _PHONE_PATTERN.fullmatch(value):
        raise ValueError("请输入有效的 11 位手机号")
    return value


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, description="登录账号或注册手机号")
    password: str = Field(min_length=1, max_length=128, description="登录密码")

    @field_validator("username")
    @classmethod
    def _strip_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("账号不能为空")
        return value


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: str


class SendCodeRequest(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, value: str) -> str:
        return normalize_phone(value)


class SendCodeResponse(BaseModel):
    expires_in: int
    resend_after: int
    # 仅开发模式返回，方便自测；生产环境 SMS_DEBUG_RETURN_CODE=false 时恒为 null
    debug_code: str | None = None
    # 日志里不方便看时，前端用它提示用户"验证码已发送到 138****8000"
    masked_phone: str


class RegisterRequest(BaseModel):
    phone: str
    code: str = Field(min_length=4, max_length=8, description="收到的 6 位数字验证码")
    password: str = Field(min_length=6, max_length=64, description="登录密码，至少 6 位")
    display_name: str = Field(min_length=1, max_length=20, description="显示昵称")

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, value: str) -> str:
        return normalize_phone(value)

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        value = value.strip()
        if not value.isdigit():
            raise ValueError("验证码只能是数字")
        return value

    @field_validator("display_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("昵称不能为空")
        return value


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    phone: str | None = None
    display_name: str
    created_at: str


class TodoCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120, description="待办内容")

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("待办内容不能为空")
        return value


class TodoUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    done: bool | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("待办内容不能为空")
        return value


class TodoOut(BaseModel):
    id: int
    title: str
    done: bool
    created_at: str
    updated_at: str


class HealthOut(BaseModel):
    status: str
    app: str
    version: str
    time: str
