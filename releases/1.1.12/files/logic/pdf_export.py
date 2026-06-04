"""Export cutting layout to PDF: B/W for printing, sheet contour, gray/hatch, dimensions inside (except sheet)."""
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import os
import sys
import math

# Шрифт с поддержкой кириллицы для русского текста в PDF
PDF_FONT = "Helvetica"
PDF_FONT_BOLD = "Helvetica-Bold"
try:
    if sys.platform == "win32":
        font_path = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf")
    else:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if os.path.isfile(font_path):
        pdfmetrics.registerFont(TTFont("ArialCyr", font_path))
        PDF_FONT = "ArialCyr"
        PDF_FONT_BOLD = "ArialCyr"
except Exception:
    pass


def _dim_vertical(c, x_dim_pt, y_top_pt, y_bottom_pt, text, gap_pt=4 * mm, text_rotated_90=True):
    """Высота: выносные от границ, размерная линия и текст. Число — со стороны листа (справа от линии)."""
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.4)
    x_left = x_dim_pt - gap_pt
    c.line(x_left, y_bottom_pt, x_dim_pt, y_bottom_pt)
    c.line(x_left, y_top_pt, x_dim_pt, y_top_pt)
    c.line(x_dim_pt, y_bottom_pt, x_dim_pt, y_top_pt)
    c.setFont(PDF_FONT, 8)
    c.setFillColor(colors.black)
    cy = (y_bottom_pt + y_top_pt) / 2
    if text_rotated_90:
        c.saveState()
        # Текст справа от размерной линии (в сторону листа), повёрнут 90°
        c.translate(x_dim_pt + 2 * mm, cy)
        c.rotate(-90)
        c.drawString(-2 * mm, 2, str(text))
        c.restoreState()
    else:
        c.drawString(x_dim_pt + 2, cy - 2, str(text))


def _dim_horizontal(c, x_left_pt, x_right_pt, y_bottom_edge_pt, text, gap_pt=4 * mm):
    """Ширина: выносные вниз, размерная линия и текст. Число под линией, по центру."""
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.4)
    y_dim = y_bottom_edge_pt - gap_pt
    c.line(x_left_pt, y_bottom_edge_pt, x_left_pt, y_dim)
    c.line(x_right_pt, y_bottom_edge_pt, x_right_pt, y_dim)
    c.line(x_left_pt, y_dim, x_right_pt, y_dim)
    c.setFont(PDF_FONT, 8)
    c.setFillColor(colors.black)
    mid_x = (x_left_pt + x_right_pt) / 2
    c.drawString(mid_x - 2 * mm, y_dim - 4, str(text))


def _dim_vertical_left_sheet(c, x_sheet_left_pt, y_bottom_pt, y_top_pt, value_mm, gap_pt=6 * mm):
    """Высота листа: вынос СЛЕВА от листа — размерная линия и только число (без слова «Высота»)."""
    c.setStrokeColor(colors.black)
    c.setLineWidth(1)
    x_dim_pt = x_sheet_left_pt - gap_pt
    c.line(x_sheet_left_pt, y_bottom_pt, x_dim_pt, y_bottom_pt)
    c.line(x_sheet_left_pt, y_top_pt, x_dim_pt, y_top_pt)
    c.line(x_dim_pt, y_bottom_pt, x_dim_pt, y_top_pt)
    c.setFont(PDF_FONT, 10)
    c.setFillColor(colors.black)
    cy = (y_bottom_pt + y_top_pt) / 2
    c.saveState()
    c.translate(x_dim_pt - 4 * mm, cy)
    c.rotate(-90)
    c.drawString(-2 * mm, 3, str(int(value_mm)))
    c.restoreState()


def _dim_horizontal_inside_sheet(c, x_left_pt, x_right_pt, y_bottom_pt, y_dim_pt, text):
    """Ширина листа: размерная линия и текст внутри листа (над нижним краем)."""
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    c.line(x_left_pt, y_bottom_pt, x_left_pt, y_dim_pt)
    c.line(x_right_pt, y_bottom_pt, x_right_pt, y_dim_pt)
    c.line(x_left_pt, y_dim_pt, x_right_pt, y_dim_pt)
    c.setFont(PDF_FONT, 10)
    c.setFillColor(colors.black)
    mid_x = (x_left_pt + x_right_pt) / 2
    c.drawString(mid_x - 2 * mm, y_dim_pt - 4, str(text))


def _dim_horizontal_sheet_with_label(c, x_left_pt, x_right_pt, y_bottom_edge_pt, text, gap_pt=5 * mm):
    """Ширина листа: выносы вниз, размерная линия; подпись — только число (как для высоты)."""
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    y_dim = y_bottom_edge_pt - gap_pt
    c.line(x_left_pt, y_bottom_edge_pt, x_left_pt, y_dim)
    c.line(x_right_pt, y_bottom_edge_pt, x_right_pt, y_dim)
    c.line(x_left_pt, y_dim, x_right_pt, y_dim)
    c.setFont(PDF_FONT, 10)
    c.setFillColor(colors.black)
    mid_x = (x_left_pt + x_right_pt) / 2
    c.drawString(mid_x - 2 * mm, y_dim + 4, str(int(text)))


def _dim_inside_rect(c, xL_pt, yT_pt, w_pt, h_pt, w_mm, h_mm):
    """Размеры внутри прямоугольника: ширина внизу по центру, высота слева (текст 90°). Числа подписаны явно."""
    c.setStrokeColor(colors.black)
    c.setFillColor(colors.black)
    c.setLineWidth(0.3)
    c.setFont(PDF_FONT, 7)
    cx = xL_pt + w_pt / 2
    yB_pt = yT_pt + h_pt
    c.drawString(cx - 1.5 * mm, yB_pt - 2.5 * mm, str(int(w_mm)))
    c.saveState()
    c.translate(xL_pt + 3, yT_pt + h_pt / 2)
    c.rotate(-90)
    c.drawString(-1.5 * mm, 2, str(int(h_mm)))
    c.restoreState()


def _draw_hatch_45(c, xL_pt, yT_pt, w_pt, h_pt, step_pt=15, line_width_pt=0.5):
    """Штриховка под 45° внутри прямоугольника (для мусора). Без clip (Canvas не имеет clip)."""
    if w_pt <= 0 or h_pt <= 0:
        return
    xR_pt = xL_pt + w_pt
    yB_pt = yT_pt + h_pt
    c.setStrokeColor(colors.black)
    c.setLineWidth(line_width_pt)
    n_min = -int(h_pt / step_pt) - 2
    n_max = int(w_pt / step_pt) + 3
    for k in range(n_min, n_max):
        x0 = xL_pt + k * step_pt
        y0 = yT_pt
        # Линия 45°: (x0 + t, y0 + t), t in [0, L]. Обрезаем по rect [xL, xR] x [yT, yB]
        t_min = max(0, xL_pt - x0, yT_pt - y0)
        t_max = min(w_pt + h_pt + step_pt * 2, xR_pt - x0, yB_pt - y0)
        if t_min < t_max:
            c.line(x0 + t_min, y0 + t_min, x0 + t_max, y0 + t_max)


def _small_piece_threshold_mm():
    """Порог мелкой детали (мм): если хотя бы одна сторона меньше — на схеме только *N. По умолчанию 200."""
    try:
        from user_settings import get_small_piece_mm
        return get_small_piece_mm()
    except Exception:
        return 200


def _draw_layout_on_canvas(
    c,
    layout,
    scale,
    origin_x,
    origin_y,
    sheet_height_mm,
    business_start=1,
    remnant_display_numbers=None,
    remnant_start_index=0,
    global_piece_start=1,
    k_base=None,
    compact_piece_captions=False,
):
    """Ч/б для печати. Нумерация изделий — сквозная 1, 2, 3… по всему заказу. Возвращает (next_business_no, next_remnant_index, small_pieces_list, next_global_piece_index).
    compact_piece_captions — без ФИО получателя на изделии (только размер и номер), для веб-сервиса производства."""
    flip_y = lambda y: sheet_height_mm - y
    gap = 4 * mm
    sw_pt = layout['sheet_width'] * scale
    sh_pt = layout['sheet_height'] * scale
    c.setFillColor(colors.white)

    # Исходный лист — контур; ширина — вынос вниз с подписью «Рез», высота — вынос слева, только число
    c.setStrokeColor(colors.black)
    c.setLineWidth(1.2)
    c.rect(origin_x, origin_y, sw_pt, sh_pt, fill=0, stroke=1)
    gap_sheet = 5 * mm
    _dim_horizontal_sheet_with_label(c, origin_x, origin_x + sw_pt, origin_y, layout['sheet_width'], gap_sheet)
    _dim_vertical_left_sheet(c, origin_x, origin_y, origin_y + sh_pt, layout.get('sheet_height', 0))

    # Неделовые отходы — контур, без заливки; штриховка 45° линиями (явно без fill)
    for r in layout.get('waste_rects', []):
        x_mm = r['x']
        y_mm = flip_y(r['y'] + r['h'])
        w_mm, h_mm = r['w'], r['h']
        xL = origin_x + x_mm * scale
        yT = origin_y + y_mm * scale
        w_pt = w_mm * scale
        h_pt = h_mm * scale
        c.setFillColor(colors.white)
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.4)
        c.rect(xL, yT, w_pt, h_pt, fill=0, stroke=1)
        _draw_hatch_45(c, xL, yT, w_pt, h_pt, step_pt=18, line_width_pt=0.4)
        _dim_inside_rect(c, xL, yT, w_pt, h_pt, int(w_mm), int(h_mm))

    # Деловые остатки — светло-серый + подпись номера (уникальный номер остатка из склада или порядковый)
    business_rects = layout.get('business_rects', [])
    next_business_no = business_start
    for i, r in enumerate(business_rects):
        x_mm = r['x']
        y_mm = flip_y(r['y'] + r['h'])
        w_mm, h_mm = r['w'], r['h']
        xL = origin_x + x_mm * scale
        yT = origin_y + y_mm * scale
        w_pt = w_mm * scale
        h_pt = h_mm * scale
        c.setFillColor(colors.HexColor('#E0E0E0'))
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.3)
        c.rect(xL, yT, w_pt, h_pt, fill=1, stroke=1)
        _dim_inside_rect(c, xL, yT, w_pt, h_pt, int(w_mm), int(h_mm))
        if remnant_display_numbers and remnant_start_index + i < len(remnant_display_numbers) and remnant_display_numbers[remnant_start_index + i]:
            sticker_no = remnant_display_numbers[remnant_start_index + i]
        else:
            sticker_no = next_business_no
        next_business_no += 1
        c.setFillColor(colors.black)
        font_size = max(6, min(14, int(min(w_pt, h_pt) / 5)))
        c.setFont(PDF_FONT_BOLD, font_size)
        txt = "№ %s" % sticker_no
        cx = xL + w_pt / 2
        cy = yT + h_pt / 2
        if h_pt > w_pt:
            c.saveState()
            c.translate(cx, cy)
            c.rotate(90)
            c.drawCentredString(0, -font_size / 2, txt)
            c.restoreState()
        else:
            c.drawCentredString(cx, cy - font_size / 2, txt)
        c.setFillColor(colors.HexColor('#E0E0E0'))

    # Линии реза (сохранённые направления H/V) — чёрные
    for seg in layout.get('cut_segments', []):
        t = seg.get('type') or 'H'
        pos = float(seg.get('pos', 0))
        lo = float(seg.get('extent_lo', 0))
        hi = float(seg.get('extent_hi', 0))
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.8)
        if t == 'H':
            y_mm = flip_y(pos)
            x1_pt = origin_x + lo * scale
            x2_pt = origin_x + hi * scale
            y_pt = origin_y + y_mm * scale
            c.line(x1_pt, y_pt, x2_pt, y_pt)
        else:
            x_pt = origin_x + pos * scale
            y1_mm = flip_y(hi)
            y2_mm = flip_y(lo)
            y1_pt = origin_y + y1_mm * scale
            y2_pt = origin_y + y2_mm * scale
            c.line(x_pt, y1_pt, x_pt, y2_pt)

    # Нормализуем pieces: всегда список dict (поддержка старых сохранённых данных и вложенной структуры)
    raw_pieces = layout.get('pieces') or layout.get('piece_list') or []
    if isinstance(raw_pieces, dict):
        raw_pieces = list(raw_pieces.values()) if raw_pieces else []
    pieces = [p if isinstance(p, dict) else {} for p in (list(raw_pieces) if raw_pieces else [])]
    thresh = _small_piece_threshold_mm()

    # Сквозная нумерация изделий: global_piece_start, global_piece_start+1, …
    piece_counter = global_piece_start
    # Первый проход: присвоить глобальные номера мелким деталям и собрать small_pieces_list
    small_pieces_list = []
    small_num_by_idx = {}
    for idx, p in enumerate(pieces):
        try:
            w_mm = int(p.get('w') or p.get('width_mm') or 0)
            h_mm = int(p.get('h') or p.get('height_mm') or 0)
        except (TypeError, ValueError):
            w_mm = h_mm = 0
        if (w_mm <= 0 and h_mm <= 0):
            continue
        if w_mm < thresh or h_mm < thresh:
            small_num_by_idx[idx] = piece_counter
            rec_small = '' if compact_piece_captions else (p.get('recipient') or '').strip()
            small_pieces_list.append((piece_counter, w_mm, h_mm, rec_small))
            piece_counter += 1

    # Изделия — серый, размеры внутри; подпись: получатель, размер, порядковый номер (1, 2, 3…)
    DIM_LEFT_PT = 18
    DIM_BOTTOM_PT = 14
    EDGE_LETTERS = {'grinding': 'Ш', 'polishing': 'П', 'facet': 'Ф'}

    def _edge_display(et, side):
        et = et or {}
        v = et.get(side)
        if v == 'facet':
            mm = et.get('facet_mm')
            return "Ф%s" % (int(mm) if mm is not None else 15)
        return EDGE_LETTERS.get(v, '') if v else ''

    c.setStrokeColor(colors.black)
    c.setLineWidth(0.4)
    for idx, p in enumerate(pieces):
        try:
            w_mm = int(p.get('w') or p.get('width_mm') or 0)
            h_mm = int(p.get('h') or p.get('height_mm') or 0)
        except (TypeError, ValueError):
            w_mm = h_mm = 0
        if w_mm <= 0 and h_mm <= 0:
            continue
        is_small = w_mm < thresh or h_mm < thresh
        if is_small:
            star_num = small_num_by_idx.get(idx, piece_counter)
            piece_counter += 1
        else:
            piece_num = piece_counter
            piece_counter += 1
        c.setFillColor(colors.HexColor('#888888'))
        x_mm = float(p.get('x', 0) or 0)
        y_mm = flip_y((float(p.get('y', 0) or 0) + h_mm))
        xL = origin_x + x_mm * scale
        yT = origin_y + y_mm * scale
        w_pt = w_mm * scale
        h_pt = h_mm * scale
        c.rect(xL, yT, w_pt, h_pt, fill=1, stroke=1)
        if is_small:
            c.setFont(PDF_FONT_BOLD, max(5, min(12, int(min(w_pt, h_pt) / 3))))
            c.setFillColor(colors.black)
            c.drawCentredString(xL + w_pt / 2, yT + h_pt / 2 - 2, "*%d" % star_num)
            c.setFillColor(colors.HexColor('#888888'))
            continue
        # Размеры и буквы кромок; подпись: получатель, размер, порядковый номер
        et = p.get('edge_treatment') or {}
        dim_font = 7
        c.setFont(PDF_FONT, dim_font)
        c.setFillColor(colors.black)
        left_letter = _edge_display(et, 'left')
        left_line = str(int(h_mm)) + (" " + left_letter if left_letter else '')
        c.saveState()
        c.translate(xL + 2 * mm, yT + h_pt / 2)
        c.rotate(-90)
        c.drawString(-1 * mm, 2, left_line)
        c.restoreState()
        bottom_letter = _edge_display(et, 'bottom')
        bottom_line = str(int(w_mm)) + (" " + bottom_letter if bottom_letter else '')
        c.drawCentredString(xL + w_pt / 2, yT + 2 * mm, bottom_line)
        c.setFillColor(colors.HexColor('#888888'))
        recipient = (p.get('recipient') or '').strip()
        line2 = "%d×%d" % (w_mm, h_mm)
        disp_num = (k_base + piece_num - 1) if (k_base is not None) else piece_num
        num_str = "K%s" % str(disp_num)
        safe_w = max(8, w_pt - DIM_LEFT_PT - 6)
        safe_h = max(12, h_pt - DIM_BOTTOM_PT - 6)
        font_size = 6
        for fs in range(8, 4, -1):
            n_lines = 2 if compact_piece_captions else 4
            if fs * n_lines <= safe_h and c.stringWidth(line2, PDF_FONT, fs) <= safe_w:
                font_size = fs
                break
        c.setFont(PDF_FONT, font_size)
        if compact_piece_captions:
            text_lines = [line2, num_str]
        else:
            recipient_lines = []
            for word in recipient.replace('\n', ' ').split():
                if not recipient_lines:
                    recipient_lines.append(word)
                else:
                    trial = recipient_lines[-1] + ' ' + word
                    if c.stringWidth(trial, PDF_FONT, font_size) <= safe_w:
                        recipient_lines[-1] = trial
                    else:
                        recipient_lines.append(word)
                if len(recipient_lines) >= 2:
                    break
            if not recipient_lines:
                recipient_lines = ['']
            recipient_lines = [s[:24] for s in recipient_lines[:2]]
            text_lines = recipient_lines + [line2] + [num_str]
        line_height = font_size * 1.35
        block_h = line_height * len(text_lines)
        cx = xL + w_pt / 2
        cy_rect = yT + h_pt / 2
        c.setFillColor(colors.black)
        if h_pt > w_pt:
            c.saveState()
            c.translate(cx, cy_rect)
            c.rotate(90)
            cy0 = (len(text_lines) - 1) * line_height / 2
            for i, line in enumerate(text_lines):
                c.drawCentredString(0, cy0 - i * line_height, line)
            c.restoreState()
        else:
            cy0 = yT + (h_pt - block_h) / 2 + block_h - line_height * 0.5
            for i, line in enumerate(text_lines):
                c.drawCentredString(cx, cy0 - i * line_height, line)
    next_remnant_index = remnant_start_index + len(business_rects)
    return (next_business_no, next_remnant_index, small_pieces_list, piece_counter)


def generate_cutting_pdf(layouts, order_info, filepath):
    """Один лист материала — одна страница PDF. Нумерация изделий сквозная: 1, 2, 3… по всему заказу.
    order_info['compact_cut_pdf'] — без имени клиента в шапке и без получателя на изделиях (веб-производство)."""
    c = canvas.Canvas(filepath, pagesize=A4)
    compact = bool((order_info or {}).get('compact_cut_pdf'))
    margin = 15
    business_next = 1
    remnant_start = 0
    global_piece_index = 1
    try:
        k_base = int(order_info.get('k_number')) if (order_info or {}).get('k_number') is not None else None
    except (TypeError, ValueError):
        k_base = None
    oid = order_info.get('order_id') or order_info.get('id')
    try:
        remnant_display_numbers = __import__('db.models', fromlist=['get_remnant_display_numbers_by_order_id']).get_remnant_display_numbers_by_order_id(oid) if oid else []
    except Exception:
        remnant_display_numbers = []

    for i, lay in enumerate(layouts):
        if i > 0:
            c.showPage()
            c.setPageSize(A4)
        width_pt, height_pt = A4
        sw, sh = lay['sheet_width'], lay['sheet_height']
        # Компактный заголовок на каждой странице
        if i == 0:
            header_bottom = height_pt - 52
            c.setFont(PDF_FONT_BOLD, 12)
            c.drawString(margin, height_pt - 18, "Карты раскроя")
            c.setFont(PDF_FONT, 9)
            order_id = order_info.get('order_id') or order_info.get('id')
            created = order_info.get('created_at') or ''
            if hasattr(created, 'strftime'):
                created = created.strftime('%d.%m.%Y')
            if compact:
                c.drawString(margin, height_pt - 30, "Заказ %s от %s  |  Листов: %d" % (order_id, created, len(layouts)))
            else:
                client = order_info.get('client_name') or '—'
                c.drawString(margin, height_pt - 30, "Заказ %s от %s  |  %s  |  Листов: %d" % (order_id, created, client, len(layouts)))
            materials = order_info.get('materials') or order_info.get('material')
            if not materials and layouts:
                materials = layouts[0].get('material') or '—'
            if isinstance(materials, list):
                materials = ", ".join(str(m) for m in materials)
            if not materials:
                materials = '—'
            c.drawString(margin, height_pt - 42, "Материал: %s" % str(materials)[:60])
            thicknesses = set()
            for lay_t in layouts:
                t = lay_t.get('thickness_mm')
                if t is not None:
                    thicknesses.add(int(t))
            if thicknesses:
                thick_str = ", ".join("%d мм" % t for t in sorted(thicknesses))
                c.drawString(margin, height_pt - 52, "Толщина: %s" % thick_str)
                header_bottom = height_pt - 62
            else:
                header_bottom = height_pt - 52
        else:
            header_bottom = height_pt - 22
            order_id = order_info.get('order_id') or order_info.get('id')
            c.setFont(PDF_FONT, 9)
            c.drawString(margin, height_pt - 16, "Заказ %s  |  Лист %d из %d" % (order_id, i + 1, len(layouts)))

        # Вся область под заголовком — под раскладку (максимальный масштаб, без ограничения 1.2)
        avail_w = width_pt - 2 * margin - 40
        avail_h = header_bottom - margin - 35  # место под подпись листа и мелкие детали
        scale = min(avail_w / max(1, sw), avail_h / max(1, sh))
        origin_x = margin + 25
        origin_y = header_bottom - margin - sh * scale

        c.setFont(PDF_FONT, 9)
        mat_line = (lay.get('material') or '')[:30]
        sheet_type = lay.get('sheet_type') or 'full'
        sheet_id = lay.get('sheet_id')
        if sheet_type == 'remnant' and sheet_id is not None:
            try:
                rem = __import__('db.models', fromlist=['get_remnant_by_id']).get_remnant_by_id(sheet_id)
                label_no = rem.get('label_number') if rem else sheet_id
                sheet_source = "Остаток № %s" % label_no
            except Exception:
                sheet_source = "Остаток № %s" % sheet_id
        else:
            sheet_source = "Целый лист со склада"
        sheet_cap = "Лист %d. %d × %d мм" % (i + 1, sw, sh)
        if mat_line:
            sheet_cap += "  %s" % mat_line
        thick = lay.get('thickness_mm')
        if thick is not None:
            sheet_cap += "  |  %d мм" % int(thick)
        # Размер листа и источник (остаток № / целый лист) в одной строке с отступом
        sheet_line = sheet_cap + "   " + sheet_source
        c.drawString(origin_x, origin_y + sh * scale + 4 * mm, sheet_line)
        business_next, remnant_start, small_pieces_list, global_piece_index = _draw_layout_on_canvas(
            c, lay, scale, origin_x, origin_y, sh,
            business_start=business_next,
            remnant_display_numbers=remnant_display_numbers if remnant_display_numbers else None,
            remnant_start_index=remnant_start,
            global_piece_start=global_piece_index,
            k_base=k_base,
            compact_piece_captions=compact,
        )
        y_pos = origin_y - 12
        if small_pieces_list:
            c.setFont(PDF_FONT, 9)
            c.setFillColor(colors.black)
            y_pos -= 10
            c.drawString(origin_x, y_pos, "Мелкие детали:")
            y_pos -= 12
            for star_num, w_mm, h_mm, recipient in small_pieces_list:
                line = "*%d — %d×%d мм, ширина %d мм, высота %d мм" % (star_num, int(w_mm), int(h_mm), int(w_mm), int(h_mm))
                if recipient:
                    line += " — %s" % (recipient[:35])
                c.drawString(origin_x + 4 * mm, y_pos, line)
                y_pos -= 10
    c.save()


def _pdf_layout_k_display_numbers(layout, piece_counter_start, k_base):
    """Номера K для крупных изделий на одном листе, как во втором проходе _draw_layout_on_canvas.
    Возвращает (список int номеров после «K», следующий глобальный piece_counter)."""
    raw_pieces = layout.get("pieces") or layout.get("piece_list") or []
    if isinstance(raw_pieces, dict):
        raw_pieces = list(raw_pieces.values()) if raw_pieces else []
    pieces = [p if isinstance(p, dict) else {} for p in (list(raw_pieces) if raw_pieces else [])]
    thresh = _small_piece_threshold_mm()
    piece_counter = int(piece_counter_start)
    for p in pieces:
        try:
            w_mm = int(p.get("w") or p.get("width_mm") or 0)
            h_mm = int(p.get("h") or p.get("height_mm") or 0)
        except (TypeError, ValueError):
            w_mm = h_mm = 0
        if w_mm <= 0 and h_mm <= 0:
            continue
        if w_mm < thresh or h_mm < thresh:
            piece_counter += 1
    k_nums = []
    for p in pieces:
        try:
            w_mm = int(p.get("w") or p.get("width_mm") or 0)
            h_mm = int(p.get("h") or p.get("height_mm") or 0)
        except (TypeError, ValueError):
            w_mm = h_mm = 0
        if w_mm <= 0 and h_mm <= 0:
            continue
        is_small = w_mm < thresh or h_mm < thresh
        if is_small:
            piece_counter += 1
        else:
            piece_num = piece_counter
            piece_counter += 1
            disp = (k_base + piece_num - 1) if (k_base is not None) else piece_num
            k_nums.append(int(disp))
    return k_nums, piece_counter


def pdf_k_display_numbers_for_order_layouts(layouts, k_base):
    """Все номера K как на напечатанных картах раскроя по списку листов подряд (только крупные изделия, не *мелкие)."""
    try:
        kb = int(k_base) if k_base is not None else None
    except (TypeError, ValueError):
        kb = None
    pc = 1
    out = set()
    for layout in layouts or []:
        if not isinstance(layout, dict):
            continue
        nums, pc = _pdf_layout_k_display_numbers(layout, pc, kb)
        for n in nums:
            out.add(int(n))
    return out
