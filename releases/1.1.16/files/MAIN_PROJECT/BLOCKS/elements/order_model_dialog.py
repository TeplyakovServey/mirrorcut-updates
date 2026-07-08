# -*- coding: utf-8 -*-
"""Окно предпросмотра: схема изделия + сводка услуг и итог (без сохранения заказа)."""
from __future__ import annotations

import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

_br = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _br not in sys.path:
    sys.path.insert(0, _br)

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtCore import QPointF, QRectF

from calc.geometry import compute_shape_metrics, triangle_exists, izd_area_m2_for_tariff, izd_perimeter_mm_for_display
from calc import palette as PAL
from calc.order_summary import build_html_summary
from window_branding import apply_window_icon


def _izd_to_shape_values(izd: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Поля «Параметры изделия» → аргументы compute_shape_metrics."""
    sh = (izd.get("Форма") or "").strip()
    if not sh:
        return None
    try:
        q = int(izd.get("Количество (шт)") or 1)
    except (TypeError, ValueError):
        q = 1

    def _i(key: str, default: int = 0) -> int:
        v = izd.get(key)
        if v is None:
            return default
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return default

    if sh == "Прямоугольник":
        w, h = _i("Ширина (мм)"), _i("Высота (мм)")
        if w <= 0 or h <= 0:
            return None
        return sh, {"quantity": q, "width": w, "height": h}
    if sh == "Овал":
        w, h = _i("Ширина (мм)"), _i("Высота (мм)")
        if w <= 0 or h <= 0:
            return None
        return sh, {"quantity": q, "width": w, "height": h}
    if sh == "Круг":
        d = _i("Диаметр (мм)") or _i("Ширина (мм)")
        if d <= 0:
            return None
        return sh, {"quantity": q, "diameter": d}
    if sh == "Треугольник":
        a, b, c = _i("Сторона A (мм)"), _i("Сторона B (мм)"), _i("Сторона C (мм)")
        if a <= 0 or b <= 0 or c <= 0:
            return None
        return sh, {"quantity": q, "a": a, "b": b, "c": c}
    if sh == "Трапеция":
        bn, bv, th = (
            _i("Трапеция низ (мм)"),
            _i("Трапеция верх (мм)"),
            _i("Трапеция высота (мм)"),
        )
        if bn <= 0 or bv <= 0 or th <= 0:
            return None
        return sh, {"quantity": q, "b_bottom": bn, "b_top": bv, "height_trap": th}
    if sh == "Сложная фигура":
        w, h = _i("Ширина (мм)"), _i("Высота (мм)")
        if w <= 0 or h <= 0:
            return None
        return sh, {"quantity": q, "width": w, "height": h}
    return None


def _fmt_m2(v: Any) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    return "%.4g" % x if x >= 0.0001 else "%.6g" % x


def _fmt_mm(v: Any) -> str:
    try:
        return "%s" % int(round(float(v)))
    except (TypeError, ValueError):
        return "—"


def _area_perimeter_lines(izd: Dict[str, Any]) -> List[str]:
    """Строки про площадь/периметр; для не-прямоугольника — площадь фигуры и площадь охвата (вписывающий прямоугольник)."""
    sh = (izd.get("Форма") or "").strip()
    pair = _izd_to_shape_values(izd)
    mgeom = compute_shape_metrics(pair[0], pair[1]) if pair else None

    s_fig = izd.get("Площадь (м²)")
    if s_fig is None and mgeom:
        s_fig = mgeom.get("Площадь (м²)")
    p_mm = izd.get("Периметр (мм)")
    if p_mm is None and mgeom:
        p_mm = mgeom.get("Периметр (мм)")
    try:
        q = max(1, int(izd.get("Количество (шт)") or (mgeom or {}).get("Количество (шт)") or 1))
    except (TypeError, ValueError):
        q = 1
    s_total = izd_area_m2_for_tariff(izd)
    p_total = izd_perimeter_mm_for_display(izd)

    s_box = mgeom.get("bbox_area_m2") if mgeom else None

    if s_fig is None and p_mm is None:
        return ["площадь: —; периметр: —"]

    if sh == "Прямоугольник":
        lines = [
            "площадь %s м²; периметр %s мм" % (_fmt_m2(s_fig), _fmt_mm(p_mm)),
        ]
        if q > 1 and s_total is not None and p_total is not None:
            lines.append(
                "всего %s шт.: площадь %s м²; периметр %s мм"
                % (q, _fmt_m2(s_total), _fmt_mm(p_total))
            )
        return lines

    line1 = "площадь %s м²; по охвату %s м²" % (_fmt_m2(s_fig), _fmt_m2(s_box))
    line2 = "периметр %s мм" % _fmt_mm(p_mm)
    lines = [line1, line2]
    if q > 1 and s_total is not None and p_total is not None:
        lines.append(
            "всего %s шт.: площадь %s м²; периметр %s мм"
            % (q, _fmt_m2(s_total), _fmt_mm(p_total))
        )
    return lines


def _triangle_xy(a: float, b: float, c: float) -> List[Tuple[float, float]]:
    """Вершины треугольника со сторонами a,b,c: (0,0), (c,0), (x,y)."""
    if c <= 0 or not triangle_exists(int(a), int(b), int(c)):
        return [(0, 0), (100, 0), (50, 80)]
    cos_angle = (a * a + c * c - b * b) / (2 * a * c)
    cos_angle = max(-1.0, min(1.0, cos_angle))
    angle = math.acos(cos_angle)
    x = a * math.cos(angle)
    y = a * math.sin(angle)
    return [(0, 0), (c, 0), (x, y)]


class ShapeSchematic(QWidget):
    """Упрощённая схема по полям «Параметры изделия»."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._izd: Dict[str, Any] = {}
        self._matp: Dict[str, Any] = {}
        self.setMinimumHeight(200)
        self.setMinimumWidth(320)

    def set_izd(self, izd: Optional[Dict[str, Any]], matp: Optional[Dict[str, Any]] = None) -> None:
        self._izd = dict(izd or {})
        self._matp = dict(matp or {})
        self.update()

    def _center_block_lines(self, center_head: Optional[str] = None) -> List[str]:
        """Строка 1: тип + цвет; строка 2: толщина; далее площадь/периметр (и для не-прямоугольника — две площади)."""
        m = self._matp
        t = (m.get("Тип материала") or "").strip()
        col = (m.get("Цвет / Вариант") or "").strip()
        if t and col:
            line1 = "%s %s" % (t, col)
        elif t:
            line1 = t
        elif col:
            line1 = col
        else:
            line1 = "—"
        th = m.get("Толщина (мм)")
        line2 = ("%s мм" % th) if th is not None else "—"
        out: List[str] = []
        if center_head:
            out.append(center_head)
        out.extend([line1, line2])
        out.extend(_area_perimeter_lines(self._izd))
        return out

    def _stamp_material_and_dims(
        self,
        p: QPainter,
        inner: QRectF,
        width_mm: float,
        height_mm: float,
        *,
        round_d_mm: Optional[float] = None,
        center_head: Optional[str] = None,
    ) -> None:
        """В центре — цвет/тип, толщина, площадь/периметр; снизу — ширина (или Ø); слева — высота (или Ø)."""
        lines = self._center_block_lines(center_head=center_head)
        font_m = QFont("Arial", 7 if len(lines) > 4 else 8)
        font_d = QFont("Arial", 8)
        p.setFont(font_m)
        p.drawText(inner, Qt.AlignCenter, "\n".join(lines))

        p.setFont(font_d)
        margin_side = 22.0
        if round_d_mm is not None:
            dim_w = "Ø %s мм" % int(round_d_mm)
            dim_h = dim_w
        else:
            dim_w = "%s мм" % int(width_mm)
            dim_h = "%s мм" % int(height_mm)

        p.drawText(
            QRectF(inner.left(), inner.bottom() + 4, inner.width(), 18.0),
            Qt.AlignHCenter | Qt.AlignTop,
            dim_w,
        )

        p.save()
        p.translate(inner.left() - margin_side, inner.center().y())
        p.rotate(-90)
        p.drawText(QRectF(-80, -10, 160, 20), Qt.AlignCenter, dim_h)
        p.restore()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#f5f5f5"))
        pen = QPen(QColor("#222"))
        pen.setWidthF(1.5)
        p.setPen(pen)
        font = QFont("Arial", 9)
        p.setFont(font)

        w, h = self.width(), self.height()
        margin = 40.0
        cw = w - 2 * margin
        ch = h - 2 * margin
        cx = w / 2.0
        cy = h / 2.0
        izd = self._izd
        sh = (izd.get("Форма") or "").strip()

        def map_poly(
            pts: List[Tuple[float, float]], labels: Optional[List[str]] = None
        ) -> Optional[QRectF]:
            if len(pts) < 2:
                return None
            xs = [x for x, _ in pts]
            ys = [y for _, y in pts]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            bw = max_x - min_x
            bh = max_y - min_y
            if bw < 1:
                bw = 1
            if bh < 1:
                bh = 1
            scale = 0.92 * min(cw / bw, ch / bh)
            ox = cx - scale * (min_x + max_x) / 2.0
            oy = cy + scale * (min_y + max_y) / 2.0

            poly = QPolygonF()
            scr: List[Tuple[float, float]] = []
            for x, y in pts:
                sx = ox + scale * x
                sy = oy - scale * y
                scr.append((sx, sy))
                poly.append(QPointF(sx, sy))
            p.drawPolygon(poly)
            if labels and len(labels) == len(scr):
                for i, ((sx, sy), lab) in enumerate(zip(scr, labels)):
                    p.drawText(QRectF(sx - 40, sy - 14, 80, 14), Qt.AlignCenter, lab)
            sxs = [s[0] for s in scr]
            sys = [s[1] for s in scr]
            return QRectF(min(sxs), min(sys), max(sxs) - min(sxs), max(sys) - min(sys))

        if sh == "Прямоугольник":
            bw = float(izd.get("Ширина (мм)") or 300)
            bh = float(izd.get("Высота (мм)") or 200)
            scale = min(cw / max(bw, 1), ch / max(bh, 1)) * 0.88
            rw, rh = bw * scale, bh * scale
            x0 = cx - rw / 2
            y0 = cy - rh / 2
            inner = QRectF(x0, y0, rw, rh)
            p.drawRect(inner)
            self._stamp_material_and_dims(p, inner, bw, bh)
            return

        if sh == "Круг":
            d = float(izd.get("Диаметр (мм)") or izd.get("Ширина (мм)") or 300)
            box = min(cw, ch) * 0.82
            inner = QRectF(cx - box / 2, cy - box / 2, box, box)
            p.drawEllipse(inner)
            self._stamp_material_and_dims(p, inner, d, d, round_d_mm=d)
            return

        if sh == "Овал":
            bw = float(izd.get("Ширина (мм)") or 400)
            bh = float(izd.get("Высота (мм)") or 250)
            scale = min(cw / max(bw, 1), ch / max(bh, 1)) * 0.82
            ow, oh = bw * scale, bh * scale
            inner = QRectF(cx - ow / 2, cy - oh / 2, ow, oh)
            p.drawEllipse(inner)
            self._stamp_material_and_dims(p, inner, bw, bh)
            return

        if sh == "Треугольник":
            a = float(izd.get("Сторона A (мм)") or 300)
            b = float(izd.get("Сторона B (мм)") or 400)
            c = float(izd.get("Сторона C (мм)") or 500)
            if triangle_exists(int(a), int(b), int(c)):
                pts = _triangle_xy(a, b, c)
                xs = [x for x, _ in pts]
                ys = [y for _, y in pts]
                bw_mm = max(xs) - min(xs)
                bh_mm = max(ys) - min(ys)
                r = map_poly(pts, None)
                if r is not None:
                    self._stamp_material_and_dims(p, r, bw_mm, bh_mm)
            else:
                p.drawText(
                    QRectF(margin, cy - 10, cw, 20),
                    Qt.AlignCenter,
                    "Треугольник: проверьте стороны",
                )
            return

        if sh == "Трапеция":
            b_niz = float(izd.get("Трапеция низ (мм)") or 400)
            b_verh = float(izd.get("Трапеция верх (мм)") or 200)
            hh = float(izd.get("Трапеция высота (мм)") or 300)
            bottom = max(b_niz, b_verh)
            top = min(b_niz, b_verh)
            x0 = -bottom / 2
            x1 = bottom / 2
            xt0 = -top / 2
            xt1 = top / 2
            y0 = 0.0
            y1 = hh
            pts = [(x0, y0), (x1, y0), (xt1, y1), (xt0, y1)]
            r = map_poly(pts, None)
            if r is not None:
                self._stamp_material_and_dims(p, r, bottom, hh)
            return

        if sh == "Сложная фигура":
            bw = float(izd.get("Ширина (мм)") or 300)
            bh = float(izd.get("Высота (мм)") or 200)
            pen2 = QPen(QColor("#888"))
            pen2.setStyle(Qt.DashLine)
            p.setPen(pen2)
            inner = QRectF(cx - 80, cy - 50, 160, 100)
            p.drawRect(inner)
            p.setPen(pen)
            self._stamp_material_and_dims(
                p, inner, bw, bh, center_head="Сложная форма (нужен макет)"
            )
            return

        p.drawText(
            QRectF(margin, cy - 10, cw, 20),
            Qt.AlignCenter,
            "Форма: %s — схема по размерам после «Рассчитать»" % (sh or "—"),
        )


class OrderPreviewDialog(QDialog):
    def __init__(self, main_app: Any, parent=None):
        super().__init__(parent or main_app)
        self._main = main_app
        self.setWindowTitle("Модель и расчёт")
        self.resize(720, 560)
        self.setObjectName("orderPreviewDlg")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._schematic = ShapeSchematic(self)
        row.addWidget(self._schematic, 2)
        self._browser = QTextBrowser(self)
        self._browser.setOpenExternalLinks(False)
        row.addWidget(self._browser, 3)
        root.addLayout(row, 1)

        self.setStyleSheet(
            "QDialog#orderPreviewDlg { background-color: %s; }\n"
            "QAbstractScrollArea { border: none; background-color: %s; }\n"
            "QTextBrowser { background-color: %s; color: #111; border: none; }\n"
            % (PAL.MAIN_WINDOW_BG, PAL.MODEL_PREVIEW_HTML_BG, PAL.MODEL_PREVIEW_HTML_BG)
        )

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        b_close = QPushButton("Закрыть")
        b_close.clicked.connect(self.hide)
        btn_row.addWidget(b_close)
        root.addLayout(btn_row)
        apply_window_icon(self)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_from_main()

    def refresh_from_main(self) -> None:
        m = self._main
        if m is None:
            return
        try:
            deb = getattr(m, "_recalc_debounce", None)
            if deb is not None:
                deb.stop()
            m._recalculate_impl(False)
        except Exception as e:
            self._browser.setHtml(
                "<p style='color:#c00'>Ошибка пересчёта: %s</p>" % str(e).replace("<", "&lt;")
            )
            return
        sel = getattr(m, "selected", {}) or {}
        izd = sel.get("Параметры изделия") or {}
        matp = sel.get("Параметры материала") or {}
        self._schematic.set_izd(izd, matp)
        html = build_html_summary(sel, m)
        self._browser.setHtml(html)
