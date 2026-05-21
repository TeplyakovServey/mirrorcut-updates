# -*- coding: utf-8 -*-
"""Обработка кромки для этикетки изделия (K): из расчёта стекла по размерам мм."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def _same_dims(a_w: float, a_h: float, b_w: float, b_h: float, tol: float = 8.0) -> bool:
    return (
        (abs(a_w - b_w) <= tol and abs(a_h - b_h) <= tol)
        or (abs(a_w - b_h) <= tol and abs(a_h - b_w) <= tol)
    )


def _extract_glass_blocks_from_payload(pl: Dict[str, Any]) -> Dict[str, Any]:
    bs = pl.get("blocks_selected") if isinstance(pl.get("blocks_selected"), dict) else {}
    if bs:
        return dict(bs)
    out: Dict[str, Any] = {}
    for bk in (
        "Полировка",
        "Шлифовка",
        "Фацет",
        "Facet",
        "Фасет",
        "Скругление углов",
        "Срезать угол",
        "Пескоструй",
        "Фотопечать",
        "Плёнка",
        "Покраска",
        "Упаковка",
        "Отверстия",
        "Материал",
    ):
        v = pl.get(bk)
        if isinstance(v, dict):
            out[bk] = v
    return out


def _edges_from_matched_bs(matched_bs: Dict[str, Any]) -> Dict[str, Any]:
    """Те же ключи, что ожидает logic.labels._draw_piece_label (top/right/bottom/left, facet_mm)."""
    edge: Dict[str, Any] = {}
    fc: Dict[str, Any] = {}
    for fkey in ("Фацет", "Facet", "Фасет"):
        cand = matched_bs.get(fkey)
        if isinstance(cand, dict):
            fc = cand
            break
    pol = matched_bs.get("Полировка") if isinstance(matched_bs.get("Полировка"), dict) else {}
    shl = matched_bs.get("Шлифовка") if isinstance(matched_bs.get("Шлифовка"), dict) else {}
    if isinstance(fc, dict):
        facet_on = any(
            bool(fc.get(k))
            for k in ("Нужен", "Включено", "Активирован", "Использовать", "Facet", "Нужен фацет")
        )
    else:
        facet_on = False
    if facet_on:
        try:
            fmm = int(fc.get("Top") or fc.get("Верх") or 0)
        except (TypeError, ValueError):
            fmm = 0
        if fmm > 0:
            edge["facet_mm"] = fmm
        for ek, rk in (("Top", "top"), ("Bottom", "bottom"), ("Left", "left"), ("Right", "right")):
            try:
                if int(fc.get(ek) or 0) > 0:
                    edge[rk] = "facet"
            except (TypeError, ValueError):
                pass
    pol_on = isinstance(pol, dict) and any(
        bool(pol.get(k)) for k in ("Нужна полировка", "Включено", "Активирован", "Использовать")
    )
    if pol_on:
        for ek, rk in (("Верх", "top"), ("Низ", "bottom"), ("Лево", "left"), ("Право", "right")):
            if bool(pol.get(ek) or pol.get("Кромка")):
                edge[rk] = "polishing"
    shl_on = isinstance(shl, dict) and any(
        bool(shl.get(k)) for k in ("Нужна шлифовка", "Включено", "Активирован", "Использовать")
    )
    if shl_on:
        for ek, rk in (("Верх", "top"), ("Низ", "bottom"), ("Лево", "left"), ("Право", "right")):
            if bool(shl.get(ek) or shl.get("Кромка")):
                edge[rk] = "grinding"
    return edge


def _match_bs_for_dims(parsed_products: List[Dict[str, Any]], piece_w: float, piece_h: float) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Вернуть (matched_bs, matched_pl) для изделия с размерами piece_w × piece_h."""
    matched_bs: Dict[str, Any] = {}
    matched_pl: Dict[str, Any] = {}
    for pr in parsed_products:
        kind = str(pr.get("kind") or "").strip()
        pl = pr.get("payload") if isinstance(pr.get("payload"), dict) else {}
        if kind == "facade":
            g = pl.get("Стекло") if isinstance(pl.get("Стекло"), dict) else {}
            if not g:
                continue
            try:
                gw = float(g.get("Ширина (мм)") or pl.get("Ширина_мм") or 0)
                gh = float(g.get("Высота (мм)") or pl.get("Высота_мм") or 0)
            except (TypeError, ValueError):
                gw = gh = 0.0
            if piece_w > 0 and piece_h > 0 and _same_dims(piece_w, piece_h, gw, gh):
                bs = g.get("blocks_selected") if isinstance(g.get("blocks_selected"), dict) else {}
                return (dict(bs) if bs else {}, pl)
        if kind == "glass_mirror":
            iz = pl.get("Параметры изделия") if isinstance(pl.get("Параметры изделия"), dict) else {}
            try:
                gw = float(iz.get("Ширина (мм)") or 0)
                gh = float(iz.get("Высота (мм)") or 0)
            except (TypeError, ValueError):
                gw = gh = 0.0
            if piece_w > 0 and piece_h > 0 and _same_dims(piece_w, piece_h, gw, gh):
                return (_extract_glass_blocks_from_payload(pl), pl)
    return matched_bs, matched_pl


def _fallback_single_glass_bs(parsed_products: List[Dict[str, Any]]) -> Dict[str, Any]:
    glass_candidates: List[Dict[str, Any]] = []
    for pr in parsed_products:
        kind = str(pr.get("kind") or "").strip()
        if kind not in ("glass_mirror", "facade"):
            continue
        pl = pr.get("payload") if isinstance(pr.get("payload"), dict) else {}
        if kind == "glass_mirror":
            bs = _extract_glass_blocks_from_payload(pl)
            if bs:
                glass_candidates.append(pr)
        else:
            g = pl.get("Стекло") if isinstance(pl.get("Стекло"), dict) else {}
            bs = g.get("blocks_selected") if isinstance(g.get("blocks_selected"), dict) else {}
            if bs:
                glass_candidates.append(pr)
    if len(glass_candidates) != 1:
        return {}
    pr = glass_candidates[0]
    kind = str(pr.get("kind") or "").strip()
    pl = pr.get("payload") if isinstance(pr.get("payload"), dict) else {}
    if kind == "glass_mirror":
        return _extract_glass_blocks_from_payload(pl)
    g = pl.get("Стекло") if isinstance(pl.get("Стекло"), dict) else {}
    bs = g.get("blocks_selected") if isinstance(g.get("blocks_selected"), dict) else {}
    return dict(bs) if bs else {}


def edge_treatment_for_piece_mm(order_row: Dict[str, Any], width_mm: int, height_mm: int, tol: float = 8.0) -> Dict[str, Any]:
    """
    Словарь обработки кромки для этикетки (полировка/шлифовка/фацет по сторонам),
    по размерам изделия и JSON расчёта заказа.
    """
    if not order_row:
        return {}
    try:
        pw = float(width_mm)
        ph = float(height_mm)
    except (TypeError, ValueError):
        return {}
    if pw <= 0 or ph <= 0:
        return {}
    try:
        from logic.blocks_bundle import parse_bundle  # noqa: WPS433
    except Exception:
        try:
            from MAIN_PROJECT.logic.blocks_bundle import parse_bundle  # noqa: WPS433
        except Exception:
            return {}
    try:
        _v, products = parse_bundle(order_row.get("blocks_calc_json"))
    except Exception:
        return {}
    parsed = [pr for pr in (products or []) if isinstance(pr, dict)]
    if not parsed:
        return {}
    matched_bs, _pl = _match_bs_for_dims(parsed, pw, ph)
    if not matched_bs:
        matched_bs = _fallback_single_glass_bs(parsed)
    if not matched_bs:
        return {}
    return _edges_from_matched_bs(matched_bs)
