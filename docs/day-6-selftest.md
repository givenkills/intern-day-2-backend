# 第六天自测记录：每日活动总结

> 记录时间：2026-11-02 ｜ 分支：`feat/daily-summary-queue` ｜ 环境：Windows 11 + Docker Desktop 29.9.2

## 一、验证环境说明

| 项 | 值 |
| --- | --- |
| 容器 | `intern-day-2-backend:drcheck`（本项目镜像，同一镜像跑 api 与 worker） |
| Redis | `redis:7.2-alpine`，容器内 `redis://drcheck-redis:6379/0`，不对外暴露端口 |
| 数据库 | 命名卷 `drcheck-data` → `/data/app.db`，api 与 worker 共用 |
| 模型 | **本地假模型服务**（OpenAI 兼容的 `/v1/chat/completions`），从 prompt 里读出统计数字并返回合规 JSON |

关于模型：本机没有可用的千问密钥，所以用本地假服务替代。**除了「模型」这一步是假的，`agent_framework → HTTP → 解析 → 5 道校验 → 落库` 全部是真实代码路径**，请求在 worker 日志里可见（`POST .../chat/completions "HTTP/1.1 200 OK"`）。真实模型仍需在配好密钥的环境里补测。

## 二、自动化测试

```bash
$ uv run pytest
123 passed in 10.39s

$ uv run ruff check app tests
All checks passed!
```

新增 45 个用例：统计口径 9、幂等并发 9、接口 16、Agent 校验 11。

## 三、容器内实测（八个场景）

### 1. 自动执行 —— 不点按钮，cron 自己跑完（已验证）

`docker logs drcheck-worker` 原样摘录（时间戳为本地时间）：

```text
15:08:00: cron:schedule_daily_reports()
15:08:00: job_enqueued job_id=daily-report:1:2026-11-01 user=1 date=2026-11-01
15:08:00: schedule_fired date=2026-11-01 users=1 enqueued=1 duplicates=0 failed=0
15:08:01: daily-report:1:2026-11-01:generate_daily_report(1, '2026-11-01')
15:08:01: job_started user=1 date=2026-11-01 attempt=1 planned=5 completed=3 skipped=1 uncompleted=1 postponed_ops=1
15:08:01: HTTP Request: POST http://<模型服务地址>:8899/v1/chat/completions "HTTP/1.1 200 OK"
15:08:01: job_succeeded user=1 date=2026-11-01 attempt=1 review_len=30
```

启动那一行同时证明时区配置生效：`worker_ready ... timezone=Asia/Shanghai cron=15:08`（演示时把 hour 和 minute 都改成了当前时间）。

### 2. 统计正确（已验证）

`GET /reports/2026-11-01`：

```json
{"report_date":"2026-11-01","status":"success",
 "stats":{"planned":5,"completed":3,"skipped":1,"uncompleted":1,"postponed_ops":1},
 "review":"当天 5 项安排完成 3 项，跳过 1 项，未完成 1 项。",
 "generated_at":"2026-11-02T15:08:01+08:00"}
```

数据由 `scripts/seed_demo_data.py` 造：2 条完成、1 条跳过、1 条未完成、1 条「推迟后完成」。推迟链上的原记录是 `postponed`，不计入计划数，所以是 5 不是 6。

### 3. 没有记录时如实说明（已验证）

```json
{"report_date":"2026-10-30","status":"success","stats":{"planned":0},
 "review":"当天没有活动记录，无法判断实际执行情况，也未记录久坐时长。",
 "sedentary":{"recorded":false,"minutes":null}}
```

不是「久坐 0 分钟」，也不是「完成 0 项，继续保持」。

### 4. 重复执行只有一份（已验证）

同一日期再点一次生成：

```json
{"report_date":"2026-11-01","status":"already_generated","message":"该日总结已生成，直接复用已有结果"}
```

库里每个日期只有一行：

```text
[('2026-10-28','success',4,1), ('2026-10-30','success',1,1), ('2026-11-01','success',1,1)]
```

### 5. 失败与有限重试，修复后能补跑（已验证）

把 worker 的 `QWEN_BASE_URL` 指向不可达端口：

```text
15:09:32: job_started ... attempt=1
15:09:39: job_retry ... attempt=1 code=AGENT_UPSTREAM_ERROR defer=30s
15:10:09: job_started ... attempt=2
15:10:17: job_retry ... attempt=2 code=AGENT_UPSTREAM_ERROR defer=60s
15:11:17: job_started ... attempt=3
15:11:24: job_failed_final ... attempt=3 code=AGENT_UPSTREAM_ERROR
```

接口只回一句人话，不带堆栈：

```json
{"status":"failed","attempt":3,"error":{"code":"AGENT_UPSTREAM_ERROR","message":"模型服务暂时不可用，总结未生成"}}
```

换回可用模型后补跑：

```json
{"status":"queued","job_id":"daily-report:1:2026-10-28:r4"}
```

10 秒后 `status=success, attempt=4`。

### 6. Worker 停机期间任务不丢（已验证）

```bash
docker stop drcheck-worker
# POST /reports/2026-10-30/generate → 202，库里 pending / attempt=0
# Redis 里能看到排队中的任务：arq:job:daily-report:1:2026-10-30
docker start drcheck-worker
# 10 秒后 status=success，日志 job_started → job_succeeded
```

### 7. 持久化与跨用户隔离（已验证）

- `docker restart drcheck-api` 后重新登录，`GET /reports/2026-11-01` 仍返回同一份 `success` 与相同统计。
- 第二个账号（走真实注册流程）读同一天：`status=not_generated`，`review=null`。

### 8. Redis 不可用时接口不撒谎（已验证）

```bash
docker stop drcheck-redis
$ curl -X POST .../reports/2026-10-29/generate
HTTP 503 {"detail":"任务队列暂不可用，总结未提交，请稍后重试"}
$ curl .../reports/2026-10-29        # 不能被留成"生成中"
status = not_generated
```

### 9. 连通性自检

```bash
$ docker exec drcheck-api python scripts/check_worker.py
[通过] 已连接 Redis：redis://drcheck-redis:6379/0
[通过] Worker 已执行测试任务：{'pong': True, 'at': '2026-11-02T07:04:04+00:00'}
```

## 四、真跑起来才发现的四个问题（都已修）

| # | 现象 | 原因 | 修法 |
| --- | --- | --- | --- |
| 1 | worker 日志里只有 ARQ 自己的行，`schedule_fired` / `job_succeeded` 一条都没有 | ARQ 的 `default_log_config` 只给 `arq` 这个 logger 装 handler，业务 logger 往 root 传播时没有 handler，INFO 全被丢掉 | `app/worker.py` 补一次 `logging.basicConfig`（已有 handler 时是空操作，不影响 uvicorn） |
| 2 | `docker compose exec backend python scripts/seed_demo_data.py` 报文件不存在 | Dockerfile 只 `COPY app`，`scripts/` 没进镜像 | Dockerfile 增加 `COPY scripts ./scripts` |
| 3 | 失败的日报补跑不了，接口回「该日任务已在队列中」 | ARQ 的 job key 保留 `keep_result`（24h），同 `job_id` 再入队返回 `None`，被误判成"排队中" | 补跑时换一代 `job_id`（`:r2`、`:r3`…），首投仍用稳定 id 去重 |
| 4 | 按文档把 cron 改成「两分钟后」不触发 | `DAILY_REPORT_CRON_MINUTE` 只改分钟，hour 仍是 0，下一次得等到凌晨 | README 改成同时设置 `DAILY_REPORT_CRON_HOUR` |

第 3 条最值得记：它不报错，只让用户以为「已经在排队了」，然后永远等不到结果。

## 五、后续补上的两项（2026-11-02 17:10）

| # | 场景 | 结果 |
| --- | --- | --- |
| 1 | 真实千问模型生成 | 已通过。`qwen3.8-flash`，单份日报耗时 33 秒，回顾与统计数字一致 |
| 2 | 页面截图 | 已补。前端仓库 `docs/screenshots/day6-daily-report.png`（手机端；桌面端与它同一份代码，仅布局断点不同） |

## 六、仍未验证

| # | 场景 | 原因 | 怎么补 |
| --- | --- | --- | --- |
| 1 | 多 Worker 实例下 cron 只触发一次 | 只有一台机器，没起第二个 worker | `docker compose up -d --scale worker=2` 后看 `schedule_fired` 是否只出现一次 |
| 2 | 处理中断（`running` 时进程被杀）的实机注入 | 任务通常秒级完成，没有稳定窗口注入；同一套 CAS 抢占逻辑已由并发单测覆盖 | 用慢模型或 `docker pause` 制造窗口后复测 |

## 七、复现命令

```bash
docker compose up -d --build
docker compose ps

# 造昨天的数据
docker compose exec backend python scripts/seed_demo_data.py --date $(date -d yesterday +%F)

# Worker 连通性（不碰模型）
docker compose exec backend python scripts/check_worker.py

# 演示自动触发：hour 和 minute 都要改
NEXT_H=$(date -d '+2 min' +%-H); NEXT_M=$(date -d '+2 min' +%-M)
DAILY_REPORT_CRON_HOUR=$NEXT_H DAILY_REPORT_CRON_MINUTE=$NEXT_M docker compose up -d worker
docker compose logs -f worker
docker compose up -d worker     # 恢复 00:05

# 自动化
uv run pytest
uv run ruff check app tests
```

> 本机验证用的是 `docker run` 起的等价三容器（api / worker / redis），因为已有容器占着 8000 端口；
> 拓扑与环境变量与 `compose.yaml` 一致。`docker compose config` 语法已校验。
