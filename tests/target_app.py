"""Маленький HTTP-сервер-мишень для интеграционных тестов генератора."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

app = FastAPI()
seen_hosts: list[str] = []


@app.get("/")
async def index(request: Request) -> PlainTextResponse:
    seen_hosts.append(request.headers.get("host", ""))
    return PlainTextResponse("ok")


@app.get("/slow")
async def slow(ms: int = 100) -> PlainTextResponse:
    await asyncio.sleep(ms / 1000)
    return PlainTextResponse("slow")


@app.get("/big")
async def big(kb: int = 1024) -> Response:
    return Response(b"x" * kb * 1024, media_type="application/octet-stream")


@app.get("/gzip-bomb")
async def gzip_bomb() -> Response:
    import gzip

    body = gzip.compress(b"0" * 20_000_000)  # 20 МБ нулей сжимаются в ~20 КБ
    return Response(body, headers={"Content-Encoding": "gzip"})


@app.get("/status/{code}")
async def status(code: int) -> PlainTextResponse:
    return PlainTextResponse("status", status_code=code)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def serve() -> Iterator[int]:
    """Запускает мишень в фоновом потоке и возвращает её порт."""
    port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("target server did not start")
        time.sleep(0.02)
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=5)
