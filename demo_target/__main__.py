"""``python -m demo_target``: запуск демо-цели через uvicorn.

Адрес и порт — ``DEMO_HOST`` (по умолчанию 127.0.0.1) и ``DEMO_PORT`` (8081).
"""

from __future__ import annotations

import os

import uvicorn

from demo_target.app import create_app


def main() -> None:
    """Запускает демо-цель в одном процессе без лишних логов доступа."""
    host = os.environ.get("DEMO_HOST", "127.0.0.1")
    port = int(os.environ.get("DEMO_PORT", "8081"))
    # Лог доступа под нагрузкой в сотни RPS только тратит CPU, который нужен генератору.
    uvicorn.run(create_app(), host=host, port=port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
