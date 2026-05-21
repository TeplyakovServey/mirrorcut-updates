# -*- coding: utf-8 -*-
"""Вертикальный слайдер мм фаски: только текущее значение и ползунок в одной линии."""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget

from calc import palette as P


class SliderFrame(QWidget):
    def __init__(self):
        super().__init__()
        self.values = ["--", " 5", 10, 15, 20, 25, 30, 35, 40, 45, 50]
        self._build_ui()

    def _build_ui(self):
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setFont(QFont("Arial", 8, QFont.Bold))
        self.label.setFixedWidth(22)
        self.label.setStyleSheet(
            "color: %s; background: transparent; padding: 0;" % P.TILE_TEXT
        )

        self.slider = QSlider(Qt.Vertical)
        self.slider.setRange(0, len(self.values) - 1)
        self.slider.setValue(0)
        self.slider.setMinimumHeight(36)
        self.slider.setMinimumWidth(20)
        self.slider.valueChanged.connect(self.updateLabel)

        g, h = P.FACET_SLIDER_GROOVE, P.FACET_SLIDER_HANDLE
        self.slider.setStyleSheet(
            """
            QSlider:vertical {
                padding-top: 11px;
                padding-left: -5px;
                width: 20px;
            }
            QSlider::groove:vertical {
                background: %s;
                width: 20px;
                border: none;
            }
            QSlider::handle:vertical {
                background: %s;
                width: 20px;
                margin: -5px 0;
                border: none;
            }
            """
            % (g, h)
        )

        row.addWidget(self.label, 0, Qt.AlignVCenter)
        row.addWidget(self.slider, 0, Qt.AlignVCenter)

        self.setMinimumWidth(48)
        self.updateLabel(0)

    def get_value(self):
        return self.values[self.slider.value()]

    def updateLabel(self, value):
        v = self.values[value]
        self.label.setText("—" if v == "--" else str(v).strip())

    def wheelEvent(self, event):
        if self.rect().contains(event.pos()):
            if event.angleDelta().y() > 0:
                self.slider.setValue(min(self.slider.value() + 1, self.slider.maximum()))
            else:
                self.slider.setValue(max(self.slider.value() - 1, self.slider.minimum()))
            event.accept()
            return
        super().wheelEvent(event)
