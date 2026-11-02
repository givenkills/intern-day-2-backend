# 第六天自测记录：每日活动总结

> 记录时间：2026-11-02 ｜ 分支：`feat/daily-summary-queue` ｜ 环境：Windows 11 + Python 3.13 + uv 0.13.6

## 一、先说清楚验证边界

本机 **Docker Hub 不可达**（`dial tcp registry-1.docker.io:443 ... no HTTPS proxy`），拉不到 `redis:7.2-alpine`，因此**与 Redis 队列相关的容器场景没有在本机跑过**。为了让业务链路仍然被真实验证，用了两个替代手段：

| 手段 | 做法 | 覆盖了什么 |
| --- | --- | --- |
| 进程内调用任务函数 | 直接 `await generate_daily_report({"job_try": 1}, uid, date)` | 除「Redis 入队/领取」一跳之外的全部业务链路，都是真代码 |
| 本地假模型服务 | 实现一个 OpenAI 兼容的 `/v1/chat/completions`，按收到的统计回一段合规 JSON | `agent_framework → HTTP → 解析 → 5 道校验 → 落库` 全走真实代码路径，只有「模型」是假的 |

**下面第 3 节标注「未验证」的项，提交时一律按未验证算，不写成已通过。**

## 二、自动化测试

```bash
$ uv run pytest
121 passed in 9.94s

$ uv run ruff check app tests
All checks passed!
```

新增 43 个用例，按关注点分四组：

| 文件 | 用例数 | 关注点 |
| --- | --- | --- |
| `tests/test_daily_report_stats.py` | 9 | 日期边界（左闭右开）、跨月跨年、推迟链只算一次、推迟次数按操作日、恒等式、零记录 |
| `tests/test_daily_report_idempotency.py` | 9 | 唯一索引、抢占只能成功一次、成功不重算、失败可重试、租约过期可接管、4 线程并发抢占 |
| `tests/test_daily_report_api.py` | 14 | 登录、日期校验、五种状态、跨用户隔离、队列不可用 503、失败不泄露堆栈 |
| `tests/test_daily_report_agent_guard.py` | 11 | 编造数字、无记录却说久坐、医疗表述、JSON 结构、长度、代码块包裹 |

## 三、手工验证

### 1. 统计口径正确（已验证）

```bash
$ python scripts/seed_demo_data.py --date 2026-11-01
已为 intern 造好 2026-11-01 的演示数据：planned=5 completed=3 skipped=1 uncompleted=1 postponed_ops=1

$ python -c "...build_stats(conn, uid, date(2026,11,1))..."
口径统计: 5 3 1 1 1 | has_records = True
恒等式: True
```

数据构成为 2 条完成、1 条跳过、1 条未完成、1 条「推迟后完成」。推迟链里的原记录是 `postponed`，不计入计划数，所以是 5 而不是 6；推迟次数按操作发生的自然日统计，所以是 1。

### 2. Agent 生成 + 校验 + 落库（已验证，模型为本地假服务）

```text
job_started user=1 date=2026-11-01 attempt=1 planned=5 completed=3 skipped=1 uncompleted=1 postponed_ops=1
job_succeeded user=1 date=2026-11-01 attempt=1 review_len=27
任务返回: {'status': 'success', 'report_date': '2026-11-01', 'planned': 5, 'completed': 3, 'postponed_ops': 1}
回顾: 当天 5 项安排完成 3 项，跳过 1 项，未完成 1 项。
建议: 把未完成的 1 项挪到上午可活动时段，冲突更少。
```

### 3. 重复执行不产生第二份（已验证）

```text
第二次执行返回: {'status': 'skipped', 'reason': 'already_success'}
库中行数 = 1，review 未被覆盖: True
```

### 4. 没有记录时如实说明（已验证）

```text
任务返回: {'status': 'success', 'report_date': '2026-10-31', 'planned': 0, ...}
回顾: 当天没有活动记录，无法判断实际执行情况，也未记录久坐时长。
sedentary_minutes = None（不是 0）
```

### 5. 模型不可用时的失败与重试（已验证）

```text
$ QWEN_BASE_URL=http://127.0.0.1:9/v1   # 不可达端口
返回值: {'status': 'failed', 'code': 'AGENT_UPSTREAM_ERROR', 'transient': True}
库中状态: failed | error_code = AGENT_UPSTREAM_ERROR
任务层: 抛出了 Retry（<Retry defer 30.00s>），符合预期
```

失败只落 `error_code`，接口返回的是「模型服务暂时不可用，总结未生成」，不含堆栈（见第 6 节）。

### 6. HTTP 接口行为（已验证，真实 uvicorn + curl）

```bash
$ curl -s .../reports/2026-11-01 -H "Authorization: Bearer $TOKEN"
{"report_date":"2026-11-01","status":"success",
 "stats":{"planned":5,"completed":3,"skipped":1,"uncompleted":1,"postponed_ops":1},
 "review":"当天 5 项安排完成 3 项，跳过 1 项，未完成 1 项。",
 "sedentary":{"recorded":false,"minutes":null},
 "generated_at":"2026-11-02T11:35:48+08:00","attempt":1,"error":null}

$ curl -s .../reports/2026-10-30 -H "Authorization: Bearer $TOKEN"
{"report_date":"2026-10-30","status":"not_generated","stats":null,...}

$ curl -X POST .../reports/2026-10-30/generate -H "Authorization: Bearer $TOKEN"   # Redis 未启动
HTTP 503
{"detail":"任务队列暂不可用，总结未提交，请稍后重试"}

$ curl -s .../reports/2026-10-30 ...    # 触发失败后再查，不能变成"生成中"
not_generated

$ curl -s .../reports/2026-11-02 ...    # 当天还没过完
HTTP 422

$ curl -s .../reports/2026-11-01         # 未带令牌
HTTP 401
```

### 7. 并发抢占（已验证）

4 个线程同时对同一 `(user_id, report_date)` 调 `claim`：1 个成功、3 个失败、0 个异常，库里仍只有 1 行。

> 这一条顺带修掉一个真问题：Windows 上多个线程**同时新建** SQLite 连接去打开 WAL 库，会偶发 `attempt to write a readonly database`。现在在建连接处加了一把锁（`app/db.py` 的 `_CONNECT_LOCK`），并把这个场景固定成用例。

## 四、未验证项（不要当成已通过）

| # | 场景 | 为什么没做 | 怎么补 |
| --- | --- | --- | --- |
| 1 | 容器内 `worker` 连上 Redis 并执行 `ping` | 本机拉不到 redis 镜像 | `docker compose up -d` 后跑 `scripts/check_worker.py` |
| 2 | cron 到点自动触发（00:05，改分钟演示） | 同上 | 改 `DAILY_REPORT_CRON_MINUTE` 后看 `docker compose logs -f worker` |
| 3 | Worker 停止 → 投递 → 启动 → 任务被处理 | 同上 | `docker compose stop worker` / `start` |
| 4 | 处理中断后的重复执行保护（真实 kill 容器） | 同上 | 任务执行中 `docker compose restart worker`，观察库里仍只有一行 |
| 5 | 真实模型（千问）生成 | 本机没有可用的 `QWEN_API_KEY` | 配好密钥后 `docker compose up -d`，看页面 |
| 6 | 页面截图 | 截图工具在本机整窗 > 10 MiB（与上周同一问题），且无真实模型 | 配好密钥后浏览器实机截「成功 / 生成中 / 未生成 / 失败」四态 |
| 7 | 重启 API 后日报仍可查 | 需要在容器里重启 | `docker compose restart backend` 后刷新页面 |

## 五、复现命令

```bash
# 自动化
uv run pytest
uv run ruff check app tests

# 只有 Python、没有 Redis 时也能跑的业务链路验证
uv run python scripts/seed_demo_data.py --date 2026-11-01
uv run python -c "
import asyncio
from app.daily_report.tasks import generate_daily_report
print(asyncio.run(generate_daily_report({'job_try': 1}, 1, '2026-11-01')))
"

# 有容器、有 Redis 时的完整验证
docker compose up --build -d
docker compose exec backend python scripts/check_worker.py
docker compose logs -f worker
```
