# -*- coding: utf-8 -*-
"""Пошаговые инструкции для цеха и веба: профили, этикетки, стекло, обработка."""
from __future__ import annotations

import copy
import html as html_module
import importlib.util
import os
import re
from typing import Any, Dict, List, Set, Tuple


def _mirror_root_dir() -> str:
    """Корень MIRROR_CUT (рядом с MAIN_PROJECT), откуда грузить logic/labels и logic/qr_utils."""
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _generate_labels_pdf_multi(remnants: List[Dict[str, Any]], pieces: List[Dict[str, Any]], filepath: str) -> None:
    """Печать этикеток стекла/остатков 100×50 мм — тот же модуль, что WEB_SERVICE и главное окно."""
    try:
        from logic.labels import generate_labels_pdf_multi as _fn
    except ImportError:
        p = os.path.join(_mirror_root_dir(), "logic", "labels.py")
        if not os.path.isfile(p):
            raise ImportError("Не найден logic/labels.py (%s)" % p)
        spec = importlib.util.spec_from_file_location("_mirror_cut_labels_mod", p)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(mod)
        _fn = mod.generate_labels_pdf_multi
    _fn(remnants, pieces, filepath)


def _make_profile_qr_image_fn():
    """При запуске из MAIN_PROJECT пакет logic — без qr_utils; грузим MIRROR_CUT/logic/qr_utils.py."""
    try:
        from logic.qr_utils import make_profile_qr_image

        return make_profile_qr_image
    except ImportError:
        p = os.path.join(_mirror_root_dir(), "logic", "qr_utils.py")
        if not os.path.isfile(p):
            return None
        try:
            spec = importlib.util.spec_from_file_location("_mirror_cut_qr_mod", p)
            mod = importlib.util.module_from_spec(spec)
            assert spec.loader
            spec.loader.exec_module(mod)
            return mod.make_profile_qr_image
        except Exception:
            return None

from db_main import order_status_to_ru
from logic.blocks_bundle import (
    CUT_SCHEME_CREATED,
    PAYMENT_TYPE_LABELS_RU,
    PRODUCTION_GLASS_MADE,
    parse_bundle,
)

_KIND_FACADE = "facade"
_KIND_GLASS = "glass_mirror"


def facade_assembly_unit_marked_done(
    events: List[Dict[str, Any]] | None,
    batch_id: int,
    product_index: int,
    facade_label: str,
    public_number: int,
) -> bool:
    """По журналу производства: фасад отмечен собранным (событие facade_instance_assembled)."""
    fl = str(facade_label or "").strip()
    pub = int(public_number)
    for ev in events or []:
        if str(ev.get("event_type") or "") != "facade_instance_assembled":
            continue
        d = ev.get("details_json")
        if not isinstance(d, dict):
            d = {}
        try:
            if int(d.get("facade_public_number") or 0) == pub:
                return True
        except (TypeError, ValueError):
            pass
        fcs = str(d.get("finished_code") or "").strip().upper()
        if fcs in (str(pub), "GF%s" % pub):
            return True
        if (
            int(d.get("batch_id") or 0) == int(batch_id)
            and int(d.get("product_index") or 0) == int(product_index)
            and str(d.get("facade_label") or "").strip() == fl
        ):
            return True
    return False


def glass_processing_lines(glass: dict | None) -> List[str]:
    """Краткий перечень обработок по blocks_selected внутри блока «Стекло» фасада."""
    if not isinstance(glass, dict):
        return ["Обработка: нет данных"]
    sel = glass.get("blocks_selected")
    if not isinstance(sel, dict):
        return ["Обработка: нет данных"]
    lines: List[str] = []
    pol = sel.get("Полировка") or {}
    if pol.get("Нужна полировка"):
        sides = [k for k in ("Верх", "Низ", "Лево", "Право", "Кромка") if pol.get(k)]
        lines.append("Полировка: %s" % (", ".join(sides) if sides else "да"))
    gr = sel.get("Шлифовка") or {}
    if gr.get("Нужна шлифовка"):
        sides = [k for k in ("Верх", "Низ", "Лево", "Право", "Кромка") if gr.get(k)]
        lines.append("Шлифовка: %s" % (", ".join(sides) if sides else "да"))
    fc = sel.get("Фацет") or {}
    if fc.get("Нужен"):
        vals = []
        for key, lab in (("Top", "верх"), ("Bottom", "низ"), ("Left", "лево"), ("Right", "право")):
            try:
                v = int(fc.get(key) or 0)
            except (TypeError, ValueError):
                v = 0
            if v > 0:
                vals.append("%s %s мм" % (lab, v))
        lines.append("Фацет: %s" % (", ".join(vals) if vals else "да"))
    sb = sel.get("Пескоструй") or {}
    if sb.get("Пескоструй"):
        lines.append(
            "Пескоструй: %s%s"
            % ((sb.get("Тип") or "да"), " (двухсторонний)" if sb.get("Двухсторонний") else "")
        )
    fl = sel.get("Плёнка") or {}
    if fl.get("Использовать плёнку"):
        lines.append("Плёнка: %s" % (fl.get("Тип плёнки") or "да"))
    pk = sel.get("Покраска") or {}
    if pk.get("Использовать покраску"):
        lines.append("Покраска: %s" % (pk.get("Цвет покраски") or "да"))
    ph = sel.get("Фотопечать") or {}
    if ph.get("Нужна"):
        lines.append("Фотопечать: да")
    if not lines:
        return []
    return lines


def _ru_facade_side(side: str) -> str:
    m = {"left": "левая", "right": "правая", "top": "верхняя", "bottom": "нижняя"}
    return m.get(str(side) or "", str(side or ""))


def facade_profile_instruction_lines(pl: dict) -> List[str]:
    out: List[str] = []
    for side, pf in (pl.get("Профили_по_сторонам") or {}).items():
        if not isinstance(pf, dict):
            continue
        src = pf.get("_source_stock") or {}
        lbl = src.get("label_number")
        src_len = src.get("length_mm")
        size_tail = (" длина заготовки %s мм" % int(src_len)) if src_len else ""
        kind = src.get("kind")
        if kind == "new":
            src_txt = "взять новый профиль с полки%s" % size_tail
        elif kind == "warehouse_remnant":
            src_txt = "взять остаток со склада, этикетка на брусе № %s%s" % ((lbl or src.get("stock_id") or "—"), size_tail)
        else:
            src_txt = "взять со склада (№ %s)%s" % ((lbl or src.get("stock_id") or "—"), size_tail)
        nm = (pf.get("name") or pf.get("series") or "профиль").strip()
        out.append("Сторона %s: «%s» — %s; на брус/остаток — своя этикетка склада." % (_ru_facade_side(side), nm, src_txt))
    if not out:
        out.append("Профили: не заданы в расчёте.")
    return out


def facade_fittings_instruction_lines(pl: dict) -> List[str]:
    out: List[str] = []
    hinges = pl.get("Петли") if isinstance(pl.get("Петли"), list) else []
    if hinges:
        n = sum(int((x or {}).get("quantity") or 0) for x in hinges)
        out.append("Петли: всего позиций в расчёте — %s (комплектация по спецификации петель)." % max(n, len(hinges)))
    for rec in pl.get("Присадка") or []:
        if not isinstance(rec, dict):
            continue
        side = _ru_facade_side(rec.get("сторона") or rec.get("side") or "")
        sup = rec.get("поставщик_петли") or rec.get("supplier") or ""
        holes = rec.get("отверстия") or rec.get("holes") or []
        for h in holes:
            off = h.get("отступ_мм") if isinstance(h, dict) else None
            if off is None and isinstance(h, dict):
                off = h.get("offset_mm")
            cutout = ""
            if isinstance(h, dict):
                cutout = str(
                    h.get("тип")
                    or h.get("тип_выреза")
                    or h.get("type")
                    or h.get("cutout_type")
                    or ""
                ).strip()
            out.append(
                "Присадка: сторона %s, петля %s — %s, отступ %s мм от края."
                % (
                    side,
                    sup or "—",
                    (cutout or "вырез"),
                    off if off is not None else "—",
                )
            )
    return out


def facade_glass_instruction_lines(pl: dict, *, include_fill_processing: bool = True) -> List[str]:
    g = pl.get("Стекло") if isinstance(pl.get("Стекло"), dict) else {}
    if not g:
        return ["Стекло в проёме: не выбрано."]
    bits = [
        "Наполнение проёма: %s" % (g.get("Название") or "—"),
        "Цвет: %s; толщина: %s мм"
        % (g.get("Цвет") or "—", g.get("Толщина (мм)") if g.get("Толщина (мм)") is not None else "—"),
        "Размер стекла (по расчёту): %s × %s мм"
        % (g.get("Ширина (мм)") or pl.get("Ширина_мм") or "—", g.get("Высота (мм)") or pl.get("Высота_мм") or "—"),
    ]
    src = g.get("_source_stock") or g.get("source_stock")
    if isinstance(src, dict):
        lb = src.get("label_number") or src.get("stock_id")
        if lb:
            bits.append("Материал наполнения: этикетка склада/листа № %s (если назначено в программе)." % lb)
    if include_fill_processing:
        bits.extend(["Обработка наполнения: %s" % x for x in glass_processing_lines(g)])
    return bits


def facade_seal_screw_lines(pl: dict) -> List[str]:
    seal = pl.get("Уплотнитель") or {}
    scr = pl.get("Винты") or {}
    return [
        "Уплотнитель по периметру стекла: %s." % ((seal.get("Цвет") if isinstance(seal, dict) else None) or "прозрачный"),
        "Винты крепления стекла: цвет %s." % ((scr.get("Цвет") if isinstance(scr, dict) else None) or "серебро"),
    ]


def _facade_profile_is_prisma(pf: dict) -> bool:
    s = str((pf or {}).get("series") or "").upper()
    n = str((pf or {}).get("name") or "").upper()
    return "PRISMA" in s or "PRISMA" in n


def facade_corner_connector_lines(pl: dict) -> List[str]:
    """Уголки F3-021 / F3-031 по углам рамы (как в калькуляторе фасада)."""
    prof = norm_facade_profiles_by_side(pl)
    top, bot, left, right = prof.get("top"), prof.get("bottom"), prof.get("left"), prof.get("right")
    qty: Dict[str, int] = {"F3-021": 0, "F3-031": 0}

    def _inc(a: dict | None, b: dict | None) -> None:
        if not (a and b and isinstance(a, dict) and isinstance(b, dict)):
            return
        code = "F3-031" if (_facade_profile_is_prisma(a) or _facade_profile_is_prisma(b)) else "F3-021"
        qty[code] = int(qty.get(code, 0)) + 1

    _inc(top, left)
    _inc(bot, left)
    _inc(top, right)
    _inc(bot, right)
    lines: List[str] = []
    for k in ("F3-021", "F3-031"):
        v = int(qty.get(k) or 0)
        if v:
            lines.append("Угловой соединитель %s: %s шт. (по углам рамы)" % (k, v))
    return lines


def _cut_piece_mm_matches_glass(gw: int, gh: int, w: int, h: int, tol: int = 8) -> bool:
    """Размеры выкроя на листе могут быть с припуском под кромку относительно расчёта."""
    if gw < 1 or gh < 1 or w < 1 or h < 1:
        return False

    def _fit(a: int, b: int, x: int, y: int) -> bool:
        return abs(a - x) <= tol and abs(b - y) <= tol

    return _fit(gw, gh, w, h) or _fit(gw, gh, h, w)


def resolve_glass_k_index_for_facade_instance(
    order_id: int,
    facade_label: str,
    gfac: dict,
    pl: dict,
) -> int | None:
    """Номер этикетки стекла K… по схеме раскроя (порядок выкроев), не общий k_number заказа."""
    from db import models as db_models

    oid = int(order_id)
    if oid < 1:
        return None
    fl = str(facade_label or "").strip()
    short = fl.split("_")[0].strip() if fl and "_" in fl else fl
    try:
        gw = int(float(gfac.get("Ширина (мм)") or 0))
        gh = int(float(gfac.get("Высота (мм)") or 0))
    except (TypeError, ValueError):
        gw, gh = 0, 0
    if gw < 1:
        try:
            gw = int(float(pl.get("Ширина_мм") or 0))
        except (TypeError, ValueError):
            gw = 0
    if gh < 1:
        try:
            gh = int(float(pl.get("Высота_мм") or 0))
        except (TypeError, ValueError):
            gh = 0
    rows = db_models.get_cut_results(oid) or []
    k = 0
    hits: List[int] = []
    for cr in rows:
        lay = cr.get("layout") if isinstance(cr.get("layout"), dict) else {}
        for piece in lay.get("pieces") or []:
            if not isinstance(piece, dict):
                continue
            k += 1
            rec = (piece.get("recipient") or piece.get("recipient_text") or "").strip()
            ql = (piece.get("quantity_label") or "").strip()
            blob = "%s %s" % (rec, ql)
            if fl and (fl in rec or fl in ql or fl in blob or (short and short in blob)):
                hits.append(k)
                continue
            if gw > 0 and gh > 0:
                try:
                    w = int(piece.get("w") or 0)
                    h = int(piece.get("h") or 0)
                except (TypeError, ValueError):
                    w, h = 0, 0
                if _cut_piece_mm_matches_glass(gw, gh, w, h):
                    low = blob.lower()
                    if "фасад" in low or "facade" in low or "рам" in low:
                        hits.append(k)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1 and fl:
        refined: List[int] = []
        k2 = 0
        for cr in rows:
            lay = cr.get("layout") if isinstance(cr.get("layout"), dict) else {}
            for piece in lay.get("pieces") or []:
                if not isinstance(piece, dict):
                    continue
                k2 += 1
                if k2 not in hits:
                    continue
                rec = (piece.get("recipient") or piece.get("recipient_text") or "").strip()
                ql = (piece.get("quantity_label") or "").strip()
                blob = "%s %s" % (rec, ql)
                if fl in blob or (short and short in blob):
                    refined.append(k2)
        if len(refined) == 1:
            return refined[0]
    inst_n: int | None = None
    mo = re.match(r"^F\d+\.(\d+)_O\d+\s*$", fl, flags=re.IGNORECASE)
    if mo:
        try:
            inst_n = int(mo.group(1))
        except (TypeError, ValueError):
            inst_n = None
    if inst_n is not None and gw > 0 and gh > 0:
        match_ks: List[int] = []
        kk = 0
        for cr in rows:
            lay = cr.get("layout") if isinstance(cr.get("layout"), dict) else {}
            for piece in lay.get("pieces") or []:
                if not isinstance(piece, dict):
                    continue
                kk += 1
                try:
                    w = int(piece.get("w") or 0)
                    h = int(piece.get("h") or 0)
                except (TypeError, ValueError):
                    w, h = 0, 0
                if _cut_piece_mm_matches_glass(gw, gh, w, h):
                    match_ks.append(kk)
        if 1 <= inst_n <= len(match_ks):
            return match_ks[inst_n - 1]
    return None


def assembly_resolve_profile_piece_labels(
    batch_items: List[Dict[str, Any]],
    labs: List[Dict[str, Any]],
    used_lab_ids: Set[int],
    product_index: int,
    facade_label: str,
) -> Dict[str, str]:
    """Этикетки брусьев A… для одного фасада; used_lab_ids общий на весь batch (порядок — по id строк batch)."""
    fl = str(facade_label or "").strip()
    pidx = int(product_index)
    own = [
        it
        for it in batch_items
        if int(it.get("product_index") or 0) == pidx and str(it.get("facade_label") or "").strip() == fl
    ]
    if not own and fl == "":
        own = [it for it in batch_items if int(it.get("product_index") or 0) == pidx]
    own_sorted = sorted(own, key=lambda x: int(x.get("id") or 0))
    side_tags: Dict[str, str] = {}
    avail = [lb for lb in labs if int(lb.get("id") or 0) not in used_lab_ids]
    chunk = avail[: len(own_sorted)]
    if len(chunk) == len(own_sorted) and own_sorted:
        ok = all(
            int(own_sorted[i].get("required_mm") or 0) == int(chunk[i].get("piece_mm") or 0)
            for i in range(len(own_sorted))
        )
        if ok:
            for it, lb in zip(own_sorted, chunk):
                sk = _std_profile_side_key(it.get("side_key"))
                if sk in ("top", "right", "bottom", "left"):
                    u = str(lb.get("unique_number") or "").strip()
                    if u:
                        side_tags[sk] = u
                used_lab_ids.add(int(lb.get("id") or 0))
            return side_tags
    for it in own_sorted:
        sk = _std_profile_side_key(it.get("side_key"))
        if sk not in ("top", "right", "bottom", "left"):
            continue
        req = int(it.get("required_mm") or 0)
        cand = [
            lb
            for lb in labs
            if int(lb.get("id") or 0) not in used_lab_ids and int(lb.get("piece_mm") or 0) == req
        ]
        cand.sort(key=lambda x: int(x.get("id") or 0))
        pool = cand if cand else [lb for lb in labs if int(lb.get("id") or 0) not in used_lab_ids]
        pool.sort(key=lambda x: int(x.get("id") or 0))
        if not pool:
            continue
        lb = pool[0]
        used_lab_ids.add(int(lb.get("id") or 0))
        u = str(lb.get("unique_number") or "").strip()
        if u:
            side_tags[sk] = u
    return side_tags


def assembly_facade_units_for_profile_batch(
    batch_id: int,
    *,
    include_drawings: bool = True,
    only_unit: Tuple[int, str] | None = None,
) -> List[Dict[str, Any]]:
    """Вкладка «Сборка»: одна карточка = один фасад (экземпляр: product_index + facade_label в batch).

    include_drawings=False — только метаданные для списка (без SVG и без тяжёлого поиска K по раскрою).
    only_unit=(product_index, facade_label) — собрать только одну позицию (страница карточки), без остальных.
    """
    from db import models as db_models

    b = db_models.get_profile_cut_batch(int(batch_id)) or {}
    if not b:
        return []
    oid = int(b.get("order_id") or 0)
    if oid < 1:
        return []
    order_row = db_models.get_order(oid)
    if not order_row:
        return []
    order_labels = db_models.get_order_for_labels(int(oid)) or {}
    client_name = (order_labels.get("client_name") or order_labels.get("o_client_name") or "").strip()
    k_number = order_labels.get("k_number")
    instr = order_instructions_dict(dict(order_row), include_schematics=include_drawings)
    by_idx = {int(p.get("index") or 0): p for p in (instr.get("products") or [])}
    items = db_models.list_profile_cut_batch_items(int(batch_id)) or []
    labs = [
        x
        for x in (db_models.list_profile_piece_labels_by_batch(int(batch_id)) or [])
        if str(x.get("label_prefix") or "").upper() == "A"
        and str(x.get("piece_kind") or "").strip().lower() == "assembly"
    ]
    labs.sort(key=lambda x: (int(x.get("id") or 0), int(x.get("label_number") or 0)))
    unit_keys: List[Tuple[int, str]] = []
    seen_keys: Set[Tuple[int, str]] = set()
    for it in sorted(items, key=lambda x: int(x.get("id") or 0)):
        pidx = int(it.get("product_index") or 0)
        flabel = str(it.get("facade_label") or "").strip()
        if pidx < 1:
            continue
        key = (pidx, flabel)
        if key in seen_keys:
            continue
        prod = by_idx.get(pidx)
        if not prod or str(prod.get("kind") or "").strip() != _KIND_FACADE:
            continue
        seen_keys.add(key)
        unit_keys.append(key)
    if only_unit is not None:
        ou_p, ou_f = int(only_unit[0]), str(only_unit[1] or "").strip()
        unit_keys = [(a, b) for a, b in unit_keys if int(a) == ou_p and str(b or "").strip() == ou_f]
    used_lab_ids: Set[int] = set()
    out: List[Dict[str, Any]] = []
    for pidx, flabel in unit_keys:
        prod = by_idx.get(pidx)
        if not prod:
            continue
        pl = prod.get("payload") if isinstance(prod.get("payload"), dict) else {}
        sch = prod.get("schematic") if isinstance(prod.get("schematic"), dict) else {}
        detail_svgs: List[Any] = list(sch.get("detail_svgs") or []) if include_drawings else []
        gfac = pl.get("Стекло") if isinstance(pl.get("Стекло"), dict) else {}
        side_asm = assembly_resolve_profile_piece_labels(items, labs, used_lab_ids, pidx, flabel)
        gk = None
        if include_drawings:
            gk = resolve_glass_k_index_for_facade_instance(oid, flabel, gfac, pl)
        src = gfac.get("_source_stock") if isinstance(gfac.get("_source_stock"), dict) else {}
        glass_no = src.get("label_number") or src.get("stock_id")
        if gk is not None:
            glass_no = gk
        elif glass_no in (None, "", "—"):
            glass_no = None
        svg_out = ""
        if include_drawings:
            spec = facade_schematic_spec(pl, assembly_side_tags=side_asm or None)
            if spec:
                spec = copy.deepcopy(spec)
                spec["glass_label_no"] = glass_no
                spec["glass_ready"] = bool(prod.get("glass_manufactured"))
                svg_out = build_facade_frame_svg(spec)
            if not svg_out:
                svg_out = prod.get("schematic_svg") or ""
        out.append(
            {
                "batch_id": int(batch_id),
                "order_id": oid,
                "client_name": client_name,
                "k_number": k_number,
                "product_index": pidx,
                "facade_label": flabel,
                "title": prod.get("title"),
                "facade_dims": prod.get("facade_dims"),
                "glass_cut_state_ru": prod.get("glass_cut_state_ru"),
                "glass_label_no": glass_no,
                "glass_manufactured": bool(prod.get("glass_manufactured")),
                "schematic_svg": svg_out,
                "detail_svgs": detail_svgs,
                "fittings_lines": list(prod.get("fittings_lines") or []),
                "seal_screw_lines": facade_seal_screw_lines(pl),
                "corner_lines": facade_corner_connector_lines(pl),
                "glass_instruction_lines": facade_glass_instruction_lines(pl, include_fill_processing=False),
            }
        )
    return out


def assembly_facade_unit_detail(
    batch_id: int,
    order_id: int,
    product_index: int,
    facade_label: str,
) -> Dict[str, Any] | None:
    """Одна карточка сборки для страницы деталей (проверка batch ↔ заказ)."""
    from db import models as db_models

    b = db_models.get_profile_cut_batch(int(batch_id)) or {}
    if not b or int(b.get("order_id") or 0) != int(order_id):
        return None
    fl = str(facade_label or "").strip()
    units = assembly_facade_units_for_profile_batch(
        int(batch_id), include_drawings=True, only_unit=(int(product_index), fl)
    )
    if not units:
        return None
    u = units[0]
    lab = db_models.get_or_create_facade_finish_label(int(batch_id), int(order_id), int(product_index), fl)
    pub = int((lab or {}).get("public_number") or 0)
    ev = db_models.list_production_events(int(order_id)) or []
    u2 = dict(u)
    u2["public_number"] = pub
    u2["finished_code"] = "GF%s" % pub
    u2["assembled"] = facade_assembly_unit_marked_done(ev, int(batch_id), int(product_index), fl, pub)
    return u2


def glass_mirror_summary_lines(payload: dict) -> List[str]:
    iz = payload.get("Параметры изделия") or {}
    mp = payload.get("Параметры материала") or {}
    shape = iz.get("Форма") or "—"
    w, h = iz.get("Ширина (мм)"), iz.get("Высота (мм)")
    dim = "%s×%s мм" % (w or "—", h or "—") if shape == "Прямоугольник" else "размеры по форме «%s»" % shape
    return [
        "Изделие: %s, %s." % (shape, dim),
        "Материал: %s, %s мм, %s."
        % (mp.get("Тип материала") or "—", mp.get("Толщина (мм)") or "—", mp.get("Цвет / Вариант") or "—"),
        "Макет клиента: %s" % ("есть" if iz.get("Файл") else "нет файла"),
    ]


def product_worker_steps(product: dict) -> List[str]:
    """Единый нумеруемый список шагов для сотрудника по одному изделию из bundle."""
    kind = str(product.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
    pl = product.get("payload") if isinstance(product.get("payload"), dict) else {}
    if kind == _KIND_FACADE:
        lines: List[str] = [
            "Фасад целиком: наружный размер рамы %s × %s мм."
            % (pl.get("Ширина_мм") or "—", pl.get("Высота_мм") or "—"),
        ]
        lines.append("— Профили (что взять со склада и какие этикетки на брусьях):")
        lines.extend(["  • %s" % x for x in facade_profile_instruction_lines(pl)])
        lines.append("— Наполнение проёма (стекло/зеркало):")
        lines.extend(["  • %s" % x for x in facade_glass_instruction_lines(pl)])
        fit_lines = facade_fittings_instruction_lines(pl)
        if fit_lines:
            lines.append("— Фурнитура и присадка:")
            lines.extend(["  • %s" % x for x in fit_lines])
        lines.extend(["  • %s" % x for x in facade_seal_screw_lines(pl)])
        lines.append(
            "Остатки после реза профиля: промаркировать этикетками остатков по правилам склада; не смешивать с другими заказами."
        )
        return lines
    lines = glass_mirror_summary_lines(pl)
    lines.append("Детализация обработок и услуг — в PDF раскроя и этикетках изделия после оплаты и раскроя.")
    return lines


def _product_total_rub(product: dict) -> int:
    pl = product.get("payload") if isinstance(product.get("payload"), dict) else {}
    kind = str(product.get("kind") or "").strip()
    if kind == _KIND_FACADE:
        try:
            return int(pl.get("_total_rub") or 0)
        except (TypeError, ValueError):
            return 0
    from logic.blocks_bundle import product_sum_excluding_order_level

    try:
        return int(product_sum_excluding_order_level(pl))
    except Exception:
        return 0


def _product_title(product: dict, index: int) -> str:
    kind = str(product.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
    pl = product.get("payload") if isinstance(product.get("payload"), dict) else {}
    if kind == _KIND_FACADE:
        return "Изделие %d — фасад %s×%s мм" % (index, pl.get("Ширина_мм") or "—", pl.get("Высота_мм") or "—")
    iz = pl.get("Параметры изделия") or {}
    return "Изделие %d — %s" % (index, iz.get("Форма") or "стекло/зеркало")


def _commercial_pdf_product_heading(product: dict, qty: int) -> str:
    """Заголовок позиции в PDF-сводке: материал · вариант · толщина · размер [· N шт]."""
    kind = str(product.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
    pl = product.get("payload") if isinstance(product.get("payload"), dict) else {}
    qv = max(1, int(qty or 1))
    qty_suf = (" %dшт" % qv) if qv > 1 else ""
    if kind == _KIND_FACADE:
        w = pl.get("Ширина_мм") or "—"
        h = pl.get("Высота_мм") or "—"
        return "фасад · %s × %s мм%s" % (w, h, qty_suf)
    try:
        from ui.glass_order_overview_dialog import _glass_block_izmat
    except ImportError:
        _glass_block_izmat = None  # type: ignore
    if _glass_block_izmat:
        izd, mat = _glass_block_izmat(pl)
    else:
        izd = pl.get("Параметры изделия") or {}
        mat = pl.get("Параметры материала") or {}
    mt = str(mat.get("Тип материала") or "—").strip().lower()
    var = str(mat.get("Цвет / Вариант") or "").strip()
    th = mat.get("Толщина (мм)")
    ths = ("%s мм" % th) if th is not None and str(th).strip() != "" else ""
    ww = izd.get("Ширина (мм)")
    hh = izd.get("Высота (мм)")
    dim = ""
    if ww is not None or hh is not None:
        dim = " %s × %s мм" % (ww or "—", hh or "—")
    parts = [p for p in (mt, var, ths) if p]
    if parts or dim:
        return (" · ".join(parts) + dim + qty_suf).strip()
    return ("изделие" + qty_suf).strip()


def _commercial_pdf_note_payload(kind: str, payload: dict) -> dict:
    """Данные для примечаний: у фасада вырезы/отверстия в blocks_selected стекла в проёме."""
    if str(kind or "").strip() != _KIND_FACADE:
        return payload or {}
    g = (payload or {}).get("Стекло")
    if not isinstance(g, dict):
        return payload or {}
    bs = g.get("blocks_selected")
    if isinstance(bs, dict):
        return bs
    return payload or {}


def _commercial_pdf_cutouts_note(payload: dict) -> str:
    vz = (payload or {}).get("Вырезы") or {}
    if not isinstance(vz, dict) or vz.get("Пусто") or vz.get("Ошибка"):
        return ""
    rows = vz.get("Строки")
    if not isinstance(rows, list) or not rows:
        return ""
    parts = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        cat = (it.get("Категория") or it.get("category_code") or "").strip()
        try:
            n = int(it.get("Кол-во") or it.get("qty") or 0)
        except (TypeError, ValueError):
            n = 0
        if cat and n > 0:
            parts.append("%s × %s" % (cat, n))
    if not parts:
        return ""
    return "вырезы: " + "; ".join(parts)


def _commercial_pdf_row_note(name: str, det: str, payload: dict) -> str:
    note = (str(det or "")).strip()
    if note == "Базовая стоимость материала":
        note = "стоимость материала"
    nm = str(name or "").strip()
    if nm == "Вырезы":
        cut = _commercial_pdf_cutouts_note(payload)
        return cut or note or "—"
    return note or "—"


def _norm_side_key(side_raw: str) -> str:
    s = str(side_raw or "").strip().lower()
    m = {
        "левая": "left",
        "правая": "right",
        "верхняя": "top",
        "нижняя": "bottom",
        "left": "left",
        "right": "right",
        "top": "top",
        "bottom": "bottom",
    }
    return m.get(s, s if s in ("left", "right", "top", "bottom") else "")


_PROFILE_SIDE_ALIASES = {
    "top": "top",
    "верх": "top",
    "верхняя": "top",
    "bottom": "bottom",
    "низ": "bottom",
    "нижняя": "bottom",
    "left": "left",
    "лево": "left",
    "левая": "left",
    "right": "right",
    "право": "right",
    "правая": "right",
}


def _std_profile_side_key(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    return _PROFILE_SIDE_ALIASES.get(s, s if s in ("top", "right", "bottom", "left") else "")


def facade_side_colors_map() -> Dict[str, str]:
    """Цвета сторон рамы: верх — синий, низ — красный, право — зелёный, лево — фиолетовый."""
    return {
        "top": "#1d4ed8",
        "right": "#15803d",
        "bottom": "#dc2626",
        "left": "#7c3aed",
    }


def _new_profile_identity(pf: dict, src: dict) -> Any | None:
    """Один и тот же физический новый брус → одинаковый ключ (для общего «Новый профиль (1)»)."""
    if str(src.get("kind") or "").strip() != "new":
        return None
    sid = src.get("stock_id")
    if sid is not None and str(sid).strip() != "":
        return ("stock", str(sid).strip())
    slot = src.get("new_profile_slot") or src.get("bar_slot") or src.get("assignment_id")
    if slot is not None and str(slot).strip() != "":
        return ("slot", str(slot).strip())
    sig = (
        str(pf.get("name") or ""),
        str(pf.get("series") or ""),
        str(pf.get("color") or ""),
        str(src.get("length_mm") or ""),
    )
    return ("sig", sig)


def norm_facade_profiles_by_side(pl: dict) -> Dict[str, dict]:
    """Сторона (top/right/bottom/left) → данные профиля из расчёта."""
    out: Dict[str, dict] = {}
    for raw_k, pf in (pl.get("Профили_по_сторонам") or {}).items():
        if not isinstance(pf, dict):
            continue
        nk = _std_profile_side_key(raw_k)
        if nk in ("top", "right", "bottom", "left"):
            out[nk] = pf
    return out


def facade_schematic_spec(pl: dict, assembly_side_tags: Dict[str, str] | None = None) -> Dict[str, Any]:
    """Компактные данные для SVG-схемы фасада (веб без PDF).

    assembly_side_tags: для сборки — подписи сторон этикетками отрезанных брусьев (top/right/bottom/left → A…).
    """
    from db import models as db_models

    def _live_remnant_tag(src: dict) -> tuple[str | None, int | None]:
        if not isinstance(src, dict):
            return (None, None)
        cand_ids: List[int] = []
        for k in ("stock_id", "new_remnant_stock_id"):
            v = src.get(k)
            try:
                sid = int(v) if v is not None else None
            except (TypeError, ValueError):
                sid = None
            if sid and sid not in cand_ids:
                cand_ids.append(sid)
        for sid in cand_ids:
            row = db_models.get_profile_stock_row(sid) or {}
            if not row or not bool(row.get("is_remnant")):
                continue
            lbl = None
            try:
                lbl = db_models.ensure_profile_label_number(sid)
            except Exception:
                lbl = None
            if lbl is None:
                lr = db_models.get_profile_label_by_stock_id(sid) or {}
                lbl = lr.get("label_number")
            if lbl is not None and str(lbl).strip() != "":
                return ("R%s" % str(lbl).strip(), sid)
            return ("R%s" % sid, sid)
        return (None, None)

    try:
        W = float(pl.get("Ширина_мм") or 0)
        H = float(pl.get("Высота_мм") or 0)
    except (TypeError, ValueError):
        W, H = 0.0, 0.0
    if W < 10 or H < 10:
        return {}
    norm_profiles = norm_facade_profiles_by_side(pl)
    colors = facade_side_colors_map()
    order_keys = ("top", "right", "bottom", "left")
    new_idx_by_ident: Dict[Any, int] = {}
    next_new_n = 1
    sides: List[Dict[str, Any]] = []
    for key in order_keys:
        pf = norm_profiles.get(key)
        if not isinstance(pf, dict):
            continue
        nm = str(pf.get("name") or pf.get("series") or "профиль").strip()[:40]
        src = pf.get("_source_stock") or {}
        lbl = src.get("label_number") or src.get("stock_id")
        kind = str(src.get("kind") or "").strip()
        src_len = src.get("length_mm")
        req_len = src.get("required_mm")
        rest_len = src.get("rest_mm")
        ident = _new_profile_identity(pf, src)
        new_no: int | None = None
        if ident is not None:
            if ident not in new_idx_by_ident:
                new_idx_by_ident[ident] = next_new_n
                next_new_n += 1
            new_no = new_idx_by_ident[ident]
        live_tag, live_sid = _live_remnant_tag(src) if kind == "warehouse_remnant" else (None, None)
        if kind == "new":
            stock_brief = "Новый профиль (%s)" % new_no if new_no is not None else "Новый профиль"
        elif kind == "warehouse_remnant":
            stock_brief = ("остаток %s (складской id %s)" % (live_tag, live_sid)) if (live_tag and live_sid) else "остатка на складе нет — взять новый"
        else:
            stock_brief = ("№%s" % lbl) if lbl else "склад"
        if src_len:
            stock_brief += " L=%s мм" % src_len
        if req_len:
            stock_brief += " → рез %s мм" % req_len
        if rest_len is not None:
            stock_brief += " (остаток %s мм)" % rest_len
        side_tag = ""
        if kind == "warehouse_remnant":
            side_tag = live_tag or "новый"
        elif kind == "new":
            side_tag = "новый"
        elif lbl is not None and str(lbl).strip() != "":
            side_tag = str(lbl).strip()
        skip_new_footer = False
        if assembly_side_tags:
            atag = str((assembly_side_tags or {}).get(key) or "").strip()
            if atag:
                try:
                    req_side_mm = int(W) if key in ("top", "bottom") else int(H)
                except (TypeError, ValueError):
                    req_side_mm = 0
                side_tag = atag
                stock_brief = (
                    "Отрезано, этикетка бруса %s (%s мм)" % (atag, req_side_mm) if req_side_mm > 0 else "Отрезано, этикетка бруса %s" % atag
                )
                skip_new_footer = True
        sides.append(
            {
                "key": key,
                "ru": _ru_facade_side(key),
                "profile_name": nm,
                "profile_color": str(pf.get("color") or "").strip(),
                "source_kind": kind or "new",
                "stock_brief": stock_brief,
                "side_tag": side_tag,
                "stroke": colors.get(key, "#334155"),
                "new_profile_no": new_no,
                "skip_new_footer": skip_new_footer,
            }
        )
    g = pl.get("Стекло") if isinstance(pl.get("Стекло"), dict) else {}
    try:
        gw = float(g.get("Ширина (мм)") or 0)
        gh = float(g.get("Высота (мм)") or 0)
    except (TypeError, ValueError):
        gw, gh = 0.0, 0.0
    if gw < 1 or gh < 1:
        gw, gh = W * 0.82, H * 0.82
    mx = max(0.0, (W - gw) / 2)
    my = max(0.0, (H - gh) / 2)
    glass = {
        "w_mm": gw,
        "h_mm": gh,
        "x_mm": mx,
        "y_mm": my,
        "name": str(g.get("Название") or "наполнение")[:48],
        "thickness_mm": g.get("Толщина (мм)"),
    }
    processing_lines = glass_processing_lines(g)[:4]
    holes: List[Dict[str, Any]] = []

    def _hole_cutout_type(h: dict, supplier_fallback: str = "") -> str:
        raw = str(
            h.get("тип")
            or h.get("тип_выреза")
            or h.get("тип выреза")
            or h.get("Тип")
            or h.get("Тип выреза")
            or h.get("type")
            or h.get("cutout_type")
            or h.get("hole_type")
            or h.get("kind")
            or h.get("название")
            or h.get("подпись")
            or ""
        ).strip()
        if raw and raw.lower() not in ("hole", "отверстие"):
            return raw[:40]
        sup = (supplier_fallback or "").strip()
        if sup and sup not in ("—", "–", "-", "нет", "н/д"):
            return ("петля · %s" % sup)[:40]
        return "вырез (тип не указан)"

    for rec in pl.get("Присадка") or []:
        if not isinstance(rec, dict):
            continue
        sk = _norm_side_key(rec.get("сторона") or rec.get("side") or "")
        if not sk:
            continue
        supplier_side = str(rec.get("поставщик_петли") or rec.get("supplier") or "").strip()
        for h in rec.get("отверстия") or rec.get("holes") or []:
            if not isinstance(h, dict):
                continue
            off = h.get("отступ_мм")
            if off is None:
                off = h.get("offset_mm")
            try:
                off_f = float(off)
            except (TypeError, ValueError):
                off_f = None
            hole_type = _hole_cutout_type(h, supplier_side)
            hole_diam = _hole_diameter_mm(h)
            holes.append(
                {
                    "side": sk,
                    "offset_mm": off_f,
                    "note": "присадка",
                    "cutout_type": hole_type,
                    "diameter_mm": hole_diam,
                    "side_color": colors.get(sk, "#dc2626"),
                }
            )
    short_lines = []
    for s in sides:
        short_lines.append("%s: взять %s; резать %s" % (s["ru"], s["stock_brief"], s["profile_name"]))
    short_lines.append("готовый фасад: после сборки наклеить 1 этикетку на изделие целиком")
    holes_grouped = _holes_grouped_label(holes)
    if holes_grouped:
        short_lines.append("Отверстия: %s" % holes_grouped)
    detail_svgs: List[Dict[str, Any]] = []
    for sk in order_keys:
        side_holes = [h for h in holes if h.get("side") == sk]
        if not side_holes:
            continue
        edge_len = H if sk in ("left", "right") else W
        sc = (side_holes[0].get("side_color") or colors.get(sk, "#dc2626")) if side_holes else colors.get(sk, "#dc2626")
        legend_lines: List[str] = []
        for h in sorted(side_holes, key=lambda hh: float(hh.get("offset_mm") or 0)):
            off = h.get("offset_mm")
            try:
                off_f = float(off) if off is not None else None
            except (TypeError, ValueError):
                off_f = None
            if off_f is None:
                continue
            ct = str(h.get("cutout_type") or "—").strip() or "—"
            legend_lines.append("отступ %s мм от торца — %s" % (_fmt_mm(off_f), ct))
        detail_svgs.append(
            {
                "title": "Профиль (%s) — отверстия по длине" % _ru_facade_side(sk),
                "side_key": sk,
                "side_color": sc,
                "svg": build_side_drill_strip_svg(edge_len, side_holes, hole_fill=str(sc)),
                "legend_lines": legend_lines,
            }
        )
    return {
        "type": "facade",
        "width_mm": W,
        "height_mm": H,
        "sides": sides,
        "glass": glass,
        "processing_lines": processing_lines,
        "holes": holes,
        "short_lines": short_lines[:8],
        "detail_svgs": detail_svgs[:4],
    }


def glass_schematic_spec(pl: dict) -> Dict[str, Any]:
    iz = pl.get("Параметры изделия") or {}
    try:
        W = float(iz.get("Ширина (мм)") or 0)
        H = float(iz.get("Высота (мм)") or 0)
    except (TypeError, ValueError):
        W, H = 0.0, 0.0
    if W < 1 or H < 1:
        return {}
    mp = pl.get("Параметры материала") or {}
    return {
        "type": "glass",
        "width_mm": W,
        "height_mm": H,
        "shape": str(iz.get("Форма") or "Прямоугольник"),
        "material": str(mp.get("Тип материала") or "—")[:32],
        "color": str(mp.get("Цвет / Вариант") or "—")[:32],
        "thickness_mm": mp.get("Толщина (мм)"),
        "short_lines": glass_mirror_summary_lines(pl)[:4],
    }


def build_facade_frame_svg(spec: Dict[str, Any]) -> str:
    if spec.get("type") != "facade":
        return ""
    W = float(spec["width_mm"])
    H = float(spec["height_mm"])
    sw = max(2.0, min(W, H) * 0.01)
    fs_pre = max(8.0, min(W, H) * 0.028)
    _m = min(W, H)
    pad_top = max(40.0, _m * 0.11)
    pad_bot = max(38.0, _m * 0.10)
    pad_side = max(26.0, _m * 0.10)
    vb_w = W + pad_side * 2
    vb_h = H + pad_top + pad_bot
    side_map = {str(s.get("key") or ""): s for s in (spec.get("sides") or []) if isinstance(s, dict)}
    top_s = side_map.get("top") or {}
    right_s = side_map.get("right") or {}
    bot_s = side_map.get("bottom") or {}
    left_s = side_map.get("left") or {}
    glass_ready = bool(spec.get("glass_ready"))
    glass_label_no = spec.get("glass_label_no")
    if glass_ready and glass_label_no not in (None, "", "—"):
        center_label = "K%s" % str(glass_label_no)
    elif glass_ready:
        center_label = "K?"
    else:
        center_label = "стекло не готово"
    parts: List[str] = []
    _bg = "#0f172a"
    _border = "#334155"
    parts.append(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" '
        'style="max-width:100%%;max-height:min(520px,82vh);height:auto;background:%s;border:1px solid %s;border-radius:8px;">'
        % (vb_w, vb_h, _bg, _border)
    )
    parts.append('<rect x="0" y="0" width="%s" height="%s" fill="%s"/>' % (vb_w, vb_h, _bg))
    parts.append('<g transform="translate(%s %s)">' % (pad_side, pad_top))

    t = max(12.0, min(W, H) * 0.078)
    gx, gy, gw, gh = t, t, max(10.0, W - 2 * t), max(10.0, H - 2 * t)
    parts.append(
        '<rect x="%s" y="%s" width="%s" height="%s" fill="#1e3a5f" stroke="#475569" stroke-width="%s"/>'
        % (gx, gy, gw, gh, sw * 0.35)
    )

    top_c = html_module.escape(str(top_s.get("stroke") or "#3358a8"))
    right_c = html_module.escape(str(right_s.get("stroke") or "#1f8f4c"))
    bot_c = html_module.escape(str(bot_s.get("stroke") or "#d9272e"))
    left_c = html_module.escape(str(left_s.get("stroke") or "#6752a3"))
    # Профиль с углами 45°
    parts.append('<polygon points="0,0 %s,0 %s,%s %s,%s" fill="%s"/>' % (W, W - t, t, t, t, top_c))
    parts.append('<polygon points="0,0 0,%s %s,%s %s,%s" fill="%s"/>' % (H, t, H - t, t, t, left_c))
    parts.append('<polygon points="%s,0 %s,%s %s,%s %s,%s" fill="%s"/>' % (W, W, H, W - t, H - t, W - t, t, right_c))
    parts.append('<polygon points="0,%s %s,%s %s,%s %s,%s" fill="%s"/>' % (H, W, H, W - t, H - t, t, H - t, bot_c))
    fs = max(10.0, min(W, H) * 0.036)
    fs_dim = fs * 1.22
    _WHITE = "#ffffff"

    def _profile_tag_font_size(tag: str) -> float:
        tag_s = str(tag or "")
        base = min(0.82 * t, 0.92 * t)
        if len(tag_s) > 6:
            base *= 0.88
        if len(tag_s) > 10:
            base *= 0.85
        return max(10.0, base)

    if top_s:
        tg = str(top_s.get("side_tag") or "—")
        fs_tag = _profile_tag_font_size(tg)
        parts.append(
            '<text x="%s" y="%s" text-anchor="middle" dominant-baseline="middle" font-size="%s" fill="%s" font-family="Segoe UI,Arial,sans-serif" font-weight="800">%s</text>'
            % (W * 0.5, t * 0.52, fs_tag, _WHITE, html_module.escape(tg))
        )
    if bot_s:
        tg = str(bot_s.get("side_tag") or "—")
        fs_tag = _profile_tag_font_size(tg)
        parts.append(
            '<text x="%s" y="%s" text-anchor="middle" dominant-baseline="middle" font-size="%s" fill="%s" font-family="Segoe UI,Arial,sans-serif" font-weight="800">%s</text>'
            % (W * 0.5, H - t * 0.48, fs_tag, _WHITE, html_module.escape(tg))
        )
    if left_s:
        tg = str(left_s.get("side_tag") or "—")
        fs_tag = _profile_tag_font_size(tg)
        parts.append(
            '<text transform="translate(%s,%s) rotate(-90)" text-anchor="middle" dominant-baseline="middle" font-size="%s" fill="%s" font-family="Segoe UI,Arial,sans-serif" font-weight="800">%s</text>'
            % (t * 0.48, H * 0.5, fs_tag, _WHITE, html_module.escape(tg))
        )
    if right_s:
        tg = str(right_s.get("side_tag") or "—")
        fs_tag = _profile_tag_font_size(tg)
        parts.append(
            '<text transform="translate(%s,%s) rotate(90)" text-anchor="middle" dominant-baseline="middle" font-size="%s" fill="%s" font-family="Segoe UI,Arial,sans-serif" font-weight="800">%s</text>'
            % (W - t * 0.48, H * 0.5, fs_tag, _WHITE, html_module.escape(tg))
        )
    dim_top = max(fs_dim * 1.75, 22.0)
    dim_left = max(fs_dim * 1.85, 24.0)
    parts.append(
        '<text x="%s" y="%s" text-anchor="middle" font-size="%s" fill="%s" font-family="Segoe UI,Arial,sans-serif" font-weight="700">%s мм</text>'
        % (W * 0.5, -dim_top, fs_dim, _WHITE, _fmt_mm(W))
    )
    parts.append(
        '<text transform="translate(%s,%s) rotate(-90)" text-anchor="middle" font-size="%s" fill="%s" font-family="Segoe UI,Arial,sans-serif" font-weight="700">%s мм</text>'
        % (-dim_left, H * 0.5, fs_dim, _WHITE, _fmt_mm(H))
    )
    if center_label == "стекло не готово":
        cfs = max(11.0, min(gw, gh) * 0.12)
        parts.append(
            '<text x="%s" y="%s" text-anchor="middle" font-size="%s" fill="%s" font-family="Segoe UI,Arial,sans-serif" font-weight="700">стекло</text>'
            % (gx + gw * 0.5, gy + gh * 0.47, cfs, _WHITE)
        )
        parts.append(
            '<text x="%s" y="%s" text-anchor="middle" font-size="%s" fill="%s" font-family="Segoe UI,Arial,sans-serif" font-weight="700">не готово</text>'
            % (gx + gw * 0.5, gy + gh * 0.63, cfs, _WHITE)
        )
    else:
        cfs = max(14.0, min(gw, gh) * 0.38)
        parts.append(
            '<text x="%s" y="%s" text-anchor="middle" font-size="%s" fill="%s" font-family="Segoe UI,Arial,sans-serif" font-weight="800">%s</text>'
            % (gx + gw * 0.5, gy + gh * 0.42, cfs, _WHITE, html_module.escape(center_label))
        )
    proc_lines = [str(x or "").strip() for x in (spec.get("processing_lines") or []) if str(x or "").strip()]
    if proc_lines:
        pfs = max(8.5, min(gw, gh) * 0.072)
        p0 = gy + gh * 0.67
        for i, ln in enumerate(proc_lines[:3]):
            parts.append(
                '<text x="%s" y="%s" text-anchor="middle" font-size="%s" fill="%s" font-family="Segoe UI,Arial,sans-serif" font-weight="600">%s</text>'
                % (gx + gw * 0.5, p0 + i * (pfs * 1.18), pfs, _WHITE, html_module.escape(ln[:64]))
            )
    holes_grouped = _holes_grouped_label(spec.get("holes") or [])
    if holes_grouped:
        hfs = max(8.5, min(gw, gh) * 0.075)
        parts.append(
            '<text x="%s" y="%s" text-anchor="middle" font-size="%s" fill="%s" font-family="Segoe UI,Arial,sans-serif" font-weight="700">отверстия: %s</text>'
            % (gx + gw * 0.5, gy + gh * 0.93, hfs, _WHITE, html_module.escape(holes_grouped))
        )

    new_lines: List[str] = []
    for s in (top_s, right_s, bot_s, left_s):
        if not s:
            continue
        if s.get("skip_new_footer"):
            continue
        if str(s.get("source_kind") or "").strip().lower() != "new":
            continue
        nm = str(s.get("profile_name") or "профиль").strip()
        clr = str(s.get("profile_color") or "—").strip() or "—"
        line = "Новый профиль (%s): %s, цвет %s" % (str(s.get("ru") or "сторона"), nm, clr)
        if line not in new_lines:
            new_lines.append(line)
    if new_lines:
        for i, line in enumerate(new_lines):
            parts.append(
                '<text x="%s" y="%s" font-size="%s" fill="#cbd5e1" font-family="Segoe UI,Arial,sans-serif">%s</text>'
                % (W * 0.01, H + fs * (2.1 + i * 0.95), fs * 0.78, html_module.escape(line))
            )
    parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts)


def build_side_drill_strip_svg(
    length_mm: float,
    holes: List[Dict[str, Any]],
    hole_fill: str = "#dc2626",
) -> str:
    """Полоска профиля в фиксированных единицах viewBox: позиции точек пропорциональны длине, шрифты не сжимаются на длинных брусьях."""
    try:
        L = float(length_mm)
    except (TypeError, ValueError):
        return ""
    if L < 1:
        return ""

    WU = 1000.0
    pad_x = 28.0
    rail_w = WU - 2 * pad_x
    y_bar = 40.0
    bar_h = 52.0
    y_circ = y_bar + bar_h * 0.5
    sw = 2.0
    fill_c = html_module.escape(str(hole_fill or "#dc2626"))
    bar_fill = html_module.escape(str(hole_fill or "#94a3b8"))

    def _cx_u(ox: float) -> float:
        if L <= 1e-9:
            return pad_x + rail_w * 0.5
        t = max(0.0, min(float(ox), L)) / L
        return pad_x + t * rail_w

    items: List[tuple[float, float, str]] = []
    for h in holes:
        off = h.get("offset_mm")
        try:
            ox = float(off) if off is not None else None
        except (TypeError, ValueError):
            ox = None
        if ox is None:
            continue
        ct_raw = str(
            h.get("cutout_type")
            or h.get("тип")
            or h.get("тип_выреза")
            or h.get("тип выреза")
            or h.get("Тип выреза")
            or h.get("hole_type")
            or "вырез (тип не указан)"
        ).strip()[:48] or "вырез (тип не указан)"
        items.append((_cx_u(ox), float(ox), ct_raw))

    fs_mm = 15.0
    fs_ct = 12.5
    line_gap = fs_mm + fs_ct + 10.0
    min_cx_gap = 70.0
    row_for: List[int] = []
    for i, (cx_u, _ox, _ct) in enumerate(items):
        best_row = 0
        for try_row in range(16):
            clash = False
            for j in range(i):
                if row_for[j] == try_row and abs(items[j][0] - cx_u) < min_cx_gap:
                    clash = True
                    break
            if not clash:
                best_row = try_row
                break
        row_for.append(best_row)
    max_row = max(row_for) if row_for else 0
    gap_below_bar = 20.0
    labels_h = (max_row + 1) * line_gap + 12.0 if items else 8.0
    y0 = y_bar + bar_h + gap_below_bar + fs_mm

    vb_h = y0 + labels_h + 28.0

    parts: List[str] = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" '
        'style="max-width:100%%;height:auto;background:#fafafa;border:1px solid #e2e8f0;border-radius:6px;">'
        % (WU, vb_h),
        '<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="%s" fill-opacity="0.38" stroke="%s" stroke-width="%s"/>'
        % (pad_x, y_bar, rail_w, bar_h, 6.0, bar_fill, bar_fill, max(sw, 1.8)),
    ]
    hr = 8.0
    for cx_u, ox, _ct in items:
        parts.append(
            '<circle cx="%s" cy="%s" r="%s" fill="%s" stroke="#fff" stroke-width="%s"/>'
            % (cx_u, y_circ, hr, fill_c, sw * 0.35)
        )
    for i, (cx_u, ox, ct_raw) in enumerate(items):
        row = row_for[i]
        ty_mm = y0 + row * line_gap
        ty_ct = ty_mm + fs_ct + 5.0
        ct = html_module.escape(ct_raw)
        parts.append(
            '<text x="%s" y="%s" font-size="%s" fill="#0f172a" font-family="Segoe UI,Arial,sans-serif" font-weight="600" text-anchor="middle">%s мм</text>'
            % (cx_u, ty_mm, fs_mm, _fmt_mm(ox))
        )
        parts.append(
            '<text x="%s" y="%s" font-size="%s" fill="#475569" font-family="Segoe UI,Arial,sans-serif" text-anchor="middle">%s</text>'
            % (cx_u, ty_ct, fs_ct, ct)
        )

    parts.append("</svg>")
    return "\n".join(parts)


def _fmt_mm(v: float) -> str:
    try:
        if abs(v - int(v)) < 0.05:
            return str(int(round(v)))
    except Exception:
        pass
    return "%.1f" % v


def _hole_diameter_mm(h: dict) -> float | None:
    """Диаметр отверстия, если он есть в payload (разные ключи/форматы)."""
    if not isinstance(h, dict):
        return None
    raw = (
        h.get("диаметр_мм")
        or h.get("диаметр")
        or h.get("diameter_mm")
        or h.get("diameter")
        or h.get("d_mm")
        or h.get("d")
    )
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    txt = str(raw).strip().replace(",", ".")
    mt = re.search(r"\d+(?:\.\d+)?", txt)
    if not mt:
        return None
    try:
        return float(mt.group(0))
    except (TypeError, ValueError):
        return None


def _holes_grouped_label(holes: List[Dict[str, Any]]) -> str:
    """Строка вида `3*8 мм, 2*10 мм`; если диаметр не известен — `N отв.`."""
    if not holes:
        return ""
    by_diam: Dict[str, int] = {}
    no_diam = 0
    for h in holes:
        d = _hole_diameter_mm(h)
        if d is None or d <= 0:
            no_diam += 1
            continue
        key = _fmt_mm(float(d))
        by_diam[key] = int(by_diam.get(key, 0)) + 1
    parts: List[str] = []
    for d_key in sorted(by_diam.keys(), key=lambda x: float(x)):
        parts.append("%s*%s мм" % (by_diam[d_key], d_key))
    if no_diam:
        parts.append("%s отв." % no_diam)
    return ", ".join(parts)


def build_glass_schematic_svg(spec: Dict[str, Any]) -> str:
    if spec.get("type") != "glass":
        return ""
    W = float(spec["width_mm"])
    H = float(spec["height_mm"])
    sw = max(2.0, min(W, H) * 0.015)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" '
        'style="max-width:100%%;height:auto;background:#fff;border:1px solid #cbd5e1;border-radius:8px;">'
        % (W, H),
        '<rect x="%s" y="%s" width="%s" height="%s" rx="%s" fill="#ecfeff" stroke="#0891b2" stroke-width="%s"/>'
        % (W * 0.05, H * 0.05, W * 0.9, H * 0.9, min(W, H) * 0.02, sw),
        '<text x="%s" y="%s" font-size="%s" fill="#164e63" font-family="Segoe UI,Arial,sans-serif">%s × %s мм · %s</text>'
        % (
            W * 0.08,
            H * 0.45,
            max(10.0, min(W, H) * 0.05),
            _fmt_mm(W),
            _fmt_mm(H),
            html_module.escape(str(spec.get("shape") or "")),
        ),
        '<text x="%s" y="%s" font-size="%s" fill="#164e63" font-family="Segoe UI,Arial,sans-serif">%s · %s мм</text>'
        % (
            W * 0.08,
            H * 0.58,
            max(9.0, min(W, H) * 0.04),
            html_module.escape(str(spec.get("material") or "")),
            html_module.escape(str(spec.get("thickness_mm") or "—")),
        ),
        "</svg>",
    ]
    return "\n".join(parts)


# Пошаговая готовность фасада (профили / стекло / этикетки профилей)
FACADE_PRODUCTION_STEP_KEYS: tuple[str, ...] = ("profiles", "glass", "labels")


def _event_details_dict(dj: Any) -> dict:
    if isinstance(dj, dict):
        return dj
    if isinstance(dj, str):
        import json

        try:
            return json.loads(dj)
        except Exception:
            return {}
    return {}


def _event_sort_ts(e: Dict[str, Any]) -> tuple:
    ca = e.get("created_at")
    ts = 0.0
    if ca is not None:
        if hasattr(ca, "timestamp"):
            try:
                ts = float(ca.timestamp())
            except Exception:
                ts = 0.0
        else:
            try:
                from datetime import datetime

                if isinstance(ca, datetime):
                    ts = ca.timestamp()
            except Exception:
                ts = 0.0
    return (ts, int(e.get("id") or 0))


def _events_chronological(events: List[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    return sorted(events or [], key=_event_sort_ts)


def production_progress_from_events(
    events: List[Dict[str, Any]] | None,
    product_kind_by_index: Dict[int, str],
) -> tuple[Dict[int, Set[str]], Set[int]]:
    """Состояние по журналу (от старых к новым): шаги фасада и готовность стекла/зеркала."""
    facade_steps: Dict[int, Set[str]] = {}
    glass_done: Dict[int, bool] = {}

    for e in _events_chronological(events):
        et = str(e.get("event_type") or "").strip()
        dj = _event_details_dict(e.get("details_json"))
        try:
            idx = int(dj.get("product_index"))
        except (TypeError, ValueError):
            continue
        if idx < 1:
            continue
        kind = str(product_kind_by_index.get(idx) or "").strip()

        if et == "facade_step_done":
            step = str(dj.get("step") or "").strip()
            if step in FACADE_PRODUCTION_STEP_KEYS and kind == _KIND_FACADE:
                facade_steps.setdefault(idx, set()).add(step)
        elif et == "facade_step_clear":
            step = str(dj.get("step") or "").strip()
            if step in FACADE_PRODUCTION_STEP_KEYS and kind == _KIND_FACADE:
                facade_steps.setdefault(idx, set()).discard(step)
        elif et == "product_done":
            if kind == _KIND_FACADE:
                facade_steps[idx] = set(FACADE_PRODUCTION_STEP_KEYS)
            else:
                glass_done[idx] = True
        elif et == "product_clear":
            if kind == _KIND_FACADE:
                facade_steps[idx] = set()
            else:
                glass_done[idx] = False

    glass_out = {i for i, v in glass_done.items() if v}
    return facade_steps, glass_out


def production_facade_steps_by_product(
    events: List[Dict[str, Any]] | None,
    product_kind_by_index: Dict[int, str] | None = None,
) -> Dict[int, Set[str]]:
    """product_index (1-based) → выполненные шаги фасада (последнее событие важнее)."""
    fm, _ = production_progress_from_events(events, product_kind_by_index or {})
    return fm


def production_done_product_indices(
    events: List[Dict[str, Any]] | None,
    instruction_data: Dict[str, Any] | None = None,
) -> Set[int]:
    """Индексы готовых изделий glass_mirror (1-based). Нужен instruction_data с products[].kind."""
    kind_by: Dict[int, str] = {}
    if instruction_data:
        for p in instruction_data.get("products") or []:
            try:
                ix = int(p.get("index") or 0)
            except (TypeError, ValueError):
                continue
            if ix >= 1:
                kind_by[ix] = str(p.get("kind") or "")
    _, glass = production_progress_from_events(events, kind_by)
    return glass


def enrich_instructions_with_progress(data: Dict[str, Any], events: List[Dict[str, Any]] | None) -> Dict[str, Any]:
    """Дополняет products: is_done, для фасада — facade_steps_done и прогресс по шагам."""
    def _product_units(p: Dict[str, Any]) -> int:
        try:
            q0 = int(p.get("quantity") or 0)
            if q0 > 0:
                return q0
        except Exception:
            pass
        kind = str(p.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
        if kind == _KIND_FACADE:
            try:
                return max(1, int((p.get("payload") or {}).get("Количество") or 1))
            except Exception:
                return 1
        pl = p.get("payload") if isinstance(p.get("payload"), dict) else {}
        izd = pl.get("Параметры изделия") if isinstance(pl.get("Параметры изделия"), dict) else {}
        if not izd:
            bs = pl.get("blocks_selected") if isinstance(pl.get("blocks_selected"), dict) else {}
            izd = bs.get("Изделие") if isinstance(bs.get("Изделие"), dict) else {}
            if not izd and isinstance(bs.get("Материал"), dict):
                izd = bs.get("Материал")
        try:
            return max(1, int((izd or {}).get("Количество (шт)") or 1))
        except Exception:
            return 1

    from db import models as db_models

    prods: List[Dict[str, Any]] = list(data.get("products") or [])
    kind_by_idx = {int(p.get("index") or 0): str(p.get("kind") or "") for p in prods if p.get("index") is not None}
    facade_map = production_facade_steps_by_product(events, kind_by_idx)
    oid_for = int(data.get("order_id") or 0)
    done_n = 0
    total_n = 0
    for p in prods:
        units = _product_units(p)
        p["units_total"] = units
        total_n += units
        kind = str(p.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
        idx = int(p.get("index") or 0)
        if kind == _KIND_FACADE:
            st = set(facade_map.get(idx, set()))
            # Стекло/наполнение — только по факту раскроя (production_glass_status), не вручную
            glass_made = bool(p.get("glass_manufactured"))
            st.discard("glass")
            if glass_made:
                st.add("glass")
            p["facade_steps_done"] = {k: (k in st) for k in FACADE_PRODUCTION_STEP_KEYS}
            p["facade_steps_labels_ru"] = {
                "profiles": "Профили (пил, брусья)",
                "glass": "Стекло / наполнение",
                "labels": "Этикетки профилей",
            }
            nst = len(st)
            p["facade_steps_progress"] = "%d/%d" % (nst, len(FACADE_PRODUCTION_STEP_KEYS))
            # Готовность фасада — только сборка всех экземпляров (не шаги профиль/стекло/этикетки).
            assembled_n = 0
            if oid_for > 0 and idx >= 1:
                try:
                    assembled_n = int(
                        db_models.count_facade_instance_assembled_events(
                            oid_for, idx, production_events=events
                        )
                    )
                except Exception:
                    assembled_n = 0
            assembled_n = max(0, min(assembled_n, units))
            p["is_done"] = assembled_n >= int(units)
        else:
            ev_done = idx in production_done_product_indices(events, data)
            pgs = str(p.get("production_glass_status") or "").strip().lower()
            p["is_done"] = bool(ev_done or (pgs == PRODUCTION_GLASS_MADE))
        if p.get("is_done"):
            done_n += units
    data["products"] = prods
    data["products_total"] = total_n
    data["products_done_count"] = done_n
    return data


def order_all_products_done(data: Dict[str, Any]) -> bool:
    prods = data.get("products") or []
    return bool(prods) and all(bool(p.get("is_done")) for p in prods)


def maybe_sync_order_made_status(order_id: int, fresh_instructions: Dict[str, Any], current_status: str | None) -> None:
    """Все изделия готовы по правилам (фасад — после сборки всех экз.) → made; иначе с made обратно в in_progress."""
    from db import models as db_models

    oid = int(order_id)
    cur = (current_status or "").strip().lower()
    if order_all_products_done(fresh_instructions):
        if cur != "made":
            db_models.set_order_status(oid, "made", sync_bundle_product_status=True)
    elif cur == "made":
        if not order_all_products_done(fresh_instructions):
            db_models.set_order_status(oid, "in_progress", sync_bundle_product_status=False)


def try_apply_pending_cut_after_production_progress(
    order_id: int,
    order_row: Dict[str, Any] | None,
    events: List[Dict[str, Any]] | None,
    fresh_instructions: Dict[str, Any] | None = None,
) -> bool:
    """
    Списание склада стекла по сохранённому раскрою (apply_pending_cut), когда:
    - отмечены все задачи раскроя по листам (cut_task_done), либо
    - задач по листам нет, но есть cut_results и все не-фасадные изделия в инструкции помечены is_done
      (например отметка «готово» по изделию без пошагового cut_task).
    Идемпотентно, если архив реза по заказу уже создан.
    """
    from db import models as db_models

    oid = int(order_id)
    if oid < 1:
        return False
    if db_models.cut_archive_exists_for_order(oid):
        return False
    orow = dict(order_row or {})
    ev = events or []
    tasks = build_cut_tasks_for_order(orow, ev)
    if tasks:
        if all(bool(t.get("is_done")) for t in tasks):
            db_models.apply_pending_cut(oid)
            return True
        return False
    if not isinstance(fresh_instructions, dict):
        return False
    rows = db_models.get_cut_results(oid) or []
    if not rows:
        return False
    prods = fresh_instructions.get("products") or []
    has_non_facade = any(str(p.get("kind") or "").strip() != "facade" for p in prods)
    if not has_non_facade:
        return False
    for p in prods:
        if str(p.get("kind") or "").strip() == "facade":
            continue
        if not bool(p.get("is_done")):
            return False
    db_models.apply_pending_cut(oid)
    return True


def _layout_has_data(layout: dict | None) -> bool:
    if not isinstance(layout, dict):
        return False
    sw = layout.get("sheet_width") or layout.get("sheet_width_mm")
    sh = layout.get("sheet_height") or layout.get("sheet_height_mm")
    return bool(
        layout.get("pieces")
        or layout.get("business_rects")
        or layout.get("waste_rects")
        or sw
        or sh
    )


def _norm_thickness_mm_str(th: Any) -> str:
    s = str(th or "").strip().replace(",", ".")
    if not s or s == "?":
        return ""
    try:
        v = float(s)
        if abs(v - int(round(v))) < 1e-6:
            return str(int(round(v)))
        return ("%s" % v).rstrip("0").rstrip(".")
    except Exception:
        return s.lower()


def _thickness_sig_equals(a: str, b: str) -> bool:
    na = _norm_thickness_mm_str(a)
    nb = _norm_thickness_mm_str(b)
    if na and nb:
        return na == nb
    return (str(a or "").strip().lower() == str(b or "").strip().lower())


def _norm_cut_material_key(s: str) -> str:
    t = str(s or "").strip().lower()
    return re.sub(r"\s+", " ", t)


def _material_matches_cut_layout(prod_mat: str, lay_mat: str) -> bool:
    """Сопоставление названия материала в изделии с подписью листа в раскрое (часто разные строки)."""
    pm = _norm_cut_material_key(prod_mat)
    lm = _norm_cut_material_key(lay_mat)
    if not pm or pm == "материал" or not lm or lm == "материал":
        return False
    if pm == lm:
        return True
    if pm in lm or lm in pm:
        return True
    toks = lambda x: {t for t in re.split(r"[^a-zа-яё0-9]+", x) if len(t) >= 3}  # noqa: E731
    return bool(toks(pm) & toks(lm))


def _product_material_signature(product: dict, fallback_status: str = "") -> Tuple[str, str]:
    kind = str(product.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
    pl = product.get("payload") if isinstance(product.get("payload"), dict) else {}
    material = ""
    thickness = ""
    if kind == _KIND_FACADE:
        g = pl.get("Стекло") if isinstance(pl.get("Стекло"), dict) else {}
        material = str(g.get("Название") or g.get("Тип материала") or "").strip().lower()
        thickness = str(g.get("Толщина (мм)") or "").strip()
    else:
        mp = pl.get("Параметры материала") if isinstance(pl.get("Параметры материала"), dict) else {}
        material = str(mp.get("Тип материала") or mp.get("Название") or "").strip().lower()
        thickness = str(mp.get("Толщина (мм)") or "").strip()
    if not material:
        material = "материал"
    if not thickness:
        thickness = "?"
    return material, thickness


def _cut_task_state_from_events(events: List[Dict[str, Any]] | None) -> Dict[str, bool]:
    state: Dict[str, bool] = {}
    for e in _events_chronological(events):
        et = str(e.get("event_type") or "").strip()
        dj = _event_details_dict(e.get("details_json"))
        key = str(dj.get("task_key") or "").strip()
        if not key:
            continue
        if et == "cut_task_done":
            state[key] = True
        elif et == "cut_task_clear":
            state[key] = False
    return state


def build_cut_g_zip_bytes_for_order(order_row: Dict[str, Any]) -> bytes:
    """ZIP со всеми .G для заказа — для WEB_SERVICE /production."""
    from db import models as db_models

    try:
        from logic.g_export import build_g_files_zip_bytes, order_info_from_row
    except ImportError:
        from MAIN_PROJECT.logic.g_export import build_g_files_zip_bytes, order_info_from_row  # type: ignore

    oid = int(order_row.get("id") or 0)
    layouts = db_models.get_cut_layouts_for_overview(oid) or []
    return build_g_files_zip_bytes(layouts, order_info_from_row(order_row))


def build_cut_g_file_bytes_for_order_sheet(order_row: Dict[str, Any], sheet_index: int) -> Tuple[str, bytes]:
    """Один .G файл для листа (sheet_index 1-based)."""
    from db import models as db_models

    try:
        from logic.g_export import layout_to_g_content, g_file_basename, order_info_from_row
    except ImportError:
        from MAIN_PROJECT.logic.g_export import layout_to_g_content, g_file_basename, order_info_from_row  # type: ignore

    oid = int(order_row.get("id") or 0)
    layouts = db_models.get_cut_layouts_for_overview(oid) or []
    idx = int(sheet_index)
    if idx < 1 or idx > len(layouts):
        raise ValueError("sheet_not_found")
    lay = layouts[idx - 1]
    sw = int(lay.get("sheet_width") or 0)
    sh = int(lay.get("sheet_height") or 0)
    info = order_info_from_row(order_row)
    fname = g_file_basename(info, idx, sw, sh, batch_seq=1)
    content = layout_to_g_content(lay, info, sheet_index=idx, batch_seq=1)
    return fname, content.encode("cp1251", errors="replace")


def build_cut_tasks_for_order(order_row: Dict[str, Any], events: List[Dict[str, Any]] | None = None) -> List[Dict[str, Any]]:
    """
    Группировка задач раскроя: order_id + material + thickness + sheet/layout.
    Каждая задача объединяет изделия, которые режутся с одного листа раскроя.
    """
    from db import models as db_models

    oid = int(order_row.get("id") or 0)
    if oid < 1:
        return []
    raw = order_row.get("blocks_calc_json")
    _ver, products = parse_bundle(raw if raw is not None else None)
    product_meta: Dict[int, Tuple[str, str]] = {}
    for i, p in enumerate(products or []):
        product_meta[i + 1] = _product_material_signature(p, str(order_row.get("status") or ""))
    rows = db_models.get_cut_results(oid) or []
    tasks: List[Dict[str, Any]] = []
    ev_state = _cut_task_state_from_events(events)
    assigned: Set[int] = set()
    for i, r in enumerate(rows):
        lay = r.get("layout") if isinstance(r.get("layout"), dict) else {}
        if not _layout_has_data(lay):
            continue
        material = str(lay.get("material") or "").strip().lower() or "материал"
        thickness = str(lay.get("thickness_mm") or "").strip() or "?"
        piece_n = len(lay.get("pieces") or [])
        sheet_key = str(r.get("id") or (i + 1))
        task_key = "o%s_m%s_t%s_s%s" % (
            oid,
            "".join(ch for ch in material if ch.isalnum())[:24] or "mat",
            "".join(ch for ch in thickness if ch.isalnum())[:8] or "na",
            "".join(ch for ch in sheet_key if ch.isalnum())[:12] or str(i + 1),
        )
        linked_indices: List[int] = []
        for idx, sig in sorted(product_meta.items(), key=lambda kv: kv[0]):
            if idx in assigned:
                continue
            if sig == (material, thickness):
                linked_indices.append(int(idx))
        if not linked_indices:
            linked_indices = [
                j + 1
                for j in range(len(products or []))
                if str((products[j] or {}).get("kind") or "") != _KIND_FACADE and (j + 1) not in assigned
            ][: max(1, piece_n)] or ([1] if products else [])
        elif piece_n > 0 and len(linked_indices) < piece_n:
            have = set(linked_indices)
            for idx in sorted(product_meta.keys()):
                if idx in assigned or idx in have:
                    continue
                if str((products[idx - 1] or {}).get("kind") or "").strip() == _KIND_FACADE:
                    continue
                sig = product_meta[idx]
                if _thickness_sig_equals(sig[1], thickness) and _material_matches_cut_layout(sig[0], material):
                    linked_indices.append(int(idx))
                    have.add(idx)
                    if len(linked_indices) >= piece_n:
                        break
            if piece_n > 0 and len(linked_indices) < piece_n:
                for idx in sorted(product_meta.keys()):
                    if idx in assigned or idx in have:
                        continue
                    if str((products[idx - 1] or {}).get("kind") or "").strip() == _KIND_FACADE:
                        continue
                    sig = product_meta[idx]
                    if _thickness_sig_equals(sig[1], thickness):
                        linked_indices.append(int(idx))
                        have.add(idx)
                        if len(linked_indices) >= piece_n:
                            break
        assigned.update(linked_indices)
        done_flag = bool(ev_state.get(task_key))
        try:
            sw_i = int(lay.get("sheet_width") or 0)
        except (TypeError, ValueError):
            sw_i = 0
        try:
            sh_i = int(lay.get("sheet_height") or 0)
        except (TypeError, ValueError):
            sh_i = 0
        g_fname = ""
        try:
            from logic.g_export import g_file_basename, order_info_from_row

            if sw_i > 0 and sh_i > 0:
                g_fname = g_file_basename(order_info_from_row(order_row), i + 1, sw_i, sh_i, batch_seq=1)
        except Exception:
            g_fname = ""
        tasks.append(
            {
                "task_key": task_key,
                "sheet_index": i + 1,
                "sheet_name": lay.get("material") or ("Лист %d" % (i + 1)),
                "material": lay.get("material") or "—",
                "thickness_mm": lay.get("thickness_mm"),
                "piece_count": len(lay.get("pieces") or []),
                "business_count": len(lay.get("business_rects") or []),
                "linked_product_indices": linked_indices,
                "is_done": done_flag,
                "layout": lay,
                "g_file_name": g_fname,
                "g_file_url": (
                    "/api/production/orders/%s/g-file/%d" % (oid, i + 1) if g_fname else ""
                ),
            }
        )
    return tasks


def instructions_dict_without_money(data: Dict[str, Any]) -> Dict[str, Any]:
    """Копия инструкции без сумм — для веба и JSON для цеха."""
    d = copy.deepcopy(data)
    d.pop("grand_total_rub", None)
    for p in d.get("products") or []:
        p.pop("total_rub", None)
    return d


def order_instructions_dict(order_row: Dict[str, Any], include_schematics: bool = True) -> Dict[str, Any]:
    """Структура для API и HTML: заказ → список изделий с шагами."""
    raw = order_row.get("blocks_calc_json")
    _, products = parse_bundle(raw if raw is not None else None)
    prods: List[Dict[str, Any]] = []
    for i, p in enumerate(products):
        kind = str(p.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
        pl = p.get("payload") if isinstance(p.get("payload"), dict) else {}
        pay_t = str(p.get("payment_type") or "unpaid").strip() or "unpaid"
        csc = str(p.get("cut_scheme_status") or "none").strip() or "none"
        pgs = str(p.get("production_glass_status") or "none").strip() or "none"
        entry: Dict[str, Any] = {
            "index": i + 1,
            "id": p.get("id"),
            "kind": kind,
            "payload": pl,
            "quantity": 1,
            "title": _product_title(p, i + 1),
            "steps": product_worker_steps(p),
            "total_rub": _product_total_rub(p),
            "payment_type": pay_t,
            "payment_type_ru": PAYMENT_TYPE_LABELS_RU.get(pay_t, pay_t),
            "cut_scheme_status": csc,
            "cut_scheme_ready": csc == CUT_SCHEME_CREATED,
            "production_glass_status": pgs,
            "glass_manufactured": pgs == PRODUCTION_GLASS_MADE,
        }
        if kind == _KIND_FACADE:
            try:
                entry["quantity"] = max(1, int(pl.get("Количество") or 1))
            except Exception:
                entry["quantity"] = 1
            gfac = pl.get("Стекло") if isinstance(pl.get("Стекло"), dict) else {}
            glass_label_no = None
            src = gfac.get("_source_stock") if isinstance(gfac.get("_source_stock"), dict) else {}
            if src:
                glass_label_no = src.get("label_number") or src.get("stock_id")
            entry["facade_dims"] = "%s×%s мм" % (pl.get("Ширина_мм") or "—", pl.get("Высота_мм") or "—")
            entry["facade_filling"] = str(gfac.get("Название") or gfac.get("Тип материала") or "—")
            entry["glass_cut_state_ru"] = "отрезано" if pgs == PRODUCTION_GLASS_MADE else "не отрезано"
            entry["glass_label_no"] = glass_label_no
            entry["fittings_lines"] = facade_fittings_instruction_lines(pl)
            entry["processing_lines"] = glass_processing_lines(gfac)
            if include_schematics:
                spec = facade_schematic_spec(pl)
                if spec:
                    spec["glass_ready"] = pgs == PRODUCTION_GLASS_MADE
                    spec["glass_label_no"] = glass_label_no
                    entry["schematic"] = spec
                    entry["schematic_svg"] = build_facade_frame_svg(spec)
        else:
            try:
                izd_q = (pl.get("Параметры изделия") or {}).get("Количество (шт)")
                entry["quantity"] = max(1, int(izd_q or 1))
            except Exception:
                entry["quantity"] = 1
            if include_schematics:
                spec = glass_schematic_spec(pl)
                if spec:
                    entry["schematic"] = spec
                    entry["schematic_svg"] = build_glass_schematic_svg(spec)
        prods.append(entry)
    from logic.blocks_bundle import bundle_grand_total_rub

    st = order_row.get("status")
    return {
        "order_id": order_row.get("id"),
        "client_name": order_row.get("client_name"),
        "status": st,
        "status_ru": order_status_to_ru(st),
        "k_number": order_row.get("k_number"),
        "products": prods,
        "grand_total_rub": bundle_grand_total_rub(products),
    }


def facade_worker_pdf_extra_lines(pl: dict) -> List[str]:
    """Строки для PDF «работнику»; перенос строки выполняет генератор PDF."""
    return product_worker_steps({"kind": _KIND_FACADE, "payload": pl})


def bundle_has_products(order_row: Dict[str, Any]) -> bool:
    raw = order_row.get("blocks_calc_json")
    _, products = parse_bundle(raw if raw is not None else None)
    return bool(products)


def cut_detail_lines_for_worker_pdf(order_id: int) -> List[str]:
    """Строки для PDF цеха: листы раскроя, детали, деловые остатки, этикетки стекла."""
    from db import models as db_models

    lines: List[str] = []
    rows = db_models.get_cut_results(int(order_id)) or []
    if not rows:
        return lines
    lines.append("Раскрой стекла / зеркала (сохранённые листы):")
    for si, r in enumerate(rows):
        lay = r.get("layout") if isinstance(r.get("layout"), dict) else {}
        mat = lay.get("material") or "—"
        th = lay.get("thickness_mm")
        ths = "%s" % th if th is not None else "—"
        sw, sh = lay.get("sheet_width"), lay.get("sheet_height")
        lines.append(
            "  Лист %d: «%s», толщина %s мм, исходный размер %s × %s мм"
            % (si + 1, mat, ths, sw or "—", sh or "—")
        )
        for pi, p in enumerate(lay.get("pieces") or []):
            if not isinstance(p, dict):
                continue
            w, h = p.get("w"), p.get("h")
            rec = (p.get("recipient") or p.get("recipient_text") or "").strip()
            tail = " — получатель: %s" % rec if rec else ""
            lines.append("    Деталь %d: %s × %s мм%s" % (pi + 1, w, h, tail))
        for bi, br in enumerate(lay.get("business_rects") or []):
            if not isinstance(br, dict):
                continue
            lines.append(
                "    Деловой остаток стекла %d: %s × %s мм — наклеить этикетку склада (номер на схеме / в программе)"
                % (bi + 1, br.get("w"), br.get("h"))
            )
    lines.append("Остатки стекла с этикетками (что клеить на брус на складе):")
    rids = db_models.get_remnant_ids_by_order_id(int(order_id)) or []
    if not rids:
        lines.append("  (записей пока нет — появятся после сохранения раскроя и выполнения заказа)")
    for rid in rids:
        rem = db_models.get_remnant_by_id(rid)
        if not rem:
            continue
        lbl = rem.get("label_number")
        uniq = rem.get("unique_number")
        tag = lbl if lbl is not None else uniq
        lines.append(
            "  Этикетка № %s — %s, %s × %s мм, толщина %s мм"
            % (
                tag or rem.get("id"),
                rem.get("name") or "—",
                rem.get("width_mm"),
                rem.get("height_mm"),
                rem.get("thickness_mm"),
            )
        )
    return lines


def write_order_worker_instructions_pdf(order_row: Dict[str, Any], filepath: str) -> None:
    """PDF «инструкция для цеха» по всем изделиям заказа (фасады, стекло) — без раскроя листов."""
    import os
    import sys

    from reportlab.lib.units import mm
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas as rl_canvas

    data = order_instructions_dict(order_row)
    prods = data.get("products") or []
    if not prods:
        raise ValueError("Нет изделий в расчёте для производственной инструкции.")

    font_name = "Helvetica"
    try:
        if sys.platform == "win32":
            fp = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf")
            if os.path.isfile(fp):
                font_name = "WorkerInstrFont"
                pdfmetrics.registerFont(TTFont(font_name, fp))
    except Exception:
        font_name = "Helvetica"

    from reportlab.lib.colors import HexColor

    W, H = A4
    c = rl_canvas.Canvas(filepath, pagesize=A4)
    y = H - 18 * mm
    margin = 14 * mm
    max_chars = 92

    def new_page():
        nonlocal y
        c.showPage()
        y = H - 18 * mm

    def emit(txt: str, size: float = 10, indent_mm: float = 0):
        nonlocal y
        c.setFont(font_name, size)
        c.setFillColor(HexColor("#0f172a"))
        t = str(txt).replace("\r", "").strip()
        if not t:
            return
        rest = t
        ind = indent_mm
        while rest:
            chunk = rest[:max_chars]
            if len(rest) > max_chars:
                sp = chunk.rfind(" ")
                if sp > 50:
                    chunk = rest[:sp]
                    rest = rest[sp:].lstrip()
                else:
                    rest = rest[max_chars:]
            else:
                rest = ""
            if y < 22 * mm:
                new_page()
            c.drawString(margin + ind, y, chunk)
            y -= 3.6 * mm

    def draw_facade_mini(x0: float, y_top: float, schematic: Dict[str, Any]) -> float:
        """Рисует раму и проём; возвращает новый y_top (ниже блока)."""
        Wm = float(schematic.get("width_mm") or 0)
        Hm = float(schematic.get("height_mm") or 0)
        if Wm < 10 or Hm < 10:
            return y_top
        max_w_pt, max_h_pt = 420, 200
        scale = min(max_w_pt / Wm, max_h_pt / Hm)
        gw, ght = Wm * scale, Hm * scale
        y_bot = y_top - ght - 3 * mm
        c.setLineWidth(1)
        c.setFillColor(HexColor("#f8fafc"))
        c.setStrokeColor(HexColor("#64748b"))
        c.rect(x0, y_bot, gw, ght, fill=1, stroke=1)
        g = schematic.get("glass") or {}
        try:
            gx = float(g.get("x_mm") or 0) * scale
            gy = float(g.get("y_mm") or 0) * scale
            gww = float(g.get("w_mm") or 0) * scale
            ghh = float(g.get("h_mm") or 0) * scale
        except (TypeError, ValueError):
            gx = gy = gww = ghh = 0
        if gww > 2 and ghh > 2:
            c.setFillColor(HexColor("#e0f2fe"))
            c.setStrokeColor(HexColor("#0284c7"))
            c.rect(x0 + gx, y_bot + gy, gww, ghh, fill=1, stroke=1)
        c.setFont(font_name, 8)
        c.setFillColor(HexColor("#475569"))
        c.drawString(x0, y_bot + ght + 1 * mm, "Фасад %d × %d мм — наполнение и присадка в WEB_QR / на экране" % (int(Wm), int(Hm)))
        return y_bot - 2 * mm

    def draw_glass_mini(x0: float, y_top: float, schematic: Dict[str, Any]) -> float:
        Wm = float(schematic.get("width_mm") or 0)
        Hm = float(schematic.get("height_mm") or 0)
        if Wm < 1 or Hm < 1:
            return y_top
        max_w_pt, max_h_pt = 360, 160
        scale = min(max_w_pt / Wm, max_h_pt / Hm)
        gw, ght = Wm * scale, Hm * scale
        y_bot = y_top - ght - 3 * mm
        c.setFillColor(HexColor("#ecfeff"))
        c.setStrokeColor(HexColor("#0891b2"))
        c.rect(x0, y_bot, gw, ght, fill=1, stroke=1)
        c.setFont(font_name, 8)
        c.setFillColor(HexColor("#164e63"))
        sh = str(schematic.get("shape") or "")
        c.drawString(x0 + 3 * mm, y_bot + ght / 2, "%d × %d мм  %s" % (int(Wm), int(Hm), sh[:24]))
        return y_bot - 2 * mm

    c.setFont(font_name, 14)
    c.setFillColor(HexColor("#0f172a"))
    if y < 24 * mm:
        new_page()
    c.drawString(margin, y, "Цех — заказ № %s" % (data.get("order_id") or "—"))
    y -= 7 * mm
    emit("%s · K %s · %s" % ((data.get("client_name") or "—"), data.get("k_number") if data.get("k_number") is not None else "—", data.get("status") or "—"), 10)

    for p in prods:
        if y < 55 * mm:
            new_page()
        c.setFont(font_name, 12)
        c.setFillColor(HexColor("#0f4c81"))
        c.drawString(margin, y, p.get("title") or "Изделие")
        y -= 5 * mm
        sch = p.get("schematic") if isinstance(p.get("schematic"), dict) else {}
        pk = str(p.get("kind") or "").strip()
        if pk == _KIND_FACADE and sch.get("type") == "facade":
            y = draw_facade_mini(margin, y, sch)
        elif sch.get("type") == "glass":
            y = draw_glass_mini(margin, y, sch)
        steps = p.get("steps") or []
        brief = steps[:3] if pk == _KIND_FACADE else steps[:2]
        for line in brief:
            emit(line, 9, indent_mm=2 * mm)
        y -= 2 * mm

    oid = order_row.get("id")
    cut_lines = cut_detail_lines_for_worker_pdf(int(oid)) if oid else []
    labs = collect_finished_facade_labels_for_order(order_row)
    prof_rem_labs = collect_profile_remnant_labels_for_order(order_row)
    labs_all = (labs or []) + (prof_rem_labs or [])
    all_profile_labs = labs_all
    glass_labs = collect_glass_remnant_labels_for_order(order_row)
    if cut_lines or all_profile_labs or glass_labs:
        y -= 2 * mm
        emit("Раскрой и маркировка", 11)
        emit("Полные схемы листов — WEB_QR. Этикетки: PDF «профили» и «деловые остатки стекла».", 9, indent_mm=1 * mm)
        for line in cut_lines[:10]:
            emit(line, 8, indent_mm=2 * mm)
        if all_profile_labs:
            emit("Профили (брусья):", 9)
            for lb in all_profile_labs[:12]:
                num = lb.get("label_number") or lb.get("unique_number") or "—"
                src = lb.get("stock_brief") or lb.get("source") or "—"
                emit("  № %s — %s (%s)" % (num, lb.get("name") or "—", src), 8, indent_mm=2 * mm)
        if glass_labs:
            emit("Деловой остаток стекла (наклейка на заготовку):", 9)
            for gl in glass_labs[:12]:
                emit(
                    "  № %s — %s × %s мм · %s мм — QR на карточку остатка"
                    % (
                        gl.get("display_no") or gl.get("label_number") or "—",
                        gl.get("width_mm") or "—",
                        gl.get("height_mm") or "—",
                        gl.get("thickness_mm") or "—",
                    ),
                    8,
                    indent_mm=2 * mm,
                )

    c.save()


def write_order_commercial_summary_pdf(order_row: Dict[str, Any], filepath: str) -> None:
    """Один PDF: клиент, дата, таблица по каждому изделию (как в сводке), итог внизу."""
    import os
    import sys
    from datetime import datetime

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from logic.blocks_bundle import bundle_grand_total_rub, bundle_surcharge_aggregate, parse_bundle

    _, products = parse_bundle(order_row.get("blocks_calc_json"))
    if not products:
        raise ValueError("Нет изделий в расчёте для сводной сметы.")

    font_name = "Helvetica"
    font_b = font_name
    try:
        if sys.platform == "win32":
            fp = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "arial.ttf")
            if os.path.isfile(fp):
                font_name = "CommercialSummaryFont"
                font_b = font_name
                pdfmetrics.registerFont(TTFont(font_name, fp))
    except Exception:
        pass

    from calc.db_postgres import fetch_drilling_price_rows, get_raw_connection
    from calc.delivery_calc import fetch_delivery_prices
    from ui.glass_order_overview_dialog import (
        _bundle_products_subtotal_and_services_rub,
        _facade_aux_prices,
        _facade_pricing_rows,
        _glass_pricing_rows,
        _product_position_qty,
        _split_pricing_rows_product_vs_services,
        _sum_price_rows_products_only,
    )

    conn = get_raw_connection()
    try:
        drill = fetch_drilling_price_rows(conn=conn) if conn else fetch_drilling_price_rows()
        delivery_prices = fetch_delivery_prices(conn=conn) if conn else fetch_delivery_prices()
        facade_aux = _facade_aux_prices()
    except Exception:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        raise

    def esc(s):
        t = str(s or "")
        return (
            t.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    created = datetime.now()
    client_name = (order_row.get("client_name") or "—").strip() or "—"
    oid = order_row.get("id")

    story = []
    h2 = ParagraphStyle("h2", fontName=font_b, fontSize=12, spaceAfter=6, leading=14)
    body = ParagraphStyle("b", fontName=font_name, fontSize=9, leading=12)
    cell_style = ParagraphStyle(
        "cell", fontName=font_name, fontSize=8, leading=10, wordWrap="CJK"
    )
    note_style = ParagraphStyle(
        "note", fontName=font_name, fontSize=8, leading=10, wordWrap="CJK"
    )

    if oid:
        story.append(Paragraph("Заказ № %s" % oid, body))
    story.append(Paragraph("Клиент: %s" % esc(client_name), body))
    story.append(
        Paragraph(created.strftime("Дата формирования: %d.%m.%Y %H:%M"), body)
    )
    story.append(Spacer(1, 5 * mm))

    order_services_rows = []

    for i, p in enumerate(products):
        pl = p.get("payload") if isinstance(p.get("payload"), dict) else {}
        kind = str(p.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
        if kind == _KIND_FACADE:
            pr_rows = _facade_pricing_rows(
                pl,
                conn,
                drill,
                1.0,
                aux_prices=facade_aux,
                delivery_prices=delivery_prices,
            )
        else:
            pr_rows = _glass_pricing_rows(pl, conn, drill, 1.0)
        prod_rows, srv_rows = _split_pricing_rows_product_vs_services(pr_rows)
        for sr in srv_rows:
            if sr not in order_services_rows:
                order_services_rows.append(sr)
        pos_qty = _product_position_qty(kind, pl)
        pos_sum_all = int(_sum_price_rows_products_only(pr_rows))
        qv = max(1, int(pos_qty or 1))
        pos_sum = int(round(float(pos_sum_all) / float(qv))) if qv > 1 else pos_sum_all

        heading = _commercial_pdf_product_heading(p, qv)
        story.append(Paragraph(esc(heading), h2))

        data_tbl = [["Позиция", "за 1 шт., ₽", "за все, ₽", "Примечание"]]
        for name, rub, det in prod_rows:
            rub_all = int(rub)
            rub_one = int(round(float(rub_all) / float(qv))) if qv > 1 else rub_all
            note_txt = _commercial_pdf_row_note(
                name, det, _commercial_pdf_note_payload(kind, pl)
            )
            data_tbl.append(
                [
                    Paragraph(esc(str(name)), cell_style),
                    str(rub_one),
                    str(rub_all),
                    Paragraph(esc(note_txt), note_style),
                ]
            )
        if len(data_tbl) == 1:
            data_tbl.append(
                [
                    Paragraph(esc("—"), cell_style),
                    "0",
                    "0",
                    Paragraph(esc("Нет строк расчёта"), note_style),
                ]
            )
        t = Table(
            data_tbl,
            colWidths=[42 * mm, 18 * mm, 18 * mm, 92 * mm],
            repeatRows=1,
        )
        t.setStyle(
            TableStyle(
                [
                    ("FONT", (0, 0), (-1, 0), font_b, 8),
                    ("FONT", (1, 1), (2, -1), font_name, 8),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e3f2fd")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (1, 0), (2, -1), "RIGHT"),
                ]
            )
        )
        story.append(t)
        story.append(
            Paragraph(
                "<b>Итого по позиции (без услуг): %s ₽</b>" % pos_sum
                + (" <i>(за %s шт.: %s ₽)</i>" % (qv, pos_sum_all) if qv > 1 else ""),
                body,
            )
        )
        story.append(Spacer(1, 4 * mm))

    _sub_prod, srv_order = _bundle_products_subtotal_and_services_rub(
        products,
        conn=conn,
        drill=drill,
        markup_factor=1.0,
        delivery_prices=delivery_prices,
        facade_aux_cached=facade_aux,
    )
    sur = int((bundle_surcharge_aggregate(products) or {}).get("total_amount") or 0)
    grand = int(bundle_grand_total_rub(products) or 0)

    if srv_order or order_services_rows:
        story.append(Paragraph("Услуги заказа", h2))
        svc_tbl = [["Услуга", "Сумма, ₽", "Примечание"]]
        seen = set()
        for name, rub, det in order_services_rows:
            key = (name, rub)
            if key in seen:
                continue
            seen.add(key)
            svc_tbl.append([
                Paragraph(esc(str(name)), cell_style),
                str(int(rub)),
                Paragraph(esc((det or "—")[:500]), note_style),
            ])
        if len(svc_tbl) == 1 and srv_order:
            svc_tbl.append(["Услуги (замер, доставка)", str(int(srv_order)), "—"])
        st = Table(svc_tbl, colWidths=[48 * mm, 22 * mm, 100 * mm])
        st.setStyle(
            TableStyle(
                [
                    ("FONT", (0, 0), (-1, -1), font_name, 8),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f5e9")),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ]
            )
        )
        story.append(st)
        story.append(Spacer(1, 3 * mm))

    if sur > 0:
        story.append(Paragraph("Доплаты по позициям: %s ₽" % sur, body))

    story.append(Spacer(1, 4 * mm))
    story.append(
        Paragraph(
            "<b>ИТОГО по заказу: %s ₽</b>" % grand,
            ParagraphStyle(
                "tot",
                parent=body,
                fontName=font_b,
                fontSize=13,
                alignment=TA_CENTER,
                spaceBefore=6,
            ),
        )
    )

    doc = SimpleDocTemplate(
        filepath,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )
    try:
        doc.build(story)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def collect_profile_labels_for_order(order_row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Список профилей фасадов для малых этикеток (как в FacadeOrderDialog)."""
    from db import models as db_models

    raw = order_row.get("blocks_calc_json")
    _, products = parse_bundle(raw if raw is not None else None)
    labels: List[Dict[str, Any]] = []
    colors = facade_side_colors_map()
    order_keys = ("top", "right", "bottom", "left")
    for p in products:
        if str(p.get("kind") or "").strip() != _KIND_FACADE:
            continue
        pl = p.get("payload") if isinstance(p.get("payload"), dict) else {}
        norm = norm_facade_profiles_by_side(pl)
        new_idx_by_ident: Dict[Any, int] = {}
        next_new_n = 1
        for key in order_keys:
            pf = norm.get(key)
            if not isinstance(pf, dict):
                continue
            src = pf.get("_source_stock") or {}
            lbl = src.get("label_number")
            uniq = None
            if not lbl and src.get("stock_id"):
                try:
                    lbl = db_models.ensure_profile_label_number(src.get("stock_id"))
                except Exception:
                    lbl = None
            if src.get("stock_id"):
                try:
                    row_lbl = db_models.get_profile_label_by_stock_id(src.get("stock_id"))
                    if row_lbl:
                        uniq = row_lbl.get("unique_number")
                        if not lbl:
                            lbl = row_lbl.get("label_number")
                except Exception:
                    pass
            ident = _new_profile_identity(pf, src)
            new_no: int | None = None
            if ident is not None:
                if ident not in new_idx_by_ident:
                    new_idx_by_ident[ident] = next_new_n
                    next_new_n += 1
                new_no = new_idx_by_ident[ident]
            kind = str(src.get("kind") or "").strip()
            if kind == "new":
                stock_brief = "Новый профиль (%s)" % new_no if new_no is not None else "Новый профиль"
            elif kind == "warehouse_remnant":
                stock_brief = ("остаток №%s" % (lbl or src.get("stock_id"))) if lbl else "остаток"
            else:
                stock_brief = ("№%s" % lbl) if lbl else "склад"
            labels.append(
                {
                    "name": pf.get("name") or pf.get("series") or "Профиль",
                    "color": pf.get("color") or "",
                    "source": src.get("kind") or "new",
                    "stock_id": src.get("stock_id"),
                    "label_number": lbl,
                    "unique_number": uniq,
                    "side_key": key,
                    "side_ru": _ru_facade_side(key),
                    "stroke": colors.get(key, "#334155"),
                    "new_profile_no": new_no,
                    "stock_brief": stock_brief,
                }
            )
    return labels


def collect_finished_facade_labels_for_order(order_row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Одна этикетка на каждый готовый фасад в заказе."""
    raw = order_row.get("blocks_calc_json")
    _, products = parse_bundle(raw if raw is not None else None)
    out: List[Dict[str, Any]] = []
    idx = 0
    for p in products:
        if str(p.get("kind") or "").strip() != _KIND_FACADE:
            continue
        idx += 1
        pl = p.get("payload") if isinstance(p.get("payload"), dict) else {}
        w = pl.get("Ширина_мм")
        h = pl.get("Высота_мм")
        side_count = len(norm_facade_profiles_by_side(pl))
        if side_count > 0:
            stock_brief = "рама из %s профилей" % side_count
        else:
            stock_brief = "готовый фасад — брусья в расчёте не указаны; этикетка на изделие целиком"
        out.append(
            {
                "name": "Готовый фасад F%s" % idx,
                "color": "%s×%s мм" % (w or "—", h or "—"),
                "source": "facade_finished",
                "stock_id": None,
                "label_number": idx,
                "unique_number": "F%s_O%s" % (idx, int(order_row.get("id") or 0)),
                "side_key": "facade",
                "side_ru": "готовый фасад",
                "stroke": "#0d47a1",
                "new_profile_no": None,
                "stock_brief": stock_brief,
            }
        )
    return out


def _profile_remnant_stock_ids_for_order(order_row: Dict[str, Any]) -> List[int]:
    """Складские id остатков профиля по заказу: история + ссылки из сохранённого расчёта фасада."""
    from db import models as db_models

    oid = int(order_row.get("id") or 0)
    if oid < 1:
        return []
    ordered: List[int] = []
    seen: set[int] = set()
    for sid in db_models.get_profile_remnants_by_order_id(oid) or []:
        try:
            i = int(sid)
        except (TypeError, ValueError):
            continue
        if i > 0 and i not in seen:
            seen.add(i)
            ordered.append(i)
    raw = order_row.get("blocks_calc_json")
    _, products = parse_bundle(raw if raw is not None else None)
    for p in products or []:
        if str(p.get("kind") or "").strip() != _KIND_FACADE:
            continue
        pl = p.get("payload") if isinstance(p.get("payload"), dict) else {}
        for _sk, pf in (pl.get("Профили_по_сторонам") or {}).items():
            if not isinstance(pf, dict):
                continue
            src = pf.get("_source_stock") or {}
            nr = src.get("new_remnant_stock_id")
            if nr is None:
                continue
            try:
                i = int(nr)
            except (TypeError, ValueError):
                continue
            if i > 0 and i not in seen:
                seen.add(i)
                ordered.append(i)
    return ordered


def collect_profile_remnant_labels_for_order(order_row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Этикетки профильных остатков, использованных/созданных по заказу."""
    from db import models as db_models

    oid = int(order_row.get("id") or 0)
    if oid < 1:
        return []
    out: List[Dict[str, Any]] = []
    seen: set[int] = set()
    rem_idx = 0
    # Нужен финальный остаток: если из одного профиля режут несколько сторон,
    # промежуточные остатки не печатаем, печатаем только последний.
    consumed_ids: set[int] = set()
    created_ids: set[int] = set()
    try:
        for ev in db_models.get_profile_remnant_history_by_order_id(oid) or []:
            try:
                sid_ev = int(ev.get("stock_id")) if ev.get("stock_id") is not None else None
            except (TypeError, ValueError):
                sid_ev = None
            act = str(ev.get("action_type") or "").strip()
            if sid_ev and act == "used_in_facade_order":
                consumed_ids.add(sid_ev)
            if sid_ev and act == "remnant_from_facade_cut":
                created_ids.add(sid_ev)
            det = ev.get("details_json") if isinstance(ev.get("details_json"), dict) else {}
            csid = det.get("consumed_stock_id")
            try:
                csid_i = int(csid) if csid is not None else None
            except (TypeError, ValueError):
                csid_i = None
            if csid_i:
                consumed_ids.add(csid_i)
    except Exception:
        pass

    raw = order_row.get("blocks_calc_json")
    _, products = parse_bundle(raw if raw is not None else None)
    for p in products or []:
        if str(p.get("kind") or "").strip() != _KIND_FACADE:
            continue
        pl = p.get("payload") if isinstance(p.get("payload"), dict) else {}
        for _sk, pf in (pl.get("Профили_по_сторонам") or {}).items():
            if not isinstance(pf, dict):
                continue
            src = pf.get("_source_stock") or {}
            sid = src.get("stock_id")
            nr = src.get("new_remnant_stock_id")
            try:
                sid_i = int(sid) if sid is not None else None
            except (TypeError, ValueError):
                sid_i = None
            try:
                nr_i = int(nr) if nr is not None else None
            except (TypeError, ValueError):
                nr_i = None
            if sid_i:
                consumed_ids.add(sid_i)
            if nr_i:
                created_ids.add(nr_i)

    preferred_final_ids = [sid for sid in sorted(created_ids) if sid not in consumed_ids]
    ordered_ids = preferred_final_ids or _profile_remnant_stock_ids_for_order(order_row)

    for sid in ordered_ids:
        try:
            stock_id = int(sid)
        except Exception:
            continue
        if stock_id in seen:
            continue
        seen.add(stock_id)
        row = db_models.get_profile_stock_row(stock_id) or db_models.get_deleted_profile_stock_by_stock_id(stock_id) or {}
        if not row:
            continue
        if not bool(row.get("is_remnant")):
            continue
        rem_idx += 1
        lbl = None
        uniq = None
        try:
            lbl = db_models.ensure_profile_label_number(stock_id)
            lr = db_models.get_profile_label_by_stock_id(stock_id) or {}
            uniq = lr.get("unique_number")
            if not lbl:
                lbl = lr.get("label_number")
        except Exception:
            pass
        uni_store = (str(uniq).strip() if uniq else "") or ("S%s" % int(lbl) if lbl is not None else "")
        disp = uni_store or ("S%s" % stock_id)
        scan_c = disp
        qru = ""
        try:
            from logic.qr_utils import profile_qr_url as _pqu_r

            if str(scan_c).upper().startswith("S"):
                qru = _pqu_r(str(scan_c).strip())
        except Exception:
            pass
        out.append(
            {
                "series": row.get("series") or "",
                "name": row.get("name") or row.get("series") or "Профильный остаток",
                "color": row.get("color") or "",
                "source": "warehouse_remnant",
                "stock_id": stock_id,
                "label_number": lbl,
                "unique_number": scan_c,
                "side_key": "remnant",
                "side_ru": "остаток профиля",
                "stroke": "#64748b",
                "new_profile_no": None,
                "stock_brief": ("остаток №%s" % (disp or lbl or stock_id)),
                "length_mm": row.get("length_mm"),
                "display_no": disp,
                "label_prefix": "S",
                "scan_code": scan_c,
                "qr_url": qru,
            }
        )
    # Фолбэк: если складская история остатка еще не зафиксирована,
    # берем остатки напрямую из сохраненного расчета фасада (rest_mm >= 300).
    fallback_seen: set[Tuple[str, str, str]] = set()
    for p in products or []:
        if str(p.get("kind") or "").strip() != _KIND_FACADE:
            continue
        pl = p.get("payload") if isinstance(p.get("payload"), dict) else {}
        for _sk, pf in (pl.get("Профили_по_сторонам") or {}).items():
            if not isinstance(pf, dict):
                continue
            src = pf.get("_source_stock") or {}
            kind = str(src.get("kind") or "").strip().lower()
            if kind == "new":
                continue
            try:
                rest_mm = int(float(src.get("rest_mm") or 0))
            except (TypeError, ValueError):
                rest_mm = 0
            if rest_mm < 300:
                continue
            sid = src.get("new_remnant_stock_id") or src.get("stock_id")
            sid_i = None
            if sid:
                try:
                    sid_i = int(sid)
                except (TypeError, ValueError):
                    sid_i = None
                if sid_i and sid_i in seen:
                    continue
            nm = str(pf.get("name") or pf.get("series") or "Профильный остаток").strip()
            clr = str(pf.get("color") or "").strip()
            dedupe_key = (nm, clr, str(rest_mm))
            if dedupe_key in fallback_seen:
                continue
            fallback_seen.add(dedupe_key)
            rem_idx += 1
            fb_row = {}
            fb_lbl = None
            fb_uniq = ""
            fb_qr = ""
            if sid_i:
                try:
                    fb_row = db_models.get_profile_stock_row(sid_i) or {}
                    fb_lbl = db_models.ensure_profile_label_number(sid_i)
                    fb_lr = db_models.get_profile_label_by_stock_id(sid_i) or {}
                    fb_uniq = str(fb_lr.get("unique_number") or "").strip() or (
                        ("S%s" % int(fb_lbl)) if fb_lbl is not None else ""
                    )
                    if fb_uniq:
                        try:
                            from logic.qr_utils import profile_qr_url as _pqu_fb

                            fb_qr = _pqu_fb(fb_uniq)
                        except Exception:
                            fb_qr = ""
                except Exception:
                    fb_row = {}
            out.append(
                {
                    "series": (fb_row.get("series") if fb_row else None) or pf.get("series") or "",
                    "name": (fb_row.get("name") if fb_row else None) or nm or "Профильный остаток",
                    "color": (fb_row.get("color") if fb_row else None) or clr,
                    "source": "warehouse_remnant",
                    "stock_id": sid_i,
                    "label_number": fb_lbl,
                    "unique_number": fb_uniq or ("R%s_O%s_TMP%s" % (rem_idx, oid, rem_idx)),
                    "side_key": "remnant",
                    "side_ru": "остаток профиля",
                    "stroke": "#64748b",
                    "new_profile_no": None,
                    "stock_brief": ("остаток ~%s мм" % rest_mm),
                    "length_mm": (fb_row.get("length_mm") if fb_row else None) or rest_mm,
                    "display_no": fb_uniq or ("~%s мм" % rest_mm),
                    "label_prefix": "S" if fb_uniq else "",
                    "scan_code": fb_uniq,
                    "qr_url": fb_qr,
                }
            )
    return out


def collect_order_piece_label_hints(order_id: int) -> List[Dict[str, Any]]:
    """Изделия из сохранённого раскроя (K1, K2…) — куда клеить этикетку на готовом стекле."""
    from db import models as db_models

    oid = int(order_id)
    if oid < 1:
        return []
    rows = db_models.get_cut_results(oid) or []
    out: List[Dict[str, Any]] = []
    k = 0
    for cr in rows:
        lay = cr.get("layout") if isinstance(cr.get("layout"), dict) else {}
        for piece in lay.get("pieces") or []:
            if not isinstance(piece, dict):
                continue
            k += 1
            w, h = piece.get("w"), piece.get("h")
            rec = (piece.get("recipient") or piece.get("recipient_text") or "").strip()
            out.append(
                {
                    "k_index": k,
                    "size": "%s×%s мм" % (w or "—", h or "—"),
                    "recipient": rec or "—",
                    "hint": "Этикетка K%s — на это изделие (видна на схеме раскроя синим прямоугольником)." % k,
                }
            )
    return out


def order_labels_guide_payload(order_row: Dict[str, Any]) -> Dict[str, Any]:
    """Данные для страницы «куда клеить этикетки»: профили, изделия, деловые остатки."""
    oid = int(order_row.get("id") or 0)
    profiles = collect_profile_labels_for_order(order_row)
    facades = collect_finished_facade_labels_for_order(order_row)
    profile_remnants = collect_profile_remnant_labels_for_order(order_row)
    remnants = collect_glass_remnant_labels_for_order(order_row)
    pieces = collect_order_piece_label_hints(oid)
    return {
        "order_id": oid,
        "client_name": order_row.get("client_name"),
        "profiles": profiles,
        "facades": facades,
        "profile_remnants": profile_remnants,
        "pieces": pieces,
        "remnants": remnants,
        "side_colors": facade_side_colors_map(),
        "side_labels_ru": {
            "top": "верх",
            "right": "право",
            "bottom": "низ",
            "left": "лево",
        },
    }


def collect_glass_remnant_labels_for_order(order_row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Деловые остатки стекла по заказу — для печати этикеток (номер как на схеме раскроя)."""
    from db import models as db_models
    from db.connection import get_connection

    oid = int(order_row.get("id") or 0)
    if oid < 1:
        return []
    display_nums = db_models.get_remnant_display_numbers_by_order_id(oid) or []
    rids_ordered: List[int] = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT remnant_id FROM mirror_remnant_history WHERE order_id = %s AND action_type = 'created' ORDER BY id""",
                (oid,),
            )
            for row in cur.fetchall() or []:
                rid = row.get("remnant_id")
                if rid is not None:
                    rids_ordered.append(int(rid))
    if not rids_ordered:
        rids_ordered = [int(x) for x in (db_models.get_remnant_ids_by_order_id(oid) or [])]
    out: List[Dict[str, Any]] = []
    for i, rid in enumerate(rids_ordered):
        rem = db_models.get_remnant_by_id(rid)
        if not rem:
            continue
        disp = display_nums[i] if i < len(display_nums) else None
        if not disp:
            disp = rem.get("label_number") or rem.get("unique_number") or str(rid)
        out.append(
            {
                "display_no": str(disp),
                "label_number": rem.get("label_number"),
                "unique_number": rem.get("unique_number"),
                "name": rem.get("name") or "Деловой остаток",
                "width_mm": rem.get("width_mm"),
                "height_mm": rem.get("height_mm"),
                "thickness_mm": rem.get("thickness_mm"),
            }
        )
    return out


def write_profile_labels_pdf(labels: List[Dict[str, Any]], filepath: str) -> None:
    """Этикетки профиля 100×50 мм — logic.labels (S/P: тип, цвет, длина по центру)."""
    root = _mirror_root_dir()
    candidates = [
        os.path.normpath(os.path.join(root, "..", "FINAL_SERVICE", "logic", "labels.py")),
        os.path.join(root, "logic", "labels.py"),
    ]
    last_err = None
    for p in candidates:
        if not os.path.isfile(p):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_mirror_prof_labels_mod", p)
            mod = importlib.util.module_from_spec(spec)
            assert spec.loader
            spec.loader.exec_module(mod)
            mod.generate_profile_labels_pdf(labels or [], filepath)
            return
        except Exception as e:
            last_err = e
    raise ImportError("Не удалось загрузить generate_profile_labels_pdf: %s" % (last_err,))


def write_glass_remnant_labels_pdf(labels: List[Dict[str, Any]], filepath: str) -> None:
    """Этикетки деловых остатков стекла — тот же PDF 100×50 мм, что в заказе и WEB_SERVICE (logic.labels)."""
    try:
        from db import models as db_models
    except Exception:
        db_models = None
    remnants: List[Dict[str, Any]] = []
    for lb in labels or []:
        rem = None
        if db_models is not None:
            rid = lb.get("remnant_id")
            if rid is not None:
                try:
                    rem = db_models.get_remnant_by_id(int(rid))
                except (TypeError, ValueError):
                    rem = None
            if rem is None and lb.get("unique_number") is not None:
                rem = db_models.get_remnant_by_unique_number(str(lb.get("unique_number")))
        if rem:
            r = dict(rem)
            if r.get("label_number") is None and r.get("id") is not None and db_models is not None:
                try:
                    r["label_number"] = db_models.ensure_remnant_label_number(int(r["id"]))
                except Exception:
                    pass
            remnants.append(r)
        else:
            remnants.append(
                {
                    "unique_number": str(lb.get("unique_number") or lb.get("label_number") or "").strip(),
                    "name": (lb.get("name") or "Деловой остаток").strip(),
                    "height_mm": int(lb.get("height_mm") or 0),
                    "width_mm": int(lb.get("width_mm") or 0),
                    "label_number": lb.get("label_number"),
                    "thickness_mm": lb.get("thickness_mm"),
                    "created_at": lb.get("created_at"),
                }
            )
    _generate_labels_pdf_multi(remnants, [], filepath)
