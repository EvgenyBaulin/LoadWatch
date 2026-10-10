# Развёртывание

Основной путь — **без Docker**: systemd + таймеры + Nginx на одном VPS (1 vCPU, 1 ГБ RAM). Docker-вариант описан в конце: он удобен на 2 ГБ RAM или для переноса «как есть».

Рабочий пример: <https://loadwatch.tarakan-tuc.ru> (Ubuntu 26.04, 1 vCPU AMD EPYC, 889 МБ RAM) и демо-сайт <https://demo.loadwatch.tarakan-tuc.ru>.

## Что получится

```
Интернет ─ Nginx :443 ─┬─ /            → frontend/dist (статика)
                       ├─ /api/        → loadwatch-api (uvicorn, 127.0.0.1:8000)
                       └─ demo.<домен> → demo-target (127.0.0.1:8081)
systemd timers: loadwatch-runner (каждую минуту), loadwatch-digest (раз в час),
                loadwatch-maintenance (раз в сутки)
PostgreSQL (локально), секреты — /etc/loadwatch/env (640 root:loadwatch)
```

| Путь | Что там |
| --- | --- |
| `/opt/loadwatch` | Код (владелец `deploy`, сервисы только читают) |
| `/opt/uv-python` | Python 3.12 от uv (в Ubuntu 26.04 системный Python — 3.14) |
| `/etc/loadwatch/env` | Настройки и секреты, создаются на сервере и никуда не копируются |
| `/etc/loadwatch/demo.env` | Необязательно: настройки демо-сайта |
| `/var/log/loadwatch` | Логи runner при запуске из cron (с таймерами — в journald) |

## 1. Подготовка сервера

Нужны: чистый Ubuntu 24.04 или новее, доступ по SSH под root, A-записи домена (и поддомена демо) на IP сервера.

```sh
# на своей машине: ключ, которым GitHub Actions будет заходить на сервер
ssh-keygen -t ed25519 -N "" -C github-actions-deploy -f deploy_key

scp -r deploy root@<IP>:/root/loadwatch-deploy
ssh root@<IP> "LETSENCRYPT_EMAIL=you@example.com DEPLOY_PUBKEY='$(cat deploy_key.pub)' \
    SCHEDULER=timers bash /root/loadwatch-deploy/bootstrap-server.sh"
```

Параметры `bootstrap-server.sh`:

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `DOMAIN` | `loadwatch.tarakan-tuc.ru` | Домен интерфейса и API |
| `DEMO_DOMAIN` | `demo.<DOMAIN>` | Поддомен демо-сайта; пустое значение — без демо |
| `LETSENCRYPT_EMAIL` | — | Почта для уведомлений Let's Encrypt |
| `DEPLOY_PUBKEY` | — | Публичный ключ для пользователя `deploy` |
| `SCHEDULER` | `cron` | `timers` (рекомендуется) или `cron` |
| `SKIP_CERTBOT` | `0` | `1` — не выпускать сертификаты (DNS ещё не настроен) |

Скрипт идемпотентный: повторный запуск обновляет unit-файлы и конфиги из `deploy/`, но **не трогает уже созданные секреты** (пароль БД, `JWT_SECRET`, ключ шифрования). Что он делает:

1. часовой пояс UTC, swap 1 ГБ, пакеты (PostgreSQL, Nginx, certbot);
2. uv и Python 3.12 в `/opt/uv-python`;
3. пользователи `loadwatch` (сервисы, без входа) и `deploy` (вход только по ключу);
4. база и роль PostgreSQL со случайным паролем, настройки PostgreSQL под 1 ГБ;
5. `/etc/loadwatch/env` со сгенерированными `JWT_SECRET`, `SECRETS_ENCRYPTION_KEY` и кодом приглашения; регистрация — только по коду (`REGISTRATION_MODE=invite`), `ALLOW_PRIVATE_TARGETS=false`;
6. unit-файлы и таймеры systemd, logrotate, лимит журнала journald;
7. Nginx и сертификаты Let's Encrypt для домена и демо-поддомена (продление — `certbot.timer`, после продления Nginx перечитывает сертификат);
8. sudoers для `deploy`: ровно те команды, что нужны деплою.

## 2. Автообновление по релизам

Workflow `.github/workflows/deploy.yml` запускается при **публикации релиза** на GitHub: прогоняет CI, собирает фронтенд, копирует код на сервер (`rsync`), выполняет `deploy/deploy.sh` (зависимости по `uv.lock`, миграции, заголовки Nginx, перезапуск API и демо, проверка `/api/v1/health`) и публикует Docker-образы в GHCR. Черновики и пре-релизы на сервер не выкладываются.

Секреты окружения `production` (Settings → Environments):

| Секрет | Значение |
| --- | --- |
| `DEPLOY_HOST` | IP или домен сервера |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | Приватный ключ `deploy_key` |
| `DEPLOY_KNOWN_HOSTS` | Вывод `ssh-keyscan -H <IP>` (защита от подмены сервера) |
| `DEPLOY_PORT` | Необязательно, если SSH не на 22 |

```sh
gh secret set DEPLOY_SSH_KEY -e production < deploy_key
ssh-keyscan -H <IP> | gh secret set DEPLOY_KNOWN_HOSTS -e production
```

Выпуск новой версии:

```sh
gh release create v0.2.0 --generate-notes     # или Releases → Draft a new release
```

Через несколько минут сервер работает на новой версии: `curl https://<домен>/api/v1/health` вернёт `"version"` из `pyproject.toml`. Ручной запуск — Actions → Deploy → Run workflow.

Секреты приложения в GitHub не хранятся. Чтобы включить почту или Telegram, отредактируйте `/etc/loadwatch/env` на сервере и выполните `systemctl restart loadwatch-api` (runner и сводка подхватят файл сами при следующем запуске).

## 3. Первый вход

```sh
ssh root@<IP> 'grep REGISTRATION_INVITE_CODE /etc/loadwatch/env'
```

Откройте сайт, зарегистрируйтесь с этим кодом. Код можно поменять в том же файле (после — `systemctl restart loadwatch-api`) или закрыть регистрацию (`REGISTRATION_MODE=closed`).

## 4. Демо-сайт

`demo_target` работает на `127.0.0.1:8081`, а снаружи доступен через Nginx по HTTPS на поддомене. LoadWatch тестирует его как обычный внешний сайт, поэтому защиту от SSRF ослаблять не нужно.

Чтобы подтвердить владение демо-сайтом:

1. В LoadWatch добавьте сайт `https://demo.<домен>` и на вкладке «Подтверждение» скопируйте токен.
2. На сервере:
   ```sh
   echo 'DEMO_VERIFY_TOKEN=<токен>' > /etc/loadwatch/demo.env
   chown root:loadwatch /etc/loadwatch/demo.env && chmod 640 /etc/loadwatch/demo.env
   systemctl restart demo-target
   ```
3. Нажмите «Проверить» способом «Файл на сайте».

Там же можно задать `DEMO_CAPACITY_RPS` (потолок `/limited`, по умолчанию 50), `DEMO_LATENCY_MS` (базовая задержка) и `DEMO_ADMIN_TOKEN` (управление задержкой через `/admin/latency` — удобно показывать регрессию). Логин демо: `demo` / `demo-password` (`POST /api/login` → `data.token`).

## 5. Профили ресурсов

`RESOURCE_PROFILE` задаёт стартовые лимиты. Пользователь может их только сужать в своих сценариях; администратор — переопределить любой (`MAX_RPS=…` и т. д.).

| Профиль | Сервер | `MAX_RPS` | `MAX_CONCURRENCY` | `MAX_DURATION_SEC` | `MAX_BANDWIDTH_MBIT` | Воркеров API |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `small` | 1 vCPU, 1 ГБ, канал 200 Мбит/с | 150 | 100 | 120 | 100 | 1 |
| `medium` | 1 vCPU, 1 ГБ, канал 1 Гбит/с | 150 | 100 | 120 | 500 | 1 |
| `large` | 1 vCPU, 2 ГБ | 200 | 200 | 180 | 100 | 2 |

Одновременно идёт один тест (`MAX_GLOBAL_CONCURRENT_RUNS=1`): на одном ядре два теста мешали бы друг другу и оба дали бы недостоверные цифры.

## 6. Бенчмарк на VPS

`scripts/benchmark_runner.py` на рабочем сервере (Ubuntu 26.04, 1 vCPU AMD EPYC-Rome, Python 3.12, `MAX_CONCURRENCY=100`, 10 с на частоту). Демо-цель и генератор делят одно ядро, поэтому это оценка снизу.

```sh
ssh root@<IP> 'cd /opt/loadwatch && runuser -u deploy -- .venv/bin/python \
    scripts/benchmark_runner.py --rates 50,100,150,200,300,400 --concurrency 100 --output /tmp/bench.md'
```

| Цель, RPS | Факт, RPS | p50, мс | p95, мс | Сброшено | Лаг loop p95, мс | Насыщен |
| ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| 25 | 24.9 | 1.94 | 3.04 | 0 | 1.2 | нет |
| 50 | 49.9 | 2.10 | 3.36 | 0 | 1.2 | нет |
| 100 | 99.9 | 1.90 | 4.57 | 0 | 1.1 | нет |
| 150 | 149.9 | 1.64 | 2.28 | 0 | 1.1 | нет |
| 200 | 199.8 | 1.67 | 3.19 | 0 | 1.1 | нет |
| 300 | 215.8–299.2 | 3.13 | 284–1288 | 6–840 | 13.9–57.8 | да |
| 400 | 399.7 | 2.75 | 4.75–6.54 | 0 | 1.4–2.2 | нет |
| 500 | 332.5 | — | 760 | 1610 | 17.7 | да |
| 600 | 359.4 | — | 695 | 2342 | 13.9 | да |
| 800 | 321.0 | — | 791 | 4709 | 16.7 | да |

Выводы:

- **До 200 RPS генератор работает стабильно** во всех прогонах; выше результаты скачут от прогона к прогону (на виртуальном ядре сказываются соседи по хосту), а с 500 RPS генератор стабильно не справляется.
- Каждый перегруженный прогон помечен `generator_saturated` — такие цифры в интерфейсе и PDF отмечены как недостоверные и не портят базовую линию регрессий.
- Память: генератор ~52–54 МБ, демо-цель ~50 МБ; весь сервер с PostgreSQL, Nginx и API занимает ~550 МБ из 889.
- `MAX_RPS=150` для профиля `small` оставляет запас ~25% до стабильной границы. Поднимать выше 200 на таком VPS не стоит.
- Больший пул соединений не помогает: на локальном прогоне (`docs/benchmark.md`) `MAX_CONCURRENCY=500` у границы давал p95 в разы хуже, чем 100.

## 7. Эксплуатация

```sh
systemctl status loadwatch-api demo-target
systemctl list-timers 'loadwatch-*'
journalctl -u loadwatch-api -f                 # API
journalctl -u loadwatch-runner -n 50           # тики runner
sudo -u postgres pg_dump loadwatch > backup.sql   # резервная копия
```

- **Откат:** Actions → Deploy → Run workflow на нужном теге. Миграции вниз автоматически не выполняются.
- **Ключ шифрования** (`SECRETS_ENCRYPTION_KEY`) храните отдельно: без него сохранённые секреты сценариев не расшифровать.
- **Фаервол** (если нужен): `ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw enable`.

## 8. Docker (альтернатива)

Локально весь стек: `docker compose up -d --build` → <http://localhost:8080>.

На сервере — `docker-compose.prod.yml` за Nginx хоста (`deploy/nginx-docker.conf`). Образы публикуются в GHCR при каждом релизе (`linux/amd64` и `linux/arm64`):

```sh
LOADWATCH_IMAGE_PREFIX=ghcr.io/evgenybaulin/ LOADWATCH_TAG=0.1.0 \
    docker compose -f docker-compose.prod.yml up -d --no-build
```

Настройки — в `/etc/loadwatch/docker.env` (список обязательных переменных — в шапке `docker-compose.prod.yml`). На 1 ГБ RAM Docker-вариант работает на пределе (демон и shim-процессы занимают ~100 МБ), поэтому там рекомендуется путь без Docker.
