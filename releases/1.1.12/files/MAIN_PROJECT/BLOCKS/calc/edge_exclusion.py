# -*- coding: utf-8 -*-
"""
Взаимоисключение по сторонам (Streamlit «обработанные стороны»):
на одной стороне полировка не сочетается с шлифовкой или фацетом.
Шлифовка и фацет на одной стороне могут сочетаться — не трогаем друг друга.
"""

# Ключ стороны → индекс чекбокса полировки/шлифовки (как в polirovka_frame / shlifovka)
_SIDE_TO_CB = {"Верх": 1, "Лево": 2, "Право": 3, "Низ": 4}


def _facet_combo(app, side_ru: str):
    m = {"Верх": app.facet.top, "Низ": app.facet.bot, "Лево": app.facet.left, "Право": app.facet.right}
    return m[side_ru]


def _edge_exclusion_applies(app) -> bool:
    """Формы, где в UI заданы стороны «Верх/Низ/Лево/Право» (как в Streamlit для прямоугольника и аналогов)."""
    return app.glass.combo_shape.currentText() in ("Прямоугольник", "Трапеция", "Треугольник")


def clear_grind_on_side(app, side_ru: str) -> None:
    idx = _SIDE_TO_CB[side_ru]
    cb = app.shlifovka.checkboxes[idx]
    cb.blockSignals(True)
    cb.setChecked(False)
    cb.blockSignals(False)


def clear_polish_on_side(app, side_ru: str) -> None:
    idx = _SIDE_TO_CB[side_ru]
    cb = app.polirovka.checkboxes[idx]
    cb.blockSignals(True)
    cb.setChecked(False)
    cb.blockSignals(False)


def clear_facet_on_side(app, side_ru: str) -> None:
    c = _facet_combo(app, side_ru)
    c.blockSignals(True)
    c.setCurrentText("--")
    c.blockSignals(False)


def on_polish_side_toggled(app, side_ru: str, checked: bool) -> None:
    if not _edge_exclusion_applies(app) or not checked:
        return
    clear_grind_on_side(app, side_ru)
    clear_facet_on_side(app, side_ru)


def on_grind_side_toggled(app, side_ru: str, checked: bool) -> None:
    if not _edge_exclusion_applies(app) or not checked:
        return
    clear_polish_on_side(app, side_ru)


def on_facet_side_changed(app, side_ru: str) -> None:
    if not _edge_exclusion_applies(app):
        return
    c = _facet_combo(app, side_ru)
    t = c.currentText()
    mm = 0
    if t != "--" and t.isdigit():
        mm = int(t)
    if mm <= 0:
        return
    clear_polish_on_side(app, side_ru)


def after_polish_select_all(app, all_on: bool) -> None:
    """После «НА ВСЕ» полировки: если все включены — снять шлифовку и фацет по всем сторонам."""
    if not _edge_exclusion_applies(app) or not all_on:
        return
    for s in _SIDE_TO_CB:
        clear_grind_on_side(app, s)
        clear_facet_on_side(app, s)


def after_grind_select_all(app, all_on: bool) -> None:
    if not _edge_exclusion_applies(app) or not all_on:
        return
    for s in _SIDE_TO_CB:
        clear_polish_on_side(app, s)


def after_facet_fill_all(app) -> None:
    """Кнопка «НА ВСЕ» у фацета — снимаем только полировку по всем сторонам."""
    if not _edge_exclusion_applies(app):
        return
    for s in _SIDE_TO_CB:
        clear_polish_on_side(app, s)
