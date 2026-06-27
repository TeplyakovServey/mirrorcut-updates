# -*- coding: utf-8 -*-
"""Геометрия изделий: как в CALC_WINDOWS/test.py + трапеция."""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

SHEET_W, SHEET_H = 2250, 3210


def check_fit(w: int, h: int) -> bool:
    fits_normal = (w <= SHEET_W) and (h <= SHEET_H)
    fits_rotated = (h <= SHEET_W) and (w <= SHEET_H)
    return fits_normal or fits_rotated


def minimal_bounding_box_dims_triangle(a: float, b: float, c: float, angle_step_deg: float = 1.0) -> Tuple[float, float, float]:
    """Минимальный охватывающий прямоугольник для треугольника со сторонами a,b,c. Возвращает (width_mm, height_mm, area_m2)."""
    cos_angle = (a ** 2 + c ** 2 - b ** 2) / (2 * a * c)
    cos_angle = max(-1.0, min(1.0, cos_angle))
    angle = math.acos(cos_angle)
    x = a * math.cos(angle)
    y = a * math.sin(angle)
    triangle = [(0, 0), (c, 0), (x, y)]
    min_area = float("inf")
    best_w = best_h = 0.0
    step = int(angle_step_deg)
    for angle_deg in range(0, 181, max(1, step)):
        theta = math.radians(angle_deg)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        rotated = [(px * cos_t - py * sin_t, px * sin_t + py * cos_t) for px, py in triangle]
        xs = [p[0] for p in rotated]
        ys = [p[1] for p in rotated]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        area = width * height
        if area < min_area:
            min_area = area
            best_w = width
            best_h = height
    area_m2 = max(0.0001, best_w * best_h / 1_000_000)
    return best_w, best_h, area_m2


def triangle_exists(a: int, b: int, c: int) -> bool:
    return a + b > c and a + c > b and b + c > a


def trapezoid_iso_edges(b_bottom: int, b_top: int, h: int) -> Tuple[int, int, float, float]:
    """Равнобедренная трапеция: нижнее b_bottom, верхнее b_top, высота h. Возвращает (низ, верх, бок, бок) длины в мм."""
    leg = math.sqrt(h * h + ((b_bottom - b_top) / 2.0) ** 2)
    return b_bottom, b_top, leg, leg


def compute_shape_metrics(
    shape: str,
    values: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    values: поля в зависимости от формы.
    Возвращает словарь с периметр_мм, площадь_м2, bbox_perimeter_mm, bbox_area_m2, ширина/высота для selected.
    """
    q = int(values.get("quantity") or 1)
    if shape == "Прямоугольник":
        w, h = int(values["width"]), int(values["height"])
        p = 2 * (w + h)
        a = (w * h) / 1_000_000
        return {
            "Периметр (мм)": p,
            "Площадь (м²)": a,
            "bbox_perimeter_mm": p,
            "bbox_area_m2": a,
            "Ширина (мм)": w,
            "Высота (мм)": h,
            "Количество (шт)": q,
            "Общий периметр (мм)": p * q,
            "Общая площадь (м²)": math.ceil(a * q * 100) / 100,
        }
    if shape == "Овал":
        w, h = int(values["width"]), int(values["height"])
        p = round(math.pi * (3 * (w + h) / 2 - math.sqrt((3 * w + h) * (w + 3 * h) / 4)))
        a = (math.pi * (w / 2) * (h / 2)) / 1_000_000
        bp = 2 * (w + h)
        ba = (w * h) / 1_000_000
        return {
            "Периметр (мм)": p,
            "Площадь (м²)": a,
            "bbox_perimeter_mm": bp,
            "bbox_area_m2": ba,
            "Ширина (мм)": w,
            "Высота (мм)": h,
            "Количество (шт)": q,
            "Общий периметр (мм)": p * q,
            "Общая площадь (м²)": math.ceil(a * q * 100) / 100,
        }
    if shape == "Круг":
        d = int(values["diameter"])
        p = math.ceil(math.pi * d)
        a = ((d / 2) ** 2 * math.pi) / 1_000_000
        bp = 4 * d
        ba = (d * d) / 1_000_000
        return {
            "Периметр (мм)": p,
            "Площадь (м²)": a,
            "bbox_perimeter_mm": bp,
            "bbox_area_m2": ba,
            "Диаметр (мм)": d,
            "Ширина (мм)": d,
            "Высота (мм)": d,
            "Количество (шт)": q,
            "Общий периметр (мм)": p * q,
            "Общая площадь (м²)": math.ceil(a * q * 100) / 100,
        }
    if shape == "Треугольник":
        a, b, c = int(values["a"]), int(values["b"]), int(values["c"])
        if not triangle_exists(a, b, c):
            return None
        p = a + b + c
        s = p / 2
        area_mm2 = math.sqrt(max(0, s * (s - a) * (s - b) * (s - c)))
        a_m2 = area_mm2 / 1_000_000
        bw, bh, _ = minimal_bounding_box_dims_triangle(float(a), float(b), float(c))
        bp = 2 * (bw + bh)
        ba = (bw * bh) / 1_000_000
        return {
            "Периметр (мм)": p,
            "Площадь (м²)": a_m2,
            "bbox_perimeter_mm": bp,
            "bbox_area_m2": ba,
            "Сторона A (мм)": a,
            "Сторона B (мм)": b,
            "Сторона C (мм)": c,
            "Ширина (мм)": int(round(bw)),
            "Высота (мм)": int(round(bh)),
            "Количество (шт)": q,
            "Общий периметр (мм)": p * q,
            "Общая площадь (м²)": math.ceil(a_m2 * q * 100) / 100,
        }
    if shape == "Трапеция":
        b_niz = int(values["b_bottom"])
        b_verh = int(values["b_top"])
        h = int(values["height_trap"])
        b_bottom = max(b_niz, b_verh)
        b_top = min(b_niz, b_verh)
        bottom_e, top_e, leg, _ = trapezoid_iso_edges(b_bottom, b_top, h)
        p = int(round(bottom_e + top_e + 2 * leg))
        a_m2 = ((bottom_e + top_e) / 2.0 * h) / 1_000_000
        bw, bh = max(b_bottom, b_top), h
        bp = 2 * (bw + bh)
        ba = (bw * bh) / 1_000_000
        return {
            "Периметр (мм)": p,
            "Площадь (м²)": a_m2,
            "bbox_perimeter_mm": bp,
            "bbox_area_m2": ba,
            "Ширина (мм)": bw,
            "Высота (мм)": bh,
            "Трапеция низ (мм)": b_niz,
            "Трапеция верх (мм)": b_verh,
            "Трапеция высота (мм)": h,
            "Кромка верх (мм)": int(round(top_e)),
            "Кромка низ (мм)": int(round(bottom_e)),
            "Кромка лево (мм)": int(round(leg)),
            "Кромка право (мм)": int(round(leg)),
            "Количество (шт)": q,
            "Общий периметр (мм)": p * q,
            "Общая площадь (м²)": math.ceil(a_m2 * q * 100) / 100,
        }
    if shape == "Сложная фигура":
        w, h = int(values["width"]), int(values["height"])
        p = int(round((w + h) * 2 * 1.5))
        a = (w * h) / 1_000_000
        bp = 2 * (w + h)
        ba = a
        return {
            "Периметр (мм)": p,
            "Площадь (м²)": a,
            "bbox_perimeter_mm": bp,
            "bbox_area_m2": ba,
            "Ширина (мм)": w,
            "Высота (мм)": h,
            "Количество (шт)": q,
            "Общий периметр (мм)": p * q,
            "Общая площадь (м²)": math.ceil(a * q * 100) / 100,
        }
    return None


def izd_area_m2_for_tariff(izd: dict) -> Optional[float]:
    """Площадь для тарифов упаковки/плёнки: при qty>1 — суммарная."""
    try:
        q = max(1, int(izd.get("Количество (шт)") or 1))
    except (TypeError, ValueError):
        q = 1
    if q > 1:
        v = izd.get("Общая площадь (м²)")
        if v is not None:
            try:
                a = float(v)
                if a > 0:
                    return a
            except (TypeError, ValueError):
                pass
    v = izd.get("Площадь (м²)")
    if v is not None:
        try:
            a = float(v)
            if a > 0:
                return a * q if q > 1 else a
        except (TypeError, ValueError):
            pass
    return None


def izd_perimeter_mm_for_display(izd: dict) -> Optional[float]:
    try:
        q = max(1, int(izd.get("Количество (шт)") or 1))
    except (TypeError, ValueError):
        q = 1
    if q > 1:
        v = izd.get("Общий периметр (мм)")
        if v is not None:
            try:
                p = float(v)
                if p > 0:
                    return p
            except (TypeError, ValueError):
                pass
    v = izd.get("Периметр (мм)")
    if v is not None:
        try:
            p = float(v)
            return p * q if q > 1 else p
        except (TypeError, ValueError):
            pass
    return None


def material_area_m2_for_price(shape: str, params: Dict[str, Any]) -> float:
    """Площадь м² для строки цены материала (как в test.py)."""
    if shape in ("Прямоугольник", "Овал"):
        w = params.get("Ширина (мм)") or 0
        h = params.get("Высота (мм)") or 0
        return w * h / 1_000_000
    if shape == "Круг":
        d = params.get("Диаметр (мм)") or 0
        return (d * d) / 1_000_000
    if shape == "Треугольник":
        a = params.get("Сторона A (мм)")
        b = params.get("Сторона B (мм)")
        c = params.get("Сторона C (мм)")
        if not all([a, b, c]):
            return 0.0
        try:
            bw, bh, am = minimal_bounding_box_dims_triangle(float(a), float(b), float(c))
            return am
        except Exception:
            return 0.0
    if shape == "Трапеция":
        return float(params.get("Площадь (м²)") or 0)
    if shape == "Сложная фигура":
        w = params.get("Ширина (мм)") or 0
        h = params.get("Высота (мм)") or 0
        return w * h / 1_000_000
    return 0.0


def shape_material_coefficient(shape: str) -> Tuple[float, str]:
    if shape == "Треугольник":
        return 1.25, "Треугольник +25%"
    if shape in ("Круг", "Овал"):
        return 1.3, "%s +30%%" % shape
    if shape == "Сложная фигура":
        return 1.75, "Сложная фигура +75%"
    if shape == "Трапеция":
        return 1.3, "Трапеция +30% (как овал)"
    return 1.0, ""
