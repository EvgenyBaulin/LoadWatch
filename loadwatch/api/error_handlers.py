"""Приводит любую ошибку к виду ``{"error": {"code", "message", "details"?}}``."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from loadwatch.errors import AppError

logger = logging.getLogger(__name__)

# Коды для ошибок самого фреймворка (нет маршрута, не тот метод и т. п.).
_HTTP_STATUS_CODES = {
    400: "bad_request",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    415: "unsupported_media_type",
}


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    """Собирает тело ошибки в едином формате."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    # Введённые значения намеренно не возвращаем: там могут быть пароли и секреты.
    return [
        {"loc": list(err.get("loc", ())), "type": err.get("type"), "msg": err.get("msg")}
        for err in exc.errors()
    ]


async def _app_error(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(AppError, exc)
    retry_after = getattr(exc, "retry_after", None)
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
    return JSONResponse(
        error_body(exc.code, exc.message, jsonable_encoder(exc.details)),
        status_code=exc.status_code,
        headers=headers,
    )


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, exc)
    return JSONResponse(
        error_body("validation_error", "Invalid request data", _validation_details(exc)),
        status_code=422,
    )


async def _http_error(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exc)
    code = _HTTP_STATUS_CODES.get(exc.status_code, "http_error")
    message = HTTPStatus(exc.status_code).phrase
    return JSONResponse(error_body(code, message), status_code=exc.status_code, headers=exc.headers)


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(error_body("internal_error", "Internal server error"), status_code=500)


async def _catch_unhandled(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # Starlette ловит необработанные исключения в самом внешнем middleware, снаружи
    # CORS, и браузер не смог бы прочитать тело 500; ловим здесь, внутри CORS.
    try:
        return await call_next(request)
    except Exception as exc:
        return await _unhandled_error(request, exc)


def register_error_handlers(app: FastAPI) -> None:
    """Ставит обработчики на ``app``; CORS-middleware добавлять после этого вызова."""
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)
    app.middleware("http")(_catch_unhandled)
