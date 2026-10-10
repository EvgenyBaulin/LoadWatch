# syntax=docker/dockerfile:1.7
#
# Образ демо-цели (demo_target): маленькое FastAPI-приложение для демонстрации
# нагрузочных тестов. Только для локального стека — в прод его не выкатывать.
#
#   docker build -f docker/demo-target.Dockerfile -t loadwatch-demo-target .
#
# Зависимости берутся из того же uv.lock, что и у API (FastAPI, uvicorn), поэтому
# версии совпадают; лишние пакеты (reportlab и т. п.) — цена одного lock-файла.

ARG PYTHON_IMAGE=python:3.12-slim

FROM ghcr.io/astral-sh/uv:0.13.0 AS uv

FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="loadwatch-demo-target" \
      org.opencontainers.image.description="Demo website for LoadWatch load tests" \
      org.opencontainers.image.licenses="MIT"

RUN groupadd --system --gid 10002 demo \
 && useradd --system --uid 10002 --gid demo --home-dir /app --no-create-home \
        --shell /usr/sbin/nologin demo

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY demo_target ./demo_target

# DEMO_HOST=0.0.0.0: в контейнере слушать нужно все интерфейсы, иначе runner
# из соседнего контейнера не достучится. Остальные DEMO_* — см. demo_target/app.py.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEMO_HOST=0.0.0.0 \
    DEMO_PORT=8081

USER demo
EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('DEMO_PORT', '8081'), timeout=2)"]

CMD ["python", "-m", "demo_target"]
