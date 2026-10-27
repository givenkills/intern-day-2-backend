"""应用配置：全部通过环境变量覆盖，默认值只服务于本地练习。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Intern Day 2 Todo API"
    app_version: str = "1.0.0"

    # 数据文件位置。容器中通过 volume 挂载到 /data，避免容器重建后数据丢失。
    database_path: str = "data/app.db"
    # 登录凭证有效期（分钟）。改小可以快速验证过期逻辑，例如 1。
    session_ttl_minutes: int = 120
    # 允许跨域的前端来源，多个用英文逗号分隔。
    cors_origins: str = "http://localhost:5173"

    # 启动时若无任何用户则自动创建练习账号，方便别人克隆后直接登录。
    seed_on_startup: bool = True
    seed_username: str = "intern"
    seed_password: str = "intern123456"
    seed_display_name: str = "练习同学"
    seed_sample_todos: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
