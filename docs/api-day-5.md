# 久坐活动助手 v1 · 接口说明

> 对应第五天任务。全部接口挂在 `/activity` 下，都需要 `Authorization: Bearer <token>`。
> 时间约定：数据库统一存 UTC，接口出参统一转成带 `+08:00` 偏移的 ISO 串（如 `2026-10-30T10:55:00+08:00`）。
> 按天/按周的分组用 `plan_date`（Asia/Shanghai 本地日期，`YYYY-MM-DD`），不靠 UTC 反推。

## 通用错误

| 状态码 | 含义 |
| --- | --- |
| 401 | 未登录 / 令牌过期 / 已退出 |
| 404 | 资源不存在（含「别人的数据」，不暴露 id 是否存在） |
| 409 | 状态冲突（对已完成/已跳过/已推迟的活动重复操作） |
| 422 | 参数校验失败（时间格式、倒挂、越界、免打扰冲突等），返回 `{detail, errors}` |

## 接口清单

### 1. 读取活动设置

- `GET /activity/settings`

返回用户的工作时段、可活动窗口、免打扰列表、每天次数。无记录时返回默认值。

### 2. 保存活动设置

- `PUT /activity/settings`

请求体：

```json
{
  "work_start": "09:00",
  "work_end": "18:00",
  "morning_start": "10:00",
  "morning_end": "12:00",
  "afternoon_start": "14:00",
  "afternoon_end": "17:30",
  "daily_target": 3,
  "reminders_enabled": true,
  "allow_in_worktime": true,
  "postpone_policy": "next_slot",
  "quiet_periods": [
    { "start_time": "12:00", "end_time": "14:00", "label": "午休", "enabled": true }
  ]
}
```

校验：时间格式 `HH:MM`、工作时段不倒挂、窗口不越界（窗口必须落在工作时段内）、`daily_target` 范围 1–8。

### 3. 今日活动安排

- `GET /activity/today`

返回 `{ date, summary, activities }`，其中 `summary` 含 `total/planned/completed/postponed/skipped`。

### 4. 生成当天安排

- `POST /activity/generate`

幂等：当天已有 `planned/completed/postponed` 记录时直接返回现有结果，不重复生成。

返回 `{ requested, planned, notice, activities }`；时段不足时 `planned < requested`，`notice` 说明原因。

### 5. 完成活动

- `POST /activity/{id}/complete`

仅 `planned` 可完成。重复操作返回 409。

### 6. 推迟活动

- `POST /activity/{id}/postpone`

成功：原记录置 `postponed`，派生一条新 `planned`（`postponed_from_id` 指向原记录，新时间在后续可用时段）。
无可用时段：返回 `200 + { ok: false, reason }`，原记录保持 `planned` 不变。

### 7. 今天不方便（跳过）

- `POST /activity/{id}/skip`

请求体 `{ "reason": "下午开会" }`（可选）。置 `skipped`，当天不再重新生成同类型活动。

### 8. 本周统计

- `GET /activity/week`

返回 `{ week_start, week_end, planned, completed, postponed, skipped, days }`。

口径：周起点为周一（Asia/Shanghai）；`planned` 不含 `postponed` 记录（推迟派生记录计入，原记录只计一次推迟动作）。

### 9. 生成安排建议（不落库）

- `POST /activity/agent/suggest?daily_target=3&from_time=15:00`

`daily_target` 范围 1–8，`from_time` 为 `HH:MM`（可选，只考虑该时间之后的时段）。

返回 `{ requested, planned, slots, notice }`，`slots` 里每条含 `scheduled_at`（+08:00）、`activity_type`、`title`。**只读，不写库。**

### 10. 确认并保存建议

- `POST /activity/agent/confirm`

请求体：

```json
{ "slots": [{ "scheduled_at": "2026-10-30T10:55:00+08:00", "activity_type": "walk", "title": "站起来走动" }] }
```

**后端逐条重新校验**：时间必须在工作时段内、在可活动窗口内、不在免打扰时段内、活动类型合法。任一不满足返回 422，整体不落库。通过后以 `source=agent` 保存。

---

## 数据模型

| 表 | 用途 |
| --- | --- |
| `activity_settings` | 一人一行的时段与次数设置 |
| `quiet_periods` | 免打扰时段（可多条） |
| `activities` | 活动安排，含 `status`（planned/completed/postponed/skipped）、`source`（system/agent）、`postponed_from_id` |
| `activity_events` | 操作流水（generate/complete/postpone/skip/create） |

数据库文件为 `data/app.db`，已加入 `.gitignore`，不提交。
