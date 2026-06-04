"""Ручное редактирование макета: выбор изделия, поворот 90°, перетаскивание с привязкой к краям, проверка и сохранение."""
import sys
import os
import copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox,
    QWidget, QSlider,
)
from PyQt5.QtCore import Qt, QPoint, QRect, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QCursor

from logic.cutting_algorithm import (
    recompute_free_rects_from_pieces,
    repack_pieces_on_sheet,
    assign_chocolate_bar_cut_segments_to_layout,
    merge_adjacent_free_rects_for_display,
    _min_strip_for_thickness,
)
from logic.cut_sequence import reorder_cut_segments_for_guillotine_simulation
from db import models


MARGIN = 60
SNAP_MM = 12   # в мм: порог привязки к краю листа и к сторонам других изделий
TOUCH_TOLERANCE_MM = 2.0   # считать «касание» двух изделий при совпадении сторон в пределах этого допуска
# Считать два реза одной осью (при смене направления/удалении снимаем все «совпадающие» линии)
CUT_AXIS_POS_MM_TOL = 0.35
# Рез не должен проходить строго внутри детали (только по границам/в отходах); допуск к границе, мм.
PIECE_INTERIOR_CLIP_EPS = 0.35
CUT_SEGMENT_MIN_SPAN_MM = 0.5
# В UI одна «линия смены направления»: сегменты V/H с близким pos (дробление из-за деталей, 555 vs 571 мм).
CUT_UI_CLUSTER_MAX_SPAN_MM = 40
def _small_piece_threshold_mm():
    """Порог мелкой детали (мм): если хотя бы одна сторона меньше — только *N на схеме."""
    try:
        from user_settings import get_small_piece_mm
        return get_small_piece_mm()
    except Exception:
        return 200
LEGEND_ITEM_H = 52
LEGEND_BOX_W = 72
LEGEND_BOX_H = 44


def _rects_overlap(a, b):
    """a, b = (x, y, w, h). Return True if they overlap."""
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


def _piece_rect(p):
    return (p['x'], p['y'], p['w'], p['h'])


def _rect_crosses_cut_segment(x, y, w, h, seg) -> bool:
    """Изделие пересекает гильотинный рез внутри протяжённости сегмента (не касание по краю)."""
    pos = float(seg.get('pos') or 0)
    elo = float(seg.get('extent_lo') or 0)
    ehi = float(seg.get('extent_hi') or 0)
    typ = str(seg.get('type') or '').strip().upper()
    if typ == 'V':
        if not (x < pos < x + w):
            return False
        return max(y, elo) < min(y + h, ehi)
    if typ == 'H':
        if not (y < pos < y + h):
            return False
        return max(x, elo) < min(x + w, ehi)
    return False


def _layout_valid(sheet_w, sheet_h, pieces, cut_segments=None):
    """Проверка: все изделия внутри листа, не пересекаются и не пересекают резы."""
    segs = cut_segments or []
    for i, p in enumerate(pieces):
        x, y, w, h = p['x'], p['y'], p['w'], p['h']
        if x < 0 or y < 0 or x + w > sheet_w or y + h > sheet_h:
            return False
        for seg in segs:
            if _rect_crosses_cut_segment(x, y, w, h, seg):
                return False
        for j, q in enumerate(pieces):
            if i == j:
                continue
            if _rects_overlap(_piece_rect(p), _piece_rect(q)):
                return False
    return True


class LayoutEditCanvas(QWidget):
    """Один лист: изделия можно выбирать (клик), перетаскивать с привязкой; показ допустимости размещения."""
    about_to_modify = pyqtSignal()  # перед изменением макета (для сохранения в undo)
    layout_changed = pyqtSignal()   # макет или направление резов изменились — обновить статистику

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_dict = None  # sheet_width, sheet_height, pieces (list of dict with x,y,w,h,...)
        self._sel_indices = set()  # выбранные изделия (один или несколько при Ctrl+клик)
        self.drag_start = None  # (frozenset(indices), mouse_x, mouse_y, {idx:(x,y)}, is_right_button) или для одного (frozenset({idx}), ...)
        self.zoom_factor = 1.0   # 0.5..4.0 — слайдер масштаба (по умолчанию 1)
        self.pan_x = 0
        self.pan_y = 0
        self.pan_start = None    # (mouse_x, mouse_y, pan_x, pan_y) при перетаскивании средней кнопкой
        self.pending_place = None  # (x, y) — при клике по синему полю перенос сюда по отпусканию
        self.hover_placement = None  # (x, y) — допустимая позиция под курсором при перетаскивании (подсветка зелёным)
        self.min_h = 0
        self.min_w = 0  # порог для деловых остатков (по материалу)
        self.cut_direction_mode = False  # режим «Изменить направление реза»: линии H/V, клик — переворот
        self.add_cut_mode = False       # режим «Добавить рез»: клик — добавить горизонтальный/вертикальный рез
        self.delete_cut_mode = False    # режим «Удалить рез»: все резы оранжевые, клик — удалить
        self.add_cut_type = 'V'         # 'H' или 'V' — тип добавляемого реза
        self._sel_cuts = set()  # выбранные линии реза (ключи сегментов)
        self._hover_cut_segment = None   # сегмент под курсором (подсветка зелёным, курсор «рука»)
        self._flip_preview_list = None   # режим смены направления: предпросмотр cut_segments после переворота (без клика)
        self._flip_preview_cache_key = None  # (id(seg), sxq, syq) — не пересчитывать превью на каждый пиксель
        self._delete_preview_list = None  # режим удаления: предпросмотр cut_segments после удаления кластера
        self._delete_preview_cache_key = None
        self._press_cut_key = None       # ключ сегмента при нажатии — переворот по отпусканию
        self._add_cut_hover_sheet = None # (sx, sy) в режиме «Добавить рез» для превью линии
        self._dark_constructor_canvas = False  # тёмная схема (конструктор сессии)
        self.setMinimumSize(500, 400)
        self.setStyleSheet("background-color: #E8EEF5;")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_layout(self, layout_dict):
        self.layout_dict = copy.deepcopy(layout_dict) if layout_dict else None
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
        # Не удаляем cut_segments/cut_rows — сохраняем сохранённые резы при загрузке макета.
        # Если ключа cut_segments ещё нет, _ensure_cut_lines() задаст [] (без автосетки по стыкам деталей).
        self.update()
        self.layout_changed.emit()

    def set_remnant_threshold(self, min_h, min_w):
        self.min_h = int(min_h or 0)
        self.min_w = int(min_w or 0)
        self.update()

    def get_layout(self):
        """Возвращает копию макета (включая cut_segments), чтобы сохранение фиксировало текущее состояние."""
        if not self.layout_dict:
            return None
        return copy.deepcopy(self.layout_dict)

    def _compute_rows_and_segments(self):
        """
        Все границы изделий как возможные резы: каждая горизонтальная линия (y = верх/низ детали)
        и каждая вертикальная (x = лево/право детали). Полная ширина/высота листа — чтобы можно было
        менять направление реза в любом месте (гильотина сохраняется, но выбор H/V везде).
        Возвращает (rows, segments). rows для совместимости с переворотом.
        """
        pieces = self.layout_dict.get('pieces') or []
        sw = self.layout_dict.get('sheet_width') or 0
        sh = self.layout_dict.get('sheet_height') or 0
        if not pieces:
            return [], []
        ys = set()
        xs = set()
        # Только границы между изделиями (не резы по краям листа — они не имеют смысла «резать пустое»)
        for p in pieces:
            if p['y'] > 0:
                ys.add(p['y'])
            if p['y'] + p['h'] < sh:
                ys.add(p['y'] + p['h'])
            if p['x'] > 0:
                xs.add(p['x'])
            if p['x'] + p['w'] < sw:
                xs.add(p['x'] + p['w'])
        # Сначала вертикали, затем горизонтали — ближе к гильотине «полоса → ряды» (канонизация ниже доводит порядок).
        segments = []
        for x in sorted(xs):
            segments.append({
                'type': 'V', 'pos': x, 'extent_lo': 0, 'extent_hi': sh,
                'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh
            })
        for y in sorted(ys):
            segments.append({
                'type': 'H', 'pos': y, 'extent_lo': 0, 'extent_hi': sw,
                'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh
            })
        rows = [[i for i in range(len(pieces))]]  # один «ряд» для совместимости
        return rows, segments

    def _ensure_cut_lines(self):
        """Только гарантировать ключи cut_segments и cut_rows.
        Резы по стыкам деталей сюда не подмешиваем: иначе при «изменить направление» появлялась
        вся сетка горизонталей/вертикалей, она оставалась после переворота одного реза и резала поля пополам."""
        if not self.layout_dict:
            return
        pieces = self.layout_dict.get('pieces') or []
        if 'cut_segments' not in self.layout_dict or self.layout_dict.get('cut_segments') is None:
            self.layout_dict['cut_segments'] = []
        if self.layout_dict.get('cut_rows') is None:
            self.layout_dict['cut_rows'] = [[i for i in range(len(pieces))]]

    def rebuild_cut_segments_from_piece_boundaries(self):
        """Минимальные гильотинные резы по охвату деталей («шоколадка»): без полной сетки по каждому ребру — не режем большой остаток лишними линиями."""
        if not self.layout_dict:
            return
        pieces = self.layout_dict.get('pieces') or []
        if not pieces:
            self.layout_dict['cut_segments'] = []
            self.layout_dict['cut_rows'] = [[]]
            return
        self.layout_dict['cut_rows'] = [[i for i in range(len(pieces))]]
        th = int(self.layout_dict.get('thickness_mm') or 4)
        assign_chocolate_bar_cut_segments_to_layout(
            self.layout_dict, _min_strip_for_thickness(th), min_h=self.min_h, min_w=self.min_w
        )

    def _refresh_free_rects_and_cut_scheme_after_move(self):
        """После перетаскивания деталей: пересчитать остатки; если уже есть схема резов — перестроить линии (алгоритм ТЗ → шоколадка)."""
        if not self.layout_dict:
            return
        pieces = self.layout_dict.get('pieces') or []
        sw = int(self.layout_dict.get('sheet_width') or 0)
        sh = int(self.layout_dict.get('sheet_height') or 0)
        if not pieces or sw <= 0 or sh <= 0:
            return
        br, wr = recompute_free_rects_from_pieces(sw, sh, pieces, self.min_h, self.min_w)
        self.layout_dict['business_rects'] = merge_adjacent_free_rects_for_display(br)
        self.layout_dict['waste_rects'] = merge_adjacent_free_rects_for_display(wr)
        if self._has_actionable_cut_segments():
            self.rebuild_cut_segments_from_piece_boundaries()

    def _has_actionable_cut_segments(self):
        """Есть ли в макете хотя бы один осмысленный сегмент H/V для выбора/переворота."""
        segs = self.layout_dict.get('cut_segments') if self.layout_dict else None
        if not isinstance(segs, list):
            return False
        for s in segs:
            if isinstance(s, dict) and self._segment_type_norm(s) in ('H', 'V'):
                return True
        return False

    def sync_cut_segments_from_piece_edges_if_needed(self):
        """Если на листе есть детали, но нет ни одного валидного реза — взять линии по границам деталей (как на схеме)."""
        if not self.layout_dict:
            return False
        if not (self.layout_dict.get('pieces') or []):
            return False
        self._ensure_cut_lines()
        if self._has_actionable_cut_segments():
            return False
        self.rebuild_cut_segments_from_piece_boundaries()
        return True

    def _segment_key(self, seg):
        return (seg['type'], seg['pos'], seg['extent_lo'], seg['extent_hi'])

    def _segment_key_normalized(self, seg):
        """Ключ с округлёнными числами — для надёжного удаления (int/float не различаем)."""
        def _r(v):
            try:
                return round(float(v))
            except (TypeError, ValueError):
                return v
        if not isinstance(seg, dict):
            return ('', 0, 0, 0)
        typ = self._segment_type_norm(seg)
        return (typ, _r(seg.get('pos', 0)), _r(seg.get('extent_lo', 0)), _r(seg.get('extent_hi', 0)))

    def _segment_axis_key(self, seg):
        """Гильотинная ось: тип линии + координата pos в мм (округл.). Все коллинеарные сегменты с тем же pos снимаются вместе."""
        try:
            return (self._segment_type_norm(seg), round(float(seg.get('pos', 0))))
        except (TypeError, ValueError):
            return (self._segment_type_norm(seg), seg.get('pos'))

    def _segment_type_norm(self, seg):
        if not isinstance(seg, dict):
            return ''
        t = seg.get('type')
        if t is None:
            return ''
        return str(t).strip().upper()

    def _segment_pos_float(self, seg):
        try:
            return float(seg.get('pos', 0))
        except (TypeError, ValueError):
            return 0.0

    def _segment_group_has_hover(self, group):
        h = self._hover_cut_segment
        if h is None or not group:
            return False
        segs = list((self.layout_dict or {}).get('cut_segments') or [])
        ch = self._ui_cluster_indices_for_seg(segs, h)
        cg = self._ui_cluster_indices_for_seg(segs, group[0])
        if ch is not None and cg is not None and ch == cg:
            return True
        return self._segments_share_guillotine_axis(group[0], h)

    def _segment_group_has_sel(self, group):
        if not group or not self._sel_cuts:
            return False
        segs = list((self.layout_dict or {}).get('cut_segments') or [])
        cg = self._ui_cluster_indices_for_seg(segs, group[0])
        if cg is not None:
            for i in cg:
                if self._segment_key_normalized(segs[i]) in self._sel_cuts:
                    return True
        for s in group:
            if self._segment_key_normalized(s) in self._sel_cuts:
                return True
        return False

    def _same_guillotine_axis(self, seg_a, seg_b):
        """Та же гильотинная линия (H на этом y или V на этом x), с допуском по мм и округлению."""
        ta = self._segment_type_norm(seg_a)
        tb = self._segment_type_norm(seg_b)
        if ta != tb or ta not in ('H', 'V'):
            return False
        pa = self._segment_pos_float(seg_a)
        pb = self._segment_pos_float(seg_b)
        if abs(pa - pb) <= CUT_AXIS_POS_MM_TOL:
            return True
        return round(pa) == round(pb)

    def _segments_share_guillotine_axis(self, s, ref):
        """Та же ось гильотины: тип совпадает и pos в одну мм-ступень (как у ключа оси)."""
        ta = self._segment_type_norm(s)
        tb = self._segment_type_norm(ref)
        if ta != tb or ta not in ('H', 'V'):
            return False
        return self._segment_axis_key(s) == self._segment_axis_key(ref)

    def _ui_cluster_groups_indices_typed(self, segments, typ):
        """Индексы сегментов одного типа, сгруппированные: pos отличается не больше CUT_UI_CLUSTER_MAX_SPAN_MM."""
        idxs = [i for i, s in enumerate(segments) if self._segment_type_norm(s) == typ]
        if not idxs:
            return []
        idxs.sort(key=lambda i: self._segment_pos_float(segments[i]))
        groups = []
        k = 0
        while k < len(idxs):
            p0 = self._segment_pos_float(segments[idxs[k]])
            batch = [idxs[k]]
            k += 1
            while k < len(idxs):
                p = self._segment_pos_float(segments[idxs[k]])
                if p - p0 <= CUT_UI_CLUSTER_MAX_SPAN_MM:
                    batch.append(idxs[k])
                    k += 1
                else:
                    break
            groups.append(batch)
        return groups

    def _ui_cluster_indices_for_seg(self, segments, seg):
        """frozenset индексов кластера UI, содержащего seg (по object identity), или None."""
        if not segments or seg is None:
            return None
        for typ in ('H', 'V'):
            for batch in self._ui_cluster_groups_indices_typed(segments, typ):
                if any(segments[i] is seg for i in batch):
                    return frozenset(batch)
        return None

    def _indices_to_remove_for_cut_cluster(self, segments, seg):
        """Индексы всех сегментов той же UI-группы, что и seg (или та же точная ось)."""
        ci = self._ui_cluster_indices_for_seg(segments, seg)
        if ci is not None:
            return set(ci)
        return {i for i, s in enumerate(segments) if self._segments_share_guillotine_axis(s, seg)}

    def _merge_1d_intervals(self, intervals):
        if not intervals:
            return []
        iv = sorted(intervals)
        out = [list(iv[0])]
        for lo, hi in iv[1:]:
            if lo <= out[-1][1] + 1e-6:
                out[-1][1] = max(out[-1][1], hi)
            else:
                out.append([lo, hi])
        return [(float(a[0]), float(a[1])) for a in out]

    def _subtract_interval_from_forbidden(self, lo, hi, forbidden_merged):
        """Вычесть объединение запрещённых интервалов из [lo, hi]. forbidden_merged — непересекающиеся, по возр. lo."""
        cur = [(float(lo), float(hi))]
        min_span = CUT_SEGMENT_MIN_SPAN_MM
        for fl, fh in forbidden_merged:
            nxt = []
            for cl, ch in cur:
                if fh <= cl or fl >= ch:
                    nxt.append((cl, ch))
                    continue
                if fl > cl:
                    nxt.append((cl, min(fl, ch)))
                if fh < ch:
                    nxt.append((max(fh, cl), ch))
            cur = [(a, b) for a, b in nxt if b - a > min_span]
            if not cur:
                return []
        return cur

    def _clip_h_segment_to_piece_interiors(self, hseg, pieces, sw, sh):
        """Убрать части горизонтали, проходящие строго внутри деталей (y внутри без учёта границы)."""
        y = float(hseg.get('pos', 0))
        elo = max(0.0, float(hseg.get('extent_lo', 0)))
        ehi = min(float(sw), float(hseg.get('extent_hi', sw)))
        if elo >= ehi - 0.01:
            return []
        eps = PIECE_INTERIOR_CLIP_EPS
        forbidden = []
        for p in pieces or []:
            px = float(p.get('x', 0))
            py = float(p.get('y', 0))
            pw = float(p.get('w', 0))
            ph = float(p.get('h', 0))
            if py + eps < y < py + ph - eps:
                forbidden.append((px, px + pw))
        if not forbidden:
            return [hseg]
        forbidden = self._merge_1d_intervals(forbidden)
        parts = self._subtract_interval_from_forbidden(elo, ehi, forbidden)
        if not parts:
            return []
        out = []
        for alo, ahi in parts:
            out.append({
                'type': 'H', 'pos': int(round(y)),
                'extent_lo': int(round(alo)), 'extent_hi': int(round(ahi)),
                'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh,
            })
        return out

    def _clip_v_segment_to_piece_interiors(self, vseg, pieces, sw, sh):
        """Убрать части вертикали, проходящие строго внутри деталей (x внутри без учёта границы)."""
        x = float(vseg.get('pos', 0))
        ylo = max(0.0, float(vseg.get('extent_lo', 0)))
        yhi = min(float(sh), float(vseg.get('extent_hi', sh)))
        if ylo >= yhi - 0.01:
            return []
        eps = PIECE_INTERIOR_CLIP_EPS
        forbidden = []
        for p in pieces or []:
            px = float(p.get('x', 0))
            py = float(p.get('y', 0))
            pw = float(p.get('w', 0))
            ph = float(p.get('h', 0))
            if px + eps < x < px + pw - eps:
                forbidden.append((py, py + ph))
        if not forbidden:
            return [vseg]
        forbidden = self._merge_1d_intervals(forbidden)
        parts = self._subtract_interval_from_forbidden(ylo, yhi, forbidden)
        if not parts:
            return []
        out = []
        for alo, ahi in parts:
            out.append({
                'type': 'V', 'pos': int(round(x)),
                'extent_lo': int(round(alo)), 'extent_hi': int(round(ahi)),
                'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh,
            })
        return out

    def _clip_segments_to_piece_interiors(self, segments, pieces, sw, sh):
        """Обрезать все H/V так, чтобы не проходить через внутренность изделий."""
        out = []
        for s in segments or []:
            if not isinstance(s, dict):
                continue
            t = self._segment_type_norm(s)
            if t == 'H':
                out.extend(self._clip_h_segment_to_piece_interiors(s, pieces, sw, sh))
            elif t == 'V':
                out.extend(self._clip_v_segment_to_piece_interiors(s, pieces, sw, sh))
            else:
                out.append(s)
        return out

    def _dedupe_cut_segments_normalized(self, segments):
        """Убрать полные дубликаты сегментов (после округлённого ключа), порядок сохраняем."""
        seen = set()
        out = []
        for s in segments:
            k = self._segment_key_normalized(s)
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
        return out

    def _merged_vertical_y_spans_by_x(self, vs, sh):
        """Для каждой вертикали (округлённый pos) — объединённые интервалы по Y (несколько V на одной оси)."""
        eps = 1.0
        shf = float(sh)
        by_x = {}
        for v in vs or []:
            if self._segment_type_norm(v) != 'V':
                continue
            try:
                xv = round(float(v.get('pos', 0)))
            except (TypeError, ValueError):
                continue
            y0 = float(v.get('extent_lo', 0))
            y1 = float(v.get('extent_hi', shf))
            if y1 < y0:
                y0, y1 = y1, y0
            by_x.setdefault(xv, []).append((y0, y1))
        out = {}
        for xv, intervals in by_x.items():
            iv = sorted(intervals, key=lambda t: t[0])
            merged = []
            for a, b in iv:
                if not merged or a > merged[-1][1] + eps:
                    merged.append([a, b])
                else:
                    merged[-1][1] = max(merged[-1][1], b)
            out[xv] = [(float(m[0]), float(m[1])) for m in merged]
        return out

    def _vertical_xs_from_segments_for_horizontal(self, y, vs, sw, sh, elo, ehi, eps):
        """X вертикалей внутри (elo,ehi), у которых объединённый интервал по Y содержит y (границы включительно)."""
        shf = float(sh)
        y = float(y)
        elo, ehi = float(elo), float(ehi)
        if elo >= ehi - 0.01:
            return []
        merged_by_x = self._merged_vertical_y_spans_by_x(vs, shf)
        xs = []
        for xv, spans in merged_by_x.items():
            if not (elo + eps < xv < ehi - eps):
                continue
            for y0, y1 in spans:
                if y0 - eps <= y <= y1 + eps:
                    xs.append(float(xv))
                    break
        return xs

    def _piece_column_boundary_x_at_y(self, y, pieces, elo, ehi, eps=1.0):
        """
        Внутренние вертикальные стыки двух деталей (правый край левой = левый край правой),
        попадающие строго внутрь интервала протяжённости горизонтали. На высоте y детали должны
        перекрываться по Y — иначе это другой «этаж» гильотины и общая горизонталь не должна
        резать уже отделённую полосу.
        """
        if not pieces:
            return []
        y = float(y)
        elo, ehi = float(elo), float(ehi)
        xs = []
        n = len(pieces)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = pieces[i], pieces[j]
                ax, ay = float(a.get('x', 0)), float(a.get('y', 0))
                aw, ah = float(a.get('w', 0)), float(a.get('h', 0))
                bx, by = float(b.get('x', 0)), float(b.get('y', 0))
                bw, bh = float(b.get('w', 0)), float(b.get('h', 0))
                ar, ab = ax + aw, ay + ah
                br, bb = bx + bw, by + bh
                xv = None
                if abs(ar - bx) <= eps:
                    xv = (ar + bx) * 0.5
                elif abs(br - ax) <= eps:
                    xv = (br + ax) * 0.5
                else:
                    continue
                if not (elo + eps < xv < ehi - eps):
                    continue
                iy_lo = max(ay, by)
                iy_hi = min(ab, bb)
                if iy_hi < iy_lo - eps:
                    continue
                if iy_lo - eps <= y <= iy_hi + eps:
                    xs.append(xv)
        return xs

    def _split_h_at_verticals(self, hseg, vs, pieces, sw, sh):
        """Разбить горизонталь по вертикалям (объединённым по X) и по вертикальным стыкам деталей-колонок."""
        y = float(hseg.get('pos', 0))
        elo = float(hseg.get('extent_lo', 0))
        ehi = float(hseg.get('extent_hi', sw))
        if elo >= ehi - 0.01:
            return []
        eps = 1.0
        # Первый полноширинный рез под чистым верхом не дробим по стыкам колонок — одна «синяя» линия.
        if (
            pieces is not None
            and elo <= 1.5
            and ehi >= float(sw) - 1.5
            and self._h_is_first_cut_under_empty_top(pieces, y, 1.5)
        ):
            return [hseg]
        xs = []
        xs.extend(self._vertical_xs_from_segments_for_horizontal(y, vs, sw, sh, elo, ehi, eps))
        try:
            xs.extend(self._piece_column_boundary_x_at_y(y, pieces or [], elo, ehi, eps))
        except Exception:
            pass
        if not xs:
            return [hseg]
        xs = sorted({round(float(x)) for x in xs if elo + eps < float(x) < ehi - eps})
        out = []
        cur = elo
        for xv in xs:
            xf = float(xv)
            if xf - cur > 0.01:
                out.append({
                    'type': 'H', 'pos': int(round(y)),
                    'extent_lo': int(round(cur)), 'extent_hi': int(round(xv)),
                    'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh,
                })
            cur = xf
        if ehi - cur > 0.01:
            out.append({
                'type': 'H', 'pos': int(round(y)),
                'extent_lo': int(round(cur)), 'extent_hi': int(round(ehi)),
                'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh,
            })
        return out if out else [hseg]

    def _split_v_at_horizontals(self, vseg, hs, sw, sh):
        """Разбить вертикальный сегмент в каждой внутренней точке пересечения с горизонталями."""
        x = float(vseg.get('pos', 0))
        ylo = float(vseg.get('extent_lo', 0))
        yhi = float(vseg.get('extent_hi', sh))
        if ylo >= yhi - 0.01:
            return []
        ys = []
        for h in hs:
            if self._segment_type_norm(h) != 'H':
                continue
            yh = float(h.get('pos', 0))
            hx0 = float(h.get('extent_lo', 0))
            hx1 = float(h.get('extent_hi', sw))
            if hx1 < hx0:
                hx0, hx1 = hx1, hx0
            if ylo < yh < yhi and hx0 < x < hx1:
                ys.append(yh)
        if not ys:
            return [vseg]
        ys = sorted(set(ys))
        out = []
        cur = ylo
        for yh in ys:
            if yh - cur > 0.01:
                out.append({
                    'type': 'V', 'pos': int(round(x)),
                    'extent_lo': int(round(cur)), 'extent_hi': int(round(yh)),
                    'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh,
                })
            cur = yh
        if yhi - cur > 0.01:
            out.append({
                'type': 'V', 'pos': int(round(x)),
                'extent_lo': int(round(cur)), 'extent_hi': int(round(yhi)),
                'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh,
            })
        return out if out else [vseg]

    def _subdivide_cut_segments_at_crossings(self, segments, sw, sh, pieces=None):
        """Укоротить/разбить сегменты у пересечений H↔V (рез не «пробивает» другой рез)."""
        pieces = list(pieces or [])
        segs = [
            dict(s) for s in (segments or [])
            if isinstance(s, dict) and self._segment_type_norm(s) in ('H', 'V')
        ]
        if not segs:
            return segs
        prev_sig = None
        for _ in range(32):
            hs = [s for s in segs if self._segment_type_norm(s) == 'H']
            vs = [s for s in segs if self._segment_type_norm(s) == 'V']
            new_hs = []
            for h in hs:
                new_hs.extend(self._split_h_at_verticals(h, vs, pieces, sw, sh))
            new_vs = []
            for v in vs:
                new_vs.extend(self._split_v_at_horizontals(v, new_hs, sw, sh))
            new_segs = new_hs + new_vs
            new_segs = self._dedupe_cut_segments_normalized(new_segs)
            sig = tuple(sorted(self._segment_key_normalized(s) for s in new_segs))
            if sig == prev_sig:
                break
            prev_sig = sig
            segs = new_segs
        return segs

    def _trim_h_segments_east_of_lone_full_height_v(self, segments, sw, sh):
        """
        Гильотина «сначала полоса»: находим вертикали, которые почти на всю высоту листа (после subdivide
        это несколько V с одним pos — склеиваем интервалы по Y). Из автосетки по деталям часто много таких
        линий; берём самую левую подходящую (не у краёв листа) и убираем/подрезаем горизонтали справа от неё.
        Раньше требовалась ровно одна такая V — при сетке из границ деталей условие никогда не выполнялось.
        """
        eps = 2.0
        sh_f = float(sh)
        sw_f = float(sw)
        x_margin = max(25.0, sw_f * 0.04)
        v_groups = {}
        for s in segments:
            if self._segment_type_norm(s) != 'V':
                continue
            key = self._segment_axis_key(s)
            el = float(s.get('extent_lo', 0))
            eh = float(s.get('extent_hi', sh))
            if el > eh:
                el, eh = eh, el
            xp = float(s.get('pos', 0))
            v_groups.setdefault(key, []).append((el, eh, xp))
        tall_x = []
        for _key, parts in v_groups.items():
            intervals = [(a, b) for a, b, _ in parts]
            intervals.sort(key=lambda t: t[0])
            merged = []
            for a, b in intervals:
                if not merged or a > merged[-1][1] + eps:
                    merged.append([a, b])
                else:
                    merged[-1][1] = max(merged[-1][1], b)
            total_span = sum(m[1] - m[0] for m in merged)
            hull_lo = min(m[0] for m in merged)
            hull_hi = max(m[1] for m in merged)
            if total_span < sh_f * 0.82:
                continue
            if hull_lo > 10.0 or hull_hi < sh_f - 10.0:
                continue
            xv = float(parts[0][2])
            if xv < x_margin or xv > sw_f - x_margin:
                continue
            tall_x.append(xv)
        if not tall_x:
            return list(segments)
        strip_min = max(x_margin, sw_f * 0.12)
        primary = [x for x in tall_x if x >= strip_min]
        if not primary:
            primary = tall_x
        xv = float(min(primary))
        out = []
        for s in segments:
            if self._segment_type_norm(s) != 'H':
                out.append(s)
                continue
            elo = float(s.get('extent_lo', 0))
            ehi = float(s.get('extent_hi', sw))
            if elo >= xv - eps:
                continue
            if ehi <= xv + eps:
                out.append(s)
                continue
            clipped = dict(s)
            clipped['extent_hi'] = int(round(xv))
            if float(clipped['extent_hi']) - elo <= eps:
                continue
            out.append(clipped)
        return out

    def _left_column_strip_right_mm(self, pieces):
        """Правый край «левой колонки»: max (x+w) у деталей, прижатых к минимальному X (гильотина сначала полоса)."""
        if not pieces:
            return None
        minx = min(float(p.get('x', 0)) for p in pieces)
        eps = 2.0
        lefties = [p for p in pieces if float(p.get('x', 0)) <= minx + eps]
        sr = max(float(p.get('x', 0)) + float(p.get('w', 0)) for p in lefties)
        if sr <= eps:
            return None
        return int(round(sr))

    def _split_h_segments_at_left_column_edge(self, segments, pieces, sw, sh):
        """Разрезать горизонтали, пересекающие границу левой колонки, на [0,sr] и [sr,sw] без полной ширины листа."""
        sr = self._left_column_strip_right_mm(pieces)
        if sr is None or sr <= 0 or sr >= int(sw):
            return list(segments or [])
        eps = 1.0
        out = []
        for s in segments or []:
            if self._segment_type_norm(s) != 'H':
                out.append(s)
                continue
            elo = float(s.get('extent_lo', 0))
            ehi = float(s.get('extent_hi', sw))
            y = float(s.get('pos', 0))
            # Первый полноширинный рез под чистой полосой сверху не дробим — иначе «синяя» линия превращается в куски.
            if self._h_is_first_cut_under_empty_top(pieces, y, eps):
                out.append(s)
                continue
            if elo >= sr - eps or ehi <= sr + eps:
                out.append(s)
                continue
            if ehi - elo <= 2 * eps:
                out.append(s)
                continue
            out.append({
                'type': 'H', 'pos': int(round(y)),
                'extent_lo': int(round(elo)), 'extent_hi': int(round(sr)),
                'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh,
            })
            out.append({
                'type': 'H', 'pos': int(round(y)),
                'extent_lo': int(round(sr)), 'extent_hi': int(round(ehi)),
                'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh,
            })
        return out

    def _h_is_first_cut_under_empty_top(self, pieces, y, eps=1.5):
        """Над горизонталом y нет целиком уложенных деталей — рез как «первая полоса под чистым верхом» (на всю ширину листа)."""
        for p in pieces or []:
            try:
                py = float(p.get('y', 0))
                ph = float(p.get('h', 0))
            except (TypeError, ValueError):
                continue
            if py + ph <= float(y) + eps:
                return False
        for p in pieces or []:
            try:
                py = float(p.get('y', 0))
            except (TypeError, ValueError):
                continue
            if py >= float(y) - eps:
                return True
        return False

    def _max_piece_right_touching_horizontal(self, pieces, y, eps=1.5):
        """Правый край по всем деталям, чья проекция по Y пересекает горизонталь y (включая границы)."""
        mx = 0.0
        y = float(y)
        for p in pieces or []:
            try:
                px = float(p.get('x', 0))
                py = float(p.get('y', 0))
                pw = float(p.get('w', 0))
                ph = float(p.get('h', 0))
            except (TypeError, ValueError):
                continue
            if py - eps <= y <= py + ph + eps:
                mx = max(mx, px + pw)
        return mx

    def _shorten_h_segments_past_piece_column(self, segments, pieces, sw, sh):
        """
        Не тянуть горизонтальные резы через большой правый остаток: обрезать extent_hi по правому краю деталей
        на этой высоте. Исключение — первый рез под полностью пустым верхом (синяя линия на всю ширину).
        Полноширинные линии в чистом поле без деталей на y удаляются.
        """
        eps = 1.5
        swf = float(sw)
        out = []
        for s in segments or []:
            if self._segment_type_norm(s) != 'H':
                out.append(s)
                continue
            try:
                y = float(s.get('pos', 0))
                elo = float(s.get('extent_lo', 0))
                ehi = float(s.get('extent_hi', sw))
            except (TypeError, ValueError):
                out.append(s)
                continue
            if self._h_is_first_cut_under_empty_top(pieces, y, eps):
                out.append(s)
                continue
            mx = self._max_piece_right_touching_horizontal(pieces, y, eps)
            if mx <= eps:
                if elo <= eps and ehi >= swf - eps:
                    continue
                out.append(s)
                continue
            cap = min(ehi, mx)
            if cap - elo < CUT_SEGMENT_MIN_SPAN_MM:
                continue
            if cap + eps >= ehi:
                out.append(s)
                continue
            ns = dict(s)
            ns['extent_hi'] = int(round(cap))
            out.append(ns)
        return out

    def _lift_full_span_verticals_to_min_piece_y(self, segments, pieces, sh):
        """Вертикали «от пола до потолка» поднимаем до верхнего края первого ряда деталей — как красная линия от первого горизонта, без пропила чистого верха."""
        if not pieces:
            return list(segments or [])
        try:
            y0 = min(float(p.get('y', 0)) for p in pieces)
        except (TypeError, ValueError):
            return list(segments or [])
        if y0 <= 1.5:
            return list(segments or [])
        shf = float(sh)
        eps = 1.5
        out = []
        for s in segments or []:
            if self._segment_type_norm(s) != 'V':
                out.append(s)
                continue
            try:
                clo = float(s.get('extent_lo', 0))
                chi = float(s.get('extent_hi', sh))
            except (TypeError, ValueError):
                out.append(s)
                continue
            if chi < clo:
                clo, chi = chi, clo
            if clo > eps or chi < shf - eps:
                out.append(s)
                continue
            ns = dict(s)
            ns['extent_lo'] = int(round(y0))
            ns['extent_hi'] = int(round(shf))
            out.append(ns)
        return out

    def _finalize_cut_segments_list(self, segments, sw, sh, pieces=None):
        """Дедуп → обрезка по деталям → разбиение по пересечениям H↔V → снова по деталям → trim по «полосе»."""
        if pieces is None:
            pieces = (self.layout_dict or {}).get('pieces') or []
        pieces = list(pieces or [])
        d = self._dedupe_cut_segments_normalized(list(segments or []))
        # До разбиения по пересечениям: убрать «ноги» вертикалей через пустой верх — иначе они режут первый горизонт на мелкие сегменты.
        d = self._lift_full_span_verticals_to_min_piece_y(d, pieces, sh)
        d = self._dedupe_cut_segments_normalized(d)
        d = self._clip_segments_to_piece_interiors(d, pieces, sw, sh)
        d = self._dedupe_cut_segments_normalized(d)
        d = self._subdivide_cut_segments_at_crossings(d, sw, sh, pieces)
        d = self._dedupe_cut_segments_normalized(d)
        d = self._split_h_segments_at_left_column_edge(d, pieces, sw, sh)
        d = self._dedupe_cut_segments_normalized(d)
        d = self._clip_segments_to_piece_interiors(d, pieces, sw, sh)
        d = self._dedupe_cut_segments_normalized(d)
        d = self._trim_h_segments_east_of_lone_full_height_v(d, sw, sh)
        d = self._dedupe_cut_segments_normalized(d)
        d = self._shorten_h_segments_past_piece_column(d, pieces, sw, sh)
        d = self._dedupe_cut_segments_normalized(d)
        d = self._lift_full_span_verticals_to_min_piece_y(d, pieces, sh)
        d = self._dedupe_cut_segments_normalized(d)
        for s in d:
            if not isinstance(s, dict):
                continue
            if self._segment_type_norm(s) not in ('H', 'V'):
                continue
            try:
                s['pos'] = int(round(float(s.get('pos', 0))))
            except (TypeError, ValueError):
                pass
        d = self._dedupe_cut_segments_normalized(d)
        d = reorder_cut_segments_for_guillotine_simulation(d, sw, sh)
        return self._dedupe_cut_segments_normalized(d)

    def _drop_verticals_subsumed_by(self, segments, cover_v):
        """Убрать вертикали на той же оси (pos), целиком лежащие внутри протяжённости cover_v (после H→V не дублировать старую частичную V)."""
        if not segments or not cover_v or self._segment_type_norm(cover_v) != 'V':
            return list(segments or [])
        try:
            cx = int(round(float(cover_v.get('pos', 0))))
            clo = float(cover_v.get('extent_lo', 0))
            chi = float(cover_v.get('extent_hi', 0))
        except (TypeError, ValueError):
            return list(segments or [])
        if chi < clo:
            clo, chi = chi, clo
        eps = 1.0
        out = []
        for s in segments:
            if self._segment_type_norm(s) != 'V':
                out.append(s)
                continue
            try:
                sx = int(round(float(s.get('pos', 0))))
                slo = float(s.get('extent_lo', 0))
                shi = float(s.get('extent_hi', 0))
            except (TypeError, ValueError):
                out.append(s)
                continue
            if shi < slo:
                slo, shi = shi, slo
            if sx == cx and slo >= clo - eps and shi <= chi + eps:
                continue
            out.append(s)
        return out

    def _drop_horizontals_subsumed_by(self, segments, cover_h):
        """Убрать горизонтали на той же оси (pos), целиком внутри протяжённости cover_h (после V→H)."""
        if not segments or not cover_h or self._segment_type_norm(cover_h) != 'H':
            return list(segments or [])
        try:
            cy = int(round(float(cover_h.get('pos', 0))))
            clo = float(cover_h.get('extent_lo', 0))
            chi = float(cover_h.get('extent_hi', 0))
        except (TypeError, ValueError):
            return list(segments or [])
        if chi < clo:
            clo, chi = chi, clo
        eps = 1.0
        out = []
        for s in segments:
            if self._segment_type_norm(s) != 'H':
                out.append(s)
                continue
            try:
                sy = int(round(float(s.get('pos', 0))))
                slo = float(s.get('extent_lo', 0))
                shi = float(s.get('extent_hi', 0))
            except (TypeError, ValueError):
                out.append(s)
                continue
            if shi < slo:
                slo, shi = shi, slo
            if sy == cy and slo >= clo - eps and shi <= chi + eps:
                continue
            out.append(s)
        return out

    def _compute_flip_new_list(self, seg, segments, pieces, sw, sh, click_sx=None, click_sy=None):
        """
        Список cut_segments после переворота seg (кластер UI целиком), без записи в layout.
        Возвращает None если переворот невозможен.
        """
        segments = list(segments or [])
        to_remove = self._indices_to_remove_for_cut_cluster(segments, seg)
        if not to_remove:
            return None
        remaining_segments = [s for i, s in enumerate(segments) if i not in to_remove]

        def obstacles_y_for_vertical(x_val):
            out = {0.0, float(sh)}
            for s in remaining_segments:
                if self._segment_type_norm(s) == 'H' and s.get('extent_lo', 0) <= x_val <= s.get('extent_hi', sw):
                    out.add(float(s.get('pos', 0)))
            eps = PIECE_INTERIOR_CLIP_EPS
            xv = float(x_val)
            for p in pieces:
                px = float(p['x'])
                py = float(p['y'])
                pw = float(p['w'])
                ph = float(p['h'])
                # Только строго внутри проекции детали: на общем вертикальном шве x = правый/левый край
                # не должен давать «ломаную» сетку препятствий и отрезать ref_y (линия старого H).
                if pw > 2 * eps and px + eps < xv < px + pw - eps:
                    out.add(py)
                    out.add(py + ph)
            return sorted(out)

        def obstacles_x_for_horizontal(y_val):
            out = {0.0, float(sw)}
            for s in remaining_segments:
                if self._segment_type_norm(s) == 'V' and s.get('extent_lo', 0) <= y_val <= s.get('extent_hi', sh):
                    out.add(float(s.get('pos', 0)))
            eps = PIECE_INTERIOR_CLIP_EPS
            yv = float(y_val)
            for p in pieces:
                px = float(p['x'])
                py = float(p['y'])
                pw = float(p['w'])
                ph = float(p['h'])
                if ph > 2 * eps and py + eps < yv < py + ph - eps:
                    out.add(px)
                    out.add(px + pw)
            return sorted(out)

        def best_gap_among_obstacles(obstacles, ref_pos, eps=1.0):
            """Самый длинный интервал между соседними препятствиями, который пересекает ref_pos (включая границу с допуском).
            Иначе при ref на шве детали выбирается «короткая» половина (0, ref) вместо полного листа — переворот H→V визуально не меняет схему."""
            oy = sorted({float(x) for x in (obstacles or [])})
            if len(oy) < 2:
                return None
            ref = float(ref_pos)
            best_lo, best_hi = None, None
            best_span = -1.0
            for i in range(len(oy) - 1):
                lo, hi = oy[i], oy[i + 1]
                if hi - lo < CUT_SEGMENT_MIN_SPAN_MM:
                    continue
                if not (lo - eps <= ref <= hi + eps):
                    continue
                span = hi - lo
                if span > best_span:
                    best_span = span
                    best_lo, best_hi = lo, hi
            if best_lo is None:
                return None
            return (best_lo, best_hi)

        new_seg = None
        typ_flip = self._segment_type_norm(seg)
        cluster_segs = [segments[i] for i in to_remove if self._segment_type_norm(segments[i]) == typ_flip]
        if not cluster_segs:
            cluster_segs = [seg]
        old_pos = sum(self._segment_pos_float(s) for s in cluster_segs) / max(1, len(cluster_segs))
        ext_lo = min(float(s.get('extent_lo', 0)) for s in cluster_segs)
        if typ_flip == 'H':
            ext_hi = max(float(s.get('extent_hi', sw)) for s in cluster_segs)
        else:
            ext_hi = max(float(s.get('extent_hi', sh)) for s in cluster_segs)
        use_magnet = click_sx is not None and click_sy is not None

        if typ_flip == 'H':
            # Новый рез вертикальный (V): перебираем кандидатов X (края деталей + ось резки), выбираем пару (X, зазор по Y)
            # с максимальной протяжённостью по Y, затем ближе к ref_x — чтобы не получить «короткую» вертикаль на границе шва.
            candidates_x = []
            for p in pieces:
                candidates_x.append(float(p['x']))
                candidates_x.append(float(p['x']) + float(p['w']))
            mid_ax = (float(ext_lo) + float(ext_hi)) * 0.5
            candidates_x.append(mid_ax)
            for x in (ext_lo, ext_hi):
                candidates_x.append(float(x))
            if use_magnet:
                candidates_x.append(float(click_sx))
            uniq_x = sorted({int(round(max(0, min(sw, c)))) for c in candidates_x if 0 < c < sw})
            if not uniq_x:
                uniq_x = list(range(max(10, sw // 20), sw, max(10, sw // 20)))
            ref_x = float(click_sx) if use_magnet else mid_ax
            ref_x = max(0, min(sw, ref_x))
            ref_y = float(click_sy) if use_magnet else float(old_pos)
            ref_y = max(0, min(sh, ref_y))
            best_pick = None  # (span, -abs(pos_x - ref_x), pos_x, lo, hi)
            for pos_x in uniq_x:
                obs_y = obstacles_y_for_vertical(pos_x)
                gap = best_gap_among_obstacles(obs_y, ref_y)
                if not gap:
                    continue
                lo, hi = gap
                span = hi - lo
                cand = (span, -abs(float(pos_x) - ref_x), float(pos_x), lo, hi)
                if best_pick is None or cand > best_pick:
                    best_pick = cand
            if best_pick:
                _, _, pos_x_f, lo, hi = best_pick
                pos_x = int(round(pos_x_f))
                new_seg = {'type': 'V', 'pos': pos_x, 'extent_lo': int(round(lo)), 'extent_hi': int(round(hi)),
                           'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh}
        else:
            # Новый рез горизонтальный (H): аналогично — максимальный зазор по X для выбранной Y.
            candidates_y = []
            for p in pieces:
                candidates_y.append(float(p['y']))
                candidates_y.append(float(p['y']) + float(p['h']))
            mid_ay = (float(ext_lo) + float(ext_hi)) * 0.5
            candidates_y.append(mid_ay)
            for y in (ext_lo, ext_hi):
                candidates_y.append(float(y))
            if use_magnet:
                candidates_y.append(float(click_sy))
            uniq_y = sorted({int(round(max(0, min(sh, c)))) for c in candidates_y if 0 < c < sh})
            if not uniq_y:
                uniq_y = list(range(max(10, sh // 20), sh, max(10, sh // 20)))
            ref_y = float(click_sy) if use_magnet else mid_ay
            ref_y = max(0, min(sh, ref_y))
            ref_x = float(click_sx) if use_magnet else float(old_pos)
            ref_x = max(0, min(sw, ref_x))
            best_pick = None
            for pos_y in uniq_y:
                obs_x = obstacles_x_for_horizontal(pos_y)
                gap = best_gap_among_obstacles(obs_x, ref_x)
                if not gap:
                    continue
                lo, hi = gap
                span = hi - lo
                cand = (span, -abs(float(pos_y) - ref_y), float(pos_y), lo, hi)
                if best_pick is None or cand > best_pick:
                    best_pick = cand
            if best_pick:
                _, _, pos_y_f, lo, hi = best_pick
                pos_y = int(round(pos_y_f))
                new_seg = {'type': 'H', 'pos': pos_y, 'extent_lo': int(round(lo)), 'extent_hi': int(round(hi)),
                           'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh}

        if new_seg is None:
            return None

        if typ_flip == 'H' and self._segment_type_norm(new_seg) == 'V':
            remaining_segments = self._drop_verticals_subsumed_by(remaining_segments, new_seg)
        elif typ_flip == 'V' and self._segment_type_norm(new_seg) == 'H':
            remaining_segments = self._drop_horizontals_subsumed_by(remaining_segments, new_seg)

        new_list = []
        inserted = False
        for i, s in enumerate(segments):
            if i in to_remove:
                if not inserted and new_seg is not None:
                    new_list.append(new_seg)
                    inserted = True
                continue
            new_list.append(s)
        if not inserted and new_seg is not None:
            new_list.append(new_seg)
        return self._finalize_cut_segments_list(new_list, sw, sh, pieces)

    def _flip_segment(self, seg, click_sx=None, click_sy=None):
        """
        Удалить кластер UI (все почти коллинеарные фрагменты) и добавить один перпендикулярный рез.
        """
        if not self.layout_dict:
            return False
        pieces = self.layout_dict.get('pieces') or []
        sw = self.layout_dict.get('sheet_width') or 0
        sh = self.layout_dict.get('sheet_height') or 0
        segments = list(self.layout_dict.get('cut_segments') or [])
        new_list = self._compute_flip_new_list(seg, segments, pieces, sw, sh, click_sx, click_sy)
        if new_list is None:
            return False
        self.layout_dict['cut_segments'] = new_list
        self.layout_dict['cut_rows'] = self.layout_dict.get('cut_rows') or [[i for i in range(len(pieces))]]
        self._hover_cut_segment = None
        self._flip_preview_list = None
        self._flip_preview_cache_key = None
        self._delete_preview_list = None
        self._delete_preview_cache_key = None
        business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, pieces, self.min_h, self.min_w)
        for s in new_list:
            business_rects, waste_rects = self._split_rects_by_segment(business_rects + waste_rects, s)
        self.layout_dict['business_rects'] = business_rects
        self.layout_dict['waste_rects'] = waste_rects
        self.update()
        return True

    def _compute_delete_new_list(self, seg, segments, pieces, sw, sh):
        """Список cut_segments после удаления кластера UI, без записи в layout."""
        segments = list(segments or [])
        rm = self._indices_to_remove_for_cut_cluster(segments, seg)
        if not rm:
            return None
        new_list = [s for i, s in enumerate(segments) if i not in rm]
        return self._finalize_cut_segments_list(new_list, sw, sh, pieces)

    def _split_rects_by_segment(self, rects_list, seg):
        """Разбить список прямоугольников (business+waste) одним сегментом реза; вернуть (business_rects, waste_rects)."""
        from logic.cutting_algorithm import _is_business_rect
        min_h, min_w = self.min_h, self.min_w
        out_business = []
        out_waste = []
        for r in rects_list:
            x, y = r.get('x', 0), r.get('y', 0)
            w, h = r.get('w', 0), r.get('h', 0)
            if self._segment_type_norm(seg) == 'V':
                pos, elo, ehi = seg['pos'], seg['extent_lo'], seg['extent_hi']
                if not (x < pos < x + w and not (ehi <= y or y + h <= elo)):
                    if _is_business_rect(w, h, min_h, min_w):
                        out_business.append(r)
                    else:
                        out_waste.append(r)
                    continue
                r1 = {'x': x, 'y': y, 'w': pos - x, 'h': h}
                r2 = {'x': pos, 'y': y, 'w': x + w - pos, 'h': h}
                for nr in (r1, r2):
                    if nr['w'] <= 0 or nr['h'] <= 0:
                        continue
                    if _is_business_rect(nr['w'], nr['h'], min_h, min_w):
                        out_business.append(nr)
                    else:
                        out_waste.append(nr)
            else:
                pos, elo, ehi = seg['pos'], seg['extent_lo'], seg['extent_hi']
                if not (y < pos < y + h and not (ehi <= x or x + w <= elo)):
                    if _is_business_rect(w, h, min_h, min_w):
                        out_business.append(r)
                    else:
                        out_waste.append(r)
                    continue
                r1 = {'x': x, 'y': y, 'w': w, 'h': pos - y}
                r2 = {'x': x, 'y': pos, 'w': w, 'h': y + h - pos}
                for nr in (r1, r2):
                    if nr['w'] <= 0 or nr['h'] <= 0:
                        continue
                    if _is_business_rect(nr['w'], nr['h'], min_h, min_w):
                        out_business.append(nr)
                    else:
                        out_waste.append(nr)
        return out_business, out_waste

    def _get_free_rects_for_draw(self):
        """Текущие деловые остатки и отходы для отрисовки: по текущим изделиям и резам (без белых дыр после перемещения)."""
        if not self.layout_dict:
            return [], []
        pieces = self.layout_dict.get('pieces') or []
        sw = self.layout_dict.get('sheet_width') or 0
        sh = self.layout_dict.get('sheet_height') or 0
        try:
            business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, pieces, self.min_h, self.min_w)
        except Exception:
            return [], []
        for seg in self.layout_dict.get('cut_segments') or []:
            business_rects, waste_rects = self._split_rects_by_segment(business_rects + waste_rects, seg)
        business_rects = merge_adjacent_free_rects_for_display(business_rects)
        waste_rects = merge_adjacent_free_rects_for_display(waste_rects)
        return business_rects, waste_rects

    def _delete_segment(self, seg):
        """Удалить рез по оси гильотины (все коллинеарные сегменты с тем же pos); пересчитать свободные области."""
        if not self.layout_dict:
            return False
        segments = list(self.layout_dict.get('cut_segments') or [])
        rm = self._indices_to_remove_for_cut_cluster(segments, seg)
        new_segments = [s for i, s in enumerate(segments) if i not in rm]
        if len(new_segments) == len(segments):
            return False
        pieces = self.layout_dict.get('pieces') or []
        sw = self.layout_dict.get('sheet_width') or 0
        sh = self.layout_dict.get('sheet_height') or 0
        new_segments = self._finalize_cut_segments_list(new_segments, sw, sh, pieces)
        self.layout_dict['cut_segments'] = new_segments
        business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, pieces, self.min_h, self.min_w)
        for s in new_segments:
            business_rects, waste_rects = self._split_rects_by_segment(business_rects + waste_rects, s)
        self.layout_dict['business_rects'] = business_rects
        self.layout_dict['waste_rects'] = waste_rects
        self.layout_dict['cut_rows'] = self.layout_dict.get('cut_rows') or [[i for i in range(len(pieces))]]
        self._hover_cut_segment = None
        self._delete_preview_list = None
        self._delete_preview_cache_key = None
        self.update()
        return True

    def _compute_add_cut_segment_at(self, sx, sy, segments_list=None):
        """
        Вычислить сегмент реза (привязка к ближайшей стороне изделия, обрезка до перпендикуляра/изделия/края листа)
        в точке (sx, sy) без изменения макета. Возвращает dict сегмента или None.
        segments_list: список сегментов для препятствий (по умолчанию текущий cut_segments).
        """
        if not self.layout_dict:
            return None
        pieces = self.layout_dict.get('pieces') or []
        sw = self.layout_dict.get('sheet_width') or 0
        sh = self.layout_dict.get('sheet_height') or 0
        segments = segments_list if segments_list is not None else list(self.layout_dict.get('cut_segments') or [])

        def obstacles_y_for_vertical(x_val):
            out = {0.0, float(sh)}
            for s in segments:
                if self._segment_type_norm(s) == 'H' and s.get('extent_lo', 0) <= x_val <= s.get('extent_hi', sw):
                    out.add(float(s.get('pos', 0)))
            for p in pieces:
                if p['x'] <= x_val <= p['x'] + p['w']:
                    out.add(float(p['y']))
                    out.add(float(p['y'] + p['h']))
            return sorted(out)

        def obstacles_x_for_horizontal(y_val):
            out = {0.0, float(sw)}
            for s in segments:
                if self._segment_type_norm(s) == 'V' and s.get('extent_lo', 0) <= y_val <= s.get('extent_hi', sh):
                    out.add(float(s.get('pos', 0)))
            for p in pieces:
                if p['y'] <= y_val <= p['y'] + p['h']:
                    out.add(float(p['x']))
                    out.add(float(p['x'] + p['w']))
            return sorted(out)

        def gap_containing(obstacles, pos, min_size=0.5):
            for i in range(len(obstacles) - 1):
                lo, hi = float(obstacles[i]), float(obstacles[i + 1])
                if lo <= pos <= hi and (hi - lo) >= min_size:
                    return lo, hi
            return None

        # Рез всегда привязан к ближайшему краю изделия в этом направлении (магнит): вертикальный — к ближайшему X края детали, горизонтальный — к Y. Клик может быть где угодно — позиция реза = ближайший край.
        if self.add_cut_type == 'V':
            candidates_x = []
            for p in pieces:
                candidates_x.append(p['x'])
                candidates_x.append(p['x'] + p['w'])
            candidates_x = sorted(set(c for c in candidates_x if 0 < c < sw))
            if not candidates_x:
                return None
            pos = min(candidates_x, key=lambda c: abs(c - sx))
            obs_y = obstacles_y_for_vertical(pos)
            gap = gap_containing(obs_y, float(sy))
            if not gap:
                return None
            lo, hi = gap
            return {'type': 'V', 'pos': pos, 'extent_lo': lo, 'extent_hi': hi,
                    'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh}
        else:
            candidates_y = []
            for p in pieces:
                candidates_y.append(p['y'])
                candidates_y.append(p['y'] + p['h'])
            candidates_y = sorted(set(c for c in candidates_y if 0 < c < sh))
            if not candidates_y:
                return None
            pos = min(candidates_y, key=lambda c: abs(c - sy))
            obs_x = obstacles_x_for_horizontal(pos)
            gap = gap_containing(obs_x, float(sx))
            if not gap:
                return None
            lo, hi = gap
            return {'type': 'H', 'pos': pos, 'extent_lo': lo, 'extent_hi': hi,
                    'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh}

    def _add_cut_at_sheet(self, sx, sy):
        """
        Добавить один гильотинный рез в точке (sx, sy): вертикальный или горизонтальный,
        обрезанный до перпендикуляра/края листа/другого реза/изделия. Один клик — один сегмент.
        Разбить затронутые свободные прямоугольники (business_rects, waste_rects) и переклассифицировать.
        Не вызываем _ensure_cut_lines() — иначе по первому клику подтянулись бы все границы изделий (много линий).
        """
        if not self.layout_dict:
            return False
        pieces = self.layout_dict.get('pieces') or []
        sw = self.layout_dict.get('sheet_width') or 0
        sh = self.layout_dict.get('sheet_height') or 0
        segments = list(self.layout_dict.get('cut_segments') or [])

        new_seg = self._compute_add_cut_segment_at(sx, sy, segments)
        if new_seg is None:
            return False

        final_segs = self._finalize_cut_segments_list(segments + [new_seg], sw, sh, pieces)
        self.layout_dict['cut_segments'] = final_segs
        self.layout_dict['cut_rows'] = self.layout_dict.get('cut_rows') or [[i for i in range(len(pieces))]]
        business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, pieces, self.min_h, self.min_w)
        for s in final_segs:
            business_rects, waste_rects = self._split_rects_by_segment(business_rects + waste_rects, s)
        self.layout_dict['business_rects'] = business_rects
        self.layout_dict['waste_rects'] = waste_rects
        self.update()
        return True

    def _flip_cut(self, pos, direction):
        """Переключить направление реза по (pos, direction). Ищет сегмент и вызывает _flip_segment. Для совместимости с мультивыбором."""
        self._ensure_cut_lines()
        segments = self.layout_dict.get('cut_segments') or []
        try:
            pos_f = float(pos)
        except (TypeError, ValueError):
            pos_f = 0.0
        dir_u = str(direction).strip().upper()
        ref = {'type': dir_u, 'pos': pos_f}
        for seg in segments:
            if self._segment_type_norm(seg) != dir_u:
                continue
            if not self._same_guillotine_axis(seg, ref):
                continue
            return self._flip_segment(seg)
        return False

    def _cut_line_at_px(self, px, py):
        """По пикселям клика/курсора вернуть сегмент (dict) или None. Зона попадания в пикселях — стабильно при любом масштабе."""
        if not self.layout_dict or (not self.cut_direction_mode and not getattr(self, 'delete_cut_mode', False)):
            return None
        self._ensure_cut_lines()
        if self.sync_cut_segments_from_piece_edges_if_needed():
            self.layout_changed.emit()
        scale = self._scale()
        if scale <= 0:
            return None
        ox, oy = self._origin()
        sw = float(self.layout_dict.get('sheet_width') or 0)
        sh = float(self.layout_dict.get('sheet_height') or 0)
        # Допуск в пикселях: линия считается «под курсором», если расстояние до неё не больше PIXEL_TOL
        PIXEL_TOL = 30 if getattr(self, '_dark_constructor_canvas', False) else 18
        EDGE_PX = 10
        segs = list(self.layout_dict.get('cut_segments') or [])
        h_groups = [[segs[i] for i in batch] for batch in self._ui_cluster_groups_indices_typed(segs, 'H')]
        v_groups = [[segs[i] for i in batch] for batch in self._ui_cluster_groups_indices_typed(segs, 'V')]
        candidates = []
        for group in h_groups:
            if not group:
                continue
            best_dy = None
            for seg in group:
                y_mm = self._segment_pos_float(seg)
                py_line = oy + y_mm * scale
                dy = abs(py - py_line)
                if best_dy is None or dy < best_dy:
                    best_dy = dy
            if best_dy is None or best_dy > PIXEL_TOL:
                continue
            px_sheet_lo = ox
            px_sheet_hi = ox + sw * scale
            if (px_sheet_lo - EDGE_PX) <= px <= (px_sheet_hi + EDGE_PX):
                candidates.append((best_dy, 1, group[0]))
        for group in v_groups:
            if not group:
                continue
            best_dx = None
            for seg in group:
                x_mm = self._segment_pos_float(seg)
                px_line = ox + x_mm * scale
                dx = abs(px - px_line)
                if best_dx is None or dx < best_dx:
                    best_dx = dx
            if best_dx is None or best_dx > PIXEL_TOL:
                continue
            py_sheet_lo = oy
            py_sheet_hi = oy + sh * scale
            if (py_sheet_lo - EDGE_PX) <= py <= (py_sheet_hi + EDGE_PX):
                candidates.append((best_dx, 0, group[0]))
        if not candidates:
            return None
        candidates.sort(key=lambda t: (t[0], t[1]))
        return candidates[0][2]

    def set_cut_direction_mode(self, on):
        self.cut_direction_mode = bool(on)
        if on:
            self._flip_preview_list = None
            self._flip_preview_cache_key = None
            self._delete_preview_list = None
            self._delete_preview_cache_key = None
            self.add_cut_mode = False
            self.delete_cut_mode = False
            self._ensure_cut_lines()
            changed = bool(self.sync_cut_segments_from_piece_edges_if_needed())
            ld = self.layout_dict
            if ld:
                sw = int(ld.get('sheet_width') or 0)
                sh = int(ld.get('sheet_height') or 0)
                pcs = list(ld.get('pieces') or [])
                if sw > 0 and sh > 0 and pcs:
                    th = int(ld.get('thickness_mm') or 4)
                    before = list(ld.get('cut_segments') or [])
                    assign_chocolate_bar_cut_segments_to_layout(
                        ld, _min_strip_for_thickness(th), min_h=self.min_h, min_w=self.min_w
                    )
                    if list(ld.get('cut_segments') or []) != before:
                        changed = True
            if changed:
                self.layout_changed.emit()
            self._sel_indices = set()
            self.drag_start = None
            self.pending_place = None
            self.setFocus(Qt.OtherFocusReason)
        self._sel_cuts = set()
        self._hover_cut_segment = None
        self._flip_preview_list = None
        self._flip_preview_cache_key = None
        self._delete_preview_list = None
        self._delete_preview_cache_key = None
        self._press_cut_key = None
        if not on:
            self.unsetCursor()
        self.update()

    def set_add_cut_mode(self, on, cut_type=None):
        if cut_type is not None:
            self.add_cut_type = 'H' if cut_type.upper() == 'H' else 'V'
        self.add_cut_mode = bool(on)
        if on:
            self.cut_direction_mode = False
            self.delete_cut_mode = False
            self._flip_preview_list = None
            self._flip_preview_cache_key = None
            self._delete_preview_list = None
            self._delete_preview_cache_key = None
            self._sel_indices = set()
            self.drag_start = None
            self.pending_place = None
            self.setFocus(Qt.OtherFocusReason)
        if not on:
            self.unsetCursor()
        self.update()

    def set_delete_cut_mode(self, on):
        self.delete_cut_mode = bool(on)
        if on:
            self._flip_preview_list = None
            self._flip_preview_cache_key = None
            self._delete_preview_list = None
            self._delete_preview_cache_key = None
            self.cut_direction_mode = False
            self.add_cut_mode = False
            self._ensure_cut_lines()
            changed = bool(self.sync_cut_segments_from_piece_edges_if_needed())
            ld = self.layout_dict
            if ld:
                sw = int(ld.get('sheet_width') or 0)
                sh = int(ld.get('sheet_height') or 0)
                pcs = list(ld.get('pieces') or [])
                if sw > 0 and sh > 0 and pcs:
                    th = int(ld.get('thickness_mm') or 4)
                    before = list(ld.get('cut_segments') or [])
                    assign_chocolate_bar_cut_segments_to_layout(
                        ld, _min_strip_for_thickness(th), min_h=self.min_h, min_w=self.min_w
                    )
                    if list(ld.get('cut_segments') or []) != before:
                        changed = True
            if changed:
                self.layout_changed.emit()
            self._sel_indices = set()
            self.drag_start = None
            self.pending_place = None
            self.setFocus(Qt.OtherFocusReason)
        self._hover_cut_segment = None
        if not on:
            self.unsetCursor()
        self.update()

    def _scale(self):
        if not self.layout_dict:
            return 1.0
        sw = self.layout_dict.get('sheet_width') or 1
        sh = self.layout_dict.get('sheet_height') or 1
        staging = int(self.layout_dict.get('staging_left_mm') or 0)
        total_w = sw + staging
        w = self.width() - 2 * MARGIN
        h = self.height() - 2 * MARGIN
        if total_w <= 0 or sh <= 0:
            return 1.0
        base = max(0.1, min(w / total_w, h / sh))
        return base * self.zoom_factor
    def set_zoom_factor(self, z):
        self.zoom_factor = max(0.5, min(4.0, float(z)))
        self.update()

    def _origin(self):
        if not self.layout_dict:
            return MARGIN + self.pan_x, MARGIN + self.pan_y
        staging = int(self.layout_dict.get('staging_left_mm') or 0)
        scale = self._scale()
        ox = MARGIN + self.pan_x + staging * scale
        oy = MARGIN + self.pan_y
        return ox, oy

    def _sheet_to_px(self, x, y):
        ox, oy = self._origin()
        scale = self._scale()
        return ox + x * scale, oy + y * scale

    def _px_to_sheet(self, px, py):
        ox, oy = self._origin()
        scale = self._scale()
        if scale <= 0:
            return 0, 0
        return (px - ox) / scale, (py - oy) / scale

    def leaveEvent(self, event):
        if self._hover_cut_segment is not None:
            self._hover_cut_segment = None
            self.unsetCursor()
            self.update()
        if self._flip_preview_list is not None or self._delete_preview_list is not None:
            self._flip_preview_list = None
            self._flip_preview_cache_key = None
            self._delete_preview_list = None
            self._delete_preview_cache_key = None
            self.update()
        if getattr(self, '_add_cut_hover_sheet', None) is not None:
            self._add_cut_hover_sheet = None
            self.update()
        super().leaveEvent(event)

    def _piece_at(self, sx, sy):
        """Индекс изделия в точке (sx, sy) в координатах листа, или None."""
        pieces = (self.layout_dict or {}).get('pieces') or []
        for i, p in enumerate(pieces):
            x, y, w, h = p['x'], p['y'], p['w'], p['h']
            if x <= sx <= x + w and y <= sy <= y + h:
                return i
        return None

    def _snap_targets(self, piece_index):
        """Списки x и y позиций: прижатие к краю листа или к началу/концу блока (другого изделия)."""
        pieces = (self.layout_dict or {}).get('pieces') or []
        sw = self.layout_dict.get('sheet_width') or 0
        sh = self.layout_dict.get('sheet_height') or 0
        p = pieces[piece_index]
        w, h = p['w'], p['h']
        x_targets = [0, sw - w]
        y_targets = [0, sh - h]
        others = [(j, q) for j, q in enumerate(pieces) if j != piece_index]
        for j, q in others:
            x_targets.append(q['x'] + q['w'])   # наше лево = право другого (конец блока)
            x_targets.append(q['x'] - w)       # наше право = лево другого (начало блока)
            y_targets.append(q['y'] + q['h'])
            y_targets.append(q['y'] - h)
        # Явно: конец ряда (правая граница правого изделия) и начало ряда (левая граница левого)
        if others:
            rightmost = max(q['x'] + q['w'] for _, q in others)
            leftmost = min(q['x'] for _, q in others)
            x_targets.append(rightmost)      # поставить в конец блока
            x_targets.append(leftmost - w)   # поставить в начало блока
            bottommost = max(q['y'] + q['h'] for _, q in others)
            topmost = min(q['y'] for _, q in others)
            y_targets.append(bottommost)
            y_targets.append(topmost - h)
        for seg in (self.layout_dict or {}).get('cut_segments') or []:
            if seg.get('type') == 'V':
                pos = float(seg.get('pos', 0))
                x_targets.append(pos)
                x_targets.append(pos - w)
            elif seg.get('type') == 'H':
                pos = float(seg.get('pos', 0))
                y_targets.append(pos)
                y_targets.append(pos - h)
        return x_targets, y_targets, (0, sw - w), (0, sh - h)

    def _snap_position(self, piece_index, nx, ny, snap_thresh_mm=None):
        """Привязка (nx, ny) к краям листа и других изделий. snap_thresh_mm — порог в мм (по умолчанию SNAP_MM)."""
        snap_thresh = snap_thresh_mm if snap_thresh_mm is not None else SNAP_MM
        x_targets, y_targets, (x_lo, x_hi), (y_lo, y_hi) = self._snap_targets(piece_index)

        def closest(val, targets, lo, hi):
            best_val = val
            best_d = snap_thresh + 1
            for t in targets:
                if lo <= t <= hi and abs(val - t) < best_d:
                    best_d = abs(val - t)
                    best_val = t
            return best_val

        best_x = closest(nx, x_targets, x_lo, x_hi)
        best_y = closest(ny, y_targets, y_lo, y_hi)
        return best_x, best_y

    def _touch_count(self, piece_index, x, y):
        """Сколько сторон детали прижаты к краю листа или другому изделию (0–4). Угол = 2."""
        pieces = (self.layout_dict or {}).get('pieces') or []
        p = pieces[piece_index]
        w, h = p['w'], p['h']
        sw = self.layout_dict.get('sheet_width') or 0
        sh = self.layout_dict.get('sheet_height') or 0
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
        for j, q in enumerate(pieces):
            if j == piece_index:
                continue
            qx, qy, qw, qh = q['x'], q['y'], q['w'], q['h']
            if abs(left - (qx + qw)) <= TOUCH_TOLERANCE_MM:
                count += 1
            if abs(right - qx) <= TOUCH_TOLERANCE_MM:
                count += 1
            if abs(top - (qy + qh)) <= TOUCH_TOLERANCE_MM:
                count += 1
            if abs(bottom - qy) <= TOUCH_TOLERANCE_MM:
                count += 1
        for seg in (self.layout_dict or {}).get('cut_segments') or []:
            if seg.get('type') == 'V':
                pos = float(seg.get('pos', 0))
                elo = float(seg.get('extent_lo', 0))
                ehi = float(seg.get('extent_hi', 0))
                if max(top, elo) < min(bottom, ehi):
                    if abs(left - pos) <= TOUCH_TOLERANCE_MM:
                        count += 1
                    if abs(right - pos) <= TOUCH_TOLERANCE_MM:
                        count += 1
            elif seg.get('type') == 'H':
                pos = float(seg.get('pos', 0))
                elo = float(seg.get('extent_lo', 0))
                ehi = float(seg.get('extent_hi', 0))
                if max(left, elo) < min(right, ehi):
                    if abs(top - pos) <= TOUCH_TOLERANCE_MM:
                        count += 1
                    if abs(bottom - pos) <= TOUCH_TOLERANCE_MM:
                        count += 1
        return count

    def _snap_on_release(self, piece_index, nx, ny):
        """При отпускании: только позиции с касанием (лист или другое изделие). Не оставлять изделие «в воздухе»."""
        x_targets, y_targets, (x_lo, x_hi), (y_lo, y_hi) = self._snap_targets(piece_index)
        pieces = (self.layout_dict or {}).get('pieces') or []
        p = pieces[piece_index]
        w, h = p['w'], p['h']
        sw = self.layout_dict.get('sheet_width') or 0
        sh = self.layout_dict.get('sheet_height') or 0

        def dist_sq(ax, ay, bx, by):
            return (ax - bx) ** 2 + (ay - by) ** 2

        staging = int(self.layout_dict.get('staging_left_mm') or 0)
        segs = (self.layout_dict or {}).get('cut_segments') or []

        def on_sheet(x, y):
            return 0 <= x and 0 <= y and x + w <= sw and y + h <= sh

        def valid(x, y):
            r = (x, y, w, h)
            for seg in segs:
                if _rect_crosses_cut_segment(x, y, w, h, seg):
                    return False
            if on_sheet(x, y):
                for j, q in enumerate(pieces):
                    if j == piece_index:
                        continue
                    if _rects_overlap(r, _piece_rect(q)):
                        return False
                return True
            if staging > 0:
                for j, q in enumerate(pieces):
                    if j == piece_index:
                        continue
                    if on_sheet(q['x'], q['y']) and _rects_overlap(r, _piece_rect(q)):
                        return False
                return True
            return False

        if staging > 0 and not on_sheet(nx, ny) and valid(nx, ny):
            return nx, ny

        candidates_with_touch = []
        for tx in x_targets:
            if tx < x_lo or tx > x_hi:
                continue
            for ty in y_targets:
                if ty < y_lo or ty > y_hi:
                    continue
                if valid(tx, ty):
                    touches = self._touch_count(piece_index, tx, ty)
                    d = dist_sq(nx, ny, tx, ty)
                    if touches >= 1:
                        candidates_with_touch.append((touches, d, tx, ty))
        if candidates_with_touch:
            candidates_with_touch.sort(key=lambda c: (-c[0], c[1]))
            return candidates_with_touch[0][2], candidates_with_touch[0][3]
        best_x, best_y = self._snap_position(piece_index, nx, ny, snap_thresh_mm=999)
        if valid(best_x, best_y):
            return best_x, best_y
        best_x, best_y = self._snap_position(piece_index, nx, ny, snap_thresh_mm=sw + sh)
        if valid(best_x, best_y):
            return best_x, best_y
        return nx, ny

    def _valid_placements(self, piece_index):
        """Все допустимые позиции для изделия с хотя бы одним касанием (лист или другое изделие). Возврат: [(x, y, touches), ...]."""
        x_targets, y_targets, (x_lo, x_hi), (y_lo, y_hi) = self._snap_targets(piece_index)
        pieces = (self.layout_dict or {}).get('pieces') or []
        p = pieces[piece_index]
        w, h = p['w'], p['h']
        sw = self.layout_dict.get('sheet_width') or 0
        sh = self.layout_dict.get('sheet_height') or 0

        segs = (self.layout_dict or {}).get('cut_segments') or []

        def valid(x, y):
            if x < 0 or y < 0 or x + w > sw or y + h > sh:
                return False
            for seg in segs:
                if _rect_crosses_cut_segment(x, y, w, h, seg):
                    return False
            r = (x, y, w, h)
            for j, q in enumerate(pieces):
                if j == piece_index:
                    continue
                if _rects_overlap(r, _piece_rect(q)):
                    return False
            return True

        out = []
        for tx in x_targets:
            if tx < x_lo or tx > x_hi:
                continue
            for ty in y_targets:
                if ty < y_lo or ty > y_hi:
                    continue
                if valid(tx, ty):
                    touches = self._touch_count(piece_index, tx, ty)
                    if touches >= 1:
                        out.append((tx, ty, touches))
        return out

    def _placement_at(self, sx, sy, piece_index):
        """Если (sx, sy) попадает в допустимые позиции, вернуть лучшую (x, y): больше касаний, ближе к (sx,sy)."""
        pieces = (self.layout_dict or {}).get('pieces') or []
        if piece_index >= len(pieces):
            return None
        p = pieces[piece_index]
        cur_x, cur_y = p['x'], p['y']
        w, h = p['w'], p['h']
        candidates = []
        for px, py, touches in self._valid_placements(piece_index):
            if (px, py) == (cur_x, cur_y):
                continue
            if px <= sx <= px + w and py <= sy <= py + h:
                cx, cy = px + w / 2, py + h / 2
                dist_sq = (sx - cx) ** 2 + (sy - cy) ** 2
                candidates.append((touches, dist_sq, px, py))
        if not candidates:
            return None
        candidates.sort(key=lambda c: (-c[0], c[1]))  # больше касаний, меньше расстояние
        return (candidates[0][2], candidates[0][3])

    def _group_bbox(self, indices):
        """Габарит выбранной группы: (min_x, min_y, box_w, box_h)."""
        pieces = (self.layout_dict or {}).get('pieces') or []
        if not indices:
            return None
        xs = [pieces[i]['x'] for i in indices if i < len(pieces)]
        ys = [pieces[i]['y'] for i in indices if i < len(pieces)]
        x2 = [pieces[i]['x'] + pieces[i]['w'] for i in indices if i < len(pieces)]
        y2 = [pieces[i]['y'] + pieces[i]['h'] for i in indices if i < len(pieces)]
        if not xs:
            return None
        min_x, min_y = min(xs), min(ys)
        max_x2, max_y2 = max(x2), max(y2)
        return (min_x, min_y, max_x2 - min_x, max_y2 - min_y)

    def _valid_placements_group(self, indices):
        """Допустимые позиции для группы: (gx, gy) — левый верх габарита; список (gx, gy, touches)."""
        pieces = (self.layout_dict or {}).get('pieces') or []
        sw = self.layout_dict.get('sheet_width') or 0
        sh = self.layout_dict.get('sheet_height') or 0
        box = self._group_bbox(indices)
        if not box:
            return []
        min_x, min_y, box_w, box_h = box
        others = [j for j in range(len(pieces)) if j not in indices]
        x_targets = [0, sw - box_w]
        y_targets = [0, sh - box_h]
        for j in others:
            q = pieces[j]
            x_targets.append(q['x'] + q['w'])
            x_targets.append(q['x'] - box_w)
            y_targets.append(q['y'] + q['h'])
            y_targets.append(q['y'] - box_h)
        segs = (self.layout_dict or {}).get('cut_segments') or []
        for seg in segs:
            if seg.get('type') == 'V':
                pos = float(seg.get('pos', 0))
                x_targets.append(pos)
                x_targets.append(pos - box_w)
            elif seg.get('type') == 'H':
                pos = float(seg.get('pos', 0))
                y_targets.append(pos)
                y_targets.append(pos - box_h)
        x_lo, x_hi = 0, sw - box_w
        y_lo, y_hi = 0, sh - box_h

        def valid(gx, gy):
            if gx < 0 or gy < 0 or gx + box_w > sw or gy + box_h > sh:
                return False
            dx, dy = gx - min_x, gy - min_y
            for i in indices:
                p = pieces[i]
                px, py, pw, ph = p['x'] + dx, p['y'] + dy, p['w'], p['h']
                for seg in segs:
                    if _rect_crosses_cut_segment(px, py, pw, ph, seg):
                        return False
                r = (px, py, pw, ph)
                for j in others:
                    if _rects_overlap(r, _piece_rect(pieces[j])):
                        return False
            for i in indices:
                for j in indices:
                    if i >= j:
                        continue
                    ri = (pieces[i]['x'] + (gx - min_x), pieces[i]['y'] + (gy - min_y), pieces[i]['w'], pieces[i]['h'])
                    rj = (pieces[j]['x'] + (gx - min_x), pieces[j]['y'] + (gy - min_y), pieces[j]['w'], pieces[j]['h'])
                    if _rects_overlap(ri, rj):
                        return False
            return True

        def touch_count(gx, gy):
            left, right = gx, gx + box_w
            top, bottom = gy, gy + box_h
            c = 0
            if left <= 0:
                c += 1
            if right >= sw:
                c += 1
            if top <= 0:
                c += 1
            if bottom >= sh:
                c += 1
            for j in others:
                q = pieces[j]
                qx, qy, qw, qh = q['x'], q['y'], q['w'], q['h']
                if abs(left - (qx + qw)) <= TOUCH_TOLERANCE_MM:
                    c += 1
                if abs(right - qx) <= TOUCH_TOLERANCE_MM:
                    c += 1
                if abs(top - (qy + qh)) <= TOUCH_TOLERANCE_MM:
                    c += 1
                if abs(bottom - qy) <= TOUCH_TOLERANCE_MM:
                    c += 1
            for seg in segs:
                if seg.get('type') == 'V':
                    pos = float(seg.get('pos', 0))
                    elo = float(seg.get('extent_lo', 0))
                    ehi = float(seg.get('extent_hi', 0))
                    if max(top, elo) < min(bottom, ehi):
                        if abs(left - pos) <= TOUCH_TOLERANCE_MM:
                            c += 1
                        if abs(right - pos) <= TOUCH_TOLERANCE_MM:
                            c += 1
                elif seg.get('type') == 'H':
                    pos = float(seg.get('pos', 0))
                    elo = float(seg.get('extent_lo', 0))
                    ehi = float(seg.get('extent_hi', 0))
                    if max(left, elo) < min(right, ehi):
                        if abs(top - pos) <= TOUCH_TOLERANCE_MM:
                            c += 1
                        if abs(bottom - pos) <= TOUCH_TOLERANCE_MM:
                            c += 1
            return c

        out = []
        for gx in x_targets:
            if gx < x_lo or gx > x_hi:
                continue
            for gy in y_targets:
                if gy < y_lo or gy > y_hi:
                    continue
                if valid(gx, gy):
                    t = touch_count(gx, gy)
                    if t >= 1:
                        out.append((gx, gy, t))
        return out

    def _placement_at_group(self, sx, sy, indices):
        """Если (sx,sy) попадает в зону допустимой позиции группы, вернуть (gx, gy)."""
        box = self._group_bbox(indices)
        if not box:
            return None
        min_x, min_y, box_w, box_h = box
        cur_gx, cur_gy = min_x, min_y
        candidates = []
        for gx, gy, touches in self._valid_placements_group(indices):
            if (gx, gy) == (cur_gx, cur_gy):
                continue
            if gx <= sx <= gx + box_w and gy <= sy <= gy + box_h:
                cx, cy = gx + box_w / 2, gy + box_h / 2
                d = (sx - cx) ** 2 + (sy - cy) ** 2
                candidates.append((touches, d, gx, gy))
        if not candidates:
            return None
        candidates.sort(key=lambda c: (-c[0], c[1]))
        return (candidates[0][2], candidates[0][3])

    def _rotate_btn_rect_px(self, piece_index_or_indices):
        """Прямоугольник кнопки «Повернуть» в пикселях (правый верхний угол изделия или группы)."""
        pieces = (self.layout_dict or {}).get('pieces') or []
        if isinstance(piece_index_or_indices, (set, frozenset)):
            indices = piece_index_or_indices
            box = self._group_bbox(indices)
            if not box:
                return None
            min_x, min_y, box_w, box_h = box
            ox, oy = self._origin()
            scale = self._scale()
            x = ox + min_x * scale
            y = oy + min_y * scale
            w = box_w * scale
            h = box_h * scale
        else:
            piece_index = piece_index_or_indices
            if piece_index >= len(pieces):
                return None
            p = pieces[piece_index]
            ox, oy = self._origin()
            scale = self._scale()
            x = ox + p['x'] * scale
            y = oy + p['y'] * scale
            w = p['w'] * scale
            h = p['h'] * scale
        size = min(28, w * 0.4, h * 0.4)
        if size < 12:
            size = 12
        return (x + w - size, y, size, size)

    def flip_selected_cuts(self):
        """Перевернуть направление всех выбранных линий реза. Возвращает True если хотя бы одна перевернута."""
        if not self._sel_cuts or not self.layout_dict:
            return False
        self.about_to_modify.emit()
        self._ensure_cut_lines()
        if self.sync_cut_segments_from_piece_edges_if_needed():
            self.layout_changed.emit()
        ok = False
        segments = list(self.layout_dict.get('cut_segments') or [])
        representatives = []
        axes_done = set()
        for seg in segments:
            if self._segment_key_normalized(seg) not in self._sel_cuts:
                continue
            ci = self._ui_cluster_indices_for_seg(segments, seg)
            ax = frozenset(ci) if ci is not None else (self._segment_type_norm(seg), self._segment_axis_key(seg))
            if ax in axes_done:
                continue
            axes_done.add(ax)
            representatives.append(seg)
        for seg in representatives:
            if self._flip_segment(seg):
                ok = True
        self._sel_cuts = set()
        self.update()
        self.layout_changed.emit()
        return ok

    def mousePressEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.pan_start = (event.x(), event.y(), self.pan_x, self.pan_y)
            self.update()
            return
        if event.button() not in (Qt.LeftButton, Qt.RightButton) or not self.layout_dict:
            super().mousePressEvent(event)
            return

        if self.add_cut_mode:
            event.accept()
            self.setFocus(Qt.MouseFocusReason)
            sx, sy = self._px_to_sheet(event.x(), event.y())
            sw = self.layout_dict.get('sheet_width') or 0
            sh = self.layout_dict.get('sheet_height') or 0
            if 0 <= sx <= sw and 0 <= sy <= sh and event.button() == Qt.LeftButton:
                self.about_to_modify.emit()
                if self._add_cut_at_sheet(sx, sy):
                    self.layout_changed.emit()
            self.update()
            return

        if getattr(self, 'delete_cut_mode', False) and event.button() == Qt.LeftButton:
            event.accept()
            self.setFocus(Qt.MouseFocusReason)
            cut = self._cut_line_at_px(event.x(), event.y())
            if cut is None and self._hover_cut_segment is not None:
                cut = self._hover_cut_segment
            if cut is not None:
                self.about_to_modify.emit()
                if self._delete_segment(cut):
                    self.layout_changed.emit()
            self.update()
            return

        if self.cut_direction_mode:
            event.accept()
            self.setFocus(Qt.MouseFocusReason)
            self.activateWindow()
            cut = self._cut_line_at_px(event.x(), event.y())
            if cut is None and self._hover_cut_segment is not None:
                cut = self._hover_cut_segment
            ctrl = event.modifiers() & Qt.ControlModifier
            if cut is not None:
                if ctrl:
                    segs_all = list(self.layout_dict.get('cut_segments') or [])
                    ci = self._ui_cluster_indices_for_seg(segs_all, cut)
                    if ci is not None:
                        axis_keys = {self._segment_key_normalized(segs_all[i]) for i in ci}
                    else:
                        axis_keys = {
                            self._segment_key_normalized(s)
                            for s in segs_all
                            if self._segments_share_guillotine_axis(s, cut)
                        }
                    if not axis_keys:
                        axis_keys = {self._segment_key_normalized(cut)}
                    if self._sel_cuts & axis_keys:
                        self._sel_cuts -= axis_keys
                    else:
                        self._sel_cuts |= axis_keys
                    self.layout_changed.emit()
                else:
                    self.about_to_modify.emit()
                    sx, sy = self._px_to_sheet(event.x(), event.y())
                    if self._flip_segment(cut, sx, sy):
                        self._sel_cuts = set()
                        self._hover_cut_segment = None
                        self.layout_changed.emit()
            else:
                self._sel_cuts = set()
            self._press_cut_key = None
            self.update()
            return

        sx, sy = self._px_to_sheet(event.x(), event.y())
        pieces = self.layout_dict.get('pieces') or []
        idx = self._piece_at(sx, sy)
        ctrl = event.modifiers() & Qt.ControlModifier

        if idx is not None and idx in self._sel_indices and ctrl:
            # Ctrl+клик по уже выделенному — начать перетаскивание группы (не снимать выделение)
            start_pos = {i: (pieces[i]['x'], pieces[i]['y']) for i in self._sel_indices}
            self.drag_start = (frozenset(self._sel_indices), event.x(), event.y(), start_pos, event.button() == Qt.RightButton)
            self.pending_place = None
            self.grabMouse()
            self.update()
            return

        if idx is not None and ctrl:
            # Ctrl+клик по другому изделию — добавить/убрать из выделения
            self._sel_indices.discard(idx) if idx in self._sel_indices else self._sel_indices.add(idx)
            if not self._sel_indices:
                self._sel_indices = set()
            self.drag_start = None
            self.pending_place = None
            self.update()
            return

        if idx is not None:
            # Клик по изделию без Ctrl: если уже выделен — поворот (кнопка 90°) или снять выделение
            if idx in self._sel_indices:
                r = self._rotate_btn_rect_px(self._sel_indices)
                if r and r[0] <= event.x() <= r[0] + r[2] and r[1] <= event.y() <= r[1] + r[3] and event.button() == Qt.LeftButton:
                    self.rotate_selected()
                    self.drag_start = None
                    self.update()
                    return
                # Без Ctrl — снять выделение
                self._sel_indices = set()
                self.drag_start = None
                self.pending_place = None
                self.update()
                return
            # Выбрать только это изделие и начать перетаскивание
            self._sel_indices = {idx}
            start_pos = {i: (pieces[i]['x'], pieces[i]['y']) for i in self._sel_indices}
            self.drag_start = (frozenset(self._sel_indices), event.x(), event.y(), start_pos, event.button() == Qt.RightButton)
            self.pending_place = None
            self.grabMouse()
        else:
            # Клик по пустому месту: если попали в допустимую зону — запомнить для переноса по отпусканию; иначе снять выделение
            self.drag_start = None
            if len(self._sel_indices) == 1:
                hit = self._placement_at(sx, sy, next(iter(self._sel_indices)))
            elif len(self._sel_indices) > 1:
                hit = self._placement_at_group(sx, sy, self._sel_indices)
            else:
                hit = None
            self.pending_place = hit if hit else None
            if hit is None:
                self._sel_indices = set()
        self.update()

    def mouseMoveEvent(self, event):
        if self.add_cut_mode and self.layout_dict:
            self._add_cut_hover_sheet = self._px_to_sheet(event.x(), event.y())
            self.update()
            return
        self._add_cut_hover_sheet = None
        if (self.cut_direction_mode or getattr(self, 'delete_cut_mode', False)) and self.layout_dict:
            if not self.hasFocus():
                self.setFocus(Qt.MouseFocusReason)
            cur_segs = self.layout_dict.get('cut_segments') or []
            if self._hover_cut_segment is not None and self._hover_cut_segment not in cur_segs:
                self._hover_cut_segment = None
            seg = self._cut_line_at_px(event.x(), event.y())
            if seg != self._hover_cut_segment:
                self._flip_preview_cache_key = None
                self._delete_preview_cache_key = None
            self._hover_cut_segment = seg
            sw = int(self.layout_dict.get('sheet_width') or 0)
            sh = int(self.layout_dict.get('sheet_height') or 0)
            pieces = list(self.layout_dict.get('pieces') or [])
            if self.cut_direction_mode and seg is not None:
                sx, sy = self._px_to_sheet(event.x(), event.y())
                ck = (id(seg), int(sx // 8), int(sy // 8))
                if ck != self._flip_preview_cache_key:
                    self._flip_preview_cache_key = ck
                    self._flip_preview_list = self._compute_flip_new_list(
                        seg, list(cur_segs), pieces, sw, sh, sx, sy
                    )
                self._delete_preview_list = None
                self._delete_preview_cache_key = None
            elif self.cut_direction_mode:
                self._flip_preview_list = None
                self._flip_preview_cache_key = None
            elif getattr(self, 'delete_cut_mode', False) and seg is not None:
                sx, sy = self._px_to_sheet(event.x(), event.y())
                ck = (id(seg), int(sx // 8), int(sy // 8))
                if ck != self._delete_preview_cache_key:
                    self._delete_preview_cache_key = ck
                    self._delete_preview_list = self._compute_delete_new_list(
                        seg, list(cur_segs), pieces, sw, sh
                    )
                self._flip_preview_list = None
                self._flip_preview_cache_key = None
            elif getattr(self, 'delete_cut_mode', False):
                self._delete_preview_list = None
                self._delete_preview_cache_key = None
            self.setCursor(QCursor(Qt.PointingHandCursor) if seg is not None else QCursor(Qt.ArrowCursor))
            self.update()
            return
        if self._hover_cut_segment is not None:
            self._hover_cut_segment = None
            self.unsetCursor()
            self.update()
        if self.pan_start is not None:
            dx = event.x() - self.pan_start[0]
            dy = event.y() - self.pan_start[1]
            self.pan_x = self.pan_start[2] + dx
            self.pan_y = self.pan_start[3] + dy
            self.update()
            return
        if self.drag_start is None or not self.layout_dict:
            super().mouseMoveEvent(event)
            return
        indices, start_px, start_py, start_dict, _ = self.drag_start
        scale = max(0.01, self._scale())
        dx = (event.x() - start_px) / scale
        dy = (event.y() - start_py) / scale
        sw = self.layout_dict.get('sheet_width') or 0
        sh = self.layout_dict.get('sheet_height') or 0
        pieces = self.layout_dict.get('pieces') or []
        for idx in indices:
            if idx >= len(pieces):
                continue
            start_x, start_y = start_dict[idx]
            p = pieces[idx]
            w, h = p['w'], p['h']
            nx = max(0, min(sw - w, start_x + dx))
            ny = max(0, min(sh - h, start_y + dy))
            pieces[idx] = dict(pieces[idx], x=round(nx), y=round(ny))
        hover_sx, hover_sy = self._px_to_sheet(event.x(), event.y())
        if len(indices) == 1:
            self.hover_placement = self._placement_at(hover_sx, hover_sy, next(iter(indices)))
        else:
            self.hover_placement = self._placement_at_group(hover_sx, hover_sy, indices)
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton:
            self.pan_start = None
            self.update()
            super().mouseReleaseEvent(event)
            return
        if event.button() in (Qt.LeftButton, Qt.RightButton):
            self._press_cut_key = None
            if self.drag_start is not None and self.layout_dict:
                indices, _px, _py, start_dict, is_right = self.drag_start
                pieces = self.layout_dict.get('pieces') or []
                if len(indices) == 1:
                    idx = next(iter(indices))
                    if idx < len(pieces):
                        nx, ny = pieces[idx]['x'], pieces[idx]['y']
                        w, h = pieces[idx]['w'], pieces[idx]['h']
                        my_rect = (nx, ny, w, h)
                        swapped = False
                        if is_right:
                            for j, q in enumerate(pieces):
                                if j == idx:
                                    continue
                                if _rects_overlap(my_rect, _piece_rect(q)):
                                    self.about_to_modify.emit()
                                    start_x, start_y = start_dict[idx]
                                    qx, qy = q['x'], q['y']
                                    pieces[idx] = dict(pieces[idx], x=round(qx), y=round(qy))
                                    pieces[j] = dict(pieces[j], x=round(start_x), y=round(start_y))
                                    sx1, sy1 = self._snap_on_release(idx, qx, qy)
                                    pieces[idx] = dict(pieces[idx], x=round(sx1), y=round(sy1))
                                    sx2, sy2 = self._snap_on_release(j, start_x, start_y)
                                    pieces[j] = dict(pieces[j], x=round(sx2), y=round(sy2))
                                    swapped = True
                                    break
                        if not swapped:
                            release_sx, release_sy = self._px_to_sheet(event.x(), event.y())
                            drop_place = self._placement_at(release_sx, release_sy, idx)
                            if drop_place is not None:
                                self.about_to_modify.emit()
                                px, py = drop_place
                                pieces[idx] = dict(pieces[idx], x=round(px), y=round(py))
                            else:
                                self.about_to_modify.emit()
                                sx, sy = self._snap_on_release(idx, nx, ny)
                                pieces[idx] = dict(pieces[idx], x=round(sx), y=round(sy))
                else:
                    # Группа: при отпускании — по зоне или как есть
                    release_sx, release_sy = self._px_to_sheet(event.x(), event.y())
                    drop_place = self._placement_at_group(release_sx, release_sy, indices)
                    if drop_place is not None:
                        self.about_to_modify.emit()
                        gx, gy = drop_place
                        box = self._group_bbox(indices)
                        if box:
                            min_x, min_y, _, _ = box
                            dx, dy = gx - min_x, gy - min_y
                            for idx in indices:
                                if idx < len(pieces):
                                    p = pieces[idx]
                                    pieces[idx] = dict(p, x=round(p['x'] + dx), y=round(p['y'] + dy))
                self.drag_start = None
                self.hover_placement = None
                self._refresh_free_rects_and_cut_scheme_after_move()
                self.layout_changed.emit()
                self.update()
            else:
                self.drag_start = None
                self.hover_placement = None
                if self.pending_place is not None and self._sel_indices and self.layout_dict:
                    pieces = self.layout_dict.get('pieces') or []
                    if len(self._sel_indices) == 1:
                        idx = next(iter(self._sel_indices))
                        if idx < len(pieces):
                            self.about_to_modify.emit()
                            px, py = self.pending_place
                            sx, sy = self._snap_on_release(idx, px, py)
                            pieces[idx] = dict(pieces[idx], x=round(sx), y=round(sy))
                    else:
                        self.about_to_modify.emit()
                        gx, gy = self.pending_place
                        box = self._group_bbox(self._sel_indices)
                        if box:
                            min_x, min_y, _, _ = box
                            dx, dy = gx - min_x, gy - min_y
                            for idx in self._sel_indices:
                                if idx < len(pieces):
                                    p = pieces[idx]
                                    pieces[idx] = dict(p, x=round(p['x'] + dx), y=round(p['y'] + dy))
                    self.pending_place = None
                    self._refresh_free_rects_and_cut_scheme_after_move()
                    self.layout_changed.emit()
                    self.update()
            if self.mouseGrabber() == self:
                self.releaseMouse()
        super().mouseReleaseEvent(event)

    def rotate_selected(self):
        if not self._sel_indices or not self.layout_dict:
            return
        self.about_to_modify.emit()
        pieces = self.layout_dict.get('pieces') or []
        sw = self.layout_dict['sheet_width']
        sh = self.layout_dict['sheet_height']
        if len(self._sel_indices) == 1:
            idx = next(iter(self._sel_indices))
            if idx >= len(pieces):
                return
            p = pieces[idx]
            w, h = p['w'], p['h']
            x, y = p['x'], p['y']
            new_w, new_h = h, w
            nx, ny = max(0, min(sw - new_w, x)), max(0, min(sh - new_h, y))
            p2 = dict(p, w=new_w, h=new_h, x=nx, y=ny, rotated=not p.get('rotated', False))
            pieces[idx] = p2
            nx, ny = self._snap_on_release(idx, nx, ny)
            pieces[idx] = dict(pieces[idx], x=round(nx), y=round(ny))
        else:
            # Группа: повернуть как одно тело (линия/блок поворачивается целиком на 90°)
            box = self._group_bbox(self._sel_indices)
            if not box:
                return
            min_x, min_y, box_w, box_h = box
            cx = min_x + box_w / 2
            cy = min_y + box_h / 2
            new_pieces = []
            for idx in self._sel_indices:
                if idx >= len(pieces):
                    continue
                p = pieces[idx]
                x, y, w, h = p['x'], p['y'], p['w'], p['h']
                px = x + w / 2
                py = y + h / 2
                rx, ry = px - cx, py - cy
                # Поворот 90° по часовой: (rx, ry) -> (ry, -rx)
                new_rx, new_ry = ry, -rx
                new_cx = cx + new_rx
                new_cy = cy + new_ry
                new_w, new_h = h, w
                nx = new_cx - new_w / 2
                ny = new_cy - new_h / 2
                new_pieces.append((idx, nx, ny, new_w, new_h, p))
            # Сдвинуть группу целиком, чтобы вся помещалась на лист
            nmin_x = min(t[1] for t in new_pieces)
            nmin_y = min(t[2] for t in new_pieces)
            nmax_x = max(t[1] + t[3] for t in new_pieces)
            nmax_y = max(t[2] + t[4] for t in new_pieces)
            shift_x = 0
            if nmin_x < 0:
                shift_x = -nmin_x
            elif nmax_x > sw:
                shift_x = sw - nmax_x
            shift_y = 0
            if nmin_y < 0:
                shift_y = -nmin_y
            elif nmax_y > sh:
                shift_y = sh - nmax_y
            for idx, nx, ny, new_w, new_h, p in new_pieces:
                pieces[idx] = dict(p, w=new_w, h=new_h, x=round(nx + shift_x), y=round(ny + shift_y), rotated=not p.get('rotated', False))
        self._refresh_free_rects_and_cut_scheme_after_move()
        self.layout_changed.emit()
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.layout_dict:
            return
        dark = getattr(self, '_dark_constructor_canvas', False)
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        scale = self._scale()
        ox, oy = self._origin()
        sw = self.layout_dict['sheet_width']
        sh = self.layout_dict['sheet_height']
        staging = int(self.layout_dict.get('staging_left_mm') or 0)
        if staging > 0:
            staging_px = staging * scale
            qp.setPen(QPen(QColor(180, 150, 0), 2))
            qp.setBrush(QColor(255, 249, 196) if not dark else QColor(80, 70, 40))
            qp.drawRect(int(ox - staging_px), int(oy), int(staging_px), int(sh * scale))
            qp.setPen(QPen(QColor(140, 110, 0), 2))
            qp.drawLine(int(ox), int(oy), int(ox), int(oy + sh * scale))
            qp.setFont(QFont("Arial", 9))
            qp.setPen(QColor(120, 90, 0) if not dark else QColor(220, 200, 140))
            qp.drawText(int(ox - staging_px), int(oy - 4), "Не для этого листа (перетащите сюда)")
        # Лист
        if dark:
            qp.setPen(QPen(QColor(160, 170, 185), 2))
            qp.setBrush(QColor(28, 34, 44))
        else:
            qp.setPen(QPen(QColor(0, 0, 0), 2))
            qp.setBrush(QColor(232, 238, 244))
        qp.drawRect(int(ox), int(oy), int(sw * scale), int(sh * scale))
        qp.setFont(QFont("Arial", 10))
        qp.setPen(QColor(0, 0, 0) if not dark else QColor(210, 215, 225))
        qp.drawText(int(ox), int(oy - 4), "Лист %d×%d мм" % (sw, sh))
        pieces = self.layout_dict.get('pieces') or []

        # Деловые остатки и мусор: всегда по текущим изделиям и резам (чтобы не было белых дыр после перемещения)
        business_rects, waste_rects = self._get_free_rects_for_draw()
        for r in waste_rects:
            rx = ox + r['x'] * scale
            ry = oy + r['y'] * scale
            rw, rh = r['w'] * scale, r['h'] * scale
            qp.setBrush(QColor(220, 53, 69))
            qp.setPen(QPen(QColor(180, 40, 50) if not dark else QColor(255, 120, 130), 1))
            qp.drawRect(int(rx), int(ry), int(rw), int(rh))
        for r in business_rects:
            rx = ox + r['x'] * scale
            ry = oy + r['y'] * scale
            rw, rh = r['w'] * scale, r['h'] * scale
            qp.setBrush(QColor(72, 187, 120))
            qp.setPen(QPen(QColor(0, 100, 0) if not dark else QColor(140, 230, 170), 1))
            qp.drawRect(int(rx), int(ry), int(rw), int(rh))
            qp.setPen(QColor(0, 0, 0) if not dark else QColor(230, 245, 235))
            qp.setFont(QFont("Arial", max(8, min(12, int(rw / 8)))))
            lbl = "%d×%d" % (r['w'], r['h'])
            if rw > 20 and rh > 14:
                qp.drawText(int(rx + 2), int(ry + rh / 2 + 4), lbl)
        # Сетка допустимых позиций для выбранного (одно изделие или группа)
        if self._sel_indices and self._sel_indices <= set(range(len(pieces))):
            if len(self._sel_indices) == 1:
                idx = next(iter(self._sel_indices))
                p = pieces[idx]
                cur_x, cur_y = p['x'], p['y']
                placements = self._valid_placements(idx)
                rw, rh = p['w'] * scale, p['h'] * scale
            else:
                box = self._group_bbox(self._sel_indices)
                if box:
                    min_x, min_y, box_w, box_h = box
                    cur_x, cur_y = min_x, min_y
                    placements = self._valid_placements_group(self._sel_indices)
                    rw, rh = box_w * scale, box_h * scale
                else:
                    placements = []
                    cur_x = cur_y = rw = rh = 0
            for item in placements:
                if len(item) == 3:
                    px, py, touches = item
                else:
                    continue
                if (px, py) == (cur_x, cur_y):
                    continue
                rx = ox + px * scale
                ry = oy + py * scale
                if touches >= 2:
                    qp.setBrush(QColor(100, 150, 255, 100))
                else:
                    qp.setBrush(QColor(100, 150, 255, 55))
                qp.setPen(QPen(QColor(80, 120, 220, 120), 1))
                qp.drawRect(int(rx), int(ry), int(rw), int(rh))
            if self.hover_placement is not None and self.drag_start is not None and rw > 0 and rh > 0:
                hx, hy = self.hover_placement
                rx = ox + hx * scale
                ry = oy + hy * scale
                qp.setBrush(QColor(80, 200, 100, 130))
                qp.setPen(QPen(QColor(40, 160, 60), 2))
                qp.drawRect(int(rx), int(ry), int(rw), int(rh))
        # Изделия; мелкие (хотя бы одна сторона < порога) — только *1, *2…; расшифровка справа
        valid = _layout_valid(sw, sh, pieces, self.layout_dict.get('cut_segments'))
        thresh = _small_piece_threshold_mm()
        small_pieces_list = []
        piece_to_star = {}
        for i, p in enumerate(pieces):
            w_p, h_p = (p.get('w', 0) or 0), (p.get('h', 0) or 0)
            if w_p < thresh or h_p < thresh:
                star_num = len(small_pieces_list) + 1
                small_pieces_list.append((i, p))
                piece_to_star[i] = star_num
        for i, p in enumerate(pieces):
            x = ox + p['x'] * scale
            y = oy + p['y'] * scale
            w = p['w'] * scale
            h = p['h'] * scale
            if i in self._sel_indices:
                qp.setPen(QPen(QColor(200, 100, 0), 3))
                qp.setBrush(QColor(230, 140, 40))
            else:
                qp.setPen(QPen(QColor(0, 0, 0) if not dark else QColor(200, 210, 225), 1))
                qp.setBrush(QColor(70, 130, 180))
            qp.drawRect(int(x), int(y), int(w), int(h))
            qp.setPen(QColor(0, 0, 0) if not dark else QColor(235, 240, 250))
            star_num = piece_to_star.get(i)
            if star_num is not None:
                qp.setFont(QFont("Arial", max(8, min(12, int(min(w, h) / 8)))))
                qp.drawText(int(x), int(y), int(w), int(h), Qt.AlignCenter, "*%d" % star_num)
            else:
                qp.drawText(int(x + 4), int(y + 14), "%d×%d" % (p['w'], p['h']))
        if small_pieces_list:
            legend_x = ox + sw * scale + 10
            legend_y0 = oy
            for star_num, (piece_i, p) in enumerate(small_pieces_list, 1):
                ly = legend_y0 + (star_num - 1) * LEGEND_ITEM_H
                qp.setPen(QColor(0, 0, 0) if not dark else QColor(220, 225, 235))
                qp.setFont(QFont("Arial", 10))
                qp.drawText(int(legend_x), int(ly + 14), "*%d" % star_num)
                box_x = legend_x + 24
                box_y = ly
                qp.setBrush(QColor(220, 230, 245) if not dark else QColor(45, 52, 65))
                qp.setPen(QPen(QColor(0, 0, 0) if not dark else QColor(160, 170, 185), 1))
                qp.drawRect(int(box_x), int(box_y), LEGEND_BOX_W, LEGEND_BOX_H)
                qp.setPen(QColor(0, 0, 0) if not dark else QColor(220, 225, 235))
                qp.setFont(QFont("Arial", 8))
                w_mm, h_mm = p['w'], p['h']
                qp.save()
                qp.translate(box_x + 10, box_y + LEGEND_BOX_H / 2)
                qp.rotate(-90)
                qp.drawText(int(-8), int(4), str(h_mm))
                qp.restore()
                qp.drawText(int(box_x + LEGEND_BOX_W / 2 - 8), int(box_y + LEGEND_BOX_H - 4), str(w_mm))
                rec = (p.get('recipient') or '').strip()[:14]
                qp.drawText(int(box_x + 2), int(box_y + 12), rec if rec else "—")
        if self._sel_indices:
            r = self._rotate_btn_rect_px(self._sel_indices)
            if r:
                qp.setPen(QPen(QColor(180, 90, 0), 1))
                qp.setBrush(QColor(255, 200, 120))
                qp.drawRect(int(r[0]), int(r[1]), int(r[2]), int(r[3]))
                qp.setPen(QColor(120, 60, 0))
                qp.setFont(QFont("Arial", max(7, int(r[3] * 0.5))))
                qp.drawText(int(r[0]), int(r[1] + r[3] * 0.7), "90°")
        # Линии реза: на тёмном конструкторе — контрастный жёлтый (зелёный сливался с заливкой остатков).
        # В режиме «Добавить рез» не вызываем _ensure_cut_lines(), чтобы не создавать кучу линий по границам изделий.
        segs_list = list((self.layout_dict or {}).get('cut_segments') or [])
        # В режимах направления/удаления — яркие линии; в обычном просмотре — тоньше и тусклее (не «выделение»).
        cut_pen_edit = (
            QPen(QColor(255, 240, 120), 4) if dark else QPen(QColor(0, 160, 0), 2)
        )
        cut_pen_view = (
            QPen(QColor(120, 130, 150), 2) if dark else QPen(QColor(70, 110, 80), 1)
        )
        constructor_hide_cut_lines = (
            dark
            and getattr(self, '_dark_constructor_canvas', False)
            and not self.add_cut_mode
            and not self.cut_direction_mode
            and not getattr(self, 'delete_cut_mode', False)
        )
        if self.layout_dict and (segs_list or self.add_cut_mode or self.cut_direction_mode or getattr(self, 'delete_cut_mode', False)) and not constructor_hide_cut_lines:
            edit_cuts_visual = self.cut_direction_mode or getattr(self, 'delete_cut_mode', False)
            if edit_cuts_visual and segs_list:
                h_groups = [[segs_list[i] for i in batch] for batch in self._ui_cluster_groups_indices_typed(segs_list, 'H')]
                v_groups = [[segs_list[i] for i in batch] for batch in self._ui_cluster_groups_indices_typed(segs_list, 'V')]

                for group in h_groups:
                    if not group:
                        continue
                    ho = self._segment_group_has_hover(group)
                    se = self.cut_direction_mode and self._segment_group_has_sel(group)
                    if getattr(self, 'delete_cut_mode', False):
                        if ho:
                            qp.setPen(QPen(QColor(255, 100, 0), 6))
                            for seg in group:
                                py = oy + self._segment_pos_float(seg) * scale
                                x1 = ox + float(seg.get('extent_lo', 0)) * scale
                                x2 = ox + float(seg.get('extent_hi', sw)) * scale
                                qp.drawLine(int(x1), int(py), int(x2), int(py))
                        else:
                            qp.setPen(QPen(QColor(220, 120, 0), 4))
                            for seg in group:
                                py = oy + self._segment_pos_float(seg) * scale
                                x1 = ox + float(seg.get('extent_lo', 0)) * scale
                                x2 = ox + float(seg.get('extent_hi', sw)) * scale
                                qp.drawLine(int(x1), int(py), int(x2), int(py))
                    else:
                        if ho or se:
                            qp.setPen(QPen(QColor(255, 80, 80), 6) if ho else QPen(QColor(255, 190, 80), 5))
                            for seg in group:
                                py = oy + self._segment_pos_float(seg) * scale
                                x1 = ox + float(seg.get('extent_lo', 0)) * scale
                                x2 = ox + float(seg.get('extent_hi', sw)) * scale
                                qp.drawLine(int(x1), int(py), int(x2), int(py))
                        else:
                            qp.setPen(cut_pen_edit)
                            for seg in group:
                                py = oy + self._segment_pos_float(seg) * scale
                                x1 = ox + float(seg.get('extent_lo', 0)) * scale
                                x2 = ox + float(seg.get('extent_hi', sw)) * scale
                                qp.drawLine(int(x1), int(py), int(x2), int(py))
                for group in v_groups:
                    if not group:
                        continue
                    ho = self._segment_group_has_hover(group)
                    se = self.cut_direction_mode and self._segment_group_has_sel(group)
                    if getattr(self, 'delete_cut_mode', False):
                        if ho:
                            qp.setPen(QPen(QColor(255, 100, 0), 6))
                            for seg in group:
                                px = ox + self._segment_pos_float(seg) * scale
                                y1 = oy + float(seg.get('extent_lo', 0)) * scale
                                y2 = oy + float(seg.get('extent_hi', sh)) * scale
                                qp.drawLine(int(px), int(y1), int(px), int(y2))
                        else:
                            qp.setPen(QPen(QColor(220, 120, 0), 4))
                            for seg in group:
                                px = ox + self._segment_pos_float(seg) * scale
                                y1 = oy + float(seg.get('extent_lo', 0)) * scale
                                y2 = oy + float(seg.get('extent_hi', sh)) * scale
                                qp.drawLine(int(px), int(y1), int(px), int(y2))
                    else:
                        if ho or se:
                            qp.setPen(QPen(QColor(255, 80, 80), 6) if ho else QPen(QColor(255, 190, 80), 5))
                            for seg in group:
                                px = ox + self._segment_pos_float(seg) * scale
                                y1 = oy + float(seg.get('extent_lo', 0)) * scale
                                y2 = oy + float(seg.get('extent_hi', sh)) * scale
                                qp.drawLine(int(px), int(y1), int(px), int(y2))
                        else:
                            qp.setPen(cut_pen_edit)
                            for seg in group:
                                px = ox + self._segment_pos_float(seg) * scale
                                y1 = oy + float(seg.get('extent_lo', 0)) * scale
                                y2 = oy + float(seg.get('extent_hi', sh)) * scale
                                qp.drawLine(int(px), int(y1), int(px), int(y2))
            else:
                for seg in segs_list:
                    qp.setPen(cut_pen_view)
                    if self._segment_type_norm(seg) == 'H':
                        py = oy + seg['pos'] * scale
                        x1 = ox + seg['extent_lo'] * scale
                        x2 = ox + seg['extent_hi'] * scale
                        qp.drawLine(int(x1), int(py), int(x2), int(py))
                    else:
                        px = ox + seg['pos'] * scale
                        y1 = oy + seg['extent_lo'] * scale
                        y2 = oy + seg['extent_hi'] * scale
                        qp.drawLine(int(px), int(y1), int(px), int(y2))
        # Предпросмотр после переворота (пунктир, не участвует в hit-test)
        if self.cut_direction_mode and self._flip_preview_list and self.layout_dict:
            qp.save()
            qp.setOpacity(0.88)
            qp.setPen(QPen(QColor(72, 220, 255), 3, Qt.DashLine))
            sw_pv = float(self.layout_dict.get('sheet_width') or 0)
            sh_pv = float(self.layout_dict.get('sheet_height') or 0)
            for s in self._flip_preview_list:
                if not isinstance(s, dict):
                    continue
                if self._segment_type_norm(s) == 'H':
                    py = oy + float(s.get('pos', 0)) * scale
                    x1 = ox + float(s.get('extent_lo', 0)) * scale
                    x2 = ox + float(s.get('extent_hi', sw_pv)) * scale
                    qp.drawLine(int(x1), int(py), int(x2), int(py))
                elif self._segment_type_norm(s) == 'V':
                    px = ox + float(s.get('pos', 0)) * scale
                    y1 = oy + float(s.get('extent_lo', 0)) * scale
                    y2 = oy + float(s.get('extent_hi', sh_pv)) * scale
                    qp.drawLine(int(px), int(y1), int(px), int(y2))
            qp.restore()
        if getattr(self, 'delete_cut_mode', False) and self._delete_preview_list and self.layout_dict:
            qp.save()
            qp.setOpacity(0.88)
            qp.setPen(QPen(QColor(255, 130, 220), 3, Qt.DashLine))
            sw_d = float(self.layout_dict.get('sheet_width') or 0)
            sh_d = float(self.layout_dict.get('sheet_height') or 0)
            for s in self._delete_preview_list:
                if not isinstance(s, dict):
                    continue
                if self._segment_type_norm(s) == 'H':
                    py = oy + float(s.get('pos', 0)) * scale
                    x1 = ox + float(s.get('extent_lo', 0)) * scale
                    x2 = ox + float(s.get('extent_hi', sw_d)) * scale
                    qp.drawLine(int(x1), int(py), int(x2), int(py))
                elif self._segment_type_norm(s) == 'V':
                    px = ox + float(s.get('pos', 0)) * scale
                    y1 = oy + float(s.get('extent_lo', 0)) * scale
                    y2 = oy + float(s.get('extent_hi', sh_d)) * scale
                    qp.drawLine(int(px), int(y1), int(px), int(y2))
            qp.restore()
        # Режим «Добавить рез»: красная превью — где окажется рез при клике (привязка к краю, обрезка по листу/резам/изделиям)
        if self.add_cut_mode and self.layout_dict and getattr(self, '_add_cut_hover_sheet', None):
            hsx, hsy = self._add_cut_hover_sheet
            preview_seg = self._compute_add_cut_segment_at(hsx, hsy)
            if preview_seg is not None:
                qp.setPen(QPen(QColor(200, 0, 0), 4))
                pos_mm = int(round(float(preview_seg.get('pos') or 0)))
                lbl = "рез -> %d мм" % pos_mm
                qp.setFont(QFont("Arial", 10))
                lbl_pen = QPen(QColor(255, 140, 140) if dark else QColor(200, 0, 0), 1)
                if preview_seg['type'] == 'V':
                    px = ox + preview_seg['pos'] * scale
                    y1 = oy + preview_seg['extent_lo'] * scale
                    y2 = oy + preview_seg['extent_hi'] * scale
                    qp.drawLine(int(px), int(y1), int(px), int(y2))
                    qp.setPen(lbl_pen)
                    qp.drawText(int(px) + 4, int((y1 + y2) / 2), lbl)
                else:
                    py = oy + preview_seg['pos'] * scale
                    x1 = ox + preview_seg['extent_lo'] * scale
                    x2 = ox + preview_seg['extent_hi'] * scale
                    qp.drawLine(int(x1), int(py), int(x2), int(py))
                    qp.setPen(lbl_pen)
                    qp.drawText(int((x1 + x2) / 2), int(py) - 4, lbl)
            else:
                qp.setPen(QPen(QColor(180, 150, 100), 1))
                sw = self.layout_dict.get('sheet_width') or 1
                sh = self.layout_dict.get('sheet_height') or 1
                if self.add_cut_type == 'V':
                    px = ox + hsx * scale
                    qp.drawLine(int(px), int(oy), int(px), int(oy + sh * scale))
                else:
                    py = oy + hsy * scale
                    qp.drawLine(int(ox), int(py), int(ox + sw * scale), int(py))
        # Статус допустимости и подсказка режима резов — фиксированно слева канваса (не следуют за смещением листа)
        status_left = 10
        status_y = self.height() - 8
        row = status_y
        qp.setFont(QFont("Arial", 10))
        if valid:
            qp.setPen(QColor(0, 120, 0) if not dark else QColor(120, 220, 150))
            qp.drawText(status_left, int(row), "Размещение допустимо")
        else:
            qp.setPen(QColor(200, 0, 0) if not dark else QColor(255, 120, 130))
            qp.drawText(status_left, int(row), "Размещение недопустимо (пересечение или выход за лист)")
        row -= 16
        qp.setPen(QColor(0, 0, 0) if not dark else QColor(190, 195, 205))
        if getattr(self, 'delete_cut_mode', False):
            qp.drawText(status_left, int(row), "Удалить рез: клик по оранжевой линии — удалить; блоки пересчитаются.")
            row -= 18
        elif self.cut_direction_mode:
            qp.drawText(status_left, int(row), "Режим направления реза: клик по линии — переворот H↔V; Ctrl+клик — выбор нескольких.")
            row -= 18
        if (self.cut_direction_mode or getattr(self, 'delete_cut_mode', False)) and not segs_list:
            qp.setPen(QColor(255, 210, 100) if dark else QColor(160, 100, 0))
            qp.drawText(
                status_left,
                int(row),
                "Нет линий реза: ПКМ по схеме — добавить рез; затем снова направление или удаление.",
            )
        qp.end()

    def get_stats(self):
        """Занятая площадь (мм²), число деловых остатков, макс. площадь остатка (мм²)."""
        if not self.layout_dict:
            return 0, 0, 0
        pieces = self.layout_dict.get('pieces') or []
        occupied = sum(p.get('w', 0) * p.get('h', 0) for p in pieces)
        business_rects, _ = self._get_free_rects_for_draw()
        max_rem = max((r.get('w', 0) * r.get('h', 0) for r in business_rects), default=0)
        return occupied, len(business_rects), max_rem


class LayoutEditDialog(QDialog):
    """Диалог ручного изменения макета: выбор изделия, поворот, перетаскивание, сохранение."""
    def __init__(self, layout_dict, sheet_index, order_id, material_name, parent=None, persist_to_db=True, session_mode=False):
        super().__init__(parent)
        self.layout_dict = layout_dict
        self.sheet_index = sheet_index
        self.order_id = order_id
        self.material_name = material_name or ''
        self._persist_to_db = bool(persist_to_db) and order_id is not None
        self._session_mode = bool(session_mode)
        self._saved_layout = None
        self._initial_layout = copy.deepcopy(layout_dict)  # для кнопки «Сброс»
        self.setWindowTitle("Изменить макет вручную — лист %d" % (sheet_index + 1))
        self.setMinimumSize(700, 500)
        layout = QVBoxLayout(self)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Масштаб:"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setMinimum(50)
        self.zoom_slider.setMaximum(400)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setTickPosition(QSlider.TicksBelow)
        self.zoom_slider.setTickInterval(50)
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        zoom_row.addWidget(self.zoom_slider, 1)
        self.zoom_label = QLabel("100%")
        zoom_row.addWidget(self.zoom_label)
        layout.addLayout(zoom_row)
        self.canvas = LayoutEditCanvas(self)
        self.canvas.set_layout(layout_dict)
        self.canvas.layout_changed.connect(self._refresh_stats)
        layout.addWidget(self.canvas, 1)
        min_h, min_w = 0, 0
        try:
            th = models.get_threshold_for_material(self.material_name, self.layout_dict.get('thickness_mm', 4))
            if th:
                min_h = th.get('min_height_mm') or 0
                min_w = th.get('min_width_mm') or 0
        except Exception:
            pass
        self.canvas.set_remnant_threshold(min_h, min_w)
        self.stats_label = QLabel("")
        self._refresh_stats()
        layout.addWidget(self.stats_label)
        row = QHBoxLayout()
        self.btn_rotate = QPushButton("Повернуть 90°")
        self.btn_rotate.clicked.connect(self._on_rotate)
        self.btn_delete = QPushButton("Удалить")
        self.btn_delete.setToolTip("Удалить выбранную деталь (сохранить — по кнопке «Сохранить»)")
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_add_detail = QPushButton("Добавить деталь")
        self.btn_add_detail.setToolTip("Добавить изделие на лист (материал и толщина — как у листа)")
        self.btn_add_detail.clicked.connect(self._on_add_detail)
        self.btn_reset = QPushButton("Сброс")
        self.btn_reset.clicked.connect(self._on_reset)
        self.btn_reset.setToolTip("Вернуть макет в исходное состояние")
        self.btn_cut_dir = QPushButton("Изменить направление реза")
        self.btn_cut_dir.setCheckable(True)
        self.btn_cut_dir.setToolTip("Режим линий реза: клик по линии — переворот горизонталь↔вертикаль; Ctrl+клик — выбор нескольких; кнопка «Перевернуть выбранные» — применить.")
        self.btn_cut_dir.toggled.connect(self._on_cut_direction_toggled)
        self.btn_flip_cuts = QPushButton("Перевернуть выбранные резы")
        self.btn_flip_cuts.setToolTip("Перевернуть направление всех выбранных линий реза (Ctrl+клик по линиям).")
        self.btn_flip_cuts.clicked.connect(self._on_flip_selected_cuts)
        self.btn_flip_cuts.setEnabled(False)
        self.btn_delete_cut = QPushButton("Удалить рез")
        self.btn_delete_cut.setCheckable(True)
        self.btn_delete_cut.setToolTip("Режим удаления: резы подсвечиваются оранжевым, клик по линии — удалить, блоки пересчитываются в реальном времени.")
        self.btn_delete_cut.toggled.connect(self._on_delete_cut_toggled)
        self.btn_add_cut = QPushButton("Добавить рез")
        self.btn_add_cut.setCheckable(True)
        self.btn_add_cut.setToolTip("Режим добавления реза: выберите тип (Гор./Верт.), затем клик по листу — линия обрежется до краёв и изделий.")
        self.btn_add_cut.toggled.connect(self._on_add_cut_toggled)
        self.btn_add_cut_h = QPushButton("Гор.")
        self.btn_add_cut_h.setCheckable(True)
        self.btn_add_cut_h.setToolTip("Добавить горизонтальный рез (клик по листу)")
        self.btn_add_cut_h.toggled.connect(lambda on: on and self._on_add_cut_type('H'))
        self.btn_add_cut_v = QPushButton("Верт.")
        self.btn_add_cut_v.setCheckable(True)
        self.btn_add_cut_v.setChecked(True)
        self.btn_add_cut_v.setToolTip("Добавить вертикальный рез (клик по листу)")
        self.btn_add_cut_v.toggled.connect(lambda on: on and self._on_add_cut_type('V'))
        self.btn_save = QPushButton("Сохранить")
        self.btn_save.clicked.connect(self._on_save)
        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_rotate)
        row.addWidget(self.btn_delete)
        row.addWidget(self.btn_add_detail)
        row.addWidget(self.btn_reset)
        row.addWidget(self.btn_cut_dir)
        row.addWidget(self.btn_flip_cuts)
        row.addWidget(self.btn_delete_cut)
        row.addWidget(self.btn_add_cut)
        row.addWidget(self.btn_add_cut_h)
        row.addWidget(self.btn_add_cut_v)
        row.addWidget(QLabel("Клик — выбор; Ctrl+клик — несколько; пусто — снять выделение; средняя кнопка — панорама."))
        row.addStretch()
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_cancel)
        layout.addLayout(row)
        self.setStyleSheet("QDialog { background: #E6F2FF; } QPushButton { background: #4682B4; color: white; padding: 8px 16px; border-radius: 5px; }")

    def _on_zoom_changed(self, value):
        self.canvas.set_zoom_factor(value / 100.0)
        self.zoom_label.setText("%d%%" % value)

    def _refresh_stats(self):
        occ, n_rem, max_rem = self.canvas.get_stats()
        sheet = self.canvas.get_layout() or {}
        sw = sheet.get('sheet_width') or 1
        sh = sheet.get('sheet_height') or 1
        total_mm2 = sw * sh
        occ_m2 = occ / 1e6
        max_rem_m2 = max_rem / 1e6
        self.stats_label.setText(
            "Занято: %.2f м² | Деловых остатков: %d | Макс. остаток: %.2f м²" % (occ_m2, n_rem, max_rem_m2)
        )
        btn = getattr(self, 'btn_flip_cuts', None)
        if btn is not None:
            btn.setEnabled(
                getattr(self.canvas, 'cut_direction_mode', False) and len(getattr(self.canvas, '_sel_cuts', set())) > 0
            )
        btn_del = getattr(self, 'btn_delete_cut', None)
        if btn_del is not None:
            lay = getattr(self.canvas, 'layout_dict', None)
            segs = list(lay.get('cut_segments') or []) if isinstance(lay, dict) else []
            btn_del.setEnabled(len(segs) > 0)
            if btn_del.isCheckable():
                delete_mode = getattr(self.canvas, 'delete_cut_mode', False)
                if btn_del.isChecked() != delete_mode:
                    btn_del.setChecked(delete_mode)

    def _on_cut_direction_toggled(self, checked):
        if checked:
            self.btn_add_cut.setChecked(False)
            self.btn_delete_cut.setChecked(False)
        self.canvas.set_cut_direction_mode(checked)
        self._refresh_stats()

    def _on_add_cut_toggled(self, checked):
        if checked:
            self.btn_cut_dir.setChecked(False)
            self.btn_delete_cut.setChecked(False)
        self.canvas.set_add_cut_mode(checked)
        self._refresh_stats()

    def _on_delete_cut_toggled(self, checked):
        if checked:
            self.btn_cut_dir.setChecked(False)
            self.btn_add_cut.setChecked(False)
        self.canvas.set_delete_cut_mode(checked)
        self._refresh_stats()

    def _on_add_cut_type(self, cut_type):
        self.canvas.set_add_cut_mode(True, cut_type)
        self.btn_add_cut.setChecked(True)
        self.btn_cut_dir.setChecked(False)
        self.btn_add_cut_v.setChecked(cut_type == 'V')
        self.btn_add_cut_h.setChecked(cut_type == 'H')
        self._refresh_stats()

    def _on_flip_selected_cuts(self):
        if self.canvas.flip_selected_cuts():
            self._refresh_stats()

    def _on_delete_cut(self):
        """Удалить выбранный рез (или под курсором); пересчитать области."""
        if not getattr(self.canvas, 'cut_direction_mode', False):
            return
        self.canvas._ensure_cut_lines()
        segs = list(self.canvas.layout_dict.get('cut_segments') or [])
        if not segs:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(self, "Удалить рез", "Нет линий реза для удаления.")
            return
        # Кого удалять: выбранные (Ctrl+клик) или один под курсором
        sel_cuts = list(getattr(self.canvas, '_sel_cuts', set()))
        hover_seg = getattr(self.canvas, '_hover_cut_segment', None)
        if sel_cuts:
            to_delete_keys = list(sel_cuts)
        elif hover_seg is not None:
            to_delete_keys = [self.canvas._segment_key_normalized(hover_seg)]
        else:
            to_delete_keys = []
        if not to_delete_keys:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(self, "Удалить рез", "Наведите курсор на линию реза и нажмите «Удалить рез», или Ctrl+клик по линии и нажмите кнопку.")
            return
        self.canvas.about_to_modify.emit()
        for key in to_delete_keys:
            segs = list(self.canvas.layout_dict.get('cut_segments') or [])
            for s in segs:
                if self.canvas._segment_key_normalized(s) == key:
                    self.canvas._delete_segment(s)
                    break
        self.canvas._sel_cuts = set()
        self.canvas._hover_cut_segment = None
        self.canvas.update()
        self.canvas.layout_changed.emit()
        self._refresh_stats()

    def _on_reset(self):
        """Сброс — вернуть макет в исходное состояние при открытии диалога."""
        self.canvas.set_layout(copy.deepcopy(self._initial_layout))

    def _on_rotate(self):
        self.canvas.rotate_selected()

    def _on_delete(self):
        """Удалить выбранную деталь. В БД сохраняется только по кнопке «Сохранить»."""
        layout = self.canvas.get_layout()
        if not layout:
            return
        pieces = list(layout.get('pieces') or [])
        sel = getattr(self.canvas, '_sel_indices', set())
        if not sel:
            QMessageBox.information(self, "Удаление", "Выберите деталь на схеме (клик по изделию), затем нажмите «Удалить».")
            return
        msg = "Удалить выбранную деталь?" if len(sel) == 1 else "Удалить выбранные детали (%d)?" % len(sel)
        if QMessageBox.question(self, "Удалить", msg, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        new_pieces = [p for i, p in enumerate(pieces) if i not in sel]
        sw = layout.get('sheet_width') or 0
        sh = layout.get('sheet_height') or 0
        min_h, min_w = 0, 0
        try:
            th = models.get_threshold_for_material(self.material_name, layout.get('thickness_mm', 4))
            if th:
                min_h = th.get('min_height_mm') or 0
                min_w = th.get('min_width_mm') or 0
        except Exception:
            pass
        repacked = repack_pieces_on_sheet(sw, sh, new_pieces, min_h, min_w)
        if repacked is not None:
            new_pieces = repacked
        business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, new_pieces, min_h, min_w)
        new_layout = dict(layout, pieces=new_pieces, business_rects=business_rects, waste_rects=waste_rects)
        new_layout.pop('cut_segments', None)
        new_layout.pop('cut_rows', None)
        self.canvas.set_layout(new_layout)
        self._refresh_stats()

    def _on_add_detail(self):
        """Добавить деталь на лист (то же окно, что при создании реза, без выбора материала и толщины)."""
        from ui.add_detail_dialog import AddDetailDialog
        d = AddDetailDialog(self.material_name, self.canvas.get_layout() if self.canvas.get_layout() else None, self)
        if d.exec_() != d.Accepted:
            return
        piece = d.get_piece()
        if not piece:
            return
        layout = self.canvas.get_layout()
        if not layout:
            return
        pieces = list(layout.get('pieces') or [])
        pieces.append(piece)
        sw = layout.get('sheet_width') or 0
        sh = layout.get('sheet_height') or 0
        min_h, min_w = 0, 0
        try:
            th = models.get_threshold_for_material(self.material_name, layout.get('thickness_mm', 4))
            if th:
                min_h = th.get('min_height_mm') or 0
                min_w = th.get('min_width_mm') or 0
        except Exception:
            pass
        repacked = repack_pieces_on_sheet(sw, sh, pieces, min_h, min_w)
        if repacked is not None:
            pieces = repacked
        else:
            QMessageBox.warning(
                self, "Добавление детали",
                "Не все детали поместились на лист после перераскладки. Разместите изделие вручную или уменьшите размеры."
            )
        business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, pieces, min_h, min_w)
        new_layout = dict(layout, pieces=pieces, business_rects=business_rects, waste_rects=waste_rects)
        new_layout.pop('cut_segments', None)
        new_layout.pop('cut_rows', None)
        self.canvas.set_layout(new_layout)
        self._refresh_stats()

    def _on_save(self):
        layout = self.canvas.get_layout()
        if not layout:
            return
        pieces = layout.get('pieces') or []
        sw = layout.get('sheet_width') or 0
        sh = layout.get('sheet_height') or 0
        if not _layout_valid(sw, sh, pieces, layout.get('cut_segments')):
            QMessageBox.warning(self, "Недопустимо", "Исправьте размещение: изделия не должны пересекаться и должны помещаться на лист.")
            return
        # Порог для деловых остатков по материалу
        min_h, min_w = 0, 0
        try:
            th = models.get_threshold_for_material(self.material_name, layout.get('thickness_mm', 4))
            if th:
                min_h = th.get('min_height_mm') or 0
                min_w = th.get('min_width_mm') or 0
        except Exception:
            pass
        # Если есть ручные резы (cut_segments), области уже разбиты в макете — не перезаписывать пересчётом по изделиям
        if layout.get('cut_segments') and (layout.get('business_rects') is not None or layout.get('waste_rects') is not None):
            business_rects = list(layout.get('business_rects') or [])
            waste_rects = list(layout.get('waste_rects') or [])
        else:
            business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, pieces, min_h, min_w)
        # Явно включаем cut_segments и cut_rows, нормализуем числа для JSON (в т.ч. numpy)
        def _to_json_num(v):
            try:
                i = int(v)
                if float(v) == i:
                    return i
            except (TypeError, ValueError):
                pass
            try:
                return float(v)
            except (TypeError, ValueError):
                return v
        def _norm_seg(s):
            if not isinstance(s, dict):
                return {}
            out = {}
            for k, v in s.items():
                if k in ('pos', 'extent_lo', 'extent_hi', 'row_iy', 'row_y_lo', 'row_y_hi'):
                    out[k] = _to_json_num(v)
                else:
                    out[k] = v
            return out
        cut_segments = [_norm_seg(seg) for seg in layout.get('cut_segments') or []]
        cut_rows = layout.get('cut_rows')
        if cut_rows is not None:
            cut_rows = [list(row) for row in cut_rows]
        layout_save = dict(layout, business_rects=business_rects, waste_rects=waste_rects,
                          cut_segments=cut_segments, cut_rows=cut_rows if cut_rows is not None else layout.get('cut_rows'))
        if self._session_mode or not self._persist_to_db:
            for p in layout_save.get('pieces') or []:
                if isinstance(p, dict):
                    p['user_fixed'] = True
            self._saved_layout = layout_save
            self.accept()
            return
        if models.update_cut_result_layout(self.order_id, self.sheet_index, layout_save):
            try:
                models.insert_layout_training_sample(sw, sh, pieces, source='manual_edit')
            except Exception:
                pass
            n_cuts = len(cut_segments)
            msg = "Макет сохранён." + (" Линий реза: %d." % n_cuts if n_cuts else "")
            QMessageBox.information(self, "Сохранено", msg)
            self.accept()
        else:
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить макет.")
