"""Чтение тела HTTP-ответа с жёстким лимитом, в том числе сжатого."""

from __future__ import annotations

import zlib
from collections.abc import AsyncIterator

import httpx


class BoundedDecoder:
    """Распаковка gzip/deflate, которая никогда не выдаёт больше ``limit`` байт.

    Встроенная распаковка httpx сначала распаковывает кусок целиком, поэтому
    маленький «zip-бомба»-ответ занял бы сотни мегабайт до проверки лимита.
    """

    def __init__(self, content_encoding: str, limit: int) -> None:
        encoding = content_encoding.strip().lower()
        if encoding in ("", "identity"):
            self._inflater = None
        elif encoding in ("gzip", "x-gzip", "deflate"):
            # wbits=47: автоопределение заголовка gzip или zlib.
            self._inflater = zlib.decompressobj(47)
        else:
            raise httpx.DecodingError(f"unsupported content encoding: {encoding}")
        self._limit = limit
        self._body = bytearray()

    def feed(self, chunk: bytes) -> bool:
        """Добавляет кусок; ``True`` — лимит достигнут."""
        room = self._limit - len(self._body)
        if self._inflater is None:
            self._body.extend(chunk[:room])
        else:
            try:
                self._body.extend(self._inflater.decompress(chunk, room))
            except zlib.error as exc:
                raise httpx.DecodingError("corrupt compressed body") from exc
        return len(self._body) >= self._limit

    @property
    def body(self) -> bytes:
        """Накопленное тело."""
        return bytes(self._body)


async def raw_chunks(response: httpx.Response) -> AsyncIterator[bytes]:
    """Сырые байты ответа без распаковки силами httpx."""
    if response.is_stream_consumed:
        yield response.content
        return
    async for chunk in response.aiter_raw():
        yield chunk
