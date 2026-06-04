# -*- coding: utf-8 -*-
from elements.calc_tile_style import apply_service_tile_frame, style_cost_label, style_tile_header
from settings import *

from calc.db_postgres import fetch_plenka_options


class Plenka(QWidget):
    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        t = QLabel("ПЛЁНКА")
        style_tile_header(t)
        lay.addWidget(t)

        self.chk = QCheckBox("Нужна плёнка")
        self.chk.setFont(QFont("Arial", 8))
        lay.addWidget(self.chk)

        self.combo = QComboBox()
        self.combo.setFont(QFont("Arial", 8))
        self._opts = []  # (name, price)
        self._reload_options()
        lay.addWidget(self.combo)

        self.lbl_hint = QLabel(
            "Если суммарная площадь изделий с этой плёнкой < 0.5 м², "
            "стоимость доводится до 0.5 м² (как в Streamlit)."
        )
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setFont(QFont("Arial", 7))
        lay.addWidget(self.lbl_hint)

        self.cost_label = QLabel("—")
        self.cost_label.setWordWrap(True)
        style_cost_label(self.cost_label)
        lay.addWidget(self.cost_label)
        lay.addStretch()

        self.chk.toggled.connect(self._on_need)
        self._on_need(self.chk.isChecked())

    def _on_need(self, on: bool):
        self.combo.setEnabled(on)
        if not on:
            self.cost_label.setText("—")

    def _reload_options(self):
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("— выберите плёнку —", None)
        self._opts = fetch_plenka_options()
        for name, price in self._opts:
            self.combo.addItem("%s | %s ₽/м²" % (name, price), (name, price))
        self.combo.blockSignals(False)

    def current_choice(self):
        if not self.chk.isChecked():
            return None
        data = self.combo.currentData()
        return data

    def set_cost_text(self, text: str):
        self.cost_label.setText(text)

    def reset_to_defaults(self):
        self.chk.blockSignals(True)
        self.chk.setChecked(False)
        self.chk.blockSignals(False)
        self.combo.setCurrentIndex(0)
        self.cost_label.setText("—")
        self._on_need(False)

