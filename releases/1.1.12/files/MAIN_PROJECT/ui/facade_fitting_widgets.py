# -*- coding: utf-8 -*-
"""Виджеты режима «Присадка» на схеме фасада: переключатель-пилюля и маркер отверстия (мм + ОК)."""
import os
import sys

_blocks = os.path.normpath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "BLOCKS"))
if _blocks not in sys.path:
    sys.path.insert(0, _blocks)

from PyQt5.QtCore import Qt, QPoint, pyqtSignal, QSize, QEvent
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush, QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QToolButton,
)

from calc import palette as P


def _lock_icon_dir() -> str:
    """Каталог PNG замков (MAIN_PROJECT/lock): разработка, onedir рядом с exe, onefile PyInstaller (_MEIPASS)."""
    here = os.path.abspath(__file__)
    dev_mp = os.path.dirname(os.path.dirname(here))
    dev_lock = os.path.join(dev_mp, "lock")
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = os.path.join(meipass, "MAIN_PROJECT", "lock")
            if os.path.isdir(bundled):
                return bundled
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        onedir = os.path.join(exe_dir, "MAIN_PROJECT", "lock")
        if os.path.isdir(onedir):
            return onedir
    return dev_lock


def _shadow_color():
    s = (P.FACADE_FITTING_SWITCH_SHADOW or "").strip()
    if s.startswith("rgba"):
        try:
            inner = s[s.index("(") + 1 : s.index(")")]
            parts = [p.strip() for p in inner.split(",")]
            r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
            a = float(parts[3])
            return QColor(r, g, b, int(min(255, max(0, round(a * 255)))))
        except Exception:
            pass
    return QColor(0, 0, 0, 51)


class FittingSwitch(QWidget):
    """Двухпозиционный переключатель в виде горизонтальной пилюли (как на референсе)."""

    toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._checked = False
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(56, 30)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def isChecked(self):
        return self._checked

    def setChecked(self, on, *, emit=True):
        on = bool(on)
        if self._checked == on:
            return
        self._checked = on
        self.update()
        if emit:
            self.toggled.emit(self._checked)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._checked)
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, Qt.transparent)
        track_h = h - 8
        track_w = w - 8
        x0, y0 = 4, 4
        rx = track_h // 2
        bg = P.FACADE_FITTING_SWITCH_ON_BG if self._checked else P.FACADE_FITTING_SWITCH_OFF_BG
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(bg)))
        p.drawRoundedRect(x0, y0, track_w, track_h, rx, rx)
        knob_r = track_h - 6
        margin = 3
        knob_x = x0 + track_w - margin - knob_r if self._checked else x0 + margin
        ky = y0 + (track_h - knob_r) // 2
        sh = _shadow_color()
        p.setBrush(QBrush(sh))
        p.drawEllipse(int(knob_x + 1), int(ky + 2), knob_r, knob_r)
        p.setBrush(QBrush(QColor(P.FACADE_FITTING_SWITCH_KNOB)))
        p.setPen(QPen(QColor(0, 0, 0, 28)))
        p.drawEllipse(int(knob_x), int(ky), knob_r, knob_r)
        p.end()


class _LabeledDragStripFrame(QFrame):
    """Полоска перетаскивания с подписью (#1, #2, …), без всплывающих подсказок."""

    def __init__(self, label_text, parent=None):
        super().__init__(parent)
        self.setFixedHeight(16)
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.SizeAllCursor)
        self._normal = "background-color:#cfe8ff;border:1px solid #7eb8e8;border-radius:4px;"
        self._hover = "background-color:#7ec8ff;border:1px solid #2b7fc4;border-radius:4px;"
        self.setStyleSheet(self._normal)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(0)
        self._lab = QLabel(label_text)
        self._lab.setStyleSheet(
            "color: #0d2740; font-weight: bold; font-size: 11px; background: transparent;"
        )
        self._lab.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay.addStretch(1)
        lay.addWidget(self._lab, 0, Qt.AlignCenter)
        lay.addStretch(1)

    def set_label_text(self, text):
        self._lab.setText(text)

    def enterEvent(self, event):
        self.setStyleSheet(self._hover)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet(self._normal)
        super().leaveEvent(event)


def _load_lock_png_pair():
    """PNG из MAIN_PROJECT/lock: red — закрытый, green — открытый (альфа сохраняется)."""
    base = _lock_icon_dir()
    red_p = os.path.join(base, "red.png")
    green_p = os.path.join(base, "green.png")
    pr = QPixmap(red_p) if os.path.isfile(red_p) else QPixmap()
    pg = QPixmap(green_p) if os.path.isfile(green_p) else QPixmap()
    return pr, pg


class MarkerLockButton(QToolButton):
    """Замок: включено (checked) = закрытый замок — участие в симметричной привязке."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAutoRaise(True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("MarkerLockButton { background: transparent; border: none; }")
        self.setToolTip(
            "Замок закрыт: отверстия на стороне двигаются согласованно (зеркально и равномерно).\n"
            "Замок открыт: это отверстие задаётся отдельно."
        )
        self._pix_closed, self._pix_open = _load_lock_png_pair()
        self.setFixedSize(24, 18)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def sizeHint(self):
        return QSize(24, 18)

    def set_lock_closed(self, closed):
        """closed=True — замок закрыт (симметрия)."""
        self.blockSignals(True)
        self.setChecked(bool(closed))
        self.blockSignals(False)
        self.update()

    def is_lock_closed(self):
        return self.isChecked()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, Qt.transparent)
        src = self._pix_closed if self.isChecked() else self._pix_open
        if src is not None and not src.isNull():
            m = 1
            pm = src.scaled(
                max(1, w - 2 * m),
                max(1, h - 2 * m),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            x = (w - pm.width()) // 2
            y = (h - pm.height()) // 2
            p.drawPixmap(x, y, pm)
        else:
            p.setRenderHint(QPainter.Antialiasing)
            cx, cy = w / 2, h / 2
            closed = self.isChecked()
            body_w, body_h = 10.0, 7.0
            bx = cx - body_w / 2
            by = cy + 0.5
            p.setPen(QPen(QColor(55, 55, 60), 1.2))
            p.setBrush(QBrush(QColor(210, 214, 222) if closed else QColor(248, 250, 255)))
            p.drawRoundedRect(int(bx), int(by), int(body_w), int(body_h), 2, 2)
            p.setBrush(Qt.NoBrush)
            if closed:
                p.drawArc(int(cx - 5), int(by - 8), 10, 10, 35 * 16, 110 * 16)
            else:
                p.drawArc(int(cx - 3), int(by - 9), 10, 10, 200 * 16, 130 * 16)
        p.end()


class HoleMarkerWidget(QFrame):
    """Поле отступа (мм) + ОК: число в поле применяется только по «ОК», не при вводе; перетаскивание — за полоску."""

    ok_clicked = pyqtSignal(int)
    lock_toggled = pyqtSignal(int, bool)  # index, closed (True = закрыт)

    def __init__(self, index, parent_canvas, parent=None):
        super().__init__(parent)
        self._index = index
        self._canvas = parent_canvas
        self._drag = False
        self._drag_anchor = QPoint()
        self._supplier_hint = ""
        self.setFrameStyle(QFrame.Box | QFrame.Raised)
        self.setLineWidth(1)
        self.setStyleSheet(
            "HoleMarkerWidget { background: #fafafa; border: 1px solid #888; border-radius: 4px; }"
        )
        self.setMinimumWidth(118)
        self.setMaximumWidth(220)
        self.setFixedHeight(72)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 4, 5, 4)
        lay.setSpacing(3)
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(4)
        self._drag_strip = _LabeledDragStripFrame("#%d" % (index + 1), self)
        self._vres_rank = index + 1
        top_row.addWidget(self._drag_strip, 7)
        self._lock_btn = MarkerLockButton(self)
        self._lock_btn.toggled.connect(self._on_lock_toggled)
        top_row.addWidget(self._lock_btn, 0, Qt.AlignVCenter)
        lay.addLayout(top_row)
        self.spin = QSpinBox()
        self.spin.setRange(1, 50000)
        self.spin.setSuffix(" мм")
        self.spin.setButtonSymbols(QSpinBox.NoButtons)
        self.spin.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.spin)
        self.btn = QPushButton("ОК")
        self.btn.setFixedHeight(22)
        self.btn.clicked.connect(self._emit_ok)
        lay.addWidget(self.btn)
        self._drag_strip.installEventFilter(self)
        self._lock_btn.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if watched in (self._drag_strip, self._lock_btn):
                self.raise_()
        return super().eventFilter(watched, event)

    def _on_lock_toggled(self, checked):
        self.lock_toggled.emit(self._index, bool(checked))

    def set_lock_closed(self, closed):
        self._lock_btn.set_lock_closed(closed)

    def is_lock_closed(self):
        return self._lock_btn.is_lock_closed()

    def _drag_strip_geometry(self):
        return self._drag_strip.geometry()

    def set_offset_mm(self, mm, *, block_signal=False):
        mm = int(max(1, mm))
        if block_signal:
            self.spin.blockSignals(True)
        self.spin.setValue(mm)
        if block_signal:
            self.spin.blockSignals(False)

    def _emit_ok(self):
        self.ok_clicked.emit(self._index)

    def set_supplier_hint(self, text):
        self._supplier_hint = (text or "").strip()

    def set_vres_rank(self, rank):
        """Подпись #N по порядку вдоль профиля (1 = ближе к началу стороны), не по индексу виджета."""
        r = int(max(1, rank))
        self._vres_rank = r
        self._drag_strip.set_label_text("#%d" % r)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.raise_()
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        g = self._drag_strip_geometry()
        if g.contains(event.pos()):
            self._drag = True
            self._drag_anchor = event.pos()
            event.accept()
            return
        w = self.childAt(event.pos())
        if w is self.spin or w is self.btn or (w and w.parent() is self.spin):
            return super().mousePressEvent(event)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag and (event.buttons() & Qt.LeftButton):
            gp = event.globalPos()
            parent_pos = self._canvas.mapFromGlobal(gp) - self._drag_anchor
            self._canvas.place_hole_marker_from_drag(self._index, parent_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag:
            self._drag = False
            if hasattr(self._canvas, "finalize_hole_drag_order"):
                self._canvas.finalize_hole_drag_order()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class FittingSwitchLabeled(QWidget):
    """Подпись «Присадка» + переключатель (фон как у панели — без чёрной полосы)."""

    toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(8)
        self.setAutoFillBackground(False)
        lab = QLabel("Присадка")
        lab.setStyleSheet("color: #000000; font-weight: bold; background: transparent;")
        row.addWidget(lab)
        self.switch = FittingSwitch(self)
        self.switch.toggled.connect(self.toggled.emit)
        row.addWidget(self.switch, 0, Qt.AlignVCenter)
        row.addStretch()

    def isChecked(self):
        return self.switch.isChecked()

    def setChecked(self, on, *, emit=True):
        self.switch.setChecked(on, emit=emit)
