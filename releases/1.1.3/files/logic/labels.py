"""Генерация этикеток в PDF: строго 100×50 мм, все элементы внутри границ.
   Остаток: верх — логотип и № по центру; слева/центр — размер и материал с толщиной; справа QR.
   Изделие (K): как MIRROR_CUT _OLD — логотипы крупнее (×1.5) с отступом 5 мм от углов; № K по центру; низ — материал, размер, клиент, дата; кромка — квадрат справа. Логотипы: web_qr/logo и logo в корне (logo_main.png), при необходимости FINAL_WINDOW/logo."""
import importlib.util
import os
import sys
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import io

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)
from app_paths import get_resource_dir
try:
    from logic.qr_utils import make_remnant_qr_image
except Exception:
    try:
        from qr_utils import make_remnant_qr_image
    except Exception:
        _qr_path = os.path.join(_PROJECT_ROOT, "logic", "qr_utils.py")
        if not os.path.isfile(_qr_path):
            raise ImportError("Не найден qr_utils (%s)" % _qr_path)
        _spec = importlib.util.spec_from_file_location("_labels_embed_qr_utils", _qr_path)
        _qr_mod = importlib.util.module_from_spec(_spec)
        assert _spec.loader
        _spec.loader.exec_module(_qr_mod)
        make_remnant_qr_image = _qr_mod.make_remnant_qr_image

LABEL_W_MM = 100
LABEL_H_MM = 50
LABEL_W_PT = LABEL_W_MM * mm
LABEL_H_PT = LABEL_H_MM * mm

EDGE_LETTERS = {'grinding': 'Ш', 'polishing': 'П', 'facet': 'Ф'}
BRAND_NAME = "Arsenal"

_LABEL_FONT = "Helvetica"
_LABEL_FONT_BOLD = "Helvetica-Bold"
try:
    if sys.platform == "win32":
        font_path = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf")
    else:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if os.path.isfile(font_path):
        pdfmetrics.registerFont(TTFont("ArialCyrLabel", font_path))
        _LABEL_FONT = "ArialCyrLabel"
        _LABEL_FONT_BOLD = "ArialCyrLabel"
except Exception:
    pass


def _resource_roots_for_logo():
    """Порядок: web_qr (веб-сервис), каталог ресурсов exe, корень проекта."""
    roots = []
    wq = os.path.join(_PROJECT_ROOT, "web_qr")
    if os.path.isdir(wq):
        roots.append(wq)
    try:
        r0 = get_resource_dir()
        if r0 and r0 not in roots:
            roots.append(r0)
    except Exception:
        pass
    if _PROJECT_ROOT and _PROJECT_ROOT not in roots:
        roots.append(_PROJECT_ROOT)
    return roots


def get_logo_path():
    """Путь к логотипу (logo/logo.png в корне проекта или в ресурсах exe)."""
    for root in _resource_roots_for_logo():
        p = os.path.join(root, "logo", "logo.png")
        if os.path.isfile(p):
            return p
    return os.path.join(_PROJECT_ROOT, "logo", "logo.png")


def get_logo_main_path():
    """Сначала logo/logo_main.png (или main.png) в web_qr, ресурсах exe и корне проекта; затем FINAL_WINDOW (совместимость)."""
    for name in ("logo_main.png", "main.png"):
        for root in _resource_roots_for_logo():
            path = os.path.join(root, "logo", name)
            if os.path.isfile(path):
                return path
    fw = os.path.join(_PROJECT_ROOT, "FINAL_WINDOW", "logo", "logo_main.png")
    if os.path.isfile(fw):
        return fw
    return None


def get_logo2_path():
    """Второй логотип (правый верх K): сначала logo/logo_2.png в web_qr и корне, затем FINAL_WINDOW/logo."""
    for name in ("logo_2.png", "logo2.png"):
        for root in _resource_roots_for_logo():
            path = os.path.join(root, "logo", name)
            if os.path.isfile(path):
                return path
    for name in ("logo_2.png", "logo2.png"):
        fw = os.path.join(_PROJECT_ROOT, "FINAL_WINDOW", "logo", name)
        if os.path.isfile(fw):
            return fw
    return None


def _edge_letter(edge_treatment, side):
    """Буква обработки кромки для стороны; для фацета — «Ф N» как в MIRROR_CUT _OLD."""
    if not edge_treatment:
        return ""
    v = edge_treatment.get(side)
    if v == 'facet':
        mm = edge_treatment.get('facet_mm')
        return "Ф %s" % (int(mm) if mm is not None else 15)
    return EDGE_LETTERS.get(v, "") if v else ""


# Квадрат обработки кромок: фиксированный размер 15×15 мм
_EDGE_RECT_MM = 15
# Целевой размер логотипа (большая сторона), мм — верстка текста как при 15 мм полосе
_LABEL_LOGO_TARGET_MM = 15 * 1.5
_LABEL_LOGO_CORNER_MM = 5

def _draw_remnant_label(c, x_pt, y_pt, w_pt, h_pt, unique_number, name, height_mm, width_mm, label_number=None, edge_treatment=None, display_number=None, thickness_mm=None, cut_date=None):
    """Этикетка остатка — как MIRROR_CUT _OLD: верх логотип слева, № по центру; центр — размер и материал; справа QR."""
    _draw_label_frame(c, x_pt, y_pt, w_pt, h_pt)
    top_zone_h = 15 * mm
    content_h = h_pt - top_zone_h
    top_zone_y0 = y_pt + h_pt - top_zone_h
    top_zone_cy = top_zone_y0 + top_zone_h / 2
    logo_sz_pt = _LABEL_LOGO_TARGET_MM * mm
    logo_inset = _LABEL_LOGO_CORNER_MM * mm
    qr_margin = 2 * mm
    qr_sz_pt = 28 * mm
    qr_x = x_pt + w_pt - qr_sz_pt - qr_margin
    qr_y = y_pt + (h_pt - qr_sz_pt) / 2
    qr_img = make_remnant_qr_image(unique_number, size_px=280)
    buf = io.BytesIO()
    qr_img.save(buf, format='PNG')
    buf.seek(0)
    c.drawImage(ImageReader(buf), qr_x, qr_y, width=qr_sz_pt, height=qr_sz_pt)
    text_right = qr_x - 2 * mm
    cx = x_pt + (text_right - x_pt) / 2
    logo_path = get_logo_main_path() or get_logo_path()
    if os.path.isfile(logo_path):
        try:
            img = ImageReader(logo_path)
            iw, ih = img.getSize()
            if iw and ih:
                scale = min(logo_sz_pt / iw, logo_sz_pt / ih)
                lw, lh = iw * scale, ih * scale
                lx = x_pt + logo_inset
                ly = y_pt + h_pt - logo_inset - lh
                c.drawImage(img, lx, ly, width=lw, height=lh)
        except Exception:
            pass
    display_no = str(display_number) if display_number is not None else (str(label_number) if label_number is not None else unique_number)
    c.setFont(_LABEL_FONT_BOLD, 20)
    c.drawCentredString(x_pt + w_pt / 2, top_zone_cy - 0.5 * mm, "№ %s" % display_no)
    line_gap = 4 * mm
    block_raise = 4 * mm
    mid_y = y_pt + content_h / 2 + block_raise
    text_max_w = max(20 * mm, (text_right - x_pt) - 6 * mm)
    c.setFont(_LABEL_FONT_BOLD, 14)
    c.drawCentredString(cx, mid_y + line_gap / 2, "%d × %d" % (int(height_mm or 0), int(width_mm or 0)))
    mat_text = (name or "").strip()
    if thickness_mm is not None and int(thickness_mm) > 0:
        mat_text = ("%s %s мм" % (mat_text, int(thickness_mm))).strip() if mat_text else "%s мм" % int(thickness_mm)
    mat_lines, mat_font = _material_lines_for_remnant_label(c, mat_text, text_max_w)
    mat_line_gap = 3.2 * mm
    y_mat = mid_y - line_gap / 2
    if len(mat_lines) == 2:
        y_mat = mid_y - mat_line_gap / 2
    c.setFont(_LABEL_FONT_BOLD, mat_font)
    for i, ln in enumerate(mat_lines):
        c.drawCentredString(cx, y_mat - i * mat_line_gap, ln)
    date_text = _format_cut_date(cut_date)
    if date_text:
        c.setFont(_LABEL_FONT, 12)
        below_mat = len(mat_lines) * mat_line_gap if mat_lines else line_gap / 2
        date_y = mid_y - line_gap / 2 - below_mat - 2 * mm
        c.drawCentredString(cx, date_y, date_text)


def _has_any_edge(edge_treatment):
    if not edge_treatment:
        return False
    return any(edge_treatment.get(s) for s in ('top', 'bottom', 'left', 'right'))


def _draw_vertical_edge_letter(c, cx, cy, letter, font_name, font_size, side):
    """Подпись обработки у левой/правой стороны квадрата — вертикально (90°), чтобы влезала «Ф 15» и т.п."""
    if not letter:
        return
    c.saveState()
    c.setFont(font_name, font_size)
    tw = c.stringWidth(letter, font_name, font_size)
    c.translate(cx, cy)
    if side == "left":
        c.rotate(90)
    else:
        c.rotate(-90)
    c.drawString(-tw / 2.0, -font_size * 0.35, letter)
    c.restoreState()


def _draw_label_frame(c, x_pt, y_pt, w_pt, h_pt):
    """Чёрная рамка 1 мм по контуру этикетки (для визуального контроля размеров)."""
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(1 * mm)
    c.rect(x_pt, y_pt, w_pt, h_pt)


def _format_order_date(order_date):
    """Форматирование даты заказа для этикетки."""
    if order_date is None:
        return ""
    s = str(order_date)
    if len(s) >= 10:
        return s[:10]  # YYYY-MM-DD
    return s


def _split_text_to_lines(c, text, font_name, font_size, max_width, max_lines=2):
    """Разбить длинную строку на 1–2 строки по ширине (для этикеток склада), без «…»."""
    text = (text or "").strip()
    if not text or max_lines < 1:
        return []
    c.setFont(font_name, font_size)
    if c.stringWidth(text, font_name, font_size) <= max_width:
        return [text]
    words = text.split()
    if not words:
        return [text]
    lines: list = []
    idx = 0
    while idx < len(words) and len(lines) < max_lines:
        current = ""
        while idx < len(words):
            trial = ("%s %s" % (current, words[idx])).strip() if current else words[idx]
            if c.stringWidth(trial, font_name, font_size) <= max_width:
                current = trial
                idx += 1
            else:
                break
        if current:
            lines.append(current)
        elif idx < len(words):
            lines.append(words[idx])
            idx += 1
    return lines


def _material_lines_for_remnant_label(c, mat_text, text_max_w):
    """До двух строк материала на этикетке склада; при необходимости чуть уменьшаем шрифт."""
    mat_text = (mat_text or "").strip()
    if not mat_text:
        return [], 12
    for fs in (12, 11, 10):
        lines = _split_text_to_lines(c, mat_text, _LABEL_FONT_BOLD, fs, text_max_w, max_lines=2)
        if not lines:
            continue
        if all(c.stringWidth(ln, _LABEL_FONT_BOLD, fs) <= text_max_w for ln in lines):
            return lines, fs
    lines = _split_text_to_lines(c, mat_text, _LABEL_FONT_BOLD, 10, text_max_w, max_lines=2)
    return (lines or [mat_text]), 10


def _format_cut_date(date_val):
    """Дата раскроя для этикетки остатка: DD.MM.YYYY."""
    if date_val is None:
        return ""
    s = str(date_val).strip()
    if len(s) >= 10:
        y, m, d = s[:4], s[5:7], s[8:10]
        if y.isdigit() and m.isdigit() and d.isdigit():
            return "%s.%s.%s" % (d, m, y)
    return s[:10] if len(s) >= 10 else s


def _draw_piece_label(c, x_pt, y_pt, w_pt, h_pt, height_mm, width_mm, edge_treatment, material_name=None, order_k_number=None,
                      client_name=None, order_date=None, thickness_mm=None, piece_number=None, piece_display_number=None,
                      omit_client_line=False, piece_label_qr=False):
    """Этикетка изделия (стекло) — как MIRROR_CUT _OLD: логотипы ×1.5 с отступом 5 мм от углов, № K по центру;
    низ — материал, размер, клиент, дата (без «клиент первой строкой»); квадрат кромки справа.
    Логотип слева: web_qr/logo или logo в корне проекта (logo_main.png), затем get_logo_main_path().
    Второй справа: logo_2.png в тех же каталогах — см. get_logo2_path().
    omit_client_line — не выводить строку клиента. piece_label_qr — QR справа по K (сужает поле текста)."""
    _draw_label_frame(c, x_pt, y_pt, w_pt, h_pt)
    edge_treatment = edge_treatment or {}
    has_edges = _has_any_edge(edge_treatment)
    rect_sz_pt = _EDGE_RECT_MM * mm
    top_zone_h = 15 * mm
    bottom_zone_h = 35 * mm
    top_zone_y0 = y_pt + bottom_zone_h
    top_zone_cy = top_zone_y0 + top_zone_h / 2
    logo_sz_pt = _LABEL_LOGO_TARGET_MM * mm
    logo_inset = _LABEL_LOGO_CORNER_MM * mm
    slice_right = x_pt + w_pt
    qr_x = None
    k_for_qr = piece_display_number if piece_display_number is not None else piece_number
    if k_for_qr is None and order_k_number is not None:
        k_for_qr = order_k_number
    if piece_label_qr and k_for_qr is not None:
        try:
            try:
                from logic.qr_utils import make_piece_k_qr_image
            except Exception:
                from qr_utils import make_piece_k_qr_image  # type: ignore
            qr_img = make_piece_k_qr_image(k_for_qr, size_px=280)
            buf = io.BytesIO()
            qr_img.save(buf, format="PNG")
            buf.seek(0)
            qr_sz = 28 * mm
            qr_m = 2 * mm
            qr_x = x_pt + w_pt - qr_sz - qr_m
            qr_y = y_pt + (h_pt - qr_sz) / 2
            c.drawImage(ImageReader(buf), qr_x, qr_y, width=qr_sz, height=qr_sz)
            slice_right = qr_x - 2 * mm
        except Exception:
            qr_x = None
            slice_right = x_pt + w_pt
    logo_path = get_logo_main_path() or get_logo_path()
    if os.path.isfile(logo_path):
        try:
            img = ImageReader(logo_path)
            iw, ih = img.getSize()
            if iw and ih:
                scale = min(logo_sz_pt / iw, logo_sz_pt / ih)
                lw = iw * scale
                lh = ih * scale
                lx = x_pt + logo_inset
                ly = y_pt + h_pt - logo_inset - lh
                c.drawImage(img, lx, ly, width=lw, height=lh)
        except Exception:
            pass
    logo2_path = get_logo2_path()
    if logo2_path and os.path.isfile(logo2_path):
        try:
            img2 = ImageReader(logo2_path)
            iw2, ih2 = img2.getSize()
            if iw2 and ih2:
                scale2 = min(logo_sz_pt / iw2, logo_sz_pt / ih2)
                lw2 = iw2 * scale2
                lh2 = ih2 * scale2
                if piece_label_qr and qr_x is not None:
                    rx2 = min(slice_right - lw2, x_pt + w_pt - logo_inset - lw2)
                else:
                    rx2 = x_pt + w_pt - logo_inset - lw2
                ly2 = y_pt + h_pt - logo_inset - lh2
                c.drawImage(img2, rx2, ly2, width=lw2, height=lh2)
        except Exception:
            pass
    cx_label = (x_pt + slice_right) / 2.0 if piece_label_qr and qr_x is not None else x_pt + w_pt / 2
    num_y = top_zone_cy - 0.5 * mm
    c.setFont(_LABEL_FONT_BOLD, 20)
    if piece_display_number is not None:
        num_text = "№ K%s" % (int(piece_display_number) if isinstance(piece_display_number, (int, float)) else piece_display_number)
    elif piece_number is not None:
        num_text = "№ %s" % (int(piece_number) if isinstance(piece_number, (int, float)) else piece_number)
    else:
        num_text = "№ K%s" % (order_k_number if order_k_number is not None else "?")
    c.drawCentredString(cx_label, num_y, str(num_text))
    text_left = x_pt + 6 * mm
    edge_letter_space = 5 * mm
    right_margin_piece = 2 * mm
    if has_edges:
        square_left = slice_right - rect_sz_pt - right_margin_piece - edge_letter_space
        text_max_width = square_left - text_left - 3 * mm
    else:
        text_max_width = slice_right - text_left - 3 * mm
    block_top = top_zone_y0 - 12 * mm
    mat_thick = (material_name or "").strip()
    if thickness_mm is not None:
        mat_thick = ("%s %d мм" % (mat_thick, int(thickness_mm))).strip() if mat_thick else "%d мм" % int(thickness_mm)
    try:
        h_mm = int(height_mm) if height_mm is not None else 0
        w_mm = int(width_mm) if width_mm is not None else 0
    except (TypeError, ValueError):
        h_mm = w_mm = 0
    label_h = h_mm
    label_w = w_mm
    if edge_treatment:
        if edge_treatment.get('top'):
            label_h -= 1
        if edge_treatment.get('bottom'):
            label_h -= 1
        if edge_treatment.get('left'):
            label_w -= 1
        if edge_treatment.get('right'):
            label_w -= 1
    label_h = max(0, label_h)
    label_w = max(0, label_w)
    size_text = "%d × %d мм" % (label_h, label_w)
    client_text = (client_name or "").strip() if client_name is not None else ""
    date_text = _format_order_date(order_date) if order_date is not None else ""
    line1 = mat_thick or "—"
    line2 = size_text
    line3 = client_text or "—"
    line4 = date_text or "—"
    if omit_client_line:
        lines_data = [line1, line2, line4]
    else:
        lines_data = [line1, line2, line3, line4]
    line_gap = 5 * mm
    bottom_margin = 2 * mm
    n_lines = len(lines_data)
    for font_line in range(14, 7, -1):
        if block_top - n_lines * line_gap < y_pt + bottom_margin:
            continue
        c.setFont(_LABEL_FONT, font_line)
        fits = True
        for s in lines_data:
            if c.stringWidth(s, _LABEL_FONT, font_line) > text_max_width:
                fits = False
                break
        if fits:
            break

    def _trunc(s, max_w):
        c.setFont(_LABEL_FONT, font_line)
        if c.stringWidth(s, _LABEL_FONT, font_line) <= max_w:
            return s
        while len(s) > 1 and c.stringWidth(s, _LABEL_FONT, font_line) > max_w:
            s = s[:-1]
        return s

    c.setFont(_LABEL_FONT, font_line)
    for i, s in enumerate(lines_data):
        s = _trunc(s, text_max_width) if has_edges else s
        c.drawString(text_left, block_top - i * line_gap, s)
    if has_edges:
        rx = slice_right - rect_sz_pt - right_margin_piece - edge_letter_space
        ry = y_pt + (bottom_zone_h - rect_sz_pt) / 2
        c.setStrokeColorRGB(0, 0, 0)
        c.setLineWidth(0.5)
        c.rect(rx, ry, rect_sz_pt, rect_sz_pt)
        c.setFont(_LABEL_FONT, 6)
        c.setFillColorRGB(0, 0, 0)
        c.drawCentredString(rx + rect_sz_pt / 2, ry + 1 * mm, str(label_w))
        c.saveState()
        c.translate(rx + 2 * mm, ry + rect_sz_pt / 2)
        c.rotate(-90)
        c.drawString(0, 0, str(label_h))
        c.restoreState()
        edge_font = 10
        c.setFont(_LABEL_FONT_BOLD, edge_font)
        out_gap = 0.5 * mm
        side_letter_gap = 2.2 * mm
        letter_cy = ry + rect_sz_pt / 2
        if _edge_letter(edge_treatment, 'top'):
            c.drawCentredString(rx + rect_sz_pt / 2, ry + rect_sz_pt + out_gap, _edge_letter(edge_treatment, 'top'))
        if _edge_letter(edge_treatment, 'bottom'):
            c.drawCentredString(rx + rect_sz_pt / 2, ry - out_gap - edge_font * 0.5, _edge_letter(edge_treatment, 'bottom'))
        if _edge_letter(edge_treatment, 'left'):
            _draw_vertical_edge_letter(
                c,
                rx - side_letter_gap,
                letter_cy,
                _edge_letter(edge_treatment, 'left'),
                _LABEL_FONT_BOLD,
                edge_font,
                'left',
            )
        if _edge_letter(edge_treatment, 'right'):
            _draw_vertical_edge_letter(
                c,
                rx + rect_sz_pt + side_letter_gap,
                letter_cy,
                _edge_letter(edge_treatment, 'right'),
                _LABEL_FONT_BOLD,
                edge_font,
                'right',
            )


def generate_labels_pdf_multi(remnants, pieces, filepath):
    """
    Один PDF: этикетки 100×50 мм, по одной на страницу (колонка — одна за другой).
    remnants: список dict (unique_number, name, height_mm, width_mm, label_number).
    pieces: список dict (w, h, edge_treatment) — изделия для клиента.
    """
    all_items = []
    for r in remnants:
        all_items.append(('remnant', r))
    for p in pieces:
        all_items.append(('piece', p))
    if not all_items:
        c = canvas.Canvas(filepath, pagesize=(LABEL_W_PT, LABEL_H_PT))
        c.save()
        return
    c = canvas.Canvas(filepath, pagesize=(LABEL_W_PT, LABEL_H_PT))
    remnant_idx = 0
    piece_idx = 0
    for idx, (kind, data) in enumerate(all_items):
        if idx > 0:
            c.showPage()
        if kind == 'remnant':
            remnant_idx += 1
            # Нумерация остатков: свой ряд 1, 2, 3… (label_number со склада)
            display_number = data.get('label_number')
            _draw_remnant_label(c, 0, 0, LABEL_W_PT, LABEL_H_PT,
                data.get('unique_number'), data.get('name'),
                data.get('height_mm', 0), data.get('width_mm', 0), data.get('label_number'),
                data.get('edge_treatment'), display_number=display_number, thickness_mm=data.get('thickness_mm'),
                cut_date=data.get('created_at'))
        else:
            piece_idx += 1
            # Нумерация изделий клиента:
            # 1) явный номер из данных (piece_display_number / piece_number),
            # 2) иначе сквозной от k_number заказа,
            # 3) fallback: piece_number / локальный 1..N.
            explicit_no = data.get('piece_display_number')
            if explicit_no is None:
                try:
                    k_base = int(data.get('k_number')) if data.get('k_number') is not None else None
                except (TypeError, ValueError):
                    k_base = None
                if k_base is not None:
                    explicit_no = (k_base + piece_idx - 1)
                else:
                    explicit_no = data.get('piece_number')
            if explicit_no is None:
                explicit_no = piece_idx
            _draw_piece_label(c, 0, 0, LABEL_W_PT, LABEL_H_PT,
                data.get('h', data.get('height_mm', 0)), data.get('w', data.get('width_mm', 0)),
                data.get('edge_treatment') or {}, data.get('name') or data.get('material'),
                data.get('k_number'), data.get('client_name'), data.get('order_date'),
                data.get('thickness_mm'), piece_number=None, piece_display_number=explicit_no,
                omit_client_line=bool(data.get('omit_client_on_label')),
                piece_label_qr=bool(data.get('piece_label_qr')))
    c.save()


def generate_label_pdf(unique_number, name, height_mm, width_mm, filepath=None, label_number=None, edge_treatment=None, thickness_mm=None, cut_date=None):
    """Одна этикетка остатка 100×50 мм (для обратной совместимости)."""
    dest = filepath if filepath else io.BytesIO()
    c = canvas.Canvas(dest, pagesize=(LABEL_W_PT, LABEL_H_PT))
    _draw_remnant_label(c, 0, 0, LABEL_W_PT, LABEL_H_PT, unique_number, name, height_mm, width_mm, label_number, edge_treatment, thickness_mm=thickness_mm, cut_date=cut_date)
    c.save()
    if filepath:
        return None
    return dest.getvalue()


def _draw_facade_finish_label(c, x_pt, y_pt, w_pt, h_pt, public_number, client_name, dims_text, order_id, qr_image_fn):
    """100×50 мм: как этикетка остатка стекла — рамка, логотип, QR справа по центру."""
    _draw_label_frame(c, x_pt, y_pt, w_pt, h_pt)
    top_zone_h = 15 * mm
    top_zone_y0 = y_pt + h_pt - top_zone_h
    top_zone_cy = top_zone_y0 + top_zone_h / 2
    logo_sz_pt = 32 * mm
    logo_left_margin = 2 * mm
    logo_top_margin = 1.5 * mm
    logo_right_edge = x_pt + logo_left_margin
    logo_path = get_logo_main_path() or get_logo_path()
    if os.path.isfile(logo_path):
        try:
            img = ImageReader(logo_path)
            iw, ih = img.getSize()
            if iw and ih:
                scale = min(logo_sz_pt / iw, logo_sz_pt / ih)
                lw, lh = iw * scale, ih * scale
                ly = y_pt + h_pt - logo_top_margin - lh
                c.drawImage(img, x_pt + logo_left_margin, ly, width=lw, height=lh)
                logo_right_edge = x_pt + logo_left_margin + lw
        except Exception:
            pass
    qr_margin = 2 * mm
    qr_sz_pt = 28 * mm
    qr_x = x_pt + w_pt - qr_sz_pt - qr_margin
    qr_y = y_pt + (h_pt - qr_sz_pt) / 2
    code_gf = "GF%s" % int(public_number)
    try:
        qr_img = qr_image_fn(code_gf, size_px=280)
        buf = io.BytesIO()
        qr_img.save(buf, format="PNG")
        buf.seek(0)
        c.drawImage(ImageReader(buf), qr_x, qr_y, width=qr_sz_pt, height=qr_sz_pt)
    except Exception:
        pass
    num_x = logo_right_edge + 10 * mm
    c.setFont(_LABEL_FONT_BOLD, 20)
    c.drawString(num_x, top_zone_cy - 0.5 * mm, "№ %s" % code_gf)
    text_right = qr_x - 2 * mm
    cx = x_pt + (text_right - x_pt) / 2
    line_gap = 4 * mm
    block_raise = 3 * mm
    mid_y = y_pt + (h_pt - top_zone_h) / 2 + block_raise
    c.setFont(_LABEL_FONT_BOLD, 13)
    c.drawCentredString(cx, mid_y + line_gap, "Готовый фасад")
    c.setFont(_LABEL_FONT_BOLD, 14)
    c.drawCentredString(cx, mid_y, (dims_text or "—")[:40])
    c.setFont(_LABEL_FONT, 11)
    c.drawCentredString(cx, mid_y - line_gap, ((client_name or "—").strip())[:36])
    if order_id:
        c.setFont(_LABEL_FONT, 10)
        c.drawCentredString(cx, mid_y - line_gap * 2, "Заказ № %s" % int(order_id))


def generate_facade_finish_labels_pdf(items, filepath):
    """PDF этикеток готового фасада — те же 100×50 мм, что у стекла."""
    try:
        from logic.qr_utils import make_facade_finished_qr_image
    except Exception:
        from qr_utils import make_facade_finished_qr_image  # type: ignore

    rows = [x for x in (items or []) if x]
    if not rows:
        c0 = canvas.Canvas(filepath, pagesize=(LABEL_W_PT, LABEL_H_PT))
        c0.save()
        return
    c = canvas.Canvas(filepath, pagesize=(LABEL_W_PT, LABEL_H_PT))
    for idx, it in enumerate(rows):
        if idx > 0:
            c.showPage()
        pub = int(it.get("public_number") or 0)
        client = str(it.get("client_name") or "").strip()
        wh = str(it.get("facade_dims") or "").strip()
        if not wh:
            hm, wm = it.get("height_mm"), it.get("width_mm")
            try:
                hi = int(hm or 0)
                wi = int(wm or 0)
                if hi > 0 and wi > 0:
                    wh = "%d × %d мм" % (hi, wi)
            except (TypeError, ValueError):
                wh = "—"
        oid = it.get("order_id")
        try:
            oid_i = int(oid) if oid is not None else None
        except (TypeError, ValueError):
            oid_i = None
        _draw_facade_finish_label(c, 0, 0, LABEL_W_PT, LABEL_H_PT, pub, client, wh or "—", oid_i, make_facade_finished_qr_image)
    c.save()