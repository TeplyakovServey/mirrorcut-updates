# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from calc.db_postgres import fetch_sandblasting_price

SAND_TYPE_TO_DB = {
    "Сплошное матирование": "сплошное матирование",
    "Рисунок": "рисунок",
    "Пескоструйная кнопка": "пескоструйная кнопка",
    "Полосы ЗСП": "полосы зсп",
}


def compute_sandblasting_cost(
    selected: Dict[str, Any], sand: Dict[str, Any], conn=None
) -> Optional[Tuple[int, int]]:
    """
    Возвращает (цена за изделие, цена за все) или None если услуга выключена.
    sand: Пескоструй, Тип, Двухсторонний, Файл (опц.)
    """
    if not sand.get("Пескоструй"):
        return None
    izd = selected.get("Параметры изделия", {})
    q = int(izd.get("Количество (шт)") or 1)
    area = float(izd.get("Площадь (м²)") or 0)
    if area <= 0:
        return None
    if area < 0.5:
        area = 0.5
    typ_ru = sand.get("Тип") or ""
    db_key = SAND_TYPE_TO_DB.get(typ_ru, typ_ru.lower())
    price = fetch_sandblasting_price(db_key, conn=conn)
    # «Полосы ЗСП» считаются как «Рисунок»; если в БД нет строки — берём цену рисунка
    if typ_ru == "Полосы ЗСП" and price <= 0:
        price = fetch_sandblasting_price("рисунок", conn=conn)
    double = bool(sand.get("Двухсторонний"))
    if typ_ru == "Пескоструйная кнопка":
        cost_one = int(price)
        cost_all = cost_one * q
        return cost_one, cost_all
    factor = 2 if double else 1
    cost_one = int(round(price * area * factor))
    cost_all = cost_one * q
    return cost_one, cost_all
