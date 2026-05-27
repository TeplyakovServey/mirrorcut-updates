# -*- coding: utf-8 -*-
"""Главное окно после авторизации: верхняя панель, область заказов, нижняя панель кнопок."""
import sys
import os
import subprocess
import threading
import shutil
from collections import Counter

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollArea,
    QFrame, QSizePolicy, QDialog, QLineEdit, QStackedWidget,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QInputDialog,
    QMessageBox, QCheckBox, QDialogButtonBox, QApplication, QDateEdit,
    QComboBox, QSpinBox, QHeaderView, QStyledItemDelegate, QStyle, QSlider,
    QProgressBar, QCompleter, QFileDialog, QMenu,
)
from PyQt5.QtCore import Qt, QTimer, QDate, QRect, QEvent, QObject, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QBrush, QPen, QFont, QPalette

from cfg_loader import color, app_cfg, get_cfg_string, get_base_dir
from db_main import (
    ROLE_ADMIN,
    ROLE_MANAGER,
    ROLE_LABELS,
    role_label_desktop,
    get_unapproved_count,
    get_user_by_id,
    order_status_to_ru,
    order_status_allows_production_print,
)
from window_branding import apply_window_icon


class _PagedOrdersScrollArea(QScrollArea):
    """Колесо и шаг страницы — одна «страница» ≈ высота области просмотра (не плавная прокрутка по пикселям)."""

    def wheelEvent(self, ev):  # noqa: N802 — Qt API
        if ev.angleDelta().y() == 0:
            super().wheelEvent(ev)
            return
        bar = self.verticalScrollBar()
        vp = max(120, self.viewport().height())
        delta = vp if ev.angleDelta().y() < 0 else -vp
        bar.setValue(bar.value() + delta)
        ev.accept()

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        vp = max(120, self.viewport().height())
        bar = self.verticalScrollBar()
        bar.setPageStep(vp)
        bar.setSingleStep(vp)


_SERVICE_STATE_ROLE = Qt.UserRole + 20
_ORDER_ROW_SEL_BG = "#5E87FF"
_ORDER_ROW_SEL_FG = "#ffffff"
_ORDER_ACTION_BTN_BG = "#ffffff"
_ORDER_ACTION_BTN_FG = "#212121"
_ORDER_ACTION_BTN_SEL_BG = "#ffffff"
_ORDER_ACTION_BTN_SEL_FG = "#ffffff"


class _PhoneInputBehavior(QObject):
    """Поведение поля телефона: быстрый старт с +7 и возможность стереть код страны."""

    def eventFilter(self, obj, ev):  # noqa: N802
        if not isinstance(obj, QLineEdit):
            return False
        if ev.type() == QEvent.FocusIn:
            txt = obj.text() or ""
            if not txt.strip():
                obj.setText("+7 ")
                obj.setCursorPosition(2)
                return False
            if txt.startswith("+7"):
                obj.setCursorPosition(2)
                return False
        if ev.type() == QEvent.MouseButtonPress:
            txt = obj.text() or ""
            if txt.startswith("+7"):
                QTimer.singleShot(0, lambda w=obj: w.setCursorPosition(2))
                return False
        if ev.type() == QEvent.KeyPress:
            key = ev.key()
            txt = obj.text() or ""
            if key in (Qt.Key_Backspace, Qt.Key_Delete) and txt.startswith("+7") and obj.cursorPosition() <= 3:
                obj.clear()
                return True
        return False

# Таблица заказов: индексы колонок (после вставки «Менеджер»)
_OC_ID = 0
_OC_CLIENT = 1
_OC_MANAGER = 2
_OC_DATE = 3
_OC_TYPE = 4
_OC_PRODUCTS = 5
_OC_TOTAL = 6
_OC_PAYMENT = 7
_OC_SURCHARGE = 8
_OC_STATUS = 9
_OC_MEASURE = 10
_OC_DELIVERY = 11
_OC_INSTALL = 12
_OC_CUT = 13
_OC_ACTIONS = 14

_ORDER_TABLE_ITEM_COLS = (
    _OC_ID,
    _OC_CLIENT,
    _OC_MANAGER,
    _OC_DATE,
    _OC_TYPE,
    _OC_PRODUCTS,
    _OC_TOTAL,
    _OC_PAYMENT,
    _OC_SURCHARGE,
    _OC_STATUS,
    _OC_MEASURE,
    _OC_DELIVERY,
    _OC_INSTALL,
    _OC_CUT,
    _OC_ACTIONS,
)
_ORDER_TABLE_WIDGET_COLS = ()
_CLIENT_ID_ITEM_ROLE = int(Qt.UserRole) + 17

# Базовые ширины колонок таблицы заказов (пропорции при старте и при изменении окна).
_ORDER_TABLE_DEFAULT_WIDTHS = (
    52,
    178,
    108,
    100,
    96,
    68,
    92,
    212,
    170,
    128,
    140,
    140,
    140,
    118,
    132,
)

_FILTER_PAYMENT_ALL = ""
_FILTER_PAYMENT_UNPAID = "__pay_unpaid__"
_FILTER_PAYMENT_PARTIAL = "__pay_partial__"
_FILTER_PAYMENT_METHOD_PREFIX = "method:"

_ORDER_FILTER_STATUS_KEYS = (
    "draft",
    "paid",
    "in_progress",
    "made",
    "checked_qr",
    "shipped",
    "completed",
)


def _build_order_filter_cache_entries(orders, blocks_zamer_by_order):
    """Кэш сумм/кол-ва/интервалов услуг для фильтров (можно вызывать из фонового потока)."""
    from ui.order_tile import order_service_intervals_for_filter

    cache = {}
    conn = None
    try:
        from calc.db_postgres import get_raw_connection, fetch_drilling_price_rows
        from calc.delivery_calc import fetch_delivery_prices
        from ui.glass_order_overview_dialog import _facade_aux_prices, live_bundle_order_base_total_rub
        from logic.blocks_bundle import (
            bundle_grand_total_rub,
            bundle_order_units_total_qty,
            bundle_surcharge_aggregate,
            parse_bundle,
        )

        conn = get_raw_connection()
        drill = fetch_drilling_price_rows(conn=conn) if conn else fetch_drilling_price_rows()
        delivery = fetch_delivery_prices(conn=conn) if conn else fetch_delivery_prices()
        aux = _facade_aux_prices()

        def _totals(o, is_sales):
            if is_sales:
                return int(o.get("total_rub") or 0), int(o.get("_sales_items_count") or 0)
            raw = o.get("blocks_calc_json")
            products = []
            if raw:
                try:
                    _v, products = parse_bundle(str(raw))
                except Exception:
                    products = []
            if not products:
                return None, 0
            base = live_bundle_order_base_total_rub(
                o, products, _conn=conn, _drill=drill, _delivery=delivery, _facade_aux=aux
            )
            if base is None:
                base = int(bundle_grand_total_rub(products) or 0)
            sur = int((bundle_surcharge_aggregate(products) or {}).get("total_amount") or 0)
            return base + sur, bundle_order_units_total_qty(products)

        for o in orders or []:
            rid = o.get("id")
            if rid is None:
                continue
            is_sales = str(o.get("__row_kind") or "").strip().lower() == "sales"
            ck = "sales:%s" % rid if is_sales else "order:%s" % rid
            if is_sales:
                iv = {"measure": (None, None), "delivery": (None, None), "install": (None, None)}
            else:
                try:
                    oid = int(rid)
                except (TypeError, ValueError):
                    oid = None
                pg = (blocks_zamer_by_order or {}).get(oid) if oid is not None else None
                iv = order_service_intervals_for_filter(o, pg)
            tot, cnt = _totals(o, is_sales)
            iv["total"] = tot
            iv["count"] = cnt
            cache[ck] = iv
    except Exception:
        return cache
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return cache


def _order_cache_key_static(row):
    rid = (row or {}).get("id")
    if rid is None:
        return None
    if str((row or {}).get("__row_kind") or "").strip().lower() == "sales":
        return "sales:%s" % rid
    return "order:%s" % rid


def _type_label_from_order_row(o):
    """Тип заказа для фильтра: сначала order_kind из БД, без parse bundle если возможно."""
    if str((o or {}).get("__row_kind") or "").strip().lower() == "sales":
        return "Продажа"
    try:
        from db.models import ORDER_KIND_FACADE, ORDER_KIND_GLASS_MIRROR, ORDER_KIND_MIXED
    except Exception:
        ORDER_KIND_GLASS_MIRROR = "glass_mirror"
        ORDER_KIND_FACADE = "facade"
        ORDER_KIND_MIXED = "mixed"
    ol = str(o.get("order_kind") or "").strip()
    if ol == ORDER_KIND_GLASS_MIRROR:
        return "Стекло / зеркало"
    if ol == ORDER_KIND_FACADE:
        return "Фасады"
    if ol == ORDER_KIND_MIXED:
        return "Смешанный"
    raw = o.get("blocks_calc_json")
    if not raw or not str(raw).strip():
        return "—"
    try:
        from logic.blocks_bundle import parse_bundle
    except Exception:
        return "—"
    try:
        _v, products = parse_bundle(str(raw))
    except Exception:
        products = []
    kinds = []
    for pr in products or []:
        kinds.append(str(pr.get("kind") or "glass_mirror").strip() or "glass_mirror")
    if not kinds:
        return "—"
    uniq = set(kinds)
    if len(uniq) == 1:
        k = next(iter(uniq))
        if k == "glass_mirror":
            return "Стекло / зеркало"
        if k == "facade":
            return "Фасады"
        return k
    return "Смешанный"


def _build_order_row_meta_light(orders, clients_by_id, clients_by_name, cut_ids):
    """Поля для мгновенной фильтрации без SQL (тяжёлые payment/surcharge — в фоне)."""
    from db_main import order_status_to_ru

    try:
        from db import models as db_models
    except Exception:
        db_models = None
    cut_set = set(cut_ids or [])
    meta = {}
    for o in orders or []:
        ck = _order_cache_key_static(o)
        if not ck:
            continue
        is_sales = str(o.get("__row_kind") or "").strip().lower() == "sales"
        cid = o.get("client_id")
        try:
            cid = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid = None
        c = (clients_by_id or {}).get(cid) if cid is not None else None
        if not c:
            nm = str(o.get("client_name") or "").strip().lower()
            if nm:
                c = (clients_by_name or {}).get(nm)
        c = c or {}
        ph = str(c.get("phone") or "")
        inn = str(c.get("inn") or "")
        if is_sales:
            st = str(o.get("status") or "").strip().lower()
            if st == "paid":
                pay_lc = "полностью оплачено"
            elif st == "calculated":
                pay_lc = "не оплачен"
            elif db_models:
                pay_lc = db_models.sales_status_to_ru(st).lower()
            else:
                pay_lc = st
            sur_lc = ""
        else:
            pay_lc = ""
            sur_lc = ""
        st_lc = (
            db_models.sales_status_to_ru(o.get("status")).lower()
            if is_sales and db_models
            else order_status_to_ru(o.get("status")).lower()
        )
        oid = o.get("id")
        if is_sales:
            cut_cell = "—"
        else:
            st = (o.get("status") or "").strip().lower()
            if st not in (
                "paid",
                "in_progress",
                "made",
                "checked_qr",
                "shipped",
                "completed",
            ):
                cut_cell = "—"
            else:
                try:
                    cut_cell = "да" if int(oid) in cut_set else "нет"
                except (TypeError, ValueError):
                    cut_cell = "нет"
        meta[ck] = {
            "type_label": _type_label_from_order_row(o),
            "id_str": str(o.get("id") or "").lower(),
            "client_lc": str(o.get("client_name") or "").lower(),
            "manager_lc": "",
            "status_lc": st_lc,
            "cut_cell": cut_cell,
            "phone_digits": "".join(ch for ch in ph if ch.isdigit()),
            "phone_lc": ph.lower(),
            "inn_digits": "".join(ch for ch in inn if ch.isdigit()),
            "inn_lc": inn.lower(),
            "payment_lc": pay_lc,
            "surcharge_lc": sur_lc,
        }
        sn = (o.get("creator_surname") or "").strip().lower()
        fn = (o.get("creator_name") or "").strip().lower()
        lg = (o.get("created_by_login") or "").strip().lower()
        meta[ck]["manager_lc"] = ("%s %s" % (sn, fn)).strip() or lg
    return meta


def _build_order_row_meta_heavy(orders, production_events_by_order=None):
    """Оплата, доплаты, статусы по изделиям — parse bundle, только в фоновом потоке."""
    from db_main import order_status_to_ru

    try:
        from logic.blocks_bundle import (
            PAYMENT_UNPAID,
            bundle_payment_aggregate,
            bundle_surcharge_aggregate,
            bundle_status_unit_counts,
            parse_bundle,
            product_is_paid,
        )
    except Exception:
        return {}

    pe_map = production_events_by_order or {}
    meta = {}

    def _facade_count(oid_i, idx1, ev):
        try:
            from db import models as db_models

            return int(
                db_models.count_facade_instance_assembled_events(
                    oid_i, idx1, production_events=ev
                )
            )
        except Exception:
            return 0

    for o in orders or []:
        ck = _order_cache_key_static(o)
        if not ck:
            continue
        is_sales = str(o.get("__row_kind") or "").strip().lower() == "sales"
        if is_sales:
            st = str(o.get("status") or "").strip().lower()
            pay_state = "full" if st == "paid" else "unpaid"
            meta[ck] = {
                "payment_lc": "полностью оплачено" if st == "paid" else "не оплачен",
                "surcharge_lc": "",
                "payment_state": pay_state,
                "payment_methods": [],
                "status_counts": {st: 1},
                "status_total": 1,
                "status_lc": order_status_to_ru(st).lower(),
                "sort_payment": (2 if st == "paid" else 0, 1 if st == "paid" else 0, 1),
                "sort_surcharge": (0, 0, 0),
                "sort_status": (1.0 if st == "paid" else -1.0, st),
                "status_display": order_status_to_ru(st).lower(),
                "payment_paid": 1 if st == "paid" else 0,
                "payment_total": 1,
                "surcharge_total": 0,
                "surcharge_paid": 0,
                "cut_x": 0,
                "cut_y": 0,
            }
            continue
        raw = o.get("blocks_calc_json")
        products = []
        if raw and str(raw).strip():
            try:
                _v, products = parse_bundle(str(raw))
            except Exception:
                products = []
        if not products:
            meta[ck] = {
                "payment_lc": "",
                "surcharge_lc": "",
                "payment_state": "unpaid",
                "payment_methods": [],
                "status_counts": {},
                "status_total": 0,
                "sort_payment": (0, 0, 0),
                "sort_surcharge": (0, 0, 0),
                "sort_status": (-1.0, str(o.get("status") or "")),
                "payment_paid": 0,
                "payment_total": 0,
                "surcharge_total": 0,
                "surcharge_paid": 0,
                "cut_x": 0,
                "cut_y": 0,
            }
            continue
        pay_bits = []
        sagg = {"total_amount": 0, "paid_amount": 0, "lines_ru": []}
        pay_state = "unpaid"
        pay_methods = []
        agg = {"state": "unpaid", "paid_count": 0, "total_count": 0, "lines_ru": []}
        try:
            agg = bundle_payment_aggregate(products)
            pay_state = str(agg.get("state") or "unpaid")
            if pay_state == "partial":
                pay_bits.append("%s/%s" % (agg.get("paid_count", 0), agg.get("total_count", 0)))
            if pay_state == "full":
                pay_bits.append("полностью")
                pay_bits.extend(agg.get("lines_ru") or [])
            if pay_state == "unpaid":
                pay_bits.append("не оплачен")
            for p in products:
                if product_is_paid(p):
                    m = str(p.get("payment_type") or PAYMENT_UNPAID).strip()
                    if m and m != PAYMENT_UNPAID and m not in pay_methods:
                        pay_methods.append(m)
        except Exception:
            pass
        sur_bits = []
        try:
            sagg = bundle_surcharge_aggregate(products)
            amt = int(sagg.get("total_amount") or 0)
            if amt > 0:
                sur_bits.append("%s ₽" % amt)
                sur_bits.append("%s из %s" % (int(sagg.get("paid_amount") or 0), amt))
                pt = int(sagg.get("positions_total") or 0)
                pp = int(sagg.get("positions_paid") or 0)
                sur_bits.append("%d/%d" % (pp, pt))
                sur_bits.extend(sagg.get("lines_ru") or [])
        except Exception:
            pass
        fallback = str(o.get("status") or "draft").strip() or "draft"
        oid = o.get("id")
        ev = None
        try:
            ev = pe_map.get(int(oid)) if oid is not None else None
        except (TypeError, ValueError):
            ev = None
        try:
            status_counts, status_total = bundle_status_unit_counts(
                products,
                order_fallback_status=fallback,
                order_id=int(oid) if oid is not None else None,
                facade_production_events=ev,
                count_facade_assembled=_facade_count if oid is not None else None,
            )
        except Exception:
            status_counts, status_total = {}, 0
        st_ord = {"unpaid": 0, "partial": 1, "full": 2}
        pay_sort = (
            st_ord.get(pay_state, 0),
            int(agg.get("paid_count") or 0),
            int(agg.get("total_count") or 0),
        )
        try:
            sagg = bundle_surcharge_aggregate(products)
            sur_sort = (
                int(sagg.get("total_amount") or 0),
                int(sagg.get("positions_paid") or 0),
                int(sagg.get("positions_total") or 0),
            )
        except Exception:
            sur_sort = (0, 0, 0)
        try:
            pt = max(1, int(agg.get("total_count") or 0))
            pc = int(agg.get("paid_count") or 0)
            status_frac = pc / float(pt) if agg.get("state") != "unpaid" else -1.0
        except Exception:
            status_frac = -1.0
        from db_main import order_status_to_ru

        st_disp = []
        for st, cnt in sorted(
            (status_counts or {}).items(), key=lambda kv: (-int(kv[1]), str(kv[0]))
        ):
            st_disp.append(
                "%s %d/%d"
                % (order_status_to_ru(st), int(cnt), max(1, int(status_total or 0)))
            )
        cut_x, cut_y = 0, 0
        if fallback.lower() in (
            "paid",
            "in_progress",
            "made",
            "checked_qr",
            "shipped",
            "completed",
        ):
            try:
                from logic.blocks_bundle import bundle_cut_scheme_counts
                from db import models as db_models

                cut_x, cut_y = bundle_cut_scheme_counts(products, db_models)
            except Exception:
                cut_x, cut_y = 0, 0
        meta[ck] = {
            "payment_lc": " ".join(pay_bits).lower(),
            "surcharge_lc": " ".join(sur_bits).lower(),
            "payment_state": pay_state,
            "payment_paid": int(agg.get("paid_count") or 0),
            "payment_total": int(agg.get("total_count") or 0),
            "payment_methods": pay_methods,
            "surcharge_total": int(sagg.get("total_amount") or 0),
            "surcharge_paid": int(sagg.get("paid_amount") or 0),
            "status_counts": dict(status_counts or {}),
            "status_total": int(status_total or 0),
            "status_display": " · ".join(st_disp) if st_disp else order_status_to_ru(fallback),
            "sort_payment": pay_sort,
            "sort_surcharge": sur_sort,
            "sort_status": (status_frac, str(o.get("status") or "")),
            "cut_x": int(cut_x),
            "cut_y": int(cut_y),
        }
    return meta


class _OrderFilterCacheThread(QThread):
    """Фоновый пересчёт кэша фильтров — не блокирует UI при загрузке списка заказов."""

    cache_ready = pyqtSignal(dict, dict, int)

    def __init__(self, generation, orders, blocks_zamer, production_events, parent=None):
        super().__init__(parent)
        self._generation = int(generation)
        self._orders = list(orders or [])
        self._blocks_zamer = dict(blocks_zamer or {})
        self._production_events = dict(production_events or {})

    def run(self):
        try:
            cache = _build_order_filter_cache_entries(self._orders, self._blocks_zamer)
            heavy = _build_order_row_meta_heavy(self._orders, self._production_events)
        except Exception:
            cache = {}
            heavy = {}
        self.cache_ready.emit(cache, heavy, self._generation)


class _DeleteOrderSliderDialog(QDialog):
    """Ползунок до конца + кнопка «Удалить» перед финальным вопросом."""

    def __init__(self, order_id: int, client_hint: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Удаление заказа")
        lay = QVBoxLayout(self)
        lay.addWidget(
            QLabel(
                "Потяните ползунок до конца, затем нажмите «Удалить заказ».\n"
                "Заказ №%s — %s"
                % (order_id, (client_hint or "—").strip() or "—")
            )
        )
        sl = QSlider(Qt.Horizontal)
        sl.setRange(0, 100)
        lay.addWidget(sl)
        bb = QDialogButtonBox()
        self._btn_del = bb.addButton("Удалить заказ", QDialogButtonBox.AcceptRole)
        self._btn_del.setEnabled(False)
        sl.valueChanged.connect(lambda v: self._btn_del.setEnabled(v >= 96))
        bb.addButton(QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class _OrderServicePortalDelegate(QStyledItemDelegate):
    """Портал «Замер/Доставка/Монтаж» без вложенных QFrame — один paint, без мерцания виджетов."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._win = main_window

    _BG = {
        "none": "#ffcdd2",
        "done": "#e8f5e9",
        "progress": "#fff3e0",
        "pending": "#e3f2fd",
    }
    _DOT = {
        "done": "#2e7d32",
        "progress": "#f57c00",
        "pending": "#1565c0",
    }

    def paint(self, painter, option, index):  # noqa: N802
        painter.save()
        st = index.data(_SERVICE_STATE_ROLE) or "pending"
        rect = QRect(option.rect)
        selected = self._win._orders_table_row_is_selected(index.row())
        if selected:
            painter.fillRect(rect, QColor(_ORDER_ROW_SEL_BG))
            painter.setPen(QColor(_ORDER_ROW_SEL_FG))
        else:
            painter.fillRect(rect, QBrush(QColor(self._BG.get(st, "#e3f2fd"))))
            if st == "none":
                painter.setPen(QPen(QColor("#e57373"), 1))
                painter.setBrush(Qt.NoBrush)
                painter.drawRect(rect.adjusted(0, 0, -1, -1))
            painter.setPen(QColor("#212121"))
        text = str(index.data(Qt.DisplayRole) or "")
        dot_reserve = 20
        tr = rect.adjusted(3, 2, -dot_reserve, -2)
        painter.drawText(tr, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, text)
        cx = rect.right() - dot_reserve + 2
        cy = rect.center().y() - 5
        sz = 10
        if st == "none":
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#b71c1c"), 2))
            painter.drawEllipse(cx, cy, sz, sz)
        else:
            dc = QColor(self._DOT.get(st, "#1565c0"))
            painter.setBrush(QBrush(dc))
            if selected:
                painter.setPen(QPen(QColor("#ffffff"), 2))
            else:
                painter.setPen(QPen(dc.darker(130), 1))
            painter.drawEllipse(cx, cy, sz, sz)
        painter.restore()


class _OrderActionsDelegate(QStyledItemDelegate):
    """Три кнопки в ячейке «Действия» (без QWidget — совместимо с быстрой сортировкой)."""

    _LABELS_ADMIN = ("Статус", "+", "Удал")
    _LABELS_USER = ("Статус", "+")

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._win = main_window

    def _is_admin(self):
        return self._win._user.get("role") == ROLE_ADMIN

    def _labels(self):
        return self._LABELS_ADMIN if self._is_admin() else self._LABELS_USER

    def _row_selected(self, index):
        return self._win._orders_table_row_is_selected(index.row())

    @staticmethod
    def _zone_rects(cell_rect, n):
        m = 2
        gap = 3
        inner = cell_rect.adjusted(m, m, -m, -m)
        w = max(1, (inner.width() - gap * (n - 1)) // n)
        out = []
        x = inner.left()
        for i in range(n):
            rw = w if i < n - 1 else inner.right() - x + 1
            out.append(QRect(x, inner.top(), rw, inner.height()))
            x += rw + gap
        return out

    def _hit_zone(self, pos, cell_rect):
        zones = self._zone_rects(cell_rect, len(self._labels()))
        for i, zr in enumerate(zones):
            if zr.contains(pos):
                return i
        return -1

    def paint(self, painter, option, index):  # noqa: N802
        painter.save()
        rect = QRect(option.rect)
        selected = self._row_selected(index)
        if selected:
            painter.fillRect(rect, QColor(_ORDER_ROW_SEL_BG))
        else:
            painter.fillRect(rect, QColor("#fafafa"))
        labels = self._labels()
        zones = self._zone_rects(rect, len(labels))
        f = painter.font()
        f.setPointSize(8)
        painter.setFont(f)
        for i, (lab, zr) in enumerate(zip(labels, zones)):
            if selected:
                painter.setPen(QPen(QColor("#ffffff"), 1))
                painter.setBrush(QColor("#ffffff"))
                txt_col = QColor(_ORDER_ROW_SEL_BG)
            else:
                painter.setPen(QPen(QColor("#bdbdbd"), 1))
                painter.setBrush(QColor(_ORDER_ACTION_BTN_BG))
                txt_col = QColor(_ORDER_ACTION_BTN_FG)
            painter.drawRoundedRect(zr, 3, 3)
            painter.setPen(txt_col)
            painter.drawText(zr, Qt.AlignCenter, lab)
        painter.restore()

    def editorEvent(self, event, model, option, index):  # noqa: N802
        if event.type() != QEvent.MouseButtonRelease or event.button() != Qt.LeftButton:
            return False
        o = self._win._order_for_table_row(index.row())
        if o is None:
            return False
        zone = self._hit_zone(event.pos(), option.rect)
        if zone < 0:
            return False
        od = dict(o)
        lock_reason = self._win._order_measure_lock_reason(od.get("id"))
        labels = self._labels()
        if zone >= len(labels):
            return False
        act = labels[zone]
        if act == "Статус":
            if lock_reason:
                return True
            self._win._row_change_status(od)
        elif act == "+":
            if lock_reason:
                return True
            self._win._row_add_product(od)
        elif act == "Удал":
            self._win._row_delete_order_flow(od)
        return True


_STATUS_DOT_COLORS = {
    "draft": "#90a4ae",
    "paid": "#43a047",
    "in_progress": "#fb8c00",
    "made": "#5c6bc0",
    "checked_qr": "#00897b",
    "shipped": "#6d4c41",
    "completed": "#2e7d32",
}


class _OrderMetaCellDelegate(QStyledItemDelegate):
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._win = main_window

    def _meta(self, index):
        o = self._win._order_for_table_row(index.row())
        if not o:
            return {}
        return self._win._order_row_meta_for(o) or {}

    def _paint_selection(self, painter, option, index=None):
        row = index.row() if index is not None else option.index.row()
        if self._win._orders_table_row_is_selected(row):
            painter.fillRect(option.rect, QColor(_ORDER_ROW_SEL_BG))
            return True
        return False


class _OrderPaymentDelegate(_OrderMetaCellDelegate):
    def paint(self, painter, option, index):  # noqa: N802
        painter.save()
        rect = QRect(option.rect)
        o = self._win._order_for_table_row(index.row()) or {}
        m = self._meta(index)
        st = str(m.get("payment_state") or "unpaid")
        text = str(index.data(Qt.DisplayRole) or "")
        if self._win._is_sales_row(o):
            st = "full" if str(o.get("status") or "").lower() == "paid" else "unpaid"
            if st == "full":
                text = "Оплачено полностью"
        if self._paint_selection(painter, option, index):
            painter.setPen(QColor(_ORDER_ROW_SEL_FG))
        elif st == "full":
            painter.fillRect(rect, QColor("#c8e6c9"))
            painter.setPen(QColor("#1b5e20"))
        elif st == "partial":
            painter.fillRect(rect, QColor("#ffffff"))
            painter.setPen(QColor("#4a148c"))
        else:
            painter.fillRect(rect, QColor("#ffffff"))
            painter.setPen(QColor("#455a64"))
        tr = rect.adjusted(4, 3, -4, -16 if st == "partial" else -3)
        f = painter.font()
        f.setBold(st in ("full", "partial"))
        f.setPointSize(max(8, f.pointSize() - 1))
        painter.setFont(f)
        painter.drawText(tr, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, text)
        if st == "partial" and not self._win._orders_table_row_is_selected(index.row()):
            paid = int(m.get("payment_paid") or 0)
            tot = max(1, int(m.get("payment_total") or 1))
            bar = QRect(rect.left() + 3, rect.bottom() - 14, rect.width() - 6, 11)
            painter.setPen(QPen(QColor("#b39ddb"), 1))
            painter.setBrush(QColor("#e1bee7"))
            painter.drawRoundedRect(bar, 2, 2)
            fill_w = int(bar.width() * min(1.0, paid / float(tot)))
            if fill_w > 0:
                painter.fillRect(bar.left(), bar.top(), fill_w, bar.height(), QColor("#ce93d8"))
            painter.setPen(QColor("#4a148c"))
            f2 = painter.font()
            f2.setPointSize(8)
            f2.setBold(False)
            painter.setFont(f2)
            painter.drawText(
                bar,
                Qt.AlignCenter,
                "оплачено %d из %d" % (paid, tot),
            )
        painter.restore()


class _OrderSurchargeDelegate(_OrderMetaCellDelegate):
    def paint(self, painter, option, index):  # noqa: N802
        painter.save()
        rect = QRect(option.rect)
        m = self._meta(index)
        o = self._win._order_for_table_row(index.row()) or {}
        text = str(index.data(Qt.DisplayRole) or "")
        total = int(m.get("surcharge_total") or 0)
        paid = min(total, max(0, int(m.get("surcharge_paid") or 0)))
        if self._paint_selection(painter, option, index):
            painter.setPen(QColor(_ORDER_ROW_SEL_FG))
            painter.drawText(rect.adjusted(4, 2, -4, -2), Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, text)
        elif self._win._is_sales_row(o) or total <= 0:
            painter.fillRect(rect, QColor("#e0e0e0"))
            painter.setPen(QColor("#78909c"))
            if not text or text == "—":
                painter.drawText(rect, Qt.AlignCenter, "—")
        else:
            ratio = paid / float(max(1, total))
            fill_w = int(rect.width() * max(0.0, min(1.0, ratio)))
            painter.fillRect(rect.left(), rect.top(), fill_w, rect.height(), QColor("#c8e6c9"))
            if fill_w < rect.width():
                painter.fillRect(
                    rect.left() + fill_w, rect.top(), rect.width() - fill_w, rect.height(), QColor("#eeeeee")
                )
            painter.setPen(QColor("#263238"))
            f = painter.font()
            f.setBold(True)
            f.setPointSize(max(8, f.pointSize() - 1))
            painter.setFont(f)
            painter.drawText(
                rect.adjusted(4, 2, -4, -2), Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap, text
            )
        painter.restore()


class _OrderStatusDelegate(_OrderMetaCellDelegate):
    def paint(self, painter, option, index):  # noqa: N802
        painter.save()
        rect = QRect(option.rect)
        m = self._meta(index)
        o = self._win._order_for_table_row(index.row()) or {}
        selected = self._win._orders_table_row_is_selected(index.row())
        if selected:
            painter.fillRect(rect, QColor(_ORDER_ROW_SEL_BG))
        else:
            painter.fillRect(rect, QColor("#ffffff"))
        text_fg = QColor(_ORDER_ROW_SEL_FG) if selected else QColor("#212121")
        counts = dict(m.get("status_counts") or {})
        total = max(1, int(m.get("status_total") or 0))
        if not counts and self._win._is_sales_row(o):
            from db import models as db_models

            painter.setPen(text_fg)
            f = painter.font()
            f.setBold(True)
            f.setPointSize(max(8, f.pointSize() - 1))
            painter.setFont(f)
            painter.drawText(
                rect.adjusted(3, 2, -3, -2),
                Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                db_models.sales_status_to_ru(o.get("status")),
            )
            painter.restore()
            return
        if not counts:
            painter.setPen(text_fg)
            painter.drawText(
                rect.adjusted(3, 2, -3, -2),
                Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                str(m.get("status_display") or index.data(Qt.DisplayRole) or ""),
            )
            painter.restore()
            return
        ordered = sorted(counts.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
        x0, y0 = rect.left() + 3, rect.top() + 3
        chip_w, chip_h, row_gap, dot_sz = 60, 15, 2, 10
        for i, (st, cnt) in enumerate(ordered):
            row_i = i // 3
            col_i = i % 3
            cx = x0 + col_i * chip_w
            cy = y0 + row_i * (chip_h + row_gap)
            dot_y = cy + max(0, (chip_h - dot_sz) // 2)
            ckey = {
                "draft": "status_draft",
                "paid": "status_paid",
                "in_progress": "status_in_progress",
                "made": "status_made",
                "checked_qr": "status_checked_qr",
                "shipped": "status_shipped",
                "completed": "status_completed",
            }.get(st)
            try:
                dot_col = QColor(color(ckey)) if ckey else QColor(_STATUS_DOT_COLORS.get(st, "#90a4ae"))
            except Exception:
                dot_col = QColor(_STATUS_DOT_COLORS.get(st, "#90a4ae"))
            painter.setBrush(QBrush(dot_col))
            painter.setPen(QPen(dot_col.darker(130), 1))
            painter.drawRect(cx, dot_y, dot_sz, dot_sz)
            painter.setPen(text_fg)
            f = painter.font()
            f.setPointSize(8)
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(
                cx + dot_sz + 3,
                cy,
                chip_w - dot_sz - 5,
                chip_h,
                Qt.AlignLeft | Qt.AlignVCenter,
                "%d/%d" % (int(cnt), total),
            )
        painter.restore()


class _OrderCutDelegate(_OrderMetaCellDelegate):
    def paint(self, painter, option, index):  # noqa: N802
        painter.save()
        rect = QRect(option.rect)
        m = self._meta(index)
        x = int(m.get("cut_x") or 0)
        y = int(m.get("cut_y") or 0)
        cc = str(m.get("cut_cell") or "—")
        # При выделении строки — тот же вид (подпись + мини-бар), без замены на «да/нет/—».
        painter.fillRect(rect, QColor("#ffffff"))
        if cc == "—" or y <= 0:
            painter.setPen(QColor("#1b5e20"))
            f = painter.font()
            f.setBold(True)
            f.setPointSize(11)
            painter.setFont(f)
            painter.drawText(rect.adjusted(2, 0, -2, -14), Qt.AlignHCenter | Qt.AlignTop, "—")
        else:
            cap = "%d из %d" % (x, y)
            painter.setPen(QColor("#1b5e20"))
            f = painter.font()
            f.setBold(True)
            f.setPointSize(11)
            painter.setFont(f)
            painter.drawText(rect.adjusted(2, 0, -2, -14), Qt.AlignHCenter | Qt.AlignTop, cap)
            bar = QRect(rect.left() + 4, rect.bottom() - 14, rect.width() - 8, 11)
            painter.setPen(QPen(QColor("#2e7d32"), 2))
            painter.setBrush(QColor("#eceff1"))
            painter.drawRoundedRect(bar, 2, 2)
            fill_w = int(bar.width() * min(1.0, x / float(max(1, y))))
            if fill_w > 0:
                painter.fillRect(bar.left(), bar.top(), fill_w, bar.height(), QColor("#a5d6a7"))
        painter.restore()


def _open_file(path):
    """Открыть файл стандартным приложением ОС."""
    if sys.platform.startswith("win"):
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError:
            pass
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)


class MainWindow(QWidget):
    def __init__(self, user, parent=None, startup_splash=None):
        super().__init__(parent)
        self._user = user
        self._startup_splash = startup_splash
        self._startup_splash_dismissed = False
        self._release_notes_prompt_done = False
        self.setWindowTitle("Расчёт стоимости заказов")
        # 4 плитки в ряд — минимальная ширина окна
        self.setMinimumWidth(1280)
        self._main_table_mode = "orders"
        self._all_orders = []
        self._orders_items = {}
        self._current_orders = []
        self._table_display_order = []
        self._order_row_meta = {}
        self._order_cut_ids = frozenset()
        self._blocks_zamer_by_order = {}
        self._all_clients_for_search = []
        self._clients_by_id = {}
        self._clients_by_name = {}
        self._order_filter_cache = {}
        self._production_events_by_order = {}
        self._filter_cache_generation = 0
        self._filter_cache_thread = None
        self._order_portal_snapshot = {}
        self._filter_apply_timer = QTimer(self)
        self._filter_apply_timer.setSingleShot(True)
        self._filter_apply_timer.setInterval(50)
        self._filter_apply_timer.timeout.connect(self._apply_all_order_filters)
        self._filter_bar_sync_timer = QTimer(self)
        self._filter_bar_sync_timer.setSingleShot(True)
        self._filter_bar_sync_timer.setInterval(90)
        self._filter_bar_sync_timer.timeout.connect(self._sync_order_filter_bar_widths_apply)
        self._orders_web_sync_timer = QTimer(self)
        self._orders_web_sync_timer.setInterval(25000)
        self._orders_web_sync_timer.timeout.connect(self._poll_orders_web_bundle_changes)
        self._last_synced_section_widths = None
        self._phone_input_behavior = _PhoneInputBehavior(self)
        self._portal_fetch_generation = 0
        self._skip_portal_snap_reuse = False
        self._build_ui()
        # Дать заставке доанимировать кадры после долгой синхронной сборки UI.
        QApplication.processEvents()
        apply_window_icon(self)
        if startup_splash is None:
            QTimer.singleShot(0, self.showMaximized)
            # Окно «что нового» — после появления главного окна, не на заставке входа
            QTimer.singleShot(600, self._maybe_show_release_notes_deferred)
        else:
            # На случай если таблица не дошла до отрисовки (сбой БД и т.д.) — не держать splash бесконечно.
            QTimer.singleShot(25000, self._dismiss_startup_splash)

    def _maybe_show_release_notes_deferred(self) -> None:
        if self._release_notes_prompt_done:
            return
        self._release_notes_prompt_done = True
        try:
            from update_client import maybe_show_release_notes

            maybe_show_release_notes(self)
        except Exception:
            pass

    def _dismiss_startup_splash(self):
        if getattr(self, "_startup_splash_dismissed", False):
            return
        self._startup_splash_dismissed = True
        sp = getattr(self, "_startup_splash", None)
        self._startup_splash = None
        if sp is not None:
            fn = getattr(sp, "set_loading_phase", None)
            if callable(fn):
                try:
                    fn("")
                except Exception:
                    pass
            sp.close()
        if not self.isVisible():
            self.showMaximized()
        # Заметки к релизу — после загрузки окна и закрытия заставки, чтобы не грузить сеть/UI на splash
        QTimer.singleShot(450, self._maybe_show_release_notes_deferred)

    def _build_ui(self):
        QApplication.processEvents()
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(0)
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Верхняя панель
        header = QFrame()
        header.setFixedHeight(52)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 6, 12, 6)
        btn_new = QPushButton("Новый заказ")
        btn_style = "QPushButton { background-color: %s; color: %s; padding: 10px 20px; font-weight: bold; border: none; border-radius: 6px; } QPushButton:hover { background-color: %s; }" % (
            color('button_bg'), color('button_text'), color('button_hover'))
        btn_new.setStyleSheet(btn_style)
        btn_new.clicked.connect(self._on_new_order)
        header_layout.addWidget(btn_new)
        btn_qe = QPushButton("Быстрый просчет")
        btn_qe.setStyleSheet(
            "QPushButton { background-color: #8e24aa; color: white; padding: 10px 20px; font-weight: bold; border: none; border-radius: 6px; }"
            "QPushButton:hover { background-color: #9c27b0; }"
        )
        btn_qe.clicked.connect(self._on_quick_estimate)
        header_layout.addWidget(btn_qe)
        header_layout.addStretch()
        # Кнопки админа
        if self._user.get('role') == ROLE_ADMIN:
            n = get_unapproved_count()
            btn_confirm = QPushButton("Подтвердить" + (" (%d)" % n if n else ""))
            btn_confirm.setStyleSheet(btn_style)
            btn_confirm.clicked.connect(self._on_confirm_users)
            header_layout.addWidget(btn_confirm)
            btn_all_users = QPushButton("Все пользователи")
            btn_all_users.setStyleSheet(btn_style)
            btn_all_users.clicked.connect(self._on_all_users)
            header_layout.addWidget(btn_all_users)
            # Кнопка управления ценами (видна только админу)
            btn_prices = QPushButton("ЦЕНЫ")
            btn_prices.setStyleSheet(btn_style)
            btn_prices.clicked.connect(self._on_prices)
            header_layout.addWidget(btn_prices)
        # Имя и роль (из БД могут прийти не строки — всегда приводим к str)
        def _s(v):
            if v is None:
                return ''
            return str(v).strip()
        name = ("%s %s" % (_s(self._user.get('name')), _s(self._user.get('surname')))).strip() or _s(self._user.get('login'))
        role = self._user.get('role') or 'manager'
        role_color = color('role_admin') if role == ROLE_ADMIN else color('role_manager')
        btn_cabinet = QPushButton("Личный кабинет")
        btn_cabinet.setStyleSheet(btn_style)
        btn_cabinet.clicked.connect(self._on_personal_cabinet)
        header_layout.addWidget(btn_cabinet)
        header_layout.addSpacing(12)
        self._header_lbl_name = QLabel(name)
        self._header_lbl_name.setStyleSheet("font-weight: bold; color: %s;" % color('header_text'))
        self._header_lbl_role = QLabel(role_label_desktop(role))
        self._header_lbl_role.setStyleSheet("color: %s; font-weight: bold; margin-left: 8px;" % role_color)
        header_layout.addWidget(self._header_lbl_name)
        header_layout.addWidget(self._header_lbl_role)
        header.setStyleSheet("QFrame { background-color: %s; }" % color('header_bg'))
        main_layout.addWidget(header)

        view_row = QHBoxLayout()
        view_row.setSpacing(6)
        lbl_view = QLabel("Таблица:")
        lbl_view.setStyleSheet("color:#333;")
        view_row.addWidget(lbl_view)
        self._btn_view_orders = QPushButton("Заказы")
        self._btn_view_quick = QPushButton("Быстрый просчет")
        vs = "QPushButton { padding:6px 12px; border-radius:4px; border:1px solid #ccc; background:#fff; } QPushButton:checked { background:#1976d2; color:#fff; border-color:#1976d2; }"
        self._btn_view_orders.setCheckable(True)
        self._btn_view_quick.setCheckable(True)
        self._btn_view_orders.setChecked(True)
        self._btn_view_orders.setStyleSheet(vs)
        self._btn_view_quick.setStyleSheet(vs)
        self._btn_view_orders.clicked.connect(lambda: self._set_main_table_mode("orders"))
        self._btn_view_quick.clicked.connect(lambda: self._set_main_table_mode("quick"))
        view_row.addWidget(self._btn_view_orders)
        view_row.addWidget(self._btn_view_quick)
        tbl_act_style = (
            "QPushButton { padding:4px 10px; border-radius:4px; border:1px solid #ccc; background:#f5f5f5; }"
            "QPushButton:disabled { color:#aaa; }"
        )
        self._btn_toolbar_model = QPushButton("Модель")
        self._btn_toolbar_status = QPushButton("Сменить статус")
        self._btn_toolbar_add = QPushButton("+ изделие")
        for b in (self._btn_toolbar_model, self._btn_toolbar_status, self._btn_toolbar_add):
            b.setStyleSheet(tbl_act_style)
            b.setEnabled(False)
        self._btn_toolbar_model.clicked.connect(self._on_toolbar_open_model)
        self._btn_toolbar_status.clicked.connect(self._on_toolbar_change_status)
        self._btn_toolbar_add.clicked.connect(self._on_toolbar_add_product)
        view_row.addWidget(self._btn_toolbar_model)
        view_row.addWidget(self._btn_toolbar_status)
        view_row.addWidget(self._btn_toolbar_add)
        view_row.addStretch()
        main_layout.addLayout(view_row)

        self._orders_table = QTableWidget(0, 15)
        self._orders_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._orders_table.setHorizontalHeaderLabels(
            [
                "№",
                "Клиент",
                "Менеджер",
                "Дата",
                "Тип",
                "Изделий",
                "Итого ₽",
                "Оплата",
                "Доплата",
                "Статус",
                "Замер · портал",
                "Доставка · портал",
                "Монтаж · портал",
                "Раскрой",
                "Действия",
            ]
        )
        self._orders_table.horizontalHeader().setStretchLastSection(False)
        _vhh = self._orders_table.verticalHeader()
        _vhh.setVisible(True)
        _vhh.setFixedWidth(14)
        _vhh.setSectionResizeMode(QHeaderView.Interactive)
        _vhh.setSectionsClickable(True)
        _vhh.setDefaultSectionSize(38)
        _vhh.setMinimumSectionSize(22)
        self._orders_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._orders_table_fit_in_progress = False
        self._orders_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._orders_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._orders_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._orders_table.doubleClicked.connect(self._on_orders_table_activated)
        self._orders_table.cellClicked.connect(self._on_orders_table_cell_clicked)
        self._orders_table.itemSelectionChanged.connect(self._on_orders_table_selection)
        self._orders_table.horizontalHeader().sectionClicked.connect(self._on_orders_header_clicked)
        self._svc_portal_delegate = _OrderServicePortalDelegate(self, self._orders_table)
        for _ci in (_OC_MEASURE, _OC_DELIVERY, _OC_INSTALL):
            self._orders_table.setItemDelegateForColumn(_ci, self._svc_portal_delegate)
        self._order_pay_delegate = _OrderPaymentDelegate(self, self._orders_table)
        self._order_surcharge_delegate = _OrderSurchargeDelegate(self, self._orders_table)
        self._order_status_delegate = _OrderStatusDelegate(self, self._orders_table)
        self._order_cut_delegate = _OrderCutDelegate(self, self._orders_table)
        self._order_actions_delegate = _OrderActionsDelegate(self, self._orders_table)
        self._orders_table.setItemDelegateForColumn(_OC_PAYMENT, self._order_pay_delegate)
        self._orders_table.setItemDelegateForColumn(_OC_SURCHARGE, self._order_surcharge_delegate)
        self._orders_table.setItemDelegateForColumn(_OC_STATUS, self._order_status_delegate)
        self._orders_table.setItemDelegateForColumn(_OC_CUT, self._order_cut_delegate)
        self._orders_table.setItemDelegateForColumn(_OC_ACTIONS, self._order_actions_delegate)
        _oab = color("orders_area_bg")
        _tbl_pal = self._orders_table.palette()
        _tbl_pal.setColor(QPalette.Highlight, QColor(_ORDER_ROW_SEL_BG))
        _tbl_pal.setColor(QPalette.HighlightedText, QColor(_ORDER_ROW_SEL_FG))
        self._orders_table.setPalette(_tbl_pal)
        self._orders_table.setStyleSheet(
            "QTableWidget { background-color: %s; gridline-color: #ccc; selection-background-color: %s; selection-color: %s; }"
            "QTableWidget::item:selected { background-color: %s; color: %s; }"
            "QHeaderView::section { background-color: %s; padding: 4px; border: 1px solid #bbb; }"
            "QTableWidget QHeaderView::vertical { background-color: %s; }"
            "QTableWidget QHeaderView::section:vertical { background-color: %s; border-right: 1px solid #bdbdbd; border-bottom: 1px solid #ccc; padding: 0; min-width: 12px; max-width: 14px; color: transparent; font-size: 1px; }"
            "QTableCornerButton::section { background-color: %s; border: 1px solid #bbb; }"
            "QAbstractScrollArea::corner { background-color: %s; border: none; }"
            "QScrollBar:horizontal, QScrollBar:vertical { background-color: %s; border: none; }"
            "QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal,"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background-color: %s; }"
            % (
                _oab,
                _ORDER_ROW_SEL_BG,
                _ORDER_ROW_SEL_FG,
                _ORDER_ROW_SEL_BG,
                _ORDER_ROW_SEL_FG,
                _oab,
                _oab,
                _oab,
                _oab,
                _oab,
                _oab,
                _oab,
            )
        )
        self._table_sort_section = None
        self._table_sort_ascending = True
        _hdr0 = self._orders_table.horizontalHeader()
        self._orders_table_columns_customized = False
        for i, w in enumerate(_ORDER_TABLE_DEFAULT_WIDTHS):
            if i < _hdr0.count():
                _hdr0.resizeSection(i, w)
        for i in range(_hdr0.count()):
            _hdr0.setSectionResizeMode(i, QHeaderView.Interactive)
        _hdr0.setCascadingSectionResizes(False)
        _hdr0.setMinimumSectionSize(28)
        _hdr0.setSectionsClickable(True)
        QTimer.singleShot(0, lambda: self._apply_orders_table_proportional_layout(use_defaults=True))

        self._build_orders_filter_bar()

        self._orders_panel = QWidget()
        self._orders_panel.setStyleSheet(
            "QWidget#ordersPanel { background-color: %s; }" % color("orders_area_bg")
        )
        self._orders_panel.setObjectName("ordersPanel")
        _opl = QVBoxLayout(self._orders_panel)
        _opl.setContentsMargins(0, 0, 0, 0)
        _opl.setSpacing(0)
        _opl.addWidget(self._orders_filter_bar)
        _opl.addWidget(self._orders_table, 1)
        self._orders_table.installEventFilter(self)

        QApplication.processEvents()

        self._quick_table = QTableWidget(0, 8)
        self._quick_table.setHorizontalHeaderLabels(["№", "Категория", "Клиент", "Источник", "Наценка", "Статус", "Дата", "Действия"])
        self._quick_table.horizontalHeader().setStretchLastSection(True)
        self._quick_table.verticalHeader().setVisible(False)
        self._quick_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._quick_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._quick_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._quick_table.doubleClicked.connect(self._on_quick_table_activated)
        self._quick_table.setStyleSheet(
            "QTableWidget { background-color: %s; gridline-color: #ccc; }"
            "QHeaderView::section { background-color: %s; padding: 4px; border: 1px solid #bbb; }"
            % (color("orders_area_bg"), color("orders_area_bg"))
        )

        self._quick_filter = QLineEdit()
        self._quick_filter.setPlaceholderText("поиск…")
        self._quick_filter.setClearButtonEnabled(True)
        self._quick_filter.textChanged.connect(self._apply_quick_filters)
        self._quick_filter_phone = QLineEdit()
        self._quick_filter_phone.setPlaceholderText("телефон…")
        self._quick_filter_phone.setClearButtonEnabled(True)
        self._quick_filter_phone.textChanged.connect(self._apply_quick_filters)
        self._quick_filter_inn = QLineEdit()
        self._quick_filter_inn.setPlaceholderText("ИНН…")
        self._quick_filter_inn.setClearButtonEnabled(True)
        self._quick_filter_inn.textChanged.connect(self._apply_quick_filters)
        self._quick_filter_reset = QPushButton("Сбросить")
        self._quick_filter_reset.clicked.connect(self._reset_quick_filters)
        self._quick_panel = QWidget()
        _ql = QVBoxLayout(self._quick_panel)
        _ql.setContentsMargins(0, 0, 0, 0)
        _ql.setSpacing(2)
        _qfr = QHBoxLayout()
        _qfr.setContentsMargins(0, 0, 0, 0)
        _qfr.addWidget(self._quick_filter, 2)
        _qfr.addWidget(self._quick_filter_phone, 1)
        _qfr.addWidget(self._quick_filter_inn, 1)
        _qfr.addWidget(self._quick_filter_reset, 0)
        _ql.addLayout(_qfr)
        _ql.addWidget(self._quick_table, 1)

        self._stack_orders = QStackedWidget()
        self._stack_orders.addWidget(self._orders_panel)
        self._stack_orders.addWidget(self._quick_panel)
        main_layout.addWidget(self._stack_orders, 1)
        # Окно показывается сразу; тяжёлая выборка заказов — на следующий кадр (логика таблицы та же).
        self._set_main_table_mode("orders")
        QTimer.singleShot(0, self._load_orders)
        QTimer.singleShot(120, self._schedule_sync_filter_bar_widths)

        # Нижняя панель
        footer = QFrame()
        footer.setFixedHeight(56)
        footer_layout = QHBoxLayout(footer)
        footer_layout.setSpacing(16)
        footer_layout.setContentsMargins(12, 8, 12, 8)
        for label, slot in [
            ("КЛИЕНТЫ", "_on_clients"),
            ("ПОСТАВЩИКИ", "_on_suppliers"),
            ("ПРОДАЖИ", "_on_sales"),
            ("СКЛАДЫ", "_on_warehouse"),
            ("НАСТРОЙКИ", "_on_settings"),
        ]:
            if label == "СКЛАДЫ" and self._user.get('role') == ROLE_MANAGER:
                continue
            btn = QPushButton(label)
            btn.setStyleSheet(btn_style)
            btn.clicked.connect(getattr(self, slot))
            footer_layout.addWidget(btn)
        btn_inv = QPushButton("Инвентаризация")
        btn_inv.setStyleSheet(btn_style)
        btn_inv.clicked.connect(self._on_inventory_tables)
        footer_layout.addWidget(btn_inv)
        if self._user.get("role") == ROLE_ADMIN:
            btn_cut_m = QPushButton("Раскрой по материалу")
            btn_cut_m.setStyleSheet(btn_style)
            btn_cut_m.clicked.connect(self._on_cut_by_material)
            footer_layout.addWidget(btn_cut_m)
        footer_layout.addWidget(self._footer_orders_legend(), 0, Qt.AlignLeft | Qt.AlignVCenter)
        footer_layout.addStretch()
        footer.setStyleSheet("QFrame { background-color: %s; }" % color('header_bg'))
        main_layout.addWidget(footer)

        _mwb = color("main_window_bg")
        _hbg = color("header_bg")
        self.setStyleSheet(
            "QWidget { background-color: %s; }"
            "QSizeGrip { background-color: %s; }"
            % (_mwb, _hbg)
        )
        self._init_zamer_board_listen()
        app = QApplication.instance()
        if app:
            app.applicationStateChanged.connect(self._on_application_state_changed)
        # Пока тяжёлый __init__, цикл событий не крутится — один pump, чтобы заставка могла обновиться.
        QApplication.processEvents()

    def _build_orders_filter_bar(self):
        """Строка фильтров строго по колонкам таблицы заказов (ширины синхронизируются с заголовком)."""
        _dmin = QDate(2000, 1, 1)
        _fab = color("orders_area_bg")
        le_st = (
            "QLineEdit { font-size: 11px; padding: 2px 4px; max-height: 22px; "
            "background-color: %s; border: 1px solid #90caf9; border-radius: 3px; color: #1a365d; }"
            % _fab
        )
        de_st = (
            "QDateEdit { font-size: 11px; padding: 1px 2px; max-height: 22px; "
            "background-color: %s; border: 1px solid #90caf9; border-radius: 3px; color: #1a365d; }"
            % _fab
        )

        def mk_de():
            d = QDateEdit()
            # Всплывающий календарь — отдельное окно; при частых пересчётах вёрстки даёт артефакты/мерцание.
            d.setCalendarPopup(False)
            d.setDisplayFormat("dd.MM.yy")
            d.setSpecialValueText("—")
            d.setMinimumDate(_dmin)
            d.setDate(d.minimumDate())
            d.setStyleSheet(de_st)
            d.dateChanged.connect(self._schedule_apply_order_filters)
            return d

        def wrap_cell(inner: QWidget) -> QFrame:
            f = QFrame()
            f.setFrameShape(QFrame.NoFrame)
            f.setStyleSheet(
                "QFrame { border-right: 1px solid #7eb8d4; border-top: 2px solid #546e7a; background-color: %s; }"
                % _fab
            )
            lay = QVBoxLayout(f)
            lay.setContentsMargins(0, 3, 0, 3)
            lay.setSpacing(2)
            inner.setMinimumWidth(0)
            inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lay.addWidget(inner)
            return f

        self._orders_filter_bar = QWidget()
        self._orders_filter_bar.setObjectName("ordersFilterBar")
        self._orders_filter_bar.setStyleSheet(
            "QWidget#ordersFilterBar { background-color: %s; }" % _fab
        )
        froot = QVBoxLayout(self._orders_filter_bar)
        froot.setContentsMargins(0, 0, 0, 0)
        froot.setSpacing(0)
        self._order_global_search_bar = QWidget()
        self._order_global_search_bar.setStyleSheet(
            "QWidget { border-bottom: 1px solid #7eb8d4; background-color: %s; }" % _fab
        )
        gs = QHBoxLayout(self._order_global_search_bar)
        gs.setContentsMargins(6, 3, 6, 3)
        gs.setSpacing(6)
        _lbl_g = QLabel("Поиск клиента/заказа:")
        _lbl_g.setStyleSheet("font-size:10px; color:#1a365d; font-weight:600;")
        gs.addWidget(_lbl_g)
        self._flt_client_phone = QLineEdit()
        self._flt_client_phone.setPlaceholderText("телефон…")
        self._flt_client_phone.setClearButtonEnabled(True)
        self._flt_client_phone.installEventFilter(self._phone_input_behavior)
        self._flt_client_phone.setStyleSheet(le_st)
        self._flt_client_phone.textChanged.connect(self._schedule_apply_order_filters)
        gs.addWidget(self._flt_client_phone, 1)
        self._flt_client_inn = QLineEdit()
        self._flt_client_inn.setPlaceholderText("ИНН…")
        self._flt_client_inn.setClearButtonEnabled(True)
        self._flt_client_inn.setInputMask("000000000000;_")
        self._flt_client_inn.setStyleSheet(le_st)
        self._flt_client_inn.textChanged.connect(self._schedule_apply_order_filters)
        gs.addWidget(self._flt_client_inn, 1)
        froot.addWidget(self._order_global_search_bar)

        fb_wrap = QWidget()
        self._order_filter_row_wrap = fb_wrap
        fb = QHBoxLayout(fb_wrap)
        fb.setContentsMargins(0, 0, 0, 0)
        fb.setSpacing(0)
        self._order_filter_row_layout = fb
        self._order_filter_left_spacer = QFrame()
        self._order_filter_left_spacer.setFrameShape(QFrame.NoFrame)
        self._order_filter_left_spacer.setStyleSheet("background-color: %s; border: none;" % _fab)
        self._order_filter_left_spacer.setFixedWidth(0)
        fb.addWidget(self._order_filter_left_spacer, 0, Qt.AlignTop)
        self._order_filter_cells = []

        self._flt_col_id = QLineEdit()
        self._flt_col_id.setPlaceholderText("№")
        self._flt_col_id.setClearButtonEnabled(True)
        self._flt_col_id.setStyleSheet(le_st)
        self._flt_col_id.textChanged.connect(self._schedule_apply_order_filters)
        _c0 = wrap_cell(self._flt_col_id)
        self._order_filter_cells.append(_c0)
        fb.addWidget(_c0, 0, Qt.AlignTop)

        self._flt_col_client = QLineEdit()
        self._flt_col_client.setPlaceholderText("имя…")
        self._flt_col_client.setClearButtonEnabled(True)
        self._flt_col_client.setStyleSheet(le_st)
        self._flt_col_client.textChanged.connect(self._schedule_apply_order_filters)
        _c1 = wrap_cell(self._flt_col_client)
        self._order_filter_cells.append(_c1)
        fb.addWidget(_c1, 0, Qt.AlignTop)

        self._flt_col_manager = QLineEdit()
        self._flt_col_manager.setPlaceholderText("поиск…")
        self._flt_col_manager.setClearButtonEnabled(True)
        self._flt_col_manager.setToolTip(
            "Частичное совпадение: фамилия, имя, логин. "
            "Несколько слов — заказ показывается, если каждое слово где-то входит (порядок не важен)."
        )
        self._flt_col_manager.setStyleSheet(le_st)
        self._flt_col_manager.textChanged.connect(self._schedule_apply_order_filters)
        _c1b = wrap_cell(self._flt_col_manager)
        self._order_filter_cells.append(_c1b)
        fb.addWidget(_c1b, 0, Qt.AlignTop)

        _w2 = QWidget()
        _l2 = QVBoxLayout(_w2)
        _l2.setContentsMargins(0, 0, 0, 0)
        _l2.setSpacing(2)
        self._flt_created_from = mk_de()
        self._flt_created_to = mk_de()
        self._flt_created_from.setToolTip(
            "Дата создания заказа в системе.\nФормат: дд.мм.гг (например 02.05.26 — 2 мая 2026)."
        )
        self._flt_created_to.setToolTip(self._flt_created_from.toolTip())
        _l2.addWidget(self._flt_created_from)
        _l2.addWidget(self._flt_created_to)
        _c2 = wrap_cell(_w2)
        self._order_filter_cells.append(_c2)
        fb.addWidget(_c2, 0, Qt.AlignTop)

        self._flt_type_combo = QComboBox()
        self._flt_type_combo.addItems(
            ["все типы", "Стекло / зеркало", "Фасады", "Смешанный"]
        )
        self._flt_type_combo.setStyleSheet(
            "QComboBox { font-size: 11px; padding: 2px 4px; max-height: 24px; "
            "background-color: %s; border: 1px solid #90caf9; border-radius: 3px; color: #1a365d; }"
            % _fab
        )
        self._flt_type_combo.currentIndexChanged.connect(self._schedule_apply_order_filters)
        _c3 = wrap_cell(self._flt_type_combo)
        self._order_filter_cells.append(_c3)
        fb.addWidget(_c3, 0, Qt.AlignTop)

        self._flt_products_spin = QSpinBox()
        self._flt_products_spin.setRange(0, 999)
        self._flt_products_spin.setSpecialValueText("—")
        self._flt_products_spin.setValue(0)
        self._flt_products_spin.setToolTip("Число изделий в расчёте; «—» = любое")
        self._flt_products_spin.setStyleSheet(
            "QSpinBox { font-size: 11px; padding: 2px 4px; max-height: 24px; "
            "background-color: %s; border: 1px solid #90caf9; border-radius: 3px; color: #1a365d; }"
            % _fab
        )
        self._flt_products_spin.valueChanged.connect(self._schedule_apply_order_filters)
        _c4 = wrap_cell(self._flt_products_spin)
        self._order_filter_cells.append(_c4)
        fb.addWidget(_c4, 0, Qt.AlignTop)

        _wt = QWidget()
        _lt = QVBoxLayout(_wt)
        _lt.setContentsMargins(0, 0, 0, 0)
        _lt.setSpacing(2)
        self._flt_sum_from = QLineEdit()
        self._flt_sum_from.setPlaceholderText("₽ от")
        self._flt_sum_from.setStyleSheet(le_st)
        self._flt_sum_from.setToolTip("Сумма заказа (колонка «Итого») — от")
        self._flt_sum_from.textChanged.connect(self._schedule_apply_order_filters)
        self._flt_sum_to = QLineEdit()
        self._flt_sum_to.setPlaceholderText("₽ до")
        self._flt_sum_to.setStyleSheet(le_st)
        self._flt_sum_to.setToolTip("Сумма заказа (колонка «Итого») — до")
        self._flt_sum_to.textChanged.connect(self._schedule_apply_order_filters)
        _lt.addWidget(self._flt_sum_from)
        _lt.addWidget(self._flt_sum_to)
        _c5 = wrap_cell(_wt)
        self._order_filter_cells.append(_c5)
        fb.addWidget(_c5, 0, Qt.AlignTop)

        cb_st = (
            "QComboBox { font-size: 11px; padding: 2px 4px; max-height: 24px; "
            "background-color: %s; border: 1px solid #90caf9; border-radius: 3px; color: #1a365d; }"
            % _fab
        )

        _wpay = QWidget()
        _lpay = QVBoxLayout(_wpay)
        _lpay.setContentsMargins(0, 0, 0, 0)
        _lpay.setSpacing(2)
        self._flt_payment_method_combo = QComboBox()
        self._flt_payment_method_combo.setStyleSheet(cb_st)
        self._populate_payment_method_filter_combo()
        self._flt_payment_method_combo.currentIndexChanged.connect(
            self._schedule_apply_order_filters
        )
        _lpay.addWidget(self._flt_payment_method_combo)
        _c5p = wrap_cell(_wpay)
        self._order_filter_cells.append(_c5p)
        fb.addWidget(_c5p, 0, Qt.AlignTop)

        self._flt_col_surcharge = QLineEdit()
        self._flt_col_surcharge.setPlaceholderText("доплата…")
        self._flt_col_surcharge.setClearButtonEnabled(True)
        self._flt_col_surcharge.setStyleSheet(le_st)
        self._flt_col_surcharge.textChanged.connect(self._schedule_apply_order_filters)
        _c5s = wrap_cell(self._flt_col_surcharge)
        self._order_filter_cells.append(_c5s)
        fb.addWidget(_c5s, 0, Qt.AlignTop)

        _wst = QWidget()
        _lst = QVBoxLayout(_wst)
        _lst.setContentsMargins(0, 0, 0, 0)
        _lst.setSpacing(2)
        self._flt_status_partial_combo = QComboBox()
        self._flt_status_partial_combo.setStyleSheet(cb_st)
        self._flt_status_partial_combo.setToolTip(
            "Частичный статус: в заказе есть хотя бы одно изделие (единица) с выбранным статусом."
        )
        self._flt_status_full_combo = QComboBox()
        self._flt_status_full_combo.setStyleSheet(cb_st)
        self._flt_status_full_combo.setToolTip(
            "Полный статус: все изделия в заказе имеют выбранный статус."
        )
        self._populate_status_filter_combos()
        self._flt_status_partial_combo.currentIndexChanged.connect(
            self._schedule_apply_order_filters
        )
        self._flt_status_full_combo.currentIndexChanged.connect(
            self._schedule_apply_order_filters
        )
        _lst.addWidget(self._flt_status_partial_combo)
        _lst.addWidget(self._flt_status_full_combo)
        _c6 = wrap_cell(_wst)
        self._order_filter_cells.append(_c6)
        fb.addWidget(_c6, 0, Qt.AlignTop)

        self._flt_meas_from = mk_de()
        self._flt_meas_to = mk_de()
        _wm = QWidget()
        _lm = QVBoxLayout(_wm)
        _lm.setContentsMargins(0, 0, 0, 0)
        _lm.setSpacing(2)
        self._flt_meas_from.setToolTip(
            "Интервал дат замера (из расчёта и заявки портала).\nПодсказка: дд.мм.гг — кратко; наведите на ячейку таблицы для расшифровки."
        )
        self._flt_meas_to.setToolTip(self._flt_meas_from.toolTip())
        _lm.addWidget(self._flt_meas_from)
        _lm.addWidget(self._flt_meas_to)
        _c7 = wrap_cell(_wm)
        self._order_filter_cells.append(_c7)
        fb.addWidget(_c7, 0, Qt.AlignTop)

        self._flt_del_from = mk_de()
        self._flt_del_to = mk_de()
        _wd = QWidget()
        _ld = QVBoxLayout(_wd)
        _ld.setContentsMargins(0, 0, 0, 0)
        _ld.setSpacing(2)
        self._flt_del_from.setToolTip(
            "Интервал дат доставки (расчёт / портал).\nФормат в поле: дд.мм.гг."
        )
        self._flt_del_to.setToolTip(self._flt_del_from.toolTip())
        _ld.addWidget(self._flt_del_from)
        _ld.addWidget(self._flt_del_to)
        _c8 = wrap_cell(_wd)
        self._order_filter_cells.append(_c8)
        fb.addWidget(_c8, 0, Qt.AlignTop)

        self._flt_inst_from = mk_de()
        self._flt_inst_to = mk_de()
        _wi = QWidget()
        _li = QVBoxLayout(_wi)
        _li.setContentsMargins(0, 0, 0, 0)
        _li.setSpacing(2)
        self._flt_inst_from.setToolTip(
            "Интервал дат монтажа (расчёт / портал).\nФормат в поле: дд.мм.гг."
        )
        self._flt_inst_to.setToolTip(self._flt_inst_from.toolTip())
        _li.addWidget(self._flt_inst_from)
        _li.addWidget(self._flt_inst_to)
        _c9 = wrap_cell(_wi)
        self._order_filter_cells.append(_c9)
        fb.addWidget(_c9, 0, Qt.AlignTop)

        self._flt_cut_combo = QComboBox()
        self._flt_cut_combo.addItems(["любой", "да", "нет"])
        self._flt_cut_combo.setStyleSheet(
            "QComboBox { font-size: 11px; padding: 2px 4px; max-height: 24px; "
            "background-color: %s; border: 1px solid #90caf9; border-radius: 3px; color: #1a365d; }"
            % _fab
        )
        self._flt_cut_combo.currentIndexChanged.connect(self._schedule_apply_order_filters)
        _c10 = wrap_cell(self._flt_cut_combo)
        self._order_filter_cells.append(_c10)
        fb.addWidget(_c10, 0, Qt.AlignTop)

        _w11 = QWidget()
        _l11 = QVBoxLayout(_w11)
        _l11.setContentsMargins(0, 0, 0, 0)
        _l11.setSpacing(4)
        _bclr = QPushButton("Сбросить")
        _bclr.setStyleSheet(
            "QPushButton { font-size:10px; padding:3px 6px; border-radius:3px; border:1px solid #64b5f6; background:%s; color:#1a365d; }"
            "QPushButton:hover { background:#bbdefb; border:1px solid #1976d2; }"
            % _fab
        )
        _bclr.clicked.connect(self._on_reset_all_order_filters)
        _bupd = QPushButton("Обновить")
        _bupd.setToolTip(
            "Обновить из базы только выделенную строку таблицы. "
            "Если строка не выделена — действие не выполняется."
        )
        _bupd.setStyleSheet(
            "QPushButton { font-size:10px; padding:3px 6px; border-radius:3px; border:1px solid #64b5f6; background:%s; color:#1a365d; }"
            "QPushButton:hover { background:#90caf9; border:1px solid #1976d2; color:#0d47a1; }"
            % _fab
        )
        _bupd.clicked.connect(self._on_refresh_orders_list)
        _l11.addWidget(_bclr)
        _l11.addWidget(_bupd)
        _c11 = wrap_cell(_w11)
        self._order_filter_cells.append(_c11)
        fb.addWidget(_c11, 0, Qt.AlignTop)

        hdr = self._orders_table.horizontalHeader()
        hdr.sectionResized.connect(self._on_orders_header_section_resized)
        hdr.geometriesChanged.connect(self._schedule_sync_filter_bar_widths)
        froot.addWidget(fb_wrap, 0)
        self._orders_filter_bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        fb_wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _populate_payment_method_filter_combo(self):
        from logic.blocks_bundle import (
            PAYMENT_BANK,
            PAYMENT_CARD,
            PAYMENT_CASH,
            PAYMENT_COD,
            PAYMENT_QR,
            PAYMENT_TYPE_LABELS_RU,
        )

        c = getattr(self, "_flt_payment_method_combo", None)
        if c is None:
            return
        c.blockSignals(True)
        c.clear()
        c.addItem("все способы", _FILTER_PAYMENT_ALL)
        c.addItem("не оплачен", _FILTER_PAYMENT_UNPAID)
        c.addItem("частичная оплата", _FILTER_PAYMENT_PARTIAL)
        for key in (PAYMENT_QR, PAYMENT_BANK, PAYMENT_CASH, PAYMENT_CARD, PAYMENT_COD):
            c.addItem(
                PAYMENT_TYPE_LABELS_RU.get(key, key),
                _FILTER_PAYMENT_METHOD_PREFIX + key,
            )
        c.setCurrentIndex(0)
        c.blockSignals(False)

    def _populate_status_filter_combos(self):
        from db_main import order_status_to_ru

        for combo, prefix in (
            (getattr(self, "_flt_status_partial_combo", None), "≥1: "),
            (getattr(self, "_flt_status_full_combo", None), "все: "),
        ):
            if combo is None:
                continue
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("%s—" % prefix, "")
            for key in _ORDER_FILTER_STATUS_KEYS:
                combo.addItem("%s%s" % (prefix, order_status_to_ru(key)), key)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is getattr(self, "_orders_table", None) and event.type() == QEvent.Resize:
            self._schedule_sync_filter_bar_widths()
            use_def = not getattr(self, "_orders_table_columns_customized", False)
            QTimer.singleShot(
                0,
                lambda d=use_def: self._apply_orders_table_proportional_layout(use_defaults=d),
            )
        return False

    def _on_orders_header_section_resized(self, logical_index, old_size, new_size):
        self._orders_table_columns_customized = True
        delta = int(new_size) - int(old_size)
        if delta != 0:
            self._adjust_orders_table_columns_right_of(int(logical_index), delta)
        self._schedule_sync_filter_bar_widths()

    def _orders_table_viewport_width(self):
        tbl = getattr(self, "_orders_table", None)
        if tbl is None:
            return 1
        return max(1, int(tbl.viewport().width() or 0))

    @staticmethod
    def _distribute_column_width_delta(sizes, delta, min_w, skip_index=None, only_indices=None):
        """Распределить +/- пикселей по колонкам пропорционально текущим ширинам."""
        n = len(sizes)
        if n < 1 or delta == 0:
            return sizes
        if only_indices is not None:
            pool = [i for i in only_indices if 0 <= i < n]
        else:
            pool = [i for i in range(n) if i != skip_index]
        if not pool:
            pool = list(range(n))
        if delta > 0:
            total = float(sum(max(min_w, sizes[i]) for i in pool) or 1)
            left = int(delta)
            for i in pool:
                if left <= 0:
                    break
                share = int(round(delta * max(min_w, sizes[i]) / total))
                share = max(0, min(share, left))
                if share <= 0 and left > 0:
                    share = 1
                sizes[i] += share
                left -= share
            if left > 0:
                sizes[pool[-1]] += left
            return sizes
        need = int(-delta)
        total = float(sum(max(0, sizes[i] - min_w) for i in pool) or 1)
        left = need
        for i in pool:
            if left <= 0:
                break
            can = max(0, sizes[i] - min_w)
            share = int(round(need * can / total)) if total > 0 else 0
            share = max(0, min(share, can, left))
            if share <= 0 and left > 0 and can > 0:
                share = 1
            sizes[i] -= share
            left -= share
        idx = 0
        guard = 0
        while left > 0 and pool and guard < n * 2000:
            i = pool[idx % len(pool)]
            if sizes[i] > min_w:
                sizes[i] -= 1
                left -= 1
            idx += 1
            guard += 1
        return sizes

    def _normalize_orders_table_column_widths(self, sizes, vp_w, min_w):
        n = len(sizes)
        sizes = [max(min_w, int(s)) for s in sizes]
        total = sum(sizes)
        if total == vp_w:
            return sizes
        if total < vp_w:
            return self._distribute_column_width_delta(sizes, vp_w - total, min_w)
        sizes = self._distribute_column_width_delta(sizes, total - vp_w, min_w)
        total = sum(sizes)
        if total > vp_w and vp_w > 0:
            scale = vp_w / float(total)
            sizes = [max(min_w, int(round(s * scale))) for s in sizes]
            drift = sum(sizes) - vp_w
            j = 0
            while drift > 0 and n > 0 and j < n * 4000:
                i = j % n
                if sizes[i] > min_w:
                    sizes[i] -= 1
                    drift -= 1
                j += 1
        return sizes

    def _apply_orders_table_column_sizes(self, sizes):
        if getattr(self, "_orders_table_fit_in_progress", False):
            return
        tbl = getattr(self, "_orders_table", None)
        if tbl is None:
            return
        hdr = tbl.horizontalHeader()
        n = min(len(sizes), hdr.count())
        if n < 1:
            return
        self._orders_table_fit_in_progress = True
        try:
            hdr.blockSignals(True)
            for i in range(n):
                hdr.resizeSection(i, int(sizes[i]))
            try:
                tbl.horizontalScrollBar().setValue(0)
            except Exception:
                pass
        finally:
            hdr.blockSignals(False)
            self._orders_table_fit_in_progress = False

    def _apply_orders_table_proportional_layout(self, use_defaults=False):
        """Старт / ресайз окна: сохранить пропорции базовых ширин, вписать в viewport без гориз. скролла."""
        tbl = getattr(self, "_orders_table", None)
        if tbl is None:
            return
        hdr = tbl.horizontalHeader()
        n = hdr.count()
        if n < 1:
            return
        min_w = max(28, hdr.minimumSectionSize())
        vp_w = self._orders_table_viewport_width()
        if use_defaults:
            base = list(_ORDER_TABLE_DEFAULT_WIDTHS[:n])
            while len(base) < n:
                base.append(80)
            total_base = sum(base) or 1
            scale = vp_w / float(total_base)
            sizes = [max(min_w, int(round(w * scale))) for w in base]
        else:
            sizes = [max(min_w, int(hdr.sectionSize(i) or min_w)) for i in range(n)]
            total = sum(sizes) or 1
            scale = vp_w / float(total)
            sizes = [max(min_w, int(round(s * scale))) for s in sizes]
        sizes = self._normalize_orders_table_column_widths(sizes, vp_w, min_w)
        self._apply_orders_table_column_sizes(sizes)

    def _adjust_orders_table_columns_right_of(self, resized_index, delta):
        """Тянем правую границу колонки: вправо — она шире, все справа уже; влево — наоборот."""
        if getattr(self, "_orders_table_fit_in_progress", False):
            return
        tbl = getattr(self, "_orders_table", None)
        if tbl is None:
            return
        hdr = tbl.horizontalHeader()
        n = hdr.count()
        ri = int(resized_index)
        if n < 1 or ri < 0 or ri >= n:
            return
        right = list(range(ri + 1, n))
        if not right:
            return
        min_w = max(28, hdr.minimumSectionSize())
        vp_w = self._orders_table_viewport_width()
        sizes = [max(min_w, int(hdr.sectionSize(i) or min_w)) for i in range(n)]
        before = list(sizes)
        if delta > 0:
            sizes = self._distribute_column_width_delta(
                sizes, -int(delta), min_w, only_indices=right
            )
            shrunk = sum(before[j] - sizes[j] for j in right)
            shortfall = int(delta) - int(shrunk)
            if shortfall > 0:
                sizes[ri] = max(min_w, sizes[ri] - shortfall)
        else:
            sizes = self._distribute_column_width_delta(
                sizes, -int(delta), min_w, only_indices=right
            )
        total = sum(sizes)
        drift = total - vp_w
        if drift != 0:
            if drift > 0:
                sizes = self._distribute_column_width_delta(
                    sizes, -int(drift), min_w, only_indices=right
                )
            else:
                sizes = self._distribute_column_width_delta(
                    sizes, int(-drift), min_w, only_indices=right
                )
        self._apply_orders_table_column_sizes(sizes)

    def _schedule_sync_filter_bar_widths(self):
        """Дебаунс: частые события заголовка не дёргают вёрстку (убирает мерцание)."""
        if getattr(self, "_filter_bar_sync_timer", None):
            self._filter_bar_sync_timer.start()

    def _sync_order_filter_bar_widths_apply(self):
        if not getattr(self, "_order_filter_cells", None):
            return
        if getattr(self, "_order_filter_row_layout", None) is not None:
            self._order_filter_row_layout.setContentsMargins(0, 0, 0, 0)
        try:
            vh_w = int(self._orders_table.verticalHeader().width() or 0)
        except Exception:
            vh_w = 0
        if getattr(self, "_order_filter_left_spacer", None) is not None:
            self._order_filter_left_spacer.setFixedWidth(max(0, vh_w))
        tbl = self._orders_table
        hdr = tbl.horizontalHeader()
        n = min(len(self._order_filter_cells), hdr.count())
        sw = [max(28, int(hdr.sectionSize(i) or 28)) for i in range(n)]
        total_w = sum(sw) + max(0, int(vh_w))
        if getattr(self, "_order_filter_row_wrap", None) is not None:
            self._order_filter_row_wrap.setFixedWidth(max(1, total_w))
        if sw == getattr(self, "_last_synced_section_widths", None):
            return
        self._last_synced_section_widths = list(sw)
        for i in range(n):
            self._order_filter_cells[i].setFixedWidth(sw[i])

    def _order_created_bounds(self, o):
        """Дата создания заказа (без времени) — точка [d,d] для фильтра по колонке «Дата»."""
        ca = o.get("created_at")
        if ca is None:
            return None, None
        try:
            if hasattr(ca, "date"):
                d = ca.date()
                return d, d
        except Exception:
            pass
        return None, None

    def _order_manager_label(self, o):
        sn = (o.get("creator_surname") or "").strip()
        fn = (o.get("creator_name") or "").strip()
        if sn or fn:
            return ("%s %s" % (sn, fn)).strip()
        login = (o.get("created_by_login") or "").strip()
        if login:
            return login
        role = (o.get("created_by_role") or "").strip()
        if role:
            return ROLE_LABELS.get(role, role) or "—"
        return "—"

    def _order_matches_manager_filter(self, o, query_lc):
        """Подстрока по имени, фамилии, логину или тексту колонки; несколько слов — все должны совпасть."""
        q = (query_lc or "").strip().lower()
        if not q:
            return True
        tokens = [t for t in q.split() if t]
        if not tokens:
            return True
        fn = (o.get("creator_name") or "").strip().lower()
        sn = (o.get("creator_surname") or "").strip().lower()
        lg = (o.get("created_by_login") or "").strip().lower()
        lbl = self._order_manager_label(o).lower()
        for t in tokens:
            if not (t in lbl or t in fn or t in sn or t in lg):
                return False
        return True

    def _orders_service_item(self, order, portal_row, service: str, payload=None):
        if self._is_sales_row(order):
            if service in ("measure", "install"):
                it = QTableWidgetItem("не используется")
                it.setData(_SERVICE_STATE_ROLE, "none")
                return it
            cnt = int(order.get("_sales_delivery_items_count") or 0)
            txt = "доставка: %d" % cnt if cnt > 0 else "доставка не добавлена"
            st = "done" if str(order.get("status") or "").strip().lower() in ("shipped", "received") else (
                "pending" if cnt > 0 else "none"
            )
            it = QTableWidgetItem(txt)
            it.setData(_SERVICE_STATE_ROLE, st)
            return it
        from ui.order_tile import order_service_cell_payload, order_service_cell_tooltip

        pg = self._order_blocks_zamer_row(order)
        p = payload if isinstance(payload, dict) else order_service_cell_payload(order, portal_row, service, pg)
        st = p.get("state") or "pending"
        lines = p.get("lines") or []
        it = QTableWidgetItem("\n".join(lines))
        it.setFlags((it.flags() | Qt.ItemIsEnabled) & ~Qt.ItemIsEditable)
        it.setData(_SERVICE_STATE_ROLE, st)
        f = QFont()
        f.setPointSize(10)
        it.setFont(f)
        it.setToolTip(order_service_cell_tooltip(order, portal_row, service, pg))
        return it


    def _portal_service_cell_unchanged(self, existing, order, portal_row, service: str, payload=None) -> bool:
        if existing is None:
            return False
        from ui.order_tile import order_service_cell_payload

        pg = self._order_blocks_zamer_row(order)
        p = payload if isinstance(payload, dict) else order_service_cell_payload(order, portal_row, service, pg)
        st = p.get("state") or "pending"
        text = "\n".join(p.get("lines") or [])
        return (
            existing.text() == text
            and (existing.data(_SERVICE_STATE_ROLE) or "pending") == st
        )

    def _async_fetch_portal_by_zids(self, zids, generation):
        zids = [int(z) for z in zids]

        def worker():
            m = {}
            try:
                from calc import zamer_api_client as api
            except Exception:
                api = None
            if api and api.api_enabled():
                for zid in zids:
                    try:
                        m[zid] = api.zamer_get(int(zid))
                    except Exception:
                        m[zid] = None
            QTimer.singleShot(0, lambda g=generation, mm=m: self._on_async_portal_map(g, mm))

        threading.Thread(target=worker, daemon=True).start()

    def _on_async_portal_map(self, generation, portal_by_zid):
        if generation != self._portal_fetch_generation:
            return
        if getattr(self, "_main_table_mode", "") != "orders":
            return
        from ui.order_tile import portal_zamer_id_from_order, order_service_cell_payload

        orders = self._current_orders
        tbl = self._orders_table
        if len(orders) != tbl.rowCount():
            return
        for row, o in enumerate(orders):
            it0 = tbl.item(row, 0)
            if not it0 or it0.data(Qt.UserRole) != self._order_cache_key(o):
                return
            zid = portal_zamer_id_from_order(o)
            pr = None
            if zid is not None:
                raw = portal_by_zid.get(int(zid))
                pr = raw if isinstance(raw, dict) else None
            ck = self._order_cache_key(o)
            if ck is not None:
                self._order_portal_snapshot[ck] = pr
            pg = self._order_blocks_zamer_row(o)
            for col, svc in (
                (_OC_MEASURE, "measure"),
                (_OC_DELIVERY, "delivery"),
                (_OC_INSTALL, "install"),
            ):
                old_it = tbl.item(row, col)
                p = order_service_cell_payload(o, pr, svc, pg)
                if self._portal_service_cell_unchanged(old_it, o, pr, svc, payload=p):
                    continue
                tbl.setItem(row, col, self._orders_service_item(o, pr, svc, payload=p))

    def _footer_orders_legend(self):
        panel_bg = color("header_bg")
        w = QWidget()
        w.setStyleSheet("background-color: %s; border: none;" % panel_bg)
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 2, 0)
        h.setSpacing(6)

        def legend_chip(bg: str, border: str, text: str, tip: str):
            row = QFrame()
            row.setObjectName("ordersLegendChip")
            row.setStyleSheet(
                "QFrame#ordersLegendChip { background-color: %s; border: none; border-radius: 8px; }"
                % panel_bg
            )
            rl = QHBoxLayout(row)
            rl.setContentsMargins(2, 1, 4, 1)
            rl.setSpacing(4)
            sq = QFrame()
            sq.setFixedSize(9, 9)
            sq.setStyleSheet(
                "QFrame { background:%s; border:1px solid %s; border-radius:3px; }" % (bg, border)
            )
            lb = QLabel(text)
            lb.setStyleSheet("font-size:9px; color:#263238; background: transparent;")
            lb.setToolTip(tip)
            row.setToolTip(tip)
            rl.addWidget(sq, 0, Qt.AlignVCenter)
            rl.addWidget(lb, 0, Qt.AlignVCenter)
            return row
        def legend_group(title: str):
            box = QWidget()
            vl = QVBoxLayout(box)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(2)
            cap = QLabel(title)
            cap.setStyleSheet("font-size:9px; font-weight:700; color:#1f3b4d;")
            vl.addWidget(cap)
            return box, vl

        # 1) Портал-колонки (замер/доставка/монтаж)
        g_portal, gl_portal = legend_group("Замер / Доставка / Монтаж")
        row_portal = QHBoxLayout()
        row_portal.setContentsMargins(0, 0, 0, 0)
        row_portal.setSpacing(3)
        row_portal.addWidget(
            legend_chip(
                "#ffcdd2",
                "#e57373",
                "услуга не нужна",
                "Колонки замер / доставка / монтаж: услуга отключена в расчёте или на портале.",
            )
        )
        row_portal.addWidget(
            legend_chip(
                "#1565c0",
                "#0d47a1",
                "ожидание",
                "Услуга запланирована, заявка на портале без исполнителя или без готового файла.",
            )
        )
        row_portal.addWidget(
            legend_chip(
                "#f57c00",
                "#e65100",
                "в работе",
                "Заявка принята монтажником: указан исполнитель (логин в ячейке).",
            )
        )
        row_portal.addWidget(
            legend_chip(
                "#2e7d32",
                "#1b5e20",
                "готово",
                "По порталу загружен файл по этой услуге или заявка завершена.",
            )
        )
        gl_portal.addLayout(row_portal)
        h.addWidget(g_portal, 0)

        # 2) Колонка раскрой
        g_cut, gl_cut = legend_group("Колонка «Раскрой»")
        row_cut = QHBoxLayout()
        row_cut.setContentsMargins(0, 0, 0, 0)
        row_cut.setSpacing(3)
        row_cut.addWidget(
            legend_chip(
                "#eceff1",
                "#90a4ae",
                "раскрой н/д",
                "Колонка «Раскрой»: не применимо к статусу заказа.",
            )
        )
        row_cut.addWidget(
            legend_chip(
                "#ffe0b2",
                "#fb8c00",
                "раскрой ждёт",
                "Оплачен или в работе, но нет сохранённого раскроя в базе.",
            )
        )
        row_cut.addWidget(
            legend_chip(
                "#c8e6c9",
                "#2e7d32",
                "раскрой есть",
                "В базе есть сохранённый раскрой по заказу.",
            )
        )
        gl_cut.addLayout(row_cut)
        h.addWidget(g_cut, 0)

        # 3) Колонка статус (2 ряда)
        g_status, gl_status = legend_group("Колонка «Статус»")
        row_st1 = QHBoxLayout()
        row_st1.setContentsMargins(0, 0, 0, 0)
        row_st1.setSpacing(3)
        row_st2 = QHBoxLayout()
        row_st2.setContentsMargins(0, 0, 0, 0)
        row_st2.setSpacing(3)
        status_chips = [
            legend_chip(color("status_draft"), "#607d8b", "просчет", "Статус изделия/заказа: просчет."),
            legend_chip(color("status_paid"), "#607d8b", "оплачен", "Статус изделия/заказа: оплачен."),
            legend_chip(color("status_in_progress"), "#607d8b", "в работе", "Статус изделия/заказа: в работе."),
            legend_chip(color("status_made"), "#607d8b", "изготовлен", "Статус изделия/заказа: изготовлен."),
            legend_chip(color("status_checked_qr"), "#607d8b", "проверен QR", "Статус изделия/заказа: проверен QR."),
            legend_chip(color("status_shipped"), "#607d8b", "отгружен", "Статус изделия/заказа: отгружен."),
            legend_chip(color("status_completed"), "#607d8b", "выполнен", "Статус изделия/заказа: выполнен."),
        ]
        for i, ch in enumerate(status_chips):
            if i < 3:
                row_st1.addWidget(ch)
            else:
                row_st2.addWidget(ch)
        gl_status.addLayout(row_st1)
        gl_status.addLayout(row_st2)
        h.addWidget(g_status, 0)
        h.addStretch(1)

        return w

    def _schedule_apply_order_filters(self):
        if getattr(self, "_main_table_mode", "") != "orders":
            return
        self._filter_apply_timer.start()

    def _date_edit_range(self, w: QDateEdit):
        if w.date() <= w.minimumDate():
            return None
        return w.date().toPyDate()

    def _parse_int_opt(self, s):
        s = (s or "").strip().replace(" ", "").replace("\u00a0", "")
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None

    def _interval_overlaps(self, os_lo, os_hi, fs, fe):
        if fs is None and fe is None:
            return True
        if os_lo is None and os_hi is None:
            return False
        s = os_lo or os_hi
        e = os_hi or os_lo
        F = fs or fe
        T = fe or fs
        if F is not None and T is not None and F > T:
            F, T = T, F
        if s is not None and e is not None and s > e:
            s, e = e, s
        return s <= T and e >= F

    def _rebuild_order_filter_cache(self):
        self._order_filter_cache = _build_order_filter_cache_entries(
            self._all_orders,
            getattr(self, "_blocks_zamer_by_order", None) or {},
        )

    def _schedule_rebuild_order_filter_cache_async(self):
        """Пересчёт сумм для фильтров в фоне — таблица рисуется сразу по JSON, потом подтягиваются SQL-цены."""
        self._filter_cache_generation += 1
        gen = self._filter_cache_generation
        old = getattr(self, "_filter_cache_thread", None)
        if old is not None and old.isRunning():
            old.wait(300)
        th = _OrderFilterCacheThread(
            gen,
            self._all_orders,
            getattr(self, "_blocks_zamer_by_order", None) or {},
            getattr(self, "_production_events_by_order", None) or {},
            self,
        )
        th.cache_ready.connect(self._on_async_filter_cache_ready)
        self._filter_cache_thread = th
        th.start()

    def _on_async_filter_cache_ready(self, cache, meta_heavy, generation):
        if int(generation) != int(getattr(self, "_filter_cache_generation", 0)):
            return
        self._order_filter_cache = dict(cache or {})
        for ck, patch in (meta_heavy or {}).items():
            if ck in self._order_row_meta:
                self._order_row_meta[ck].update(patch)
            else:
                self._order_row_meta[ck] = dict(patch)
        if getattr(self, "_main_table_mode", "") != "orders":
            return
        tbl = self._orders_table
        disp = getattr(self, "_table_display_order", None) or []
        if tbl.rowCount() != len(disp):
            return
        tbl.setUpdatesEnabled(False)
        try:
            for row, o in enumerate(disp):
                if tbl.isRowHidden(row):
                    continue
                ck = self._order_cache_key(o)
                iv = (self._order_filter_cache or {}).get(ck) if ck else None
                tot = iv.get("total") if iv else None
                if tot is None:
                    continue
                it_tot = tbl.item(row, _OC_TOTAL)
                if it_tot is not None:
                    it_tot.setText(str(tot))
                it_cnt = tbl.item(row, _OC_PRODUCTS)
                if it_cnt is not None and iv.get("count") is not None:
                    it_cnt.setText(str(int(iv.get("count") or 0)))
        finally:
            tbl.setUpdatesEnabled(True)
        tbl.viewport().update()

    def _rebuild_order_row_meta_light(self):
        self._order_row_meta = _build_order_row_meta_light(
            self._all_orders,
            getattr(self, "_clients_by_id", None) or {},
            getattr(self, "_clients_by_name", None) or {},
            getattr(self, "_order_cut_ids", None) or frozenset(),
        )

    def _order_matches_payment_filter_key(self, o, meta, pay_key: str) -> bool:
        if not pay_key:
            return True
        if self._is_sales_row(o):
            st = str(o.get("status") or "").strip().lower()
            if pay_key == _FILTER_PAYMENT_UNPAID:
                return st != "paid"
            if pay_key == _FILTER_PAYMENT_PARTIAL:
                return False
            if pay_key == _FILTER_PAYMENT_ALL:
                return True
            return st == "paid"
        ps = str((meta or {}).get("payment_state") or "")
        if not ps:
            try:
                from logic.blocks_bundle import bundle_payment_aggregate

                products = self._glass_order_bundle_products(o)
                ps = str((bundle_payment_aggregate(products) or {}).get("state") or "unpaid")
            except Exception:
                ps = "unpaid"
        if pay_key == _FILTER_PAYMENT_UNPAID:
            return ps == "unpaid"
        if pay_key == _FILTER_PAYMENT_PARTIAL:
            return ps == "partial"
        if pay_key.startswith(_FILTER_PAYMENT_METHOD_PREFIX):
            method = pay_key[len(_FILTER_PAYMENT_METHOD_PREFIX) :]
            methods = (meta or {}).get("payment_methods") or []
            if methods:
                return method in methods
            try:
                from logic.blocks_bundle import PAYMENT_UNPAID, product_is_paid

                for p in self._glass_order_bundle_products(o):
                    if product_is_paid(p) and str(p.get("payment_type") or "").strip() == method:
                        return True
            except Exception:
                pass
            return False
        return True

    def _order_status_counts_for_filter(self, o, meta):
        cnts = (meta or {}).get("status_counts")
        tot = (meta or {}).get("status_total")
        if isinstance(cnts, dict) and tot is not None:
            return cnts, int(tot or 0)
        if self._is_sales_row(o):
            st = str(o.get("status") or "draft").strip() or "draft"
            return {st: 1}, 1
        products = self._glass_order_bundle_products(o)
        if not products:
            st = str(o.get("status") or "draft").strip() or "draft"
            return {st: 1}, 1
        try:
            from logic.blocks_bundle import bundle_status_unit_counts
        except Exception:
            return {}, 0
        oid = o.get("id")
        ev = None
        if oid is not None:
            try:
                ev = (getattr(self, "_production_events_by_order", None) or {}).get(int(oid))
            except (TypeError, ValueError):
                ev = None
        count_cb = None
        if oid is not None:
            try:
                from db import models as db_models

                if hasattr(db_models, "count_facade_instance_assembled_events"):

                    def _count(oid_i, idx1, events):
                        return int(
                            db_models.count_facade_instance_assembled_events(
                                oid_i, idx1, production_events=events
                            )
                        )

                    count_cb = _count
            except Exception:
                count_cb = None
        try:
            return bundle_status_unit_counts(
                products,
                order_fallback_status=str(o.get("status") or "draft"),
                order_id=int(oid) if oid is not None else None,
                facade_production_events=ev,
                count_facade_assembled=count_cb,
            )
        except Exception:
            return {}, 0

    def _order_matches_status_partial_filter(self, o, meta, status_key: str) -> bool:
        if not status_key:
            return True
        counts, _total = self._order_status_counts_for_filter(o, meta)
        return int(counts.get(status_key) or 0) >= 1

    def _order_matches_status_full_filter(self, o, meta, status_key: str) -> bool:
        if not status_key:
            return True
        counts, total = self._order_status_counts_for_filter(o, meta)
        if total <= 0:
            return False
        return int(counts.get(status_key) or 0) == total

    def _order_type_label_fast(self, orow):
        ck = self._order_cache_key(orow)
        if ck:
            m = (getattr(self, "_order_row_meta", None) or {}).get(ck)
            if m and m.get("type_label"):
                return m["type_label"]
        return _type_label_from_order_row(orow)

    def _row_passes_active_filters(self, o):
        """Проверка одной строки по кэшу — без parse bundle и без перерисовки таблицы."""
        from db import models as db_models

        ck = self._order_cache_key(o)
        m = (getattr(self, "_order_row_meta", None) or {}).get(ck) if ck else None
        if m is None:
            m = {}

        qid = (self._flt_col_id.text() or "").strip().lower()
        if qid and qid not in m.get("id_str", str(o.get("id") or "").lower()):
            return False
        text = (self._flt_col_client.text() or "").strip().lower()
        if text and text not in m.get("client_lc", str(o.get("client_name") or "").lower()):
            return False
        mgr = (self._flt_col_manager.text() or "").strip().lower()
        if mgr:
            tokens = [t for t in mgr.split() if t]
            fn = (o.get("creator_name") or "").strip().lower()
            sn = (o.get("creator_surname") or "").strip().lower()
            lg = (o.get("created_by_login") or "").strip().lower()
            lbl = m.get("manager_lc") or self._order_manager_label(o).lower()
            for t in tokens:
                if t not in fn and t not in sn and t not in lg and t not in lbl:
                    return False
        ti = self._flt_type_combo.currentIndex()
        if ti > 0:
            want = self._flt_type_combo.itemText(ti)
            if m.get("type_label") != want:
                return False
        np = int(self._flt_products_spin.value())
        if np > 0:
            iv = (self._order_filter_cache or {}).get(ck) if ck else None
            if iv is not None and iv.get("count") is not None:
                cnt = int(iv.get("count") or 0)
            else:
                cnt = int(self._glass_order_total_and_count(o)[1] or 0)
            if cnt != np:
                return False
        cf = self._date_edit_range(self._flt_created_from)
        ct = self._date_edit_range(self._flt_created_to)
        if cf is not None or ct is not None:
            a, b = self._order_created_bounds(o)
            if not self._interval_overlaps(a, b, cf, ct):
                return False
        sf = self._parse_int_opt((getattr(self, "_flt_sum_from", None) or QLineEdit()).text())
        st = self._parse_int_opt((getattr(self, "_flt_sum_to", None) or QLineEdit()).text())
        if sf is not None or st is not None:
            tot = (self._order_filter_cache.get(ck) or {}).get("total") if ck else None
            if tot is None:
                return False
            if sf is not None and int(tot) < int(sf):
                return False
            if st is not None and int(tot) > int(st):
                return False
        pay_key = ""
        if getattr(self, "_flt_payment_method_combo", None) is not None:
            pay_key = str(self._flt_payment_method_combo.currentData() or "")
        if pay_key:
            if not self._order_matches_payment_filter_key(o, m, pay_key):
                return False
        surcharge_sub = (self._flt_col_surcharge.text() or "").strip().lower()
        if surcharge_sub:
            sur_lc = m.get("surcharge_lc")
            if not sur_lc:
                sur_lc = self._order_surcharge_filter_text(o).lower()
            if surcharge_sub not in (sur_lc or ""):
                return False
        part_st = ""
        full_st = ""
        if getattr(self, "_flt_status_partial_combo", None) is not None:
            part_st = str(self._flt_status_partial_combo.currentData() or "").strip()
        if getattr(self, "_flt_status_full_combo", None) is not None:
            full_st = str(self._flt_status_full_combo.currentData() or "").strip()
        if part_st and not self._order_matches_status_partial_filter(o, m, part_st):
            return False
        if full_st and not self._order_matches_status_full_filter(o, m, full_st):
            return False
        phone_sub = (self._flt_client_phone.text() or "").strip().lower()
        if phone_sub:
            phone_digits = "".join(ch for ch in phone_sub if ch.isdigit())
            ph_d = m.get("phone_digits", "")
            ph_lc = m.get("phone_lc", "")
            if not ((phone_digits and phone_digits in ph_d) or (phone_sub in ph_lc)):
                return False
        inn_sub = (self._flt_client_inn.text() or "").strip().lower()
        if inn_sub:
            inn_digits = "".join(ch for ch in inn_sub if ch.isdigit())
            inn_d = m.get("inn_digits", "")
            inn_lc = m.get("inn_lc", "")
            if not ((inn_digits and inn_digits in inn_d) or (inn_sub in inn_lc)):
                return False
        ci = self._flt_cut_combo.currentIndex()
        if ci == 1 and m.get("cut_cell") != "да":
            return False
        if ci == 2 and m.get("cut_cell") != "нет":
            return False
        for svc, wf, wt in (
            ("measure", self._flt_meas_from, self._flt_meas_to),
            ("delivery", self._flt_del_from, self._flt_del_to),
            ("install", self._flt_inst_from, self._flt_inst_to),
        ):
            df = self._date_edit_range(wf)
            dt = self._date_edit_range(wt)
            if df is None and dt is None:
                continue
            pair = (self._order_filter_cache.get(ck) or {}).get(svc) if ck else None
            if pair is None:
                pair = (None, None)
            if not self._interval_overlaps(pair[0], pair[1], df, dt):
                return False
        return True

    def _apply_filter_visibility(self):
        """Скрыть/показать уже отрисованные строки — без пересоздания виджетов и без БД."""
        if getattr(self, "_main_table_mode", "") != "orders":
            return
        tbl = self._orders_table
        disp = getattr(self, "_table_display_order", None) or []
        if tbl.rowCount() != len(disp):
            self._table_display_order = list(self._all_orders)
            disp = self._table_display_order
            self._fill_orders_table(disp)
        visible = []
        tbl.setUpdatesEnabled(False)
        try:
            for row, o in enumerate(disp):
                show = self._row_passes_active_filters(o)
                tbl.setRowHidden(row, not show)
                if show:
                    visible.append(o)
        finally:
            tbl.setUpdatesEnabled(True)
        self._current_orders = visible

    def _is_sales_row(self, row):
        return str((row or {}).get("__row_kind") or "").strip().lower() == "sales"

    def _order_cache_key(self, row):
        rid = (row or {}).get("id")
        if rid is None:
            return None
        return "%s:%s" % ("sales" if self._is_sales_row(row) else "order", rid)

    def _order_blocks_zamer_row(self, order_row):
        """Снимок blocks_zamer из кэша загрузки заказов (та же БД, что синхронизируется с порталом)."""
        if self._is_sales_row(order_row):
            return None
        oid = (order_row or {}).get("id")
        if oid is None:
            return None
        try:
            oid = int(oid)
        except (TypeError, ValueError):
            return None
        return (getattr(self, "_blocks_zamer_by_order", None) or {}).get(oid)

    def _order_client_row(self, order_row):
        cid = order_row.get("client_id")
        try:
            cid = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid = None
        if cid is not None and cid in self._clients_by_id:
            return self._clients_by_id.get(cid)
        name = str(order_row.get("client_name") or "").strip().lower()
        if name:
            return self._clients_by_name.get(name)
        return None

    def _row_client_for_generic(self, row):
        cid = row.get("client_id")
        try:
            cid = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid = None
        if cid is not None and cid in self._clients_by_id:
            return self._clients_by_id.get(cid)
        name = str(row.get("client_name") or "").strip().lower()
        if name:
            return self._clients_by_name.get(name)
        return None

    def _rebuild_order_global_search_completers(self):
        phones = []
        inns = []
        for c in (self._all_clients_for_search or []):
            nm = str(c.get("name") or "").strip()
            ph = str(c.get("phone") or "").strip()
            inn = str(c.get("inn") or "").strip()
            if ph:
                phones.append("%s · %s" % (ph, nm or "клиент"))
            if inn:
                inns.append("%s · %s" % (inn, nm or "клиент"))
        if getattr(self, "_flt_client_phone", None) is not None:
            cp = QCompleter(sorted(set(phones)), self._flt_client_phone)
            cp.setCaseSensitivity(Qt.CaseInsensitive)
            cp.setFilterMode(Qt.MatchContains)
            cp.setCompletionMode(QCompleter.PopupCompletion)
            self._flt_client_phone.setCompleter(cp)
        if getattr(self, "_flt_client_inn", None) is not None:
            ci = QCompleter(sorted(set(inns)), self._flt_client_inn)
            ci.setCaseSensitivity(Qt.CaseInsensitive)
            ci.setFilterMode(Qt.MatchContains)
            ci.setCompletionMode(QCompleter.PopupCompletion)
            self._flt_client_inn.setCompleter(ci)

    def _compute_filtered_order_rows(self):
        """Список заказов после фильтров (только память + кэш, без БД и без отрисовки)."""
        return [o for o in (self._all_orders or []) if self._row_passes_active_filters(o)]

    def _apply_column_sort_to_rows(self, rows):
        rows = list(rows)
        sec = getattr(self, "_table_sort_section", None)
        if sec is None or int(sec) >= _OC_ACTIONS:
            return rows
        asc = getattr(self, "_table_sort_ascending", True)

        def keyfn(o):
            return self._orders_sort_key(o, int(sec))

        rows.sort(key=keyfn, reverse=not asc)
        return rows

    def _order_row_keys_sequence(self, rows):
        return [self._order_cache_key(o) for o in rows]

    def _table_row_for_cache_key(self, cache_key):
        tbl = self._orders_table
        for r in range(tbl.rowCount()):
            it = tbl.item(r, 0)
            if it is not None and it.data(Qt.UserRole) == cache_key:
                return r
        return -1

    def _orders_table_display_sequence(self):
        """Все строки таблицы в порядке активной сортировки (без фильтра видимости)."""
        return self._apply_column_sort_to_rows(list(self._all_orders or []))

    def _table_keys_from_widget(self):
        tbl = self._orders_table
        keys = []
        for r in range(tbl.rowCount()):
            it = tbl.item(r, 0)
            keys.append(it.data(Qt.UserRole) if it is not None else None)
        return keys

    def _order_dict_by_cache_key(self, cache_key):
        if not cache_key:
            return None
        for o in self._all_orders or []:
            if self._order_cache_key(o) == cache_key:
                return o
        return None

    def _refresh_orders_table_row_by_cache_key(self, cache_key):
        o = self._order_dict_by_cache_key(cache_key)
        if o is None:
            return
        self._patch_order_filter_cache_for_orders([o])
        self._patch_order_row_meta_for_orders([o])
        tr = self._table_row_for_cache_key(cache_key)
        if tr >= 0:
            self._rewrite_orders_table_row(tr, o)

    def _insert_orders_table_row(self, index: int, o):
        try:
            from calc import zamer_api_client as _zamer_api
        except Exception:
            _zamer_api = None
        api_on = bool(_zamer_api and _zamer_api.api_enabled())
        tbl = self._orders_table
        prev_snap = dict(self._order_portal_snapshot or {})
        idx = max(0, min(int(index), tbl.rowCount()))
        tbl.insertRow(idx)
        zids_for_fetch = set()
        tbl.setUpdatesEnabled(False)
        try:
            self._fill_orders_table_row_at(tbl, idx, o, prev_snap, api_on, _zamer_api, zids_for_fetch)
        finally:
            tbl.setUpdatesEnabled(True)
        if zids_for_fetch and api_on and _zamer_api:
            self._portal_fetch_generation += 1
            self._async_fetch_portal_by_zids(zids_for_fetch, self._portal_fetch_generation)

    def _schedule_table_sync(self, fn) -> None:
        """Обновить таблицу после закрытия диалога — в следующем тике event loop (UI не замирает на exec_)."""
        QTimer.singleShot(0, fn)

    def _sync_orders_table_after_data_change(
        self,
        *,
        mirror_touch_id=None,
        mirror_removed_id=None,
        mirror_appended_id=None,
        sales_touch_id=None,
        sales_removed_id=None,
        sales_appended_id=None,
    ):
        """Точечное обновление таблицы: removeRow / insertRow / swap / rewrite одной строки.

        Полный ``_fill_orders_table`` — только если рассинхрон или сменился набор ключей.
        """
        if getattr(self, "_main_table_mode", "") != "orders":
            self._current_orders = list(self._all_orders)
            self._refresh_orders_display(self._current_orders)
            return
        tbl = self._orders_table

        if sales_removed_id is not None:
            ck = "sales:%s" % int(sales_removed_id)
            self._order_row_meta.pop(ck, None)
            self._table_display_order = self._orders_table_display_sequence()
            r = self._table_row_for_cache_key(ck)
            if r >= 0:
                tbl.removeRow(r)
            self._apply_filter_visibility()
            return

        if sales_appended_id is not None:
            ck = "sales:%s" % int(sales_appended_id)
            new_o = self._order_dict_by_cache_key(ck)
            if new_o:
                self._patch_order_filter_cache_for_orders([new_o])
                self._patch_order_row_meta_for_orders([new_o])
            self._table_display_order = self._orders_table_display_sequence()
            want_idx = next(
                (i for i, o in enumerate(self._table_display_order) if self._order_cache_key(o) == ck),
                -1,
            )
            n_need = len(self._table_display_order)
            if new_o is not None and tbl.rowCount() == n_need - 1 and 0 <= want_idx <= n_need - 1:
                self._insert_orders_table_row(want_idx, new_o)
            else:
                self._fill_orders_table(self._table_display_order)
            self._apply_filter_visibility()
            return

        if mirror_removed_id is not None:
            ck = "order:%s" % int(mirror_removed_id)
            self._order_row_meta.pop(ck, None)
            self._table_display_order = self._orders_table_display_sequence()
            r = self._table_row_for_cache_key(ck)
            if r >= 0:
                tbl.removeRow(r)
            self._apply_filter_visibility()
            return

        if mirror_appended_id is not None:
            ck = "order:%s" % int(mirror_appended_id)
            new_o = self._order_dict_by_cache_key(ck)
            if new_o:
                self._patch_order_filter_cache_for_orders([new_o])
                self._patch_order_row_meta_for_orders([new_o])
            self._table_display_order = self._orders_table_display_sequence()
            want_idx = next(
                (i for i, o in enumerate(self._table_display_order) if self._order_cache_key(o) == ck),
                -1,
            )
            n_need = len(self._table_display_order)
            if new_o is not None and tbl.rowCount() == n_need - 1 and 0 <= want_idx <= n_need - 1:
                self._insert_orders_table_row(want_idx, new_o)
            else:
                self._fill_orders_table(self._table_display_order)
            self._apply_filter_visibility()
            return

        touch_ck = None
        if sales_touch_id is not None:
            touch_ck = "sales:%s" % int(sales_touch_id)
        elif mirror_touch_id is not None:
            touch_ck = "order:%s" % int(mirror_touch_id)

        self._table_display_order = self._orders_table_display_sequence()
        want_keys = self._order_row_keys_sequence(self._table_display_order)
        cur_keys = self._table_keys_from_widget()

        if cur_keys == want_keys:
            if touch_ck:
                self._refresh_orders_table_row_by_cache_key(touch_ck)
        elif len(cur_keys) == len(want_keys) and Counter(cur_keys) == Counter(want_keys):
            try:
                self._reorder_orders_table_fast(self._table_display_order)
            except Exception:
                self._fill_orders_table(self._table_display_order)
            if touch_ck:
                self._refresh_orders_table_row_by_cache_key(touch_ck)
        else:
            self._fill_orders_table(self._table_display_order)

        self._apply_filter_visibility()

    def _sales_order_dict_in_all_orders(self, sales_id: int):
        sid = int(sales_id)
        for o in self._all_orders or []:
            if self._is_sales_row(o) and int(o.get("id") or 0) == sid:
                return o
        return None

    def _light_resync_sales_order_from_db(self, sales_id) -> None:
        from db import models as db_models

        sid = int(sales_id)
        old = self._sales_order_dict_in_all_orders(sid)
        row = db_models.get_sales_order(sid)
        if not row:
            if old is not None:
                self._light_remove_sales_order_from_memory(sid)
            return
        if old is None:
            self._light_append_sales_order_from_db(sid)
            return
        d = dict(row)
        d["__row_kind"] = "sales"
        try:
            cnts = db_models.list_sales_items_counts_bulk([sid]) or {}
            c = cnts.get(sid) or {}
            d["_sales_items_count"] = int(c.get("items_count") or 0)
            d["_sales_delivery_items_count"] = int(c.get("delivery_count") or 0)
        except Exception:
            d["_sales_items_count"] = int(old.get("_sales_items_count") or 0)
            d["_sales_delivery_items_count"] = int(old.get("_sales_delivery_items_count") or 0)
        old.clear()
        old.update(d)
        self._patch_order_filter_cache_for_orders([old])
        self._patch_order_row_meta_for_orders([old])
        if getattr(self, "_main_table_mode", "") == "orders":
            self._sync_orders_table_after_data_change(sales_touch_id=sid)
        else:
            self._current_orders = list(self._all_orders)

    def _light_remove_sales_order_from_memory(self, sales_id) -> None:
        sid = int(sales_id)
        self._all_orders = [
            o
            for o in (self._all_orders or [])
            if not (self._is_sales_row(o) and int(o.get("id") or 0) == sid)
        ]
        self._order_filter_cache.pop("sales:%s" % sid, None)
        self._order_row_meta.pop("sales:%s" % sid, None)
        if getattr(self, "_main_table_mode", "") == "orders":
            self._sync_orders_table_after_data_change(sales_removed_id=sid)
        else:
            self._current_orders = list(self._all_orders)

    def _light_append_sales_order_from_db(self, sales_id) -> None:
        from db import models as db_models

        sid = int(sales_id)
        if self._sales_order_dict_in_all_orders(sid) is not None:
            self._light_resync_sales_order_from_db(sid)
            return
        row = db_models.get_sales_order(sid)
        if not row:
            return
        d = dict(row)
        d["__row_kind"] = "sales"
        try:
            cnts = db_models.list_sales_items_counts_bulk([sid]) or {}
            c = cnts.get(sid) or {}
            d["_sales_items_count"] = int(c.get("items_count") or 0)
            d["_sales_delivery_items_count"] = int(c.get("delivery_count") or 0)
        except Exception:
            d["_sales_items_count"] = 0
            d["_sales_delivery_items_count"] = 0
        self._all_orders.append(d)
        self._sort_mirror_and_sales_orders_inplace()
        self._patch_order_filter_cache_for_orders([d])
        self._patch_order_row_meta_for_orders([d])
        if getattr(self, "_main_table_mode", "") == "orders":
            self._sync_orders_table_after_data_change(sales_appended_id=sid)
        else:
            self._current_orders = list(self._all_orders)

    def _refresh_mirror_cut_column_after_bulk_cut(self) -> None:
        """После массового раскроя — только флаги «раскрой да/нет», без полного _load_orders."""
        from db import models as db_models

        mirror_orders = [o for o in (self._all_orders or []) if not self._is_sales_row(o) and o.get("id")]
        oids = []
        for o in mirror_orders:
            try:
                oids.append(int(o.get("id")))
            except (TypeError, ValueError):
                continue
        if oids:
            try:
                self._order_cut_ids = frozenset(db_models.get_order_ids_with_cut_results(oids))
            except Exception:
                pass
        self._patch_order_row_meta_for_orders(mirror_orders)
        if getattr(self, "_main_table_mode", "") != "orders":
            self._current_orders = list(self._all_orders)
            return
        tbl = self._orders_table
        disp = getattr(self, "_table_display_order", None) or []
        tbl.setUpdatesEnabled(False)
        try:
            for row, o in enumerate(disp):
                if self._is_sales_row(o) or row >= tbl.rowCount():
                    continue
                it = tbl.item(row, _OC_ID)
                if it is None or it.data(Qt.UserRole) != self._order_cache_key(o):
                    continue
                tbl.setItem(row, _OC_CUT, self._orders_cut_item(o))
        finally:
            tbl.setUpdatesEnabled(True)
        self._apply_filter_visibility()

    def _apply_all_order_filters(self):
        if getattr(self, "_main_table_mode", "") != "orders":
            return
        self._apply_filter_visibility()

    def _on_reset_all_order_filters(self):
        self._flt_col_id.blockSignals(True)
        self._flt_col_client.blockSignals(True)
        self._flt_col_manager.blockSignals(True)
        self._flt_col_surcharge.blockSignals(True)
        self._flt_payment_method_combo.blockSignals(True)
        self._flt_status_partial_combo.blockSignals(True)
        self._flt_status_full_combo.blockSignals(True)
        self._flt_sum_from.blockSignals(True)
        self._flt_sum_to.blockSignals(True)
        self._flt_client_phone.blockSignals(True)
        self._flt_client_inn.blockSignals(True)
        self._flt_col_id.clear()
        self._flt_col_client.clear()
        self._flt_col_manager.clear()
        self._flt_col_surcharge.clear()
        self._flt_payment_method_combo.setCurrentIndex(0)
        self._flt_status_partial_combo.setCurrentIndex(0)
        self._flt_status_full_combo.setCurrentIndex(0)
        self._flt_sum_from.clear()
        self._flt_sum_to.clear()
        self._flt_client_phone.clear()
        self._flt_client_inn.clear()
        self._flt_col_id.blockSignals(False)
        self._flt_col_client.blockSignals(False)
        self._flt_col_manager.blockSignals(False)
        self._flt_col_surcharge.blockSignals(False)
        self._flt_payment_method_combo.blockSignals(False)
        self._flt_status_partial_combo.blockSignals(False)
        self._flt_status_full_combo.blockSignals(False)
        self._flt_sum_from.blockSignals(False)
        self._flt_sum_to.blockSignals(False)
        self._flt_client_phone.blockSignals(False)
        self._flt_client_inn.blockSignals(False)
        self._flt_cut_combo.blockSignals(True)
        self._flt_cut_combo.setCurrentIndex(0)
        self._flt_cut_combo.blockSignals(False)
        self._flt_type_combo.blockSignals(True)
        self._flt_type_combo.setCurrentIndex(0)
        self._flt_type_combo.blockSignals(False)
        self._flt_products_spin.blockSignals(True)
        self._flt_products_spin.setValue(0)
        self._flt_products_spin.blockSignals(False)
        for w in (
            self._flt_created_from,
            self._flt_created_to,
            self._flt_meas_from,
            self._flt_meas_to,
            self._flt_del_from,
            self._flt_del_to,
            self._flt_inst_from,
            self._flt_inst_to,
        ):
            w.blockSignals(True)
            w.setDate(w.minimumDate())
            w.blockSignals(False)
        self._apply_all_order_filters()

    def _on_refresh_orders_list(self):
        if getattr(self, "_main_table_mode", "") != "orders":
            return
        sel = self._orders_table.selectionModel().selectedRows()
        if not sel:
            return
        r = sel[0].row()
        it = self._orders_table.item(r, 0)
        if not it:
            return
        ck = it.data(Qt.UserRole)
        if not ck:
            return
        from db import models as db_models

        self._skip_portal_snap_reuse = True
        try:
            if isinstance(ck, str) and ck.startswith("order:"):
                oid = int(ck.split(":", 1)[1])
                old = self._mirror_order_dict_in_all_orders(oid)
                if not old:
                    return
                row = db_models.get_mirror_order_list_row(oid)
                if not row:
                    return
                raw_before = old.get("blocks_calc_json")
                self._invalidate_bundle_products_for_raw(raw_before)
                old.clear()
                old.update(dict(row))
                try:
                    bulk = db_models.get_order_items_bulk([oid]) or {}
                    self._orders_items[oid] = list(bulk.get(oid) or [])
                except Exception:
                    try:
                        self._orders_items[oid] = db_models.get_order_items(oid) or []
                    except Exception:
                        self._orders_items[oid] = []
                try:
                    zm = db_models.get_blocks_zamer_rows_by_mirror_order_ids([oid]) or {}
                    self._blocks_zamer_by_order.update(zm)
                except Exception:
                    pass
                try:
                    without_oid = frozenset(int(x) for x in self._order_cut_ids if int(x) != oid)
                    cut = db_models.get_order_ids_with_cut_results([oid]) or set()
                    self._order_cut_ids = without_oid | frozenset(int(x) for x in cut)
                except Exception:
                    pass
                self._patch_order_filter_cache_for_orders([old])
                self._sync_orders_table_after_data_change(mirror_touch_id=oid)
            elif isinstance(ck, str) and ck.startswith("sales:"):
                sid = int(ck.split(":", 1)[1])
                old = None
                for o in self._all_orders or []:
                    if self._is_sales_row(o) and int(o.get("id") or 0) == sid:
                        old = o
                        break
                if not old:
                    return
                row = db_models.get_sales_order(sid)
                if not row:
                    return
                d = dict(row)
                d["__row_kind"] = "sales"
                try:
                    cnts = db_models.list_sales_items_counts_bulk([sid]) or {}
                    c = cnts.get(sid) or {}
                    d["_sales_items_count"] = int(c.get("items_count") or 0)
                    d["_sales_delivery_items_count"] = int(c.get("delivery_count") or 0)
                except Exception:
                    d["_sales_items_count"] = int(old.get("_sales_items_count") or 0)
                    d["_sales_delivery_items_count"] = int(old.get("_sales_delivery_items_count") or 0)
                old.clear()
                old.update(d)
                self._patch_order_filter_cache_for_orders([old])
                self._sync_orders_table_after_data_change(sales_touch_id=sid)
        finally:
            self._skip_portal_snap_reuse = False

    def _reset_quick_filters(self):
        self._quick_filter.clear()
        self._quick_filter_phone.clear()
        self._quick_filter_inn.clear()
        self._apply_quick_filters()

    def _reload_quick_estimates_rows_only(self):
        """Обновить только список быстрых просчётов (без полного _load_orders)."""
        try:
            from db import models as db_models

            self._all_quick = db_models.list_quick_estimates(status="draft") or []
        except Exception:
            self._all_quick = []
        self._apply_quick_filters()

    def _apply_quick_filters(self):
        rows = list(self._all_quick or [])
        q = (self._quick_filter.text() or "").strip().lower()
        qp = (self._quick_filter_phone.text() or "").strip().lower()
        qi = (self._quick_filter_inn.text() or "").strip().lower()
        qpd = "".join(ch for ch in qp if ch.isdigit())
        qid = "".join(ch for ch in qi if ch.isdigit())
        if q:
            rows = [
                r
                for r in rows
                if q in str(r.get("id") or "").lower()
                or q in str(r.get("client_name") or "").lower()
                or q in str(r.get("category") or "").lower()
                or q in str(r.get("status") or "").lower()
            ]
        if qp:
            nxt = []
            for r in rows:
                c = self._row_client_for_generic(r) or {}
                ph = str(c.get("phone") or "")
                phd = "".join(ch for ch in ph if ch.isdigit())
                if (qpd and qpd in phd) or (qp in ph.lower()):
                    nxt.append(r)
            rows = nxt
        if qi:
            nxt = []
            for r in rows:
                c = self._row_client_for_generic(r) or {}
                inn = str(c.get("inn") or "")
                innd = "".join(ch for ch in inn if ch.isdigit())
                if (qid and qid in innd) or (qi in inn.lower()):
                    nxt.append(r)
            rows = nxt
        self._fill_quick_table(rows)

    def _init_zamer_board_listen(self):
        self._zamer_board_listen = None
        self._zamer_listen_ui_timer = QTimer(self)
        self._zamer_listen_ui_timer.setSingleShot(True)
        try:
            self._zamer_listen_ui_ms = int((os.environ.get("MC_ZAMER_LISTEN_UI_MS") or "2500").strip())
        except ValueError:
            self._zamer_listen_ui_ms = 2500
        self._zamer_listen_ui_ms = max(800, self._zamer_listen_ui_ms)
        self._zamer_listen_ui_timer.setInterval(self._zamer_listen_ui_ms)
        self._zamer_listen_ui_timer.timeout.connect(self._refresh_orders_portal_hints)
        try:
            from ui.zamer_pg_listen import ZamerBoardPgListen
        except Exception:
            return
        lst = ZamerBoardPgListen(self)
        self._zamer_board_listen = lst
        lst.changed.connect(self._schedule_zamer_listen_ui_refresh)
        lst.start()

    def _schedule_zamer_listen_ui_refresh(self):
        # По умолчанию выкл.: NOTIFY с доски часто даёт лишние перерисовки; включить: MC_ZAMER_LISTEN_REFRESH_UI=1
        v = (os.environ.get("MC_ZAMER_LISTEN_REFRESH_UI") or "0").strip().lower()
        if v not in ("1", "true", "yes", "on"):
            return
        self._zamer_listen_ui_timer.stop()
        self._zamer_listen_ui_timer.start(self._zamer_listen_ui_ms)

    def _refresh_orders_portal_hints(self):
        """Обновить только ячейки портала (кол. замер/доставка/монтаж), без полной перерисовки таблицы."""
        if self._main_table_mode != "orders":
            return
        self._patch_orders_table_portal_service_cells(self._current_orders)

    def _on_application_state_changed(self, state):
        """Опционально подтянуть портал при фокусе (по умолчанию выкл. — убирает мерцание). MC_ZAMER_APPACTIVE_REFRESH=1."""
        if state != Qt.ApplicationActive:
            return
        if (os.environ.get("MC_ZAMER_APPACTIVE_REFRESH") or "0").strip().lower() not in (
            "1",
            "true",
            "yes",
            "on",
        ):
            return
        QTimer.singleShot(800, self._refresh_orders_portal_hints)

    def _set_main_table_mode(self, mode):
        prev = self._main_table_mode
        self._main_table_mode = mode
        self._btn_view_orders.setChecked(mode == "orders")
        self._btn_view_quick.setChecked(mode == "quick")
        if mode == "orders":
            idx = 0
        else:
            idx = 1
        self._stack_orders.setCurrentIndex(idx)
        self._update_table_toolbar_enabled()
        if mode == "orders" and prev != "orders":
            QTimer.singleShot(40, self._sync_orders_tab_visible)
        t = getattr(self, "_orders_web_sync_timer", None)
        if t is not None:
            if mode == "orders":
                t.start()
            else:
                t.stop()

    def _sync_orders_tab_visible(self):
        if getattr(self, "_main_table_mode", "") != "orders":
            return
        self._apply_all_order_filters()

    def _table_selected_order(self):
        rows = self._orders_table.selectionModel().selectedRows()
        if not rows:
            return None
        r = rows[0].row()
        it = self._orders_table.item(r, 0)
        if not it:
            return None
        row_key = it.data(Qt.UserRole)
        for x in self._current_orders:
            if self._order_cache_key(x) == row_key:
                return x
        return None

    def _on_orders_table_selection(self):
        self._orders_table.viewport().update()
        self._update_table_toolbar_enabled()

    def _update_table_toolbar_enabled(self):
        sel = self._table_selected_order()
        en = sel is not None and getattr(self, "_main_table_mode", "orders") == "orders"
        for b in (self._btn_toolbar_model, self._btn_toolbar_status, self._btn_toolbar_add):
            b.setEnabled(en)

    def _on_toolbar_open_model(self):
        o = self._table_selected_order()
        if o:
            if self._is_sales_row(o):
                self._open_sales_order(o.get("id"))
            else:
                self._on_order_tile_click(o)

    def _on_toolbar_change_status(self):
        o = self._table_selected_order()
        if o:
            self._row_change_status(o)

    def _on_toolbar_add_product(self):
        o = self._table_selected_order()
        if o:
            self._row_add_product(o)

    def _row_open_model(self, od):
        self._on_order_tile_click(dict(od))

    def _order_measure_lock_reason(self, order_id):
        try:
            from db import models as db_models

            return db_models.get_order_measure_lock_reason(int(order_id))
        except Exception:
            return None

    def _row_change_status(self, od):
        if self._is_sales_row(od):
            self._open_sales_order(od.get("id"))
            return
        from db import models as db_models
        from db_main import ORDER_STATUS_RU

        lock_reason = self._order_measure_lock_reason(od.get("id"))
        if lock_reason:
            QMessageBox.information(self, "Статус заказа", lock_reason)
            return

        keys = list(ORDER_STATUS_RU.keys())
        labels = [ORDER_STATUS_RU[k] for k in keys]
        cur = od.get("status") or "draft"
        try:
            idx = keys.index(cur)
        except ValueError:
            idx = 0
        choice, ok = QInputDialog.getItem(
            self, "Статус заказа", "Выберите статус:", labels, idx, False
        )
        if not ok:
            return
        new_status = keys[labels.index(choice)]
        if new_status == "paid":
            try:
                from logic.blocks_bundle import (
                    PAYMENT_BANK,
                    PAYMENT_CARD,
                    PAYMENT_CASH,
                    PAYMENT_COD,
                    PAYMENT_QR,
                    PAYMENT_TYPE_LABELS_RU,
                    PAYMENT_UNPAID,
                    parse_bundle,
                    set_product_payment_type,
                )
            except Exception as e:
                QMessageBox.critical(self, "Оплата", str(e))
                return
            payment_keys = [
                PAYMENT_COD,
                PAYMENT_BANK,
                PAYMENT_QR,
                PAYMENT_CASH,
                PAYMENT_CARD,
                "partial",
                PAYMENT_UNPAID,
            ]
            payment_labels = [
                PAYMENT_TYPE_LABELS_RU.get(PAYMENT_COD, PAYMENT_COD),
                PAYMENT_TYPE_LABELS_RU.get(PAYMENT_BANK, PAYMENT_BANK),
                PAYMENT_TYPE_LABELS_RU.get(PAYMENT_QR, PAYMENT_QR),
                PAYMENT_TYPE_LABELS_RU.get(PAYMENT_CASH, PAYMENT_CASH),
                PAYMENT_TYPE_LABELS_RU.get(PAYMENT_CARD, PAYMENT_CARD),
                "частичная оплата",
                PAYMENT_TYPE_LABELS_RU.get(PAYMENT_UNPAID, PAYMENT_UNPAID),
            ]
            p_choice, p_ok = QInputDialog.getItem(
                self,
                "Способ оплаты",
                "Выберите способ оплаты для заказа:",
                payment_labels,
                0,
                False,
            )
            if not p_ok:
                return
            payment_key = payment_keys[payment_labels.index(p_choice)]
            # Любой конкретный способ => считаем, что все изделия оплачены этим способом.
            if payment_key not in ("partial", PAYMENT_UNPAID):
                row0 = db_models.get_order(int(od["id"])) or {}
                raw0 = row0.get("blocks_calc_json")
                _ver, products = parse_bundle(str(raw0) if raw0 else None)
                if products:
                    cur_raw = raw0
                    for pr in products:
                        pid = str(pr.get("id") or "")
                        if not pid:
                            continue
                        cur_raw = set_product_payment_type(cur_raw, pid, payment_key)
                    db_models.update_order_blocks_calc(int(od["id"]), cur_raw)
        try:
            db_models.set_order_status(int(od["id"]), new_status)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        self._light_resync_mirror_order_from_db(int(od["id"]), refresh_items=False)

    def add_product_to_order(self, order_id: int):
        """Тип изделия → нужный калькулятор; один заказ может содержать стекло, фасады и др."""
        from ui.new_order_modal import NewOrderModal
        from ui.order_product_flow import run_product_creation_flow

        m = NewOrderModal(self, dialog_title="Тип изделия")
        if m.exec_() != QDialog.Accepted:
            return
        t = m.chosen_type()
        if not t:
            return
        if t not in ("glass", "facades"):
            QMessageBox.information(
                self,
                "Тип изделия",
                "Этот тип пока в разработке — доступны «СТЕКЛО / ЗЕРКАЛО» и «ФАСАДЫ».",
            )
            return
        run_product_creation_flow(self, int(order_id), t)
        self._light_resync_mirror_order_from_db(int(order_id), refresh_items=True)

    def _row_add_product(self, od):
        if self._is_sales_row(od):
            self._open_sales_order(od.get("id"))
            return
        oid = od.get("id")
        if oid is None:
            return
        lock_reason = self._order_measure_lock_reason(oid)
        if lock_reason:
            QMessageBox.information(self, "Изделия", lock_reason)
            return
        self.add_product_to_order(int(oid))

    def _order_type_label_for_row(self, orow):
        return self._order_type_label_fast(orow)

    def _orders_sort_key(self, order_data, section: int):
        o = order_data
        ck = self._order_cache_key(o)
        m = (getattr(self, "_order_row_meta", None) or {}).get(ck) if ck else None
        iv = (getattr(self, "_order_filter_cache", None) or {}).get(ck) if ck else None
        if m is None:
            m = {}
        if iv is None:
            iv = {}
        if section == _OC_ID:
            return int(o.get("id") or 0)
        if section == _OC_CLIENT:
            return str(o.get("client_name") or "").lower()
        if section == _OC_MANAGER:
            return m.get("manager_lc") or self._order_manager_label(o).lower()
        if section == _OC_DATE:
            ca = o.get("created_at")
            if ca is None:
                return ""
            if hasattr(ca, "isoformat"):
                return ca.isoformat()
            return str(ca)
        if section == _OC_TYPE:
            return m.get("type_label") or self._order_type_label_for_row(o)
        if section == _OC_PRODUCTS:
            if iv.get("count") is not None:
                return int(iv.get("count") or 0)
            return int(self._glass_order_total_and_count(o)[1] or 0)
        if section == _OC_TOTAL:
            if iv.get("total") is not None:
                return int(iv.get("total"))
            tot, _cnt = self._glass_order_total_and_count(o)
            return int(tot if tot is not None else -1)
        if section == _OC_PAYMENT:
            sk = m.get("sort_payment")
            if sk is not None:
                return tuple(sk)
            try:
                from logic.blocks_bundle import bundle_payment_aggregate

                agg = bundle_payment_aggregate(self._glass_order_bundle_products(o))
                st = agg.get("state") or "unpaid"
                st_ord = {"unpaid": 0, "partial": 1, "full": 2}
                return (
                    st_ord.get(st, 0),
                    int(agg.get("paid_count") or 0),
                    int(agg.get("total_count") or 0),
                )
            except Exception:
                return (0, 0, 0)
        if section == _OC_SURCHARGE:
            sk = m.get("sort_surcharge")
            if sk is not None:
                return tuple(sk)
            try:
                from logic.blocks_bundle import bundle_surcharge_aggregate

                agg = bundle_surcharge_aggregate(self._glass_order_bundle_products(o))
                return (
                    int(agg.get("total_amount") or 0),
                    int(agg.get("positions_paid") or 0),
                    int(agg.get("positions_total") or 0),
                )
            except Exception:
                return (0, 0, 0)
        if section == _OC_STATUS:
            sk = m.get("sort_status")
            if sk is not None:
                return tuple(sk)
            try:
                from logic.blocks_bundle import bundle_payment_aggregate

                agg = bundle_payment_aggregate(self._glass_order_bundle_products(o))
                tot = max(1, int(agg.get("total_count") or 0))
                pc = int(agg.get("paid_count") or 0)
                frac = pc / float(tot) if agg.get("state") != "unpaid" else -1.0
            except Exception:
                frac = -1.0
            return (frac, str(o.get("status") or ""))
        if section in (_OC_MEASURE, _OC_DELIVERY, _OC_INSTALL):
            from ui.order_tile import order_service_sort_tuple

            ck = self._order_cache_key(o)
            pr = (getattr(self, "_order_portal_snapshot", None) or {}).get(ck) if ck else None
            pg = self._order_blocks_zamer_row(o)
            svc = ("measure", "delivery", "install")[
                section - _OC_MEASURE
            ]
            return order_service_sort_tuple(o, pr, svc, pg)
        if section == _OC_CUT:
            st = (o.get("status") or "").strip().lower()
            paidish = st in (
                "paid",
                "in_progress",
                "made",
                "checked_qr",
                "shipped",
                "completed",
            )
            if not paidish:
                return (0, -1.0)
            try:
                from logic.blocks_bundle import bundle_cut_scheme_counts
                from db import models as db_models

                x, y = bundle_cut_scheme_counts(self._glass_order_bundle_products(o), db_models)
            except Exception:
                x, y = 0, 0
            if y <= 0:
                return (1, 0.0)
            return (1, x / float(y))
        return ""

    def _swap_orders_table_row_meta(self, tbl, row_a: int, row_b: int):
        h_a, h_b = tbl.rowHeight(row_a), tbl.rowHeight(row_b)
        tbl.setRowHeight(row_a, h_b)
        tbl.setRowHeight(row_b, h_a)
        hid_a, hid_b = tbl.isRowHidden(row_a), tbl.isRowHidden(row_b)
        tbl.setRowHidden(row_a, hid_b)
        tbl.setRowHidden(row_b, hid_a)
        vh_a = tbl.takeVerticalHeaderItem(row_a)
        vh_b = tbl.takeVerticalHeaderItem(row_b)
        if vh_a is not None:
            tbl.setVerticalHeaderItem(row_b, vh_a)
        if vh_b is not None:
            tbl.setVerticalHeaderItem(row_a, vh_b)

    def _swap_orders_table_items_between(self, tbl, row_a: int, row_b: int):
        if row_a == row_b:
            return
        for c in _ORDER_TABLE_ITEM_COLS:
            it_a = tbl.takeItem(row_a, c)
            it_b = tbl.takeItem(row_b, c)
            if it_a is not None:
                tbl.setItem(row_b, c, it_a)
            if it_b is not None:
                tbl.setItem(row_a, c, it_b)
        self._swap_orders_table_row_meta(tbl, row_a, row_b)

    def _reorder_orders_table_fast(self, sorted_orders):
        """Перестановка только ячеек (все колонки — items, без виджетов)."""
        tbl = self._orders_table
        n = len(sorted_orders)
        if n <= 0:
            return
        if tbl.rowCount() != n:
            self._fill_orders_table(sorted_orders)
            return
        want = [self._order_cache_key(o) for o in sorted_orders]
        cur = []
        for r in range(n):
            it = tbl.item(r, _OC_ID)
            cur.append(it.data(Qt.UserRole) if it is not None else None)
        if cur == want:
            return
        pos = {cur[r]: r for r in range(n) if cur[r] is not None}
        tbl.setUpdatesEnabled(False)
        try:
            for i in range(n):
                if cur[i] == want[i]:
                    continue
                j = pos.get(want[i])
                if j is None or j == i:
                    raise RuntimeError("sort permutation mismatch")
                self._swap_orders_table_items_between(tbl, i, j)
                cur[i], cur[j] = cur[j], cur[i]
                pos[cur[i]] = i
                pos[cur[j]] = j
        finally:
            tbl.setUpdatesEnabled(True)
        tbl.viewport().update()

    def _orders_sort_key_fast(self, order_data, section: int):
        """Ключ сортировки из кэша (без тяжёлого parse bundle на каждое сравнение)."""
        o = order_data
        ck = self._order_cache_key(o)
        m = (getattr(self, "_order_row_meta", None) or {}).get(ck) if ck else None
        iv = (getattr(self, "_order_filter_cache", None) or {}).get(ck) if ck else None
        if m is None:
            m = {}
        if iv is None:
            iv = {}
        if section == _OC_ID:
            return (int(o.get("id") or 0),)
        if section == _OC_CLIENT:
            return (m.get("client_lc") or str(o.get("client_name") or "").lower(),)
        if section == _OC_MANAGER:
            return (m.get("manager_lc") or "",)
        if section == _OC_DATE:
            ca = o.get("created_at")
            if ca is None:
                return ("",)
            if hasattr(ca, "isoformat"):
                return (ca.isoformat(),)
            return (str(ca),)
        if section == _OC_TYPE:
            return (m.get("type_label") or "",)
        if section == _OC_PRODUCTS:
            return (int(iv.get("count") or 0),)
        if section == _OC_TOTAL:
            t = iv.get("total")
            return (int(t) if t is not None else -1,)
        if section == _OC_PAYMENT:
            sk = m.get("sort_payment")
            return tuple(sk) if sk is not None else (0, 0, 0)
        if section == _OC_SURCHARGE:
            sk = m.get("sort_surcharge")
            return tuple(sk) if sk is not None else (0, 0, 0)
        if section == _OC_STATUS:
            sk = m.get("sort_status")
            return tuple(sk) if sk is not None else (-1.0, "")
        if section in (_OC_MEASURE, _OC_DELIVERY, _OC_INSTALL):
            from ui.order_tile import order_service_sort_tuple

            svc = ("measure", "delivery", "install")[section - _OC_MEASURE]
            pr = (getattr(self, "_order_portal_snapshot", None) or {}).get(ck) if ck else None
            pg = self._order_blocks_zamer_row(o)
            return order_service_sort_tuple(o, pr, svc, pg)
        if section == _OC_CUT:
            cc = m.get("cut_cell") or ""
            return (0 if cc == "—" else 1, 0 if cc in ("", "нет") else 1)
        return ("",)

    def _orders_sort_key_safe(self, order_data, section: int):
        """Ключ сортировки без падений при сравнении разных типов."""
        try:
            k = self._orders_sort_key_fast(order_data, section)
        except Exception:
            try:
                k = self._orders_sort_key(order_data, section)
            except Exception:
                return ("",)
        if isinstance(k, (list, tuple)):
            return tuple("" if x is None else str(x) for x in k)
        return ("" if k is None else str(k),)

    def _on_orders_header_clicked(self, logical_index):
        if logical_index >= _OC_ACTIONS:
            return
        if getattr(self, "_table_sort_section", None) == logical_index:
            self._table_sort_ascending = not getattr(self, "_table_sort_ascending", True)
        else:
            self._table_sort_section = logical_index
            self._table_sort_ascending = True
        QTimer.singleShot(0, lambda idx=int(logical_index): self._apply_orders_column_sort(idx))

    def _apply_orders_column_sort(self, sec: int):
        """Сортировка после клика по заголовку (вне обработчика сигнала заголовка)."""
        if getattr(self, "_main_table_mode", "") != "orders":
            return
        tbl = self._orders_table
        asc = getattr(self, "_table_sort_ascending", True)

        sel_key = None
        sel_row = tbl.currentRow()
        if sel_row >= 0:
            it_sel = tbl.item(sel_row, _OC_ID)
            if it_sel is not None:
                sel_key = it_sel.data(Qt.UserRole)

        disp = list(getattr(self, "_table_display_order", None) or self._all_orders)
        sort_pairs = [
            (self._orders_sort_key_safe(o, sec), self._order_cache_key(o) or "", o) for o in disp
        ]
        sort_pairs.sort(key=lambda t: (t[0], t[1]), reverse=not asc)
        disp = [t[2] for t in sort_pairs]
        self._table_display_order = disp
        try:
            self._reorder_orders_table_fast(disp)
            self._apply_filter_visibility()
        except Exception:
            self._table_display_order = list(self._all_orders)
            self._fill_orders_table(self._table_display_order)
            self._apply_filter_visibility()
        if sel_key is not None:
            for row in range(tbl.rowCount()):
                it = tbl.item(row, _OC_ID)
                if it is not None and it.data(Qt.UserRole) == sel_key:
                    tbl.setCurrentCell(row, 0)
                    break

    def _refresh_orders_display(self, orders):
        self._fill_orders_table(orders)

    def _patch_orders_table_portal_service_cells(self, orders):
        """Подтянуть портал в фоне и обновить только колонки замер/доставка/монтаж."""
        from ui.order_tile import portal_zamer_id_from_order

        try:
            from calc import zamer_api_client as _zamer_api
        except Exception:
            _zamer_api = None
        tbl = self._orders_table
        disp = getattr(self, "_table_display_order", None) or orders
        if len(disp) != tbl.rowCount():
            self._fill_orders_table(disp)
            return
        for row, o in enumerate(disp):
            it = tbl.item(row, 0)
            if not it or it.data(Qt.UserRole) != self._order_cache_key(o):
                self._fill_orders_table(disp)
                return
        if not (_zamer_api and _zamer_api.api_enabled()):
            return
        zids = set()
        for o in disp:
            z = portal_zamer_id_from_order(o)
            if z is not None:
                zids.add(int(z))
        if not zids:
            return
        self._portal_fetch_generation += 1
        self._async_fetch_portal_by_zids(zids, self._portal_fetch_generation)

    def _glass_order_total_and_count_fast(self, order_row):
        """Сумма из JSON без SQL — для мгновенной отрисовки таблицы."""
        if self._is_sales_row(order_row):
            return int(order_row.get("total_rub") or 0), int(order_row.get("_sales_items_count") or 0)
        try:
            from logic.blocks_bundle import (
                bundle_grand_total_rub,
                bundle_order_units_total_qty,
                bundle_surcharge_aggregate,
            )
        except Exception:
            return None, 0
        products = self._glass_order_bundle_products(order_row)
        if not products:
            return None, 0
        base_total = int(bundle_grand_total_rub(products) or 0)
        surcharge_total = int((bundle_surcharge_aggregate(products) or {}).get("total_amount") or 0)
        return base_total + surcharge_total, bundle_order_units_total_qty(products)

    def _glass_order_total_and_count(
        self,
        order_row,
        *,
        _conn=None,
        _drill=None,
        _delivery=None,
        _facade_aux=None,
    ):
        if self._is_sales_row(order_row):
            return int(order_row.get("total_rub") or 0), int(order_row.get("_sales_items_count") or 0)
        ck = self._order_cache_key(order_row)
        if ck and _conn is None and _drill is None and _delivery is None and _facade_aux is None:
            iv = (getattr(self, "_order_filter_cache", None) or {}).get(ck)
            if iv is not None and iv.get("total") is not None:
                return iv.get("total"), int(iv.get("count") or 0)
        if _conn is not None or _drill is not None or _delivery is not None or _facade_aux is not None:
            try:
                from logic.blocks_bundle import (
                    bundle_grand_total_rub,
                    bundle_order_units_total_qty,
                    bundle_surcharge_aggregate,
                )
                from ui.glass_order_overview_dialog import live_bundle_order_base_total_rub
            except Exception:
                return self._glass_order_total_and_count_fast(order_row)
            products = self._glass_order_bundle_products(order_row)
            if not products:
                return None, 0
            base_total = live_bundle_order_base_total_rub(
                order_row,
                products,
                _conn=_conn,
                _drill=_drill,
                _delivery=_delivery,
                _facade_aux=_facade_aux,
            )
            if base_total is None:
                base_total = int(bundle_grand_total_rub(products) or 0)
            surcharge_total = int((bundle_surcharge_aggregate(products) or {}).get("total_amount") or 0)
            return base_total + surcharge_total, bundle_order_units_total_qty(products)
        return self._glass_order_total_and_count_fast(order_row)

    def _bundle_products_cached_by_raw(self, raw):
        key = str(raw) if raw is not None else ""
        if not key.strip():
            return []
        cache = getattr(self, "_bundle_products_cache", None)
        if cache is None:
            cache = {}
            self._bundle_products_cache = cache
        got = cache.get(key, None)
        if got is not None:
            return got
        try:
            from logic.blocks_bundle import parse_bundle
            _v, products = parse_bundle(key)
            out = list(products or [])
        except Exception:
            out = []
        cache[key] = out
        return out

    def _glass_order_bundle_products(self, order_row):
        raw = order_row.get("blocks_calc_json")
        return self._bundle_products_cached_by_raw(raw)

    def _order_payment_filter_text(self, o):
        if self._is_sales_row(o):
            from db import models as db_models
            st = str(o.get("status") or "").strip().lower()
            if st == "paid":
                return "полностью оплачено"
            if st == "calculated":
                return "не оплачен"
            return db_models.sales_status_to_ru(st).lower()
        try:
            from logic.blocks_bundle import bundle_payment_aggregate

            agg = bundle_payment_aggregate(self._glass_order_bundle_products(o))
            bits = []
            if agg.get("state") == "partial":
                bits.append("%s/%s" % (agg.get("paid_count", 0), agg.get("total_count", 0)))
            if agg.get("state") == "full":
                bits.append("полностью")
                bits.extend(agg.get("lines_ru") or [])
            if agg.get("state") == "unpaid":
                bits.append("не оплачен")
            return " ".join(bits)
        except Exception:
            return ""

    def _order_surcharge_filter_text(self, o):
        try:
            from logic.blocks_bundle import bundle_surcharge_aggregate

            agg = bundle_surcharge_aggregate(self._glass_order_bundle_products(o))
            amt = int(agg.get("total_amount") or 0)
            if amt <= 0:
                return ""
            paid_amt = int(agg.get("paid_amount") or 0)
            bits = ["%s ₽" % amt]
            bits.append("%s из %s" % (paid_amt, amt))
            pt = int(agg.get("positions_total") or 0)
            pp = int(agg.get("positions_paid") or 0)
            bits.append("%d/%d" % (pp, pt))
            bits.extend(agg.get("lines_ru") or [])
            return " ".join(bits)
        except Exception:
            return ""

    def _open_client_card_from_table(self, client_id: int, client_name: str):
        from ui.clients_dialog import ClientCardDialog

        ClientCardDialog(int(client_id), client_name or "—", self).exec_()

    def _order_row_meta_for(self, o):
        ck = self._order_cache_key(o)
        return (getattr(self, "_order_row_meta", None) or {}).get(ck) if ck else None

    def _orders_client_item(self, o):
        name = str(o.get("client_name") or "—").strip() or "—"
        disp = name if len(name) <= 48 else (name[:45] + "…")
        it = QTableWidgetItem(disp)
        cid = o.get("client_id")
        try:
            if cid is not None:
                it.setData(_CLIENT_ID_ITEM_ROLE, int(cid))
                it.setForeground(QBrush(QColor("#1565c0")))
                it.setToolTip("Клик — карточка клиента")
        except (TypeError, ValueError):
            pass
        return it

    def _orders_payment_item(self, o):
        m = self._order_row_meta_for(o) or {}
        st = str(m.get("payment_state") or "unpaid")
        if self._is_sales_row(o):
            st = "full" if str(o.get("status") or "").lower() == "paid" else "unpaid"
            txt = "Оплачено полностью" if st == "full" else "не оплачен"
        elif st == "full":
            txt = "Оплачено полностью"
            lines = (m.get("payment_lc") or "").replace("полностью", "").strip()
            if lines:
                txt = "Оплачено полностью · " + lines
        elif st == "partial":
            txt = "частично: %d/%d" % (
                int(m.get("payment_paid") or 0),
                max(1, int(m.get("payment_total") or 1)),
            )
        else:
            txt = (m.get("payment_lc") or "").strip() or self._order_payment_filter_text(o).lower() or "не оплачен"
        it = QTableWidgetItem(txt)
        it.setToolTip(txt)
        return it

    def _orders_surcharge_item(self, o):
        if self._is_sales_row(o):
            it = QTableWidgetItem("—")
            it.setToolTip("—")
            return it
        m = self._order_row_meta_for(o) or {}
        total = int(m.get("surcharge_total") or 0)
        paid = int(m.get("surcharge_paid") or 0)
        if total <= 0:
            it = QTableWidgetItem("—")
            it.setToolTip("—")
            return it
        txt = "внесено %s из %s ₽" % (paid, total)
        sur_lc = (m.get("surcharge_lc") or "").strip()
        if sur_lc:
            txt = sur_lc
        it = QTableWidgetItem(txt)
        it.setToolTip(txt)
        return it

    def _orders_status_item(self, o):
        m = self._order_row_meta_for(o) or {}
        if self._is_sales_row(o):
            from db import models as db_models

            txt = db_models.sales_status_to_ru(o.get("status"))
        else:
            txt = (m.get("status_display") or m.get("status_lc") or "").strip()
            if not txt:
                txt = order_status_to_ru(o.get("status"))
        it = QTableWidgetItem("")
        it.setToolTip(txt or "—")
        return it

    def _orders_cut_item(self, o):
        m = self._order_row_meta_for(o) or {}
        txt = str(m.get("cut_cell") or "—")
        it = QTableWidgetItem(txt)
        return it

    def _orders_actions_item(self, o):
        it = QTableWidgetItem("")
        it.setToolTip("Статус · добавить изделие" + (" · удалить" if self._user.get("role") == ROLE_ADMIN else ""))
        return it

    def _orders_table_row_is_selected(self, row: int) -> bool:
        sm = self._orders_table.selectionModel()
        if sm is None:
            return False
        for idx in sm.selectedIndexes():
            if idx.row() == row:
                return True
        return False

    def _order_for_table_row(self, row: int):
        tbl = self._orders_table
        it = tbl.item(row, _OC_ID)
        if it is None:
            return None
        ck = it.data(Qt.UserRole)
        if not ck:
            return None
        for o in getattr(self, "_table_display_order", None) or self._all_orders or []:
            if self._order_cache_key(o) == ck:
                return o
        return None

    def _show_order_actions_menu(self, row: int, column: int, order_row):
        tbl = self._orders_table
        it = tbl.item(row, column)
        if it is None:
            return
        od = dict(order_row)
        lock_reason = self._order_measure_lock_reason(od.get("id"))
        menu = QMenu(self)
        act_status = menu.addAction("Статус")
        act_status.triggered.connect(lambda _=False, d=od: self._row_change_status(d))
        if lock_reason:
            act_status.setEnabled(False)
            act_status.setToolTip(lock_reason)
        act_add = menu.addAction("Добавить изделие")
        act_add.triggered.connect(lambda _=False, d=od: self._row_add_product(d))
        if lock_reason:
            act_add.setEnabled(False)
            act_add.setToolTip(lock_reason)
        if self._user.get("role") == ROLE_ADMIN:
            act_del = menu.addAction("Удалить")
            act_del.setToolTip("Удерживайте ~1 с в старой версии; здесь — сразу с подтверждением")
            act_del.triggered.connect(lambda _=False, d=od: self._row_delete_order_flow(d))
        rect = tbl.visualItemRect(it)
        menu.exec_(tbl.viewport().mapToGlobal(rect.bottomLeft()))

    def _orders_client_cell(self, o):
        w = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(2, 0, 2, 0)
        hl.setSpacing(0)
        cid = o.get("client_id")
        name = str(o.get("client_name") or "—")
        disp = name if len(name) <= 42 else (name[:39] + "…")
        btn = QPushButton(disp)
        btn.setFlat(True)
        btn.setStyleSheet(
            "QPushButton { color: #1565c0; text-align: left; padding: 2px 4px; font-size: 11px; border: none; }"
            "QPushButton:hover { color: #0d47a1; text-decoration: underline; }"
            "QPushButton:disabled { color: #78909c; text-decoration: none; }"
        )
        btn.setCursor(Qt.PointingHandCursor)
        if cid:
            btn.clicked.connect(
                lambda _=False, c=int(cid), n=name: self._open_client_card_from_table(c, n)
            )
        else:
            btn.setEnabled(False)
            btn.setToolTip("Нет привязки к справочнику клиентов")
        hl.addWidget(btn, 1)
        return w

    def _orders_payment_cell(self, o):
        if self._is_sales_row(o):
            from db import models as db_models
            w = QWidget()
            vl = QVBoxLayout(w)
            vl.setContentsMargins(2, 2, 2, 2)
            vl.setSpacing(2)
            st = str(o.get("status") or "").strip().lower()
            if st == "calculated":
                txt = "не оплачен"
                css = "font-size:10px; color:#455a64; font-weight:600;"
            elif st == "paid":
                txt = "Оплачено полностью"
                css = "font-size:10px; color:#1b5e20; font-weight:700;"
            else:
                txt = db_models.sales_status_to_ru(st)
                css = "font-size:10px; color:#37474f; font-weight:600;"
            lab = QLabel(txt)
            lab.setWordWrap(True)
            lab.setStyleSheet(css)
            vl.addWidget(lab)
            return w
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(2, 2, 2, 2)
        vl.setSpacing(2)
        try:
            from logic.blocks_bundle import bundle_payment_aggregate

            agg = bundle_payment_aggregate(self._glass_order_bundle_products(o))
            st = agg.get("state") or "unpaid"
            tot = max(1, int(agg.get("total_count") or 0))
            paid = int(agg.get("paid_count") or 0)
            if st == "unpaid":
                lab = QLabel("не оплачен")
                lab.setStyleSheet("font-size:10px; color:#455a64; font-weight:600;")
                lab.setWordWrap(True)
                vl.addWidget(lab)
            elif st == "partial":
                lab = QLabel("частично: %d/%d" % (paid, tot))
                lab.setStyleSheet("font-size:10px; color:#4a148c; font-weight:600;")
                vl.addWidget(lab)
                bar = QProgressBar()
                bar.setRange(0, tot)
                bar.setValue(paid)
                bar.setTextVisible(True)
                bar.setFormat("оплачено %v из %m")
                bar.setStyleSheet(
                    "QProgressBar { border: 1px solid #b39ddb; border-radius: 3px; height: 14px; font-size: 9px; }"
                    "QProgressBar::chunk { background-color: #e1bee7; }"
                )
                vl.addWidget(bar)
            else:
                from ui.glass_order_overview_dialog import live_bundle_payment_lines_ru

                lines = live_bundle_payment_lines_ru(o, self._glass_order_bundle_products(o))
                if lines is None:
                    lines = agg.get("lines_ru") or []
                if lines:
                    txt = "Оплачено полностью · " + " · ".join(lines)
                else:
                    txt = "Оплачено полностью"
                lab = QLabel(txt)
                lab.setWordWrap(True)
                lab.setAlignment(Qt.AlignTop | Qt.AlignLeft)
                lab.setStyleSheet(
                    "font-size:10px; color:#000000; font-weight:600; background: transparent;"
                )
                w.setStyleSheet(
                    "QWidget#ordersPayFullCell { background-color: #c8e6c9; border-radius: 2px; }"
                )
                w.setObjectName("ordersPayFullCell")
                vl.setContentsMargins(4, 4, 4, 4)
                vl.addWidget(lab)
        except Exception:
            lab = QLabel("—")
            lab.setStyleSheet("font-size:10px; color:#78909c;")
            vl.addWidget(lab)
        return w

    def _orders_surcharge_cell(self, o):
        if self._is_sales_row(o):
            w = QWidget()
            bg = QFrame()
            bg.setStyleSheet("QFrame { background-color: #e0e0e0; border-radius: 2px; }")
            vl = QVBoxLayout(w)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.addWidget(bg)
            return w
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        bg = QFrame()
        bg_l = QVBoxLayout(bg)
        bg_l.setContentsMargins(0, 0, 0, 0)
        bg_l.setSpacing(0)
        vl.addWidget(bg)
        try:
            from logic.blocks_bundle import bundle_surcharge_aggregate

            agg = bundle_surcharge_aggregate(self._glass_order_bundle_products(o))
            total_amount = int(agg.get("total_amount") or 0)
            if total_amount <= 0:
                bg.setStyleSheet("QFrame { background-color: #e0e0e0; border-radius: 2px; }")
                return w
            paid_amount = min(total_amount, max(0, int(agg.get("paid_amount") or 0)))
            ratio = float(paid_amount) / float(max(1, total_amount))
            stop = max(0.0, min(1.0, ratio))
            bg.setStyleSheet(
                "QFrame { border-radius: 2px; "
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0 #c8e6c9, stop:%0.4f #c8e6c9, stop:%0.4f #eeeeee, stop:1 #eeeeee); }"
                % (stop, stop)
            )
            top = QLabel()
            methods_txt = ", ".join(agg.get("lines_ru") or [])
            if methods_txt:
                top.setText("внесено %s из %s ₽ (%s)" % (paid_amount, total_amount, methods_txt))
            else:
                top.setText("внесено %s из %s ₽" % (paid_amount, total_amount))
            top.setStyleSheet("font-size:10px; color:#263238; font-weight:700; background: transparent;")
            top.setWordWrap(True)
            top.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            top.setContentsMargins(4, 3, 4, 3)
            bg_l.addWidget(top)
        except Exception:
            lab = QLabel("—")
            lab.setStyleSheet("font-size:10px; color:#78909c;")
            bg_l.addWidget(lab)
        return w

    def _orders_status_cell(self, o):
        if self._is_sales_row(o):
            from db import models as db_models
            w = QWidget()
            vl = QVBoxLayout(w)
            vl.setContentsMargins(2, 2, 2, 2)
            vl.setSpacing(1)
            top = QLabel(db_models.sales_status_to_ru(o.get("status")))
            top.setStyleSheet("font-size:10px; font-weight:600; color:#212121;")
            top.setWordWrap(True)
            vl.addWidget(top)
            return w
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(2, 2, 2, 2)
        vl.setSpacing(1)

        status_color_key = {
            "draft": "status_draft",
            "paid": "status_paid",
            "in_progress": "status_in_progress",
            "made": "status_made",
            "checked_qr": "status_checked_qr",
            "shipped": "status_shipped",
            "completed": "status_completed",
        }

        products = self._glass_order_bundle_products(o)
        if not products:
            st_ru = order_status_to_ru(o.get("status"))
            top = QLabel(st_ru)
            top.setStyleSheet("font-size:10px; font-weight:600; color:#212121;")
            top.setWordWrap(True)
            vl.addWidget(top)
            return w

        from logic.blocks_bundle import bundle_status_unit_counts

        fallback = str(o.get("status") or "draft").strip() or "draft"
        oid = o.get("id")
        try:
            from db import models as _db_models_for_facade_ev
        except Exception:
            _db_models_for_facade_ev = None

        facade_production_events = None
        if oid and any(str((p or {}).get("kind") or "").strip() == "facade" for p in products):
            try:
                facade_production_events = (getattr(self, "_production_events_by_order", None) or {}).get(
                    int(oid)
                )
            except (TypeError, ValueError):
                facade_production_events = None
            if facade_production_events is None and (
                _db_models_for_facade_ev is not None
                and hasattr(_db_models_for_facade_ev, "list_production_events")
            ):
                try:
                    facade_production_events = _db_models_for_facade_ev.list_production_events(int(oid)) or []
                except Exception:
                    facade_production_events = None

        count_cb = None
        if (
            oid
            and _db_models_for_facade_ev is not None
            and hasattr(_db_models_for_facade_ev, "count_facade_instance_assembled_events")
        ):

            def _count_facade(oid_i, idx1, ev):
                return int(
                    _db_models_for_facade_ev.count_facade_instance_assembled_events(
                        oid_i, idx1, production_events=ev
                    )
                )

            count_cb = _count_facade

        counts, total = bundle_status_unit_counts(
            products,
            order_fallback_status=fallback,
            order_id=int(oid) if oid else None,
            facade_production_events=facade_production_events,
            count_facade_assembled=count_cb,
        )
        ordered = sorted(counts.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
        rows = [QHBoxLayout(), QHBoxLayout()]
        for rw in rows:
            rw.setContentsMargins(0, 0, 0, 0)
            rw.setSpacing(2)
        for i, (st, cnt) in enumerate(ordered):
            host = QWidget()
            hl = QHBoxLayout(host)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(2)
            dot = QLabel()
            dot.setFixedSize(8, 8)
            ckey = status_color_key.get(st)
            try:
                dot_col = color(ckey) if ckey else "#90a4ae"
            except Exception:
                dot_col = "#90a4ae"
            dot.setStyleSheet("background:%s; border:1px solid #546e7a; border-radius:2px;" % dot_col)
            txt = QLabel("%d/%d" % (int(cnt), max(1, int(total))))
            txt.setStyleSheet("font-size:9px; font-weight:700; color:#263238;")
            txt.setToolTip("%s: %d из %d" % (order_status_to_ru(st), int(cnt), int(total)))
            host.setToolTip("%s: %d из %d" % (order_status_to_ru(st), int(cnt), int(total)))
            hl.addWidget(dot)
            hl.addWidget(txt)
            rows[min(i // 3, 1)].addWidget(host)
        rows[0].addStretch(1)
        rows[1].addStretch(1)
        vl.addLayout(rows[0])
        vl.addLayout(rows[1])
        return w

    def _orders_cut_progress_cell(self, o):
        st = (o.get("status") or "").strip().lower()
        paidish = st in (
            "paid",
            "in_progress",
            "made",
            "checked_qr",
            "shipped",
            "completed",
        )
        w = QWidget()
        vl = QVBoxLayout(w)
        vl.setContentsMargins(2, 0, 2, 2)
        vl.setSpacing(0)
        cap = QLabel()
        cap.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        cap.setStyleSheet(
            "font-size: 13px; font-weight: 700; color: #1b5e20; padding: 0 2px 2px 2px; "
            "background: transparent;"
        )
        bar = QProgressBar()
        bar.setTextVisible(False)
        bar.setFixedHeight(12)
        _cut_bar_style_grey = (
            "QProgressBar { border: 2px solid #b0bec5; border-radius: 3px; height: 12px; }"
            "QProgressBar::chunk { background-color: #eceff1; }"
        )
        _cut_bar_style_green = (
            "QProgressBar { border: 2px solid #2e7d32; border-radius: 3px; height: 12px; }"
            "QProgressBar::chunk { background-color: #a5d6a7; }"
        )
        if not paidish:
            cap.setText("—")
            cap.setStyleSheet(
                "font-size: 12px; font-weight: 600; color: #546e7a; padding: 0 2px 2px 2px; background: transparent;"
            )
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setStyleSheet(_cut_bar_style_grey)
            vl.addWidget(cap)
            vl.addWidget(bar)
            return w
        try:
            from logic.blocks_bundle import bundle_cut_scheme_counts
            from db import models as db_models

            x, y = bundle_cut_scheme_counts(self._glass_order_bundle_products(o), db_models)
        except Exception:
            x, y = 0, 0
        if y <= 0:
            cap.setText("н/д")
            cap.setStyleSheet(
                "font-size: 12px; font-weight: 600; color: #546e7a; padding: 0 2px 2px 2px; background: transparent;"
            )
            bar.setRange(0, 1)
            bar.setValue(0)
            bar.setStyleSheet(_cut_bar_style_grey)
        else:
            cap.setText("%d из %d" % (x, y))
            bar.setRange(0, y)
            bar.setValue(x)
            bar.setStyleSheet(_cut_bar_style_green)
        vl.addWidget(cap)
        vl.addWidget(bar)
        return w

    def _fill_orders_table_row_at(self, tbl, row, o, prev_snap, api_on, _zamer_api, zids_for_fetch):
        from ui.order_tile import portal_zamer_id_from_order

        cache_key = self._order_cache_key(o)
        iv = (getattr(self, "_order_filter_cache", None) or {}).get(cache_key) if cache_key else None
        if iv is None:
            iv = {}
        oid = o.get("id")
        tbl.setItem(row, _OC_ID, QTableWidgetItem(str(oid or "")))
        tbl.item(row, _OC_ID).setData(Qt.UserRole, cache_key)
        tbl.setItem(row, _OC_CLIENT, self._orders_client_item(o))
        tbl.setItem(row, _OC_MANAGER, QTableWidgetItem(self._order_manager_label(o)))
        ca = o.get("created_at")
        ds = ca.strftime("%d.%m.%Y %H:%M") if hasattr(ca, "strftime") else str(ca or "")[:16]
        tbl.setItem(row, _OC_DATE, QTableWidgetItem(ds))
        lbl_type = self._order_type_label_for_row(o)
        tbl.setItem(row, _OC_TYPE, QTableWidgetItem(lbl_type))
        cnt = iv.get("count")
        tot = iv.get("total")
        if cnt is None or tot is None:
            tot, cnt = self._glass_order_total_and_count(o)
        show_num = lbl_type != "—" and cnt is not None
        tbl.setItem(
            row,
            _OC_PRODUCTS,
            QTableWidgetItem(str(cnt) if show_num and cnt else ("—" if lbl_type == "—" else "0")),
        )
        tbl.setItem(row, _OC_TOTAL, QTableWidgetItem(str(tot if tot is not None else "") if show_num else ""))
        tbl.setItem(row, _OC_PAYMENT, self._orders_payment_item(o))
        tbl.setItem(row, _OC_SURCHARGE, self._orders_surcharge_item(o))
        tbl.setItem(row, _OC_STATUS, self._orders_status_item(o))
        tbl.setItem(row, _OC_CUT, self._orders_cut_item(o))

        pr_keep = prev_snap.get(cache_key) if cache_key is not None else None
        if cache_key is not None:
            self._order_portal_snapshot[cache_key] = pr_keep
        tbl.setItem(row, _OC_MEASURE, self._orders_service_item(o, pr_keep, "measure"))
        tbl.setItem(row, _OC_DELIVERY, self._orders_service_item(o, pr_keep, "delivery"))
        tbl.setItem(row, _OC_INSTALL, self._orders_service_item(o, pr_keep, "install"))
        tbl.setItem(row, _OC_ACTIONS, self._orders_actions_item(o))
        vhi = QTableWidgetItem("")
        vhi.setToolTip("Потяните границу между строками, чтобы изменить высоту")
        tbl.setVerticalHeaderItem(row, vhi)
        vh = tbl.verticalHeader()
        rh = tbl.rowHeight(row)
        if rh < vh.minimumSectionSize():
            tbl.setRowHeight(row, vh.defaultSectionSize())
        if api_on and _zamer_api and _zamer_api.api_enabled():
            zid = portal_zamer_id_from_order(o)
            if zid is not None:
                zids_for_fetch.add(int(zid))

    def _rewrite_orders_table_row(self, row, o):
        from ui.order_tile import portal_zamer_id_from_order

        try:
            from calc import zamer_api_client as _zamer_api
        except Exception:
            _zamer_api = None
        api_on = bool(_zamer_api and _zamer_api.api_enabled())
        tbl = self._orders_table
        prev_snap = dict(self._order_portal_snapshot or {})
        zids_for_fetch = set()
        self._fill_orders_table_row_at(tbl, row, o, prev_snap, api_on, _zamer_api, zids_for_fetch)
        if zids_for_fetch and api_on and _zamer_api:
            self._portal_fetch_generation += 1
            self._async_fetch_portal_by_zids(zids_for_fetch, self._portal_fetch_generation)

    def _fill_orders_table(self, orders):
        try:
            from calc import zamer_api_client as _zamer_api
        except Exception:
            _zamer_api = None
        api_on = bool(_zamer_api and _zamer_api.api_enabled())

        tbl = self._orders_table
        keep_keys = {self._order_cache_key(o) for o in orders if self._order_cache_key(o)}
        if getattr(self, "_skip_portal_snap_reuse", False):
            prev_snap = {}
            self._order_portal_snapshot = {}
        else:
            old_snap = dict(self._order_portal_snapshot or {})
            prev_snap = {k: v for k, v in old_snap.items() if k in keep_keys}
            self._order_portal_snapshot = dict(prev_snap)
        tbl.setUpdatesEnabled(False)
        try:
            tbl.setRowCount(len(orders))
            zids_for_fetch = set()
            for row, o in enumerate(orders):
                self._fill_orders_table_row_at(tbl, row, o, prev_snap, api_on, _zamer_api, zids_for_fetch)
        finally:
            tbl.setUpdatesEnabled(True)
        if zids_for_fetch and api_on and _zamer_api:
            self._portal_fetch_generation += 1
            self._async_fetch_portal_by_zids(zids_for_fetch, self._portal_fetch_generation)
        # Закрываем заставку сразу после отрисовки таблицы из БД. Опрос портала идёт в фоне (иначе при зависшем
        # HTTP колбэк не вызывался бы и splash оставался бы навсегда).
        if getattr(self, "_startup_splash", None) and not getattr(self, "_startup_splash_dismissed", False):
            QTimer.singleShot(0, self._dismiss_startup_splash)
        QTimer.singleShot(0, lambda: self._apply_orders_table_proportional_layout(use_defaults=False))

    def _order_row_actions_widget(self, order_row):
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)
        hl = QHBoxLayout(w)
        hl.setContentsMargins(2, 1, 2, 1)
        hl.setSpacing(4)
        hl.setAlignment(Qt.AlignVCenter)
        od = dict(order_row)
        lock_reason = self._order_measure_lock_reason(od.get("id"))
        btn_style = (
            "font-size:9px; padding:1px 8px; min-height:20px; max-height:36px;"
        )
        bs = QPushButton("Статус")
        bs.setStyleSheet(btn_style)
        bs.clicked.connect(lambda checked=False, d=od: self._row_change_status(d))
        if lock_reason:
            bs.setEnabled(False)
            bs.setToolTip(lock_reason)
        bp = QPushButton("+")
        bp.setToolTip("Добавить изделие")
        bp.setStyleSheet(btn_style)
        bp.clicked.connect(lambda checked=False, d=od: self._row_add_product(d))
        if lock_reason:
            bp.setEnabled(False)
            bp.setToolTip(lock_reason)
        bi = QPushButton("Инфо")
        bi.setStyleSheet(btn_style)
        bi.clicked.connect(lambda checked=False, d=od: self._row_info(d))
        for bx in (bs, bp, bi):
            bx.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.MinimumExpanding)
        hl.addWidget(bs)
        hl.addWidget(bp)
        hl.addWidget(bi)
        if self._user.get("role") == ROLE_ADMIN:
            bd = QPushButton("Удалить")
            bd.setToolTip("Удерживайте ~1 с — откроется подтверждение с ползунком")
            bd.setStyleSheet(btn_style)
            tdel = QTimer(bd)
            tdel.setSingleShot(True)
            tdel.setInterval(1000)
            tdel.timeout.connect(lambda d=od: self._row_delete_order_flow(d))
            bd.pressed.connect(tdel.start)
            bd.released.connect(tdel.stop)
            bd.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.MinimumExpanding)
            hl.addWidget(bd)
        hl.addStretch()
        return w

    def _row_delete_order_flow(self, od):
        oid = od.get("id")
        if oid is None:
            return
        hint = str(od.get("client_name") or "")
        if _DeleteOrderSliderDialog(int(oid), hint, self).exec_() != QDialog.Accepted:
            return
        r = QMessageBox.question(
            self,
            "Удаление заказа",
            (
                "Удалить продажу №%s безвозвратно?\nСвязанные позиции будут удалены из базы."
                if self._is_sales_row(od)
                else "Удалить заказ №%s безвозвратно?\nСвязанные позиции и раскрой будут удалены из базы."
            )
            % oid,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if r != QMessageBox.Yes:
            return
        try:
            from db import models as db_models

            if self._is_sales_row(od):
                db_models.delete_sales_order(int(oid))
            else:
                n = db_models.delete_order(int(oid))
                if not n:
                    QMessageBox.warning(self, "Удаление", "Заказ не найден.")
                    return
                try:
                    rep = db_models.get_last_delete_order_report(int(oid)) or {}
                except Exception:
                    rep = {}
                if rep.get("converted_to_remnants"):
                    txt = (
                        "Заказ удалён.\n"
                        "Клиентские куски переведены в остатки: %s шт.\n"
                        "PDF этикеток: %s"
                    ) % (
                        int(rep.get("created_remnants_count") or 0),
                        str(rep.get("labels_pdf_path") or "—"),
                    )
                    box = QMessageBox(self)
                    box.setWindowTitle("Удаление заказа")
                    box.setText(txt)
                    btn_open = box.addButton("Открыть PDF", QMessageBox.ActionRole)
                    btn_save = box.addButton("Скачать PDF как…", QMessageBox.ActionRole)
                    box.addButton(QMessageBox.Ok)
                    box.exec_()
                    picked = box.clickedButton()
                    pdf_path = str(rep.get("labels_pdf_path") or "").strip()
                    if picked == btn_open:
                        try:
                            os.startfile(pdf_path)  # type: ignore[attr-defined]
                        except Exception:
                            try:
                                subprocess.Popen([pdf_path], shell=True)
                            except Exception:
                                pass
                    elif picked == btn_save:
                        target, _ = QFileDialog.getSaveFileName(
                            self,
                            "Скачать PDF",
                            os.path.basename(pdf_path) or "deleted_order_labels.pdf",
                            "PDF files (*.pdf)",
                        )
                        if target and pdf_path and os.path.isfile(pdf_path):
                            try:
                                shutil.copyfile(pdf_path, target)
                            except Exception as ex:
                                QMessageBox.warning(self, "Скачивание", "Не удалось сохранить PDF: %s" % ex)
                elif rep.get("restored_as_is"):
                    QMessageBox.information(
                        self,
                        "Удаление заказа",
                        "Заказ удалён. Материал возвращён как был (без подтверждённого раскроя).",
                    )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        if self._is_sales_row(od):
            self._light_remove_sales_order_from_memory(int(oid))
        else:
            self._light_remove_mirror_order_from_memory(int(oid))

    def _order_cut_status_cell(self, o):
        if self._is_sales_row(o):
            return "—"
        st = (o.get("status") or "").strip().lower()
        if st not in (
            "paid",
            "in_progress",
            "made",
            "checked_qr",
            "shipped",
            "completed",
        ):
            return "—"
        oid = o.get("id")
        return "да" if oid in self._order_cut_ids else "нет"

    def _row_info(self, od):
        if self._is_sales_row(od):
            self._open_sales_order(od.get("id"))
            return
        from ui.order_info_dialog import OrderInfoDialog
        d = OrderInfoDialog(int(od.get("id")), self)
        d.exec_()

    def _on_orders_table_cell_clicked(self, row, column):
        if column == _OC_CLIENT:
            it = self._orders_table.item(row, column)
            if it is None:
                return
            try:
                cid = it.data(_CLIENT_ID_ITEM_ROLE)
            except Exception:
                cid = None
            if cid is None:
                return
            self._open_client_card_from_table(int(cid), it.text())
            return
        if column == _OC_ACTIONS:
            return

    def _on_orders_table_activated(self, index):
        if index.column() >= _OC_ACTIONS:
            return
        row = index.row()
        it = self._orders_table.item(row, 0)
        if not it:
            return
        row_key = it.data(Qt.UserRole)
        for o in self._current_orders:
            if self._order_cache_key(o) == row_key:
                if self._is_sales_row(o):
                    self._open_sales_order(o.get("id"))
                else:
                    self._on_order_tile_click(o)
                break

    def _open_sales_order(self, sales_id):
        if not sales_id:
            return
        from ui.sales_order_dialog import SalesOrderDialog
        d = SalesOrderDialog(self, sales_order_id=int(sales_id))
        d.exec_()
        self._schedule_table_sync(lambda sid=int(sales_id): self._light_resync_sales_order_from_db(sid))

    def _fill_quick_table(self, rows):
        tbl = self._quick_table
        tbl.setRowCount(len(rows))
        for i, r in enumerate(rows):
            qid = r.get("id")
            tbl.setItem(i, 0, QTableWidgetItem(str(qid or "")))
            tbl.item(i, 0).setData(Qt.UserRole, qid)
            rc = str(r.get("category") or "").strip().lower()
            if rc == "glass":
                cat = "Стекло / зеркало"
            elif rc == "facade":
                cat = "Фасады"
            elif rc == "sales":
                cat = "Продажа"
            else:
                cat = rc or "—"
            tbl.setItem(i, 1, QTableWidgetItem(cat))
            tbl.setItem(i, 2, QTableWidgetItem(str(r.get("client_name") or "—")))
            tbl.setItem(i, 3, QTableWidgetItem(str(r.get("lead_source") or "")))
            tbl.setItem(i, 4, QTableWidgetItem("+%s%%" % int(r.get("markup_percent") or 0)))
            tbl.setItem(i, 5, QTableWidgetItem(str(r.get("status") or "")))
            dt = r.get("estimate_at")
            dts = dt.strftime("%d.%m.%Y %H:%M") if hasattr(dt, "strftime") else str(dt or "")
            tbl.setItem(i, 6, QTableWidgetItem(dts))
            w = QWidget()
            hl = QHBoxLayout(w)
            hl.setContentsMargins(2, 2, 2, 2)
            bo = QPushButton("В работу")
            bo.setStyleSheet("font-size:9px; padding:3px 8px; max-height:26px;")
            bo.clicked.connect(lambda checked=False, qid=qid: self._quick_to_work(qid))
            hl.addWidget(bo)
            hl.addStretch()
            tbl.setCellWidget(i, 7, w)

    def _quick_to_work(self, quick_id):
        if not quick_id:
            return
        from db import models as db_models
        from ui.quick_estimate_client_pick_dialog import (
            open_quick_estimate_transfer_client_dialog,
        )
        qrow = db_models.get_quick_estimate(int(quick_id))
        if not qrow:
            return
        meta = open_quick_estimate_transfer_client_dialog(self, dict(qrow))
        if not meta:
            return
        parts = []
        ph = (meta.get("phone") or "").strip()
        ex = (meta.get("extra_contact") or "").strip()
        if ph:
            parts.append(ph)
        if ex:
            parts.append(ex)
        db_models.update_quick_estimate_client_meta(
            int(quick_id),
            client_id=meta.get("client_id"),
            quick_client_id=meta.get("quick_client_id"),
            client_name=meta.get("client_name"),
            lead_source=meta.get("lead_source"),
            contact_info=(" · ".join(parts))[:255],
            markup_percent=int(meta.get("markup_percent") or 0),
        )
        qrow = db_models.get_quick_estimate(int(quick_id)) or qrow
        cat = str((qrow or {}).get("category") or "").strip().lower()
        try:
            rid = db_models.transfer_quick_estimate_to_order(int(quick_id))
        except Exception as e:
            QMessageBox.warning(self, "Быстрый просчет", str(e))
            return
        if cat == "sales" and rid:
            self._open_sales_order(int(rid))
            self._schedule_table_sync(lambda sid=int(rid): self._light_append_sales_order_from_db(sid))
        else:
            try:
                oid_new = int(rid) if rid is not None else None
            except (TypeError, ValueError):
                oid_new = None
            if oid_new is not None:
                self._light_append_mirror_order_from_db(oid_new)
                self._reload_quick_estimates_rows_only()
            else:
                self._load_orders()

    def _on_quick_table_activated(self, index):
        row = index.row()
        if row < 0:
            return
        it = self._quick_table.item(row, 0)
        if not it:
            return
        qid = it.data(Qt.UserRole)
        if not qid:
            return
        from db import models as db_models
        from ui.glass_order_overview_dialog import GlassOrderOverviewDialog

        q = db_models.get_quick_estimate(int(qid)) or {}
        cat = str(q.get("category") or "").strip().lower()
        if cat == "sales":
            payload = q.get("payload")
            if payload is None:
                try:
                    import json as _json

                    raw = q.get("payload_json")
                    payload = _json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
                except Exception:
                    payload = {}
            sid = (payload or {}).get("sales_order_id")
            try:
                sid = int(sid) if sid is not None else None
            except (TypeError, ValueError):
                sid = None
            if sid:
                self._open_sales_order(sid)
                return
            QMessageBox.information(
                self,
                "Быстрый просчёт",
                "№%s\nКатегория: %s\nКлиент: %s\nКонтакт: %s\nСтатус: %s"
                % (
                    q.get("id") or "—",
                    q.get("category") or "—",
                    q.get("client_name") or "—",
                    q.get("contact_info") or "—",
                    q.get("status") or "—",
                ),
            )
            return

        tid = q.get("transferred_order_id")
        try:
            tid = int(tid) if tid is not None else None
        except (TypeError, ValueError):
            tid = None
        if tid:
            fresh = db_models.get_order(tid)
            if fresh and self._order_has_bundle_products(fresh):
                GlassOrderOverviewDialog(fresh, self, summary_only=True).exec_()
                return

        import json as _json
        from logic.blocks_bundle import parse_bundle

        raw_blocks = q.get("payload_json")
        if isinstance(raw_blocks, dict):
            blocks_text = _json.dumps(raw_blocks, ensure_ascii=False)
        elif isinstance(raw_blocks, str) and raw_blocks.strip():
            blocks_text = raw_blocks.strip()
        else:
            blocks_text = None
        if not blocks_text:
            QMessageBox.information(
                self,
                "Быстрый просчёт",
                "Нет сохранённого расчёта для просмотра сводки.",
            )
            return
        try:
            _ver, prods = parse_bundle(blocks_text)
        except Exception:
            prods = []
        if not prods:
            QMessageBox.information(
                self,
                "Быстрый просчёт",
                "В просчёте нет изделий — нечего показать в сводке.",
            )
            return

        synthetic = {
            "id": None,
            "client_name": q.get("client_name"),
            "client_id": q.get("client_id"),
            "quick_client_id": q.get("quick_client_id"),
            "status": str(q.get("status") or "draft"),
            "blocks_calc_json": blocks_text,
        }
        GlassOrderOverviewDialog(synthetic, self, summary_only=True).exec_()

    def _load_orders(self):
        """Полная перезагрузка списка заказов из БД. Кэш bundle — только точечная инвалидация по изменённым JSON."""
        prev_calc = {}
        for o in getattr(self, "_all_orders", []) or []:
            if self._is_sales_row(o):
                continue
            oid = o.get("id")
            if oid is None:
                continue
            try:
                prev_calc[int(oid)] = str(o.get("blocks_calc_json") or "")
            except (TypeError, ValueError):
                continue
        hide_draft_sales = set()
        try:
            from db import models as db_models
            orders = db_models.get_orders_all()
            sales = db_models.list_sales_orders()
            try:
                hide_draft_sales = db_models.sales_order_ids_in_draft_quick_estimates()
            except Exception:
                hide_draft_sales = set()
            sales_items = {}
            if sales and hasattr(db_models, "list_sales_items_counts_bulk"):
                sales_items = db_models.list_sales_items_counts_bulk(
                    [int(x.get("id")) for x in (sales or []) if x.get("id") is not None]
                ) or {}
            quick = db_models.list_quick_estimates(status="draft")
            clients = db_models.get_all_clients() or []
        except Exception:
            db_models = None
            orders = []
            sales = []
            sales_items = {}
            quick = []
            clients = []
            hide_draft_sales = set()
        merged_orders = list(orders or [])
        for s in sales or []:
            sid = s.get("id")
            if sid is None:
                continue
            try:
                if int(sid) in hide_draft_sales:
                    continue
            except (TypeError, ValueError):
                pass
            cnts = sales_items.get(int(sid)) or {}
            row = dict(s)
            row["__row_kind"] = "sales"
            row["_sales_items_count"] = int(cnts.get("items_count") or 0)
            row["_sales_delivery_items_count"] = int(cnts.get("delivery_count") or 0)
            merged_orders.append(row)
        merged_orders.sort(
            key=lambda r: (
                (r.get("created_at").isoformat() if hasattr(r.get("created_at"), "isoformat") else str(r.get("created_at") or "")),
                int(r.get("id") or 0),
            ),
            reverse=True,
        )
        new_calc = {}
        for o in orders or []:
            oid = o.get("id")
            if oid is None:
                continue
            try:
                new_calc[int(oid)] = str(o.get("blocks_calc_json") or "")
            except (TypeError, ValueError):
                continue
        if getattr(self, "_bundle_products_cache", None) is None:
            self._bundle_products_cache = {}
        for oid, old_raw in prev_calc.items():
            if oid not in new_calc or new_calc.get(oid) != old_raw:
                self._invalidate_bundle_products_for_raw(old_raw)
        self._all_orders = merged_orders
        self._all_quick = quick
        self._all_clients_for_search = list(clients or [])
        self._clients_by_id = {}
        self._clients_by_name = {}
        for c in self._all_clients_for_search:
            try:
                cid = int(c.get("id")) if c.get("id") is not None else None
            except (TypeError, ValueError):
                cid = None
            if cid is not None:
                self._clients_by_id[cid] = c
            nm = str(c.get("name") or "").strip().lower()
            if nm and nm not in self._clients_by_name:
                self._clients_by_name[nm] = c
        self._rebuild_order_global_search_completers()
        self._orders_items = {}
        oids = [o.get("id") for o in orders if o.get("id")]
        if db_models:
            try:
                self._order_cut_ids = frozenset(
                    db_models.get_order_ids_with_cut_results(oids)
                )
            except Exception:
                self._order_cut_ids = frozenset()
            try:
                self._blocks_zamer_by_order = (
                    db_models.get_blocks_zamer_rows_by_mirror_order_ids(oids)
                )
            except Exception:
                self._blocks_zamer_by_order = {}
            bulk = None
            try:
                bulk = db_models.get_order_items_bulk(oids)
            except Exception:
                bulk = None
            for o in orders:
                oid = o.get("id")
                if not oid:
                    continue
                if bulk is not None:
                    self._orders_items[oid] = list(bulk.get(oid) or [])
                else:
                    try:
                        self._orders_items[oid] = db_models.get_order_items(oid) or []
                    except Exception:
                        self._orders_items[oid] = []
        else:
            self._order_cut_ids = frozenset()
            self._blocks_zamer_by_order = {}
        try:
            self._production_events_by_order = (
                db_models.list_production_events_for_orders(oids) if db_models and oids else {}
            )
        except Exception:
            self._production_events_by_order = {}
        self._order_filter_cache = {}
        self._rebuild_order_row_meta_light()
        if getattr(self, "_main_table_mode", "") == "orders":
            self._table_display_order = self._orders_table_display_sequence()
            self._fill_orders_table(self._table_display_order)
            self._apply_filter_visibility()
        else:
            self._current_orders = list(self._all_orders)
        self._apply_quick_filters()
        self._schedule_rebuild_order_filter_cache_async()
        if getattr(self, "_orders_web_sync_timer", None) is not None:
            self._orders_web_sync_timer.start()

    @staticmethod
    def _order_web_sync_fingerprint(o: dict) -> tuple:
        import json

        raw = o.get("blocks_calc_json")
        if isinstance(raw, dict):
            body = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
        else:
            body = str(raw or "")
        return (str(o.get("status") or "").strip(), body)

    def _poll_orders_web_bundle_changes(self) -> None:
        """Подхват изменений bundle/status из WEB_SERVICE (production_glass_status и т.д.)."""
        if getattr(self, "_main_table_mode", "") != "orders":
            return
        tbl = getattr(self, "_orders_table", None)
        if tbl is None or tbl.rowCount() < 1:
            return
        from db import models as db_models

        row_by_oid: dict = {}
        for row in range(tbl.rowCount()):
            it = tbl.item(row, 0)
            if not it:
                continue
            ck = it.data(Qt.UserRole)
            if not isinstance(ck, str) or not ck.startswith("order:"):
                continue
            try:
                oid = int(ck.split(":", 1)[1])
            except (ValueError, IndexError):
                continue
            row_by_oid[oid] = row
        if not row_by_oid:
            return
        try:
            snap = db_models.get_mirror_orders_blocks_snapshot_bulk(list(row_by_oid.keys()))
        except Exception:
            return
        for oid, row in row_by_oid.items():
            chunk = snap.get(int(oid))
            if not chunk:
                continue
            o = self._mirror_order_dict_in_all_orders(oid)
            if not o:
                continue
            old_fp = self._order_web_sync_fingerprint(o)
            old_raw = o.get("blocks_calc_json")
            o["status"] = chunk.get("status")
            o["blocks_calc_json"] = chunk.get("blocks_calc_json")
            if self._order_web_sync_fingerprint(o) == old_fp:
                continue
            self._invalidate_bundle_products_for_raw(old_raw)
            self._patch_order_filter_cache_for_orders([o])
            self._patch_order_row_meta_for_orders([o])
            self._sync_orders_table_after_data_change(mirror_touch_id=int(oid))

    def _mirror_order_dict_in_all_orders(self, order_id):
        oid = int(order_id)
        for o in self._all_orders or []:
            if self._is_sales_row(o):
                continue
            try:
                if int(o.get("id") or 0) == oid:
                    return o
            except (TypeError, ValueError):
                continue
        return None

    def _invalidate_bundle_products_for_raw(self, raw):
        cache = getattr(self, "_bundle_products_cache", None)
        if not cache:
            return
        key = str(raw) if raw is not None else ""
        if key in cache:
            del cache[key]

    def _sort_mirror_and_sales_orders_inplace(self):
        self._all_orders.sort(
            key=lambda r: (
                (
                    r.get("created_at").isoformat()
                    if hasattr(r.get("created_at"), "isoformat")
                    else str(r.get("created_at") or "")
                ),
                int(r.get("id") or 0),
            ),
            reverse=True,
        )

    def _patch_order_filter_cache_for_orders(self, order_rows):
        """Пересчитать кэш фильтров только для указанных строк (точечно, без полного списка)."""
        if getattr(self, "_order_filter_cache", None) is None:
            self._order_filter_cache = {}
        patch = _build_order_filter_cache_entries(
            list(order_rows or []),
            getattr(self, "_blocks_zamer_by_order", None) or {},
        )
        self._order_filter_cache.update(patch)

    def _patch_order_row_meta_for_orders(self, order_rows):
        if getattr(self, "_order_row_meta", None) is None:
            self._order_row_meta = {}
        light = _build_order_row_meta_light(
            list(order_rows or []),
            getattr(self, "_clients_by_id", None) or {},
            getattr(self, "_clients_by_name", None) or {},
            getattr(self, "_order_cut_ids", None) or frozenset(),
        )
        self._order_row_meta.update(light)
        heavy = _build_order_row_meta_heavy(
            list(order_rows or []),
            getattr(self, "_production_events_by_order", None) or {},
        )
        for ck, p in heavy.items():
            if ck in self._order_row_meta:
                self._order_row_meta[ck].update(p)
            else:
                self._order_row_meta[ck] = dict(p)

    def _light_resync_mirror_order_from_db(self, order_id, *, refresh_items=False):
        """После смены статуса / блоков / позиций — один SELECT по заказу + точечный кэш фильтров, без полного _load_orders."""
        from db import models as db_models

        oid = int(order_id)
        old = self._mirror_order_dict_in_all_orders(oid)
        if old is None:
            self._load_orders()
            return
        raw_before = old.get("blocks_calc_json")
        row = db_models.get_mirror_order_list_row(oid)
        if not row:
            self._load_orders()
            return
        self._invalidate_bundle_products_for_raw(raw_before)
        old.clear()
        old.update(dict(row))
        if refresh_items:
            try:
                bulk = db_models.get_order_items_bulk([oid]) or {}
                self._orders_items[oid] = list(bulk.get(oid) or [])
            except Exception:
                try:
                    self._orders_items[oid] = db_models.get_order_items(oid) or []
                except Exception:
                    self._orders_items[oid] = []
        try:
            zm = db_models.get_blocks_zamer_rows_by_mirror_order_ids([oid]) or {}
            self._blocks_zamer_by_order.update(zm)
        except Exception:
            pass
        # Сброс кэша API портала по строке: иначе замер/доставка/монтаж держат старый pr и не краснеют «не нужна».
        try:
            ck = self._order_cache_key(old)
            if ck is not None:
                self._order_portal_snapshot.pop(ck, None)
        except Exception:
            pass
        try:
            without_oid = frozenset(int(x) for x in self._order_cut_ids if int(x) != oid)
            cut = db_models.get_order_ids_with_cut_results([oid]) or set()
            self._order_cut_ids = without_oid | frozenset(int(x) for x in cut)
        except Exception:
            pass
        self._patch_order_filter_cache_for_orders([old])
        self._patch_order_row_meta_for_orders([old])
        if getattr(self, "_main_table_mode", "") == "orders":
            self._sync_orders_table_after_data_change(mirror_touch_id=oid)
        else:
            self._current_orders = list(self._all_orders)
            self._refresh_orders_display(self._current_orders)

    def _light_remove_mirror_order_from_memory(self, order_id):
        oid = int(order_id)
        self._all_orders = [
            o for o in (self._all_orders or []) if self._is_sales_row(o) or int(o.get("id") or 0) != oid
        ]
        self._order_filter_cache.pop("order:%s" % oid, None)
        self._order_row_meta.pop("order:%s" % oid, None)
        try:
            del self._orders_items[oid]
        except Exception:
            pass
        self._blocks_zamer_by_order.pop(oid, None)
        try:
            self._order_cut_ids = frozenset(x for x in self._order_cut_ids if int(x) != oid)
        except Exception:
            pass
        if getattr(self, "_main_table_mode", "") == "orders":
            self._sync_orders_table_after_data_change(mirror_removed_id=oid)
        else:
            self._current_orders = list(self._all_orders)
            self._refresh_orders_display(self._current_orders)

    def _light_append_mirror_order_from_db(self, order_id):
        from db import models as db_models

        oid = int(order_id)
        if self._mirror_order_dict_in_all_orders(oid) is not None:
            self._light_resync_mirror_order_from_db(oid, refresh_items=True)
            return
        row = db_models.get_mirror_order_list_row(oid)
        if not row:
            self._load_orders()
            return
        self._all_orders.append(dict(row))
        self._sort_mirror_and_sales_orders_inplace()
        self._orders_items.setdefault(oid, [])
        try:
            zm = db_models.get_blocks_zamer_rows_by_mirror_order_ids([oid]) or {}
            self._blocks_zamer_by_order.update(zm)
        except Exception:
            pass
        try:
            cut = db_models.get_order_ids_with_cut_results([oid])
            if cut:
                self._order_cut_ids = frozenset(self._order_cut_ids) | frozenset(int(x) for x in cut)
        except Exception:
            pass
        new_o = self._mirror_order_dict_in_all_orders(oid)
        if new_o:
            self._patch_order_filter_cache_for_orders([new_o])
            self._patch_order_row_meta_for_orders([new_o])
        if getattr(self, "_main_table_mode", "") == "orders":
            self._sync_orders_table_after_data_change(mirror_appended_id=oid)
        else:
            self._current_orders = list(self._all_orders)
            self._refresh_orders_display(self._current_orders)

    def _order_has_bundle_products(self, order_data) -> bool:
        return len(self._glass_order_bundle_products(order_data)) > 0

    def _on_order_tile_click(self, order_data):
        oid = order_data.get('id')
        if oid is None:
            return
        from ui.glass_order_overview_dialog import GlassOrderOverviewDialog

        if self._order_has_bundle_products(order_data):
            dlg = GlassOrderOverviewDialog(order_data, self)
            dlg.exec_()
            if getattr(dlg, "_main_orders_changed", False):
                oid = order_data.get("id")
                if oid:
                    QTimer.singleShot(
                        0,
                        lambda o=int(oid): self._light_resync_mirror_order_from_db(o, refresh_items=True),
                    )
                else:
                    QTimer.singleShot(0, self._load_orders)
            return
        from ui.order_detail_dialog import OrderDetailDialog

        d = OrderDetailDialog(oid, self)
        d.exec_()
        self._schedule_table_sync(
            lambda o=int(oid): self._light_resync_mirror_order_from_db(o, refresh_items=False)
        )

    def _on_order_tile_client_click(self, client_id, client_name):
        from ui.clients_dialog import ClientCardDialog
        d = ClientCardDialog(client_id, client_name, self)
        d.exec_()

    def _on_new_order(self):
        from ui.new_order_modal import NewOrderModal

        d = NewOrderModal(self)
        if d.exec_() == QDialog.Accepted:
            t = d.chosen_type()
            if t == 'facades':
                from ui.facade_order_dialog import FacadeOrderDialog

                # Заказ в БД создаётся только по кнопке сохранения в диалоге фасада, не при открытии окна.
                fd = FacadeOrderDialog(self)
                fd.exec_()
                new_oid = getattr(fd, "_linked_order_id", None)
                if new_oid:
                    self._schedule_table_sync(
                        lambda o=int(new_oid): self._light_append_mirror_order_from_db(o)
                    )
            elif t == 'glass':
                try:
                    from ui.glass_mirror_calc_dialog import GlassMirrorCalcDialog

                    # Заказ в БД создаётся только по «Завершить расчёт» в калькуляторе, не при открытии окна.
                    gd = GlassMirrorCalcDialog(self, order_id=None, append_new=True)
                    gd.exec_()
                    new_oid = getattr(gd, "_order_id", None)
                    if new_oid:
                        self._schedule_table_sync(
                            lambda o=int(new_oid): self._light_append_mirror_order_from_db(o)
                        )
                except Exception as e:
                    from PyQt5.QtWidgets import QMessageBox

                    QMessageBox.warning(self, "Новый заказ", "Не удалось открыть расчёт: %s" % e)
            elif t == 'sales':
                try:
                    from ui.sales_order_dialog import SalesOrderDialog
                    sd = SalesOrderDialog(self)
                    sd.exec_()
                    sid = getattr(sd, "_sales_order_id", None)
                    if sid:
                        self._schedule_table_sync(
                            lambda s=int(sid): self._light_append_sales_order_from_db(s)
                        )
                except Exception as e:
                    from PyQt5.QtWidgets import QMessageBox
                    QMessageBox.warning(self, "Продажа", "Не удалось открыть модуль продаж: %s" % e)
            elif t:
                from PyQt5.QtWidgets import QMessageBox
                QMessageBox.information(self, "Новый заказ", "Тип заказа: %s (в разработке)." % t)

    def _on_quick_estimate(self):
        role = str(self._user.get("role") or "")
        if role not in (ROLE_ADMIN, ROLE_MANAGER):
            QMessageBox.warning(
                self,
                "Быстрый просчет",
                "Доступно только для администратора и менеджера.",
            )
            return
        from ui.new_order_modal import NewOrderModal

        d = NewOrderModal(self, dialog_title="Быстрый просчет: выберите категорию")
        if d.exec_() != QDialog.Accepted:
            return
        t = d.chosen_type()
        if t not in ("glass", "facades", "sales"):
            QMessageBox.information(
                self,
                "Быстрый просчет",
                "Для быстрого просчёта доступны «Стекло/зеркало», «Фасады» и «Продажа».",
            )
            return

        table_changed = False
        if t == "glass":
            from ui.glass_mirror_calc_dialog import GlassMirrorCalcDialog

            gd = GlassMirrorCalcDialog(
                self,
                order_id=None,
                append_new=True,
                quick_estimate_mode=True,
            )
            if gd.exec_() == QDialog.Accepted:
                oid = getattr(gd, "_order_id", None)
                self._save_order_as_quick_estimate(oid, "glass")
                table_changed = bool(oid)
        elif t == "facades":
            from ui.facade_order_dialog import FacadeOrderDialog

            fd = FacadeOrderDialog(self, quick_estimate_mode=True)
            if fd.exec_() == QDialog.Accepted:
                oid = getattr(fd, "_linked_order_id", None)
                self._save_order_as_quick_estimate(oid, "facade")
                table_changed = bool(oid)
        else:
            from ui.sales_order_dialog import SalesOrderDialog

            sd = SalesOrderDialog(self, quick_estimate_mode=True)
            if sd.exec_() == QDialog.Accepted:
                sid = getattr(sd, "_sales_order_id", None)
                self._save_sales_as_quick_estimate(sid)
                table_changed = bool(sid)
        if table_changed:
            self._schedule_table_sync(self._load_orders)

    def _save_order_as_quick_estimate(self, order_id, category, quick_meta=None):
        if not order_id:
            return
        from db import models as db_models

        row = db_models.get_order(int(order_id))
        if not row:
            return
        cid = row.get("client_id")
        try:
            cid = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid = None
        qcid = row.get("quick_client_id")
        try:
            qcid = int(qcid) if qcid is not None else None
        except (TypeError, ValueError):
            qcid = None
        qm = dict(quick_meta or {})
        if qcid:
            base = db_models.quick_estimate_meta_from_quick_client_id(qcid) or {}
        elif cid:
            base = db_models.quick_estimate_meta_from_client_id(cid) or {}
        else:
            base = {}
        for k in ("lead_source", "phone", "extra_contact", "markup_percent", "client_name"):
            if qm.get(k) is not None and qm.get(k) != "":
                base[k] = qm[k]
        cname = (base.get("client_name") or row.get("client_name") or "").strip()
        parts = []
        ph = (base.get("phone") or "").strip()
        ex = (base.get("extra_contact") or "").strip()
        if ph:
            parts.append(ph)
        if ex:
            parts.append(ex)
        contact_info = (" · ".join(parts))[:255]
        db_models.create_quick_estimate(
            category=category,
            client_id=cid,
            quick_client_id=qcid,
            client_name=cname,
            lead_source=(base.get("lead_source") or "").strip()[:64],
            contact_info=contact_info,
            markup_percent=int(base.get("markup_percent") or 0),
            estimate_at=row.get("created_at") or None,
            created_by_user_id=self._user.get("id"),
            created_by_login=self._user.get("login"),
            created_by_role=self._user.get("role"),
            payload_json=row.get("blocks_calc_json"),
        )
        try:
            db_models.delete_order(int(order_id))
        except Exception:
            pass

    def _save_sales_as_quick_estimate(self, sales_order_id, quick_meta=None):
        if not sales_order_id:
            return
        import json
        from db import models as db_models

        row = db_models.get_sales_order(int(sales_order_id))
        if not row:
            return
        cid = row.get("client_id")
        try:
            cid = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid = None
        qcid = row.get("quick_client_id")
        try:
            qcid = int(qcid) if qcid is not None else None
        except (TypeError, ValueError):
            qcid = None
        qm = dict(quick_meta or {})
        if qcid:
            base = db_models.quick_estimate_meta_from_quick_client_id(qcid) or {}
        elif cid:
            base = db_models.quick_estimate_meta_from_client_id(cid) or {}
        else:
            base = {}
        for k in ("lead_source", "phone", "extra_contact", "markup_percent", "client_name"):
            if qm.get(k) is not None and qm.get(k) != "":
                base[k] = qm[k]
        cname = (base.get("client_name") or row.get("client_name") or "").strip()
        parts = []
        ph = (base.get("phone") or "").strip()
        ex = (base.get("extra_contact") or "").strip()
        if ph:
            parts.append(ph)
        if ex:
            parts.append(ex)
        contact_info = (" · ".join(parts))[:255]
        db_models.create_quick_estimate(
            category="sales",
            client_id=cid,
            quick_client_id=qcid,
            client_name=cname,
            lead_source=(base.get("lead_source") or "").strip()[:64],
            contact_info=contact_info,
            markup_percent=int(base.get("markup_percent") or 0),
            estimate_at=row.get("created_at") or None,
            created_by_user_id=self._user.get("id"),
            created_by_login=self._user.get("login"),
            created_by_role=self._user.get("role"),
            payload_json=json.dumps({"sales_order_id": int(sales_order_id)}, ensure_ascii=False),
        )

    def _on_prices(self):
        """Открыть окно управления ценами (доступно только админу)."""
        try:
            from ui.prices_dialog import PricesDialog
        except Exception:
            return
        d = PricesDialog(self._user, self)
        d.exec_()

    def _on_confirm_users(self):
        from ui.confirm_users_dialog import ConfirmUsersDialog
        d = ConfirmUsersDialog(self)
        d.exec_()
        self._refresh_confirm_button()

    def _refresh_confirm_button(self):
        for w in self.findChildren(QPushButton):
            if "Подтвердить" in (w.text() or ""):
                n = get_unapproved_count()
                w.setText("Подтвердить" + (" (%d)" % n if n else ""))
                break

    def _refresh_header_user_labels(self):
        def _s(v):
            if v is None:
                return ""
            return str(v).strip()

        name = ("%s %s" % (_s(self._user.get("name")), _s(self._user.get("surname")))).strip() or _s(
            self._user.get("login")
        )
        role = self._user.get("role") or "manager"
        role_color = color("role_admin") if role == ROLE_ADMIN else color("role_manager")
        self._header_lbl_name.setText(name)
        self._header_lbl_role.setText(role_label_desktop(role))
        self._header_lbl_role.setStyleSheet(
            "color: %s; font-weight: bold; margin-left: 8px;" % role_color
        )

    def _on_personal_cabinet(self):
        from ui.personal_cabinet_dialog import PersonalCabinetDialog

        d = PersonalCabinetDialog(self._user, self)
        if d.exec_() != QDialog.Accepted:
            return
        u = d.get_updated_user()
        if u:
            self._user = dict(u)
            self._refresh_header_user_labels()

    def _on_all_users(self):
        from ui.all_users_dialog import AllUsersDialog
        d = AllUsersDialog(self, current_user=self._user)
        d.exec_()
        uid = self._user.get("id")
        if uid is not None:
            fresh = get_user_by_id(uid)
            if fresh:
                self._user = dict(fresh)
                self._refresh_header_user_labels()

    def _on_clients(self):
        from ui.clients_dialog import ClientsDialog
        d = ClientsDialog(self)
        d.exec_()

    def _on_suppliers(self):
        from ui.suppliers_dialog import SuppliersDialog
        d = SuppliersDialog(self)
        d.exec_()

    def _on_sales(self):
        try:
            from ui.sales_orders_dialog import SalesOrdersDialog
            d = SalesOrdersDialog(self)
            d.exec_()
        except Exception as e:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Продажи", "Не удалось открыть список продаж: %s" % e)

    def _on_warehouse(self):
        from ui._mirror_dialogs import load_warehouse_dialog
        from PyQt5.QtWidgets import QMessageBox
        WarehouseDialog = load_warehouse_dialog()
        if WarehouseDialog is None:
            QMessageBox.warning(self, "Склады", "Модуль склада не найден (MIRROR_CUT/ui).")
            return
        try:
            d = WarehouseDialog(self)
            d.exec_()
        except Exception as e:
            QMessageBox.warning(self, "Склады", "Ошибка открытия склада: %s" % e)

    def _on_settings(self):
        from ui.settings_dialog import SettingsDialog
        d = SettingsDialog(self)
        if d.exec_() == QDialog.Accepted:
            self._on_search_client()

    def _on_trial_cut(self):
        from ui.trial_cut_dialog import TrialCutDialog
        d = TrialCutDialog(self)
        d.exec_()

    def _on_inventory_tables(self):
        from ui.inventory_tables_dialog import InventoryTablesDialog

        InventoryTablesDialog(self, current_user=self._user or {}).exec_()

    def _on_cut_by_material(self):
        if self._user.get("role") != ROLE_ADMIN:
            return
        from ui.cut_by_material_dialog import CutByMaterialDialog

        if CutByMaterialDialog(self).exec_() == QDialog.Accepted:
            self._schedule_table_sync(self._refresh_mirror_cut_column_after_bulk_cut)

    def _open_cut_program(self, order_data):
        """Модуль раскроя стекла (MIRROR_CUT): листы, варианты раскладки, запись в mirror_cut_results."""
        from PyQt5.QtWidgets import QMessageBox
        from mirror_cut_sys_path import mirror_cut_imports_first

        lock_reason = self._order_measure_lock_reason(order_data.get("id"))
        if lock_reason:
            QMessageBox.information(self, "Раскрой", lock_reason)
            return

        try:
            with mirror_cut_imports_first():
                from ui.create_cut_dialog import CreateCutDialog
                from db import models as _cut_db
                from mirror_cut_prefill import cut_prefill_for_main_order

                parts, _, bundle_client = cut_prefill_for_main_order(order_data, _cut_db)
                if not parts:
                    QMessageBox.information(
                        self,
                        "Раскрой",
                        "Нет данных для раскроя: в расчёте нет прямоугольного стекла "
                        "(отдельные позиции стекло/зеркало или наполнение проёма в фасаде с размерами).",
                    )
                    return
                d = CreateCutDialog(
                    self,
                    pin_order_id=order_data.get("id"),
                    initial_parts=parts,
                    # Менеджер должен уметь указать лист, если раскрой не помещается на один.
                    lock_parts_ui=(self._user.get("role") != ROLE_MANAGER),
                    bundle_client_name=bundle_client,
                )
                oid = order_data.get("id")
                base_title = d.windowTitle()
                d.setWindowTitle(
                    "%s — заказ №%s (у каждой детали: «Выбрать лист»; лист «в работе» без реза — в списке цветом)"
                    % (base_title, oid or "—")
                )
                d.exec_()
        except Exception as e:
            QMessageBox.warning(
                self,
                "Раскрой",
                "Не удалось загрузить модуль раскроя (MIRROR_CUT/ui/create_cut_dialog.py).\n%s" % e,
            )
            return
        oid = order_data.get("id")
        if oid is not None:
            self._schedule_table_sync(
                lambda o=int(oid): self._light_resync_mirror_order_from_db(o, refresh_items=False)
            )

    # --- Раскрой: схема, PDF, этикетки (из MIRROR_CUT) ---

    def _open_scheme_with_layouts(self, order_data, layout_dicts):
        """Открыть диалог схемы раскроя для уже посчитанного заказа."""
        from PyQt5.QtWidgets import QMessageBox
        from mirror_cut_sys_path import mirror_cut_imports_first

        if not layout_dicts:
            QMessageBox.information(self, "Схема", "Нет данных раскладки.")
            return
        with mirror_cut_imports_first():
            from ui.cutting_result_dialog import CuttingResultDialog

            d = CuttingResultDialog(layout_dicts, order_data, self)
            try:
                _onum = order_data.get("id") or order_data.get("order_id")
                if _onum is not None:
                    d.setWindowTitle(
                        "Схема раскроя — все листы заказа (сохранённый раскрой) — заказ №%s" % _onum
                    )
            except Exception:
                pass

            # Кнопка PDF: экспорт карт раскроя
            d.btn_pdf.clicked.connect(lambda: self._open_pdf(order_data, layouts_getter=lambda: d.layouts))
            # Кнопка этикеток: печать этикеток с QR
            d.print_labels_requested.connect(lambda: self._open_label(order_data))
            d.exec_()

    def _open_pdf(self, order_data, layouts_getter=None, *, overview_summary_only=False):
        """Карты раскроя + задание цеху; из сводки по изделиям — один PDF-смета."""
        from PyQt5.QtWidgets import QMessageBox

        from logic.production_instructions import (
            bundle_has_products,
            write_order_commercial_summary_pdf,
            write_order_worker_instructions_pdf,
        )

        oid = order_data.get("id")
        layouts = []
        if layouts_getter and callable(layouts_getter):
            layouts = list(layouts_getter())
        else:
            from mirror_cut_sys_path import mirror_cut_imports_first

            with mirror_cut_imports_first():
                from db.models import get_cut_results

                for r in get_cut_results(oid) or []:
                    lay = r.get("layout")
                    if isinstance(lay, dict) and (lay.get("pieces") or lay.get("sheet_width")):
                        layouts.append(lay)
        layouts = [lay for lay in layouts if isinstance(lay, dict) and (lay.get("pieces") or lay.get("sheet_width"))]

        from db import models as db_models

        row = db_models.get_order(int(oid)) if oid else None
        if not row:
            QMessageBox.warning(self, "PDF", "Заказ не найден.")
            return

        cfg = app_cfg()
        folder = get_cfg_string(cfg, "paths", "cutting_pdf_dir", "") if cfg else ""
        if not folder:
            folder = get_base_dir()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            pass

        opened_any = False

        if overview_summary_only:
            if bundle_has_products(row):
                cpath = os.path.join(folder, "Сводка_заказ_%s.pdf" % (oid or "просчет"))
                try:
                    write_order_commercial_summary_pdf(row, cpath)
                    _open_file(cpath)
                    opened_any = True
                except Exception as e:
                    QMessageBox.warning(self, "PDF", str(e))
            if not opened_any:
                QMessageBox.warning(
                    self,
                    "PDF",
                    "Нет изделий в расчёте для формирования сводки.",
                )
            return

        if bundle_has_products(row):
            cpath = os.path.join(folder, "Смета_заказ_%s.pdf" % (oid or "0"))
            try:
                write_order_commercial_summary_pdf(row, cpath)
                _open_file(cpath)
                opened_any = True
            except Exception as e:
                QMessageBox.warning(self, "PDF сметы", str(e))

        if layouts:
            from mirror_cut_sys_path import mirror_cut_imports_first

            with mirror_cut_imports_first():
                from logic.pdf_export import generate_cutting_pdf

                order_info = {
                    "order_id": row.get("id"),
                    "client_name": row.get("client_name"),
                    "created_at": row.get("created_at"),
                    "k_number": row.get("k_number"),
                }
                path = os.path.join(folder, "Карты_раскроя_заказ_%s.pdf" % (oid or "0"))
                try:
                    generate_cutting_pdf(layouts, order_info, path)
                    _open_file(path)
                    opened_any = True
                except Exception as e:
                    QMessageBox.critical(self, "PDF раскроя", str(e))
                    if not bundle_has_products(row):
                        return

        if bundle_has_products(row):
            wpath = os.path.join(folder, "Задание_цех_заказ_%s.pdf" % (oid or "0"))
            try:
                write_order_worker_instructions_pdf(row, wpath)
                _open_file(wpath)
                opened_any = True
            except Exception as e:
                if not layouts:
                    QMessageBox.critical(self, "PDF задания цеху", str(e))
                    return

        if not opened_any:
            QMessageBox.warning(
                self,
                "PDF",
                "Нет данных для PDF: в заказе нет сохранённого раскроя стекла и пустой расчёт изделий.",
            )

    def _prompt_label_kinds(self):
        """Если доступны и стекло, и профили — спросить, что печатать. Иначе None = не отмена."""
        d = QDialog(self)
        d.setWindowTitle("Печать этикеток")
        v = QVBoxLayout(d)
        v.addWidget(QLabel("Выберите тип этикеток для формирования PDF:"))
        cb_g = QCheckBox("Стекло / зеркало (изделия и деловые остатки на склад — этикетки с QR)")
        cb_p = QCheckBox("Профили фасада (брусья со склада)")
        cb_g.setChecked(True)
        cb_p.setChecked(True)
        v.addWidget(cb_g)
        v.addWidget(cb_p)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        v.addWidget(bb)
        if d.exec_() != QDialog.Accepted:
            return None
        if not cb_g.isChecked() and not cb_p.isChecked():
            QMessageBox.warning(d, "Этикетки", "Отметьте хотя бы один тип.")
            return None
        return cb_g.isChecked(), cb_p.isChecked()

    def _open_label(self, order_data):
        """Сгенерировать PDF этикеток (остатки + нарезанные изделия) для заказа; при фасадах — выбор профиль/стекло."""
        from PyQt5.QtWidgets import QMessageBox
        from mirror_cut_sys_path import mirror_cut_imports_first

        order_id = order_data.get('id')
        with mirror_cut_imports_first():
            from datetime import datetime

            from db import models
            from db.models import get_cut_results, get_remnant_ids_by_order_id
            from logic.blocks_bundle import bundle_to_json, parse_bundle
            from logic.labels import generate_labels_pdf_multi
            from logic.production_instructions import (
                collect_finished_facade_labels_for_order,
                collect_profile_remnant_labels_for_order,
                write_profile_labels_pdf,
            )

            order_fresh = models.get_order_for_labels(order_id) if order_id else None
            if not order_fresh or not order_status_allows_production_print(order_fresh.get("status")):
                QMessageBox.information(
                    self,
                    "Этикетки",
                    "Печать этикеток доступна с «Оплачен» и для последующих статусов («В работе», «Изготовлен» и т.д.).",
                )
                return

            if order_id:
                try:
                    if models.get_cut_results(int(order_id)):
                        models.sync_missing_remnant_records_for_order(int(order_id))
                except Exception:
                    pass

            cfg = app_cfg()
            out_dir = get_cfg_string(cfg, 'paths', 'labels_pdf_dir', '') if cfg else ''
            if not out_dir:
                out_dir = get_base_dir()
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as e:
                QMessageBox.critical(self, "Ошибка", "Не удалось создать папку: %s" % e)
                return

            row_for_lbl = models.get_order(int(order_id)) if order_id else None
            row_for_lbl = dict(row_for_lbl) if row_for_lbl else dict(order_data)
            # Склад фасадного профиля списывается при подтверждении реза в WEB_SERVICE; этикетки — по данным заказа/партии.
            prof_labels = collect_finished_facade_labels_for_order(row_for_lbl)
            prof_labels.extend(collect_profile_remnant_labels_for_order(row_for_lbl))

            remnants = []
            for rid in get_remnant_ids_by_order_id(order_data['id']) or []:
                rem = models.get_remnant_by_id(rid)
                if not rem:
                    continue
                label_no = rem.get('label_number')
                if label_no is None:
                    label_no = models.ensure_remnant_label_number(rem['id'])
                rem['label_number'] = label_no
                remnants.append(rem)

            pieces = []
            order_for_labels = order_fresh
            k_number = (
                (order_for_labels or order_data).get('k_number')
                if (order_for_labels or order_data)
                else order_data.get('k_number')
            )
            order_date = (
                (order_for_labels or order_data).get('accepted_at')
                or (order_for_labels or order_data).get('created_at')
                if (order_for_labels or order_data)
                else order_data.get('accepted_at') or order_data.get('created_at')
            )

            if order_for_labels and order_for_labels.get('status') == 'completed' and order_id:
                client_name = (models.get_order_client_name_from_archive(order_id) or '').strip()
            else:
                client_name = (order_for_labels.get('client_name') or '') if order_for_labels else ''
                client_name = (client_name or '').strip()
            if not client_name and order_id:
                client_name = (models.get_order_client_name(order_id) or '').strip()
            if not client_name and order_id:
                cl_id = (order_for_labels or order_data).get('client_id')
                if cl_id:
                    cl = models.get_client_by_id(cl_id)
                    if cl:
                        client_name = (models._client_display_name(cl) or cl.get('name') or '').strip()
            if not client_name:
                client_name = (order_data.get('client_name') or '').strip()

            piece_number = 0
            for r in get_cut_results(order_id or order_data['id']) or []:
                lay = r.get('layout')
                if isinstance(lay, dict):
                    mat = lay.get('material') or ''
                    thick = lay.get('thickness_mm')
                    for p in lay.get('pieces') or []:
                        piece_number += 1
                        piece = dict(p)
                        if not piece.get('name') and not piece.get('material'):
                            piece['name'] = mat
                        piece['piece_number'] = piece_number
                        if k_number is not None:
                            piece['k_number'] = k_number
                        piece['client_name'] = (client_name or '').strip()
                        piece['order_date'] = order_date
                        if thick is not None:
                            piece['thickness_mm'] = thick
                        try:
                            from logic.glass_piece_edges import edge_treatment_for_piece_mm  # noqa: WPS433

                            ew = int(piece.get('w') or piece.get('width_mm') or 0)
                            eh = int(piece.get('h') or piece.get('height_mm') or 0)
                            et = edge_treatment_for_piece_mm(dict(order_for_labels or row_for_lbl or order_data), ew, eh)
                            if et:
                                piece['edge_treatment'] = et
                        except Exception:
                            pass
                        pieces.append(piece)

            has_glass = bool(remnants or pieces)
            has_prof = bool(prof_labels)
            order_kind_txt = str((order_for_labels or order_data).get("order_kind") or "").strip().lower()
            is_facade_order = ("facade" in order_kind_txt) or has_prof
            want_glass = has_glass
            want_prof = has_prof
            if has_glass and has_prof and not is_facade_order:
                choice = self._prompt_label_kinds()
                if choice is None:
                    return
                want_glass, want_prof = choice
            elif has_glass and has_prof and is_facade_order:
                # Для фасадов печатаем оба типа в одном действии: и профили, и стекло/зеркало.
                want_glass, want_prof = True, True
            elif has_glass:
                want_glass, want_prof = True, False
            elif has_prof:
                want_glass, want_prof = False, True
            else:
                QMessageBox.information(
                    self,
                    "Этикетки",
                    "Нет этикеток раскроя стекла — выполните «Раскрой» и сохраните листы в базе.\n\n"
                    "Для фасадов этикетки на брусья профиля доступны после назначения профилей со склада в расчёте.",
                )
                return

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            if want_prof and prof_labels:
                pl_path = os.path.join(
                    out_dir, "Этикетки_профилей_заказ_%s_%s.pdf" % (order_data.get("id", ""), stamp)
                )
                try:
                    write_profile_labels_pdf(prof_labels, pl_path)
                except Exception as e:
                    QMessageBox.critical(self, "Этикетки профилей", str(e))
                    return
                _open_file(pl_path)

            if want_glass and (remnants or pieces):
                unique_name = "Этикетки_стекло_заказ_%s_%s.pdf" % (order_data.get('id', ''), stamp)
                filepath = os.path.join(out_dir, unique_name)
                try:
                    generate_labels_pdf_multi(remnants, pieces, filepath)
                except Exception as e:
                    QMessageBox.critical(self, "Этикетки", "Ошибка при сохранении PDF: %s" % e)
                    return
                _open_file(filepath)
            elif want_glass and not remnants and not pieces:
                QMessageBox.information(
                    self,
                    "Этикетки стекла",
                    "Данных для этикеток раскроя стекла пока нет — сохраните раскрой листов.",
                )


