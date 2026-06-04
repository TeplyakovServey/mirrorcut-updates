"""
Step-by-step cutting sequence canvas: plan -> cut line -> two separate rects -> ...
At every step all dimensions are drawn (extension lines without arrows).
Final step: compact layout via rectpack.
"""
import math
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics

from ui.cutting_canvas import _draw_dim_inside_rect

try:
    from rectpack import newPacker
    from rectpack import PackingMode
    from rectpack import SORT_AREA
    _HAS_RECTPACK = True
except ImportError:
    newPacker = None
    _HAS_RECTPACK = False

GAP_DIM = 8
TIER_STEP = 12  # шаг яруса выносок (как на схеме раскроя)


def _draw_dim_vert_simple(qp, x_left, y_top, y_bottom, value_mm, gap_px, text_rotated=True):
    """Вертикальный размер (высота листа): выноски влево, линия, подпись «Высота … мм» хорошо видна."""
    x_dim = x_left - max(gap_px, 14)
    qp.setPen(QPen(QColor(0, 0, 0), 2))
    qp.drawLine(int(x_dim), int(y_bottom), int(x_left), int(y_bottom))
    qp.drawLine(int(x_dim), int(y_top), int(x_left), int(y_top))
    qp.drawLine(int(x_dim), int(y_bottom), int(x_dim), int(y_top))
    cy = (y_top + y_bottom) / 2
    qp.setFont(QFont("Arial", 14, QFont.Bold))
    txt = "Высота %d мм" % int(value_mm)
    if text_rotated:
        qp.save()
        qp.translate(x_dim - 22, cy)
        qp.rotate(-90)
        fm = QFontMetrics(qp.font())
        tw = fm.horizontalAdvance(txt)
        qp.drawText(int(-tw / 2), int(6), txt)
        qp.restore()
    else:
        qp.drawText(int(x_dim - 24), int(cy + 4), txt)
    qp.setPen(QPen(QColor(0, 0, 0), 1))


def _draw_dim_horz_simple(qp, x_left, x_right, y_bottom, value_mm, gap_px):
    """Горизонтальный размер: выноски вниз, линия, текст (без стрелок)."""
    y_dim = y_bottom + gap_px
    qp.drawLine(int(x_left), int(y_bottom), int(x_left), int(y_dim))
    qp.drawLine(int(x_right), int(y_bottom), int(x_right), int(y_dim))
    qp.drawLine(int(x_left), int(y_dim), int(x_right), int(y_dim))
    cx = (x_left + x_right) / 2
    qp.drawText(int(cx - 12), int(y_dim + 14), str(value_mm))


EDGE_LETTERS = {'grinding': 'Ш', 'polishing': 'П', 'facet': 'Ф'}


def _edge_display(et, side):
    """Подпись кромки: П, Ш или «Ф N» при фацете."""
    et = et or {}
    v = et.get(side)
    if v == 'facet':
        mm = et.get('facet_mm')
        return "Ф %s" % (int(mm) if mm is not None else 15)
    return EDGE_LETTERS.get(v, '') if v else ''


def _small_piece_threshold_mm():
    try:
        from user_settings import get_small_piece_mm
        return get_small_piece_mm()
    except Exception:
        return 200
LEGEND_ITEM_H = 52
LEGEND_BOX_W = 72
LEGEND_BOX_H = 44


def _tier_gaps_by_value(items):
    """items = [(position, dimension_value), ...]. Самый большой размер — самый длинный вынос (в px)."""
    if not items:
        return {}
    merged = {}
    for pos, val in items:
        merged[pos] = max(merged.get(pos, 0), val)
    lst = list(merged.items())
    order = sorted(range(len(lst)), key=lambda i: -lst[i][1])
    n = len(lst)
    return {lst[order[i]][0]: GAP_DIM + (n - 1 - i) * TIER_STEP for i in range(n)}


def _draw_rect_with_dims(qp, x, y, w_px, h_px, w_mm, h_mm, gap_px, label=None):
    """Прямоугольник и размеры (выноски без стрелок)."""
    qp.setPen(QPen(QColor(0, 0, 0), 1))
    qp.drawRect(int(x), int(y), int(w_px), int(h_px))
    _draw_dim_vert_simple(qp, x, y, y + h_px, h_mm, gap_px)
    _draw_dim_horz_simple(qp, x, x + w_px, y + h_px, w_mm, gap_px)
    if label and w_px > 30 and h_px > 20:
        qp.drawText(int(x + 4), int(y + 14), str(label)[:20])


class CutSequenceStepCanvas(QWidget):
    """Draws one step of the cutting sequence: plan, cut_rect, after_cut, final (with numbered rectangles)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = None
        self.steps = []
        self.step_index = 0
        self.setMinimumSize(400, 300)
        self.setStyleSheet("background-color: #E8EEF5;")

    def set_sheet_and_steps(self, layout, steps):
        self.layout = layout
        self.steps = steps or []
        self.step_index = min(max(0, self.step_index), max(0, len(self.steps) - 1))
        # Перерисовку делает set_step — не дублируем update (ускоряет смену листа).

    def set_step(self, index):
        self.step_index = min(max(0, index), max(0, len(self.steps) - 1))
        if not self.steps:
            self.setMinimumHeight(300)
        else:
            step = self.steps[self.step_index] if self.step_index < len(self.steps) else self.steps[0]
            if step.get('type') == 'final':
                prods = step.get('products', [])
                self.setMinimumHeight(min(800, 280 + len(prods) * 42))
            else:
                self.setMinimumHeight(300)
        self.update()

    def _scale(self):
        if not self.layout or not self.steps:
            return 0.5
        w, h = self.width(), self.height()
        if w < 150 or h < 150:
            return 0.5
        step = self.steps[self.step_index] if self.step_index < len(self.steps) else self.steps[0]
        sw = step.get('sheet_w', self.layout['sheet_width'])
        sh = step.get('sheet_h', self.layout['sheet_height'])
        if step['type'] == 'after_cut':
            lr, rr = step['left_rect'], step['right_rect']
            total_w = (lr['w'] + 40 + rr['w']) * 1.2
            total_h = max(lr['h'], rr['h']) * 1.2
        elif step['type'] == 'cut_rect':
            total_w = step.get('rect_w', sw) * 1.2
            total_h = step.get('rect_h', sh) * 1.2
        elif step['type'] == 'final':
            prods = step.get('products', [])
            if not prods:
                return 0.5
            total_w = sum(pr['w'] for pr in prods[:5]) * 0.8
            total_h = max(pr['h'] for pr in prods) * len(prods) * 0.5
        else:
            total_w = sw * 1.2
            total_h = sh * 1.2
        scale_w = (w - 120) / total_w if total_w else 0.5
        scale_h = (h - 80) / total_h if total_h else 0.5
        return max(0.1, min(scale_w, scale_h))

    def paintEvent(self, event):
        if not self.layout or not self.steps or self.step_index >= len(self.steps):
            super().paintEvent(event)
            return
        step = self.steps[self.step_index]
        scale = self._scale()
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setRenderHint(QPainter.TextAntialiasing)
        qp.setFont(QFont("Arial", 11))
        margin = 60
        # Origin at bottom-left of drawing area (sheet bottom = bottom)
        bottom_y = self.height() - margin
        left_x = margin

        if step['type'] == 'plan':
            self._draw_plan(qp, step, scale, left_x, bottom_y)
        elif step['type'] == 'cut_rect':
            self._draw_cut_rect(qp, step, scale, left_x, bottom_y)
        elif step['type'] == 'after_cut':
            self._draw_after_cut(qp, step, scale, left_x, bottom_y)
        elif step['type'] == 'final':
            self._draw_final(qp, step, scale, left_x, bottom_y)
        qp.end()

    def _sheet_to_px(self, step, scale, left_x, bottom_y):
        """Convert sheet coords (y from top) to pixel coords (origin bottom-left)."""
        sh = step.get('sheet_h', 0)
        def px_x(x):
            return left_x + x * scale
        def px_y(y_from_top):
            return bottom_y - sh * scale + y_from_top * scale
        return px_x, px_y

    def _draw_plan(self, qp, step, scale, left_x, bottom_y):
        sw = step['sheet_w']
        sh = step['sheet_h']
        px_x, px_y = self._sheet_to_px(step, scale, left_x, bottom_y)
        sheet_top = bottom_y - sh * scale
        # Выноски только у листа
        vert_gaps = _tier_gaps_by_value([(left_x, sh)])
        horz_gaps = _tier_gaps_by_value([(bottom_y, sw)])
        gap_sheet = vert_gaps.get(left_x, GAP_DIM)
        qp.setBrush(QColor(232, 238, 244))
        qp.setPen(QPen(QColor(0, 0, 0), 2))
        qp.drawRect(int(left_x), int(sheet_top), int(sw * scale), int(sh * scale))
        _draw_dim_vert_simple(qp, left_x, sheet_top, bottom_y, sh, gap_sheet, text_rotated=True)
        _draw_dim_horz_simple(qp, left_x, left_x + sw * scale, bottom_y, sw, horz_gaps.get(bottom_y, GAP_DIM))
        sheet_no = (self.layout or {}).get('sheet_id', '')
        title = "План. Лист %d×%d мм" % (sw, sh)
        if sheet_no not in (None, ''):
            title += "  № %s" % sheet_no
        qp.setPen(QColor(0, 70, 120))
        qp.drawText(int(left_x), int(sheet_top - 20), title)
        qp.setPen(QPen(QColor(0, 0, 0), 1))
        for p in step.get('pieces', []):
            x, y = px_x(p['x']), px_y(p['y'])
            w_px, h_px = p['w'] * scale, p['h'] * scale
            qp.setBrush(QColor(70, 130, 180))
            qp.drawRect(int(x), int(y), int(w_px), int(h_px))
            _draw_dim_inside_rect(qp, x, y, w_px, h_px, int(p['w']), int(p['h']), 1.0, p.get('edge_treatment'))
            qp.setFont(QFont("Arial", 11))
            qp.drawText(int(x + 4), int(y + 14), "%d×%d" % (p['w'], p['h']))
        qp.setFont(QFont("Arial", 11))
        for r in step.get('business_rects', []):
            x, y = px_x(r['x']), px_y(r['y'])
            w_px, h_px = r['w'] * scale, r['h'] * scale
            qp.setBrush(QColor(72, 187, 120))
            qp.drawRect(int(x), int(y), int(w_px), int(h_px))
            _draw_dim_inside_rect(qp, x, y, w_px, h_px, int(r['w']), int(r['h']), 1.0, None)
        for r in step.get('waste_rects', []):
            x, y = px_x(r['x']), px_y(r['y'])
            w_px, h_px = r['w'] * scale, r['h'] * scale
            qp.setBrush(QColor(220, 53, 69))
            qp.drawRect(int(x), int(y), int(w_px), int(h_px))
            _draw_dim_inside_rect(qp, x, y, w_px, h_px, int(r['w']), int(r['h']), 1.0, None)

    def _draw_cut_rect(self, qp, step, scale, left_x, bottom_y):
        """Draw 'take rect N', rectangle with cut line (X or Y)."""
        rect_w = step['rect_w']
        rect_h = step['rect_h']
        axis = step.get('axis', 'x')
        cut_dim = step.get('cut_dim', 0)
        rect_id = step.get('rect_id', 0)
        rect_top = bottom_y - rect_h * scale
        gap = GAP_DIM
        qp.setBrush(QColor(200, 220, 240))
        qp.setPen(QPen(QColor(0, 0, 0), 2))
        qp.drawRect(int(left_x), int(rect_top), int(rect_w * scale), int(rect_h * scale))
        _draw_dim_vert_simple(qp, left_x, rect_top, bottom_y, rect_h, gap, text_rotated=True)
        _draw_dim_horz_simple(qp, left_x, left_x + rect_w * scale, bottom_y, rect_w, gap)
        if axis == 'x':
            x_line = left_x + cut_dim * scale
            qp.setPen(QPen(QColor(220, 50, 50), 4))
            qp.drawLine(int(x_line), int(rect_top), int(x_line), int(bottom_y))
            qp.setPen(QColor(0, 0, 0))
            _draw_dim_horz_simple(qp, left_x, x_line, bottom_y, cut_dim, gap + 14)
            if rect_w - cut_dim > 0:
                _draw_dim_horz_simple(qp, x_line, left_x + rect_w * scale, bottom_y, rect_w - cut_dim, gap + 28)
        else:
            # Y cut: horizontal line. cut_dim = strip height (bottom part); top part = rect_h - cut_dim
            top_part_h = rect_h - cut_dim
            y_line = rect_top + top_part_h * scale
            qp.setPen(QPen(QColor(220, 50, 50), 4))
            qp.drawLine(int(left_x), int(y_line), int(left_x + rect_w * scale), int(y_line))
            qp.setPen(QColor(0, 0, 0))
            if top_part_h > 0:
                _draw_dim_vert_simple(qp, left_x, rect_top, y_line, top_part_h, gap + 14, text_rotated=True)
            if cut_dim > 0:
                _draw_dim_vert_simple(qp, left_x, y_line, bottom_y, cut_dim, gap + 28, text_rotated=True)
        qp.setFont(QFont("Arial", 11, QFont.Bold))
        qp.drawText(int(left_x), int(rect_top - 24), step.get('label', 'Возьмите прямоугольник №%d. Режьте здесь.' % rect_id))
        qp.setFont(QFont("Arial", 11))

    def _draw_after_cut(self, qp, step, scale, left_x, bottom_y):
        """Draw two rectangles with numbers (№left_id, №right_id)."""
        lr, rr = step['left_rect'], step['right_rect']
        gap = 36
        lw_px, lh_px = lr['w'] * scale, lr['h'] * scale
        rw_px, rh_px = rr['w'] * scale, rr['h'] * scale
        left_top = bottom_y - lh_px
        right_top = bottom_y - rh_px
        if lr['w'] > 0 and lr['h'] > 0:
            qp.setBrush(QColor(232, 238, 244))
            qp.setPen(QPen(QColor(0, 0, 0), 2))
            qp.drawRect(int(left_x), int(left_top), int(lw_px), int(lh_px))
            _draw_dim_inside_rect(qp, left_x, left_top, lw_px, lh_px, int(lr['w']), int(lr['h']), 1.0, None)
            qp.drawText(int(left_x), int(left_top - 18), step.get('label_left', '№?  %d×%d' % (lr['w'], lr['h'])))
        if rr['w'] > 0 and rr['h'] > 0:
            rx = left_x + lw_px + gap
            qp.setBrush(QColor(200, 220, 240))
            qp.setPen(QPen(QColor(0, 0, 0), 2))
            qp.drawRect(int(rx), int(right_top), int(rw_px), int(rh_px))
            _draw_dim_inside_rect(qp, rx, right_top, rw_px, rh_px, rr['w'], rr['h'])
            qp.drawText(int(rx), int(right_top - 18), step.get('label_right', '№?  %d×%d' % (rr['w'], rr['h'])))

    def _draw_final(self, qp, step, scale, left_x, bottom_y):
        """Компактная раскладка всех продуктов (rectpack) с размерами внутри и подписью 1/N."""
        margin = 50
        products = step.get('products', [])
        if not products:
            qp.drawText(int(left_x), int(bottom_y - 20), "Нет продуктов.")
            return
        # Минимальные отступы для более компактной упаковки (мм)
        PACK_PAD_MM = 0
        packed_list = []
        if _HAS_RECTPACK and newPacker:
            total_area = sum((pr['w'] + 1) * (pr['h'] + 1) for pr in products)
            max_w = max(pr['w'] for pr in products)
            max_h = max(pr['h'] for pr in products)
            for mult in [1.0, 1.05, 1.1, 1.2, 1.4, 1.8, 2.2]:
                side = max(max_w, max_h, int(math.sqrt(total_area) * mult), 1)
                packer = newPacker(rotation=False)
                packer.add_bin(side, side)
                for i, pr in enumerate(products):
                    packer.add_rect(int(pr['w']) + PACK_PAD_MM, int(pr['h']) + PACK_PAD_MM, i)
                packer.pack()
                packed_list = packer.rect_list()
                if len(packed_list) >= len(products):
                    break
        # Fallback: simple row if no rectpack or pack failed (компактнее)
        if len(packed_list) < len(products):
            x_cur, y_cur = 0, 0
            row_h = 0
            pad = 2
            packed_list = []
            for i, pr in enumerate(products):
                w, h = pr['w'], pr['h']
                if x_cur + w > 2 * max(pr['w'] for pr in products) and x_cur > 0:
                    x_cur = 0
                    y_cur += row_h + pad
                    row_h = 0
                packed_list.append((0, x_cur, y_cur, w, h, i))
                row_h = max(row_h, h)
                x_cur += w + pad
        # Bounding box of packed layout (in mm)
        if not packed_list:
            return
        pack_w = max(r[1] + r[3] for r in packed_list)
        pack_h = max(r[2] + r[4] for r in packed_list)
        avail_w = self.width() - 2 * margin
        avail_h = self.height() - 2 * margin
        scale = max(0.1, min(avail_w / pack_w, avail_h / pack_h))
        ox = left_x
        oy_bottom = bottom_y
        colors = {'piece': QColor(70, 130, 180), 'business': QColor(72, 187, 120), 'waste': QColor(220, 53, 69)}
        from collections import defaultdict
        piece_keys = [(pr.get('recipient') or '', pr['w'], pr['h']) for pr in products if pr.get('type') == 'piece']
        group_count = defaultdict(int)
        for k in piece_keys:
            group_count[k] += 1
        group_index = defaultdict(int)

        # Мелкие изделия (оба < SMALL_PIECE_MM): только *N на схеме; расшифровка справа
        small_pieces_list = []
        idx_to_star = {}
        for r in packed_list:
            idx = r[5]
            pr = products[idx]
            if pr.get('type') == 'piece':
                w_mm, h_mm = pr['w'], pr['h']
                thresh = _small_piece_threshold_mm()
                if w_mm < thresh or h_mm < thresh:
                    star_num = len(small_pieces_list) + 1
                    small_pieces_list.append((star_num, pr))
                    idx_to_star[idx] = star_num

        PACK_PAD_MM = 0
        qp.setFont(QFont("Arial", 10))
        qp.setPen(QPen(QColor(0, 0, 0), 1))
        for r in packed_list:
            b, x_mm, y_mm, packed_w, packed_h, idx = r[0], r[1], r[2], r[3], r[4], r[5]
            pr = products[idx]
            w_mm, h_mm = pr['w'], pr['h']
            w_px = w_mm * scale
            h_px = h_mm * scale
            left = ox + (x_mm + PACK_PAD_MM / 2) * scale
            top_y = oy_bottom - pack_h * scale + (y_mm + PACK_PAD_MM / 2) * scale
            qp.setBrush(colors.get(pr.get('type'), QColor(200, 200, 200)))
            qp.drawRect(int(left), int(top_y), int(w_px), int(h_px))
            star_num = idx_to_star.get(idx)
            if star_num is not None:
                qp.setPen(QColor(0, 0, 0))
                font_pt = max(8, min(12, int(min(w_px, h_px) / 6)))
                qp.setFont(QFont("Arial", font_pt))
                qp.drawText(int(left), int(top_y), int(w_px), int(h_px), Qt.AlignCenter, "*%d" % star_num)
                continue
            _draw_dim_inside_rect(qp, left, top_y, w_px, h_px, int(w_mm), int(h_mm), 1.0, pr.get('edge_treatment'))
            if pr.get('type') == 'piece':
                k = (pr.get('recipient') or '', pr['w'], pr['h'])
                group_index[k] += 1
                cur, total = group_index[k], group_count[k]
                num_str = "%d/%d" % (cur, total) if total > 1 else ""
            else:
                cur = total = 1
                num_str = ""
            text_lines = [(pr.get('recipient') or '').strip()[:20], "%d×%d" % (pr['w'], pr['h'])]
            if num_str:
                text_lines.append(num_str)
            text_lines = [s for s in text_lines if s]
            safe_left = left + 22
            safe_top = top_y + 6
            safe_w = w_px - 28
            safe_h = h_px - 24
            font_pt = 10
            if safe_w > 12 and safe_h > 12 and text_lines:
                lbl = "\n".join(text_lines)
                for pt in range(min(14, max(8, int(min(safe_w, safe_h) / 4))), 6, -1):
                    qp.setFont(QFont("Arial", pt))
                    fm = QFontMetrics(qp.font())
                    br = fm.boundingRect(0, 0, int(safe_w), int(safe_h), Qt.AlignCenter | Qt.AlignVCenter | Qt.TextWordWrap, lbl)
                    if br.width() <= safe_w and br.height() <= safe_h:
                        font_pt = pt
                        break
                qp.setFont(QFont("Arial", font_pt))
                fm = QFontMetrics(qp.font())
                qp.setPen(QColor(0, 0, 0))
                cx_rect = left + w_px / 2
                cy_rect = top_y + h_px / 2
                line_height = font_pt * 1.35
                block_h = line_height * len(text_lines)
                if h_px > w_px:
                    qp.save()
                    qp.translate(cx_rect, cy_rect)
                    qp.rotate(90)
                    cy0 = (len(text_lines) - 1) * line_height / 2
                    for i, line in enumerate(text_lines):
                        qp.drawText(int(-fm.horizontalAdvance(line) / 2), int(cy0 - i * line_height), line)
                    qp.restore()
                else:
                    cy0 = top_y + (h_px - block_h) / 2 + line_height * 0.8
                    for i, line in enumerate(text_lines):
                        tw = fm.horizontalAdvance(line)
                        qp.drawText(int(left + (w_px - tw) / 2), int(cy0 + i * line_height), line)

        if small_pieces_list:
            legend_x = ox + pack_w * scale + 10
            legend_y0 = oy_bottom - pack_h * scale
            for star_num, pr in small_pieces_list:
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
                w_mm, h_mm = pr['w'], pr['h']
                qp.save()
                qp.translate(box_x + 10, box_y + LEGEND_BOX_H / 2)
                qp.rotate(-90)
                qp.drawText(int(-8), int(4), str(h_mm))
                qp.restore()
                qp.drawText(int(box_x + LEGEND_BOX_W / 2 - 8), int(box_y + LEGEND_BOX_H - 4), str(w_mm))
                rec = (pr.get('recipient') or '').strip()[:14]
                qp.drawText(int(box_x + 2), int(box_y + 12), rec if rec else "—")

        qp.setFont(QFont("Arial", 11))
        qp.drawText(int(left_x), int(bottom_y + 14), "Итог — компактная раскладка.")
