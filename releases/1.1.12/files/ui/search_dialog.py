"""Поиск: по клиенту (все заказы и статусы), по номеру (деловой остаток) или по K-номеру (продукт для клиента)."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QMessageBox, QGroupBox, QFrame,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from db import models


def _fmt_dt(dt):
    if dt is None:
        return "—"
    return dt.strftime("%d.%m.%Y %H:%M") if hasattr(dt, 'strftime') else str(dt)[:16]


class SearchDialog(QDialog):
    """Поиск по клиенту, по номеру остатка или по K-номеру продукта."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Поиск")
        self.setMinimumSize(500, 420)
        layout = QVBoxLayout(self)

        grp = QGroupBox("Клиент, номер остатка или K-номер (например K1)")
        grp_layout = QVBoxLayout(grp)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Введите имя клиента, число (100) или K1, K2…")
        self.search_edit.returnPressed.connect(self._do_search)
        grp_layout.addWidget(self.search_edit)
        btn_search = QPushButton("Искать")
        btn_search.clicked.connect(self._do_search)
        grp_layout.addWidget(btn_search)
        layout.addWidget(grp)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet("color: #333; font-size: 12px;")
        layout.addWidget(self.result_label)

        self.list = QListWidget()
        self.list.setVisible(False)
        self.list.itemDoubleClicked.connect(self._on_item_activated)
        layout.addWidget(self.list)

        self._last_orders = []
        self._last_mode = None  # 'client' | 'remnant' | 'k'

    def _do_search(self):
        text = (self.search_edit.text() or "").strip()
        self.list.clear()
        self.list.setVisible(False)
        self._last_orders = []
        self._last_mode = None
        if not text:
            self.result_label.setText("Введите запрос.")
            return

        # K-номер: K1, K2, k10
        if text.upper().startswith('K'):
            try:
                k_num = int(text[1:].strip())
            except ValueError:
                self.result_label.setText("Неверный K-номер. Пример: K1, K2.")
                return
            order = models.get_order_by_k_number(k_num)
            if not order:
                self.result_label.setText("Продукт K%s не найден." % k_num)
                return
            self._last_mode = 'k'
            self._last_orders = [order]
            summary = models.order_ready_summary(order['id'])
            client = order.get('client_name') or '—'
            from_sheets = ", ".join((summary.get('from_sheets') or [])[:5]) if summary else "—"
            self.result_label.setText(
                "K%s — для клиента: %s. Из листов: %s. Заказ #%s."
                % (k_num, client, from_sheets, order.get('id'))
            )
            self.list.addItem("Заказ #%s (%s) — двойной клик для подробностей" % (order.get('id'), client))
            self.list.setVisible(True)
            return

        # Только цифры — поиск по номеру этикетки (деловой остаток)
        if text.isdigit():
            label_num = int(text)
            rem = models.get_remnant_by_label_number(label_num)
            if not rem:
                self.result_label.setText("Остаток с номером %s не найден." % label_num)
                return
            self._last_mode = 'remnant'
            self.result_label.setText(
                "Остаток №%s: %s, %s×%s мм."
                % (label_num, rem.get('name'), rem.get('height_mm'), rem.get('width_mm'))
            )
            return

        # Иначе — поиск по клиенту
        orders = models.get_orders_by_client_name(text)
        self._last_mode = 'client'
        self._last_orders = orders
        if not orders:
            self.result_label.setText("Заказы по клиенту «%s» не найдены." % text)
            return
        self.result_label.setText("Найдено заказов: %d. Двойной клик — подробности." % len(orders))
        for o in orders:
            status = "Выполнен" if o.get('status') == 'completed' else "В работе"
            k_str = (" K%s" % o.get('k_number')) if o.get('k_number') else ""
            self.list.addItem("#%s — %s — %s%s" % (o.get('id'), (o.get('client_name') or '—')[:30], status, k_str))
        self.list.setVisible(True)

    def _on_item_activated(self, item):
        if self._last_mode == 'client' or self._last_mode == 'k':
            idx = self.list.currentRow()
            if 0 <= idx < len(self._last_orders):
                order = self._last_orders[idx]
                self._open_order_detail(order)

    def _open_order_detail(self, order_data=None, order_id=None):
        from ui.status_board import OrderDetailDialog
        order = order_data
        if not order and order_id:
            order = models.get_order(order_id) or models.get_order_by_k_number(order_id)
        if not order and order_id:
            for o in models.get_orders_all():
                if o.get('id') == order_id:
                    order = o
                    break
        if not order:
            order = {'id': order_id or 0, 'client_name': '—', 'status': 'in_progress', 'created_at': None, 'accepted_at': None, 'notes': None}
        d = OrderDetailDialog(order, self)
        d.exec_()
