# -*- coding: utf-8 -*-
"""Диалог подтверждения пользователей: список неподтверждённых, кнопки Подтвердить / Заблокировать."""
import sys
import os
_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QListWidget, QListWidgetItem,
    QMessageBox, QLabel,
)
from PyQt5.QtCore import Qt

from db_main import (
    get_all_users,
    set_approved,
    set_blocked,
    role_label_desktop,
    account_origin_ru,
    is_boss_protected_user_id,
)
from ui.boss_block_dialogs import show_boss_block_forbidden_sequence


class ConfirmUsersDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Подтверждение пользователей")
        self.setMinimumSize(450, 350)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Пользователи, ожидающие подтверждения:"))
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.SingleSelection)
        layout.addWidget(self.list)
        row = QHBoxLayout()
        btn_approve = QPushButton("Подтвердить")
        btn_approve.clicked.connect(self._approve)
        row.addWidget(btn_approve)
        btn_block = QPushButton("Заблокировать")
        btn_block.clicked.connect(self._block)
        row.addWidget(btn_block)
        row.addStretch()
        layout.addLayout(row)
        self._fill()

    def _fill(self):
        self.list.clear()
        self._users = [u for u in get_all_users() if not u.get('approved') and not u.get('blocked')]
        for u in self._users:
            blk = "заблокирован" if u.get("blocked") else "не заблокирован"
            text = "%s %s — %s · %s · %s · %s" % (
                str(u.get('name') or '').strip(),
                str(u.get('surname') or '').strip(),
                (u.get('login') or ''),
                role_label_desktop(u.get('role')),
                blk,
                account_origin_ru(u.get("account_origin")),
            )
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, u.get('id'))
            self.list.addItem(item)

    def _approve(self):
        row = self.list.currentRow()
        if row < 0:
            QMessageBox.information(self, "Подтверждение", "Выберите пользователя.")
            return
        uid = self.list.currentItem().data(Qt.UserRole)
        set_approved(uid, True)
        QMessageBox.information(self, "Подтверждение", "Пользователь подтверждён.")
        self._fill()

    def _block(self):
        row = self.list.currentRow()
        if row < 0:
            QMessageBox.information(self, "Блокировка", "Выберите пользователя.")
            return
        uid = self.list.currentItem().data(Qt.UserRole)
        if is_boss_protected_user_id(uid):
            show_boss_block_forbidden_sequence(self)
            return
        if not set_blocked(uid, True):
            QMessageBox.warning(self, "Блокировка", "Не удалось заблокировать пользователя.")
            return
        QMessageBox.information(self, "Блокировка", "Пользователь заблокирован.")
        self._fill()
