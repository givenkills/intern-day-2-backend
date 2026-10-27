# intern-day-2-backend

实习生第二天练习的后端仓库：**FastAPI + 不透明会话令牌 + SQLite**，为「待办清单」前端提供登录与数据接口。

- 前端仓库（配套）：`https://github.com/givenkills/intern-day-2-frontend`
- 接口文档（启动后）：<http://localhost:8000/docs>
- 配套 PR：https://github.com/givenkills/intern-day-2-frontend/pull/1
- 当前联调分支：`feat/login-and-todo`（两个仓库同分支名）

---

## 1. 功能范围

| 能力 | 说明 |
| --- | --- |
| 账号密码登录 | 校验数据库中的真实账号与 Argon2 密码哈希，成功签发会话令牌 |
| 获取当前用户 | 凭令牌返回必要的用户信息（id、账号、显示名、创建时间） |
| 退出登录 | 服务端删除会话，旧令牌立即失效 |
| 待办清单 | 列出、新增、修改完成状态、删除；**全部要求登录，且只能操作自己的数据** |
| 健康检查 | `GET /health`，不需要登录，供容器健康检查使用 |

不做的部分（按任务说明无需实现）：注册、找回密码、短信/第三方登录、角色权限。

### 验收条件

1. 用练习账号能登录，并进入功能页看到后端返回的显示名。
2. 账号不存在与密码错误返回**同一句**提示，不泄露账号是否存在。
3. 不登录直接访问 `/todos` 返回 `401`；伪造令牌同样返回 `401`。
4. 令牌过期后访问接口返回 `401`，前端提示重新登录。
5. 退出登录后再用旧令牌访问 `/todos` 返回 `401`。
6. 容器重建后，账号与待办数据仍然存在（SQLite 文件在命名卷中）。

---

## 2. 接口约定

| 方法 | 路径 | 是否需要登录 | 说明 |
| --- | --- | --- | --- |
| GET | `/health` | 否 | 返回 `{status, app, version, time}` |
| POST | `/auth/login` | 否 | 请求体 `{username, password}`，返回 `{access_token, token_type, expires_at}` |
| GET | `/auth/me` | 是 | 返回 `{id, username, display_name, created_at}` |
| POST | `/auth/logout` | 是 | 返回 `204`，服务端删除当前会话 |
| GET | `/todos` | 是 | 返回当前用户的待办数组，未完成为先、新创建的在前 |
| POST | `/todos` | 是 | 请求体 `{title}`，返回 `201` 与新建的待办 |
| PATCH | `/todos/{id}` | 是 | 请求体 `{title?, done?}`，返回更新后的待办 |
| DELETE | `/todos/{id}` | 是 | 返回 `204`；不存在或不属于自己都返回 `404` |

访问受保护接口时在请求头带上：

```
Authorization: Bearer <access_token>
```

### 错误响应

| 状态码 | 场景 | 响应体示例 |
| --- | --- | --- |
| `401` | 未携带令牌 / 令牌伪造 / 已过期 / 已退出 | `{"detail": "登录凭证无效或已退出"}` |
| `401` | 账号或密码错误 | `{"detail": "账号或密码不正确"}` |
| `404` | 待办不存在（含别人的待办） | `{"detail": "待办不存在"}` |
| `422` | 字段缺失、空值、超长 | `{"detail": "请求参数校验失败", "errors": [{"field": "password", "message": "..."}]}` |

---

## 3. 登录状态方案

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
| 用户（含密码哈希） | SQLite `users` 表 | 保留（卷 `backend-data` 挂载到 `/data`） |
| 会话令牌指纹 | SQLite `sessions` 表 | 保留，但令牌本身过期后自然失效 |
| 待办数据 | SQLite `todos` 表 | 保留 |

执行 `docker compose down -v` 会删除卷，数据才会清空；普通的 `down` / `up` 不影响数据。

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
uv run pytest         # 19 个用例
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

## 8. 练习账号

账号与密码通过环境变量配置，**不写在源码里**：

```env
SEED_USERNAME=intern
SEED_PASSWORD=intern123456
SEED_DISPLAY_NAME=练习同学
```

首次启动时若 `users` 表为空，会自动创建该账号，并插入 3 条示例待办。

也可以在容器里手动执行（适合库里已有其他用户时补建）：

```bash
docker compose exec backend python -m app.seed
```

本机开发环境同理：

```bash
uv run python -m app.seed
```

---

## 9. 自测记录

在本机（Windows 11 + Python 3.13）与容器（python:3.12-slim）中执行，结果一致。

| 检查项 | 操作 | 实际结果 |
| --- | --- | --- |
| 单元测试 | `uv run pytest` | 19 passed |
| 代码检查 | `uv run ruff check .` | All checks passed |
| 健康检查 | `curl /health` | `200`，返回 `status: ok` |
| 正常登录 | `POST /auth/login` | `200`，返回令牌与过期时间 |
| 错误密码 | `POST /auth/login` | `401`，`账号或密码不正确` |
| 账号不存在 | `POST /auth/login` | `401`，提示与上一条完全相同 |
| 空密码 | `POST /auth/login` | `422`，`errors[0].field = password` |
| 未带令牌 | `GET /todos` | `401` |
| 伪造令牌 | `GET /auth/me` 带 `Bearer forged-token-value` | `401` |
| 令牌过期 | 把会话 `expires_at` 改到过去 | `401`，提示「登录已过期」 |
| 退出登录 | `POST /auth/logout` → 再请求 `/auth/me` | 退出 `204`，复用令牌 `401` |
| 越权访问 | 用 A 的令牌改/删 B 的待办 | `404`，B 的数据未变 |
| CORS 预检 | `OPTIONS /todos` 带 `Origin: http://localhost:5173` | 返回 `access-control-allow-origin` 与 `allow-credentials: true` |
| 镜像构建 | `docker build -t intern-day-2-backend:verify .` | 成功，运行期依赖按 `uv.lock` 安装 |
| 容器启动 | 用该镜像启动并映射端口 | `/health` 返回 `200`，启动时自动建库并创建练习账号 |
| 容器重建 | 删除容器后用同一个数据卷重建 | 重建前 4 条待办，重建后仍是 4 条，账号可直接登录 |

> 容器相关几项在 Windows 11 + Docker Desktop 上验证；容器内 Python 为 3.12，本机为 3.13，行为一致。

---

## 10. 已知问题与限制

- 使用 SQLite 单文件，未做并发写优化；练习规模下够用，不适合多实例部署。
- 令牌保存在浏览器 `localStorage`，存在 XSS 风险；生产环境应改为 HttpOnly Cookie + CSRF 防护。
- 未实现登录失败次数限制与账号锁定。
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
│   ├── config.py           # 环境变量配置
│   ├── db.py               # SQLite 建表与连接
│   ├── security.py         # 密码哈希、令牌、时间工具
│   ├── deps.py             # 数据库连接与登录校验依赖
│   ├── schemas.py          # 请求/响应模型
│   ├── seed.py             # 练习账号初始化
│   └── routers/
│       ├── auth.py         # 登录 / 当前用户 / 退出
│       └── todos.py        # 待办增删改查
├── tests/                  # pytest 用例
├── Dockerfile              # 运行镜像（多阶段，按 uv.lock 安装）
├── compose.yaml            # 独立启动后端
├── pyproject.toml
└── uv.lock
```
