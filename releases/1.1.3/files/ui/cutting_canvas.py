"""Схема раскроя: выносные размерные линии (как в начертательной геометрии), красный — мусор, зелёные — остатки.
   Режим fit_to_view: масштаб подстраивается под размер виджета, лист влезает целиком."""
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics
from collections import defaultdict

# Отступы для подписей и размерных линий (px при любом масштабе)
MARGIN_LEFT = 70
MARGIN_TOP = 28
MARGIN_BOTTOM = 50
MARGIN_RIGHT = 100
GAP_SHEETS = 55


# Масштабируемые размеры (в "мм" на чертеже): выносы — без стрелок; самый длинный вынос у листа, у изделий/остатков короче
GAP_MM = 22
TEXT_OFFSET_MM = 12
TIER_STEP_MM = 20


def _draw_dimension_vertical(qp, x_left, y_bottom, y_top, value, gap_px, scale, text_rotated_90=True):
    """Высота: выносные линии и размерная линия (без стрелок); текст повёрнут на 90° (как на чертеже)."""
    x_dim = x_left - gap_px
    qp.drawLine(int(x_dim), int(y_bottom), int(x_left), int(y_bottom))
    qp.drawLine(int(x_dim), int(y_top), int(x_left), int(y_top))
    qp.drawLine(int(x_dim), int(y_bottom), int(x_dim), int(y_top))
    cy = (y_bottom + y_top) / 2
    if text_rotated_90:
        qp.save()
        qp.translate(x_dim - 14, cy)
        qp.rotate(-90)
        qp.drawText(int(-12), int(4), str(value))
        qp.restore()
    else:
        qp.drawText(int(x_dim - 24), int(cy + 4), str(value))


def _draw_dimension_horizontal(qp, x_left, x_right, y_bottom_edge, value, gap_px, scale):
    """Ширина: выносные вниз, размерная линия (без стрелок) и текст."""
    y_dim = y_bottom_edge + gap_px
    qp.drawLine(int(x_left), int(y_bottom_edge), int(x_left), int(y_dim))
    qp.drawLine(int(x_right), int(y_bottom_edge), int(x_right), int(y_dim))
    qp.drawLine(int(x_left), int(y_dim), int(x_right), int(y_dim))
    cx = (x_left + x_right) / 2
    qp.drawText(int(cx - 12), int(y_dim + TEXT_OFFSET_MM * scale + 4), str(value))


def _draw_dimension_horizontal_above(qp, x_left, x_right, y_top_edge, value, gap_px, scale):
    """Ширина листа: размерная линия и текст НАД рамкой (выносы вверх), чтобы не выходить за нижнюю границу."""
    y_dim = y_top_edge - gap_px
    qp.drawLine(int(x_left), int(y_top_edge), int(x_left), int(y_dim))
    qp.drawLine(int(x_right), int(y_top_edge), int(x_right), int(y_dim))
    qp.drawLine(int(x_left), int(y_dim), int(x_right), int(y_dim))
    qp.setFont(QFont("Arial", 12, QFont.Bold))
    fm = QFontMetrics(qp.font())
    txt = "%d мм" % int(value)
    tw = fm.horizontalAdvance(txt)
    cx = (x_left + x_right) / 2
    qp.drawText(int(cx - tw / 2), int(y_dim - 4), txt)


def _draw_dimension_vertical_left(qp, x_sheet_left, y_bottom, y_top, value, gap_px, scale):
    """Высота листа: вынос СЛЕВА от листа — размерная линия и только число (без слова «Высота»)."""
    gap = max(gap_px, 24)
    x_dim = x_sheet_left - gap
    qp.setPen(QPen(QColor(0, 0, 0), 2))
    qp.drawLine(int(x_sheet_left), int(y_bottom), int(x_dim), int(y_bottom))
    qp.drawLine(int(x_sheet_left), int(y_top), int(x_dim), int(y_top))
    qp.drawLine(int(x_dim), int(y_bottom), int(x_dim), int(y_top))
    cy = (y_bottom + y_top) / 2
    qp.save()
    qp.setFont(QFont("Arial", 12, QFont.Bold))
    qp.setPen(QColor(0, 0, 0))
    qp.translate(x_dim - 16, cy)
    qp.rotate(-90)
    qp.drawText(int(-8), int(5), "%d" % int(value))
    qp.restore()


# Зона размеров внутри прямоугольника: низ (ширина) ~14px, левый край (высота) ~22px — подпись не должна заходить
DIM_BOTTOM_PX = 18
DIM_LEFT_PX = 24


def _max_font_single_line_in_box(text, max_w_px, max_h_px, min_pt=5, max_pt=18):
    """
    Максимальный кегль Arial, при котором одна строка помещается в прямоугольник max_w×max_h (пиксели).
    Подбор по самой плитке, а не по масштабу всего листа.
    """
    text = str(text)
    mw = max(float(max_w_px), 6.0)
    mh = max(float(max_h_px), 6.0)
    for pt in range(int(max_pt), int(min_pt) - 1, -1):
        fm = QFontMetrics(QFont("Arial", pt))
        adv = float(fm.horizontalAdvance(text))
        ht = float(fm.height())
        if adv <= mw and ht <= mh:
            return pt
    return int(min_pt)


def _max_font_rotated_minus_90_in_tile(text, max_vertical_run_px, max_horizontal_depth_px, min_pt=5, max_pt=18):
    """
    Текст рисуется горизонтально, затем rotate(-90): advance идёт вдоль вертикали плитки, высота шрифта — вдоль ширины.
    Ограничения: horizontalAdvance(text) <= max_vertical_run_px, height() <= max_horizontal_depth_px.
    """
    text = str(text)
    mv = max(float(max_vertical_run_px), 6.0)
    md = max(float(max_horizontal_depth_px), 6.0)
    for pt in range(int(max_pt), int(min_pt) - 1, -1):
        fm = QFontMetrics(QFont("Arial", pt))
        adv = float(fm.horizontalAdvance(text))
        ht = float(fm.height())
        if adv <= mv and ht <= md:
            return pt
    return int(min_pt)


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

# Цветная легенда: ширина блока и правый отступ совпадают с _draw_legend_three
_LEGEND_COLOR_BOX_W = 16
_LEGEND_COLOR_GAP = 8
_LEGEND_COLOR_RIGHT_MARGIN = 12
_LEGEND_COLOR_ITEMS = (
    (QColor(70, 130, 180), "Изделие заказчика"),
    (QColor(255, 167, 38), "Изделие текущего заказа"),
    (QColor(72, 187, 120), "Деловой остаток"),
    (QColor(220, 53, 69), "Мусор (неделовой)"),
)


def _legend_color_block_width_px():
    fm = QFontMetrics(QFont("Arial", 11))
    max_tw = max(fm.horizontalAdvance(text) for _, text in _LEGEND_COLOR_ITEMS)
    return _LEGEND_COLOR_BOX_W + _LEGEND_COLOR_GAP + max_tw


def _legend_color_reserved_right_px():
    return _legend_color_block_width_px() + _LEGEND_COLOR_RIGHT_MARGIN + 8


# Буквы для обработки кромок (только в схеме/последовательности/PDF; в окне создания реза — полные подписи)
EDGE_LETTERS = {'grinding': 'Ш', 'polishing': 'П', 'facet': 'Ф'}


def _edge_display(et, side):
    """Подпись кромки для стороны: П, Ш или «Ф N» при фацете."""
    et = et or {}
    v = et.get(side)
    if v == 'facet':
        mm = et.get('facet_mm')
        return "Ф %s" % (int(mm) if mm is not None else 15)
    return EDGE_LETTERS.get(v, '') if v else ''


def _draw_dim_inside_rect(qp, x, y, w_px, h_px, w_mm, h_mm, scale=1.0, edge_treatment=None):
    """Только 2 размера: ширина внизу, высота слева (внутри прямоугольника). Буквы кромок: П, Ш, Ф N.
    Кегль подбирается отдельно для каждой подписи по пиксельному размеру плитки (w_px×h_px), не от scale листа."""
    et = edge_treatment or {}
    pad = 4.0
    # Колонка слева под повёрнутый текст — не шире половины плитки и не уже 10px
    col_depth = max(10.0, min(float(DIM_LEFT_PX), w_px * 0.42))
    # Полоса снизу под ширину
    row_h = max(10.0, min(float(DIM_BOTTOM_PX), h_px * 0.36))
    # Вертикальный «ход» текста после поворота = вдоль высоты плитки
    vert_run = max(12.0, h_px - 2 * pad)
    left_letter = _edge_display(et, "left")
    left_line = str(h_mm) + (" " + left_letter if left_letter else "")
    bottom_letter = _edge_display(et, "bottom")
    bottom_line = str(w_mm) + (" " + bottom_letter if bottom_letter else "")

    pt_left = _max_font_rotated_minus_90_in_tile(left_line, vert_run - 2, col_depth - 2)
    pt_bot = _max_font_single_line_in_box(bottom_line, max(10.0, w_px - 2 * pad), row_h - 2)

    # Слева: высота + буква кромки (повёрнуто -90°)
    qp.save()
    qp.setFont(QFont("Arial", pt_left))
    fm_l = QFontMetrics(qp.font())
    qp.translate(x + col_depth * 0.35, y + h_px / 2)
    qp.rotate(-90)
    adv = float(fm_l.horizontalAdvance(left_line))
    qp.drawText(int(-adv / 2), int(fm_l.ascent() - 1), left_line)
    qp.restore()

    # Снизу: ширина + буква кромки
    qp.setFont(QFont("Arial", pt_bot))
    fm_b = QFontMetrics(qp.font())
    cx = x + w_px / 2
    bw = float(fm_b.horizontalAdvance(bottom_line))
    baseline_y = y + h_px - float(fm_b.descent()) - 2.0
    qp.drawText(int(cx - bw / 2), int(baseline_y), bottom_line)


class CuttingCanvas(QWidget):
    """Канвас раскроя. fit_to_view=True — масштаб по размеру окна с сохранением пропорций.
    preview_mode=True — только схема без размеров и подписей (для миниатюр в диалоге выбора варианта)."""
    def __init__(self, parent=None, fit_to_view=True, preview_mode=False):
        super().__init__(parent)
        self.layouts = []
        self.scale = 0.5
        self.fit_to_view = fit_to_view
        self.preview_mode = preview_mode
        self.draw_origin_bottom_left = True  # привязка листа к нижнему левому углу (снизу вверх по оси x)
        self.setMinimumSize(300 if preview_mode else 400, 300)
        self.setStyleSheet("background-color: #E6F2FF;")
        # Пошаговый режим: либо один общий step_index, либо step_by_sheet — список шагов по листам (слайдер на каждый лист).
        self.step_index = 0
        self.step_by_sheet = None  # [step0, step1, ...] или None — тогда используется step_index
        self._global_cut_order = []
        self._piece_to_global = {}
        self._piece_to_local = {}   # (lay_idx, piece_idx) -> локальный номер реза на этом листе (0, 1, 2, ...)
        self._remnant_display_numbers = []  # номера деловых остатков (уникальные с склада) по порядку листов
        # Если задано — масштаб считается по viewport (один лист влезает в viewport), размер канваса = контент (для прокрутки)
        self._viewport_w = None
        self._viewport_h = None

    def set_viewport_size(self, w, h):
        """Задать размер видимой области (viewport). Масштаб = «один лист влезает в viewport», размер канваса = полный контент."""
        if w is not None and h is not None and w > 0 and h > 0:
            self._viewport_w = int(w)
            self._viewport_h = int(h)
        else:
            self._viewport_w = None
            self._viewport_h = None
        self.updateGeometry()
        self.update()

    def set_remnant_display_numbers(self, numbers):
        """Задать номера для подписи деловых остатков (№ на схеме и в PDF — как на складе)."""
        self._remnant_display_numbers = list(numbers) if numbers else []
        self.update()

    def set_layouts(self, layouts):
        self.layouts = layouts or []
        self._global_cut_order = []
        self._piece_to_global = {}
        self._piece_to_local = {}
        # Порядок реза: снизу слева, по оси x вправо и вверх (сначала нижняя полоса, затем следующая)
        for lay_idx, lay in enumerate(self.layouts):
            pieces = lay.get('pieces', [])
            ordered = sorted(range(len(pieces)), key=lambda i: (-pieces[i]['y'], pieces[i]['x']))
            for local_i, piece_idx in enumerate(ordered):
                g = len(self._global_cut_order)
                self._global_cut_order.append((lay_idx, piece_idx))
                self._piece_to_global[(lay_idx, piece_idx)] = g
                self._piece_to_local[(lay_idx, piece_idx)] = local_i
        if not self.fit_to_view:
            total_w = total_h = 0
            for lay in self.layouts:
                total_w = max(total_w, lay['sheet_width'] * self.scale + 120)
                total_h += lay['sheet_height'] * self.scale + 60
            self.setMinimumSize(int(total_w), int(total_h))
        self.update()

    def total_cut_steps(self):
        """Число резов (изделий) по всем листам."""
        return len(self._global_cut_order)

    def steps_per_sheet(self):
        """Число шагов реза по каждому листу (0..n для n изделий на листе). Возвращает [n0, n1, ...]."""
        out = []
        for lay_idx, lay in enumerate(self.layouts):
            n = len(lay.get('pieces', []))
            out.append(n)
        return out

    def _compute_scale(self):
        if not self.layouts or not self.fit_to_view:
            return self.scale
        w = self._viewport_w if (self._viewport_w and self._viewport_h) else self.width()
        h = self._viewport_h if (self._viewport_w and self._viewport_h) else self.height()
        if w < 50 or h < 50:
            return self.scale
        max_sw = max(lay['sheet_width'] for lay in self.layouts)
        total_sh = sum(lay['sheet_height'] for lay in self.layouts)
        max_sheet_h = max(lay['sheet_height'] for lay in self.layouts)
        if max_sw <= 0 or total_sh <= 0:
            return self.scale
        if self.preview_mode:
            pad = 4
            avail_w = w - 2 * pad
            avail_h = h - 2 * pad - (len(self.layouts) - 1) * 4
            scale_w = avail_w / max_sw if max_sw > 0 else 0.1
            scale_h = avail_h / total_sh if total_sh > 0 else scale_w
            return max(0.05, min(scale_w, scale_h))
        # Запас по вертикали и слева (выносные размеры)
        top_extra_px = 70
        extra_mm_left = 50
        avail_w = w - MARGIN_LEFT - MARGIN_RIGHT
        avail_h = h - MARGIN_TOP - MARGIN_BOTTOM - top_extra_px
        # Один лист влезает в viewport; при нескольких листах контент выше — вертикальная прокрутка
        scale_w = avail_w / (max_sw + extra_mm_left)
        scale_h = avail_h / max_sheet_h if max_sheet_h > 0 else scale_w
        scale_fit = max(0.05, min(scale_w, scale_h))
        return scale_fit * 0.92

    def content_size_for_scale(self, scale):
        """Размер канваса в пикселях при заданном масштабе (для прокрутки при нескольких листах)."""
        if not self.layouts:
            return (400, 300)
        max_sw = max(lay['sheet_width'] for lay in self.layouts)
        total_sh = sum(lay['sheet_height'] for lay in self.layouts)
        top_extra_px = 70
        gap_sheets = 4 if self.preview_mode else GAP_SHEETS
        right_pad = MARGIN_RIGHT if self.preview_mode else max(MARGIN_RIGHT, _legend_color_reserved_right_px())
        content_w = int(MARGIN_LEFT + max_sw * scale + right_pad)
        content_h = int(MARGIN_TOP + top_extra_px + total_sh * scale + (len(self.layouts) - 1) * gap_sheets + MARGIN_BOTTOM)
        return (max(400, content_w), max(300, content_h))

    def sizeHint(self):
        """При заданном viewport — размер контента (несколько листов → вертикальная прокрутка)."""
        if self._viewport_w and self._viewport_h and self.layouts and self.fit_to_view and not self.preview_mode:
            scale = self._compute_scale()
            return QSize(*self.content_size_for_scale(scale))
        return super().sizeHint() if super().sizeHint().isValid() else QSize(400, 300)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_to_view and self.layouts:
            self.update()

    def _tier_gaps_by_value(self, items, scale):
        """items = [(position, dimension_value), ...]. Самый большой размер — самый длинный вынос, чтобы числа не пересекались."""
        if not items:
            return {}
        # Сортируем по убыванию значения размера: самый длинный размер получает самый удалённый вынос
        order = sorted(range(len(items)), key=lambda i: -items[i][1])
        n = len(items)
        result = {}
        for rank, idx in enumerate(order):
            pos = items[idx][0]
            result[pos] = GAP_MM * scale + (n - 1 - rank) * TIER_STEP_MM * scale
        return result

    def paintEvent(self, event):
        if not self.layouts:
            super().paintEvent(event)
            return
        scale = self._compute_scale()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setRenderHint(QPainter.TextAntialiasing)
        qp.setFont(QFont("Arial", 11))
        qp.setPen(QPen(QColor(0, 0, 0), 1))
        pad = 4 if self.preview_mode else 0
        margin_left = pad or MARGIN_LEFT
        margin_bottom = pad or MARGIN_BOTTOM
        margin_top = pad or MARGIN_TOP
        gap_sheets = 4 if self.preview_mode else GAP_SHEETS
        # Привязка к нижнему левому: лист 1 снизу, лист 2 выше и т.д.
        if self.draw_origin_bottom_left and self.layouts:
            bottom_y = self.height() - margin_bottom
            y_offsets = []
            for i, lay in enumerate(self.layouts):
                sh_pt = lay['sheet_height'] * scale
                off = bottom_y - sh_pt
                for j in range(i):
                    off -= (self.layouts[j]['sheet_height'] * scale + gap_sheets)
                y_offsets.append(off)
        else:
            y_offsets = [margin_top]
            for i in range(1, len(self.layouts)):
                y_offsets.append(y_offsets[-1] + self.layouts[i - 1]['sheet_height'] * scale + gap_sheets)
        for idx, lay in enumerate(self.layouts):
            sh = lay['sheet_height']
            sw = lay['sheet_width']
            sheet_h_pt = sh * scale
            sheet_w_pt = sw * scale
            origin_x = margin_left
            origin_y = y_offsets[idx] if idx < len(y_offsets) else margin_top
            def sheet_to_px_y(y_from_top):
                return origin_y + y_from_top * scale
            def sheet_to_px_bottom(y_from_top, height):
                return origin_y + (y_from_top + height) * scale

            gap_horz_above = GAP_MM * scale
            inset_vertical = 24 * scale

            pieces_sheet = lay.get('pieces') or []
            reserve_no_cuts = (not pieces_sheet) and sw > 0 and sh > 0
            # Лист зарезервирован, выкроев ещё нет — тёплый фон (отличается от обычного листа на схеме у менеджера)
            qp.setPen(QPen(QColor(0, 0, 0), 1 if self.preview_mode else 2))
            if reserve_no_cuts:
                qp.setBrush(QColor(255, 236, 205))
            else:
                qp.setBrush(QColor(232, 238, 244))
            qp.drawRect(int(origin_x), int(origin_y), int(sheet_w_pt), int(sheet_h_pt))

            if not self.preview_mode:
                _draw_dimension_horizontal_above(qp, origin_x, origin_x + sheet_w_pt, origin_y, sw, gap_horz_above, scale)
                _draw_dimension_vertical_left(qp, origin_x, origin_y + sheet_h_pt, origin_y, sh, inset_vertical, scale)
                material = (lay.get('material') or 'Лист')[:30]
                rot_txt = " (90°)" if lay.get('rotated') else ""
                sheet_no = lay.get('sheet_id', '—')
                title = "Лист %d. № %s. %s  %d × %d мм%s" % (idx + 1, sheet_no, material, sw, sh, rot_txt)
                qp.setPen(QColor(0, 70, 120))
                qp.setFont(QFont("Arial", 12, QFont.Bold))
                title_y = origin_y - gap_horz_above - 20
                qp.drawText(int(origin_x), int(title_y), title)
                if reserve_no_cuts:
                    qp.setFont(QFont("Arial", 10, QFont.Bold))
                    qp.setPen(QColor(160, 82, 0))
                    qp.drawText(int(origin_x), int(title_y + 16), "лист без выкроев (зарезервирован)")
                qp.setFont(QFont("Arial", 11))
                qp.setPen(QPen(QColor(0, 0, 0), 1))

            # Неделовые отходы (мусор) — красный
            qp.setBrush(QColor(220, 53, 69))
            qp.setPen(QPen(QColor(180, 40, 50), 1))
            for r in lay.get('waste_rects', []):
                x = origin_x + r['x'] * scale
                y = sheet_to_px_y(r['y'])
                w, h = r['w'] * scale, r['h'] * scale
                qp.drawRect(int(x), int(y), int(w), int(h))
                if not self.preview_mode:
                    qp.setPen(QPen(QColor(0, 0, 0), 1))
                    _draw_dim_inside_rect(qp, x, y, w, h, int(r['w']), int(r['h']), scale)
                    qp.setPen(QPen(QColor(180, 40, 50), 1))

            # Деловые остатки — зелёный; подпись № по уникальному номеру остатка (если задан)
            business_rects = lay.get('business_rects', [])
            remnant_offset = sum(len(self.layouts[k].get('business_rects', [])) for k in range(idx))
            for i, r in enumerate(business_rects):
                qp.setBrush(QColor(72, 187, 120))
                qp.setPen(QPen(QColor(0, 100, 0), 1))
                x = origin_x + r['x'] * scale
                y = sheet_to_px_y(r['y'])
                w, h = r['w'] * scale, r['h'] * scale
                qp.drawRect(int(x), int(y), int(w), int(h))
                if not self.preview_mode:
                    qp.setPen(QPen(QColor(0, 0, 0), 1))
                    _draw_dim_inside_rect(qp, x, y, w, h, int(r['w']), int(r['h']), scale)
                    gi = remnant_offset + i
                    if gi < len(self._remnant_display_numbers) and self._remnant_display_numbers[gi]:
                        lbl_rm = "№ %s" % self._remnant_display_numbers[gi]
                        pt_rm = _max_font_single_line_in_box(
                            lbl_rm,
                            max(12.0, min(w - 6.0, w * 0.92)),
                            max(12.0, min(h - 6.0, h * 0.92)),
                            min_pt=5,
                            max_pt=22,
                        )
                        qp.setFont(QFont("Arial", pt_rm))
                        qp.drawText(int(x), int(y), int(w), int(h), Qt.AlignCenter, lbl_rm)

            # Линии реза: если в макете сохранены cut_segments — рисуем их (направления H/V после правок)
            pieces = pieces_sheet
            cut_segments = lay.get('cut_segments') or []
            if self.step_by_sheet and idx < len(self.step_by_sheet):
                step = self.step_by_sheet[idx]
            else:
                step = self.step_index
            if not self.preview_mode and cut_segments:
                qp.setPen(QPen(QColor(0, 0, 0), 2))
                for seg in cut_segments:
                    t = str(seg.get('type') or 'H').strip().upper()
                    pos = float(seg.get('pos', 0))
                    lo = float(seg.get('extent_lo', 0))
                    hi = float(seg.get('extent_hi', sw if t == 'H' else sh))
                    if t == 'H':
                        y_px = sheet_to_px_y(pos)
                        qp.drawLine(int(origin_x + lo * scale), int(y_px), int(origin_x + hi * scale), int(y_px))
                    else:
                        x_px = origin_x + pos * scale
                        y1_px = sheet_to_px_y(lo)
                        y2_px = sheet_to_px_y(hi)
                        qp.drawLine(int(x_px), int(y1_px), int(x_px), int(y2_px))
                qp.setPen(QPen(QColor(0, 0, 0), 1))
            elif not self.preview_mode and not cut_segments:
                # Иначе — линия по шагу последовательности резов (если есть)
                if step > 0 and pieces:
                    not_cut = [i for i in range(len(pieces)) if self._piece_to_local.get((idx, i), -1) >= step]
                    if not_cut:
                        y_cut_sheet = min(pieces[i]['y'] for i in not_cut)
                        y_cut_px = sheet_to_px_y(y_cut_sheet)
                        qp.setPen(QPen(QColor(200, 50, 50), 3))
                        qp.drawLine(int(origin_x), int(y_cut_px), int(origin_x + sheet_w_pt), int(y_cut_px))
                        qp.setPen(QPen(QColor(0, 0, 0), 1))
            piece_groups = defaultdict(list)
            for i in range(len(pieces)):
                k = (pieces[i].get('recipient') or '', pieces[i]['w'], pieces[i]['h'])
                piece_groups[k].append(i)
            piece_index = {}
            for k, indices in piece_groups.items():
                total = len(indices)
                for pos, piece_i in enumerate(indices):
                    piece_index[(idx, piece_i)] = (pos + 1, total)

            # Мелкие изделия (хотя бы одна сторона < порога): на схеме только *1, *2…; расшифровка — в легенде справа
            thresh = _small_piece_threshold_mm()
            small_pieces_list = []
            for i, p in enumerate(pieces):
                w_p, h_p = (p.get('w', 0) or 0), (p.get('h', 0) or 0)
                if w_p < thresh or h_p < thresh:
                    small_pieces_list.append((i, p))
            piece_to_star = {}
            for star_num, (piece_i, p) in enumerate(small_pieces_list, 1):
                piece_to_star[(idx, piece_i)] = star_num

            for i, p in enumerate(pieces):
                local_index = self._piece_to_local.get((idx, i), -1)
                is_order_piece = bool(p.get("_is_order_piece"))
                if not self.preview_mode and step > 0 and local_index >= 0:
                    is_cut_off = (local_index < step)
                    if is_cut_off:
                        qp.setBrush(QColor(160, 180, 200))
                        qp.setPen(QPen(QColor(80, 100, 120), 1))
                    elif is_order_piece:
                        qp.setBrush(QColor(255, 167, 38))
                        qp.setPen(QPen(QColor(230, 126, 34), 1))
                    else:
                        qp.setBrush(QColor(70, 130, 180))
                        qp.setPen(QPen(QColor(0, 0, 0), 1))
                else:
                    if is_order_piece:
                        qp.setBrush(QColor(255, 167, 38))
                        qp.setPen(QPen(QColor(230, 126, 34), 1))
                    else:
                        qp.setBrush(QColor(70, 130, 180))
                        qp.setPen(QPen(QColor(0, 0, 0), 1))
                x = origin_x + p['x'] * scale
                y = sheet_to_px_y(p['y'])
                w = p['w'] * scale
                h = p['h'] * scale
                qp.drawRect(int(x), int(y), int(w), int(h))
                if not self.preview_mode:
                    star_num = piece_to_star.get((idx, i))
                    if star_num is not None:
                        # Мелкое изделие: только *N по центру
                        qp.setPen(QColor(0, 0, 0))
                        star_txt = "*%d" % star_num
                        pt_st = _max_font_single_line_in_box(star_txt, max(10.0, w - 8), max(10.0, h - 8), min_pt=6, max_pt=24)
                        qp.setFont(QFont("Arial", pt_st))
                        qp.drawText(int(x), int(y), int(w), int(h), Qt.AlignCenter, star_txt)
                    else:
                        qp.setPen(QPen(QColor(0, 0, 0), 1))
                        _draw_dim_inside_rect(qp, x, y, w, h, int(p['w']), int(p['h']), scale, p.get('edge_treatment'))
                        qp.setPen(QColor(0, 0, 0))
                        cur, total = piece_index.get((idx, i), (1, 1))
                        num_str = "%d/%d" % (cur, total) if total > 1 else ""
                        text_lines = [(p.get('recipient') or '').strip()[:16], "%d×%d" % (p['w'], p['h'])]
                        if num_str:
                            text_lines.append(num_str)
                        text_lines = [s for s in text_lines if s]
                        left_reserve = min(float(DIM_LEFT_PX), max(14.0, w * 0.22))
                        bot_reserve = min(float(DIM_BOTTOM_PX), max(12.0, h * 0.22))
                        safe_left = x + left_reserve
                        safe_top = y + 4
                        safe_w = max(8.0, w - left_reserve - 8)
                        safe_h = max(8.0, h - bot_reserve - 8)
                        if safe_w > 10 and safe_h > 10 and text_lines:
                            lbl = "\n".join(text_lines)
                            font_pt = 8
                            start_pt = min(18, max(6, int(min(safe_w, safe_h) / max(2.2, len(text_lines) * 0.9))))
                            for pt in range(start_pt, 5, -1):
                                qp.setFont(QFont("Arial", pt))
                                fm = QFontMetrics(qp.font())
                                br = fm.boundingRect(0, 0, int(safe_w), int(safe_h), Qt.AlignCenter | Qt.AlignVCenter | Qt.TextWordWrap, lbl)
                                if br.width() <= safe_w and br.height() <= safe_h:
                                    font_pt = pt
                                    break
                            qp.setFont(QFont("Arial", font_pt))
                            fm = QFontMetrics(qp.font())
                            line_height = font_pt * 1.35
                            block_h = line_height * len(text_lines)
                            cx_rect = x + w / 2
                            cy_rect = y + h / 2
                            if h > w:
                                qp.save()
                                qp.translate(cx_rect, cy_rect)
                                qp.rotate(90)
                                cy0 = (len(text_lines) - 1) * line_height / 2
                                for ii, line in enumerate(text_lines):
                                    qp.drawText(int(-fm.horizontalAdvance(line) / 2), int(cy0 - ii * line_height), line)
                                qp.restore()
                            else:
                                cy0 = y + (h - block_h) / 2 + line_height * 0.8
                            for ii, line in enumerate(text_lines):
                                tw = fm.horizontalAdvance(line)
                                qp.drawText(int(x + (w - tw) / 2), int(cy0 + ii * line_height), line)

            # Подсветка зон (мм → пиксели), напр. деловой остаток в «истории остатка»
            for hr in (lay.get("_highlight_rects_mm") or []):
                if not isinstance(hr, dict):
                    continue
                try:
                    hx = int(hr.get("x", 0) or 0)
                    hy = int(hr.get("y", 0) or 0)
                    hw = int(hr.get("w", 0) or 0)
                    hh = int(hr.get("h", 0) or 0)
                except (TypeError, ValueError):
                    continue
                if hw <= 0 or hh <= 0:
                    continue
                qp.setBrush(QColor(255, 193, 7, 95))
                qp.setPen(QPen(QColor(230, 126, 34), 3))
                x = origin_x + hx * scale
                y = sheet_to_px_y(hy)
                w = hw * scale
                h = hh * scale
                qp.drawRect(int(x), int(y), int(w), int(h))

            # Легенда мелких деталей справа: *1, *2… и блок с размерами (высота слева 90°, ширина снизу) и получателем
            if not self.preview_mode and small_pieces_list:
                legend_x = origin_x + sheet_w_pt + 10
                legend_y0 = origin_y
                for star_num, (piece_i, p) in enumerate(small_pieces_list, 1):
                    ly = legend_y0 + (star_num - 1) * LEGEND_ITEM_H
                    qp.setPen(QColor(0, 0, 0))
                    qp.setFont(QFont("Arial", 10))
                    qp.drawText(int(legend_x), int(ly + 14), "*%d" % star_num)
                    box_x = legend_x + 24
                    box_y = ly
                    qp.setBrush(QColor(220, 230, 245))
                    qp.setPen(QPen(QColor(0, 0, 0), 1))
                    qp.drawRect(int(box_x), int(box_y), LEGEND_BOX_W, LEGEND_BOX_H)
                    qp.setPen(QColor(0, 0, 0))
                    qp.setFont(QFont("Arial", 8))
                    w_mm, h_mm = p['w'], p['h']
                    # Высота слева (повёрнуто 90°)
                    qp.save()
                    qp.translate(box_x + 10, box_y + LEGEND_BOX_H / 2)
                    qp.rotate(-90)
                    qp.drawText(int(-8), int(4), str(h_mm))
                    qp.restore()
                    # Ширина снизу
                    qp.drawText(int(box_x + LEGEND_BOX_W / 2 - 8), int(box_y + LEGEND_BOX_H - 4), str(w_mm))
                    # Получатель сверху/по центру
                    rec = (p.get('recipient') or '').strip()[:14]
                    qp.drawText(int(box_x + 2), int(box_y + 12), rec if rec else "—")

        if not self.preview_mode:
            self._draw_legend_three(qp)
        qp.end()

    def _draw_legend_three(self, qp):
        """Легенда по типам заливки: верхний правый угол; ширина канваса с запасом под блок."""
        LEGEND_TOP_MARGIN = 10
        LEGEND_LINE_HEIGHT = 20
        BOX_H = 14
        qp.setPen(QPen(QColor(0, 0, 0), 1))
        qp.setFont(QFont("Arial", 11))
        total_w = _legend_color_block_width_px()
        x0 = self.width() - _LEGEND_COLOR_RIGHT_MARGIN - total_w
        y0 = LEGEND_TOP_MARGIN
        if x0 < MARGIN_LEFT + 50:
            return
        for i, (color, text) in enumerate(_LEGEND_COLOR_ITEMS):
            y = y0 + i * LEGEND_LINE_HEIGHT
            qp.setBrush(color)
            qp.drawRect(int(x0), int(y), _LEGEND_COLOR_BOX_W, BOX_H)
            qp.setPen(QColor(0, 0, 0))
            qp.drawText(int(x0 + _LEGEND_COLOR_BOX_W + _LEGEND_COLOR_GAP), int(y + 12), text)
