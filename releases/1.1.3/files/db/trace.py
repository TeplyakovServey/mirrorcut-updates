# -*- coding: utf-8 -*-
"""Трассировка обращений к PostgreSQL и фаз UI (включается только явно).

Переменные окружения:
  MC_DB_TRACE=1       — логировать каждый блок ``with get_connection()`` (время, порядковый номер).
  MC_DB_TRACE_SQL=1   — дополнительно каждый ``cursor.execute`` (усечённый текст SQL, время).
                        Имеет смысл только вместе с MC_DB_TRACE или отдельно (SQL всё равно пишется).

Вывод в stderr, с меткой времени HH:MM:SS.mmm.

Пример (PowerShell):
  $env:MC_DB_TRACE=\"1\"
  $env:MC_DB_TRACE_SQL=\"1\"
  python MAIN_PROJECT/run.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Type

from psycopg2.extras import RealDictCursor

_lock = threading.Lock()
_seq = 0
_sql_seq = 0


def enabled() -> bool:
    return (os.environ.get("MC_DB_TRACE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def sql_enabled() -> bool:
    return (os.environ.get("MC_DB_TRACE_SQL") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def ui(msg: str) -> None:
    """Точка в UI (тот же флаг MC_DB_TRACE)."""
    if not enabled():
        return
    print(f"[MC_TRACE {_ts()}] [UI] {msg}", file=sys.stderr, flush=True)


def _sql_preview(query: Any, max_len: int = 160) -> str:
    if query is None:
        return ""
    s = str(query).replace("\n", " ").strip()
    if len(s) > max_len:
        s = s[: max_len - 3] + "..."
    return s


class TracingRealDictCursor(RealDictCursor):
    """RealDictCursor с замером execute (только при MC_DB_TRACE_SQL)."""

    def execute(self, query, vars=None):
        if not sql_enabled():
            if vars is not None:
                return super().execute(query, vars)
            return super().execute(query)
        global _sql_seq
        t0 = time.perf_counter()
        try:
            if vars is not None:
                return super().execute(query, vars)
            return super().execute(query)
        finally:
            dt_ms = (time.perf_counter() - t0) * 1000
            with _lock:
                _sql_seq += 1
                sn = _sql_seq
            print(
                f"[MC_TRACE {_ts()}] [SQL #{sn}] {dt_ms:7.2f} ms  {_sql_preview(query)}",
                file=sys.stderr,
                flush=True,
            )


def cursor_factory_class() -> Type[RealDictCursor]:
    return TracingRealDictCursor if sql_enabled() else RealDictCursor


@contextmanager
def connection_span():
    """Оборачивает весь блок get_connection (включая работу курсора и commit)."""
    if not enabled():
        yield
        return
    global _seq
    with _lock:
        _seq += 1
        n = _seq
    t0 = time.perf_counter()
    print(f"[MC_TRACE {_ts()}] [DB #{n}] get_connection START", file=sys.stderr, flush=True)
    try:
        yield
    finally:
        dt_ms = (time.perf_counter() - t0) * 1000
        print(
            f"[MC_TRACE {_ts()}] [DB #{n}] get_connection END   {dt_ms:7.2f} ms",
            file=sys.stderr,
            flush=True,
        )
