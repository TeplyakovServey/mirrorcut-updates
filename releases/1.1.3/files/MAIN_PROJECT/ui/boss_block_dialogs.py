# -*- coding: utf-8 -*-
"""Два шага предупреждения при попытке заблокировать защищённого пользователя boss (без крестика закрытия)."""
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
from PyQt5.QtCore import Qt


def _solo_button_modal(parent, title, message, button_label):
    d = QDialog(parent)
    d.setWindowTitle(title)
    d.setWindowFlags(
        (Qt.Dialog | Qt.WindowTitleHint | Qt.CustomizeWindowHint)
        & ~Qt.WindowCloseButtonHint
        & ~Qt.WindowSystemMenuHint
    )
    lay = QVBoxLayout(d)
    lbl = QLabel(message)
    lbl.setWordWrap(True)
    lay.addWidget(lbl)
    btn = QPushButton(button_label)
    btn.setDefault(True)
    btn.clicked.connect(d.accept)
    lay.addWidget(btn)
    d.exec_()


def show_boss_block_forbidden_sequence(parent=None):
    _solo_button_modal(
        parent,
        "Блокировка",
        "Диму нельзя блокировать, он кого хочешь сам блокирует.",
        "Я всё понял",
    )
    _solo_button_modal(
        parent,
        "Блокировка",
        "Точно понял?",
        "Да господин",
    )
