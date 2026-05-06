# mail-dayou-agent · FC v3 Custom Container 镜像
#
# build 上下文需要 sibling 含 akong-agent-base/ (deploy.yml clone 进来)
# 用法: docker build -t agentaily/mail-dayou-agent:<tag> .

FROM python:3.11-slim

WORKDIR /code

# 装 uv (从官方 image copy)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 系统依赖 · uvicorn 需 ssl/curl 健康检查
RUN apt-get update -qq && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 1. akong-agent-base 在 sibling (../akong-agent-base · 跟 pyproject [tool.uv.sources] 路径一致)
COPY akong-agent-base /akong-agent-base

# 2. 装依赖 (uv 解析 pyproject [tool.uv.sources] · 拿 sibling)
COPY pyproject.toml uv.lock ./
RUN uv pip install --system --no-cache .

# 3. cp src + workspace + skills
COPY src ./src
COPY workspace ./workspace
COPY skills ./skills

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/code/src \
    PORT=9000

EXPOSE 9000

CMD ["python", "-m", "uvicorn", "dayou_agent.server:app", "--host", "0.0.0.0", "--port", "9000"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD curl -fsS http://localhost:9000/health || exit 1
