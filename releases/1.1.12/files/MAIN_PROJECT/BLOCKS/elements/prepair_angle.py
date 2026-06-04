# -*- coding: utf-8 -*-
"""Обработка углов: ключи как block_corner_processing в test.py."""
from elements.calc_tile_style import (
    apply_service_tile_frame,
    style_compact_button,
    style_cost_label,
    style_tile_header,
)
from calc.corner_labels import corner_sort_keys, vertex_display
from calc.corner_rounding import parse_thickness_mm
from settings import *
from PyQt5.QtCore import QEvent, QObject, QPointF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

RECT_CORNERS = ["Верхний левый", "Нижний левый", "Верхний правый", "Нижний правый"]


class _SpinEnterFilter(QObject):
    """Enter в спинбоксе не закрывает диалог — только OK."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.KeyPress and ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            if isinstance(obj, QSpinBox):
                obj.interpretText()
                obj.clearFocus()
                return True
        return super().eventFilter(obj, ev)
# Как в Streamlit: «Угол А» с кириллической А
TRI_CORNERS = ["Угол А", "Угол B", "Угол C"]


def _corner_list(shape: str):
    if shape in ("Прямоугольник", "Трапеция"):
        return RECT_CORNERS
    if shape == "Треугольник":
        return TRI_CORNERS
    return []


class _CornerVertexPanel(QFrame):
    """Один угол: радио Нет / Срез / Скругление (мм)."""

    def __init__(self, cname: str, rounding: dict, cutting: dict, canvas: "CornerCanvasEditor"):
        super().__init__(canvas)
        self._name = cname
        self._canvas = canvas
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        self.setStyleSheet("QFrame { background: #f8fff8; border: 1px solid #5a8a5a; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 2, 3, 2)
        lay.setSpacing(0)

        self._grp = QButtonGroup(self)
        self._r0 = QRadioButton("Нет")
        self._r1 = QRadioButton("Срез")
        self._r2 = QRadioButton("Скругл.")
        for r in (self._r0, self._r1, self._r2):
            r.setFont(QFont("Arial", 7))
            self._grp.addButton(r)
        lay.addWidget(self._r0)
        lay.addWidget(self._r1)
        lay.addWidget(self._r2)

        self._spin = QSpinBox()
        self._spin.setRange(0, 300)
        self._spin.setSingleStep(5)
        self._spin.setMaximumWidth(82)
        ro = int(rounding.get(cname, 0) or 0)
        cu = bool(cutting.get(cname))
        if cu:
            self._r1.setChecked(True)
            self._spin.setValue(0)
        elif ro > 0:
            self._r2.setChecked(True)
            self._spin.setValue(ro)
        else:
            self._r0.setChecked(True)
            self._spin.setValue(0)
        lay.addWidget(self._spin)
        self._sync_spin()

        self._grp.buttonClicked.connect(self._on_mode)
        self._spin.valueChanged.connect(self._on_spin)

    def _sync_spin(self):
        on = self._r2.isChecked()
        self._spin.setEnabled(on)
        if self._r1.isChecked() or self._r0.isChecked():
            self._spin.blockSignals(True)
            self._spin.setValue(0)
            self._spin.blockSignals(False)

    def _on_mode(self, _b=None):
        self._sync_spin()
        if self._r2.isChecked() and self._spin.value() == 0:
            self._spin.blockSignals(True)
            self._spin.setValue(5)
            self._spin.blockSignals(False)
        self._canvas.schedule_price_refresh()

    def _on_spin(self, _v=None):
        self._canvas.schedule_price_refresh()

    def rounding_and_cut(self):
        if self._r1.isChecked():
            return 0, True
        if self._r2.isChecked():
            return int(self._spin.value() or 0), False
        return 0, False


class CornerCanvasEditor(QWidget):
    """Чертёж; панели привязаны к вершинам; цена срезов внизу."""

    # Позиции панелей (x,y): правый нижний угол панели = вершина фигуры (кроме верхней вершины треугольника)
    _PANEL_RECT = (92, 86)
    _RECT_PANEL_POS = {
        "Верхний левый": (70, 14),
        "Верхний правый": (380, 14),
        "Нижний правый": (380, 260),
        "Нижний левый": (70, 260),
    }
    _TRI_PANEL_POS = {
        "Угол А": (225, 18),
        "Угол B": (60, 172),
        "Угол C": (390, 172),
    }
    _TRAP_PANEL_POS = {
        "Верхний левый": (110, 14),
        "Верхний правый": (340, 14),
        "Нижний правый": (380, 260),
        "Нижний левый": (70, 260),
    }

    def __init__(
        self,
        shape: str,
        rounding: dict,
        cutting: dict,
        cut_type: str,
        price_label: QLabel,
        parent=None,
        thickness_mm: int = 0,
        perimeter_mm: float = 0.0,
    ):
        super().__init__(parent)
        self._shape = shape
        self._cut_type = cut_type or "С обработкой"
        self._price_label = price_label
        self._th = int(thickness_mm or 0)
        self._per = float(perimeter_mm or 0.0)
        self._price_timer = QTimer(self)
        self._price_timer.setSingleShot(True)
        self._price_timer.timeout.connect(self._refresh_prices)
        self.setFixedSize(520, 368)
        self.setObjectName("CornerCanvasEditor")
        self.setStyleSheet("#CornerCanvasEditor { background: #eef5ee; }")
        corners = _corner_list(shape)
        self._panels: dict = {}
        pos_map = {
            "Прямоугольник": self._RECT_PANEL_POS,
            "Трапеция": self._TRAP_PANEL_POS,
            "Треугольник": self._TRI_PANEL_POS,
        }.get(shape, {})
        pw, ph = self._PANEL_RECT

        for cname in corners:
            xy = pos_map.get(cname, (8, 8))
            panel = _CornerVertexPanel(cname, rounding, cutting, self)
            panel.setParent(self)
            panel.setGeometry(xy[0], xy[1], pw, ph)
            self._panels[cname] = panel

        self._refresh_prices()

    def set_cut_type(self, t: str):
        self._cut_type = (t or "С обработкой").strip() or "С обработкой"
        self.schedule_price_refresh()

    def schedule_price_refresh(self):
        self._price_timer.start(200)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#1a3d1a"))
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(QBrush(QColor("#b8e6c8")))
        lf = p.font()
        lf.setPointSize(8)
        lf.setBold(True)
        p.setFont(lf)
        p.setPen(QColor("#0d3d16"))
        if self._shape == "Прямоугольник":
            p.drawRect(160, 100, 220, 160)
            p.drawText(168, 118, "A")
            p.drawText(368, 118, "B")
            p.drawText(368, 268, "C")
            p.drawText(168, 268, "D")
        elif self._shape == "Треугольник":
            poly = QPolygonF(
                [QPointF(270, 108), QPointF(150, 258), QPointF(390, 258)]
            )
            p.drawPolygon(poly)
            p.drawText(262, 102, "A")
            p.drawText(130, 268, "B")
            p.drawText(392, 268, "C")
        elif self._shape == "Трапеция":
            poly = QPolygonF(
                [
                    QPointF(200, 100),
                    QPointF(340, 100),
                    QPointF(380, 260),
                    QPointF(160, 260),
                ]
            )
            p.drawPolygon(poly)
            p.drawText(192, 118, "A")
            p.drawText(332, 118, "B")
            p.drawText(368, 268, "C")
            p.drawText(152, 268, "D")

    def _refresh_prices(self):
        from calc.corner_rounding import compute_corner_rounding_detail

        ppu = 25 if self._cut_type == "С обработкой" else 15
        lines = []
        rdict, _cd = self.get_values()
        order = corner_sort_keys(self._shape) or list(rdict.keys())
        for name in order:
            p = self._panels.get(name)
            if not p:
                continue
            rv, cv = p.rounding_and_cut()
            if cv:
                lines.append(
                    "%s: срез — %s ₽" % (vertex_display(self._shape, name), ppu)
                )
        n_cut = sum(1 for p in self._panels.values() if p.rounding_and_cut()[1])
        if n_cut > 0:
            lines.append(
                "Срезы итого (за изд.): %s шт. × %s ₽ = %s ₽"
                % (n_cut, ppu, n_cut * ppu)
            )

        any_r = any(int(v or 0) > 0 for v in rdict.values())
        if any_r and self._th > 0 and self._per > 0:
            rr, kriv, r_pcs, r_ex = compute_corner_rounding_detail(
                rdict, self._per, self._th, conn=None, shape=self._shape
            )
            for key, rmm, sub in r_pcs:
                lines.append(
                    "%s: R %s мм — %s ₽"
                    % (vertex_display(self._shape, key), rmm, sub)
                )
            for title, sub in r_ex:
                lines.append("%s: %s ₽" % (title, sub))
            if r_pcs and rr > 0:
                suf = " (криволинейка)" if kriv else ""
                lines.append("Скругление итого (за изд.): %s ₽%s" % (rr, suf))
        elif any_r:
            lines.append(
                "Скругление: задайте толщину и периметр (кнопка «Рассчитать» в блоке стекла)."
            )
        self._price_label.setText("\n".join(lines) if lines else "—")

    def get_values(self):
        r = {}
        c = {}
        for name, p in self._panels.items():
            rv, cv = p.rounding_and_cut()
            r[name] = rv
            c[name] = cv
        return r, c


class CornerConfigDialog(QDialog):
    def __init__(
        self,
        shape: str,
        rounding: dict,
        cutting: dict,
        parent=None,
        cut_type: str = "С обработкой",
        thickness_mm: int = 0,
        perimeter_mm: float = 0.0,
    ):
        super().__init__(parent)
        self.setWindowTitle("Углы изделия")
        self.resize(560, 520)
        self._shape = shape
        lay = QVBoxLayout(self)
       
        self._rc_proc = QRadioButton("С обработкой")
        self._rc_raw = QRadioButton("Без обработки")
        for r in (self._rc_proc, self._rc_raw):
            r.setFont(QFont("Arial", 8))
        if (cut_type or "").strip() == "Без обработки":
            self._rc_raw.setChecked(True)
        else:
            self._rc_proc.setChecked(True)
        self._grp_cut = QButtonGroup(self)
        self._grp_cut.addButton(self._rc_proc)
        self._grp_cut.addButton(self._rc_raw)
        lay.addWidget(self._rc_proc)
        lay.addWidget(self._rc_raw)
        self._price_lbl = QLabel("—")
        self._price_lbl.setWordWrap(True)
        self._price_lbl.setFont(QFont("Arial", 9, QFont.Bold))
        self._price_lbl.setStyleSheet("color: #1a3d1a;")
        ct0 = (cut_type or "С обработкой").strip() or "С обработкой"
        self._canvas = CornerCanvasEditor(
            shape,
            rounding,
            cutting,
            ct0,
            self._price_lbl,
            self,
            thickness_mm=int(thickness_mm or 0),
            perimeter_mm=float(perimeter_mm or 0.0),
        )
        self._grp_cut.buttonClicked.connect(self._on_cut_radio)
        lay.addWidget(self._canvas, 0, Qt.AlignCenter)
        lay.addWidget(self._price_lbl)
        row = QHBoxLayout()
        ok = QPushButton("OK")
        cancel = QPushButton("Отмена")
        ok.setAutoDefault(False)
        ok.setDefault(False)
        cancel.setAutoDefault(False)
        cancel.setDefault(False)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)
        self._spin_filter = _SpinEnterFilter(self)
        for p in self._canvas._panels.values():
            p._spin.installEventFilter(self._spin_filter)

    def _on_cut_radio(self, _b=None):
        t = "Без обработки" if self._rc_raw.isChecked() else "С обработкой"
        self._canvas.set_cut_type(t)

    def get_values(self):
        r, c = self._canvas.get_values()
        ct = "Без обработки" if self._rc_raw.isChecked() else "С обработкой"
        return r, c, ct


class Angle_Frame(QWidget):
    cornersChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)

        self._shape = ""
        self.cut_type = "С обработкой"
        self.rounding = {k: 0 for k in RECT_CORNERS + TRI_CORNERS}
        self.cutting = {k: False for k in RECT_CORNERS + TRI_CORNERS}

        lay = QVBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(1)

        t = QLabel("УГЛЫ")
        style_tile_header(t)
        lay.addWidget(t)

        self.lbl_shape = QLabel("Форма: —")
        self.lbl_shape.setWordWrap(True)
        style_cost_label(self.lbl_shape)
        lay.addWidget(self.lbl_shape)

        self.lbl_sum = QLabel("—")
        self.lbl_sum.setWordWrap(True)
        style_cost_label(self.lbl_sum)
        lay.addWidget(self.lbl_sum)

        self.lbl_corner_money = QLabel("")
        self.lbl_corner_money.setWordWrap(True)
        style_cost_label(self.lbl_corner_money)
        lay.addWidget(self.lbl_corner_money)

        self.btn = QPushButton("Настроить…")
        style_compact_button(self.btn)
        self.btn.clicked.connect(self._open)
        lay.addWidget(self.btn)
        lay.addStretch()

    def set_shape(self, shape: str):
        self._shape = shape or ""
        self.lbl_shape.setText("Форма: %s" % (self._shape or "—"))
        self._refresh_summary()

    def _open(self):
        allowed = ("Прямоугольник", "Треугольник", "Трапеция")
        if self._shape not in allowed:
            QMessageBox.information(
                self,
                "Углы",
                "Скругление и срез доступны для прямоугольника, треугольника и трапеции.",
            )
            return
        th = 0
        per = 0.0
        win = self.window()
        if win is not None and hasattr(win, "selected"):
            try:
                izd = win.selected.get("Параметры изделия") or {}
                matp = win.selected.get("Параметры материала") or {}
                th = parse_thickness_mm(matp.get("Толщина (мм)"))
                pv = izd.get("Периметр (мм)")
                try:
                    per = float(pv) if pv is not None else 0.0
                except (TypeError, ValueError):
                    per = 0.0
            except Exception:
                pass
        dlg = CornerConfigDialog(
            self._shape,
            self.rounding,
            self.cutting,
            self,
            cut_type=self.cut_type,
            thickness_mm=th,
            perimeter_mm=per,
        )
        if dlg.exec_() == QDialog.Accepted:
            r, c, ct = dlg.get_values()
            self.cut_type = ct or "С обработкой"
            self.rounding.update(r)
            self.cutting.update(c)
            for corner, is_cut in c.items():
                if is_cut:
                    self.rounding[corner] = 0
            self._refresh_summary()
            self.cornersChanged.emit()

    def set_corner_price_lines(self, lines: list):
        """Многострочная детализация цен с главного пересчёта (xx.py)."""
        self.lbl_corner_money.setText("\n".join(lines) if lines else "")

    def _refresh_summary(self):
        corners = _corner_list(self._shape)
        if not corners:
            self.lbl_sum.setText("—")
            self.lbl_corner_money.setText("")
            return
        parts_r = []
        parts_c = []
        for k in corners:
            v = int(self.rounding.get(k, 0) or 0)
            if v > 0:
                parts_r.append(
                    "%s: R %s мм" % (vertex_display(self._shape, k), v)
                )
        for k in corners:
            if self.cutting.get(k):
                parts_c.append("%s: срез" % vertex_display(self._shape, k))
        sr = "; ".join(parts_r) if parts_r else "Окр. нет"
        lines = [sr]
        if parts_c:
            lines.append("; ".join(parts_c))
        self.lbl_sum.setText("\n".join(lines))

    def to_selected_blocks(self):
        """Как selected[\"Скругление углов\"] и selected[\"Срезать угол\"] в test.py."""
        corners = _corner_list(self._shape)
        ok_shape = self._shape in ("Прямоугольник", "Треугольник", "Трапеция")
        round_on = ok_shape and any(
            int(self.rounding.get(k, 0) or 0) > 0 for k in corners
        )
        cut_on = ok_shape and any(bool(self.cutting.get(k)) for k in corners)
        round_vals = {k: int(self.rounding.get(k, 0) or 0) for k in corners} if round_on else {}
        cut_vals = {k: bool(self.cutting.get(k)) for k in corners} if cut_on else {}
        rounding_cost = 0
        ppu = 25 if self.cut_type == "С обработкой" else 15
        cut_price = (sum(1 for k in corners if cut_vals.get(k)) * ppu) if cut_on else 0
        r_blk = {
            "Включено": bool(round_on),
            "Значения": round_vals,
            "Криволинейка": False,
            "Стоимость за изделие": rounding_cost,
            "Общая стоимость": 0,
        }
        c_blk = {
            "Включено": bool(cut_on),
            "Углы": cut_vals,
            "Тип": self.cut_type if cut_on else "—",
            "Цена за 1 изделие": cut_price,
        }
        return r_blk, c_blk
