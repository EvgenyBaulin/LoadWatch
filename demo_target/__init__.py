"""Демо-цель LoadWatch: маленький сайт с управляемыми задержками, ошибками и потолком RPS."""

from demo_target.app import DemoConfig, create_app

__all__ = ["DemoConfig", "create_app"]
