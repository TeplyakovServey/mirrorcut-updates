#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Создаёт таблицу blocks_furniture (если нет), колонки полок, заливает держатели + Grace/Kristal 6/8 мм.
Повторный запуск безопасен (INSERT с NOT EXISTS).

Затем по желанию подтягивает цены с МДМ по source_url (как 005_parse_shelf_furniture_prices.py).

Запуск из папки migrations:
  python apply_006_blocks_furniture_bootstrap.py
  python apply_006_blocks_furniture_bootstrap.py --no-prices

Из BLOCKS:
  python sql/migrations/apply_006_blocks_furniture_bootstrap.py
"""
from __future__ import annotations

import argparse
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


def _apply_sql_file(conn, path: str) -> None:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    for stmt in _sql_statements(text):
        with conn.cursor() as cur:
            cur.execute(stmt)
    conn.commit()


def _update_prices_from_mdm(conn) -> int:
    from FASAD.mdm_parser import fetch_price_from_mdm_url

    updated = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source_url FROM blocks_furniture
            WHERE is_shelf_holder = TRUE AND COALESCE(source_url, '') <> ''
            ORDER BY id
            """
        )
        rows = cur.fetchall()
    for fid, url in rows:
        url = (url or "").strip()
        if not url:
            continue
        p = fetch_price_from_mdm_url(url)
        if p is None:
            print("id=%s — цена не получена: %s" % (fid, url))
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
        updated += 1
        print("id=%s -> %s rub" % (fid, rub))
    conn.commit()
    return updated


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--no-prices",
        action="store_true",
        help="Не ходить на сайт МДМ за ценами",
    )
    args = ap.parse_args()

    sql_path = os.path.join(_HERE, "006_blocks_furniture_bootstrap.sql")
    if not os.path.isfile(sql_path):
        print("Нет файла:", sql_path)
        return 1

    conn = get_raw_connection()
    if not conn:
        print("Нет подключения к PostgreSQL (config.py в корне MIRROR_CUT или MC_PG_*).")
        return 1
    try:
        print("Применяю", sql_path)
        _apply_sql_file(conn, sql_path)
        print("SQL применён.")
        if not args.no_prices:
            print("Обновление цен с mdm-complect.ru …")
            n = _update_prices_from_mdm(conn)
            print("Обновлено цен:", n)
        return 0
    except Exception as e:
        print("Ошибка:", e)
        conn.rollback()
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
