# -*- coding: utf-8 -*-
"""Модальный диалог заказа «Продажа»: клиент, позиции, итог, статусы."""
import json
import math
import os
import sys
from typing import Optional

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QListWidget, QListWidgetItem,
    QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QMessageBox, QFrame,
    QStackedWidget, QScrollArea, QWidget, QHeaderView, QSizePolicy,
)

from db import models as db_models
from db_main import (
    facades_get_all_profiles,
    facades_get_all_hinges,
    facades_get_all_angle_seal,
    facades_get_all_screws,
    facades_get_all_plates,
    facades_get_all_seal,
    facades_get_all_corners,
)
from calc.delivery_calc import (
    delivery_price_rub,
    fetch_delivery_prices,
    montazh_price_rub,
    zamer_visit_price_rub,
)

_blocks_dir = os.path.normpath(os.path.join(_mp, "BLOCKS"))
if _blocks_dir not in sys.path:
    sys.path.insert(0, _blocks_dir)
from elements.zamer_tile import ZamerTile

try:
    from cfg_loader import color as _cfg_color
except Exception:
    def _cfg_color(section_key):  # noqa: E306
        return {"main_window_bg": "#E8F4FC", "orders_area_bg": "#e3f2fd", "header_bg": "#bbdefb"}.get(
            section_key, "#E8F4FC"
        )

try:
    from window_branding import apply_fraction_window_geometry, apply_window_icon
except Exception:
    apply_fraction_window_geometry = None  # type: ignore
    apply_window_icon = None  # type: ignore


def apply_sales_dialog_chrome(dialog: QDialog, fraction: float = 0.8) -> None:
    """Голубая тема как на главной + ~80% экрана по центру."""
    mwb = _cfg_color("main_window_bg")
    fab = _cfg_color("orders_area_bg")
    hbg = _cfg_color("header_bg")
    dialog.setStyleSheet(
        (
            "QDialog { background-color: %s; }"
            "QWidget { background-color: %s; color: #1a365d; }"
            "QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {"
            "  background-color: %s; border: 1px solid #90caf9; border-radius: 4px;"
            "  padding: 4px 6px; color: #1a365d; min-height: 22px; }"
            "QComboBox QAbstractItemView { background: #ffffff; color: #1a365d;"
            "  selection-background-color: #bbdefb; }"
            + _QTY_SPIN_STYLE
            + "QTableWidget { background-color: #ffffff; gridline-color: #90caf9;"
            "  border: 1px solid #64b5f6; border-radius: 4px; }"
            "QHeaderView::section { background-color: %s; color: #1a365d;"
            "  border: 1px solid #90caf9; padding: 4px; font-weight: 600; }"
            "QPushButton { background-color: %s; border: 1px solid #64b5f6; border-radius: 5px;"
            "  padding: 6px 12px; color: #1a365d; font-weight: 600; }"
            "QPushButton:hover { background-color: #bbdefb; border-color: #1976d2; }"
            "QListWidget { background-color: #ffffff; border: 1px solid #90caf9; }"
            "QFrame[frameShape=\"4\"] { color: #90caf9; }"
        )
        % (mwb, mwb, fab, hbg, hbg)
    )
    if apply_window_icon:
        try:
            apply_window_icon(dialog)
        except Exception:
            pass
    if apply_fraction_window_geometry:
        apply_fraction_window_geometry(dialog, fraction)
        QTimer.singleShot(0, lambda: apply_fraction_window_geometry(dialog, fraction))


class SalesClientBridge(QWidget):
    """Мост к ZamerTile: клиент из поля продажи (как ClientStrip в калькуляторе стекла)."""

    clientIdentityChanged = pyqtSignal()

    def __init__(self, dialog: "SalesOrderDialog", parent=None):
        super().__init__(parent)
        self._dialog = dialog
        self.hide()

    def get_payload(self):
        d = self._dialog
        return {
            "client_id": d._client_id,
            "quick_client_id": d._quick_client_id,
            "name": (d.client_edit.text() or "").strip(),
        }

ITEM_TYPE_RU = {
    "profile": "Профиль",
    "hinge": "Петля",
    "corner": "Уголок",
    "seal": "Уплотнитель",
    "screw": "Винт",
    "plate": "Площадка",
    "delivery": "Доставка",
}

_QTY_SPIN_STYLE = (
    "QSpinBox, QDoubleSpinBox { font-size: 14px; font-weight: 600;"
    " min-height: 26px; max-height: 30px; padding: 2px 6px; }"
)


def _sales_corner_keep(name: str) -> bool:
    """В каталоге продаж — только «уголок малый» и «уголок большой»."""
    n = (name or "").strip().lower()
    return ("малы" in n and "угол" in n) or ("больш" in n and "угол" in n)

UNIT_RU = {"pcs": "штуки", "m": "метры"}
PROFILE_BAR_LENGTH_M = 6.0


def _ceil_money(v: float) -> int:
    return int(math.ceil(float(v)))


def _profile_unit_price(base_per_meter: float, unit: str, factor: float = 1.0) -> int:
    """Цена за единицу: шт = 6 м × ₽/м, отрез = ₽/м."""
    base = float(base_per_meter or 0)
    u = str(unit or "pcs").strip().lower()
    if u == "m":
        return _ceil_money(base * factor)
    return _ceil_money(base * PROFILE_BAR_LENGTH_M * factor)


def _profile_line_total(unit_price: int, qty, unit: str) -> int:
    u = str(unit or "pcs").strip().lower()
    q = float(qty or 0)
    if u == "m":
        return _ceil_money(float(unit_price) * q)
    return int(unit_price) * int(q)


def _fasad_base_dir():
    try:
        from cfg_loader import get_base_dir
        return get_base_dir()
    except Exception:
        return _root


def _img_by_photo_number(photo_number) -> Optional[str]:
    n = str(photo_number or "").strip()
    if not n:
        return None
    base = os.path.join(_fasad_base_dir(), "FASAD", "img")
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = os.path.join(base, n + ext)
        if os.path.isfile(p):
            return p
    return None


def _find_file_under_fasad(subdir_keywords, filenames):
    """Ищет файл в FASAD/img/... если путь папки содержит все keywords."""
    base = os.path.join(_fasad_base_dir(), "FASAD", "img")
    if not os.path.isdir(base):
        return None
    targets = {str(f).lower() for f in filenames if f}
    try:
        for dp, _dns, fns in os.walk(base):
            dpl = str(dp).lower()
            if not all(k in dpl for k in subdir_keywords):
                continue
            for fn in fns or []:
                if str(fn).lower() in targets and os.path.isfile(os.path.join(dp, fn)):
                    return os.path.join(dp, fn)
    except Exception:
        pass
    return None


def _resolve_seal_image_path(variant: str) -> Optional[str]:
    vl = (variant or "").strip().lower()
    key = "черный" if "черн" in vl else "прозрачный"
    fname = "black.png" if key == "черный" else "transparent.png"
    p = _find_file_under_fasad(("уплотн",), (fname,))
    if p:
        return p
    return _find_file_under_fasad(("уплотн",), ("black.png", "transparent.png", "clear.png"))


def _resolve_screw_image_path(color_or_variant: str) -> Optional[str]:
    cv = (color_or_variant or "").strip().lower()
    if "золот" in cv:
        target = "gold.png"
    elif "черн" in cv:
        target = "black.png"
    else:
        target = "silver.png"
    return _find_file_under_fasad(("винт",), (target,))


def _resolve_sales_item_image_path(kind: str, data: dict) -> Optional[str]:
    if not data:
        return None
    kind = str(kind or "").strip().lower()
    if kind == "profile":
        try:
            from ui.facade_profile_dialog import _fasad_img_path

            return _fasad_img_path(
                data.get("photo_number"),
                series=data.get("series"),
                name=data.get("name"),
            )
        except Exception:
            pass
        return None
    if kind == "hinge":
        try:
            from ui.facade_hinge_dialog import resolve_hinge_image_path

            return resolve_hinge_image_path(data.get("photo_number"))
        except Exception:
            pass
        return None
    if kind == "seal":
        return _resolve_seal_image_path(data.get("color") or data.get("variant") or "")
    if kind == "screw":
        return _resolve_screw_image_path(data.get("color") or data.get("variant") or data.get("name") or "")
    if kind in ("corner", "plate"):
        p = _img_by_photo_number(data.get("photo_number"))
        if p:
            return p
        var = str(data.get("color") or data.get("variant") or "").strip()
        if kind == "corner" and var:
            p2 = _img_by_photo_number(var)
            if p2:
                return p2
        return None
    photo = data.get("photo_number")
    if photo:
        return _img_by_photo_number(photo)
    return None


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
        self._build_ui()
        apply_sales_dialog_chrome(self, 0.8)
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
        body.setSpacing(10)

        left = QVBoxLayout()
        left.addWidget(QLabel("Состав заказа"))
        self.items_table = QTableWidget(0, 6)
        self.items_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.items_table.setHorizontalHeaderLabels(["Тип", "Товар", "Цвет/вариант", "Кол-во", "Цена", "Сумма"])
        self.items_table.setSelectionBehavior(self.items_table.SelectRows)
        hdr = self.items_table.horizontalHeader()
        hdr.setStretchLastSection(True)
        for col in range(6):
            hdr.setSectionResizeMode(col, QHeaderView.Stretch)
        left.addWidget(self.items_table, 1)
        row_btns = QHBoxLayout()
        btn_rm = QPushButton("Удалить выбранную позицию")
        btn_rm.clicked.connect(self._remove_selected_item)
        row_btns.addWidget(btn_rm)
        row_btns.addStretch()
        left.addLayout(row_btns)

        right_wrap = QWidget()
        right_wrap.setMinimumWidth(240)
        right_wrap.setMaximumWidth(320)
        right_wrap.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        right = QVBoxLayout(right_wrap)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(6)
        right.addWidget(QLabel("Добавить товар"))
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("Профили", "profile")
        self.kind_combo.addItem("Петли", "hinge")
        self.kind_combo.addItem("Уголки", "corner")
        self.kind_combo.addItem("Уплотнители", "seal")
        self.kind_combo.addItem("Винты", "screw")
        self.kind_combo.addItem("Площадки", "plate")
        self.kind_combo.currentIndexChanged.connect(self._reload_catalog)
        right.addWidget(self.kind_combo)
        self.item_combo = QComboBox()
        self.item_combo.currentIndexChanged.connect(self._update_item_preview)
        self.item_combo.currentIndexChanged.connect(self._update_catalog_price_hint)
        right.addWidget(self.item_combo)
        self._catalog_price_lbl = QLabel("")
        self._catalog_price_lbl.setStyleSheet("color: #1565c0; font-weight: 600;")
        right.addWidget(self._catalog_price_lbl)
        self._item_preview = QLabel()
        self._item_preview.setFixedSize(200, 200)
        self._item_preview.setAlignment(Qt.AlignCenter)
        self._item_preview.setStyleSheet("border: 1px solid #90caf9; background: #ffffff; border-radius: 4px;")
        self._item_preview.setVisible(False)
        right.addWidget(self._item_preview, 0, Qt.AlignHCenter)
        self.profile_mode_combo = QComboBox()
        self.profile_mode_combo.addItem("Профиль 6 м (штуки)", "pcs")
        self.profile_mode_combo.addItem("Отрез (метры)", "m")
        self.profile_mode_combo.currentIndexChanged.connect(self._on_profile_mode_changed)
        right.addWidget(self.profile_mode_combo)
        self._qty_label = QLabel("Количество (штуки):")
        self._qty_label.setStyleSheet("font-weight: 600;")
        right.addWidget(self._qty_label)
        self._qty_stack = QStackedWidget()
        self.qty_spin = QSpinBox()
        self.qty_spin.setMinimum(1)
        self.qty_spin.setMaximum(10000)
        self.qty_spin.setValue(1)
        self.qty_spin.setStyleSheet(_QTY_SPIN_STYLE)
        self.qty_meter_spin = QDoubleSpinBox()
        self.qty_meter_spin.setMinimum(0.1)
        self.qty_meter_spin.setMaximum(10000.0)
        self.qty_meter_spin.setDecimals(1)
        self.qty_meter_spin.setSingleStep(0.1)
        self.qty_meter_spin.setValue(1.0)
        self.qty_meter_spin.setStyleSheet(_QTY_SPIN_STYLE)
        self._qty_stack.addWidget(self.qty_spin)
        self._qty_stack.addWidget(self.qty_meter_spin)
        right.addWidget(self._qty_stack)
        right.addStretch(1)
        btn_add = QPushButton("Добавить позицию")
        btn_add.clicked.connect(self._add_item)
        right.addWidget(btn_add, 0, Qt.AlignBottom)

        body.addLayout(left, 1)
        body.addWidget(right_wrap, 0, Qt.AlignTop)
        root.addLayout(body, 1)

        svc_cap = QLabel("Замер | доставка | монтаж")
        svc_cap.setStyleSheet("font-weight: 700; color: #1565c0; margin-top: 4px;")
        root.addWidget(svc_cap)
        self._client_bridge = SalesClientBridge(self)
        self.zamer_tile = ZamerTile(self._client_bridge, self)
        self.zamer_tile.visitChanged.connect(self._on_zamer_tile_changed)
        zamer_scroll = QScrollArea()
        zamer_scroll.setWidgetResizable(True)
        zamer_scroll.setFrameShape(QFrame.NoFrame)
        zamer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        zamer_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        zamer_scroll.setWidget(self.zamer_tile)
        zamer_scroll.setMinimumHeight(200)
        root.addWidget(zamer_scroll)

        bottom_line = QFrame()
        bottom_line.setFrameShape(QFrame.HLine)
        root.addWidget(bottom_line)

        bot = QHBoxLayout()
        self.total_lbl = QLabel("Итого: 0 ₽")
        bot.addWidget(self.total_lbl)
        bot.addStretch()
        self.status_combo = QComboBox()
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
        self._on_profile_mode_changed()
        self._rebuild_status_combo()
        self.zamer_tile.reset_to_defaults()
        self.zamer_tile.apply_cta_slot_visibility(True, True, True)

    def _on_profile_mode_changed(self, _index=0):
        is_m = self.kind_combo.currentData() == "profile" and self.profile_mode_combo.currentData() == "m"
        self.profile_mode_combo.setVisible(self.kind_combo.currentData() == "profile")
        self._qty_stack.setCurrentIndex(1 if is_m else 0)
        self._qty_label.setText("Количество (метры):" if is_m else "Количество (штуки):")
        self._update_catalog_price_hint()

    def _current_qty_value(self):
        if self.kind_combo.currentData() == "profile" and self.profile_mode_combo.currentData() == "m":
            return float(self.qty_meter_spin.value())
        return int(self.qty_spin.value())

    def _rebuild_status_combo(self):
        flow = db_models.sales_status_flow_for_items(self._items)
        cur = self.status_combo.currentData()
        if not self._sales_order_id and not cur:
            cur = db_models.sales_default_status_for_new_order(self._items)
        self.status_combo.blockSignals(True)
        self.status_combo.clear()
        for st in flow:
            self.status_combo.addItem(db_models.sales_status_to_ru(st), st)
        pick = cur if cur in flow else flow[0] if flow else db_models.SALES_STATUS_CALCULATED
        for i in range(self.status_combo.count()):
            if self.status_combo.itemData(i) == pick:
                self.status_combo.setCurrentIndex(i)
                break
        self.status_combo.blockSignals(False)

    def _parse_sales_meta(self, notes_value):
        raw = (notes_value or "").strip()
        if not raw:
            return {}
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _migrate_delivery_block_to_zamer(dblk):
        if not isinstance(dblk, dict) or not dblk.get("Активирован"):
            return None
        dd = dblk.get("Данные") if isinstance(dblk.get("Данные"), dict) else {}
        visit = dict(dd)
        return {
            "Активирован": True,
            "Данные": {
                "Замер": False,
                "Монтаж": False,
                "Доставка": True,
                "Адрес": (dd.get("Адрес") or "").strip(),
                "Данные выезда": visit,
                "Оплата": (dd.get("Оплата") or "не указано"),
            },
        }

    @staticmethod
    def _visit_inside_and_km(zd: dict):
        vd = zd.get("Данные выезда") if isinstance(zd.get("Данные выезда"), dict) else {}
        inside = bool(vd.get("Внутри КАД", True))
        km = vd.get("Расстояние до КАД")
        try:
            km_i = int(km) if km is not None else None
        except (TypeError, ValueError):
            km_i = None
        return inside, km_i

    def _compose_sales_notes(self):
        meta = dict(self._sales_meta or {})
        if self.zamer_tile.has_any_service():
            meta["zamer_block"] = self.zamer_tile.to_selected_block()
        else:
            meta.pop("zamer_block", None)
        meta.pop("delivery_block", None)
        try:
            return json.dumps(meta, ensure_ascii=False)
        except Exception:
            return "{}"

    def _sync_service_items_from_zamer(self):
        keep = [it for it in self._items if str((it or {}).get("item_type") or "") != "delivery"]
        if not self.zamer_tile.has_any_service():
            self._items = keep
            self._refresh_items()
            return
        blk = self.zamer_tile.to_selected_block()
        zd = blk.get("Данные") if isinstance(blk.get("Данные"), dict) else {}
        prices = fetch_delivery_prices()
        inside, km = self._visit_inside_and_km(zd)
        addr = (zd.get("Адрес") or "").strip()
        pay = (zd.get("Оплата") or "").strip()

        def _append_service(caption: str, price: int):
            title = caption
            if addr:
                title = "%s: %s" % (caption, addr[:90] + ("..." if len(addr) > 90 else ""))
            p = max(0, int(price or 0))
            keep.append(
                {
                    "item_type": "delivery",
                    "item_ref_id": None,
                    "item_name": title,
                    "color": pay,
                    "qty": 1,
                    "unit": "pcs",
                    "base_price_rub": p,
                    "unit_price_rub": p,
                    "line_total_rub": p,
                }
            )

        if zd.get("Доставка"):
            _append_service("Доставка", delivery_price_rub(prices, inside, km))
        if zd.get("Замер"):
            _append_service("Замер (выезд)", zamer_visit_price_rub(prices, inside, km))
        if zd.get("Монтаж"):
            _append_service("Монтаж", montazh_price_rub(prices))
        self._items = keep
        self._refresh_items()

    def _on_zamer_tile_changed(self):
        self._sync_service_items_from_zamer()

    def _notify_client_bridge(self):
        if getattr(self, "_client_bridge", None) is not None:
            self._client_bridge.clientIdentityChanged.emit()

    def _on_client_text(self, text):
        if self._quick_client_preset:
            return
        self._client_id = None
        self._quick_client_id = None
        pref = (text or "").strip()
        self.client_list.clear()
        if not pref:
            self.client_list.hide()
            self._reprice_items_for_client()
            self._update_catalog_price_hint()
            self._notify_client_bridge()
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
        self._try_resolve_client_from_name(pref)
        self._reprice_items_for_client()
        self._update_catalog_price_hint()
        self._notify_client_bridge()

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
        self._update_catalog_price_hint()
        self._notify_client_bridge()

    def _try_resolve_client_from_name(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            self._client_id = None
            self._quick_client_id = None
            return
        cid = db_models.get_client_id_by_name(name)
        if cid:
            self._client_id = int(cid)
            self._quick_client_id = None
            return
        try:
            qcid = db_models.get_mirror_quick_client_id_by_name(name)
        except Exception:
            qcid = None
        if qcid:
            self._quick_client_id = int(qcid)
            self._client_id = None
            return
        self._client_id = None
        self._quick_client_id = None

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
        if not self._client_id and not self._quick_client_id:
            self._try_resolve_client_from_name((self.client_edit.text() or "").strip())
        if self._quick_client_id:
            row = db_models.get_mirror_quick_client_by_id(int(self._quick_client_id)) or {}
            p = int(row.get("markup_percent") or 0)
            return 1.0 + max(0, p) / 100.0
        if not self._client_id:
            return 1.0
        row = db_models.get_client_by_id(int(self._client_id))
        return float(db_models.client_price_factor(row))

    @staticmethod
    def _angle_seal_rows_for(kind: str):
        out = []
        for r in facades_get_all_angle_seal() or []:
            it = str(r.get("item_type") or "").strip().lower()
            if kind == "corner" and "углов" in it:
                out.append(r)
            elif kind == "seal" and "уплот" in it:
                out.append(r)
            elif kind == "screw" and "винт" in it:
                out.append(r)
        return out

    def _reload_catalog(self):
        self.item_combo.clear()
        kind = self.kind_combo.currentData()
        self.profile_mode_combo.setVisible(kind == "profile")
        self._on_profile_mode_changed()
        if kind == "profile":
            for r in facades_get_all_profiles() or []:
                nm = "%s | %s | %s" % (r.get("series") or "", r.get("name") or "", r.get("color") or "")
                self.item_combo.addItem(
                    nm,
                    {
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "color": r.get("color"),
                        "price": float(r.get("price_per_meter") or 0),
                        "photo_number": r.get("photo_number"),
                        "series": r.get("series"),
                    },
                )
            self._update_item_preview()
            return
        if kind == "hinge":
            for r in facades_get_all_hinges() or []:
                nm = "%s | %s | %s" % (r.get("number") or "", r.get("name") or "", r.get("color") or "")
                self.item_combo.addItem(
                    nm,
                    {
                        "id": r.get("id"),
                        "name": r.get("name"),
                        "color": r.get("color"),
                        "price": float(r.get("price") or 0),
                        "photo_number": r.get("photo_number"),
                    },
                )
            self._update_item_preview()
            return
        if kind == "plate":
            for r in facades_get_all_plates() or []:
                nm = "%s | %s | %s" % (r.get("series") or "", r.get("name") or "", r.get("color") or "")
                self.item_combo.addItem(
                    nm,
                    {
                        "id": r.get("id"),
                        "name": r.get("name") or "Площадка",
                        "color": r.get("color") or "",
                        "price": float(r.get("price") or 0),
                        "photo_number": r.get("photo_number"),
                    },
                )
            self._update_item_preview()
            return
        if kind == "screw":
            seen = set()
            for r in facades_get_all_screws() or []:
                col = (r.get("color") or r.get("name") or "").strip()
                key = col.lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                nm = "%s | %s" % (r.get("name") or "Винт", col)
                self.item_combo.addItem(
                    nm,
                    {
                        "id": r.get("id"),
                        "name": r.get("name") or "Винт",
                        "color": col,
                        "price": float(r.get("price") or 0),
                        "photo_number": r.get("photo_number"),
                    },
                )
            for r in self._angle_seal_rows_for("screw"):
                var = str(r.get("variant") or "").strip()
                key = var.lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                self.item_combo.addItem(
                    "Винт | %s" % var,
                    {
                        "id": r.get("id"),
                        "name": "Винт",
                        "color": var,
                        "price": float(r.get("price") or 0),
                        "photo_number": None,
                        "price_source": "angle_seal",
                    },
                )
            self._update_item_preview()
            return
        if kind == "seal":
            seen = set()
            for r in facades_get_all_seal() or []:
                col = (r.get("color") or r.get("name") or "").strip()
                key = col.lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                self.item_combo.addItem(
                    "%s | %s" % (r.get("name") or "Уплотнитель", col),
                    {
                        "id": r.get("id"),
                        "name": r.get("name") or "Уплотнитель",
                        "color": col,
                        "price": float(r.get("price") or 0),
                        "photo_number": r.get("photo_number"),
                    },
                )
            for r in self._angle_seal_rows_for("seal"):
                var = str(r.get("variant") or "").strip()
                key = var.lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                self.item_combo.addItem(
                    "Уплотнитель | %s" % var,
                    {
                        "id": r.get("id"),
                        "name": "Уплотнитель",
                        "color": var,
                        "price": float(r.get("price") or 0),
                        "photo_number": None,
                        "price_source": "angle_seal",
                    },
                )
            self._update_item_preview()
            return
        if kind == "corner":
            seen = set()
            for r in facades_get_all_corners() or []:
                nm = (r.get("name") or r.get("series") or "").strip()
                if not _sales_corner_keep(nm):
                    continue
                key = nm.lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                self.item_combo.addItem(
                    "%s | %s" % (r.get("series") or "", nm),
                    {
                        "id": r.get("id"),
                        "name": nm,
                        "color": r.get("color") or "",
                        "price": float(r.get("price") or 0),
                        "photo_number": r.get("photo_number"),
                    },
                )
            self._update_item_preview()

    def _update_item_preview(self):
        data = self.item_combo.currentData() or {}
        kind = self.kind_combo.currentData()
        if not data or kind in ("delivery", None):
            self._item_preview.clear()
            self._item_preview.setPixmap(QPixmap())
            self._item_preview.setText("")
            self._item_preview.setVisible(False)
            return
        path = _resolve_sales_item_image_path(str(kind or ""), data)
        self._item_preview.setVisible(True)
        if path and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                self._item_preview.setPixmap(
                    pix.scaled(self._item_preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
                self._item_preview.setText("")
                return
        nm = (data.get("name") or "")[:24]
        col = (data.get("color") or "")[:16]
        self._item_preview.setPixmap(QPixmap())
        self._item_preview.setText("%s\n%s" % (nm, col) if col else nm)
        self._update_catalog_price_hint()

    def _update_catalog_price_hint(self):
        data = self.item_combo.currentData() or {}
        kind = self.kind_combo.currentData()
        if not data or kind in ("delivery", None):
            self._catalog_price_lbl.setText("")
            return
        factor = self._client_factor()
        base = float(data.get("price") or 0)
        if kind == "profile":
            unit = self.profile_mode_combo.currentData() or "pcs"
            up = _profile_unit_price(base, unit, factor)
            if unit == "m":
                self._catalog_price_lbl.setText("Цена с наценкой: %s ₽/м" % up)
            else:
                self._catalog_price_lbl.setText("Цена с наценкой: %s ₽/хлыст" % up)
        else:
            up = _ceil_money(base * factor)
            self._catalog_price_lbl.setText("Цена с наценкой: %s ₽/шт" % up)

    def _add_item(self):
        data = self.item_combo.currentData() or {}
        if not data:
            return
        kind = self.kind_combo.currentData()
        qty = self._current_qty_value()
        unit = self.profile_mode_combo.currentData() if kind == "profile" else "pcs"
        factor = self._client_factor()
        base = float(data.get("price") or 0)
        if kind == "profile":
            unit_price = _profile_unit_price(base, unit, factor)
            line_total = _profile_line_total(unit_price, qty, unit)
            store_qty = int(round(float(qty))) if unit == "m" else int(qty)
        else:
            unit_price = _ceil_money(base * factor)
            store_qty = int(qty)
            line_total = _ceil_money(unit_price * store_qty)
        self._items.append(
            {
                "item_type": kind,
                "item_ref_id": data.get("id"),
                "item_name": data.get("name") or "",
                "color": data.get("color") or "",
                "qty": store_qty,
                "unit": unit,
                "base_price_rub": _ceil_money(base),
                "unit_price_rub": unit_price,
                "line_total_rub": line_total,
            }
        )
        self._refresh_items()
        self._rebuild_status_combo()

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
        elif item_type == "screw":
            for r in facades_get_all_screws() or []:
                if int(r.get("id") or 0) == rid:
                    return _ceil_money(float(r.get("price") or 0))
        elif item_type == "plate":
            for r in facades_get_all_plates() or []:
                if int(r.get("id") or 0) == rid:
                    return _ceil_money(float(r.get("price") or 0))
        elif item_type == "seal":
            for r in facades_get_all_seal() or []:
                if int(r.get("id") or 0) == rid:
                    return _ceil_money(float(r.get("price") or 0))
        elif item_type == "corner":
            for r in facades_get_all_corners() or []:
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
            qty = it.get("qty") or 0
            unit = str(it.get("unit") or "pcs").strip().lower()
            if str(it.get("item_type") or "") == "profile":
                unit_price = _profile_unit_price(base, unit, factor)
                line_total = _profile_line_total(unit_price, qty, unit)
            else:
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
            itype = str(it.get("item_type") or "")
            type_lbl = ITEM_TYPE_RU.get(itype, itype)
            if itype == "delivery":
                nm0 = str(it.get("item_name") or "")
                type_lbl = nm0.split(":")[0].strip() if nm0 else type_lbl
            self.items_table.setItem(i, 0, QTableWidgetItem(type_lbl))
            self.items_table.setItem(i, 1, QTableWidgetItem(str(it.get("item_name") or "")))
            self.items_table.setItem(i, 2, QTableWidgetItem(str(it.get("color") or "")))
            u = str(it.get("unit") or "pcs").strip().lower()
            u_ru = UNIT_RU.get(u, u)
            qv = it.get("qty") or 0
            if u == "m":
                qtxt = "%s %s" % (("%g" % float(qv)).rstrip("0").rstrip("."), u_ru)
            else:
                qtxt = "%s %s" % (int(qv), u_ru)
            self.items_table.setItem(i, 3, QTableWidgetItem(qtxt))
            if str(it.get("item_type") or "") == "profile" and u == "m":
                price_lbl = "%s ₽/м" % int(it.get("unit_price_rub") or 0)
            elif str(it.get("item_type") or "") == "profile":
                price_lbl = "%s ₽/хлыст" % int(it.get("unit_price_rub") or 0)
            else:
                price_lbl = "%s ₽/шт" % int(it.get("unit_price_rub") or 0)
            self.items_table.setItem(i, 4, QTableWidgetItem(price_lbl))
            self.items_table.setItem(i, 5, QTableWidgetItem("%s ₽" % int(it.get("line_total_rub") or 0)))
        self.total_lbl.setText("Итого: %s ₽" % total)

    def _remove_selected_item(self):
        r = self.items_table.currentRow()
        if r < 0 or r >= len(self._items):
            return
        self._items.pop(r)
        self._refresh_items()
        self._rebuild_status_combo()

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
        self._sync_service_items_from_zamer()
        if not self._items:
            QMessageBox.warning(self, "Продажа", "Добавьте хотя бы одну позицию.")
            return
        st = self.status_combo.currentData() or db_models.SALES_STATUS_CALCULATED
        if not self._sales_order_id:
            st = db_models.sales_default_status_for_new_order(self._items)
            for i in range(self.status_combo.count()):
                if self.status_combo.itemData(i) == st:
                    self.status_combo.setCurrentIndex(i)
                    break
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
            old_st = None
            if self._sales_order_id:
                row0 = db_models.get_sales_order(self._sales_order_id) or {}
                old_st = str(row0.get("status") or "").strip().lower()
            if st != old_st:
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
            self._rebuild_status_combo()
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
        zblk = self._sales_meta.get("zamer_block")
        if not isinstance(zblk, dict):
            zblk = self._migrate_delivery_block_to_zamer(self._sales_meta.get("delivery_block"))
        if isinstance(zblk, dict):
            self.zamer_tile.apply_saved_block(zblk)
        else:
            self.zamer_tile.reset_to_defaults()
        self._items = list(db_models.get_sales_order_items(self._sales_order_id) or [])
        self._rebuild_status_combo()
        st = str(row.get("status") or db_models.SALES_STATUS_CALCULATED)
        for i in range(self.status_combo.count()):
            if self.status_combo.itemData(i) == st:
                self.status_combo.setCurrentIndex(i)
                break
        for it in self._items:
            if it.get("base_price_rub") is None:
                it["base_price_rub"] = int(it.get("unit_price_rub") or 0)
        if isinstance(zblk, dict) or self.zamer_tile.has_any_service():
            self._sync_service_items_from_zamer()
        self._refresh_items()
        self._notify_client_bridge()
