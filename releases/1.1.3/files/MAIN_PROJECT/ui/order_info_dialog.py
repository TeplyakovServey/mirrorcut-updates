# -*- coding: utf-8 -*-
"""Информация по заказу с быстрым доступом к клиенту."""
import os
import sys

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QScrollArea, QWidget

from db import models as db_models
from db_main import order_status_to_ru


class OrderInfoDialog(QDialog):
    def __init__(self, order_id, parent=None):
        super().__init__(parent)
        self._order_id = int(order_id)
        self.setWindowTitle("Информация по заказу №%s" % self._order_id)
        self.resize(760, 520)
        lay = QVBoxLayout(self)
        row = db_models.get_order_for_labels(self._order_id)
        if not row:
            lay.addWidget(QLabel("Заказ не найден."))
            return
        lay.addWidget(QLabel("Клиент: %s" % (row.get("client_name") or "—")))
        lay.addWidget(QLabel("Статус: %s" % order_status_to_ru(row.get("status"))))
        created = row.get("created_at")
        ds = created.strftime("%d.%m.%Y %H:%M") if hasattr(created, "strftime") else str(created or "")
        lay.addWidget(QLabel("Создан: %s" % ds))
        lay.addWidget(QLabel("Тип: %s" % (row.get("order_kind") or "—")))
        if row.get("client_id"):
            b = QPushButton("Открыть карточку клиента")
            b.clicked.connect(lambda: self._open_client(int(row.get("client_id")), str(row.get("client_name") or "")))
            lay.addWidget(b)

        scr = QScrollArea()
        scr.setWidgetResizable(True)
        host = QWidget()
        v = QVBoxLayout(host)
        items = db_models.get_order_items(self._order_id) or []
        if not items:
            v.addWidget(QLabel("Позиции не найдены."))
        for it in items:
            v.addWidget(
                QLabel(
                    "• %s | %sx%s | qty=%s | %s"
                    % (
                        it.get("material_name") or "—",
                        int(it.get("height_mm") or 0),
                        int(it.get("width_mm") or 0),
                        int(it.get("quantity") or 1),
                        it.get("recipient_text") or "",
                    )
                )
            )
        v.addStretch()
        scr.setWidget(host)
        lay.addWidget(scr, 1)

    def _open_client(self, cid, cname):
        from ui.clients_dialog import ClientCardDialog
        d = ClientCardDialog(cid, cname, self)
        d.exec_()
