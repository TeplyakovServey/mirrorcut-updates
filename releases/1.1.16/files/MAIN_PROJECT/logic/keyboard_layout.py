# -*- coding: utf-8 -*-
"""Преобразование латиницы (QWERTY) в кириллицу (ЙЦУКЕН) при вводе в русских полях."""
from __future__ import annotations

_EN_TO_RU = str.maketrans(
    "qwertyuiop[]asdfghjkl;'zxcvbnm,./QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?",
    "йцукенгшщзхъфывапролджэячсмитьбю.ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,",
)


def en_qwerty_to_ru(text: str) -> str:
    if not text:
        return text
    return text.translate(_EN_TO_RU)


def normalize_cyrillic_search(text: str) -> str:
    """Если в строке только латиница/цифры — пробуем раскладку RU."""
    s = (text or "").strip()
    if not s:
        return s
    has_cyr = any("\u0400" <= ch <= "\u04FF" for ch in s)
    if has_cyr:
        return s
    if any(ch.isalpha() for ch in s):
        return en_qwerty_to_ru(s)
    return s


def client_search_prefix(text: str) -> str:
    """Префикс для поиска клиента: EN QWERTY → RU ЙЦУКЕН."""
    return normalize_cyrillic_search(text or "").strip()


def client_search_prefix_variants(text: str) -> tuple:
    """Варианты префикса для поиска: нормализованный и как введено (без дублей)."""
    raw = (text or "").strip()
    if not raw:
        return ()
    out = []
    for s in (client_search_prefix(raw), raw):
        k = s.strip().lower()
        if k and k not in out:
            out.append(k)
    return tuple(out)


def install_client_search_layout_helper(edit, on_changed=None) -> None:
    """QLineEdit: EN QWERTY → RU при вводе; опциональный callback после нормализации."""

    def _norm():
        raw = edit.text()
        norm = normalize_cyrillic_search(raw)
        if norm == raw:
            return
        pos = edit.cursorPosition()
        edit.blockSignals(True)
        edit.setText(norm)
        edit.setCursorPosition(min(pos, len(norm)))
        edit.blockSignals(False)
        if on_changed is not None:
            on_changed(norm)

    edit.textChanged.connect(_norm)
