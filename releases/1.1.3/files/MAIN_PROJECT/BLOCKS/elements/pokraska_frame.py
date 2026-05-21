# -*- coding: utf-8 -*-
from elements.calc_tile_style import apply_service_tile_frame, style_cost_label, style_tile_header
from settings import *

from calc.db_postgres import fetch_pokraska_options


class Pokraska(QWidget):
    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        t = QLabel("ПОКРАСКА")
        style_tile_header(t)
        lay.addWidget(t)

        self.chk = QCheckBox("Нужна покраска")
        self.chk.setFont(QFont("Arial", 8))
        lay.addWidget(self.chk)

        self.combo = QComboBox()
        self.combo.setFont(QFont("Arial", 8))
        self.combo.addItem("— выберите цвет —", None)
        for name, price in fetch_pokraska_options():
            self.combo.addItem("%s | %s ₽/м²" % (name, price), (name, price))
        lay.addWidget(self.combo)

        self.cost_label = QLabel("—")
        self.cost_label.setWordWrap(True)
        style_cost_label(self.cost_label)
        lay.addWidget(self.cost_label)
        lay.addStretch()

    def current_choice(self):
        if not self.chk.isChecked():
            return None
        return self.combo.currentData()

    def set_cost_text(self, text: str):
        self.cost_label.setText(text)

    def reset_to_defaults(self):
        self.chk.blockSignals(True)
        self.chk.setChecked(False)
        self.chk.blockSignals(False)
        self.combo.setCurrentIndex(0)
        self.cost_label.setText("—")
