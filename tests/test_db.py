"""Вспомогательные типы БД."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from loadwatch.db import UtcDateTime

DIALECT = postgresql.dialect()  # type: ignore[no-untyped-call]


def test_utc_datetime_converts_to_utc() -> None:
    moscow = datetime(2026, 1, 1, 15, 0, tzinfo=timezone(timedelta(hours=3)))
    stored = UtcDateTime().process_bind_param(moscow, DIALECT)
    assert stored == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert stored is not None and stored.tzinfo is UTC
    loaded = UtcDateTime().process_result_value(moscow, DIALECT)
    assert loaded is not None and loaded.utcoffset() == timedelta(0)


def test_utc_datetime_rejects_naive_and_passes_none() -> None:
    with pytest.raises(ValueError, match="naive"):
        UtcDateTime().process_bind_param(datetime(2026, 1, 1), DIALECT)
    assert UtcDateTime().process_bind_param(None, DIALECT) is None
    assert UtcDateTime().process_result_value(None, DIALECT) is None
