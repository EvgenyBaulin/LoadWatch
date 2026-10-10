"""PDF-отчёт о запуске (reportlab, без matplotlib — экономия памяти).

Шрифт DejaVu Sans лежит в репозитории, поэтому кириллица работает на любом
сервере. Язык — параметр ``lang`` или ``users.locale``. Секретов в отчёте нет:
шаги показываются как метод и путь-шаблон (``{{secret.NAME}}`` — только имя).
"""

from __future__ import annotations

import io
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from loadwatch.notifications.builders import regression_lines
from loadwatch.notifications.messages import t

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT = "DejaVuSans"
FONT_BOLD = "DejaVuSans-Bold"
ACCENT = colors.HexColor("#c24e00")  # оранжевый акцент LoadWatch (контраст на белом ≥ 4.5)
SERIES_P95 = colors.HexColor("#c24e00")
SERIES_RPS = colors.HexColor("#1f6feb")
GRID = colors.HexColor("#d0d7de")
MUTED = colors.HexColor("#57606a")
OK = colors.HexColor("#1a7f37")
BAD = colors.HexColor("#cf222e")


@cache
def _register_fonts() -> None:
    pdfmetrics.registerFont(TTFont(FONT, str(FONTS_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONTS_DIR / "DejaVuSans-Bold.ttf")))


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle(
            "title", fontName=FONT_BOLD, fontSize=16, leading=20, textColor=ACCENT
        ),
        "h2": ParagraphStyle(
            "h2", fontName=FONT_BOLD, fontSize=11, leading=14, spaceBefore=8, spaceAfter=4
        ),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=9, leading=12),
        "muted": ParagraphStyle("muted", fontName=FONT, fontSize=8, leading=10, textColor=MUTED),
        "warn": ParagraphStyle("warn", fontName=FONT_BOLD, fontSize=9, leading=12, textColor=BAD),
    }


_CELL = ParagraphStyle("cell", fontName=FONT, fontSize=8, leading=10)
_CELL_HEAD = ParagraphStyle("cell_head", fontName=FONT_BOLD, fontSize=8, leading=10)


def _cell(value: Any, style: ParagraphStyle) -> Paragraph:
    """Ячейка-абзац: текст переносится, переводы строк схлопываются, разметка экранируется."""
    text = " ".join(str(value).split())
    if len(text) > 300:
        text = text[:297] + "…"
    return Paragraph(escape(text), style)


def _table(rows: list[list[Any]], widths: list[float] | None = None, header: bool = True) -> Table:
    cells = [
        [_cell(value, _CELL_HEAD if header and index == 0 else _CELL) for value in row]
        for index, row in enumerate(rows)
    ]
    table = Table(cells, colWidths=widths or _even_widths(rows), repeatRows=1 if header else 0)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    if header:
        style += [
            ("FONTNAME", (0, 0), (-1, 0), FONT_BOLD),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f6f8fa")),
        ]
    table.setStyle(TableStyle(style))
    return table


def _even_widths(rows: list[list[Any]]) -> list[float]:
    """Без явной ширины колонки делят ширину страницы поровну — иначе текст не переносится."""
    columns = max((len(row) for row in rows), default=1)
    return [174 * mm / columns] * columns


def _num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return str(value)


def _line_chart(series: list[dict[str, Any]], key: str, title: str, color: Any) -> Drawing:
    drawing = Drawing(170 * mm, 45 * mm)
    points = [(float(point["t"]), float(point.get(key) or 0)) for point in series]
    if not points:
        points = [(0.0, 0.0)]
    plot = LinePlot()
    plot.x, plot.y = 12 * mm, 6 * mm
    plot.width, plot.height = 150 * mm, 32 * mm
    plot.data = [points]
    plot.lines[0].strokeColor = color
    plot.lines[0].strokeWidth = 1.2
    plot.xValueAxis.labels.fontName = FONT
    plot.yValueAxis.labels.fontName = FONT
    plot.xValueAxis.labels.fontSize = 7
    plot.yValueAxis.labels.fontSize = 7
    plot.yValueAxis.valueMin = 0
    plot.yValueAxis.gridStrokeColor = GRID
    plot.yValueAxis.visibleGrid = True
    drawing.add(plot)
    drawing.add(String(12 * mm, 41 * mm, title, fontName=FONT, fontSize=8, fillColor=MUTED))
    return drawing


def _bar_chart(
    labels: list[str], values: list[float], title: str, bar_colors: list[Any]
) -> Drawing:
    drawing = Drawing(170 * mm, 45 * mm)
    chart = VerticalBarChart()
    chart.x, chart.y = 12 * mm, 8 * mm
    chart.width, chart.height = 150 * mm, 30 * mm
    chart.data = [values or [0]]
    chart.categoryAxis.categoryNames = labels or [""]
    chart.categoryAxis.labels.fontName = FONT
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.labels.fontName = FONT
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.gridStrokeColor = GRID
    chart.valueAxis.visibleGrid = True
    for index, color in enumerate(bar_colors):
        chart.bars[(0, index)].fillColor = color
    chart.bars[0].strokeColor = None
    drawing.add(chart)
    drawing.add(String(12 * mm, 41 * mm, title, fontName=FONT, fontSize=8, fillColor=MUTED))
    return drawing


def build_run_report(
    *,
    locale: str,
    site_name: str,
    site_url: str,
    scenario_name: str | None,
    run: dict[str, Any],
    params: dict[str, Any] | None,
    summary: dict[str, Any] | None,
    series: list[dict[str, Any]] | None,
    slo: dict[str, Any],
    regression: dict[str, Any] | None,
    generated_at: datetime,
) -> bytes:
    """Собирает PDF и возвращает его байты."""
    _register_fonts()
    styles = _styles()

    def tr(key: str, **params_: Any) -> str:
        return t(locale, key, **params_)

    story: list[Any] = [Paragraph(tr("report.title"), styles["title"]), Spacer(1, 4 * mm)]
    meta = [
        [tr("report.site"), f"{site_name} ({site_url})"],
        [tr("report.scenario"), scenario_name or "—"],
        [tr("report.run"), run["id"]],
        [tr("report.started"), run["started_at"] or "—"],
        [tr("report.status"), run["status"]],
        [tr("report.source"), run["source"]],
        [tr("report.label"), run["label"] or "—"],
    ]
    if summary:
        meta.insert(
            4,
            [tr("report.duration"), tr("report.seconds", value=_num(summary.get("duration_sec")))],
        )
    story.append(_table(meta, widths=[40 * mm, 130 * mm], header=False))

    if run.get("generator_saturated"):
        story += [Spacer(1, 3 * mm), Paragraph(tr("report.saturated"), styles["warn"])]

    if params:
        profile = ", ".join(f"{key}={value}" for key, value in params.get("profile", {}).items())
        story += [
            Paragraph(tr("report.profile"), styles["h2"]),
            Paragraph(escape(profile), styles["body"]),
        ]
        flow_rows = [[tr("report.step"), "", ""]] + [
            [step.get("name", ""), step.get("method", "GET"), step.get("path", "")]
            for step in params.get("flow", [])
        ]
        story += [
            Paragraph(tr("report.flow"), styles["h2"]),
            _table(flow_rows, widths=[50 * mm, 20 * mm, 100 * mm]),
        ]

    if not summary:
        story += [Spacer(1, 4 * mm), Paragraph(tr("report.no_metrics"), styles["body"])]
        return _render(
            story, tr("report.generated", at=generated_at.strftime("%Y-%m-%d %H:%M UTC")), styles
        )

    latency = summary.get("latency_ms", {})
    apdex_t = (params or {}).get("apdex_t_ms")
    apdex_label = tr("report.apdex_t", t=apdex_t) if apdex_t else tr("report.apdex")
    slo_text = {
        True: tr("report.slo_met"),
        False: tr("report.slo_failed"),
        None: tr("report.slo_none"),
    }[slo.get("met")]
    if slo.get("p95_target") is not None or slo.get("error_rate_target") is not None:
        targets = []
        if slo.get("p95_target") is not None:
            targets.append(f"p95 ≤ {slo['p95_target']}")
        if slo.get("error_rate_target") is not None:
            targets.append(f"errors ≤ {_num(slo['error_rate_target'] * 100)}%")
        slo_text += f" ({', '.join(targets)})"
    summary_rows = [
        [
            tr("report.requests"),
            _num(summary.get("requests")),
            tr("report.rps"),
            _num(summary.get("rps")),
        ],
        [
            tr("report.errors"),
            _num(summary.get("errors")),
            tr("report.error_rate"),
            f"{_num((summary.get('error_rate') or 0) * 100)}%",
        ],
        [
            "p50 / p90",
            f"{_num(latency.get('p50'))} / {_num(latency.get('p90'))}",
            "p95 / p99",
            f"{_num(latency.get('p95'))} / {_num(latency.get('p99'))}",
        ],
        [
            "min / avg / max",
            f"{_num(latency.get('min'))} / {_num(latency.get('avg'))} / {_num(latency.get('max'))}",
            apdex_label,
            _num(summary.get("apdex")),
        ],
        [
            tr("report.slo"),
            slo_text,
            tr("report.ceiling"),
            _num((summary.get("step_load") or {}).get("ceiling_rps")),
        ],
    ]
    story += [
        Paragraph(f"{tr('report.summary')} — {tr('report.latency')}", styles["h2"]),
        _table(summary_rows, widths=[30 * mm, 55 * mm, 30 * mm, 55 * mm], header=False),
    ]

    timings = summary.get("timings", {})
    story += [
        Paragraph(tr("report.timings"), styles["h2"]),
        _table(
            [
                ["DNS", "connect", "TLS", "TTFB", "transfer", "TTFB p95"],
                [
                    _num(timings.get(k))
                    for k in (
                        "dns_ms",
                        "connect_ms",
                        "tls_ms",
                        "ttfb_ms",
                        "transfer_ms",
                        "ttfb_p95_ms",
                    )
                ],
            ]
        ),
    ]

    if series:
        story += [
            Spacer(1, 3 * mm),
            _line_chart(series, "p95_ms", tr("report.chart_latency"), SERIES_P95),
            _line_chart(series, "rps", tr("report.chart_rps"), SERIES_RPS),
        ]

    codes = summary.get("status_codes", {})
    if codes:
        labels = list(codes)
        bar_colors = [OK if code.startswith(("2", "3")) else BAD for code in labels]
        story.append(
            _bar_chart(
                labels, [float(codes[c]) for c in labels], tr("report.status_codes"), bar_colors
            )
        )

    steps = summary.get("steps", [])
    if steps:
        rows = [
            [
                tr("report.step"),
                tr("report.requests"),
                tr("report.error_rate"),
                "p50",
                "p95",
                "p99",
                tr("report.apdex"),
            ]
        ]
        for step in steps:
            step_latency = step.get("latency_ms", {})
            rows.append(
                [
                    step["name"],
                    _num(step.get("requests")),
                    f"{_num((step.get('error_rate') or 0) * 100)}%",
                    _num(step_latency.get("p50")),
                    _num(step_latency.get("p95")),
                    _num(step_latency.get("p99")),
                    _num(step.get("apdex")),
                ]
            )
        story += [Paragraph(tr("report.steps"), styles["h2"]), _table(rows)]

    step_load = summary.get("step_load")
    if step_load and step_load.get("steps"):
        load_rows = [
            [
                "#",
                tr("report.target_rate"),
                tr("report.rps"),
                tr("report.error_rate"),
                "p95",
                tr("report.passed"),
            ]
        ]
        for item in step_load["steps"]:
            load_rows.append(
                [
                    item["step"] + 1,
                    _num(item["target_rate"]),
                    _num(item["rps"]),
                    f"{_num(item['error_rate'] * 100)}%",
                    _num(item["p95_ms"]),
                    tr("report.yes") if item["passed"] else tr("report.no"),
                ]
            )
        story += [Paragraph(tr("report.step_load"), styles["h2"]), _table(load_rows)]
        story.append(
            _bar_chart(
                [str(item["step"] + 1) for item in step_load["steps"]],
                [float(item["rps"]) for item in step_load["steps"]],
                tr("report.step_load"),
                [OK if item["passed"] else BAD for item in step_load["steps"]],
            )
        )

    errors = summary.get("errors_top", [])
    if errors:
        rows = [[tr("report.error_type"), tr("report.count")]] + [
            [e["type"], e["count"]] for e in errors
        ]
        story += [
            Paragraph(tr("report.errors_top"), styles["h2"]),
            _table(rows, widths=[120 * mm, 50 * mm]),
        ]

    story.append(Paragraph(tr("report.regression"), styles["h2"]))
    if regression is None:
        story.append(Paragraph(tr("report.regression_unknown"), styles["body"]))
    elif not regression.get("detected"):
        story.append(Paragraph(tr("report.regression_none"), styles["body"]))
    else:
        for line in regression_lines(locale, regression):
            story.append(Paragraph(line, styles["body"]))

    return _render(
        story, tr("report.generated", at=generated_at.strftime("%Y-%m-%d %H:%M UTC")), styles
    )


def _render(story: list[Any], footer: str, styles: dict[str, ParagraphStyle]) -> bytes:
    story += [Spacer(1, 6 * mm), Paragraph(footer, styles["muted"])]
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="LoadWatch report",
        author="LoadWatch",
    )
    document.build(story)
    return buffer.getvalue()
