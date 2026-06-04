# -*- coding: utf-8 -*-
"""Выбор изделий заказа для раскроя по материалу (dbl-click по строке заказа)."""
from __future__ import annotations

from typing import Dict, List, Set

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QCloseEvent
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QCheckBox,
    QWidget,
    QHeaderView,
    QAbstractItemView,
)

from window_branding import apply_fraction_window_geometry, apply_window_icon


class CutOrderProductsPickerDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        order_id: int,
        client_name: str = "",
        rows: List[Dict] | None = None,
        checked_product_ids: Set[str] | None = None,
    ):
        super().__init__(parent)
        self._order_id = int(order_id)
        self._rows = list(rows or [])
        self._checked = set(checked_product_ids or set())
        self.setWindowTitle("Заказ №%s — выбор изделий" % self._order_id)
        apply_window_icon(self)
        apply_fraction_window_geometry(self, 0.6)

        lay = QVBoxLayout(self)
        sub = (client_name or "").strip() or "—"
        lay.addWidget(QLabel("Клиент: %s" % sub))

        self._tbl = QTableWidget(0, 4)
        self._tbl.setHorizontalHeaderLabels(["", "№ изд.", "Размер (мм)", "Экз."])
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self._tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        lay.addWidget(self._tbl, 1)

        by_product: Dict[str, List[Dict]] = {}
        for row in self._rows:
            pid = str(row.get("product_id") or "")
            if not pid:
                continue
            by_product.setdefault(pid, []).append(row)

        self._product_ids: List[str] = []
        ri = 0
        for pid in sorted(by_product.keys(), key=lambda p: int(by_product[p][0].get("product_index") or 0)):
            grp = by_product[pid]
            self._product_ids.append(pid)
            self._tbl.insertRow(ri)
            cb = QCheckBox()
            cb.setChecked(pid in self._checked)
            cw = QWidget()
            hl = QHBoxLayout(cw)
            hl.setContentsMargins(4, 0, 4, 0)
            hl.addWidget(cb)
            hl.setAlignment(Qt.AlignCenter)
            self._tbl.setCellWidget(ri, 0, cw)
            idx = int(grp[0].get("product_index") or 0)
            it_idx = QTableWidgetItem(str(idx))
            it_idx.setData(Qt.UserRole, pid)
            self._tbl.setItem(ri, 1, it_idx)
            self._tbl.setItem(ri, 2, QTableWidgetItem(str(grp[0].get("size_mm") or "—")))
            inst_total = int(grp[0].get("instance_total") or len(grp))
            self._tbl.setItem(ri, 3, QTableWidgetItem(str(inst_total)))
            ri += 1

        btn_row = QHBoxLayout()
        btn_all = QPushButton("Выбрать все")
        btn_all.clicked.connect(self._select_all)
        btn_none = QPushButton("Снять все")
        btn_none.clicked.connect(self._select_none)
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch()
        lay.addLayout(btn_row)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, lambda: apply_fraction_window_geometry(self, 0.6))

    def closeEvent(self, event: QCloseEvent) -> None:
        self.accept()
        super().closeEvent(event)

    def _select_all(self):
        for r in range(self._tbl.rowCount()):
            w = self._tbl.cellWidget(r, 0)
            if w:
                cb = w.findChild(QCheckBox)
                if cb:
                    cb.setChecked(True)

    def _select_none(self):
        for r in range(self._tbl.rowCount()):
            w = self._tbl.cellWidget(r, 0)
            if w:
                cb = w.findChild(QCheckBox)
                if cb:
                    cb.setChecked(False)

    def selected_product_ids(self) -> Set[str]:
        out: Set[str] = set()
        for r in range(self._tbl.rowCount()):
            w = self._tbl.cellWidget(r, 0)
            if not w:
                continue
            cb = w.findChild(QCheckBox)
            it = self._tbl.item(r, 1)
            if cb and cb.isChecked() and it:
                pid = it.data(Qt.UserRole)
                if pid:
                    out.add(str(pid))
        return out
