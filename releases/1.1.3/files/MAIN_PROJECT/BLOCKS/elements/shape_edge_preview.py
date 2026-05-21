# -*- coding: utf-8 -*-
"""Схематичное превью изделия для блоков полировки / шлифовки / фацета."""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

from PyQt5.QtCore import QPointF, QRect, QRectF, Qt
from PyQt5.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import QWidget

from calc import palette as P
from calc.geometry import triangle_exists
from elements.calc_tile_style import TILE_SIDE_PX


def _parse_mm(v, default: Optional[int] = None) -> Optional[int]:
    if v is None or v == "" or v == "—":
        return default
    try:
        n = int(v)
        return n if n > 0 else default
    except (TypeError, ValueError):
        return default


def _draw_text_rotated(p, cx, cy, text, deg=-90.0):
    """Текст по центру (cx,cy), повёрнутый — экономит место у сторон."""
    p.save()
    p.translate(cx, cy)
    p.rotate(deg)
    p.drawText(QRectF(-40, -8, 80, 16), Qt.AlignCenter, str(text))
    p.restore()


def _triangle_mm_centered(
    a: int, b: int, c: int
) -> Tuple[List[Tuple[float, float]], float, float]:
    """Вершины в мм, центр в (0,0); возвращает (points, bbox_w, bbox_h)."""
    if not triangle_exists(a, b, c):
        a = b = c = 300
    cos_angle = (a**2 + c**2 - b**2) / (2 * a * c)
    cos_angle = max(-1.0, min(1.0, cos_angle))
    ang = math.acos(cos_angle)
    x = a * math.cos(ang)
    y = a * math.sin(ang)
    pts = [(0.0, 0.0), (float(c), 0.0), (float(x), float(y))]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    bw = max(maxx - minx, 1.0)
    bh = max(maxy - miny, 1.0)
    cx = (minx + maxx) / 2.0
    cy = (miny + maxy) / 2.0
    centered = [(px - cx, py - cy) for px, py in pts]
    return centered, bw, bh


class ShapeEdgePreview(QWidget):
    """
    Компактное превью под ширину плитки (~TILE_SIDE_PX), без выхода за виджет
    (clip + масштаб по реальным мм), чтобы не наезжало на соседние элементы.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        w = max(132, int(TILE_SIDE_PX * 0.78))
        h = max(96, int(TILE_SIDE_PX * 0.52))
        self.setFixedSize(w, h)
        self._izd: dict = {}

    def set_izd(self, izd: dict):
        self._izd = dict(izd) if izd else {}
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        clip = self.rect().adjusted(2, 2, -2, -2)
        p.setClipRect(clip)
        surf = QColor(P.TILE_SURFACE)
        p.fillRect(self.rect(), surf)
        pen = QPen(QColor("#1a1a1a"))
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(QBrush(surf))
        sh = self._izd.get("Форма", "")
        R = self.rect().adjusted(8, 10, -8, -12)
        cx, cy = R.center().x(), R.center().y()
        f = p.font()
        f.setPointSize(7)
        f.setBold(True)
        p.setFont(f)

        if sh == "Прямоугольник":
            self._paint_rectangle(p, R, cx, cy)
        elif sh == "Круг":
            self._paint_circle(p, R, cx, cy)
        elif sh == "Овал":
            self._paint_oval(p, R, cx, cy)
        elif sh == "Треугольник":
            self._paint_triangle(p, R, cx, cy)
        elif sh == "Трапеция":
            self._paint_trapezoid(p, R, cx, cy)
        else:
            self._paint_complex(p, R)

    def _inner_draw_rect(self, R: QRect) -> QRect:
        """Область под фигуру с запасом под подписи размеров."""
        return R.adjusted(18, 14, -18, -14)

    def _scale_to_fit(self, C: QRect, bw: float, bh: float) -> float:
        if bw <= 0 or bh <= 0:
            return 1.0
        return min(C.width() / bw, C.height() / bh) * 0.92

    def _paint_rectangle(self, p: QPainter, R: QRect, cx: int, cy: int):
        C = self._inner_draw_rect(R)
        w_mm = _parse_mm(self._izd.get("Ширина (мм)"), 400) or 400
        h_mm = _parse_mm(self._izd.get("Высота (мм)"), 300) or 300
        s = self._scale_to_fit(C, float(w_mm), float(h_mm))
        iw, ih = w_mm * s, h_mm * s
        iw = max(iw, 20.0)
        ih = max(ih, 18.0)
        if iw > C.width() or ih > C.height():
            s2 = min(C.width() / iw, C.height() / ih)
            iw, ih = iw * s2, ih * s2
        x0, y0 = cx - iw / 2.0, cy - ih / 2.0
        p.drawRect(int(round(x0)), int(round(y0)), int(round(iw)), int(round(ih)))
        wv = self._izd.get("Ширина (мм)", "—")
        hv = self._izd.get("Высота (мм)", "—")
        p.setPen(QColor("#222"))
        p.drawText(
            QRect(int(x0), int(y0) - 14, int(iw), 12), Qt.AlignCenter, str(wv)
        )
        p.drawText(
            QRect(int(x0), int(y0 + ih) + 1, int(iw), 12), Qt.AlignCenter, str(wv)
        )
        band = max(10, min(14, int(iw * 0.08)))
        _draw_text_rotated(p, int(x0) + band // 2, int(y0 + ih // 2), hv, -90)
        _draw_text_rotated(
            p, int(x0 + iw) - band // 2, int(y0 + ih // 2), hv, -90
        )

    def _paint_circle(self, p: QPainter, R: QRect, cx: int, cy: int):
        C = self._inner_draw_rect(R)
        d_mm = _parse_mm(self._izd.get("Диаметр (мм)"), 280) or 280
        side = min(C.width(), C.height())
        s = (side / float(d_mm)) * 0.9
        r = d_mm * s / 2.0
        r = max(r, 10.0)
        if 2 * r > min(C.width(), C.height()):
            r = min(C.width(), C.height()) / 2.0 - 2
        p.drawEllipse(QPointF(cx, cy), r, r)
        dv = self._izd.get("Диаметр (мм)", "—")
        p.setPen(QColor("#1a1a1a"))
        ir = int(max(r - 2, 8))
        p.drawText(
            QRect(int(cx - ir), int(cy - ir), 2 * ir, 2 * ir),
            Qt.AlignCenter,
            "⌀ %s" % dv,
        )

    def _paint_oval(self, p: QPainter, R: QRect, cx: int, cy: int):
        C = self._inner_draw_rect(R)
        w_mm = _parse_mm(self._izd.get("Ширина (мм)"), 360) or 360
        h_mm = _parse_mm(self._izd.get("Высота (мм)"), 220) or 220
        s = self._scale_to_fit(C, float(w_mm), float(h_mm))
        ow, oh = w_mm * s / 2.0, h_mm * s / 2.0
        ow = max(ow, 12.0)
        oh = max(oh, 10.0)
        if ow * 2 > C.width() or oh * 2 > C.height():
            t = min(C.width() / (2 * ow), C.height() / (2 * oh))
            ow, oh = ow * t, oh * t
        p.drawEllipse(QPointF(cx, cy), ow, oh)
        p.setPen(QColor("#222"))
        p.drawText(
            QRect(int(cx - ow), int(cy - oh) - 14, int(2 * ow), 12),
            Qt.AlignCenter,
            str(self._izd.get("Ширина (мм)", "—")),
        )
        _draw_text_rotated(
            p, int(cx + ow) + 8, int(cy), self._izd.get("Высота (мм)", "—"), -90
        )

    def _paint_triangle(self, p: QPainter, R: QRect, cx: int, cy: int):
        C = self._inner_draw_rect(R)
        a = _parse_mm(self._izd.get("Сторона A (мм)"), 300) or 300
        b = _parse_mm(self._izd.get("Сторона B (мм)"), 300) or 300
        c = _parse_mm(self._izd.get("Сторона C (мм)"), 300) or 300
        pts_mm, bw, bh = _triangle_mm_centered(a, b, c)
        s = self._scale_to_fit(C, bw, bh)
        poly = QPolygonF()
        for px, py in pts_mm:
            sx = cx + px * s
            sy = cy - py * s
            poly.append(QPointF(sx, sy))
        p.drawPolygon(poly)
        p.setPen(QColor("#222"))
        av, bv, cv = (
            self._izd.get("Сторона A (мм)", "—"),
            self._izd.get("Сторона B (мм)", "—"),
            self._izd.get("Сторона C (мм)", "—"),
        )
        bx = poly.boundingRect()
        p.drawText(
            QRect(int(bx.center().x() - 28), int(bx.top() - 2), 56, 12),
            Qt.AlignCenter,
            str(av),
        )
        p.drawText(
            QRect(int(bx.left()) - 2, int(bx.bottom()) - 4, 40, 12),
            Qt.AlignLeft,
            str(bv),
        )
        p.drawText(
            QRect(int(bx.right()) - 38, int(bx.bottom()) - 4, 40, 12),
            Qt.AlignRight,
            str(cv),
        )

    def _paint_trapezoid(self, p: QPainter, R: QRect, cx: int, cy: int):
        C = self._inner_draw_rect(R)
        t = _parse_mm(self._izd.get("Кромка верх (мм)"), None)
        b = _parse_mm(self._izd.get("Кромка низ (мм)"), None)
        hh = _parse_mm(
            self._izd.get("Трапеция высота (мм)"),
            _parse_mm(self._izd.get("Высота (мм)"), None),
        )
        if t is None:
            t = _parse_mm(self._izd.get("Трапеция верх (мм)"), 200) or 200
        if b is None:
            b = _parse_mm(self._izd.get("Трапеция низ (мм)"), 320) or 320
        if hh is None:
            hh = 140
        tw, bw = float(min(t, b)), float(max(t, b))
        hh = float(hh)
        w_max = max(tw, bw)
        s = self._scale_to_fit(C, w_max, hh)
        tws, bws, hhs = tw * s, bw * s, hh * s
        y_top, y_bot = -hhs / 2.0, hhs / 2.0
        poly = QPolygonF(
            [
                QPointF(cx - bws / 2, cy + y_bot),
                QPointF(cx + bws / 2, cy + y_bot),
                QPointF(cx + tws / 2, cy + y_top),
                QPointF(cx - tws / 2, cy + y_top),
            ]
        )
        p.drawPolygon(poly)
        p.setPen(QColor("#222"))
        tv = self._izd.get("Кромка верх (мм)", self._izd.get("Трапеция верх (мм)", "—"))
        bv = self._izd.get("Кромка низ (мм)", self._izd.get("Трапеция низ (мм)", "—"))
        le = self._izd.get("Кромка лево (мм)", "—")
        ri = self._izd.get("Кромка право (мм)", "—")
        br = poly.boundingRect()
        p.drawText(
            QRect(int(br.center().x() - 40), int(br.top() - 14), 80, 10),
            Qt.AlignCenter,
            str(tv or "—"),
        )
        p.drawText(
            QRect(int(br.center().x() - 40), int(br.bottom()) + 2, 80, 10),
            Qt.AlignCenter,
            str(bv or "—"),
        )
        _draw_text_rotated(p, int(br.left() - 4), int(br.center().y()), le, -90)
        _draw_text_rotated(p, int(br.right() + 4), int(br.center().y()), ri, -90)

    def _paint_complex(self, p: QPainter, R: QRect):
        C = self._inner_draw_rect(R)
        cx, cy = R.center().x(), R.center().y()
        w_mm = _parse_mm(self._izd.get("Ширина (мм)"), 400) or 400
        h_mm = _parse_mm(self._izd.get("Высота (мм)"), 280) or 280
        s = self._scale_to_fit(C, float(w_mm), float(h_mm))
        iw, ih = w_mm * s, h_mm * s
        iw = max(iw, 24.0)
        ih = max(ih, 20.0)
        if iw > C.width() or ih > C.height():
            t = min(C.width() / iw, C.height() / ih)
            iw, ih = iw * t, ih * t
        x0, y0 = cx - iw / 2.0, cy - ih / 2.0
        p.setBrush(QBrush(QColor(P.TILE_SURFACE)))
        rr = QRect(int(round(x0)), int(round(y0)), int(round(iw)), int(round(ih)))
        p.drawRect(rr)
        p.setPen(QColor("#222"))
        f2 = p.font()
        f2.setPointSize(8)
        p.setFont(f2)
        p.drawText(rr, Qt.AlignCenter, "Сложная\nфигура")
