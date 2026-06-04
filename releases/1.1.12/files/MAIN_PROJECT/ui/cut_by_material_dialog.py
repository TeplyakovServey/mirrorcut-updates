# -*- coding: utf-8 -*-
"""Раскрой по материалу: оплаченные позиции из разных заказов, плитки материалов, чекбоксы по позициям."""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from typing import Any, Dict, List, Set

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QMessageBox,
    QWidget,
    QCheckBox,
    QScrollArea,
    QGridLayout,
    QButtonGroup,
    QSizePolicy,
)
from PyQt5.QtCore import Qt, QTimer

from db import models as db_models
from db.trace import ui as _mc_trace_ui
from logic.blocks_bundle import (
    CUT_SCHEME_CREATED,
    parse_bundle,
    product_is_paid,
    set_bundle_cut_storage_order_id,
    set_products_cut_scheme_status,
)
from mirror_cut_prefill import (
    bundle_product_cut_material_key,
    bundle_product_cut_size_display_mm,
    _glass_block_izmat,
    order_bundle_has_cuttable_glass,
)
from mirror_cut_sys_path import mirror_cut_imports_first
from window_branding import apply_window_icon, apply_fraction_window_geometry


def _piece_uid_for_row(row: Dict[str, Any]) -> str:
    oid = row.get("order_id")
    pid = row.get("product_id") or ""
    inst = max(1, int(row.get("instance_no") or 1))
    return "%s:%s:%d" % (oid if oid is not None else "o", pid, inst - 1)


def _filter_parts_for_placed_uids(
    all_parts: List[Dict[str, Any]], placed_uids: Set[str]
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in all_parts:
        oid = p.get("source_order_id")
        pid = p.get("bundle_product_id") or ""
        qty = max(1, int(p.get("quantity") or 1))
        kept = 0
        for k in range(qty):
            uid = "%s:%s:%d" % (oid if oid is not None else "o", pid, k)
            if uid in placed_uids:
                kept += 1
        if kept > 0:
            pp = dict(p)
            pp["quantity"] = kept
            out.append(pp)
    return out


def _pids_fully_placed_in_session(
    sel: Dict[int, Set[str]],
    checked_uids: Set[str],
    session_uids: Set[str],
) -> Dict[int, Set[str]]:
    """Заказ → id изделий, у которых все отмеченные экземпляры попали в сессию."""
    out: Dict[int, Set[str]] = {}
    for oid, pids in sel.items():
        for pid in pids:
            prefix = "%s:%s:" % (int(oid), pid)
            need = {u for u in checked_uids if u.startswith(prefix)}
            if need and need <= session_uids:
                out.setdefault(int(oid), set()).add(str(pid))
    return out


class CutByMaterialDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        _mc_trace_ui("CutByMaterialDialog: open window")
        self.setWindowTitle("Раскрой по материалу")
        apply_window_icon(self)
        apply_fraction_window_geometry(self, 0.8)
        self._selected_material_key = ""
        self._positions_all_on = True
        self._eligible_rows_cache: List[Dict[str, Any]] = []
        self._material_btn_group = QButtonGroup(self)
        self._material_btn_group.setExclusive(True)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Материал (плитка — как в заказе: тип, цвет, лист, толщина):"))

        self._material_scroll = QScrollArea()
        self._material_scroll.setWidgetResizable(True)
        self._material_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._material_scroll.setMaximumHeight(200)
        self._tile_host = QWidget()
        self._tiles_grid = QGridLayout(self._tile_host)
        self._tiles_grid.setSpacing(8)
        self._tiles_grid.setContentsMargins(4, 4, 4, 4)
        self._material_scroll.setWidget(self._tile_host)
        lay.addWidget(self._material_scroll)

        self._tbl = QTableWidget(0, 5)
        self._tbl.setHorizontalHeaderLabels(
            ["", "Заказ", "Клиент", "№ изд.", "Размер (мм)"]
        )
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        _th = self._tbl.horizontalHeader()
        _th.setStretchLastSection(False)
        _th.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        _th.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        _th.setSectionResizeMode(2, QHeaderView.Stretch)
        _th.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        _th.setSectionResizeMode(4, QHeaderView.Stretch)
        self._tbl.cellDoubleClicked.connect(self._on_row_double_clicked)
        lay.addWidget(self._tbl, 1)

        bot = QHBoxLayout()
        self._selected_count_lbl = QLabel("Выбрано изделий: 0")
        bot.addWidget(self._selected_count_lbl)
        self._btn_toggle_positions = QPushButton("Снять все позиции")
        self._btn_toggle_positions.setToolTip("Переключить отметки всех строк в таблице")
        self._btn_toggle_positions.clicked.connect(self._toggle_all_positions)
        b_run = QPushButton("Раскроить по заказам…")
        b_run.setObjectName("primary")
        b_run.clicked.connect(self._run_cuts)
        b_close = QPushButton("Закрыть")
        b_close.clicked.connect(self.reject)
        bot.addWidget(self._btn_toggle_positions)
        bot.addStretch()
        bot.addWidget(b_run)
        bot.addWidget(b_close)
        lay.addLayout(bot)

        self._rebuild_rows_cache()
        _mc_trace_ui("CutByMaterialDialog: rows cache + tiles loaded")
        self._rebuild_material_tiles()
        self._reload_table()
        _mc_trace_ui("CutByMaterialDialog: table ready")
        self._update_selected_count()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: apply_fraction_window_geometry(self, 0.8))

    def _update_selected_count(self):
        n = len(self._checked_piece_uids())
        self._selected_count_lbl.setText("Выбрано изделий: %d" % n)

    def _on_row_double_clicked(self, row: int, _col: int):
        if row < 0 or row >= self._tbl.rowCount():
            return
        it_ord = self._tbl.item(row, 1)
        if not it_ord:
            return
        oid = it_ord.data(Qt.UserRole)
        if oid is None:
            return
        oid = int(oid)
        rows_all = self._collect_rows()
        order_rows = [r for r in rows_all if int(r.get("order_id") or 0) == oid]
        if not order_rows:
            return
        sel = self._checked_selection()
        checked_pids = sel.get(oid, set())
        from ui.cut_order_products_picker_dialog import CutOrderProductsPickerDialog

        dlg = CutOrderProductsPickerDialog(
            self,
            order_id=oid,
            client_name=str(order_rows[0].get("client") or ""),
            rows=order_rows,
            checked_product_ids=checked_pids,
        )
        dlg.exec_()
        picked = dlg.selected_product_ids()
        for r in range(self._tbl.rowCount()):
            it_o = self._tbl.item(r, 1)
            it_i = self._tbl.item(r, 3)
            if not it_o or not it_i:
                continue
            if int(it_o.data(Qt.UserRole) or 0) != oid:
                continue
            pid = str(it_i.data(Qt.UserRole) or "")
            w = self._tbl.cellWidget(r, 0)
            if w:
                cb = w.findChild(QCheckBox)
                if cb:
                    cb.setChecked(pid in picked)
        self._update_selected_count()

    def _clear_tile_grid(self) -> None:
        while self._tiles_grid.count():
            item = self._tiles_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _eligible_product_rows(self) -> List[Dict[str, Any]]:
        def _qty_for_product(pr: Dict[str, Any]) -> int:
            kind = str(pr.get("kind") or "").strip()
            pl = pr.get("payload") if isinstance(pr.get("payload"), dict) else {}
            if kind == "facade":
                try:
                    return max(1, int(pl.get("Количество") or 1))
                except Exception:
                    return 1
            try:
                izd, _mat = _glass_block_izmat(pl)
                return max(1, int((izd or {}).get("Количество (шт)") or 1))
            except Exception:
                return 1

        out: List[Dict[str, Any]] = []
        for o in db_models.get_orders_all() or []:
            oid = o.get("id")
            if oid is None:
                continue
            raw = o.get("blocks_calc_json")
            _v, products = parse_bundle(raw if raw is not None else None)
            if not products or not order_bundle_has_cuttable_glass(products, db_models):
                continue
            for i, pr in enumerate(products):
                if not product_is_paid(pr):
                    continue
                cst = str(pr.get("cut_scheme_status") or "").strip()
                if cst == CUT_SCHEME_CREATED:
                    continue
                mkey = bundle_product_cut_material_key(pr, db_models)
                if not mkey:
                    continue
                qty = _qty_for_product(pr)
                for inst in range(max(1, int(qty))):
                    out.append(
                        {
                            "order_id": int(oid),
                            "client": (o.get("client_name") or "").strip() or "—",
                            "product_index": i + 1,
                            "product_id": str(pr.get("id") or ""),
                            "material_key": mkey,
                            "size_mm": bundle_product_cut_size_display_mm(pr, db_models),
                            "instance_no": int(inst + 1),
                            "instance_total": int(qty),
                        }
                    )
        return out

    def _rebuild_rows_cache(self) -> None:
        self._eligible_rows_cache = self._eligible_product_rows()

    def _material_keys_in_use(self) -> List[str]:
        keys: Dict[str, int] = {}
        for row in self._eligible_rows_cache:
            k = row["material_key"]
            keys[k] = keys.get(k, 0) + 1
        return sorted(keys.keys(), key=lambda k: (-keys[k], k))

    def _rebuild_material_tiles(self):
        for b in list(self._material_btn_group.buttons()):
            self._material_btn_group.removeButton(b)
            b.deleteLater()
        self._clear_tile_grid()

        keys = self._material_keys_in_use()
        if not keys:
            empty = QLabel("Нет оплаченных позиций со стеклом без схемы раскроя.")
            empty.setWordWrap(True)
            empty.setStyleSheet("color:#666; padding:8px;")
            self._tiles_grid.addWidget(empty, 0, 0)
            self._selected_material_key = ""
            return

        cols = 3
        tile_style = (
            "QPushButton { text-align: left; padding: 10px 12px; border: 2px solid #90caf9; "
            "border-radius: 8px; background: #f5f9ff; font-size: 11px; color: #0d47a1; }"
            "QPushButton:checked { border: 2px solid #1976d2; background: #e3f2fd; font-weight: 600; }"
            "QPushButton:hover { border: 2px solid #42a5f5; }"
        )
        for idx, key in enumerate(keys):
            # QPushButton в PyQt5 не имеет setWordWrap — переносы только через \n в тексте
            btn = QPushButton(key.replace(" · ", "\n· "))
            btn.setCheckable(True)
            btn.setMinimumHeight(72)
            btn.setMinimumWidth(160)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
            btn.setStyleSheet(tile_style)
            btn.setProperty("material_key", key)
            self._material_btn_group.addButton(btn)
            r, c = divmod(idx, cols)
            self._tiles_grid.addWidget(btn, r, c)
            btn.clicked.connect(self._on_material_tile_clicked)

        first = self._material_btn_group.buttons()[0]
        if isinstance(first, QPushButton):
            first.setChecked(True)
            self._selected_material_key = str(first.property("material_key") or keys[0])

    def _on_material_tile_clicked(self):
        b = self.sender()
        if not isinstance(b, QPushButton):
            return
        k = b.property("material_key")
        self._selected_material_key = str(k) if k else ""
        self._positions_all_on = True
        self._reload_table()

    def _collect_rows(self) -> List[Dict[str, Any]]:
        mk = (self._selected_material_key or "").strip()
        if not mk:
            return []
        out: List[Dict[str, Any]] = []
        for row in self._eligible_rows_cache:
            if row["material_key"] == mk:
                out.append(dict(row))
        return out

    def _reload_table(self):
        rows = self._collect_rows()
        self._tbl.setRowCount(len(rows))
        for r, row in enumerate(rows):
            cb = QCheckBox()
            cb.setChecked(self._positions_all_on)
            cb.stateChanged.connect(lambda *_a: self._update_selected_count())
            cw = QWidget()
            hl = QHBoxLayout(cw)
            hl.setContentsMargins(4, 0, 4, 0)
            hl.addWidget(cb)
            hl.setAlignment(Qt.AlignCenter)
            self._tbl.setCellWidget(r, 0, cw)
            it_ord = QTableWidgetItem(str(row["order_id"]))
            it_ord.setData(Qt.UserRole, row["order_id"])
            self._tbl.setItem(r, 1, it_ord)
            self._tbl.setItem(r, 2, QTableWidgetItem(row["client"]))
            it_idx = QTableWidgetItem(str(row["product_index"]))
            it_idx.setData(Qt.UserRole, row["product_id"])
            self._tbl.setItem(r, 3, it_idx)
            self._tbl.setItem(r, 4, QTableWidgetItem(row.get("size_mm") or "—"))
            if int(row.get("instance_total") or 1) > 1:
                self._tbl.setItem(r, 2, QTableWidgetItem("%s (экз. %s/%s)" % (row["client"], row.get("instance_no"), row.get("instance_total"))))
        self._btn_toggle_positions.setText(
            "Снять все позиции" if self._positions_all_on else "Отметить все позиции"
        )
        self._update_selected_count()

    def _toggle_all_positions(self):
        self._positions_all_on = not self._positions_all_on
        for r in range(self._tbl.rowCount()):
            w = self._tbl.cellWidget(r, 0)
            if w:
                cb = w.findChild(QCheckBox)
                if cb:
                    cb.setChecked(self._positions_all_on)
        self._btn_toggle_positions.setText(
            "Снять все позиции" if self._positions_all_on else "Отметить все позиции"
        )
        self._update_selected_count()

    def _checked_selection(self) -> Dict[int, Set[str]]:
        by_order: Dict[int, Set[str]] = defaultdict(set)
        for r in range(self._tbl.rowCount()):
            w = self._tbl.cellWidget(r, 0)
            if not w:
                continue
            cb = w.findChild(QCheckBox)
            if not cb or not cb.isChecked():
                continue
            it = self._tbl.item(r, 1)
            it_idx = self._tbl.item(r, 3)
            if not it or not it_idx:
                continue
            oid = it.data(Qt.UserRole)
            pid = it_idx.data(Qt.UserRole)
            if oid is not None and pid:
                by_order[int(oid)].add(str(pid))
        return dict(by_order)

    def _checked_piece_uids(self) -> Set[str]:
        rows = self._collect_rows()
        out: Set[str] = set()
        for r in range(self._tbl.rowCount()):
            w = self._tbl.cellWidget(r, 0)
            if not w:
                continue
            cb = w.findChild(QCheckBox)
            if not cb or not cb.isChecked():
                continue
            if r < len(rows):
                out.add(_piece_uid_for_row(rows[r]))
        return out

    def _run_cuts(self):
        _mc_trace_ui("CutByMaterialDialog: «Раскроить по заказам…» clicked")
        sel = self._checked_selection()
        if not sel:
            QMessageBox.information(self, "Раскрой", "Отметьте хотя бы одну позицию.")
            return
        from mirror_cut_prefill import cut_prefill_for_main_order
        all_parts: List[Dict[str, Any]] = []
        clients: Set[str] = set()
        anchor_order_id = min(sel.keys()) if sel else None
        _mc_trace_ui(
            "CutByMaterialDialog: loading %d order(s), cut_prefill…" % len(sel)
        )
        for oid, pids in sorted(sel.items()):
            o = db_models.get_order(int(oid))
            if not o:
                continue
            parts, _lock, bclient = cut_prefill_for_main_order(
                dict(o), db_models, product_ids_filter=set(pids)
            )
            if not parts:
                continue
            for p in parts:
                # Для "по материалу" раскрой должен быть единым: не тащим привязку "с этого листа" по каждой позиции,
                # иначе диалог может просить лист отдельно для каждого изделия.
                pp = dict(p)
                pp["chosen_sheet"] = None
                pp["source_order_id"] = int(oid)
                all_parts.append(pp)
            if bclient:
                clients.add(str(bclient).strip())

        if not all_parts:
            QMessageBox.warning(
                self,
                "Раскрой",
                "Нет деталей для раскроя по выбранным позициям.",
            )
            return

        checked_uids = self._checked_piece_uids()
        parts_for_session = all_parts
        mat_label = (self._selected_material_key or "").strip()

        try:
            with mirror_cut_imports_first():
                import importlib
                import logic.cutting_algorithm as _cut_algo

                importlib.reload(_cut_algo)
                compute_cut_session_layouts = _cut_algo.compute_cut_session_layouts
                summarize_cut_session_placement = _cut_algo.summarize_cut_session_placement
                from ui.cut_material_session_dialog import (
                    CutMaterialSessionDialog,
                    expand_parts_to_units,
                    list_stock_sheets_for_material,
                )

                units, _ = expand_parts_to_units(all_parts)
                if not units:
                    QMessageBox.warning(self, "Раскрой", "Нет деталей для раскроя.")
                    return
                mat = (units[0].get("material_name") or "").strip()
                th = int(units[0].get("thickness_mm") or 4)
                if not mat_label:
                    mat_label = "%s · %s мм" % (mat, th)

                def get_sheets(m, t):
                    return list_stock_sheets_for_material(m, t)

                def get_th(m, t):
                    return db_models.get_threshold_for_material(m, t)

                if not get_sheets(mat, th):
                    QMessageBox.warning(
                        self,
                        "Раскрой",
                        "На складе нет материала «%s»: нет целых листов, остатков "
                        "и листов «в работе».\n\n"
                        "Добавьте подходящий лист или остаток на склад и повторите раскрой."
                        % mat_label,
                    )
                    return

                preview = compute_cut_session_layouts(units, get_sheets, get_th)
                summary = summarize_cut_session_placement(
                    units, preview.get("layouts")
                )
                n_tot = int(summary.get("total") or 0)
                n_placed = int(summary.get("placed_count") or 0)

                if n_placed <= 0:
                    QMessageBox.warning(
                        self,
                        "Раскрой",
                        "Ни одно из %d выбранных изделий не помещается на доступные "
                        "листы «в работе», остатки и целые листы для «%s».\n\n"
                        "Проверьте размеры деталей или добавьте подходящий лист на склад."
                        % (n_tot, mat_label),
                    )
                    return

                if n_placed < n_tot:
                    btn = QMessageBox.question(
                        self,
                        "Раскрой",
                        "На складе не хватает места для всех выбранных изделий.\n\n"
                        "Можно разместить только %d из %d.\n"
                        "Продолжить раскрой только для них?\n\n"
                        "Остальные позиции останутся со статусом «Оплачен»."
                        % (n_placed, n_tot),
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.No,
                    )
                    if btn != QMessageBox.Yes:
                        return
                    parts_for_session = _filter_parts_for_placed_uids(
                        all_parts, summary["placed_uids"]
                    )

        except Exception as e:
            detail = str(e)
            try:
                import logic.cutting_algorithm as _ca

                detail += "\n\n(модуль: %s)" % getattr(_ca, "__cutting_algorithm_source__", getattr(_ca, "__file__", "?"))
            except Exception:
                pass
            QMessageBox.critical(self, "Раскрой", detail)
            return

        _mc_trace_ui(
            "CutByMaterialDialog: %d part line(s), open CutMaterialSessionDialog…"
            % len(parts_for_session)
        )
        try:
            with mirror_cut_imports_first():
                import importlib
                import logic.cutting_algorithm as _cut_algo

                importlib.reload(_cut_algo)
                import ui.cut_material_session_dialog as _cms

                importlib.reload(_cms)
                CutMaterialSessionDialog = _cms.CutMaterialSessionDialog

                bundle_client_name = list(clients)[0] if len(clients) == 1 else ""
                d = CutMaterialSessionDialog(
                    self,
                    all_parts=parts_for_session,
                    pin_order_id=anchor_order_id,
                    bundle_client_name=bundle_client_name,
                )
                d.exec_()
                _mc_trace_ui("CutByMaterialDialog: CutMaterialSessionDialog closed")
                saved_oid = getattr(d, "_cut_saved_order_id", None)
                if saved_oid:
                    session_uids = {
                        str(u.get("piece_uid") or "").strip()
                        for u in getattr(d, "_unit_items", []) or []
                    }
                    session_uids.discard("")
                    mark_sel = _pids_fully_placed_in_session(
                        sel, checked_uids, session_uids
                    )
                    for oid, pids in sorted(mark_sel.items()):
                        if not pids:
                            continue
                        raw = (db_models.get_order(int(oid)) or {}).get("blocks_calc_json")
                        new_json = set_products_cut_scheme_status(
                            raw, list(pids), CUT_SCHEME_CREATED
                        )
                        if int(saved_oid) != int(oid):
                            new_json = set_bundle_cut_storage_order_id(
                                new_json, int(saved_oid)
                            )
                        else:
                            new_json = set_bundle_cut_storage_order_id(new_json, None)
                        db_models.update_order_blocks_calc(int(oid), new_json)
                        try:
                            orow = db_models.get_order(int(oid)) or {}
                            st = (orow.get("status") or "").strip().lower()
                            if st in ("draft", "paid"):
                                db_models.set_order_status(int(oid), "in_progress")
                        except Exception:
                            pass
                    self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Раскрой", str(e))
            return
