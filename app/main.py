"""FastAPI 应用入口。

统一约定：
- 所有业务接口挂在 /auth 与 /todos 下，交互文档在 /docs。
- 参数校验失败返回 422，统一的中文提示结构，前端可以直接展示。
- 跨域来源由 CORS_ORIGINS 控制，浏览器直接访问宿主机 8000 端口。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .activity.router import router as activity_router
from .agent.endpoint import register_agent_endpoint
from .config import get_settings
from .db import init_db
from .routers import auth, todos
from .schemas import HealthOut
from .security import to_iso, utcnow
from .seed import seed_demo_user

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.seed_on_startup:
        seed_demo_user()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="实习生第二天练习：账号密码登录 + 待办清单。登录凭证为后端签发的不透明令牌。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # 允许安卓模拟器（10.0.2.2）和局域网真机调试访问；生产环境应把正则置空。
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """把 FastAPI 默认的英文校验错误整理成前端可直接展示的结构。"""
    errors = []
    for error in exc.errors():
        location = [str(part) for part in error["loc"] if part not in ("body", "query", "path")]
        errors.append(
            {
                "field": ".".join(location) or "body",
                "message": error["msg"],
                "type": error["type"],
            }
        )
    return JSONResponse(
        status_code=422,
        content={"detail": "请求参数校验失败", "errors": errors},
    )


@app.get("/health", response_model=HealthOut, tags=["运维"], summary="健康检查")
def health() -> HealthOut:
    """不需要登录，用于确认容器已正常启动。"""
    return HealthOut(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        time=to_iso(utcnow()),
    )


app.include_router(auth.router)
app.include_router(todos.router)
app.include_router(activity_router)

# Agent 走 AG-UI 协议，接口是 POST /agent（SSE 事件流）。
# 没配模型密钥时不注册：其余功能照常可用，也不会带着空密钥去调用模型服务。
if settings.agent_ready:
    register_agent_endpoint(app)
else:
    logger.warning("未配置 QWEN_API_KEY，/agent 入口未注册；登录与待办功能不受影响")
