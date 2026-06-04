# -*- coding: utf-8 -*-
"""
Применяет 001_manual_edge_sand_photo.sql к PostgreSQL.
Параметры подключения — как у калькулятора: корень проекта MIRROR_CUT / config.py
(или MC_PG_* в окружении через db_postgres._config_tuple — здесь напрямую config).

Запуск из корня MIRROR_CUT:
  python MAIN_PROJECT/BLOCKS/sql/migrations/apply_001_manual_edge_sand_photo.py
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import psycopg2
except ImportError:
    print("Нужен psycopg2: pip install psycopg2-binary")
    sys.exit(1)

import config  # noqa: E402

SQL_FILE = os.path.join(os.path.dirname(__file__), "001_manual_edge_sand_photo.sql")


def _statements(sql_text: str) -> list[str]:
    lines = [ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")]
    text = "\n".join(lines)
    parts: list[str] = []
    buf: list[str] = []
    for line in text.splitlines():
        buf.append(line)
        if line.rstrip().endswith(";"):
            parts.append("\n".join(buf).strip())
            buf = []
    if buf:
        parts.append("\n".join(buf).strip())
    return [p for p in parts if p]


def main() -> None:
    with open(SQL_FILE, encoding="utf-8") as f:
        sql_text = f.read()
    stmts = _statements(sql_text)
    cfg = config.DB_CONFIG
    conn = psycopg2.connect(
        dbname=cfg["dbname"],
        user=cfg["user"],
        password=cfg["password"],
        host=cfg["host"],
        port=str(cfg.get("port", "5432")),
    )
    conn.autocommit = True
    cur = conn.cursor()
    try:
        for i, st in enumerate(stmts, 1):
            cur.execute(st)
            print("OK [%s/%s]" % (i, len(stmts)))
    finally:
        cur.close()
        conn.close()
    print("Done: %s SQL statements applied." % len(stmts))


if __name__ == "__main__":
    main()
