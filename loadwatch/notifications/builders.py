"""Сборка уведомлений о событиях на языке пользователя."""

from __future__ import annotations

from typing import Any

from loadwatch.notifications.messages import Message, t


def _num(value: float) -> str:
    """Число без лишних нулей: 120.0 → 120, 0.95 → 0.95."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def regression_lines(locale: str, regression: dict[str, Any]) -> list[str]:
    """Строки вида «p95 выросла на 40% относительно медианы последних 5 запусков»."""
    lines = []
    for finding in regression.get("findings", []):
        metric = finding["metric"]
        params = {
            "baseline_runs": finding["baseline_runs"],
            "baseline": _num(finding["baseline"]),
            "current": _num(finding["current"]),
            "change_pct": _num(finding["change_pct"] or 0),
            "change_pct_abs": _num(abs(finding["change_pct"] or 0)),
            "change_points": _num(finding["change_abs"] * 100),
            "baseline_pct": _num(finding["baseline"] * 100),
            "current_pct": _num(finding["current"] * 100),
        }
        lines.append(t(locale, f"regression.{metric}", **params))
    return lines


def alert_message(
    locale: str,
    alert_type: str,
    *,
    site_name: str,
    details: dict[str, Any],
    link: str | None,
    repeat: bool = False,
) -> Message:
    """Открытие алерта (или повтор, пока он открыт)."""
    title = t(locale, f"alert.{alert_type}.title", site=site_name)
    if repeat:
        title = t(locale, "alert.repeat_prefix") + title
    lines: list[str] = []
    if alert_type == "regression" and details.get("regression"):
        lines += regression_lines(locale, details["regression"])
    elif alert_type == "slo_breach":
        slo = details.get("slo", {})
        if slo.get("p95_target") is not None and slo.get("p95") is not None:
            lines.append(
                t(locale, "slo.p95", current=_num(slo["p95"]), target=_num(slo["p95_target"]))
            )
        if slo.get("error_rate_target") is not None and slo.get("error_rate") is not None:
            lines.append(
                t(
                    locale,
                    "slo.error_rate",
                    current=_num(slo["error_rate"] * 100),
                    target=_num(slo["error_rate_target"] * 100),
                )
            )
    elif alert_type == "run_failed":
        lines.append(t(locale, "run_failed.reason", reason=details.get("error") or "unknown"))
    elif alert_type == "site_unverified":
        lines.append(t(locale, "site_unverified.line"))
    if details.get("consecutive"):
        lines.append(t(locale, "alert.consecutive", count=details["consecutive"]))
    return Message(
        event=alert_type,
        locale=locale,
        title=title,
        lines=lines,
        link=link,
        payload={"type": alert_type, "site": site_name, "repeat": repeat, "details": details},
    )


def recovery_message(
    locale: str, alert_type: str, *, site_name: str, runs: int, link: str | None
) -> Message:
    """Алерт закрыт: проблема ушла."""
    problem = t(locale, f"problem.{alert_type}")
    lines = [t(locale, "recovery.line", count=runs)] if runs else []
    return Message(
        event="recovery",
        locale=locale,
        title=t(locale, "recovery.title", site=site_name, problem=problem),
        lines=lines,
        link=link,
        payload={"type": alert_type, "site": site_name, "runs": runs},
    )


def channel_test_message(locale: str, channel_name: str) -> Message:
    """Кнопка «отправить тест»."""
    return Message(
        event="test",
        locale=locale,
        title=t(locale, "test.title"),
        lines=[t(locale, "test.line", channel=channel_name)],
        payload={"channel": channel_name},
    )
