# Архитектура LoadWatch

LoadWatch регулярно нагружает сайты пользователей и следит, не стали ли они медленнее. Сервис состоит из двух процессов и трёх служебных команд. Процессы общаются только через PostgreSQL.

## Процессы

```mermaid
flowchart LR
    user([Браузер]) -->|HTTPS| nginx[Nginx]
    ci([CI после деплоя]) -->|POST /hooks/sites/:id/run| nginx
    nginx -->|статика| spa[Vue SPA<br/>frontend/dist]
    nginx -->|/api/| api[Web API<br/>FastAPI + Uvicorn<br/>systemd]
    api <--> db[(PostgreSQL)]
    cron([cron раз в минуту<br/>flock]) --> runner[Runner<br/>python -m loadwatch.runner]
    runner <--> db
    runner -->|нагрузка на закреплённый IP| site[(Сайт пользователя)]
    runner -->|уведомления| notify{{Telegram · Email · Slack ·<br/>Discord · Webhook}}
    cronh([cron раз в час]) --> digest[python -m loadwatch.digest]
    cronn([cron раз в сутки]) --> maint[python -m loadwatch.maintenance]
    digest <--> db
    digest --> notify
    maint --> db
```

| Компонент | Что делает | Где |
| --- | --- | --- |
| **Web API** | Принимает запросы, проверяет владение сайтом, ставит запуски в очередь (`queued`), отдаёт метрики и отчёты. Тесты не выполняет — кроме лёгкого dry-run одного прохода | `loadwatch/api`, `loadwatch/services` |
| **Runner** | Раз в минуту: переводит зависшие запуски в `failed`, перепроверяет владение, создаёт запуски по расписаниям, выполняет очередь, считает регрессии и SLO, открывает и закрывает алерты | `loadwatch/runner`, `loadwatch/alerts` |
| **Digest** | Раз в час: отправляет еженедельную сводку тем, у кого наступили выбранные день и час | `loadwatch/digest`, `loadwatch/services/digest_service.py` |
| **Maintenance** | Раз в сутки: удаляет посекундные ряды старше `RETENTION_DAYS` | `loadwatch/maintenance.py` |
| **Frontend** | Дашборд в стиле Grafana: Vue 3, Pinia, vue-i18n, Chart.js | `frontend/` |
| **demo_target** | Отдельный демо-сайт для показа: задержки, лимит RPS, логин и защищённая страница | `demo_target/` |

API не хранит состояния между запросами (кроме счётчиков rate limit в памяти), поэтому его можно запускать в несколько воркеров. Runner можно заменить пулом воркеров с очередью: захват запуска уже атомарный (`SELECT … FOR UPDATE SKIP LOCKED` + advisory lock).

## Слои кода

```mermaid
flowchart TB
    routers[api/routers<br/>HTTP, схемы, коды ответов] --> services[services<br/>бизнес-логика, транзакции]
    services --> repositories[repositories<br/>SQL]
    repositories --> models[models<br/>SQLAlchemy 2.x]
    services --> domain[runner · flow · metrics · analysis ·<br/>verification · notifications · security]
```

Роутеры не пишут SQL, репозитории не знают про HTTP. Любое изменение схемы — только миграцией Alembic (`migrations/versions`).

## Как выполняется запуск

```mermaid
sequenceDiagram
    participant API
    participant DB as PostgreSQL
    participant R as Runner
    participant S as Сайт
    API->>DB: INSERT test_runs (queued)
    R->>DB: claim_next (advisory lock, SKIP LOCKED) → running
    R->>R: проверка владения, SSRF: резолв и проверка всех IP
    loop каждую секунду теста
        R->>S: запросы на закреплённый IP (Host + SNI)
        R->>DB: heartbeat раз в 5 с, проверка отмены
    end
    R->>DB: run_metrics, slo_met, regression
    R->>DB: alerts (открыть / повторить / закрыть)
    R-->>R: уведомления в каналы владельца
```

## Диаграмма классов

```mermaid
classDiagram
    class LoadProfile {
        <<abstract>>
        +duration_sec
        +rate_at(elapsed)
        +clamp(limits)
        +observe(second, stats, saturated)
        +stop_reason()
        +result()
    }
    LoadProfile <|-- ConstantLoad
    LoadProfile <|-- RampUpLoad
    LoadProfile <|-- StepLoad

    class OwnershipVerifier {
        <<abstract>>
        +instructions(target, token)
        +verify(target, token)
    }
    OwnershipVerifier <|-- DnsTxtVerifier
    OwnershipVerifier <|-- HttpFileVerifier

    class Notifier {
        <<abstract>>
        +validate(raw)
        +send(message, config)
    }
    Notifier <|-- TelegramNotifier
    Notifier <|-- EmailNotifier
    Notifier <|-- SlackNotifier
    Notifier <|-- DiscordNotifier
    Notifier <|-- WebhookNotifier

    class LoadRunner {
        +run(profile, iteration, on_second)
    }
    class FlowExecutor {
        +run(requester)
    }
    class RequestStep
    class Flow
    class PinnedRequester {
        +request(step, method, path)
    }
    class MetricsCollector {
        +record_request()
        +record_iteration()
        +summary()
        +series()
    }
    class LagMonitor
    class RegressionDetector {
        +detect(current, history)
    }
    class AlertManager {
        +process_run(run_id)
        +site_lost(site_id)
        +site_reverified(site_id)
    }
    class ScheduleService
    class RunService {
        +execute(run_id)
    }

    Flow "1" *-- "1..10" RequestStep
    FlowExecutor --> Flow
    FlowExecutor --> PinnedRequester
    LoadRunner --> LoadProfile
    LoadRunner --> MetricsCollector
    LoadRunner --> LagMonitor
    RunService --> LoadRunner
    RunService --> FlowExecutor
    RunService --> RegressionDetector
    AlertManager --> Notifier
    ScheduleService --> RunService : через очередь
```

Профили, верификаторы и нотификаторы создаются по типу из БД через фабрики/реестры (`profile_from_dict`, `VerifierRegistry`, `NotifierRegistry`).

## ER-диаграмма

```mermaid
erDiagram
    users ||--o{ sites : "владеет"
    users ||--o{ notification_channels : "имеет"
    sites ||--o{ scenarios : ""
    sites ||--o| schedules : ""
    sites ||--o{ test_runs : ""
    sites ||--o{ alerts : ""
    sites ||--o{ annotations : ""
    scenarios ||--o{ scenario_secrets : "зашифрованы"
    scenarios ||--o{ test_runs : "SET NULL при удалении"
    test_runs ||--o| run_metrics : ""

    users {
        uuid id PK
        string email UK
        string password_hash
        string locale
        string timezone
        bool digest_enabled
        smallint digest_weekday
        smallint digest_hour
        timestamptz last_digest_at
    }
    sites {
        uuid id PK
        uuid owner_id FK
        string url "origin"
        string verification_status
        string verification_token
        timestamptz last_verified_at
        string trigger_token_hash
    }
    scenarios {
        uuid id PK
        uuid site_id FK
        string name
        jsonb load_profile
        jsonb flow
        int apdex_t_ms
        int slo_p95_ms
        float slo_max_error_rate
    }
    scenario_secrets {
        uuid id PK
        uuid scenario_id FK
        string name
        text value_encrypted
    }
    schedules {
        uuid id PK
        uuid site_id FK,UK
        uuid scenario_id FK
        string cron
        string timezone
        timestamptz next_run_at
    }
    test_runs {
        uuid id PK
        uuid site_id FK
        uuid scenario_id FK
        string status
        string source
        string label
        string params_hash
        bool generator_saturated
        bool slo_met
        jsonb regression
        timestamptz heartbeat_at
    }
    run_metrics {
        uuid run_id PK,FK
        float latency_p95_ms
        float error_rate
        float rps
        float apdex
        float ceiling_rps
        jsonb summary
        jsonb series
    }
    notification_channels {
        uuid id PK
        uuid user_id FK
        string type
        jsonb config
        text secret_config
        jsonb events
    }
    alerts {
        uuid id PK
        uuid site_id FK
        string type
        string status
        timestamptz opened_at
        timestamptz last_notified_at
        timestamptz resolved_at
    }
    annotations {
        uuid id PK
        uuid site_id FK
        string kind
        string label
        timestamptz at
    }
```

Ключевые индексы и ограничения:

- `test_runs(site_id, started_at)` — история и графики;
- `test_runs(status, queued_at)` — очередь;
- частичный уникальный индекс «один активный запуск на сайт» (`status IN ('queued','running')`);
- «один открытый алерт на сайт и тип»;
- `schedules(enabled, next_run_at)`;
- уникальность `(owner_id, url)` у сайтов и `(site_id, name)` у сценариев.

## Почему так

- **Один процесс-генератор и `asyncio`.** На 1 vCPU потоки не помогут, а event loop с `httpx` держит сотни запросов в секунду при ~65 МБ памяти.
- **Потоковые метрики.** Логарифмические корзины с точностью 1% вместо списка задержек: память не зависит от числа запросов.
- **Sync SQLAlchemy.** Генератор во время теста почти не ходит в базу (heartbeat в фоне), поэтому async-драйвер не нужен, а sync-код проще.
- **Время — UTC везде.** Часовой пояс есть только у расписания и в интерфейсе.
