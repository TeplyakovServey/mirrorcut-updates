# -*- coding: utf-8 -*-
"""Модальное окно выбора типа заказа: кнопки типов и красный крестик."""
import sys
import os
_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QGridLayout,
    QMessageBox,
)
from PyQt5.QtCore import Qt

from cfg_loader import color

ORDER_TYPES = [
    ("СТЕКЛО / ЗЕРКАЛО", "glass"),
    ("ФАСАДЫ", "facades"),
    ("ПРОДАЖА", "sales"),
    ("ДВЕРИ", "doors"),
    ("ЗЕРКАЛО С ПОДСВЕТКОЙ", "mirror_light"),
    ("ГАРДЕРОБНЫЕ", "wardrobes"),
    ("ДУШЕВЫЕ КАБИНЫ", "shower"),
]


class NewOrderModal(QDialog):
    """По центру экрана: кнопки типов заказа и красный крестик закрытия."""

    def __init__(self, parent=None, *, dialog_title=None):
        super().__init__(parent)
        self.setWindowTitle(dialog_title or "Новый заказ")
        self._chosen_type = None
        self.setModal(True)
        layout = QVBoxLayout(self)
        cap = QLabel(dialog_title or "Выберите тип заказа")
        cap.setStyleSheet("font-weight: bold; font-size: 11pt; color: #1a365d;")
        top_row = QHBoxLayout()
        top_row.addWidget(cap)
        top_row.addStretch()
        close_btn = QPushButton("×")
        close_btn.setFixedSize(40, 40)
        close_btn.setStyleSheet("background-color: #DC3545; color: white; font-size: 24px; border: none; border-radius: 4px;")
        close_btn.clicked.connect(self.reject)
        top_row.addWidget(close_btn)
        layout.addLayout(top_row)
        # Сетка кнопок
        grid = QGridLayout()
        btn_style = """
            QPushButton { background-color: %s; color: %s; padding: 14px 20px; font-weight: bold; border: none; border-radius: 6px; }
            QPushButton:hover { background-color: %s; }
        """ % (color('button_bg'), color('button_text'), color('button_hover'))
        for i, (label, key) in enumerate(ORDER_TYPES):
            btn = QPushButton(label)
            btn.setStyleSheet(btn_style)
            btn.setMinimumWidth(220)
            btn.clicked.connect(lambda checked, k=key: self._on_type(k))
            if key == "sales":
                grid.addWidget(btn, 1, 0, 1, 2)
            else:
                pos_i = i if i < 2 else i - 1
                row_i = (pos_i // 2) + (1 if pos_i >= 2 else 0)
                col_i = pos_i % 2
                grid.addWidget(btn, row_i, col_i)
        layout.addLayout(grid)
        self.setStyleSheet("QDialog { background-color: #E8F4FC; }")

    def _on_type(self, order_type):
        if order_type == "shower":
            QMessageBox.information(
                self,
                "Душевые кабины",
                "Блок в разработке.",
            )
            return
        self._chosen_type = order_type
        self.accept()

    def chosen_type(self):
        return self._chosen_type
