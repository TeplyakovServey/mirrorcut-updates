# -*- coding: utf-8 -*-
"""Модальный диалог заказа «Продажа»: клиент, позиции, итог, статусы."""
import json
import math
import os
import sys

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem,
    QComboBox, QSpinBox, QTableWidget, QTableWidgetItem, QMessageBox, QFrame
)

from db import models as db_models
from db_main import (
    facades_get_all_profiles,
    facades_get_all_hinges,
    facades_get_all_angle_seal,
)
from calc.delivery_calc import delivery_price_rub, fetch_delivery_prices

_blocks_dir = os.path.normpath(os.path.join(_mp, "BLOCKS"))
if _blocks_dir not in sys.path:
    sys.path.insert(0, _blocks_dir)
from elements.delivery_tile import DeliveryTile

ITEM_TYPE_RU = {
    "profile": "Профиль",
    "hinge": "Петля",
    "corner": "Уголок",
    "seal": "Уплотнитель",
    "screw": "Винт",
    "delivery": "Доставка",
}


def _ceil_money(v: float) -> int:
    return int(math.ceil(float(v)))


class SalesOrderDialog(QDialog):
    def __init__(
        self,
        parent=None,
        sales_order_id=None,
        quick_client_preset=None,
        quick_estimate_mode=False,
    ):
        super().__init__(parent)
        self._sales_order_id = int(sales_order_id) if sales_order_id else None
        self._client_id = None
        self._quick_client_id = None
        self._quick_estimate_mode = bool(quick_estimate_mode)
        self._quick_client_preset = quick_client_preset if isinstance(quick_client_preset, dict) else None
        self._items = []
        self._sales_meta = {}
        self.setWindowTitle("Продажа" + (" №%s" % self._sales_order_id if self._sales_order_id else ""))
        self.resize(1080, 700)
        self._build_ui()
        if self._quick_client_preset:
            try:
                cid = int(self._quick_client_preset.get("client_id"))
            except (TypeError, ValueError):
                cid = None
            try:
                qcid = int(self._quick_client_preset.get("quick_client_id"))
            except (TypeError, ValueError):
                qcid = None
            cname = (self._quick_client_preset.get("client_name") or "").strip()
            if (cid or qcid) and cname:
                self._client_id = cid
                self._quick_client_id = qcid
                self.client_edit.setText(cname)
                self.client_edit.setReadOnly(True)
                self._btn_new_client.setVisible(False)
            elif qcid and not cname:
                rqc = db_models.get_mirror_quick_client_by_id(int(qcid)) or {}
                self._quick_client_id = qcid
                self.client_edit.setText((rqc.get("name") or "").strip())
                self.client_edit.setReadOnly(True)
                self._btn_new_client.setVisible(False)
        if self._sales_order_id:
            self._load_order()

    def _build_ui(self):
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Клиент:"))
        self.client_edit = QLineEdit()
        self.client_edit.setPlaceholderText(
            "Справочник или быстрый клиент…"
            if self._quick_estimate_mode
            else "Введите имя или выберите из подсказки"
        )
        self.client_edit.textChanged.connect(self._on_client_text)
        top.addWidget(self.client_edit, 1)
        self._btn_new_client = QPushButton(
            "Новый (быстрый клиент)" if self._quick_estimate_mode else "Создать клиента"
        )
        self._btn_new_client.clicked.connect(self._on_new_client)
        top.addWidget(self._btn_new_client)
        root.addLayout(top)
        self.client_list = QListWidget()
        self.client_list.setMaximumHeight(120)
        self.client_list.hide()
        self.client_list.itemClicked.connect(self._on_client_pick)
        root.addWidget(self.client_list)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        left = QVBoxLayout()
        body.addLayout(left, 3)
        left.addWidget(QLabel("Состав заказа"))
        self.items_table = QTableWidget(0, 6)
        self.items_table.setHorizontalHeaderLabels(["Тип", "Товар", "Цвет/вариант", "Кол-во", "Цена", "Сумма"])
        self.items_table.setSelectionBehavior(self.items_table.SelectRows)
        left.addWidget(self.items_table, 1)
        row_btns = QHBoxLayout()
        btn_rm = QPushButton("Удалить выбранную позицию")
        btn_rm.clicked.connect(self._remove_selected_item)
        row_btns.addWidget(btn_rm)
        row_btns.addStretch()
        left.addLayout(row_btns)

        right = QVBoxLayout()
        body.addLayout(right, 2)
        right.addWidget(QLabel("Добавить товар"))
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("Профили", "profile")
        self.kind_combo.addItem("Петли", "hinge")
        self.kind_combo.addItem("Уголки", "corner")
        self.kind_combo.addItem("Уплотнители", "seal")
        self.kind_combo.addItem("Винты", "screw")
        self.kind_combo.currentIndexChanged.connect(self._reload_catalog)
        right.addWidget(self.kind_combo)
        self.item_combo = QComboBox()
        right.addWidget(self.item_combo)
        self.profile_mode_combo = QComboBox()
        self.profile_mode_combo.addItem("Профиль 6 м (шт)", "pcs")
        self.profile_mode_combo.addItem("Отрез (метры)", "m")
        right.addWidget(self.profile_mode_combo)
        self.qty_spin = QSpinBox()
        self.qty_spin.setMinimum(1)
        self.qty_spin.setMaximum(10000)
        right.addWidget(self.qty_spin)
        btn_add = QPushButton("Добавить позицию")
        btn_add.clicked.connect(self._add_item)
        right.addWidget(btn_add)
        right.addWidget(QLabel("Доставка (только сервис доставки)"))
        self.delivery_tile = DeliveryTile(self)
        self.delivery_tile.deliveryChanged.connect(self._on_delivery_tile_changed)
        right.addWidget(self.delivery_tile)
        right.addStretch()

        bottom_line = QFrame()
        bottom_line.setFrameShape(QFrame.HLine)
        root.addWidget(bottom_line)

        bot = QHBoxLayout()
        self.total_lbl = QLabel("Итого: 0 ₽")
        bot.addWidget(self.total_lbl)
        bot.addStretch()
        self.status_combo = QComboBox()
        for st in db_models.SALES_STATUS_FLOW:
            self.status_combo.addItem(db_models.sales_status_to_ru(st), st)
        bot.addWidget(QLabel("Статус:"))
        bot.addWidget(self.status_combo)
        btn_status = QPushButton("Обновить статус")
        btn_status.clicked.connect(self._change_status)
        bot.addWidget(btn_status)
        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self._save)
        bot.addWidget(btn_save)
        root.addLayout(bot)
        self._reload_catalog()
        self.delivery_tile.reset_to_defaults()

    def _parse_sales_meta(self, notes_value):
        raw = (notes_value or "").strip()
        if not raw:
            return {}
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _compose_sales_notes(self):
        meta = dict(self._sales_meta or {})
        meta["delivery_block"] = self.delivery_tile.to_selected_block()
        try:
            return json.dumps(meta, ensure_ascii=False)
        except Exception:
            return "{}"

    def _delivery_price(self, blk):
        if not isinstance(blk, dict) or not blk.get("Активирован"):
            return 0
        dd = blk.get("Данные") if isinstance(blk.get("Данные"), dict) else {}
        inside = bool(dd.get("Внутри КАД"))
        km = dd.get("Расстояние до КАД")
        try:
            return int(delivery_price_rub(fetch_delivery_prices(), inside, km) or 0)
        except Exception:
            return 0

    def _sync_delivery_item_from_tile(self):
        blk = self.delivery_tile.to_selected_block()
        keep = [it for it in self._items if str((it or {}).get("item_type") or "") != "delivery"]
        if not isinstance(blk, dict) or not blk.get("Активирован"):
            self._items = keep
            self._refresh_items()
            return
        dd = blk.get("Данные") if isinstance(blk.get("Данные"), dict) else {}
        addr = (dd.get("Адрес") or "").strip()
        price = max(0, int(self._delivery_price(blk)))
        title = "Доставка"
        if addr:
            title = "Доставка: %s" % (addr[:90] + ("..." if len(addr) > 90 else ""))
        keep.append(
            {
                "item_type": "delivery",
                "item_ref_id": None,
                "item_name": title,
                "color": dd.get("Оплата") or "",
                "qty": 1,
                "unit": "pcs",
                "base_price_rub": price,
                "unit_price_rub": price,
                "line_total_rub": price,
            }
        )
        self._items = keep
        self._refresh_items()

    def _on_delivery_tile_changed(self):
        self._sync_delivery_item_from_tile()

    def _on_client_text(self, text):
        if self._quick_client_preset:
            return
        self._client_id = None
        self._quick_client_id = None
        pref = (text or "").strip()
        self.client_list.clear()
        if not pref:
            self.client_list.hide()
            return
        if self._quick_estimate_mode:
            sug = db_models.list_quick_estimate_client_suggestions(pref, limit=40) or []
            rows = sug
        else:
            rows = [
                {"label": str(r.get("name") or "").strip(), "name": str(r.get("name") or "").strip(), "client_id": r.get("id"), "quick_client_id": None}
                for r in (db_models.get_clients_by_prefix(pref) or [])
                if r.get("name")
            ]
        for s in rows[:20]:
            lab = (s.get("label") or s.get("name") or "").strip()
            if not lab:
                continue
            it = QListWidgetItem(lab)
            cid = s.get("client_id")
            try:
                cid = int(cid) if cid is not None else None
            except (TypeError, ValueError):
                cid = None
            qcid = s.get("quick_client_id")
            try:
                qcid = int(qcid) if qcid is not None else None
            except (TypeError, ValueError):
                qcid = None
            it.setData(Qt.UserRole, cid)
            it.setData(Qt.UserRole + 1, qcid)
            it.setData(Qt.UserRole + 2, (s.get("name") or "").strip())
            self.client_list.addItem(it)
        self.client_list.setVisible(bool(rows))

    def _on_client_pick(self, item):
        cid = item.data(Qt.UserRole)
        qcid = item.data(Qt.UserRole + 1)
        canon = (item.data(Qt.UserRole + 2) or "").strip()
        nm = canon or (item.text() or "").strip()
        if " · быстрый" in nm and not canon:
            nm = nm.replace(" · быстрый", "").strip()
        self.client_edit.setText(nm)
        self.client_list.hide()
        try:
            self._client_id = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            self._client_id = None
        try:
            self._quick_client_id = int(qcid) if qcid is not None else None
        except (TypeError, ValueError):
            self._quick_client_id = None
        self._reprice_items_for_client()

    def _on_new_client(self):
        if self._quick_client_preset:
            QMessageBox.information(
                self,
                "Клиент",
                "В быстром просчёте клиент уже задан в начале (быстрый формат).",
            )
            return
        if self._quick_estimate_mode:
            from ui.quick_client_create_dialog import open_quick_client_create_dialog

            meta = open_quick_client_create_dialog(
                self,
                initial_name=(self.client_edit.text() or "").strip(),
                save_to_quick_table_only=True,
            )
            if meta:
                self._client_id = None
                qid = meta.get("quick_client_id")
                try:
                    self._quick_client_id = int(qid) if qid is not None else None
                except (TypeError, ValueError):
                    self._quick_client_id = None
                nm = (meta.get("client_name") or "").strip()
                if nm:
                    self.client_edit.setText(nm)
                self.client_list.hide()
            return
        from ui._mirror_dialogs import _load_dialog
        saved_ui = sys.modules.pop('ui', None)
        try:
            NewClientDialog = _load_dialog('new_client_dialog', 'NewClientDialog')
            if NewClientDialog is None:
                QMessageBox.warning(self, "Клиент", "Не удалось загрузить окно создания клиента.")
                return
            d = NewClientDialog(self, initial_name=(self.client_edit.text() or "").strip())
            if d.exec_() != QDialog.Accepted:
                return
            nm = d.get_saved_name()
            if nm:
                self.client_edit.setText(nm)
                self._client_id = d.get_saved_client_id() or db_models.get_client_id_by_name(nm)
        finally:
            if saved_ui is not None:
                sys.modules['ui'] = saved_ui

    def _client_factor(self):
        if self._quick_client_id:
            row = db_models.get_mirror_quick_client_by_id(int(self._quick_client_id)) or {}
            p = int(row.get("markup_percent") or 0)
            return 1.0 + max(0, p) / 100.0
        if not self._client_id:
            return 1.0
        row = db_models.get_client_by_id(int(self._client_id))
        return float(db_models.client_price_factor(row))

    def _reload_catalog(self):
        self.item_combo.clear()
        kind = self.kind_combo.currentData()
        self.profile_mode_combo.setVisible(kind == "profile")
        if kind == "delivery":
            self.item_combo.addItem(
                "Доставка",
                {"id": None, "name": "Доставка", "color": "", "price": 0.0},
            )
            return
        if kind == "profile":
            for r in facades_get_all_profiles() or []:
                nm = "%s | %s | %s" % (r.get("series") or "", r.get("name") or "", r.get("color") or "")
                self.item_combo.addItem(nm, {"id": r.get("id"), "name": r.get("name"), "color": r.get("color"), "price": float(r.get("price_per_meter") or 0)})
            return
        if kind == "hinge":
            for r in facades_get_all_hinges() or []:
                nm = "%s | %s | %s" % (r.get("number") or "", r.get("name") or "", r.get("color") or "")
                self.item_combo.addItem(nm, {"id": r.get("id"), "name": r.get("name"), "color": r.get("color"), "price": float(r.get("price") or 0)})
            return
        rows = facades_get_all_angle_seal() or []
        if kind == "corner":
            rows = [x for x in rows if "углов" in str(x.get("item_type") or "").lower()]
        elif kind == "seal":
            rows = [x for x in rows if "уплот" in str(x.get("item_type") or "").lower()]
        elif kind == "screw":
            rows = [x for x in rows if "винт" in str(x.get("item_type") or "").lower()]
        for r in rows:
            nm = "%s | %s" % (r.get("item_type") or "", r.get("variant") or "")
            self.item_combo.addItem(nm, {"id": r.get("id"), "name": r.get("item_type"), "color": r.get("variant"), "price": float(r.get("price") or 0)})

    def _add_item(self):
        data = self.item_combo.currentData() or {}
        if not data:
            return
        kind = self.kind_combo.currentData()
        qty = int(self.qty_spin.value())
        unit = self.profile_mode_combo.currentData() if kind == "profile" else "pcs"
        factor = self._client_factor()
        unit_price = _ceil_money(float(data.get("price") or 0) * factor)
        line_total = _ceil_money(unit_price * qty)
        self._items.append(
            {
                "item_type": kind,
                "item_ref_id": data.get("id"),
                "item_name": data.get("name") or "",
                "color": data.get("color") or "",
                "qty": qty,
                "unit": unit,
                "base_price_rub": _ceil_money(float(data.get("price") or 0)),
                "unit_price_rub": unit_price,
                "line_total_rub": line_total,
            }
        )
        self._refresh_items()

    def _base_price_for_item(self, item):
        if (item or {}).get("base_price_rub") is not None:
            return int((item or {}).get("base_price_rub") or 0)
        item_type = str((item or {}).get("item_type") or "").strip().lower()
        ref_id = (item or {}).get("item_ref_id")
        if item_type == "delivery":
            return int((item or {}).get("unit_price_rub") or 0)
        try:
            rid = int(ref_id) if ref_id is not None else None
        except (TypeError, ValueError):
            rid = None
        if rid is None:
            return int((item or {}).get("unit_price_rub") or 0)
        if item_type == "profile":
            for r in facades_get_all_profiles() or []:
                if int(r.get("id") or 0) == rid:
                    return _ceil_money(float(r.get("price_per_meter") or 0))
        elif item_type == "hinge":
            for r in facades_get_all_hinges() or []:
                if int(r.get("id") or 0) == rid:
                    return _ceil_money(float(r.get("price") or 0))
        elif item_type in ("corner", "seal", "screw"):
            for r in facades_get_all_angle_seal() or []:
                if int(r.get("id") or 0) == rid:
                    return _ceil_money(float(r.get("price") or 0))
        return int((item or {}).get("unit_price_rub") or 0)

    def _reprice_items_for_client(self):
        if not self._items:
            return
        factor = self._client_factor()
        changed = False
        for it in self._items:
            base = self._base_price_for_item(it)
            qty = int(it.get("qty") or 0)
            unit_price = _ceil_money(float(base) * factor)
            line_total = _ceil_money(float(unit_price) * qty)
            if int(it.get("unit_price_rub") or 0) != unit_price or int(it.get("line_total_rub") or 0) != line_total:
                changed = True
            it["base_price_rub"] = base
            it["unit_price_rub"] = unit_price
            it["line_total_rub"] = line_total
        if changed:
            self._refresh_items()

    def _refresh_items(self):
        self.items_table.setRowCount(len(self._items))
        total = 0
        for i, it in enumerate(self._items):
            total += int(it.get("line_total_rub") or 0)
            self.items_table.setItem(i, 0, QTableWidgetItem(ITEM_TYPE_RU.get(str(it.get("item_type") or ""), str(it.get("item_type") or ""))))
            self.items_table.setItem(i, 1, QTableWidgetItem(str(it.get("item_name") or "")))
            self.items_table.setItem(i, 2, QTableWidgetItem(str(it.get("color") or "")))
            qtxt = "%s %s" % (it.get("qty") or 0, it.get("unit") or "")
            self.items_table.setItem(i, 3, QTableWidgetItem(qtxt))
            self.items_table.setItem(i, 4, QTableWidgetItem("%s ₽" % int(it.get("unit_price_rub") or 0)))
            self.items_table.setItem(i, 5, QTableWidgetItem("%s ₽" % int(it.get("line_total_rub") or 0)))
        self.total_lbl.setText("Итого: %s ₽" % total)

    def _remove_selected_item(self):
        r = self.items_table.currentRow()
        if r < 0 or r >= len(self._items):
            return
        self._items.pop(r)
        self._refresh_items()

    def _ensure_client(self):
        name = (self.client_edit.text() or "").strip()
        cid = self._client_id
        qcid = self._quick_client_id
        if not cid and not qcid and name:
            cid = db_models.get_client_id_by_name(name)
            if not cid:
                try:
                    qcid = db_models.get_mirror_quick_client_id_by_name(name)
                except Exception:
                    qcid = None
        if not cid and not qcid:
            QMessageBox.warning(self, "Клиент", "Клиент обязателен: выберите из базы или создайте.")
            return None, None, None
        if cid is not None:
            self._client_id = int(cid)
        if qcid is not None:
            self._quick_client_id = int(qcid)
        if not name:
            if self._client_id:
                row = db_models.get_client_by_id(self._client_id) or {}
                name = (row.get("name") or "").strip()
            elif self._quick_client_id:
                row = db_models.get_mirror_quick_client_by_id(self._quick_client_id) or {}
                name = (row.get("name") or "").strip()
            self.client_edit.setText(name)
        self._reprice_items_for_client()
        return self._client_id, self._quick_client_id, name

    def _save(self):
        cid, qcid, cname = self._ensure_client()
        if not cid and not qcid:
            return
        self._sync_delivery_item_from_tile()
        if not self._items:
            QMessageBox.warning(self, "Продажа", "Добавьте хотя бы одну позицию.")
            return
        st = self.status_combo.currentData() or db_models.SALES_STATUS_CALCULATED
        try:
            if not self._sales_order_id:
                self._sales_order_id = db_models.create_sales_order(
                    cname, client_id=cid, quick_client_id=qcid, status=st
                )
            db_models.update_sales_order(
                self._sales_order_id,
                client_name=cname,
                client_id=cid,
                quick_client_id=qcid,
                notes=self._compose_sales_notes(),
                status=st,
                items=self._items,
            )
            if st == db_models.SALES_STATUS_PAID:
                db_models.update_sales_order_status(self._sales_order_id, st)
        except Exception as e:
            QMessageBox.critical(self, "Продажа", str(e))
            return
        self.accept()

    def _change_status(self):
        if not self._sales_order_id:
            QMessageBox.information(self, "Статус", "Сначала сохраните заказ.")
            return
        st = self.status_combo.currentData()
        try:
            db_models.update_sales_order_status(self._sales_order_id, st)
        except Exception as e:
            QMessageBox.warning(self, "Статус", str(e))
            return

    def _load_order(self):
        row = db_models.get_sales_order(self._sales_order_id)
        if not row:
            return
        self._client_id = row.get("client_id")
        try:
            self._quick_client_id = int(row["quick_client_id"]) if row.get("quick_client_id") is not None else None
        except (TypeError, ValueError, KeyError):
            self._quick_client_id = None
        self.client_edit.setText((row.get("client_name") or "").strip())
        self._sales_meta = self._parse_sales_meta(row.get("notes"))
        dblk = self._sales_meta.get("delivery_block")
        if isinstance(dblk, dict):
            self.delivery_tile.apply_saved_block(dblk)
        st = str(row.get("status") or db_models.SALES_STATUS_CALCULATED)
        for i in range(self.status_combo.count()):
            if self.status_combo.itemData(i) == st:
                self.status_combo.setCurrentIndex(i)
                break
        self._items = list(db_models.get_sales_order_items(self._sales_order_id) or [])
        for it in self._items:
            if it.get("base_price_rub") is None:
                it["base_price_rub"] = int(it.get("unit_price_rub") or 0)
        if isinstance(dblk, dict):
            self._sync_delivery_item_from_tile()
        self._refresh_items()
