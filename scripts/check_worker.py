"""验证 Worker 真的连着 Redis、并且能执行任务。

用法：
    python scripts/check_worker.py                  # 投递 ping，等结果
    python scripts/check_worker.py --enqueue 7 2026-11-01   # 直接投递一条日报任务

退出码 0 表示任务被 Worker 执行完成；非 0 会打印卡在哪一步
（连不上 Redis / 入队失败 / 等不到结果 —— 通常是 Worker 没起来）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arq import create_pool  # noqa: E402
from arq.connections import RedisSettings  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.daily_report.queue import job_id_for  # noqa: E402


async def run(args) -> int:
    settings = get_settings()

    try:
        redis = await asyncio.wait_for(
            create_pool(RedisSettings.from_dsn(settings.redis_url)), timeout=5
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[失败] 连不上 Redis（{settings.redis_url}）：{exc}")
        print("       容器内应使用服务名 redis；本机直跑请改成 localhost")
        return 2

    print(f"[通过] 已连接 Redis：{settings.redis_url}")

    if args.enqueue:
        user_id, report_date = args.enqueue
        from datetime import date

        day = date.fromisoformat(report_date)
        job = await redis.enqueue_job(
            "generate_daily_report", user_id, day.isoformat(), _job_id=job_id_for(user_id, day)
        )
        target = f"{job.job_id if job else job_id_for(user_id, day)}（日报 {report_date}）"
        if job is None:
            print("[提示] 同 id 任务已在队列中，直接等结果")
        result = None
        try:
            from arq.jobs import Job

            result = await Job(job_id_for(user_id, day), redis).result(timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"[失败] 等不到结果（{target}）：{exc}")
            print("       Worker 没起来、或任务执行超过 timeout")
            return 3
        print(f"[通过] 任务已执行：{result}")
        await redis.aclose()
        return 0

    job = await redis.enqueue_job("ping", _job_id=f"ping:{asyncio.get_running_loop().time():.0f}")
    if job is None:
        print("[失败] 入队返回 None，任务未投递")
        await redis.aclose()
        return 3

    try:
        result = await job.result(timeout=args.timeout)
    except Exception as exc:  # noqa: BLE001
        print(f"[失败] 已入队但等不到结果：{exc}")
        print("       说明 Redis 通了，但 Worker 没在跑（docker compose ps 看一下 worker）")
        await redis.aclose()
        return 3

    print(f"[通过] Worker 已执行测试任务：{result}")
    await redis.aclose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Worker 连通性自检")
    parser.add_argument("--timeout", type=float, default=20.0, help="等待任务结果的秒数")
    parser.add_argument(
        "--enqueue",
        nargs=2,
        metavar=("USER_ID", "DATE"),
        help="不走 ping，直接投递一条日报任务（需要 Worker 在跑）",
    )
    args = parser.parse_args()

    try:
        user_id = int(args.enqueue[0]) if args.enqueue else None
    except ValueError:
        print("[失败] USER_ID 必须是数字")
        return 2
    if args.enqueue:
        args.enqueue = (user_id, args.enqueue[1])

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
