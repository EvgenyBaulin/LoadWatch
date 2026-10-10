#!/usr/bin/env bash
# Деплой LoadWatch на VPS (выполняется на сервере после rsync кода в /opt/loadwatch).
#
# Вызывается из GitHub Actions (deploy.yml) по SSH под пользователем deploy:
#   cd /opt/loadwatch && bash deploy/deploy.sh
# Можно запустить и руками — под deploy или под root (тогда скрипт сам
# переключится на владельца /opt/loadwatch, чтобы не плодить файлы root).
#
# Что делает (идемпотентно, повторный запуск безопасен):
#   1. uv sync --frozen --no-dev — зависимости строго по uv.lock;
#   2. alembic upgrade head — миграции под пользователем loadwatch (через sudo);
#   3. заголовки безопасности Nginx (CSP с хэшем inline-скрипта) из index.html;
#   4. перезапуск loadwatch-api и проверка /api/v1/health с повторами.
# Runner/digest/maintenance перезапускать не нужно: cron или таймер сам запустит
# новый код в следующую минуту. Демо-цель перезапускается, если она включена.
#
# Секреты не печатаются: скрипт не читает /etc/loadwatch/env, его читает только
# приложение (под loadwatch). Права sudo для пользователя deploy ограничены
# файлом /etc/sudoers.d/loadwatch-deploy (создаёт bootstrap-server.sh).

set -euo pipefail

APP_DIR=/opt/loadwatch
ENV_FILE=/etc/loadwatch/env
SERVICE_USER=loadwatch
HEALTH_URL=http://127.0.0.1:8000/api/v1/health
HEADERS_SNIPPET=/etc/nginx/snippets/loadwatch-headers.conf

log() { printf '[deploy %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ОШИБКА: $*" >&2; exit 1; }

# Под root — переключаемся на владельца кода (обычно deploy).
if [[ $EUID -eq 0 ]]; then
    owner=$(stat -c %U "$APP_DIR")
    [[ $owner != root ]] || die "$APP_DIR принадлежит root; запустите bootstrap-server.sh"
    log "запущено от root, продолжаю от имени $owner"
    exec runuser -u "$owner" -- bash "$APP_DIR/deploy/deploy.sh" "$@"
fi

cd "$APP_DIR"
[[ -f uv.lock && -f pyproject.toml ]] || die "в $APP_DIR нет кода (rsync не выполнен?)"
[[ -f frontend/dist/index.html ]] || die "нет frontend/dist/index.html (фронтенд собирается в CI)"

# --- 1. Зависимости -------------------------------------------------------------
log "uv sync (по uv.lock, без dev-зависимостей)"
# Python 3.12 от uv в /opt/uv-python (каталог создаёт bootstrap-server.sh): в новых
# Ubuntu системный Python другой версии. Повторная установка — no-op.
export UV_PYTHON_INSTALL_DIR=/opt/uv-python
export UV_COMPILE_BYTECODE=1       # байткод заранее: сервисы работают с read-only кодом
uv python install --quiet 3.12
uv sync --frozen --no-dev --python 3.12
# Байткод самого проекта (uv компилирует только зависимости).
.venv/bin/python -m compileall -q loadwatch migrations demo_target >/dev/null

# --- 2. Миграции ------------------------------------------------------------------
# От имени loadwatch: только он (и root) может читать $ENV_FILE с секретами.
# Команда совпадает символ в символ с разрешённой в sudoers.
log "alembic upgrade head"
sudo -n -u "$SERVICE_USER" /usr/bin/env LOADWATCH_ENV_FILE="$ENV_FILE" \
    "$APP_DIR/.venv/bin/alembic" -c "$APP_DIR/alembic.ini" upgrade head

# --- 3. Заголовки Nginx -----------------------------------------------------------
log "заголовки безопасности Nginx (CSP с хэшем inline-скрипта темы)"
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
python3 deploy/gen_security_headers.py frontend/dist/index.html --hsts > "$tmp"
if ! cmp -s "$tmp" "$HEADERS_SNIPPET"; then
    cp "$tmp" "$HEADERS_SNIPPET"   # файл принадлежит deploy (bootstrap-server.sh)
    sudo -n /usr/sbin/nginx -t -q || die "nginx -t не прошёл, Nginx не перезагружен"
    sudo -n /usr/bin/systemctl reload nginx.service
    log "Nginx перезагружен"
else
    log "заголовки не изменились"
fi

# --- 4. Перезапуск и проверка -----------------------------------------------------
log "перезапуск loadwatch-api"
sudo -n /usr/bin/systemctl restart loadwatch-api.service
# Демо-цель — только если она включена (bootstrap-server.sh с DEMO_DOMAIN). restart
# запускает и ещё не стартовавшую: при первом деплое кода до этого не было.
if systemctl is-enabled --quiet demo-target.service 2>/dev/null; then
    sudo -n /usr/bin/systemctl restart demo-target.service
fi

log "проверка $HEALTH_URL"
for _ in $(seq 1 30); do
    if body=$(curl -fsS --max-time 3 "$HEALTH_URL" 2>/dev/null); then
        log "API здоров: $body"
        log "готово, версия $(git rev-parse --short HEAD 2>/dev/null || cat .deploy-revision 2>/dev/null || echo '?')"
        exit 0
    fi
    sleep 2
done

log "API не ответил за 60 с. Последние строки журнала:"
journalctl -u loadwatch-api.service -n 40 --no-pager 2>/dev/null || true
die "health check не прошёл"
