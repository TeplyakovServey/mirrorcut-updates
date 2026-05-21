# -*- coding: utf-8 -*-
"""Отдельный список заказов «Продажа»."""
import os
import sys

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QTableWidget, QTableWidgetItem, QMessageBox
)

from db import models as db_models


class SalesOrdersDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Продажи")
        self.resize(900, 520)
        self._rows = []
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.status_combo = QComboBox()
        self.status_combo.addItem("Все статусы", "")
        for st in db_models.SALES_STATUS_FLOW:
            self.status_combo.addItem(db_models.sales_status_to_ru(st), st)
        self.status_combo.currentIndexChanged.connect(self._load)
        top.addWidget(self.status_combo)
        btn_new = QPushButton("Новая продажа")
        btn_new.clicked.connect(self._new_sales)
        top.addWidget(btn_new)
        btn_open = QPushButton("Открыть")
        btn_open.clicked.connect(self._open_selected)
        top.addWidget(btn_open)
        top.addStretch()
        lay.addLayout(top)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ID", "Клиент", "Статус", "Сумма", "Дата"])
        self.table.doubleClicked.connect(self._open_selected)
        lay.addWidget(self.table, 1)
        self._load()

    def _load(self):
        st = self.status_combo.currentData()
        rows = list(db_models.list_sales_orders(status=st or None) or [])
        try:
            hide = db_models.sales_order_ids_in_draft_quick_estimates()
        except Exception:
            hide = set()
        self._rows = []
        for r in rows:
            try:
                rid = int(r.get("id") or 0)
            except (TypeError, ValueError):
                rid = 0
            if rid in hide:
                continue
            self._rows.append(r)
        self.table.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            self.table.setItem(i, 0, QTableWidgetItem(str(r.get("id") or "")))
            self.table.setItem(i, 1, QTableWidgetItem(str(r.get("client_name") or "—")))
            self.table.setItem(i, 2, QTableWidgetItem(db_models.sales_status_to_ru(r.get("status"))))
            self.table.setItem(i, 3, QTableWidgetItem("%s ₽" % int(r.get("total_rub") or 0)))
            created = r.get("created_at")
            dt = created.strftime("%d.%m.%Y %H:%M") if hasattr(created, "strftime") else str(created or "")
            self.table.setItem(i, 4, QTableWidgetItem(dt))

    def _selected_row(self):
        idx = self.table.currentRow()
        if idx < 0 or idx >= len(self._rows):
            return None
        return self._rows[idx]

    def _open_selected(self):
        row = self._selected_row()
        if not row:
            return
        from ui.sales_order_dialog import SalesOrderDialog
        d = SalesOrderDialog(self, sales_order_id=row.get("id"))
        d.exec_()
        self._load()

    def _new_sales(self):
        from ui.sales_order_dialog import SalesOrderDialog
        d = SalesOrderDialog(self)
        d.exec_()
        self._load()
