# ---------- 构建阶段：严格按 uv.lock 安装运行期依赖 ----------
FROM python:3.12-slim-bookworm AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# 固定 uv 版本，和开发容器保持一致
RUN pip install --no-cache-dir uv==0.13.6

WORKDIR /app

# 只先拷依赖清单，锁文件不变时这一层可以直接命中缓存
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# ---------- 运行阶段 ----------
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    DATABASE_PATH=/data/app.db

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY app ./app

RUN mkdir -p /data

# 必须监听 0.0.0.0，否则容器外（宿主机浏览器）访问不到
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
