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

### 8. 周度复盘

- `GET /activity/week?week_start=2026-10-26&timezone=Asia/Shanghai`

参数：

- `week_start`：周一，格式 `YYYY-MM-DD`；不传时返回当前周。
- `timezone`：IANA 时区名（如 `Asia/Shanghai`）或固定偏移（如 `+08:00`），默认 `Asia/Shanghai`。周边界按该时区的周一 00:00 计算。

返回：

```json
{
  "week_start": "2026-10-26",
  "week_end": "2026-11-01",
  "timezone": "Asia/Shanghai",
  "is_current": false,
  "is_complete": true,
  "planned": 3,
  "completed": 1,
  "skipped": 1,
  "pending": 1,
  "denominator": 3,
  "completion_rate": 0.3333333333333333,
  "postponed_activities": 1,
  "postponed_ops": 2,
  "generated_at": "2026-11-05T02:00:00+00:00",
  "days": [
    {
      "date": "2026-10-26",
      "planned": 1,
      "completed": 1,
      "skipped": 0,
      "pending": 0,
      "denominator": 1,
      "completion_rate": 1.0,
      "has_activities": true,
      "activities": [
        {
          "id": 1,
          "title": "远眺",
          "scheduled_at": "2026-10-26T02:00:00+00:00",
          "local_time": "10:00",
          "status": "completed",
          "source": "system",
          "duration_minutes": 5,
          "is_postponed": false
        }
      ]
    }
  ]
}
```

口径：

- 周起点为周一；`planned` 只数最终记录，推迟链上的中间 `postponed` 记录不计。
- `completed + skipped + pending = planned`；`planned = 0` 时 `completion_rate` 为 `null`，不显示 0%。
- `postponed_activities` 按活动只数一次；`postponed_ops` 是推迟操作次数。两者是不同维度，不能相加。
- 推迟只允许落在原活动当天；跨日返回 422。
- 未来周、非周一、非法时区、格式错误均返回 422；未登录返回 401。

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
