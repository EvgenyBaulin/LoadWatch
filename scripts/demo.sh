#!/usr/bin/env bash
# Сквозная демонстрация LoadWatch на локальной машине:
# регистрация → demo-сайт → подтверждение владения → многошаговый сценарий
# (главная → логин с секретом → защищённая страница) → dry-run → запуск runner →
# StepLoad против /limited → «держит ~N RPS» → PDF-отчёт.
#
# Нужно: запущенный Postgres из docker-compose.dev.yml (или DEMO_DATABASE_URL),
# uv, curl и python3 (любой 3.x — только для разбора JSON).
# .env не читается: все настройки передаются процессам явно.
#
# Переменные (необязательно):
#   DEMO_DATABASE_URL  база для демо (по умолчанию отдельная loadwatch_demo в Docker)
#   LW_PORT            порт API (по умолчанию 8000 или свободный)
#   DEMO_PORT          порт демо-сайта (по умолчанию 8081 или свободный)
#   DEMO_CAPACITY_RPS  потолок демо-сайта на /limited (по умолчанию 55)
#   DEMO_OUT_DIR       куда положить логи и PDF (по умолчанию временный каталог)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OUT_DIR="${DEMO_OUT_DIR:-$(mktemp -d "${TMPDIR:-/tmp}/loadwatch-demo.XXXXXX")}"
mkdir -p "$OUT_DIR"
CAPACITY="${DEMO_CAPACITY_RPS:-55}"
API_PID=""
DEMO_PID=""

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
fail() { printf '\n\033[31mОшибка: %s\033[0m\n' "$*" >&2; exit 1; }

# Остановка фоновых процессов при любом выходе (успех, ошибка, Ctrl+C).
cleanup() {
  for pid in "$API_PID" "$DEMO_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# Свободен ли TCP-порт на 127.0.0.1.
port_free() {
  python3 -c 'import socket,sys
s=socket.socket()
try:
    s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)' "$1"
}

# Желаемый порт, а если он занят — любой свободный.
pick_port() {
  if port_free "$1"; then
    echo "$1"
  else
    python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'
  fi
}

# Достаёт значение из JSON на stdin по пути через точки: json_get data.token
json_get() {
  python3 -c 'import json,sys
value=json.load(sys.stdin)
for part in sys.argv[1].split("."):
    value=value[int(part)] if isinstance(value,list) else value[part]
print(value if not isinstance(value,(dict,list)) else json.dumps(value))' "$1"
}

# Ждёт, пока URL начнёт отвечать 200.
wait_http() {
  local url="$1" name="$2"
  for _ in $(seq 1 100); do
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then
      return 0
    fi
    sleep 0.2
  done
  fail "$name не поднялся ($url), см. логи в $OUT_DIR"
}

# Запрос к API LoadWatch: api МЕТОД ПУТЬ [JSON]. Тело ответа — в stdout,
# неожиданный HTTP-код — ошибка с текстом ответа.
api() {
  local method="$1" path="$2" data="${3:-}" body_file="$OUT_DIR/last_response.json" code
  local args=(-sS -X "$method" -o "$body_file" -w '%{http_code}' -H 'Accept: application/json')
  [[ -n "${TOKEN:-}" ]] && args+=(-H "Authorization: Bearer $TOKEN")
  [[ -n "$data" ]] && args+=(-H 'Content-Type: application/json' --data "$data")
  code="$(curl "${args[@]}" "$API$path")"
  if [[ "$code" != 2* ]]; then
    fail "$method $path → HTTP $code: $(cat "$body_file")"
  fi
  cat "$body_file"
}

say "Подготовка окружения"
command -v uv >/dev/null || fail "не найден uv"
command -v curl >/dev/null || fail "не найден curl"
command -v python3 >/dev/null || fail "не найден python3"
uv sync --frozen --quiet
PY="$ROOT/.venv/bin/python"

if [[ -n "${DEMO_DATABASE_URL:-}" ]]; then
  DB_URL="$DEMO_DATABASE_URL"
  info "База из DEMO_DATABASE_URL"
else
  COMPOSE=(docker compose -f "$ROOT/docker-compose.dev.yml")
  "${COMPOSE[@]}" exec -T postgres pg_isready -U loadwatch >/dev/null \
    || fail "Postgres не запущен: docker compose -f docker-compose.dev.yml up -d"
  exists="$("${COMPOSE[@]}" exec -T postgres psql -U loadwatch -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = 'loadwatch_demo'")"
  if [[ "$exists" != "1" ]]; then
    "${COMPOSE[@]}" exec -T postgres psql -U loadwatch -d postgres -qc \
      "CREATE DATABASE loadwatch_demo" >/dev/null
    info "Создана база loadwatch_demo"
  fi
  DB_URL="postgresql+psycopg://loadwatch:loadwatch@localhost:5432/loadwatch_demo"
  info "Postgres в Docker доступен, база loadwatch_demo"
fi

LW_PORT="$(pick_port "${LW_PORT:-8000}")"
DEMO_PORT="$(pick_port "${DEMO_PORT:-8081}")"
API="http://127.0.0.1:$LW_PORT/api/v1"
SITE_URL="http://127.0.0.1:$DEMO_PORT"
JWT_SECRET="$("$PY" -c 'import secrets; print(secrets.token_hex(32))')"
FERNET_KEY="$("$PY" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

# Настройки LoadWatch передаются явно каждому процессу; .env не используется.
# Ключ шифрования генерируется заново, поэтому секреты прошлых прогонов демо не читаются —
# это нормально: каждый прогон регистрирует нового пользователя.
LW_ENV=(
  env
  LOADWATCH_ENV_FILE=
  DATABASE_URL="$DB_URL"
  JWT_SECRET="$JWT_SECRET"
  SECRETS_ENCRYPTION_KEY="$FERNET_KEY"
  ALLOW_PRIVATE_TARGETS=true
  SITE_COOLDOWN_SEC=0
  RESOURCE_PROFILE=small
)

say "Миграции базы"
"${LW_ENV[@]}" "$PY" -m alembic upgrade head 2>&1 | sed 's/^/    /'

say "Запуск API LoadWatch на порту $LW_PORT"
"${LW_ENV[@]}" "$PY" -m uvicorn --factory loadwatch.api.main:create_app --host 127.0.0.1 --port "$LW_PORT" \
  >"$OUT_DIR/api.log" 2>&1 &
API_PID=$!
wait_http "$API/health" "API"
info "API отвечает: $API (лог: $OUT_DIR/api.log)"

# Демо-сайт запускается функцией: после получения токена его перезапускаем с ним.
start_demo() {
  local verify_token="${1:-}"
  env DEMO_HOST=127.0.0.1 DEMO_PORT="$DEMO_PORT" DEMO_CAPACITY_RPS="$CAPACITY" \
    DEMO_VERIFY_TOKEN="$verify_token" "$PY" -m demo_target >>"$OUT_DIR/demo_target.log" 2>&1 &
  DEMO_PID=$!
  wait_http "$SITE_URL/health" "demo_target"
}

say "Запуск демо-сайта на порту $DEMO_PORT (потолок /limited: $CAPACITY RPS)"
start_demo ""
info "Демо-сайт отвечает: $SITE_URL"

say "Регистрация пользователя"
EMAIL="demo-$(date +%Y%m%d%H%M%S)-$RANDOM@example.com"
TOKEN="$(api POST /auth/register \
  "{\"email\":\"$EMAIL\",\"password\":\"demo-password-123\",\"locale\":\"ru\"}" \
  | json_get access_token)"
info "Пользователь: $EMAIL"

say "Добавление сайта $SITE_URL"
SITE_ID="$(api POST /sites "{\"url\":\"$SITE_URL\",\"name\":\"Demo target\"}" | json_get id)"
info "site_id=$SITE_ID"

say "Подтверждение владения (HTTP-файл)"
VERIFY_TOKEN="$(api GET "/sites/$SITE_ID/verification" | json_get token)"
info "Токен: $VERIFY_TOKEN — перезапускаем демо-сайт с файлом /.well-known/loadwatch-<token>.txt"
kill "$DEMO_PID" && wait "$DEMO_PID" 2>/dev/null || true
start_demo "$VERIFY_TOKEN"
STATUS="$(api POST "/sites/$SITE_ID/verify" '{"method":"http"}' | json_get verification_status)"
[[ "$STATUS" == "verified" ]] || fail "сайт не подтверждён: $STATUS"
info "Статус: $STATUS"

say "Многошаговый сценарий: главная → логин (секрет PASSWORD) → защищённая страница"
FLOW_SCENARIO='{
  "name": "Главная → логин → кабинет",
  "load_profile": {"type": "constant", "rate": 5, "duration_sec": 10},
  "flow": [
    {"name": "Главная", "method": "GET", "path": "/", "expected_status": ["200"]},
    {"name": "Логин", "method": "POST", "path": "/api/login",
     "body": {"type": "json", "content": {"username": "demo", "password": "{{secret.PASSWORD}}"}},
     "expected_status": ["200"],
     "extract": [{"var": "token", "source": "json", "expr": "data.token"}]},
    {"name": "Кабинет", "method": "GET", "path": "/dashboard",
     "headers": {"Authorization": "Bearer {{token}}"},
     "expected_status": ["200"], "assert_contains": "Welcome"}
  ],
  "slo_p95_ms": 500,
  "slo_max_error_rate": 0.01
}'
FLOW_ID="$(api POST "/sites/$SITE_ID/scenarios" "$FLOW_SCENARIO" | json_get id)"
MASKED="$(api PUT "/scenarios/$FLOW_ID/secrets" '{"secrets":{"PASSWORD":"demo-password"}}' \
  | json_get secrets.0.value)"
info "scenario_id=$FLOW_ID, секрет PASSWORD сохранён (в ответе API только маска: $MASKED)"

say "Dry-run: один проход потока"
api POST "/scenarios/$FLOW_ID/dry-run" >"$OUT_DIR/dry-run.json"
python3 - "$OUT_DIR/dry-run.json" <<'PY' || fail "dry-run не прошёл"
import json, sys
d = json.load(open(sys.argv[1]))
for s in d["steps"]:
    mark = "ok  " if s["ok"] else "FAIL"
    extra = ""
    if s["extracted"]:
        extra = ", извлечено: " + ", ".join(f"{k}={v[:6]}…" for k, v in s["extracted"].items())
    print(f"    [{mark}] {s['name']}: {s['method']} {s['path']} → {s['status']}, "
          f"{s['duration_ms']:.1f} мс{extra}")
print("    Итог:", "успех" if d["ok"] else "ошибка " + str(d.get("error")))
sys.exit(0 if d["ok"] else 1)
PY

# Запускает runner (один тик: выполняет очередь) и печатает итог запуска.
run_and_report() {
  local run_id="$1"
  info "Запуск в очереди: run_id=$run_id; выполняем python -m loadwatch.runner"
  "${LW_ENV[@]}" "$PY" -m loadwatch.runner >>"$OUT_DIR/runner.log" 2>&1 \
    || fail "runner завершился с ошибкой, см. $OUT_DIR/runner.log"
  api GET "/runs/$run_id" >"$OUT_DIR/run-$run_id.json"
}

say "Нагрузочный прогон многошагового сценария (5 проходов/с, 10 с)"
RUN1="$(api POST "/sites/$SITE_ID/runs" "{\"scenario_id\":\"$FLOW_ID\",\"label\":\"demo flow\"}" \
  | json_get id)"
run_and_report "$RUN1"
python3 - "$OUT_DIR/run-$RUN1.json" <<'PY'
import json, sys
run = json.load(open(sys.argv[1]))
m = run["metrics"] or {}
print(f"    Статус: {run['status']} ({run['stop_reason']}), запросов: {m.get('requests')}, "
      f"RPS: {m.get('rps')}, p95: {m.get('latency_p95_ms')} мс, ошибки: {m.get('error_rate')}, "
      f"SLO выполнено: {run['slo_met']}")
if run["status"] != "finished":
    sys.exit(f"run failed: {run.get('error')}")
PY

say "StepLoad против /limited: ступени +10 RPS по 5 с, порог ошибок 5 %"
STEP_SCENARIO='{
  "name": "Поиск потолка /limited",
  "load_profile": {"type": "step", "start_rate": 10, "step_rate": 10, "step_duration_sec": 5,
                   "max_steps": 10, "max_error_rate": 0.05},
  "flow": [{"name": "limited", "method": "GET", "path": "/limited", "expected_status": ["200"]}]
}'
STEP_ID="$(api POST "/sites/$SITE_ID/scenarios" "$STEP_SCENARIO" | json_get id)"
RUN2="$(api POST "/sites/$SITE_ID/runs" "{\"scenario_id\":\"$STEP_ID\",\"label\":\"step load\"}" \
  | json_get id)"
run_and_report "$RUN2"
python3 - "$OUT_DIR/run-$RUN2.json" <<'PY'
import json, sys
run = json.load(open(sys.argv[1]))
if run["status"] != "finished":
    sys.exit(f"run failed: {run.get('error')}")
summary = run.get("summary") or {}
result = summary.get("step_load") or {}
print("    Ступень  цель RPS  факт RPS  ошибки   p95, мс  прошла")
for step in result.get("steps", []):
    print(f"    {step['step']:>7}  {step['target_rate']:>8}  {step['rps']:>8}  "
          f"{step['error_rate']:>6.1%}  {step['p95_ms']:>8}  {'да' if step['passed'] else 'нет'}")
ceiling = (run.get("metrics") or {}).get("ceiling_rps") or result.get("ceiling_rps")
print(f"    Остановка: {run['stop_reason']}, генератор насыщен: {run['generator_saturated']}")
if ceiling is None:
    sys.exit("ceiling_rps is not defined")
print(f"\n    Результат: сайт держит ~{round(ceiling)} RPS")
PY

say "PDF-отчёт по StepLoad"
PDF="$OUT_DIR/loadwatch-run-$RUN2.pdf"
curl -fsS -H "Authorization: Bearer $TOKEN" -o "$PDF" "$API/runs/$RUN2/report.pdf?lang=ru"
[[ "$(head -c 5 "$PDF")" == "%PDF-" ]] || fail "отчёт не похож на PDF"
info "Отчёт: $PDF ($(wc -c <"$PDF" | tr -d ' ') байт)"
info "Логи: $OUT_DIR/{api,demo_target,runner}.log"

say "Готово"
