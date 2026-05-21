# -*- coding: utf-8 -*-
"""Общее сохранение раскроя в БД (заказ + mirror_cut_results), как в CreateCutDialog."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Set, Tuple

from PyQt5.QtWidgets import QMessageBox

from db import models
from logic.cutting_algorithm import recompute_free_rects_from_pieces, refresh_cut_segments_for_layout
from logic.pdf_export import generate_cutting_pdf


def _cut_pdf_fallback_dir():
    try:
        from app_paths import get_base_dir
        return get_base_dir()
    except Exception:
        pass
    try:
        from cfg_loader import get_base_dir
        return get_base_dir()
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _open_cut_output_file(path):
    import sys
    import subprocess
    if sys.platform.startswith("win"):
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError:
            pass
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)


def _remnants_from_layout(layout):
    mat = layout.get('material') or ''
    th = layout.get('thickness_mm', 4)
    return [
        {'name': mat, 'height_mm': r['h'], 'width_mm': r['w'], 'thickness_mm': th}
        for r in (layout.get('business_rects') or [])
    ]


def layouts_to_saved_payload(
    raw_layouts: List[dict],
    order_id: Optional[int],
    min_h: int,
    min_w: int,
) -> Tuple[List[dict], Set[int]]:
    """
    Разнести in_work / чужие листы в БД; вернуть layouts_saved только для новых листов заказа.
    """
    added_to_orders: Set[int] = set()
    new_sheet_layouts: List[dict] = []
    # Несколько листов могут ссылаться на один заказ — не дергаем get_cut_results повторно.
    cut_rows_cache: Dict[int, List] = {}

    def _cut_rows_for_order(ow: int):
        if ow not in cut_rows_cache:
            cut_rows_cache[ow] = models.get_cut_results(ow) or []
        return cut_rows_cache[ow]

    for lay in raw_layouts or []:
        if lay.get('in_work_order_id') is not None and lay.get('in_work_sheet_index') is not None:
            ow = lay['in_work_order_id']
            si = lay['in_work_sheet_index']
            rect = lay.get('in_work_rect') or {}
            ox = int(rect.get('x') or 0)
            oy = int(rect.get('y') or 0)
            rows = _cut_rows_for_order(int(ow))
            if si < len(rows) and isinstance(rows[si].get('layout'), dict):
                existing = rows[si]['layout']
                sw = existing.get('sheet_width') or 0
                sh = existing.get('sheet_height') or 0
                existing_pieces = list(existing.get('pieces') or [])
                new_src = lay.get('in_work_new_pieces_local')
                if new_src is None:
                    new_src = lay.get('pieces') or []
                new_pieces = []
                for p in new_src:
                    np = dict(p)
                    np['x'] = int(p.get('x') or 0) + ox
                    np['y'] = int(p.get('y') or 0) + oy
                    new_pieces.append(np)
                merged_pieces = existing_pieces + new_pieces
                business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, merged_pieces, min_h, min_w)
                merged_layout = dict(existing, pieces=merged_pieces, business_rects=business_rects, waste_rects=waste_rects)
                refresh_cut_segments_for_layout(merged_layout, min_h, min_w)
                models.update_cut_result_layout(ow, si, merged_layout)
                added_to_orders.add(int(ow))
            continue
        st, sid = lay.get('sheet_type'), lay.get('sheet_id')
        existing_usage = models.find_in_progress_sheet_usage(st, sid, exclude_order_id=order_id) if (st and sid is not None) else None
        if existing_usage:
            ow, si = existing_usage
            rows = _cut_rows_for_order(int(ow))
            if si >= 0 and si < len(rows) and isinstance(rows[si].get('layout'), dict):
                existing = rows[si]['layout']
                sw = existing.get('sheet_width') or 0
                sh = existing.get('sheet_height') or 0
                existing_pieces = list(existing.get('pieces') or [])
                new_pieces = [dict(p, x=int(p.get('x') or 0), y=int(p.get('y') or 0)) for p in (lay.get('pieces') or [])]
                merged_pieces = existing_pieces + new_pieces
                business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, merged_pieces, min_h, min_w)
                merged_layout = dict(existing, pieces=merged_pieces, business_rects=business_rects, waste_rects=waste_rects)
                refresh_cut_segments_for_layout(merged_layout, min_h, min_w)
                models.update_cut_result_layout(ow, si, merged_layout)
                added_to_orders.add(int(ow))
            continue
        new_sheet_layouts.append(lay)

    by_sheet: Dict[Tuple[Any, Any], List[dict]] = {}
    for lay in new_sheet_layouts:
        key = (lay.get('sheet_type'), lay.get('sheet_id'))
        by_sheet.setdefault(key, []).append(lay)

    layouts_saved: List[dict] = []
    for _key, group in by_sheet.items():
        if len(group) == 1:
            lay = group[0]
            layouts_saved.append({
                'sheet_type': lay['sheet_type'],
                'sheet_id': lay['sheet_id'],
                'layout': lay,
                'remnants_created': _remnants_from_layout(lay),
            })
        else:
            sw = group[0].get('sheet_width') or 0
            sh = group[0].get('sheet_height') or 0
            all_pieces = []
            for lay in group:
                all_pieces.extend(lay.get('pieces') or [])
            business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, all_pieces, min_h, min_w)
            merged = dict(group[0], pieces=all_pieces, business_rects=business_rects, waste_rects=waste_rects)
            layouts_saved.append({
                'sheet_type': merged['sheet_type'],
                'sheet_id': merged['sheet_id'],
                'layout': merged,
                'remnants_created': _remnants_from_layout(merged),
            })
    return layouts_saved, added_to_orders


def commit_cut_session(
    parent,
    line_items: List[dict],
    session_layouts: List[dict],
    *,
    pin_order_id: Optional[int] = None,
    bundle_client_name: str = "",
    combine_order_id: Optional[int] = None,
    show_result_dialog: bool = True,
    silent: bool = False,
) -> Optional[int]:
    """
    line_items — строки как в PartBlock (с quantity), для add_order_item.
    session_layouts — раскладки листов (как из алгоритма).
    Возвращает order_id при успехе.
    """
    if not session_layouts:
        QMessageBox.warning(parent, "Раскрой", "Нет листов для сохранения.")
        return None

    try:
        th = models.get_threshold_for_material(
            line_items[0]['material_name'] if line_items else '',
            line_items[0].get('thickness_mm', 4) if line_items else 4,
        )
        min_h = (th or {}).get('min_height_mm', 0) or 0
        min_w = (th or {}).get('min_width_mm', 0) or 0
    except Exception:
        min_h, min_w = 0, 0

    order_id: Optional[int] = None
    if combine_order_id is not None:
        order_id = int(combine_order_id)

    layouts_saved, added_to_orders = layouts_to_saved_payload(session_layouts, order_id, min_h, min_w)
    new_sheet_layouts = [x['layout'] for x in layouts_saved]

    if not new_sheet_layouts:
        if order_id is not None:
            msg = "Все изделия размещены на свободных местах листов заказов в работе. Новые целые листы не использованы."
            if added_to_orders:
                msg += "\n\nИзделия добавлены к заказу(ам): %s." % ", ".join("№%s" % o for o in sorted(added_to_orders))
            if not silent:
                QMessageBox.information(parent, "Раскрой", msg)
            return order_id
        if added_to_orders:
            target_order_id = min(added_to_orders)
            for it in line_items:
                et = it.get('edge_treatment') or {}
                models.add_order_item(
                    target_order_id, it['material_name'], it['height_mm'], it['width_mm'],
                    it['quantity'], it.get('recipient_text'),
                    edge_treatment_json=json.dumps(et) if et else None,
                    thickness_mm=it.get('thickness_mm', 4),
                )
            if not silent:
                QMessageBox.information(
                    parent, "Раскрой",
                    "Все изделия размещены на листах в работе.\n\nПозиции добавлены в заказ(ы): %s."
                    % ", ".join("№%s" % o for o in sorted(added_to_orders)),
                )
            return target_order_id
        if not silent:
            QMessageBox.information(parent, "Раскрой", "Все изделия размещены на листах в работе.")
        elif silent:
            QMessageBox.warning(
                parent,
                "Раскрой",
                "Раскрой в заказ не записан: нет новых целых листов для записи, а слияние с листами "
                "«в работе» не выполнилось (несовпадение заказа или индекса листа с базой). "
                "Пересчитайте раскрой или выберите другие листы.",
            )
        return None

    if order_id is None:
        if pin_order_id is not None:
            try:
                order_id = int(pin_order_id)
            except (TypeError, ValueError):
                order_id = None
            if order_id is not None:
                existing_rows = models.get_order_items(order_id) or []
                if not existing_rows:
                    for it in line_items:
                        et = it.get('edge_treatment') or {}
                        models.add_order_item(
                            order_id, it['material_name'], it['height_mm'], it['width_mm'],
                            it['quantity'], it.get('recipient_text'),
                            edge_treatment_json=json.dumps(et) if et else None,
                            thickness_mm=it.get('thickness_mm', 4),
                        )
        if order_id is None:
            client_name = (bundle_client_name or (line_items[0].get('recipient_text') or '').strip() if line_items else '').strip()
            if not client_name:
                QMessageBox.warning(
                    parent, "Раскрой",
                    "Укажите получателя хотя бы у первого изделия — без клиента новый заказ не создаётся.",
                )
                return None
            client_id = models.get_client_id_by_name(client_name) if client_name else None
            try:
                order_id = models.create_order(client_name, client_id=client_id)
            except ValueError as e:
                QMessageBox.warning(parent, "Раскрой", str(e))
                return None
            for it in line_items:
                et = it.get('edge_treatment') or {}
                models.add_order_item(
                    order_id, it['material_name'], it['height_mm'], it['width_mm'],
                    it['quantity'], it.get('recipient_text'),
                    edge_treatment_json=json.dumps(et) if et else None,
                    thickness_mm=it.get('thickness_mm', 4),
                )

    for r in layouts_saved:
        r['layout'].pop('cut_segments', None)
        r['layout'].pop('cut_rows', None)

    try:
        models.save_cut_results(int(order_id), layouts_saved)
    except RuntimeError as e:
        QMessageBox.warning(parent, "Сохранение", str(e))
        return None

    try:
        row_st = models.get_order(int(order_id)) or {}
        st = (row_st.get("status") or "").strip().lower()
        if st in ("draft", "paid"):
            models.set_order_status(int(order_id), "in_progress")
    except Exception:
        pass

    try:
        models.sync_bundle_after_mirror_cut_save(int(order_id), session_layouts)
    except Exception:
        pass

    if added_to_orders and not silent:
        QMessageBox.information(
            parent, "Раскрой",
            "Часть изделий добавлена к заказу(ам) в работе: %s. Остальное сохранено в заказ №%s."
            % (", ".join("№%s" % o for o in sorted(added_to_orders)), order_id),
        )

    if show_result_dialog:
        from ui.cutting_result_dialog import CuttingResultDialog
        order_row = models.get_order(int(order_id)) or {}
        order_info = {
            'id': order_id,
            'order_id': order_id,
            'client_name': (order_row.get('client_name') or '').strip() if order_row else '',
            'created_at': order_row.get('created_at') if order_row else None,
        }
        if combine_order_id is not None:
            order_info['combined_with'] = combine_order_id
        layouts_for_dialog = [x['layout'] for x in layouts_saved]
        res_dlg = CuttingResultDialog(layouts_for_dialog, order_info, parent, results_payload=layouts_saved)

        def save_pdf():
            try:
                from user_settings import get_models_dir
                folder = get_models_dir()
            except Exception:
                folder = None
            folder = folder or _cut_pdf_fallback_dir()
            path = os.path.join(folder, "Карты_раскроя_заказ_%s.pdf" % (order_id or "0"))
            generate_cutting_pdf(res_dlg.layouts, order_info, path)
            QMessageBox.information(parent, "PDF", "Сохранено: %s" % path)
            _open_cut_output_file(path)

        res_dlg.btn_pdf.clicked.connect(save_pdf)
        res_dlg.exec_()

    return int(order_id)
