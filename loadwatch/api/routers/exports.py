"""Экспорт: CSV по запуску и по сайту за период, PDF-отчёт запуска."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from loadwatch.api.deps import CurrentUser, ExportServiceDep

router = APIRouter(tags=["exports"])


def _attachment(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}


@router.get("/runs/{run_id}/export.csv", response_class=Response)
def run_csv(run_id: uuid.UUID, user: CurrentUser, service: ExportServiceDep) -> Response:
    """Метрики запуска по шагам (CSV)."""
    filename, content = service.run_csv(user, run_id)
    return Response(content, media_type="text/csv; charset=utf-8", headers=_attachment(filename))


@router.get("/sites/{site_id}/export.csv", response_class=Response)
def site_csv(
    site_id: uuid.UUID,
    user: CurrentUser,
    service: ExportServiceDep,
    since: Annotated[datetime | None, Query(alias="from")] = None,
    until: Annotated[datetime | None, Query(alias="to")] = None,
) -> Response:
    """Все запуски сайта за период (по умолчанию 30 дней), строка на запуск (CSV)."""
    filename, content = service.site_csv(user, site_id, since=since, until=until)
    return Response(content, media_type="text/csv; charset=utf-8", headers=_attachment(filename))


@router.get("/runs/{run_id}/report.pdf", response_class=Response)
def run_pdf(
    run_id: uuid.UUID,
    user: CurrentUser,
    service: ExportServiceDep,
    lang: Literal["ru", "en"] | None = None,
) -> Response:
    """PDF-отчёт о запуске: сводка, SLO, графики, шаги, ошибки, регрессия."""
    filename, content = service.run_pdf(user, run_id, lang=lang)
    return Response(content, media_type="application/pdf", headers=_attachment(filename))
