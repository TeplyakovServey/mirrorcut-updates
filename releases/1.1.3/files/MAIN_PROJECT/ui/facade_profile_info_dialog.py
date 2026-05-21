# -*- coding: utf-8 -*-
"""Модальное окно: информация о профиле на стороне — название, фото, стоимость за м, за сторону, общая по изделию."""
import sys
import os

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap

from ui.facade_profile_dialog import _fasad_img_path


SIDE_LABELS = {'top': 'Верх', 'bottom': 'Низ', 'left': 'Лево', 'right': 'Право'}


class FacadeProfileInfoDialog(QDialog):
    def __init__(self, profile, side, width_mm, height_mm, parent=None, delete_all_cuts_callback=None):
        super().__init__(parent)
        self.setWindowTitle("Профиль: %s" % (profile.get('name') or profile.get('series') or '—'))
        self.setMinimumSize(400, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Сторона: %s" % SIDE_LABELS.get(side, side)))
        layout.addWidget(QLabel("Название: %s" % (profile.get('name') or '—')))
        layout.addWidget(QLabel("Серия: %s" % (profile.get('series') or '—')))
        layout.addWidget(QLabel("Цвет: %s" % (profile.get('color') or '—')))
        price_m = float(profile.get('price_per_meter') or 0)
        length_m = (width_mm / 1000.0) if side in ('top', 'bottom') else (height_mm / 1000.0)
        cost_side = length_m * price_m
        layout.addWidget(QLabel("Стоимость за п.м.: %.2f ₽" % price_m))
        layout.addWidget(QLabel("Стоимость за эту сторону: %.2f ₽" % cost_side))
        path = _fasad_img_path(
            profile.get('photo_number'),
            series=profile.get('series'),
            name=profile.get('name'),
        )
        if path and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                lbl = QLabel()
                lbl.setPixmap(pix.scaled(340, 340, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                layout.addWidget(lbl, 0, Qt.AlignCenter)
        if delete_all_cuts_callback:
            btn_del = QPushButton("Удалить все вырезы на этой стороне")
            btn_del.setStyleSheet(
                "QPushButton { background-color: #c62828; color: white; padding: 6px 12px; border-radius: 4px; }"
            )

            def _run_del():
                delete_all_cuts_callback()
                self.accept()

            btn_del.clicked.connect(_run_del)
            layout.addWidget(btn_del)
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
