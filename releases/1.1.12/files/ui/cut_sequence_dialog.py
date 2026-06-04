"""Диалог «Последовательность резов»: по одному слайдеру на каждый лист, пошаговый показ (план → линия реза → два куска отдельно → … → финал)."""
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSlider, QLabel, QScrollArea,
    QGroupBox, QWidget, QComboBox,
)
from PyQt5.QtCore import Qt, QEvent, QTimer
from PyQt5.QtGui import QPixmap
from ui.cutting_canvas import CuttingCanvas
from ui.cut_sequence_canvas import CutSequenceStepCanvas
from logic.cut_sequence import build_cut_sequence_for_sheet


def _remnant_preview_pixmap(size_px=80):
    """Образец QR для превью этикетки (номер при выполнении заказа)."""
    try:
        import io
        from logic.qr_utils import make_remnant_qr_image
        img = make_remnant_qr_image("PREVIEW", size_px=size_px)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        pm = QPixmap()
        pm.loadFromData(buf.getvalue())
        return pm
    except Exception:
        return QPixmap()


class CutSequenceDialog(QDialog):
    def __init__(self, layouts, order_info=None, parent=None):
        super().__init__(parent)
        self.layouts = layouts or []
        self.order_info = order_info or {}
        self.setWindowTitle("Последовательность резов — Заказ #%s" % self.order_info.get('id', ''))
        self.setMinimumSize(800, 600)
        layout = QVBoxLayout(self)

        self.sheet_steps = [build_cut_sequence_for_sheet(lay) for lay in self.layouts]
        self._canvas_sheet_idx = -1
        self._step_refresh_timer = QTimer(self)
        self._step_refresh_timer.setSingleShot(True)
        self._step_refresh_timer.setInterval(32)
        self._step_refresh_timer.timeout.connect(self._apply_step_to_canvas)
        self.step_canvas = CutSequenceStepCanvas(self)
        scroll = QScrollArea()
        scroll.setWidget(self.step_canvas)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(400)
        layout.addWidget(scroll, 1)

        hint = QLabel(
            "Снизу слева, по оси X вправо и вверх. Режем только полной линией (вся ширина или вся высота). "
            "Шаг 1 = план; далее: «режьте здесь» → два получившихся куска отдельно; в конце — все изделия и остатки отдельно с размерами."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #333; font-size: 11px; padding: 4px 0;")
        layout.addWidget(hint)

        # Выбор листа и слайдер шагов для этого листа
        row = QHBoxLayout()
        row.addWidget(QLabel("Лист:"))
        self.sheet_combo = QComboBox()
        for i in range(len(self.layouts)):
            n = len(self.sheet_steps[i]) if i < len(self.sheet_steps) else 0
            self.sheet_combo.addItem("Лист %d (%d шагов)" % (i + 1, n), i)
        self.sheet_combo.currentIndexChanged.connect(self._on_sheet_changed)
        row.addWidget(self.sheet_combo)
        row.addWidget(QLabel("Шаг:"))
        self.step_slider = QSlider(Qt.Horizontal)
        self.step_slider.setMinimum(0)
        self.step_slider.setMinimumHeight(28)
        self.step_slider.setTracking(False)
        self.step_slider.valueChanged.connect(self._on_step_slider)
        row.addWidget(self.step_slider, 1)
        self.step_label = QLabel("0")
        row.addWidget(self.step_label)
        layout.addLayout(row)

        self._update_slider_max()
        if self.layouts and self.sheet_steps:
            self.step_slider.setValue(0)
        self._show_current_step()

        # Превью этикеток для деловых остатков (при смене статуса на «Выполнен»)
        self._add_label_preview(layout)

        self.setStyleSheet("""
            QDialog { background-color: #E6F2FF; }
            QSlider::groove:horizontal {
                height: 8px; background: #B0C4DE; border-radius: 4px;
            }
            QSlider::handle:horizontal {
                width: 18px; margin: -5px 0;
                background: #4682B4; border-radius: 9px;
            }
            QSlider::handle:horizontal:hover { background: #5A9BD5; }
            QGroupBox { font-weight: bold; margin-top: 8px; }
        """)

    def _add_label_preview(self, layout):
        """Блок: как будет выглядеть этикетка на деловом остатке при выполнении заказа (№ и QR — после смены статуса)."""
        remnants = []
        for lay in self.layouts:
            mat = lay.get("material") or "Лист"
            for r in lay.get("business_rects", []):
                remnants.append((mat, r["w"], r["h"]))
        if not remnants:
            return
        grp = QGroupBox("Этикетки деловых остатков (при смене статуса на «Выполнен»)")
        inner = QVBoxLayout()
        inner.addWidget(QLabel("При выполнении заказа каждому остатку будет присвоен номер и этикетка с QR для склада. Ниже — образец QR."))
        for i, (name, w, h) in enumerate(remnants):
            row = QHBoxLayout()
            row.addWidget(QLabel("%s  %d×%d мм" % (name, w, h)))
            row.addWidget(QLabel("  → № на этикетке будет присвоен при выполнении"))
            qr_lbl = QLabel()
            qr_lbl.setPixmap(_remnant_preview_pixmap(64))
            row.addWidget(qr_lbl)
            row.addStretch()
            inner.addLayout(row)
        grp.setLayout(inner)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grp)
        scroll.setMaximumHeight(180)
        layout.addWidget(scroll)

    def showEvent(self, event):
        super().showEvent(event)
        if event.type() == QEvent.Show:
            self.showMaximized()

    def _update_slider_max(self):
        idx = self.sheet_combo.currentIndex()
        if idx < 0 or idx >= len(self.sheet_steps):
            return
        steps = self.sheet_steps[idx]
        n = max(0, len(steps) - 1)
        self.step_slider.setMaximum(n)
        self.step_slider.setValue(min(self.step_slider.value(), n))

    def _on_sheet_changed(self, _idx):
        self._update_slider_max()
        self._canvas_sheet_idx = -1
        self._show_current_step()

    def _on_step_slider(self, value):
        idx = self.sheet_combo.currentIndex()
        steps = self.sheet_steps[idx] if 0 <= idx < len(self.sheet_steps) else []
        n = len(steps)
        if value < n:
            step = steps[value]
            step_names = {'plan': 'План', 'cut_rect': 'Рез прямоугольника', 'after_cut': 'После реза (№№)', 'final': 'Итог'}
            st = step.get('type', '')
            self.step_label.setText("%d: %s" % (value, step_names.get(st, st)))
        else:
            self.step_label.setText(str(value))
        self._step_refresh_timer.stop()
        self._step_refresh_timer.start()

    def _apply_step_to_canvas(self):
        idx = self.sheet_combo.currentIndex()
        if idx < 0 or idx >= len(self.layouts):
            return
        lay = self.layouts[idx]
        steps = self.sheet_steps[idx]
        step_idx = self.step_slider.value()
        if idx != self._canvas_sheet_idx:
            self.step_canvas.set_sheet_and_steps(lay, steps)
        self._canvas_sheet_idx = idx
        self.step_canvas.set_step(step_idx)

    def _show_current_step(self):
        idx = self.sheet_combo.currentIndex()
        if idx < 0 or idx >= len(self.layouts):
            return
        self._step_refresh_timer.stop()
        lay = self.layouts[idx]
        steps = self.sheet_steps[idx]
        step_idx = self.step_slider.value()
        if idx != self._canvas_sheet_idx:
            self.step_canvas.set_sheet_and_steps(lay, steps)
        self._canvas_sheet_idx = idx
        self.step_canvas.set_step(step_idx)
