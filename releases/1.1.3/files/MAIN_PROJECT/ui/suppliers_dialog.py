# -*- coding: utf-8 -*-
"""Диалог «Поставщики»: список, поиск по всем полям, создание и карточка."""
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
    QLineEdit,
    QAbstractItemView,
)
from PyQt5.QtCore import Qt

from db import models as db_models

SUPPLIER_TYPE_LABELS = {
    "legal": "Юр. лицо",
    "ip": "ИП",
    "individual": "Физ. лицо",
}


def _supplier_search_haystack(s):
    ctype = str(s.get("supplier_type") or "").strip().lower()
    parts = [
        str(s.get("name") or ""),
        str(s.get("phone") or ""),
        str(s.get("email") or ""),
        str(s.get("inn") or ""),
        str(s.get("kpp") or ""),
        str(s.get("okpo") or ""),
        str(s.get("ogrn") or ""),
        str(s.get("first_name") or ""),
        str(s.get("last_name") or ""),
        str(s.get("notes") or ""),
        str(s.get("source") or ""),
        str(s.get("legal_address") or ""),
        str(s.get("actual_address") or ""),
        SUPPLIER_TYPE_LABELS.get(ctype, ctype),
        str(s.get("id") or ""),
    ]
    return " ".join(parts).lower()


class SuppliersDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Поставщики")
        self.setMinimumSize(900, 500)
        layout = QVBoxLayout(self)
        try:
            self._all = db_models.get_all_suppliers() or []
        except Exception:
            self._all = []

        row = QHBoxLayout()
        row.addWidget(QLabel("Поиск:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("имя, ИНН, телефон, email, КПП, ОГРН…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        row.addWidget(self._search, 1)
        layout.addLayout(row)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Наименование", "Тип", "ИНН", "Телефон", "Email"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._open_card)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        btn_card = QPushButton("Карточка")
        btn_card.clicked.connect(self._open_card)
        btn_row.addWidget(btn_card)
        btn_row.addStretch()
        btn_create = QPushButton("Создать поставщика")
        btn_create.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; "
            "padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:hover { background-color: #388e3c; }"
        )
        btn_create.clicked.connect(self._create_supplier)
        btn_row.addWidget(btn_create)
        layout.addLayout(btn_row)

        self._visible = []
        self._apply_filter()

    def _remove_supplier_row(self, supplier_id: int):
        """Убрать поставщика из списка и таблицы без перезагрузки из БД."""
        sid = int(supplier_id)
        self._all = [s for s in self._all if int(s.get("id") or 0) != sid]
        vis_row = -1
        for i, s in enumerate(self._visible):
            if int(s.get("id") or 0) == sid:
                vis_row = i
                break
        if vis_row < 0:
            return
        self._visible.pop(vis_row)
        self.table.setUpdatesEnabled(False)
        try:
            self.table.removeRow(vis_row)
            n = self.table.rowCount()
            if n > 0:
                self.table.setCurrentCell(min(vis_row, n - 1), 0)
        finally:
            self.table.setUpdatesEnabled(True)

    def _apply_filter(self):
        q = (self._search.text() or "").strip().lower()
        tokens = [t for t in q.split() if t] if q else []
        if not tokens:
            self._visible = list(self._all)
        else:
            self._visible = []
            for s in self._all:
                hay = _supplier_search_haystack(s)
                q_digits = "".join(ch for ch in q if ch.isdigit())
                ok = all(t in hay for t in tokens)
                if not ok and len(q_digits) >= 3:
                    inn_d = "".join(ch for ch in str(s.get("inn") or "") if ch.isdigit())
                    ph_d = "".join(ch for ch in str(s.get("phone") or "") if ch.isdigit())
                    ok = (q_digits in inn_d) or (q_digits in ph_d)
                if ok:
                    self._visible.append(s)
        self.table.setRowCount(len(self._visible))
        for i, s in enumerate(self._visible):
            ctype = str(s.get("supplier_type") or "legal")
            self.table.setItem(i, 0, QTableWidgetItem(str(s.get("id") or "")))
            self.table.setItem(
                i, 1, QTableWidgetItem(db_models._supplier_display_name(s) or s.get("name") or "")
            )
            self.table.setItem(i, 2, QTableWidgetItem(SUPPLIER_TYPE_LABELS.get(ctype, ctype)))
            self.table.setItem(i, 3, QTableWidgetItem(str(s.get("inn") or "—")))
            self.table.setItem(i, 4, QTableWidgetItem(str(s.get("phone") or "—")))
            self.table.setItem(i, 5, QTableWidgetItem(str(s.get("email") or "—")))

    def _selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._visible):
            return None
        return self._visible[row]

    def _open_card(self, *_a):
        s = self._selected()
        if not s:
            QMessageBox.information(self, "Поставщик", "Выберите строку в таблице.")
            return
        from ui.supplier_card_dialog import SupplierCardDialog

        sid = int(s["id"])
        dlg = SupplierCardDialog(sid, db_models._supplier_display_name(s), self)
        dlg.exec_()
        if dlg.was_deleted():
            self._remove_supplier_row(sid)

    def _create_supplier(self):
        try:
            from ui._mirror_dialogs import _load_dialog

            NewClientDialog = _load_dialog("new_client_dialog", "NewClientDialog")
            if NewClientDialog is None:
                from ui.new_client_dialog import NewClientDialog
        except Exception as e:
            QMessageBox.warning(self, "Поставщик", "Не удалось открыть форму: %s" % e)
            return
        d = NewClientDialog(self, entity="supplier")
        if d.exec_() != QDialog.Accepted:
            return
        db_models.invalidate_suppliers_cache()
        self._all = db_models.get_all_suppliers() or []
        self._apply_filter()
