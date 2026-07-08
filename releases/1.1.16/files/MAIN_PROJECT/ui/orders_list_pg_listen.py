# -*- coding: utf-8 -*-
"""LISTEN mirror_orders_list — мгновенное обновление главной таблицы без polling.

Требуется миграция 018_mirror_orders_list_notify.sql.
Отключить: MC_ORDERS_PG_LISTEN=0.
"""
from __future__ import annotations

import os
import sys

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtCore import QObject, QSocketNotifier, QTimer, pyqtSignal


class OrdersListPgListen(QObject):
    """Отдельное AUTOCOMMIT-соединение; payload из pg_notify → notify(str)."""

    notify = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._conn = None
        self._notifier = None
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.setInterval(2500)
        self._reconnect_timer.timeout.connect(self._reconnect)
        v = os.environ.get("MC_ORDERS_PG_LISTEN", "1").strip().lower()
        self._enabled = v not in ("0", "false", "no", "off")

    def start(self) -> bool:
        if not self._enabled:
            return False
        try:
            import psycopg2.extensions
            from calc.db_postgres import get_raw_connection
        except Exception:
            return False
        self.stop()
        conn = get_raw_connection()
        if not conn:
            return False
        try:
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
            cur = conn.cursor()
            cur.execute("LISTEN mirror_orders_list;")
            cur.close()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            return False
        self._conn = conn
        try:
            fd = conn.fileno()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            self._conn = None
            return False
        self._notifier = QSocketNotifier(fd, QSocketNotifier.Read, self)
        self._notifier.activated.connect(self._on_socket_read)
        return True

    def stop(self) -> None:
        self._reconnect_timer.stop()
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _reconnect(self) -> None:
        if not self._enabled:
            return
        self.start()

    def _on_socket_read(self, _socket: int) -> None:
        if not self._conn:
            return
        try:
            self._conn.poll()
        except Exception:
            self.stop()
            if self._enabled:
                self._reconnect_timer.start()
            return
        while self._conn.notifies:
            n = self._conn.notifies.pop(0)
            payload = (n.payload or "").strip() or "refresh"
            self.notify.emit(payload)
