# 开发容器镜像：只放工具链，不放业务代码。
# 业务代码通过 VS Code 挂载进容器，所以在容器里改文件等于改本机文件。
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# 固定 uv 版本，避免今天和明天装出不同的解析结果
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /usr/local/bin/

# sqlite3 命令行方便直接查看练习数据
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 curl \
    && rm -rf /var/lib/apt/lists/*
