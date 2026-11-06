# intern-day-2-backend

久坐活动助手后端：**FastAPI + 不透明会话令牌 + SQLite**，提供手机号注册、登录鉴权与久坐活动安排接口，多个账户的数据彼此隔离。

- 前端仓库（配套）：`https://github.com/givenkills/intern-day-2-frontend`
- 接口文档（启动后）：<http://localhost:8000/docs>
- 配套 PR：后端 [#2](https://github.com/givenkills/intern-day-2-backend/pull/2) · 前端 [#2](https://github.com/givenkills/intern-day-2-frontend/pull/2)（均已合并）
- 当前分支：`main`（第二天与第三天的改动都已合并，分阶段 commit 完整保留）

---

## 第十天变更：移除待办清单

产品聚焦久坐活动助手，待办清单已从前后端删除：

- 删除 `/todos` 路由、Todo 模型、`todos` 表建表与索引、示例待办和 `test_todos.py`。
- Agent 移除 `list_my_todos`，只保留三个只读活动工具：`get_my_activity_settings`、`list_my_activities`、`preview_schedule`；内部 Agent 名仍为 `todo_assistant`，避免 Runtime/前端联调改动。
- 旧数据库里的 `todos` 表不会自动删除（避免误删数据），代码已不再读写；需要清理时可手动 `DROP TABLE todos`。
- 当前测试：`uv run pytest` → 149 passed；`uv run ruff check .` 通过。
- 下面第二、三、四天的章节保留为历史记录，其中的待办描述不再代表当前版本。

---

## 第五天新增：久坐活动助手

在登录、待办、Agent 的基础上，新增「久坐活动助手」：设置工作/可活动/免打扰时段，生成当天活动安排，对每条活动执行完成/推迟/跳过，查看周统计；Agent 参与安排建议（工具算槽 + 用户确认保存）。

| 项 | 值 |
| --- | --- |
| 功能分支 | `feat/day-5-activity-assistant` |
| 数据表 | `activity_settings` / `quiet_periods` / `activities` / `activity_events` |
| 接口 | `/activity/*`，共 10 个，详见 [`docs/api-day-5.md`](docs/api-day-5.md) |
| Agent | 3 个只读工具（settings / activities / preview_schedule）+ `suggest` / `confirm` 接口，详见 [`docs/day-5-agent.md`](docs/day-5-agent.md) |
| 测试 | `uv run pytest` → 74 passed（原 33 + 活动 41） |

核心设计：

- **排期算法**：累积位置法，把有效时段（可活动窗口 ∩ 工作时段 − 免打扰 − 已占用）首尾相接成虚拟时间轴，均匀取点，天然跨过午休/会议空洞。
- **推迟建模**：原记录置 `postponed`，派生新 `planned`（`postponed_from_id` 指向原记录），历史可回溯，周统计「推迟次数」才数得准。
- **Agent 不越权**：工具只读、时间由后端算，`confirm` 接口对每个时间逐条二次校验（工作时段/窗口/免打扰），建议落在免打扰时段会被 422 拒。

启动方式不变（见第 6 节）；Agent 相关环境变量沿用 `QWEN_API_KEY` 等，不配密钥时 `/agent` 不注册、活动功能照常可用。

---

## 第六天新增：每日活动总结（Redis + ARQ）

关掉浏览器，服务器也会在每天 **00:05（Asia/Shanghai）** 把前一天的总结做出来：Worker 读库算统计 → 交给 Agent 写两句人话 → 校验 → 存进 `daily_reports`，页面只负责查。

| 项 | 值 |
| --- | --- |
| 功能分支 | `feat/daily-summary-queue`（基于 `feat/day-5-activity-assistant`，因为第五天的活动助手还没合进 `main`） |
| 新增服务 | `worker`（ARQ 进程）与 `redis`（队列），`worker` 与 `backend` **共用同一个镜像** |
| 数据表 | 新增 `daily_reports`；统计复用第五天的 `activities` / `activity_events` |
| 接口 | `GET /reports/{date}`、`POST /reports/{date}/generate`，详见 [`docs/api-day-6.md`](docs/api-day-6.md) |
| 依赖 | `arq==0.28.0`（自动带 `redis==5.3.1`），已固定版本并写入 `uv.lock` |
| 测试 | `uv run pytest` → 121 passed；`ruff check` 零告警 |

### 组件分工（对应任务说明第二节的五个问题）

**① Redis 和业务数据库的分工**

Redis 存「还没发生的事」：待执行的 job、cron 的下次触发时间、任务状态与结果。它快、可以丢、丢了能重建。业务库存「已经发生的事」：谁在哪天完成了什么，有唯一约束、要能查一年、要能审计。所以**日报只住在 SQLite 里**，Redis 里那份只是任务结果缓存，`keep_result` 一过就没了。反过来，把队列塞进业务库会带来轮询和锁竞争，也不合适。

**② ARQ 的四个动作**

- 注册：`WorkerSettings.functions` 里列出可被调用的协程（`func(...)` 可以给单个函数配 `max_tries` / `timeout`）。
- 投递：`await redis.enqueue_job("generate_daily_report", user_id, "2026-11-01", _job_id="daily-report:1:2026-11-01")`。参数会被序列化，所以只传 id 和日期，不传对象。
- 启动：`arq app.worker.WorkerSettings`，CLI 读这个类的属性。
- 定时：`cron_jobs` 里放 `cron(schedule_daily_reports, hour=0, minute=5)`，**时区由 `WorkerSettings.timezone` 决定**。不设就用系统时区——这是最容易踩的坑，容器里系统时区通常是 UTC，00:05 会变成北京时间 08:05。本项目显式传 `Asia/Shanghai`。

**③ API 和 Worker 为什么要分成两个进程**

一是职责不同：Worker 是长驻轮询进程，塞进 API 会和请求抢事件循环。二是故障隔离：Worker 崩了登录照常能用，API 发版重启也不会打断正在跑的总结。三是模型调用很慢（几秒到几十秒），放在 HTTP 请求里会把连接和内存一起拖住。共用镜像只是省一次构建，启动命令是两条：`uvicorn ...` 和 `arq app.worker.WorkerSettings`。

**④ 为什么同一个任务可能被执行两次**

ARQ 是「悲观执行」：任务成功前不会被确认移除。Worker 被 kill、容器重启、`Retry` 触发，任务都会重新入队；`enqueue_job` 网络超时也可能「其实投递成功了」。所以幂等不是加分项，是设计的一部分——本项目的保底手段是数据库唯一索引，见第 4 节。

**⑤ 三种「成功」不要混为一谈**

| 阶段 | 日志关键字 | 失败意味着 |
| --- | --- | --- |
| 调度成功 | `schedule_fired date=... users=N` | cron 没触发，或时区算错 |
| 入队成功 | `job_enqueued job_id=...` | Redis 连不上 / 序列化失败 |
| 执行成功 | `job_succeeded job_id=... attempt=1` | 统计、模型或写库出错 |

### 日报日期与统计口径

**日报日期就是 `activities.plan_date`**，也就是记录所属的自然日（Asia/Shanghai）。本地 `00:00` 对应 UTC 前一天 `16:00`，所以凡是要按时间区间过滤的地方（推迟事件）都换算成 UTC 后左闭右开：`2026-11-01T16:00:00+00:00 <= created_at < 2026-11-02T16:00:00+00:00`。23:59 属于当天，次日 00:00 属于下一天。

**跨日推迟怎么办？** 本项目的推迟**不会跨日**：`postpone_activity` 要求新时间落在当天的工作时段内，`plan_date` 保持不变。所以一条活动永远只属于一个日期，不存在「在两天里各算一次」的问题。如果以后允许推到明天，需要同步改统计口径，这里先写清楚现状。

**推迟不重复计数**：一条活动推迟两次会产生 3 行记录（2 行 `postponed` + 1 行 `planned`）。统计时只把 `postponed` 以外的记录算进计划数，推迟链上的中间记录只贡献「操作次数」。

| 指标 | 定义 |
| --- | --- |
| `planned` | 当天 `status != 'postponed'` 的记录数 |
| `completed` | 其中 `status = 'completed'` |
| `skipped` | 其中 `status = 'skipped'` |
| `uncompleted` | `planned - completed - skipped`（用减法，不另查一次，避免状态枚举漏项时数字对不上） |
| `postponed_ops` | `activity_events` 里 `action='postpone'` 且 `created_at` 落在当天的条数 |

两条硬规则：

1. **恒等式** `planned == completed + skipped + uncompleted`。写库前会再断言一次。
2. **`postponed_ops` 是操作次数，与 `completed` 可以重叠**（一条活动推迟后又完成了，两边各 +1），所以**不能相加**去算总活动数。

示例（`scripts/seed_demo_data.py` 造的就是这份数据）：

| 活动 | 计划 | 操作 | 最终 |
| --- | --- | --- | --- |
| A1 | 09:30 | 完成 | completed |
| A2 | 11:00 | 完成 | completed |
| A3 | 14:00 | 今天不方便 | skipped |
| A4 | 16:00 | 无操作 | planned |
| A5 | 17:00 | 推迟到 17:30 后完成 | 原行 postponed + 新行 completed |

→ `planned=5 · completed=3 · skipped=1 · uncompleted=1 · postponed_ops=1`，`5 = 3+1+1`。

**久坐**：目前没有用户自报久坐时长的入口，`daily_reports.sedentary_minutes` 恒为 `NULL`，表示「未记录」，页面显示「未记录（无法判断实际久坐时长）」。它**不等于 0 分钟**，也不能拿「未完成活动的时间」倒推。

### 调度与执行

```text
00:05 cron 触发
  └─ schedule_daily_reports：目标日期 = 本地昨天（可用 DAILY_REPORT_TARGET_DATE 覆盖）
       └─ 查出「有设置 / 有活动 / 已有日报」的用户并集，逐个 enqueue
            └─ generate_daily_report(user_id, date)
                 ├─ 占位 + 抢占（CAS）→ 抢不到直接退出
                 ├─ 读活动 + 事件，算 5 个指标
                 ├─ 调 Agent（不持有数据库连接）
                 ├─ 5 道校验
                 └─ 写 success / 写 failed
```

- 任务参数只有 `user_id` 和 `report_date`，其余全部从库里现读；日志里也只打这两个 + `job_id`。
- 每个用户单独投递、单独执行，一个失败不影响其他用户（调度循环里逐个 `try/except`）。
- `job_id = daily-report:{user_id}:{report_date}`，重复点击/重复调度不会排两条。
- 演示时把 `DAILY_REPORT_CRON_MINUTE` 改成「下一分钟」，日志里能看到完整四段（见第 5 节）。

### 幂等、失败与重试

**唯一约束是地基**：`daily_reports` 上有 `UNIQUE(user_id, report_date)`。写库只走 `INSERT ... ON CONFLICT DO NOTHING`，**不允许「先查再插」**。

**抢占是条件更新**（不是查了再改）：

```sql
UPDATE daily_reports
   SET status='running', attempt=attempt+1, locked_at=?
 WHERE user_id=? AND report_date=?
   AND (status IN ('pending','failed')
        OR (status='running' AND locked_at < 租约截止时间))
```

`rowcount = 0` 就说明：已经有成功结果（直接复用），或者另一个 Worker 正在跑（直接退出）。写成功/写失败也都带 `AND status='running'`，所以不会覆盖别人做好的日报。

**任务状态**：`pending`（已入队）→ `running`（有 Worker 抢到）→ `success` / `failed`。租约 10 分钟：Worker 崩溃后 `running` 会卡住，超过租约允许别的实例接管；已有 `success` 的行永远不会被再次执行。

**失败怎么处理**：

| 错误 | 重试 | 最终 |
| --- | --- | --- |
| 模型超时 / 上游 5xx / 网络抖动 | 是，退避 `30s × 第几次` | 3 次后 `failed` |
| 输出不是合法 JSON、数字对不上、出现久坐时长 | 就地重写 1 次 | 仍不合格 → `failed` |
| 缺 `QWEN_API_KEY`、日期参数非法 | **不重试**（重试多少次都一样） | `failed`，日志说明原因 |

失败只写 `error_code` + 内部 `error_detail`，接口只回一句人话（如「模型服务暂时不可用，总结未生成」），不返回堆栈。

### 启动、演示与补跑

```bash
# 1) 三件套一起起来（backend + worker + redis）
cp .env.example .env          # 填 QWEN_API_KEY
docker compose up --build -d
docker compose ps             # backend healthy、worker running、redis healthy
docker compose logs -f worker # 应看到 worker_ready ... timezone=Asia/Shanghai cron=00:05

# 2) 造昨天的演示数据
docker compose exec backend python scripts/seed_demo_data.py --date $(date -d yesterday +%F)

# 3) 只验证「Worker 连上 Redis 并能执行任务」（不碰模型）
docker compose exec backend python scripts/check_worker.py

# 4) 演示自动调度：把 cron 临时改成「两分钟后」
#    注意 hour 和 minute 都要改——只改分钟的话，小时还是 0，下一次触发要等到凌晨
NEXT_H=$(date -d '+2 min' +%-H); NEXT_M=$(date -d '+2 min' +%-M)
DAILY_REPORT_CRON_HOUR=$NEXT_H DAILY_REPORT_CRON_MINUTE=$NEXT_M docker compose up -d worker
docker compose logs -f worker | grep -E "schedule_fired|job_enqueued|job_started|job_succeeded"

# 5) 恢复默认（00:05）
docker compose up -d worker
```

真实跑出来的日志长这样（时间戳是本地时间，说明时区配置生效）：

```text
15:08:00: cron:schedule_daily_reports()
15:08:00: job_enqueued job_id=daily-report:1:2026-11-01 user=1 date=2026-11-01
15:08:00: schedule_fired date=2026-11-01 users=1 enqueued=1 duplicates=0 failed=0
15:08:01: daily-report:1:2026-11-01:generate_daily_report(1, '2026-11-01')
15:08:01: job_started user=1 date=2026-11-01 attempt=1 planned=5 completed=3 skipped=1 uncompleted=1 postponed_ops=1
15:08:01: job_succeeded user=1 date=2026-11-01 attempt=1 review_len=30
```

**补跑指定日期**（服务停机时漏掉的那天，不会自动补，只能手动补）：

```bash
# 命令行
docker compose exec backend python scripts/check_worker.py --enqueue 1 2026-11-01
# 或者用接口（只能操作当前登录用户）
curl -X POST http://localhost:8000/reports/2026-11-01/generate -H "Authorization: Bearer $TOKEN"
```

也可以设 `DAILY_REPORT_TARGET_DATE=2026-11-01` 让调度固定生成那一天，只用于演示。

### 容器与数据卷

| 服务 | 启动命令 | 说明 |
| --- | --- | --- |
| `backend` | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | 对宿主机暴露 8000 |
| `worker` | `arq app.worker.WorkerSettings` | 不暴露端口，与 backend 同镜像 |
| `redis` | `redis-server --appendonly yes --appendfsync everysec` | 不 publish 端口 |

- **API 与 Worker 的 `DATABASE_PATH` 必须完全一致**（`/data/app.db`），并且挂同一个命名卷 `backend-data`。写成相对路径会变成各自容器里的两份副本，症状是「Worker 说成功、接口查不到」。
- **Redis 持久化**：AOF `everysec`（最多丢 1 秒写入）+ RDB `save 900 1` 兜底。要说清楚的是：Redis 持久化**不是业务数据的保障**，日报在 SQLite 里；Redis 丢了只意味着排队中的任务需要重新投递。
- **不把 Redis 暴露出去**：compose 里没有 `ports`，队列只在容器网络内可达。本地调试需要时临时加 `127.0.0.1:6379:6379`（只绑回环），公网部署必须加 `requirepass`。
- **SQLite 并发**：开启 WAL（一写多读）+ `busy_timeout=5000` + 短事务。Worker 调模型时**不持有任何数据库连接**，否则 API 那边的写会卡到超时。`max_jobs=4` 也是因为 SQLite 只有单写者。

### 已知限制

- **不自动补跑**：服务在 00:05 停着，那天的日报不会自己补，必须用上面的补跑命令。cron 不会「补齐所有遗漏日期」。
- `_job_id` 去重只在 `keep_result`（24 小时）内有效，超过一天同 id 可以再次入队——真正的幂等靠数据库唯一约束，不靠 Redis。
- 失败补跑会自动换一代 job_id（`...:r2`、`...:r3`）：旧任务的结果还在 Redis 里保留着，沿用原 id 会被 ARQ 当成「已存在」，补跑就静默失效了。这一点是在演示里踩出来的。
- 单 Worker 实例，没有高可用；多实例时 cron 的 `unique=True` 可以避免同一时刻重复调度。
- 模型输出做了数字白名单校验，但只覆盖阿拉伯数字；用中文数字写的「三项」不会被拦（提示词里已要求不要用）。
- 第五天的活动助手分支还没合进 `main`，本分支是叠在它上面的，合并顺序需要先第五天再本分支。

---

## 第四天新增：Agent 评测与优化

在第三天的 Agent 上建立了可重复的评测基线，按基线失败项做了两处优化，并用同一组用例复测。

| 项 | 值 |
| --- | --- |
| 功能分支 | `feat/day-4-agent-eval`（基于 `641898d`） |
| 评测用例 | `tests/agent/`，14 条，覆盖正常 / 工具 / 含糊 / 越界 / 异常 / 安全 / 上下文 |
| 运行方式 | `uv run python tests/agent/run_eval.py --label baseline`（需后端已启动） |
| 前后对比 | `uv run python tests/agent/report.py <baseline.jsonl> <after.jsonl> --labels 基线,优化后` |
| 优化记录 | [`docs/day-4-agent-optimization.md`](docs/day-4-agent-optimization.md) |
| 结果数据 | `tests/agent/results/*.jsonl`（含每条用例的实际回答与判定理由） |

一句话结果：运行通过率 22/24 → 24/24，用例全部通过 12/14 → 14/14，工具调用正确 13/13 持平，
另外原有 33 个测试全绿。详细口径与限制见优化记录。

评测**不会**在没配 `QWEN_API_KEY` 时假装通过：`agent` 类用例会直接报错退出，
而不是降级成 Mock 结果。

---

## 第三天新增：Agent 与 AG-UI 接入

在第二天的登录与待办之上，加了一个可以在浏览器里对话的 Agent。登录、获取当前用户、退出登录、待办增删改查全部保持原样。

| 项 | 值 |
| --- | --- |
| 功能分支 | `feat/agent-chat` → 已合并至 `main` |
| Agent 名称 | `todo_assistant`（前端 Runtime 的 agents 键、Provider 的 agentId 都用这个名字） |
| Agent 接口 | `POST /agent`，AG-UI 协议，`text/event-stream` 流式返回 |
| 只读工具 | `list_my_todos`：查询**当前登录用户**自己的待办 |
| 模型 | 千问 `qwen3.8-flash`，走 DashScope 的 OpenAI 兼容端点 |
| 原理说明 | [`docs/day-3-agent-learning.md`](docs/day-3-agent-learning.md) |
| 交互图 | [`docs/diagrams/day-3-agent-interaction.png`](docs/diagrams/day-3-agent-interaction.png) |

### 接口

`POST /agent`

- 请求头：`Authorization: Bearer <登录令牌>`、`Content-Type: application/json`
- 请求体：AG-UI 的 `RunAgentInput`（`threadId`、`runId`、`messages` 等）
- 响应：`200 text/event-stream`，事件依次为 `RUN_STARTED` → `TEXT_MESSAGE_*` / `TOOL_CALL_*` → `RUN_FINISHED`
- 未登录 / 令牌过期：**在建流之前**返回 `401 application/json`，不会产生模型调用

鉴权用的是 AG-UI 适配包提供的 `authorization_scope_resolver`（一个普通 FastAPI 依赖），它做了两件事：

1. 用与 `/todos` 完全相同的 `authenticate_token()` 校验令牌；
2. 把校验出的用户写进 `ContextVar`，工具执行时只按这个身份查库；返回值 `user:<id>` 同时作为 thread 快照的归属范围。

工具签名里**没有** `user_id` 参数，模型无法指定查谁的数据。

### 环境变量

新增 4 个，全部只影响 Agent，不影响登录与待办：

| 变量 | 说明 | 示例（占位） |
| --- | --- | --- |
| `QWEN_API_KEY` | 模型密钥，**只放后端** | `sk-xxxxxxxx` |
| `QWEN_BASE_URL` | OpenAI 兼容端点 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `QWEN_MODEL` | 模型名 | `qwen3.8-flash` |
| `AGENT_TOOL_TODO_LIMIT` | 工具一次最多返回多少条 | `20` |

`QWEN_API_KEY` 留空时 `/agent` **不会注册**（日志里有一条 warning），登录与待办功能完全不受影响，也不会带着空密钥去调用模型服务。

密钥不出现在：代码、`.env.example`、`compose.yaml` 的字面值、镜像构建参数、前端任何环境变量、聊天消息。

### 依赖

只装三个包，**不要装 `agent-framework` 元包**——它会把 Azure / Anthropic / Gemini / Qdrant / mem0 全部拉进来（解析出 227 个包，仅 `claude-agent-sdk` 就 123 MB）。

```toml
"agent-framework-ag-ui>=1.7.0",
"agent-framework-openai>=1.17.0",
```

实际安装版本：`agent-framework-core 1.23.0`、`agent-framework-ag-ui 1.7.0`、`agent-framework-openai 1.17.0`、`ag-ui-protocol 0.1.22`、`openai 3.40.0`。锁文件见 `uv.lock`。

### 容器与网络

后端 Compose 增加了外部网络 `intern-agent-net`。前端仓库的 Runtime 容器在这个网络里用服务名 `backend:8000` 访问本服务。

```bash
docker network create intern-agent-net   # 两个仓库启动前各执行一次
docker compose up --build                # 本仓库，端口 8000
```

不建这个网络，`docker compose up` 会直接报网络不存在——这是刻意的，避免悄悄启动一个别人连不上的服务。

### 会话行为（刷新、重启、换账号）

| 场景 | 行为 |
| --- | --- |
| 同一页面连续追问 | 上下文有效：浏览器每次把完整消息历史放进请求体 |
| 刷新页面 | 前端消息列表清空，历史不再发送；**后端本来就不存**，所以没有残留 |
| 后端重启 | 对上下文无影响（不落库）；登录令牌仍在 SQLite 里，未过期就还能用 |
| 点「新建对话」 | 生成新 `threadId`，Provider 重新挂载，客户端消息清空 |
| 退出 / 换账号 | 清 `localStorage` 令牌，并按 user id 分开存放的 `threadId` 不会串 |

`threadId` **不是**登录凭证：换成别人的 `threadId`，请求依然以你自己的身份执行；鉴权只看 `Authorization`。

### 自测结果

| 检查项 | 方式 | 结果 |
| --- | --- | --- |
| 既有功能 | 登录 → 待办增删改 → 退出 | 通过（第二天用例未改动） |
| Agent 对话 | 登录后提问 | **通过**：`qwen3.8-flash` 返回真实回答 |
| 流式输出 | 观察 SSE 是否逐条到达 | **通过**：一次回答收到 20+ 个 `TEXT_MESSAGE_CONTENT`，间隔几十毫秒而非最后一次性出现 |
| 多轮上下文 | 第一轮让其记住虚构昵称，第二轮追问 | **通过**：第二轮正确答出「小海豹」 |
| 工具调用 | 问「我还有哪些待办没做完」 | **通过**：`TOOL_CALL_RESULT` 带回数据库里的 2 条真实待办，回答与之一致 |
| 未登录访问 | `curl -X POST /agent` 不带令牌 | **通过**：`401 {"detail":"未登录或缺少登录凭证"}`，无模型调用 |
| 伪造令牌 | 带随机字符串 | **通过**：`401 {"detail":"登录凭证无效或已退出"}` |
| 退出后调用 | 退出再用旧令牌 | **通过**：`401` |
| 会话隔离 | 第二个账号（手机号 13900000000 系列）登录后问同样的问题 | **通过**：工具返回 `{"owner":"隔离测试","total":0}`，看不到第一个账号的数据 |
| 异常处理 | 停掉后端容器后发送消息 | **通过**：页面顶部提示「连不上后端服务」，加载态结束；恢复容器后同一会话重新发送即可正常回答 |
| 容器复现 | 建共享网络 → 两个仓库 `compose up -d` | **通过**：三个容器 healthy，容器内登录 / 对话 / 工具调用 / 401 全部复测 |

复现命令（后端需先启动，且 `.env` 里已配好 `QWEN_API_KEY`）：

```bash
uv run python scripts/check_agent.py
```

脚本会依次验证：未登录被拒 → 第一轮流式回复 → 第二轮记住昵称 → 第三轮触发工具。

浏览器侧演示截图见前端仓库 `docs/screenshots/day3-agent-chat.jpg`。

### 容器内复测（2026-10-28）

为避免与本机直跑的进程抢端口，验证时把宿主机端口错开（容器内部端口不变，容器间仍用 `backend:8000` 与共享网络）：

| 宿主机端口 | 映射到 | 服务 |
| --- | --- | --- |
| 8010 | 8000 | backend |
| 8790 | 8787 | runtime |
| 5180 | 5173 | frontend |

```bash
docker network create intern-agent-net

# 后端
BACKEND_PORT=8010 CORS_ORIGINS=http://localhost:5180 docker compose up -d --wait

# 前端
FRONTEND_PORT=5180 RUNTIME_PORT=8790 \
AGENT_URL=http://backend:8000/agent \
VITE_API_BASE_URL=http://localhost:8010 \
VITE_COPILOT_RUNTIME_URL=http://localhost:8790/copilotkit \
RUNTIME_CORS_ORIGINS=http://localhost:5180 \
docker compose up -d --wait
```

实测结果：

```
backend health: 200 ok
login ok
无令牌经 Runtime -> RUN_ERROR: HTTP 401: {"detail":"未登录或缺少登录凭证"}
带令牌 -> 事件总数 27，首事件 20ms，末事件 3203ms
         文本增量 12 个，间隔 最小 0ms / 中位 2ms / 最大 203ms   ← 不是一次性返回
         工具调用 True
容器库内新建待办后追问 -> 工具返回 {"owner":"练习同学","total":1,"open":1,...}
                        回答与数据库内容一致
```

流式间隔中位数 2ms、最大 203ms、整体跨 3.2 秒，说明事件是陆续到达的，没有被任何一层缓冲后再一次性吐出。

前端截图（容器版本）：`intern-day-2-frontend/docs/screenshots/day3-agent-chat-container.jpg`

### 验证记录（2026-10-28）

```
[1/4] 未登录访问 /agent
  未带令牌：HTTP 401 {"detail":"未登录或缺少登录凭证"}
[2/4] 登录并开始第一轮
  收到 28 个事件
[3/4] 第二轮：追问上一轮说的昵称
  助手回复：小海豹
[4/4] 第三轮：触发只读工具
  · 调用工具 list_my_todos
  工具返回：{"owner":"练习同学","filter":"open","total":3,"open":2,"returned":2,...}
  助手回复：你还有 2 件没做完：1. 验证不登录时直接访问功能页会被弹回登录页 …
```

### 已知问题

1. 对话历史只在浏览器内存里，刷新即丢。任务允许「只保存当前会话」，这里选择了更简的形态，没有做服务端持久化。
2. `qwen3.8-flash` 是推理模型，兼容端点会额外返回 `reasoning_content`，当前链路没有把它透出成 AG-UI 事件，用户在气泡里只看到最终回答。
3. Agent 只读、只查待办；不支持新增/删除待办，也没有多 Agent、RAG、MCP。

---

## 1. 功能范围

| 能力 | 说明 |
| --- | --- |
| 账号密码登录 | 校验数据库中的真实账号与 Argon2 密码哈希，成功签发会话令牌；账号或手机号都能登录 |
| 手机号注册 | 手机号 + 短信验证码 + 密码创建**独立账户**，注册成功直接登录；手机号即登录名 |
| 多账户数据隔离 | 每个账户的活动数据只有自己能看、能改；用别人的 id 直接调接口返回 `404` |
| 获取当前用户 | 凭令牌返回必要的用户信息（id、账号、手机号、显示名、创建时间） |
| 退出登录 | 服务端删除会话，旧令牌立即失效 |
| 活动安排 | 设置、生成、完成/推迟/跳过、周统计、每日总结；**全部要求登录，且只能操作自己的数据** |
| 健康检查 | `GET /health`，不需要登录，供容器健康检查使用 |

不做的部分：找回密码、第三方登录、角色权限。真实短信通道未接入——当前是可替换的模拟实现，见 3.1 节。

### 验收条件

1. 用练习账号能登录，并进入功能页看到后端返回的显示名。
2. 账号不存在与密码错误返回**同一句**提示，不泄露账号是否存在。
3. 不登录直接访问 `/activity/today` 返回 `401`；伪造令牌同样返回 `401`。
4. 令牌过期后访问接口返回 `401`，前端提示重新登录。
5. 退出登录后再用旧令牌访问 `/activity/today` 返回 `401`。
6. 容器重建后，账号与活动数据仍然存在（SQLite 文件在命名卷中）。
7. 任意 11 位虚构手机号都能注册出新账户；验证码错误、过期、已使用、次数超限都注册不了。
8. 两个账户分别登录，互相看不到对方的活动；用对方的活动 id 调接口返回 `404`。

---

## 2. 接口约定

| 方法 | 路径 | 是否需要登录 | 说明 |
| --- | --- | --- | --- |
| GET | `/health` | 否 | 返回 `{status, app, version, time}` |
| POST | `/auth/sms/send` | 否 | 请求体 `{phone}`，返回 `{expires_in, resend_after, debug_code, masked_phone}` |
| POST | `/auth/register` | 否 | 请求体 `{phone, code, password, display_name}`，返回 `201` 与登录令牌 |
| POST | `/auth/login` | 否 | 请求体 `{username, password}`，`username` 可填账号或手机号；返回 `{access_token, token_type, expires_at}` |
| GET | `/auth/me` | 是 | 返回 `{id, username, phone, display_name, created_at}` |
| POST | `/auth/logout` | 是 | 返回 `204`，服务端删除当前会话 |
| `/activity/*` | 是 | 活动设置、今日安排、完成/推迟/跳过、模板、周统计，详见 `docs/api-day-5.md` |
| `/reports/*` | 是 | 每日总结查询与补跑，详见 `docs/api-day-6.md` |

访问受保护接口时在请求头带上：

```
Authorization: Bearer <access_token>
```

### 错误响应

| 状态码 | 场景 | 响应体示例 |
| --- | --- | --- |
| `401` | 未携带令牌 / 令牌伪造 / 已过期 / 已退出 | `{"detail": "登录凭证无效或已退出"}` |
| `401` | 账号或密码错误 | `{"detail": "账号或密码不正确"}` |
| `400` | 验证码错误 / 已过期 / 未获取 | `{"detail": "验证码不正确，还可尝试 4 次"}` |
| `409` | 手机号已注册 | `{"detail": "该手机号已注册，请直接登录"}` |
| `429` | 发送过于频繁 / 验证码错误次数超限 / 当日次数超限 | `{"detail": "发送过于频繁，请 43 秒后重试"}` |
| `404` | 活动不存在（含别人的活动） | `{"detail": "活动不存在"}` |
| `422` | 字段缺失、空值、超长、手机号格式不对 | `{"detail": "请求参数校验失败", "errors": [{"field": "phone", "message": "..."}]}` |

---

## 3. 账号体系：注册、登录与凭证

### 3.1 手机号注册

注册走「手机号 → 验证码 → 建号」三步：

1. `POST /auth/sms/send`：校验手机号格式后生成 6 位验证码（`secrets.randbelow`），写入 `verification_codes` 表。
2. `POST /auth/register`：校验验证码，通过后创建账户。
   - `username` 与 `phone` 都取手机号，所以**手机号就是登录名**，昵称可重复、不参与唯一性。
   - 手机号上有**部分唯一索引**（`WHERE phone IS NOT NULL`），所以初始化脚本建的练习账号（无手机号）不会冲突。
   - 并发注册同一手机号由唯一索引兜底，返回 `409`。
3. 注册成功直接签发会话，前端不用再登录一次。

验证码的约束（都在 `app/sms.py`）：

| 规则 | 默认值 | 配置项 |
| --- | --- | --- |
| 有效期 | 5 分钟 | `SMS_CODE_TTL_SECONDS` |
| 同一手机号发送间隔 | 60 秒 | `SMS_RESEND_SECONDS` |
| 单个验证码最多输错 | 5 次 | `SMS_MAX_ATTEMPTS` |
| 同一手机号 24 小时发送上限 | 10 次 | `SMS_DAILY_LIMIT` |
| 一次性使用 | 校验通过即写 `consumed_at`，不能复用 | — |

**验证码不落明文**：库里存的是 `HMAC-SHA256(APP_SECRET, "手机号:验证码")`，密钥不在数据库中，泄露数据库也反推不出验证码。

**关于短信通道**：本项目**没有接入真实短信服务**。`app/sms.py` 里定义了 `SmsSender` 协议，默认实现 `ConsoleSmsSender` 只把验证码写到后端日志：

```bash
docker compose logs --tail=20 backend | grep SMS
```

开发模式下（`SMS_DEBUG_RETURN_CODE=true`）响应里还会带 `debug_code`，前端直接显示在页面上，方便自测与演示。
**上线前必须把 `SMS_DEBUG_RETURN_CODE` 设为 `false`**，并实现一个真实的 `SmsSender`（在 `send` 方法里调用云厂商 SDK），其余注册逻辑不用改动。

### 3.2 登录凭证

**后端签发不透明随机令牌，服务端保存会话。**

- 令牌：`secrets.token_urlsafe(32)`，与用户信息无任何关联，猜不出下一个。
- 存储：`sessions` 表只存令牌的 **SHA-256 指纹**，不存令牌原文；即使数据库被读走也换不出可用凭证。
- 有效期：默认 120 分钟（`SESSION_TTL_MINUTES`），过期时间存在数据库里，由后端判断。
- 退出：`DELETE FROM sessions`，删除后同一令牌立刻失效。
- 登录时会顺手清理已过期的会话，避免会话表无限增长。

为什么不用 JWT：JWT 是无状态的，要满足「退出后原凭证立即失效」就得再加一张黑名单表，反而比直接存会话更绕。练习项目选会话表，语义更直白。

前端使用 `localStorage` 保存令牌，每次请求放在 `Authorization` 头里，刷新页面后调用 `/auth/me` 重新校验一次——**前端的登录状态永远以后端校验结果为准**。

---

## 4. 数据保存方式

| 数据 | 存放位置 | 容器重建后 |
| --- | --- | --- |
| 用户（含密码哈希、手机号） | SQLite `users` 表 | 保留（卷 `backend-data` 挂载到 `/data`） |
| 会话令牌指纹 | SQLite `sessions` 表 | 保留，但令牌本身过期后自然失效 |
| 验证码（HMAC 哈希） | SQLite `verification_codes` 表 | 保留，但 5 分钟后自然过期 |

执行 `docker compose down -v` 会删除卷，数据才会清空；普通的 `down` / `up` 不影响数据。

新增 `users.phone` 列时不需要手动迁移：`init_db()` 会读 `PRAGMA table_info`，发现缺列就 `ALTER TABLE` 补上，再建唯一索引，老库可以直接升级。

---

## 5. 在开发容器中开发（推荐）

1. 用 VS Code 打开本仓库目录。
2. 命令面板执行 **Dev Containers: Reopen in Container**。
3. 容器首次创建后会自动执行 `uv sync` 安装依赖。

容器内可用命令：

```bash
git --version
python --version      # 3.12
uv --version
sqlite3 --version

uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
uv run pytest         # 33 个用例
uv run ruff check .
```

---

## 6. 独立启动（Docker Compose）

```bash
cp .env.example .env      # 可选：不建 .env 也能用默认值启动
docker compose config
docker compose up --build -d
docker compose ps
docker compose logs --tail=100
```

- 后端地址：<http://localhost:8000>
- 接口文档：<http://localhost:8000/docs>
- 健康检查：`curl http://localhost:8000/health`

`up` 会同时拉起 `backend`、`worker`、`redis` 三个服务（见「第六天新增」）。只想起接口不跑后台任务时：

```bash
docker compose up -d backend
```

停止并重新创建（数据保留在卷里）：

```bash
docker compose down
docker compose up --build -d
```

> 如果 8000 端口被占用，可以 `BACKEND_PORT=8010 docker compose up --build -d`，
> 同时要把前端仓库的 `VITE_API_BASE_URL` 改成 `http://localhost:8010`。

---

## 7. 前后端联调

两个仓库是**两个独立的 Compose 项目**，容器之间不共享服务发现，所以：

- 浏览器直接访问宿主机映射端口：前端 `http://localhost:5173` → 后端 `http://localhost:8000`。
- 前端代码里的 API 基地址填 `http://localhost:8000`，**不能**填 `http://backend:8000`（那是容器内网地址，浏览器解析不了）。
- 后端通过 `CORS_ORIGINS` 放行前端来源，默认 `http://localhost:5173`，并已允许 `OPTIONS` 预检与 `Authorization` 请求头。

联调步骤：

```bash
# 终端 1：后端仓库根目录
docker compose up --build -d

# 终端 2：前端仓库根目录
docker compose up --build -d
```

浏览器打开 <http://localhost:5173>，用下方练习账号登录。

---

## 8. 账号

### 8.1 练习账号（初始化脚本创建）

账号与密码通过环境变量配置，**不写在源码里**：

```env
SEED_USERNAME=intern
SEED_PASSWORD=intern123456
SEED_DISPLAY_NAME=练习同学
```

首次启动时若 `users` 表为空，会自动创建该账号；活动数据由用户在活动页生成。

也可以在容器里手动执行（适合库里已有其他用户时补建）：

```bash
docker compose exec backend python -m app.seed
# 本机开发环境同理
uv run python -m app.seed
```

### 8.2 自己注册新账户

打开前端 <http://localhost:5173/register>：

1. 填任意 11 位虚构手机号（例如 `13800138000`，联通/移动号段都行，只要格式对）。
2. 点「获取验证码」，开发模式下页面会直接显示 6 位验证码；也可以从后端日志看：
   ```bash
   docker compose logs --tail=20 backend | grep SMS
   ```
3. 填验证码、昵称、密码（≥6 位），提交后自动登录。

注册出来的账户和练习账号完全平级：**各自只能看到自己的活动数据**。想验证隔离，可以用两个手机号各注册一个，分别登录对比今日安排。

---

## 9. 历史自测记录（第二天，仅供参考）

测试环境：Windows 11 + Docker Desktop（容器内 Python 3.12），Chrome 视口 1280×800。
完整的逐项记录（操作步骤、预期结果、15 张截图、命令输出）见 **[docs/自测报告.md](docs/自测报告.md)**。

### 9.1 任务自测清单（12 项，全部通过）

| # | 检查项 | 预期结果 | 实际结果 | 结论 |
| --- | --- | --- | --- | --- |
| 1 | 容器启动 | 前后端服务同时正常运行 | 后端 `Up (healthy)` 映射 8000、前端 `Up` 映射 5173；`/health` 与前端首页均 `200` | 通过 |
| 2 | 正常登录 | 进入功能页，显示后端返回的当前用户 | 跳转 `/todos`，右上角显示「练习同学 / intern」，数据来自 `GET /auth/me` | 通过 |
| 3 | 无效登录 | 登录失败或被校验拦截；错误账号与错误密码提示统一 | 空值被前端拦截；错误密码与不存在的账号均返回「账号或密码不正确」，两次逐字相同 | 通过 |
| 4 | 页面保护 | 返回登录页，不展示受保护内容 | 未登录访问 `/todos` 后 `location.pathname` 变为 `/login` | 通过 |
| 5 | 接口保护 | 返回 `401`，无法绕过前端访问数据 | 无凭证与伪造凭证请求 `/auth/me` 均 `401`；浏览器直接打开 `/todos` 返回 `{"detail":"未登录或缺少登录凭证"}` | 通过 |
| 6 | 刷新页面 | 与 README 描述一致，需重新向后端校验 | 保持登录，但会重新调用 `/auth/me` 后才置为已登录 | 通过 |
| 7 | 凭证过期 | 后端返回 `401`，前端提示重新登录 | 临时把 `SESSION_TTL_MINUTES` 改为 1，等待 70 秒后请求 → `401`；前端跳回登录页并提示「登录状态已失效」 | 通过 |
| 8 | 退出登录 | 返回登录页；旧凭证访问受保护接口 `401` | 退出 `204`，本地令牌被清除，用退出前的令牌请求 `/todos` → `401` | 通过 |
| 9 | 自选功能 | 后端处理数据，前端显示真实结果 | 新增后列表 0→1 条，勾选后统计更新为「已完成 1 项」，刷新后数据仍在 | 通过 |
| 10 | 网络异常 | 显示可理解的失败提示，恢复后可重新操作 | 停掉后端后提示「无法连接后端服务，请确认后端已启动」；恢复后重新登录成功 | 通过 |
| 11 | 重启验证 | 能按 README 初始化或读取账号，数据与会话符合说明 | `down` 后 `up --build -d`，`intern` 的待办 1 条 → 1 条（命名卷），账号可直接登录 | 通过 |
| 12 | 交付复现 | 可复现初始化、启动、登录和自选功能 | 另一目录克隆两仓库并切到 `feat/login-and-todo`，全新数据卷自动初始化 3 条示例待办；登录 `200`、新增 `201`、无令牌 `401` | 通过 |

> 第 12 项说明：两个仓库的默认分支已设为 `feat/login-and-todo`，克隆下来就是完整代码；`main` 只保留基础骨架，用于 PR 对比。本机复现时为拿到全新数据卷临时用了独立 Compose 项目名，等价于换一台机器。

### 9.2 自动化与静态检查

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 单元测试 | `uv run pytest` | 33 passed |
| 代码检查 | `uv run ruff check .` | All checks passed |
| 依赖锁定 | `docker build` | 运行期依赖按 `uv.lock` 安装成功 |
| Compose 配置 | `docker compose config` | 校验通过 |

### 9.3 补充检查（接口层）

| 检查项 | 操作 | 实际结果 |
| --- | --- | --- |
| 健康检查 | `GET /health` | `200`，返回 `status: ok` |
| 空密码 | `POST /auth/login` 密码传空 | `422`，`errors[0].field = password` |
| 手机号格式 | `POST /auth/sms/send` 传 `12345` / `23800138000` | `422`，`请输入有效的 11 位手机号` |
| 发送验证码 | `POST /auth/sms/send` 传新手机号 | `200`，返回 `expires_in=300`、`resend_after=60`、掩码手机号 |
| 发送频控 | 同一手机号连续发两次 | 第二次 `429`，提示「请 N 秒后重试」 |
| 验证码错误 | `POST /auth/register` 传错码 | `400`，提示剩余可试次数 |
| 错误次数超限 | 连续错 5 次后再提交 | `429`，`验证码错误次数过多，请重新获取` |
| 验证码过期 | 把 `expires_at` 改到过去 | `400`，`验证码已过期，请重新获取` |
| 验证码一次性 | 注册成功后查 `consumed_at` | 已写入时间戳，不能复用 |
| 重复手机号 | 同一手机号再注册 | `409`，`该手机号已注册，请直接登录` |
| 弱密码 | 密码传 `123` | `422` |
| 手机号登录 | `POST /auth/login` 用手机号 + 密码 | `200`，正常签发令牌 |
| **多账户隔离** | 甲新建待办后，乙查列表 / 改 / 删 | 乙列表为空；改删均 `404`；甲数据不变 |
| 越权访问 | 用 A 的令牌改/删 B 的待办 | `404`，B 的数据未变 |
| CORS 预检 | `OPTIONS /todos` 带 `Origin: http://localhost:5173` | 返回 `access-control-allow-origin` 与 `allow-credentials: true` |
| 并发写入 | 20 个并发新增待办 | 全部 `201`，无 `database is locked` |
| 内容注入 | 待办标题写入 `<img onerror>` | 接口原样存储，前端按纯文本渲染，脚本未执行 |
| 老库迁移 | 用旧库（`users` 无 `phone` 列）启动新镜像 | 自动补列并建唯一索引，原账号与待办完好 |
| 容器重建 | 删除容器后用同一个数据卷重建 | 重建前后待办条数一致，账号可直接登录 |

> 容器相关几项在 Windows 11 + Docker Desktop 上验证；容器内 Python 为 3.12，本机为 3.13，行为一致。

---

## 10. 已知问题与限制

- 使用 SQLite 单文件，未做并发写优化；练习规模下够用，不适合多实例部署。第六天开了 WAL、把写事务压短，并在建连接处加了一把锁（Windows 上多线程同时打开 WAL 库会偶发 `attempt to write a readonly database`），但没有改变「单写者」这个前提。
- 每日总结**不会自动补齐停机期间漏掉的日期**，只能用补跑接口或脚本指定日期重跑（见「第六天新增 · 启动、演示与补跑」）。
- 令牌保存在浏览器 `localStorage`，存在 XSS 风险；生产环境应改为 HttpOnly Cookie + CSRF 防护。
- **短信通道是模拟的**：验证码只写日志、开发模式下还会通过接口返回，生产环境必须换成真实 `SmsSender` 并关闭 `SMS_DEBUG_RETURN_CODE`。
- 未实现登录失败次数限制与账号锁定（验证码有次数限制，密码登录没有）。
- 未实现找回密码：忘记密码目前只能删库重建或直接改 `password_hash`。
- 注册时先消费验证码再插入用户，若插入因并发失败，用户需要重新获取验证码（不影响正确性）。
- 会话清理只在登录时触发，长期不登录会留下过期记录（不影响正确性）。
- 服务时间统一用 UTC，前端展示时未做本地时区转换（只显示「已完成/未完成」，不展示时间）。

---

## 11. 目录结构

```text
intern-day-2-backend/
├── .devcontainer/          # 开发容器：Git + Python 3.12 + uv
├── .github/
│   └── pull_request_template.md
├── app/
│   ├── main.py             # 应用入口、CORS、校验错误处理
│   ├── worker.py           # ARQ WorkerSettings：任务注册、cron、时区、重试
│   ├── config.py           # 环境变量配置
│   ├── db.py               # SQLite 建表、轻量迁移与连接（WAL + busy_timeout）
│   ├── security.py         # 密码哈希、令牌、验证码哈希、时间工具
│   ├── deps.py             # 数据库连接与登录校验依赖
│   ├── schemas.py          # 请求/响应模型、手机号校验
│   ├── sms.py              # 验证码生成/频控/校验 + 短信通道抽象
│   ├── seed.py             # 练习账号初始化
│   ├── routers/
│   │   └── auth.py         # 发送验证码 / 注册 / 登录 / 当前用户 / 退出
│   ├── activity/           # 第五天：设置、排期、完成/推迟/跳过、周统计
│   ├── agent/              # Agent 组装、AG-UI 入口、身份 ContextVar（活动工具在 activity/agent_tools.py）
│   └── daily_report/       # 第六天：每日总结
│       ├── stats.py        # 统计口径（纯 SQL，模型不参与）
│       ├── store.py        # daily_reports 读写 + CAS 抢占
│       ├── agent.py        # 写总结 + 5 道事实校验
│       ├── service.py      # 编排：抢占 → 统计 → 模型 → 落库
│       ├── tasks.py        # ARQ 任务：调度 + 单用户执行 + ping
│       ├── queue.py        # Redis 连接池、job_id、入队失败语义
│       ├── router.py       # GET /reports/{date}、POST .../generate
│       └── schemas.py      # 响应模型
├── docs/                   # 每日接口说明、Agent 说明、自测记录
├── scripts/
│   ├── check_worker.py     # Worker 连通性自检 / 指定日期补跑
│   └── seed_demo_data.py   # 造某一天的演示活动数据
├── tests/                  # pytest 用例（含统计口径与幂等并发）
├── Dockerfile              # 运行镜像（多阶段，按 uv.lock 安装）
├── compose.yaml            # backend + worker + redis
├── pyproject.toml
└── uv.lock
```
