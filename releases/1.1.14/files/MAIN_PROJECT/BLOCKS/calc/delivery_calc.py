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

DELIVERY_BASE_B2B = 2000
DELIVERY_BASE_B2C = 2500
DELIVERY_BASE_B2C_PREMIUM = 3000

DELIVERY_TIER_B2B = "b2b2000"
DELIVERY_TIER_B2C = "b2c2500"
DELIVERY_TIER_B2C3000 = "b2c3000"

DELIVERY_TIER_CHOICES = (
    (DELIVERY_TIER_B2B, DELIVERY_BASE_B2B, "B2B — 2000 ₽"),
    (DELIVERY_TIER_B2C, DELIVERY_BASE_B2C, "B2C — 2500 ₽"),
    (DELIVERY_TIER_B2C3000, DELIVERY_BASE_B2C_PREMIUM, "B2C — 3000 ₽"),
)


def default_delivery_tier_code(pricing_tier: str | None) -> str:
    t = str(pricing_tier or "").strip().lower()
    if t in ("b2c30", "b2c50"):
        return DELIVERY_TIER_B2C
    return DELIVERY_TIER_B2B


def delivery_base_rub_from_tier_code(tier_code: str | None) -> int:
    code = str(tier_code or "").strip().lower()
    for tc, rub, _label in DELIVERY_TIER_CHOICES:
        if code == tc:
            return int(rub)
    if code in ("b2b", "b2b2000"):
        return DELIVERY_BASE_B2B
    if code in ("b2c", "b2c2500", "b2c30", "b2c50"):
        return DELIVERY_BASE_B2C
    if code in ("b2c3000",):
        return DELIVERY_BASE_B2C_PREMIUM
    return DELIVERY_BASE_B2B


def default_delivery_base_rub(pricing_tier: str | None) -> int:
    return delivery_base_rub_from_tier_code(default_delivery_tier_code(pricing_tier))


def delivery_tariff_payload(tier_code: str | None) -> dict:
    code = default_delivery_tier_code(tier_code) if tier_code in (None, "") else str(tier_code).strip().lower()
    if not any(code == tc for tc, _r, _l in DELIVERY_TIER_CHOICES):
        code = default_delivery_tier_code(tier_code)
    rub = delivery_base_rub_from_tier_code(code)
    return {"Тариф": code, "Базовый тариф (₽)": int(rub)}


def delivery_base_rub_from_data(data: dict | None, del_prices: Dict[str, int] | None = None) -> int:
    """База доставки из сохранённого блока или fallback из БД / B2B."""
    if isinstance(data, dict):
        raw = data.get("Базовый тариф (₽)")
        if raw is not None:
            try:
                v = int(raw)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                pass
        tier = data.get("Тариф")
        if tier:
            return delivery_base_rub_from_tier_code(str(tier))
    prices = del_prices or {}
    fb = int(prices.get("В пределах КАД", 0) or 0)
    return fb if fb > 0 else DELIVERY_BASE_B2B


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


def delivery_price_rub(
    del_prices: Dict[str, int],
    inside_kad: bool,
    distance_km: Optional[int],
    base_rub: Optional[int] = None,
) -> int:
    """
    Внутри КАД: base_rub или «В пределах КАД» из БД.
    Вне: base_rub или «За КАД база» + distance_km * «За 1 км» (distance_km обязателен).
    """
    if inside_kad:
        if base_rub is not None:
            return int(base_rub)
        return int(del_prices.get("В пределах КАД", 0) or 0) or DELIVERY_BASE_B2B
    if distance_km is None:
        return 0
    base = int(base_rub if base_rub is not None else del_prices.get("За КАД база", 0) or 0)
    if base <= 0:
        base = DELIVERY_BASE_B2B
    per_km = int(del_prices.get("За 1 км", 0) or 0)
    return base + int(distance_km) * per_km


def routing_access_token() -> str:
    return _ROUTING_ACCESS_TOKEN
