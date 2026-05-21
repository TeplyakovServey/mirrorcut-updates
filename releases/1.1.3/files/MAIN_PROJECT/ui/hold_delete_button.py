# -*- coding: utf-8 -*-
"""Кнопка удаления с удержанием ~1 с: красная заливка слева направо."""
from __future__ import annotations

from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QRect
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QPushButton


class HoldDeleteButtonLTR(QPushButton):
    holdComplete = pyqtSignal()

    def __init__(self, text="Удалить", hold_ms=1000, parent=None):
        super().__init__(text, parent)
        self._hold_ms = max(200, int(hold_ms))
        self._fill = QColor(220, 38, 38)
        self._progress = 0.0
        self._active = False
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._on_tick)
        self.setMinimumHeight(28)
        self.setStyleSheet(
            "HoldDeleteButtonLTR { font-size: 11px; color: #374151; padding: 4px 10px; "
            "border: 1px solid #d1d5db; border-radius: 6px; background: #f9fafb; }"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._active = True
            self._progress = 0.0
            self._timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._active:
            self._timer.stop()
            self._active = False
            self._progress = 0.0
            self.update()
        super().mouseReleaseEvent(event)

    def _on_tick(self):
        if not self._active:
            return
        self._progress += 40.0 / float(self._hold_ms)
        if self._progress >= 1.0:
            self._timer.stop()
            self._active = False
            self._progress = 0.0
            self.holdComplete.emit()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.setPen(QPen(QColor(200, 210, 220)))
        p.setBrush(QColor(249, 250, 251))
        p.drawRoundedRect(r.adjusted(0, 0, -1, -1), 6, 6)
        if self._progress > 0:
            fw = int(r.width() * self._progress)
            if fw > 0:
                p.setPen(Qt.NoPen)
                p.setBrush(self._fill)
                p.drawRoundedRect(QRect(r.left(), r.top(), fw, r.height()), 6, 6)
        p.setPen(QColor(30, 41, 59))
        p.setFont(self.font())
        p.drawText(r.adjusted(6, 0, -6, 0), Qt.AlignCenter, self.text())
