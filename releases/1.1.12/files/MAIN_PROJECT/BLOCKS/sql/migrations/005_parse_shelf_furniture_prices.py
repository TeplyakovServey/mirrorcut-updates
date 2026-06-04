#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Однократное заполнение цен полкодержателей (blocks_furniture.is_shelf_holder)
по ссылкам source_url через FASAD.mdm_parser. Целые рубли, без копеек.

Запуск:
  python MAIN_PROJECT/BLOCKS/sql/migrations/005_parse_shelf_furniture_prices.py

Из папки MAIN_PROJECT/BLOCKS:
  python sql/migrations/005_parse_shelf_furniture_prices.py

Нужны: requests, beautifulsoup4, lxml.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLOCKS = os.path.abspath(os.path.join(_HERE, "..", ".."))
_MAIN = os.path.abspath(os.path.join(_BLOCKS, ".."))
for p in (_BLOCKS, _MAIN):
    if p not in sys.path:
        sys.path.insert(0, p)

from FASAD.mdm_parser import fetch_price_from_mdm_url  # noqa: E402
from calc.db_postgres import get_raw_connection  # noqa: E402


def main():
    conn = get_raw_connection()
    if not conn:
        print("Нет подключения к PostgreSQL (config / MC_PG_*).")
        return 1
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_url FROM blocks_furniture
                WHERE is_shelf_holder = TRUE AND COALESCE(source_url, '') <> ''
                ORDER BY id
                """
            )
            rows = cur.fetchall()
        if not rows:
            print("Нет строк is_shelf_holder с source_url. Примените 005_shelf_furniture.sql")
            return 1
        for fid, url in rows:
            url = (url or "").strip()
            if not url:
                continue
            p = fetch_price_from_mdm_url(url)
            if p is None:
                print("id=%s — не удалось получить цену: %s" % (fid, url))
                continue
            rub = int(round(float(p)))
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE blocks_furniture
                    SET price_legal = %s, price_individual = %s
                    WHERE id = %s
                    """,
                    (rub, rub, fid),
                )
            print("id=%s -> %s rub (%s)" % (fid, rub, url))
        conn.commit()
        print("Готово.")
        return 0
    except Exception as e:
        print("Ошибка:", e)
        conn.rollback()
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
