# REST API

Префикс — `/api/v1`. Полная интерактивная документация:

- **ReDoc** — `/api/v1/redoc` (основная);
- **Swagger UI** — `/api/v1/docs` (пробовать запросы);
- схема OpenAPI — `/api/v1/openapi.json`.

## Общие правила

- **Аутентификация:** `POST /auth/login` → `access_token`. Дальше — заголовок `Authorization: Bearer <token>`. Исключения: `/health`, `/auth/register`, `/auth/login` и триггер из CI (у него свой токен).
- **Формат ошибок** — всегда один:
  ```json
  {"error": {"code": "cooldown_active", "message": "Too soon after the previous test of this site", "details": {"retry_after": 120}}}
  ```
  `code` — стабильный машинный код: фронтенд переводит по нему, текст `message` — только для разработчиков. При `rate_limited` и `cooldown_active` есть ещё заголовок `Retry-After`.
- **Пагинация списков:** `?limit=20&offset=0` (limit ≤ 100), ответ `{"items": […], "total": N, "limit": 20, "offset": 0}`.
- **Время** — ISO 8601 в UTC. В фильтрах периода (`from`, `to`) часовой пояс обязателен: `2026-01-01T00:00:00Z`.
- **Чужие объекты** неотличимы от несуществующих: 404 `not_found`.

## Коды ошибок

| HTTP | `code` | Когда |
| --- | --- | --- |
| 401 | `not_authenticated` | Нет токена или он недействителен |
| 401 | `invalid_credentials` | Неверный e-mail или пароль |
| 401 | `invalid_trigger_token` | Неверный токен триггера CI |
| 403 | `registration_closed`, `invalid_invite_code` | Режим регистрации |
| 404 | `not_found` | Объекта нет или он чужой |
| 409 | `email_taken`, `site_exists`, `scenario_exists` | Дубликат |
| 409 | `site_not_verified` | Сайт не подтверждён |
| 409 | `run_in_progress` | У сайта уже есть тест в очереди или в работе |
| 409 | `missing_secrets` | Сценарий использует незаданные секреты |
| 409 | `run_not_cancellable`, `run_has_no_metrics` | Состояние запуска |
| 422 | `validation_error` | Неверные данные (`details[].loc`, `details[].msg`; введённые значения не возвращаются) |
| 422 | `invalid_site_url`, `target_forbidden`, `target_unresolvable` | Адрес сайта |
| 422 | `verification_failed` | `details.reason`: `token_not_found` / `target_forbidden` / `error` |
| 422 | `invalid_schedule`, `invalid_period`, `invalid_channel`, `scenario_required`, `export_too_large` | Параметры |
| 429 | `rate_limited`, `cooldown_active` | Лимиты частоты, пауза между тестами |
| 503 | `secrets_unavailable` | На сервере не задан `SECRETS_ENCRYPTION_KEY` |
| 500 | `internal_error` | Внутренняя ошибка (подробности только в логе сервера) |

## Эндпоинты

### Сервис и пользователь

| Метод | Путь | Описание |
| --- | --- | --- |
| GET | `/health` | `{"status":"ok","database":"ok"}`; 503, если база недоступна |
| POST | `/auth/register` | `{email, password, invite_code?, locale?}` → токен и пользователь |
| POST | `/auth/login` | `{email, password}` → токен и пользователь |
| GET | `/auth/me` | Текущий пользователь |
| PUT | `/users/me/preferences` | `{locale?, timezone?, digest_enabled?, digest_weekday?, digest_hour?}` |

### Сайты и владение

| Метод | Путь | Описание |
| --- | --- | --- |
| POST | `/sites` | `{url, name?}` — origin вида `https://example.com` |
| GET | `/sites` | Список (пагинация) |
| GET | `/sites/{id}` | Сайт |
| DELETE | `/sites/{id}` | Удалить вместе со сценариями и историей |
| GET | `/sites/{id}/verification` | Токен и инструкции для DNS и HTTP |
| POST | `/sites/{id}/verify` | `{method: "dns" \| "http"}` |

### Сценарии

| Метод | Путь | Описание |
| --- | --- | --- |
| POST | `/sites/{id}/scenarios` | Создать (формат — [SCENARIOS.md](SCENARIOS.md)) |
| GET | `/sites/{id}/scenarios` | Список |
| GET / PUT / DELETE | `/scenarios/{id}` | Прочитать, заменить, удалить |
| PUT | `/scenarios/{id}/secrets` | `{"secrets": {"NAME": "value" \| null}}` — только запись |
| POST | `/scenarios/{id}/dry-run` | Один проход потока синхронно; результат по шагам |

### Запуски и расписание

| Метод | Путь | Описание |
| --- | --- | --- |
| POST | `/sites/{id}/runs` | `{scenario_id, label?}` → запуск в очереди (`queued`) |
| GET | `/sites/{id}/runs` | История: `?status=&scenario_id=&source=&limit=&offset=` |
| GET | `/runs/{id}` | Запуск со сводкой, рядом, параметрами и регрессией |
| POST | `/runs/{id}/cancel` | Из очереди — сразу, идущий — остановится за секунды |
| GET / PUT | `/sites/{id}/schedule` | `{scenario_id, cron, timezone, enabled}`; GET без расписания → `null` |

### Дашборд, метки, алерты

| Метод | Путь | Описание |
| --- | --- | --- |
| GET | `/sites/{id}/metrics` | `?from=&to=&scenario_id=` — точки по завершённым запускам |
| GET | `/sites/{id}/summary` | `?days=30` — последний запуск, базовая линия, регрессия, потолок, % в SLO |
| GET / POST | `/sites/{id}/annotations` | Метки (деплои и комментарии) |
| DELETE | `/sites/{id}/annotations/{annotation_id}` | Удалить метку |
| GET | `/sites/{id}/alerts` | `?status=open\|resolved` |

### Экспорт

| Метод | Путь | Описание |
| --- | --- | --- |
| GET | `/runs/{id}/export.csv` | Метрики запуска по шагам |
| GET | `/sites/{id}/export.csv` | Запуски сайта за `?from=&to=` (по умолчанию 30 дней) |
| GET | `/runs/{id}/report.pdf` | `?lang=ru\|en` (по умолчанию — язык пользователя) |

### Уведомления

| Метод | Путь | Описание |
| --- | --- | --- |
| GET / POST | `/users/me/channels` | Каналы: `telegram`, `email`, `slack`, `discord`, `webhook` |
| PUT / DELETE | `/users/me/channels/{id}` | Изменить, удалить |
| POST | `/users/me/channels/{id}/test` | Тестовое уведомление → `{ok, error}` |

### Триггер из CI

| Метод | Путь | Описание |
| --- | --- | --- |
| POST | `/sites/{id}/trigger-token` | Выпустить или перевыпустить токен (показывается один раз) |
| POST | `/hooks/sites/{id}/run` | `Authorization: Bearer <токен>`, тело `{label?, scenario_id?}` |

## Примеры

Вход и список сайтов:

```sh
B=https://loadwatch.example/api/v1
TOKEN=$(curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"…"}' | jq -r .access_token)
curl -s $B/sites -H "Authorization: Bearer $TOKEN"
```

Запуск теста после деплоя (GitHub Actions):

```yaml
- name: Load test after deploy
  run: |
    curl -fsS -X POST "https://loadwatch.example/api/v1/hooks/sites/${{ vars.LOADWATCH_SITE_ID }}/run" \
      -H "Authorization: Bearer ${{ secrets.LOADWATCH_TOKEN }}" \
      -H "Content-Type: application/json" \
      -d "{\"label\": \"${GITHUB_REF_NAME}\"}"
```

Проверка подписи generic webhook (Python):

```python
import hashlib, hmac


def valid(body: bytes, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```
