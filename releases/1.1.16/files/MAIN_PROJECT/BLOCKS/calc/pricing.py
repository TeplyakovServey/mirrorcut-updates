# -*- coding: utf-8 -*-
"""Расчёт стоимости блоков (упрощённый порт test.py без Streamlit)."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from calc.db_postgres import (
    fetch_facet_price_rows,
    fetch_manual_edge_price,
    fetch_scalar,
    remote_db_price_int,
)
from calc.geometry import material_area_m2_for_price, shape_material_coefficient

SHORT_EDGE_MANUAL_MM = 75
# Порядок выбора «одной длинной» стороны для замены на фикс (детерминированно)
_ORDER_RECT = ["Верх", "Низ", "Лево", "Право"]
_ORDER_TRI = ["Верх", "Лево", "Право"]


def polish_coef_sides(shape: str, selected: Dict[str, Any], grind_fit: bool) -> float:
    coef = 1.5 if grind_fit else 1.0
    izd = selected.get("Параметры изделия", {})
    sides: List[float] = []
    if shape in ("Прямоугольник", "Овал", "Сложная фигура", "Трапеция"):
        w = float(izd.get("Ширина (мм)") or 0)
        h = float(izd.get("Высота (мм)") or 0)
        sides = [w, h]
    elif shape == "Круг":
        sides = [float(izd.get("Диаметр (мм)") or 0)]
    elif shape == "Треугольник":
        sides = [
            float(izd.get("Сторона A (мм)") or 0),
            float(izd.get("Сторона B (мм)") or 0),
            float(izd.get("Сторона C (мм)") or 0),
        ]
    if not sides:
        return coef
    if any(side < 120 for side in sides):
        coef *= 2
    elif any(side >= 2401 for side in sides):
        coef *= 2
    elif any(side >= 1901 for side in sides):
        coef *= 1.6
    elif any(side >= 1401 for side in sides):
        coef *= 1.3
    return coef


def _polish_grind_any_selected(sides: Optional[Dict[str, bool]]) -> bool:
    if not sides:
        return False
    return any(bool(v) for v in sides.values())


def grind_sides_all_on_for_facet_4mm(shape: str) -> Dict[str, bool]:
    """
    Устар.: раньше при фацете 4 мм включались все стороны шлифовки.
    Используйте grind_sides_from_facet_edge_mm по фактическим мм фацета.
    """
    if shape == "Треугольник":
        return {"Верх": True, "Лево": True, "Право": True, "Низ": False}
    if shape in ("Прямоугольник", "Трапеция"):
        return {"Верх": True, "Лево": True, "Право": True, "Низ": True}
    if shape in ("Круг", "Овал", "Сложная фигура"):
        return {"Кромка": True}
    return {}


def grind_sides_from_facet_edge_mm(shape: str, edge_mm: Optional[Dict[str, int]]) -> Dict[str, bool]:
    """
    Бесплатная шлифовка с фацетом 4 мм — только те стороны, где задан фацет (мм > 0).
    edge_mm: ключи «Верх», «Низ», «Лево», «Право» (как в UI фацета / шлифовки).
    """
    e = edge_mm or {}
    if shape in ("Прямоугольник", "Трапеция"):
        return {
            "Верх": max(0, int(e.get("Верх") or 0)) > 0,
            "Низ": max(0, int(e.get("Низ") or 0)) > 0,
            "Лево": max(0, int(e.get("Лево") or 0)) > 0,
            "Право": max(0, int(e.get("Право") or 0)) > 0,
        }
    if shape == "Треугольник":
        return {
            "Верх": max(0, int(e.get("Верх") or 0)) > 0,
            "Лево": max(0, int(e.get("Лево") or 0)) > 0,
            "Право": max(0, int(e.get("Право") or 0)) > 0,
            "Низ": False,
        }
    return {}


def _linear_plus_manual_rect_like(
    selected_sides: Dict[str, bool],
    lengths_ru: Dict[str, int],
    order: List[str],
    price_per_meter: float,
    coef: float,
    thickness: int,
    conn,
) -> Tuple[float, int]:
    """
    Суммарная длина (мм) для линейного счёта и фикс ручной (₽) за одну длинную выбранную сторону,
    если min ребра < 75 мм и в БД есть цена.
    """
    vals = [lengths_ru[k] for k in lengths_ru if lengths_ru.get(k, 0) > 0]
    min_len = min(vals) if vals else 10**9
    manual_rub = int(fetch_manual_edge_price(thickness, conn=conn) or 0)
    selected = [k for k in order if selected_sides.get(k) and lengths_ru.get(k, 0) > 0]
    if not selected:
        return 0.0, 0
    if min_len >= SHORT_EDGE_MANUAL_MM or manual_rub <= 0:
        return float(sum(lengths_ru[k] for k in selected)), 0
    pairs = [(k, lengths_ru[k]) for k in selected]
    max_l = max(l for _, l in pairs)
    long_keys = [k for k, ln in pairs if ln == max_l]
    replace_k = min(long_keys, key=lambda k: order.index(k) if k in order else 99)
    linear_mm = sum(lengths_ru[k] for k in selected if k != replace_k)
    return float(linear_mm), manual_rub


def compute_polish_cost(
    selected: Dict[str, Any], polish_sides: Dict[str, bool], conn=None
) -> Optional[Dict[str, Any]]:
    mat = selected.get("Параметры материала", {})
    izd = selected.get("Параметры изделия", {})
    thickness = int(mat.get("Толщина (мм)") or 0)
    shape = izd.get("Форма", "")
    perimeter = izd.get("Периметр (мм)")
    quantity = int(izd.get("Количество (шт)") or 1)
    grind_fit = bool(izd.get("Подгонка размеров"))
    if not perimeter:
        return None
    if not _polish_grind_any_selected(polish_sides):
        return None
    coef = polish_coef_sides(shape, selected, grind_fit)
    w = int(izd.get("Ширина (мм)") or 0)
    h = int(izd.get("Высота (мм)") or 0)
    a = int(izd.get("Сторона A (мм)") or 0)
    b = int(izd.get("Сторона B (мм)") or 0)
    c = int(izd.get("Сторона C (мм)") or 0)

    if shape in ("Круг", "Овал", "Сложная фигура"):
        mr = int(fetch_manual_edge_price(thickness, conn=conn) or 0)
        cost_one = int(round(mr * coef)) if mr else 0
        perimeter_m = float(perimeter) / 1000.0
        return {
            "Стоимость за изделие (₽)": cost_one,
            "Общая стоимость (₽)": cost_one * quantity,
            "Длина полировки (м)": round(perimeter_m * quantity, 3),
        }

    price_line = fetch_scalar(
        "SELECT price_per_meter FROM polirovka_price WHERE thickness_mm = %s", (thickness,), conn=conn
    )
    price_per_meter = float(remote_db_price_int(price_line, "polirovka"))

    if shape in ("Прямоугольник", "Трапеция"):
        if shape == "Прямоугольник":
            lengths_ru = {"Верх": w, "Низ": w, "Лево": h, "Право": h}
        else:
            lengths_ru = {
                "Верх": int(izd.get("Кромка верх (мм)") or 0),
                "Низ": int(izd.get("Кромка низ (мм)") or 0),
                "Лево": int(izd.get("Кромка лево (мм)") or 0),
                "Право": int(izd.get("Кромка право (мм)") or 0),
            }
        linear_mm, manual_rub = _linear_plus_manual_rect_like(
            polish_sides, lengths_ru, _ORDER_RECT, price_per_meter, coef, thickness, conn
        )
        length_m = linear_mm / 1000.0
        cost_one = int(round(length_m * price_per_meter * coef + manual_rub))
        total_m = sum(lengths_ru[k] for k in _ORDER_RECT if polish_sides.get(k)) / 1000.0
        return {
            "Стоимость за изделие (₽)": cost_one,
            "Общая стоимость (₽)": cost_one * quantity,
            "Длина полировки (м)": round(total_m * quantity, 2),
        }
    if shape == "Треугольник":
        lengths_ru = {"Верх": a, "Лево": b, "Право": c}
        linear_mm, manual_rub = _linear_plus_manual_rect_like(
            polish_sides, lengths_ru, _ORDER_TRI, price_per_meter, coef, thickness, conn
        )
        length_m = linear_mm / 1000.0
        cost_one = int(round(length_m * price_per_meter * coef + manual_rub))
        total_m = sum(lengths_ru[k] for k in _ORDER_TRI if polish_sides.get(k)) / 1000.0
        return {
            "Стоимость за изделие (₽)": cost_one,
            "Общая стоимость (₽)": cost_one * quantity,
            "Длина полировки (м)": round(total_m * quantity, 2),
        }
    return None


def grind_coef(shape: str, izd: Dict[str, Any]) -> float:
    coef = 1.0
    w = int(izd.get("Ширина (мм)") or 0)
    h = int(izd.get("Высота (мм)") or 0)
    if shape == "Прямоугольник":
        if w < 120 or h < 120:
            coef = 2
        elif w >= 2401 or h >= 2401:
            coef = 2
        elif w >= 1901 or h >= 1901:
            coef = 1.6
        elif w >= 1401 or h >= 1401:
            coef = 1.3
    elif shape == "Круг":
        d = int(izd.get("Диаметр (мм)") or 0)
        if d < 120:
            coef = 2
        elif d >= 2401:
            coef = 2
        elif d >= 1901:
            coef = 1.6
        elif d >= 1401:
            coef = 1.3
    elif shape == "Треугольник":
        a = int(izd.get("Сторона A (мм)") or 0)
        b = int(izd.get("Сторона B (мм)") or 0)
        c = int(izd.get("Сторона C (мм)") or 0)
        mx = max(a, b, c)
        mn = min(a, b, c)
        if mn < 120:
            coef = 2
        elif mx >= 2401:
            coef = 2
        elif mx >= 1901:
            coef = 1.6
        elif mx >= 1401:
            coef = 1.3
    elif shape == "Трапеция":
        w = int(izd.get("Ширина (мм)") or 0)
        h = int(izd.get("Высота (мм)") or 0)
        if min(w, h) < 120:
            coef = 2
        elif max(w, h) >= 2401:
            coef = 2
        elif max(w, h) >= 1901:
            coef = 1.6
        elif max(w, h) >= 1401:
            coef = 1.3
    return coef


def compute_grind_cost(
    selected: Dict[str, Any],
    grind_sides: Dict[str, bool],
    conn=None,
    *,
    zero_price_facet_4mm: bool = False,
) -> Optional[Dict[str, Any]]:
    mat = selected.get("Параметры материала", {})
    izd = selected.get("Параметры изделия", {})
    thickness = int(mat.get("Толщина (мм)") or 0)
    shape = izd.get("Форма", "")
    perimeter = float(izd.get("Периметр (мм)") or 0)
    quantity = int(izd.get("Количество (шт)") or 1)
    grind_fit = bool(izd.get("Подгонка размеров"))
    if not perimeter:
        return None
    if not _polish_grind_any_selected(grind_sides):
        return None
    coef = grind_coef(shape, izd)
    if grind_fit:
        coef *= 1.5
    w = int(izd.get("Ширина (мм)") or 0)
    h = int(izd.get("Высота (мм)") or 0)
    a = int(izd.get("Сторона A (мм)") or 0)
    b = int(izd.get("Сторона B (мм)") or 0)
    c = int(izd.get("Сторона C (мм)") or 0)

    if shape in ("Круг", "Овал", "Сложная фигура"):
        mr = int(fetch_manual_edge_price(thickness, conn=conn) or 0)
        cost_one = int(round(mr * coef)) if mr else 0
        total_length_m = perimeter / 1000.0
        out = {
            "Стоимость за изделие (₽)": cost_one,
            "Общая стоимость (₽)": cost_one * quantity,
            "Длина шлифовки (м)": round(total_length_m * quantity, 2),
        }
        if zero_price_facet_4mm:
            out["Стоимость за изделие (₽)"] = 0
            out["Общая стоимость (₽)"] = 0
            out["Бесплатно с фацетом 4 мм"] = True
        return out

    price_line = fetch_scalar(
        "SELECT price_per_meter FROM polirovka_price WHERE thickness_mm = %s",
        (thickness,),
        conn=conn,
    )
    price_per_meter = float(remote_db_price_int(price_line, "polirovka"))

    if shape == "Прямоугольник":
        lengths_ru = {"Верх": w, "Низ": w, "Лево": h, "Право": h}
        linear_mm, manual_rub = _linear_plus_manual_rect_like(
            grind_sides, lengths_ru, _ORDER_RECT, price_per_meter, coef, thickness, conn
        )
        total_length_m = sum(lengths_ru[k] for k in _ORDER_RECT if grind_sides.get(k)) / 1000.0
    elif shape == "Трапеция":
        lengths_ru = {
            "Верх": int(izd.get("Кромка верх (мм)") or 0),
            "Низ": int(izd.get("Кромка низ (мм)") or 0),
            "Лево": int(izd.get("Кромка лево (мм)") or 0),
            "Право": int(izd.get("Кромка право (мм)") or 0),
        }
        linear_mm, manual_rub = _linear_plus_manual_rect_like(
            grind_sides, lengths_ru, _ORDER_RECT, price_per_meter, coef, thickness, conn
        )
        total_length_m = sum(lengths_ru[k] for k in _ORDER_RECT if grind_sides.get(k)) / 1000.0
    elif shape == "Треугольник":
        lengths_ru = {"Верх": a, "Лево": b, "Право": c}
        linear_mm, manual_rub = _linear_plus_manual_rect_like(
            grind_sides, lengths_ru, _ORDER_TRI, price_per_meter, coef, thickness, conn
        )
        total_length_m = sum(lengths_ru[k] for k in _ORDER_TRI if grind_sides.get(k)) / 1000.0
    else:
        return None

    length_m = linear_mm / 1000.0
    cost_one = int(round(length_m * price_per_meter * coef + manual_rub))
    out = {
        "Стоимость за изделие (₽)": cost_one,
        "Общая стоимость (₽)": cost_one * quantity,
        "Длина шлифовки (м)": round(total_length_m * quantity, 2),
    }
    if zero_price_facet_4mm:
        out["Стоимость за изделие (₽)"] = 0
        out["Общая стоимость (₽)"] = 0
        out["Бесплатно с фацетом 4 мм"] = True
    return out


def _facet_thickness_col(thickness: int) -> Optional[str]:
    m = {4: "material_4mm", 5: "material_5mm", 6: "material_6mm", 8: "material_8mm", 10: "material_10mm"}
    return m.get(thickness)


def compute_facet_cost(
    selected: Dict[str, Any], facet_state: Dict[str, Any], conn=None
) -> Optional[Dict[str, Any]]:
    """facet_state: Top, Bottom, Left, Right int мм фацета или 0; Нужен bool."""
    if not facet_state.get("Нужен"):
        return None
    izd = selected.get("Параметры изделия", {})
    mat = selected.get("Параметры материала", {})
    shape = izd.get("Форма", "")
    thickness = int(mat.get("Толщина (мм)") or 0)
    quantity = int(izd.get("Количество (шт)") or 1)
    grind_fit = bool(izd.get("Подгонка размеров"))
    zakalka = bool(mat.get("Закалка"))
    w = int(izd.get("Ширина (мм)") or 0)
    h = int(izd.get("Высота (мм)") or 0)
    perimeter = float(izd.get("Периметр (мм)") or 0)
    tcol = _facet_thickness_col(thickness)
    if not tcol:
        return None
    rows = fetch_facet_price_rows(conn=conn)
    if not rows:
        return None

    def price_for_size(sz: int) -> float:
        if sz <= 0:
            return 0.0
        for r in rows:
            if int(r.get("facet_size") or 0) == sz:
                return float(r.get(tcol) or 0)
        return 0.0

    if shape == "Прямоугольник":
        used = [
            int(facet_state.get("Top") or 0),
            int(facet_state.get("Bottom") or 0),
            int(facet_state.get("Left") or 0),
            int(facet_state.get("Right") or 0),
        ]
        active = [s for s in used if s > 0]
        if not active:
            return {"Стоимость за изделие (₽)": 0, "Общая стоимость (₽)": 0}
        price_per_m = sum(price_for_size(s) for s in active) / len(active)
        length_m = (
            (facet_state.get("Top", 0) > 0) * w
            + (facet_state.get("Bottom", 0) > 0) * w
            + (facet_state.get("Left", 0) > 0) * h
            + (facet_state.get("Right", 0) > 0) * h
        ) / 1000.0
    elif shape == "Трапеция":
        top = int(izd.get("Кромка верх (мм)") or 0)
        bottom = int(izd.get("Кромка низ (мм)") or 0)
        left = int(izd.get("Кромка лево (мм)") or 0)
        right = int(izd.get("Кромка право (мм)") or 0)
        used = [
            int(facet_state.get("Top") or 0),
            int(facet_state.get("Bottom") or 0),
            int(facet_state.get("Left") or 0),
            int(facet_state.get("Right") or 0),
        ]
        active = [s for s in used if s > 0]
        if not active:
            return {"Стоимость за изделие (₽)": 0, "Общая стоимость (₽)": 0}
        price_per_m = sum(price_for_size(s) for s in active) / len(active)
        length_m = (
            (facet_state.get("Top", 0) > 0) * top
            + (facet_state.get("Bottom", 0) > 0) * bottom
            + (facet_state.get("Left", 0) > 0) * left
            + (facet_state.get("Right", 0) > 0) * right
        ) / 1000.0
    elif shape == "Треугольник":
        used = [int(facet_state.get("Top") or 0)]
        active = [s for s in used if s > 0]
        if not active:
            return {"Стоимость за изделие (₽)": 0, "Общая стоимость (₽)": 0}
        price_per_m = sum(price_for_size(s) for s in active) / len(active)
        length_m = float(izd.get("Периметр (мм)") or perimeter) / 1000.0
    else:
        used = [int(facet_state.get("Top") or 0)]
        active = [s for s in used if s > 0]
        if not active:
            return {"Стоимость за изделие (₽)": 0, "Общая стоимость (₽)": 0}
        price_per_m = sum(price_for_size(s) for s in active) / len(active)
        length_m = perimeter / 1000.0

    coef = 1.0
    if grind_fit:
        coef *= 1.5
    xx = [i for i in used if i != 0]
    if len(set(xx)) > 1:
        coef *= 2
    max_side = max(w, h) if w and h else max(
        int(izd.get("Сторона A (мм)") or 0),
        int(izd.get("Сторона B (мм)") or 0),
        int(izd.get("Сторона C (мм)") or 0),
    )
    if shape == "Трапеция":
        max_side = max(
            max_side,
            int(izd.get("Кромка верх (мм)") or 0),
            int(izd.get("Кромка низ (мм)") or 0),
            int(izd.get("Кромка лево (мм)") or 0),
            int(izd.get("Кромка право (мм)") or 0),
        )
    if max_side >= 2401:
        coef *= 2
    elif max_side >= 1901:
        coef *= 1.6
    elif max_side >= 1401:
        coef *= 1.3
    elif w and h and min(w, h) < 210:
        coef *= 2

    cost_one = int(round(length_m * price_per_m * coef))
    return {"Стоимость за изделие (₽)": cost_one, "Общая стоимость (₽)": cost_one * quantity}


def compute_material_cost(selected: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    mat = selected.get("Параметры материала", {})
    izd = selected.get("Параметры изделия", {})
    base_price = mat.get("Цена за м²")
    if base_price is None:
        return None
    shape = izd.get("Форма", "")
    area_m2 = material_area_m2_for_price(shape, izd)
    if area_m2 <= 0:
        return None
    eff_area = max(0.1, area_m2)
    coeff, _ = shape_material_coefficient(shape)
    price_m2 = math.ceil(float(base_price) * coeff)
    grind_fit = bool(izd.get("Подгонка размеров"))
    if grind_fit:
        price_m2 = math.ceil(price_m2 * 1.5)
    quantity = int(izd.get("Количество (шт)") or 1)
    price_one = math.ceil(price_m2 * eff_area)
    price_all = math.ceil(price_one * quantity)
    return {
        "Цена за м² (₽)": price_m2,
        "Стоимость за изделие (₽)": price_one,
        "Общая стоимость (₽)": price_all,
    }


def compute_temper_cost(selected: Dict[str, Any], conn=None) -> Optional[Dict[str, Any]]:
    """
    Стоимость закалки как отдельной услуги.
    Логика перенесена из исходного Streamlit-калькулятора:
    - база за м² из zakalka_price по толщине;
    - минимальная площадь для расчёта 0.1 м²;
    - коэффициент по форме: triangle 1.2, circle/oval 1.35, complex 1.55.
    """
    mat = selected.get("Параметры материала", {}) or {}
    izd = selected.get("Параметры изделия", {}) or {}
    if not bool(mat.get("Закалка")):
        return None
    thickness = int(mat.get("Толщина (мм)") or 0)
    if thickness <= 0:
        return None
    area_raw = izd.get("Площадь (м²)")
    try:
        area_m2 = float(area_raw)
    except (TypeError, ValueError):
        return None
    if area_m2 <= 0:
        return None
    area_use = max(0.1, area_m2)
    shape = str(izd.get("Форма") or "")
    coef = 1.0
    note = ""
    if shape == "Треугольник":
        coef = 1.2
        note = "Треугольник: +20% к закалке"
    elif shape in ("Круг", "Овал"):
        coef = 1.35
        note = "%s: +35% к закалке" % shape
    elif shape == "Сложная фигура":
        coef = 1.55
        note = "Сложная фигура: +55% к закалке"

    base = fetch_scalar(
        "SELECT price FROM zakalka_price WHERE thickness_mm = %s",
        (thickness,),
        conn=conn,
    )
    if base is None:
        return None
    base_m2 = float(remote_db_price_int(base, "zakalka"))
    if base_m2 <= 0:
        return None
    q = int(izd.get("Количество (шт)") or 1)
    one = int(math.ceil(base_m2 * area_use * coef))
    all_rub = int(math.ceil(one * q))
    return {
        "Цена закалки за м² (₽)": int(math.ceil(base_m2)),
        "Площадь для закалки (м²)": area_use,
        "Коэффициент формы (закалка)": coef,
        "Стоимость закалки за изделие (₽)": one,
        "Стоимость закалки за все изделия (₽)": all_rub,
        "Комментарий закалки": note,
    }


def apply_template_surcharge_to_material_cost(
    mc: Optional[Dict[str, Any]],
    template_on: bool,
    pct_raw: Any,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Наценка «изготовление по шаблону»: ×(1 + p/100) к суммам материала; p только 30–70.
    Возвращает (новый mc или исходный, суффикс подписи).
    """
    if not mc or not template_on:
        return mc, ""
    try:
        p = int(pct_raw)
    except (TypeError, ValueError):
        return mc, ""
    if p < 30 or p > 70:
        return mc, ""
    mult = 1.0 + p / 100.0
    out = dict(mc)
    pm = int(mc["Цена за м² (₽)"])
    p1 = int(mc["Стоимость за изделие (₽)"])
    pa = int(mc["Общая стоимость (₽)"])
    out["Цена за м² (₽)"] = int(math.ceil(pm * mult))
    out["Стоимость за изделие (₽)"] = int(math.ceil(p1 * mult))
    out["Общая стоимость (₽)"] = int(math.ceil(pa * mult))
    delta = out["Стоимость за изделие (₽)"] - p1
    suffix = " +шаблон %s%% (+%s ₽ к изд.)" % (p, delta)
    return out, suffix
