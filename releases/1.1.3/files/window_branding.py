# -*- coding: utf-8 -*-
"""Единый логотип для окон и центрирование на экране (любое разрешение)."""
from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QWidget

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
