"""Диалог «Добавить деталь»: размеры, получатель, кромки. Без выбора материала и толщины (добавляется на текущий лист)."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QGroupBox, QGridLayout, QDialogButtonBox, QRadioButton, QButtonGroup,
)
from PyQt5.QtCore import Qt

from ui.create_cut_dialog import PartPreview, FacetSizeDialog


class AddDetailDialog(QDialog):
    """Окно как при добавлении изделия в «Создать рез», но без материала и толщины."""
    def __init__(self, material_name, layout_dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить деталь на лист")
        self.setMinimumSize(420, 380)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Высота (мм):"))
        self.spin_h = QSpinBox()
        self.spin_h.setRange(1, 10000)
        self.spin_h.setValue(500)
        row2.addWidget(self.spin_h)
        row2.addWidget(QLabel("Ширина (мм):"))
        self.spin_w = QSpinBox()
        self.spin_w.setRange(1, 10000)
        self.spin_w.setValue(700)
        row2.addWidget(self.spin_w)
        row2.addWidget(QLabel("Кол-во:"))
        self.spin_qty = QSpinBox()
        self.spin_qty.setRange(1, 999)
        self.spin_qty.setValue(1)
        row2.addWidget(self.spin_qty)
        layout.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Получатель:"))
        self.recipient_edit = QLineEdit()
        self.recipient_edit.setPlaceholderText("Необязательно")
        row3.addWidget(self.recipient_edit, 1)
        layout.addLayout(row3)
        edge_grp = QGroupBox("Обработка кромок по сторонам")
        edge_main = QGridLayout(edge_grp)
        EDGE_OPTIONS = [(None, '—'), ('grinding', 'Шлифовка'), ('polishing', 'Полировка'), ('facet', 'Фацет')]
        self._edge_radios = {}
        self._edge_groups = {}
        self._facet_mm = 15

        def _connect_rb(side, val, rb):
            if val == 'facet':
                rb.toggled.connect(lambda checked, s=side: self._on_facet_toggled(checked, s))
            else:
                rb.toggled.connect(self._update_preview)

        for side in ['top', 'left', 'right', 'bottom']:
            self._edge_radios[side] = []
            grp = QButtonGroup(self)
            self._edge_groups[side] = grp
            for val, label in EDGE_OPTIONS:
                rb = QRadioButton(label)
                if val is None:
                    rb.setChecked(True)
                grp.addButton(rb)
                self._edge_radios[side].append((val, rb))
                _connect_rb(side, val, rb)
        top_row = QHBoxLayout()
        top_row.addStretch()
        for _, rb in self._edge_radios['top']:
            top_row.addWidget(rb)
        top_row.addStretch()
        edge_main.addLayout(top_row, 0, 1)
        left_col = QVBoxLayout()
        left_col.addStretch()
        for _, rb in self._edge_radios['left']:
            left_col.addWidget(rb)
        left_col.addStretch()
        edge_main.addLayout(left_col, 1, 0)
        self.preview = PartPreview(self)
        edge_main.addWidget(self.preview, 1, 1, Qt.AlignCenter)
        right_col = QVBoxLayout()
        right_col.addStretch()
        for _, rb in self._edge_radios['right']:
            right_col.addWidget(rb)
        right_col.addStretch()
        edge_main.addLayout(right_col, 1, 2)
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        for _, rb in self._edge_radios['bottom']:
            bottom_row.addWidget(rb)
        bottom_row.addStretch()
        edge_main.addLayout(bottom_row, 2, 1)
        layout.addWidget(edge_grp)
        self.spin_h.valueChanged.connect(self._update_preview)
        self.spin_w.valueChanged.connect(self._update_preview)
        self.spin_qty.valueChanged.connect(self._update_preview)
        self.recipient_edit.textChanged.connect(self._update_preview)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self._update_preview()

    def _on_facet_toggled(self, checked, side):
        if not checked:
            self._update_preview()
            return
        d = FacetSizeDialog(self._facet_mm, self)
        if d.exec_() != QDialog.Accepted:
            self._edge_groups[side].setExclusive(False)
            self._edge_radios[side][0][1].setChecked(True)
            self._edge_groups[side].setExclusive(True)
            return
        self._facet_mm = d.get_facet_mm()
        self._update_preview()

    def _get_edge_treatment(self):
        out = {}
        for side in ['left', 'right', 'top', 'bottom']:
            for val, rb in self._edge_radios[side]:
                if rb.isChecked():
                    out[side] = val
                    break
            else:
                out[side] = None
        if any(out.get(s) == 'facet' for s in ['left', 'right', 'top', 'bottom']):
            out['facet_mm'] = self._facet_mm
        return out

    def _update_preview(self):
        self.preview.set_data(
            self.spin_h.value(), self.spin_w.value(),
            self.spin_qty.value(), self.recipient_edit.text(),
            self._get_edge_treatment(),
        )

    def get_piece(self):
        """Вернуть dict для добавления в layout['pieces']: x, y, w, h, recipient, quantity_label, rotated, edge_treatment."""
        edge = self._get_edge_treatment()
        edge = {k: v for k, v in edge.items() if v is not None or k == 'facet_mm'}
        qty = max(1, self.spin_qty.value())
        return {
            'x': 0,
            'y': 0,
            'w': self.spin_w.value(),
            'h': self.spin_h.value(),
            'recipient': (self.recipient_edit.text() or '').strip() or '',
            'quantity_label': '× %d' % qty if qty > 1 else '1',
            'rotated': False,
            'edge_treatment': edge,
        }
