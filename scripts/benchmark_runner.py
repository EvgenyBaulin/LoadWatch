"""Бенчмарк генератора: сколько RPS LoadWatch реально выдаёт на этой машине.

Скрипт запускает ``demo_target`` отдельным процессом на свободном порту
(с практически бесконечной ёмкостью), затем для каждой целевой частоты гоняет
``LoadRunner`` с постоянной нагрузкой на ``GET /`` и записывает фактический RPS,
p95, долю ошибок, лаг event loop, флаг насыщения генератора и память.

Запуск::

    uv run python scripts/benchmark_runner.py                  # таблица + docs/benchmark.md
    uv run python scripts/benchmark_runner.py --rates 50,100 --duration 5 --no-write

Демо-цель и генератор делят один процессор, поэтому цифры ориентировочные:
на отдельной машине с целью генератор выдаст больше.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import resource
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from loadwatch.config import Settings
from loadwatch.runner.generator import LoadRunner, RunLimits, simple_get
from loadwatch.runner.http import PinnedRequester, new_load_client
from loadwatch.runner.profiles import ConstantLoad
from loadwatch.security.ssrf import parse_target_url, resolve_target

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RATES = (50, 100, 150, 200, 300, 400, 600, 800, 1200, 1600)
DEFAULT_OUTPUT = ROOT / "docs" / "benchmark.md"
DEFAULT_CONCURRENCY = (100, 500)  # small-профиль и «щедрый» пул
# Пороги насыщения — значения по умолчанию из настроек, как у runner.
_DEFAULTS = {name: field.default for name, field in Settings.model_fields.items()}
LAG_THRESHOLD_MS: float = _DEFAULTS["generator_lag_threshold_ms"]
SATURATION_SECONDS: int = _DEFAULTS["generator_saturation_seconds"]
CPU_THRESHOLD: float = _DEFAULTS["generator_cpu_threshold"]


@dataclass(frozen=True, slots=True)
class BenchRow:
    """Результат одного прогона на одной целевой частоте."""

    target_rps: float
    achieved_rps: float
    requests: int
    p50_ms: float
    p95_ms: float
    error_rate: float
    dropped: int
    lag_p95_ms: float
    saturated: bool
    generator_rss_mb: float
    target_rss_mb: float


def free_port() -> int:
    """Свободный TCP-порт на 127.0.0.1."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def rss_mb(pid: int) -> float:
    """Текущий RSS процесса в мегабайтах (через ``ps``: работает в macOS и Linux)."""
    out = subprocess.run(  # noqa: S603 — фиксированная команда без пользовательского ввода
        ["ps", "-o", "rss=", "-p", str(pid)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return round(int(out) / 1024, 1) if out else 0.0


def peak_rss_mb() -> float:
    """Пиковый RSS текущего процесса (``ru_maxrss``: байты в macOS, КБ в Linux)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(peak / divisor, 1)


def cpu_model() -> str:
    """Модель процессора, насколько её можно узнать без сторонних библиотек."""
    if sys.platform == "darwin":
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout.strip():
            return result.stdout.strip()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine()


def start_target(port: int) -> subprocess.Popen[bytes]:
    """Запускает demo_target с огромной ёмкостью и ждёт ``/health``."""
    env = {
        **os.environ,
        "DEMO_HOST": "127.0.0.1",
        "DEMO_PORT": str(port),
        "DEMO_CAPACITY_RPS": "1000000",
        "DEMO_LATENCY_MS": "0",
    }
    process = subprocess.Popen([sys.executable, "-m", "demo_target"], cwd=ROOT, env=env)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"demo_target exited with code {process.returncode}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1):
                return process
        except OSError:
            time.sleep(0.2)
    process.terminate()
    raise RuntimeError("demo_target did not start in 20 seconds")


async def run_rate(
    port: int, target_pid: int, rate: float, duration: float, concurrency: int
) -> BenchRow:
    """Один прогон постоянной нагрузки ``rate`` проходов в секунду на ``GET /``."""
    target = parse_target_url(f"http://127.0.0.1:{port}", allow_private=True)
    pinned = resolve_target(target, resolver=lambda host, p: ["127.0.0.1"], allow_private=True)
    limits = RunLimits(
        max_concurrency=concurrency,
        max_bandwidth_mbit=10_000,
        lag_threshold_ms=LAG_THRESHOLD_MS,
        saturation_seconds=SATURATION_SECONDS,
        cpu_threshold=CPU_THRESHOLD,
        drain_timeout_sec=10,
    )
    target_rss = 0.0

    async def on_second(second: int) -> bool:
        """Запоминает пиковую память демо-цели во время теста."""
        nonlocal target_rss
        target_rss = max(target_rss, await asyncio.to_thread(rss_mb, target_pid))
        return False

    async with new_load_client(max_connections=concurrency, timeout_sec=10) as client:
        requester = PinnedRequester(client, pinned, dns_ms=0.0, max_response_bytes=256 * 1024)
        outcome = await LoadRunner(requester, limits).run(
            ConstantLoad(rate, duration), simple_get("/"), on_second=on_second
        )
    summary = outcome.collector.summary(outcome.duration_sec)
    latency = summary["latency_ms"]
    return BenchRow(
        target_rps=rate,
        achieved_rps=summary["rps"],
        requests=summary["requests"],
        p50_ms=latency["p50"],
        p95_ms=latency["p95"],
        error_rate=summary["error_rate"],
        dropped=summary["iterations"]["dropped"],
        lag_p95_ms=outcome.generator["lag_p95_ms"],
        saturated=outcome.generator_saturated,
        generator_rss_mb=peak_rss_mb(),
        target_rss_mb=target_rss,
    )


def render_table(rows: list[BenchRow]) -> str:
    """Таблица результатов в Markdown."""
    lines = [
        "| Цель, RPS | Факт, RPS | Запросов | p50, мс | p95, мс | Ошибки | Сброшено "
        "| Лаг loop p95, мс | Насыщен | RSS генератора, МБ | RSS demo_target, МБ |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|:---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row.target_rps:g} | {row.achieved_rps:.1f} | {row.requests} "
            f"| {row.p50_ms:.2f} | {row.p95_ms:.2f} | {row.error_rate:.2%} | {row.dropped} "
            f"| {row.lag_p95_ms:.1f} | {'да' if row.saturated else 'нет'} "
            f"| {row.generator_rss_mb:.1f} | {row.target_rss_mb:.1f} |"
        )
    return "\n".join(lines)


def ceiling(rows: list[BenchRow]) -> BenchRow | None:
    """Последняя частота подряд снизу, выданная честно: ≥95 % цели, без сбросов и насыщения."""
    best: BenchRow | None = None
    for row in sorted(rows, key=lambda item: item.target_rps):
        if row.saturated or row.dropped or row.achieved_rps < row.target_rps * 0.95:
            break
        best = row
    return best


def render_section(concurrency: int, rows: list[BenchRow]) -> str:
    """Таблица и вывод для одного значения ``MAX_CONCURRENCY``."""
    best = ceiling(rows)
    parts = [f"### MAX_CONCURRENCY={concurrency}", "", render_table(rows), ""]
    if best:
        parts.append(
            "Без сброшенных проходов и насыщения (подряд снизу) генератор выдал "
            f"**~{best.achieved_rps:.0f} RPS** "
            f"(цель {best.target_rps:g}); максимум фактического RPS за все прогоны — "
            f"{max(row.achieved_rps for row in rows):.0f}."
        )
    else:
        parts.append("Ни одна частота не прошла без сброшенных проходов или насыщения.")
    hidden = [row for row in rows if row.dropped and not row.saturated]
    if hidden:
        rates = ", ".join(f"{row.target_rps:g}" for row in hidden)
        parts += [
            "",
            f"Внимание: на частотах {rates} проходы сбрасывались и задержка выросла, но флаг "
            "`generator_saturated` не поднялся — лаг event loop и загрузка CPU "
            "остались ниже порогов.",
        ]
    return "\n".join(parts)


def render_report(results: dict[int, list[BenchRow]], duration: float) -> str:
    """Полный текст ``docs/benchmark.md``."""
    sections = "\n\n".join(
        render_section(concurrency, rows) for concurrency, rows in results.items()
    )
    return f"""# Бенчмарк генератора нагрузки

Сколько запросов в секунду генератор LoadWatch реально выдаёт на этой машине.
Сгенерировано `scripts/benchmark_runner.py` {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.

## Машина

- CPU: {cpu_model()}
- Логических ядер: {os.cpu_count()}
- ОС: {platform.system()} {platform.release()} ({platform.machine()})
- Python: {platform.python_version()}

## Методика

- Цель — `demo_target` (FastAPI + uvicorn, один процесс, лог доступа выключен) на
  127.0.0.1, `GET /` без задержки, ёмкость `/limited` не используется.
- Для каждой частоты — `ConstantLoad` на {duration:g} с через `LoadRunner` напрямую
  (без БД и API), порог лага {LAG_THRESHOLD_MS:g} мс или загрузки CPU
  {CPU_THRESHOLD:.0%} ядра, насыщение — {SATURATION_SECONDS} с подряд выше порога
  (как в настройках по умолчанию). Пул соединений httpx равен `MAX_CONCURRENCY`,
  как в runner.
- «Сброшено» — проходы, для которых не нашлось свободного слота конкурентности.
- RSS генератора — пиковый RSS процесса бенчмарка; RSS demo_target — пиковый за прогон.

**Важно:** демо-цель и генератор работают на одной машине и конкурируют за CPU
(на VPS с 1 vCPU — за единственное ядро). Цифры ориентировочные: это оценка
возможностей генератора снизу, на другой машине и под другой фоновой нагрузкой
они будут другими. У границы возможностей результаты немонотонны: соседние
частоты могут то проходить, то нет из-за фоновой нагрузки на машину, поэтому
за потолок берётся последняя частота, пройденная без сбросов подряд снизу.
Перед выбором `MAX_RPS` прогоните бенчмарк на целевом VPS.

## Результаты

{sections}

Для профиля `small` (1 vCPU) стартовое `MAX_RPS=150`; если на целевом VPS
бенчмарк показывает меньше, `MAX_RPS` стоит снизить до измеренного значения.
"""


async def benchmark(
    rates: list[float], duration: float, concurrency: int, port: int, target_pid: int
) -> list[BenchRow]:
    """Прогоны по возрастанию частоты с паузой между ними."""
    rows: list[BenchRow] = []
    for rate in rates:
        print(f"→ concurrency {concurrency}: {rate:g} RPS × {duration:g} s ...", flush=True)
        row = await run_rate(port, target_pid, rate, duration, concurrency)
        rows.append(row)
        print(
            f"  achieved {row.achieved_rps:.1f} RPS, p95 {row.p95_ms:.2f} ms, "
            f"errors {row.error_rate:.2%}, dropped {row.dropped}, "
            f"lag p95 {row.lag_p95_ms:.1f} ms, saturated {row.saturated}",
            flush=True,
        )
        await asyncio.sleep(2)  # даём закрыться соединениям и остыть процессору
    return rows


def _numbers(text: str) -> list[float]:
    """Список чисел из строки через запятую."""
    return [float(item) for item in text.split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--rates",
        default=",".join(str(rate) for rate in DEFAULT_RATES),
        help="целевые частоты через запятую",
    )
    parser.add_argument("--duration", type=float, default=10.0, help="секунд на частоту")
    parser.add_argument(
        "--concurrency",
        default=",".join(str(value) for value in DEFAULT_CONCURRENCY),
        help="значения MAX_CONCURRENCY через запятую (по таблице на каждое)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="куда писать отчёт")
    parser.add_argument("--no-write", action="store_true", help="только напечатать таблицу")
    args = parser.parse_args(argv)
    rates = _numbers(args.rates)
    concurrencies = [int(value) for value in _numbers(args.concurrency)]

    port = free_port()
    target = start_target(port)
    results: dict[int, list[BenchRow]] = {}
    try:
        for concurrency in concurrencies:
            results[concurrency] = asyncio.run(
                benchmark(rates, args.duration, concurrency, port, target.pid)
            )
    finally:
        target.terminate()
        target.wait(timeout=10)

    for concurrency, rows in results.items():
        print(f"\nMAX_CONCURRENCY={concurrency}")
        print(render_table(rows))
    if not args.no_write:
        args.output.write_text(render_report(results, args.duration), encoding="utf-8")
        print(f"\nReport written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
