# -*- coding: utf-8 -*-
"""Пробный раскрой: расчёт без сохранения в БД; кнопка «Добавить в базу» — ввод данных и сохранение заказа."""
import sys
import os
_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QFormLayout, QMessageBox,
)
from PyQt5.QtCore import Qt

from db import models as db_models


class AddToDbDialog(QDialog):
    """Ввод данных для сохранения заказа в базу после пробного расчёта."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить заказ в базу")
        self.setMinimumWidth(400)
        layout = QFormLayout(self)
        self.client_edit = QLineEdit()
        self.client_edit.setPlaceholderText("Имя клиента")
        layout.addRow("Клиент:", self.client_edit)
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("Примечание")
        layout.addRow("Примечание:", self.notes_edit)
        row = QHBoxLayout()
        btn_ok = QPushButton("Сохранить заказ")
        btn_ok.clicked.connect(self._save)
        row.addWidget(btn_ok)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(btn_cancel)
        layout.addRow(row)
        self._order_id = None

    def _save(self):
        name = self.client_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Сохранение", "Введите имя клиента.")
            return
        try:
            self._order_id = db_models.create_order(client_name=name, notes=self.notes_edit.text().strip() or None)
            QMessageBox.information(self, "Сохранение", "Заказ #%s создан и добавлен в базу." % self._order_id)
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить заказ: %s" % e)

    def order_id(self):
        return self._order_id


class TrialCutDialog(QDialog):
    """Пробный раскрой: информация и кнопка «Добавить в базу»."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Пробный раскрой")
        self.setMinimumSize(450, 280)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Пробный раскрой позволяет выполнить расчёт без сохранения в базу данных.\n\n"
            "После расчёта вы можете нажать «Добавить в базу», ввести данные клиента и сохранить заказ.\n\n"
            "Полный сценарий расчёта раскроя (выбор листа, упаковка, варианты) будет подключён к этой кнопке."
        ))
        layout.addStretch()
        btn_add = QPushButton("Добавить в базу (создать заказ)")
        btn_add.clicked.connect(self._add_to_db)
        layout.addWidget(btn_add)
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.reject)
        layout.addWidget(btn_close)
        self.setStyleSheet("QDialog { background-color: #E8F4FC; }")

    def _add_to_db(self):
        d = AddToDbDialog(self)
        if d.exec_() == QDialog.Accepted:
            self.accept()
