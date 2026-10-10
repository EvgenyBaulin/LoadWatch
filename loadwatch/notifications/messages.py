"""Тексты уведомлений из словаря локалей (без склейки строк в коде)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

LOCALES_DIR = Path(__file__).parent / "locales"
TEMPLATES_DIR = Path(__file__).parent / "templates"
DEFAULT_LOCALE = "en"


@cache
def _catalog(locale: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{locale}.json"
    if not path.exists():
        path = LOCALES_DIR / f"{DEFAULT_LOCALE}.json"
    return dict(json.loads(path.read_text(encoding="utf-8")))


def t(locale: str, key: str, **params: Any) -> str:
    """Перевод ключа с подстановкой; нет перевода — английский, нет и его — сам ключ."""
    template = _catalog(locale).get(key) or _catalog(DEFAULT_LOCALE).get(key) or key
    return template.format(**params)


@dataclass(slots=True)
class Message:
    """Уведомление, готовое к отправке в любой канал."""

    event: str
    locale: str
    title: str
    lines: list[str] = field(default_factory=list)
    link: str | None = None
    # Машиночитаемое тело для generic webhook (стабильные ключи, без перевода).
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Простой текст: заголовок, строки, ссылка."""
        parts = [self.title, *self.lines]
        if self.link:
            parts.append(t(self.locale, "footer.link", link=self.link))
        return "\n".join(parts)


@cache
def _jinja() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        # Экранируется только HTML-шаблон; в текстовом письме «<» должно остаться «<».
        autoescape=select_autoescape(enabled_extensions=("html.j2",), default_for_string=False),
        keep_trailing_newline=True,
    )


def render_email(message: Message) -> tuple[str, str]:
    """Письмо из шаблонов Jinja2: (простой текст, HTML)."""
    context = {
        "locale": message.locale,
        "title": message.title,
        "lines": message.lines,
        "link": message.link,
        "link_text": t(message.locale, "footer.link", link=message.link) if message.link else "",
    }
    text_template = _jinja().get_template("email.txt.j2")
    html_template = _jinja().get_template("email.html.j2")
    return text_template.render(**context), html_template.render(**context)
