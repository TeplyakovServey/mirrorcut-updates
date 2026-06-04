# -*- coding: utf-8 -*-
"""Данные для диалога раскроя из заказа MAIN_PROJECT: mirror_order_items или стекло из blocks_calc_json."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from logic.blocks_bundle import parse_bundle

_KIND_GLASS = "glass_mirror"
_KIND_FACADE = "facade"

_RU_TO_SIDE = {"Верх": "top", "Низ": "bottom", "Лево": "left", "Право": "right"}
_EN_SIDE_TO_RU = {"Top": "Верх", "Bottom": "Низ", "Left": "Лево", "Right": "Право"}


def _parse_thickness_mm(val: Any) -> int:
    if val is None:
        return 4
    if isinstance(val, (int, float)):
        return max(1, int(val))
    s = str(val).strip().lower().replace("мм", "").replace("mm", "").strip()
    try:
        return max(1, int(float(s)))
    except (TypeError, ValueError):
        return 4


def _glass_block_izmat(payload: dict) -> Tuple[dict, dict]:
    izd = dict(payload.get("Параметры изделия") or {})
    mat = dict(payload.get("Параметры материала") or {})
    bs = payload.get("blocks_selected")
    if isinstance(bs, dict):
        iz2 = bs.get("Изделие") if isinstance(bs.get("Изделие"), dict) else {}
        if not iz2 and isinstance(bs.get("Материал"), dict):
            iz2 = bs["Материал"]
        if isinstance(iz2, dict):
            if iz2.get("Форма") or iz2.get("Форма "):
                izd.setdefault("Форма", iz2.get("Форма") or iz2.get("Форма "))
            for k in ("Ширина (мм)", "Высота (мм)", "Количество (шт)"):
                if iz2.get(k) is not None:
                    izd[k] = iz2.get(k)
        mat2 = bs.get("Материал") if isinstance(bs.get("Материал"), dict) else {}
        if mat2:
            for k in ("Тип материала", "Цвет / Вариант", "Толщина (мм)", "Закалка"):
                if mat2.get(k) is not None and k not in mat:
                    mat[k] = mat2.get(k)
    return izd, mat


def _resolve_sheet_material(mt: str, var: str, catalog_names: Sequence[str]) -> str:
    mt = (mt or "").strip()
    var = (var or "").strip()
    if not mt and not var:
        return ""
    candidates: List[str] = []
    if mt and var:
        candidates.extend(("%s %s" % (mt, var), "%s (%s)" % (mt, var), "%s %s" % (var, mt)))
    if var:
        candidates.append(var)
    if mt:
        candidates.append(mt)
    names_set = {n: True for n in catalog_names}
    for c in candidates:
        if c in names_set:
            return c
    cl = [c.lower() for c in candidates]
    for n in catalog_names:
        nl = n.lower()
        if any(nl == c for c in cl):
            return n
    best = ""
    for n in catalog_names:
        nl = n.lower()
        ok = True
        if mt and mt.lower() not in nl:
            ok = False
        if var and var.lower() not in nl:
            ok = False
        if ok and len(n) > len(best):
            best = n
    if best:
        return best
    return candidates[0] if candidates else ""


def _edge_treatment_from_glass_payload(payload: dict, izd: dict) -> Dict[str, Any]:
    shape = str(izd.get("Форма") or "Прямоугольник").strip() or "Прямоугольник"
    pol = payload.get("Полировка") or {}
    shl = payload.get("Шлифовка") or {}
    fc = payload.get("Фацет") or {}
    facet_mm_by_ru: Dict[str, int] = {k: 0 for k in _RU_TO_SIDE}
    if fc.get("Нужен"):
        for en_key, ru_key in _EN_SIDE_TO_RU.items():
            try:
                facet_mm_by_ru[ru_key] = max(0, int(fc.get(en_key) or 0))
            except (TypeError, ValueError):
                facet_mm_by_ru[ru_key] = 0
    pol_on = bool(pol.get("Нужна полировка"))
    shl_on = bool(shl.get("Нужна шлифовка"))
    kromka_pol = bool(pol.get("Кромка")) if pol_on else False
    kromka_shl = bool(shl.get("Кромка")) if shl_on else False
    out: Dict[str, Any] = {"top": None, "bottom": None, "left": None, "right": None}
    max_facet = 0
    for ru, side in _RU_TO_SIDE.items():
        mm = facet_mm_by_ru.get(ru) or 0
        if mm > 0:
            out[side] = "facet"
            max_facet = max(max_facet, mm)
            continue
        p_side = bool(pol.get(ru)) if pol_on else False
        s_side = bool(shl.get(ru)) if shl_on else False
        if shape == "Прямоугольник":
            if kromka_pol:
                p_side = True
            if kromka_shl:
                s_side = True
        if p_side:
            out[side] = "polishing"
        elif s_side:
            out[side] = "grinding"
    if max_facet > 0:
        out["facet_mm"] = max_facet if max_facet in (5, 10, 15, 20, 25) else 15
    return out


def _facade_glass_cut_base(
    facade_payload: dict, catalog_names: Sequence[str]
) -> Optional[Tuple[dict, int]]:
    """Общие поля позиции раскроя для стекла фасада и количество штук (экземпляров)."""
    g = facade_payload.get("Стекло") if isinstance(facade_payload.get("Стекло"), dict) else {}
    if not g:
        return None
    try:
        w = int(g.get("Ширина (мм)") or facade_payload.get("Ширина_мм") or 0)
        h = int(g.get("Высота (мм)") or facade_payload.get("Высота_мм") or 0)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    qty = 1
    try:
        qty = max(1, int(facade_payload.get("Количество") or 1))
    except (TypeError, ValueError):
        qty = 1
    if isinstance(bs0 := g.get("blocks_selected"), dict):
        iz = bs0.get("Изделие") if isinstance(bs0.get("Изделие"), dict) else {}
        try:
            qty = max(1, int((iz or {}).get("Количество (шт)") or qty))
        except (TypeError, ValueError):
            pass
    mt = str(g.get("Название") or "").strip()
    var = str(g.get("Цвет") or "").strip()
    material_name = _resolve_sheet_material(mt, var, catalog_names)
    if not material_name and mt:
        material_name = mt
    th = _parse_thickness_mm(g.get("Толщина (мм)"))
    bs = g.get("blocks_selected") if isinstance(g.get("blocks_selected"), dict) else {}
    synthetic = {
        "Полировка": bs.get("Полировка") if isinstance(bs.get("Полировка"), dict) else (g.get("Полировка") or {}),
        "Шлифовка": bs.get("Шлифовка") if isinstance(bs.get("Шлифовка"), dict) else (g.get("Шлифовка") or {}),
        "Фацет": bs.get("Фацет") if isinstance(bs.get("Фацет"), dict) else (g.get("Фацет") or {}),
    }
    izd = {"Форма": "Прямоугольник"}
    et = _edge_treatment_from_glass_payload(synthetic, izd)
    part = {
        "material_name": material_name or mt or "—",
        "thickness_mm": th,
        "height_mm": h,
        "width_mm": w,
        "edge_treatment": et,
        "chosen_sheet": None,
    }
    return part, qty


def _facade_glass_to_part(facade_payload: dict, catalog_names: Sequence[str], client_name: str) -> Optional[dict]:
    """Стекло в проёме фасада → одна строка для диалога (совместимость: quantity может быть >1)."""
    r = _facade_glass_cut_base(facade_payload, catalog_names)
    if not r:
        return None
    base, qty = r
    return {
        **base,
        "quantity": qty,
        "recipient_text": (client_name or "").strip() or None,
    }


def _facade_glass_cut_parts(
    facade_payload: dict,
    catalog_names: Sequence[str],
    client_name: str,
    order_id: int,
    bundle_product_index_1based: int,
) -> List[dict]:
    """По одному выкрою на каждый экземпляр фасада; в подписи — F{линия}.{экземпляр}_O{заказ} (как в очереди резки профиля)."""
    r = _facade_glass_cut_base(facade_payload, catalog_names)
    if not r:
        return []
    base, qty = r
    oid = int(order_id or 0)
    line = int(bundle_product_index_1based)
    out: List[dict] = []
    cn = (client_name or "").strip()
    for n in range(qty):
        tag = "F%s.%s_O%s" % (line, n + 1, oid)
        rec = "%s · %s" % (cn, tag) if cn else tag
        out.append(dict(base, quantity=1, recipient_text=rec))
    return out


def _glass_payload_to_part(payload: dict, catalog_names: Sequence[str], client_name: str) -> Optional[dict]:
    izd, mat = _glass_block_izmat(payload)
    shape = str(izd.get("Форма") or "Прямоугольник").strip() or "Прямоугольник"
    if shape != "Прямоугольник":
        return None
    try:
        h = int(izd.get("Высота (мм)") or 0)
        w = int(izd.get("Ширина (мм)") or 0)
    except (TypeError, ValueError):
        return None
    if h <= 0 or w <= 0:
        return None
    try:
        qty = max(1, int(izd.get("Количество (шт)") or 1))
    except (TypeError, ValueError):
        qty = 1
    mt = str(mat.get("Тип материала") or "").strip()
    var = str(mat.get("Цвет / Вариант") or "").strip()
    material_name = _resolve_sheet_material(mt, var, catalog_names)
    th = _parse_thickness_mm(mat.get("Толщина (мм)"))
    et = _edge_treatment_from_glass_payload(payload, izd)
    return {
        "material_name": material_name,
        "thickness_mm": th,
        "height_mm": h,
        "width_mm": w,
        "quantity": qty,
        "recipient_text": (client_name or "").strip() or None,
        "edge_treatment": et,
        "chosen_sheet": None,
    }


def _order_item_row_to_part(row: dict) -> dict:
    et = row.get("edge_treatment") or {}
    if not isinstance(et, dict):
        et = {}
    return {
        "material_name": (row.get("material_name") or "").strip(),
        "thickness_mm": int(row.get("thickness_mm") or 4),
        "height_mm": int(row.get("height_mm") or 0),
        "width_mm": int(row.get("width_mm") or 0),
        "quantity": max(1, int(row.get("quantity") or 1)),
        "recipient_text": row.get("recipient_text"),
        "edge_treatment": et,
        "chosen_sheet": None,
    }


def bundle_product_sheet_material_name(product: dict, db_models: Any) -> str:
    """Имя материала листа для раскроя по одному изделию bundle (стекло или стекло в фасаде)."""
    names = db_models.get_all_material_names() or []
    kind = str(product.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
    pl = product.get("payload") if isinstance(product.get("payload"), dict) else {}
    if kind == _KIND_FACADE:
        part = _facade_glass_to_part(pl, names, "")
        return (part.get("material_name") or "").strip() if part else ""
    if kind == _KIND_GLASS:
        part = _glass_payload_to_part(pl, names, "")
        return (part.get("material_name") or "").strip() if part else ""
    return ""


def bundle_product_cut_material_key(product: dict, db_models: Any) -> str:
    """
    Стабильный ключ материала для раскроя (плитки фильтра): имя листа из каталога + толщина,
    либо тип/цвет + толщина, если каталог не сопоставился — без повторения одного и того же текста.
    """
    names = db_models.get_all_material_names() or []
    kind = str(product.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
    pl = product.get("payload") if isinstance(product.get("payload"), dict) else {}
    if kind == _KIND_FACADE:
        part = _facade_glass_to_part(pl, names, "")
        if not part:
            return ""
        g = pl.get("Стекло") if isinstance(pl.get("Стекло"), dict) else {}
        mt = str(g.get("Название") or "").strip()
        var = str(g.get("Цвет") or "").strip()
        mn = str(part.get("material_name") or "").strip()
        th = int(part.get("thickness_mm") or 4)
        th_s = "%s мм" % th
        if mn:
            return " · ".join([mn, th_s])
        bits = []
        for x in (mt, var):
            xs = (x or "").strip()
            if xs and xs not in bits:
                bits.append(xs)
        bits.append(th_s)
        return " · ".join(bits)
    if kind != _KIND_GLASS:
        return ""
    part = _glass_payload_to_part(pl, names, "")
    if not part:
        return ""
    _izd, mat = _glass_block_izmat(pl)
    mt = str(mat.get("Тип материала") or "").strip()
    var = str(mat.get("Цвет / Вариант") or "").strip()
    mn = str(part.get("material_name") or "").strip()
    th = int(part.get("thickness_mm") or 4)
    th_s = "%s мм" % th
    if mn:
        return " · ".join([mn, th_s])
    bits = []
    for x in (mt, var):
        xs = (x or "").strip()
        if xs and xs not in bits:
            bits.append(xs)
    bits.append(th_s)
    return " · ".join(bits)


def bundle_product_cut_rect_mm(product: dict, db_models: Any) -> Tuple[int, int]:
    """Ширина и высота стекла для раскроя (мм) по изделию bundle."""
    names = db_models.get_all_material_names() or []
    kind = str(product.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
    pl = product.get("payload") if isinstance(product.get("payload"), dict) else {}
    if kind == _KIND_FACADE:
        part = _facade_glass_to_part(pl, names, "")
        if not part:
            return 0, 0
        return int(part.get("width_mm") or 0), int(part.get("height_mm") or 0)
    if kind != _KIND_GLASS:
        return 0, 0
    part = _glass_payload_to_part(pl, names, "")
    if not part:
        return 0, 0
    return int(part.get("width_mm") or 0), int(part.get("height_mm") or 0)


def bundle_product_cut_size_display_mm(product: dict, db_models: Any) -> str:
    """Строка «ширина × высота» мм для таблиц и подсказок."""
    w, h = bundle_product_cut_rect_mm(product, db_models)
    if w <= 0 or h <= 0:
        return "—"
    return "%d × %d" % (w, h)


def order_bundle_has_cuttable_glass(products: List[dict], db_models: Any) -> bool:
    """True, если в списке изделий из bundle есть прямоугольное стекло для раскроя (отдельно или в фасаде)."""
    names = db_models.get_all_material_names() or []
    for pr in products:
        kind = str(pr.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
        pl = pr.get("payload") if isinstance(pr.get("payload"), dict) else {}
        if kind == _KIND_FACADE:
            if _facade_glass_to_part(pl, names, "") is not None:
                return True
            continue
        if kind == _KIND_GLASS and _glass_payload_to_part(pl, names, "") is not None:
            return True
    return False


def cut_prefill_for_main_order(
    order_data: dict,
    db_models: Any,
    product_ids_filter: Optional[Set[str]] = None,
) -> Tuple[Optional[List[dict]], bool, str]:
    """
    Возвращает (initial_parts, lock_ui, client_name).
    initial_parts — None, если оставить стандартный пустой блок в диалоге.
    """
    client = (order_data.get("client_name") or "").strip()
    oid = order_data.get("id")
    oid_i = 0
    if oid is not None:
        try:
            oid_i = int(oid)
        except (TypeError, ValueError):
            oid_i = 0
        if oid_i > 0:
            filt_early = None
            if product_ids_filter:
                filt_early = {str(x).strip() for x in product_ids_filter if str(x).strip()}
            rows = db_models.get_order_items(oid_i) or []
            # Без фильтра — старый путь: все строки mirror_order_items.
            # С фильтром (раскрой по материалу с галочками) — только bundle: id позиций там, а items не режутся.
            if rows and not filt_early:
                return [_order_item_row_to_part(r) for r in rows], True, client

    raw = order_data.get("blocks_calc_json")
    _, products = parse_bundle(raw if raw is not None else None)
    names = db_models.get_all_material_names() or []
    filt = None
    if product_ids_filter:
        filt = {str(x).strip() for x in product_ids_filter if str(x).strip()}
    parts: List[dict] = []
    for bundle_line_idx, pr in enumerate(products or []):
        pid = str(pr.get("id") or "").strip()
        if filt is not None and pid and pid not in filt:
            continue
        kind = str(pr.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
        pl = pr.get("payload")
        if not isinstance(pl, dict):
            continue
        if kind == _KIND_FACADE:
            for fg in _facade_glass_cut_parts(pl, names, client, oid_i, bundle_line_idx + 1):
                parts.append(dict(fg, bundle_product_id=pid or None))
            continue
        if kind != _KIND_GLASS:
            continue
        p = _glass_payload_to_part(pl, names, client)
        if p:
            p = dict(p, bundle_product_id=pid or None)
            parts.append(p)
    if not parts:
        return None, False, client
    return parts, True, client
