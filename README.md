# LoadWatch

> Website performance monitoring through scheduled load tests. *(Full README is written in phase 11.)*

Сервис мониторинга производительности сайтов через регулярные нагрузочные тесты. Проект в разработке: готовы конфигурация, база данных и регистрация с входом.

```sh
uv sync
docker compose -f docker-compose.dev.yml up -d
uv run alembic upgrade head
uv run uvicorn --factory loadwatch.api.main:create_app --reload
# Swagger: http://127.0.0.1:8000/api/v1/docs
```
