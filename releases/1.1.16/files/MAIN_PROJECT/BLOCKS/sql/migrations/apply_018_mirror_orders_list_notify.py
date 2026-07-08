#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Применить 018_mirror_orders_list_notify.sql к PostgreSQL."""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLOCKS = os.path.abspath(os.path.join(_HERE, "..", ".."))
_MAIN = os.path.abspath(os.path.join(_BLOCKS, ".."))
for p in (_BLOCKS, _MAIN):
    if p not in sys.path:
        sys.path.insert(0, p)

from calc.db_postgres import get_raw_connection  # noqa: E402


def _sql_statements(sql_text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    dollar_depth = 0
    for line in sql_text.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        dollar_depth = (dollar_depth + line.count("$$")) % 2
        if dollar_depth == 0 and line.rstrip().endswith(";"):
            block = "\n".join(buf).strip()
            if block:
                parts.append(block)
            buf = []
    if buf:
        block = "\n".join(buf).strip()
        if block:
            parts.append(block)
    return parts


def main() -> int:
    path = os.path.join(_HERE, "018_mirror_orders_list_notify.sql")
    if not os.path.isfile(path):
        print("No file:", path)
        return 1
    conn = get_raw_connection()
    if not conn:
        print("No PostgreSQL connection.")
        return 1
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for stmt in _sql_statements(text):
            with conn.cursor() as cur:
                cur.execute(stmt)
        conn.commit()
        print("OK: 018_mirror_orders_list_notify")
        return 0
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print("Error:", e)
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
