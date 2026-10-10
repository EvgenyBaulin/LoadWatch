# syntax=docker/dockerfile:1.7
#
# Образ фронтенда: сборка Vue/Vite + Nginx со статикой и прокси /api/ на API.
#
#   docker build -f docker/frontend.Dockerfile -t loadwatch-frontend .
#
# Контекст сборки — корень репозитория (нужны frontend/, docker/ и deploy/).
# Адрес API внутри сети compose задаётся при сборке: --build-arg API_UPSTREAM=api:8000.

# --- 1. Сборка статики ---------------------------------------------------------------
FROM node:24-bookworm-slim AS build
WORKDIR /src/frontend
# Сначала только манифесты: слой с node_modules кэшируется, пока не изменится lock.
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. Заголовки безопасности: CSP с хэшем inline-скрипта темы ----------------------
# Хэш считается по собранному index.html, поэтому всегда совпадает с тем, что отдаётся.
FROM python:3.12-alpine AS headers
COPY deploy/gen_security_headers.py /gen.py
COPY --from=build /src/frontend/dist/index.html /index.html
RUN python /gen.py /index.html > /security-headers.conf

# --- 3. Nginx со статикой -----------------------------------------------------------
FROM nginx:1.28-alpine AS runtime

LABEL org.opencontainers.image.title="loadwatch-frontend" \
      org.opencontainers.image.description="LoadWatch SPA served by Nginx" \
      org.opencontainers.image.licenses="MIT"

ARG API_UPSTREAM=api:8000
ENV API_UPSTREAM=${API_UPSTREAM} \
    NGINX_ENVSUBST_FILTER=^API_UPSTREAM$

# Шаблон обрабатывает envsubst из стандартного entrypoint образа nginx
# (подставляется только ${API_UPSTREAM}, переменные Nginx вида $host не трогаются).
COPY docker/nginx-frontend.conf.template /etc/nginx/templates/default.conf.template
COPY --from=headers /security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /src/frontend/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
