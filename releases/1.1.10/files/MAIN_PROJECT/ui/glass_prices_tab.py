# -*- coding: utf-8 -*-
"""Вкладка «Стекло / зеркало»: редактирование таблиц прайса калькулятора BLOCKS (PostgreSQL)."""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QHeaderView,
    QAbstractItemView,
)

from logic.glass_prices_pg_admin import (
    fetch_table,
    row_dict_to_ui_strings,
    save_table_updates,
)


# Разделы: ключ, SQL-таблица, ORDER BY (фиксированная строка), первичный ключ, подсказка.
GLASS_PRICE_SECTIONS: List[Dict[str, Any]] = [
    {
        "key": "materials",
        "sql": "materials",
        "title": "Материалы (стекло / зеркало)",
        "order": "material_type, material_variant, thickness",
        "pk": ["id"],
        "hint": "Цена за м², статус закалки (как в калькуляторе). Тип, вариант и толщина — справочник номенклатуры.",
    },
    {
        "key": "facet_price",
        "sql": "facet_price",
        "title": "Фацет (₽ за м по размеру фаски)",
        "order": "facet_size",
        "pk": ["id"],
        "hint": "Строки — размер фаски (мм); колонки material_* — цена погонного метра для толщины стекла.",
    },
    {
        "key": "polirovka_price",
        "sql": "polirovka_price",
        "title": "Полировка и шлифовка (₽/м)",
        "order": "thickness_mm",
        "pk": ["thickness_mm"],
        "hint": "В калькуляторе и полировка, и шлифовка берут цену за метр из этой таблицы по толщине.",
    },
    {
        "key": "zakalka_price",
        "sql": "zakalka_price",
        "title": "Закалка (₽/м²)",
        "order": "thickness_mm",
        "pk": ["thickness_mm"],
        "hint": "База закалки за м² по толщине (используется при расчёте услуги «Закалка»).",
    },
    {
        "key": "corner_rounding_price",
        "sql": "corner_rounding_price",
        "title": "Скругление углов",
        "order": "thickness",
        "pk": ["thickness"],
        "hint": "Цены по толщине и радиусу скругления (столбцы r_*).",
    },
    {
        "key": "manual_edge_processing_price",
        "sql": "manual_edge_processing_price",
        "title": "Ручная обработка короткой кромки",
        "order": "thickness_mm",
        "pk": ["thickness_mm"],
        "hint": "Фиксированная доплата при короткой стороне менее 75 мм (полировка/шлифовка по периметру).",
    },
    {
        "key": "plenka",
        "sql": "plenka",
        "title": "Плёнка",
        "order": "id",
        "pk": ["id"],
        "hint": "Наименование вида плёнки и цена за м² для блока «Плёнка».",
    },
    {
        "key": "pokraska",
        "sql": "pokraska",
        "title": "Покраска",
        "order": "id",
        "pk": ["id"],
        "hint": "Цвет покраски и цена за изделие.",
    },
    {
        "key": "sandblasting_price",
        "sql": "sandblasting_price",
        "title": "Пескоструй",
        "order": "type",
        "pk": ["type"],
        "hint": "Тип обработки в нижнем регистре (как в коде): сплошное матирование, рисунок и т.д.",
    },
    {
        "key": "photo_print_price",
        "sql": "photo_print_price",
        "title": "Фотопечать",
        "order": "service",
        "pk": ["service"],
        "hint": "Обычно одна строка «Установка фотопечати» и цена установки.",
    },
    {
        "key": "drilling_prices",
        "sql": "drilling_prices",
        "title": "Сверление (отверстия)",
        "order": "id",
        "pk": ["id"],
        "hint": "Матрица диаметров и толщин; используется расчётом отверстий.",
    },
    {
        "key": "blocks_furniture",
        "sql": "blocks_furniture",
        "title": "Фурнитура",
        "order": "id",
        "pk": ["id"],
        "hint": "Юр./физ. цены, полкодержатели при необходимости по толщине материала.",
    },
    {
        "key": "blocks_uf_skleyka_prices",
        "sql": "blocks_uf_skleyka_prices",
        "title": "УФ-склейка",
        "order": "thickness_mm",
        "pk": ["id"],
        "hint": "Строка thickness_mm=0 — цены за петлю (наклейка/снятие); остальные — ₽/м по толщине.",
    },
    {
        "key": "blocks_virez_prices",
        "sql": "blocks_virez_prices",
        "title": "Вырезы по сложности",
        "order": "id",
        "pk": ["id"],
        "hint": "Код категории (simple/medium/complex), название и цена за вырез.",
    },
    {
        "key": "packaging_price",
        "sql": "packaging_price",
        "title": "Упаковка",
        "order": "packaging_type",
        "pk": ["packaging_type"],
        "hint": "Тип упаковки и цена; в расчёте ключи приводятся к нижнему регистру.",
    },
]

# Тарифы выезда — отдельная вкладка «Работы» в диалоге «Цены».
WORKS_PRICE_SECTIONS: List[Dict[str, Any]] = [
    {
        "key": "delivery_price",
        "sql": "delivery_price",
        "title": "Доставка, замер и монтаж",
        "order": "name",
        "pk": ["id"],
        "hint": (
            "Строки: «В пределах КАД», «За КАД база», «За 1 км» — доставка вне КАД; "
            "«Замер» — выезд замерщика; «Монтаж» — фиксированная цена монтажа (по умолчанию 2000 ₽)."
        ),
    },
]


def _effective_pk(meta: Dict[str, Any], cols: List[str]) -> List[str]:
    """Подбор первичного ключа, если в БД нет ожидаемого столбца (например id)."""
    want = list(meta.get("pk") or [])
    if want and all(w in cols for w in want):
        return want
    if "id" in cols:
        return ["id"]
    alt = {
        "delivery_price": ["name"],
        "materials": ["material_type", "material_variant", "thickness"],
        "photo_print_price": ["service"],
        "sandblasting_price": ["type"],
        "packaging_price": ["packaging_type"],
        "polirovka_price": ["thickness_mm"],
        "zakalka_price": ["thickness_mm"],
        "corner_rounding_price": ["thickness"],
        "blocks_uf_skleyka_prices": ["thickness_mm"],
    }
    key = meta.get("key") or ""
    cand = alt.get(key)
    if cand and all(x in cols for x in cand):
        return list(cand)
    return want


class GlassPricesTab(QWidget):
    """Вкладка управления прайсами стекла (отдельно от фасадов: без импорта Excel и без % ко всему)."""

    def __init__(self, parent=None, sections: Optional[List[Dict[str, Any]]] = None):
        super().__init__(parent)
        self._sections_list = sections if sections is not None else GLASS_PRICE_SECTIONS
        self._sections = {s["key"]: s for s in self._sections_list}
        self._columns: List[str] = []
        self._original_rows: List[Dict[str, Any]] = []
        self._pk_effective: List[str] = []
        self._hide_cols: set = set()
        self._current_section_key: Optional[str] = None
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Раздел:"))
        self.combo = QComboBox()
        for s in self._sections_list:
            self.combo.addItem(s["title"], s["key"])
        self.combo.currentIndexChanged.connect(self._on_section_changed)
        row.addWidget(self.combo, 1)
        lay.addLayout(row)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #37474f; padding: 4px;")
        lay.addWidget(self.hint)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked | QAbstractItemView.EditKeyPressed
        )
        lay.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        self.btn_reload = QPushButton("Обновить из базы")
        self.btn_reload.clicked.connect(self._reload_current)
        self.btn_save = QPushButton("Сохранить изменения в этой таблице")
        self.btn_save.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: white; font-weight: bold; padding: 8px 14px; border-radius: 6px; }"
        )
        self.btn_save.clicked.connect(self._save_current)
        btn_row.addWidget(self.btn_reload)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_save)
        lay.addLayout(btn_row)

        self._on_section_changed()

    def _section_meta(self) -> Dict[str, Any]:
        key = self.combo.currentData()
        return self._sections.get(key) or self._sections_list[0]

    def _on_section_changed(self, *_a):
        meta = self._section_meta()
        self._current_section_key = meta["key"]
        hint_txt = meta.get("hint") or ""
        self.hint.setText("<b>%s</b><br/>%s" % (meta["title"], hint_txt))
        self._reload_current()

    def _reload_current(self):
        meta = self._section_meta()
        sql = meta["sql"]
        order = meta["order"]
        try:
            cols, rows = fetch_table(sql, order)
        except Exception as e:
            QMessageBox.warning(self, "База данных", str(e))
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self._columns = []
            self._original_rows = []
            self._pk_effective = []
            self._hide_cols = set()
            return

        self._columns = cols
        self._original_rows = [copy.deepcopy(r) for r in rows]
        self._pk_effective = _effective_pk(meta, cols)

        hide: set = set()
        for c in cols:
            lc = (c or "").lower()
            if lc == "data":
                hide.add(c)

        self._hide_cols = hide
        vis_cols = [c for c in cols if c not in hide]

        self.table.setColumnCount(len(vis_cols))
        labels = [_column_label(meta["sql"], c) for c in vis_cols]
        self.table.setHorizontalHeaderLabels(labels)
        self.table.setRowCount(len(rows))

        for ri, row in enumerate(rows):
            ui = row_dict_to_ui_strings(row)
            for ci, cname in enumerate(vis_cols):
                txt = ui.get(cname, "")
                if txt == "<binary>":
                    it = QTableWidgetItem("(не редактируется)")
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                else:
                    it = QTableWidgetItem(txt)
                it.setData(Qt.UserRole, cname)
                self.table.setItem(ri, ci, it)
        self.table.resizeColumnsToContents()

    def _read_ui_rows(self) -> List[Dict[str, str]]:
        vis = [c for c in self._columns if c not in self._hide_cols]
        out: List[Dict[str, str]] = []
        for ri in range(self.table.rowCount()):
            d: Dict[str, str] = {}
            for ci, cname in enumerate(vis):
                it = self.table.item(ri, ci)
                d[cname] = (it.text() if it else "").strip()
            out.append(d)
        return out

    def _save_current(self):
        meta = self._section_meta()
        sql = meta["sql"]
        pk = list(self._pk_effective)
        vis_cols = [c for c in self._columns if c not in self._hide_cols]

        if len(self._original_rows) != self.table.rowCount():
            QMessageBox.warning(
                self,
                "Сохранение",
                "Число строк изменилось. Нажмите «Обновить из базы» и правьте существующие строки.",
            )
            return

        ui_partial = self._read_ui_rows()
        for p in pk:
            for orig in self._original_rows:
                if p not in orig:
                    QMessageBox.warning(
                        self,
                        "Сохранение",
                        "Не удалось определить ключ «%s» в строках таблицы." % p,
                    )
                    return

        full_ui: List[Dict[str, str]] = []
        for ri, partial in enumerate(ui_partial):
            orig = self._original_rows[ri]
            merged: Dict[str, str] = {}
            for c in self._columns:
                if c in self._hide_cols:
                    merged[c] = row_dict_to_ui_strings(orig).get(c, "")
                else:
                    merged[c] = partial.get(c, "")
            full_ui.append(merged)

        try:
            n = save_table_updates(sql, pk, vis_cols, self._original_rows, full_ui)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка сохранения", str(e))
            return

        QMessageBox.information(self, "Сохранение", "Обновлено записей (строк): %d" % n)
        self._reload_current()


def _column_label(sql_table: str, col: str) -> str:
    c = col or ""
    RU = {
        "material_type": "Тип материала",
        "material_variant": "Цвет / вариант",
        "thickness": "Толщина (мм)",
        "thickness_mm": "Толщина (мм)",
        "price": "Цена",
        "status_zakalka": "Закалка (0/1)",
        "name": "Название тарифа",
        "facet_size": "Размер фаски (мм)",
        "material_4mm": "4 мм ₽/м",
        "material_5mm": "5 мм ₽/м",
        "material_6mm": "6 мм ₽/м",
        "material_8mm": "8 мм ₽/м",
        "material_10mm": "10 мм ₽/м",
        "price_per_meter": "Цена ₽/м",
        "r_3_10": "Радиус 3–10 мм",
        "r_11_20": "Радиус 11–20 мм",
        "r_21_35": "Радиус 21–35 мм",
        "r_36_50": "Радиус 36–50 мм",
        "r_51_100": "Радиус 51–100 мм",
        "price_rub": "Цена ₽",
        "type": "Тип (ключ)",
        "service": "Услуга",
        "packaging_type": "Тип упаковки",
        "category_code": "Код категории",
        "title_ru": "Название",
        "price_per_meter_rub": "Цена ₽/м",
        "hinge_paste_one_rub": "Петля наклейка ₽",
        "hinge_remove_one_rub": "Петля снятие ₽",
        "price_legal": "Цена юр. ₽",
        "price_individual": "Цена физ. ₽",
        "photo_base": "Фото (база)",
        "source_url": "Ссылка",
        "is_shelf_holder": "Полкодержатель",
        "diameter_range": "Диапазон D",
    }
    return RU.get(c, c)
