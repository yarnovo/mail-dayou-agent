FROM python:3.13-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY pyproject.toml ./
COPY src ./src

RUN pip install --upgrade pip && \
    pip install -e .

EXPOSE 9000

CMD ["uv", "run", "uvicorn", "dayou_agent.server:app", "--host", "0.0.0.0", "--port", "9000"]
