# -*- coding: utf-8 -*-
"""Модальное окно деталей заказа: позиции, сумма и (для заказов с раскроем) схема, PDF и этикетки."""
import sys
import os

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QMessageBox,
)
from PyQt5.QtCore import Qt

from db_main import order_status_to_ru
from cfg_loader import get_mirror_cut_root

# Импорт из MIRROR_CUT
from db import models as db_models
from ui._mirror_dialogs import _load_dialog


def _load_cutting_result_dialog_class():
    """Загрузить CuttingResultDialog из MIRROR_CUT/ui, не трогая остальные диалоги."""
    try:
        return _load_dialog('cutting_result_dialog', 'CuttingResultDialog')
    except Exception:
        return None


class OrderDetailDialog(QDialog):
    def __init__(self, order_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Заказ #%s" % order_id)
        self.setMinimumSize(520, 440)
        layout = QVBoxLayout(self)
        order = db_models.get_order(order_id)
        if not order:
            layout.addWidget(QLabel("Заказ не найден."))
            return
        self._order = order

        # Шапка: клиент + кнопка «Клиент» + статус
        # Пытаемся максимально надёжно получить имя клиента, как в MIRROR_CUT.
        client_name = str(order.get('client_name') or '').strip()
        client_id = order.get('client_id')
        # Если в заказе нет client_name, пробуем подтянуть его из справочника клиентов
        if not client_name:
            # 1) если есть client_id — пробуем справочник клиентов
            if client_id:
                try:
                    cl = db_models.get_client_by_id(client_id)
                except Exception:
                    cl = None
                if cl:
                    client_name = str(cl.get('name') or '').strip()
            # 2) если всё ещё пусто — используем вспомогательную функцию из models
            if not client_name:
                try:
                    client_name = (db_models.get_order_client_name(order_id) or '').strip()
                except Exception:
                    client_name = ''
        if not client_name:
            client_name = '—'
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("Клиент: %s" % client_name))
        if client_id:
            btn_client = QPushButton("Клиент")
            btn_client.clicked.connect(lambda: self._on_open_client(client_id, client_name))
            header_row.addWidget(btn_client)
        header_row.addStretch()
        header_row.addWidget(QLabel("Статус: %s" % order_status_to_ru(order.get('status'))))
        layout.addLayout(header_row)

        layout.addWidget(QLabel(""))
        layout.addWidget(QLabel("Позиции:"))
        self.list = QListWidget()
        items = db_models.get_order_items(order_id)
        for it in items:
            text = "%s — %d×%d мм, кол-во %s" % (
                str(it.get('material_name') or '—'),
                int(it.get('height_mm') or 0),
                int(it.get('width_mm') or 0),
                int(it.get('quantity') or 1),
            )
            if it.get('recipient_text'):
                text += " — %s" % str(it.get('recipient_text') or '')
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, it)
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(self._on_item_click)
        layout.addWidget(self.list)

        # Если по заказу есть результаты раскроя — показываем блок схемы / PDF / этикеток
        has_cut = False
        try:
            results = db_models.get_cut_results(order_id)
            has_cut = bool(results)
        except Exception:
            results = []
            has_cut = False

        if has_cut:
            btn_row = QHBoxLayout()
            btn_scheme = QPushButton("Схема раскроя")
            btn_scheme.clicked.connect(self._on_open_scheme)
            btn_row.addWidget(btn_scheme)
            btn_pdf = QPushButton("Карты раскроя (PDF)")
            btn_pdf.clicked.connect(self._on_open_pdf)
            btn_row.addWidget(btn_pdf)
            btn_labels = QPushButton("Этикетки (PDF)")
            btn_labels.clicked.connect(self._on_open_labels)
            btn_row.addWidget(btn_labels)
            btn_row.addStretch()
            layout.addLayout(btn_row)

        layout.addWidget(QLabel("Общая сумма: — (расчёт по типам заказов в разработке)"))
        self.setStyleSheet("QDialog { background-color: #E8F4FC; }")

    def _on_item_click(self, list_item):
        it = list_item.data(Qt.UserRole)
        if not it:
            return
        msg = "Позиция: %s\nРазмер: %d×%d мм\nКоличество: %s\nПолучатель: %s" % (
            str(it.get('material_name') or '—'),
            int(it.get('height_mm') or 0),
            int(it.get('width_mm') or 0),
            int(it.get('quantity') or 1),
            str(it.get('recipient_text') or '—'),
        )
        QMessageBox.information(self, "Позиция", msg)

    def _cut_layouts(self):
        """Список layout-словари для схемы раскроя (как в программе раскроя)."""
        try:
            results = db_models.get_cut_results(self._order.get('id'))
        except Exception:
            return []
        layouts = []
        for r in results or []:
            lay = r.get('layout')
            if isinstance(lay, dict):
                layouts.append(lay)
            elif lay:
                layouts.append(
                    {
                        'pieces': lay,
                        'waste_rects': [],
                        'business_rects': [],
                        'sheet_width': 0,
                        'sheet_height': 0,
                    }
                )
        return layouts

    def _on_open_scheme(self):
        layouts = self._cut_layouts()
        if not layouts:
            QMessageBox.information(self, "Схема", "Нет данных раскроя для этого заказа.")
            return
        # Открываем диалог схемы раскроя из MIRROR_CUT/ui
        CuttingResultDialog = _load_cutting_result_dialog_class()
        if CuttingResultDialog is None:
            QMessageBox.information(self, "Схема", "Диалог схемы раскроя не найден в MIRROR_CUT/ui.")
            return
        d = CuttingResultDialog(layouts, self._order, self)
        parent = self.parent()
        # Если родитель умеет PDF и этикетки — подключаем кнопки схемы к этим действиям
        if parent and hasattr(parent, "_open_pdf"):
            try:
                d.btn_pdf.clicked.connect(lambda: parent._open_pdf(self._order, layouts_getter=lambda: d.layouts))
            except Exception:
                pass
        if parent and hasattr(parent, "_open_label"):
            try:
                d.print_labels_requested.connect(lambda: parent._open_label(self._order))
            except Exception:
                pass
        d.exec_()

    def _on_open_pdf(self):
        parent = self.parent()
        if not parent:
            return
        layouts = self._cut_layouts()
        if not layouts and not hasattr(parent, "_open_pdf"):
            QMessageBox.information(self, "PDF", "Нет данных раскроя для экспорта.")
            return
        if hasattr(parent, "_open_pdf"):
            try:
                if layouts:
                    parent._open_pdf(self._order, layouts_getter=lambda: list(layouts))
                else:
                    parent._open_pdf(self._order)
            except Exception:
                pass

    def _on_open_labels(self):
        parent = self.parent()
        if not parent or not hasattr(parent, "_open_label"):
            return
        try:
            parent._open_label(self._order)
        except Exception:
            pass

    def _on_open_client(self, client_id, client_name):
        """Показать карточку клиента (ИП/юр. лицо и т.д.), как в WebQR."""
        try:
            from ui.clients_dialog import ClientCardDialog
        except Exception:
            QMessageBox.information(self, "Клиент", "Окно карточки клиента недоступно.")
            return
        d = ClientCardDialog(client_id, client_name, self)
        d.exec_()
