# -*- coding: utf-8 -*-
"""Сессия раскроя по материалу: авто-карточки листов + конструктор (DnD)."""
from __future__ import annotations

import copy
import json
import math
import os
import sys
from typing import Dict, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5 import sip
from PyQt5.QtCore import (
    Qt,
    QMimeData,
    QTimer,
    QRect,
    QPoint,
    pyqtSignal,
    QMargins,
)
from PyQt5.QtGui import QDrag, QPainter, QColor, QPen, QBrush, QFont, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox,
    QScrollArea, QWidget, QFrame, QStackedWidget, QGridLayout, QSlider,
    QSizePolicy, QMenu,
)

from db import models
from db.trace import ui as _mc_trace_ui
from logic.cutting_algorithm import (
    apply_cut_segments_to_free_rects,
    assign_chocolate_bar_cut_segments_to_layout,
    merge_adjacent_free_rects_for_display,
    collapse_session_if_one_sheet_fits_all,
    compute_cut_session_layouts,
    compute_layout_variants_for_one_sheet,
    dedupe_layouts_pieces_prefer_sheet,
    optimize_layout_after_sheet_rotate,
    redistribute_session_after_sheet_edit,
    remap_manual_sheet_indices,
    recompute_free_rects_from_pieces,
    repack_pieces_on_sheet,
    squash_session_layouts_to_largest_sheet_if_repack,
    _layout_score_for_variant,
    _min_strip_for_thickness,
    _unit_item_cut_dims,
)
from ui.create_cut_dialog import ChooseSheetDialog, STYLE, EDGE_LABELS
from ui.cutting_result_dialog import ChooseVariantDialog, CuttingResultDialog
from ui.cut_commit import commit_cut_session
from ui.layout_edit_dialog import LayoutEditCanvas, _rect_crosses_cut_segment


MIME_PIECE_UID = "application/x-mc-cut-uid"


def _layout_to_forced_sheet_descriptor(lay: dict) -> dict:
    """Описание листа для forced_slot_sheets из текущего макета (тот же формат, что у склада)."""
    out = {
        'id': lay.get('sheet_id'),
        'width_mm': int(lay.get('sheet_width') or 0),
        'height_mm': int(lay.get('sheet_height') or 0),
        'sheet_type': lay.get('sheet_type') or 'full',
        'thickness_mm': lay.get('thickness_mm', 4),
    }
    if lay.get('in_work_order_id') is not None:
        out['in_work_order_id'] = lay['in_work_order_id']
        out['in_work_sheet_index'] = lay.get('in_work_sheet_index', 0)
        if lay.get('in_work_rect'):
            out['in_work_rect'] = dict(lay['in_work_rect'])
        pcs = lay.get('pieces') or []
        if pcs:
            out['saved_layout'] = {
                'sheet_width': int(lay.get('sheet_width') or 0),
                'sheet_height': int(lay.get('sheet_height') or 0),
                'pieces': copy.deepcopy(pcs),
                'rotated': bool(lay.get('rotated')),
                'thickness_mm': lay.get('thickness_mm', 4),
                'material': lay.get('material'),
            }
    return out


# Оформление страницы конструктора — те же кнопки/акценты, что в ui.create_cut_dialog.STYLE
CONSTRUCTOR_PAGE_QSS = """
QWidget#constructorPage {
    background-color: #0a0a0a;
}
QWidget#constructorHeaderCell {
    background-color: transparent;
}
QLabel#constructorTitle {
    background-color: transparent;
    color: #E6F2FF;
    font-size: 20px;
    font-weight: bold;
    padding: 0;
}
QLabel#constructorStatsLabel {
    background-color: transparent;
    color: #C8D9EE;
    font-size: 12px;
    padding: 4px 0;
}
QPushButton {
    background-color: #4682B4;
    color: white;
    border: none;
    border-radius: 5px;
    padding: 6px 14px;
    font-size: 12px;
    min-height: 26px;
}
QPushButton:hover { background-color: #5A9BD5; }
QPushButton:pressed { background-color: #3A6B94; }
QPushButton:checked {
    background-color: #3A6B94;
    border: 1px solid #7B9BC1;
}
QPushButton#primary {
    background-color: #2E7D32;
    min-height: 28px;
}
QPushButton#primary:hover { background-color: #388E3C; }
QPushButton#primary:pressed { background-color: #27642A; }
QPushButton#danger {
    background-color: #B22222;
    min-height: 26px;
}
QPushButton#danger:hover { background-color: #CD5C5C; }
QPushButton#danger:pressed { background-color: #8B1A1A; }
QPushButton#constructorMuted {
    background-color: #5C7A99;
    color: white;
}
QPushButton#constructorMuted:hover { background-color: #6D8CB0; }
QPushButton#constructorMuted:pressed { background-color: #4A627A; }
QPushButton#constructorPageNav {
    min-width: 34px;
    max-width: 40px;
    padding: 4px 8px;
}
QScrollArea#constructorPoolScroll {
    background: transparent;
    border: none;
}
QScrollArea#constructorPoolScroll QWidget#qt_scrollarea_viewport {
    background: transparent;
}
QWidget#constructorSheetsHost {
    background-color: #0a0a0a;
}
QLabel#constructorPageLabel {
    background-color: transparent;
    color: #E6F2FF;
    font-size: 13px;
    font-weight: bold;
    padding: 0 10px;
}
QSlider#constructorZoomSlider {
    background-color: transparent;
    border: none;
}
QSlider#constructorZoomSlider::groove:horizontal {
    height: 5px;
    background: #2a3544;
    border-radius: 3px;
    border: none;
}
QSlider#constructorZoomSlider::handle:horizontal {
    background: #4A86E8;
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
    border: none;
}
QSlider#constructorZoomSlider::add-page:horizontal,
QSlider#constructorZoomSlider::sub-page:horizontal {
    background: transparent;
}

/* Панель под листом: стили только здесь — у самого виджета панели нельзя вызывать setStyleSheet(),
   иначе в Qt обрывается каскад от #constructorPage и кнопки остаются без цветов. */
QWidget#constructorSheetPanel {
    background-color: transparent;
}
QWidget#constructorHVPair {
    background-color: transparent;
}
QWidget#constructorSheetPanel QPushButton#constructorSheetBtnRotateBlue {
    background-color: #4A76A8;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 3px 6px;
    font-size: 12px;
    font-weight: 600;
    min-height: 22px;
    max-height: 26px;
}
QWidget#constructorSheetPanel QPushButton#constructorSheetBtnRotateBlue:hover { background-color: #5588BE; }
QWidget#constructorSheetPanel QPushButton#constructorSheetBtnRotateBlue:pressed { background-color: #3D6594; }
QWidget#constructorSheetPanel QPushButton#constructorSheetBtnDarkGreen {
    background-color: #1E5935;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 3px 6px;
    font-size: 12px;
    font-weight: 600;
    min-height: 22px;
    max-height: 26px;
}
QWidget#constructorSheetPanel QPushButton#constructorSheetBtnDarkGreen:hover { background-color: #277548; }
QWidget#constructorSheetPanel QPushButton#constructorSheetBtnDarkGreen:pressed { background-color: #164228; }
QWidget#constructorSheetPanel QPushButton#constructorSheetBtnRedOrange {
    background-color: #C84040;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 3px 6px;
    font-size: 12px;
    font-weight: 600;
    min-height: 22px;
    max-height: 26px;
}
QWidget#constructorSheetPanel QPushButton#constructorSheetBtnRedOrange:hover { background-color: #D85A5A; }
QWidget#constructorSheetPanel QPushButton#constructorSheetBtnRedOrange:pressed { background-color: #A83535; }
QWidget#constructorSheetPanel QPushButton#constructorSheetBtnMaroon {
    background-color: #7B1D1D;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 3px 6px;
    font-size: 12px;
    font-weight: 600;
    min-height: 22px;
    max-height: 26px;
}
QWidget#constructorSheetPanel QPushButton#constructorSheetBtnMaroon:hover { background-color: #922A2A; }
QWidget#constructorSheetPanel QPushButton#constructorSheetBtnMaroon:pressed { background-color: #5C1010; }
QWidget#constructorSheetPanel QPushButton:disabled {
    background-color: #4a4a4a;
    color: #aaaaaa;
    border: 1px solid #555;
}
"""


def _draw_piece_labels_on_rect(
    qp: QPainter,
    x: float,
    y: float,
    rw: float,
    rh: float,
    width_mm: int,
    height_mm: int,
    edge_treatment: Optional[dict] = None,
    recipient: str = "",
) -> None:
    """Подписи на выкрое как в PartPreview (размеры по сторонам + кромки)."""
    if rw < 2 or rh < 2:
        return
    et = edge_treatment or {}
    qp.setPen(QColor(0, 0, 0))
    fs = max(6, min(9, int(min(rw, rh) / 8)))
    qp.setFont(QFont("Arial", fs))

    def _edge_label(side: str):
        v = et.get(side)
        if v == 'facet':
            return "Ф %s" % (et.get('facet_mm') or 15)
        return EDGE_LABELS.get(v) if v else None

    if rw < 52 or rh < 36:
        qp.drawText(
            int(x), int(y), int(rw), int(rh),
            Qt.AlignCenter,
            "%d × %d" % (int(width_mm), int(height_mm)),
        )
        if (recipient or "").strip() and rw > 28:
            qp.setFont(QFont("Arial", max(6, fs - 1)))
            qp.drawText(
                int(x), int(y), int(rw), int(rh),
                Qt.AlignCenter | Qt.AlignBottom,
                recipient.strip()[:12],
            )
        return

    h_str = str(int(height_mm))
    for side, x_pos in [('left', x - 6), ('right', x + rw + 2)]:
        label = _edge_label(side)
        line = h_str + (" " + label if label else "")
        qp.save()
        qp.translate(x_pos, y + rh / 2)
        qp.rotate(-90)
        qp.drawText(int(-8), int(4), line)
        qp.restore()
    w_str = str(int(width_mm))
    for side, y_pos in [('top', y - 3), ('bottom', y + rh + 11)]:
        label = _edge_label(side)
        line = w_str + (" " + label if label else "")
        cx = x + rw / 2
        fm = qp.fontMetrics()
        tw = fm.horizontalAdvance(line)
        qp.drawText(int(cx - tw / 2), int(y_pos), line)
    rec = (recipient or "").strip()
    if rec:
        qp.setFont(QFont("Arial", max(7, fs - 1)))
        qp.drawText(int(x + 3), int(y + rh / 2 - 4), rec[:14])
MIME_SOURCE_SHEET = "application/x-mc-cut-source-sheet"  # -1 = пул; иначе индекс листа в конструкторе


def _rects_overlap_mm(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def _can_place_piece(
    sw: int,
    sh: int,
    pieces: List[dict],
    x: int,
    y: int,
    w: int,
    h: int,
    exclude_uid: Optional[str] = None,
    cut_segments: Optional[List[dict]] = None,
) -> bool:
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > sw or y + h > sh:
        return False
    for seg in cut_segments or []:
        if _rect_crosses_cut_segment(x, y, w, h, seg):
            return False
    r = (x, y, w, h)
    for p in pieces:
        uid = p.get('piece_uid')
        if exclude_uid is not None and uid == exclude_uid:
            continue
        pr = (int(p.get('x') or 0), int(p.get('y') or 0), int(p.get('w') or 0), int(p.get('h') or 0))
        if _rects_overlap_mm(r, pr):
            return False
    return True


TOUCH_TOLERANCE_MM = 2.0


def _touch_count_rect(
    sw: int,
    sh: int,
    pieces: List[dict],
    x: int,
    y: int,
    w: int,
    h: int,
    exclude_uid: Optional[str] = None,
) -> int:
    """Число «касаний» листа и других деталей (как в layout_edit_dialog.LayoutEditCanvas)."""
    left, right = x, x + w
    top, bottom = y, y + h
    count = 0
    if left <= 0:
        count += 1
    if right >= sw:
        count += 1
    if top <= 0:
        count += 1
    if bottom >= sh:
        count += 1
    for p in pieces:
        if exclude_uid is not None and p.get('piece_uid') == exclude_uid:
            continue
        qx, qy = int(p.get('x') or 0), int(p.get('y') or 0)
        qw, qh = int(p.get('w') or 0), int(p.get('h') or 0)
        if abs(left - (qx + qw)) <= TOUCH_TOLERANCE_MM:
            count += 1
        if abs(right - qx) <= TOUCH_TOLERANCE_MM:
            count += 1
        if abs(top - (qy + qh)) <= TOUCH_TOLERANCE_MM:
            count += 1
        if abs(bottom - qy) <= TOUCH_TOLERANCE_MM:
            count += 1
    return count


def _valid_placements_rect(
    sw: int,
    sh: int,
    pieces: List[dict],
    w: int,
    h: int,
    exclude_uid: Optional[str] = None,
    cut_segments: Optional[List[dict]] = None,
) -> List[Tuple[int, int, int]]:
    """Все допустимые (x, y) с хотя бы одним касанием; элементы (x, y, touches)."""
    if w > sw or h > sh:
        return []
    x_targets = [0, sw - w]
    y_targets = [0, sh - h]
    for p in pieces:
        if exclude_uid is not None and p.get('piece_uid') == exclude_uid:
            continue
        qx, qy = int(p.get('x') or 0), int(p.get('y') or 0)
        qw, qh = int(p.get('w') or 0), int(p.get('h') or 0)
        x_targets.append(qx + qw)
        x_targets.append(qx - w)
        y_targets.append(qy + qh)
        y_targets.append(qy - h)
    x_lo, x_hi = 0, sw - w
    y_lo, y_hi = 0, sh - h
    out: List[Tuple[int, int, int]] = []
    for tx in x_targets:
        if tx < x_lo or tx > x_hi:
            continue
        for ty in y_targets:
            if ty < y_lo or ty > y_hi:
                continue
            if _can_place_piece(sw, sh, pieces, tx, ty, w, h, exclude_uid, cut_segments):
                touches = _touch_count_rect(sw, sh, pieces, tx, ty, w, h, exclude_uid)
                if touches >= 1:
                    out.append((tx, ty, touches))
    return out


def _valid_placements_union_orientations(
    sw: int,
    sh: int,
    pieces: List[dict],
    w: int,
    h: int,
    exclude_uid: Optional[str] = None,
    cut_segments: Optional[List[dict]] = None,
) -> List[Tuple[int, int, int, int, int]]:
    """Допустимые позиции с учётом поворота 90°: (px, py, touches, wp, hp)."""
    orients: List[Tuple[int, int]] = [(w, h)]
    if w != h:
        orients.append((h, w))
    best: Dict[Tuple[int, int, int, int], int] = {}
    for wp, hp in orients:
        for px, py, touches in _valid_placements_rect(sw, sh, pieces, wp, hp, exclude_uid, cut_segments):
            key = (px, py, wp, hp)
            if touches > best.get(key, -1):
                best[key] = touches
    return [(px, py, t, wp, hp) for (px, py, wp, hp), t in best.items()]


def _placement_snap_from_valid_multi(
    sx: float,
    sy: float,
    valid: List[Tuple[int, int, int, int, int]],
    skip_rect: Optional[Tuple[int, int, int, int]] = None,
) -> Optional[Tuple[int, int, int, int]]:
    """Выбор (px, py, wp, hp) по курсору; skip_rect — текущий прямоугольник детали при перетаскивании."""
    inside: List[Tuple[int, float, int, int, int, int]] = []
    all_c: List[Tuple[int, float, int, int, int, int]] = []
    for px, py, touches, wp, hp in valid:
        if skip_rect is not None and (px, py, wp, hp) == skip_rect:
            continue
        cx, cy = px + wp / 2, py + hp / 2
        dist_sq = (sx - cx) ** 2 + (sy - cy) ** 2
        t = (touches, dist_sq, px, py, wp, hp)
        all_c.append(t)
        if px <= sx <= px + wp and py <= sy <= py + hp:
            inside.append(t)
    pool = inside if inside else all_c
    if not pool:
        return None
    pool.sort(key=lambda c: (-c[0], c[1]))
    _, _, px, py, wp, hp = pool[0]
    return (px, py, wp, hp)


def _placement_at_rect(
    sw: int,
    sh: int,
    pieces: List[dict],
    w: int,
    h: int,
    sx: float,
    sy: float,
    exclude_uid: Optional[str] = None,
    skip_xy: Optional[Tuple[int, int]] = None,
) -> Optional[Tuple[int, int]]:
    """Точка (sx,sy) в мм: если попадает в одну из допустимых зон — лучшая позиция (как _placement_at в OLD)."""
    candidates: List[Tuple[int, float, int, int]] = []
    for px, py, touches in _valid_placements_rect(sw, sh, pieces, w, h, exclude_uid):
        if skip_xy is not None and (px, py) == (skip_xy[0], skip_xy[1]):
            continue
        if px <= sx <= px + w and py <= sy <= py + h:
            cx, cy = px + w / 2, py + h / 2
            dist_sq = (sx - cx) ** 2 + (sy - cy) ** 2
            candidates.append((touches, dist_sq, px, py))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c[0], c[1]))
    return (candidates[0][2], candidates[0][3])


def _placement_snap_from_valid(
    w: int,
    h: int,
    sx: float,
    sy: float,
    valid_list: List[Tuple[int, int, int]],
    skip_xy: Optional[Tuple[int, int]] = None,
) -> Optional[Tuple[int, int]]:
    """
    Если курсор (sx,sy) не внутри ни одной зоны — всё равно выбрать лучшую позицию
    (ближе к курсору, с приоритетом большего числа касаний). Иначе DnD почти всегда ignore().
    """
    inside: List[Tuple[int, float, int, int]] = []
    all_c: List[Tuple[int, float, int, int]] = []
    for px, py, touches in valid_list:
        if skip_xy is not None and (px, py) == (skip_xy[0], skip_xy[1]):
            continue
        cx, cy = px + w / 2, py + h / 2
        dist_sq = (sx - cx) ** 2 + (sy - cy) ** 2
        all_c.append((touches, dist_sq, px, py))
        if px <= sx <= px + w and py <= sy <= py + h:
            inside.append((touches, dist_sq, px, py))
    pool = inside if inside else all_c
    if not pool:
        return None
    pool.sort(key=lambda c: (-c[0], c[1]))
    return (pool[0][2], pool[0][3])


def _mime_source_sheet(mime: QMimeData) -> int:
    if not mime.hasFormat(MIME_SOURCE_SHEET):
        return -1
    try:
        return int(bytes(mime.data(MIME_SOURCE_SHEET)).decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return -1


def list_stock_sheets_for_material(mat: str, th: Optional[int]) -> List[dict]:
    """Целые листы, остатки и листы «в работе» для материала (формат раскроя)."""
    thick = int(th) if th is not None else 4
    remnants = models.get_remnants_by_material_and_thickness(mat, thick)
    in_work = models.get_sheets_in_work_for_material_thickness(mat, thick)
    full = models.get_full_sheets_by_material_and_thickness(mat, thick)
    sheets = []
    for r in remnants:
        sheets.append({
            'id': r['id'], 'width_mm': r['width_mm'], 'height_mm': r['height_mm'],
            'sheet_type': 'remnant', 'thickness_mm': r.get('thickness_mm', thick),
        })
    for idx, s in enumerate(in_work):
        uid = models.in_work_pool_entry_id(s['order_id'], s.get('sheet_index', 0), idx)
        row = {
            'id': uid,
            'width_mm': s['rect_w'],
            'height_mm': s['rect_h'],
            'sheet_type': 'in_work',
            'thickness_mm': s['thickness_mm'],
            'in_work_order_id': s['order_id'],
            'in_work_sheet_index': s.get('sheet_index', 0),
            'in_work_rect': {'x': s['rect_x'], 'y': s['rect_y'], 'w': s['rect_w'], 'h': s['rect_h']},
            'no_cuts_yet': bool(s.get('no_cuts_yet', True)),
            'planned_piece_count': int(s.get('planned_piece_count') or 0),
        }
        if s.get('saved_layout') is not None:
            row['saved_layout'] = copy.deepcopy(s['saved_layout'])
        sheets.append(row)
    for f in full:
        qty = max(1, int(f.get('quantity') or 1))
        for _ in range(qty):
            sheets.append({
                'id': f['id'],
                'width_mm': f['width_mm'],
                'height_mm': f['height_mm'],
                'sheet_type': 'full',
                'thickness_mm': f.get('thickness_mm', thick),
            })
    type_rank = {'in_work': 0, 'remnant': 1, 'full': 2}
    sheets.sort(
        key=lambda s: (
            type_rank.get(s.get('sheet_type') or 'full', 9),
            (int(s.get('width_mm') or 0) * int(s.get('height_mm') or 0)),
        )
    )
    return sheets


def expand_parts_to_units(all_parts: List[dict]) -> Tuple[List[dict], Dict[str, dict]]:
    """Развернуть quantity в единичные записи с piece_uid; вернуть unit_items и uid -> meta."""
    units: List[dict] = []
    by_uid: Dict[str, dict] = {}
    for pi, p in enumerate(all_parts):
        mat = (p.get('material_name') or '').strip()
        th = int(p.get('thickness_mm') or 4)
        qty = max(1, int(p.get('quantity') or 1))
        oid = p.get('source_order_id')
        pid = p.get('bundle_product_id') or str(pi)
        for k in range(qty):
            uid = "%s:%s:%d" % (oid if oid is not None else "o", pid, k)
            u = {
                'material_name': mat,
                'thickness_mm': th,
                'width_mm': int(p.get('width_mm') or 0),
                'height_mm': int(p.get('height_mm') or 0),
                'recipient_text': p.get('recipient_text'),
                'edge_treatment': p.get('edge_treatment') or {},
                'piece_uid': uid,
                'source_order_id': oid,
                'bundle_product_id': p.get('bundle_product_id'),
            }
            units.append(u)
            by_uid[uid] = u
    return units, by_uid


class LongPressSheetCard(QFrame):
    """Карточка листа: удержание 1 с — полоска прогресса; отпускание — сброс."""

    def __init__(self, sheet_index: int, parent=None):
        super().__init__(parent)
        self._sheet_index = sheet_index
        self.open_scheme_requested = None  # callable(index)
        self.progress = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._hold_ms = 0
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setMinimumHeight(56)
        self.setMouseTracking(True)
        inner = QVBoxLayout(self)
        inner.setContentsMargins(8, 8, 8, 8)
        self._lbl = QLabel("")
        self._lbl.setWordWrap(True)
        self._lbl.setStyleSheet("background: transparent;")
        self._lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        inner.addWidget(self._lbl)

    def setText(self, text: str):
        self._lbl.setText(text)

    def set_sheet_index(self, idx: int):
        self._sheet_index = idx

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = e.pos()
            self._hold_ms = 0
            self.progress = 0.0
            self._timer.start()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._timer.stop()
        self.progress = 0.0
        self.update()
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e):
        self._timer.stop()
        self.progress = 0.0
        self.update()
        super().leaveEvent(e)

    def _tick(self):
        self._hold_ms += 16
        self.progress = min(1.0, self._hold_ms / 1000.0)
        self.update()
        if self._hold_ms >= 1000:
            self._timer.stop()
            self.progress = 0.0
            if callable(self.open_scheme_requested):
                self.open_scheme_requested(self._sheet_index)
            self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.progress <= 0:
            return
        qp = QPainter(self)
        qp.fillRect(0, 0, int(self.width() * self.progress), self.height(), QColor(70, 130, 180, 100))


class DraggablePoolChip(QLabel):
    def __init__(self, uid: str, meta: dict, parent=None):
        w, h = int(meta.get('width_mm') or 0), int(meta.get('height_mm') or 0)
        super().__init__("%d × %d" % (w, h), parent)
        self._uid = uid
        self._drag_start_pos: Optional[QPoint] = None
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(78, 44)
        self.setStyleSheet(
            "background:#9EC5E8; color:#0d1b2a; border:2px solid #5C8CC4; border-radius:8px; "
            "font-weight:600; font-size:11px;"
        )

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_start_pos = QPoint(e.pos())
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_start_pos = None
        super().mouseReleaseEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() != Qt.LeftButton or self._drag_start_pos is None:
            return super().mouseMoveEvent(e)
        if (e.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return super().mouseMoveEvent(e)
        self._drag_start_pos = None
        drag = QDrag(self)
        md = QMimeData()
        md.setData(MIME_PIECE_UID, self._uid.encode('utf-8'))
        md.setData(MIME_SOURCE_SHEET, str(-1).encode('utf-8'))
        drag.setMimeData(md)
        pm = self.grab()
        if pm.isNull() or pm.width() < 2:
            pm = QPixmap(self.size())
            pm.fill(QColor(158, 194, 237))
            pnt = QPainter(pm)
            pnt.setPen(QColor(13, 27, 42))
            pnt.setFont(self.font())
            pnt.drawText(pm.rect(), Qt.AlignCenter, self.text())
            pnt.end()
        drag.setPixmap(pm)
        drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
        # Copy с пула; Move — если цель попросит (совместимость с accept)
        drag.exec_(Qt.CopyAction | Qt.MoveAction)


class PoolDropHost(QWidget):
    """Зона плиток сверху: подсветка при перетаскивании детали с листа; сброс на пул."""

    piece_return_to_pool = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._drop_highlight = False
        self.setStyleSheet("background: #0a0a0a;")

    def dragEnterEvent(self, e):
        if not e.mimeData().hasFormat(MIME_PIECE_UID):
            e.ignore()
            return
        if _mime_source_sheet(e.mimeData()) >= 0:
            e.acceptProposedAction()
            self._drop_highlight = True
            self.update()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if not e.mimeData().hasFormat(MIME_PIECE_UID):
            e.ignore()
            return
        if _mime_source_sheet(e.mimeData()) >= 0:
            e.acceptProposedAction()
        else:
            e.ignore()
        self.update()

    def dragLeaveEvent(self, e):
        self._drop_highlight = False
        self.update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        self._drop_highlight = False
        mime = e.mimeData()
        if not mime.hasFormat(MIME_PIECE_UID):
            e.ignore()
            self.update()
            return
        uid = bytes(mime.data(MIME_PIECE_UID)).decode('utf-8')
        src = _mime_source_sheet(mime)
        if src >= 0:
            self.piece_return_to_pool.emit(uid, src)
            e.acceptProposedAction()
        else:
            e.ignore()
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._drop_highlight:
            qp = QPainter(self)
            qp.setPen(QPen(QColor(129, 199, 132), 3))
            qp.setBrush(QBrush(QColor(80, 200, 120, 70)))
            qp.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 8, 8)


class ConstructorLogoWidget(QWidget):
    """Мини-логотип в цветах приложения (как акценты в create_cut_dialog / PartPreview)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(52, 52)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        frame = 2
        inner = 2
        rx, ry, rw, rh = frame, frame, w - 2 * frame, h - 2 * frame
        painter.setPen(QPen(QColor(70, 130, 180), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(int(rx), int(ry), int(rw), int(rh), 6, 6)
        ix = rx + inner
        iy = ry + inner
        iw = rw - 2 * inner
        ih = rh - 2 * inner
        gap = 3
        cw = max(1, int((iw - gap) / 2))
        ch = max(1, int((ih - gap) / 2))
        steel = QColor(70, 130, 180)
        green = QColor(46, 125, 50)
        ice = QColor(240, 248, 255)
        danger = QColor(178, 34, 34)
        cells = [
            (int(ix), int(iy), cw, ch, steel),
            (int(ix + cw + gap), int(iy), cw, ch, green),
            (int(ix), int(iy + ch + gap), cw, ch, ice),
            (int(ix + cw + gap), int(iy + ch + gap), cw, ch, danger),
        ]
        for x, y, cw0, ch0, col in cells:
            painter.setPen(Qt.NoPen)
            painter.setBrush(col)
            painter.drawRoundedRect(x, y, cw0, ch0, 3, 3)


class CutMaterialSessionDialog(QDialog):
    def __init__(
        self,
        parent=None,
        all_parts: Optional[List[dict]] = None,
        pin_order_id: Optional[int] = None,
        bundle_client_name: str = "",
    ):
        super().__init__(parent)
        _mc_trace_ui("CutMaterialSessionDialog: __init__ start")
        self._pin_order_id = pin_order_id
        self._bundle_client_name = (bundle_client_name or "").strip()
        self._line_items = [dict(p) for p in (all_parts or [])]
        self._combine_order_id = None
        self._cut_saved_order_id: Optional[int] = None

        self._unit_items, self._unit_by_uid = expand_parts_to_units(self._line_items)
        _mc_trace_ui(
            "CutMaterialSessionDialog: units expanded, count=%d" % len(self._unit_items)
        )
        self._session_layouts: List[dict] = []
        self._constructor_layouts: List[dict] = []
        self._manual_sheet_indices: Set[int] = set()
        self._forced_slots: List[Optional[dict]] = []
        self._constructor_page_index: int = 0
        self._pre_constructor_geom = None
        self._pre_constructor_maximized: bool = False

        self.setWindowTitle("Раскрой по материалу — план листов")
        self.setMinimumSize(720, 520)
        self.setStyleSheet(STYLE)

        root = QVBoxLayout(self)
        self._root_layout = root
        self._root_margins_auto = QMargins(root.contentsMargins())
        self._stack = QStackedWidget(self)
        root.addWidget(self._stack, 1)

        self._page_auto = QWidget()
        auto_lay = QVBoxLayout(self._page_auto)
        self._hdr = QLabel("")
        self._hdr.setStyleSheet("font-size: 15px; font-weight: bold;")
        auto_lay.addWidget(self._hdr)

        self._cards_scroll = QScrollArea()
        self._cards_scroll.setWidgetResizable(True)
        self._cards_host = QWidget()
        self._cards_layout = QVBoxLayout(self._cards_host)
        self._cards_scroll.setWidget(self._cards_host)
        auto_lay.addWidget(self._cards_scroll, 1)

        row_btns = QHBoxLayout()
        self._btn_constructor = QPushButton("Конструктор")
        self._btn_constructor.clicked.connect(self._show_constructor)
        self._btn_recalc = QPushButton("Пересчитать авто")
        self._btn_recalc.clicked.connect(self._recalc_auto_clear_forced)
        self._btn_save = QPushButton("Сохранить раскрой")
        self._btn_save.setObjectName("primary")
        self._btn_save.clicked.connect(self._on_save)
        row_btns.addWidget(self._btn_constructor)
        row_btns.addWidget(self._btn_recalc)
        row_btns.addStretch()
        row_btns.addWidget(self._btn_save)
        auto_lay.addLayout(row_btns)

        self._stack.addWidget(self._page_auto)

        self._page_constructor = QWidget()
        self._page_constructor.setObjectName("constructorPage")
        self._page_constructor.setStyleSheet(CONSTRUCTOR_PAGE_QSS)
        con_lay = QVBoxLayout(self._page_constructor)
        con_lay.setContentsMargins(0, 0, 0, 0)
        con_lay.setSpacing(0)

        header = QGridLayout()
        header.setColumnStretch(0, 1)
        header.setColumnStretch(1, 0)
        header.setColumnStretch(2, 1)
        self._btn_back_auto = QPushButton("Назад к авто")
        self._btn_back_auto.setFocusPolicy(Qt.NoFocus)
        self._btn_back_auto.clicked.connect(self._sync_constructor_to_session)
        header.addWidget(self._btn_back_auto, 0, 0, Qt.AlignLeft | Qt.AlignVCenter)

        center_title = QHBoxLayout()
        center_title.setSpacing(14)
        center_title.setContentsMargins(0, 0, 0, 0)
        center_title.addStretch(1)
        center_title.addWidget(ConstructorLogoWidget(self), 0, Qt.AlignVCenter)
        self._constructor_title = QLabel("Конструктор")
        self._constructor_title.setObjectName("constructorTitle")
        self._constructor_title.setAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        center_title.addWidget(self._constructor_title, 0, Qt.AlignVCenter)
        center_title.addStretch(1)
        cw = QWidget()
        cw.setObjectName("constructorHeaderCell")
        cw.setAttribute(Qt.WA_StyledBackground, True)
        cw.setLayout(center_title)
        header.addWidget(cw, 0, 1, Qt.AlignCenter)

        right_head = QHBoxLayout()
        right_head.setSpacing(10)
        right_head.setContentsMargins(0, 0, 0, 0)
        right_head.addStretch(1)
        self._pagination_bar = QWidget()
        self._pagination_bar.setObjectName("constructorHeaderCell")
        self._pagination_bar.setAttribute(Qt.WA_StyledBackground, True)
        ph = QHBoxLayout(self._pagination_bar)
        ph.setContentsMargins(0, 0, 0, 0)
        ph.setSpacing(6)
        self._btn_page_up = QPushButton("▲")
        self._btn_page_up.setObjectName("constructorPageNav")
        self._btn_page_up.setFocusPolicy(Qt.NoFocus)
        self._btn_page_up.clicked.connect(self._constructor_page_prev)
        ph.addWidget(self._btn_page_up)
        self._constructor_page_label = QLabel("Лист 1 из 1")
        self._constructor_page_label.setObjectName("constructorPageLabel")
        self._constructor_page_label.setAlignment(Qt.AlignCenter)
        ph.addWidget(self._constructor_page_label)
        self._btn_page_down = QPushButton("▼")
        self._btn_page_down.setObjectName("constructorPageNav")
        self._btn_page_down.setFocusPolicy(Qt.NoFocus)
        self._btn_page_down.clicked.connect(self._constructor_page_next)
        ph.addWidget(self._btn_page_down)
        right_head.addWidget(self._pagination_bar)
        self._btn_add_sheet = QPushButton("Добавить лист")
        self._btn_add_sheet.setObjectName("primary")
        self._btn_add_sheet.setFocusPolicy(Qt.NoFocus)
        self._btn_add_sheet.clicked.connect(self._constructor_add_sheet)
        right_head.addWidget(self._btn_add_sheet)
        rw = QWidget()
        rw.setObjectName("constructorHeaderCell")
        rw.setAttribute(Qt.WA_StyledBackground, True)
        rw.setLayout(right_head)
        header.addWidget(rw, 0, 2, Qt.AlignRight | Qt.AlignVCenter)
        con_lay.addLayout(header)
        header.setContentsMargins(8, 6, 8, 4)

        self._pool_area = QScrollArea()
        self._pool_area.setObjectName("constructorPoolScroll")
        self._pool_area.setWidgetResizable(True)
        self._pool_area.setMaximumHeight(150)
        self._pool_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._pool_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._pool_area.setAcceptDrops(True)
        self._pool_area.viewport().setAcceptDrops(True)
        self._pool_host = PoolDropHost(self)
        self._pool_host.piece_return_to_pool.connect(self._on_piece_return_to_pool)
        self._pool_grid = QGridLayout(self._pool_host)
        self._pool_grid.setContentsMargins(8, 6, 8, 6)
        self._pool_grid.setSpacing(8)
        self._pool_area.setWidget(self._pool_host)
        self._pool_viewport = self._pool_area.viewport()
        self._pool_viewport.installEventFilter(self)
        con_lay.addWidget(self._pool_area)

        self._sheet_constructor_host = QWidget()
        self._sheet_constructor_host.setObjectName("constructorSheetsHost")
        self._sheet_constructor_host.setAcceptDrops(True)
        self._sheet_constructor_layout = QHBoxLayout(self._sheet_constructor_host)
        self._sheet_constructor_layout.setContentsMargins(0, 0, 0, 0)
        self._sheet_constructor_layout.setSpacing(8)
        con_lay.addWidget(self._sheet_constructor_host, 1)

        self._stack.addWidget(self._page_constructor)

        self._card_rows: List[Tuple[LongPressSheetCard, QPushButton]] = []

        mat = (self._unit_items[0]['material_name'] if self._unit_items else '')
        th = (self._unit_items[0].get('thickness_mm', 4) if self._unit_items else 4)
        self._hdr.setText("Изделие: %s, %s мм" % (mat, th))

        self._recalc_auto()
        _mc_trace_ui("CutMaterialSessionDialog: _recalc_auto done")
        self._rebuild_cards()
        _mc_trace_ui("CutMaterialSessionDialog: __init__ finished (cards visible)")

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if (
            getattr(self, '_pool_viewport', None) is not None
            and obj is self._pool_viewport
            and event.type() == QEvent.Resize
        ):
            self._sync_pool_host_min_height()
        return super().eventFilter(obj, event)

    def closeEvent(self, event):  # noqa: N802
        """Остановить таймеры, если есть (совместимость со старыми сборками)."""
        t = getattr(self, '_rebalance_ctor_timer', None)
        if t is not None:
            t.stop()
        super().closeEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        """В конструкторе Esc не закрывает диалог (только «Назад к авто»)."""
        if event.key() == Qt.Key_Escape and self._stack.currentWidget() == self._page_constructor:
            event.accept()
            return
        super().keyPressEvent(event)

    def _constructor_num_pages(self) -> int:
        n = len(self._constructor_layouts)
        if n <= 0:
            return 1
        return max(1, int(math.ceil(n / 3.0)))

    def _clamp_constructor_page(self) -> None:
        npages = self._constructor_num_pages()
        if self._constructor_page_index >= npages:
            self._constructor_page_index = max(0, npages - 1)
        if self._constructor_page_index < 0:
            self._constructor_page_index = 0

    def _update_constructor_pagination_ui(self) -> None:
        npages = self._constructor_num_pages()
        self._clamp_constructor_page()
        cur = self._constructor_page_index + 1
        self._constructor_page_label.setText("Лист %d из %d" % (cur, npages))
        multi = npages > 1
        self._pagination_bar.setVisible(multi)
        if multi:
            self._btn_page_up.setVisible(self._constructor_page_index > 0)
            self._btn_page_down.setVisible(self._constructor_page_index < npages - 1)

    def _constructor_page_prev(self) -> None:
        if self._constructor_page_index <= 0:
            return
        self._constructor_page_index -= 1
        self._build_constructor_pool_and_sheets()

    def _constructor_page_next(self) -> None:
        npages = self._constructor_num_pages()
        if self._constructor_page_index >= npages - 1:
            return
        self._constructor_page_index += 1
        self._build_constructor_pool_and_sheets()

    def _get_sheets_fn(self, mat: str, th: Optional[int]) -> List[dict]:
        return list_stock_sheets_for_material(mat, th)

    def _get_threshold_fn(self, mat: str, th: Optional[int]):
        return models.get_threshold_for_material(mat, th)

    def _sync_forced_slots_from_layouts(self):
        """После любого пересчёта: forced-листы совпадают с фактическими листами сессии."""
        if not self._session_layouts:
            self._forced_slots = []
            return
        self._forced_slots = [_layout_to_forced_sheet_descriptor(lay) for lay in self._session_layouts]

    def _recalc_auto(self):
        if not self._unit_items:
            return
        forced = self._forced_slots if self._forced_slots else None
        res = compute_cut_session_layouts(
            self._unit_items,
            self._get_sheets_fn,
            self._get_threshold_fn,
            forced_slot_sheets=forced,
        )
        self._session_layouts = list(res.get('layouts') or [])
        if res.get('errors'):
            QMessageBox.warning(self, "Раскрой", "\n".join(res['errors']))
        self._sync_forced_slots_from_layouts()

    def _recalc_auto_clear_forced(self):
        self._forced_slots = []
        self._manual_sheet_indices.clear()
        self._recalc_auto()
        self._rebuild_cards()

    def _rebuild_cards(self):
        while self._cards_layout.count():
            it = self._cards_layout.takeAt(0)
            if it is None:
                continue
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif it.spacerItem() is not None:
                sip.delete(it)
        self._card_rows.clear()
        total = len(self._unit_items)
        for i, lay in enumerate(self._session_layouts):
            card = LongPressSheetCard(i, self)
            card.open_scheme_requested = self._on_long_press_open_scheme
            w = int(lay.get('sheet_width') or 0)
            h = int(lay.get('sheet_height') or 0)
            n = len(lay.get('pieces') or [])
            ex_ct = int(lay.get('in_work_existing_piece_count') or 0)
            if ex_ct > 0 and lay.get('in_work_order_id') is not None:
                n_new = max(0, n - ex_ct)
                card.setText(
                    "%d-й лист %d × %d\nна листе %d изд. (уже %d + эта сессия %d) из %d в сессии"
                    % (i + 1, w, h, n, ex_ct, n_new, total)
                )
            else:
                card.setText("%d-й лист %d × %d\nпоместилось %d/%d изделий" % (i + 1, w, h, n, total))
            btn = QPushButton("Выбрать другой")
            btn.clicked.connect(lambda checked=False, idx=i: self._choose_other_sheet(idx))

            inner = QHBoxLayout()
            inner.addWidget(card, 2)
            inner.addWidget(btn, 0)
            wrap = QWidget()
            wrap.setLayout(inner)
            self._cards_layout.addWidget(wrap)
            self._card_rows.append((card, btn))
        self._cards_layout.addStretch(1)

    def _find_session_sheet_index_for_chosen(self, chosen: dict) -> Optional[int]:
        """Индекс листа в сессии после пересчёта по id/типу/in_work (слот мог сместиться)."""
        if not chosen:
            return None
        sid = chosen.get('id')
        st = chosen.get('sheet_type') or 'full'
        for i, lay in enumerate(self._session_layouts):
            lsid = lay.get('sheet_id')
            if sid is not None and lsid is not None:
                try:
                    if int(lsid) != int(sid):
                        continue
                except (TypeError, ValueError):
                    if str(lsid) != str(sid):
                        continue
            elif lsid != sid:
                continue
            if (lay.get('sheet_type') or 'full') != st:
                continue
            if st == 'in_work':
                if int(lay.get('in_work_order_id') or 0) != int(chosen.get('in_work_order_id') or -1):
                    continue
                if int(lay.get('in_work_sheet_index') or 0) != int(chosen.get('in_work_sheet_index') or 0):
                    continue
            return i
        return None

    def _try_force_single_sheet_if_all_fit_on_chosen(self, chosen: dict) -> None:
        """Если все изделия помещаются на размеры выбранного листа — оставляем один макет без «хвостов»."""
        if self._manual_sheet_indices or not chosen or not self._unit_items:
            return
        units = sorted(
            self._unit_by_uid.values(),
            key=lambda u: str(u.get('piece_uid') or ''),
        )
        if not units:
            return
        sl = chosen.get('saved_layout') if isinstance(chosen.get('saved_layout'), dict) else None
        if sl and (sl.get('pieces') or []):
            return
        mat = (self._unit_items[0].get('material_name') or '').strip()
        th = int(self._unit_items[0].get('thickness_mm') or 4)
        sw = int(chosen.get('width_mm') or 0)
        sh = int(chosen.get('height_mm') or 0)
        if sw <= 0 or sh <= 0:
            return
        tmpl = {
            'sheet_id': chosen.get('id'),
            'sheet_type': chosen.get('sheet_type') or 'full',
            'sheet_width': sw,
            'sheet_height': sh,
            'thickness_mm': int(chosen.get('thickness_mm') or th),
            'material': mat,
        }
        st = tmpl['sheet_type']
        if st == 'in_work' and chosen.get('in_work_order_id') is not None:
            tmpl['in_work_order_id'] = chosen['in_work_order_id']
            tmpl['in_work_sheet_index'] = int(chosen.get('in_work_sheet_index') or 0)
            if chosen.get('in_work_rect'):
                tmpl['in_work_rect'] = dict(chosen['in_work_rect'])
        out = collapse_session_if_one_sheet_fits_all([tmpl], units, self._get_threshold_fn)
        if len(out) != 1:
            return
        pcs = out[0].get('pieces') or []
        if len(pcs) != len(units):
            return
        self._session_layouts = out

    def _prune_auto_session_if_one_sheet_has_all_unit_uids(self, chosen: dict) -> None:
        """Если один лист уже содержит все piece_uid изделий — оставляем только его (после squash/repack)."""
        if self._manual_sheet_indices or not self._session_layouts or not self._unit_items:
            return
        want = set()
        for it in self._unit_items:
            u = it.get('piece_uid')
            if u is None:
                continue
            k = str(u).strip()
            if k:
                want.add(k)
        if not want:
            return

        def _uids_on(lay: dict) -> Set[str]:
            s: Set[str] = set()
            for p in lay.get('pieces') or []:
                u = p.get('piece_uid')
                if u is None:
                    continue
                k = str(u).strip()
                if k:
                    s.add(k)
            return s

        order: List[int] = []
        ji = self._find_session_sheet_index_for_chosen(chosen)
        if ji is not None and 0 <= ji < len(self._session_layouts):
            order.append(ji)
        rest = sorted(
            range(len(self._session_layouts)),
            key=lambda i: -(
                int(self._session_layouts[i].get('sheet_width') or 0)
                * int(self._session_layouts[i].get('sheet_height') or 0)
            ),
        )
        for i in rest:
            if i not in order:
                order.append(i)

        mat = (self._unit_items[0].get('material_name') or '').strip()
        th = int(self._unit_items[0].get('thickness_mm') or 4)
        thr = self._get_threshold_fn(mat, th) or {}
        min_h = int(thr.get('min_height_mm', 0) or 0)
        min_w = int(thr.get('min_width_mm', 0) or 0)

        for i in order:
            lay = self._session_layouts[i]
            if _uids_on(lay) != want:
                continue
            base = copy.deepcopy(lay)
            sw = int(base.get('sheet_width') or 0)
            sh = int(base.get('sheet_height') or 0)
            pcs = list(base.get('pieces') or [])
            if sw <= 0 or sh <= 0:
                self._session_layouts = [base]
                return
            rp = repack_pieces_on_sheet(sw, sh, pcs, min_h, min_w)
            if rp and len(rp) == len(want):
                base['pieces'] = rp
                base['business_rects'], base['waste_rects'] = recompute_free_rects_from_pieces(
                    sw, sh, rp, min_h, min_w
                )
            self._session_layouts = [base]
            return

    def _dedupe_and_collapse_session_after_sheet_pick(self, chosen: dict) -> None:
        """
        Каждое изделие (piece_uid) — ровно на одном листе.
        Без ручных листов: если всё помещается на один лист — схлопываем и убираем пустые.
        """
        if not self._session_layouts:
            return
        ji = self._find_session_sheet_index_for_chosen(chosen)
        if ji is not None:
            pref = max(0, min(ji, len(self._session_layouts) - 1))
        else:
            pref = max(
                range(len(self._session_layouts)),
                key=lambda i: int(self._session_layouts[i].get('sheet_width') or 0)
                * int(self._session_layouts[i].get('sheet_height') or 0),
            )
        dedupe_layouts_pieces_prefer_sheet(self._session_layouts, pref)
        if not self._manual_sheet_indices:
            units = sorted(
                self._unit_by_uid.values(),
                key=lambda u: str(u.get('piece_uid') or ''),
            )
            self._session_layouts = collapse_session_if_one_sheet_fits_all(
                self._session_layouts,
                units,
                self._get_threshold_fn,
            )
            self._session_layouts = [
                L for L in self._session_layouts
                if L.get('pieces')
            ]

    def _choose_other_sheet(self, index: int):
        if not self._unit_items:
            return
        if index < 0 or index >= len(self._session_layouts):
            return
        mat = self._unit_items[0]['material_name']
        th = int(self._unit_items[0].get('thickness_mm') or 4)
        part_rects = []
        for u in self._unit_items:
            w_cut, h_cut = _unit_item_cut_dims(u)
            uid = u.get('piece_uid')
            if uid is not None and str(uid).strip():
                part_rects.append((h_cut, w_cut, str(uid).strip()))
            else:
                part_rects.append((h_cut, w_cut))
        ex_iw, ex_rem, ex_full = set(), set(), set()
        uid_to_sheet = {}
        for si, lay in enumerate(self._session_layouts):
            for p in lay.get('pieces') or []:
                u = p.get('piece_uid')
                if u is not None and str(u).strip():
                    uid_to_sheet[str(u).strip()] = si
            # Исключаем все листы сессии, включая заменяемый слот — один физический лист не может
            # быть выбран второй раз из списка «под замену».
            sid = lay.get('sheet_id')
            if sid is None:
                continue
            st = lay.get('sheet_type') or 'full'
            if st == 'in_work':
                ex_iw.add(int(sid))
            elif st == 'remnant':
                ex_rem.add(int(sid))
            elif st == 'full':
                ex_full.add(int(sid))
        d = ChooseSheetDialog(
            mat, th, 0, 0, self,
            part_rects_mm=part_rects,
            exclude_in_work_pool_ids=ex_iw,
            exclude_remnant_ids=ex_rem,
            exclude_full_ids=ex_full,
            replace_sheet_index=index,
            manual_sheet_indices=set(self._manual_sheet_indices),
            piece_uid_to_sheet_index=uid_to_sheet,
        )
        if d.exec_() != QDialog.Accepted:
            return
        ch = d.get_chosen()
        if not ch or len(ch) < 2:
            return
        sid, stype = int(ch[0]), ch[1]
        ow = ch[2] if len(ch) > 2 else None
        sheets = self._get_sheets_fn(mat, th)
        chosen = next(
            (s for s in sheets if s.get('id') == sid and s.get('sheet_type') == stype),
            None,
        )
        if not chosen and stype == 'in_work':
            chosen = models.find_in_work_pool_sheet_descriptor(mat, th, sid)
        if not chosen and stype == 'remnant':
            row = models.get_remnant_by_id(sid)
            if row:
                th_r = int(row.get('thickness_mm') or th)
                nm = ((row.get('name') or '').strip() or '').lower()
                if th_r == th and (not mat or nm == (mat or '').strip().lower()):
                    chosen = {
                        'id': row['id'],
                        'width_mm': row['width_mm'],
                        'height_mm': row['height_mm'],
                        'sheet_type': 'remnant',
                        'thickness_mm': th_r,
                    }
        if not chosen and stype == 'full':
            row = models.get_full_sheet_by_id(sid)
            if row:
                th_f = int(row.get('thickness_mm') or th)
                nm = ((row.get('name') or '').strip() or '').lower()
                if th_f == th and (not mat or nm == (mat or '').strip().lower()):
                    chosen = {
                        'id': row['id'],
                        'width_mm': row['width_mm'],
                        'height_mm': row['height_mm'],
                        'sheet_type': 'full',
                        'thickness_mm': th_f,
                    }
        if not chosen:
            QMessageBox.warning(self, "Лист", "Выбранный лист не найден в текущем списке склада.")
            return
        if ow is not None:
            self._combine_order_id = int(ow)

        # Замена листа со склада = полный авто-пересчёт; флаги «ручных» листов сбрасываем,
        # иначе остаётся ветка fixed без squash и три карточки при 5/5 на одном листе.
        self._manual_sheet_indices.clear()

        # Полный жадный пересчёт: первый слот — только что выбранный лист, без привязки к
        # старым размерам остальных слотов (иначе детали остаются по одной на лист без дублей uid).
        try:
            res = compute_cut_session_layouts(
                self._unit_items,
                self._get_sheets_fn,
                self._get_threshold_fn,
                forced_slot_sheets=[chosen],
            )
            self._session_layouts = list(res.get('layouts') or [])
            self._try_force_single_sheet_if_all_fit_on_chosen(chosen)
            if res.get('errors'):
                QMessageBox.warning(self, "Раскрой", "\n".join(res['errors']))
        except Exception as e:
            QMessageBox.warning(self, "Раскрой", "Ошибка пересчёта: %s" % e)
            return

        self._dedupe_and_collapse_session_after_sheet_pick(chosen)
        self._try_force_single_sheet_if_all_fit_on_chosen(chosen)
        squashed = squash_session_layouts_to_largest_sheet_if_repack(
            self._session_layouts,
            chosen,
            self._unit_by_uid,
            self._unit_items,
            self._get_threshold_fn,
        )
        if squashed is not None:
            self._session_layouts = squashed
        else:
            self._prune_auto_session_if_one_sheet_has_all_unit_uids(chosen)
        # Не помечаем лист «ручным» только из-за выбора со склада: иначе при следующей
        # замене сработает ветка fixed без squash — снова останутся лишние карточки с дублями.
        self._sync_forced_slots_from_layouts()
        self._rebuild_cards()

    def _on_long_press_open_scheme(self, sheet_index: int):
        if sheet_index < 0 or sheet_index >= len(self._session_layouts):
            return
        lay = copy.deepcopy(self._session_layouts[sheet_index])
        dlg = CuttingResultDialog(
            [lay],
            order_info={},
            parent=self,
            results_payload=None,
            preview_mode=True,
        )
        if dlg.exec_() != QDialog.Accepted:
            return
        if dlg.layouts:
            new_lay = dlg.layouts[0]
            old_manual = set(self._manual_sheet_indices)
            new_all, errs, meta = redistribute_session_after_sheet_edit(
                self._session_layouts,
                sheet_index,
                new_lay,
                self._unit_by_uid,
                self._get_sheets_fn,
                self._get_threshold_fn,
                forced_slot_sheets=None,
                manual_sheet_indices=old_manual,
            )
            self._session_layouts = new_all
            self._manual_sheet_indices = remap_manual_sheet_indices(
                old_manual | {sheet_index},
                meta.get('fixed_order') or [],
                meta.get('middle_original_indices') or [],
            )
            if errs:
                QMessageBox.warning(self, "Раскрой", "\n".join(errs))
            self._sync_forced_slots_from_layouts()
        self._rebuild_cards()

    def _show_constructor(self):
        self._pre_constructor_geom = self.geometry()
        self._pre_constructor_maximized = self.isMaximized()
        self._constructor_page_index = 0
        self._constructor_layouts = copy.deepcopy(self._session_layouts)
        self._manual_sheet_indices = set(range(len(self._constructor_layouts)))
        self._normalize_constructor_layout_cuts()
        self._build_constructor_pool_and_sheets()
        self._stack.setCurrentWidget(self._page_constructor)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self.showFullScreen()

    def constructor_remove_selected_pieces(self, sheet_index: int, sel_indices: Set[int]) -> bool:
        """Убрать выбранные изделия с листа в пул (карточки сверху), без перепаковки оставшихся."""
        if sheet_index < 0 or sheet_index >= len(self._constructor_layouts):
            return False
        if not sel_indices:
            return False
        lay = self._constructor_layouts[sheet_index]
        pieces = list(lay.get('pieces') or [])
        new_pieces = [p for i, p in enumerate(pieces) if i not in sel_indices]
        if len(new_pieces) == len(pieces):
            return False
        lay['pieces'] = new_pieces
        self._apply_constructor_chocolate_cuts(lay)
        self._refresh_layout_rects(lay)
        QTimer.singleShot(0, self._build_constructor_pool_and_sheets)
        return True

    def _build_constructor_pool_and_sheets(self):
        while self._pool_grid.count():
            it = self._pool_grid.takeAt(0)
            if it.widget():
                it.widget().setParent(None)
        while self._sheet_constructor_layout.count():
            it = self._sheet_constructor_layout.takeAt(0)
            if it.widget():
                it.widget().setParent(None)

        placed_uids: Set[str] = set()
        for lay in self._constructor_layouts:
            for p in lay.get('pieces') or []:
                uid = p.get('piece_uid')
                if uid:
                    placed_uids.add(uid)

        pool_uids = [u['piece_uid'] for u in self._unit_items if u['piece_uid'] not in placed_uids]
        pool_uids.sort(
            key=lambda uid: -(self._unit_by_uid[uid]['width_mm'] * self._unit_by_uid[uid]['height_mm'])
        )
        col = 0
        for uid in pool_uids:
            meta = self._unit_by_uid[uid]
            chip = self._make_pool_chip(uid, meta)
            self._pool_grid.addWidget(chip, 0, col)
            col += 1

        self._clamp_constructor_page()
        n = len(self._constructor_layouts)
        start = self._constructor_page_index * 3
        end = min(start + 3, n)
        for si in range(start, end):
            lay = self._constructor_layouts[si]
            w = self._make_constructor_sheet_widget(si, lay)
            self._sheet_constructor_layout.addWidget(w, 1)

        self._update_constructor_pagination_ui()
        QTimer.singleShot(0, self._sync_pool_host_min_height)

    def _sync_pool_host_min_height(self):
        ha = getattr(self, '_pool_area', None)
        if ha is None:
            return
        h = max(96, ha.viewport().height() - 4)
        self._pool_host.setMinimumHeight(h)

    def _make_pool_chip(self, uid: str, meta: dict) -> QLabel:
        return DraggablePoolChip(uid, meta, self)

    def _make_constructor_sheet_widget(self, sheet_index: int, lay: dict) -> QWidget:
        return ConstructorSheetPanel(sheet_index, lay, self)

    def _on_constructor_drop(
        self,
        sheet_index: int,
        uid: str,
        nx: int,
        ny: int,
        w_mm: Optional[int] = None,
        h_mm: Optional[int] = None,
        rotated: bool = False,
    ):
        if sheet_index < 0 or sheet_index >= len(self._constructor_layouts):
            return
        meta = self._unit_by_uid.get(uid)
        if not meta:
            return
        for oi, olay in enumerate(self._constructor_layouts):
            pcs = [p for p in (olay.get('pieces') or []) if p.get('piece_uid') != uid]
            if len(pcs) != len(olay.get('pieces') or []):
                olay['pieces'] = pcs
                self._apply_constructor_chocolate_cuts(olay)
                self._refresh_layout_rects(olay)
                self._manual_sheet_indices.add(oi)
        lay = self._constructor_layouts[sheet_index]
        w0, h0 = _unit_item_cut_dims(meta)
        if w_mm is None or h_mm is None:
            w_mm, h_mm = w0, h0
            rotated = False
        np = {
            'x': nx, 'y': ny, 'w': w_mm, 'h': h_mm,
            'piece_uid': uid,
            'recipient': meta.get('recipient_text') or '',
            'quantity_label': '',
            'edge_treatment': meta.get('edge_treatment') or {},
            'rotated': bool(rotated),
        }
        pieces = list(lay.get('pieces') or [])
        pieces.append(np)
        lay['pieces'] = pieces
        self._apply_constructor_chocolate_cuts(lay)
        self._refresh_layout_rects(lay)
        self._manual_sheet_indices.add(sheet_index)
        QTimer.singleShot(0, self._build_constructor_pool_and_sheets)

    def _on_piece_return_to_pool(self, uid: str, source_sheet: int):
        if source_sheet < 0 or source_sheet >= len(self._constructor_layouts):
            return
        lay = self._constructor_layouts[source_sheet]
        pcs = [p for p in (lay.get('pieces') or []) if p.get('piece_uid') != uid]
        if len(pcs) == len(lay.get('pieces') or []):
            return
        lay['pieces'] = pcs
        self._apply_constructor_chocolate_cuts(lay)
        self._refresh_layout_rects(lay)
        QTimer.singleShot(0, self._build_constructor_pool_and_sheets)

    def _normalize_constructor_layout_cuts(self):
        """При входе в конструктор: минимальные резы «шоколадка» — без _finalize, иначе снова появляются лишние горизонтали через остаток."""
        for lay in self._constructor_layouts:
            self._apply_constructor_chocolate_cuts(lay)
            self._refresh_layout_rects(lay)

    def _refresh_layout_rects(self, lay: dict):
        pieces = list(lay.get('pieces') or [])
        th = int(lay.get('thickness_mm') or 4)
        mat = lay.get('material') or ''
        try:
            thm = models.get_threshold_for_material(mat, th)
            min_h = (thm or {}).get('min_height_mm', 0) or 0
            min_w = (thm or {}).get('min_width_mm', 0) or 0
        except Exception:
            min_h, min_w = 0, 0
        sw = int(lay.get('sheet_width') or 0)
        sh = int(lay.get('sheet_height') or 0)
        br, wr = recompute_free_rects_from_pieces(sw, sh, pieces, min_h, min_w)
        segs = list(lay.get('cut_segments') or [])
        if segs:
            helper = LayoutEditCanvas.__new__(LayoutEditCanvas)
            helper.min_h = int(min_h or 0)
            helper.min_w = int(min_w or 0)
            for seg in segs:
                br, wr = helper._split_rects_by_segment(br + wr, seg)
        lay['business_rects'] = merge_adjacent_free_rects_for_display(br)
        lay['waste_rects'] = merge_adjacent_free_rects_for_display(wr)

    def _apply_constructor_chocolate_cuts(self, lay: dict):
        """Минимальные резы по охвату деталей: после DnD/переноса не оставлять старую сетку cut_segments."""
        pieces = list(lay.get('pieces') or [])
        if not pieces:
            lay.pop('cut_segments', None)
            lay.pop('cut_rows', None)
            return
        th = int(lay.get('thickness_mm') or 4)
        min_h, min_w = 0, 0
        mat = lay.get('material') or ''
        try:
            thm = models.get_threshold_for_material(mat, th)
            if thm:
                min_h = (thm or {}).get('min_height_mm', 0) or 0
                min_w = (thm or {}).get('min_width_mm', 0) or 0
        except Exception:
            pass
        assign_chocolate_bar_cut_segments_to_layout(
            lay, _min_strip_for_thickness(th), min_h=int(min_h or 0), min_w=int(min_w or 0)
        )

    def _constructor_sheet_usage_for_dialog(self) -> Dict[Tuple[str, int], int]:
        """Сколько раз каждый лист (тип + id) уже на схеме конструктора — не предлагать лишние."""
        out: Dict[Tuple[str, int], int] = {}
        for lay in self._constructor_layouts:
            st = str(lay.get('sheet_type') or 'full')
            sid = lay.get('sheet_id')
            if sid is None:
                continue
            k = (st, int(sid))
            out[k] = out.get(k, 0) + 1
        return out

    def _constructor_add_sheet(self):
        if not self._unit_items:
            return
        mat = self._unit_items[0]['material_name']
        th = int(self._unit_items[0].get('thickness_mm') or 4)
        d = ChooseSheetDialog(
            mat,
            th,
            0,
            0,
            self,
            session_sheet_usage=self._constructor_sheet_usage_for_dialog(),
            constructor_quick_open=True,
        )
        if d.exec_() != QDialog.Accepted:
            return
        ch = d.get_chosen()
        if not ch:
            return
        sheets = self._get_sheets_fn(mat, th)
        sid, stype = int(ch[0]), ch[1]
        sh = next((s for s in sheets if s.get('id') == sid and s.get('sheet_type') == stype), None)
        if not sh:
            return
        bw = int(sh['width_mm'])
        bh = int(sh['height_mm'])
        new_lay = {
            'sheet_id': sh['id'],
            'sheet_type': sh['sheet_type'],
            'material': mat,
            'sheet_width': bw,
            'sheet_height': bh,
            'thickness_mm': sh.get('thickness_mm', th),
            'rotated': False,
            'pieces': [],
            'business_rects': [],
            'waste_rects': [],
        }
        if sh.get('in_work_order_id') is not None:
            new_lay['in_work_order_id'] = sh['in_work_order_id']
            new_lay['in_work_sheet_index'] = sh.get('in_work_sheet_index', 0)
            if sh.get('in_work_rect'):
                new_lay['in_work_rect'] = dict(sh['in_work_rect'])
        try:
            thm = models.get_threshold_for_material(mat, th)
            mh = (thm or {}).get('min_height_mm', 0) or 0
            mw = (thm or {}).get('min_width_mm', 0) or 0
        except Exception:
            mh, mw = 0, 0
        new_lay['business_rects'], new_lay['waste_rects'] = recompute_free_rects_from_pieces(
            bw, bh, [], mh, mw
        )
        self._constructor_layouts.append(new_lay)
        self._constructor_page_index = max(0, (len(self._constructor_layouts) - 1) // 3)
        self._build_constructor_pool_and_sheets()

    def _constructor_delete_sheet(self, index: int):
        if index < 0 or index >= len(self._constructor_layouts):
            return
        self._constructor_layouts.pop(index)
        self._manual_sheet_indices = {
            (i if i < index else i - 1)
            for i in self._manual_sheet_indices
            if i != index
        }
        self._build_constructor_pool_and_sheets()

    def _sync_constructor_to_session(self):
        """Вернуться к карточкам авто: без redistribute — переносим макет конструктора как есть."""
        if self._constructor_layouts:
            self._session_layouts = copy.deepcopy(self._constructor_layouts)
            for lay in self._session_layouts:
                self._refresh_layout_rects(lay)
            self._manual_sheet_indices = set(range(len(self._session_layouts)))
            self._sync_forced_slots_from_layouts()
        self._stack.setCurrentWidget(self._page_auto)
        self._root_layout.setContentsMargins(self._root_margins_auto)
        self.showNormal()
        if self._pre_constructor_maximized:
            self.showMaximized()
        elif self._pre_constructor_geom is not None and self._pre_constructor_geom.isValid():
            self.setGeometry(self._pre_constructor_geom)
        self._rebuild_cards()

    def _on_save(self):
        if self._stack.currentWidget() is self._page_constructor and self._constructor_layouts:
            self._session_layouts = copy.deepcopy(self._constructor_layouts)
            for lay in self._session_layouts:
                self._refresh_layout_rects(lay)
            self._manual_sheet_indices = set(range(len(self._session_layouts)))
            self._sync_forced_slots_from_layouts()
        if not self._session_layouts:
            QMessageBox.warning(self, "Раскрой", "Нет листов.")
            return
        non_empty = [lay for lay in self._session_layouts if lay.get('pieces')]
        if not non_empty:
            QMessageBox.warning(self, "Раскрой", "Нет изделий на листах.")
            return
        oid = commit_cut_session(
            self,
            self._line_items,
            non_empty,
            pin_order_id=self._pin_order_id,
            bundle_client_name=self._bundle_client_name,
            combine_order_id=self._combine_order_id,
            show_result_dialog=False,
            silent=True,
        )
        if oid:
            self._cut_saved_order_id = oid
            self.accept()


class ConstructorLayoutCanvas(LayoutEditCanvas):
    """Лист конструктора: полное поведение LayoutEditCanvas + DnD с пула и Shift+перетаскивание на пул."""

    def __init__(self, sheet_index: int, unit_by_uid: dict, session: CutMaterialSessionDialog):
        super().__init__(session)
        self._dark_constructor_canvas = True
        self.setStyleSheet("background-color: #000000;")
        self._sheet_index = sheet_index
        self._uids = unit_by_uid
        self._session = session
        self.setAcceptDrops(True)
        self._pool_valid: List[Tuple[int, int, int, int, int]] = []
        self._pool_ghost: Optional[Tuple[int, int, int, int]] = None
        self._pool_wh: Optional[Tuple[int, int]] = None  # оставлено для совместимости; превью по _pool_valid
        self._export_drag_uid_prepare: Optional[Tuple[str, QPoint]] = None

    @staticmethod
    def _accept_piece_drop_action(e) -> bool:
        """Windows/Qt часто дают proposedAction=Ignore; явно выбираем Copy или Move."""
        if e.possibleActions() & Qt.CopyAction:
            e.setDropAction(Qt.CopyAction)
            return True
        if e.possibleActions() & Qt.MoveAction:
            e.setDropAction(Qt.MoveAction)
            return True
        return False

    def set_layout(self, layout_dict):
        self.layout_dict = layout_dict if layout_dict else None
        self._sel_indices = set()
        self._sel_cuts = set()
        self._hover_cut_segment = None
        self._flip_preview_list = None
        self._flip_preview_cache_key = None
        self._delete_preview_list = None
        self._delete_preview_cache_key = None
        self._press_cut_key = None
        self.drag_start = None
        self.pan_start = None
        self.pending_place = None
        self.hover_placement = None
        self.update()
        self.layout_changed.emit()

    def contextMenuEvent(self, event):
        if not self.layout_dict:
            return
        m = QMenu(self)
        act_h = m.addAction("Добавить горизонтальный рез")
        act_v = m.addAction("Добавить вертикальный рез")
        m.addSeparator()
        act_flip = m.addAction("Смена направления реза")
        act_del = m.addAction("Удалить рез")
        m.addSeparator()
        act_clear = m.addAction("Выйти из режима реза")
        chosen = m.exec_(event.globalPos())
        if chosen == act_h:
            self.set_cut_direction_mode(False)
            self.set_delete_cut_mode(False)
            self.set_add_cut_mode(True, 'H')
        elif chosen == act_v:
            self.set_cut_direction_mode(False)
            self.set_delete_cut_mode(False)
            self.set_add_cut_mode(True, 'V')
        elif chosen == act_flip:
            self.set_add_cut_mode(False)
            self.set_delete_cut_mode(False)
            self.set_cut_direction_mode(True)
        elif chosen == act_del:
            self.set_add_cut_mode(False)
            self.set_cut_direction_mode(False)
            self.set_delete_cut_mode(True)
        elif chosen == act_clear:
            self.set_add_cut_mode(False)
            self.set_cut_direction_mode(False)
            self.set_delete_cut_mode(False)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.cut_direction_mode or self.add_cut_mode or getattr(self, 'delete_cut_mode', False):
                super().keyPressEvent(event)
                return
            sel = set(getattr(self, '_sel_indices', set()))
            if sel and self.layout_dict and self._session.constructor_remove_selected_pieces(self._sheet_index, sel):
                self._sel_indices.clear()
                self._sel_cuts.clear()
                self.drag_start = None
                self.pending_place = None
                self.hover_placement = None
                self.update()
                self.layout_changed.emit()
                event.accept()
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self._export_drag_uid_prepare = None
        if (
            event.button() == Qt.LeftButton
            and (event.modifiers() & Qt.ShiftModifier)
            and self.layout_dict
            and not (self.add_cut_mode or self.delete_cut_mode or self.cut_direction_mode)
        ):
            sx, sy = self._px_to_sheet(event.x(), event.y())
            idx = self._piece_at(sx, sy)
            if idx is not None:
                p = self.layout_dict['pieces'][idx]
                uid = p.get('piece_uid')
                if uid:
                    self._export_drag_uid_prepare = (str(uid), QPoint(event.pos()))
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._export_drag_uid_prepare and (event.buttons() & Qt.LeftButton):
            uid, pos0 = self._export_drag_uid_prepare
            if (event.pos() - pos0).manhattanLength() >= QApplication.startDragDistance():
                self._export_drag_uid_prepare = None
                self._start_drag_to_pool(uid)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._export_drag_uid_prepare = None
        super().mouseReleaseEvent(event)

    def _start_drag_to_pool(self, uid: str):
        drag = QDrag(self)
        md = QMimeData()
        md.setData(MIME_PIECE_UID, uid.encode('utf-8'))
        md.setData(MIME_SOURCE_SHEET, str(self._sheet_index).encode('utf-8'))
        drag.setMimeData(md)
        drag.exec_(Qt.MoveAction)

    def dragEnterEvent(self, e):
        if not self.layout_dict or not e.mimeData().hasFormat(MIME_PIECE_UID):
            e.ignore()
            return
        if self._update_pool_preview(e.mimeData(), e.pos()):
            if self._accept_piece_drop_action(e):
                e.accept()
            else:
                e.ignore()
        else:
            e.ignore()
        self.update()

    def dragMoveEvent(self, e):
        if not e.mimeData().hasFormat(MIME_PIECE_UID):
            e.ignore()
            return
        if self._update_pool_preview(e.mimeData(), e.pos()):
            if self._accept_piece_drop_action(e):
                e.accept()
            else:
                e.ignore()
        else:
            e.ignore()
        self.update()

    def dragLeaveEvent(self, e):
        self._pool_valid = []
        self._pool_ghost = None
        self._pool_wh = None
        self.update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        ghost_snap = self._pool_ghost
        self._pool_valid = []
        self._pool_ghost = None
        self._pool_wh = None
        mime = e.mimeData()
        if not mime.hasFormat(MIME_PIECE_UID) or self.layout_dict is None:
            e.ignore()
            self.update()
            return
        uid = bytes(mime.data(MIME_PIECE_UID)).decode('utf-8')
        src = _mime_source_sheet(mime)
        meta = self._uids.get(uid)
        if not meta:
            e.ignore()
            self.update()
            return
        w_cut, h_cut = _unit_item_cut_dims(meta)
        sw = int(self.layout_dict.get('sheet_width') or 0)
        sh = int(self.layout_dict.get('sheet_height') or 0)
        pieces = list(self.layout_dict.get('pieces') or [])
        exclude = uid if src == self._sheet_index else None
        skip_rect: Optional[Tuple[int, int, int, int]] = None
        if exclude:
            for p in pieces:
                if p.get('piece_uid') == uid:
                    skip_rect = (int(p['x']), int(p['y']), int(p['w']), int(p['h']))
                    break
        sx, sy = self._px_to_sheet(e.pos().x(), e.pos().y())
        segs = list(self.layout_dict.get('cut_segments') or [])
        valid_drop = _valid_placements_union_orientations(sw, sh, pieces, w_cut, h_cut, exclude, segs)
        if skip_rect is not None:
            valid_drop = [t for t in valid_drop if (t[0], t[1], t[3], t[4]) != skip_rect]
        at = _placement_snap_from_valid_multi(sx, sy, valid_drop, None) if valid_drop else None
        if at is None and ghost_snap is not None:
            gx, gy, gw, gh = ghost_snap
            if _can_place_piece(sw, sh, pieces, gx, gy, gw, gh, exclude, segs):
                if skip_rect is None or (gx, gy, gw, gh) != skip_rect:
                    at = (gx, gy, gw, gh)
        if at is None:
            e.ignore()
            self.update()
            return
        if not self._accept_piece_drop_action(e):
            e.ignore()
            self.update()
            return
        e.accept()
        px, py, pw, ph = at
        rotated = (w_cut != h_cut) and (pw, ph) == (h_cut, w_cut)
        self._session._on_constructor_drop(self._sheet_index, uid, px, py, pw, ph, rotated)
        self.update()

    def _update_pool_preview(self, mime: QMimeData, pos) -> bool:
        self._pool_valid = []
        self._pool_ghost = None
        self._pool_wh = None
        if not self.layout_dict or not mime.hasFormat(MIME_PIECE_UID):
            return False
        uid = bytes(mime.data(MIME_PIECE_UID)).decode('utf-8')
        src = _mime_source_sheet(mime)
        meta = self._uids.get(uid)
        if not meta:
            return False
        w_cut, h_cut = _unit_item_cut_dims(meta)
        sw = int(self.layout_dict.get('sheet_width') or 0)
        sh = int(self.layout_dict.get('sheet_height') or 0)
        if sw <= 0 or sh <= 0:
            return False
        pieces = list(self.layout_dict.get('pieces') or [])
        exclude = uid if src == self._sheet_index else None
        skip_rect: Optional[Tuple[int, int, int, int]] = None
        if exclude:
            for p in pieces:
                if p.get('piece_uid') == uid:
                    skip_rect = (int(p['x']), int(p['y']), int(p['w']), int(p['h']))
                    break
        segs = list(self.layout_dict.get('cut_segments') or [])
        self._pool_valid = _valid_placements_union_orientations(sw, sh, pieces, w_cut, h_cut, exclude, segs)
        if skip_rect is not None:
            self._pool_valid = [t for t in self._pool_valid if (t[0], t[1], t[3], t[4]) != skip_rect]
        if not self._pool_valid:
            self._pool_wh = None
            return False
        self._pool_wh = (w_cut, h_cut)
        sx, sy = self._px_to_sheet(pos.x(), pos.y())
        at = _placement_snap_from_valid_multi(sx, sy, self._pool_valid, None)
        if at:
            self._pool_ghost = (at[0], at[1], at[2], at[3])
        return True

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.layout_dict or (not self._pool_valid and not self._pool_ghost):
            return
        ox, oy = self._origin()
        scale = self._scale()
        if scale <= 0:
            return
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        for px, py, touches, pw, ph in self._pool_valid:
            rx = ox + px * scale
            ry = oy + py * scale
            rw = max(3, pw * scale)
            rh = max(3, ph * scale)
            if touches >= 2:
                qp.setBrush(QColor(100, 150, 255, 100))
            else:
                qp.setBrush(QColor(100, 150, 255, 55))
            qp.setPen(QPen(QColor(80, 120, 220, 120), 1))
            qp.drawRect(QRect(int(rx), int(ry), int(rw), int(rh)))
        if self._pool_ghost:
            gx, gy, gw, gh = self._pool_ghost
            qp.setBrush(QColor(80, 200, 120, 130))
            qp.setPen(QPen(QColor(40, 160, 60), 2))
            qp.drawRect(
                QRect(
                    int(ox + gx * scale),
                    int(oy + gy * scale),
                    int(max(3, gw * scale)),
                    int(max(3, gh * scale)),
                )
            )


class ConstructorSheetPanel(QWidget):
    """Один лист в конструкторе: масштаб, схема, сетка кнопок (пул сверху у родителя)."""

    def __init__(self, sheet_index: int, lay: dict, session: CutMaterialSessionDialog):
        super().__init__(session)
        self._sheet_index = sheet_index
        self._lay = lay
        self._session = session
        self._other_scheme_variants_dialog = None
        self.setObjectName("constructorSheetPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        exp = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        btn_h = 22

        vl = QVBoxLayout(self)
        vl.setSpacing(6)
        vl.setContentsMargins(0, 0, 0, 0)

        zoom_row = QHBoxLayout()
        zoom_row.setContentsMargins(0, 0, 0, 0)
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setObjectName("constructorZoomSlider")
        self.zoom_slider.setMinimum(50)
        self.zoom_slider.setMaximum(400)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setTickPosition(QSlider.NoTicks)
        self.zoom_slider.setAutoFillBackground(False)
        self.zoom_slider.setAttribute(Qt.WA_StyledBackground, True)
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        zoom_row.addWidget(self.zoom_slider, 1)
        vl.addLayout(zoom_row)

        self.canvas = ConstructorLayoutCanvas(sheet_index, session._unit_by_uid, session)
        self.canvas.set_layout(lay)
        mat = lay.get('material') or ''
        th = int(lay.get('thickness_mm') or 4)
        try:
            thm = models.get_threshold_for_material(mat, th)
            mh = (thm or {}).get('min_height_mm', 0) or 0
            mw = (thm or {}).get('min_width_mm', 0) or 0
        except Exception:
            mh, mw = 0, 0
        self.canvas.set_remnant_threshold(mh, mw)
        self.canvas.layout_changed.connect(self._on_canvas_layout_changed)
        self.canvas.setMinimumSize(420, 460)
        vl.addWidget(self.canvas, 1)

        self.stats_label = QLabel("")
        self.stats_label.setObjectName("constructorStatsLabel")
        self.stats_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.stats_label.setWordWrap(True)
        vl.addWidget(self.stats_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for c in range(3):
            grid.setColumnStretch(c, 1)

        self.btn_rotate = QPushButton("Повернуть 90")
        self.btn_rotate.setObjectName("constructorSheetBtnRotateBlue")
        self.btn_rotate.setToolTip("Поворот выбранной детали на 90°")
        self.btn_rotate.setSizePolicy(exp)
        self.btn_rotate.setMinimumHeight(btn_h)
        self.btn_rotate.clicked.connect(self.canvas.rotate_selected)
        grid.addWidget(self.btn_rotate, 0, 0)

        self.btn_other_scheme = QPushButton("Другая схема")
        self.btn_other_scheme.setObjectName("constructorSheetBtnDarkGreen")
        self.btn_other_scheme.setToolTip("Несколько вариантов раскладки тех же деталей (остатки, поворот листа)")
        self.btn_other_scheme.setSizePolicy(exp)
        self.btn_other_scheme.setMinimumHeight(btn_h)
        self.btn_other_scheme.clicked.connect(self._on_other_scheme)
        grid.addWidget(self.btn_other_scheme, 0, 1)

        self.btn_rotate_sheet = QPushButton("Повернуть лист")
        self.btn_rotate_sheet.setObjectName("constructorSheetBtnDarkGreen")
        self.btn_rotate_sheet.setToolTip(
            "Повернуть лист на 90°: пересчитать укладку (полки снизу / колонки слева / авто), "
            "чтобы детали оставались связным блоком и остатки были лучше; линии реза пересчитываются."
        )
        self.btn_rotate_sheet.setSizePolicy(exp)
        self.btn_rotate_sheet.setMinimumHeight(btn_h)
        self.btn_rotate_sheet.clicked.connect(self._on_rotate_sheet)
        grid.addWidget(self.btn_rotate_sheet, 0, 2)

        self.btn_reset = QPushButton("Сброс")
        self.btn_reset.setObjectName("constructorSheetBtnRedOrange")
        self.btn_reset.setToolTip("Очистить лист — все детали вернутся в пул")
        self.btn_reset.setSizePolicy(exp)
        self.btn_reset.setMinimumHeight(btn_h)
        self.btn_reset.clicked.connect(self._on_reset_sheet)
        grid.addWidget(self.btn_reset, 1, 0)

        self.btn_delete = QPushButton("Удалить")
        self.btn_delete.setObjectName("constructorSheetBtnRedOrange")
        self.btn_delete.setToolTip(
            "Удалить выбранные детали и перепаковать оставшиеся на листе. "
            "Del — только снять с листа в пул сверху, без перепаковки."
        )
        self.btn_delete.setSizePolicy(exp)
        self.btn_delete.setMinimumHeight(btn_h)
        self.btn_delete.clicked.connect(self._on_delete_pieces)
        grid.addWidget(self.btn_delete, 1, 1)

        btn_del_sheet = QPushButton("Удалить лист")
        btn_del_sheet.setObjectName("constructorSheetBtnMaroon")
        btn_del_sheet.setSizePolicy(exp)
        btn_del_sheet.setMinimumHeight(btn_h)
        btn_del_sheet.setCursor(Qt.PointingHandCursor)
        btn_del_sheet.clicked.connect(lambda checked=False, idx=sheet_index: session._constructor_delete_sheet(idx))
        grid.addWidget(btn_del_sheet, 1, 2)

        vl.addLayout(grid)
        self._sync_cut_mode_controls()
        self._refresh_stats()

    def _on_zoom_changed(self, value):
        self.canvas.set_zoom_factor(value / 100.0)

    def _on_canvas_layout_changed(self):
        c = self.canvas
        if c.layout_dict is self._lay:
            if not (self._lay.get('pieces') or []):
                self._session._apply_constructor_chocolate_cuts(self._lay)
            elif not (
                c.add_cut_mode
                or getattr(c, 'cut_direction_mode', False)
                or getattr(c, 'delete_cut_mode', False)
            ):
                self._session._apply_constructor_chocolate_cuts(self._lay)
        self._session._refresh_layout_rects(self._lay)
        self._sync_cut_mode_controls()
        self._refresh_stats()

    def _refresh_stats(self):
        occ, n_rem, max_rem = self.canvas.get_stats()
        occ_m2 = occ / 1e6
        max_rem_m2 = max_rem / 1e6
        self.stats_label.setText(
            "Занято: %.2f м² | Деловых остатков: %d | Макс. остаток: %.2f м²"
            % (occ_m2, n_rem, max_rem_m2)
        )

    def _sync_cut_mode_controls(self):
        """Режимы реза перенесены в контекстное меню схемы (ПКМ)."""

    def _constructor_modeless_message(self, icon, title: str, text: str):
        mb = QMessageBox(icon, title, text, QMessageBox.Ok, self)
        mb.setModal(False)
        mb.setWindowModality(Qt.NonModal)
        mb.setAttribute(Qt.WA_DeleteOnClose, True)
        mb.show()

    def _close_other_scheme_variants_dialog(self):
        d = self._other_scheme_variants_dialog
        if d is None:
            return
        self._other_scheme_variants_dialog = None
        d.close()
        d.deleteLater()

    def _on_other_scheme_variant_picker_finished(self, dialog, result):
        if self._other_scheme_variants_dialog is dialog:
            self._other_scheme_variants_dialog = None
        if result != QDialog.Accepted:
            return
        lay = self.canvas.layout_dict
        if not lay:
            return
        chosen = dialog.get_chosen()
        if not chosen or not chosen.get('layouts'):
            return
        chosen_lay = chosen['layouts'][0]
        new_layout = {
            k: v
            for k, v in lay.items()
            if k
            not in (
                'pieces',
                'business_rects',
                'waste_rects',
                'sheet_width',
                'sheet_height',
                'rotated',
                'cut_segments',
                'cut_rows',
            )
        }
        new_layout.update(
            {
                'pieces': chosen_lay['pieces'],
                'business_rects': chosen_lay['business_rects'],
                'waste_rects': chosen_lay['waste_rects'],
                'sheet_width': chosen_lay['sheet_width'],
                'sheet_height': chosen_lay['sheet_height'],
                'rotated': chosen_lay.get('rotated', False),
            }
        )
        if chosen_lay.get('cut_segments') is not None:
            new_layout['cut_segments'] = list(chosen_lay['cut_segments'])
        if chosen_lay.get('cut_rows') is not None:
            new_layout['cut_rows'] = chosen_lay['cut_rows']
        self._lay.clear()
        self._lay.update(new_layout)
        self.canvas.set_layout(self._lay)
        self._session._refresh_layout_rects(self._lay)
        self.canvas.update()
        self.canvas.layout_changed.emit()
        self._refresh_stats()
        QTimer.singleShot(0, self._session._build_constructor_pool_and_sheets)

    def _present_other_scheme_variants(self, lay, variant_layouts):
        if not variant_layouts:
            self._constructor_modeless_message(
                QMessageBox.Warning,
                "Другая схема",
                "Не удалось построить варианты раскладки для этого листа.",
            )
            return
        variant_results = []
        _excl = (
            'pieces',
            'business_rects',
            'waste_rects',
            'sheet_width',
            'sheet_height',
            'rotated',
            'cut_segments',
            'cut_rows',
        )
        for vlay in variant_layouts:
            full_layout = {k: v for k, v in lay.items() if k not in _excl}
            full_layout.update(
                {
                    'pieces': vlay['pieces'],
                    'sheet_width': vlay['sheet_width'],
                    'sheet_height': vlay['sheet_height'],
                    'rotated': vlay.get('rotated', False),
                }
            )
            self._session._apply_constructor_chocolate_cuts(full_layout)
            self._session._refresh_layout_rects(full_layout)
            variant_results.append({'layouts': [full_layout]})
        self._close_other_scheme_variants_dialog()
        d = ChooseVariantDialog(variant_results, self, dark_theme=True)
        d.setModal(False)
        d.setWindowModality(Qt.NonModal)
        d.setAttribute(Qt.WA_DeleteOnClose, True)
        d.finished.connect(
            lambda code, dlg=d: self._on_other_scheme_variant_picker_finished(dlg, code)
        )
        self._other_scheme_variants_dialog = d
        d.show()

    def _on_other_scheme(self):
        lay = self.canvas.layout_dict
        if not lay:
            return
        pieces = list(lay.get('pieces') or [])
        sw = int(lay.get('sheet_width') or 0)
        sh = int(lay.get('sheet_height') or 0)
        if not pieces or sw <= 0 or sh <= 0:
            self._constructor_modeless_message(
                QMessageBox.Warning,
                "Другая схема",
                "На листе нет изделий или не заданы размеры листа.",
            )
            return
        self._close_other_scheme_variants_dialog()
        min_h, min_w = 0, 0
        try:
            th = models.get_threshold_for_material(self._material_name_for_thresholds(), lay.get('thickness_mm', 4))
            if th:
                min_h = th.get('min_height_mm') or 0
                min_w = th.get('min_width_mm') or 0
        except Exception:
            pass
        th_mm = int(lay.get('thickness_mm') or 4)
        old_stats = self.stats_label.text()
        self.btn_other_scheme.setEnabled(False)
        self.stats_label.setText("Подбор вариантов раскладки…")
        QApplication.processEvents()
        try:
            variant_layouts = compute_layout_variants_for_one_sheet(sw, sh, pieces, min_h, min_w, th_mm)
        except Exception:
            import traceback

            self.btn_other_scheme.setEnabled(True)
            self.stats_label.setText(old_stats)
            self._constructor_modeless_message(QMessageBox.Critical, "Другая схема", traceback.format_exc())
            return
        self.btn_other_scheme.setEnabled(True)
        self.stats_label.setText(old_stats)
        self._present_other_scheme_variants(lay, variant_layouts)

    def _on_rotate_sheet(self):
        lay = self.canvas.layout_dict
        if not lay:
            return
        pieces = list(lay.get('pieces') or [])
        if not pieces:
            QMessageBox.information(self, "Повернуть лист", "На листе нет изделий.")
            return
        min_h, min_w = 0, 0
        try:
            th = models.get_threshold_for_material(self._material_name_for_thresholds(), lay.get('thickness_mm', 4))
            if th:
                min_h = th.get('min_height_mm') or 0
                min_w = th.get('min_width_mm') or 0
        except Exception:
            pass
        sw = int(lay.get('sheet_width') or 0)
        sh = int(lay.get('sheet_height') or 0)
        if sw <= 0 or sh <= 0:
            return
        try:
            opt = optimize_layout_after_sheet_rotate(pieces, sw, sh, min_h, min_w)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        if opt is None:
            QMessageBox.warning(
                self,
                "Повернуть лист",
                "Не удалось уложить все изделия после поворота листа (связный блок, габариты).",
            )
            return
        lay['pieces'] = opt['pieces']
        lay['sheet_width'] = opt['sheet_width']
        lay['sheet_height'] = opt['sheet_height']
        lay['business_rects'] = opt['business_rects']
        lay['waste_rects'] = opt['waste_rects']
        lay['rotated'] = not bool(lay.get('rotated', False))
        lay.pop('cut_segments', None)
        lay.pop('cut_rows', None)
        self._session._apply_constructor_chocolate_cuts(lay)
        self._session._refresh_layout_rects(lay)
        self.canvas.update()
        self.canvas.layout_changed.emit()
        self._refresh_stats()
        QTimer.singleShot(0, self._session._build_constructor_pool_and_sheets)

    def _material_name_for_thresholds(self) -> str:
        layout = self._lay
        if layout.get('material'):
            return str(layout['material'])
        if self._session._unit_items:
            return str(self._session._unit_items[0]['material_name'])
        return ''

    def _on_delete_pieces(self):
        layout = self.canvas.layout_dict
        if not layout:
            return
        pieces = list(layout.get('pieces') or [])
        sel = getattr(self.canvas, '_sel_indices', set())
        if not sel:
            QMessageBox.information(self, "Удаление", "Выберите деталь на схеме, затем нажмите «Удалить».")
            return
        msg = "Удалить выбранную деталь?" if len(sel) == 1 else "Удалить выбранные детали (%d)?" % len(sel)
        if QMessageBox.question(self, "Удалить", msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        new_pieces = [p for i, p in enumerate(pieces) if i not in sel]
        sw = layout.get('sheet_width') or 0
        sh = layout.get('sheet_height') or 0
        min_h, min_w = 0, 0
        try:
            th = models.get_threshold_for_material(self._material_name_for_thresholds(), layout.get('thickness_mm', 4))
            if th:
                min_h = th.get('min_height_mm') or 0
                min_w = th.get('min_width_mm') or 0
        except Exception:
            pass
        repacked = repack_pieces_on_sheet(sw, sh, new_pieces, min_h, min_w)
        if repacked is not None:
            new_pieces = repacked
        business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, new_pieces, min_h, min_w)
        layout['pieces'] = new_pieces
        layout['business_rects'] = business_rects
        layout['waste_rects'] = waste_rects
        self.canvas._sel_indices.clear()
        self.canvas._sel_cuts.clear()
        self.canvas.update()
        self.canvas.layout_changed.emit()
        self._sync_cut_mode_controls()
        self._refresh_stats()
        QTimer.singleShot(0, self._session._build_constructor_pool_and_sheets)

    def _on_reset_sheet(self):
        self._lay['pieces'] = []
        self._session._apply_constructor_chocolate_cuts(self._lay)
        self._session._refresh_layout_rects(self._lay)
        self.canvas._sel_indices.clear()
        self.canvas._sel_cuts.clear()
        self.canvas.drag_start = None
        self.canvas.pending_place = None
        self.canvas.hover_placement = None
        self.canvas.update()
        self.canvas.layout_changed.emit()
        self._sync_cut_mode_controls()
        self._refresh_stats()
        QTimer.singleShot(0, self._session._build_constructor_pool_and_sheets)
