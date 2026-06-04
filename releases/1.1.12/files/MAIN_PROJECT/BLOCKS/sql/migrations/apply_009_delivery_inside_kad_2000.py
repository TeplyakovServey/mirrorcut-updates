#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Применить 009_delivery_inside_kad_2000.sql к PostgreSQL."""
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
    for line in sql_text.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
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
    path = os.path.join(_HERE, "009_delivery_inside_kad_2000.sql")
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
        print("OK: 009_delivery_inside_kad_2000")
        return 0
    except Exception as e:
        print("Error:", e)
        conn.rollback()
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
