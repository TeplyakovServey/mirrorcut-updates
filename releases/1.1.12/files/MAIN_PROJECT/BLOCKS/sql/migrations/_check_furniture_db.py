# -*- coding: utf-8 -*-
"""Разовая проверка blocks_furniture в PostgreSQL. Запуск: python _check_furniture_db.py из папки migrations."""
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


def main() -> int:
    conn = get_raw_connection()
    if not conn:
        print("Нет подключения (config / MC_PG_*).")
        return 1
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'blocks_furniture'
                ORDER BY ordinal_position
                """
            )
            cols = [r[0] for r in cur.fetchall()]
            print("Колонки blocks_furniture:", cols)
            cur.execute("SELECT COUNT(*) FROM blocks_furniture")
            total = cur.fetchone()[0]
            print("Всего строк:", total)
            if "is_shelf_holder" in cols:
                cur.execute(
                    "SELECT COUNT(*) FROM blocks_furniture WHERE is_shelf_holder = TRUE"
                )
                print("Полкодержатели (is_shelf_holder):", cur.fetchone()[0])
                cur.execute(
                    """
                    SELECT id, name, color, thickness_mm, photo_base,
                           price_legal, substring(source_url from 1 for 60)
                    FROM blocks_furniture
                    WHERE is_shelf_holder = TRUE
                    ORDER BY id
                    """
                )
                for row in cur.fetchall():
                    print(row)
            else:
                print("Колонки is_shelf_holder нет — миграция 005 не применена.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
