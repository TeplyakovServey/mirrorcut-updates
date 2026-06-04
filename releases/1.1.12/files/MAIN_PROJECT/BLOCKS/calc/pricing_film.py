# -*- coding: utf-8 -*-
"""Логика плёнки: доплата до 0.5 м² суммарной площади (как calculate.recalculate_film_cost)."""
from __future__ import annotations

from typing import Optional, Tuple


def film_price_per_order(
    area_per_item_m2: float, quantity: int, price_m2: int
) -> Optional[Tuple[int, int]]:
    """
    Одна строка заказа: (цена за изделие, общая за qty).
    Если суммарная площадь (area * qty) < 0.5 м² — добавляем долю доплаты до 0.5.
    """
    q = max(1, int(quantity or 1))
    af = float(area_per_item_m2 or 0)
    if af <= 0:
        return None
    total_area = af * q
    base = af * price_m2 * q
    if total_area < 0.5:
        item_share = (af * q) / total_area if total_area > 0 else 1.0
        additional = (0.5 - total_area) * price_m2 * item_share
        total_cost = int(round(base + additional))
    else:
        total_cost = int(round(base))
    per_one = int(round(total_cost / q)) if q else total_cost
    return per_one, total_cost
