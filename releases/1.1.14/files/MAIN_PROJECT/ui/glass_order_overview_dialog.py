# -*- coding: utf-8 -*-
"""Сводка заказа: все изделия, миниатюры, цены по позициям; полный расчёт по клику."""
import sys
import os
import math
import copy
import time
import urllib.request
from functools import lru_cache

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtCore import QObject, QRunnable, QRect, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap, QPainter, QFont, QColor, QPen, QBrush
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QScrollArea,
    QWidget,
    QSizePolicy,
    QMessageBox,
    QTextBrowser,
    QFrame,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QComboBox,
    QCheckBox,
    QLineEdit,
)
from db_main import (
    order_status_to_ru,
    facades_get_all_angle_seal,
    order_status_allows_production_print,
    order_status_allows_bundle_edit,
)
from db import models as db_models

from logic.blocks_bundle import (
    CUT_SCHEME_CREATED,
    ORDER_LEVEL_ROW_NAMES,
    PAYMENT_CARD,
    PAYMENT_CASH,
    PAYMENT_COD,
    PAYMENT_BANK,
    PAYMENT_QR,
    PAYMENT_TYPE_LABELS_RU,
    PAYMENT_UNPAID,
    PRODUCTION_GLASS_MADE,
    parse_bundle,
    bundle_to_json,
    bundle_grand_total_rub,
    product_is_paid,
    bundle_product_headline,
    bundle_payment_aggregate,
    bundle_surcharge_aggregate,
    bundle_all_units_in_terminal_order_statuses,
    set_product_surcharge,
    set_product_payment_type,
)
from mirror_cut_prefill import order_bundle_has_cuttable_glass
from window_branding import apply_window_icon

# Сводная строка по стеклу в фасаде: сумма = сумма строк детализации ниже — не входит в «Итого по позиции» дважды.
_FACADE_GLASS_SPEC_ROW = "Стекло в проёме (по спецификации)"

_OVERVIEW_SCREEN_FRACTION = 0.8


def _overview_available_screen_geometry(widget=None):
    geo = None
    try:
        if widget is not None:
            scr = widget.screen()
            if scr is not None:
                geo = scr.availableGeometry()
    except Exception:
        pass
    if geo is None:
        app = QApplication.instance()
        if app is not None:
            try:
                scr = app.primaryScreen()
                if scr is not None:
                    geo = scr.availableGeometry()
            except Exception:
                pass
    if geo is None:
        try:
            desk = QApplication.desktop()
            if desk is not None:
                geo = desk.availableGeometry(widget)
        except Exception:
            pass
    return geo


def _is_service_surcharge_row(name: str) -> bool:
    """Строки замера, доставки, монтажа — отдельная таблица «Услуги», не входят в итого «только изделия»."""
    n = str(name or "").strip()
    if n in ORDER_LEVEL_ROW_NAMES:
        return True
    low = n.lower()
    if low.startswith("доставк"):
        return True
    if "монтаж" in low:
        return True
    if "замер" in low:
        return True
    return False


def _split_pricing_rows_product_vs_services(rows: list) -> tuple[list, list]:
    prod: list = []
    srv: list = []
    for row in rows or []:
        if not row:
            continue
        nm = str(row[0] or "")
        if _is_service_surcharge_row(nm):
            srv.append(row)
        else:
            prod.append(row)
    return prod, srv


def _sum_price_rows_products_only(rows: list) -> int:
    s = 0
    for name, rub, _ in rows or []:
        if name == _FACADE_GLASS_SPEC_ROW:
            continue
        if _is_service_surcharge_row(str(name)):
            continue
        s += int(rub or 0)
    return s


def _bundle_products_subtotal_and_services_rub(
    products: list,
    *,
    conn,
    drill,
    markup_factor: float,
    delivery_prices,
    facade_aux_cached,
) -> tuple[int, int]:
    """(сумма только по изделиям, сумма услуг заказа: доставка+замер один раз как в смете)."""
    if not products:
        return 0, 0
    sub_products = 0
    for p in products:
        pl = p.get("payload")
        if not isinstance(pl, dict):
            continue
        kind = str(p.get("kind") or "glass_mirror").strip() or "glass_mirror"
        if kind == "facade":
            pr_rows = _facade_pricing_rows(
                pl,
                conn,
                drill,
                markup_factor,
                aux_prices=facade_aux_cached,
                delivery_prices=delivery_prices,
            )
        else:
            pr_rows = _glass_pricing_rows(pl, conn, drill, markup_factor)
        sub_products += int(_sum_price_rows_products_only(pr_rows))
    dlv, zam, mon = _order_level_amounts_once_priced_mixed(
        products, conn, drill, markup_factor, delivery_prices, facade_aux_cached
    )
    return int(sub_products), int(dlv) + int(zam) + int(mon)


class _OverviewServicesClientProxy:
    """Минимальный «клиент» для ZamerTile/DeliveryTile в диалоге услуг из сводки заказа."""

    def __init__(self, order_data: dict):
        self._order_data = dict(order_data or {})
        cid = self._order_data.get("client_id")
        try:
            self._client_id = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            self._client_id = None
        qcid = self._order_data.get("quick_client_id")
        try:
            self._quick_client_id = int(qcid) if qcid is not None else None
        except (TypeError, ValueError):
            self._quick_client_id = None

    def get_payload(self):
        pl = {
            "id": self._client_id,
            "Имя": (self._order_data.get("client_name") or "").strip(),
        }
        if self._quick_client_id is not None:
            pl["quick_client_id"] = self._quick_client_id
        return pl


class _OverviewComboBox(QComboBox):
    """В QScrollArea колесо над комбобоксом не меняет выбранный пункт (только клик по списку)."""

    def wheelEvent(self, event):
        event.ignore()


class _BlocksPersistSignals(QObject):
    ok = pyqtSignal(object)
    err = pyqtSignal(str)


class _BlocksPersistRunnable(QRunnable):
    """Запись blocks_calc_json в PostgreSQL вне GUI-потока."""

    def __init__(self, order_id: int, payload: str, sigs: _BlocksPersistSignals):
        super().__init__()
        self._order_id = int(order_id)
        self._payload = str(payload or "")
        self._sigs = sigs

    def run(self):
        try:
            db_models.update_order_blocks_calc(self._order_id, self._payload)
            self._sigs.ok.emit(self._payload)
        except Exception as e:
            self._sigs.err.emit(str(e))


def _drain_qthread_pool(pool: QThreadPool, max_seconds: float = 6.0) -> None:
    """Дождаться QThreadPool на GUI-потоке с processEvents — один waitForDone(20000) блокировал весь UI при закрытии."""
    app = QApplication.instance()
    t0 = time.monotonic()
    try:
        while pool.activeThreadCount() > 0 and (time.monotonic() - t0) < max_seconds:
            pool.waitForDone(80)
            if app:
                app.processEvents()
    except Exception:
        try:
            pool.waitForDone(int(max(1000, max_seconds * 1000)))
        except Exception:
            pass


def _ensure_blocks_path() -> None:
    _bd = os.path.normpath(os.path.join(_mp, "BLOCKS"))
    if _bd not in sys.path:
        sys.path.insert(0, _bd)


def _client_markup_factor(order_data: dict | None) -> float:
    """Наценка по клиенту заказа: справочник (pricing_tier) или быстрый клиент (markup_percent)."""
    if not order_data:
        return 1.0
    qcid = order_data.get("quick_client_id")
    if qcid is not None:
        try:
            row = db_models.get_mirror_quick_client_by_id(int(qcid)) or {}
            p = int(row.get("markup_percent") or 0)
            return 1.0 + max(0, p) / 100.0
        except Exception:
            pass
    cid = order_data.get("client_id")
    if cid is None:
        return 1.0
    try:
        row = db_models.get_client_by_id(int(cid)) or {}
        return float(db_models.client_price_factor(row))
    except Exception:
        return 1.0


def _glass_table_row_gets_client_markup(row_title: str) -> bool:
    """Доставка и замер/выезд — без клиентской наценки в таблице сметы."""
    t = str(row_title or "").strip().lower()
    if t.startswith("доставк"):
        return False
    if t.startswith("замер"):
        return False
    return True


def _money_markup(x: float | int, markup_factor: float) -> int:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0
    mk = max(1.0, float(markup_factor))
    if mk <= 1.0:
        return int(round(v))
    return int(math.ceil(v * mk))


def _facade_aux_prices() -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    corner = {"F3-021": 0.0, "F3-031": 0.0}
    seal = {"черный": 0.0, "прозрачный": 0.0}
    screw = {"серебро": 0.0, "золото": 0.0}
    for r in facades_get_all_angle_seal() or []:
        it = (r.get("item_type") or "").strip()
        v = str(r.get("variant") or "").strip()
        try:
            price = float(r.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        if it == "Угловой соединитель" and v in corner:
            corner[v] = price
        elif it == "Уплотнитель":
            vl = v.lower()
            if "черн" in vl:
                seal["черный"] = price
            elif "прозрач" in vl:
                seal["прозрачный"] = price
        elif it == "Винт":
            vl = v.lower()
            if "сереб" in vl:
                screw["серебро"] = price
            elif "золот" in vl:
                screw["золото"] = price
    return corner, seal, screw


def _payload_seal_variant_key(pl: dict) -> str:
    u = pl.get("Уплотнитель") if isinstance(pl.get("Уплотнитель"), dict) else {}
    v = str(u.get("Цвет") or "").strip().lower()
    return "черный" if "черн" in v else "прозрачный"


def _payload_screw_variant_key(pl: dict) -> str:
    u = pl.get("Винты") if isinstance(pl.get("Винты"), dict) else {}
    v = str(u.get("Цвет") or "").strip().lower()
    return "золото" if "золот" in v else "серебро"


def _facade_service_money_from_payload(
    pl: dict, *, delivery_prices: dict | None = None
) -> list[tuple[str, int, str]]:
    """Доставка и замер по блокам в payload фасада (как в FacadeOrderDialog._calc_service_prices)."""
    from calc.delivery_calc import (
        delivery_price_rub,
        fetch_delivery_prices,
        montazh_price_rub,
        zamer_visit_price_rub,
        delivery_base_rub_from_data,
    )

    prices = delivery_prices if delivery_prices is not None else fetch_delivery_prices()
    out: list[tuple[str, int, str]] = []
    dblk = pl.get("Доставка") if isinstance(pl.get("Доставка"), dict) else {}
    if dblk.get("Активирован") and isinstance(dblk.get("Данные"), dict):
        dd = dict(dblk.get("Данные") or {})
        inside = bool(dd.get("Внутри КАД", True))
        km = dd.get("Расстояние до КАД")
        if inside or km is not None:
            base = delivery_base_rub_from_data(dd, prices)
            dr = int(delivery_price_rub(prices, inside, km, base_rub=base) or 0)
            if dr:
                addr = str(dd.get("Адрес") or "").strip()
                out.append(("Доставка", dr, addr and ("адрес: %s" % addr) or ""))
    zblk = pl.get("Замер") if isinstance(pl.get("Замер"), dict) else {}
    zd = zblk.get("Данные") if isinstance(zblk.get("Данные"), dict) else {}
    if zblk.get("Активирован") and isinstance(zd, dict):
        vd = zd.get("Данные выезда") or {}
        inside = bool(vd.get("Внутри КАД", True))
        km = vd.get("Расстояние до КАД")
        visit_ok = inside or km is not None
        addr = str(zd.get("Адрес") or "").strip()
        if bool(zd.get("Замер")) and visit_ok:
            zr = int(zamer_visit_price_rub(prices, inside, km) or 0)
            if zr:
                note = "замер"
                if addr:
                    note = "%s; %s" % (note, addr)
                out.append(("Замер (выезд)", zr, note))
        if bool(zd.get("Монтаж")):
            mr = int(montazh_price_rub(prices) or 0)
            if mr:
                note = "монтаж"
                if addr:
                    note = "%s; %s" % (note, addr)
                out.append(("Монтаж", mr, note))
    return out


def _profile_series_is_prisma(profile: dict | None) -> bool:
    series = str((profile or {}).get("series") or "").strip().upper()
    return "PRISMA" in series


def _corner_code_for_profiles(a: dict | None, b: dict | None) -> str:
    if _profile_series_is_prisma(a or {}) or _profile_series_is_prisma(b or {}):
        return "F3-031"
    return "F3-021"


def client_line(order_data):
    return (order_data.get("client_name") or "").strip() or "—"


def _table_show_full_no_scroll(tbl: QTableWidget) -> None:
    """Вся таблица видна целиком: без полос прокрутки, высота по числу строк."""
    tbl.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    tbl.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    tbl.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    tbl.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    tbl.setWordWrap(True)
    tbl.verticalHeader().setVisible(False)
    tbl.resizeRowsToContents()
    hdr_h = tbl.horizontalHeader().height()
    body = 0
    for r in range(tbl.rowCount()):
        rh = tbl.rowHeight(r)
        if rh <= 0:
            rh = tbl.sizeHintForRow(r)
        if rh <= 0:
            rh = 28
        body += rh
    if tbl.rowCount() == 0:
        body = tbl.verticalHeader().defaultSectionSize()
    frame = tbl.frameWidth() * 2 if tbl.frameWidth() > 0 else 4
    tbl.setFixedHeight(hdr_h + body + frame)
    tbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


def _facade_thumb_pixmap(pl: dict, w: int = 168, h: int = 126) -> QPixmap:
    try:
        from ui.facade_order_dialog import FacadeCanvas
    except Exception:
        return QPixmap()
    c = FacadeCanvas(font_scale=1.55)
    pl = pl or {}
    try:
        c.set_dimensions(int(pl.get("Ширина_мм") or 400), int(pl.get("Высота_мм") or 600))
    except (TypeError, ValueError):
        c.set_dimensions(400, 600)
    c.set_side_profiles(pl.get("Профили_по_сторонам") or {})
    gi = pl.get("Стекло") if isinstance(pl.get("Стекло"), dict) else {}
    c.set_glass_info(gi)
    uv = pl.get("Уплотнитель") if isinstance(pl.get("Уплотнитель"), dict) else {}
    c.set_seal_variant(uv.get("Цвет"))
    # Рендер с запасом по пикселям, чтобы крупный макет не был «мыльным».
    rw = max(120, min(1600, max(int(w * 2), 320)))
    rh = max(100, min(1200, max(int(h * 2), 240)))
    c.resize(rw, rh)
    pm = QPixmap(c.size())
    pm.fill(Qt.white)
    c.render(pm)
    return pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _schedule_facade_thumb(owner: QWidget, label: QLabel, pl: dict, tw: int, th: int) -> None:
    """Рендер миниатюры после первого кадра: окно не ждёт холодный импорт FacadeCanvas → WebEngine."""
    pl_copy = dict(pl or {})

    def _go():
        try:
            pm = _facade_thumb_pixmap(pl_copy, tw, th)
            if pm.isNull():
                label.setText("макет")
            else:
                label.setPixmap(pm)
        except RuntimeError:
            pass

    t = QTimer(owner)
    t.setSingleShot(True)
    t.timeout.connect(_go)
    t.start(0)


def _glass_block_izmat(payload: dict) -> tuple[dict, dict]:
    """Параметры изделия и материала: корень + blocks_selected."""
    izd = dict(payload.get("Параметры изделия") or {})
    mat = dict(payload.get("Параметры материала") or {})
    bs = payload.get("blocks_selected")
    if isinstance(bs, dict):
        iz2 = bs.get("Изделие") if isinstance(bs.get("Изделие"), dict) else {}
        if not iz2 and isinstance(bs.get("Материал"), dict):
            iz2 = bs["Материал"]
        if isinstance(iz2, dict):
            if iz2.get("Форма") or iz2.get("Форма "):
                izd.setdefault("Форма", iz2.get("Форма") or iz2.get("Форма "))
            for k in ("Ширина (мм)", "Высота (мм)", "Количество (шт)"):
                if iz2.get(k) is not None:
                    izd[k] = iz2.get(k)
        mat2 = bs.get("Материал") if isinstance(bs.get("Материал"), dict) else {}
        if mat2:
            for k in ("Тип материала", "Цвет / Вариант", "Толщина (мм)", "Закалка"):
                if mat2.get(k) is not None and k not in mat:
                    mat[k] = mat2.get(k)
    return izd, mat


def _load_sketch_pixmap_payload(payload: dict) -> QPixmap:
    pix = QPixmap()
    izd, _ = _glass_block_izmat(payload)
    fi = izd.get("Файл") or ""
    if not fi:
        return pix
    p = str(fi).strip()
    try:
        b = _load_sketch_bytes_cached(p)
        if b:
            pix.loadFromData(b)
    except Exception:
        pass
    return pix


@lru_cache(maxsize=256)
def _load_sketch_bytes_cached(path_or_url: str):
    p = str(path_or_url or "").strip()
    if not p:
        return b""
    try:
        if p.startswith("http://") or p.startswith("https://"):
            with urllib.request.urlopen(p, timeout=4) as r:
                return r.read() or b""
        if os.path.isfile(p):
            with open(p, "rb") as f:
                return f.read() or b""
    except Exception:
        return b""
    return b""


def _glass_schematic_pixmap(payload: dict, w: int, h: int) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(QColor(250, 252, 255))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    izd, mat = _glass_block_izmat(payload)
    shape = str(izd.get("Форма") or "—")
    qty = izd.get("Количество (шт)")
    qty_s = str(qty) if qty is not None and str(qty).strip() != "" else "1"
    mw = str(mat.get("Тип материала") or "—")
    col = str(mat.get("Цвет / Вариант") or "—")
    th = mat.get("Толщина (мм)")
    ths = str(th) if th is not None and str(th).strip() != "" else "—"
    ww = izd.get("Ширина (мм)")
    hh = izd.get("Высота (мм)")
    dim_s = "—"
    if ww is not None or hh is not None:
        dim_s = "%s × %s мм" % (ww or "—", hh or "—")
    margin = 10
    y = margin
    p.setPen(QColor(21, 101, 192))
    f_title = QFont("Arial", 12, QFont.Bold)
    f_body = QFont("Arial", 11)
    p.setFont(f_title)
    p.drawText(margin, y, w - 2 * margin, 22, Qt.AlignLeft | Qt.TextWordWrap, "Стекло / зеркало")
    y += 26
    p.setFont(f_body)
    p.setPen(QColor(40, 40, 40))
    lh = 18
    header_lines = [
        "Форма: %s   ·   Кол-во: %s шт." % (shape, qty_s),
        "Материал: %s" % mw,
        "Цвет / вариант: %s   ·   Толщина: %s мм" % (col, ths),
        "Размер: %s" % dim_s,
    ]
    if mat.get("Закалка"):
        header_lines.insert(3, "Закалка: да")
    for ln in header_lines:
        p.drawText(margin, y, w - 2 * margin, lh + 8, Qt.AlignLeft | Qt.TextWordWrap, ln)
        y += lh + 4
    sketch = _load_sketch_pixmap_payload(payload)
    draw_top = y + 6
    draw_h = max(60, h - draw_top - margin - 28)
    draw_w = w - 2 * margin
    center_rect = QRect(margin, draw_top, draw_w, draw_h)
    if not sketch.isNull():
        sc = sketch.scaled(
            max(40, center_rect.width() - 12),
            max(40, center_rect.height() - 12),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        p.setPen(QPen(QColor(190, 195, 210), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(center_rect)
        x0 = center_rect.center().x() - sc.width() // 2
        y0 = center_rect.center().y() - sc.height() // 2
        p.drawPixmap(x0, y0, sc)
    else:
        p.setPen(QPen(QColor(100, 140, 190), 2))
        p.setBrush(QBrush(QColor(225, 235, 250)))
        try:
            iw = int(ww) if ww is not None else 0
            ih = int(hh) if hh is not None else 0
        except (TypeError, ValueError):
            iw = ih = 0
        if iw > 0 and ih > 0:
            scale = min(
                (center_rect.width() - 24) / float(iw),
                (center_rect.height() - 24) / float(ih),
                2.8,
            )
            rw = max(48, int(iw * scale))
            rh = max(48, int(ih * scale))
        else:
            rw = min(center_rect.width() - 48, 220)
            rh = min(center_rect.height() - 48, 160)
        rx = center_rect.center().x() - rw // 2
        ry = center_rect.center().y() - rh // 2
        p.drawRect(rx, ry, rw, rh)
        p.setPen(QColor(50, 50, 50))
        p.setFont(QFont("Arial", 9))
        rect_lines = [mw, "толщина: %s мм" % ths, "цвет: %s" % col]
        if mat.get("Закалка"):
            rect_lines.append("закалка")
        p.drawText(
            QRect(rx + 4, ry + 4, rw - 8, rh - 8),
            Qt.AlignCenter | Qt.TextWordWrap,
            "\n".join(rect_lines),
        )
    p.setFont(QFont("Arial", 11, QFont.Bold))
    p.setPen(QColor(21, 101, 192))
    p.drawText(margin, h - margin - 22, w - 2 * margin, 22, Qt.AlignHCenter, dim_s)
    p.end()
    return pm


def _glass_overview_label(payload: dict, w: int, h: int) -> QLabel:
    lab = QLabel()
    lab.setFixedSize(w, h)
    lab.setAlignment(Qt.AlignCenter)
    lab.setPixmap(_glass_schematic_pixmap(payload, w, h))
    lab.setStyleSheet("QLabel { border:1px solid #c5cae9; background:#fafafa; border-radius:8px; }")
    return lab


def _price_table_widget(rows: list[tuple[str, int, str]], position_qty: int = 1) -> QTableWidget:
    qty = max(1, int(position_qty or 1))
    tbl = QTableWidget()
    tbl.setColumnCount(5)
    tbl.setHorizontalHeaderLabels(["Позиция", "Кол-во", "₽/шт", "₽ всего", "Примечание"])
    tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
    tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
    tbl.setRowCount(len(rows))
    for i, (name, rub, note) in enumerate(rows):
        sep = str(name).strip().startswith("—")
        it0 = QTableWidgetItem(str(name))
        total_rub = int(rub or 0)
        if sep:
            qty_txt = "—"
            unit_txt = "—"
            total_txt = "—" if total_rub == 0 else str(total_rub)
        else:
            qty_txt = str(qty)
            unit_txt = str(int(round(float(total_rub) / float(qty)))) if qty > 0 else str(total_rub)
            total_txt = str(total_rub)
        it1 = QTableWidgetItem(qty_txt)
        it2 = QTableWidgetItem(unit_txt)
        it3 = QTableWidgetItem(total_txt)
        it4 = QTableWidgetItem(str(note or ""))
        if sep:
            f = QFont()
            f.setBold(True)
            br = QBrush(QColor(21, 101, 192))
            for it in (it0, it1, it2, it3, it4):
                it.setFont(f)
                it.setForeground(br)
        tbl.setItem(i, 0, it0)
        tbl.setItem(i, 1, it1)
        tbl.setItem(i, 2, it2)
        tbl.setItem(i, 3, it3)
        tbl.setItem(i, 4, it4)
    _table_show_full_no_scroll(tbl)
    return tbl


def _facade_pricing_rows(
    pl: dict,
    conn,
    drill,
    markup_factor: float = 1.0,
    *,
    aux_prices: tuple[dict[str, float], dict[str, float], dict[str, float]] | None = None,
    delivery_prices: dict | None = None,
) -> list[tuple[str, int, str]]:
    """Позиции фасада: профиль, уголки, петли, присадка, уплотнитель, винты; затем стекло по строкам калькулятора."""
    _ensure_blocks_path()
    from calc.order_summary import collect_line_items
    from ui.facade_constants import FITTING_SIDE_LABELS_RU

    def m(x):
        return _money_markup(x, markup_factor)

    pl = pl or {}
    out: list[tuple[str, int, str]] = []
    try:
        w_mm = int(pl.get("Ширина_мм") or 0)
        h_mm = int(pl.get("Высота_мм") or 0)
    except (TypeError, ValueError):
        w_mm, h_mm = 0, 0
    if w_mm <= 0:
        w_mm = 400
    if h_mm <= 0:
        h_mm = 600

    profiles = pl.get("Профили_по_сторонам") or {}
    by_id: dict[int, dict] = {}
    for side, prof in (profiles or {}).items():
        if not isinstance(prof, dict):
            continue
        try:
            pid = int(prof.get("id"))
        except (TypeError, ValueError):
            continue
        price_m = float(prof.get("price_per_meter") or 0)
        length_m = (w_mm / 1000.0) if side in ("top", "bottom") else (h_mm / 1000.0)
        side_cost = length_m * price_m
        row = by_id.get(pid)
        if not row:
            row = {"profile": prof, "length_m_by_side": {}, "total_rub": 0.0}
            by_id[pid] = row
        row["length_m_by_side"][side] = float(length_m)
        row["total_rub"] += side_cost
    profiles_cost_rows = [{"id": pid, **v} for pid, v in by_id.items()]
    profiles_cost_rows.sort(
        key=lambda r: (
            str((r.get("profile") or {}).get("series") or ""),
            str((r.get("profile") or {}).get("name") or ""),
        )
    )
    for row in profiles_cost_rows:
        prof = row.get("profile") or {}
        total_rub = m(row.get("total_rub") or 0)
        lengths_by_side = row.get("length_m_by_side") or {}
        side_parts: list[str] = []
        for sk in ("top", "bottom", "left", "right"):
            if sk in lengths_by_side:
                side_ru = FITTING_SIDE_LABELS_RU.get(sk, sk)
                side_parts.append("%s %.2f п.м." % (side_ru, float(lengths_by_side.get(sk) or 0)))
        ppm = float(prof.get("price_per_meter") or 0)
        name_prof = str(prof.get("name") or prof.get("series") or "Профиль")
        note = "серия: %s; цвет: %s; прайс %s ₽/м; %s" % (
            prof.get("series") or "—",
            prof.get("color") or "—",
            int(round(ppm)),
            "; ".join(side_parts) if side_parts else "—",
        )
        out.append(("Профиль: %s" % name_prof, total_rub, note))

    top = profiles.get("top") if isinstance(profiles, dict) else None
    bottom = profiles.get("bottom") if isinstance(profiles, dict) else None
    left = profiles.get("left") if isinstance(profiles, dict) else None
    right = profiles.get("right") if isinstance(profiles, dict) else None
    corners_qty: dict[str, int] = {"F3-021": 0, "F3-031": 0}

    def _inc_if(a, b):
        if not (a and b and isinstance(a, dict) and isinstance(b, dict)):
            return
        corners_qty[_corner_code_for_profiles(a, b)] += 1

    _inc_if(top, left)
    _inc_if(bottom, left)
    _inc_if(top, right)
    _inc_if(bottom, right)

    corner_prices, seal_prices, screw_prices = (
        aux_prices if aux_prices is not None else _facade_aux_prices()
    )
    for code, qty in corners_qty.items():
        if qty <= 0:
            continue
        p_unit = float(corner_prices.get(code) or 0)
        sub = qty * p_unit
        rub = m(sub)
        out.append(
            (
                "Угловой соединитель %s" % code,
                rub,
                "%s шт. × %s ₽/шт. (прайс)" % (qty, int(round(p_unit))),
            )
        )

    hinge_qty_by_id: dict[int, dict] = {}
    for item in pl.get("Петли") or []:
        if not isinstance(item, dict):
            continue
        hinge = item.get("hinge") or {}
        if not isinstance(hinge, dict):
            continue
        try:
            hid = int(hinge.get("id"))
        except (TypeError, ValueError):
            continue
        qty = int(item.get("quantity") or 0)
        if qty <= 0:
            continue
        price_one = float(hinge.get("price") or 0)
        row = hinge_qty_by_id.get(hid)
        if not row:
            row = {"hinge": hinge, "count": 0, "total_rub": 0.0}
            hinge_qty_by_id[hid] = row
        row["count"] += qty
        row["total_rub"] += qty * price_one
    hinges_sorted = sorted(
        hinge_qty_by_id.items(),
        key=lambda kv: str((kv[1].get("hinge") or {}).get("number") or ""),
    )
    for _hid, row in hinges_sorted:
        hinge = row.get("hinge") or {}
        qty = int(row.get("count") or 0)
        sub = float(row.get("total_rub") or 0)
        rub = m(sub)
        num = str(hinge.get("number") or hinge.get("photo_number") or "").strip()
        nm = str(hinge.get("name") or "петля")
        col_h = str(hinge.get("color") or hinge.get("Цвет") or "").strip()
        ser = str(hinge.get("series") or "").strip()
        note_parts: list[str] = []
        if num:
            note_parts.append("№ %s" % num)
        if col_h:
            note_parts.append("цвет: %s" % col_h)
        if ser:
            note_parts.append(ser)
        price_one = float(hinge.get("price") or 0)
        note_parts.append("%s шт. × %s ₽ (прайс)" % (qty, int(round(price_one))))
        out.append(("Петли: %s" % nm, rub, "; ".join(note_parts)))

    prs = pl.get("Присадка")
    pr_list = prs if isinstance(prs, list) else ([prs] if isinstance(prs, dict) else [])
    for pr in pr_list:
        if not isinstance(pr, dict):
            continue
        side = pr.get("сторона") or pr.get("side")
        if not side:
            continue
        holes = pr.get("отверстия") or pr.get("holes") or []
        sup = pr.get("поставщик_петли") or pr.get("supplier") or "—"
        side_ru = {"top": "верх", "bottom": "низ", "left": "лево", "right": "право"}.get(side, str(side))
        out.append(
            (
                "Присадка (вырезы под петли), %s" % side_ru,
                0,
                "%s отверст.; поставщик петли: %s" % (len(holes), sup),
            )
        )

    seal_key = _payload_seal_variant_key(pl)
    perimeter_m = 2 * (w_mm + h_mm) / 1000.0
    seal_price_per_m = float(seal_prices.get(seal_key) or 0)
    seal_sub = perimeter_m * seal_price_per_m
    seal_rub = m(seal_sub)
    seal_color = (pl.get("Уплотнитель") or {}).get("Цвет") if isinstance(pl.get("Уплотнитель"), dict) else "—"
    out.append(
        (
            "Уплотнитель",
            seal_rub,
            "цвет: %s; периметр %.2f м; %s ₽/м (прайс)"
            % (seal_color or "—", perimeter_m, int(round(seal_price_per_m))),
        )
    )

    screw_key = _payload_screw_variant_key(pl)
    screws_qty = 2 * int(sum(int(x or 0) for x in corners_qty.values()))
    screw_price_one = float(screw_prices.get(screw_key) or 0)
    screws_sub = screws_qty * screw_price_one
    screws_rub = m(screws_sub)
    screw_color = (pl.get("Винты") or {}).get("Цвет") if isinstance(pl.get("Винты"), dict) else "—"
    out.append(
        (
            "Винты (к уголкам)",
            screws_rub,
            "цвет: %s; %s шт. × %s ₽ (прайс)"
            % (screw_color or "—", screws_qty, int(round(screw_price_one))),
        )
    )

    g = pl.get("Стекло") if isinstance(pl.get("Стекло"), dict) else {}
    bs = g.get("blocks_selected") if isinstance(g, dict) else None
    glass_lines: list[tuple[str, int, str]] = []
    if isinstance(bs, dict):
        lr_g, _, _ = collect_line_items(bs, None, conn=conn, drilling_rows_cached=drill)
        for name, rub, det in lr_g:
            if name in ORDER_LEVEL_ROW_NAMES:
                continue
            glass_lines.append((str(name), m(rub), str(det or "")))
    glass_subtotal = sum(int(r[1]) for r in glass_lines)

    if g:
        nm = str(g.get("Название") or "—")
        gc = str(g.get("Цвет") or "—")
        gt = g.get("Толщина (мм)")
        gth = str(gt) if gt is not None and str(gt).strip() != "" else "—"
        bs0 = g.get("blocks_selected") if isinstance(g.get("blocks_selected"), dict) else {}
        izd0 = bs0.get("Изделие") if isinstance(bs0.get("Изделие"), dict) else {}
        if not izd0 and isinstance(bs0.get("Материал"), dict):
            izd0 = bs0["Материал"]
        wmm = izd0.get("Ширина (мм)") if isinstance(izd0, dict) else None
        hmm = izd0.get("Высота (мм)") if isinstance(izd0, dict) else None
        dim_g = "—"
        if wmm is not None or hmm is not None:
            dim_g = "%s × %s мм" % (wmm or "—", hmm or "—")
        out.append(
            (
                _FACADE_GLASS_SPEC_ROW,
                glass_subtotal,
                "%s; цвет: %s; толщина: %s мм; размер: %s" % (nm, gc, gth, dim_g),
            )
        )

    out.append(("— Стекло в проёме (материал и работы) —", 0, "по строкам ниже"))
    out.extend(glass_lines)

    for name, rub, note in _facade_service_money_from_payload(pl, delivery_prices=delivery_prices):
        rub_i = int(rub)
        if _glass_table_row_gets_client_markup(str(name)):
            rub_i = m(rub_i)
        out.append((name, rub_i, note))

    return out


def _glass_pricing_rows(payload: dict, conn, drill, markup_factor: float = 1.0) -> list[tuple[str, int, str]]:
    _ensure_blocks_path()
    from calc.order_summary import collect_line_items

    def m(x):
        return _money_markup(x, markup_factor)

    lr, _, _ = collect_line_items(payload, None, conn=conn, drilling_rows_cached=drill)
    out = []
    for name, rub, det in lr:
        rub_i = int(rub)
        if _glass_table_row_gets_client_markup(str(name)):
            rub_i = m(rub_i)
        out.append((str(name), rub_i, str(det or "")))
    return out


def _product_position_qty(kind: str, payload: dict) -> int:
    if str(kind or "").strip() == "facade":
        try:
            return max(1, int((payload or {}).get("Количество") or 1))
        except (TypeError, ValueError):
            return 1
    izd = (payload or {}).get("Параметры изделия")
    if not isinstance(izd, dict):
        bs = (payload or {}).get("blocks_selected")
        if isinstance(bs, dict):
            izd = bs.get("Изделие") if isinstance(bs.get("Изделие"), dict) else {}
            if not izd and isinstance(bs.get("Материал"), dict):
                izd = bs.get("Материал")
    try:
        return max(1, int((izd or {}).get("Количество (шт)") or 1))
    except (TypeError, ValueError):
        return 1


def _sum_price_rows(rows: list[tuple[str, int, str]]) -> int:
    s = 0
    for name, rub, _ in rows:
        if name == _FACADE_GLASS_SPEC_ROW:
            continue
        s += int(rub)
    return s


def _sum_price_rows_excluding_order_level(rows: list[tuple[str, int, str]]) -> int:
    """Сумма по изделию без строк услуг (доставка, замер, монтаж — отдельно)."""
    return _sum_price_rows_products_only(rows)


def _rows_have_priced_order_level(rows: list[tuple[str, int, str]]) -> bool:
    for name, rub, _ in rows:
        if _is_service_surcharge_row(str(name)) and int(rub or 0):
            return True
    return False


def _order_level_amounts_once_priced_mixed(
    products: list,
    conn,
    drill,
    markup_factor: float,
    delivery_prices,
    facade_aux_cached,
) -> tuple[int, int, int]:
    """Первые ненулевые «Доставка», «Замер (выезд)» и «Монтаж» по порядку изделий."""
    dlv, zam, mon = 0, 0, 0
    for p in products or []:
        pl = p.get("payload")
        if not isinstance(pl, dict):
            continue
        kind = str(p.get("kind") or "glass_mirror").strip() or "glass_mirror"
        if kind == "facade":
            rows = _facade_pricing_rows(
                pl,
                conn,
                drill,
                markup_factor,
                aux_prices=facade_aux_cached,
                delivery_prices=delivery_prices,
            )
        else:
            rows = _glass_pricing_rows(pl, conn, drill, markup_factor)
        for name, rub, _det in rows:
            nm = str(name).strip()
            if nm == "Доставка" and int(rub or 0) and not dlv:
                dlv = int(rub)
            if nm == "Замер (выезд)" and int(rub or 0) and not zam:
                zam = int(rub)
            if nm == "Монтаж" and int(rub or 0) and not mon:
                mon = int(rub)
        if dlv and zam and mon:
            break
    return dlv, zam, mon


def _overview_bundle_order_total_rub(
    products: list,
    *,
    conn,
    drill,
    markup_factor: float,
    delivery_prices,
    facade_aux_cached,
) -> int:
    """
    Итого по заказу в рублях как сумма «Итого по позиции» + доставка/замер один раз.
    Не использовать bundle_grand_total_rub в шапке: там collect_line_items без conn — сумма расходится с SQL-прайсом.
    """
    if not products:
        return 0
    sub_ex = 0
    for p in products:
        pl = p.get("payload")
        if not isinstance(pl, dict):
            continue
        kind = str(p.get("kind") or "glass_mirror").strip() or "glass_mirror"
        if kind == "facade":
            pr_rows = _facade_pricing_rows(
                pl,
                conn,
                drill,
                markup_factor,
                aux_prices=facade_aux_cached,
                delivery_prices=delivery_prices,
            )
        else:
            pr_rows = _glass_pricing_rows(pl, conn, drill, markup_factor)
        sub_ex += int(_sum_price_rows_excluding_order_level(pr_rows))
    dlv, zam, mon = _order_level_amounts_once_priced_mixed(
        products, conn, drill, markup_factor, delivery_prices, facade_aux_cached
    )
    return int(sub_ex) + int(dlv) + int(zam) + int(mon)


def _format_rub_plain(n: int) -> str:
    try:
        v = int(n)
    except (TypeError, ValueError):
        v = 0
    return ("%d" % v).replace(",", " ")


def live_bundle_order_base_total_rub(
    _order_data: dict | None,
    products: list,
    *,
    _conn=None,
    _drill=None,
    _delivery=None,
    _facade_aux=None,
) -> int | None:
    """
    Базовая сумма заказа (без доплат по позициям) в рублях — как «Итого по заказу» в сводке (SQL-прайс).
    None — не удалось посчитать (нет БД и т.п.), тогда можно взять bundle_grand_total_rub.
    Суммы в blocks_calc_json уже с клиентской наценкой при сохранении — здесь markup_factor=1.0.
    Опционально _conn/_drill/_delivery — один раз на пакет пересчёта таблицы заказов.
    """
    if not products:
        return 0
    try:
        from calc.db_postgres import get_raw_connection, fetch_drilling_price_rows
        from calc.delivery_calc import fetch_delivery_prices
    except Exception:
        return None
    conn = _conn
    owns_conn = conn is None
    try:
        if conn is None:
            conn = get_raw_connection()
        drill = _drill if _drill is not None else fetch_drilling_price_rows(conn=conn)
        delivery = _delivery if _delivery is not None else fetch_delivery_prices(conn=conn)
        aux = _facade_aux if _facade_aux is not None else _facade_aux_prices()
        return int(
            _overview_bundle_order_total_rub(
                products,
                conn=conn,
                drill=drill,
                markup_factor=1.0,
                delivery_prices=delivery,
                facade_aux_cached=aux,
            )
        )
    except Exception:
        return None
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def live_bundle_payment_lines_ru(
    _order_data: dict | None,
    products: list,
    *,
    _conn=None,
    _drill=None,
    _delivery=None,
    _facade_aux=None,
) -> list[str] | None:
    """
    Строки «сумма — способ» для полностью оплаченного bundle (аналог bundle_payment_aggregate lines_ru),
    с суммами позиций как в сводке (SQL-прайс). None — ошибка, использовать старые lines_ru.
    """
    if not products:
        return []
    try:
        from calc.db_postgres import get_raw_connection, fetch_drilling_price_rows
        from calc.delivery_calc import fetch_delivery_prices
    except Exception:
        return None
    conn = _conn
    owns_conn = conn is None
    try:
        if conn is None:
            conn = get_raw_connection()
        drill = _drill if _drill is not None else fetch_drilling_price_rows(conn=conn)
        delivery = _delivery if _delivery is not None else fetch_delivery_prices(conn=conn)
        aux = _facade_aux if _facade_aux is not None else _facade_aux_prices()
        by_method: dict[str, int] = {}
        for p in products or []:
            if not product_is_paid(p):
                continue
            m = str(p.get("payment_type") or PAYMENT_UNPAID).strip()
            pl = p.get("payload") if isinstance(p.get("payload"), dict) else {}
            kind = str(p.get("kind") or "glass_mirror").strip() or "glass_mirror"
            if kind == "facade":
                pr_rows = _facade_pricing_rows(
                    pl, conn, drill, 1.0, aux_prices=aux, delivery_prices=delivery
                )
            else:
                pr_rows = _glass_pricing_rows(pl, conn, drill, 1.0)
            amt = int(_sum_price_rows_excluding_order_level(pr_rows))
            by_method[m] = by_method.get(m, 0) + amt
        lines: list[str] = []
        for m, s in sorted(by_method.items(), key=lambda x: -x[1]):
            lab = PAYMENT_TYPE_LABELS_RU.get(m, m)
            lines.append("%s — %s" % (_format_rub_plain(s), lab))
        return lines
    except Exception:
        return None
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _cut_layout_has_drawable_content(lay: dict | None) -> bool:
    """Есть что показать на схеме (не только зарезервированный пустой лист)."""
    if not isinstance(lay, dict):
        return False
    if lay.get("pieces"):
        return True
    if lay.get("business_rects") or lay.get("waste_rects"):
        return True
    return False


def _piece_highlight_for_order(p: dict, order_data: dict | None) -> bool:
    """Деталь относится к этому заказу — для оранжевой подсветки на канвасе."""
    if not isinstance(p, dict):
        return False
    oid = (order_data or {}).get("id")
    try:
        if oid is not None:
            oid_i = int(oid)
            so = p.get("source_order_id")
            if so is not None and int(so) == oid_i:
                return True
    except (TypeError, ValueError):
        pass
    uid = str(p.get("piece_uid") or "").strip()
    if uid and oid is not None:
        try:
            head = uid.split(":", 1)[0]
            if head.isdigit() and int(head) == int(oid):
                return True
        except (TypeError, ValueError):
            pass
    client = str((order_data or {}).get("client_name") or "").strip().lower()
    rec = str(p.get("recipient") or p.get("recipient_text") or "").strip().lower()
    if not client or not rec:
        return False
    if rec == client:
        return True
    if len(client) >= 4 and len(rec) >= 4 and (client in rec or rec in client):
        return True
    return False


def _piece_matches_bundle_product(p: dict, product_id) -> bool:
    """Кусок относится к изделию bundle (id в JSON пакета), по bundle_product_id или piece_uid «order:product:…»."""
    if not isinstance(p, dict) or not product_id:
        return False
    pid = str(product_id).strip()
    if not pid:
        return False
    if str(p.get("bundle_product_id") or "").strip() == pid:
        return True
    uid = str(p.get("piece_uid") or "").strip()
    if not uid:
        return False
    parts = uid.split(":")
    return len(parts) >= 2 and parts[1] == pid


def _piece_belongs_to_mirror_order_id(p: dict, order_id) -> bool:
    """Кусок с этого заказа (по source_order_id или префиксу piece_uid «orderId:…»)."""
    if not isinstance(p, dict) or order_id is None:
        return False
    try:
        oi = int(order_id)
    except (TypeError, ValueError):
        return False
    try:
        so = p.get("source_order_id")
        if so is not None and int(so) == oi:
            return True
    except (TypeError, ValueError):
        pass
    uid = str(p.get("piece_uid") or "").strip()
    if uid:
        head = uid.split(":", 1)[0]
        if head.isdigit() and int(head) == oi:
            return True
    return False


def _layouts_with_order_piece_highlight(
    layouts: list, order_data: dict, highlight_product_id=None
) -> list:
    """Пометить куски флагом _is_order_piece: весь заказ или только одно изделие (оранжевый на схеме)."""
    hp = str(highlight_product_id).strip() if highlight_product_id is not None else ""
    out = []
    for lay in (layouts or []):
        if not isinstance(lay, dict):
            continue
        lay_copy = dict(lay)
        marked_pieces = []
        for p in (lay.get("pieces") or []):
            if not isinstance(p, dict):
                continue
            p2 = dict(p)
            if hp:
                p2["_is_order_piece"] = _piece_matches_bundle_product(p2, hp) and (
                    _piece_belongs_to_mirror_order_id(p2, order_data.get("id"))
                    or _piece_highlight_for_order(p2, order_data)
                )
            else:
                p2["_is_order_piece"] = _piece_highlight_for_order(p2, order_data)
            marked_pieces.append(p2)
        lay_copy["pieces"] = marked_pieces
        out.append(lay_copy)
    return out


class _HoldToDeleteDraftProductButton(QPushButton):
    """«Удалить» для изделия в просчёте: красная заливка ~70% непрозрачности, удержание 1 с → полная непрозрачность и удаление."""

    def __init__(self, *, delete_callback, hold_seconds: float = 1.0, parent=None):
        super().__init__("Удалить", parent)
        self._delete_callback = delete_callback
        self._hold_seconds = max(0.05, float(hold_seconds))
        self._pressing = False
        self._completed = False
        self._start_t = 0.0
        self._alpha0 = int(round(255 * 0.7))
        self._alpha1 = 255
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(
            "Удерживайте 1 секунду, чтобы удалить только это изделие из заказа (не весь заказ)."
        )
        self._apply_alpha(self._alpha0)

    def _apply_alpha(self, a: int) -> None:
        a = max(0, min(255, int(a)))
        b = min(255, a + 35)
        self.setStyleSheet(
            "QPushButton {"
            "  color: rgba(255,255,255,255);"
            "  font-weight: bold;"
            f"  background-color: rgba(211,47,47,{a});"
            f"  border: 1px solid rgba(183,28,28,{b});"
            "  border-radius: 6px;"
            "  padding: 4px 10px;"
            "}"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pressing = True
            self._completed = False
            self._start_t = time.monotonic()
            self._apply_alpha(self._alpha0)
            self._timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._timer.stop()
        if not self._completed and self._pressing:
            self._apply_alpha(self._alpha0)
        self._pressing = False
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self._pressing and not self._completed:
            self._timer.stop()
            self._pressing = False
            self._apply_alpha(self._alpha0)
        super().leaveEvent(event)

    def _tick(self):
        if not self._pressing or self._completed:
            return
        elapsed = time.monotonic() - self._start_t
        ratio = min(1.0, elapsed / self._hold_seconds)
        a = int(round(self._alpha0 + (self._alpha1 - self._alpha0) * ratio))
        self._apply_alpha(a)
        if ratio >= 1.0:
            self._timer.stop()
            self._completed = True
            self._pressing = False
            self._apply_alpha(self._alpha1)
            QTimer.singleShot(0, self._delete_callback)


class GlassOrderOverviewDialog(QDialog):
    _ORDER_STATUS_KEYS = (
        "draft",
        "paid",
        "in_progress",
        "made",
        "shipped",
        "completed",
    )

    @staticmethod
    def _status_choices():
        return [
            (k, order_status_to_ru(k))
            for k in GlassOrderOverviewDialog._ORDER_STATUS_KEYS
        ]

    @staticmethod
    def _order_status_allows_add_products(order_status: str) -> bool:
        """Добавление изделий в заказ — для любого статуса, кроме отгружен/выполнен."""
        st = str(order_status or "").strip().lower()
        return st not in ("shipped", "completed")

    @staticmethod
    def _services_cta_flags(order_status: str, products: list) -> tuple[bool, tuple[bool, bool, bool]]:
        """
        (показывать ли кнопку, (замер, монтаж, доставка) — какие услуги ещё можно добавить в UI).
        Замер — только при статусах **заказа** «просчёт» (draft) и «оплачен» (paid), не по статусу изделия.
        Начиная с «в работе» (in_progress) и далее — замер не предлагаем. Завершённый/отгруженный заказ — ничего.
        """
        st = str(order_status or "").strip().lower()
        if st in ("shipped", "completed"):
            return False, (False, False, False)
        can_measure = st in ("draft", "paid")
        has_m = has_i = has_d = False
        sep_delivery = False
        for pr in products or []:
            pl = pr.get("payload") if isinstance(pr.get("payload"), dict) else {}
            zb = pl.get("Замер") if isinstance(pl.get("Замер"), dict) else {}
            if zb.get("Активирован"):
                zd = zb.get("Данные") if isinstance(zb.get("Данные"), dict) else {}
                has_m = has_m or bool(zd.get("Замер"))
                has_i = has_i or bool(zd.get("Монтаж"))
                has_d = has_d or bool(zd.get("Доставка"))
            db = pl.get("Доставка") if isinstance(pl.get("Доставка"), dict) else {}
            if db.get("Активирован"):
                sep_delivery = True
        want_m = bool(can_measure and not has_m)
        want_i = not has_i
        want_d = not (has_d or sep_delivery)
        return (bool(want_m or want_i or want_d), (want_m, want_i, want_d))

    @staticmethod
    def _offer_cta_services(order_status: str, products: list) -> bool:
        ok, _flags = GlassOrderOverviewDialog._services_cta_flags(order_status, products)
        return ok

    @staticmethod
    def _format_order_total_header_html(products_sub_rub: int, services_rub: int, surcharge_total: int) -> str:
        tot_no_srv = int(products_sub_rub) + int(surcharge_total)
        main = "<span style='font-size:13pt'><b>Итого по заказу: %s ₽</b></span>" % tot_no_srv
        if int(surcharge_total or 0) > 0:
            main += (
                " <span style='font-size:10pt; color:#546e7a;'>(изделия %s + доплаты %s; "
                "без услуг замер/доставка/монтаж)</span>"
                % (int(products_sub_rub), int(surcharge_total))
            )
        else:
            main += " <span style='font-size:10pt; color:#546e7a;'>(только изделия, без услуг)</span>"
        parts = [main]
        if int(services_rub or 0) > 0:
            parts.append(
                "<br/><span style='font-size:10pt;color:#455a64'>Услуги (замер, доставка, монтаж): "
                "<b>%s ₽</b> — в сумму выше не включены.</span>" % int(services_rub)
            )
        return "".join(parts)

    def _build_price_core_frame(self, pid: str, kind: str, pl0: dict, pr_rows: list) -> QFrame:
        price_core = QFrame()
        price_core.setObjectName("ov_price_core_%s" % pid)
        pcl = QVBoxLayout(price_core)
        pcl.setContentsMargins(0, 0, 0, 0)
        pcl.setSpacing(6)
        pcl.addWidget(QLabel("<b>Смета по позиции (изделия)</b>"))
        pos_qty = _product_position_qty(kind, pl0)
        _prod_rows, _srv_rows = _split_pricing_rows_product_vs_services(pr_rows)
        pcl.addWidget(_price_table_widget(_prod_rows, position_qty=pos_qty))
        if _srv_rows:
            pcl.addWidget(QLabel("<b>Услуги по позиции</b>"))
            pcl.addWidget(_price_table_widget(_srv_rows, position_qty=1))
        pos_sum_all = int(_sum_price_rows_products_only(pr_rows))
        qv = max(1, int(pos_qty or 1))
        pos_sum = int(round(float(pos_sum_all) / float(qv))) if qv > 1 else pos_sum_all
        pcl.addWidget(
            QLabel(
                "<span style='font-size:12pt'><b>Итого по позиции (без услуг): %s ₽</b></span>" % pos_sum
            )
        )
        if qv > 1:
            pcl.addWidget(
                QLabel(
                    "<span style='font-size:11pt; color:#1b5e20;'><b>Итого за %s шт (без услуг): %s ₽</b></span>"
                    % (qv, pos_sum_all)
                )
            )
        return price_core

    def _update_order_header_totals_from_live_bundle(self) -> None:
        lbl = getattr(self, "_lbl_tot_header", None)
        if lbl is None:
            return
        try:
            _ver, products = parse_bundle(self._live_blocks_json)
        except Exception:
            return
        _sur_total = int((bundle_surcharge_aggregate(products) or {}).get("total_amount") or 0)
        _conn = None
        try:
            from calc.db_postgres import get_raw_connection, fetch_drilling_price_rows
            from calc.delivery_calc import fetch_delivery_prices

            _conn = get_raw_connection()
            _drill = fetch_drilling_price_rows(conn=_conn) if _conn else fetch_drilling_price_rows()
            _delivery_prices = fetch_delivery_prices(conn=_conn) if _conn else fetch_delivery_prices()
            _facade_aux_cached = _facade_aux_prices()
            _sub_prod, _srv_order = _bundle_products_subtotal_and_services_rub(
                products,
                conn=_conn,
                drill=_drill,
                markup_factor=1.0,
                delivery_prices=_delivery_prices,
                facade_aux_cached=_facade_aux_cached,
            )
            lbl.setText(
                GlassOrderOverviewDialog._format_order_total_header_html(
                    _sub_prod, _srv_order, _sur_total
                )
            )
        except Exception:
            pass
        finally:
            if _conn is not None:
                try:
                    _conn.close()
                except Exception:
                    pass
        btn = getattr(self, "_btn_top_services_cta", None)
        if btn is not None:
            ost = str(self._order_data.get("status") or "")
            oid = self._order_data.get("id")
            btn.setVisible(
                bool(oid)
                and (not self._summary_only)
                and GlassOrderOverviewDialog._offer_cta_services(ost, products)
            )

    def _rebuild_overview_price_core_blocks(self) -> None:
        try:
            _ver, products = parse_bundle(self._live_blocks_json)
        except Exception:
            return
        _conn = None
        try:
            from calc.db_postgres import get_raw_connection, fetch_drilling_price_rows
            from calc.delivery_calc import fetch_delivery_prices

            _conn = get_raw_connection()
            _drill = fetch_drilling_price_rows(conn=_conn) if _conn else fetch_drilling_price_rows()
            _delivery_prices = fetch_delivery_prices(conn=_conn) if _conn else fetch_delivery_prices()
            _facade_aux_cached = _facade_aux_prices()
            for pr in products:
                pid = str(pr.get("id") or "")
                if not pid:
                    continue
                kind = str(pr.get("kind") or "glass_mirror").strip() or "glass_mirror"
                pl0 = pr.get("payload") or {}
                if not isinstance(pl0, dict):
                    pl0 = {}
                if kind == "facade":
                    pr_rows = _facade_pricing_rows(
                        pl0,
                        _conn,
                        _drill,
                        1.0,
                        aux_prices=_facade_aux_cached,
                        delivery_prices=_delivery_prices,
                    )
                else:
                    pr_rows = _glass_pricing_rows(pl0, _conn, _drill, 1.0)
                old = self.findChild(QFrame, "ov_price_core_%s" % pid)
                if old is None:
                    continue
                par = old.parent()
                lay = par.layout()
                if lay is None:
                    continue
                ix = lay.indexOf(old)
                if ix < 0:
                    continue
                lay.removeWidget(old)
                old.deleteLater()
                new_fr = self._build_price_core_frame(pid, kind, pl0, pr_rows)
                lay.insertWidget(ix, new_fr)
        finally:
            if _conn is not None:
                try:
                    _conn.close()
                except Exception:
                    pass

    def _on_add_delivery_install_services(self):
        """Диалог как при расчёте фасада: замер / доставка / монтаж → запись в bundle и синк blocks_zamer."""
        if self._summary_only:
            return
        oid = self._order_data.get("id")
        if oid is None:
            return
        if not self._order_data.get("client_id"):
            QMessageBox.warning(
                self,
                "Клиент",
                "Привяжите к заказу клиента из справочника — нужен адрес и контакты для услуг.",
            )
            return
        try:
            from ui.facade_order_dialog import _FacadeServicesDialog
        except Exception as e:
            QMessageBox.warning(self, "Услуги", "Не удалось открыть окно услуг: %s" % e)
            return
        ver, prods = parse_bundle(self._live_blocks_json)
        if not prods:
            return
        target_idx = 0
        best = None
        for i, pr in enumerate(prods):
            pl = pr.get("payload") if isinstance(pr.get("payload"), dict) else {}
            zb = pl.get("Замер") if isinstance(pl.get("Замер"), dict) else {}
            if zb.get("Активирован"):
                best = i
                break
        if best is not None:
            target_idx = best
        else:
            for i, pr in enumerate(prods):
                if str(pr.get("kind") or "").strip() == "facade":
                    target_idx = i
                    break
        pr = prods[target_idx]
        pl = copy.deepcopy(pr.get("payload")) if isinstance(pr.get("payload"), dict) else {}
        zblk = copy.deepcopy(pl.get("Замер")) if isinstance(pl.get("Замер"), dict) else {"Активирован": False, "Данные": None}
        dblk = copy.deepcopy(pl.get("Доставка")) if isinstance(pl.get("Доставка"), dict) else {"Активирован": False, "Данные": None}
        proxy = _OverviewServicesClientProxy(self._order_data)
        ost = str(self._order_data.get("status") or "")
        _v2, prods2 = parse_bundle(self._live_blocks_json)
        _cta_ok, svc_wants = GlassOrderOverviewDialog._services_cta_flags(ost, prods2)
        if not _cta_ok:
            QMessageBox.information(self, "Услуги", "Для этого заказа сейчас нечего добавлять по услугам.")
            return
        dlg = _FacadeServicesDialog(proxy, zblk, dblk, self, order_status=ost, service_wants=svc_wants)
        if dlg.exec_() != QDialog.Accepted:
            return
        zb, db = dlg.blocks()
        pl["Замер"] = zb
        pl["Доставка"] = db
        pr["payload"] = pl
        prods[target_idx] = pr
        new_j = bundle_to_json(ver, prods)
        self._set_live_bundle_json(new_j)
        self._update_order_header_totals_from_live_bundle()
        self._rebuild_overview_price_core_blocks()
        QMessageBox.information(self, "Сохранено", "Услуги записаны в заказ.")

    def _save_product_status(self, order_id, product_id: str, status_key: str):
        if order_id is None:
            return False
        raw2 = self._live_blocks_json
        if not raw2 or not str(raw2).strip():
            return False
        ver, prods = parse_bundle(raw2)
        changed = False
        pid = str(product_id or "").strip()
        st = str(status_key or "").strip() or "draft"
        for p in prods:
            if str(p.get("id") or "").strip() == pid:
                p["status"] = st
                changed = True
                break
        if not changed:
            return False
        self._set_live_bundle_json(bundle_to_json(ver, prods))
        return True

    def _open_client_card(self):
        cid = self._order_data.get("client_id")
        if not cid:
            return
        from ui.clients_dialog import ClientCardDialog

        cname = str(self._order_data.get("client_name") or "—")
        ClientCardDialog(int(cid), cname, self).exec_()

    def _open_saved_scheme_view_only(self, highlight_product_id=None):
        oid = self._order_data.get("id")
        if oid is None:
            return
        try:
            oid_int = int(oid)
        except (TypeError, ValueError):
            return
        hp = str(highlight_product_id).strip() if highlight_product_id is not None else ""
        layouts_draw: list = []
        layouts_any: list = []
        try:
            for lay in db_models.get_cut_layouts_for_overview(oid_int) or []:
                if not isinstance(lay, dict):
                    continue
                layouts_any.append(lay)
                if _cut_layout_has_drawable_content(lay):
                    layouts_draw.append(lay)
        except Exception:
            layouts_draw = []
            layouts_any = []
        layouts = layouts_draw if layouts_draw else layouts_any
        if not layouts:
            QMessageBox.information(
                self,
                "Схема",
                "Нет сохранённого раскроя в базе.",
            )
            return
        layouts = _layouts_with_order_piece_highlight(
            layouts, self._order_data, highlight_product_id=(hp or None)
        )
        # Тот же полноценный диалог, что и «Открыть схему во весь экран»: PDF, этикетки, легенда, все листы.
        p = self.parent()
        while p is not None:
            _open_fn = getattr(p, "_open_scheme_with_layouts", None)
            if callable(_open_fn):
                _open_fn(dict(self._order_data), layouts)
                return
            p = p.parent()

        from mirror_cut_sys_path import mirror_cut_imports_first

        with mirror_cut_imports_first():
            from ui.cutting_result_dialog import CuttingResultDialog

            od = dict(self._order_data)
            dlg = CuttingResultDialog(layouts, od, self, preview_mode=False, view_only=True)
            wired_pdf = False
            p2 = self.parent()
            while p2 is not None:
                if hasattr(p2, "_open_pdf") and hasattr(p2, "_open_label"):
                    dlg.btn_pdf.clicked.connect(
                        lambda _=False, _od=od, _d=dlg, _mw=p2: _mw._open_pdf(
                            _od, layouts_getter=lambda: _d.layouts
                        )
                    )
                    dlg.print_labels_requested.connect(lambda _od=od, _mw=p2: _mw._open_label(_od))
                    wired_pdf = True
                    break
                p2 = p2.parent()
            if not wired_pdf:
                dlg.btn_pdf.setVisible(False)
                dlg.btn_label.setVisible(False)
            dlg.exec_()

    def __init__(self, order_data, parent=None, *, summary_only=False):
        super().__init__(parent)
        self._summary_only = bool(summary_only)
        # Если True — после закрытия окна главная таблица должна перечитать заказы (см. MainWindow._on_order_tile_click).
        # Раньше список всегда перезагружался после любого просмотра → долгий _load_orders() и «подвисание» при закрытии.
        self._main_orders_changed = False
        self._overview_shutting_down = False
        self._order_data = dict(order_data or {})
        oid = self._order_data.get("id")
        if oid is not None:
            raw0 = self._order_data.get("blocks_calc_json")
            raw_ok = raw0 is not None and str(raw0).strip()
            st0 = self._order_data.get("status")
            st_ok = st0 is not None and str(st0).strip() != ""
            need_fill = not (raw_ok and st_ok)
            if need_fill or (not self._summary_only):
                try:
                    fr = db_models.get_order(int(oid))
                    if isinstance(fr, dict):
                        keys = (
                            "status",
                            "client_name",
                            "client_id",
                            "blocks_calc_json",
                            "k_number",
                            "accepted_at",
                            "created_at",
                        )
                        if not self._summary_only:
                            for k in keys:
                                if fr.get(k) is not None:
                                    self._order_data[k] = fr[k]
                        elif need_fill:
                            for k in keys:
                                if fr.get(k) is not None:
                                    self._order_data[k] = fr[k]
                except Exception:
                    pass
        if self._summary_only:
            self.setWindowTitle("Сводка просчёта — заказ № %s" % (oid or "—"))
        else:
            self.setWindowTitle("Заказ № %s — сводка по изделиям" % (oid or "—"))
        apply_window_icon(self)

        _bc0 = self._order_data.get("blocks_calc_json")
        self._live_blocks_json = str(_bc0) if _bc0 is not None else ""
        self._last_written_json = self._live_blocks_json
        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(85)
        self._persist_timer.timeout.connect(self._flush_blocks_persist)
        self._persist_pool = QThreadPool(self)
        self._persist_pool.setMaxThreadCount(1)
        self._persist_signals = _BlocksPersistSignals(self)
        self._persist_signals.ok.connect(self._on_blocks_persist_ok, Qt.QueuedConnection)
        self._persist_signals.err.connect(self._on_blocks_persist_err, Qt.QueuedConnection)
        self._write_inflight = False

        root = QVBoxLayout(self)

        _ver, products = parse_bundle(self._live_blocks_json)
        headline = bundle_product_headline(products)
        _base_total = bundle_grand_total_rub(products)
        _sur_total = int((bundle_surcharge_aggregate(products) or {}).get("total_amount") or 0)
        total = int(_base_total) + _sur_total
        order_status = str(self._order_data.get("status") or "")

        top = QHBoxLayout()
        top.addWidget(QLabel("<b>%s</b>" % GlassOrderOverviewDialog._esc(headline)))
        top.addWidget(QLabel("· клиент:"))
        client_name = str(client_line(self._order_data) or "—")
        if self._order_data.get("client_id"):
            client_btn = QPushButton(client_name)
            client_btn.setFlat(True)
            client_btn.setStyleSheet(
                "QPushButton { color:#1565c0; font-weight:600; text-align:left; border:none; padding:0 2px; }"
                "QPushButton:hover { color:#0d47a1; text-decoration:underline; }"
            )
            client_btn.setCursor(Qt.PointingHandCursor)
            client_btn.clicked.connect(self._open_client_card)
            client_btn.setToolTip("Открыть карточку клиента")
            top.addWidget(client_btn)
        else:
            top.addWidget(
                QLabel("<span style='color:#607d8b'>%s</span>" % GlassOrderOverviewDialog._esc(client_name))
            )
        if not self._summary_only:
            top.addWidget(
                QLabel("Статус: <b>%s</b>" % GlassOrderOverviewDialog._esc(order_status_to_ru(order_status)))
            )
        top.addStretch()

        tot_html = GlassOrderOverviewDialog._format_order_total_header_html(int(_base_total), 0, _sur_total)
        lbl_tot = QLabel(tot_html)
        self._lbl_tot_header = lbl_tot
        top.addWidget(lbl_tot)
        root.addLayout(top)
        has_facade = any(str(p.get("kind") or "").strip() == "facade" for p in products)
        has_glass_positions = order_bundle_has_cuttable_glass(products, db_models)
        can_edit_bundle = order_status_allows_bundle_edit(order_status)
        measure_lock_reason = None
        if oid is not None:
            try:
                measure_lock_reason = db_models.get_order_measure_lock_reason(int(oid))
            except Exception:
                measure_lock_reason = None
        if measure_lock_reason:
            can_edit_bundle = False
        if self._summary_only:
            can_edit_bundle = False
        can_edit_surcharge = (
            (not self._summary_only)
            and str(order_status or "").strip().lower() != "completed"
            and not measure_lock_reason
        )
        can_add_products = (
            (not self._summary_only)
            and oid is not None
            and GlassOrderOverviewDialog._order_status_allows_add_products(order_status)
        )
        can_print = order_status_allows_production_print(order_status)
        if self._summary_only:
            can_print = False
        try:
            oid_int = int(oid) if oid is not None else None
        except (TypeError, ValueError):
            oid_int = None

        has_cut = False
        has_drawable_scheme = False
        order_has_scheme_created = any(
            str(pr.get("cut_scheme_status") or "").strip() == CUT_SCHEME_CREATED for pr in (products or [])
        )
        if oid_int:
            try:
                cut_rows, cut_host_oid = db_models.get_cut_results_effective_for_order(oid_int)
                if cut_host_oid != oid_int:
                    cut_rows = [
                        r
                        for r in (cut_rows or [])
                        if db_models.cut_layout_has_piece_for_source_order((r or {}).get("layout"), oid_int)
                    ]
                for r in cut_rows or []:
                    lay = (r or {}).get("layout")
                    if isinstance(lay, dict) and (
                        lay.get("pieces")
                        or lay.get("business_rects")
                        or lay.get("sheet_width")
                        or lay.get("sheet_height")
                    ):
                        has_cut = True
                    if _cut_layout_has_drawable_content(lay):
                        has_drawable_scheme = True
            except Exception:
                has_cut = False
                has_drawable_scheme = False

        cta_delivery_install = (
            bool(oid_int)
            and (not self._summary_only)
            and GlassOrderOverviewDialog._offer_cta_services(order_status, products)
        )
        if cta_delivery_install:
            spacer_cta = QWidget()
            spacer_cta.setFixedHeight(10)
            root.addWidget(spacer_cta)
            row_top_sv = QHBoxLayout()
            row_top_sv.setContentsMargins(0, 4, 0, 8)
            row_top_sv.addStretch(1)
            btn_top_sv = QPushButton("+ доставка | монтаж | замер")
            btn_top_sv.setToolTip(
                "Добавить или изменить доставку, монтаж и замер в расчёте заказа; "
                "данные уйдут в таблицу заказов и на портал по текущим правилам."
            )
            btn_top_sv.setStyleSheet(
                "QPushButton {"
                "  background: #e3f2fd;"
                "  color: #0d47a1;"
                "  font-weight: bold;"
                "  padding: 10px 18px;"
                "  border-radius: 8px;"
                "  border: 1px solid #64b5f6;"
                "}"
                "QPushButton:hover { background: #bbdefb; border-color: #42a5f5; }"
                "QPushButton:pressed { background: #90caf9; }"
                "QPushButton:disabled { background: #eceff1; color: #90a4ae; border-color: #cfd8dc; }"
            )
            btn_top_sv.clicked.connect(self._on_add_delivery_install_services)
            # Блокировка по замеру (нет файлов на портале) не должна отключать доставку/монтаж —
            # диалог услуг сам скрывает уже выбранные пункты; второй замер не добавить без want_m.
            if measure_lock_reason:
                btn_top_sv.setToolTip(
                    btn_top_sv.toolTip()
                    + "\n\n"
                    + str(measure_lock_reason)
                    + " Редактирование изделий и расчёта по-прежнему ограничено до загрузки замера."
                )
            row_top_sv.addWidget(btn_top_sv)
            row_top_sv.addStretch(1)
            root.addLayout(row_top_sv)
            self._btn_top_services_cta = btn_top_sv
        else:
            self._btn_top_services_cta = None

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        vlay = QVBoxLayout(holder)
        vlay.setSpacing(8)

        if not self._summary_only:
            portal_slot = QWidget(holder)
            portal_slot_lay = QVBoxLayout(portal_slot)
            portal_slot_lay.setContentsMargins(0, 0, 0, 0)
            portal_slot_lay.setSpacing(0)
            portal_stub = QLabel("Загрузка данных портала...")
            portal_stub.setStyleSheet("color:#546e7a; padding:4px 2px;")
            portal_slot_lay.addWidget(portal_stub)
            vlay.addWidget(portal_slot)

            self._portal_slot_parent = portal_slot
            self._portal_slot_lay = portal_slot_lay

            def _reload_portal_fulfillment_block():
                if getattr(self, "_overview_shutting_down", False):
                    return
                lay = getattr(self, "_portal_slot_lay", None)
                if lay is None:
                    return
                oid = self._order_data.get("id")
                if oid:
                    try:
                        fr = db_models.get_order(int(oid))
                        if isinstance(fr, dict):
                            if fr.get("blocks_calc_json") is not None:
                                self._order_data["blocks_calc_json"] = fr.get("blocks_calc_json")
                            self._live_blocks_json = str(fr.get("blocks_calc_json") or "")
                    except Exception:
                        pass
                while lay.count():
                    it = lay.takeAt(0)
                    w = it.widget()
                    if w:
                        w.deleteLater()
                stub = QLabel("Загрузка данных портала...")
                stub.setStyleSheet("color:#546e7a; padding:4px 2px;")
                lay.addWidget(stub)

                def _load_again():
                    if getattr(self, "_overview_shutting_down", False):
                        return
                    try:
                        from ui.zamer_portal_fulfillment import portal_fulfillment_block_widget

                        _portal_w = portal_fulfillment_block_widget(
                            self._order_data,
                            self._portal_slot_parent,
                            on_remote_mutated=_reload_portal_fulfillment_block,
                        )
                        if _portal_w:
                            lay.replaceWidget(stub, _portal_w)
                            stub.deleteLater()
                            self._update_order_header_totals_from_live_bundle()
                            self._rebuild_overview_price_core_blocks()
                            self._notify_main_window_portal_bundle_changed()
                            return
                    except Exception:
                        pass
                    stub.deleteLater()

                QTimer.singleShot(0, _load_again)

            def _load_portal_block_deferred():
                if getattr(self, "_overview_shutting_down", False):
                    return
                try:
                    from ui.zamer_portal_fulfillment import portal_fulfillment_block_widget

                    _portal_w = portal_fulfillment_block_widget(
                        self._order_data,
                        self._portal_slot_parent,
                        on_remote_mutated=_reload_portal_fulfillment_block,
                    )
                    if _portal_w:
                        portal_slot_lay.replaceWidget(portal_stub, _portal_w)
                        portal_stub.deleteLater()
                        self._update_order_header_totals_from_live_bundle()
                        self._rebuild_overview_price_core_blocks()
                        return
                except Exception:
                    pass
                portal_stub.deleteLater()

            QTimer.singleShot(0, _load_portal_block_deferred)

        if not products:
            br = QTextBrowser()
            br.setHtml("<p>Нет сохранённых изделий. Нажмите «+ Изделие».</p>")
            vlay.addWidget(br)
        else:
            use_glass_sql = True
            _conn = None
            _drill = None
            if use_glass_sql:
                from calc.db_postgres import get_raw_connection, fetch_drilling_price_rows
                from calc.delivery_calc import fetch_delivery_prices

                _conn = get_raw_connection()
                _drill = fetch_drilling_price_rows(conn=_conn) if _conn else fetch_drilling_price_rows()
                _delivery_prices = fetch_delivery_prices(conn=_conn) if _conn else fetch_delivery_prices()
            else:
                _delivery_prices = None
            try:
                _facade_aux_cached = _facade_aux_prices()
                try:
                    _sub_prod, _srv_order = _bundle_products_subtotal_and_services_rub(
                        products,
                        conn=_conn,
                        drill=_drill,
                        markup_factor=1.0,
                        delivery_prices=_delivery_prices,
                        facade_aux_cached=_facade_aux_cached,
                    )
                    lbl_tot.setText(
                        GlassOrderOverviewDialog._format_order_total_header_html(
                            _sub_prod, _srv_order, _sur_total
                        )
                    )
                except Exception:
                    pass
                for idx, pr in enumerate(products):
                    pid = str(pr.get("id") or "")
                    if idx > 0:
                        sep = QFrame()
                        sep.setFrameShape(QFrame.HLine)
                        sep.setStyleSheet("color:#bbb; max-height:2px;")
                        vlay.addWidget(sep)

                    p_status = str(pr.get("status") or order_status or "")
                    kind = str(pr.get("kind") or "glass_mirror").strip() or "glass_mirror"
                    pl0 = pr.get("payload") or {}
                    if not isinstance(pl0, dict):
                        pl0 = {}

                    box = QFrame()
                    box.setFrameShape(QFrame.StyledPanel)
                    box.setStyleSheet("QFrame { background:#fff; border:1px solid #e0e0e0; border-radius:8px; }")
                    bl = QVBoxLayout(box)
                    bl.setSpacing(6)

                    if self._summary_only:
                        title_simple = QLabel(
                            "<b>Изделие %d</b> · %s"
                            % (
                                idx + 1,
                                GlassOrderOverviewDialog._esc(
                                    "фасад" if kind == "facade" else "стекло / зеркало"
                                ),
                            )
                        )
                        title_simple.setStyleSheet("color:#1565c0;")
                        bl.addWidget(title_simple)
                    else:
                        title = QLabel(
                            "<b>Изделие %d</b> · %s · статус: <b>%s</b>"
                            % (
                                idx + 1,
                                GlassOrderOverviewDialog._esc(
                                    "фасад" if kind == "facade" else "стекло / зеркало"
                                ),
                                GlassOrderOverviewDialog._esc(order_status_to_ru(p_status)),
                            )
                        )
                        title.setStyleSheet("color:#1565c0;")
                        title_row = QHBoxLayout()
                        title_row.addWidget(title, 1)
                        if can_edit_bundle and oid is not None and pid:
                            status_combo = _OverviewComboBox()
                            for skey, stext in self._status_choices():
                                status_combo.addItem(stext, skey)
                            cur_status_idx = status_combo.findData(p_status)
                            if cur_status_idx < 0:
                                cur_status_idx = status_combo.findData(order_status or "draft")
                            status_combo.setCurrentIndex(max(0, cur_status_idx))
                            if measure_lock_reason:
                                status_combo.setToolTip(measure_lock_reason)
                            else:
                                status_combo.setToolTip("Изменить статус изделия")

                            def _save_product_status(
                                _i,
                                _oid=oid,
                                _pid=pid,
                                _kind=kind,
                                _idx=idx,
                                _cb=status_combo,
                                _title=title,
                            ):
                                if not self._save_product_status(
                                    _oid, _pid, str(_cb.currentData() or "draft")
                                ):
                                    return
                                _title.setText(
                                    "<b>Изделие %d</b> · %s · статус: <b>%s</b>"
                                    % (
                                        int(_idx) + 1,
                                        GlassOrderOverviewDialog._esc(
                                            "фасад" if _kind == "facade" else "стекло / зеркало"
                                        ),
                                        GlassOrderOverviewDialog._esc(
                                            order_status_to_ru(str(_cb.currentData() or "draft"))
                                        ),
                                    )
                                )

                            status_combo.currentIndexChanged.connect(_save_product_status)
                            title_row.addWidget(status_combo, 0)
                        if (
                            oid is not None
                            and pid
                            and not measure_lock_reason
                            and str(p_status).strip().lower() == "draft"
                        ):

                            def _do_delete_draft(_pid=pid):
                                self._on_delete_product(str(_pid), confirm=False)

                            title_row.addWidget(
                                _HoldToDeleteDraftProductButton(
                                    delete_callback=_do_delete_draft,
                                    hold_seconds=1.0,
                                    parent=self,
                                ),
                                0,
                            )
                        bl.addLayout(title_row)

                        if can_edit_bundle:
                            pay_row = QHBoxLayout()
                            pay_row.addWidget(QLabel("Оплата по позиции:"))
                            pay_combo = _OverviewComboBox()
                            pay_order = [
                                PAYMENT_UNPAID,
                                PAYMENT_COD,
                                PAYMENT_BANK,
                                PAYMENT_QR,
                                PAYMENT_CASH,
                                PAYMENT_CARD,
                            ]
                            for pk in pay_order:
                                pay_combo.addItem(PAYMENT_TYPE_LABELS_RU.get(pk, pk), pk)
                            cur_pay = str(pr.get("payment_type") or PAYMENT_UNPAID).strip() or PAYMENT_UNPAID
                            pay_combo.blockSignals(True)
                            ix = pay_combo.findData(cur_pay)
                            pay_combo.setCurrentIndex(max(0, ix))
                            pay_combo.blockSignals(False)
                            pay_enabled = str(p_status).strip().lower() == "paid"
                            pay_combo.setEnabled(pay_enabled)
                            if measure_lock_reason:
                                pay_combo.setToolTip(measure_lock_reason)
                            elif not pay_enabled:
                                pay_combo.setToolTip("Доступно после статуса «Оплачен»")
                            else:
                                pay_combo.setToolTip("Способ оплаты по этой позиции")

                            def _save_pay(_i, _oid=oid, _pid=pid, _cb=pay_combo):
                                if _oid is None:
                                    return
                                pk2 = _cb.currentData()
                                new_j = set_product_payment_type(
                                    self._live_blocks_json, str(_pid), str(pk2 or PAYMENT_UNPAID)
                                )
                                self._set_live_bundle_json(new_j)

                            pay_combo.currentIndexChanged.connect(_save_pay)
                            pay_row.addWidget(pay_combo, 1)
                            bl.addLayout(pay_row)

                        if can_edit_surcharge:
                            surcharge_row = QHBoxLayout()
                            surcharge_row.addWidget(QLabel("Доплата по позиции:"))
                            surcharge_edit = QLineEdit()
                            surcharge_edit.setPlaceholderText("0")
                            surcharge_edit.setToolTip("Сумма доплаты по этой позиции в рублях")
                            surcharge_edit.setMaximumWidth(120)
                            surcharge_edit.setText(str(int(pr.get("surcharge_amount") or 0)))
                            surcharge_row.addWidget(surcharge_edit)
                            surcharge_row.addWidget(QLabel("₽"))
                            surcharge_paid_cb = QCheckBox("Доплата произведена")
                            surcharge_paid_cb.setChecked(bool(pr.get("surcharge_paid") or False))
                            surcharge_row.addWidget(surcharge_paid_cb)
                            surcharge_row.addWidget(QLabel("Способ доплаты:"))
                            surcharge_combo = _OverviewComboBox()
                            surcharge_pay_order = [
                                PAYMENT_COD,
                                PAYMENT_BANK,
                                PAYMENT_QR,
                                PAYMENT_CASH,
                                PAYMENT_CARD,
                            ]
                            for pk in surcharge_pay_order:
                                surcharge_combo.addItem(PAYMENT_TYPE_LABELS_RU.get(pk, pk), pk)
                            cur_surcharge_pay = (
                                str(pr.get("surcharge_payment_type") or PAYMENT_UNPAID).strip()
                                or PAYMENT_UNPAID
                            )
                            sx = surcharge_combo.findData(cur_surcharge_pay)
                            surcharge_combo.setCurrentIndex(max(0, sx if sx >= 0 else 0))
                            surcharge_row.addWidget(surcharge_combo, 1)
                            bl.addLayout(surcharge_row)

                            def _save_surcharge(
                                _oid=oid,
                                _pid=pid,
                                _ed=surcharge_edit,
                                _cb_paid=surcharge_paid_cb,
                                _cb_type=surcharge_combo,
                            ):
                                if _oid is None:
                                    return
                                try:
                                    amt = int((_ed.text() or "0").strip())
                                except Exception:
                                    amt = 0
                                amt = max(0, amt)
                                new_j = set_product_surcharge(
                                    self._live_blocks_json,
                                    str(_pid),
                                    amt,
                                    bool(_cb_paid.isChecked()),
                                    str(_cb_type.currentData() or PAYMENT_UNPAID),
                                )
                                self._set_live_bundle_json(new_j)

                            surcharge_edit.editingFinished.connect(_save_surcharge)
                            surcharge_paid_cb.stateChanged.connect(lambda _st: _save_surcharge())
                            surcharge_combo.currentIndexChanged.connect(lambda _i: _save_surcharge())

                        chip_row = QHBoxLayout()
                        cst = str(pr.get("cut_scheme_status") or "").strip()
                        if cst == CUT_SCHEME_CREATED:
                            sch = QLabel("Схема раскроя создана")
                            sch.setStyleSheet(
                                "background:#e8f5e9; color:#1b5e20; font-weight:bold; padding:4px 10px; "
                                "border-radius:6px; border:1px solid #66bb6a;"
                            )
                            chip_row.addWidget(sch)
                        pgs = str(pr.get("production_glass_status") or "").strip()
                        if pgs == PRODUCTION_GLASS_MADE:
                            if kind == "facade":
                                glb = QLabel("Стекло/зеркало изготовлено")
                            else:
                                glb = QLabel("Выполнено")
                            glb.setStyleSheet(
                                "background:#e3f2fd; color:#0d47a1; font-weight:bold; padding:4px 10px; "
                                "border-radius:6px; border:1px solid #42a5f5;"
                            )
                            chip_row.addWidget(glb)
                        if (
                            can_edit_bundle
                            and idx == 0
                            and oid_int
                            and (has_cut or has_drawable_scheme or order_has_scheme_created)
                        ):
                            btn_revert = QPushButton("Отменить раскрой заказа")
                            btn_revert.setToolTip(
                                "Сразу отменить раскрой (без дополнительного окна), если это возможно: "
                                "заказ и изделия вернутся в «Оплачен» со способами оплаты из расчёта. "
                                "Нельзя, если стекло отмечено изготовленным или остаток раскроя уже ушёл в другой заказ — "
                                "тогда появится сообщение с причиной."
                            )
                            btn_revert.clicked.connect(
                                lambda _=False, _oid=oid_int: self._on_revert_cut(_oid)
                            )
                            if measure_lock_reason:
                                btn_revert.setToolTip(measure_lock_reason)
                            chip_row.addWidget(btn_revert)
                        chip_row.addStretch()
                        bl.addLayout(chip_row)

                    if kind == "facade":
                        pr_rows = _facade_pricing_rows(
                            pl0,
                            _conn,
                            _drill,
                            1.0,
                            aux_prices=_facade_aux_cached,
                            delivery_prices=_delivery_prices,
                        )
                    else:
                        pr_rows = _glass_pricing_rows(pl0, _conn, _drill, 1.0)

                    # Крупный макет по центру, под ним — смета по этой позиции.
                    tw, th = 520, 390
                    mock_wrap = QHBoxLayout()
                    mock_wrap.addStretch(1)
                    if kind == "facade":
                        tl = QLabel()
                        tl.setFixedSize(tw, th)
                        tl.setAlignment(Qt.AlignCenter)
                        tl.setScaledContents(False)
                        tl.setStyleSheet(
                            "QLabel { border:1px solid #c5cae9; background:#fafafa; border-radius:8px; }"
                        )
                        _schedule_facade_thumb(self, tl, pl0, tw, th)
                    else:
                        tl = _glass_overview_label(pl0, tw, th)
                    mock_wrap.addWidget(tl, 0, Qt.AlignCenter)
                    mock_wrap.addStretch(1)
                    bl.addLayout(mock_wrap)

                    price_core = self._build_price_core_frame(pid, kind, pl0, pr_rows)
                    bl.addWidget(price_core)

                    if not self._summary_only:
                        surcharge_sum = int(pr.get("surcharge_amount") or 0)
                        bl.addWidget(
                            QLabel(
                                "<span style='font-size:11pt; color:#6a1b9a;'><b>Доплата по позиции: %s ₽</b></span>"
                                % surcharge_sum
                            )
                        )

                        if can_edit_bundle and not measure_lock_reason:
                            btn_row = QHBoxLayout()
                            b_edit = QPushButton("Открыть расчёт")
                            b_edit.setToolTip("Полный редактор, как при оформлении заказа")
                            b_edit.clicked.connect(lambda _=False, p=pid: self._on_edit_product(p))
                            btn_row.addWidget(b_edit)
                            if str(p_status).strip().lower() != "draft":
                                b_del = QPushButton("Удалить")
                                b_del.clicked.connect(lambda _=False, p=pid: self._on_delete_product(p))
                                b_del.setToolTip(
                                    "Удалить позицию из заказа (при необходимости удалите весь заказ в таблице заказов)."
                                )
                                btn_row.addWidget(b_del)
                            btn_row.addStretch()
                            bl.addLayout(btn_row)
                    vlay.addWidget(box)

                # Схема раскроя — при наличии листов с выкроями; под всеми изделиями (фасад/стекло), не сверху.
                if oid_int and has_drawable_scheme and not self._summary_only:
                    try:

                        def _build_cut_scheme_ui(layouts_main: list, show_services_cta: bool) -> QFrame:
                            cut_block = QFrame()
                            cut_block.setStyleSheet(
                                "QFrame { background:#f5f9ff; border:1px solid #90caf9; border-radius:10px; }"
                            )
                            cbl = QVBoxLayout(cut_block)
                            cbl.setSpacing(10)
                            head = QLabel(
                                "<b>Схема раскроя</b> — все листы заказа "
                                "<span style='color:#2e7d32;'>(сохранённый раскрой)</span>"
                            )
                            head.setAlignment(Qt.AlignCenter)
                            head.setStyleSheet("font-size:12pt; color:#1565c0;")
                            cbl.addWidget(head)
                            if show_services_cta:
                                row_cta = QHBoxLayout()
                                row_cta.addStretch(1)
                                btn_cta = QPushButton("+ доставка | монтаж | замер")
                                btn_cta.setToolTip(
                                    "Добавить или изменить доставку, монтаж и замер в расчёте заказа; "
                                    "данные уйдут в таблицу заказов и на портал по текущим правилам."
                                )
                                btn_cta.setStyleSheet(
                                    "QPushButton {"
                                    "  background: #e3f2fd;"
                                    "  color: #0d47a1;"
                                    "  font-weight: bold;"
                                    "  padding: 10px 18px;"
                                    "  border-radius: 8px;"
                                    "  border: 1px solid #64b5f6;"
                                    "}"
                                    "QPushButton:hover { background: #bbdefb; border-color: #42a5f5; }"
                                    "QPushButton:pressed { background: #90caf9; }"
                                    "QPushButton:disabled { background: #eceff1; color: #90a4ae; border-color: #cfd8dc; }"
                                )
                                btn_cta.clicked.connect(self._on_add_delivery_install_services)
                                row_cta.addWidget(btn_cta)
                                row_cta.addStretch(1)
                                cbl.addLayout(row_cta)
                            row_c = QHBoxLayout()
                            row_c.addStretch(1)
                            from mirror_cut_sys_path import mirror_cut_imports_first

                            with mirror_cut_imports_first():
                                from ui.cutting_canvas import CuttingCanvas

                                cv_m = CuttingCanvas(parent=cut_block, fit_to_view=True, preview_mode=False)
                                cv_m.set_layouts(_layouts_with_order_piece_highlight(layouts_main, self._order_data))
                                cv_m.setFixedSize(900, 620)
                            row_c.addWidget(cv_m, 0, Qt.AlignCenter)
                            row_c.addStretch(1)
                            cbl.addLayout(row_c)
                            btn_row_c = QHBoxLayout()
                            btn_row_c.addStretch(1)
                            btn_full = QPushButton("Открыть схему во весь экран…")
                            mw_c = self.parent()
                            od_c = dict(self._order_data)
                            lo_c = _layouts_with_order_piece_highlight(list(layouts_main), self._order_data)
                            btn_full.clicked.connect(
                                lambda _=False, o=od_c, layouts=lo_c: (
                                    mw_c._open_scheme_with_layouts(o, layouts)
                                    if mw_c is not None and hasattr(mw_c, "_open_scheme_with_layouts")
                                    else None
                                )
                            )
                            btn_row_c.addWidget(btn_full)
                            btn_row_c.addStretch(1)
                            cbl.addLayout(btn_row_c)
                            return cut_block

                        cut_lazy = QFrame()
                        cut_lazy.setStyleSheet(
                            "QFrame { background:#f5f9ff; border:1px dashed #90caf9; border-radius:10px; }"
                        )
                        cl = QVBoxLayout(cut_lazy)
                        cl.setSpacing(8)
                        btn_load_cut = QPushButton("Показать схему раскроя…")
                        btn_load_cut.setStyleSheet(
                            "QPushButton { padding:10px 14px; font-weight:bold; color:#1565c0; }"
                        )
                        cut_inner_host = QWidget()
                        cut_inner_lay = QVBoxLayout(cut_inner_host)
                        cut_inner_lay.setContentsMargins(0, 0, 0, 0)
                        cut_inner_host.setVisible(False)

                        def _on_show_cut():
                            btn_load_cut.setEnabled(False)
                            btn_load_cut.setText("Загрузка…")
                            try:
                                all_l = [
                                    L
                                    for L in (db_models.get_cut_layouts_for_overview(oid_int) or [])
                                    if isinstance(L, dict)
                                ]
                                layouts_main = [L for L in all_l if _cut_layout_has_drawable_content(L)]
                                if not layouts_main:
                                    layouts_main = list(all_l)
                                if not layouts_main:
                                    btn_load_cut.setText("Нет сохранённого раскроя")
                                    return
                                cut_inner_lay.addWidget(_build_cut_scheme_ui(layouts_main, cta_delivery_install))
                                cut_inner_host.setVisible(True)
                                btn_load_cut.setVisible(False)
                            except Exception:
                                btn_load_cut.setText("Не удалось загрузить схему")
                                btn_load_cut.setEnabled(True)

                        btn_load_cut.clicked.connect(_on_show_cut)
                        cl.addWidget(btn_load_cut)
                        cl.addWidget(cut_inner_host)
                        sep_cut = QFrame()
                        sep_cut.setFrameShape(QFrame.HLine)
                        sep_cut.setStyleSheet("color:#bbb; max-height:2px;")
                        vlay.addWidget(sep_cut)
                        vlay.addWidget(cut_lazy)
                    except Exception:
                        pass
            finally:
                if _conn is not None:
                    _conn.close()

        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        bot = QHBoxLayout()
        if not self._summary_only:
            btn_pdf = QPushButton("PDF")
            btn_labels = QPushButton("Этикетки")
            btn_add = QPushButton("+ Изделие")
            btn_add.setStyleSheet(
                "QPushButton { background:#1976d2; color:white; font-weight:bold; padding:8px 14px; border-radius:6px; }"
            )
            btn_add.clicked.connect(self._on_add_product)
            btn_add.setVisible(bool(can_add_products))
            if can_add_products:
                if measure_lock_reason:
                    btn_add.setToolTip(
                        "%s Добавление изделия станет доступно после снятия ограничения."
                        % measure_lock_reason
                    )
                else:
                    btn_add.setToolTip("Добавить изделие в заказ")
            btn_pdf.clicked.connect(self._on_pdf)
            btn_labels.clicked.connect(self._on_labels)
            btn_pdf.setVisible(bool(can_print))
            btn_labels.setVisible(bool(can_print))
            if can_print:
                if not has_glass_positions:
                    if has_facade:
                        btn_pdf.setToolTip(
                            "PDF: инструкция для цеха — какие профили и наполнение проёма взять, присадка, склад."
                        )
                        btn_labels.setToolTip("Этикетки профилей на брусья по данным фасадов в заказе.")
                    else:
                        btn_pdf.setToolTip("PDF по изделиям заказа (если в расчёте есть данные).")
                        btn_labels.setToolTip("Этикетки по данным заказа (профили или раскрой при наличии).")
                else:
                    if has_facade and not has_cut:
                        btn_pdf.setToolTip(
                            "PDF: инструкция для цеха — какие профили и наполнение проёма взять, присадка, этикетки склада. "
                            "Карты раскроя стекла появятся после «Раскрой», если в заказе есть стекло/зеркало."
                        )
                        btn_labels.setToolTip(
                            "Этикетки профилей на брусья (по данным фасадов в заказе). "
                            "Этикетки нарезанного стекла — после сохранения раскроя листов."
                        )
                    elif has_facade and has_cut:
                        btn_pdf.setToolTip(
                            "PDF: карты раскроя стекла (если есть) и инструкция для цеха по фасадам и изделиям."
                        )
                        btn_labels.setToolTip(
                            "Этикетки: нарезанное стекло по раскрою; при фасадах — также этикетки профилей, если нет данных стекла."
                        )
                    elif has_glass_positions and not has_cut:
                        btn_pdf.setToolTip(
                            "Сначала «Раскрой» и сохраните листы — тогда будут карты раскроя. "
                            "Пока можно открыть PDF инструкции, если в расчёте есть текст для цеха."
                        )
                        btn_labels.setToolTip(
                            "Этикетки нарезанного стекла появятся после «Раскрой». Профили в заказе не указаны."
                        )
                    elif not has_cut:
                        btn_pdf.setToolTip(
                            "Нет раскроя стекла в базе. При наличии расчёта в заказе можно сформировать PDF инструкции для цеха."
                        )
                        btn_labels.setToolTip(
                            "Нет данных для этикеток раскроя. Для фасадов с профилями со склада доступны этикетки брусьев."
                        )
                    else:
                        btn_pdf.setToolTip(
                            "Экспорт карт раскроя стекла в PDF и инструкции для цеха по изделиям в заказе."
                        )
                        btn_labels.setToolTip(
                            "Этикетки стекла по раскрою; если раскроя нет — этикетки профилей фасада (при их наличии в расчёте)."
                        )
            bot.addWidget(btn_pdf)
            bot.addWidget(btn_labels)
            bot.addWidget(btn_add)
            if has_facade:
                btn_facades = QPushButton("Все фасады (вкладки)")
                btn_facades.setToolTip("Окно расчёта фасадов со всеми позициями этого заказа")
                btn_facades.clicked.connect(self._on_open_all_facades)
                btn_facades.setVisible(bool(can_edit_bundle) and not bool(measure_lock_reason))
                if btn_facades.isVisible():
                    if measure_lock_reason:
                        btn_facades.setToolTip(measure_lock_reason)
                    else:
                        btn_facades.setToolTip("Окно расчёта фасадов со всеми позициями этого заказа")
                bot.addWidget(btn_facades)
            bot.addStretch()
            btn_calc = QPushButton("Расчёт (первое изделие)")
            btn_calc.setToolTip("Открыть полный редактор для первой позиции в списке")
            btn_calc.clicked.connect(self._on_open_first_calc)
            btn_calc.setVisible(bool(can_edit_bundle) and not bool(measure_lock_reason))
            if btn_calc.isVisible():
                if measure_lock_reason:
                    btn_calc.setToolTip(measure_lock_reason)
                else:
                    btn_calc.setToolTip("Открыть полный редактор для первой позиции в списке")
            bot.addWidget(btn_calc)
        else:
            bot.addStretch(1)
        btn_close = QPushButton("ОК")
        btn_close.clicked.connect(self.accept)
        bot.addWidget(btn_close)
        root.addLayout(bot)
        QTimer.singleShot(0, self._apply_overview_window_geometry)

    def _apply_overview_window_geometry(self):
        """80 % доступной области экрана; не даём содержимому раздувать minimumSizeHint."""
        geo = _overview_available_screen_geometry(self)
        if geo is None:
            self.resize(1040, 820)
            return
        w = max(640, int(geo.width() * _OVERVIEW_SCREEN_FRACTION))
        h = max(480, int(geo.height() * _OVERVIEW_SCREEN_FRACTION))
        self.setMinimumSize(0, 0)
        self.resize(w, h)
        fg = self.frameGeometry()
        fg.moveCenter(geo.center())
        self.move(fg.topLeft())

    def _touch_main_orders_list(self):
        self._main_orders_changed = True

    def _notify_main_window_portal_bundle_changed(self):
        """После мутации портала из сводки — обновить строку заказа в главной таблице (колонки портала)."""
        self._touch_main_orders_list()
        par = self.parent()
        oid = self._order_data.get("id")
        if par is None or oid is None:
            return
        if not hasattr(par, "_light_resync_mirror_order_from_db"):
            return
        try:
            o_int = int(oid)
        except (TypeError, ValueError):
            return

        def _go():
            try:
                par._light_resync_mirror_order_from_db(o_int, refresh_items=False)
            except Exception:
                pass

        QTimer.singleShot(0, _go)

    def _maybe_promote_order_paid_after_bundle_save(self):
        """Если по всем позициям в bundle выбрана оплата — заказ «Оплачен» без ручной смены статуса строки."""
        if self._summary_only:
            return
        oid = self._order_data.get("id")
        if oid is None:
            return
        raw = self._live_blocks_json
        if not raw or not str(raw).strip():
            return
        try:
            _ver, products = parse_bundle(raw)
            if not products:
                return
            if bundle_payment_aggregate(list(products)).get("state") != "full":
                return
            row = db_models.get_order(int(oid))
            if not row:
                return
            st = str(row.get("status") or "").strip().lower() or "draft"
            if st != "draft":
                return
            db_models.set_order_status(int(oid), "paid")
            self._order_data["status"] = "paid"
            self._touch_main_orders_list()
        except Exception:
            pass

    def _maybe_promote_order_made_after_bundle_save(self):
        """Если по всем штукам изделий статус «изготовлено» — заказ «Изготовлен» без ручной смены."""
        if self._summary_only:
            return
        oid = self._order_data.get("id")
        if oid is None:
            return
        raw = self._live_blocks_json
        if not raw or not str(raw).strip():
            return
        try:
            _ver, products = parse_bundle(raw)
            if not products:
                return
            row = db_models.get_order(int(oid))
            if not row:
                return
            st = str(row.get("status") or "").strip().lower() or "draft"
            if st in ("made", "shipped", "completed", "cancelled"):
                return
            if st == "draft":
                return
            fallback = st
            facade_ev = None
            if any(str((p or {}).get("kind") or "").strip() == "facade" for p in products):
                try:
                    facade_ev = db_models.list_production_events(int(oid)) or []
                except Exception:
                    facade_ev = None

            count_cb = None
            if hasattr(db_models, "count_facade_instance_assembled_events"):

                def _count_f(oid_i, idx1, ev):
                    return int(
                        db_models.count_facade_instance_assembled_events(
                            oid_i, idx1, production_events=ev
                        )
                    )

                count_cb = _count_f

            if not bundle_all_units_in_terminal_order_statuses(
                list(products),
                order_fallback_status=fallback,
                order_id=int(oid),
                facade_production_events=facade_ev,
                count_facade_assembled=count_cb,
            ):
                return
            db_models.set_order_status(int(oid), "made")
            self._order_data["status"] = "made"
            self._touch_main_orders_list()
        except Exception:
            pass

    def _set_live_bundle_json(self, new_j: str):
        """Обновить JSON пакета в памяти и поставить отложенную запись в БД (не блокирует UI)."""
        new_j = str(new_j or "")
        self._live_blocks_json = new_j
        self._order_data["blocks_calc_json"] = new_j
        self._touch_main_orders_list()
        if self._summary_only or self._order_data.get("id") is None:
            return
        self._persist_timer.stop()
        self._persist_timer.start(85)

    def _flush_blocks_persist(self):
        if getattr(self, "_overview_shutting_down", False):
            return
        if self._summary_only:
            return
        oid = self._order_data.get("id")
        if oid is None:
            return
        if self._live_blocks_json == self._last_written_json:
            return
        if self._write_inflight:
            return
        self._write_inflight = True
        self._persist_pool.start(
            _BlocksPersistRunnable(int(oid), self._live_blocks_json, self._persist_signals)
        )

    def _on_blocks_persist_ok(self, payload):
        self._write_inflight = False
        p = payload if isinstance(payload, str) else str(payload)
        if p == self._live_blocks_json:
            self._last_written_json = p
            self._maybe_promote_order_paid_after_bundle_save()
            self._maybe_promote_order_made_after_bundle_save()
        if getattr(self, "_overview_shutting_down", False):
            return
        if self._live_blocks_json != self._last_written_json:
            QTimer.singleShot(0, self._flush_blocks_persist)

    def _on_blocks_persist_err(self, msg: str):
        self._write_inflight = False
        if getattr(self, "_overview_shutting_down", False):
            return
        QMessageBox.warning(self, "Сохранение в базу", msg or "Ошибка записи заказа.")

    def done(self, result):
        """Дождаться фоновой записи и добить несохранённое — без лагов при закрытии."""
        self._overview_shutting_down = True
        self._persist_timer.stop()
        _drain_qthread_pool(self._persist_pool, max_seconds=6.0)
        oid = self._order_data.get("id")
        if oid is not None and self._live_blocks_json != self._last_written_json:
            try:
                db_models.update_order_blocks_calc(int(oid), self._live_blocks_json)
                self._last_written_json = self._live_blocks_json
                self._maybe_promote_order_paid_after_bundle_save()
                self._maybe_promote_order_made_after_bundle_save()
            except Exception as ex:
                QMessageBox.warning(self, "Сохранение", str(ex))
        super().done(result)

    def _on_revert_cut(self, order_id):
        if not order_id:
            return
        ok, msg = db_models.revert_order_cut_draft(int(order_id))
        if ok:
            try:
                fr = db_models.get_order(int(order_id))
                if isinstance(fr, dict) and fr.get("blocks_calc_json") is not None:
                    self._live_blocks_json = str(fr.get("blocks_calc_json") or "")
                    self._last_written_json = self._live_blocks_json
                    self._order_data["blocks_calc_json"] = self._live_blocks_json
                    self._order_data["status"] = fr.get("status")
            except Exception:
                pass
            self._persist_timer.stop()
            _drain_qthread_pool(self._persist_pool, max_seconds=5.0)
            self.accept()
            self._reload_main()
        else:
            QMessageBox.warning(self, "Отмена раскроя", msg or "Операция невозможна.")

    def _on_pdf(self):
        parent = self.parent()
        if parent is not None and hasattr(parent, "_open_pdf"):
            parent._open_pdf(self._order_data, overview_summary_only=True)

    def _on_labels(self):
        parent = self.parent()
        if parent is not None and hasattr(parent, "_open_label"):
            parent._open_label(self._order_data)

    def _reload_main(self):
        mw = self.parent()
        if mw is None:
            return
        oid = self._order_data.get("id")
        try:
            oid_int = int(oid) if oid is not None else None
        except (TypeError, ValueError):
            oid_int = None
        if oid_int is not None and hasattr(mw, "_light_resync_mirror_order_from_db"):
            try:
                mw._light_resync_mirror_order_from_db(oid_int, refresh_items=True)
                return
            except Exception:
                pass
        if hasattr(mw, "_load_orders"):
            mw._load_orders()

    def _on_edit_product(self, product_id: str):
        from db import models as db_models
        from logic.blocks_bundle import parse_bundle
        from ui.glass_mirror_calc_dialog import GlassMirrorCalcDialog
        from ui.facade_order_dialog import FacadeOrderDialog

        parent = self.parent()
        oid = self._order_data.get("id")
        try:
            lock_reason = db_models.get_order_measure_lock_reason(int(oid)) if oid is not None else None
        except Exception:
            lock_reason = None
        if lock_reason:
            QMessageBox.information(self, "Изделия", lock_reason)
            return
        row = db_models.get_order(oid)
        _, products = parse_bundle(row.get("blocks_calc_json")) if row else (0, [])
        pr = next((p for p in products if str(p.get("id")) == str(product_id)), None)
        kind = str((pr or {}).get("kind") or "glass_mirror").strip() or "glass_mirror"
        self.accept()
        if kind == "facade":
            FacadeOrderDialog(parent, linked_order_id=int(oid), product_id=str(product_id)).exec_()
        else:
            GlassMirrorCalcDialog(
                parent,
                int(oid),
                product_id=str(product_id),
                append_new=False,
            ).exec_()
        if parent:
            GlassOrderOverviewDialog._reload_static(parent, oid)
            fresh = db_models.get_order(oid)
            if fresh:
                GlassOrderOverviewDialog(fresh, parent).exec_()

    def _on_delete_product(self, product_id: str, *, confirm: bool = True):
        from db import models as db_models

        oid = self._order_data.get("id")
        if not oid:
            return
        if confirm:
            r = QMessageBox.question(
                self,
                "Удалить изделие",
                "Удалить это изделие из заказа?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                return
        try:
            ok, msg = db_models.delete_bundle_product_with_cut_cleanup(int(oid), str(product_id))
            if not ok:
                QMessageBox.warning(self, "Удаление", msg or "Операция невозможна.")
                return
            fresh_row = db_models.get_order(int(oid)) or {}
            merged = fresh_row.get("blocks_calc_json")
            merged = str(merged) if merged is not None else ""
            self._live_blocks_json = merged
            self._last_written_json = merged
            self._order_data["blocks_calc_json"] = merged
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        self._persist_timer.stop()
        _drain_qthread_pool(self._persist_pool, max_seconds=5.0)
        parent = self.parent()
        self.accept()
        if parent:
            GlassOrderOverviewDialog._reload_static(parent, oid)
            fresh = db_models.get_order(oid)
            if fresh:
                GlassOrderOverviewDialog(fresh, parent).exec_()

    def _on_add_product(self):
        from db import models as db_models
        from ui.new_order_modal import NewOrderModal
        from ui.order_product_flow import run_product_creation_flow

        oid = self._order_data.get("id")
        try:
            lock_reason = db_models.get_order_measure_lock_reason(int(oid)) if oid is not None else None
        except Exception:
            lock_reason = None
        if lock_reason:
            QMessageBox.information(self, "Изделия", lock_reason)
            return

        m = NewOrderModal(self, dialog_title="Тип изделия")
        if m.exec_() != QDialog.Accepted:
            return
        t = m.chosen_type()
        parent = self.parent()
        oid = self._order_data.get("id")
        self.accept()
        if not t:
            return
        if t not in ("glass", "facades"):
            QMessageBox.information(
                parent or self,
                "Тип изделия",
                "Этот тип пока в разработке — доступны «СТЕКЛО / ЗЕРКАЛО» и «ФАСАДЫ».",
            )
        else:
            run_product_creation_flow(parent, int(oid), t)
        if parent:
            GlassOrderOverviewDialog._reload_static(parent, oid)
            fresh = db_models.get_order(oid)
            if fresh:
                GlassOrderOverviewDialog(fresh, parent).exec_()

    def _on_open_all_facades(self):
        from ui.facade_order_dialog import FacadeOrderDialog

        parent = self.parent()
        oid = self._order_data.get("id")
        if not oid:
            return
        self.accept()
        FacadeOrderDialog(parent, linked_order_id=int(oid)).exec_()
        if parent:
            GlassOrderOverviewDialog._reload_static(parent, oid)
            fresh = db_models.get_order(oid)
            if fresh:
                GlassOrderOverviewDialog(fresh, parent).exec_()

    def _on_open_first_calc(self):
        from db import models as db_models
        from logic.blocks_bundle import parse_bundle
        from ui.glass_mirror_calc_dialog import GlassMirrorCalcDialog
        from ui.facade_order_dialog import FacadeOrderDialog

        oid = self._order_data.get("id")
        try:
            lock_reason = db_models.get_order_measure_lock_reason(int(oid)) if oid is not None else None
        except Exception:
            lock_reason = None
        if lock_reason:
            QMessageBox.information(self, "Изделия", lock_reason)
            return

        _, products = parse_bundle(self._order_data.get("blocks_calc_json"))
        pr0 = products[0] if products else None
        pid = str(pr0.get("id")) if pr0 else None
        kind = str((pr0 or {}).get("kind") or "glass_mirror").strip() or "glass_mirror"
        parent = self.parent()
        oid = self._order_data.get("id")
        self.accept()
        if kind == "facade" and pid:
            FacadeOrderDialog(parent, linked_order_id=int(oid), product_id=str(pid)).exec_()
        elif pid:
            GlassMirrorCalcDialog(
                parent, int(oid), product_id=pid, append_new=False
            ).exec_()
        else:
            GlassMirrorCalcDialog(parent, int(oid), append_new=True).exec_()
        if parent:
            GlassOrderOverviewDialog._reload_static(parent, oid)
            fresh = db_models.get_order(oid)
            if fresh:
                GlassOrderOverviewDialog(fresh, parent).exec_()

    @staticmethod
    def _reload_static(parent, order_id=None):
        if parent is None:
            return
        if order_id is not None and hasattr(parent, "_light_resync_mirror_order_from_db"):
            try:
                parent._light_resync_mirror_order_from_db(int(order_id), refresh_items=True)
                return
            except Exception:
                pass
        if hasattr(parent, "_load_orders"):
            parent._load_orders()

    @staticmethod
    def _esc(s):
        s = str(s or "")
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
