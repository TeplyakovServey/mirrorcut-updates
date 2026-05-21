# -*- coding: utf-8 -*-
"""УФ склейка: строки в списке — петли (накл./снять) и сегменты (кол-во×длина); цены из БД."""
from __future__ import annotations

import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QIntValidator
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from elements.calc_tile_style import apply_service_tile_frame, style_cost_label, style_tile_header

_UF_BTN_QSS = (
    "background-color: #ddab22; color: black; font-weight: bold; font-size: 17px;"
)


def _format_hinge_line_ru(nakleit: int, snyat: int, rub: int) -> str:
    parts = []
    if nakleit > 0:
        parts.append("наклеить %s" % nakleit)
    if snyat > 0:
        parts.append("снять %s" % snyat)
    return "%s — %s ₽" % (", ".join(parts), rub)


class UF_Frame(QWidget):
    """Блок активен при высоте изделия > 70 мм (управление из xx._recalculate_impl)."""

    TILE_H = 200

    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)
        self.setFixedSize(200, self.TILE_H)

        self._interaction_enabled = False

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        self._hdr = QLabel("УФ СКЛЕЙКА")
        style_tile_header(self._hdr)
        root.addWidget(self._hdr)

        self.frame = QFrame()
        self.frame.setObjectName("ufInner")
        self.frame.setFrameShape(QFrame.NoFrame)
        self.frame.setStyleSheet("#ufInner { border: none; background: transparent; }")
        self.frame.setFixedHeight(164)
        root.addWidget(self.frame)

        self.lbl_height_hint = QLabel("", self.frame)
        self.lbl_height_hint.setGeometry(4, 4, 184, 12)
        self.lbl_height_hint.setStyleSheet("color: #333; font-size: 8px;")
        self.lbl_height_hint.setWordWrap(True)

        self.items_list = QListWidget(self.frame)
        self.items_list.setGeometry(4, 18, 184, 24)
        self.items_list.setFrameShape(QFrame.NoFrame)
        self.items_list.setFont(QFont("Arial", 10, QFont.Bold))
        self.items_list.setStyleSheet(
            "QListWidget { border: none; background: #ffffff; color: #111; font-size: 10pt; }"
            "QListWidget::item { padding: 2px 4px; min-height: 18px; }"
            "QListWidget::item:selected { background: #c5e3d7; color: #000; }"
        )
        self.items_list.installEventFilter(self)

        f8 = QFont("Arial", 8, QFont.Bold)
        fb = QFont("Arial", 9, QFont.Bold)

        self.lbl_hinge = QLabel("Наклеить   Снять", self.frame)
        self.lbl_hinge.setGeometry(4, 44, 160, 12)
        self.lbl_hinge.setFont(f8)

        self.nakleit = QLineEdit(self.frame)
        self.nakleit.setValidator(QIntValidator(0, 9999, self))
        self.nakleit.setGeometry(4, 58, 40, 20)
        self.nakleit.setFont(fb)

        self.snyat = QLineEdit(self.frame)
        self.snyat.setValidator(QIntValidator(0, 9999, self))
        self.snyat.setGeometry(48, 58, 40, 20)
        self.snyat.setFont(fb)

        self.add_btn_hinge = QPushButton("+", self.frame)
        self.add_btn_hinge.setGeometry(150, 58, 34, 20)
        self.add_btn_hinge.setStyleSheet(_UF_BTN_QSS)
        self.add_btn_hinge.clicked.connect(self.add_hinge_row)

        self.lbl_qty_len = QLabel("Кол-во   Длина (мм)", self.frame)
        self.lbl_qty_len.setGeometry(4, 80, 160, 12)
        self.lbl_qty_len.setFont(f8)

        self.kol_vo = QLineEdit(self.frame)
        self.kol_vo.setValidator(QIntValidator(0, 999999, self))
        self.kol_vo.setGeometry(4, 94, 40, 20)
        self.kol_vo.setFont(fb)

        self.dlina = QLineEdit(self.frame)
        self.dlina.setValidator(QIntValidator(0, 999999, self))
        self.dlina.setGeometry(48, 94, 40, 20)
        self.dlina.setFont(fb)

        self.add_btn_segment = QPushButton("+", self.frame)
        self.add_btn_segment.setGeometry(150, 94, 34, 20)
        self.add_btn_segment.setStyleSheet(_UF_BTN_QSS)
        self.add_btn_segment.clicked.connect(self.add_segment_row)

        self.cost_label = QLabel("—", self.frame)
        self.cost_label.setGeometry(4, 118, 184, 44)
        self.cost_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.cost_label.setWordWrap(True)
        style_cost_label(self.cost_label)
        cf = self.cost_label.font()
        cf.setPointSize(8)
        self.cost_label.setFont(cf)

        self.items = []
        self._hinge_hp = 320
        self._hinge_hr = 320
        self._apply_interaction_enabled_state()

    def update_hinge_rate_cache(self, hp: int, hr: int) -> None:
        self._hinge_hp = int(hp or 0)
        self._hinge_hr = int(hr or 0)

    def set_interaction_enabled(self, enabled: bool, height_mm: int = 0):
        self._interaction_enabled = bool(enabled)
        if not self._interaction_enabled:
            h = max(0, int(height_mm or 0))
            self.lbl_height_hint.setText(
                "Доступно при высоте изделия > 70 мм (сейчас: %s мм)." % h
            )
        else:
            self.lbl_height_hint.setText("")
        self._apply_interaction_enabled_state()

    def _apply_interaction_enabled_state(self):
        on = self._interaction_enabled
        for w in (
            self.items_list,
            self.kol_vo,
            self.dlina,
            self.add_btn_segment,
            self.nakleit,
            self.snyat,
            self.add_btn_hinge,
            self.lbl_qty_len,
            self.lbl_hinge,
        ):
            w.setEnabled(on)
        if not on:
            self.cost_label.setText("—")

    def set_cost_summary(self, text: str):
        self.cost_label.setText(text if (text or "").strip() else "—")

    def row_display_text(self, d: dict, pm: int | None, hp: int, hr: int) -> str:
        t = d.get("type")
        if not t and "количество" in d and "длина" in d:
            t = "segment"
        if t == "hinge":
            nk = int(d.get("наклеить") or 0)
            sn = int(d.get("снять") or 0)
            rub = nk * int(hp) + sn * int(hr)
            return _format_hinge_line_ru(nk, sn, rub)
        if t == "segment":
            q = int(d.get("количество") or 0)
            L = int(d.get("длина") or 0)
            if pm is None:
                return "кол-во: %s, длина: %s мм — нет тарифа (м)" % (q, L)
            meters = (q * L) / 1000.0
            rub = int(round(meters * float(pm)))
            return "кол-во: %s, длина: %s мм — %s ₽" % (q, L, rub)
        return str(d)

    def rebuild_list_texts(self, pm: int | None, hp: int, hr: int) -> None:
        for i, d in enumerate(self.items):
            if i < self.items_list.count():
                self.items_list.item(i).setText(self.row_display_text(d, pm, hp, hr))

    def block_should_highlight_used(self) -> bool:
        return bool(self._interaction_enabled and self.items)

    def reset_to_defaults(self):
        self.items_list.clear()
        self.items.clear()
        for fld in (self.kol_vo, self.dlina, self.nakleit, self.snyat):
            fld.clear()
        self.cost_label.setText("—")

    def eventFilter(self, obj, event):
        if (
            obj == self.items_list
            and self._interaction_enabled
            and event.type() == event.KeyPress
            and event.key() == Qt.Key_Delete
        ):
            row = self.items_list.currentRow()
            if row >= 0:
                self.items_list.takeItem(row)
                del self.items[row]
        return super().eventFilter(obj, event)

    def add_hinge_row(self):
        if not self._interaction_enabled:
            return
        try:
            nk = int((self.nakleit.text() or "").strip() or 0)
            sn = int((self.snyat.text() or "").strip() or 0)
        except ValueError:
            QMessageBox.warning(self, "УФ склейка", "Введите целые числа.")
            return
        if nk <= 0 and sn <= 0:
            QMessageBox.warning(
                self, "УФ склейка", "Укажите хотя бы одно: наклеить или снять (больше 0)."
            )
            return
        hp, hr = self._hinge_hp, self._hinge_hr
        rub = nk * hp + sn * hr
        text = _format_hinge_line_ru(nk, sn, rub)
        self.items_list.addItem(QListWidgetItem(text))
        self.items.append({"type": "hinge", "наклеить": nk, "снять": sn})
        self.nakleit.clear()
        self.snyat.clear()

    def add_segment_row(self):
        if not self._interaction_enabled:
            return
        kol_s = (self.kol_vo.text() or "").strip()
        dl_s = (self.dlina.text() or "").strip()
        if not kol_s or not dl_s:
            QMessageBox.warning(self, "УФ склейка", "Укажите количество и длину (мм).")
            return
        try:
            kol = int(kol_s)
            dl = int(dl_s)
        except ValueError:
            return
        if kol <= 0 or dl <= 0:
            QMessageBox.warning(self, "УФ склейка", "Количество и длина должны быть больше нуля.")
            return
        self.items_list.addItem(
            QListWidgetItem("кол-во: %s, длина: %s мм — …" % (kol, dl))
        )
        self.items.append({"type": "segment", "количество": kol, "длина": dl})
        self.kol_vo.clear()
        self.dlina.clear()

    def get_payload(self) -> dict:
        return {"Строки": [dict(x) for x in self.items]}


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = UF_Frame()
    w.set_interaction_enabled(True, 100)
    w.show()
    sys.exit(app.exec_())
