# -*- coding: utf-8 -*-
"""Вырезы: сложность (простой / средний / сложный), количество, цены из БД."""
from __future__ import annotations

import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QIntValidator
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from calc.db_postgres import fetch_virez_price_table
from elements.calc_tile_style import apply_service_tile_frame, style_cost_label

_VIREZ_BTN_QSS = (
    "background-color: #ddab22; color: black; font-weight: bold; font-size: 17px;"
)


class Virez_Fame(QWidget):
    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 3, 4, 4)
        root.setSpacing(2)

        self.frame1 = QFrame()
        self.frame1.setFrameShape(QFrame.NoFrame)
        self.frame1.setStyleSheet(
            "background-color: #dadada; color: black; font-weight: bold; font-size: 11px; border: none;"
        )
        lay = QVBoxLayout(self.frame1)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel("ВЫРЕЗЫ")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setFont(QFont("Arial", 12, QFont.Bold))
        lay.addWidget(self.label)
        root.addWidget(self.frame1)

        self.items_list = QListWidget()
        self.items_list.setMaximumHeight(38)
        self.items_list.setMinimumHeight(32)
        self.items_list.setFrameShape(QFrame.NoFrame)
        self.items_list.setFont(QFont("Arial", 9, QFont.Bold))
        self.items_list.setStyleSheet(
            "QListWidget { border: none; background: #dadada; color: #111; font-size: 9pt; }"
            "QListWidget::item { padding: 1px 2px; min-height: 14px; }"
            "QListWidget::item:selected { background: #b0c4d4; color: #000; }"
        )
        self.items_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.items_list.itemSelectionChanged.connect(self._sync_delete_button)
        self.items_list.installEventFilter(self)
        root.addWidget(self.items_list)

        self.cost_label = QLabel("—")
        self.cost_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.cost_label.setWordWrap(True)
        self.cost_label.setMaximumHeight(22)
        style_cost_label(self.cost_label)
        cf = self.cost_label.font()
        cf.setPointSize(8)
        self.cost_label.setFont(cf)
        root.addWidget(self.cost_label)

        f8 = QFont("Arial", 8, QFont.Bold)
        fb = QFont("Arial", 9, QFont.Bold)

        self.lbl_row = QLabel("Категория / кол-во")
        self.lbl_row.setFont(f8)
        root.addWidget(self.lbl_row)

        row_mid = QHBoxLayout()
        row_mid.setSpacing(3)
        self.category = QComboBox()
        self.category.setMinimumHeight(18)
        self.category.setFont(fb)
        self._fill_category_combo()

        self.amount = QLineEdit()
        self.amount.setFixedWidth(36)
        self.amount.setValidator(QIntValidator(1, 99999, self))
        self.amount.setFont(fb)

        self.add_button = QPushButton("+")
        self.add_button.setFixedWidth(28)
        self.add_button.setMinimumHeight(18)
        self.add_button.setStyleSheet(_VIREZ_BTN_QSS)
        self.add_button.clicked.connect(self.add_item)

        row_mid.addWidget(self.category, 3)
        row_mid.addWidget(self.amount, 0)
        row_mid.addWidget(self.add_button, 0)
        root.addLayout(row_mid)

        root.addStretch(1)

        self.btn_delete = QPushButton("Удалить")
        self.btn_delete.setObjectName("virezDel")
        self.btn_delete.setMinimumHeight(20)
        self.btn_delete.setFont(QFont("Arial", 8, QFont.Bold))
        self.btn_delete.setEnabled(False)
        self.btn_delete.setStyleSheet(
            "QPushButton#virezDel:enabled { background-color: #c62828; color: #fff; border-radius: 3px; }"
            "QPushButton#virezDel:disabled { background-color: #bdbdbd; color: #616161; border-radius: 3px; }"
        )
        self.btn_delete.clicked.connect(self.delete_selected_row)
        root.addWidget(self.btn_delete)

        self.items = []

    def _fill_category_combo(self) -> None:
        self.category.blockSignals(True)
        self.category.clear()
        for r in fetch_virez_price_table():
            code = r.get("category_code") or ""
            title = r.get("title_ru") or code
            self.category.addItem(title, code)
        self.category.blockSignals(False)
        if self.category.count() == 0:
            for r in (
                ("Простой", "simple"),
                ("Средний", "medium"),
                ("Сложный", "complex"),
            ):
                self.category.addItem(r[0], r[1])

    def set_cost_summary(self, text: str) -> None:
        self.cost_label.setText(text if (text or "").strip() else "—")

    def rebuild_list_texts(self, price_by_code: dict, title_by_code: dict) -> None:
        for i, it in enumerate(self.items):
            if i >= self.items_list.count():
                break
            code = self._item_code(it)
            qty = self._item_qty(it)
            title = title_by_code.get(code) or str(it.get("Категория") or code)
            pu = int(price_by_code.get(code, 0) or 0)
            rub = pu * qty
            self.items_list.item(i).setText("%s × %s — %s ₽" % (title, qty, rub))

    @staticmethod
    def _item_code(it: dict) -> str:
        c = (it.get("category_code") or "").strip()
        if c:
            return c
        leg = str(it.get("Категория") or "").strip()
        if leg == "4":
            return "simple"
        if leg == "6":
            return "medium"
        if leg == "10":
            return "complex"
        low = leg.lower()
        if "прост" in low:
            return "simple"
        if "сред" in low:
            return "medium"
        if "слож" in low:
            return "complex"
        return "simple"

    @staticmethod
    def _item_qty(it: dict) -> int:
        v = it.get("Кол-во", it.get("Количество", 0))
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return 0

    def reset_to_defaults(self) -> None:
        self.items_list.clear()
        self.items.clear()
        self.amount.clear()
        self.cost_label.setText("—")
        self._fill_category_combo()
        self.category.setCurrentIndex(0)
        self._sync_delete_button()

    def _sync_delete_button(self) -> None:
        has = self.items_list.currentRow() >= 0 and bool(self.items_list.selectedItems())
        self.btn_delete.setEnabled(has)

    def delete_selected_row(self) -> None:
        row = self.items_list.currentRow()
        if row < 0 or row >= len(self.items):
            return
        self.items_list.takeItem(row)
        del self.items[row]
        self._sync_delete_button()

    def eventFilter(self, obj, event):
        if (
            obj == self.items_list
            and event.type() == event.KeyPress
            and event.key() == Qt.Key_Delete
        ):
            self.delete_selected_row()
        return super().eventFilter(obj, event)

    def add_item(self):
        amt_s = (self.amount.text() or "").strip()
        if not amt_s:
            QMessageBox.warning(self, "Вырезы", "Введите количество.")
            return
        try:
            qty = int(amt_s)
        except ValueError:
            return
        if qty <= 0:
            QMessageBox.warning(self, "Вырезы", "Количество должно быть больше нуля.")
            return
        idx = self.category.currentIndex()
        code = self.category.itemData(idx)
        if code is None:
            code = "simple"
        else:
            code = str(code)
        title = (self.category.currentText() or "").strip() or code
        self.items_list.addItem(QListWidgetItem("%s × %s — …" % (title, qty)))
        self.items.append(
            {
                "category_code": code,
                "Категория": title,
                "Кол-во": qty,
            }
        )
        self.amount.clear()
        self._sync_delete_button()

    def get_info(self):
        return self.items

    def load_from_saved(self, vz) -> None:
        """Восстановить строки из сохранённого блока «Вырезы» (после загрузки JSON)."""
        self.reset_to_defaults()
        if not isinstance(vz, dict) or vz.get("Пусто"):
            return
        rows = vz.get("Строки")
        if not isinstance(rows, list) or not rows:
            return
        for it in rows:
            if not isinstance(it, dict):
                continue
            code = self._item_code(it)
            qty = self._item_qty(it)
            if qty <= 0:
                continue
            title = (it.get("Категория") or "").strip() or code
            self.items_list.addItem(QListWidgetItem("%s × %s — …" % (title, qty)))
            self.items.append(
                {
                    "category_code": code,
                    "Категория": title,
                    "Кол-во": qty,
                }
            )
        self._sync_delete_button()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = Virez_Fame()
    w.show()
    sys.exit(app.exec_())
