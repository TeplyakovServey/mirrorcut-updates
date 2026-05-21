# -*- coding: utf-8 -*-
"""Чтение/запись прайсов калькулятора BLOCKS в PostgreSQL (таблицы из calc.db_postgres)."""
from __future__ import annotations

import json
import os
import sys
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

_MP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BLOCKS = os.path.join(_MP, "BLOCKS")
if _BLOCKS not in sys.path:
    sys.path.insert(0, _BLOCKS)

from calc.db_postgres import get_raw_connection

# Имена таблиц — только из этого списка (защита от SQL-инъекций).
ALLOWED_SQL_TABLES = frozenset(
    {
        "materials",
        "delivery_price",
        "facet_price",
        "polirovka_price",
        "corner_rounding_price",
        "manual_edge_processing_price",
        "plenka",
        "pokraska",
        "sandblasting_price",
        "photo_print_price",
        "drilling_prices",
        "blocks_furniture",
        "blocks_uf_skleyka_prices",
        "blocks_virez_prices",
        "packaging_price",
        "zakalka_price",
    }
)


def _blocks_path_ok(sql_table: str) -> bool:
    return sql_table in ALLOWED_SQL_TABLES


def fetch_table(sql_table: str, order_by_sql: str) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    SELECT * FROM sql_table ORDER BY order_by_sql.
    order_by_sql — только из белого списка в метаданных вкладки.
    """
    if not _blocks_path_ok(sql_table):
        raise ValueError("Неизвестная таблица")
    conn = get_raw_connection()
    if not conn:
        raise RuntimeError("Нет подключения к PostgreSQL (calc.db_postgres).")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM " + sql_table + " ORDER BY " + order_by_sql)
            cols = [d[0] for d in cur.description]
            rows = []
            for tup in cur.fetchall():
                row = {}
                for c, v in zip(cols, tup):
                    row[c] = v
                rows.append(row)
        return cols, rows
    finally:
        conn.close()


def _py_val(text: str, sample: Any, col: str = "") -> Any:
    t = (text or "").strip()
    if t == "" or t.lower() == "none":
        return None
    if isinstance(sample, bool):
        return t.lower() in ("1", "true", "да", "yes")
    if isinstance(sample, int) and not isinstance(sample, bool):
        try:
            return int(round(float(t.replace(",", "."))))
        except ValueError:
            raise ValueError("Ожидалось целое число: %s" % text)
    if isinstance(sample, float):
        try:
            return float(t.replace(",", "."))
        except ValueError:
            raise ValueError("Ожидалось число: %s" % text)
    if isinstance(sample, Decimal):
        try:
            return Decimal(t.replace(",", "."))
        except Exception:
            raise ValueError("Ожидалось число: %s" % text)
    # bytes / memoryview — не редактируем через текст
    if isinstance(sample, (bytes, memoryview)):
        raise ValueError("Двоичные поля здесь не редактируются.")
    if sample is None:
        lc = (col or "").lower()
        if any(
            k in lc
            for k in (
                "price",
                "rub",
                "_mm",
                "thickness",
                "facet",
                "material_",
                "r_",
                "diameter",
            )
        ):
            try:
                if "." in t or "," in t:
                    return float(t.replace(",", "."))
                return int(round(float(t.replace(",", "."))))
            except ValueError:
                pass
        return t
    return t


def save_table_updates(
    sql_table: str,
    pk_cols: Sequence[str],
    columns: Sequence[str],
    originals: Sequence[Dict[str, Any]],
    edited_rows: Sequence[Dict[str, str]],
) -> int:
    """
    Для каждой строки: UPDATE по pk; значения берутся из edited_rows (строковые ячейки).
    originals[i] — снимок строки из БД до правки (для типов и WHERE).
    """
    if not _blocks_path_ok(sql_table):
        raise ValueError("Неизвестная таблица")
    if len(originals) != len(edited_rows):
        raise ValueError("Число строк не совпадает")
    pk_cols = list(pk_cols)
    columns = list(columns)
    conn = get_raw_connection()
    if not conn:
        raise RuntimeError("Нет подключения к PostgreSQL.")
    updated = 0
    try:
        with conn.cursor() as cur:
            for orig, ui in zip(originals, edited_rows):
                sets: List[str] = []
                vals: List[Any] = []
                for col in columns:
                    if col in pk_cols:
                        continue
                    if col not in orig:
                        continue
                    raw_txt = ui.get(col, "")
                    try:
                        new_v = _py_val(raw_txt, orig[col], col)
                    except ValueError as ex:
                        raise ValueError("%s: %s" % (col, ex)) from ex
                    old_v = orig[col]
                    if _vals_equal(old_v, new_v):
                        continue
                    sets.append(col + " = %s")
                    vals.append(new_v)
                if not sets:
                    continue
                where_vals = [orig[pk] for pk in pk_cols]
                sql = (
                    "UPDATE "
                    + sql_table
                    + " SET "
                    + ", ".join(sets)
                    + " WHERE "
                    + " AND ".join(pk + " = %s" for pk in pk_cols)
                )
                cur.execute(sql, tuple(vals + where_vals))
                updated += int(cur.rowcount or 0)
        conn.commit()
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _vals_equal(a: Any, b: Any) -> bool:
    if a == b:
        return True
    try:
        if float(a) == float(b):
            return True
    except Exception:
        pass
    return False


def row_dict_to_ui_strings(row: Dict[str, Any]) -> Dict[str, str]:
    out = {}
    for k, v in row.items():
        if v is None:
            out[k] = ""
        elif isinstance(v, (bytes, memoryview)):
            out[k] = "<binary>"
        elif isinstance(v, Decimal):
            out[k] = str(v)
        elif isinstance(v, float):
            out[k] = str(v).rstrip("0").rstrip(".") if "." in str(v) else str(v)
        else:
            out[k] = str(v)
    return out
