# -*- coding: utf-8 -*-
"""Подготовительные услуги: суммы по чекбоксам через модальное окно."""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QInputDialog,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from elements.calc_tile_style import apply_service_tile_frame, style_cost_label, style_tile_header


class Pod_USLUGI(QWidget):
    _KEYS = (
        ("check_1", "Скачивание файла"),
        ("check_2", "Изготовление шаблона"),
        ("check_4", "Работа дизайнера"),
        ("check_5", "Работа технолога"),
    )

    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)
        self.setFixedSize(200, 200)

        self._sums: dict[str, int] = {"check_1": 0, "check_2": 0, "check_4": 0, "check_5": 0}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)
        t = QLabel("ПОДГОТОВИТЕЛЬНЫЕ УСЛУГИ")
        style_tile_header(t)
        t.setFont(QFont("Arial", 10, QFont.Bold))
        lay.addWidget(t)

        self.check_1 = QCheckBox("Скачивание файла")
        self.check_2 = QCheckBox("Изготовление шаблона")
        self.check_4 = QCheckBox("Работа дизайнера")
        self.check_5 = QCheckBox("Работа технолога")
        font = QFont("Arial", 9, QFont.Bold)
        for cb in (self.check_1, self.check_2, self.check_4, self.check_5):
            cb.setFont(font)
            lay.addWidget(cb)

        for attr, _title in self._KEYS:
            cb = getattr(self, attr)
            cb.clicked.connect(lambda checked, a=attr, b=cb: self._on_pod_clicked(a, b, checked))

        lay.addStretch(1)

        self._lbl_total = QLabel("Итого: 0 ₽")
        self._lbl_total.setAlignment(Qt.AlignLeft | Qt.AlignBottom)
        self._lbl_total.setWordWrap(True)
        style_cost_label(self._lbl_total)
        tf = self._lbl_total.font()
        tf.setPointSize(9)
        self._lbl_total.setFont(tf)
        lay.addWidget(self._lbl_total)
        self._refresh_total_label()

    def _total_rub(self) -> int:
        return sum(
            int(self._sums.get(a, 0) or 0)
            for a, _t in self._KEYS
            if getattr(self, a).isChecked()
        )

    def _refresh_total_label(self) -> None:
        n = self._total_rub()
        self._lbl_total.setText("Итого: %s ₽" % n)

    def _on_pod_clicked(self, attr: str, cb: QCheckBox, checked: bool) -> None:
        if checked:
            prev = int(self._sums.get(attr, 0) or 0)
            val, ok = QInputDialog.getInt(
                self,
                "Сумма",
                "Сумма, ₽:",
                prev if prev > 0 else 1000,
                0,
                99_999_999,
                1,
            )
            if not ok or val <= 0:
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)
                self._refresh_total_label()
                return
            self._sums[attr] = int(val)
        else:
            self._sums[attr] = 0
        self._refresh_total_label()

    def reset_to_defaults(self) -> None:
        self._sums = {k: 0 for k in ("check_1", "check_2", "check_4", "check_5")}
        for attr, _t in self._KEYS:
            cb = getattr(self, attr)
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        self._refresh_total_label()

    def get_info(self) -> dict:
        """Словарь для selected['Подготовительные услуги'] и подсветки."""
        out: dict = {}
        for attr, title in self._KEYS:
            cb = getattr(self, attr)
            amt = int(self._sums.get(attr, 0) or 0) if cb.isChecked() else 0
            out[title] = {"Включено": cb.isChecked() and amt > 0, "Сумма (₽)": amt}
        out["Итого (₽)"] = sum(
            int(self._sums.get(a, 0) or 0)
            for a, _t in self._KEYS
            if getattr(self, a).isChecked()
        )
        return out
