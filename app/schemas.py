"""请求与响应模型：字段缺失、空值、超长都在这一层被拦下。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64, description="登录账号")
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


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
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
