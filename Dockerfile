# syntax=docker/dockerfile:1.7
#
# Образ API и runner LoadWatch.
#
#   docker build -t loadwatch-api .
#
# Один образ — четыре роли (меняется только команда):
#   API (по умолчанию):  uvicorn --factory loadwatch.api.main:create_app ...
#   runner:              python -m loadwatch.runner
#   сводка:              python -m loadwatch.digest
#   очистка:             python -m loadwatch.maintenance
#   миграции:            alembic upgrade head
#   всё по расписанию:   supercronic /app/docker/crontab   (сервис runner в compose)
#
# Настройки — только переменные окружения (см. loadwatch/config.py, класс Settings).
# Файл .env в образ не попадает (.dockerignore), LOADWATCH_ENV_FILE пустой.

ARG PYTHON_IMAGE=python:3.12-slim

# --- uv: менеджер зависимостей, версия закреплена ------------------------------------
FROM ghcr.io/astral-sh/uv:0.13.0 AS uv

# --- supercronic: cron для контейнера (пишет в stdout, корректно ловит SIGTERM) -------
# Бинарник проверяется по SHA-256 из релиза; архитектура выбирается автоматически.
FROM scratch AS supercronic-amd64
ADD --chmod=755 \
    --checksum=sha256:a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1 \
    https://github.com/aptible/supercronic/releases/download/v0.2.49/supercronic-linux-amd64 \
    /supercronic

FROM scratch AS supercronic-arm64
ADD --chmod=755 \
    --checksum=sha256:02aa0cb229ba09050cba6638059dadb9eedc2276632ea43d6a57a2f8c1629dd5 \
    https://github.com/aptible/supercronic/releases/download/v0.2.49/supercronic-linux-arm64 \
    /supercronic

ARG TARGETARCH
FROM supercronic-${TARGETARCH} AS supercronic

# --- сборка окружения ---------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
# Только зависимости из uv.lock (--frozen: lock не пересчитывается, расхождение = ошибка).
# Сам пакет не устанавливается: код лежит в /app и находится через PYTHONPATH,
# поэтому правка кода не сбрасывает слой с зависимостями.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

# --- итоговый образ ----------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

LABEL org.opencontainers.image.title="loadwatch-api" \
      org.opencontainers.image.description="LoadWatch API, runner, digest and maintenance" \
      org.opencontainers.image.licenses="MIT"

# Непривилегированный пользователь с фиксированным UID (удобно для томов и k8s).
RUN groupadd --system --gid 10001 loadwatch \
 && useradd --system --uid 10001 --gid loadwatch --home-dir /app --no-create-home \
        --shell /usr/sbin/nologin loadwatch

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=supercronic /supercronic /usr/local/bin/supercronic
COPY alembic.ini ./
COPY migrations ./migrations
# Включая loadwatch/assets/fonts — шрифты для PDF-отчётов.
COPY loadwatch ./loadwatch
COPY docker/crontab ./docker/crontab

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LOADWATCH_ENV_FILE=""

USER loadwatch
EXPOSE 8000

# Проверка через /api/v1/health: 200 — API и база живы, 503 — база недоступна.
# curl в slim-образе нет, поэтому стандартный urllib (ошибка HTTP = исключение = код 1).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"]

# --forwarded-allow-ips='*': в контейнере API доступен только через свой Nginx
# (порт наружу не публикуется в prod), поэтому X-Forwarded-For от него доверенный.
CMD ["uvicorn", "--factory", "loadwatch.api.main:create_app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
