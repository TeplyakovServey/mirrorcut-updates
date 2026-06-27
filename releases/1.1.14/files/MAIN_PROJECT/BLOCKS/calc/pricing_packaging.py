# -*- coding: utf-8 -*-
"""Расчёт упаковки по логике CALC_WINDOWS/test.py block_packaging."""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from calc.geometry import minimal_bounding_box_dims_triangle, izd_area_m2_for_tariff


def _area_m2_per_unit_from_izd(izd: dict) -> Optional[float]:
    v = izd.get("Площадь (м²)")
    if v is not None:
        try:
            a = float(v)
            if a > 0:
                return a
        except (TypeError, ValueError):
            pass
    shape = izd.get("Форма") or ""
    if shape in ("Прямоугольник", "Овал"):
        w = izd.get("Ширина (мм)") or 0
        h = izd.get("Высота (мм)") or 0
        try:
            wf, hf = float(w), float(h)
            if wf > 0 and hf > 0:
                return wf * hf / 1_000_000.0
        except (TypeError, ValueError):
            pass
    if shape == "Круг":
        d = izd.get("Диаметр (мм)")
        try:
            df = float(d or 0)
            if df > 0:
                return (df * df) / 1_000_000.0
        except (TypeError, ValueError):
            pass
    if shape == "Треугольник":
        a = izd.get("Сторона A (мм)")
        b = izd.get("Сторона B (мм)")
        c = izd.get("Сторона C (мм)")
        if a and b and c:
            try:
                _w, _h, am = minimal_bounding_box_dims_triangle(float(a), float(b), float(c))
                return float(am)
            except (TypeError, ValueError):
                pass
    if shape == "Сложная фигура":
        w = izd.get("Ширина (мм)") or 0
        h = izd.get("Высота (мм)") or 0
        try:
            wf, hf = float(w), float(h)
            if wf > 0 and hf > 0:
                return wf * hf / 1_000_000.0
        except (TypeError, ValueError):
            pass
    return None


def _area_m2_from_izd(izd: dict) -> Optional[float]:
    """Суммарная площадь для тарифа (с учётом количества)."""
    total = izd_area_m2_for_tariff(izd)
    if total is not None and total > 0:
        return total
    per = _area_m2_per_unit_from_izd(izd)
    if per is None:
        return None
    try:
        q = max(1, int(izd.get("Количество (шт)") or 1))
    except (TypeError, ValueError):
        q = 1
    return per * q


def _price_lookup(prices_lower: Dict[str, int], *aliases: str) -> int:
    for a in aliases:
        k = a.lower().strip()
        if k in prices_lower:
            return int(prices_lower[k] or 0)
    return 0


STRETCH_PRICE_B2B = 50
STRETCH_PRICE_B2C = 65


def _stretch_price_per_m2(pricing_tier: str | None, prices_lower: Dict[str, int]) -> int:
    t = str(pricing_tier or "").strip().lower()
    if t in ("b2c30", "b2c50", "b2c", "b2c2500", "b2c3000"):
        return STRETCH_PRICE_B2C
    if t in ("b2b", "b2b2000", ""):
        return STRETCH_PRICE_B2B
    return STRETCH_PRICE_B2B


def compute_packaging_block(
    izd: dict,
    flags: Dict[str, bool],
    prices_lower: Dict[str, int],
    pricing_tier: str | None = None,
) -> Dict[str, Any]:
    """
    flags: stretch_film, bubble_wrap, cardboard, plastic_corners
    prices_lower: packaging_type.lower() -> price (как в Streamlit).
    """
    stretch = bool(flags.get("stretch_film"))
    bubble = bool(flags.get("bubble_wrap"))
    cardboard = bool(flags.get("cardboard"))
    corners = bool(flags.get("plastic_corners"))
    if not any((stretch, bubble, cardboard, corners)):
        return {}

    area_total = _area_m2_from_izd(izd)
    if area_total is None:
        return {"Ошибка": "Нет площади изделия — нажмите «Рассчитать» в блоке стекла"}

    if area_total < 0.5:
        area_total = 0.5

    try:
        quantity = max(1, int(izd.get("Количество (шт)") or 1))
    except (TypeError, ValueError):
        quantity = 1

    details: Dict[str, Any] = {}
    total = 0

    def _line(name: str, used: bool, per_m2: int) -> int:
        nonlocal total
        if not used:
            return 0
        per_all = int(math.ceil(area_total * per_m2))
        per_one = int(math.ceil(per_all / max(1, quantity)))
        details[name] = {
            "Используется": True,
            "Цена за 1 изделие (₽)": per_one,
            "Цена за все изделия (₽)": per_all,
        }
        total += per_all
        return per_all

    _line(
        "Стрейч-плёнка",
        stretch,
        _stretch_price_per_m2(pricing_tier, prices_lower),
    )
    _line(
        "Воздушно-пузырьковая плёнка",
        bubble,
        _price_lookup(
            prices_lower,
            "воздушно-пузырьковая пленка",
            "воздушно-пузырьковая плёнка",
            "bubble",
        ),
    )
    _line(
        "Картон",
        cardboard,
        _price_lookup(prices_lower, "картон", "cardboard"),
    )

    if corners:
        unit = _price_lookup(
            prices_lower,
            "пластиковые уголки",
            "уголки",
            "corners",
        )
        total_corners = quantity * 4
        price_all = total_corners * unit
        price_one = unit * 4
        details["Пластиковые уголки"] = {
            "Используются": True,
            "Цена за 1 уголок (₽)": unit,
            "Количество уголков на изделие": 4,
            "Цена за 1 изделие (₽)": price_one,
            "Общее количество уголков": total_corners,
            "Цена за все изделия (₽)": price_all,
        }
        total += price_all

    return {
        "Общая стоимость упаковки (₽)": int(total),
        "Количество изделий": quantity,
        "Площадь для тарифа (м²)": round(area_total, 4),
        **details,
    }
