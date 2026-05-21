# -*- coding: utf-8 -*-
"""Выбор сторон фасада для назначения профиля: чекбоксы верх/низ/лево/право + «На все» + ОК."""
import sys
import os

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

import os

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QDialogButtonBox,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap

from ui.facade_profile_dialog import _fasad_img_path


class FacadeSidesDialog(QDialog):
    def __init__(self, profile, width_mm, height_mm, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.width_mm = width_mm
        self.height_mm = height_mm
        self.setWindowTitle("Стороны для профиля: %s" % (profile.get('name') or profile.get('series') or '—'))
        self.setMinimumSize(560, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Выберите стороны, на которые устанавливается этот профиль:"))

        info_row = QHBoxLayout()
        self.photo_lbl = QLabel()
        self.photo_lbl.setFixedSize(200, 200)
        self.photo_lbl.setAlignment(Qt.AlignCenter)
        self.photo_lbl.setStyleSheet("background:#f4f4f4; border:1px solid #ccc; border-radius:6px;")
        path = _fasad_img_path(
            profile.get('photo_number'),
            series=profile.get('series'),
            name=profile.get('name'),
        )
        if path and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                self.photo_lbl.setPixmap(pix.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                self.photo_lbl.setText("Нет изображения")
        else:
            self.photo_lbl.setText("Фото %s" % (profile.get('photo_number') or '—'))
        info_row.addWidget(self.photo_lbl)
        txt = QLabel(
            "<b>Серия:</b> %s<br/><b>Название:</b> %s<br/><b>Цвет:</b> %s<br/><b>Поставщик:</b> %s<br/><b>Цена за п.м.:</b> %s"
            % (
                profile.get('series') or '—',
                profile.get('name') or '—',
                profile.get('color') or '—',
                profile.get('supplier') or '—',
                profile.get('price_per_meter') or '—',
            )
        )
        txt.setWordWrap(True)
        txt.setTextFormat(Qt.RichText)
        txt.setStyleSheet("font-size:12px; padding:4px;")
        info_row.addWidget(txt, 1)
        layout.addLayout(info_row)

        # Расположение в виде квадрата:
        #      Верх
        # Лево  [На все]  Право
        #       Низ
        row_top = QHBoxLayout()
        row_mid = QHBoxLayout()
        row_bottom = QHBoxLayout()

        self.cb_top = QCheckBox("Верх")
        self.cb_top.setStyleSheet("font-size: 14px;")
        row_top.addStretch()
        row_top.addWidget(self.cb_top)
        row_top.addStretch()

        self.cb_left = QCheckBox("Лево")
        self.cb_left.setStyleSheet("font-size: 14px;")
        self.cb_right = QCheckBox("Право")
        self.cb_right.setStyleSheet("font-size: 14px;")
        self.btn_all = QPushButton("На все")
        self.btn_all.setMinimumSize(90, 40)
        self.btn_all.clicked.connect(self._on_toggle_all)

        row_mid.addStretch()
        row_mid.addWidget(self.cb_left)
        row_mid.addSpacing(20)
        row_mid.addWidget(self.btn_all)
        row_mid.addSpacing(20)
        row_mid.addWidget(self.cb_right)
        row_mid.addStretch()

        self.cb_bottom = QCheckBox("Низ")
        self.cb_bottom.setStyleSheet("font-size: 14px;")
        row_bottom.addStretch()
        row_bottom.addWidget(self.cb_bottom)
        row_bottom.addStretch()

        layout.addLayout(row_top)
        layout.addSpacing(8)
        layout.addLayout(row_mid)
        layout.addSpacing(8)
        layout.addLayout(row_bottom)
        layout.addWidget(QLabel(""))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def _on_toggle_all(self):
        any_checked = self.cb_top.isChecked() or self.cb_bottom.isChecked() or self.cb_left.isChecked() or self.cb_right.isChecked()
        v = not any_checked
        self.cb_top.setChecked(v)
        self.cb_bottom.setChecked(v)
        self.cb_left.setChecked(v)
        self.cb_right.setChecked(v)

    def get_sides(self):
        """Вернуть список выбранных сторон: 'top', 'bottom', 'left', 'right'."""
        out = []
        if self.cb_top.isChecked():
            out.append('top')
        if self.cb_bottom.isChecked():
            out.append('bottom')
        if self.cb_left.isChecked():
            out.append('left')
        if self.cb_right.isChecked():
            out.append('right')
        return out
