# -*- coding: utf-8 -*-
"""PDF просчёта BLOCKS: таблица услуг, параметры изделия, итог (ReportLab + кириллица)."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from calc.order_summary import collect_line_items


def _register_cyrillic_font() -> tuple[str, str]:
    name = "BlocksOrderFont"
    bold = "BlocksOrderFont"
    try:
        if sys.platform == "win32":
            font_path = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf")
        else:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if os.path.isfile(font_path):
            pdfmetrics.registerFont(TTFont(name, font_path))
            return name, name
    except Exception:
        pass
    return "Helvetica", "Helvetica-Bold"


def write_blocks_order_pdf(filepath: str, main_app: Any) -> None:
    """main_app: MainApp с .selected и виджетами (srochno и т.д.)."""
    created = datetime.now()
    font, font_b = _register_cyrillic_font()
    selected = getattr(main_app, "selected", {}) or {}
    rows, total, warns = collect_line_items(selected, main_app)

    izd = selected.get("Параметры изделия") or {}
    matp = selected.get("Параметры материала") or {}

    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "t",
        parent=styles["Heading1"],
        fontName=font_b,
        fontSize=16,
        spaceAfter=8,
    )
    h2 = ParagraphStyle("h2", parent=styles["Normal"], fontName=font_b, fontSize=11, spaceAfter=6)
    body = ParagraphStyle("b", parent=styles["Normal"], fontName=font, fontSize=9, leading=12)

    client_block = selected.get("Клиент") or {}
    client_name = (client_block.get("Имя") or "").strip()

    story = []
    story.append(Paragraph("Коммерческое предложение", title_style))
    if client_name:
        story.append(
            Paragraph(
                ("Клиент: %s" % client_name).replace("&", "&amp;"),
                body,
            )
        )
    story.append(
        Paragraph(
            created.strftime("Документ создан: %d.%m.%Y %H:%M"),
            body,
        )
    )
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("Параметры изделия", h2))
    th = matp.get("Толщина (мм)")
    ths = str(th) if th is not None else "—"
    lines = [
        "Форма: %s; количество: %s шт."
        % (izd.get("Форма") or "—", izd.get("Количество (шт)") or "—"),
        "Материал: %s; вариант: %s; толщина: %s мм; закалка: %s"
        % (
            matp.get("Тип материала") or "—",
            matp.get("Цвет / Вариант") or "—",
            ths,
            "да" if matp.get("Закалка") else "нет",
        ),
    ]
    if izd.get("Площадь (м²)") is not None:
        q = int(izd.get("Количество (шт)") or 1)
        if q > 1 and izd.get("Общая площадь (м²)") is not None:
            lines.append(
                "Площадь за 1 шт.: %s м²; всего %s шт.: %s м²; периметр за 1: %s мм; всего P: %s мм"
                % (
                    izd.get("Площадь (м²)"),
                    q,
                    izd.get("Общая площадь (м²)"),
                    izd.get("Периметр (мм)"),
                    izd.get("Общий периметр (мм)", int(izd.get("Периметр (мм)") or 0) * q),
                )
            )
        else:
            lines.append("Площадь: %s м²; периметр: %s мм" % (izd.get("Площадь (м²)"), izd.get("Периметр (мм)")))
    for line in lines:
        story.append(Paragraph(line.replace("&", "&amp;"), body))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("Услуги", h2))
    data = [["Услуга", "Сумма, ₽", "Комментарий"]]
    for name, rub, det in rows:
        data.append([name, str(rub), (det or "—")[:200]])
    if len(data) == 1:
        data.append(["—", "0", "Нет строк — выполните расчёт в программе"])

    t = Table(data, colWidths=[70 * mm, 28 * mm, 75 * mm])
    t.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, -1), font, 8),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8e8e8")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("<b>Итого: %s ₽</b>" % total, ParagraphStyle("tot", parent=body, fontName=font_b, fontSize=12)))
    for w in warns:
        story.append(Paragraph("<i>%s</i>" % w.replace("&", "&amp;"), body))

    # HTML-сводка как доп. страница не нужна — таблица выше достаточна
    doc.build(story)
