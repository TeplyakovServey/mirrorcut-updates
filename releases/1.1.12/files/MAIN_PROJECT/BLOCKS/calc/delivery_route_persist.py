# -*- coding: utf-8 -*-
"""Сохранение / загрузка маршрута доставки в PostgreSQL; линия по сохранённым точкам."""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from calc.delivery_geo import route_length_m
from calc.db_postgres import get_raw_connection

SNAPSHOT_KEY_LAST = "last"


def ensure_delivery_route_table(conn) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS delivery_route_snapshot (
                    route_key VARCHAR(32) PRIMARY KEY,
                    payload_json JSONB NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False


def rebuild_route_from_saved_coordinates(
    coords: Optional[List[Any]],
) -> Optional[Dict[str, Any]]:
    """
    По сохранённой ломаной [[lon, lat], ...] считает длину и км для тарифа, без API.
    Возвращает dict: coordinates, length_m, distance_km_tariff или None.
    """
    if not coords or len(coords) < 2:
        return None
    clean: List[List[float]] = []
    for p in coords:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            clean.append([float(p[0]), float(p[1])])
    if len(clean) < 2:
        return None
    length_m = route_length_m(clean)
    km = max(1, int(math.ceil(length_m / 1000.0)))
    return {
        "coordinates": clean,
        "length_m": length_m,
        "distance_km_tariff": km,
    }


def save_delivery_route_snapshot(payload: Dict[str, Any], conn=None) -> bool:
    """Сохраняет последний просчитанный маршрут (JSON-сериализуемый словарь)."""
    own = conn is None
    if own:
        conn = get_raw_connection()
    if not conn:
        return False
    try:
        ensure_delivery_route_table(conn)
        body = json.dumps(payload, ensure_ascii=False)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO delivery_route_snapshot (route_key, payload_json)
                VALUES (%s, %s::jsonb)
                ON CONFLICT (route_key) DO UPDATE SET
                    payload_json = EXCLUDED.payload_json,
                    updated_at = NOW()
                """,
                (SNAPSHOT_KEY_LAST, body),
            )
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        if own and conn:
            conn.close()


def load_delivery_route_snapshot(conn=None) -> Optional[Dict[str, Any]]:
    """Загружает последний сохранённый снимок или None."""
    own = conn is None
    if own:
        conn = get_raw_connection()
    if not conn:
        return None
    try:
        ensure_delivery_route_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT payload_json::text FROM delivery_route_snapshot
                WHERE route_key = %s
                """,
                (SNAPSHOT_KEY_LAST,),
            )
            row = cur.fetchone()
        if not row or not row[0]:
            return None
        return json.loads(row[0])
    except Exception:
        return None
    finally:
        if own and conn:
            conn.close()
