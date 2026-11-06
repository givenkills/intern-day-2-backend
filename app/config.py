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

    app_name: str = "久坐活动助手 API"
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

    # ---- 手机验证码（练习用模拟通道，不接真实短信）----
    # 验证码的 HMAC 密钥，正式环境必须换成随机值并从环境变量注入。
    app_secret: str = "dev-only-please-change-me"
    # 开发模式下把验证码一起返回给前端，方便自测和演示；上线必须设成 false。
    sms_debug_return_code: bool = True
    # 验证码有效期（秒）
    sms_code_ttl_seconds: int = 300
    # 同一手机号两次发送的最小间隔（秒）
    sms_resend_seconds: int = 60
    # 单个验证码最多允许输错的次数
    sms_max_attempts: int = 5
    # 同一手机号 24 小时内最多发送次数
    sms_daily_limit: int = 10

    # ---- 模型服务：千问（DashScope 的 OpenAI 兼容模式）----
    # 密钥只从后端环境变量读取，不会出现在代码、镜像构建参数或前端环境变量里。
    qwen_api_key: str = ""
    # DashScope 兼容模式地址；换成其他 OpenAI 兼容服务时只改这里。
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-plus"

    # ---- 每日总结：Redis 队列 + ARQ 后台任务 ----
    # 队列地址。容器内必须用 Compose 服务名 redis，不能用 localhost（那是容器自己）。
    redis_url: str = "redis://redis:6379/0"
    # 日报归属的自然日时区。显式配置，不读容器 TZ，也不依赖宿主机时区。
    daily_report_timezone: str = "Asia/Shanghai"
    # 每天几点几分生成「前一天」的总结。ARQ cron 用这个时间 + 上面的时区。
    daily_report_cron_hour: int = 0
    daily_report_cron_minute: int = 5
    # 单个任务的模型调用失败重试上限（不含首次执行）
    daily_report_max_tries: int = 3
    # 单次任务执行超时（秒）：要覆盖「模型超时 45s + 退避 + 写库」的最坏情况
    daily_report_job_timeout: int = 180
    # 单次模型调用超时（秒），超时按可重试错误处理
    daily_report_agent_timeout: int = 45
    # 任务租约（秒）：抢到 running 的 Worker 崩溃后，多久允许别人接管
    daily_report_lease_seconds: int = 600
    # Worker 同时执行的任务数。SQLite 单写者，开太大只会排队等锁
    daily_report_max_jobs: int = 4
    # 仅用于演示与补跑：强制指定目标日期（YYYY-MM-DD），留空表示「昨天」
    daily_report_target_date: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def agent_ready(self) -> bool:
        """没有配置密钥时不注册 Agent 接口，避免带着空密钥去调用模型服务。"""
        return bool(self.qwen_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
