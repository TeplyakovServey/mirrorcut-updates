# -*- coding: utf-8 -*-
"""Плитка доставки: в пределах КАД / вне КАД, карта и сохранённый маршрут."""
from typing import List, Optional

from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from elements.calc_tile_style import (
    apply_service_tile_frame,
    style_compact_button,
    style_cost_label,
    style_tile_header,
)
from elements.delivery_dialog import DeliveryOutsideDialog


class DeliveryTile(QWidget):
    deliveryChanged = pyqtSignal()

    MODE_NONE = 0
    MODE_INSIDE = 1
    MODE_OUTSIDE = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        apply_service_tile_frame(self)
        self._mode = self.MODE_NONE
        self._data: dict = {}
        self._price_rub: Optional[int] = None
        self._price_lines: List[str] = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        t = QLabel("ДОСТАВКА")
        style_tile_header(t)
        lay.addWidget(t)

        self._grp = QButtonGroup(self)
        self._rb_none = QRadioButton("Без доставки")
        self._rb_in = QRadioButton("В пределах КАД")
        self._rb_out = QRadioButton("Вне КАД")
        for r in (self._rb_none, self._rb_in, self._rb_out):
            r.setFont(QFont("Arial", 8))
            self._grp.addButton(r)
            lay.addWidget(r)
        self._rb_none.setChecked(True)

        self._addr_in = QLineEdit()
        self._addr_in.setPlaceholderText("Адрес (в пределах КАД)")
        self._addr_in.setVisible(False)
        self._addr_in.setFont(QFont("Arial", 8))
        self._addr_in.textChanged.connect(self._on_inside_address_text)
        lay.addWidget(self._addr_in)

        self.btn = QPushButton("Изменить адрес…")
        style_compact_button(self.btn)
        self.btn.setEnabled(False)
        lay.addWidget(self.btn)

        self.lbl = QLabel("—")
        self.lbl.setWordWrap(True)
        style_cost_label(self.lbl)
        lay.addWidget(self.lbl)

        row_pay = QHBoxLayout()
        row_pay.setSpacing(4)
        _lp = QLabel("Оплата:")
        _lp.setFont(QFont("Arial", 8))
        row_pay.addWidget(_lp)
        self._oplata = QComboBox()
        self._oplata.setFont(QFont("Arial", 8))
        self._oplata.addItems(["не указано", "оплачено", "не оплачено"])
        self._oplata.setMaxVisibleItems(4)
        row_pay.addWidget(self._oplata, 1)
        lay.addLayout(row_pay)

        lay.addStretch()

        self._grp.buttonClicked.connect(self._on_mode)
        self.btn.clicked.connect(self._open_outside)

    def _on_inside_address_text(self, text: str):
        if self._mode != self.MODE_INSIDE:
            return
        if not isinstance(self._data, dict):
            self._data = {}
        self._data["Адрес"] = (text or "").strip()
        self.deliveryChanged.emit()

    def _sync_inside_address_field(self):
        if self._mode != self.MODE_INSIDE:
            return
        addr = ""
        if isinstance(self._data, dict):
            addr = (self._data.get("Адрес") or "").strip()
        self._addr_in.blockSignals(True)
        self._addr_in.setText(addr)
        self._addr_in.blockSignals(False)

    def _on_mode(self):
        prev_mode = self._mode
        if self._rb_none.isChecked():
            self._mode = self.MODE_NONE
            self._data = {}
            self._price_rub = None
            self._price_lines = []
            self.btn.setEnabled(False)
            self.btn.setVisible(True)
            self._addr_in.setVisible(False)
            self.lbl.setText("—")
        elif self._rb_in.isChecked():
            self._mode = self.MODE_INSIDE
            self.btn.setEnabled(False)
            self.btn.setVisible(False)
            self._addr_in.setVisible(True)
            if prev_mode != self.MODE_INSIDE:
                self._addr_in.blockSignals(True)
                self._addr_in.clear()
                self._addr_in.blockSignals(False)
            self._data = {
                "Адрес": (self._addr_in.text() or "").strip(),
                "Внутри КАД": True,
                "Расстояние до КАД": None,
                "Расстояние маршрута м": None,
                "lat": None,
                "lon": None,
                "Маршрут координаты": None,
            }
            self._sync_inside_address_field()
            self._price_rub = None
            self._price_lines = []
            self._refresh_lbl()
        else:
            self._mode = self.MODE_OUTSIDE
            self._addr_in.setVisible(False)
            self.btn.setVisible(True)
            self.btn.setEnabled(True)
            if not self._data or self._data.get("Внутри КАД"):
                self._data = {
                    "Адрес": "",
                    "Внутри КАД": False,
                    "Расстояние до КАД": None,
                    "Расстояние маршрута м": None,
                    "lat": None,
                    "lon": None,
                    "Маршрут координаты": None,
                }
            self._price_rub = None
            self._price_lines = []
            self._refresh_lbl()
            QTimer.singleShot(0, self._open_outside)
        self.deliveryChanged.emit()

    def _open_outside(self):
        if self._mode != self.MODE_OUTSIDE:
            return
        dlg = DeliveryOutsideDialog(self._data if self._data else None, self)
        if dlg.exec_() == QDialog.Accepted:
            self._data = dlg.get_result()
            self._price_rub = None
            self._price_lines = []
            self._refresh_lbl()
            self.deliveryChanged.emit()

    def _refresh_lbl(self):
        if self._mode == self.MODE_NONE:
            self.lbl.setText("—")
            return
        if self._mode == self.MODE_INSIDE:
            addr = (self._data.get("Адрес") or "").strip() if isinstance(self._data, dict) else ""
            head = "В пределах КАД"
            if addr:
                head += "\n%s" % (addr[:72] + ("…" if len(addr) > 74 else ""))
            if self._price_lines:
                self.lbl.setText(head + "\n" + "\n".join(self._price_lines))
            elif self._price_rub is not None:
                self.lbl.setText("%s\nИтого: %s ₽" % (head, self._price_rub))
            else:
                self.lbl.setText(head)
            return
        if not self._data.get("Расстояние до КАД"):
            self.lbl.setText("Вне КАД — укажите адрес")
            return
        parts = []
        addr = (self._data.get("Адрес") or "").strip()
        if addr:
            parts.append(addr[:48] + ("…" if len(addr) > 50 else ""))
        if self._price_lines:
            parts.extend(self._price_lines)
        else:
            dkm = self._data.get("Расстояние до КАД")
            if dkm is not None:
                parts.append("До границы КАД (тариф): %s км" % dkm)
            dm = self._data.get("Расстояние маршрута м")
            if dm is not None:
                try:
                    parts.append("Длина маршрута: %.2f км" % (float(dm) / 1000.0))
                except (TypeError, ValueError):
                    pass
            if self._price_rub is not None:
                parts.append("Итого: %s ₽" % self._price_rub)
        self.lbl.setText("\n".join(parts) if parts else "—")

    def set_price_detail(self, rub: Optional[int], lines: Optional[List[str]] = None):
        self._price_rub = rub
        self._price_lines = list(lines) if lines else []
        self._refresh_lbl()

    def reset_to_defaults(self):
        self._grp.blockSignals(True)
        self._rb_none.setChecked(True)
        self._grp.blockSignals(False)
        self._oplata.blockSignals(True)
        self._oplata.setCurrentIndex(0)
        self._oplata.blockSignals(False)
        self._mode = self.MODE_NONE
        self._data = {}
        self._price_rub = None
        self._price_lines = []
        self.btn.setEnabled(False)
        self.btn.setVisible(True)
        self._addr_in.blockSignals(True)
        self._addr_in.clear()
        self._addr_in.blockSignals(False)
        self._addr_in.setVisible(False)
        self.lbl.setText("—")

    def to_selected_block(self) -> dict:
        if self._mode == self.MODE_NONE:
            return {"Активирован": False, "Данные": None}
        dd = dict(self._data)
        dd["Оплата"] = self._oplata.currentText()
        return {"Активирован": True, "Данные": dd}

    def apply_saved_block(self, blk) -> None:
        if not isinstance(blk, dict) or not blk.get("Активирован"):
            return
        bd = blk.get("Данные")
        if not isinstance(bd, dict):
            return
        op = (bd.get("Оплата") or "").strip()
        if op in ("оплачено", "не оплачено", "не указано"):
            self._oplata.blockSignals(True)
            self._oplata.setCurrentText(op)
            self._oplata.blockSignals(False)
        inside = bd.get("Внутри КАД")
        if inside is True or (inside is None and not bd.get("Расстояние до КАД") and not bd.get("Расстояние маршрута м")):
            self._grp.blockSignals(True)
            self._rb_in.setChecked(True)
            self._grp.blockSignals(False)
            self._mode = self.MODE_INSIDE
            self._data = {
                "Адрес": (bd.get("Адрес") or "").strip(),
                "Внутри КАД": True,
                "Расстояние до КАД": None,
                "Расстояние маршрута м": None,
                "lat": None,
                "lon": None,
                "Маршрут координаты": None,
            }
            self.btn.setEnabled(False)
            self.btn.setVisible(False)
            self._addr_in.setVisible(True)
            self._addr_in.blockSignals(True)
            self._addr_in.setText(self._data.get("Адрес") or "")
            self._addr_in.blockSignals(False)
        else:
            self._grp.blockSignals(True)
            self._rb_out.setChecked(True)
            self._grp.blockSignals(False)
            self._mode = self.MODE_OUTSIDE
            self._data = {k: bd.get(k) for k in ("Адрес", "Внутри КАД", "Расстояние до КАД", "Расстояние маршрута м", "lat", "lon", "Маршрут координаты")}
            self._data["Внутри КАД"] = False
            self.btn.setVisible(True)
            self.btn.setEnabled(True)
            self._addr_in.setVisible(False)
        self._refresh_lbl()

    def is_configured_for_price(self) -> bool:
        if self._mode == self.MODE_NONE:
            return False
        if self._mode == self.MODE_INSIDE:
            return True
        return self._data.get("Расстояние до КАД") is not None

    def highlight_delivery_used(self) -> bool:
        if self._mode == self.MODE_NONE:
            return False
        if self._mode == self.MODE_INSIDE:
            return True
        return self.is_configured_for_price()
