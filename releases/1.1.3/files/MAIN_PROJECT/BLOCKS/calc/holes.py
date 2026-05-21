# -*- coding: utf-8 -*-
"""Отверстия: как block_holes в test.py."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def find_drilling_price(rows: List[dict], diameter: int, thickness: int) -> int:
    if thickness < 4 or thickness > 10 or not rows:
        return 0
    col = "thickness_%smm" % thickness
    d_use = min(int(diameter), 70)
    for r in rows:
        range_text = r.get("diameter_range") or ""
        p = r.get(col)
        if p is None:
            continue
        nums = [
            int(s)
            for s in range_text.replace("мм", "").replace("D", "").replace("-", " ").split()
            if s.isdigit()
        ]
        if len(nums) == 1:
            min_d, max_d = 0, nums[0]
        elif len(nums) == 2:
            min_d, max_d = nums[0], nums[1]
        else:
            continue
        if min_d <= d_use <= max_d:
            return int(p)
    return 0


def compute_holes_line_details(
    rows: List[dict],
    holes: List[Dict[str, Any]],
    thickness: int,
    tempered: bool,
) -> Tuple[List[Optional[Dict[str, Any]]], int, bool, int, int]:
    """
    По каждому элементу holes — словарь {unit, line, qty, d} или None если строка неполная.
    Возвращает (line_details, subtotal, markup, final_total, qty_sum).
    """
    line_details: List[Optional[Dict[str, Any]]] = []
    diameters = set()
    qty_sum = 0
    subtotal = 0
    for h in holes:
        qty = int(h.get("Количество") or 0)
        d = int(h.get("Размер") or 0)
        if qty <= 0 or d <= 0:
            line_details.append(None)
            continue
        sink = bool(h.get("Зенковка")) or tempered
        unit = find_drilling_price(rows, d, thickness)
        if sink:
            unit = int(unit * 1.5)
        line = unit * qty
        line_details.append({"unit": unit, "line": line, "qty": qty, "d": d, "sink": sink})
        subtotal += line
        diameters.add(min(d, 70))
        qty_sum += qty
    markup = qty_sum >= 7 or len(diameters) >= 4
    final = int(subtotal * 1.5) if markup and subtotal else subtotal
    return line_details, subtotal, markup, final, qty_sum


def compute_holes_cost(
    rows: List[dict],
    holes: List[Dict[str, Any]],
    thickness: int,
    tempered: bool,
) -> Tuple[int, bool]:
    _det, subtotal, markup, final, _qs = compute_holes_line_details(
        rows, holes, thickness, tempered
    )
    return final, markup
