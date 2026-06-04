# -*- coding: utf-8 -*-
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QComboBox, QGridLayout, QLabel, QPushButton, QSizePolicy, QWidget

from elements.calc_tile_style import (
    apply_service_tile_frame,
    style_cost_label,
    style_orange_action_button,
    style_tile_header,
)
from elements.frame_slider import SliderFrame
from elements.shape_edge_preview import ShapeEdgePreview


class FrameFacet(QWidget):
    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)

        layout_r = QGridLayout(self)
        layout_r.setContentsMargins(4, 2, 4, 4)
        layout_r.setSpacing(0)

        self.label = QLabel("ФАЦЕТ")
        style_tile_header(self.label)
        layout_r.addWidget(self.label, 0, 0, 1, 4)

        self._preview = ShapeEdgePreview(self)
        layout_r.addWidget(self._preview, 1, 0, 1, 4, Qt.AlignHCenter | Qt.AlignTop)

        gap = QWidget()
        gap.setFixedHeight(10)
        gap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        gap.setStyleSheet("background: transparent; border: none;")
        layout_r.addWidget(gap, 2, 0, 1, 4)

        self.slider = SliderFrame()
        self.slider.setMaximumHeight(64)
        self.slider.setMaximumWidth(36)
        layout_r.addWidget(self.slider, 4, 0, 4, 1)

        self.left = QComboBox()
        self.right = QComboBox()
        self.top = QComboBox()
        self.bot = QComboBox()
        for c in (self.left, self.right, self.top, self.bot):
            c.addItems(["--", "5", "10", "15", "20", "25", "30", "35", "40", "45", "50"])
        self.button = QPushButton("НА ВСЕ", self)
        style_orange_action_button(self.button)
        self.button.clicked.connect(self.btn_click)

        layout_r.addWidget(self.top, 3, 2, Qt.AlignCenter)
        layout_r.addWidget(self.right, 4, 1, Qt.AlignCenter)
        layout_r.addWidget(self.button, 4, 2, Qt.AlignCenter)
        layout_r.addWidget(self.left, 4, 3, Qt.AlignCenter)
        layout_r.addWidget(self.bot, 5, 2, 1, 1)

        self.cost_label = QLabel("—")
        self.cost_label.setWordWrap(True)
        style_cost_label(self.cost_label)
        layout_r.addWidget(self.cost_label, 8, 0, 1, 4)

        self._facet_widgets = (
            self.slider,
            self.left,
            self.right,
            self.top,
            self.bot,
            self.button,
        )

    def sync_from_izd(self, izd: dict):
        self._preview.set_izd(izd)

    def set_facet_active(self, active: bool):
        for w in self._facet_widgets:
            w.setEnabled(active)
        self.cost_label.setEnabled(active)

    @staticmethod
    def _mm(combo) -> int:
        t = combo.currentText()
        if t == "--":
            return 0
        try:
            return int(t)
        except ValueError:
            return 0

    def btn_click(self):
        slider_value = str(self.slider.get_value())
        for combo in [self.top, self.left, self.right, self.bot]:
            combo.setCurrentText(str(5))
        for combo in [self.top, self.left, self.right, self.bot]:
            combo.setCurrentText(str(slider_value))

    def facet_needed(self) -> bool:
        return any(self._mm(c) > 0 for c in (self.top, self.left, self.right, self.bot))

    def get_facet_edge_mm_by_side(self, shape: str) -> dict:
        """Мм фацета по сторонам (как ключи шлифовки: Верх/Низ/Лево/Право)."""
        t, b, l, r = self._mm(self.top), self._mm(self.bot), self._mm(self.left), self._mm(self.right)
        if shape in ("Прямоугольник", "Трапеция"):
            return {"Верх": t, "Низ": b, "Лево": l, "Право": r}
        if shape == "Треугольник":
            return {"Верх": t, "Низ": b, "Лево": l, "Право": r}
        return {}

    def get_facet_state(self, shape: str) -> dict:
        if shape in ("Прямоугольник", "Трапеция"):
            return {
                "Нужен": self.facet_needed(),
                "Top": self._mm(self.top),
                "Bottom": self._mm(self.bot),
                "Left": self._mm(self.left),
                "Right": self._mm(self.right),
            }
        if shape == "Треугольник":
            topv = self._mm(self.top)
            if topv <= 0 and self.facet_needed():
                topv = max(self._mm(self.left), self._mm(self.right), self._mm(self.bot))
            return {"Нужен": topv > 0, "Top": topv, "Bottom": 0, "Left": 0, "Right": 0}
        return {"Нужен": False, "Top": 0, "Bottom": 0, "Left": 0, "Right": 0}

    def reset_to_defaults(self):
        for c in (self.top, self.left, self.right, self.bot):
            c.blockSignals(True)
            c.setCurrentIndex(0)
            c.blockSignals(False)
        self.cost_label.setText("—")

    def set_cost_text(self, text: str):
        self.cost_label.setText(text)
