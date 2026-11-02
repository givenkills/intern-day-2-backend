# 每日总结接口（第六天）

基础路径 `/reports`，两个接口都要求登录。用户身份只从 `Authorization: Bearer <token>` 解析——路径和请求体里都没有 `user_id`，所以「改 URL 看别人的日报」在协议层就不成立。

- 成功返回 `200` / `202`，响应体都是 JSON。
- 未登录返回 `401`，与其他接口一致。
- 日期非法返回 `422`，`detail` 是中文提示，前端可直接展示。
- 队列不可用返回 `503`，表示**没有**提交成功。

---

## GET /reports/{report_date}

读取某一天的日报。**没有生成过也返回 200**，用 `status = not_generated` 表达，避免前端把「没日报」和「没权限」混在一起。

### 路径参数

| 参数 | 说明 |
| --- | --- |
| `report_date` | `YYYY-MM-DD`，必须是**已结束**的自然日（今天及以后返回 422），且不早于一年前 |

### 响应

```json
{
  "report_date": "2026-11-01",
  "status": "success",
  "stats": {
    "planned": 5,
    "completed": 3,
    "skipped": 1,
    "uncompleted": 1,
    "postponed_ops": 1
  },
  "review": "当天 5 项安排完成 3 项，跳过 1 项，未完成 1 项。",
  "suggestion": "把未完成的 1 项挪到上午可活动时段，冲突更少。",
  "sedentary": { "recorded": false, "minutes": null },
  "generated_at": "2026-11-02T00:05:09+08:00",
  "attempt": 1,
  "error": null
}
```

### status 取值

| status | 含义 | 页面表现 |
| --- | --- | --- |
| `not_generated` | 还没有这条记录 | 提示 + 「生成这一天」按钮 |
| `pending` | 已入队，Worker 还没领 | 「正在生成」，3 秒轮询 |
| `running` | Worker 正在执行 | 「正在生成」，3 秒轮询 |
| `success` | 已完成 | 统计卡片 + 回顾 + 建议 |
| `failed` | 重试后仍失败 | 失败提示 + 「重新生成」 |

### 字段说明

- `stats`：只有 `success` 才有值，其余为 `null`。所有数字由后端 SQL 算出，模型不参与。
- `postponed_ops`：推迟**操作**次数，与活动数不同维度，可与 `completed` 重叠，**不能相加**。
- `sedentary.recorded = false` 表示用户没有自报久坐时长，`minutes` 为 `null`。它不等于 0 分钟。
- `generated_at`：生成时间（+08:00），与 `report_date` 分开保存。
- `error`：仅 `failed` 时有值，`code` 是稳定的枚举，`message` 是给用户看的一句话；内部堆栈只在数据库的 `error_detail` 里。

### 零活动

`status = success` 且 `stats.planned = 0` 时，`review` 会明确说明「当日没有活动记录，无法判断」，页面额外提示一句。没有记录 ≠ 久坐 0 分钟。

---

## POST /reports/{report_date}/generate

手动生成或补跑。**只做占位 + 入队，立刻返回**，不等模型。

### 响应

| 状态码 | status | 场景 |
| --- | --- | --- |
| `202` | `queued` | 已成功入队，带 `job_id` |
| `200` | `in_progress` | 任务已在队列中，或另一个 Worker 正在执行 |
| `200` | `already_generated` | 已有成功结果，直接复用，不重新生成 |
| `503` | — | Redis 不可用，**没有**提交成功 |

```json
// 202
{
  "report_date": "2026-11-01",
  "status": "queued",
  "job_id": "daily-report:1:2026-11-01",
  "message": "已提交到后台队列，稍后刷新查看"
}
```

```json
// 200 已有结果
{
  "report_date": "2026-11-01",
  "status": "already_generated",
  "message": "该日总结已生成，直接复用已有结果"
}
```

```json
// 503
{ "detail": "任务队列暂不可用，总结未提交，请稍后重试" }
```

### 行为约定

- 入队成功后才写 `pending` 占位。Redis 挂了不会留下一条永远「生成中」的假记录。
- `failed` 的记录允许再次提交（补跑）；`success` 的记录不会重新生成（今天不做「强制重算」）。
- 重复提交用同一个 `job_id`，Redis 里已有同 id 任务时返回 `in_progress` 而不是排第二条。

---

## 调用示例

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"intern","password":"intern123456"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 查昨天的
curl -s "http://localhost:8000/reports/$(date -d yesterday +%F)" -H "Authorization: Bearer $TOKEN"

# 补跑
curl -s -X POST "http://localhost:8000/reports/2026-11-01/generate" -H "Authorization: Bearer $TOKEN"
```
