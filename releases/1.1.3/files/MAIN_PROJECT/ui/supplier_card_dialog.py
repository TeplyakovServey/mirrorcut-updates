# -*- coding: utf-8 -*-
"""Карточка поставщика: реквизиты, поставки; правка/удаление — только админ."""
from __future__ import annotations

import os
import sys

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QLabel,
    QFormLayout,
    QGroupBox,
    QAbstractItemView,
)
from PyQt5.QtCore import Qt

from db import models as db_models
from db_main import ROLE_ADMIN
from ui.suppliers_dialog import SUPPLIER_TYPE_LABELS
from ui.hold_delete_button import HoldDeleteButtonLTR


def _str(v):
    return (str(v).strip() if v is not None else "") or "—"


def _fmt_date(v):
    if v is None:
        return "—"
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%d.%m.%Y")
        except Exception:
            pass
    return str(v).strip() or "—"


def _resolve_is_admin(parent) -> bool:
    p = parent
    seen = set()
    while p is not None:
        pid = id(p)
        if pid in seen:
            break
        seen.add(pid)
        u = getattr(p, "_user", None)
        if isinstance(u, dict) and str(u.get("role") or "").strip() == ROLE_ADMIN:
            return True
        try:
            p = p.parent()
        except Exception:
            break
    return False


def open_supplier_card(supplier_id, parent=None):
    """Открыть карточку; возвращает True, если поставщик удалён."""
    row = db_models.get_supplier_by_id(int(supplier_id)) if supplier_id else None
    name = db_models._supplier_display_name(row) if row else "—"
    dlg = SupplierCardDialog(int(supplier_id), name, parent)
    dlg.exec_()
    return dlg.was_deleted()


class SupplierCardDialog(QDialog):
    def __init__(self, supplier_id, supplier_name, parent=None):
        super().__init__(parent)
        self._supplier_id = int(supplier_id)
        self._deleted = False
        self._is_admin = _resolve_is_admin(parent)
        self.setWindowTitle("Поставщик: %s" % (supplier_name or "—"))
        self.setMinimumSize(720, 520)
        layout = QVBoxLayout(self)

        supplier = db_models.get_supplier_by_id(self._supplier_id)
        self._supplier = dict(supplier) if supplier else None

        if self._supplier:
            grp = QGroupBox("Данные поставщика")
            form = QFormLayout()
            ctype = self._supplier.get("supplier_type") or "legal"
            form.addRow("Тип:", QLabel(SUPPLIER_TYPE_LABELS.get(ctype, ctype)))
            form.addRow("Наименование:", QLabel(_str(self._supplier.get("name"))))
            form.addRow("ИНН:", QLabel(_str(self._supplier.get("inn"))))
            form.addRow("КПП:", QLabel(_str(self._supplier.get("kpp"))))
            form.addRow("Телефон:", QLabel(_str(self._supplier.get("phone"))))
            form.addRow("Email:", QLabel(_str(self._supplier.get("email"))))
            grp.setLayout(form)
            layout.addWidget(grp)
            row_btn = QHBoxLayout()
            btn_stat = QPushButton("Статистика")
            btn_stat.clicked.connect(self._stats)
            row_btn.addWidget(btn_stat)
            if self._is_admin:
                btn_edit = QPushButton("Изменить")
                btn_edit.setStyleSheet(
                    "QPushButton { background:#1565c0; color:#fff; font-weight:600; "
                    "padding:6px 14px; border-radius:6px; }"
                )
                btn_edit.clicked.connect(self._edit)
                row_btn.addWidget(btn_edit)
                btn_del = HoldDeleteButtonLTR("Удалить поставщика", hold_ms=1000)
                btn_del.setToolTip("Удерживайте 1 секунду для удаления")
                btn_del.holdComplete.connect(self._delete_supplier)
                row_btn.addWidget(btn_del)
            row_btn.addStretch()
            layout.addLayout(row_btn)
        else:
            layout.addWidget(QLabel("Поставщик не найден."))

        layout.addWidget(QLabel("Последние поставки (целые листы):"))
        deliveries = db_models.get_supplier_deliveries(self._supplier_id, limit=30)
        if not deliveries:
            layout.addWidget(QLabel("Нет записей о поставках."))
        else:
            tbl = QTableWidget()
            tbl.setColumnCount(7)
            tbl.setHorizontalHeaderLabels(
                ["Дата", "Материал", "Размер", "мм", "Кол-во", "Сумма ₽", "Накладная"]
            )
            tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tbl.setRowCount(len(deliveries))
            for i, d in enumerate(deliveries):
                tbl.setItem(i, 0, QTableWidgetItem(_fmt_date(d.get("arrival_date"))))
                tbl.setItem(i, 1, QTableWidgetItem(_str(d.get("name"))))
                w, h = int(d.get("width_mm") or 0), int(d.get("height_mm") or 0)
                tbl.setItem(i, 2, QTableWidgetItem("%d × %d" % (w, h)))
                tbl.setItem(i, 3, QTableWidgetItem(str(d.get("thickness_mm") or "—")))
                tbl.setItem(i, 4, QTableWidgetItem(str(d.get("quantity") or 0)))
                cost = float(d.get("cost") or 0)
                qty = int(d.get("quantity") or 1)
                tbl.setItem(i, 5, QTableWidgetItem("%.2f" % (cost * qty)))
                tbl.setItem(i, 6, QTableWidgetItem(_str(d.get("warehouse_number"))))
            layout.addWidget(tbl)

    def _edit(self):
        if not self._is_admin:
            return
        try:
            from ui._mirror_dialogs import _load_dialog

            NewClientDialog = _load_dialog("new_client_dialog", "NewClientDialog")
            if NewClientDialog is None:
                from ui.new_client_dialog import NewClientDialog
        except Exception as e:
            QMessageBox.warning(self, "Поставщик", "Не удалось открыть форму: %s" % e)
            return
        d = NewClientDialog(self, entity="supplier", edit_supplier=self._supplier)
        if d.exec_() == QDialog.Accepted:
            self._supplier = db_models.get_supplier_by_id(self._supplier_id)
            QMessageBox.information(self, "Сохранено", "Данные поставщика обновлены.")

    def _delete_supplier(self):
        if not self._is_admin:
            return
        name = db_models._supplier_display_name(self._supplier) or "—"
        r = QMessageBox.question(
            self,
            "Удаление",
            "Удалить поставщика «%s»?\n\nСвязь с листами на складе будет снята (текст поставщика в строках сохранится)."
            % name,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if r != QMessageBox.Yes:
            return
        try:
            db_models.delete_supplier(self._supplier_id)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", "Не удалось удалить: %s" % e)
            return
        QMessageBox.information(self, "Удалено", "Поставщик удалён.")
        self._deleted = True
        self.accept()

    def was_deleted(self):
        return bool(self._deleted)

    def _stats(self):
        from ui.supplier_statistics_dialog import SupplierStatisticsDialog

        SupplierStatisticsDialog(
            self._supplier_id,
            db_models._supplier_display_name(self._supplier),
            self,
        ).exec_()
