# LoadWatch

[![CI](https://github.com/EvgenyBaulin/LoadWatch/actions/workflows/ci.yml/badge.svg)](https://github.com/EvgenyBaulin/LoadWatch/actions/workflows/ci.yml)
[![Deploy](https://github.com/EvgenyBaulin/LoadWatch/actions/workflows/deploy.yml/badge.svg)](https://github.com/EvgenyBaulin/LoadWatch/actions/workflows/deploy.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> **LoadWatch** is a self-hosted service that monitors website performance with scheduled load tests. It verifies site ownership, runs multi-step scenarios (constant, ramp-up and step load) from an asyncio load generator with SSRF protection, keeps the history of p50/p95/p99, error rate, Apdex and capacity, detects regressions against a rolling baseline and alerts via Telegram, e-mail, Slack, Discord or webhooks. Python 3.12 · FastAPI · SQLAlchemy 2 · PostgreSQL · Vue 3. Fits a 1 vCPU / 1 GB VPS.

**Демо:** <https://loadwatch.tarakan-tuc.ru> · документация API (ReDoc): <https://loadwatch.tarakan-tuc.ru/api/v1/redoc> · демо-сайт для тестов: <https://demo.loadwatch.tarakan-tuc.ru>

## Что это

LoadWatch по расписанию нагружает ваш сайт и следит, не стал ли он медленнее. После каждого деплоя (или раз в час) видно: выросла ли задержка p95, сколько ошибок, укладывается ли сайт в SLO и где у него потолок по RPS. Если стало хуже — приходит уведомление.

## Возможности

- **Подтверждение владения** сайтом через DNS TXT или файл на сайте. Без подтверждения тест не запустится ни из API, ни из runner.
- **Сценарии из нескольких шагов**: логин → извлечение токена (JSONPath, regex, заголовок, cookie) → запросы с `{{переменными}}`, проверки ответа, секреты в зашифрованном виде (Fernet), cookie у каждого виртуального пользователя свои. Пробный прогон (dry-run) одного прохода.
- **Профили нагрузки**: постоянная, с разгоном (ramp-up) и ступенчатая с поиском потолка.
- **Метрики**: p50/p90/p95/p99, ошибки по типам, коды ответов, разбивка времени (DNS, TCP, TLS, TTFB, передача), Apdex, всё и по каждому шагу. Генератор сам следит за своим насыщением и помечает недостоверные запуски.
- **Регрессии** относительно медианы последних 5 сопоставимых запусков, **SLO** по p95 и доле ошибок, **алерты** с антиспамом и уведомлением о восстановлении.
- **Уведомления**: Telegram, e-mail, Slack, Discord, webhook с HMAC-подписью; еженедельная сводка.
- **Расписание** (cron с часовым поясом), **триггер из CI** по токену, **метки деплоев** на графиках.
- **Экспорт** в CSV и PDF-отчёт (русский и английский).
- **Интерфейс** в стиле Grafana: светлая и тёмная темы, русский и английский языки.
- **Безопасность**: защита от SSRF с закреплением проверенного IP на весь тест, лимиты частоты и пауза между тестами, проверка владельца на каждом эндпоинте.

## Быстрый старт (локально)

Нужны [uv](https://docs.astral.sh/uv/), Docker и Node.js 24.

```sh
uv sync
docker compose -f docker-compose.dev.yml up -d   # PostgreSQL
# создайте .env с DATABASE_URL и JWT_SECRET (таблица ниже) или задайте их в окружении
uv run alembic upgrade head
uv run uvicorn --factory loadwatch.api.main:create_app --reload   # API на :8000
uv run python -m loadwatch.runner                                 # один тик runner

cd frontend && npm ci && npm run dev                              # интерфейс на :5173
```

Весь стек одной командой (API, runner, фронтенд, демо-сайт): `docker compose up -d --build` → <http://localhost:8080>.

Сценарий показа на демо-сайте: `scripts/demo.sh`.

## Настройки

Читаются из переменных окружения или файла `.env` (на сервере — `/etc/loadwatch/env`). Полный список — класс `Settings` в [`loadwatch/config.py`](loadwatch/config.py).

| Переменная | Обязательна | Назначение |
| --- | :---: | --- |
| `DATABASE_URL` | да | `postgresql+psycopg://user:pass@host:5432/loadwatch` |
| `JWT_SECRET` | да | Не короче 32 символов: `openssl rand -hex 32` |
| `SECRETS_ENCRYPTION_KEY` | для секретов сценариев | Ключ Fernet: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `ENVIRONMENT` | | `development` / `production` |
| `PUBLIC_BASE_URL`, `CORS_ORIGINS` | в проде | Адрес интерфейса (ссылки в уведомлениях) и разрешённые origin |
| `REGISTRATION_MODE` | | `open` / `invite` / `closed`; для `invite` — `REGISTRATION_INVITE_CODE` |
| `RESOURCE_PROFILE` | | `small` / `medium` / `large` — стартовые лимиты под размер VPS |
| `MAX_RPS`, `MAX_CONCURRENCY`, `MAX_DURATION_SEC`, `MAX_BANDWIDTH_MBIT` | | Переопределяют лимиты профиля |
| `ALLOW_PRIVATE_TARGETS` | | `true` только для локального демо: отключает защиту от SSRF |
| `TELEGRAM_BOT_TOKEN` | | Бот для уведомлений в Telegram |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | | Почта для уведомлений и сводок |
| `RETENTION_DAYS` | | Сколько дней хранить посекундные ряды (90) |

## Проверки

```sh
uv run pytest                      # тестам нужен PostgreSQL: база с суффиксом _test
uv run ruff check . && uv run ruff format --check .
uv run mypy loadwatch
cd frontend && npm run lint && npm run type-check && npm run i18n:check && npm run build
```

## Документация

| Документ | О чём |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Процессы, слои, схема БД, путь запуска от очереди до алерта |
| [docs/API.md](docs/API.md) | REST API, коды ошибок, примеры (интерактивно — `/api/v1/redoc`) |
| [docs/SCENARIOS.md](docs/SCENARIOS.md) | Формат сценариев: шаги, переменные, проверки, секреты |
| [docs/METRICS.md](docs/METRICS.md) | Как считаются перцентили, Apdex, SLO, регрессии, потолок |
| [docs/SECURITY.md](docs/SECURITY.md) | SSRF, владение сайтом, секреты, лимиты |
| [docs/DEPLOY.md](docs/DEPLOY.md) | Установка на чистый Ubuntu, автообновление по релизам, Docker |
| [docs/benchmark.md](docs/benchmark.md) | Сколько RPS выдаёт генератор |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Путь к коммерческой версии |

## Лицензия

[MIT](LICENSE) © Evgeny Baulin
