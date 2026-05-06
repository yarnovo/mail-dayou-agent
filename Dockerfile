# mail-dayou-agent FC v3 custom container
#
# FC v3 默认监听 PORT (env · 默认 9000) · uvicorn 用 PORT。
# FC v3 custom runtime 直接跑 web server · 不需要 fcapp wrapper。
#
# 业务 agent 仓 cp 后改:
#   mail-dayou-agent     业务 agent slug (例: discovery-xiaoyan)
#   mail_dayou_agent   Python module 名 (underscore · 例: discovery_xiaoyan)

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

RUN apt-get update -qq \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/

COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

COPY src/      ./src/
COPY workspace/ ./workspace/

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=9000

EXPOSE 9000

CMD ["uv", "run", "uvicorn", "mail_dayou_agent.server:app", "--host", "0.0.0.0", "--port", "9000"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:9000/health || exit 1
