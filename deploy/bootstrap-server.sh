#!/usr/bin/env bash
# Первичная подготовка чистого Ubuntu 24.04 под LoadWatch (основной путь без Docker).
#
# Запуск от root из копии репозитория (нужен каталог deploy/ рядом со скриптом):
#   scp -r deploy root@72.56.86.172:/root/loadwatch-deploy
#   ssh root@72.56.86.172
#   LETSENCRYPT_EMAIL=you@example.com DEPLOY_PUBKEY="ssh-ed25519 AAAA… github-actions" \
#       bash /root/loadwatch-deploy/bootstrap-server.sh
#
# Скрипт идемпотентный: повторный запуск ничего не ломает и НЕ меняет уже
# созданные секреты (пароль БД, JWT_SECRET, ключ шифрования) — иначе пропали бы
# сессии и зашифрованные секреты сценариев. Повторный запуск также обновляет
# unit-файлы, crontab, конфиги Nginx/logrotate/journald/PostgreSQL из deploy/.
#
# Что делает:
#   1. часовой пояс UTC, swap-файл 1 ГБ, пакеты (PostgreSQL, Nginx, certbot…);
#   2. uv в /usr/local/bin (Node на сервер не ставится — фронтенд собирается в CI);
#   3. пользователи: loadwatch (сервисы, без входа) и deploy (вход по SSH-ключу для CI);
#   4. каталоги /opt/loadwatch, /var/log/loadwatch, /etc/loadwatch;
#   5. роль и база PostgreSQL со сгенерированным паролем + настройки под 1 ГБ;
#   6. /etc/loadwatch/env с секретами, сгенерированными здесь же (в репозитории их нет);
#   7. unit-файлы systemd, расписание (cron или таймеры), logrotate, journald;
#   8. Nginx + сертификат Let's Encrypt (webroot) для домена;
#   9. sudoers для deploy: только нужные деплою команды.
# Первый выпуск кода — workflow deploy.yml (или rsync + deploy/deploy.sh руками).
#
# Параметры (переменные окружения):
#   DOMAIN             домен (по умолчанию loadwatch.tarakan-tuc.ru)
#   LETSENCRYPT_EMAIL  почта для уведомлений Let's Encrypt (пусто — без почты)
#   DEPLOY_PUBKEY      публичный SSH-ключ для пользователя deploy (GitHub Actions)
#   SCHEDULER          cron (по умолчанию) или timers — чем запускать runner/digest/maintenance
#   SKIP_CERTBOT=1     не выпускать сертификат (например, DNS ещё не настроен)

set -euo pipefail

DOMAIN=${DOMAIN:-loadwatch.tarakan-tuc.ru}
LETSENCRYPT_EMAIL=${LETSENCRYPT_EMAIL:-}
DEPLOY_PUBKEY=${DEPLOY_PUBKEY:-}
SCHEDULER=${SCHEDULER:-cron}
SKIP_CERTBOT=${SKIP_CERTBOT:-0}
UV_VERSION=0.11.26

APP_DIR=/opt/loadwatch
LOG_DIR=/var/log/loadwatch
CONF_DIR=/etc/loadwatch
ENV_FILE=$CONF_DIR/env
SERVICE_USER=loadwatch
DEPLOY_USER=deploy
DB_NAME=loadwatch
DB_USER=loadwatch
WEBROOT=/var/www/certbot
HEADERS_SNIPPET=/etc/nginx/snippets/loadwatch-headers.conf
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)   # каталог deploy/ с файлами

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'ОШИБКА: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "запускать от root"
[[ -f $SRC/loadwatch-api.service ]] || die "рядом со скриптом нет файлов deploy/"
[[ $SCHEDULER == cron || $SCHEDULER == timers ]] || die "SCHEDULER: cron или timers"
# shellcheck source=/dev/null
. /etc/os-release
[[ ${ID:-} == ubuntu ]] || die "скрипт рассчитан на Ubuntu 24.04 (найдено: ${PRETTY_NAME:-?})"

# --- 1. Система ---------------------------------------------------------------------
log "Часовой пояс UTC (время в БД и в cron — UTC)"
timedatectl set-timezone UTC

log "Swap 1 ГБ (страховка от OOM на 1 ГБ RAM)"
if ! swapon --show=NAME --noheadings | grep -q '^/swapfile$'; then
    if [[ ! -f /swapfile ]]; then
        fallocate -l 1G /swapfile
        chmod 600 /swapfile
        mkswap /swapfile >/dev/null
    fi
    swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
# Swap — только на крайний случай, а не вместо RAM.
cat > /etc/sysctl.d/90-loadwatch.conf <<'EOF'
vm.swappiness = 10
vm.vfs_cache_pressure = 50
EOF
sysctl -q --system

log "Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
    postgresql postgresql-contrib nginx certbot python3-certbot-nginx \
    logrotate cron rsync curl ca-certificates openssl python3 git

log "uv $UV_VERSION в /usr/local/bin"
if ! /usr/local/bin/uv --version 2>/dev/null | grep -q " $UV_VERSION"; then
    curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" \
        | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
fi
/usr/local/bin/uv --version

# --- 2. Пользователи и каталоги ----------------------------------------------------
log "Пользователи $SERVICE_USER и $DEPLOY_USER"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/loadwatch --create-home \
        --shell /usr/sbin/nologin "$SERVICE_USER"
fi
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$DEPLOY_USER"
    passwd -l "$DEPLOY_USER" >/dev/null   # вход только по ключу
fi
# Чтение журнала API при неудачном деплое (journalctl без sudo).
usermod -aG systemd-journal "$DEPLOY_USER"
if [[ -n $DEPLOY_PUBKEY ]]; then
    install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
    keys="/home/$DEPLOY_USER/.ssh/authorized_keys"
    touch "$keys"
    grep -qxF "$DEPLOY_PUBKEY" "$keys" || echo "$DEPLOY_PUBKEY" >> "$keys"
    chown "$DEPLOY_USER:$DEPLOY_USER" "$keys"
    chmod 600 "$keys"
fi

log "Каталоги"
# Код принадлежит deploy и только читается сервисами: сервис не может подменить свой код.
install -d -m 755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_DIR"
install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$LOG_DIR"
install -d -m 750 -o root -g "$SERVICE_USER" "$CONF_DIR"
install -d -m 755 "$WEBROOT"

# --- 3. PostgreSQL ------------------------------------------------------------------
log "PostgreSQL: настройки под 1 ГБ"
PG_VERSION=$(find /etc/postgresql -mindepth 1 -maxdepth 1 -printf "%f\n" | sort -V | tail -n 1)
PG_CONF_D=/etc/postgresql/$PG_VERSION/main/conf.d
install -d "$PG_CONF_D"
if ! cmp -s "$SRC/postgresql-1gb.conf" "$PG_CONF_D/loadwatch.conf"; then
    install -m 644 "$SRC/postgresql-1gb.conf" "$PG_CONF_D/loadwatch.conf"
    systemctl restart postgresql
fi
systemctl enable --quiet postgresql

# Пароль БД: берём из существующего env-файла, иначе генерируем новый.
# Значения секретов никогда не выводятся на экран.
if [[ -f $ENV_FILE ]]; then
    DB_PASSWORD=$(sed -n 's#^DATABASE_URL=postgresql+psycopg://[^:]*:\([^@]*\)@.*#\1#p' "$ENV_FILE")
    [[ -n $DB_PASSWORD ]] || die "в $ENV_FILE не найден пароль в DATABASE_URL"
    NEW_ENV=0
else
    DB_PASSWORD=$(openssl rand -hex 24)   # hex: безопасно и в URL, и в SQL
    NEW_ENV=1
fi

log "PostgreSQL: роль и база $DB_NAME"
# Пароль передаётся через stdin (heredoc), а не аргументом — его не видно в ps.
if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1; then
    runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 <<SQL
CREATE ROLE $DB_USER LOGIN PASSWORD '$DB_PASSWORD';
SQL
elif [[ $NEW_ENV == 1 ]]; then
    # Роль есть, а env-файла нет (его удалили) — синхронизируем пароль с новым файлом.
    runuser -u postgres -- psql -q -v ON_ERROR_STOP=1 <<SQL
ALTER ROLE $DB_USER WITH PASSWORD '$DB_PASSWORD';
SQL
fi
if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1; then
    runuser -u postgres -- createdb -O "$DB_USER" -E UTF8 "$DB_NAME"
fi

# --- 4. Файл окружения ----------------------------------------------------------------
# Формат KEY=value без кавычек: его читают и systemd (EnvironmentFile), и приложение
# (LOADWATCH_ENV_FILE). Права 640 root:loadwatch: задачи cron работают от loadwatch
# и читают файл сами; deploy читать его не может (миграции — через sudo -u loadwatch).
log "Файл окружения $ENV_FILE"
if [[ $NEW_ENV == 1 ]]; then
    JWT_SECRET=$(openssl rand -hex 32)
    SECRETS_KEY=$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')
    INVITE_CODE=$(openssl rand -hex 12)
    (
        umask 077
        cat > "$ENV_FILE" <<EOF
# LoadWatch, продакшен. Создан bootstrap-server.sh $(date -u +%Y-%m-%d).
# Полный список переменных: loadwatch/config.py (класс Settings), README.
# Секреты сгенерированы на сервере; не копируйте этот файл в репозиторий.
# Значения без кавычек и без комментариев в конце строки (формат systemd).
ENVIRONMENT=production
DATABASE_URL=postgresql+psycopg://$DB_USER:$DB_PASSWORD@localhost:5432/$DB_NAME
JWT_SECRET=$JWT_SECRET
# Ключ Fernet для секретов сценариев. Потеря ключа = потеря всех сохранённых секретов.
SECRETS_ENCRYPTION_KEY=$SECRETS_KEY
PUBLIC_BASE_URL=https://$DOMAIN
CORS_ORIGINS=https://$DOMAIN

# Регистрация только по коду приглашения.
REGISTRATION_MODE=invite
REGISTRATION_INVITE_CODE=$INVITE_CODE

# Тариф: small (1 ГБ, 200 Мбит/с) | medium (1 Гбит/с) | large (2 ГБ).
RESOURCE_PROFILE=small
# API_WORKERS=1
# Защита от SSRF: в проде всегда false.
ALLOW_PRIVATE_TARGETS=false
RETENTION_DAYS=90

# Почта (уведомления и сводки). Раскомментируйте и заполните.
# SMTP_HOST=smtp.example.com
# SMTP_PORT=587
# SMTP_USER=
# SMTP_PASSWORD=
# SMTP_FROM=loadwatch@$DOMAIN
# SMTP_STARTTLS=true

# Telegram-бот для уведомлений.
# TELEGRAM_BOT_TOKEN=
EOF
    )
    echo "Создан. Код приглашения: sudo grep REGISTRATION_INVITE_CODE $ENV_FILE"
else
    echo "Уже существует — секреты не меняются."
fi
chown root:"$SERVICE_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

# --- 5. systemd, расписание, логи -----------------------------------------------------
log "Unit-файлы systemd"
for unit in loadwatch-api.service \
            loadwatch-runner.service loadwatch-runner.timer \
            loadwatch-digest.service loadwatch-digest.timer \
            loadwatch-maintenance.service loadwatch-maintenance.timer \
            demo-target.service; do
    install -m 644 "$SRC/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --quiet loadwatch-api.service
# Демо-цель не включается: она требует ALLOW_PRIVATE_TARGETS=true (см. demo-target.service).
if [[ -x $APP_DIR/.venv/bin/uvicorn ]]; then
    systemctl restart loadwatch-api.service
else
    echo "Кода ещё нет — API запустится после первого деплоя (deploy.yml / deploy.sh)."
fi

log "Расписание: $SCHEDULER"
TIMERS=(loadwatch-runner.timer loadwatch-digest.timer loadwatch-maintenance.timer)
if [[ $SCHEDULER == cron ]]; then
    systemctl disable --now --quiet "${TIMERS[@]}" 2>/dev/null || true
    crontab -u "$SERVICE_USER" "$SRC/crontab.example"
    systemctl enable --now --quiet cron
else
    crontab -u "$SERVICE_USER" -r 2>/dev/null || true
    systemctl enable --now --quiet "${TIMERS[@]}"
fi

log "logrotate и journald"
install -m 644 "$SRC/logrotate.d/loadwatch" /etc/logrotate.d/loadwatch
install -d /etc/systemd/journald.conf.d
if ! cmp -s "$SRC/journald-loadwatch.conf" /etc/systemd/journald.conf.d/loadwatch.conf; then
    install -m 644 "$SRC/journald-loadwatch.conf" /etc/systemd/journald.conf.d/loadwatch.conf
    systemctl restart systemd-journald
fi

# --- 6. Nginx и HTTPS -------------------------------------------------------------------
log "Nginx"
rm -f /etc/nginx/sites-enabled/default
install -d /etc/nginx/snippets
# Заголовки безопасности генерирует deploy.sh (с хэшем inline-скрипта); до первого
# деплоя — базовый вариант без хэшей. Файл принадлежит deploy, чтобы деплой мог его менять.
if [[ ! -s $HEADERS_SNIPPET ]]; then
    printf '<html></html>' > /tmp/loadwatch-empty.html
    python3 "$SRC/gen_security_headers.py" /tmp/loadwatch-empty.html --hsts > "$HEADERS_SNIPPET"
    rm -f /tmp/loadwatch-empty.html
fi
chown "$DEPLOY_USER:$DEPLOY_USER" "$HEADERS_SNIPPET"
chmod 644 "$HEADERS_SNIPPET"

CERT=/etc/letsencrypt/live/$DOMAIN/fullchain.pem
SITE=/etc/nginx/sites-available/loadwatch.conf
if [[ ! -f $CERT && $SKIP_CERTBOT != 1 ]]; then
    # Сертификата ещё нет, а полный конфиг ссылается на него — сначала временный
    # HTTP-only сервер только для проверки ACME.
    cat > "$SITE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    location ^~ /.well-known/acme-challenge/ { root $WEBROOT; }
    location / { return 503; }
}
EOF
    ln -sf "$SITE" /etc/nginx/sites-enabled/loadwatch.conf
    nginx -t -q && systemctl reload nginx
    log "Let's Encrypt для $DOMAIN (домен должен уже указывать на этот сервер)"
    email_args=(--register-unsafely-without-email)
    [[ -n $LETSENCRYPT_EMAIL ]] && email_args=(--email "$LETSENCRYPT_EMAIL")
    certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
        --agree-tos --non-interactive "${email_args[@]}"
fi
# После продления (certbot.timer) Nginx перечитывает сертификат.
install -d /etc/letsencrypt/renewal-hooks/deploy
cat > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh <<'EOF'
#!/bin/sh
systemctl reload nginx
EOF
chmod 755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

if [[ -f $CERT ]]; then
    sed "s/loadwatch\.tarakan-tuc\.ru/$DOMAIN/g" "$SRC/nginx.conf" > "$SITE"
    ln -sf "$SITE" /etc/nginx/sites-enabled/loadwatch.conf
    nginx -t -q
    systemctl reload nginx
    systemctl enable --quiet nginx
else
    echo "Сертификата нет (SKIP_CERTBOT=1?) — полный конфиг Nginx не установлен."
fi

# --- 7. sudo для деплоя ------------------------------------------------------------------
log "sudoers для $DEPLOY_USER"
SUDOERS=/etc/sudoers.d/loadwatch-deploy
cat > "$SUDOERS.tmp" <<EOF
# Ровно те команды, что выполняет deploy/deploy.sh. Создано bootstrap-server.sh.
$DEPLOY_USER ALL=($SERVICE_USER) NOPASSWD: /usr/bin/env LOADWATCH_ENV_FILE=$ENV_FILE $APP_DIR/.venv/bin/alembic -c $APP_DIR/alembic.ini upgrade head
$DEPLOY_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart loadwatch-api.service, /usr/bin/systemctl try-restart demo-target.service, /usr/bin/systemctl reload nginx.service, /usr/sbin/nginx -t -q
EOF
visudo -cqf "$SUDOERS.tmp" || die "ошибка в sudoers"
install -m 440 "$SUDOERS.tmp" "$SUDOERS"
rm -f "$SUDOERS.tmp"

log "Готово"
cat <<EOF
Дальше:
  1. GitHub → Settings → Secrets (environment production): DEPLOY_HOST, DEPLOY_USER=$DEPLOY_USER,
     DEPLOY_SSH_KEY, DEPLOY_KNOWN_HOSTS (ssh-keyscan -H $DOMAIN), при нестандартном порте DEPLOY_PORT.
  2. Запустить workflow Deploy (тег v* или вручную) — он выложит код и запустит API.
  3. Проверка: curl -fsS https://$DOMAIN/api/v1/health
  4. Почта/Telegram: отредактировать $ENV_FILE, затем systemctl restart loadwatch-api.
  Фаервол (если нужен): ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable
EOF
