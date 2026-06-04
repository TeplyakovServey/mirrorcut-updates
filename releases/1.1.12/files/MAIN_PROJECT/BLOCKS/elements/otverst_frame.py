# -*- coding: utf-8 -*-
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QIntValidator
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from elements.calc_tile_style import apply_service_tile_frame, style_cost_label, style_tile_header

HOLE_DIAMETERS = [
    4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
    20, 22, 24, 26, 28, 30, 32, 34, 35, 36, 38, 40, 45, 46, 48, 50,
    55, 60, 65, 70, 80, 90, 100, 110, 120, 130,
]


class _HoleRow(QFrame):
    """Одна позиция: количество, диаметр, зенковка, строка суммы, удалить."""

    def __init__(self, parent_tile: "Otverst"):
        super().__init__(parent_tile)
        self._tile = parent_tile
        self.setObjectName("HoleRowCard")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet(
            "#HoleRowCard { border: 1px solid #666; border-radius: 3px; background: #f8f8f8; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 2, 3, 2)
        lay.setSpacing(1)

        top = QHBoxLayout()
        self._idx_lbl = QLabel("1")
        self._idx_lbl.setFont(QFont("Arial", 8, QFont.Bold))
        self._idx_lbl.setFixedWidth(18)
        top.addWidget(self._idx_lbl)

        top.addWidget(QLabel("шт"))
        self.cnt = QLineEdit()
        self.cnt.setValidator(QIntValidator(0, 999))
        self.cnt.setMaximumWidth(32)
        self.cnt.setFont(QFont("Arial", 8))
        top.addWidget(self.cnt)

        top.addWidget(QLabel("Ø"))
        self.dia = QComboBox()
        self.dia.setFont(QFont("Arial", 7))
        self.dia.addItems([str(x) for x in HOLE_DIAMETERS])
        self.dia.setMaximumWidth(46)
        top.addWidget(self.dia)

        self.z = QCheckBox("Зенк.")
        self.z.setFont(QFont("Arial", 7))
        top.addWidget(self.z)

        rm = QPushButton("×")
        rm.setFixedSize(22, 22)
        rm.setFont(QFont("Arial", 10, QFont.Bold))
        rm.clicked.connect(self._remove)
        top.addWidget(rm)
        top.addStretch(1)
        lay.addLayout(top)

        self._cost_lbl = QLabel("—")
        self._cost_lbl.setFont(QFont("Arial", 7))
        self._cost_lbl.setWordWrap(True)
        lay.addWidget(self._cost_lbl)

        self.cnt.textChanged.connect(parent_tile._emit_changed)
        self.cnt.editingFinished.connect(parent_tile._emit_changed)
        self.cnt.returnPressed.connect(parent_tile._emit_changed)
        self.dia.currentIndexChanged.connect(parent_tile._emit_changed)
        self.z.stateChanged.connect(parent_tile._emit_changed)

    def _remove(self):
        self._tile._remove_row(self)

    def mousePressEvent(self, event):
        self._tile._select_row(self)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self._tile._remove_row(self)
            event.accept()
            return
        super().keyPressEvent(event)

    def set_index(self, n: int):
        self._idx_lbl.setText(str(n))

    def set_cost_hint(self, text: str):
        self._cost_lbl.setText(text)

    def get_hole_dict(self):
        t = self.cnt.text().strip()
        if not t.isdigit() or int(t) <= 0:
            return None
        dtxt = self.dia.currentText()
        if not dtxt.isdigit():
            return None
        return {
            "Количество": int(t),
            "Размер": int(dtxt),
            "Зенковка": self.z.isChecked(),
        }


class Otverst(QWidget):
    holesChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)
        self.setMaximumWidth(188)
        self.setFocusPolicy(Qt.StrongFocus)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(3)

        title = QLabel("ОТВЕРСТИЯ")
        style_tile_header(title)
        root.addWidget(title)

        head = QHBoxLayout()
        head.addWidget(QLabel("Добавляйте позиции кнопкой +"))
        lab = head.itemAt(0).widget()
        if lab:
            lab.setFont(QFont("Arial", 7))
        self._btn_add = QPushButton("+")
        self._btn_add.setFixedSize(28, 28)
        self._btn_add.setFont(QFont("Arial", 14, QFont.Bold))
        self._btn_add.clicked.connect(self._add_row)
        head.addWidget(self._btn_add)
        head.addStretch(1)
        root.addLayout(head)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setMaximumHeight(118)
        self._scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        inner = QWidget()
        self._rows_layout = QVBoxLayout(inner)
        self._rows_layout.setSpacing(4)
        self._rows_layout.addStretch(1)
        self._scroll.setWidget(inner)
        root.addWidget(self._scroll)

        self._rows: list[_HoleRow] = []
        self._selected_row: _HoleRow | None = None

        self._total_one = QLabel("За изделие: —")
        self._total_one.setFont(QFont("Arial", 8, QFont.Bold))
        self._total_all = QLabel("")
        self._total_all.setFont(QFont("Arial", 7))
        root.addWidget(self._total_one)
        root.addWidget(self._total_all)

        self.cost_label = QLabel("—")
        self.cost_label.setWordWrap(True)
        style_cost_label(self.cost_label)
        root.addWidget(self.cost_label)

    def _emit_changed(self):
        self.holesChanged.emit()

    def _add_row(self):
        row = _HoleRow(self)
        self._rows.append(row)
        self._rows_layout.insertWidget(self._rows_layout.count() - 1, row)
        self._select_row(row)
        self._renumber()
        self._emit_changed()

    def _remove_row(self, row: _HoleRow):
        if row not in self._rows:
            return
        self._rows.remove(row)
        if self._selected_row is row:
            self._selected_row = None
        row.setParent(None)
        row.deleteLater()
        if self._rows:
            self._select_row(self._rows[min(len(self._rows) - 1, 0)])
        self._renumber()
        self._emit_changed()

    def _select_row(self, row: _HoleRow | None):
        self._selected_row = row if (row in self._rows) else None
        for r in self._rows:
            if r is self._selected_row:
                r.setStyleSheet(
                    "#HoleRowCard { border: 2px solid #1d4ed8; border-radius: 3px; background: #e8f0ff; }"
                )
                r.setFocus(Qt.MouseFocusReason)
            else:
                r.setStyleSheet(
                    "#HoleRowCard { border: 1px solid #666; border-radius: 3px; background: #f8f8f8; }"
                )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete and self._selected_row is not None:
            self._remove_row(self._selected_row)
            event.accept()
            return
        super().keyPressEvent(event)

    def _renumber(self):
        for i, r in enumerate(self._rows, start=1):
            r.set_index(i)

    def get_holes(self):
        out = []
        for r in self._rows:
            d = r.get_hole_dict()
            if d:
                out.append(d)
        return out

    def set_cost_text(self, text: str):
        self.cost_label.setText(text)

    def apply_price_breakdown(
        self,
        line_details: list,
        subtotal: int,
        markup: bool,
        final_one: int,
        qty_items: int,
    ):
        """line_details в том же порядке, что список из get_holes() (только валидные строки)."""
        hi = 0
        for r in self._rows:
            if not r.get_hole_dict():
                r.set_cost_hint("—")
                continue
            det = line_details[hi] if hi < len(line_details) else None
            hi += 1
            if not det:
                r.set_cost_hint("—")
                continue
            zt = ", зенк." if det.get("sink") else ""
            r.set_cost_hint(
                "Ø%s × %s шт%s → %s ₽"
                % (det["d"], det["qty"], zt, det["line"])
            )

        note = " *+50%% к сумме (много отв. / много диаметров)" if markup else ""
        self._total_one.setText("За изделие: %s ₽%s" % (final_one, note))
        if qty_items > 1:
            self._total_all.setText(
                "Всего за %s изд.: %s ₽" % (qty_items, final_one * qty_items)
            )
        else:
            self._total_all.setText("")

    def clear_price_breakdown(self):
        for r in self._rows:
            r.set_cost_hint("—")
        self._total_one.setText("За изделие: —")
        self._total_all.setText("")

    def apply_tempered_zenk(self, tempered: bool):
        for r in self._rows:
            r.z.blockSignals(True)
            if tempered:
                r.z.setChecked(True)
                r.z.setEnabled(False)
            else:
                r.z.setEnabled(True)
            r.z.blockSignals(False)

    def reset_to_defaults(self):
        for r in list(self._rows):
            r.setParent(None)
            r.deleteLater()
        self._rows.clear()
        self._selected_row = None
        self.clear_price_breakdown()
        self.cost_label.setText("—")
        self._emit_changed()
