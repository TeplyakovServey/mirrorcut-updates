# -*- coding: utf-8 -*-
"""Доставка вне КАД: адрес, карта, маршрут, цена в окне, сохранение маршрута в БД."""
from __future__ import annotations

import math
import os
import sqlite3
import sys
from typing import Callable, List, Optional

from PyQt5.QtCore import QObject, Qt, QRunnable, QThreadPool, QTimer, QStringListModel, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QCompleter,
)

from calc.delivery_calc import delivery_price_rub, fetch_delivery_prices, routing_access_token
from calc.delivery_geo import (
    geocode_by_token,
    kad_contains,
    optimize_route_to_kad,
    route_length_m,
)
from calc.delivery_route_persist import (
    load_delivery_route_snapshot,
    rebuild_route_from_saved_coordinates,
    save_delivery_route_snapshot,
)
from elements.delivery_web_map import DeliveryWebMap


def _mirror_cut_root() -> str:
    """Корень установки / MIRROR_CUT (рядом с MirrorCut.exe или FINAL_WINDOW)."""
    try:
        from cfg_loader import get_mirror_cut_root

        return get_mirror_cut_root()
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    # elements → BLOCKS → MAIN_PROJECT → корень дерева (не на уровень выше exe).
    return os.path.abspath(os.path.join(here, "..", "..", ".."))


def _cities_db_path() -> str:
    """Локальный SQLite: справочник городов/улиц (быстрее, без сети к Postgres)."""
    here = os.path.dirname(os.path.abspath(__file__))
    main_project = os.path.abspath(os.path.join(here, "..", ".."))
    install_root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    candidates: List[str] = []
    try:
        root = _mirror_cut_root()
        candidates.append(os.path.join(root, "CALC_WINDOWS", "cities_streets.db"))
        candidates.append(os.path.join(root, "FINAL_WINDOW", "CALC_WINDOWS", "cities_streets.db"))
    except Exception:
        root = install_root
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        for base in (exe_dir, os.path.join(exe_dir, "_internal")):
            candidates.append(os.path.join(base, "CALC_WINDOWS", "cities_streets.db"))
    for base in (install_root, root, main_project):
        candidates.append(os.path.join(base, "CALC_WINDOWS", "cities_streets.db"))
        candidates.append(os.path.join(base, "FINAL_WINDOW", "CALC_WINDOWS", "cities_streets.db"))
    legacy_repo = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
    candidates.append(os.path.join(legacy_repo, "CALC_WINDOWS", "cities_streets.db"))
    candidates.append(os.path.join(legacy_repo, "FINAL_WINDOW", "CALC_WINDOWS", "cities_streets.db"))
    seen = set()
    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        if os.path.isfile(p):
            return p
    return os.path.join(install_root, "CALC_WINDOWS", "cities_streets.db")


def load_cities() -> List[str]:
    p = _cities_db_path()
    if not os.path.isfile(p):
        return ["Санкт - Петербург"]
    try:
        conn = sqlite3.connect(p)
        cur = conn.cursor()
        cur.execute("SELECT city_name FROM cities ORDER BY city_name")
        rows = [r[0] for r in cur.fetchall() if r[0]]
        conn.close()
    except Exception:
        return ["Санкт - Петербург"]
    if "Санкт - Петербург" in rows:
        rows.remove("Санкт - Петербург")
    return ["Санкт - Петербург"] + sorted(rows)


def load_streets(city: str) -> List[str]:
    if not city:
        return []
    p = _cities_db_path()
    if not os.path.isfile(p):
        return []
    try:
        conn = sqlite3.connect(p)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.street_name FROM streets s
            JOIN cities c ON s.city_id = c.city_id
            WHERE c.city_name = ? ORDER BY s.street_name
            """,
            (city,),
        )
        out = [r[0] for r in cur.fetchall() if r[0]]
        conn.close()
        return out
    except Exception:
        return []


_MAX_ADDR_SUGGESTIONS = 250
_MAP_DIALOG_SCREEN_FRACTION = 0.8


def _map_dialog_available_screen_geometry(widget=None):
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


def filter_address_suggestions(items: List[str], prefix: str, limit: int = _MAX_ADDR_SUGGESTIONS) -> List[str]:
    """Сначала строки, начинающиеся с prefix, затем где prefix встречается внутри (без учёта регистра)."""
    pref = (prefix or "").strip().lower()
    if not pref:
        return list(items)[:limit]
    starts: List[str] = []
    inner: List[str] = []
    seen = set()
    for x in items:
        if not x:
            continue
        lw = x.lower()
        if lw.startswith(pref):
            if x not in seen:
                seen.add(x)
                starts.append(x)
        elif pref in lw:
            if x not in seen:
                seen.add(x)
                inner.append(x)
    out = starts + inner
    return out[:limit]


def _setup_editable_address_combo(
    combo: QComboBox,
    get_source: Callable[[], List[str]],
) -> QStringListModel:
    """Редактируемый комбобокс + подсказки с приоритетом «с начала», затем «внутри строки»."""
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    combo.setMaxVisibleItems(18)
    model = QStringListModel()
    completer = QCompleter(model, combo)
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    completer.setCompletionMode(QCompleter.PopupCompletion)
    completer.setMaxVisibleItems(18)
    combo.setCompleter(completer)

    def refill(text: str) -> None:
        src = get_source()
        model.setStringList(filter_address_suggestions(src, text))

    le = combo.lineEdit()
    le.textEdited.connect(refill)
    refill(le.text())
    return model


class _DeliveryMapWorkerSignals(QObject):
    route_done = pyqtSignal(int, object)
    geocode_done = pyqtSignal(int, object)


class _RouteBuildTask(QRunnable):
    def __init__(self, lat: float, lon: float, token: str, generation: int, signals: _DeliveryMapWorkerSignals):
        super().__init__()
        self._lat = lat
        self._lon = lon
        self._token = token
        self._gen = generation
        self._signals = signals

    def run(self):
        res = None
        try:
            res = optimize_route_to_kad(self._lat, self._lon, self._token)
        except Exception:
            res = None
        self._signals.route_done.emit(self._gen, res)


class _GeocodeTask(QRunnable):
    def __init__(self, query: str, token: str, generation: int, signals: _DeliveryMapWorkerSignals):
        super().__init__()
        self._query = query
        self._token = token
        self._gen = generation
        self._signals = signals

    def run(self):
        g = None
        try:
            g = geocode_by_token(self._query, self._token)
        except Exception:
            g = None
        self._signals.geocode_done.emit(self._gen, g)


class DeliveryOutsideDialog(QDialog):
    """Адрес вне КАД: карта, маршрут, расчёт цены, сохранение в БД."""

    def __init__(
        self,
        initial: Optional[dict] = None,
        parent=None,
        window_title: str = "Доставка: вне КАД",
    ):
        super().__init__(parent)
        self.setWindowTitle(window_title)
        self._token = routing_access_token()
        self._worker_signals = _DeliveryMapWorkerSignals()
        self._worker_signals.route_done.connect(self._on_route_built)
        self._worker_signals.geocode_done.connect(self._on_geocode_done)
        self._route_generation = 0
        self._geocode_generation = 0
        self._route_busy = False
        self._cached_prices: Optional[dict] = None
        self._lat: Optional[float] = None
        self._lon: Optional[float] = None
        self._address = ""
        self._distance_km: Optional[int] = None
        self._distance_m: Optional[float] = None
        self._route_coords: Optional[List[List[float]]] = None
        self._full_mode = False

        merged: dict = {}
        if initial:
            merged.update(dict(initial))
        if merged.get("lat") is None and merged.get("lon") is None:
            snap = load_delivery_route_snapshot()
            if isinstance(snap, dict):
                merged.update(snap)
        rc_fix = merged.get("Маршрут координаты")
        rebuilt = rebuild_route_from_saved_coordinates(rc_fix)
        if rebuilt and merged.get("Расстояние маршрута м") is None:
            merged["Расстояние маршрута м"] = rebuilt["length_m"]
        if rebuilt and merged.get("Расстояние до КАД") is None:
            merged["Расстояние до КАД"] = rebuilt["distance_km_tariff"]

        saved_route: Optional[List[List[float]]] = None
        self._address = str(merged.get("Адрес") or "")
        d = merged.get("Расстояние до КАД")
        self._distance_km = int(d) if d is not None else None
        dm = merged.get("Расстояние маршрута м")
        if dm is not None:
            try:
                self._distance_m = float(dm)
            except (TypeError, ValueError):
                self._distance_m = None
        lat0, lon0 = merged.get("lat"), merged.get("lon")
        if lat0 is not None and lon0 is not None:
            try:
                self._lat = float(lat0)
                self._lon = float(lon0)
            except (TypeError, ValueError):
                pass
        rc = merged.get("Маршрут координаты")
        if isinstance(rc, list) and len(rc) > 1:
            saved_route = [list(p) for p in rc if isinstance(p, (list, tuple)) and len(p) >= 2]
            if len(saved_route) > 1:
                self._route_coords = saved_route

        root = QVBoxLayout(self)

        addr_box = QGroupBox("Укажите адрес")
        addr_l = QVBoxLayout(addr_box)
        self._all_cities = load_cities()
        self._streets_all: List[str] = []
        self._house_catalog: List[str] = []

        self._city = QComboBox()
        self._street = QComboBox()
        self._house = QComboBox()
        self._city_model = _setup_editable_address_combo(
            self._city, lambda: self._all_cities
        )
        self._street_model = _setup_editable_address_combo(
            self._street, lambda: self._streets_all
        )
        self._house_model = _setup_editable_address_combo(
            self._house, lambda: self._house_catalog
        )
        self._house.lineEdit().setPlaceholderText("дом")

        if self._all_cities:
            self._city.clear()
            self._city.addItems(self._all_cities)
            self._city_model.setStringList(self._all_cities)

        cc = self._city.completer()
        if cc:
            cc.activated[str].connect(lambda t: self._reload_streets_for_city((t or "").strip()))
        self._city.lineEdit().editingFinished.connect(self._on_city_editing_finished)

        row_f = QHBoxLayout()
        self._btn_find_addr = QPushButton("Найти")
        self._btn_find_addr.clicked.connect(self._on_find_structured)
        b_nl = QPushButton("Нет в списке")
        b_nl.clicked.connect(self._on_toggle_full)
        row_f.addWidget(self._btn_find_addr)
        row_f.addWidget(b_nl)
        self._full_address = QLineEdit()
        self._full_address.setPlaceholderText("Полный адрес")
        self._full_address.setVisible(False)
        self._btn_find_full = QPushButton("Найти полный адрес")
        self._btn_find_full.clicked.connect(self._on_find_full)
        self._btn_find_full.setVisible(False)
        form = QFormLayout()
        form.addRow("Город:", self._city)
        form.addRow("Улица:", self._street)
        form.addRow("Дом:", self._house)
        addr_l.addLayout(form)
        addr_l.addLayout(row_f)
        addr_l.addWidget(self._full_address)
        addr_l.addWidget(self._btn_find_full)
        root.addWidget(addr_box)

        self._map = None
        try:
            self._map = DeliveryWebMap(self._token, self)
            self._map.pointClicked.connect(self._on_map_click)
            self._map.reload_map(self._lat, self._lon, saved_route)
            root.addWidget(self._map, 1)
        except Exception:
            root.addWidget(
                QLabel("Карта недоступна. Установите: pip install PyQtWebEngine")
            )

        self._status = QLabel("—")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._price_lbl = QLabel("—")
        self._price_lbl.setWordWrap(True)
        f = QFont("Arial", 9, QFont.Bold)
        self._price_lbl.setFont(f)
        self._price_lbl.setStyleSheet("color: #1a4d1a;")
        root.addWidget(self._price_lbl)

        row2 = QHBoxLayout()
        self._btn_rebuild_route = QPushButton("Перестроить маршрут")
        self._btn_rebuild_route.clicked.connect(self._on_route_force)
        row2.addWidget(self._btn_rebuild_route)
        self._spin_km = QSpinBox()
        self._spin_km.setRange(0, 500)
        self._spin_km.setSpecialValueText("—")
        self._spin_km.setValue(0)
        self._spin_km.valueChanged.connect(lambda _v: self._refresh_delivery_price())
        row2.addWidget(QLabel("Км вручную до КАД:"))
        row2.addWidget(self._spin_km)
        root.addLayout(row2)

        row3 = QHBoxLayout()
        ok = QPushButton("OK")
        cancel = QPushButton("Отмена")
        for b in (ok, cancel):
            b.setAutoDefault(False)
            b.setDefault(False)
        ok.clicked.connect(self._on_ok)
        cancel.clicked.connect(self.reject)
        row3.addStretch()
        row3.addWidget(ok)
        row3.addWidget(cancel)
        root.addLayout(row3)

        self._reload_streets_for_city(self._city.currentText().strip())

        self._sync_status_after_load()
        self._refresh_delivery_price()
        QTimer.singleShot(0, self._apply_map_dialog_geometry)

    def _set_map_busy(self, busy: bool) -> None:
        self._route_busy = bool(busy)
        if self._map is not None:
            self._map.setEnabled(not busy)
        for w in (
            getattr(self, "_spin_km", None),
            getattr(self, "_btn_find_addr", None),
            getattr(self, "_btn_find_full", None),
            getattr(self, "_btn_rebuild_route", None),
        ):
            if w is not None:
                w.setEnabled(not busy)

    def _apply_map_dialog_geometry(self):
        """80 % доступной области экрана, по центру."""
        geo = _map_dialog_available_screen_geometry(self)
        if geo is None:
            self.resize(720, 640)
            return
        w = max(640, int(geo.width() * _MAP_DIALOG_SCREEN_FRACTION))
        h = max(480, int(geo.height() * _MAP_DIALOG_SCREEN_FRACTION))
        self.setMinimumSize(0, 0)
        self.resize(w, h)
        fg = self.frameGeometry()
        fg.moveCenter(geo.center())
        self.move(fg.topLeft())

    def _delivery_prices(self) -> dict:
        if self._cached_prices is not None:
            return self._cached_prices
        conn = None
        try:
            from calc.db_postgres import get_raw_connection

            conn = get_raw_connection()
            if conn:
                self._cached_prices = fetch_delivery_prices(conn=conn) or {}
            else:
                self._cached_prices = fetch_delivery_prices() or {}
        except Exception:
            self._cached_prices = {}
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        return self._cached_prices or {}

    def _refresh_delivery_price(self):
        try:
            prices = self._delivery_prices()
            km = None
            if self._spin_km.value() > 0:
                km = self._spin_km.value()
            elif self._distance_km is not None:
                km = int(self._distance_km)
            if km is None:
                self._price_lbl.setText("Цена доставки: — (нужны км до КАД)")
                return
            rub = delivery_price_rub(prices, False, km)
            base = int(prices.get("За КАД база", 0) or 0)
            pk = int(prices.get("За 1 км", 0) or 0)
            self._price_lbl.setText(
                "Цена доставки: %s ₽  (база %s + %s км × %s ₽/км)"
                % (rub, base, km, pk)
            )
        except Exception as ex:
            self._price_lbl.setText("Цена: ошибка (%s)" % ex)

    def _sync_status_after_load(self):
        if self._lat is None or self._lon is None:
            self._status.setText("Введите адрес или выберите точку на карте.")
            return
        if kad_contains(self._lon, self._lat):
            self._status.setText(
                "Точка внутри КАД. На плитке выберите «В пределах КАД»."
            )
            return
        if self._route_coords and len(self._route_coords) > 1:
            self._apply_route_metrics(self._route_coords)
        else:
            self._status.setText("Строим маршрут…")
            self._build_route_async()

    def _on_toggle_full(self):
        self._full_mode = not self._full_mode
        self._full_address.setVisible(self._full_mode)
        self._btn_find_full.setVisible(self._full_mode)

    def _on_city_editing_finished(self):
        self._reload_streets_for_city(self._city.currentText().strip())

    def _reload_streets_for_city(self, city: str):
        self._streets_all = load_streets(city or "")
        self._street.blockSignals(True)
        self._street.clear()
        if self._streets_all:
            self._street.addItems(self._streets_all)
        self._street.lineEdit().blockSignals(True)
        self._street_model.setStringList(
            filter_address_suggestions(self._streets_all, self._street.lineEdit().text())
        )
        self._street.lineEdit().blockSignals(False)
        self._street.blockSignals(False)

    def _on_find_structured(self):
        city = self._city.currentText().strip()
        if not city:
            QMessageBox.warning(self, "Адрес", "Укажите город.")
            return
        street = self._street.currentText().strip()
        house = self._house.currentText().strip()
        self._run_geocode(f"{city} {street} {house}".strip())

    def _on_find_full(self):
        q = self._full_address.text().strip()
        if not q:
            QMessageBox.warning(self, "Адрес", "Введите адрес.")
            return
        self._run_geocode(q)

    def _run_geocode(self, q: str):
        if self._route_busy:
            return
        self._geocode_generation += 1
        gen = self._geocode_generation
        self._set_map_busy(True)
        self._status.setText("Ищем адрес…")
        QApplication.processEvents()
        QThreadPool.globalInstance().start(
            _GeocodeTask(q.strip(), self._token, gen, self._worker_signals)
        )

    def _on_geocode_done(self, generation: int, result: object) -> None:
        if generation != self._geocode_generation:
            return
        self._set_map_busy(False)
        g = result
        if not g:
            self._status.setText("Адрес не найден — укажите точку на карте.")
            QMessageBox.information(
                self,
                "Адрес",
                "Не найдено. Укажите точку на карте кликом.",
            )
            return
        lat, lon, name = g
        self._lat = lat
        self._lon = lon
        self._address = name
        self._route_coords = None
        self._distance_m = None
        self._distance_km = None
        if self._map:
            self._map.js_set_marker(lat, lon)
        self._handle_new_point_outside()

    def _on_map_click(self, lat: float, lon: float):
        if self._route_busy:
            return
        self._lat = lat
        self._lon = lon
        if not self._address.strip():
            self._address = "Точка на карте"
        self._route_coords = None
        self._distance_m = None
        self._distance_km = None
        if self._map:
            self._map.js_set_marker(lat, lon)
        self._handle_new_point_outside()

    def _handle_new_point_outside(self):
        if self._lat is None or self._lon is None:
            return
        if kad_contains(self._lon, self._lat):
            self._status.setText(
                "Точка внутри КАД. На плитке выберите «В пределах КАД»."
            )
            self._refresh_delivery_price()
            return
        self._status.setText("Строим маршрут до границы КАД…")
        self._build_route_async()

    def _apply_route_metrics(self, coords: List[List[float]]):
        dist_m = route_length_m(coords)
        self._distance_m = dist_m
        self._distance_km = max(1, int(math.ceil(dist_m / 1000.0)))
        self._status.setText(
            "Маршрут до границы КАД: ≈ %.2f км (в тарифе: %s км)."
            % (dist_m / 1000.0, self._distance_km)
        )
        self._refresh_delivery_price()

    def _build_route_async(self) -> None:
        if self._lat is None or self._lon is None:
            return
        if kad_contains(self._lon, self._lat):
            return
        self._route_generation += 1
        gen = self._route_generation
        self._set_map_busy(True)
        self._status.setText("Строим маршрут до границы КАД…")
        QThreadPool.globalInstance().start(
            _RouteBuildTask(self._lat, self._lon, self._token, gen, self._worker_signals)
        )

    def _on_route_built(self, generation: int, result: object) -> None:
        if generation != self._route_generation:
            return
        self._set_map_busy(False)
        res = result
        if not res:
            self._status.setText(
                "Маршрут не построен (сеть или Mapbox). Повторите клик или введите км вручную."
            )
            self._route_coords = None
            self._distance_m = None
            self._distance_km = None
            self._refresh_delivery_price()
            return
        dist_m, coords = res
        self._route_coords = coords
        self._distance_m = dist_m
        self._distance_km = max(1, int(math.ceil(dist_m / 1000.0)))
        if self._map:
            self._map.js_set_route(coords)
        self._apply_route_metrics(coords)

    def _on_route_force(self):
        if self._route_busy:
            return
        if self._lat is None or self._lon is None:
            QMessageBox.information(self, "Маршрут", "Сначала задайте точку.")
            return
        if kad_contains(self._lon, self._lat):
            QMessageBox.information(self, "КАД", "Точка внутри КАД.")
            return
        self._build_route_async()

    def closeEvent(self, event):  # noqa: N802
        self._route_generation += 1
        self._geocode_generation += 1
        super().closeEvent(event)

    def _finalize_ok(self):
        save_delivery_route_snapshot(self.get_result())
        self.accept()

    def _on_ok(self):
        if self._spin_km.value() > 0:
            self._distance_km = self._spin_km.value()
            if self._route_coords is None:
                self._distance_m = None
            self._finalize_ok()
            return
        if self._lat is None or self._lon is None:
            QMessageBox.warning(
                self,
                "Доставка",
                "Укажите точку на карте или введите километраж вручную.",
            )
            return
        if kad_contains(self._lon, self._lat):
            self._distance_km = None
            self._distance_m = None
            self._route_coords = None
            self._finalize_ok()
            return
        if self._distance_km is None:
            QMessageBox.warning(
                self,
                "Маршрут",
                "Нет расстояния. Дождитесь маршрута или введите км вручную.",
            )
            return
        self._finalize_ok()

    def get_result(self) -> dict:
        inside = (
            self._lat is not None
            and self._lon is not None
            and kad_contains(self._lon, self._lat)
        )
        return {
            "Адрес": self._address or "",
            "Внутри КАД": inside,
            "Расстояние до КАД": None if inside else self._distance_km,
            "Расстояние маршрута м": self._distance_m,
            "lat": self._lat,
            "lon": self._lon,
            "Маршрут координаты": list(self._route_coords) if self._route_coords else None,
        }
