"""
Build the step-by-step cutting sequence: plan -> take rect N, cut (Y or X) -> two numbered rects -> ...
Every cut is recorded: first Y (horizontal line, full width), then X (vertical line, full height of strip).
Rectangles numbered 1, 2, 3...
"""
from copy import deepcopy


def _piece_bottom(p):
    """Bottom edge of piece in sheet coords (y from top): larger = lower on sheet."""
    return p['y'] + p['h']


def _add_cut_step(steps, rect_id, next_id, axis, rect_w, rect_h, cut_dim, left_w, left_h, right_w, right_h, sheet_w, sheet_h):
    """Append cut_rect and after_cut with two new rect ids (next_id, next_id+1). Returns next_id + 2.
    axis 'y' = horizontal cut line (рез по оси X), axis 'x' = vertical cut line (рез по оси Y)."""
    left_id = next_id
    right_id = next_id + 1
    # Рез по оси X = горизонтальная линия реза; рез по оси Y = вертикальная линия реза
    axis_label = 'X' if axis == 'y' else 'Y'
    steps.append({
        'type': 'cut_rect',
        'sheet_w': sheet_w, 'sheet_h': sheet_h,
        'rect_id': rect_id,
        'rect_w': rect_w,
        'rect_h': rect_h,
        'axis': axis,
        'cut_pos': 0,
        'cut_dim': cut_dim,
        'left_rect': {'w': left_w, 'h': left_h},
        'right_rect': {'w': right_w, 'h': right_h},
        'left_id': left_id,
        'right_id': right_id,
        'label': 'Возьмите прямоугольник №%d. Режьте по оси %s на отметке %d мм.' % (rect_id, axis_label, cut_dim),
    })
    steps.append({
        'type': 'after_cut',
        'sheet_w': sheet_w, 'sheet_h': sheet_h,
        'left_id': left_id, 'right_id': right_id,
        'left_rect': {'w': left_w, 'h': left_h},
        'right_rect': {'w': right_w, 'h': right_h},
        'label_left': '№%d  %d×%d' % (left_id, left_w, left_h),
        'label_right': '№%d  %d×%d' % (right_id, right_w, right_h),
    })
    return right_id + 1


def _seg_norm_type(seg):
    t = str(seg.get('type') or '').strip().upper()
    return t if t in ('H', 'V') else ''


# Поиск лучшего порядка резов: полный перебор ветвей с ограничением узлов (типично n≤18).
_MAX_GUILLOTINE_SEARCH_NODES = 120_000
_MAX_SEGMENTS_FOR_OPTIMIZER = 18


def _try_apply_segment_to_rects(rects, seg, swf, shf):
    """
    Один шаг гильотины: если seg попадает в один из rects, вернуть (новый список rects, True).
    rects: [(id, x, y, w, h)] в координатах как в _build_sequence_from_cut_segments.
    """
    t = _seg_norm_type(seg)
    if t not in ('H', 'V'):
        return list(rects), False
    pos = float(seg.get('pos', 0))
    lo = float(seg.get('extent_lo', 0))
    hi = float(seg.get('extent_hi', swf if t == 'H' else shf))
    idx = None
    for i, (_rid, rx, ry, rw, rh) in enumerate(rects):
        if t == 'H':
            if ry <= pos < ry + rh and lo <= rx + 1e-6 and (rx + rw) <= hi + 1e-6:
                idx = i
                break
        else:
            if rx <= pos < rx + rw and lo <= ry + 1e-6 and (ry + rh) <= hi + 1e-6:
                idx = i
                break
    if idx is None:
        return list(rects), False
    _rid, rx, ry, rw, rh = rects[idx]
    rects = list(rects)
    if t == 'H':
        cut_dim = pos - ry
        left_h = cut_dim
        right_h = rh - cut_dim
        if left_h <= 0 or right_h <= 0:
            return rects, False
        rects.pop(idx)
        rects.append((0, rx, ry, rw, left_h))
        rects.append((0, rx, pos, rw, right_h))
    else:
        cut_dim = pos - rx
        left_w = cut_dim
        right_w = rw - cut_dim
        if left_w <= 0 or right_w <= 0:
            return rects, False
        rects.pop(idx)
        rects.append((0, rx, ry, left_w, rh))
        rects.append((0, pos, ry, right_w, rh))
    return rects, True


def _score_operator_rects(rects):
    """
    Оценка финальных заготовок после всех резов: больше площадь и меньше «полос» (макс. aspect).
    Чем выше — тем предпочтительнее порядок для остатков/удобства.
    """
    score = 0.0
    for _rid, rx, ry, rw, rh in rects:
        rwf, rhf = float(rw), float(rh)
        area = max(0.0, rwf) * max(0.0, rhf)
        if area <= 0:
            continue
        M = max(rwf, rhf, 1e-6)
        m = min(rwf, rhf)
        aspect = m / M
        score += area * (0.15 + 0.85 * aspect)
    return score


def _child_branch_priority(rects, seg, swf, shf):
    """Упорядочить ветки DFS: сначала варианты с более «толстыми» кусками после реза."""
    nr, ok = _try_apply_segment_to_rects(rects, seg, swf, shf)
    if not ok:
        return -1e30
    m = min(r[3] * r[4] for r in nr) if nr else 0.0
    return m


def _reorder_cut_segments_greedy(segments, sw, sh):
    """Быстрый жадный порядок (как раньше) — запасной, если оптимизатор не нашёл полный порядок."""
    swf, shf = float(sw), float(sh)
    segs = [dict(s) for s in (segments or []) if isinstance(s, dict) and _seg_norm_type(s) in ('H', 'V')]
    if len(segs) <= 1:
        return list(segments or [])
    remaining = list(segs)
    rects = [(1, 0.0, 0.0, swf, shf)]
    ordered = []
    max_passes = len(segs) * len(segs) + 10
    passes = 0
    while remaining and passes < max_passes:
        passes += 1
        placed = False
        for j, seg in enumerate(remaining):
            nr, ok = _try_apply_segment_to_rects(rects, seg, swf, shf)
            if not ok:
                continue
            rects = nr
            ordered.append(seg)
            remaining.pop(j)
            placed = True
            break
        if not placed:
            break
    if remaining:
        ordered.extend(remaining)
    return ordered


def optimize_cut_segments_order(segments, sw, sh, max_nodes=_MAX_GUILLOTINE_SEARCH_NODES, max_n=_MAX_SEGMENTS_FOR_OPTIMIZER):
    """
    Найти порядок cut_segments (H/V), при котором:
    1) максимизируется число успешно применимых резов в симуляции;
    2) при полном применении всех резов — максимизируется _score_operator_rects (крупные, не полосатые заготовки).

    Возвращает список сегментов в том же формате, что и вход (сохраняются исходные dict-объекты где возможно).
    """
    originals = [s for s in (segments or []) if isinstance(s, dict) and _seg_norm_type(s) in ('H', 'V')]
    others = [s for s in (segments or []) if not isinstance(s, dict) or _seg_norm_type(s) not in ('H', 'V')]
    n = len(originals)
    if n <= 1:
        return list(segments or [])
    swf, shf = float(sw), float(sh)

    if n > max_n:
        return _reorder_cut_segments_greedy(segments, sw, sh) + others

    best_order_idx = None
    best_applied = -1
    best_score = -1e30
    counter = [0]

    def record_if_better(applied_len, order_idx, rects):
        nonlocal best_order_idx, best_applied, best_score
        sc = _score_operator_rects(rects)
        if applied_len > best_applied or (applied_len == best_applied and sc > best_score):
            best_applied = applied_len
            best_score = sc
            best_order_idx = list(order_idx)

    def dfs(rects, rem_set, order_idx):
        counter[0] += 1
        if counter[0] > max_nodes:
            return
        if not rem_set:
            record_if_better(len(order_idx), order_idx, rects)
            return
        candidates = []
        for j in rem_set:
            nr, ok = _try_apply_segment_to_rects(rects, originals[j], swf, shf)
            if ok:
                pr = _child_branch_priority(rects, originals[j], swf, shf)
                candidates.append((pr, j, nr))
        if not candidates:
            record_if_better(len(order_idx), order_idx, rects)
            return
        candidates.sort(key=lambda t: -t[0])
        for _pr, j, nr in candidates:
            dfs(nr, rem_set - {j}, order_idx + [j])

    dfs([(1, 0.0, 0.0, swf, shf)], set(range(n)), [])

    if best_order_idx is not None and best_applied == n:
        return [originals[i] for i in best_order_idx] + others

    return _reorder_cut_segments_greedy(segments, sw, sh) + others


def reorder_cut_segments_for_guillotine_simulation(segments, sw, sh):
    """
    Упорядочить cut_segments для симуляции раскроя.
    Сначала подбор порядка с максимизацией качества остатков (optimize_cut_segments_order),
    при нехватке бюджета поиска — жадный метод.
    """
    return optimize_cut_segments_order(segments, sw, sh)


def _build_sequence_from_cut_segments(lay):
    """
    Построить последовательность резов строго по сохранённым cut_segments (актуальный макет после смены направлений).
    Старая линия не отображается — только резы из cut_segments.
    """
    sh = lay['sheet_height']
    sw = lay['sheet_width']
    pieces = list(lay.get('pieces', []))
    business = list(lay.get('business_rects', []))
    waste = list(lay.get('waste_rects', []))
    segments = list(lay.get('cut_segments') or [])
    steps = []
    steps.append({
        'type': 'plan',
        'sheet_w': sw, 'sheet_h': sh,
        'pieces': deepcopy(pieces), 'business_rects': deepcopy(business), 'waste_rects': deepcopy(waste),
    })
    if not segments:
        steps.append({'type': 'final', 'sheet_w': sw, 'sheet_h': sh, 'products': _products_list(lay)})
        return steps
    segments = reorder_cut_segments_for_guillotine_simulation(segments, sw, sh)
    # rects: list of (id, x, y, w, h) — верхний левый, y вниз
    rects = [(1, 0, 0, sw, sh)]
    next_id = 2
    for seg in segments:
        t = str(seg.get('type') or 'H').strip().upper()
        pos = float(seg.get('pos', 0))
        lo = float(seg.get('extent_lo', 0))
        hi = float(seg.get('extent_hi', sw if t == 'H' else sh))
        # Найти прямоугольник, содержащий линию реза
        idx = None
        for i, (rid, rx, ry, rw, rh) in enumerate(rects):
            if t == 'H':
                if ry <= pos < ry + rh and lo <= rx + 1e-6 and (rx + rw) <= hi + 1e-6:
                    idx = i
                    break
            else:
                if rx <= pos < rx + rw and lo <= ry + 1e-6 and (ry + rh) <= hi + 1e-6:
                    idx = i
                    break
        if idx is None:
            continue
        rid, rx, ry, rw, rh = rects[idx]
        if t == 'H':
            # горизонтальная линия: верхняя часть (ry .. pos), нижняя (pos .. ry+rh)
            cut_dim = int(round(pos - ry))
            left_h = cut_dim
            right_h = rh - cut_dim
            if left_h <= 0 or right_h <= 0:
                continue
            next_id = _add_cut_step(steps, rid, next_id, 'y', rw, rh, cut_dim, rw, left_h, rw, right_h, sw, sh)
            rects.pop(idx)
            rects.append((next_id - 2, rx, ry, rw, left_h))
            rects.append((next_id - 1, rx, pos, rw, right_h))
        else:
            cut_dim = int(round(pos - rx))
            left_w = cut_dim
            right_w = rw - cut_dim
            if left_w <= 0 or right_w <= 0:
                continue
            next_id = _add_cut_step(steps, rid, next_id, 'x', rw, rh, cut_dim, left_w, rh, right_w, rh, sw, sh)
            rects.pop(idx)
            rects.append((next_id - 2, rx, ry, left_w, rh))
            rects.append((next_id - 1, pos, ry, right_w, rh))
    steps.append({'type': 'final', 'sheet_w': sw, 'sheet_h': sh, 'products': _products_list(lay)})
    return steps


def build_cut_sequence_for_sheet(lay):
    """
    Strip-based sequence so that every cut is recorded.
    If layout has cut_segments (saved directions), build sequence from them so it matches the current layout.
    Otherwise infer from piece positions (rows by bottom edge).
    """
    if lay.get('cut_segments'):
        return _build_sequence_from_cut_segments(lay)
    sh = lay['sheet_height']
    sw = lay['sheet_width']
    pieces = list(lay.get('pieces', []))
    business = list(lay.get('business_rects', []))
    waste = list(lay.get('waste_rects', []))

    steps = []
    steps.append({
        'type': 'plan',
        'sheet_w': sw, 'sheet_h': sh,
        'pieces': deepcopy(pieces), 'business_rects': deepcopy(business), 'waste_rects': deepcopy(waste),
    })

    if not pieces:
        steps.append({'type': 'final', 'sheet_w': sw, 'sheet_h': sh, 'products': _products_list(lay)})
        return steps

    # Rows: group by bottom edge (y + h), sort rows bottom-first (largest first)
    pieces_sorted = sorted(pieces, key=lambda p: (-_piece_bottom(p), p['x']))
    rows = []
    current_bottom = _piece_bottom(pieces_sorted[0])
    current_row = []
    for p in pieces_sorted:
        if _piece_bottom(p) == current_bottom:
            current_row.append(p)
        else:
            if current_row:
                rows.append((current_bottom, current_row))
            current_bottom = _piece_bottom(p)
            current_row = [p]
    if current_row:
        rows.append((current_bottom, current_row))
    rows.sort(key=lambda r: -r[0])

    next_rect_id = 2
    remaining_sheet_h = sh
    remaining_rect_id = 1

    for row_bottom, row_pieces in rows:
        strip_h = max(p['h'] for p in row_pieces)
        if strip_h <= 0:
            continue

        # Step: cut along X (horizontal line, full width) — separate strip from remaining sheet.
        # Do this whenever remaining is taller than the strip so we get two cuts for one piece (Y then X).
        if remaining_sheet_h > strip_h:
            rem_h = remaining_sheet_h - strip_h
            next_rect_id = _add_cut_step(
                steps, remaining_rect_id, next_rect_id, 'y',
                sw, remaining_sheet_h,
                strip_h,
                sw, rem_h,
                sw, strip_h,
                sw, sh
            )
            remaining_rect_id = next_rect_id - 2
            strip_rect_id = next_rect_id - 1
            remaining_sheet_h = rem_h
        else:
            strip_rect_id = remaining_rect_id

        # Current strip: cut along X for each piece (left to right)
        row_pieces_sorted = sorted([p for p in row_pieces if p['w'] > 0 and p['h'] > 0], key=lambda p: p['x'])
        strip_w_used = 0

        for p in row_pieces_sorted:
            strip_w = sw - strip_w_used
            rem_w = strip_w - p['w']
            if rem_w < 0:
                continue
            if strip_w > 0 and strip_h > 0:
                next_rect_id = _add_cut_step(
                    steps, strip_rect_id, next_rect_id, 'x',
                    strip_w, strip_h,
                    p['w'],
                    p['w'], p['h'],
                    rem_w, strip_h,
                    sw, sh
                )
                strip_rect_id = next_rect_id - 1
            strip_w_used = p['x'] + p['w']

    steps.append({
        'type': 'final',
        'sheet_w': sw, 'sheet_h': sh,
        'products': _products_list(lay),
    })
    return steps


def _products_list(lay):
    """List of all products (pieces, business, waste) with w, h, type, label."""
    out = []
    for p in lay.get('pieces', []):
        out.append({
            'type': 'piece',
            'w': p['w'], 'h': p['h'],
            'label': ('%d×%d' % (p['w'], p['h'])) + ((' ' + (p.get('recipient') or '')[:16]) if p.get('recipient') else ''),
            'recipient': p.get('recipient') or '',
            'piece': p,
            'edge_treatment': p.get('edge_treatment') or {},
        })
    for r in lay.get('business_rects', []):
        out.append({
            'type': 'business',
            'w': r['w'], 'h': r['h'],
            'label': 'Остаток деловой %d×%d' % (r['w'], r['h']),
        })
    for r in lay.get('waste_rects', []):
        out.append({
            'type': 'waste',
            'w': r['w'], 'h': r['h'],
            'label': 'Мусор %d×%d' % (r['w'], r['h']),
        })
    return out


def build_cut_sequence_all_layouts(layouts):
    """Return list of (sheet_index, steps) for each sheet."""
    return [(i, build_cut_sequence_for_sheet(lay)) for i, lay in enumerate(layouts)]


def total_steps_for_sheet(steps):
    """Number of steps for one sheet (for slider max)."""
    return len(steps) - 1  # 0-based index, so max = len-1
