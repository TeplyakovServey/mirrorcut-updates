# -*- coding: utf-8 -*-
"""Единый вид сервисных плиток; цвета — из calc.palette."""
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QPushButton, QWidget

from calc import palette as P

TILE_SIDE_PX = 200


def _service_tile_qss(border_hex: str) -> str:
    return """
#CalcServiceTile {{
    border: 3px solid {border};
    background-color: {bg};
    border-radius: 3px;
    color: {fg};
    font-weight: bold;
}}
#CalcServiceTile:disabled {{
    background-color: #9a9a9a;
    border: 3px solid #252525;
    color: #2a2a2a;
}}
""".format(
        border=border_hex,
        bg=P.TILE_SURFACE,
        fg=P.TILE_TEXT,
    )


SERVICE_TILE_QSS = _service_tile_qss(P.TILE_BORDER_IDLE)


def _service_tile_children_qss() -> str:
    """Единый фон для комбобоксов и чекбоксов внутри сервисной плитки (без белых вставок)."""
    surf = P.TILE_SURFACE
    fg = P.TILE_TEXT
    hdr = P.TILE_HEADER_BG
    bd = P.CONTROL_BORDER
    acc = P.CONTROL_ACCENT
    return """
#CalcServiceTile QComboBox {{
    background-color: {surf};
    color: {fg};
    border: 1px solid {bd};
    border-radius: 2px;
    padding: 1px 4px;
}}
#CalcServiceTile QComboBox QAbstractItemView {{
    background-color: {hdr};
    color: {fg};
    selection-background-color: {acc};
    selection-color: #ffffff;
}}
#CalcServiceTile QComboBox::drop-down {{
    border: none;
    width: 16px;
}}
#CalcServiceTile QCheckBox {{
    color: {fg};
    spacing: 4px;
    background: transparent;
}}
#CalcServiceTile QCheckBox::indicator {{
    width: 14px;
    height: 14px;
}}
#CalcServiceTile QCheckBox::indicator:unchecked {{
    background-color: {surf};
    border: 1px solid {bd};
}}
#CalcServiceTile QCheckBox::indicator:checked {{
    background-color: {acc};
    border: 1px solid {bd};
}}
#CalcServiceTile QLineEdit {{
    background-color: {surf};
    color: {fg};
    border: 1px solid {bd};
}}
#CalcServiceTile QLabel {{
    background: transparent;
}}
#CalcServiceTile QRadioButton {{
    color: {fg};
    background: transparent;
    spacing: 4px;
}}
#CalcServiceTile QRadioButton::indicator {{
    width: 14px;
    height: 14px;
}}
#CalcServiceTile QRadioButton::indicator:unchecked {{
    background-color: {surf};
    border: 2px solid {bd};
    border-radius: 7px;
}}
#CalcServiceTile QRadioButton::indicator:checked {{
    background-color: {acc};
    border: 2px solid {bd};
    border-radius: 7px;
}}
#CalcServiceTile QTextEdit {{
    background-color: {surf};
    color: {fg};
    border: 1px solid {bd};
    border-radius: 2px;
}}
#CalcServiceTile QDateEdit {{
    background-color: {surf};
    color: {fg};
    border: 1px solid {bd};
    border-radius: 2px;
    padding: 1px 2px;
}}
#CalcServiceTile QListWidget {{
    background-color: {surf};
    color: {fg};
    border: 1px solid {bd};
}}
#CalcServiceTile QWidget#ServiceTileBody,
#CalcServiceTile QWidget#ZamerSplitLeft,
#CalcServiceTile QWidget#ZamerSplitRight {{
    background-color: {surf};
}}
#CalcServiceTile QScrollArea,
#CalcServiceTile QScrollArea#ZamerThumbScroll {{
    background-color: {surf};
    border: 1px solid {bd};
}}
#CalcServiceTile QScrollArea QWidget {{
    background-color: {surf};
}}
#CalcServiceTile QPushButton {{
    background-color: {surf};
    color: {fg};
    border: 1px solid {bd};
    border-radius: 2px;
    padding: 2px 6px;
}}
""".format(
        surf=surf,
        fg=fg,
        hdr=hdr,
        bd=bd,
        acc=acc,
    )


def glass_tile_children_qss() -> str:
    """Без белых полей внутри большой плитки изделия (#glassProductTile)."""
    surf = P.GLASS_TILE_FILL
    fg = P.TILE_TEXT
    bd = P.CONTROL_BORDER
    acc = P.CONTROL_ACCENT
    return """
#glassProductTile QComboBox {{
    background-color: {surf};
    color: {fg};
    border: 1px solid {bd};
    border-radius: 2px;
    padding: 1px 4px;
}}
#glassProductTile QComboBox QAbstractItemView {{
    background-color: {surf};
    color: {fg};
    selection-background-color: {acc};
    selection-color: #ffffff;
}}
#glassProductTile QComboBox::drop-down {{
    border: none;
    width: 16px;
}}
#glassProductTile QLineEdit {{
    background-color: {surf};
    color: {fg};
    border: 1px solid {bd};
}}
#glassProductTile QCheckBox {{
    color: {fg};
    spacing: 4px;
    background: transparent;
}}
#glassProductTile QCheckBox::indicator {{
    width: 14px;
    height: 14px;
}}
#glassProductTile QCheckBox::indicator:unchecked {{
    background-color: {surf};
    border: 1px solid {bd};
}}
#glassProductTile QCheckBox::indicator:checked {{
    background-color: {acc};
    border: 1px solid {bd};
}}
#glassProductTile QStackedWidget {{
    background: transparent;
}}
#glassProductTile QLabel {{
    background: transparent;
    color: {fg};
}}
#glassProductTile QPushButton {{
    background-color: {surf};
    color: {fg};
    border: 1px solid {bd};
    border-radius: 2px;
}}
""".format(
        surf=surf,
        fg=fg,
        bd=bd,
        acc=acc,
    )

HEADER_LABEL_QSS = (
    "background-color: %s; color: %s; font-weight: bold; font-size: 11px; "
    "padding: 3px 4px; min-height: 18px; max-height: 20px;"
) % (P.TILE_HEADER_BG, P.TILE_TEXT)

COST_LABEL_QSS = "font-size: 10px; color: %s; font-weight: bold;" % P.TILE_COST_TEXT

ORANGE_ACTION_QSS = (
    "background-color: %s; color: %s; font-weight: bold; font-size: 9px; "
    "padding: 2px 6px; min-height: 20px;"
) % (P.ACTION_ORANGE_BG, P.ACTION_ORANGE_TEXT)


def apply_service_tile_frame(widget: QWidget) -> None:
    apply_service_tile_frame_sized(widget, TILE_SIDE_PX, TILE_SIDE_PX)


def apply_service_tile_frame_sized(widget: QWidget, width: int, height: int) -> None:
    widget.setObjectName("CalcServiceTile")
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setAutoFillBackground(False)
    widget.setStyleSheet(_service_tile_qss(P.TILE_BORDER_IDLE) + _service_tile_children_qss())
    widget.setFixedSize(int(width), int(height))


def apply_service_tile_frame_fixed_width(widget: QWidget, width: int) -> None:
    """Стиль сервисной плитки с фиксированной шириной; высота по содержимому (ZamerTile — несколько рядов полей)."""
    widget.setObjectName("CalcServiceTile")
    widget.setAttribute(Qt.WA_StyledBackground, True)
    widget.setAutoFillBackground(False)
    widget.setStyleSheet(_service_tile_qss(P.TILE_BORDER_IDLE) + _service_tile_children_qss())
    widget.setFixedWidth(int(width))
    widget.setMinimumHeight(int(TILE_SIDE_PX))


def set_service_tile_border_used(widget: QWidget, used: bool) -> None:
    """Рамка «чёрная» по умолчанию / «активная» если в блоке есть выбор."""
    if widget.objectName() != "CalcServiceTile":
        return
    b = P.TILE_BORDER_USED if used else P.TILE_BORDER_IDLE
    widget.setStyleSheet(_service_tile_qss(b) + _service_tile_children_qss())


def style_tile_header(label: QLabel) -> None:
    label.setAlignment(Qt.AlignCenter)
    label.setAttribute(Qt.WA_StyledBackground, True)
    label.setStyleSheet(HEADER_LABEL_QSS)


def style_cost_label(label: QLabel) -> None:
    label.setAttribute(Qt.WA_StyledBackground, True)
    label.setStyleSheet(COST_LABEL_QSS)


def style_orange_action_button(btn: QPushButton) -> None:
    btn.setStyleSheet(ORANGE_ACTION_QSS)


COMPACT_BTN_QSS = "font-weight: bold; font-size: 8px; padding: 2px 6px; color: %s;" % P.TILE_TEXT


def style_compact_button(btn: QPushButton) -> None:
    btn.setStyleSheet(COMPACT_BTN_QSS)
