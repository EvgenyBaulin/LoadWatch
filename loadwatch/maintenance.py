"""``python -m loadwatch.maintenance``: очистка по сроку хранения (cron раз в сутки).

Посекундные ряды запусков старше ``RETENTION_DAYS`` удаляются — это основной
объём данных. Агрегаты запусков (p95, RPS, ошибки, сводка) остаются навсегда:
на них строятся графики за большие периоды и базовая линия регрессий.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, null, select, update
from sqlalchemy.orm import Session

from loadwatch.config import Settings, get_settings
from loadwatch.db import create_db_engine, create_session_factory, utcnow
from loadwatch.logging_setup import configure_logging
from loadwatch.models import Alert, RunMetrics, TestRun

logger = logging.getLogger("loadwatch.maintenance")

# Закрытые алерты держим дольше рядов: это короткие записи, нужные для истории сайта.
RESOLVED_ALERTS_MULTIPLIER = 4


def purge(session: Session, settings: Settings, *, now: datetime | None = None) -> dict[str, int]:
    """Чистит старые данные; возвращает, сколько записей затронуто."""
    now = now or utcnow()
    cutoff = now - timedelta(days=settings.retention_days)
    old_runs = select(TestRun.id).where(TestRun.finished_at < cutoff)
    cleared = session.execute(
        update(RunMetrics)
        .where(RunMetrics.run_id.in_(old_runs), RunMetrics.series.is_not(None))
        .values(series=null())
    )
    alerts_cutoff = now - timedelta(days=settings.retention_days * RESOLVED_ALERTS_MULTIPLIER)
    deleted = session.execute(
        delete(Alert).where(Alert.status == "resolved", Alert.resolved_at < alerts_cutoff)
    )
    session.commit()
    result = {
        "series_cleared": int(getattr(cleared, "rowcount", 0) or 0),
        "alerts_deleted": int(getattr(deleted, "rowcount", 0) or 0),
    }
    logger.info("Maintenance: %s (retention %d days)", result, settings.retention_days)
    return result


def main() -> int:
    """Точка входа."""
    settings = get_settings()
    configure_logging(settings)
    engine = create_db_engine(settings)
    try:
        with create_session_factory(engine)() as session:
            purge(session, settings)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
