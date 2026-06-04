# -*- coding: utf-8 -*-
"""Упаковка: чекбоксы как в Streamlit block_packaging."""
from __future__ import annotations

from typing import Dict

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from elements.calc_tile_style import apply_service_tile_frame, style_cost_label, style_tile_header


class PackagingTile(QWidget):
    """Флаги для calc.pricing_packaging.compute_packaging_block."""

    packagingChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        apply_service_tile_frame(self)
        self.setFixedSize(200, 200)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        t = QLabel("УПАКОВКА")
        style_tile_header(t)
        lay.addWidget(t)

        lf = QFont("Arial", 8, QFont.Bold)
        self.cb_stretch = QCheckBox("Стрейч-плёнка")
        self.cb_bubble = QCheckBox("Пузырчатая плёнка")
        self.cb_card = QCheckBox("Картон")
        self.cb_corners = QCheckBox("Пластиковые уголки")
        for cb in (self.cb_stretch, self.cb_bubble, self.cb_card, self.cb_corners):
            cb.setFont(lf)
            lay.addWidget(cb)
            cb.toggled.connect(self._emit)

        self.lbl = QLabel("—")
        self.lbl.setWordWrap(True)
        style_cost_label(self.lbl)
        lay.addWidget(self.lbl)
        lay.addStretch(1)

    def _emit(self, *_):
        self.packagingChanged.emit()

    def flags(self) -> Dict[str, bool]:
        return {
            "stretch_film": self.cb_stretch.isChecked(),
            "bubble_wrap": self.cb_bubble.isChecked(),
            "cardboard": self.cb_card.isChecked(),
            "plastic_corners": self.cb_corners.isChecked(),
        }

    def set_cost_summary(self, text: str) -> None:
        self.lbl.setText(text if (text or "").strip() else "—")

    def reset_to_defaults(self) -> None:
        for cb in (self.cb_stretch, self.cb_bubble, self.cb_card, self.cb_corners):
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self.lbl.setText("—")

    def is_any_selected(self) -> bool:
        return any(self.flags().values())
