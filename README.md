# intern-day-2-backend

> **完整代码在 `feat/login-and-todo` 分支。** `main` 只保留了项目骨架（初始化说明与工程配置）；
> 接口实现、测试、Docker 配置与自测报告都在功能分支上，评审请直接看 PR：
> <https://github.com/givenkills/intern-day-2-backend/pull/1>

实习生第二天练习的后端仓库：**FastAPI + SQLite**，为「待办清单」前端提供登录与数据接口。

- 前端仓库（配套）：`https://github.com/givenkills/intern-day-2-frontend`
- 接口文档：<http://localhost:8000/docs>

## 功能范围（初稿）

| 能力 | 说明 |
| --- | --- |
| 账号密码登录 | 校验数据库中的真实账号，成功签发登录凭证 |
| 获取当前用户 | 凭凭证返回必要的用户信息 |
| 退出登录 | 服务端使当前凭证失效 |
| 待办清单 | 列出、新增、修改完成状态、删除，全部要求登录 |

## 接口约定（初稿）

| 方法 | 路径 | 是否需要登录 |
| --- | --- | --- |
| GET | `/health` | 否 |
| POST | `/auth/login` | 否 |
| GET | `/auth/me` | 是 |
| POST | `/auth/logout` | 是 |
| GET / POST | `/todos` | 是 |
| PATCH / DELETE | `/todos/{id}` | 是 |

## 验收条件

1. 用练习账号能登录，并拿到可用于后续请求的凭证。
2. 账号不存在与密码错误返回同一句提示。
3. 未携带、伪造、过期、已退出的凭证访问受保护接口均返回 `401`。
4. 待办接口只能操作自己的数据。
5. 容器重建后账号与待办数据仍然存在。

登录状态方案、启动步骤与自测记录在功能完成后补充。
