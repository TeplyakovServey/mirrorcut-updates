# -*- coding: utf-8 -*-
"""Сводка просчёта для окна «модель» и PDF: строки услуг и итог по данным selected."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from calc.corner_labels import corner_sort_keys, vertex_display
from calc.corner_rounding import parse_thickness_mm
from calc.db_postgres import fetch_drilling_price_rows, get_raw_connection
from calc.holes import compute_holes_line_details
from calc.pricing import apply_template_surcharge_to_material_cost, compute_material_cost


def _i(v: Any) -> int:
    if v is None:
        return 0
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def _edge_lengths_mm(shape: str, izd: Dict[str, Any]) -> Dict[str, int]:
    w = _i(izd.get("Ширина (мм)"))
    h = _i(izd.get("Высота (мм)"))
    a = _i(izd.get("Сторона A (мм)"))
    b = _i(izd.get("Сторона B (мм)"))
    c = _i(izd.get("Сторона C (мм)"))
    tv = _i(izd.get("Кромка верх (мм)"))
    tb = _i(izd.get("Кромка низ (мм)"))
    tl = _i(izd.get("Кромка лево (мм)"))
    tr = _i(izd.get("Кромка право (мм)"))
    if shape in ("Прямоугольник",):
        return {"Верх": w, "Низ": w, "Лево": h, "Право": h}
    if shape == "Трапеция":
        return {"Верх": tv, "Низ": tb, "Лево": tl, "Право": tr}
    if shape == "Треугольник":
        return {"Верх": a, "Лево": b, "Право": c, "Низ": 0}
    if shape in ("Круг", "Овал", "Сложная фигура"):
        per = _i(izd.get("Периметр (мм)"))
        return {"Кромка": max(per, 0)}
    return {}


def _ru_side(nm: str) -> str:
    m = {
        "Верх": "верх",
        "Низ": "низ",
        "Лево": "лево",
        "Право": "право",
        "Кромка": "кромка",
    }
    return m.get(nm, nm)


def _fmt_polish_or_grind_detail(
    shape: str,
    izd: Dict[str, Any],
    sides: Dict[str, Any],
    *,
    facet_mm: Optional[Dict[str, int]] = None,
) -> str:
    lens = _edge_lengths_mm(shape, izd)
    parts: List[str] = []
    order_keys = ["Верх", "Низ", "Лево", "Право", "Кромка"]
    for k in order_keys:
        if not sides.get(k):
            continue
        mm_edge = int(lens.get(k) or 0)
        if facet_mm is not None:
            fm = int(facet_mm.get(k) or 0)
            if fm > 0:
                parts.append("%s (%s мм)" % (_ru_side(k), fm))
        elif mm_edge > 0:
            parts.append("%s (%s мм)" % (_ru_side(k), mm_edge))
        else:
            parts.append(_ru_side(k))
    return ", ".join(parts)


def _fmt_facet_detail(shape: str, fc: Dict[str, Any], izd: Dict[str, Any]) -> str:
    if shape in ("Прямоугольник", "Трапеция"):
        mm = {
            "Верх": int(fc.get("Top") or 0),
            "Низ": int(fc.get("Bottom") or 0),
            "Лево": int(fc.get("Left") or 0),
            "Право": int(fc.get("Right") or 0),
        }
        sides_on = {k: v > 0 for k, v in mm.items()}
        return _fmt_polish_or_grind_detail(shape, izd, sides_on, facet_mm=mm)
    if shape == "Треугольник":
        t = int(fc.get("Top") or 0)
        if t > 0:
            return "фацет %s мм (периметр по расчёту)" % t
    return ""


def _fmt_corners_detail(
    shape: str,
    rb: Dict[str, Any],
    cb: Dict[str, Any],
    *,
    include_round: bool = True,
    include_cut: bool = True,
) -> str:
    bits: List[str] = []
    vals = rb.get("Значения") if isinstance(rb.get("Значения"), dict) else {}
    if include_round and rb.get("Включено") and vals:
        for k in corner_sort_keys(shape) or []:
            rmm = int(vals.get(k) or 0)
            if rmm > 0:
                bits.append("%s: скругление %s мм" % (vertex_display(shape, k), rmm))
    cuts = cb.get("Углы") if isinstance(cb.get("Углы"), dict) else {}
    if include_cut and cb.get("Включено") and cuts:
        for k in corner_sort_keys(shape) or []:
            if cuts.get(k):
                bits.append("%s: срез" % vertex_display(shape, k))
    return "; ".join(bits)


def _fmt_oplata_note(label: str, data: Dict[str, Any]) -> str:
    op = (data.get("Оплата") or "").strip()
    if not op or op == "—" or op.lower() == "не указано":
        return ""
    return "%s: %s" % (label, op)


def _rub_block_all(block: Dict[str, Any], *keys: str) -> int:
    for k in keys:
        if k in block and block[k] is not None:
            n = _i(block[k])
            if n:
                return n
    return 0


def material_order_rub(selected: Dict[str, Any]) -> int:
    mc = compute_material_cost(selected)
    if not mc:
        return 0
    izd = selected.get("Параметры изделия") or {}
    tpl_on = bool(izd.get("Изготовление по шаблону"))
    tpl_pct = izd.get("Процент шаблон (%)")
    tpl_bad = False
    if tpl_on:
        try:
            p0 = int(tpl_pct)
            if p0 < 30 or p0 > 70:
                tpl_bad = True
        except (TypeError, ValueError):
            tpl_bad = True
    mc_use, _ = apply_template_surcharge_to_material_cost(mc, tpl_on and not tpl_bad, tpl_pct)
    if not mc_use:
        return 0
    return _i(mc_use.get("Общая стоимость (₽)"))


def material_breakdown_order(selected: Dict[str, Any]) -> Dict[str, int]:
    """
    Разбивка материала на базу и надбавки первого блока:
    - база (форма + минимальная площадь + подгонка если выбрана, но без шаблона)
    - наценка за шаблон
    - итог по материалу (без закалки)
    """
    out = {
        "base_all": 0,
        "fit_add_all": 0,
        "template_add_all": 0,
        "material_total_all": 0,
    }
    matp = selected.get("Параметры материала") or {}
    # Предпочитаем уже рассчитанную в MainApp разбивку (чтобы совпадала с UI один в один).
    base_cached = _i(matp.get("Стоимость материала за все изделия (база)"))
    fit_cached = _i(matp.get("Подгонка за все изделия"))
    no_tpl_cached = _i(matp.get("Стоимость материала за все изделия (без шаблона)"))
    total_cached = _i(matp.get("Стоимость материала за все изделия"))
    if total_cached:
        out["base_all"] = base_cached
        out["fit_add_all"] = fit_cached
        out["material_total_all"] = total_cached
        out["template_add_all"] = max(0, total_cached - max(0, no_tpl_cached))
        return out

    mc = compute_material_cost(selected)
    if not mc:
        return out
    base_all = _i(mc.get("Общая стоимость (₽)"))
    izd = selected.get("Параметры изделия") or {}
    tpl_on = bool(izd.get("Изготовление по шаблону"))
    tpl_pct = izd.get("Процент шаблон (%)")
    tpl_bad = False
    if tpl_on:
        try:
            p0 = int(tpl_pct)
            if p0 < 30 or p0 > 70:
                tpl_bad = True
        except (TypeError, ValueError):
            tpl_bad = True
    mc_use, _ = apply_template_surcharge_to_material_cost(mc, tpl_on and not tpl_bad, tpl_pct)
    total_all = _i((mc_use or {}).get("Общая стоимость (₽)"))
    out["base_all"] = base_all
    out["material_total_all"] = total_all
    out["template_add_all"] = max(0, total_all - base_all)
    return out


def holes_order_rub(
    selected: Dict[str, Any],
    conn,
    drilling_rows: Optional[List[Any]] = None,
) -> int:
    izd = selected.get("Параметры изделия") or {}
    matp = selected.get("Параметры материала") or {}
    holes = selected.get("Отверстия") or []
    th = parse_thickness_mm(matp.get("Толщина (мм)"))
    tempered = bool(matp.get("Закалка"))
    q = max(1, _i(izd.get("Количество (шт)") or 1))
    if not holes or th < 4:
        return 0
    drill_rows = drilling_rows if drilling_rows is not None else fetch_drilling_price_rows(conn=conn)
    _d, _s, _m, final_h, _qs = compute_holes_line_details(drill_rows, holes, th, tempered)
    return int(final_h * q)


def srochno_adjustment_rub(si: Dict[str, Any], subtotal_before: int) -> int:
    """Фиксированные ₽ и % от промежуточной суммы (как логика скидки/наценки в интерфейсе)."""
    typ = (si.get("Тип изменения цены") or "").strip()
    sign = -1 if typ == "Скидка" else 1
    amt = 0
    r = si.get("Рубли")
    if r is not None:
        try:
            amt += sign * int(r)
        except (TypeError, ValueError):
            pass
    p = si.get("Проценты")
    if p is not None:
        try:
            pf = float(p)
            amt += sign * int(round(subtotal_before * pf / 100.0))
        except (TypeError, ValueError):
            pass
    return amt


def collect_line_items(
    selected: Dict[str, Any],
    main_app: Any,
    *,
    conn=None,
    drilling_rows_cached: Optional[List[Any]] = None,
) -> Tuple[List[Tuple[str, int, str]], int, List[str]]:
    """
    Возвращает: [(название, сумма_по_заказу, детали_кратко)], итог, предупреждения.
    Суммы — по заказу (все изделия), где применимо.
    conn — если передан и не закрывается здесь (переиспользование между изделиями в одном окне).
    drilling_rows_cached — результат fetch_drilling_price_rows для conn (одна выборка на несколько изделий).
    """
    warns: List[str] = []
    rows: List[Tuple[str, int, str]] = []
    owns_conn = conn is None
    if owns_conn:
        conn = get_raw_connection()
    try:

        mb = material_breakdown_order(selected)
        mr = int(mb.get("material_total_all") or 0)
        if mr:
            matp = selected.get("Параметры материала") or {}
            mt = matp.get("Тип материала") or "—"
            var = matp.get("Цвет / Вариант") or ""
            th = matp.get("Толщина (мм)")
            mat_note = "%s, %s мм — %s" % (mt, th, var)
            base_mat = int(mb.get("base_all") or 0)
            fit_add = int(mb.get("fit_add_all") or 0)
            tpl_add = int(mb.get("template_add_all") or 0)
            if base_mat:
                mat_title = "%s · %s · %s мм" % (
                    mt,
                    var or "—",
                    th if th is not None else "—",
                )
                rows.append(
                    (
                        mat_title,
                        base_mat,
                        "стоимость материала",
                    )
                )
            if fit_add:
                rows.append(
                    (
                        "Подгонка размеров",
                        fit_add,
                        "+50% к материалу",
                    )
                )
            if tpl_add:
                rows.append(
                    (
                        "Изготовление по шаблону",
                        tpl_add,
                        "%s%% к материалу" % (selected.get("Параметры изделия") or {}).get("Процент шаблон (%)", "—"),
                    )
                )
            if not base_mat and not tpl_add:
                rows.append(
                    (
                        "Материал",
                        mr,
                        mat_note,
                    )
                )

        zak = selected.get("Закалка") or {}
        if zak.get("Нужна"):
            zr = _i(zak.get("Цена за все изделия"))
            if zr:
                rows.append(
                    (
                        "Закалка",
                        zr,
                        (zak.get("Комментарий") or "").strip(),
                    )
                )

        pod = selected.get("Подготовительные услуги") or {}
        pi = _i(pod.get("Итого (₽)"))
        if pi:
            parts = [
                str(k)
                for k, v in pod.items()
                if k != "Итого (₽)" and isinstance(v, dict) and v.get("Включено")
            ]
            rows.append(("Подготовительные услуги", pi, ", ".join(parts) or "—"))

        izd0 = selected.get("Параметры изделия") or {}
        shape0 = str(izd0.get("Форма") or "")

        rb = selected.get("Скругление углов") or {}
        cb = selected.get("Срезать угол") or {}
        if rb.get("Включено"):
            ru = _i(rb.get("Общая стоимость"))
            if ru:
                rows.append(
                    ("Скругление углов", ru, _fmt_corners_detail(shape0, rb, cb, include_round=True, include_cut=False))
                )

        if cb.get("Включено"):
            q = max(1, _i((selected.get("Параметры изделия") or {}).get("Количество (шт)") or 1))
            c1 = _i(cb.get("Цена за 1 изделие"))
            cut_all = c1 * q
            if cut_all:
                det_cut = _fmt_corners_detail(shape0, rb, cb, include_round=False, include_cut=True)
                rows.append(("Срез углов", cut_all, (cb.get("Тип") or "") + (("; " + det_cut) if det_cut else "")))

        pol = selected.get("Полировка") or {}
        if pol.get("Нужна полировка"):
            pr = _rub_block_all(pol, "Общая стоимость (₽)")
            if pr:
                sides_p = {k: bool(pol.get(k)) for k in ("Верх", "Низ", "Лево", "Право", "Кромка") if k in pol}
                det_p = _fmt_polish_or_grind_detail(shape0, izd0, sides_p)
                rows.append(("Полировка", pr, det_p))

        shl = selected.get("Шлифовка") or {}
        if shl.get("Нужна шлифовка"):
            gr = _rub_block_all(shl, "Общая стоимость (₽)")
            if gr:
                sides_g = {k: bool(shl.get(k)) for k in ("Верх", "Низ", "Лево", "Право", "Кромка") if k in shl}
                det_g = _fmt_polish_or_grind_detail(shape0, izd0, sides_g)
                rows.append(("Шлифовка", gr, det_g))

        fc = selected.get("Фацет") or {}
        if fc.get("Нужен"):
            fr = _rub_block_all(fc, "Общая стоимость (₽)")
            if fr:
                rows.append(("Фацет", fr, _fmt_facet_detail(shape0, fc, izd0)))

        if conn:
            hr = holes_order_rub(selected, conn, drilling_rows=drilling_rows_cached)
            if hr:
                nh = len(selected.get("Отверстия") or [])
                rows.append(("Отверстия", hr, "%s отв." % nh if nh else ""))

        fl = selected.get("Плёнка") or {}
        if fl.get("Использовать плёнку") and fl.get("Общая стоимость") is not None:
            pr = _i(fl.get("Общая стоимость"))
            if pr:
                rows.append(("Плёнка", pr, fl.get("Тип плёнки") or ""))

        sand = selected.get("Пескоструй") or {}
        if sand.get("Пескоструй"):
            sr = _i(sand.get("Общая стоимость"))
            if sr:
                rows.append(("Пескоструй", sr, ""))

        ph = selected.get("Фотопечать") or {}
        if ph.get("Нужна"):
            pr = _i(ph.get("Общая стоимость"))
            if pr:
                rows.append(("Фотопечать", pr, ""))

        pk = selected.get("Покраска") or {}
        if pk.get("Использовать покраску"):
            pr = _i(pk.get("Цена за все изделия"))
            if pr:
                rows.append(("Покраска", pr, pk.get("Цвет покраски") or ""))

        uf = selected.get("УФ склейка") or {}
        if uf.get("Доступно") and not uf.get("Пусто") and not uf.get("Ошибка"):
            ur = _rub_block_all(uf, "Цена за все изделия (₽)")
            if ur:
                rows.append(("УФ склейка", ur, ""))

        vz = selected.get("Вырезы") or {}
        if vz.get("Строки") and not vz.get("Ошибка"):
            vr = _rub_block_all(vz, "Цена за все изделия (₽)")
            if vr:
                rows.append(("Вырезы", vr, ""))

        fur = selected.get("Фурнитура") or {}
        if fur.get("Включено") and fur.get("id"):
            fr = _i(fur.get("За все изделия в заказе (₽)"))
            if fr:
                rows.append(("Фурнитура", fr, fur.get("Название") or fur.get("Строка") or ""))

        pack = selected.get("Упаковка") or {}
        if pack and not pack.get("Ошибка"):
            pr = _i(pack.get("Общая стоимость упаковки (₽)"))
            if pr:
                rows.append(("Упаковка", pr, ""))

        dlv = selected.get("Доставка") or {}
        if dlv.get("Активирован"):
            dd = dlv.get("Данные") or {}
            dr = dd.get("Доставка цена")
            if dr is not None:
                dr_i = _i(dr)
                if dr_i:
                    pay = _fmt_oplata_note("Оплата доставки", dd)
                    rows.append(("Доставка", dr_i, pay))

        zam = selected.get("Замер") or {}
        if zam.get("Активирован"):
            zz = zam.get("Данные") or {}
            zr = zz.get("Замер цена выезда")
            if zr is not None:
                zr_i = _i(zr)
                if zr_i:
                    payz = _fmt_oplata_note("Оплата замера", zz)
                    rows.append(("Замер (выезд)", zr_i, payz))

        dop = selected.get("Дополнительно") or {}
        ds = _i(dop.get("Сумма (₽)"))
        if not ds:
            ds = _i(selected.get("Доп. начисления вне калькулятора (₽)"))
        if ds:
            rows.append(("Доп. начисления (вручную)", ds, ""))

    finally:
        if owns_conn and conn is not None:
            conn.close()

    sub = sum(r[1] for r in rows)
    si = {}
    if main_app is not None and hasattr(main_app, "srochno"):
        try:
            si = main_app.srochno.get_info() or {}
        except Exception:
            si = {}
    if si.get("Срочность") or si.get("Тип изменения цены"):
        adj = srochno_adjustment_rub(si, sub)
        if adj:
            label = "Срочность (изм. цены)"
            rows.append((label, adj, "%s / %s" % (si.get("Срочность") or "", si.get("Тип изменения цены") or "")))
        elif si.get("Рубли") or si.get("Проценты"):
            warns.append("Срочность: укажите скидку или наценку для учёта суммы.")

    total = sum(r[1] for r in rows)
    return rows, total, warns


def build_html_summary(
    selected: Dict[str, Any],
    main_app: Any,
    *,
    conn=None,
    drilling_rows_cached: Optional[List[Any]] = None,
) -> str:
    rows, total, warns = collect_line_items(
        selected,
        main_app,
        conn=conn,
        drilling_rows_cached=drilling_rows_cached,
    )
    izd = selected.get("Параметры изделия") or {}
    matp = selected.get("Параметры материала") or {}
    shape = izd.get("Форма") or "—"
    qty = izd.get("Количество (шт)", "—")
    th_raw = matp.get("Толщина (мм)")
    th_show = th_raw if th_raw is not None else "—"
    parts = [
        "<h3>Изделие</h3>",
        "<p><b>Форма:</b> %s &nbsp;|&nbsp; <b>Кол-во:</b> %s шт.</p>" % (shape, qty),
        "<p><b>Материал:</b> %s &nbsp;|&nbsp; <b>Вариант:</b> %s &nbsp;|&nbsp; <b>Толщина:</b> %s мм</p>"
        % (matp.get("Тип материала") or "—", matp.get("Цвет / Вариант") or "—", th_show),
    ]

    parts.append("<h3>Услуги и суммы</h3><table border='1' cellspacing='0' cellpadding='4' width='100%'>")
    parts.append("<tr><th>Услуга</th><th align='right'>₽</th><th>Комментарий</th></tr>")
    for name, rub, det in rows:
        parts.append(
            "<tr><td>%s</td><td align='right'>%s</td><td>%s</td></tr>"
            % (name, rub, det.replace("<", "&lt;"))
        )
    if not rows:
        parts.append("<tr><td colspan='3' align='center'>Нет рассчитанных строк — нажмите «Рассчитать» в блоке стекла.</td></tr>")
    parts.append("</table>")
    parts.append("<p style='font-size:14pt'><b>Итого по расчёту: %s ₽</b></p>" % total)
    for w in warns:
        parts.append("<p style='color:#a60'><i>%s</i></p>" % w.replace("<", "&lt;"))
    return "\n".join(parts)
