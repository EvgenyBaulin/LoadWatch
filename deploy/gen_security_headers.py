#!/usr/bin/env python3
"""Генерирует сниппет Nginx с заголовками безопасности для LoadWatch.

Запуск: ``python3 deploy/gen_security_headers.py frontend/dist/index.html [--hsts] > файл``.

Зачем генерация, а не статичный файл: в ``index.html`` есть маленький inline-скрипт
темы (ставит тему до отрисовки, чтобы не было «вспышки» светлой темы). Строгий CSP
запрещает inline-скрипты, а ``'unsafe-inline'`` открыл бы дорогу любому внедрённому
скрипту. Поэтому разрешаем ровно эти скрипты по SHA-256 их содержимого: хэш
считается из собранного ``index.html`` при каждой сборке/деплое и не устаревает.

Используется в двух местах: ``docker/frontend.Dockerfile`` (контейнер фронтенда) и
``deploy/deploy.sh`` (VPS). Только стандартная библиотека Python.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import sys
from pathlib import Path

# Inline-скрипт = тег <script> без атрибута src. Vite не трогает содержимое таких
# тегов, поэтому хэш считается по байтам между открывающим и закрывающим тегом.
SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
SRC_RE = re.compile(r"\bsrc\s*=", re.IGNORECASE)
# Не-JS блоки (JSON-LD, importmap) CSP script-src не касаются, кроме importmap.
NON_JS_TYPE_RE = re.compile(r"\btype\s*=\s*[\"']?application/(ld\+)?json", re.IGNORECASE)


def inline_script_hashes(html: str) -> list[str]:
    """CSP-источники вида 'sha256-…' для всех inline-скриптов страницы."""
    hashes: list[str] = []
    for attrs, body in SCRIPT_RE.findall(html):
        if SRC_RE.search(attrs) or NON_JS_TYPE_RE.search(attrs):
            continue
        digest = hashlib.sha256(body.encode("utf-8")).digest()
        source = f"'sha256-{base64.b64encode(digest).decode('ascii')}'"
        if source not in hashes:
            hashes.append(source)
    return hashes


def build_snippet(hashes: list[str], *, hsts: bool) -> str:
    """Текст сниппета: директивы add_header с флагом always (и для ошибок тоже)."""
    script_src = " ".join(["'self'", *hashes])
    csp = "; ".join(
        [
            "default-src 'self'",
            f"script-src {script_src}",
            # Стили: 'unsafe-inline' — компромисс. Chart.js и Vue выставляют inline-стили
            # элементам; через CSS исполнить JS нельзя, поэтому риск невелик.
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "manifest-src 'self'",
            "worker-src 'self' blob:",
        ]
    )
    lines = [
        "# Сгенерировано deploy/gen_security_headers.py — не править вручную.",
        f"# Inline-скриптов в index.html: {len(hashes)}.",
        f'add_header Content-Security-Policy "{csp}" always;',
        'add_header X-Content-Type-Options "nosniff" always;',
        'add_header X-Frame-Options "DENY" always;',
        'add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
        'add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), '
        'payment=(), usb=()" always;',
        'add_header Cross-Origin-Opener-Policy "same-origin" always;',
    ]
    if hsts:
        # Только для HTTPS-сервера: браузер год не будет пробовать HTTP.
        lines.append(
            'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;'
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    """Читает index.html и печатает сниппет в stdout."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("index_html", type=Path, help="собранный frontend/dist/index.html")
    parser.add_argument("--hsts", action="store_true", help="добавить Strict-Transport-Security")
    args = parser.parse_args()
    try:
        html = args.index_html.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {args.index_html}: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(build_snippet(inline_script_hashes(html), hsts=args.hsts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
