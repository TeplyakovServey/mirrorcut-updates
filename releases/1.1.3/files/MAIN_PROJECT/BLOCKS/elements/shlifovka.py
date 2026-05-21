# -*- coding: utf-8 -*-
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QCheckBox, QGridLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from elements.calc_tile_style import (
    apply_service_tile_frame,
    style_cost_label,
    style_orange_action_button,
    style_tile_header,
)
from elements.shape_edge_preview import ShapeEdgePreview


class FrameShlifovka(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ШЛИФОВКА")
        apply_service_tile_frame(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 4)
        layout.setSpacing(0)

        self.label = QLabel("ШЛИФОВКА", self)
        style_tile_header(self.label)
        self.status = False
        layout.addWidget(self.label)

        self._preview = ShapeEdgePreview(self)
        layout.addWidget(self._preview, alignment=Qt.AlignHCenter | Qt.AlignTop)
        layout.addSpacing(2)

        grid_layout = QGridLayout()
        grid_layout.setVerticalSpacing(0)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(grid_layout)
        self.button = QPushButton("НА ВСЕ", self)
        style_orange_action_button(self.button)
        self.button.clicked.connect(self.select_all_checkboxes)

        self.checkboxes = {
            1: QCheckBox("", self),
            2: QCheckBox("", self),
            3: QCheckBox("", self),
            4: QCheckBox("", self),
        }
        _cb_style = "font-size: 11px; font-weight: bold;"
        for cb in self.checkboxes.values():
            cb.setStyleSheet(_cb_style)

        grid_layout.addWidget(self.checkboxes[1], 0, 1, Qt.AlignCenter)
        grid_layout.addWidget(self.checkboxes[2], 1, 0, Qt.AlignCenter)
        grid_layout.addWidget(self.button, 1, 1, Qt.AlignCenter)
        grid_layout.addWidget(self.checkboxes[3], 1, 2, Qt.AlignCenter)
        grid_layout.addWidget(self.checkboxes[4], 2, 1, Qt.AlignCenter)

        self.cost_label = QLabel("—")
        self.cost_label.setWordWrap(True)
        style_cost_label(self.cost_label)
        layout.addWidget(self.cost_label)

        self._shape = "Прямоугольник"
        self._auto_facet4 = False

    def _grind_key_for_checkbox_index(self, idx: int) -> str:
        if self._shape in ("Круг", "Овал", "Сложная фигура"):
            return "Кромка"
        if self._shape == "Треугольник":
            return {1: "Верх", 2: "Лево", 3: "Право"}.get(idx, "")
        return {1: "Верх", 2: "Лево", 3: "Право", 4: "Низ"}.get(idx, "")

    def set_facet4_free_lock(self, sides: Optional[dict]):
        """
        None — обычный режим (пользователь редактирует чекбоксы).
        dict с ключами Верх/Низ/Лево/Право/Кромка — зафиксировать выбор (бесплатная шлифовка с фацетом 4 мм).
        """
        if sides is None:
            was_auto = self._auto_facet4
            self._auto_facet4 = False
            for cb in self.checkboxes.values():
                cb.setEnabled(True)
            self.button.setEnabled(True)
            if was_auto:
                for cb in self.checkboxes.values():
                    if cb.isVisible():
                        cb.blockSignals(True)
                        cb.setChecked(False)
                        cb.blockSignals(False)
            return
        self._auto_facet4 = True
        for i, cb in self.checkboxes.items():
            if not cb.isVisible():
                continue
            key = self._grind_key_for_checkbox_index(i)
            on = bool(sides.get(key)) if key else False
            cb.blockSignals(True)
            cb.setChecked(on)
            cb.blockSignals(False)
            cb.setEnabled(False)
        self.button.setEnabled(False)

    def sync_from_izd(self, izd: dict):
        self._preview.set_izd(izd)
        sh = izd.get("Форма", "") or "Прямоугольник"
        self._shape = sh
        w = izd.get("Ширина (мм)", "—")
        h = izd.get("Высота (мм)", "—")
        a, b, c = izd.get("Сторона A (мм)", "—"), izd.get("Сторона B (мм)", "—"), izd.get("Сторона C (мм)", "—")
        tv = izd.get("Кромка верх (мм)", "—")
        tb = izd.get("Кромка низ (мм)", "—")
        tl = izd.get("Кромка лево (мм)", "—")
        tr = izd.get("Кромка право (мм)", "—")

        for i, cb in self.checkboxes.items():
            cb.setVisible(True)
        if sh in ("Круг", "Овал", "Сложная фигура"):
            self.checkboxes[1].setText("Кромка" if sh != "Сложная фигура" else "Обработка")
            for i in (2, 3, 4):
                self.checkboxes[i].setVisible(False)
        elif sh == "Треугольник":
            self.checkboxes[1].setText("%s" % a)
            self.checkboxes[2].setText("%s" % b)
            self.checkboxes[3].setText("%s" % c)
            self.checkboxes[4].setVisible(False)
        elif sh == "Трапеция":
            self.checkboxes[1].setText("%s" % tv)
            self.checkboxes[2].setText("%s" % tl)
            self.checkboxes[3].setText("%s" % tr)
            self.checkboxes[4].setText("%s" % tb)
        else:
            self.checkboxes[1].setText("%s" % w)
            self.checkboxes[2].setText("%s" % h)
            self.checkboxes[3].setText("%s" % h)
            self.checkboxes[4].setText("%s" % w)

    def reset_to_defaults(self):
        self.set_facet4_free_lock(None)
        self.status = False
        for cb in self.checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self.cost_label.setText("—")

    def select_all_checkboxes(self):
        self.status = not self.status
        for cb in self.checkboxes.values():
            if cb.isVisible():
                cb.setChecked(self.status)

    def get_grind_sides(self):
        if self._shape in ("Круг", "Овал", "Сложная фигура"):
            on = self.checkboxes[1].isChecked()
            return {"Кромка": on}
        if self._shape == "Треугольник":
            return {
                "Верх": self.checkboxes[1].isChecked(),
                "Лево": self.checkboxes[2].isChecked(),
                "Право": self.checkboxes[3].isChecked(),
                "Низ": False,
            }
        return {
            "Верх": self.checkboxes[1].isChecked(),
            "Лево": self.checkboxes[2].isChecked(),
            "Право": self.checkboxes[3].isChecked(),
            "Низ": self.checkboxes[4].isChecked(),
        }

    def grind_needed(self) -> bool:
        return any(cb.isChecked() for cb in self.checkboxes.values() if cb.isVisible())

    def set_cost_text(self, text: str):
        self.cost_label.setText(text)
