"""ARQ Worker 入口。

启动命令：`arq app.worker.WorkerSettings`
容器里由 compose 的 worker 服务执行同一条命令，镜像与 api 完全相同，
只是启动命令不同——同一份代码，两个进程。

时区说明：`timezone` 显式设成业务时区（默认 Asia/Shanghai），
不读容器 TZ，也不依赖宿主机设置，`, cron(hour=0, minute=5)` 才会落在本地 00:05。
"""

from __future__ import annotations

import logging
import re

from arq import cron
from arq.connections import RedisSettings
from arq.worker import func

from .activity.timeutil import TZ
from .config import get_settings
from .daily_report.tasks import generate_daily_report, ping, schedule_daily_reports
from .db import init_db

# ARQ 的 CLI 只给 `arq` 这个 logger 装 handler（见 arq/logs.py 的 default_log_config），
# 业务 logger 往 root 传播时没有 handler，INFO 级别的调度/入队/执行日志会全部消失。
# 这里补一个 root handler；已经有 handler 时 basicConfig 什么也不做，不影响 uvicorn 那边。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s: %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger(__name__)

settings = get_settings()


def mask_url(url: str) -> str:
    """日志里不回显 Redis 密码。"""
    return re.sub(r"://([^:@/]*):([^@/]*)@", r"://\1:***@", url)


async def on_startup(ctx) -> None:
    init_db()
    if settings.daily_report_target_date.strip():
        logger.warning(
            "DAILY_REPORT_TARGET_DATE=%s 已生效，调度会固定生成这一天（仅用于演示/补跑）",
            settings.daily_report_target_date,
        )
    logger.info(
        "worker_ready redis=%s timezone=%s cron=%02d:%02d max_jobs=%d max_tries=%d job_timeout=%ss",
        mask_url(settings.redis_url),
        settings.daily_report_timezone,
        settings.daily_report_cron_hour,
        settings.daily_report_cron_minute,
        settings.daily_report_max_jobs,
        settings.daily_report_max_tries,
        settings.daily_report_job_timeout,
    )


async def on_shutdown(ctx) -> None:
    logger.info("worker_stopped")


class WorkerSettings:
    """ARQ 从这个类的属性读配置。"""

    functions = [
        func(
            generate_daily_report,
            max_tries=settings.daily_report_max_tries,
            timeout=settings.daily_report_job_timeout,
        ),
        # 连通性自检：不重试、很快超时
        func(ping, max_tries=1, timeout=30),
    ]

    cron_jobs = [
        cron(
            schedule_daily_reports,
            hour=settings.daily_report_cron_hour,
            minute=settings.daily_report_cron_minute,
            # 多个 Worker 实例时，同一时刻只跑一次调度
            unique=True,
            run_at_startup=False,
            timeout=120,
            max_tries=1,
        ),
    ]

    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    timezone = TZ

    # SQLite 单写者，并发拉太高只会让任务排队等锁
    max_jobs = settings.daily_report_max_jobs
    job_timeout = settings.daily_report_job_timeout
    # 任务结果留一天：够接口回查 job 状态，也够同 id 在这一天内去重
    keep_result = 86400
    retry_jobs = True
    on_startup = on_startup
    on_shutdown = on_shutdown
