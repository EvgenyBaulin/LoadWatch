"""``/health``: liveness and database reachability."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from loadwatch import __version__
from loadwatch.api.deps import SessionDep

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


class HealthOut(BaseModel):
    """Service status."""

    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    version: str


@router.get("/health")
def health(session: SessionDep, response: Response) -> HealthOut:
    """Return 200 when the database answers, 503 otherwise."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("Health check: database unavailable", exc_info=True)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthOut(status="degraded", database="unavailable", version=__version__)
    return HealthOut(status="ok", database="ok", version=__version__)
