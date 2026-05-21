# -*- coding: utf-8 -*-
"""Превью «что нового» 960×560: HTML + фон, кнопка «Закрыть» снизу."""
from __future__ import annotations

import os
from typing import Optional

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)


class ReleaseNotesPreviewDialog(QDialog):
    """Без рамки окна, фиксированный размер, фон canvas_bg, HTML с baseUrl для картинок."""

    def __init__(
        self,
        parent: Optional[QWidget],
        html: str,
        canvas_bg: str = "#1e3a5f",
        *,
        base_path: Optional[str] = None,
        notes_base_url: Optional[str] = None,
        frameless: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Изменения в версии")
        self.setFixedSize(960, 560)
        if frameless:
            self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self._canvas_bg = canvas_bg or "#1e3a5f"

        root = QFrame(self)
        root.setObjectName("rn_root")
        root.setStyleSheet(
            "#rn_root { background-color: %s; border-radius: 12px; }" % self._canvas_bg
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(root)
        inner = QVBoxLayout(root)
        inner.setContentsMargins(16, 16, 16, 12)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        self._browser.setStyleSheet(
            "QTextBrowser { background: transparent; color: #f0f0f0; border: none; font-size: 14px; }"
        )
        if notes_base_url:
            self._browser.document().setBaseUrl(QUrl(notes_base_url))
        elif base_path and os.path.isdir(base_path):
            self._browser.document().setBaseUrl(QUrl.fromLocalFile(os.path.abspath(base_path) + os.sep))
        # Выравнивание блоков (text-align на div/p) + картинки по центру, как в макете
        self._browser.document().setDefaultStyleSheet(
            "body { color: #f0f0f0; font-size: 15px; line-height: 1.45; } "
            "p { margin: 0.28em 0; } "
            "div.rn-block { margin: 0.25em 0; } "
            "div.rn-block, div.rn-block p, div.rn-block span { text-align: inherit; } "
            "img { display: block; margin-left: auto; margin-right: auto; max-width: 92%; height: auto; } "
            "a { color: #a8d4ff; }"
        )
        self._browser.setHtml(html)
        inner.addWidget(self._browser, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_close = QPushButton("Закрыть")
        self._btn_close.setMinimumWidth(160)
        self._btn_close.setStyleSheet(
            "QPushButton { background-color: #e8944a; color: #111; font-weight: bold; "
            "padding: 10px 24px; border-radius: 8px; }"
            "QPushButton:hover { background-color: #f0a060; }"
        )
        self._btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self._btn_close)
        btn_row.addStretch(1)
        inner.addLayout(btn_row)

    def set_canvas_bg(self, color: str) -> None:
        self._canvas_bg = color or "#1e3a5f"
        f = self.findChild(QFrame, "rn_root")
        if f:
            f.setStyleSheet(
                "#rn_root { background-color: %s; border-radius: 12px; }" % self._canvas_bg
            )

    @staticmethod
    def validate_hex(c: str, default: str = "#1e3a5f") -> str:
        c = (c or "").strip()
        if not c.startswith("#") or len(c) not in (4, 7, 9):
            return default
        try:
            QColor(c)
            return c
        except Exception:
            return default
