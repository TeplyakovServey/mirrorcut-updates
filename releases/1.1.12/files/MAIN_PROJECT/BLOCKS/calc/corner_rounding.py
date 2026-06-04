# -*- coding: utf-8 -*-
"""Скругление углов: как block_corner_processing в CALC_WINDOWS/test.py (4068+)."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from calc.corner_labels import corner_sort_keys
from calc.db_postgres import fetch_scalar, get_raw_connection, remote_db_price_int

# (ключ вершины в данных, R мм, ₽ за этот угол)
RoundCornerLine = Tuple[str, int, int]
# (заголовок строки, ₽) — криволинейка / кап 100
RoundExtraLine = Tuple[str, int]

_RANGE_COLS = [
    (3, 10, "r_3_10"),
    (11, 20, "r_11_20"),
    (21, 35, "r_21_35"),
    (36, 50, "r_36_50"),
    (51, 100, "r_51_100"),
]

_BAND_KEYS = [c for _a, _b, c in _RANGE_COLS]


def parse_thickness_mm(raw: Any) -> int:
    """Толщина из материала: «4», « 6 », «4.0», «4 мм» и т.п."""
    if raw is None:
        return 0
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, float):
        return max(0, int(round(raw)))
    if isinstance(raw, Decimal):
        return max(0, int(raw))
    s = str(raw).strip().replace(",", ".")
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    m = re.match(r"^\D*(\d+)", s)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    if m:
        return max(0, int(float(m.group(1))))
    return 0


def _norm_key(s: str) -> str:
    return str(s).lower().replace("-", "_").strip()


def _band_price_from_desc_row(desc: List[str], row: tuple) -> Optional[Dict[str, float]]:
    """
    Собирает цены по 5 диапазонам из имён столбцов; при неудаче — позиции как в Streamlit (row[1]..[5]).
    """
    d = {_norm_key(desc[i]): row[i] for i in range(len(desc))}
    out: Dict[str, float] = {}
    for band in _BAND_KEYS:
        v = d.get(band)
        if v is None:
            for k, val in d.items():
                if k == band or k.endswith(band) or band.replace("_", "") in k.replace("_", ""):
                    v = val
                    break
        if v is None:
            break
        try:
            out[band] = float(v or 0)
        except (TypeError, ValueError):
            out.clear()
            break
    else:
        if len(out) == 5:
            return out

    n = len(row)
    # Как в test.py: row[0] — не цена диапазона; первый прайс — row[1]
    if n >= 6:
        try:
            return {band: float(row[i + 1] or 0) for i, band in enumerate(_BAND_KEYS)}
        except (TypeError, ValueError, IndexError):
            pass
    # id + thickness + 5 цен
    if n >= 7:
        try:
            return {band: float(row[i + 2] or 0) for i, band in enumerate(_BAND_KEYS)}
        except (TypeError, ValueError, IndexError):
            pass
    if n == 5:
        try:
            return {band: float(row[i] or 0) for i, band in enumerate(_BAND_KEYS)}
        except (TypeError, ValueError, IndexError):
            pass
    if n > 7:
        try:
            tail = row[-5:]
            return {band: float(tail[i] or 0) for i, band in enumerate(_BAND_KEYS)}
        except (TypeError, ValueError, IndexError):
            pass
    return None


def _load_corner_band_prices(conn, thickness_mm: int) -> Optional[Dict[str, float]]:
    if not conn or thickness_mm <= 0:
        return None
    th = int(thickness_mm)
    sqls = [
        "SELECT * FROM corner_rounding_price WHERE thickness = %s",
        "SELECT * FROM corner_rounding_price WHERE thickness_mm = %s",
        "SELECT * FROM corner_rounding_price WHERE glass_thickness = %s",
    ]
    for sql in sqls:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (th,))
                row = cur.fetchone()
                if not row:
                    continue
                desc = [d[0] for d in cur.description]
                parsed = _band_price_from_desc_row(desc, row)
                if parsed is not None:
                    return {k: float(remote_db_price_int(v, "corner_rounding")) for k, v in parsed.items()}
        except Exception:
            continue
    return None


def _crivalineyka_price(perimeter_mm: float, thickness_mm: int, conn) -> int:
    v = fetch_scalar(
        "SELECT price FROM edge_processing_price WHERE thickness_mm = %s LIMIT 1",
        (int(thickness_mm),),
        conn=conn,
    )
    if v is None:
        return 0
    ppm = remote_db_price_int(v, "edge_processing")
    return int(round(float(ppm) * (float(perimeter_mm) / 1000.0)))


def _not_crivolineyka_total(rounding: Dict[str, int], conn, thickness_mm: int) -> int:
    capped = {k: min(100, int(v)) for k, v in rounding.items()}
    bands = _load_corner_band_prices(conn, thickness_mm)
    if not bands:
        return 0
    cost = 0
    for r in capped.values():
        if r <= 0:
            continue
        for min_r, max_r, col in _RANGE_COLS:
            if min_r <= r <= max_r:
                cost += int(round(float(bands.get(col) or 0)))
                break
    return int(round(cost))


def compute_corner_rounding_detail(
    rounding_values: Dict[str, Any],
    perimeter_mm: float,
    thickness_mm: int,
    conn=None,
    shape: str = "",
) -> Tuple[int, bool, List[RoundCornerLine], List[RoundExtraLine]]:
    """
    Возвращает (
        стоимость за изделие,
        криволинейка,
        построчно по углам: [(ключ вершины, R мм, ₽), ...],
        доп. строки: [(заголовок, ₽)] — при R>100 одна суммарная строка,
    ).
    """
    own = conn is None
    if own:
        conn = get_raw_connection()
    if not conn:
        return 0, False, [], []
    try:
        vals: Dict[str, int] = {}
        for k, v in (rounding_values or {}).items():
            r = int(v or 0)
            if r > 0:
                vals[str(k)] = r
        if not vals:
            return 0, False, [], []

        max_r = max(vals.values())
        th = int(parse_thickness_mm(thickness_mm) or thickness_mm)
        per = float(perimeter_mm)

        if max_r > 100:
            p1 = _crivalineyka_price(per, th, conn)
            p2 = _not_crivolineyka_total(vals, conn, th)
            if p1 > p2:
                return (
                    p1,
                    True,
                    [],
                    [("Криволинейка (R > 100 мм), за изделие", p1)],
                )
            return (
                p2,
                False,
                [],
                [("Скругление по прайсу (кап 100 мм), за изделие", p2)],
            )

        bands = _load_corner_band_prices(conn, th)
        if not bands:
            return 0, False, [], []

        order = corner_sort_keys(shape) or sorted(vals.keys())
        per_corner: List[RoundCornerLine] = []
        total = 0
        for corner in order:
            if corner not in vals:
                continue
            r = vals[corner]
            piece = 0
            for min_r, max_r, col in _RANGE_COLS:
                if min_r <= r <= max_r:
                    piece = int(round(float(bands.get(col) or 0)))
                    break
            total += piece
            per_corner.append((corner, r, piece))
        return int(round(total)), False, per_corner, []
    finally:
        if own and conn:
            conn.close()
