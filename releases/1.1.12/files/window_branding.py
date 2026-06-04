# -*- coding: utf-8 -*-
"""Единый логотип для окон и центрирование на экране (любое разрешение)."""
from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QDialog, QWidget

_dialog_exec_original = None

from app_paths import get_base_dir, get_resource_dir


def _project_roots() -> List[str]:
    """Возможные корни проекта для поиска logo.png."""
    here = os.path.dirname(os.path.abspath(__file__))
    roots = [
        get_resource_dir(),
        get_base_dir(),
        here,
        os.path.dirname(here),
        os.path.join(os.path.dirname(here), "MAIN_PROJECT"),
        os.path.join(os.path.dirname(here), "MAIN_PROJECT", "BLOCKS"),
    ]
    seen = set()
    out = []
    for r in roots:
        r = os.path.abspath(r)
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _logo_relative_paths() -> List[str]:
    return [
        os.path.join("logo", "logo.png"),
        "logo.png",
        os.path.join("resources", "logo.png"),
        os.path.join("MAIN_PROJECT", "BLOCKS", "logo.png"),
    ]


def get_logo_path() -> Optional[str]:
    """Первый найденный файл логотипа или None."""
    for root in _project_roots():
        for rel in _logo_relative_paths():
            p = os.path.normpath(os.path.join(root, rel))
            if os.path.isfile(p):
                return p
    return None


def get_app_icon() -> QIcon:
    p = get_logo_path()
    return QIcon(p) if p else QIcon()


def apply_app_icon(app: QApplication) -> None:
    """Иконка приложения — для главного окна и по умолчанию для диалогов."""
    ico = get_app_icon()
    if not ico.isNull():
        app.setWindowIcon(ico)


def apply_window_icon(widget: QWidget) -> None:
    """Явно выставить иконку окну (виджет верхнего уровня)."""
    ico = get_app_icon()
    if not ico.isNull():
        widget.setWindowIcon(ico)


def center_widget_on_screen(widget: QWidget, screen_index: Optional[int] = None) -> None:
    """Центрировать окно в доступной области экрана (учёт панели задач и DPI)."""
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    try:
        widget.adjustSize()
        frame = widget.frameGeometry()
        app = QApplication.instance()
        if app is None:
            return
        screen = None
        if screen_index is not None:
            screens = app.screens()
            if 0 <= screen_index < len(screens):
                screen = screens[screen_index]
        if screen is None:
            screen = app.primaryScreen()
        if screen is None:
            desk = app.desktop()
            ag = desk.availableGeometry(widget)
        else:
            ag = screen.availableGeometry()
        frame.moveCenter(ag.center())
        widget.move(frame.topLeft())
    finally:
        widget.setAttribute(Qt.WA_DontShowOnScreen, False)


def available_screen_geometry(widget: QWidget):
    """Доступная область экрана для виджета (учёт панели задач)."""
    try:
        scr = widget.screen()
        if scr is not None:
            return scr.availableGeometry()
    except Exception:
        pass
    app = QApplication.instance()
    if app is None:
        return None
    try:
        scr = widget.screen()
        if scr is not None:
            return scr.availableGeometry()
    except Exception:
        pass
    desk = app.desktop()
    if desk is not None:
        return desk.availableGeometry(widget)
    return None


def apply_fraction_window_geometry(widget: QWidget, fraction: float = 0.8) -> None:
    """Размер ~fraction экрана, по центру (как overview/cut-диалоги)."""
    geo = available_screen_geometry(widget)
    if geo is None:
        return
    frac = max(0.5, min(0.95, float(fraction)))
    w = max(640, int(geo.width() * frac))
    h = max(480, int(geo.height() * frac))
    widget.setMinimumSize(0, 0)
    widget.resize(w, h)
    fg = widget.frameGeometry()
    fg.moveCenter(geo.center())
    widget.move(fg.topLeft())


def prepare_modal_dialog_geometry(dialog: QWidget) -> None:
    """Размер и позиция до показа — без краткой вспышки пустого окна (Windows)."""
    center_widget_on_screen(dialog)


def install_dialog_flash_guard(app: QApplication) -> None:
    """Перед exec_() у QDialog выставить геометрию без отрисовки на экране."""
    global _dialog_exec_original
    if _dialog_exec_original is not None:
        return

    _dialog_exec_original = QDialog.exec_

    def _exec_patched(self):  # type: ignore[no-untyped-def]
        self.setAttribute(Qt.WA_DontShowOnScreen, True)
        try:
            if self.layout() is not None:
                self.layout().activate()
            if self.minimumWidth() > 0 and self.minimumHeight() > 0:
                if self.width() < self.minimumWidth() or self.height() < self.minimumHeight():
                    self.resize(
                        max(self.width(), self.minimumWidth()),
                        max(self.height(), self.minimumHeight()),
                    )
            prepare_modal_dialog_geometry(self)
        finally:
            self.setAttribute(Qt.WA_DontShowOnScreen, False)
        return _dialog_exec_original(self)

    QDialog.exec_ = _exec_patched  # type: ignore[method-assign]
