# -*- coding: utf-8 -*-
"""Подписи вершин для прямоугольника / трапеции / треугольника (как в prepair_angle)."""
from __future__ import annotations

from typing import List

# Порядок как в elements.prepair_angle.RECT_CORNERS
RECT_TRAP_CORNERS: List[str] = [
    "Верхний левый",
    "Нижний левый",
    "Верхний правый",
    "Нижний правый",
]

TRI_CORNERS_KEYS: List[str] = ["Угол А", "Угол B", "Угол C"]

_RECT_WORD = {
    "Верхний левый": "верхний левый",
    "Верхний правый": "верхний правый",
    "Нижний левый": "нижний левый",
    "Нижний правый": "нижний правый",
}

# Трапеция: A–D против часовой от верхнего левого
_TRAP_VERTEX = {
    "Верхний левый": "A",
    "Верхний правый": "B",
    "Нижний правый": "C",
    "Нижний левый": "D",
}


def corner_sort_keys(shape: str) -> List[str]:
    if shape == "Треугольник":
        return list(TRI_CORNERS_KEYS)
    if shape in ("Прямоугольник", "Трапеция"):
        return list(RECT_TRAP_CORNERS)
    return []


def vertex_display(shape: str, corner_key: str) -> str:
    """Краткая подпись вершины для UI и строк прайса."""
    if shape == "Прямоугольник":
        return _RECT_WORD.get(corner_key, corner_key)
    if shape == "Трапеция":
        letter = _TRAP_VERTEX.get(corner_key, "?")
        human = _RECT_WORD.get(corner_key, corner_key)
        return "Угол %s (%s)" % (letter, human)
    if shape == "Треугольник":
        if corner_key == "Угол А":
            return "Вершина A"
        if corner_key == "Угол B":
            return "Вершина B"
        if corner_key == "Угол C":
            return "Вершина C"
        return corner_key
    return corner_key
