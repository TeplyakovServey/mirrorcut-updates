"""
Cutting layout algorithm: pack rectangles into sheets (remnants first, then full),
with 90° rotation, guillotine-only cuts (full axis), minimal waste and minimal
number of cuts; prefer one large business remnant over many small ones.
Минимальная ширина отрезаемой полосы по толщине стекла (мм): 4→10, 5→15, 6→18, 8→25, 10→30.
"""
# Минимальная ширина полосы (мм) при резе в зависимости от толщины листа (мм)
MIN_STRIP_BY_THICKNESS = {4: 10, 5: 15, 6: 18, 8: 25, 10: 30}
MIN_BUSINESS_SIDE_MM = 200
# Алгоритм 1 (ТЗ): перебор комбинаций (8 режимов сортировки при равных ключах × 4 политики порядка подобластей).
DOC_PACK_VARIANT_COUNT = 32
# Алгоритм 2: при нескольких допустимых линиях реза на узле — перебор с ограничением ветвления.
DOC_CUT_TREE_MAX_BRANCHES = 32

try:
    from rectpack import newPacker
    from rectpack import PackingMode
    from rectpack import SORT_AREA
    from rectpack import MaxRectsBl
    try:
        from rectpack import SORT_LSIDE, SORT_RATIO
    except ImportError:
        SORT_LSIDE = SORT_RATIO = None
    # Доп. стратегии порядка: «сначала отрезать полосу сверху/снизу/сбоку» — перебираем и выбираем лучший результат
    # rectlist: список (w, h, rid); сортировка задаёт порядок размещения → разный порядок резов (гильотина)
    def _sort_height_desc(rectlist):
        return sorted(rectlist, key=lambda r: (-r[1], -r[0]))  # высокие сначала — полоса сверху
    def _sort_height_asc(rectlist):
        return sorted(rectlist, key=lambda r: (r[1], r[0]))   # низкие сначала — заполнение снизу вверх
    def _sort_width_desc(rectlist):
        return sorted(rectlist, key=lambda r: (-r[0], -r[1]))  # широкие сначала — полоса сбоку
    def _sort_width_asc(rectlist):
        return sorted(rectlist, key=lambda r: (r[0], r[1]))    # узкие сначала
    _EXTRA_SORT_ALGOS = [_sort_height_desc, _sort_height_asc, _sort_width_desc, _sort_width_asc]
    try:
        from rectpack import GuillotineBssfSas, GuillotineBssfMaxas, GuillotineBssfMinas
        from rectpack import GuillotineBlsfSas, GuillotineBlsfMaxas
        # Maxas первыми: при разбиении свободного места выбирают разрез, при котором больший из двух остатков максимален — остаётся одна большая полоса, а не много мелких.
        _GUILLOTINE_ALGOS = [GuillotineBssfMaxas, GuillotineBlsfMaxas, GuillotineBssfSas, GuillotineBlsfSas, GuillotineBssfMinas]
    except ImportError:
        _GUILLOTINE_ALGOS = []
    _HAS_RECTPACK = True
except ImportError:
    MaxRectsBl = None
    SORT_LSIDE = SORT_RATIO = None
    _EXTRA_SORT_ALGOS = []
    _GUILLOTINE_ALGOS = []
    _HAS_RECTPACK = False

import copy
import itertools
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _subtract_rect(free_list, placed):
    """Subtract placed rect (x, y, w, h) from each free rect in free_list; return new free list."""
    px, py, pw, ph = placed
    new_free = []
    for (fx, fy, fw, fh) in free_list:
        if px >= fx + fw or px + pw <= fx or py >= fy + fh or py + ph <= fy:
            new_free.append((fx, fy, fw, fh))
            continue
        # overlap: split into at most 4 rects (top, bottom, left, right of placed)
        if fy < py:
            new_free.append((fx, fy, fw, py - fy))
        if py + ph < fy + fh:
            new_free.append((fx, py + ph, fw, fy + fh - (py + ph)))
        if fx < px:
            hh = min(fy + fh, py + ph) - max(fy, py)
            if hh > 0:
                new_free.append((fx, max(fy, py), px - fx, hh))
        if px + pw < fx + fw:
            hh = min(fy + fh, py + ph) - max(fy, py)
            if hh > 0:
                new_free.append((px + pw, max(fy, py), fx + fw - (px + pw), hh))
    return [(a, b, c, d) for (a, b, c, d) in new_free if c > 0 and d > 0]


def _is_business_rect(w, h, min_h, min_w):
    """Check if rectangle (w,h) is >= (min_w, min_h) in both dimensions (can swap for 90°)."""
    # Правило цеха: если меньшая сторона < 200 мм, это неделовой отход.
    if min(w, h) < MIN_BUSINESS_SIDE_MM:
        return False
    if min_h <= 0 and min_w <= 0:
        return True
    return (w >= min_w and h >= min_h) or (w >= min_h and h >= min_w)


def recompute_free_rects_from_pieces(sheet_width, sheet_height, pieces, min_h, min_w):
    """
    По списку изделий (каждое с x, y, w, h в координатах top-left, y вниз от верха)
    вычислить свободные прямоугольники и разбить на business_rects и waste_rects.
    Возвращает (business_rects, waste_rects) — списки dict с x, y, w, h (top-left).
    """
    free_bl = [(0, 0, sheet_width, sheet_height)]
    for p in pieces:
        x, y = p['x'], p['y']
        w, h = p['w'], p['h']
        bl_y = sheet_height - y - h
        free_bl = _subtract_rect(free_bl, (x, bl_y, w, h))
    business_rects = []
    waste_rects = []
    for (fx, fy, fw, fh) in free_bl:
        if fw <= 0 or fh <= 0:
            continue
        top_y = sheet_height - fy - fh
        if _is_business_rect(fw, fh, min_h, min_w):
            business_rects.append({'x': fx, 'y': int(top_y), 'w': fw, 'h': fh})
        else:
            waste_rects.append({'x': fx, 'y': int(top_y), 'w': fw, 'h': fh})
    return (business_rects, waste_rects)


def _normalize_packed_tuples_top_left_mins(tuples):
    """
    rectpack размещает от нижнего левого угла бина → в координатах top-left (y вниз от верха)
    над блоком деталей остаётся пустая «полоса», хотя расклад можно сдвинуть вверх без изменения
    взаимного расположения. Сдвигаем все прямоугольники к (0,0), чтобы рез шёл от края листа.
    Возвращает (tuples_norm, min_x, min_y) — сдвиг до нормализации (для подстройки cut_segments).
    """
    if not tuples:
        return tuples, 0, 0
    min_x = min(t[0] for t in tuples)
    min_y = min(t[1] for t in tuples)
    if min_x == 0 and min_y == 0:
        return tuples, 0, 0
    out = [(t[0] - min_x, t[1] - min_y, t[2], t[3], t[4]) for t in tuples]
    return out, min_x, min_y


def _normalize_packed_tuples_top_left(tuples):
    out, _, _ = _normalize_packed_tuples_top_left_mins(tuples)
    return out


def merge_adjacent_free_rects_for_display(rects):
    """
    Слить пары смежных осевых прямоугольников (одинаковые x,w и стык по y, или y,h и стык по x).
    После _subtract_rect свободное место иногда распадается на несколько блоков в одной колонке/ряду —
    для схемы это один связный остаток, без «лишних» горизонталей между полосами.
    """
    if not rects or len(rects) < 2:
        return list(rects) if rects else []
    out = [dict(r) for r in rects]
    merged = True
    while merged:
        merged = False
        n = len(out)
        for i in range(n):
            if i >= len(out):
                break
            a = out[i]
            for j in range(i + 1, len(out)):
                b = out[j]
                if a['x'] == b['x'] and a['w'] == b['w']:
                    if a['y'] + a['h'] == b['y']:
                        out[i] = {'x': a['x'], 'y': a['y'], 'w': a['w'], 'h': a['h'] + b['h']}
                        out.pop(j)
                        merged = True
                        break
                    if b['y'] + b['h'] == a['y']:
                        out[i] = {'x': b['x'], 'y': b['y'], 'w': b['w'], 'h': a['h'] + b['h']}
                        out.pop(j)
                        merged = True
                        break
                elif a['y'] == b['y'] and a['h'] == b['h']:
                    if a['x'] + a['w'] == b['x']:
                        out[i] = {'x': a['x'], 'y': a['y'], 'w': a['w'] + b['w'], 'h': a['h']}
                        out.pop(j)
                        merged = True
                        break
                    if b['x'] + b['w'] == a['x']:
                        out[i] = {'x': b['x'], 'y': b['y'], 'w': a['w'] + b['w'], 'h': a['h']}
                        out.pop(j)
                        merged = True
                        break
            if merged:
                break
    return out


def _split_free_rects_by_one_segment(rects_list, seg, min_h, min_w, sheet_w=0, sheet_h=0):
    """
    Разбить список свободных прямоугольников (dict x,y,w,h) одним сегментом H/V.
    Логика совпадает с LayoutEditCanvas._split_rects_by_segment.
    """
    out_business = []
    out_waste = []
    t = str(seg.get('type') or '').strip().upper()
    for r in rects_list:
        x, y = r.get('x', 0), r.get('y', 0)
        w, h = r.get('w', 0), r.get('h', 0)
        if t == 'V':
            pos = seg.get('pos', 0)
            elo = seg.get('extent_lo', 0)
            ehi = seg.get('extent_hi', sheet_h or 0)
            try:
                pos = float(pos)
                elo = float(elo)
                ehi = float(ehi)
            except (TypeError, ValueError):
                pos = elo = ehi = 0.0
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
            pos = seg.get('pos', 0)
            elo = seg.get('extent_lo', 0)
            ehi = seg.get('extent_hi', sheet_w or 0)
            try:
                pos = float(pos)
                elo = float(elo)
                ehi = float(ehi)
            except (TypeError, ValueError):
                pos = elo = ehi = 0.0
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


def apply_cut_segments_to_free_rects(sheet_width, sheet_height, pieces, cut_segments, min_h, min_w):
    """Свободные области после изделий и последовательного применения cut_segments."""
    br, wr = recompute_free_rects_from_pieces(sheet_width, sheet_height, pieces, min_h, min_w)
    for seg in cut_segments or []:
        if not isinstance(seg, dict):
            continue
        if str(seg.get('type') or '').strip().upper() not in ('H', 'V'):
            continue
        br, wr = _split_free_rects_by_one_segment(br + wr, seg, min_h, min_w, sheet_width, sheet_height)
    return br, wr


def optimize_cut_segments_for_remnants(layout_dict, min_h, min_w):
    """
    При фиксированных изделиях: жадно убрать линии реза, пока улучшается оценка остатков
    (_layout_score_for_variant: деловые площади и крупность, затем отходы; при равенстве —
    меньше число линий). Затем сравнить с полным отсутствием реза.
    Возвращает новый список сегментов H/V (без финализации UI).
    """
    pieces = list(layout_dict.get('pieces') or [])
    sw = int(layout_dict.get('sheet_width') or 0)
    sh = int(layout_dict.get('sheet_height') or 0)
    raw = layout_dict.get('cut_segments') or []
    segs = [
        dict(s) for s in raw
        if isinstance(s, dict) and str(s.get('type') or '').strip().upper() in ('H', 'V')
    ]

    def metric(cut_list):
        br, wr = apply_cut_segments_to_free_rects(sw, sh, pieces, cut_list, min_h, min_w)
        sc = _layout_score_for_variant(br, wr, min_h, min_w, pieces, sw, sh)
        return sc + (len(cut_list),)

    if not segs:
        return []

    current = list(segs)
    cur_m = metric(current)
    while len(current) > 0:
        best_i = None
        best_m = cur_m
        for i in range(len(current)):
            trial = current[:i] + current[i + 1:]
            m = metric(trial)
            if m < best_m:
                best_m = m
                best_i = i
        if best_i is None:
            break
        current.pop(best_i)
        cur_m = best_m

    m_empty = metric([])
    if m_empty < cur_m:
        return []
    return current


def _try_extra_cut(layout_dict, min_h, min_w, min_strip=0):
    """
    Попробовать один дополнительный гильотинный рез, разрезающий крупный отход так, чтобы
    одна часть стала деловым остатком. min_strip: минимальная ширина отрезаемой полосы (мм) по толщине —
    не создаём рез, если одна из частей будет уже min_strip. Изменяет layout_dict на месте.
    """
    waste_rects = list(layout_dict.get('waste_rects') or [])
    business_rects = list(layout_dict.get('business_rects') or [])
    segments = list(layout_dict.get('cut_segments') or [])
    sw = layout_dict.get('sheet_width') or 0
    sh = layout_dict.get('sheet_height') or 0
    strip_lim = max(10, min_strip)
    if not waste_rects:
        return False
    waste_rects = sorted(waste_rects, key=lambda r: (r.get('w') or 0) * (r.get('h') or 0), reverse=True)
    for wr in waste_rects:
        x, y = wr.get('x', 0), wr.get('y', 0)
        w, h = wr.get('w', 0), wr.get('h', 0)
        if w <= 0 or h <= 0:
            continue
        # Вертикальный разрез: обе части не уже strip_lim
        if w >= min_w + strip_lim and (w - min_w) >= strip_lim and min_w >= strip_lim:
            cut_x = x + (w - min_w)
            right_w = min_w
            left_w = w - min_w
            if _is_business_rect(right_w, h, min_h, min_w):
                new_seg = {'type': 'V', 'pos': cut_x, 'extent_lo': y, 'extent_hi': y + h,
                           'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh}
                new_business = business_rects + [{'x': cut_x, 'y': y, 'w': right_w, 'h': h}]
                new_waste = [r for r in waste_rects if r is not wr] + [{'x': x, 'y': y, 'w': left_w, 'h': h}]
                if not _is_business_rect(left_w, h, min_h, min_w):
                    pass  # левая часть остаётся отходом
                else:
                    new_business.append({'x': x, 'y': y, 'w': left_w, 'h': h})
                    new_waste = [r for r in waste_rects if r is not wr]
                layout_dict['cut_segments'] = segments + [new_seg]
                layout_dict['business_rects'] = new_business
                layout_dict['waste_rects'] = new_waste
                return True
            if _is_business_rect(left_w, h, min_h, min_w):
                new_seg = {'type': 'V', 'pos': x + min_w, 'extent_lo': y, 'extent_hi': y + h,
                           'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh}
                new_business = business_rects + [{'x': x, 'y': y, 'w': left_w, 'h': h}]
                new_waste = [r for r in waste_rects if r is not wr] + [{'x': x + min_w, 'y': y, 'w': w - min_w, 'h': h}]
                layout_dict['cut_segments'] = segments + [new_seg]
                layout_dict['business_rects'] = new_business
                layout_dict['waste_rects'] = new_waste
                return True
        # Горизонтальный разрез: обе части не уже strip_lim
        if h >= min_h + strip_lim and (h - min_h) >= strip_lim and min_h >= strip_lim:
            cut_y = y + (h - min_h)
            top_h = min_h
            bot_h = h - min_h
            if _is_business_rect(w, top_h, min_h, min_w):
                new_seg = {'type': 'H', 'pos': cut_y, 'extent_lo': x, 'extent_hi': x + w,
                           'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh}
                new_business = business_rects + [{'x': x, 'y': cut_y, 'w': w, 'h': top_h}]
                new_waste = [r for r in waste_rects if r is not wr] + [{'x': x, 'y': y, 'w': w, 'h': bot_h}]
                if _is_business_rect(w, bot_h, min_h, min_w):
                    new_business.append({'x': x, 'y': y, 'w': w, 'h': bot_h})
                    new_waste = [r for r in waste_rects if r is not wr]
                layout_dict['cut_segments'] = segments + [new_seg]
                layout_dict['business_rects'] = new_business
                layout_dict['waste_rects'] = new_waste
                return True
            if _is_business_rect(w, bot_h, min_h, min_w):
                new_seg = {'type': 'H', 'pos': y + min_h, 'extent_lo': x, 'extent_hi': x + w,
                           'row_iy': 0, 'row_y_lo': 0, 'row_y_hi': sh}
                new_business = business_rects + [{'x': x, 'y': y, 'w': w, 'h': bot_h}]
                new_waste = [r for r in waste_rects if r is not wr] + [{'x': x, 'y': y + min_h, 'w': w, 'h': h - min_h}]
                layout_dict['cut_segments'] = segments + [new_seg]
                layout_dict['business_rects'] = new_business
                layout_dict['waste_rects'] = new_waste
                return True
    return False


def _resolve_one_sheet_per_bin(used_bins, bin_meta, bin_rects):
    """
    Один физический лист — один контейнер. Если для одного sheet_id использованы оба варианта (нормальный и 90°),
    оставляем контейнер с большей занятой площадью; детали из второго возвращаем как unpacked: [(rid, w, h), ...].
    """
    by_sheet = {}
    for b in used_bins:
        sid = bin_meta[b]['sheet_id']
        if sid not in by_sheet:
            by_sheet[sid] = []
        by_sheet[sid].append(b)
    kept_bins = set()
    unpacked = []
    for sid, bins in by_sheet.items():
        if len(bins) == 1:
            kept_bins.add(bins[0])
            continue
        # Два контейнера на один лист — оставляем тот, где больше площадь деталей
        def area(b):
            return sum(r[2] * r[3] for r in bin_rects[b])
        best = max(bins, key=area)
        kept_bins.add(best)
        for b in bins:
            if b == best:
                continue
            for r in bin_rects[b]:
                # r = (x, y, w, h, rec, qty_label) или с rid в конце
                if len(r) >= 7:
                    rid = r[6]
                else:
                    rid = None
                w, h = r[2], r[3]
                if rid is not None:
                    unpacked.append((rid, w, h))
    return kept_bins, unpacked


def _remnant_quality(fw, fh):
    """
    «Полезность» делового остатка: чем ближе к квадрату, тем лучше (удобнее использовать).
    quality = площадь * (min/max сторон). Квадрат = площадь*1, длинная полоса 268×2400 ≈ площадь*0.11.
    """
    if fw <= 0 or fh <= 0:
        return 0
    a = fw * fh
    ratio = min(fw, fh) / max(fw, fh)
    return a * ratio


def _score_packing(bw, bh, placed_tuples, min_h, min_w):
    """
    placed_tuples = [(x, y_top, w, h, rid), ...] в координатах top-left (y вниз от верха).
    Возвращает (waste_area, business_area, num_business_rects, max_business_rect_area, max_business_quality).
    Цель: меньше отходов, меньше остатков, один большой остаток лучше многих мелких,
    и остаток ближе к квадрату лучше длинной полосы (quality = площадь * aspect_ratio).
    """
    free_bl = [(0, 0, bw, bh)]
    for (x, y_top, w, h, _) in placed_tuples:
        bl_y = bh - y_top - h
        free_bl = _subtract_rect(free_bl, (x, bl_y, w, h))
    waste_area = 0
    business_area = 0
    business_areas = []
    business_qualities = []
    for (fx, fy, fw, fh) in free_bl:
        if fw <= 0 or fh <= 0:
            continue
        if _is_business_rect(fw, fh, min_h, min_w):
            a = fw * fh
            business_area += a
            business_areas.append(a)
            business_qualities.append(_remnant_quality(fw, fh))
        else:
            waste_area += fw * fh
    max_business = max(business_areas) if business_areas else 0
    max_quality = max(business_qualities) if business_qualities else 0
    num_business = len(business_areas)
    return (waste_area, business_area, num_business, max_business, max_quality)


def _pack_into_one_bin(rects_with_rid, bw, bh, sort_algos, pack_algos, min_h=0, min_w=0):
    """
    Упаковать список (w, h, rid) в один контейнер (bw, bh).
    Сначала алгоритм 1 из ТЗ (несколько вариантов порядка при равных ключах), затем при неудаче — rectpack.
    Возвращает (used_area, packed_list).
    """
    bw, bh = int(bw), int(bh)
    best_key = None
    best_packed = []
    for vi in range(DOC_PACK_VARIANT_COUNT):
        packed_doc = _pack_guillotine_doc_recursive(rects_with_rid, bw, bh, vi)
        if packed_doc and len(packed_doc) == len(rects_with_rid):
            waste_area, business_area, _, max_business, max_quality = _score_packing(
                bw, bh, packed_doc, min_h, min_w
            )
            n_p = len(packed_doc)
            key = (n_p, -waste_area, business_area, max_business, max_quality)
            if best_key is None or key > best_key:
                best_key = key
                best_packed = packed_doc
    if best_packed and len(best_packed) == len(rects_with_rid):
        best_used = sum(r[2] * r[3] for r in best_packed)
        return best_used, best_packed
    if not _HAS_RECTPACK:
        return 0, []
    for pack_algo in pack_algos:
        if pack_algo is None:
            continue
        for sort_algo in sort_algos:
            packer = newPacker(
                mode=PackingMode.Offline,
                sort_algo=sort_algo,
                rotation=True,
                pack_algo=pack_algo,
            )
            packer.add_bin(bw, bh)
            for (w, h, rid) in rects_with_rid:
                packer.add_rect(int(w), int(h), rid)
            packer.pack()
            packed = packer.rect_list()
            packed_tuples = _normalize_packed_tuples_top_left(
                [(r[1], bh - r[2] - r[4], r[3], r[4], r[5]) for r in packed]
            )
            waste_area, business_area, _, max_business, max_quality = _score_packing(bw, bh, packed_tuples, min_h, min_w)
            n_p = len(packed_tuples)
            key = (n_p, -waste_area, business_area, max_business, max_quality)
            if best_key is None or key > best_key:
                best_key = key
                best_packed = packed_tuples
    best_used = sum(r[2] * r[3] for r in best_packed) if best_packed else 0
    return best_used, best_packed


def _translate_packed_region_to_sheet_tl(fx, fy, fw, fh, sh, packed_tuples):
    """
    packed_tuples — (x, y, w, h, rid) в top-left координатах под-бина (y вниз).
    Свободная область в bottom-left: левый нижний угол (fx, fy), размер (fw, fh), sh — высота листа.
    """
    out = []
    fh = int(fh)
    for (x, y_tl, w, h, rid) in packed_tuples:
        sx = int(fx) + int(x)
        sy = int(sh) - int(fy) - fh + int(y_tl)
        out.append((sx, sy, int(w), int(h), rid))
    return out


def _try_pack_rects_into_free_space(
    sheet_w,
    sheet_h,
    existing_pieces,
    rects_with_rid,
    sort_algos,
    pack_algos,
    min_h,
    min_w,
    min_strip,
):
    """
    Разместить все rects_with_rid в свободной области листа с уже стоящими изделиями (координаты top-left).
    existing_pieces: [{'x','y','w','h'}, ...].
    Возвращает [(x,y,w,h,rid), ...] для новых изделий в координатах листа или None.
    """
    if not rects_with_rid:
        return []
    sw, sh = int(sheet_w), int(sheet_h)
    if sw <= 0 or sh <= 0:
        return None
    remaining = list(rects_with_rid)
    all_placed = []
    while remaining:
        free_bl = [(0, 0, sw, sh)]
        for p in existing_pieces:
            x, y = int(p.get('x', 0)), int(p.get('y', 0))
            w, h = int(p.get('w', 0)), int(p.get('h', 0))
            if w <= 0 or h <= 0:
                continue
            bl_y = sh - y - h
            free_bl = _subtract_rect(free_bl, (x, bl_y, w, h))
        for pl in all_placed:
            x, y, w, h, _rid = pl[0], pl[1], pl[2], pl[3], pl[4]
            bl_y = sh - y - h
            free_bl = _subtract_rect(free_bl, (x, bl_y, w, h))
        best_pack = None
        best_key = None
        best_region = None
        for (fx, fy, fw, fh) in free_bl:
            if fw <= 0 or fh <= 0 or min(int(fw), int(fh)) < min_strip:
                continue
            for bww, bhh in ((fw, fh), (fh, fw)) if fw != fh else ((fw, fh),):
                if min(int(bww), int(bhh)) < min_strip:
                    continue
                used, packed = _pack_into_one_bin(remaining, bww, bhh, sort_algos, pack_algos, min_h, min_w)
                if not packed:
                    continue
                n = len(packed)
                if n > len(remaining):
                    continue
                waste, b_area, n_br, m_area, m_quality = _score_packing(bww, bhh, packed, min_h, min_w)
                key = (n, -waste, b_area, m_area, m_quality)
                if best_key is None or key > best_key:
                    best_key = key
                    best_pack = packed
                    best_region = (fx, fy, fw, fh, bww, bhh)
        if not best_pack or best_region is None:
            return None
        fx, fy, fw, fh, bww, bhh = best_region
        placed_abs = _translate_packed_region_to_sheet_tl(fx, fy, fw, fh, sh, best_pack)
        all_placed.extend(placed_abs)
        placed_rids = {t[4] for t in best_pack}
        remaining = [r for r in remaining if r[2] not in placed_rids]
    return all_placed


def repack_pieces_on_sheet(sheet_width, sheet_height, pieces, min_h=0, min_w=0):
    """
    Переупаковать все изделия на один лист (алгоритм 1 из ТЗ; при необходимости — rectpack).
    pieces: список dict с ключами w, h и опционально recipient, quantity_label, edge_treatment, rotated.
    Возвращает новый список pieces с обновлёнными x, y (и теми же w, h и метаданными).
    Если не все помещаются, возвращает None — тогда вызывающий оставляет раскладку как есть.
    """
    if not pieces:
        return []
    rects_with_rid = [(int(p.get('w', 0)), int(p.get('h', 0)), i) for i, p in enumerate(pieces)]
    pack_algos = _GUILLOTINE_ALGOS if _GUILLOTINE_ALGOS else ([MaxRectsBl] if MaxRectsBl else [])
    sort_algos = [SORT_AREA]
    if SORT_LSIDE is not None:
        sort_algos.append(SORT_LSIDE)
    if SORT_RATIO is not None:
        sort_algos.append(SORT_RATIO)
    used, best_packed = _pack_into_one_bin(
        rects_with_rid, int(sheet_width), int(sheet_height), sort_algos, pack_algos, min_h, min_w
    )
    if len(best_packed) != len(pieces):
        return None
    result = []
    for (x, y, w, h, rid) in best_packed:
        p = dict(pieces[rid])
        p['x'] = int(x)
        p['y'] = int(y)
        p['w'] = int(w)
        p['h'] = int(h)
        result.append(p)
    return result


def _min_strip_for_thickness(thickness_mm):
    """Минимальная ширина отрезаемой полосы (мм) для данной толщины листа. Нельзя отрезать полосу уже этого (например 3 мм)."""
    return MIN_STRIP_BY_THICKNESS.get(int(thickness_mm), 10)


def _interval_overlap_len(a0, a1, b0, b1):
    return max(0, min(a1, b1) - max(a0, b0))


def pieces_edge_connected(pieces):
    """
    Все изделия образуют один связный блок по общим рёбрам (стык по отрезку ненулевой длины).
    Угол в одной точке не считается стыком. Одна деталь на листе — допустимо.
    """
    if not pieces:
        return True
    n = len(pieces)
    if n == 1:
        return True
    rects = []
    for p in pieces:
        try:
            rects.append(
                (int(p.get('x', 0)), int(p.get('y', 0)), int(p.get('w', 0)), int(p.get('h', 0)))
            )
        except (TypeError, ValueError):
            rects.append((0, 0, 0, 0))

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        ax, ay, aw, ah = rects[i]
        for j in range(i + 1, n):
            bx, by, bw, bh = rects[j]
            if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
                continue
            if ax + aw == bx and _interval_overlap_len(ay, ay + ah, by, by + bh) > 0:
                union(i, j)
            elif bx + bw == ax and _interval_overlap_len(ay, ay + ah, by, by + bh) > 0:
                union(i, j)
            elif ay + ah == by and _interval_overlap_len(ax, ax + aw, bx, bx + bw) > 0:
                union(i, j)
            elif by + bh == ay and _interval_overlap_len(ax, ax + aw, bx, bx + bw) > 0:
                union(i, j)

    root0 = find(0)
    return all(find(i) == root0 for i in range(n))


def translate_pieces_to_origin(pieces):
    """Сдвиг всех деталей так, чтобы min(x)=min(y)=0."""
    if not pieces:
        return []
    xs = []
    ys = []
    for p in pieces:
        try:
            xs.append(int(p.get('x', 0)))
            ys.append(int(p.get('y', 0)))
        except (TypeError, ValueError):
            xs.append(0)
            ys.append(0)
    mx, my = min(xs), min(ys)
    if mx == 0 and my == 0:
        return [dict(p) for p in pieces]
    out = []
    for p in pieces:
        q = dict(p)
        q['x'] = int(p.get('x', 0)) - mx
        q['y'] = int(p.get('y', 0)) - my
        out.append(q)
    return out


def rotate_pieces_coords_cw90(pieces, sheet_width):
    """
    Поворот координат деталей на 90° по часовой стрелке; новый лист будет old_height × old_width.
    Координаты top-left, y вниз.
    """
    W = int(sheet_width)
    out = []
    for p in pieces:
        q = dict(p)
        try:
            x = float(p.get('x', 0))
            y = float(p.get('y', 0))
            w = float(p.get('w', 0))
            h = float(p.get('h', 0))
        except (TypeError, ValueError):
            x = y = w = h = 0.0
        q['x'] = int(round(y))
        q['y'] = int(round(W - x - w))
        q['w'] = int(round(h))
        q['h'] = int(round(w))
        out.append(q)
    return out


def _pieces_fit_sheet(pieces, sw, sh):
    sw, sh = int(sw), int(sh)
    for p in pieces:
        try:
            x, y = int(p.get('x', 0)), int(p.get('y', 0))
            w, h = int(p.get('w', 0)), int(p.get('h', 0))
        except (TypeError, ValueError):
            return False
        if x < 0 or y < 0 or x + w > sw or y + h > sh or w <= 0 or h <= 0:
            return False
    return True


def _anchor_cluster_top_left(pieces):
    """Прижать связный блок к верхнему левому углу листа (min x и min y на границе)."""
    pcs = [dict(p) for p in pieces]
    if not pcs:
        return pcs
    mx = min(int(p['x']) for p in pcs)
    my = min(int(p['y']) for p in pcs)
    for p in pcs:
        p['x'] = int(p['x']) - mx
        p['y'] = int(p['y']) - my
    return pcs


def _anchor_cluster_bottom_left(pieces, sheet_height):
    """Прижать блок к левому нижнему углу: левый край к x=0, низ блока к нижней границе листа."""
    pcs = [dict(p) for p in pieces]
    sh = int(sheet_height)
    if not pcs:
        return pcs
    mx = min(int(p['x']) for p in pcs)
    mb = max(int(p['y']) + int(p['h']) for p in pcs)
    dx = -mx
    dy = sh - mb
    for p in pcs:
        p['x'] = int(p['x']) + dx
        p['y'] = int(p['y']) + dy
    return pcs


def _pick_best_anchored_placement(pieces, bw, bh, min_h, min_w):
    """
    Для одной геометрии раскладки выбрать лучшее прижатие: верх-лево или низ-лево (цеховые края).
    Возвращает (score_tuple, pieces) или (None, None).
    """
    if not pieces:
        return None, None
    best_sc = None
    best_pcs = None
    for anchored in (_anchor_cluster_top_left(pieces), _anchor_cluster_bottom_left(pieces, bh)):
        if len(anchored) > 1 and not pieces_edge_connected(anchored):
            continue
        if not _pieces_fit_sheet(anchored, bw, bh):
            continue
        br, wr = recompute_free_rects_from_pieces(bw, bh, anchored, min_h, min_w)
        sc = _layout_score_for_variant(br, wr, min_h, min_w, anchored, bw, bh)
        if best_sc is None or sc < best_sc:
            best_sc = sc
            best_pcs = anchored
    return best_sc, best_pcs


def _packed_tuples_to_pieces(packed_tuples, pieces_template):
    out = []
    for (x, y, w, h, rid) in packed_tuples:
        p = dict(pieces_template[rid])
        p['x'], p['y'], p['w'], p['h'] = int(x), int(y), int(w), int(h)
        out.append(p)
    return out


def optimize_layout_after_sheet_rotate(pieces, sheet_width, sheet_height, min_h, min_w):
    """
    После поворота листа на 90°: геометрия со сдвигом, перебор поворотов деталей (до лимита),
    при необходимости — алгоритм 1 из ТЗ (полный перебор вариантов размещения, см. DOC_PACK_VARIANT_COUNT) и прижатие блока к верху-слева или низу-слева.
    """
    if not pieces:
        return None
    sw, sh = int(sheet_width), int(sheet_height)
    if sw <= 0 or sh <= 0:
        return None
    tpl = list(pieces)
    rotated = rotate_pieces_coords_cw90(tpl, sw)
    shifted = translate_pieces_to_origin(rotated)
    new_sw, new_sh = sh, sw

    size_templates = []
    for i in range(len(tpl)):
        q = dict(tpl[i])
        q['w'] = shifted[i]['w']
        q['h'] = shifted[i]['h']
        size_templates.append(q)

    rects_with_rid = [(int(q['w']), int(q['h']), i) for i, q in enumerate(size_templates)]

    candidates = []

    def try_add(pclist):
        if not pclist or len(pclist) != len(tpl):
            return
        if not _pieces_fit_sheet(pclist, new_sw, new_sh):
            return
        if not pieces_edge_connected(pclist):
            return
        br, wr = recompute_free_rects_from_pieces(new_sw, new_sh, pclist, min_h, min_w)
        sc = _layout_score_for_variant(br, wr, min_h, min_w, pclist, new_sw, new_sh)
        candidates.append((sc, pclist))

    _, anch_geom = _pick_best_anchored_placement(shifted, new_sw, new_sh, min_h, min_w)
    if anch_geom:
        try_add(anch_geom)

    enum_pcs = enumerate_piece_rotations_best_layout(rects_with_rid, new_sw, new_sh, tpl, min_h, min_w)
    if enum_pcs:
        try_add(enum_pcs)

    if not candidates:
        for vi in range(DOC_PACK_VARIANT_COUNT):
            packed = _pack_guillotine_doc_recursive(rects_with_rid, new_sw, new_sh, vi)
            if not packed or len(packed) != len(tpl):
                continue
            _, a = _pick_best_anchored_placement(_packed_tuples_to_pieces(packed, tpl), new_sw, new_sh, min_h, min_w)
            if a:
                try_add(a)

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    best_pieces = candidates[0][1]
    br, wr = recompute_free_rects_from_pieces(new_sw, new_sh, best_pieces, min_h, min_w)
    return {
        'pieces': best_pieces,
        'business_rects': br,
        'waste_rects': wr,
        'sheet_width': new_sw,
        'sheet_height': new_sh,
    }


def _pack_shelf_bottom_aligned(rects_with_rid, bw, bh):
    """
    Укладка «полками» снизу вверх: в каждом ряду детали слева направо, низ ряда на одной линии y = bh.
    Порядок в ряду — по убыванию площади среди оставшихся (крупные раньше). Допускается поворот 90°.
    Координаты top-left; верх листа может остаться пустым (деловой остаток сверху), сдвиг вверх не делается.
    Возвращает [(x, y, w, h, rid), ...] или None.
    """
    bw, bh = int(bw), int(bh)
    if bw <= 0 or bh <= 0 or not rects_with_rid:
        return None
    items = [
        {
            'w': max(0, int(w)),
            'h': max(0, int(h)),
            'rid': rid,
            'area': max(0, int(w)) * max(0, int(h)),
        }
        for (w, h, rid) in rects_with_rid
    ]
    items.sort(key=lambda t: (-t['area'], -max(t['w'], t['h']), -min(t['w'], t['h'])))
    remaining = items[:]
    placed = []
    current_bottom = bh
    while remaining:
        x = 0
        row_max_h = 0
        new_rem = []
        for p in remaining:
            ok = False
            for w, h in ((p['w'], p['h']), (p['h'], p['w'])):
                if w <= 0 or h <= 0:
                    continue
                if x + w <= bw and current_bottom - h >= 0:
                    y = current_bottom - h
                    placed.append((x, y, w, h, p['rid']))
                    x += w
                    row_max_h = max(row_max_h, h)
                    ok = True
                    break
            if not ok:
                new_rem.append(p)
        if row_max_h == 0:
            return None
        current_bottom -= row_max_h
        remaining = new_rem
    return placed if len(placed) == len(rects_with_rid) else None


def _pack_shelf_top_aligned(rects_with_rid, bw, bh):
    """
    Полки сверху вниз: первый ряд у верхнего края (y=0), в ряду слева направо.
    Крупные по площади среди оставшихся — раньше в ряду. Поворот 90° на каждой детали.
    """
    bw, bh = int(bw), int(bh)
    if bw <= 0 or bh <= 0 or not rects_with_rid:
        return None
    items = [
        {
            'w': max(0, int(w)),
            'h': max(0, int(h)),
            'rid': rid,
            'area': max(0, int(w)) * max(0, int(h)),
        }
        for (w, h, rid) in rects_with_rid
    ]
    items.sort(key=lambda t: (-t['area'], -max(t['w'], t['h']), -min(t['w'], t['h'])))
    remaining = items[:]
    placed = []
    current_y = 0
    while remaining:
        x = 0
        row_max_h = 0
        new_rem = []
        for p in remaining:
            ok = False
            for w, h in ((p['w'], p['h']), (p['h'], p['w'])):
                if w <= 0 or h <= 0:
                    continue
                if x + w <= bw and current_y + h <= bh:
                    placed.append((x, current_y, w, h, p['rid']))
                    x += w
                    row_max_h = max(row_max_h, h)
                    ok = True
                    break
            if not ok:
                new_rem.append(p)
        if row_max_h == 0:
            return None
        current_y += row_max_h
        remaining = new_rem
    return placed if len(placed) == len(rects_with_rid) else None


def _pack_column_left_aligned(rects_with_rid, bw, bh):
    """
    Укладка «колонками» слева направо: в колонке детали сверху вниз, левый край колонки на одной линии x = 0.
    Порядок — по убыванию площади. Поворот 90°. Координаты top-left, прижато к верхнему левому углу листа.
    Возвращает [(x, y, w, h, rid), ...] или None.
    """
    bw, bh = int(bw), int(bh)
    if bw <= 0 or bh <= 0 or not rects_with_rid:
        return None
    items = [
        {
            'w': max(0, int(w)),
            'h': max(0, int(h)),
            'rid': rid,
            'area': max(0, int(w)) * max(0, int(h)),
        }
        for (w, h, rid) in rects_with_rid
    ]
    items.sort(key=lambda t: (-t['area'], -max(t['w'], t['h']), -min(t['w'], t['h'])))
    remaining = items[:]
    placed = []
    current_x = 0
    while remaining:
        y = 0
        col_max_w = 0
        new_rem = []
        for p in remaining:
            ok = False
            for w, h in ((p['w'], p['h']), (p['h'], p['w'])):
                if w <= 0 or h <= 0:
                    continue
                if current_x + w <= bw and y + h <= bh:
                    placed.append((current_x, y, w, h, p['rid']))
                    y += h
                    col_max_w = max(col_max_w, w)
                    ok = True
                    break
            if not ok:
                new_rem.append(p)
        if col_max_w == 0:
            return None
        current_x += col_max_w
        remaining = new_rem
    return placed if len(placed) == len(rects_with_rid) else None


def _pack_guillotine_doc_recursive(rects_with_rid, bw, bh, variant_index=0):
    """
    Алгоритм 1 (ТЗ): сортировка экземпляров по убыванию max(w,h), затем площади;
    рекурсивное размещение в левом нижнем углу области с двумя гильотинными остатками
    (полоса справа на всю высоту и полоса сверху шириной детали); выбор ориентации по
    максимуму площади крупнейшего остатка, при равенстве — по сумме площадей остатков.
    Координаты внутри — bottom-left; результат — top-left (y вниз), как в остальном коде.
    variant_index — комбинация режима сортировки (младшие 3 бита) и политики порядка подобластей
    (следующие 2 бита): всего DOC_PACK_VARIANT_COUNT вариантов для «Другая схема» / лучший выбор.
    """
    bw, bh = int(bw), int(bh)
    if bw <= 0 or bh <= 0 or not rects_with_rid:
        return None
    items = [(max(0, int(w)), max(0, int(h)), int(rid)) for (w, h, rid) in rects_with_rid]
    sort_mode = int(variant_index) % 8
    subs_policy = (int(variant_index) // 8) % 4

    def sort_items(seq):
        def keyfun(t):
            w, h, rid = t
            ms = max(w, h)
            ar = w * h
            mn = min(w, h)
            sm = sort_mode
            if sm == 0:
                return (-ms, -ar, -mn, rid)
            if sm == 1:
                return (-ms, -ar, mn, rid)
            if sm == 2:
                return (-ms, -ar, -rid, mn)
            if sm == 3:
                return (-ms, -ar, mn, -rid)
            if sm == 4:
                return (-ms, -ar, -w, -h, rid)
            if sm == 5:
                return (-ms, -ar, -h, -w, rid)
            if sm == 6:
                return (-ms, -ar, w, h, rid)
            return (-ms, -ar, h, w, rid)

        return sorted(seq, key=keyfun)

    def place(rx, ry, rw, rh, seq):
        seq = sort_items(seq)
        if not seq:
            return []
        w0, h0, rid0 = seq[0]
        rest = list(seq[1:])
        best_ori = None
        best_key = None

        def consider(pw, ph):
            nonlocal best_ori, best_key
            if pw <= 0 or ph <= 0 or pw > rw or ph > rh:
                return
            right_a = (rw - pw) * rh if rw > pw else 0
            top_a = pw * (rh - ph) if rh > ph else 0
            primary = max(right_a, top_a)
            secondary = min(right_a, top_a) if right_a > 0 and top_a > 0 else max(right_a, top_a)
            tertiary = right_a + top_a
            key = (primary, secondary, tertiary, -ry, -rx)
            if best_key is None or key > best_key:
                best_key = key
                best_ori = (pw, ph)

        if w0 == h0:
            consider(w0, h0)
        else:
            consider(w0, h0)
            consider(h0, w0)
        if best_ori is None:
            return None
        pw, ph = best_ori
        subs = []
        if rw > pw:
            subs.append((rx + pw, ry, rw - pw, rh))
        if rh > ph:
            subs.append((rx, ry + ph, pw, rh - ph))
        if subs_policy == 0:
            subs.sort(key=lambda s: -(s[2] * s[3]))
        elif subs_policy == 1:
            subs.sort(key=lambda s: (s[2] * s[3]))
        elif subs_policy == 2:
            subs.sort(key=lambda s: -(s[2] * s[3]))
            if len(subs) == 2 and subs[0][2] * subs[0][3] == subs[1][2] * subs[1][3]:
                subs.reverse()
        else:
            subs.sort(key=lambda s: (-s[2], -s[3]))
        acc = [(rx, ry, pw, ph, rid0)]
        remaining = rest
        for sx, sy, srw, srh in subs:
            sub_pl = place(sx, sy, srw, srh, remaining)
            if sub_pl is None:
                return None
            placed_rids = {t[4] for t in sub_pl}
            remaining = [t for t in remaining if t[2] not in placed_rids]
            acc.extend(sub_pl)
        if remaining:
            return None
        return acc

    bl_pl = place(0, 0, bw, bh, items)
    if bl_pl is None:
        return None
    out = []
    for bx, by, pw, ph, rid in bl_pl:
        y_tl = bh - by - ph
        out.append((int(bx), int(y_tl), int(pw), int(ph), int(rid)))
    return out if len(out) == len(items) else None


def _pack_bl_max_largest_remnant(rects_with_rid, bw, bh):
    """
    Совместимость с прежним именем: алгоритм 1 из ТЗ, вариант 0.
    """
    return _pack_guillotine_doc_recursive(rects_with_rid, bw, bh, 0)


def _enumerate_doc_splits_vertical(rects, x0, y0, W, H):
    """Все вертикали x=c внутри области, дающие непустые левую и правую группы без пересечений."""
    out = []
    if len(rects) <= 1:
        return out
    xs = set()
    for (px, py, pw, ph) in rects:
        xs.add(px)
        xs.add(px + pw)
    for c in sorted(xs):
        if c <= x0 or c >= x0 + W:
            continue
        left = [r for r in rects if r[0] + r[2] <= c]
        right = [r for r in rects if r[0] >= c]
        if len(left) + len(right) != len(rects) or not left or not right:
            continue
        out.append((c, left, right))
    return out


def _enumerate_doc_splits_horizontal(rects, x0, y0, W, H):
    """Все горизонтали y=c (top-left), дающие верх/низ без пересечений."""
    out = []
    if len(rects) <= 1:
        return out
    ys = set()
    for (px, py, pw, ph) in rects:
        ys.add(py)
        ys.add(py + ph)
    for c in sorted(ys):
        if c <= y0 or c >= y0 + H:
            continue
        top = [r for r in rects if r[1] + r[3] <= c]
        bottom = [r for r in rects if r[1] >= c]
        if len(top) + len(bottom) != len(rects) or not top or not bottom:
            continue
        out.append((c, top, bottom))
    return out


def _try_doc_split_vertical(rects, x0, y0, W, H):
    """Первая допустимая вертикаль (совместимость)."""
    spl = _enumerate_doc_splits_vertical(rects, x0, y0, W, H)
    return spl[0] if spl else None


def _try_doc_split_horizontal(rects, x0, y0, W, H):
    """Первая допустимая горизонталь (совместимость)."""
    spl = _enumerate_doc_splits_horizontal(rects, x0, y0, W, H)
    return spl[0] if spl else None


def _rect_group_pieces(rects_with_p, pure_subgroup):
    keys = {(a, b, c, d) for (a, b, c, d) in pure_subgroup}
    return [t for t in rects_with_p if (t[0], t[1], t[2], t[3]) in keys]


def _doc_cut_segments_from_rects(
    rects_with_p,
    x0,
    y0,
    W,
    H,
    sheet_h,
    vertical_first=True,
    max_branches=DOC_CUT_TREE_MAX_BRANCHES,
):
    """
    Алгоритм 2 (ТЗ): дерево гильотинных разрезов по готовому размещению, preorder → cut_segments.
    rects_with_p: список (px, py, pw, ph, p_ref).
    При нескольких линиях реза перебираем варианты (ограничение max_branches на узел).
    vertical_first: на каждом узле сначала вертикали, затем горизонтали (как в ТЗ); False — обратный порядок.
    """
    if len(rects_with_p) <= 1:
        return []
    pure = [(t[0], t[1], t[2], t[3]) for t in rects_with_p]

    def branch_vertical(c, left_r, right_r):
        left_p = _rect_group_pieces(rects_with_p, left_r)
        right_p = _rect_group_pieces(rects_with_p, right_r)
        seg = {
            'type': 'V',
            'pos': int(c),
            'extent_lo': int(y0),
            'extent_hi': int(y0 + H),
            'row_iy': 0,
            'row_y_lo': 0,
            'row_y_hi': int(sheet_h),
        }
        wL = c - x0
        wR = (x0 + W) - c
        left_segs = _doc_cut_segments_from_rects(
            left_p, x0, y0, wL, H, sheet_h, vertical_first, max_branches
        )
        if left_segs is None:
            return None
        right_segs = _doc_cut_segments_from_rects(
            right_p, c, y0, wR, H, sheet_h, vertical_first, max_branches
        )
        if right_segs is None:
            return None
        return [seg] + left_segs + right_segs

    def branch_horizontal(c, top_r, bot_r):
        top_p = _rect_group_pieces(rects_with_p, top_r)
        bot_p = _rect_group_pieces(rects_with_p, bot_r)
        seg = {
            'type': 'H',
            'pos': int(c),
            'extent_lo': int(x0),
            'extent_hi': int(x0 + W),
            'row_iy': 0,
            'row_y_lo': 0,
            'row_y_hi': int(sheet_h),
        }
        hT = c - y0
        hB = (y0 + H) - c
        top_segs = _doc_cut_segments_from_rects(
            top_p, x0, y0, W, hT, sheet_h, vertical_first, max_branches
        )
        if top_segs is None:
            return None
        bot_segs = _doc_cut_segments_from_rects(
            bot_p, x0, c, W, hB, sheet_h, vertical_first, max_branches
        )
        if bot_segs is None:
            return None
        return [seg] + top_segs + bot_segs

    def try_order(v_before_h):
        if v_before_h:
            for c, left_r, right_r in _enumerate_doc_splits_vertical(pure, x0, y0, W, H)[:max_branches]:
                got = branch_vertical(c, left_r, right_r)
                if got is not None:
                    return got
            for c, top_r, bot_r in _enumerate_doc_splits_horizontal(pure, x0, y0, W, H)[:max_branches]:
                got = branch_horizontal(c, top_r, bot_r)
                if got is not None:
                    return got
        else:
            for c, top_r, bot_r in _enumerate_doc_splits_horizontal(pure, x0, y0, W, H)[:max_branches]:
                got = branch_horizontal(c, top_r, bot_r)
                if got is not None:
                    return got
            for c, left_r, right_r in _enumerate_doc_splits_vertical(pure, x0, y0, W, H)[:max_branches]:
                got = branch_vertical(c, left_r, right_r)
                if got is not None:
                    return got
        return None

    return try_order(vertical_first)


def assign_doc_guillotine_cut_segments_to_layout(layout_dict):
    """
    Построить cut_segments по алгоритму 2 (ТЗ). Возвращает True, если дерево найдено.
    """
    pieces = list(layout_dict.get('pieces') or [])
    try:
        sw = int(layout_dict.get('sheet_width') or 0)
        sh = int(layout_dict.get('sheet_height') or 0)
    except (TypeError, ValueError):
        return False
    if not pieces or sw <= 0 or sh <= 0:
        layout_dict['cut_segments'] = []
        return True
    rects_with_p = []
    for p in pieces:
        try:
            rects_with_p.append(
                (int(p['x']), int(p['y']), int(p['w']), int(p['h']), p)
            )
        except (TypeError, ValueError, KeyError):
            return False
    segs = _doc_cut_segments_from_rects(rects_with_p, 0, 0, sw, sh, sh, True)
    if segs is None:
        segs = _doc_cut_segments_from_rects(rects_with_p, 0, 0, sw, sh, sh, False)
    if segs is None:
        return False
    layout_dict['cut_segments'] = segs
    return True


def _best_pieces_from_rect_assignments(rects_with_rid, bw, bh, pieces_template, min_h, min_w):
    """
    По фиксированным (w,h) на каждый rid — укладка алгоритмом 1 из ТЗ (лучший вариант из DOC_PACK_VARIANT_COUNT по оценке),
    затем прижатие блока к верху-слева или низу-слева.
    Возвращает (score_tuple, pieces_list) или (None, None).
    """
    best_sc = None
    best_pcs = None

    def consider_packed(packed):
        nonlocal best_sc, best_pcs
        if not packed or len(packed) != len(rects_with_rid):
            return
        pcs = _packed_tuples_to_pieces(packed, pieces_template)
        sc, anchored = _pick_best_anchored_placement(pcs, bw, bh, min_h, min_w)
        if anchored is None:
            return
        if best_sc is None or sc < best_sc:
            best_sc = sc
            best_pcs = anchored

    best_pack = None
    best_key = None
    for vi in range(DOC_PACK_VARIANT_COUNT):
        packed = _pack_guillotine_doc_recursive(rects_with_rid, bw, bh, vi)
        if not packed or len(packed) != len(rects_with_rid):
            continue
        waste_area, business_area, _, max_business, max_quality = _score_packing(bw, bh, packed, min_h, min_w)
        key = (len(packed), -waste_area, business_area, max_business, max_quality)
        if best_key is None or key > best_key:
            best_key = key
            best_pack = packed
    consider_packed(best_pack)

    if best_pcs is None and _HAS_RECTPACK:
        rep_specs = []
        for (w, h, r) in rects_with_rid:
            d = dict(pieces_template[r])
            d['w'], d['h'] = int(w), int(h)
            rep_specs.append(d)
        rep = repack_pieces_on_sheet(bw, bh, rep_specs, min_h, min_w)
        if rep:
            sc, anchored = _pick_best_anchored_placement(rep, bw, bh, min_h, min_w)
            if anchored is not None:
                best_sc, best_pcs = sc, anchored

    return best_sc, best_pcs


FULL_ROTATION_ENUM_LIMIT = 32768


def enumerate_piece_rotations_best_layout(
    rects_with_rid, bw, bh, pieces_template, min_h, min_w, max_combinations=FULL_ROTATION_ENUM_LIMIT
):
    """
    Полный перебор поворотов 90° по каждой детали с разными (w,h) при w≠h.
    Если комбинаций слишком много — откат на жадный алгоритм.
    """
    opts = []
    for (w, h, r) in rects_with_rid:
        if w == h:
            opts.append([(w, h, r)])
        else:
            opts.append([(w, h, r), (h, w, r)])
    ncomb = 1
    for o in opts:
        ncomb *= len(o)
    if ncomb > max_combinations:
        return greedy_improve_piece_rotations(rects_with_rid, bw, bh, pieces_template, min_h, min_w)

    best_sc = None
    best_pcs = None
    for combo in itertools.product(*opts):
        rid_list = list(combo)
        sc, pcs = _best_pieces_from_rect_assignments(rid_list, bw, bh, pieces_template, min_h, min_w)
        if pcs is None:
            continue
        if best_sc is None or sc < best_sc:
            best_sc = sc
            best_pcs = pcs
    return best_pcs


def greedy_improve_piece_rotations(rects_with_rid, bw, bh, pieces_template, min_h, min_w):
    """
    Жадно: на каждом шаге пробуем повернуть на 90° одну деталь (меняем w↔h только у неё),
    пересчитываем лучшую укладку среди полок/колонок/repack; если оценка улучшилась — принимаем и повторяем.
    """
    rid = [(int(w), int(h), int(r)) for (w, h, r) in rects_with_rid]
    max_rounds = max(12, len(rid) * 4)
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        base_sc, base_pcs = _best_pieces_from_rect_assignments(rid, bw, bh, pieces_template, min_h, min_w)
        if base_pcs is None:
            return None
        best_rid = rid
        best_sc = base_sc
        best_pcs = base_pcs
        for i in range(len(rid)):
            w, h, r = rid[i]
            if w == h:
                continue
            trial = list(rid)
            trial[i] = (h, w, r)
            sc2, pcs2 = _best_pieces_from_rect_assignments(trial, bw, bh, pieces_template, min_h, min_w)
            if pcs2 is not None and sc2 < best_sc:
                best_sc = sc2
                best_rid = trial
                best_pcs = pcs2
        if best_rid == rid:
            return best_pcs
        rid = best_rid
    return best_pcs


def _layout_respects_min_strip(bw, bh, placed_tuples, min_strip):
    """
    Проверка: все свободные прямоугольники после упаковки имеют min(ширина, высота) >= min_strip.
    Иначе раскрой потребует отрезать полосу уже min_strip (невозможно). placed_tuples = [(x, y, w, h, rid), ...] (top-left).
    """
    if min_strip <= 0:
        return True
    free_bl = [(0, 0, bw, bh)]
    for (x, y_top, w, h, _) in placed_tuples:
        bl_y = bh - y_top - h
        free_bl = _subtract_rect(free_bl, (x, bl_y, w, h))
    for (fx, fy, fw, fh) in free_bl:
        if fw <= 0 or fh <= 0:
            continue
        if min(fw, fh) < min_strip:
            return False
    return True


def _pack_one_bin_single(rects_with_rid, bw, bh, pack_algo, sort_algo, min_h=0, min_w=0):
    """Одна комбинация (pack_algo, sort_algo): упаковать в (bw, bh), вернуть packed_tuples или None."""
    if not _HAS_RECTPACK or not rects_with_rid or pack_algo is None:
        return None
    packer = newPacker(mode=PackingMode.Offline, sort_algo=sort_algo, rotation=True, pack_algo=pack_algo)
    packer.add_bin(int(bw), int(bh))
    for (w, h, rid) in rects_with_rid:
        packer.add_rect(int(w), int(h), rid)
    packer.pack()
    packed = packer.rect_list()
    if len(packed) != len(rects_with_rid):
        return None
    return _normalize_packed_tuples_top_left(
        [(r[1], bh - r[2] - r[4], r[3], r[4], r[5]) for r in packed]
    )


def _pack_one_bin_with_variant(rects_with_rid, bw, bh, layout_variant, min_h, min_w):
    """
    Упаковать прямоугольники в один контейнер, используя фиксированную комбинацию алгоритма для варианта 0..3.
    Возвращает packed_tuples = [(x, y, w, h, rid), ...] или None если не всё поместилось.
    """
    if not _HAS_RECTPACK or not rects_with_rid:
        return None
    pack_algos, sort_algos, _, force_rotated = _variant_pack_and_sort(layout_variant)
    if force_rotated:
        bw, bh = bh, bw
    pack_algo = pack_algos[0] if pack_algos else (MaxRectsBl if MaxRectsBl else None)
    sort_algo = sort_algos[0] if sort_algos else SORT_AREA
    return _pack_one_bin_single(rects_with_rid, bw, bh, pack_algo, sort_algo, min_h, min_w)


def _thin_waste_penalty(waste_rects):
    """Штраф за «полоски» отхода (большое отношение сторон): хуже длинная узкая полоса при той же площади."""
    pen = 0.0
    for r in waste_rects or []:
        w, h = float(r.get('w') or 0), float(r.get('h') or 0)
        if w <= 0 or h <= 0:
            continue
        M, m = max(w, h), min(w, h)
        if m < 1.0:
            continue
        ar = M / m
        if ar > 6.0:
            area = w * h
            pen += area * (ar / 6.0 - 1.0) * 0.55
    return int(round(pen))


def _layout_score_for_variant(
    business_rects,
    waste_rects,
    min_h,
    min_w,
    pieces=None,
    sheet_width=0,
    sheet_height=0,
):
    """Оценка варианта. Кортеж — меньше по лексикографии — лучше.

    1) Суммарная площадь деловых остатков (больше — лучше).
    2) «Крупность» с учётом доли листа: для каждого остатка площадь × (площадь / площадь листа);
       чем больше эта величина, тем выше ценность (меньше дробление на мелкие куски при той же сумме).
    3) Число деловых остатков — меньше фрагментов лучше.
    4) Отходы: площадь (со штрафом за узкие полосы), число кусков мусора, число зон.
    """
    total_waste = sum((r.get('w') or 0) * (r.get('h') or 0) for r in waste_rects)
    total_waste_eff = total_waste + _thin_waste_penalty(waste_rects)
    n_waste = len(waste_rects or [])
    n_free_slots = len(business_rects or []) + n_waste
    business_areas = []
    for r in business_rects or []:
        w, h = r.get('w') or 0, r.get('h') or 0
        if _is_business_rect(w, h, min_h, min_w):
            business_areas.append(w * h)
    total_business = sum(business_areas)
    n_business = len(business_areas)
    try:
        sw = int(sheet_width or 0)
        sh = int(sheet_height or 0)
    except (TypeError, ValueError):
        sw, sh = 0, 0
    sheet_area = max(0, sw * sh)
    if sheet_area > 0 and business_areas:
        # sum_i (area_i / S) * area_i — крупные остатки как доля листа весят сильнее
        pct_mass = float(sum(a * a for a in business_areas)) / float(sheet_area)
    else:
        pct_mass = 0.0
    return (
        -total_business,
        -pct_mass,
        n_business,
        total_waste_eff,
        n_waste,
        n_free_slots,
    )


def _pack_with_first_cut(rects_with_rid, sw, sh, cut_pos, vertical_cut, pack_algo, sort_algo, pieces_list, min_h, min_w, min_strip=0):
    """
    Дерево резов: сначала один гильотинный рез (вертикальный или горизонтальный), затем упаковка в две области.
    min_strip: минимальная ширина отрезаемой полосы (мм) по толщине стекла — каждая из двух частей не меньше min_strip.
    Возвращает layout dict или None.
    """
    if not _HAS_RECTPACK or not rects_with_rid or pack_algo is None:
        return None
    strip_lim = max(10, min_strip)
    if vertical_cut:
        left_w, left_h = cut_pos, sh
        right_w, right_h = sw - cut_pos, sh
        if left_w < strip_lim or right_w < strip_lim:
            return None
    else:
        left_w, left_h = sw, cut_pos
        right_w, right_h = sw, sh - cut_pos
        if left_h < strip_lim or right_h < strip_lim:
            return None
    packer = newPacker(mode=PackingMode.Offline, sort_algo=sort_algo, rotation=True, pack_algo=pack_algo)
    packer.add_bin(int(left_w), int(left_h))
    for (w, h, rid) in rects_with_rid:
        packer.add_rect(int(w), int(h), rid)
    packer.pack()
    left_packed = packer.rect_list()
    placed_rids = set(r[5] for r in left_packed)
    remaining = [(w, h, rid) for (w, h, rid) in rects_with_rid if rid not in placed_rids]
    if not remaining or len(remaining) == len(rects_with_rid):
        return None
    packer2 = newPacker(mode=PackingMode.Offline, sort_algo=sort_algo, rotation=True, pack_algo=pack_algo)
    packer2.add_bin(int(right_w), int(right_h))
    for (w, h, rid) in remaining:
        packer2.add_rect(int(w), int(h), rid)
    packer2.pack()
    right_packed = packer2.rect_list()
    if len(right_packed) != len(remaining):
        return None
    if vertical_cut:
        left_tuples = [(r[1], left_h - r[2] - r[4], r[3], r[4], r[5]) for r in left_packed]
        right_tuples = [(cut_pos + r[1], right_h - r[2] - r[4], r[3], r[4], r[5]) for r in right_packed]
    else:
        left_tuples = [(r[1], left_h - r[2] - r[4], r[3], r[4], r[5]) for r in left_packed]
        right_tuples = [(r[1], cut_pos + (right_h - r[2] - r[4]), r[3], r[4], r[5]) for r in right_packed]
    all_tuples, mx, my = _normalize_packed_tuples_top_left_mins(left_tuples + right_tuples)
    if vertical_cut:
        cut_seg = {
            'type': 'V',
            'pos': int(cut_pos - mx),
            'extent_lo': 0,
            'extent_hi': sh,
            'row_iy': 0,
            'row_y_lo': 0,
            'row_y_hi': sh,
        }
    else:
        cut_seg = {
            'type': 'H',
            'pos': int(cut_pos - my),
            'extent_lo': 0,
            'extent_hi': sw,
            'row_iy': 0,
            'row_y_lo': 0,
            'row_y_hi': sh,
        }
    new_pieces = []
    for (x, y, w, h, rid) in all_tuples:
        p = dict(pieces_list[rid])
        p['x'] = int(x)
        p['y'] = int(y)
        p['w'] = int(w)
        p['h'] = int(h)
        new_pieces.append(p)
    business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, new_pieces, min_h, min_w)
    return {
        'pieces': new_pieces,
        'business_rects': business_rects,
        'waste_rects': waste_rects,
        'sheet_width': sw,
        'sheet_height': sh,
        'rotated': False,
        'cut_segments': [cut_seg],
    }


def _pack_h_band_then_v_in_band_then_bottom(
    rects_with_rid, sw, sh, h_band, v_pos, pack_algo, sort_algo, pieces_list, min_h, min_w, min_strip=0
):
    """
    Двухуровневая гильотина: (1) горизонтальный рез на всю ширину на высоте h_band — отделяет верхнюю
    полосу sw×h_band от низа; (2) вертикальный рез только внутри полосы (0…h_band) на x=v_pos;
    (3) снизу — целый прямоугольник sw×(sh−h_band). Упаковка: сначала в левую верхнюю ячейку v_pos×h_band,
    остаток — в правую верхнюю (sw−v_pos)×h_band, затем не поместившиеся — в низ.
    Так один «лишний» полноширинный пропил посередине полосы (как 571 мм при высоте рядов 571+255)
    не режет правый зелёный остаток на два куска — сначала отрезаем полосу по высоте 826, потом режем её по вертикали.
    """
    if not _HAS_RECTPACK or not rects_with_rid or pack_algo is None:
        return None
    strip_lim = max(10, int(min_strip))
    h_band = int(h_band)
    v_pos = int(v_pos)
    sw, sh = int(sw), int(sh)
    if h_band < strip_lim or (sh - h_band) < strip_lim:
        return None
    if v_pos < strip_lim or (sw - v_pos) < strip_lim:
        return None
    bottom_h = sh - h_band

    packer = newPacker(mode=PackingMode.Offline, sort_algo=sort_algo, rotation=True, pack_algo=pack_algo)
    packer.add_bin(int(v_pos), int(h_band))
    for (w, h, rid) in rects_with_rid:
        packer.add_rect(int(w), int(h), rid)
    packer.pack()
    left_packed = packer.rect_list()
    if not left_packed:
        return None
    placed_left = {r[5] for r in left_packed}
    remaining = [(w, h, rid) for (w, h, rid) in rects_with_rid if rid not in placed_left]
    left_tuples = [(r[1], h_band - r[2] - r[4], r[3], r[4], r[5]) for r in left_packed]

    right_tuples = []
    if remaining:
        packer2 = newPacker(mode=PackingMode.Offline, sort_algo=sort_algo, rotation=True, pack_algo=pack_algo)
        packer2.add_bin(int(sw - v_pos), int(h_band))
        for (w, h, rid) in remaining:
            packer2.add_rect(int(w), int(h), rid)
        packer2.pack()
        right_packed = packer2.rect_list()
        if len(right_packed) != len(remaining):
            return None
        right_tuples = [(v_pos + r[1], h_band - r[2] - r[4], r[3], r[4], r[5]) for r in right_packed]
        placed_right = {r[5] for r in right_packed}
        remaining2 = [(w, h, rid) for (w, h, rid) in rects_with_rid if rid not in placed_left and rid not in placed_right]
    else:
        remaining2 = []

    bottom_tuples = []
    if remaining2:
        packer3 = newPacker(mode=PackingMode.Offline, sort_algo=sort_algo, rotation=True, pack_algo=pack_algo)
        packer3.add_bin(int(sw), int(bottom_h))
        for (w, h, rid) in remaining2:
            packer3.add_rect(int(w), int(h), rid)
        packer3.pack()
        bottom_packed = packer3.rect_list()
        if len(bottom_packed) != len(remaining2):
            return None
        bottom_tuples = [
            (r[1], h_band + (bottom_h - r[2] - r[4]), r[3], r[4], r[5]) for r in bottom_packed
        ]

    raw_all = left_tuples + right_tuples + bottom_tuples
    if len(raw_all) != len(rects_with_rid):
        return None

    all_tuples, mx, my = _normalize_packed_tuples_top_left_mins(raw_all)
    new_pieces = []
    for (x, y, w, h, rid) in all_tuples:
        p = dict(pieces_list[rid])
        p['x'] = int(x)
        p['y'] = int(y)
        p['w'] = int(w)
        p['h'] = int(h)
        new_pieces.append(p)

    h_line = int(h_band - my)
    v_line = int(v_pos - mx)
    cut_h = {
        'type': 'H',
        'pos': h_line,
        'extent_lo': 0,
        'extent_hi': sw,
        'row_iy': 0,
        'row_y_lo': 0,
        'row_y_hi': sh,
    }
    cut_v = {
        'type': 'V',
        'pos': v_line,
        'extent_lo': 0,
        'extent_hi': h_line,
        'row_iy': 0,
        'row_y_lo': 0,
        'row_y_hi': sh,
    }
    business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, new_pieces, min_h, min_w)
    return {
        'pieces': new_pieces,
        'business_rects': business_rects,
        'waste_rects': waste_rects,
        'sheet_width': sw,
        'sheet_height': sh,
        'rotated': False,
        'cut_segments': [cut_h, cut_v],
    }


def _try_append_best_internal_vertical_spine_cut(layout_dict, min_strip, min_h, min_w):
    """
    Один дополнительный вертикальный рез по правому краю «узкой» группы деталей (все целиком слева от линии),
    от минимального y этой группы до низа листа. Часто даёт один крупный остаток справа от колонки (*1, узкие)
    вместо двух горизонтальных полос отхода (см. 858×333 + 747×222).
    """
    pieces = list(layout_dict.get('pieces') or [])
    segs = list(layout_dict.get('cut_segments') or [])
    if len(pieces) < 2 or not segs:
        return
    try:
        sw = int(layout_dict.get('sheet_width') or 0)
        sh = int(layout_dict.get('sheet_height') or 0)
    except (TypeError, ValueError):
        return
    if sw <= 0 or sh <= 0:
        return
    lim = max(10, int(min_strip))
    mh, mw = int(min_h or 0), int(min_w or 0)
    x1_cluster = int(round(max(float(p.get('x', 0)) + float(p.get('w', 0)) for p in pieces)))

    def _score(seglist):
        br, wr = apply_cut_segments_to_free_rects(sw, sh, pieces, seglist, mh, mw)
        br = merge_adjacent_free_rects_for_display(br)
        wr = merge_adjacent_free_rects_for_display(wr)
        return _layout_score_for_variant(br, wr, mh, mw, pieces, sw, sh)

    existing_v = {
        int(round(float(s.get('pos', 0))))
        for s in segs
        if str(s.get('type') or '').strip().upper() == 'V'
    }

    best = list(segs)
    best_sc = _score(best)

    candidates_x = sorted(
        {int(round(float(p.get('x', 0)) + float(p.get('w', 0)))) for p in pieces}
    )

    for xv in candidates_x:
        if xv < lim or xv > x1_cluster - lim or xv >= sw - lim:
            continue
        if xv in existing_v:
            continue
        xv_f = float(xv)
        left_p = [
            p for p in pieces
            if float(p.get('x', 0)) + float(p.get('w', 0)) <= xv_f + 0.51
        ]
        right_p = [p for p in pieces if float(p.get('x', 0)) >= xv_f - 0.51]
        if not left_p or not right_p:
            continue
        try:
            y_lo = int(round(min(float(p.get('y', 0)) for p in left_p)))
        except (TypeError, ValueError):
            continue
        if y_lo < 0 or sh - y_lo < lim:
            continue
        # Пересечение только по реальному участку реза [y_lo, sh], как в _rect_crosses_cut_segment
        elo, ehi = float(y_lo), float(sh)
        crosses = False
        for p in pieces:
            try:
                px, py = float(p.get('x', 0)), float(p.get('y', 0))
                pw, ph = float(p.get('w', 0)), float(p.get('h', 0))
            except (TypeError, ValueError):
                continue
            if not (px < xv_f < px + pw):
                continue
            if max(py, elo) < min(py + ph, ehi):
                crosses = True
                break
        if crosses:
            continue
        cand = {
            'type': 'V',
            'pos': int(xv),
            'extent_lo': y_lo,
            'extent_hi': int(sh),
            'row_iy': 0,
            'row_y_lo': 0,
            'row_y_hi': sh,
        }
        order_trials = [list(segs) + [cand]]
        if len(segs) >= 1:
            order_trials.append(segs[:-1] + [cand] + [segs[-1]])
        for trial in order_trials:
            sc = _score(trial)
            if sc < best_sc:
                best_sc = sc
                best = trial
            elif sc == best_sc:
                _, wr_a = apply_cut_segments_to_free_rects(sw, sh, pieces, trial, mh, mw)
                _, wr_b = apply_cut_segments_to_free_rects(sw, sh, pieces, best, mh, mw)
                nw_a = len(merge_adjacent_free_rects_for_display(wr_a))
                nw_b = len(merge_adjacent_free_rects_for_display(wr_b))
                if nw_a < nw_b:
                    best = trial

    layout_dict['cut_segments'] = best


def _assign_chocolate_bar_cut_segments_legacy(layout_dict, min_strip=10, force_order=None, min_h=0, min_w=0):
    """
    Запасной вариант резов («шоколадка»), если по готовому размещению не удаётся построить дерево алгоритма 2.
    """
    pieces = list(layout_dict.get('pieces') or [])
    try:
        sw = int(layout_dict.get('sheet_width') or 0)
        sh = int(layout_dict.get('sheet_height') or 0)
    except (TypeError, ValueError):
        return
    if not pieces or sw <= 0 or sh <= 0:
        return
    lim = max(10, int(min_strip))
    y0 = int(round(min(float(p.get('y', 0)) for p in pieces)))
    y1 = int(round(max(float(p.get('y', 0)) + float(p.get('h', 0)) for p in pieces)))
    x0 = int(round(min(float(p.get('x', 0)) for p in pieces)))
    x1 = int(round(max(float(p.get('x', 0)) + float(p.get('w', 0)) for p in pieces)))
    top_h = y0
    left_w = x0
    right_w = sw - x1
    bottom_h = sh - y1
    top_area = top_h * sw if top_h >= lim else -1
    left_area = left_w * sh if left_w >= lim else -1
    right_area = right_w * sh if right_w >= lim else -1
    bottom_area = bottom_h * sw if bottom_h >= lim else -1

    def _h(pos, elo, ehi):
        return {
            'type': 'H',
            'pos': int(pos),
            'extent_lo': int(elo),
            'extent_hi': int(ehi),
            'row_iy': 0,
            'row_y_lo': 0,
            'row_y_hi': sh,
        }

    def _v(pos, ylo, yhi):
        return {
            'type': 'V',
            'pos': int(pos),
            'extent_lo': int(ylo),
            'extent_hi': int(yhi),
            'row_iy': 0,
            'row_y_lo': 0,
            'row_y_hi': sh,
        }

    def _segments_for_h_first(h_first_flag):
        segs = []
        if h_first_flag:
            if top_area >= 0:
                segs.append(_h(y0, 0, sw))
            if right_area >= 0:
                y_lo_v = y0 if top_area >= 0 else 0
                segs.append(_v(x1, y_lo_v, sh))
        else:
            if right_area >= 0:
                segs.append(_v(x1, 0, sh))
            if top_area >= 0:
                segs.append(_h(y0, 0, x1))
        return segs

    def _segments_bottom_first():
        """Сначала полноширинный H по нижней границе кластера деталей (отщепить большой низ), затем V только
        в зоне деталей [y0,y1] — правый остаток сверху один, большой низ не режется вертикалью."""
        segs = []
        if bottom_area >= 0:
            segs.append(_h(y1, 0, sw))
        if right_area >= 0 and y1 > y0:
            segs.append(_v(x1, y0, y1))
        return segs

    if top_area < 0 and right_area < 0 and bottom_area < 0 and left_area < 0:
        layout_dict['cut_segments'] = []
        return

    bottom_seg_opt = _segments_bottom_first()

    def _score_chocolate_segs(seg_list):
        """Меньше кортеж — лучше: крупнейший один деловой остаток, меньше резов, затем общая оценка."""
        mh, mw = int(min_h or 0), int(min_w or 0)
        br, wr = apply_cut_segments_to_free_rects(sw, sh, pieces, seg_list, mh, mw)
        br = merge_adjacent_free_rects_for_display(br)
        wr = merge_adjacent_free_rects_for_display(wr)
        max_bus = 0
        for r in br or []:
            w, h = r.get('w') or 0, r.get('h') or 0
            if _is_business_rect(w, h, mh, mw):
                max_bus = max(max_bus, w * h)
        base = _layout_score_for_variant(br, wr, mh, mw, pieces, sw, sh)
        return (-max_bus, len(seg_list or []), base)

    def _seg_key(seg_list):
        return tuple(
            (s.get('type'), int(s.get('pos', 0)), int(s.get('extent_lo', 0)), int(s.get('extent_hi', 0)))
            for s in seg_list
        )

    chosen = None
    if force_order == 'HV':
        if top_area >= 0 and right_area >= 0:
            chosen = _segments_for_h_first(True)
        elif bottom_seg_opt:
            chosen = bottom_seg_opt
        else:
            chosen = _segments_for_h_first(True)
    elif force_order == 'VH':
        if top_area >= 0 and right_area >= 0:
            chosen = _segments_for_h_first(False)
        elif bottom_seg_opt:
            chosen = bottom_seg_opt
        else:
            chosen = _segments_for_h_first(False)
    else:
        candidates = []
        seen_k = set()

        def add_cand(segs):
            if not segs:
                return
            k = _seg_key(segs)
            if k in seen_k:
                return
            seen_k.add(k)
            candidates.append(segs)

        if top_area >= 0 and right_area >= 0:
            add_cand(_segments_for_h_first(True))
            add_cand(_segments_for_h_first(False))
        if bottom_seg_opt:
            add_cand(bottom_seg_opt)
        # Один гильотинный рез по краю листа — отложить целый остаток (не проводить полосу через большой «столбец» справа/слева).
        if top_area >= 0:
            add_cand([_h(y0, 0, sw)])
        if bottom_area >= 0:
            add_cand([_h(y1, 0, sw)])
        if right_area >= 0:
            add_cand([_v(x1, 0, sh)])
        if left_area >= 0:
            add_cand([_v(x0, 0, sh)])

        if not candidates:
            layout_dict['cut_segments'] = []
            return
        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            chosen = min(candidates, key=_score_chocolate_segs)

    layout_dict['cut_segments'] = chosen if chosen is not None else []
    _try_append_best_internal_vertical_spine_cut(layout_dict, lim, int(min_h or 0), int(min_w or 0))


def assign_chocolate_bar_cut_segments_to_layout(layout_dict, min_strip=10, force_order=None, min_h=0, min_w=0):
    """Сначала алгоритм 2 из ТЗ (дерево гильотинных резов); иначе — прежняя «шоколадка»."""
    if assign_doc_guillotine_cut_segments_to_layout(layout_dict):
        return
    _assign_chocolate_bar_cut_segments_legacy(layout_dict, min_strip, force_order, min_h, min_w)


def refresh_cut_segments_for_layout(layout_dict, min_h=0, min_w=0):
    """
    После слияния листов или смены pieces/business_rects пересчитать cut_segments/cut_rows.
    Иначе в сводке и PDF остаются старые линии реза — визуально «детали друг на друга», неверные резы.
    """
    if not isinstance(layout_dict, dict):
        return
    layout_dict.pop('cut_segments', None)
    layout_dict.pop('cut_rows', None)
    th = int(layout_dict.get('thickness_mm') or 4)
    min_strip = _min_strip_for_thickness(th)
    assign_chocolate_bar_cut_segments_to_layout(
        layout_dict, min_strip, min_h=int(min_h or 0), min_w=int(min_w or 0)
    )


def compute_layout_variants_for_one_sheet(
    sheet_width, sheet_height, pieces, min_h=0, min_w=0, thickness_mm=None, max_variants=4
):
    """
    Несколько геометрически разных раскладок: алгоритм 1 из ТЗ с полным перебором variant_index (0..DOC_PACK_VARIANT_COUNT-1).
    Резы для каждого варианта — алгоритм 2 (дерево), см. assign_chocolate_bar_cut_segments_to_layout.
    thickness_mm: минимальная ширина полосы для доп. эвристики _try_extra_cut.
    """
    max_variants = max(1, min(int(max_variants), 4))
    if not pieces:
        return []
    rects_with_rid = [(int(p.get('w', 0)), int(p.get('h', 0)), i) for i, p in enumerate(pieces)]
    sw = int(sheet_width)
    sh = int(sheet_height)
    candidates = []
    seen = set()
    heuristic_keys = set()

    def _append_packed_layout(packed, bw, bh, rotated, from_heuristic=False):
        if not packed or len(packed) != len(rects_with_rid):
            return
        new_pieces = []
        for (x, y, w, h, rid) in packed:
            p = dict(pieces[rid])
            p['x'] = int(x)
            p['y'] = int(y)
            p['w'] = int(w)
            p['h'] = int(h)
            new_pieces.append(p)
        _, anchored = _pick_best_anchored_placement(new_pieces, bw, bh, min_h, min_w)
        if anchored is None:
            return
        new_pieces = anchored
        key = tuple(sorted((p['x'], p['y'], p['w'], p['h']) for p in new_pieces))
        if key in seen:
            return
        seen.add(key)
        if from_heuristic:
            heuristic_keys.add(key)
        if len(new_pieces) > 1 and not pieces_edge_connected(new_pieces):
            return
        business_rects, waste_rects = recompute_free_rects_from_pieces(bw, bh, new_pieces, min_h, min_w)
        score = _layout_score_for_variant(
            business_rects, waste_rects, min_h, min_w, new_pieces, bw, bh
        )
        candidates.append((score, {
            'pieces': new_pieces,
            'business_rects': business_rects,
            'waste_rects': waste_rects,
            'sheet_width': bw,
            'sheet_height': bh,
            'rotated': rotated,
        }))

    bw, bh = sw, sh
    for vi in range(DOC_PACK_VARIANT_COUNT):
        packed = _pack_guillotine_doc_recursive(rects_with_rid, bw, bh, vi)
        _append_packed_layout(packed, bw, bh, False, from_heuristic=True)

    def _count_unique_geo():
        keys = set()
        for _, lay in candidates:
            keys.add(tuple(sorted((p['x'], p['y'], p['w'], p['h']) for p in lay['pieces'])))
        return len(keys)

    # Раньше добивали кандидатов только до 2 уникальных геометрий — в UI оставалось 2 «других схемы».
    # Нужно набирать до max_variants (4) различных раскладок, как при полном переборе rectpack.
    if _count_unique_geo() < max_variants:
        for pack_fn in (_pack_shelf_bottom_aligned, _pack_shelf_top_aligned, _pack_column_left_aligned):
            if _count_unique_geo() >= max_variants:
                break
            packed = pack_fn(rects_with_rid, bw, bh)
            _append_packed_layout(packed, bw, bh, False, from_heuristic=False)
    if _count_unique_geo() < max_variants and _HAS_RECTPACK:
        palgos = _GUILLOTINE_ALGOS if _GUILLOTINE_ALGOS else ([MaxRectsBl] if MaxRectsBl else [])
        salgos = [SORT_AREA]
        if SORT_LSIDE is not None:
            salgos.append(SORT_LSIDE)
        if SORT_RATIO is not None:
            salgos.append(SORT_RATIO)
        salgos = salgos + list(_EXTRA_SORT_ALGOS)
        for pack_algo in palgos:
            if pack_algo is None:
                continue
            if _count_unique_geo() >= max_variants:
                break
            for sort_algo in salgos:
                packed = _pack_one_bin_single(rects_with_rid, sw, sh, pack_algo, sort_algo, min_h, min_w)
                _append_packed_layout(packed, sw, sh, False, from_heuristic=False)
                if _count_unique_geo() >= max_variants:
                    break

    min_strip = _min_strip_for_thickness(int(thickness_mm) if thickness_mm is not None else 4)
    candidates.sort(key=lambda x: x[0])

    def _lay_geo_key(lay):
        return tuple(sorted((p['x'], p['y'], p['w'], p['h']) for p in lay['pieces']))

    picked = []
    picked_keys = set()
    # Сначала показываем все различные эвристики (полка снизу / колонка слева), иначе по оценке часто
    # в топ-4 попадают только варианты rectpack — в конструкторе «Другая схема» выглядит «как раньше».
    for _, lay in candidates:
        k = _lay_geo_key(lay)
        if k in heuristic_keys and k not in picked_keys:
            picked_keys.add(k)
            picked.append(lay)
        if len(picked) >= max_variants:
            break
    if len(picked) < max_variants:
        for _, lay in candidates:
            k = _lay_geo_key(lay)
            if k in picked_keys:
                continue
            picked_keys.add(k)
            picked.append(lay)
            if len(picked) >= max_variants:
                break

    results = []
    for lay in picked[:max_variants]:
        _try_extra_cut(lay, min_h, min_w, min_strip)
        assign_chocolate_bar_cut_segments_to_layout(lay, min_strip, min_h=min_h, min_w=min_w)
        results.append(lay)
    return results


def _variant_pack_and_sort(layout_variant):
    """
    Для кнопки «Другие варианты»: два варианта с листом как есть, два — с первым листом повёрнутым на 90°.
    Bssf (Best Short Side First) — резы по короткой стороне; Blsf (Best Long Side First) — по длинной.
    Возвращает (pack_algos, sort_algos, order_big_first_first, force_rotated_first).
    """
    pack_all = _GUILLOTINE_ALGOS if _GUILLOTINE_ALGOS else ([MaxRectsBl] if MaxRectsBl else [])
    pack_blsf = [a for a in (_GUILLOTINE_ALGOS or []) if 'Blsf' in (getattr(a, '__name__', '') or '')] if _GUILLOTINE_ALGOS else pack_all
    pack_bssf = [a for a in (_GUILLOTINE_ALGOS or []) if 'Bssf' in (getattr(a, '__name__', '') or '')] if _GUILLOTINE_ALGOS else pack_all
    sort_area = [SORT_AREA]
    sort_lside = [SORT_LSIDE] if SORT_LSIDE is not None else []
    sort_ratio = [SORT_RATIO] if SORT_RATIO is not None else []
    force_rotated = layout_variant in (2, 3)
    if layout_variant == 0:
        return pack_bssf or pack_all, sort_area + sort_lside + sort_ratio, False, force_rotated
    if layout_variant == 1:
        return pack_blsf or pack_all, sort_lside + sort_area, True, force_rotated
    if layout_variant == 2:
        return pack_bssf or pack_all, sort_ratio + sort_area, False, force_rotated
    if layout_variant == 3:
        return pack_blsf or pack_all, sort_ratio + sort_lside + sort_area, True, force_rotated
    return pack_all, sort_area + sort_lside + sort_ratio, False, False


def compute_cutting_layout(order_items, get_sheets_for_material, get_threshold_for_material, prefer_height_cuts=False, fixed_first_sheet=None, layout_variant=None):
    """
    order_items: list of dicts with keys: material_name, height_mm, width_mm, quantity, recipient_text, thickness_mm (optional, default 4)
    get_sheets_for_material(material_name, thickness_mm=None) -> list of dicts: id, height_mm, width_mm, sheet_type ('remnant'|'full'), thickness_mm (int)
        When thickness_mm is set, return only sheets of that thickness. Order: remnants first, then full sheets.
    get_threshold_for_material(material_name, thickness_mm=None) -> dict with min_height_mm, min_width_mm or None
    prefer_height_cuts: если True — приоритет разрезов по высоте (другие варианты раскроя).
    fixed_first_sheet: опционально {(material, thickness_mm): {'sheet_id': int, 'sheet_type': 'full'|'remnant'}} или {material: {...}} — сначала упаковать на этот лист.
    layout_variant: 0..3 — вариант для «Другие варианты» (разные резы по X/Y и ориентация листа).

    Returns:
        result: {
            'layouts': [  # one per used sheet
                {
                    'sheet_id': int,
                    'sheet_type': 'remnant'|'full',
                    'material': str,
                    'sheet_width': int,
                    'sheet_height': int,
                    'pieces': [{'x': int, 'y': int, 'w': int, 'h': int, 'recipient': str, 'quantity_label': str, 'rotated': bool}],
                    'business_rects': [{'x': int, 'y': int, 'w': int, 'h': int}],
                    'waste_rects': [{'x': int, 'y': int, 'w': int, 'h': int}],
                }
            ],
            'new_remnants': [{'name': str, 'height_mm': int, 'width_mm': int}],  # to insert into DB
            'used_sheet_ids': [{'sheet_type': str, 'sheet_id': int}],
            'errors': [str],  # e.g. "На 2 изделий 300x400 мм не хватает материала Стекло 4мм"
        }
    """
    # Group items by (material, thickness_mm)
    by_material_thickness = {}
    for it in order_items:
        mat = it['material_name']
        thick = int(it.get('thickness_mm') or 4)
        key = (mat, thick)
        if key not in by_material_thickness:
            by_material_thickness[key] = []
        by_material_thickness[key].append(it)

    result = {
        'layouts': [],
        'new_remnants': [],
        'used_sheet_ids': [],
        'errors': [],
    }

    for (material, thickness_mm), items in by_material_thickness.items():
        sheets = get_sheets_for_material(material, thickness_mm)
        threshold = get_threshold_for_material(material, thickness_mm)
        if threshold is None:
            min_h, min_w = 0, 0
        else:
            min_h, min_w = threshold.get('min_height_mm', 0), threshold.get('min_width_mm', 0)

        # Build rect list with ids: (w, h, rid). При обработке кромки добавляем припуск 1 мм на сторону (кромка «съедает» 1 мм).
        rects_to_pack = []
        for it in items:
            w, h = int(it['width_mm']), int(it['height_mm'])
            rec = it.get('recipient_text') or ''
            qty = int(it.get('quantity') or 1)
            qty_label = 'x' + str(qty) if qty > 1 else ''
            edge_treatment = it.get('edge_treatment') or {}
            allow_w = (1 if edge_treatment.get('left') else 0) + (1 if edge_treatment.get('right') else 0)
            allow_h = (1 if edge_treatment.get('top') else 0) + (1 if edge_treatment.get('bottom') else 0)
            w_cut, h_cut = w + allow_w, h + allow_h
            for _ in range(qty):
                rects_to_pack.append((w_cut, h_cut, (rec, qty_label, material, edge_treatment)))

        if not rects_to_pack:
            continue
        if not sheets:
            result['errors'].append(
                'На %d изделий не хватает материала "%s" (нет листов и остатков).' % (len(rects_to_pack), material)
            )
            continue

        if layout_variant is not None and 0 <= layout_variant <= 3:
            pack_algos, sort_algos, order_big_first_first, force_rotated_first_sheet = _variant_pack_and_sort(layout_variant)
        else:
            force_rotated_first_sheet = False
        if layout_variant is None or layout_variant < 0 or layout_variant > 3:
            pack_algos = _GUILLOTINE_ALGOS if _GUILLOTINE_ALGOS else ([MaxRectsBl] if MaxRectsBl else [])
            if prefer_height_cuts and _GUILLOTINE_ALGOS:
                pack_algos = [a for a in _GUILLOTINE_ALGOS if 'Blsf' in (getattr(a, '__name__', '') or '')] or pack_algos
            # Базовые + стратегии «полоса сверху/снизу/сбоку» — перебираем все, выбираем лучший результат
            sort_algos = [SORT_AREA]
            if SORT_LSIDE is not None:
                sort_algos.append(SORT_LSIDE)
            if SORT_RATIO is not None:
                sort_algos.append(SORT_RATIO)
            sort_algos = sort_algos + list(_EXTRA_SORT_ALGOS) if _HAS_RECTPACK else sort_algos
            order_big_first_first = False

        piece_info = []
        for w, h, info in rects_to_pack:
            rid = len(piece_info)
            piece_info.append(info)

        # Фиксированный первый лист для материала/толщины (если передан fixed_first_sheet)
        fixed_sheet = (fixed_first_sheet or {}).get((material, thickness_mm)) or (fixed_first_sheet or {}).get(material)
        sheets_for_run = list(sheets)
        if fixed_sheet and fixed_sheet.get('sheet_id') is not None:
            sid = fixed_sheet['sheet_id']
            stype = fixed_sheet.get('sheet_type') or 'full'
            fixed_sh = next((s for s in sheets if s.get('id') == sid and s.get('sheet_type') == stype), None)
            if fixed_sh:
                sheets_for_run = [fixed_sh] + [s for s in sheets if s.get('id') != sid or s.get('sheet_type') != stype]

        def run_sheet_order(sheets_ordered):
            """Жадно упаковать по заданному порядку листов. Учитываем min_strip по толщине листа. Возвращает (sheet_assignments, remaining_rids)."""
            rem = set(range(len(piece_info)))
            assignments = []
            for sh in sheets_ordered:
                if not rem:
                    break
                thickness = int(sh.get('thickness_mm') or 4)
                min_strip = _min_strip_for_thickness(thickness)
                rects_remaining = [(rects_to_pack[rid][0], rects_to_pack[rid][1], rid) for rid in rem]
                rects_remaining = [(w, h, rid) for (w, h, rid) in rects_remaining if min(w, h) >= min_strip]
                bw0, bh0 = int(sh['width_mm']), int(sh['height_mm'])
                def score_tuple(bw, bh, packed):
                    if not packed:
                        return (10 ** 9, 0, 0, 0)
                    waste, b, n, m, mq = _score_packing(bw, bh, packed, min_h, min_w)
                    return (waste, -b, -mq, -m)

                # Сначала лучшая упаковка по умолчанию (оба ориентации)
                used1, packed1 = _pack_into_one_bin(rects_remaining, bw0, bh0, sort_algos, pack_algos, min_h, min_w)
                if bw0 != bh0:
                    used2, packed2 = _pack_into_one_bin(rects_remaining, bh0, bw0, sort_algos, pack_algos, min_h, min_w)
                else:
                    used2, packed2 = 0, []
                valid1 = bool(packed1) and _layout_respects_min_strip(bw0, bh0, packed1, min_strip)
                valid2 = bool(packed2) and _layout_respects_min_strip(bh0, bw0, packed2, min_strip)
                # Если обе ориентации недопустимы (полоса < min_strip) — перебираем все комбинации алгоритмов
                if not valid1 and not valid2 and pack_algos and sort_algos:
                    best_valid = None
                    best_score = (10 ** 9, 0, 0, 0)
                    for pack_algo in pack_algos:
                        if pack_algo is None:
                            continue
                        for sort_algo in sort_algos:
                            pt1 = _pack_one_bin_single(rects_remaining, bw0, bh0, pack_algo, sort_algo, min_h, min_w)
                            if pt1 and _layout_respects_min_strip(bw0, bh0, pt1, min_strip):
                                sc = score_tuple(bw0, bh0, pt1)
                                if sc < best_score:
                                    best_score = sc
                                    best_valid = (False, pt1)
                            if bw0 != bh0:
                                pt2 = _pack_one_bin_single(rects_remaining, bh0, bw0, pack_algo, sort_algo, min_h, min_w)
                                if pt2 and _layout_respects_min_strip(bh0, bw0, pt2, min_strip):
                                    sc = score_tuple(bh0, bw0, pt2)
                                    if sc < best_score:
                                        best_score = sc
                                        best_valid = (True, pt2)
                    if best_valid:
                        rotated_alt, packed_alt = best_valid
                        valid1, valid2 = (not rotated_alt, rotated_alt)
                        packed1, packed2 = (packed_alt, []) if not rotated_alt else ([], packed_alt)
                s1 = score_tuple(bw0, bh0, packed1)
                s2 = score_tuple(bh0, bw0, packed2)
                is_first_sheet = len(assignments) == 0
                if is_first_sheet:
                    if force_rotated_first_sheet and valid2:
                        assignments.append((sh, True, packed2))
                        for _, _, _, _, rid in packed2:
                            rem.discard(rid)
                    elif force_rotated_first_sheet and valid1:
                        assignments.append((sh, False, packed1))
                        for _, _, _, _, rid in packed1:
                            rem.discard(rid)
                    elif (not force_rotated_first_sheet) and valid1:
                        assignments.append((sh, False, packed1))
                        for _, _, _, _, rid in packed1:
                            rem.discard(rid)
                    elif (not force_rotated_first_sheet) and valid2:
                        assignments.append((sh, True, packed2))
                        for _, _, _, _, rid in packed2:
                            rem.discard(rid)
                else:
                    if valid1 and (not valid2 or s1 < s2):
                        assignments.append((sh, False, packed1))
                        for _, _, _, _, rid in packed1:
                            rem.discard(rid)
                    elif valid2:
                        assignments.append((sh, True, packed2))
                        for _, _, _, _, rid in packed2:
                            rem.discard(rid)
            return (assignments, rem)

        # Несколько стратегий порядка листов: максимум использования остатков, меньше отходов.
        def _area(s):
            return (s.get('width_mm') or 0) * (s.get('height_mm') or 0)
        # Порядок «по заполняемости»: остатки первыми, затем лист, на который влезает больше всего изделий
        # (не обязательно самый маленький по площади — может быть 2-й/3-й, но с большим числом деталей).
        def _pieces_fit(sheet):
            thick = int(sheet.get('thickness_mm') or 4)
            min_strip = _min_strip_for_thickness(thick)
            rects_ok = [(rects_to_pack[rid][0], rects_to_pack[rid][1], rid) for rid in range(len(piece_info))
                        if min(rects_to_pack[rid][0], rects_to_pack[rid][1]) >= min_strip]
            if not rects_ok:
                return 0
            bw0, bh0 = int(sheet['width_mm']), int(sheet['height_mm'])
            used1, packed1 = _pack_into_one_bin(rects_ok, bw0, bh0, sort_algos, pack_algos, min_h, min_w)
            used2, packed2 = _pack_into_one_bin(rects_ok, bh0, bw0, sort_algos, pack_algos, min_h, min_w) if bw0 != bh0 else (0, [])
            return max(len(packed1), len(packed2))
        def _sheet_priority(s):
            t = s.get('sheet_type') or 'full'
            return 0 if t == 'remnant' else (1 if t == 'in_work' else 2)
        # Сначала остатки и «в работе», затем целые листы. Внутри группы — по площади или по заполняемости.
        order_small_first = sorted(sheets_for_run, key=lambda s: (_sheet_priority(s), _area(s)))
        order_big_first = sorted(sheets_for_run, key=lambda s: (_sheet_priority(s), -_area(s)))
        order_by_fill = sorted(sheets_for_run, key=lambda s: (
            _sheet_priority(s),
            -_pieces_fit(s),
            _area(s),
        ))
        # Явно «в работе» по убыванию площади — сначала самые крупные свободные места
        order_in_work_big_first = sorted(sheets_for_run, key=lambda s: (
            _sheet_priority(s),
            -(_area(s) if (s.get('sheet_type') or '') == 'in_work' else 0),
            _area(s),
        ))
        # Приоритет: остатки и «в работе» перед целым листом. Между остатком и «в работе» — меньше отходов (лучше подходящий по размеру).
        # Сначала пробуем порядок «от маленького» (остаток/прямоугольник близок к изделию = меньше мусора), затем по заполняемости, затем in_work крупные.
        orderings = [order_small_first, order_by_fill, order_in_work_big_first, order_big_first] if order_big_first_first else [order_small_first, order_by_fill, order_in_work_big_first, order_big_first]
        best_assignments = None
        best_remaining = set(range(len(piece_info)))
        best_score = (10 ** 9, -10 ** 9, 10 ** 9, 0, 0, 10 ** 9)  # (full_sheets, -in_work, remnants, ...)
        for sheets_ordered in orderings:
            assignments, remaining_rids = run_sheet_order(sheets_ordered)
            if remaining_rids:
                continue
            sc = _score_sheet_assignments(assignments, min_h, min_w, piece_meta=piece_info)
            if sc < best_score:
                best_score = sc
                best_assignments = assignments
                best_remaining = remaining_rids
        if best_assignments is None:
            assignments, remaining_rids = run_sheet_order(order_small_first)
            sheet_assignments = assignments
            remaining_rids = remaining_rids
        else:
            sheet_assignments = best_assignments
            remaining_rids = best_remaining

        if remaining_rids:
            result['errors'].append(
                'Не всё поместилось: для материала «%s» %d изделий остались без листа. '
                'То, что поместилось, будет на схеме ниже. Для остатка снова откройте раскрой, '
                'нажмите «Выбрать лист» у нужных позиций и рассчитайте ещё раз — существующие листы '
                'заказа обновятся, при необходимости добавьте новый лист со склада.'
                % (material, len(remaining_rids))
            )

        if not sheet_assignments:
            continue

        def to_top_left(bh, x, y, w, h):
            return (x, bh - y - h, w, h)

        for sh, rotated, placed_tuples in sheet_assignments:
            bw = sh['height_mm'] if rotated else sh['width_mm']
            bh = sh['width_mm'] if rotated else sh['height_mm']
            bw, bh = int(bw), int(bh)
            placed = [
                (x, y, w, h, piece_info[rid][0], piece_info[rid][1], rid, piece_info[rid][3] if len(piece_info[rid]) > 3 else {})
                for (x, y, w, h, rid) in placed_tuples
            ]
            free_bl = [(0, 0, bw, bh)]
            for r in placed:
                x, y, w, h = r[0], r[1], r[2], r[3]
                bl_y = bh - y - h
                free_bl = _subtract_rect(free_bl, (x, bl_y, w, h))
            business_rects = []
            waste_rects = []
            for (fx, fy, fw, fh) in free_bl:
                if fw <= 0 or fh <= 0:
                    continue
                fx2, fy2, fw2, fh2 = to_top_left(bh, fx, fy, fw, fh)
                if _is_business_rect(fw2, fh2, min_h, min_w):
                    business_rects.append({'x': fx2, 'y': fy2, 'w': fw2, 'h': fh2})
                    # Только деловые остатки нумеруются и сохраняются; неделовые (waste) — в утиль, не в БД
                    if sh.get('sheet_type') != 'in_work':
                        result['new_remnants'].append({
                            'name': material,
                            'height_mm': fh2,
                            'width_mm': fw2,
                            'thickness_mm': sh.get('thickness_mm', 4),
                        })
                else:
                    waste_rects.append({'x': fx2, 'y': fy2, 'w': fw2, 'h': fh2})

            pieces = [
                {
                    'x': x, 'y': y, 'w': w, 'h': h,
                    'recipient': rec, 'quantity_label': qty_label,
                    'rotated': False,
                    'edge_treatment': edge_treatment,
                }
                for (x, y, w, h, rec, qty_label, _, edge_treatment) in placed
            ]

            lay_out = {
                'sheet_id': sh['id'],
                'sheet_type': sh['sheet_type'],
                'material': material,
                'sheet_width': bw,
                'sheet_height': bh,
                'thickness_mm': sh.get('thickness_mm', 4),
                'rotated': rotated,
                'pieces': pieces,
                'business_rects': business_rects,
                'waste_rects': waste_rects,
            }
            if sh.get('in_work_order_id') is not None and sh.get('in_work_sheet_index') is not None:
                lay_out['in_work_order_id'] = sh['in_work_order_id']
                lay_out['in_work_sheet_index'] = sh['in_work_sheet_index']
                if sh.get('in_work_rect'):
                    lay_out['in_work_rect'] = dict(sh['in_work_rect'])
            min_strip_lay = _min_strip_for_thickness(int(lay_out.get('thickness_mm') or thickness_mm or 4))
            assign_chocolate_bar_cut_segments_to_layout(lay_out, min_strip_lay, min_h=min_h, min_w=min_w)
            result['layouts'].append(lay_out)
            result['used_sheet_ids'].append({
                'sheet_type': sh['sheet_type'],
                'sheet_id': sh['id'],
            })

    return result


def _score_result(result):
    """Оценка варианта: приоритет — меньше отходов, больше деловой остаток. Меньше — лучше.
    Возвращает (total_waste, -total_business_area, -max_remnant_quality, -max_remnant_area)."""
    total_business_area = 0
    max_remnant_area = 0
    max_remnant_quality = 0
    total_waste = 0
    for lay in result.get('layouts') or []:
        for r in lay.get('business_rects') or []:
            w, h = r.get('w') or 0, r.get('h') or 0
            a = w * h
            total_business_area += a
            if a > max_remnant_area:
                max_remnant_area = a
            q = _remnant_quality(w, h)
            if q > max_remnant_quality:
                max_remnant_quality = q
        for r in lay.get('waste_rects') or []:
            total_waste += (r.get('w') or 0) * (r.get('h') or 0)
    return (total_waste, -total_business_area, -max_remnant_quality, -max_remnant_area)


def compute_cutting_layout_variants(order_items, get_sheets_for_material, get_threshold_for_material, num_variants=4, fixed_first_sheet=None):
    """
    Сгенерировать несколько вариантов раскроя (разные резы по X/Y, ориентация листа).
    Возвращает список из до num_variants результатов, отсортированных по качеству.
    """
    variants = []
    for v in range(num_variants):
        try:
            res = compute_cutting_layout(
                order_items, get_sheets_for_material, get_threshold_for_material,
                fixed_first_sheet=fixed_first_sheet, layout_variant=v,
            )
            if not res.get('layouts'):
                continue
            # Частичное размещение: есть layouts и текст в errors — вариант всё равно полезен
            err_n = len(res.get('errors') or [])
            score = (err_n,) + _score_result(res)
            variants.append((score, res))
        except Exception:
            continue
    sorted_variants = sorted(variants, key=lambda x: x[0])
    return [res for _, res in sorted_variants[:num_variants]]


def _unit_item_cut_dims(it):
    """Размеры для раскроя с учётом кромки (+1 мм на сторону с обработкой)."""
    w, h = int(it['width_mm']), int(it['height_mm'])
    edge_treatment = it.get('edge_treatment') or {}
    allow_w = (1 if edge_treatment.get('left') else 0) + (1 if edge_treatment.get('right') else 0)
    allow_h = (1 if edge_treatment.get('top') else 0) + (1 if edge_treatment.get('bottom') else 0)
    return w + allow_w, h + allow_h


def _score_sheet_assignments(assignments, min_h, min_w, *, prioritize_remnant_sheets=False, piece_meta=None):
    """Оценка набора листов (меньше кортеж — лучше).

    По умолчанию (обычный раскрой): меньше целых листов, больше «в работе», меньше отходов.

    Для сессии «по материалу» (prioritize_remnant_sheets): минимум занятой площади стекла (мелкие листы
    и плотная укладка), минимум самого крупного листа в плане, меньше целых листов со склада и отходов.
    Число листов не максимизируем — важна компактность, а не «больше листов».
    """
    def _unpack(a):
        return (a[0], a[1], a[2], a[3] if len(a) > 3 else None)

    num_full_sheets = sum(
        1 for a in assignments if (_unpack(a)[0].get('sheet_type') or 'full') == 'full'
    )
    num_in_work = sum(1 for a in assignments if (_unpack(a)[0].get('sheet_type') or '') == 'in_work')
    num_remnant_sheets = sum(1 for a in assignments if (_unpack(a)[0].get('sheet_type') or '') == 'remnant')
    total_business_area = 0
    max_remnant_area = 0
    max_remnant_quality = 0
    total_waste = 0
    total_n_business_rects = 0
    total_bin_area = 0
    remnant_bin_area = 0
    full_bin_area = 0
    max_full_bin_area = 0
    max_bin_area = 0
    for a in assignments:
        sh, rotated, packed_tuples, merge_flag = _unpack(a)
        bw = int(sh['height_mm'] if rotated else sh['width_mm'])
        bh = int(sh['width_mm'] if rotated else sh['height_mm'])
        bin_area = bw * bh
        total_bin_area += bin_area
        if bin_area > max_bin_area:
            max_bin_area = bin_area
        st = sh.get('sheet_type') or 'full'
        if st == 'remnant':
            remnant_bin_area += bin_area
        elif st == 'full':
            full_bin_area += bin_area
            if bin_area > max_full_bin_area:
                max_full_bin_area = bin_area
        if merge_flag and piece_meta:
            sl = sh.get('saved_layout') or {}

            def _uid_for_rid(rid):
                try:
                    meta = piece_meta[rid]
                except (IndexError, KeyError, TypeError):
                    return ''
                if isinstance(meta, dict):
                    return str(meta.get('piece_uid') or '').strip()
                return ''

            uids_pl = {_uid_for_rid(t[4]) for t in packed_tuples}
            uids_pl.discard('')
            fake_exist = [
                (int(p.get('x', 0)), int(p.get('y', 0)), int(p.get('w', 0)), int(p.get('h', 0)), -3000 - i)
                for i, p in enumerate(sl.get('pieces') or [])
                if str(p.get('piece_uid') or '').strip() not in uids_pl
            ]
            score_tuples = fake_exist + list(packed_tuples)
        else:
            score_tuples = packed_tuples
        waste, b_area, n_rem, m_area, m_quality = _score_packing(bw, bh, score_tuples, min_h, min_w)
        total_business_area += b_area
        total_waste += waste
        total_n_business_rects += int(n_rem or 0)
        if m_area > max_remnant_area:
            max_remnant_area = m_area
        if m_quality > max_remnant_quality:
            max_remnant_quality = m_quality
    tail = (total_waste, -total_business_area, -max_remnant_quality, -max_remnant_area)
    if prioritize_remnant_sheets:
        return (
            total_bin_area,
            max_bin_area,
            num_full_sheets,
            total_waste,
            total_n_business_rects,
            -total_business_area,
            -num_remnant_sheets,
            -num_in_work,
            full_bin_area,
            remnant_bin_area,
            -max_remnant_quality,
            -max_remnant_area,
        )
    return (num_full_sheets, -num_in_work) + tail


def compute_cut_session_layouts(
    unit_items,
    get_sheets_for_material,
    get_threshold_for_material,
    forced_slot_sheets=None,
):
    """
    Жадная мультилистовая раскладка для сессии «раскрой по материалу» (каждая деталь — отдельная запись).
    unit_items: список dict с ключами material_name, thickness_mm, width_mm, height_mm,
      recipient_text, edge_treatment, piece_uid (str).
    forced_slot_sheets: список или None; для слота i если элемент не None — использовать этот лист
      (тот же формат, что у get_sheets_for_material), иначе брать со склада. Порядок листов — как в
      compute_cutting_layout: перебор порядков листов и выбор полного плана с минимальной суммарной
      площадью листов и максимальной плотностью (мелкий лист + как можно больше деталей на нём).
    Возвращает тот же формат, что compute_cutting_layout: layouts, new_remnants, used_sheet_ids, errors.
    """
    forced_slot_sheets = forced_slot_sheets or []
    by_key = {}
    for it in unit_items:
        mat = (it.get('material_name') or '').strip()
        thick = int(it.get('thickness_mm') or 4)
        key = (mat, thick)
        if key not in by_key:
            by_key[key] = []
        by_key[key].append(it)

    result = {'layouts': [], 'new_remnants': [], 'used_sheet_ids': [], 'errors': []}
    if not _HAS_RECTPACK:
        result['errors'].append('Установите библиотеку rectpack: pip install rectpack')
        return result

    for (material, thickness_mm), items in by_key.items():
        sheets = list(get_sheets_for_material(material, thickness_mm) or [])
        threshold = get_threshold_for_material(material, thickness_mm)
        min_h = (threshold or {}).get('min_height_mm', 0) or 0
        min_w = (threshold or {}).get('min_width_mm', 0) or 0
        if not sheets:
            result['errors'].append(
                'На складе нет материала «%s» %s мм: нет целых листов, остатков и листов «в работе».'
                % (material, thickness_mm)
            )
            continue

        rects_to_pack = []
        piece_meta = []
        for it in items:
            w_cut, h_cut = _unit_item_cut_dims(it)
            rid = len(piece_meta)
            piece_meta.append({
                'piece_uid': it.get('piece_uid') or str(rid),
                'recipient_text': it.get('recipient_text') or '',
                'edge_treatment': it.get('edge_treatment') or {},
                'width_mm': int(it.get('width_mm') or 0),
                'height_mm': int(it.get('height_mm') or 0),
                'thickness_mm': thick,
                'material_name': material,
            })
            rects_to_pack.append((w_cut, h_cut, rid))

        if not rects_to_pack:
            continue

        pack_algos = _GUILLOTINE_ALGOS if _GUILLOTINE_ALGOS else ([MaxRectsBl] if MaxRectsBl else [])
        sort_algos = [SORT_AREA]
        if SORT_LSIDE is not None:
            sort_algos.append(SORT_LSIDE)
        if SORT_RATIO is not None:
            sort_algos.append(SORT_RATIO)
        sort_algos = sort_algos + list(_EXTRA_SORT_ALGOS) if _HAS_RECTPACK else sort_algos

        def _area(s):
            return (s.get('width_mm') or 0) * (s.get('height_mm') or 0)

        def _sheet_priority(s):
            t = s.get('sheet_type') or 'full'
            if t == 'in_work':
                return 0
            if t == 'remnant':
                return 1
            return 2

        def _sheet_dedupe_key(sh):
            return (sh.get('id'), sh.get('sheet_type') or 'full')

        def run_session_greedy(sheets_ordered):
            """Жадная укладка: слоты с forced, затем листы из sheets_ordered без повторного id/type."""
            rem = set(range(len(piece_meta)))
            assignments = []
            used_keys = set()
            slot = 0
            oi = 0
            n_forced = len(forced_slot_sheets)

            while rem:
                sh = None
                if slot < n_forced and forced_slot_sheets[slot] is not None:
                    sh = forced_slot_sheets[slot]
                else:
                    while oi < len(sheets_ordered):
                        cand = sheets_ordered[oi]
                        oi += 1
                        if _sheet_dedupe_key(cand) in used_keys:
                            continue
                        sh = cand
                        break
                if sh is None:
                    break

                is_forced_slot = slot < n_forced and forced_slot_sheets[slot] is not None

                thickness = int(sh.get('thickness_mm') or thickness_mm)
                min_strip = _min_strip_for_thickness(thickness)
                rects_remaining = [(rects_to_pack[rid][0], rects_to_pack[rid][1], rid) for rid in rem]
                rects_remaining = [(w, h, rid) for (w, h, rid) in rects_remaining if min(w, h) >= min_strip]
                if not rects_remaining:
                    if is_forced_slot:
                        used_keys.add(_sheet_dedupe_key(sh))
                        slot += 1
                    continue

                bw0, bh0 = int(sh['width_mm']), int(sh['height_mm'])
                saved_lay = sh.get('saved_layout')
                uids_to_place = {
                    str(piece_meta[rid].get('piece_uid') or '').strip()
                    for _, _, rid in rects_remaining
                }
                uids_to_place.discard('')
                ex_all = list((saved_lay or {}).get('pieces') or []) if isinstance(saved_lay, dict) else []
                ex_pieces = [
                    p for p in ex_all
                    if str(p.get('piece_uid') or '').strip() not in uids_to_place
                ]
                has_existing = isinstance(saved_lay, dict) and bool(ex_pieces)

                if has_existing:
                    rot_sv = bool(saved_lay.get('rotated', False))
                    sw0 = int(saved_lay.get('sheet_width') or bw0)
                    sh0 = int(saved_lay.get('sheet_height') or bh0)
                    packed_m = _try_pack_rects_into_free_space(
                        sw0, sh0, ex_pieces, rects_remaining,
                        sort_algos, pack_algos, min_h, min_w, min_strip,
                    )
                    if packed_m is None:
                        if is_forced_slot:
                            used_keys.add(_sheet_dedupe_key(sh))
                            slot += 1
                        continue
                    fake_exist = [
                        (
                            int(p.get('x', 0)), int(p.get('y', 0)),
                            int(p.get('w', 0)), int(p.get('h', 0)),
                            -3000 - i,
                        )
                        for i, p in enumerate(ex_pieces)
                    ]
                    if not _layout_respects_min_strip(sw0, sh0, fake_exist + packed_m, min_strip):
                        if is_forced_slot:
                            used_keys.add(_sheet_dedupe_key(sh))
                            slot += 1
                        continue
                    used_keys.add(_sheet_dedupe_key(sh))
                    slot += 1
                    assignments.append((sh, rot_sv, packed_m, True))
                    for _x, _y, _w, _h, rid in packed_m:
                        rem.discard(rid)
                    continue

                def score_tuple(bw, bh, packed):
                    if not packed:
                        return (10 ** 9, 0, 0, 0, 0, 0, 0)
                    waste, b, n, m, mq = _score_packing(bw, bh, packed, min_h, min_w)
                    n_packed = len(packed)
                    # Сначала максимум деталей на этом листе, затем меньше отходов и мельче лист.
                    return (-n_packed, waste, int(n or 0), -b, -mq, -m, bw * bh)

                used1, packed1 = _pack_into_one_bin(rects_remaining, bw0, bh0, sort_algos, pack_algos, min_h, min_w)
                if bw0 != bh0:
                    used2, packed2 = _pack_into_one_bin(rects_remaining, bh0, bw0, sort_algos, pack_algos, min_h, min_w)
                else:
                    used2, packed2 = 0, []
                valid1 = bool(packed1) and _layout_respects_min_strip(bw0, bh0, packed1, min_strip)
                valid2 = bool(packed2) and _layout_respects_min_strip(bh0, bw0, packed2, min_strip)
                if not valid1 and not valid2:
                    if is_forced_slot:
                        used_keys.add(_sheet_dedupe_key(sh))
                        slot += 1
                    continue

                used_keys.add(_sheet_dedupe_key(sh))
                slot += 1

                s1 = score_tuple(bw0, bh0, packed1)
                s2 = score_tuple(bh0, bw0, packed2)
                if valid1 and (not valid2 or s1 <= s2):
                    assignments.append((sh, False, packed1, None))
                    for _, _, _, _, rid in packed1:
                        rem.discard(rid)
                elif valid2:
                    assignments.append((sh, True, packed2, None))
                    for _, _, _, _, rid in packed2:
                        rem.discard(rid)

            return assignments, rem

        def _pieces_fit(sheet):
            thick = int(sheet.get('thickness_mm') or thickness_mm)
            min_strip = _min_strip_for_thickness(thick)
            rects_ok = [
                (rects_to_pack[rid][0], rects_to_pack[rid][1], rid)
                for rid in range(len(piece_meta))
                if min(rects_to_pack[rid][0], rects_to_pack[rid][1]) >= min_strip
            ]
            if not rects_ok:
                return 0
            sl = sheet.get('saved_layout')
            if isinstance(sl, dict) and (sl.get('pieces') or []):
                uids_ord = {
                    str(piece_meta[i].get('piece_uid') or '').strip()
                    for i in range(len(piece_meta))
                }
                uids_ord.discard('')
                ex = [
                    p for p in (sl.get('pieces') or [])
                    if str(p.get('piece_uid') or '').strip() not in uids_ord
                ]
                if not ex:
                    bw0, bh0 = int(sheet['width_mm']), int(sheet['height_mm'])
                    used1, packed1 = _pack_into_one_bin(rects_ok, bw0, bh0, sort_algos, pack_algos, min_h, min_w)
                    if bw0 != bh0:
                        used2, packed2 = _pack_into_one_bin(rects_ok, bh0, bw0, sort_algos, pack_algos, min_h, min_w)
                    else:
                        used2, packed2 = 0, []
                    return max(len(packed1), len(packed2))
                sw0 = int(sl.get('sheet_width') or sheet.get('width_mm') or 0)
                sh0 = int(sl.get('sheet_height') or sheet.get('height_mm') or 0)
                pm = _try_pack_rects_into_free_space(
                    sw0, sh0, ex, rects_ok, sort_algos, pack_algos, min_h, min_w, min_strip,
                )
                return len(rects_ok) if pm is not None else 0
            bw0, bh0 = int(sheet['width_mm']), int(sheet['height_mm'])
            used1, packed1 = _pack_into_one_bin(rects_ok, bw0, bh0, sort_algos, pack_algos, min_h, min_w)
            if bw0 != bh0:
                used2, packed2 = _pack_into_one_bin(rects_ok, bh0, bw0, sort_algos, pack_algos, min_h, min_w)
            else:
                used2, packed2 = 0, []
            return max(len(packed1), len(packed2))

        sheets_for_run = list(sheets)
        order_small_first = sorted(sheets_for_run, key=lambda s: (_sheet_priority(s), _area(s)))
        order_by_fill = sorted(sheets_for_run, key=lambda s: (
            _sheet_priority(s),
            -_pieces_fit(s),
            _area(s),
        ))
        order_in_work_big_first = sorted(sheets_for_run, key=lambda s: (
            _sheet_priority(s),
            -(_area(s) if (s.get('sheet_type') or '') == 'in_work' else 0),
            _area(s),
        ))
        # Доп. порядок перебора (другая жадная цепочка при равных полных планах).
        order_remnant_large_first = sorted(
            sheets_for_run,
            key=lambda s: (
                _sheet_priority(s),
                -_area(s) if (s.get('sheet_type') or '') == 'remnant' else _area(s),
            ),
        )
        orderings_all = (
            order_small_first,
            order_by_fill,
            order_in_work_big_first,
            order_remnant_large_first,
        )
        best_assignments = None
        best_remaining = set(range(len(piece_meta)))
        best_score = None
        for sheets_ordered in orderings_all:
            assignments, remaining_rids = run_session_greedy(sheets_ordered)
            if remaining_rids:
                continue
            sc = _score_sheet_assignments(
                assignments, min_h, min_w, prioritize_remnant_sheets=True, piece_meta=piece_meta,
            )
            if best_score is None or sc < best_score:
                best_score = sc
                best_assignments = assignments
                best_remaining = remaining_rids
        if best_assignments is None:
            sheet_assignments, remaining_rids = run_session_greedy(order_small_first)
        else:
            sheet_assignments = best_assignments
            remaining_rids = best_remaining

        if remaining_rids:
            result['errors'].append(
                'Не всё поместилось: «%s» %s мм — %d изделий без листа.'
                % (material, thickness_mm, len(remaining_rids))
            )

        def to_top_left(bh, x, y, w, h):
            return (x, bh - y - h, w, h)

        group_layouts = []
        for assignment in sheet_assignments:
            sh = assignment[0]
            rotated = assignment[1]
            placed_tuples = assignment[2]
            merge_flag = assignment[3] if len(assignment) > 3 else None
            bw = sh['height_mm'] if rotated else sh['width_mm']
            bh = sh['width_mm'] if rotated else sh['height_mm']
            bw, bh = int(bw), int(bh)
            existing_display = []
            uids_place = {
                str(piece_meta[rid].get('piece_uid') or '').strip()
                for rid in (t[4] for t in placed_tuples)
            }
            uids_place.discard('')
            if merge_flag:
                sl = sh.get('saved_layout') or {}
                for p in (sl.get('pieces') or []):
                    if str(p.get('piece_uid') or '').strip() in uids_place:
                        continue
                    existing_display.append(dict(p))

            placed = []
            for (x, y, w, h, rid) in placed_tuples:
                meta = piece_meta[rid]
                placed.append((x, y, w, h, meta))

            pieces_new_abs = []
            for (x, y, w, h, meta) in placed:
                pieces_new_abs.append({
                    'x': x, 'y': y, 'w': w, 'h': h,
                    'recipient': meta.get('recipient_text') or '',
                    'quantity_label': '',
                    'rotated': False,
                    'edge_treatment': meta.get('edge_treatment') or {},
                    'piece_uid': meta.get('piece_uid'),
                })

            if merge_flag:
                pieces = [dict(p) for p in existing_display] + pieces_new_abs
            else:
                pieces = pieces_new_abs

            free_bl = [(0, 0, bw, bh)]
            for p in pieces:
                x, y = int(p.get('x', 0)), int(p.get('y', 0))
                w, h = int(p.get('w', 0)), int(p.get('h', 0))
                bl_y = bh - y - h
                free_bl = _subtract_rect(free_bl, (x, bl_y, w, h))
            business_rects = []
            waste_rects = []
            for (fx, fy, fw, fh) in free_bl:
                if fw <= 0 or fh <= 0:
                    continue
                fx2, fy2, fw2, fh2 = to_top_left(bh, fx, fy, fw, fh)
                if _is_business_rect(fw2, fh2, min_h, min_w):
                    business_rects.append({'x': fx2, 'y': fy2, 'w': fw2, 'h': fh2})
                else:
                    waste_rects.append({'x': fx2, 'y': fy2, 'w': fw2, 'h': fh2})

            lay_out = {
                'sheet_id': sh['id'],
                'sheet_type': sh['sheet_type'],
                'material': material,
                'sheet_width': bw,
                'sheet_height': bh,
                'thickness_mm': sh.get('thickness_mm', 4),
                'rotated': rotated,
                'pieces': pieces,
                'business_rects': business_rects,
                'waste_rects': waste_rects,
            }
            if merge_flag:
                lay_out['in_work_merge_from_saved'] = True
                lay_out['in_work_existing_piece_count'] = len(existing_display)
            rect = sh.get('in_work_rect') or {}
            ox = int(rect.get('x') or 0)
            oy = int(rect.get('y') or 0)
            if merge_flag and sh.get('in_work_order_id') is not None:
                lay_out['in_work_new_pieces_local'] = [
                    dict(p, x=int(p.get('x', 0)) - ox, y=int(p.get('y', 0)) - oy)
                    for p in pieces_new_abs
                ]
            if sh.get('in_work_order_id') is not None and sh.get('in_work_sheet_index') is not None:
                lay_out['in_work_order_id'] = sh['in_work_order_id']
                lay_out['in_work_sheet_index'] = sh['in_work_sheet_index']
                if sh.get('in_work_rect'):
                    lay_out['in_work_rect'] = dict(sh['in_work_rect'])
            group_layouts.append(lay_out)

        if len(group_layouts) > 1:
            group_layouts = collapse_session_if_one_sheet_fits_all(
                group_layouts, items, get_threshold_for_material
            )

        for lay_out in group_layouts:
            min_strip_lay = _min_strip_for_thickness(int(lay_out.get('thickness_mm') or thickness_mm or 4))
            assign_chocolate_bar_cut_segments_to_layout(lay_out, min_strip_lay, min_h=min_h, min_w=min_w)
            result['layouts'].append(lay_out)
            result['used_sheet_ids'].append({
                'sheet_type': lay_out['sheet_type'],
                'sheet_id': lay_out['sheet_id'],
            })
            for br in lay_out.get('business_rects') or []:
                if lay_out.get('sheet_type') != 'in_work':
                    result['new_remnants'].append({
                        'name': material,
                        'height_mm': br['h'],
                        'width_mm': br['w'],
                        'thickness_mm': lay_out.get('thickness_mm', 4),
                    })

    return result


def summarize_cut_session_placement(unit_items, layouts):
    """
    Сколько изделий сессии реально попало в layouts (только piece_uid из unit_items).
    Возвращает dict: total, placed_count, placed_uids (set), unplaced_uids (list).
    """
    want = {
        str(it.get('piece_uid') or '').strip()
        for it in (unit_items or [])
    }
    want.discard('')
    placed = set()
    for lay in layouts or []:
        for p in lay.get('pieces') or []:
            uid = str(p.get('piece_uid') or '').strip()
            if uid in want:
                placed.add(uid)
    unplaced = sorted(want - placed)
    return {
        'total': len(want),
        'placed_count': len(placed),
        'placed_uids': placed,
        'unplaced_uids': unplaced,
    }


def _layout_dict_from_bin_pack(
    sh,
    rotated,
    placed_tuples,
    piece_meta,
    material,
    min_h,
    min_w,
    new_remnants_out,
    sheet_type_default='full',
):
    """Собрать layout как в compute_cut_session_layouts (один бин)."""
    bw = sh['height_mm'] if rotated else sh['width_mm']
    bh = sh['width_mm'] if rotated else sh['height_mm']
    bw, bh = int(bw), int(bh)
    placed = []
    for (x, y, w, h, rid) in placed_tuples:
        meta = piece_meta[rid]
        placed.append((x, y, w, h, meta))
    free_bl = [(0, 0, bw, bh)]
    for r in placed:
        x, y, w, h = r[0], r[1], r[2], r[3]
        bl_y = bh - y - h
        free_bl = _subtract_rect(free_bl, (x, bl_y, w, h))

    def to_top_left(bh2, x, y, w, h):
        return (x, bh2 - y - h, w, h)

    business_rects = []
    waste_rects = []
    for (fx, fy, fw, fh) in free_bl:
        if fw <= 0 or fh <= 0:
            continue
        fx2, fy2, fw2, fh2 = to_top_left(bh, fx, fy, fw, fh)
        if _is_business_rect(fw2, fh2, min_h, min_w):
            business_rects.append({'x': fx2, 'y': fy2, 'w': fw2, 'h': fh2})
            if sh.get('sheet_type') != 'in_work' and new_remnants_out is not None:
                new_remnants_out.append({
                    'name': material,
                    'height_mm': fh2,
                    'width_mm': fw2,
                    'thickness_mm': sh.get('thickness_mm', 4),
                })
        else:
            waste_rects.append({'x': fx2, 'y': fy2, 'w': fw2, 'h': fh2})

    pieces = []
    for (x, y, w, h, meta) in placed:
        pieces.append({
            'x': x, 'y': y, 'w': w, 'h': h,
            'recipient': meta.get('recipient_text') or '',
            'quantity_label': '',
            'rotated': False,
            'edge_treatment': meta.get('edge_treatment') or {},
            'piece_uid': meta.get('piece_uid'),
        })

    lay_out = {
        'sheet_id': sh['id'],
        'sheet_type': sh.get('sheet_type') or sheet_type_default,
        'material': material,
        'sheet_width': bw,
        'sheet_height': bh,
        'thickness_mm': sh.get('thickness_mm', 4),
        'rotated': rotated,
        'pieces': pieces,
        'business_rects': business_rects,
        'waste_rects': waste_rects,
    }
    if sh.get('in_work_order_id') is not None and sh.get('in_work_sheet_index') is not None:
        lay_out['in_work_order_id'] = sh['in_work_order_id']
        lay_out['in_work_sheet_index'] = sh['in_work_sheet_index']
        if sh.get('in_work_rect'):
            lay_out['in_work_rect'] = dict(sh['in_work_rect'])
    min_strip_lay = _min_strip_for_thickness(int(sh.get('thickness_mm') or 4))
    assign_chocolate_bar_cut_segments_to_layout(lay_out, min_strip_lay, min_h=min_h, min_w=min_w)
    return lay_out


def _template_to_pack_sh(template_lay, thick_default):
    """Описание листа для упаковки из сохранённого layout (уже эффективные ширина/высота)."""
    return {
        'id': template_lay.get('sheet_id'),
        'width_mm': int(template_lay.get('sheet_width') or 0),
        'height_mm': int(template_lay.get('sheet_height') or 0),
        'sheet_type': template_lay.get('sheet_type') or 'full',
        'thickness_mm': template_lay.get('thickness_mm', thick_default),
        'in_work_order_id': template_lay.get('in_work_order_id'),
        'in_work_sheet_index': template_lay.get('in_work_sheet_index'),
        'in_work_rect': template_lay.get('in_work_rect'),
    }


def collapse_session_if_one_sheet_fits_all(layouts, unit_items, get_threshold_for_material):
    """
    Если все unit_items помещаются на типоразмер одного из layouts — вернуть один layout.
    Порядок перебора: как в списке (сначала лист, который пользователь мог увеличить).

    Допускается len(layouts) == 1: один шаблон листа проверяется на вместимость всех unit
    (замена листа в сессии «всё на выбранный типоразмер»).
    """
    if not layouts or not unit_items:
        return layouts
    if any(l.get('in_work_merge_from_saved') for l in layouts):
        return layouts
    material = (unit_items[0].get('material_name') or '').strip()
    thick = int(unit_items[0].get('thickness_mm') or 4)
    threshold = get_threshold_for_material(material, thick)
    min_h = (threshold or {}).get('min_height_mm', 0) or 0
    min_w = (threshold or {}).get('min_width_mm', 0) or 0

    piece_meta = []
    rects_to_pack = []
    for it in unit_items:
        w_cut, h_cut = _unit_item_cut_dims(it)
        rid = len(piece_meta)
        piece_meta.append({
            'piece_uid': it.get('piece_uid') or str(rid),
            'recipient_text': it.get('recipient_text') or '',
            'edge_treatment': it.get('edge_treatment') or {},
            'width_mm': int(it.get('width_mm') or 0),
            'height_mm': int(it.get('height_mm') or 0),
            'thickness_mm': thick,
            'material_name': material,
        })
        rects_to_pack.append((w_cut, h_cut, rid))
    n = len(rects_to_pack)

    pack_algos = _GUILLOTINE_ALGOS if _GUILLOTINE_ALGOS else ([MaxRectsBl] if MaxRectsBl else [])
    sort_algos = [SORT_AREA]
    if SORT_LSIDE is not None:
        sort_algos.append(SORT_LSIDE)
    if SORT_RATIO is not None:
        sort_algos.append(SORT_RATIO)
    sort_algos = sort_algos + list(_EXTRA_SORT_ALGOS) if _HAS_RECTPACK else sort_algos

    for template in layouts:
        sh = _template_to_pack_sh(template, thick)
        bw0, bh0 = int(sh['width_mm']), int(sh['height_mm'])
        if bw0 <= 0 or bh0 <= 0:
            continue
        min_strip = _min_strip_for_thickness(int(sh.get('thickness_mm') or thick))
        rects_remaining = [(w, h, rid) for (w, h, rid) in rects_to_pack if min(w, h) >= min_strip]
        if len(rects_remaining) != n:
            continue
        used1, packed1 = _pack_into_one_bin(rects_remaining, bw0, bh0, sort_algos, pack_algos, min_h, min_w)
        if bw0 != bh0:
            used2, packed2 = _pack_into_one_bin(rects_remaining, bh0, bw0, sort_algos, pack_algos, min_h, min_w)
        else:
            used2, packed2 = 0, []
        pick = None
        if packed1 and len(packed1) == n and _layout_respects_min_strip(bw0, bh0, packed1, min_strip):
            pick = (False, packed1)
        elif packed2 and len(packed2) == n and _layout_respects_min_strip(bh0, bw0, packed2, min_strip):
            pick = (True, packed2)
        if not pick:
            continue
        rotated, packed_tuples = pick
        nr = []
        lay = _layout_dict_from_bin_pack(
            sh, rotated, packed_tuples, piece_meta, material, min_h, min_w, nr,
        )
        return [lay]
    return layouts


def squash_session_layouts_to_largest_sheet_if_repack(
    layouts,
    chosen,
    unit_by_uid,
    unit_items,
    get_threshold_for_material,
):
    """
    Несколько листов с дубликатами piece_uid или «лишние» листы после пересчёта:
    оставить по одному экземпляру каждого uid, переупаковать на лист максимальной площади
    (приоритет — лист, совпадающий с chosen по id/типу/in_work). Успех — один layout, иначе None.
    """
    if not layouts or not chosen or not unit_by_uid or not unit_items:
        return None

    by_uid = {}
    for k, v in (unit_by_uid or {}).items():
        if k is None:
            continue
        nk = str(k).strip()
        if nk:
            by_uid[nk] = v

    want = {
        str(it.get('piece_uid')).strip()
        for it in unit_items
        if it.get('piece_uid') is not None and str(it.get('piece_uid')).strip() in by_uid
    }
    n_u = len(want)
    if n_u <= 0:
        return None

    def _match_chosen(i: int) -> bool:
        lay = layouts[i]
        lsid, cid = lay.get('sheet_id'), chosen.get('id')
        if cid is not None and lsid is not None:
            try:
                if int(lsid) != int(cid):
                    return False
            except (TypeError, ValueError):
                if str(lsid) != str(cid):
                    return False
        elif lsid != cid:
            return False
        if (lay.get('sheet_type') or 'full') != (chosen.get('sheet_type') or 'full'):
            return False
        st = chosen.get('sheet_type') or 'full'
        if st == 'in_work':
            if int(lay.get('in_work_order_id') or 0) != int(chosen.get('in_work_order_id') or -1):
                return False
            if int(lay.get('in_work_sheet_index') or 0) != int(chosen.get('in_work_sheet_index') or 0):
                return False
        return True

    ti = None
    for i in range(len(layouts)):
        if _match_chosen(i):
            ti = i
            break
    if ti is None:
        ti = max(
            range(len(layouts)),
            key=lambda j: int(layouts[j].get('sheet_width') or 0) * int(layouts[j].get('sheet_height') or 0),
        )

    mat = (unit_items[0].get('material_name') or '').strip()
    thick = int(unit_items[0].get('thickness_mm') or 4)
    threshold = get_threshold_for_material(mat, thick) or {}
    min_h = int(threshold.get('min_height_mm', 0) or 0)
    min_w = int(threshold.get('min_width_mm', 0) or 0)

    order = [ti] + [i for i in range(len(layouts)) if i != ti]
    uid_to_p = {}
    for i in order:
        for p in layouts[i].get('pieces') or []:
            uid = p.get('piece_uid')
            if uid is None:
                continue
            k = str(uid).strip()
            if not k or k not in by_uid:
                continue
            if k not in uid_to_p:
                uid_to_p[k] = copy.deepcopy(p)
    if set(uid_to_p.keys()) != want:
        return None

    pieces = list(uid_to_p.values())
    base = copy.deepcopy(layouts[ti])
    sw = int(base.get('sheet_width') or 0)
    sh = int(base.get('sheet_height') or 0)
    if sw <= 0 or sh <= 0:
        return None
    rp = repack_pieces_on_sheet(sw, sh, pieces, min_h, min_w)
    if not rp or len(rp) != n_u:
        return None
    base['pieces'] = rp
    base['business_rects'], base['waste_rects'] = recompute_free_rects_from_pieces(sw, sh, rp, min_h, min_w)
    return [base]


def dedupe_layouts_pieces_prefer_sheet(layouts, prefer_index: int = 0):
    """Одна деталь (piece_uid) — только на одном листе; приоритет у prefer_index (отредактированный / первый)."""
    if not layouts:
        return layouts
    prefer_index = max(0, min(int(prefer_index or 0), len(layouts) - 1))
    order = [prefer_index] + [i for i in range(len(layouts)) if i != prefer_index]
    seen = set()
    for i in order:
        new_list = []
        for p in layouts[i].get('pieces') or []:
            uid = p.get('piece_uid')
            if uid is not None:
                uk = str(uid).strip()
                if uk:
                    if uk in seen:
                        continue
                    seen.add(uk)
            new_list.append(p)
        layouts[i]['pieces'] = new_list
    return layouts


def try_place_pieces_into_free_rects(layout, incoming_piece_dicts, min_h, min_w):
    """
    Пытается добавить incoming_piece_dicts (с ключами w,h и метаданными) в свободные прямоугольники
    layout без сдвига уже стоящих pieces. Возвращает (updated_pieces_list, still_unplaced_list).
    Эвристика: свободные rect из recompute_free_rects_from_pieces, по убыванию площади;
    для каждой детали — первая подходящая ориентация, позиция top-left free rect (в координатах top-left листа).
    """
    sw = int(layout.get('sheet_width') or 0)
    sh = int(layout.get('sheet_height') or 0)
    fixed = list(layout.get('pieces') or [])
    placed_new = []
    pool = list(incoming_piece_dicts)
    if not pool or sw <= 0 or sh <= 0:
        return fixed + placed_new, pool

    def free_rects_from_pieces(pieces):
        br, wr = recompute_free_rects_from_pieces(sw, sh, pieces, min_h, min_w)
        rects = []
        for r in br + wr:
            rects.append((int(r['x']), int(r['y']), int(r['w']), int(r['h'])))
        return rects

    work = list(fixed)
    still = []
    for p in pool:
        w0 = int(p.get('w') or 0)
        h0 = int(p.get('h') or 0)
        if w0 <= 0 or h0 <= 0:
            still.append(p)
            continue
        frs = free_rects_from_pieces(work)
        frs.sort(key=lambda t: -(t[2] * t[3]))
        pos = None
        nw, nh = w0, h0
        for fx, fy, fw, fh in frs:
            if w0 <= fw and h0 <= fh:
                pos = (fx, fy)
                nw, nh = w0, h0
                break
            if h0 <= fw and w0 <= fh:
                pos = (fx, fy)
                nw, nh = h0, w0
                break
        if not pos:
            still.append(p)
            continue
        nx, ny = pos[0], pos[1]
        np = dict(p, x=nx, y=ny, w=nw, h=nh, rotated=bool(nw != w0 or nh != h0))
        work.append(np)
        placed_new.append(np)

    return work, still


def _mm_rects_overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def _pieces_mm_overlap(pieces):
    rects = []
    for p in pieces:
        rects.append((
            int(p.get('x') or 0),
            int(p.get('y') or 0),
            int(p.get('w') or 0),
            int(p.get('h') or 0),
        ))
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            if _mm_rects_overlap(rects[i], rects[j]):
                return True
    return False


def _pieces_inside_sheet(sw, sh, pieces):
    for p in pieces:
        x, y = int(p.get('x') or 0), int(p.get('y') or 0)
        w, h = int(p.get('w') or 0), int(p.get('h') or 0)
        if w <= 0 or h <= 0:
            return False
        if x < 0 or y < 0 or x + w > sw or y + h > sh:
            return False
    return True


def _spill_excess_from_sheet(lay, min_h, min_w, unit_by_uid):
    """
    Если детали не помещаются или пересекаются — снять крупные в overflow (unit dicts),
    пока repack_pieces_on_sheet не сработает.
    """
    sw, sh = int(lay.get('sheet_width') or 0), int(lay.get('sheet_height') or 0)
    pcs = list(lay.get('pieces') or [])
    if not pcs:
        return [], []
    rp = repack_pieces_on_sheet(sw, sh, pcs, min_h, min_w)
    if rp is not None:
        return rp, []
    overflow_units = []
    work = list(pcs)
    while len(work) > 1:
        work.sort(key=lambda p: -(int(p.get('w') or 0) * int(p.get('h') or 0)))
        victim = work.pop(0)
        uid = victim.get('piece_uid')
        if uid and uid in unit_by_uid:
            overflow_units.append(dict(unit_by_uid[uid]))
        rp2 = repack_pieces_on_sheet(sw, sh, work, min_h, min_w)
        if rp2 is not None:
            return rp2, overflow_units
    if not work:
        return [], overflow_units
    one = work[0]
    w0, h0 = int(one.get('w') or 0), int(one.get('h') or 0)
    if w0 <= sw and h0 <= sh:
        rp_one = repack_pieces_on_sheet(sw, sh, work, min_h, min_w)
        if rp_one is not None:
            return rp_one, overflow_units
    uid = one.get('piece_uid')
    if uid and uid in unit_by_uid:
        overflow_units.append(dict(unit_by_uid[uid]))
    return [], overflow_units


def _layout_refresh_free_rects(lay, min_h, min_w):
    pcs = list(lay.get('pieces') or [])
    lay['business_rects'], lay['waste_rects'] = recompute_free_rects_from_pieces(
        int(lay.get('sheet_width') or 0),
        int(lay.get('sheet_height') or 0),
        pcs,
        min_h,
        min_w,
    )


def redistribute_session_multi(
    layouts,
    unit_items_by_uid,
    get_sheets_for_material,
    get_threshold_for_material,
    priority_first=None,
    drop_empty_sheets=True,
    reassign_unplaced_to_new_sheets=True,
):
    """
    Межлистовой пересчёт: валидация каждого листа, перенос деталей в свободные прямоугольники
    других листов (в порядке priority_first, затем остальные по индексу), лишнее — новые листы.

    priority_first: какие индексы обрабатывать первыми при каждом проходе (например лист после
    смены размера или отредактированная схема).
    drop_empty_sheets: удалять листы без деталей (в конструкторе обычно False).
    reassign_unplaced_to_new_sheets: если False (конструктор), детали только в пуле не получают
    автоматически новые листы; True — все unit без позиции уходят в жадный пересчёт (авто/схема).
    """
    layouts = copy.deepcopy(layouts or [])
    errors = []
    if not layouts:
        return [], errors

    mat = (layouts[0].get('material') or '').strip()
    th = int(layouts[0].get('thickness_mm') or 4)
    threshold = get_threshold_for_material(mat, th)
    min_h = (threshold or {}).get('min_height_mm', 0) or 0
    min_w = (threshold or {}).get('min_width_mm', 0) or 0

    overflow_units = []
    for lay in layouts:
        pcs = list(lay.get('pieces') or [])
        if not pcs:
            _layout_refresh_free_rects(lay, min_h, min_w)
            continue
        sw, sh = int(lay.get('sheet_width') or 0), int(lay.get('sheet_height') or 0)
        ok = _pieces_inside_sheet(sw, sh, pcs) and not _pieces_mm_overlap(pcs)
        if ok:
            _layout_refresh_free_rects(lay, min_h, min_w)
            continue
        rp, ov = _spill_excess_from_sheet(lay, min_h, min_w, unit_items_by_uid)
        lay['pieces'] = rp
        overflow_units.extend(ov)
        _layout_refresh_free_rects(lay, min_h, min_w)

    n = len(layouts)
    prio_raw = list(priority_first or [])
    prio = []
    seen_p = set()
    for i in prio_raw:
        if isinstance(i, int) and 0 <= i < n and i not in seen_p:
            seen_p.add(i)
            prio.append(i)
    rest = [i for i in range(n) if i not in seen_p]
    order = prio + rest

    max_passes = max(4, n + 2)
    for _ in range(max_passes):
        moved = False
        for pi in order:
            if pi < 0 or pi >= len(layouts):
                continue
            lay_pi = layouts[pi]
            sw = int(lay_pi.get('sheet_width') or 0)
            sh = int(lay_pi.get('sheet_height') or 0)
            if sw <= 0 or sh <= 0:
                continue
            pool = []
            sources = {}
            for q, lay_q in enumerate(layouts):
                if q == pi:
                    continue
                for p in list(lay_q.get('pieces') or []):
                    uid = p.get('piece_uid')
                    if not uid or uid not in unit_items_by_uid:
                        continue
                    meta = unit_items_by_uid[uid]
                    w_cut, h_cut = _unit_item_cut_dims(meta)
                    pool.append({
                        'piece_uid': uid,
                        'w': w_cut,
                        'h': h_cut,
                        'recipient': meta.get('recipient_text') or '',
                        'recipient_text': meta.get('recipient_text') or '',
                        'edge_treatment': meta.get('edge_treatment') or {},
                        'rotated': False,
                    })
                    sources[uid] = q
            if not pool:
                continue
            fixed_before = {p.get('piece_uid') for p in (lay_pi.get('pieces') or []) if p.get('piece_uid')}
            work_layout = dict(lay_pi)
            placed_all, still = try_place_pieces_into_free_rects(work_layout, pool, min_h, min_w)
            new_uids = {p.get('piece_uid') for p in placed_all if p.get('piece_uid')} - fixed_before
            if not new_uids:
                continue
            lay_pi['pieces'] = placed_all
            for uid in new_uids:
                q = sources.get(uid)
                if q is None:
                    continue
                lay_q = layouts[q]
                lay_q['pieces'] = [
                    p for p in (lay_q.get('pieces') or [])
                    if p.get('piece_uid') != uid
                ]
                _layout_refresh_free_rects(lay_q, min_h, min_w)
            _layout_refresh_free_rects(lay_pi, min_h, min_w)
            moved = True
        if not moved:
            break

    pref = 0
    if priority_first:
        for p in priority_first:
            if isinstance(p, int) and 0 <= p < len(layouts):
                pref = p
                break
    dedupe_layouts_pieces_prefer_sheet(layouts, pref)
    units_list = sorted(
        unit_items_by_uid.values(),
        key=lambda u: str(u.get('piece_uid') or ''),
    )
    layouts = collapse_session_if_one_sheet_fits_all(
        layouts, units_list, get_threshold_for_material
    )

    placed_uids = set()
    for lay in layouts:
        for p in lay.get('pieces') or []:
            uid = p.get('piece_uid')
            if uid:
                placed_uids.add(uid)

    ov_by_uid = {}
    for u in overflow_units:
        uid = u.get('piece_uid')
        if uid:
            ov_by_uid[uid] = u
    if reassign_unplaced_to_new_sheets:
        for uid in unit_items_by_uid.keys():
            if uid not in placed_uids:
                ov_by_uid[uid] = dict(unit_items_by_uid[uid])
    overflow_units = list(ov_by_uid.values())

    if overflow_units:
        rest_result = compute_cut_session_layouts(
            overflow_units,
            get_sheets_for_material,
            get_threshold_for_material,
            forced_slot_sheets=None,
        )
        errors.extend(list(rest_result.get('errors') or []))
        layouts.extend(list(rest_result.get('layouts') or []))

    dedupe_layouts_pieces_prefer_sheet(layouts, pref)
    layouts = collapse_session_if_one_sheet_fits_all(
        layouts, units_list, get_threshold_for_material
    )

    for lay in layouts:
        _layout_refresh_free_rects(lay, min_h, min_w)

    if drop_empty_sheets:
        layouts = [L for L in layouts if L.get('pieces')]

    return layouts, errors


def remap_manual_sheet_indices(
    old_manual: set,
    fixed_order: list,
    middle_original_indices: list,
) -> set:
    """Сопоставить старые индексы листов новым после redistribute_with_fixed_layouts."""
    new_m = set()
    pos_fixed = {fi: k for k, fi in enumerate(fixed_order)}
    base_mid = len(fixed_order)
    pos_mid = {orig: base_mid + j for j, orig in enumerate(middle_original_indices)}
    for o in old_manual:
        if o in pos_fixed:
            new_m.add(pos_fixed[o])
        elif o in pos_mid:
            new_m.add(pos_mid[o])
    return new_m


def redistribute_with_fixed_layouts(
    layouts,
    fixed_indices,
    unit_items,
    unit_items_by_uid,
    get_sheets_for_material,
    get_threshold_for_material,
    priority_first=None,
):
    """
    Пересчёт сессии с сохранением «ручных» листов (fixed_indices): их раскладка и размер не трогаем,
    кроме попытки добавить детали в свободные прямоугольники. Остальные листы очищаются и
    заполняются заново; нехватка — новые листы из жадного алгоритма.

    Возвращает (layouts, errors, meta) где meta = {'fixed_order', 'middle_original_indices'} для remap manual.
    """
    layouts = copy.deepcopy(layouts or [])
    errors = []
    meta = {'fixed_order': [], 'middle_original_indices': []}
    if not layouts or not unit_items:
        return layouts, errors, meta

    n = len(layouts)
    fixed_indices = {int(i) for i in fixed_indices if isinstance(i, int) and 0 <= i < n}
    if not fixed_indices:
        res = compute_cut_session_layouts(
            unit_items,
            get_sheets_for_material,
            get_threshold_for_material,
            forced_slot_sheets=None,
        )
        errs = list(res.get('errors') or [])
        lays = list(res.get('layouts') or [])
        return lays, errs, meta

    mat = (layouts[0].get('material') or '').strip()
    th = int(layouts[0].get('thickness_mm') or 4)
    threshold = get_threshold_for_material(mat, th)
    min_h = (threshold or {}).get('min_height_mm', 0) or 0
    min_w = (threshold or {}).get('min_width_mm', 0) or 0

    pinned = set()
    for fi in fixed_indices:
        for p in layouts[fi].get('pieces') or []:
            uid = p.get('piece_uid')
            if uid:
                pinned.add(uid)

    loose_by_uid = {}
    for i, lay in enumerate(layouts):
        if i in fixed_indices:
            continue
        for p in lay.get('pieces') or []:
            uid = p.get('piece_uid')
            if not uid or uid not in unit_items_by_uid:
                continue
            meta_u = unit_items_by_uid[uid]
            w_cut, h_cut = _unit_item_cut_dims(meta_u)
            loose_by_uid[uid] = {
                'piece_uid': uid,
                'w': w_cut,
                'h': h_cut,
                'recipient': meta_u.get('recipient_text') or '',
                'recipient_text': meta_u.get('recipient_text') or '',
                'edge_treatment': meta_u.get('edge_treatment') or {},
                'rotated': False,
            }
        lay['pieces'] = []

    for u in unit_items:
        uid = u.get('piece_uid')
        if not uid or uid in pinned or uid in loose_by_uid:
            continue
        w_cut, h_cut = _unit_item_cut_dims(u)
        loose_by_uid[uid] = {
            'piece_uid': uid,
            'w': w_cut,
            'h': h_cut,
            'recipient': u.get('recipient_text') or '',
            'recipient_text': u.get('recipient_text') or '',
            'edge_treatment': u.get('edge_treatment') or {},
            'rotated': False,
        }

    def _still_loose(still_list):
        out = {}
        for t in still_list:
            uid = t.get('piece_uid')
            if uid:
                out[uid] = t
        return out

    work_loose = list(loose_by_uid.values())

    preset = []
    seen_pf = set()
    if priority_first:
        for p in priority_first:
            if p not in fixed_indices and 0 <= p < n and p not in seen_pf:
                seen_pf.add(p)
                preset.append(p)

    for p in preset:
        lay = layouts[p]
        placed_all, still = try_place_pieces_into_free_rects(lay, work_loose, min_h, min_w)
        lay['pieces'] = placed_all
        _layout_refresh_free_rects(lay, min_h, min_w)
        work_loose = list(_still_loose(still).values())

    fixed_order = sorted(fixed_indices)
    if priority_first:
        pri_f = [x for x in priority_first if x in fixed_indices]
        rest_f = [x for x in fixed_order if x not in pri_f]
        fixed_order = pri_f + rest_f

    for fi in fixed_order:
        lay = layouts[fi]
        placed_all, still = try_place_pieces_into_free_rects(lay, work_loose, min_h, min_w)
        lay['pieces'] = placed_all
        _layout_refresh_free_rects(lay, min_h, min_w)
        work_loose = list(_still_loose(still).values())

    still_units = []
    for t in work_loose:
        uid = t.get('piece_uid')
        if uid and uid in unit_items_by_uid:
            still_units.append(dict(unit_items_by_uid[uid]))

    result = []
    for fi in fixed_order:
        result.append(layouts[fi])

    nf_with = [(i, layouts[i]) for i in range(n) if i not in fixed_indices and layouts[i].get('pieces')]
    nf_with.sort(key=lambda t: t[0])
    middle_original_indices = [i for i, _ in nf_with]
    for _, lay in nf_with:
        result.append(lay)

    if still_units:
        rest_result = compute_cut_session_layouts(
            still_units,
            get_sheets_for_material,
            get_threshold_for_material,
            forced_slot_sheets=None,
        )
        errors.extend(list(rest_result.get('errors') or []))
        tail = list(rest_result.get('layouts') or [])
        tail = collapse_session_if_one_sheet_fits_all(
            tail, still_units, get_threshold_for_material
        )
        result.extend(tail)

    # Не collapse всей сессии: иначе несколько «ручных» листов сольются в один шаблон.

    for lay in result:
        _layout_refresh_free_rects(lay, min_h, min_w)

    # Убираем только пустые «хвостовые» листы из compute; фиксированные (ручные) сохраняем даже без деталей.
    n_pre = len(fixed_order) + len(middle_original_indices)
    result = result[:n_pre] + [L for L in result[n_pre:] if L.get('pieces')]
    meta['fixed_order'] = fixed_order
    meta['middle_original_indices'] = middle_original_indices
    return result, errors, meta


def redistribute_session_after_sheet_edit(
    all_layouts,
    edited_index,
    edited_layout,
    unit_items_by_uid,
    get_sheets_for_material,
    get_threshold_for_material,
    forced_slot_sheets=None,
    manual_sheet_indices=None,
):
    """
    После правки схемы одного листа: пересчёт с сохранением других «ручных» листов.
    manual_sheet_indices: множество индексов листов с раскладкой пользователя (до вызова).
    """
    _ = forced_slot_sheets
    layouts = [copy.deepcopy(l) for l in (all_layouts or [])]
    if edited_index < 0 or edited_index >= len(layouts):
        return layouts, [], {'fixed_order': [], 'middle_original_indices': []}
    layouts[edited_index] = copy.deepcopy(edited_layout)
    manual = set(manual_sheet_indices or [])
    manual.add(edited_index)
    units = sorted(
        unit_items_by_uid.values(),
        key=lambda u: str(u.get('piece_uid') or ''),
    )
    return redistribute_with_fixed_layouts(
        layouts,
        manual,
        units,
        unit_items_by_uid,
        get_sheets_for_material,
        get_threshold_for_material,
        priority_first=[edited_index] + sorted(manual - {edited_index}),
    )
