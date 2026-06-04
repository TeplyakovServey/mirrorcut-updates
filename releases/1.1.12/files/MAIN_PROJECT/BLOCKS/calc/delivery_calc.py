# -*- coding: utf-8 -*-
"""Тарифы доставки из БД и формула как в Streamlit (x.py block_additional_services)."""
from __future__ import annotations

from typing import Dict, Optional

from calc.db_postgres import get_raw_connection, remote_db_price_int

# Ключ маршрутизации и геокодера, как в CALC_WINDOWS (test.py, x.py).
_ROUTING_ACCESS_TOKEN = (
    "sk.eyJ1IjoidGVwbHlha292c2VyZ2V5MTk5NiIsImEiOiJjbWE4cm50ZGcxN3kyMmtxdWJjdnBjcXF4In0."
    "2dHFxrmJsKklV3vL_4d_jg"
)


def fetch_delivery_prices(conn=None) -> Dict[str, int]:
    """Ключи: «В пределах КАД», «За КАД база», «За 1 км», «Замер» и др. из delivery_price."""
    own = conn is None
    if own:
        conn = get_raw_connection()
    prices: Dict[str, int] = {}
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name, price FROM delivery_price")
                for name, price in cur.fetchall():
                    if name:
                        prices[str(name)] = remote_db_price_int(price, "delivery")
        except Exception:
            pass
    if own and conn:
        conn.close()
    return prices


def zamer_visit_price_rub(del_prices: Dict[str, int], inside_kad: bool, distance_km: Optional[int]) -> int:
    """Тариф выезда замерщика: «Замер» внутри КАД, иначе база+км (свои строки или как у доставки)."""
    if inside_kad:
        if "Замер" in del_prices:
            return int(del_prices.get("Замер", 0) or 0)
        return int(del_prices.get("В пределах КАД", 0) or 0)
    if distance_km is None:
        return 0
    base = int(
        del_prices.get("Замер за КАД база", del_prices.get("За КАД база", 0)) or 0
    )
    per_km = int(del_prices.get("Замер за 1 км", del_prices.get("За 1 км", 0)) or 0)
    return base + int(distance_km) * per_km


def montazh_price_rub(del_prices: Dict[str, int]) -> int:
    """Фиксированная цена монтажа (строка «Монтаж» в delivery_price, по умолчанию 2000 ₽)."""
    if "Монтаж" in del_prices:
        return int(del_prices.get("Монтаж", 0) or 0)
    return 2000


def delivery_price_rub(del_prices: Dict[str, int], inside_kad: bool, distance_km: Optional[int]) -> int:
    """
    Внутри КАД: «В пределах КАД».
    Вне: «За КАД база» + distance_km * «За 1 км» (distance_km обязателен).
    """
    if inside_kad:
        return int(del_prices.get("В пределах КАД", 0) or 0)
    if distance_km is None:
        return 0
    base = int(del_prices.get("За КАД база", 0) or 0)
    per_km = int(del_prices.get("За 1 км", 0) or 0)
    return base + int(distance_km) * per_km


def routing_access_token() -> str:
    return _ROUTING_ACCESS_TOKEN
