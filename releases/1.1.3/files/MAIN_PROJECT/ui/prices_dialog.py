# -*- coding: utf-8 -*-
"""Диалог управления ценами: вкладки «Фасады» и «Стекло / зеркало» (PostgreSQL калькулятора)."""
import sys
import os
import math

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QWidget, QLineEdit,
    QMessageBox, QProgressDialog, QFileDialog,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap

from cfg_loader import get_base_dir
from db_main import (
    ROLE_ADMIN,
    facades_get_all_profiles,
    facades_get_all_hinges,
    facades_get_all_screws,
    facades_get_all_plates,
    facades_get_all_seal,
    facades_get_all_corners,
    facades_get_all_angle_seal,
    facades_update_profile_price,
    facades_update_hinge_price,
    facades_update_screw_price,
    facades_update_plate_price,
    facades_update_seal_price,
    facades_update_corner_price,
    facades_update_angle_seal_price,
    facades_write_profiles_demo_excel,
)
from ui.continental_import_dialog import ContinentalImportDialog
from ui.facade_profile_dialog import _fasad_img_path
from ui.facade_hinge_dialog import resolve_hinge_image_path
from ui.glass_prices_tab import GlassPricesTab


def _facade_price_display_text(value):
    """Целые ₽ для отображения в таблице фасадов: округление вверх (как в БД)."""
    if value is None or value == "":
        return ""
    try:
        return str(int(math.ceil(float(value))))
    except (TypeError, ValueError):
        return str(value)


def _facade_price_parse_optional(text):
    """Текст ячейки → (значение int|None, успех). Пустая ячейка → (None, True). Округление вверх."""
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None, True
    try:
        return int(math.ceil(float(text))), True
    except ValueError:
        return None, False


def _img_path(folder, number, extensions=('.png', '.jpg', '.jpeg')):
    base = os.path.join(get_base_dir(), 'FASAD', folder)
    n = str(number or '').strip()
    if not n:
        return None
    for ext in extensions:
        p = os.path.join(base, n + ext)
        if os.path.isfile(p):
            return p
    return None


def _fmt_price_updated_at(pu):
    """Отображение price_updated_at из БД (если колонка есть)."""
    if pu is None:
        return "—"
    if hasattr(pu, "strftime"):
        try:
            return pu.strftime("%d.%m.%Y %H:%M")
        except Exception:
            return str(pu)
    s = str(pu).strip()
    return s if s else "—"


class PricesDialog(QDialog):
    """Диалог цен. Пока реализована вкладка «Фасады» с несколькими блоками."""
    def __init__(self, user, parent=None):
        super().__init__(parent)
        self._user = user
        self.setWindowTitle("Цены")
        self.setMinimumSize(900, 600)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # Вкладка фасадов
        facades_tab = QWidget()
        facades_layout = QVBoxLayout(facades_tab)

        # Кнопки выбора таблицы
        buttons_row = QHBoxLayout()
        self.btn_show_profiles = QPushButton("Профили")
        self.btn_show_hinges = QPushButton("Петли")
        self.btn_show_screws = QPushButton("Винты к уголкам")
        self.btn_show_plates = QPushButton("Площадки")
        self.btn_show_seal = QPushButton("Уплотнитель")
        self.btn_show_corners = QPushButton("Уголки")
        self.btn_show_angle_seal = QPushButton("уголки_уплотнитель")
        for b in (
            self.btn_show_profiles,
            self.btn_show_hinges,
            self.btn_show_screws,
            self.btn_show_plates,
            self.btn_show_seal,
            self.btn_show_corners,
            self.btn_show_angle_seal,
        ):
            buttons_row.addWidget(b)
        buttons_row.addStretch()
        facades_layout.addLayout(buttons_row)

        # Блок изменения цен для текущего типа фасадов (только текущая таблица)
        block_percent_row = QHBoxLayout()
        block_percent_row.addWidget(QLabel("Текущий блок, %:"))
        self.block_percent_edit = QLineEdit()
        self.block_percent_edit.setFixedWidth(60)
        block_percent_row.addWidget(self.block_percent_edit)
        self.block_percent_apply = QPushButton("ОК")
        block_percent_row.addWidget(self.block_percent_apply)
        block_percent_row.addStretch()
        facades_layout.addLayout(block_percent_row)

        # Верх/низ: таблица и превью
        splitter = QSplitter(Qt.Vertical)
        facades_layout.addWidget(splitter, 1)

        # Общая область для таблиц (каждая таблица занимает всю ширину, видна только одна)
        tables_container = QWidget()
        tables_layout = QVBoxLayout(tables_container)
        tables_layout.setContentsMargins(0, 0, 0, 0)

        self.profiles_table = QTableWidget()
        self.profiles_table.setColumnCount(6)
        self.profiles_table.setHorizontalHeaderLabels(
            ["Серия", "Название", "Цвет", "Цена/м", "№ фото", "Обновлено"]
        )
        self.profiles_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.profiles_table.setSelectionMode(QTableWidget.SingleSelection)
        tables_layout.addWidget(self.profiles_table)

        self.hinges_table = QTableWidget()
        self.hinges_table.setColumnCount(7)
        self.hinges_table.setHorizontalHeaderLabels(
            ["№", "Серия", "Название", "Цена", "№ фото", "Ссылка", "Обновлено"]
        )
        self.hinges_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.hinges_table.setSelectionMode(QTableWidget.SingleSelection)
        tables_layout.addWidget(self.hinges_table)

        self.screws_table = QTableWidget()
        self.screws_table.setColumnCount(7)
        self.screws_table.setHorizontalHeaderLabels(
            ["№", "Серия", "Название", "Цена", "№ фото", "Ссылка", "Обновлено"]
        )
        self.screws_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.screws_table.setSelectionMode(QTableWidget.SingleSelection)
        tables_layout.addWidget(self.screws_table)

        self.plates_table = QTableWidget()
        self.plates_table.setColumnCount(8)
        self.plates_table.setHorizontalHeaderLabels(
            ["№", "Серия", "Название", "Цвет", "Цена", "№ фото", "Ссылка", "Обновлено"]
        )
        self.plates_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.plates_table.setSelectionMode(QTableWidget.SingleSelection)
        tables_layout.addWidget(self.plates_table)

        self.seal_table = QTableWidget()
        self.seal_table.setColumnCount(7)
        self.seal_table.setHorizontalHeaderLabels(
            ["№", "Серия", "Название", "Цена", "№ фото", "Ссылка", "Обновлено"]
        )
        self.seal_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.seal_table.setSelectionMode(QTableWidget.SingleSelection)
        tables_layout.addWidget(self.seal_table)

        self.corners_table = QTableWidget()
        self.corners_table.setColumnCount(7)
        self.corners_table.setHorizontalHeaderLabels(
            ["Серия", "Название", "Цена", "№ фото", "Ссылка", "Поставщик", "Обновлено"]
        )
        self.corners_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.corners_table.setSelectionMode(QTableWidget.SingleSelection)
        tables_layout.addWidget(self.corners_table)

        self.angle_seal_table = QTableWidget()
        self.angle_seal_table.setColumnCount(4)
        self.angle_seal_table.setHorizontalHeaderLabels(["Тип", "Вариант", "Цена", "Обновлено"])
        self.angle_seal_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.angle_seal_table.setSelectionMode(QTableWidget.SingleSelection)
        tables_layout.addWidget(self.angle_seal_table)

        splitter.addWidget(tables_container)

        # Нижняя часть: превью и информация
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)
        self.preview_label = QLabel("Выберите позицию для просмотра")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedHeight(250)
        self.preview_label.setStyleSheet("background: #f0f0f0; border: 1px solid #ccc;")
        bottom_layout.addWidget(self.preview_label, 0, Qt.AlignCenter)

        self.info_label = QLabel("")
        self.info_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        bottom_layout.addWidget(self.info_label)

        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        tabs.addTab(facades_tab, "Фасады")

        self._glass_prices_tab = GlassPricesTab(self)
        tabs.addTab(self._glass_prices_tab, "Стекло / зеркало")

        # Кнопки управления: проценты | по центру Excel (профили, админ) | сохранить, закрыть
        btn_row = QHBoxLayout()
        self._lbl_facades_pct = QLabel("Все фасады, %:")
        btn_row.addWidget(self._lbl_facades_pct)
        self.facades_percent_edit = QLineEdit()
        self.facades_percent_edit.setFixedWidth(60)
        btn_row.addWidget(self.facades_percent_edit)
        self.facades_percent_apply = QPushButton("ОК")
        btn_row.addWidget(self.facades_percent_apply)

        btn_row.addStretch(1)
        self._profiles_excel_group = QWidget()
        ex_layout = QHBoxLayout(self._profiles_excel_group)
        ex_layout.setContentsMargins(0, 0, 0, 0)
        ex_layout.setSpacing(8)
        self.btn_demo_profiles_excel = QPushButton("Демо EXCEL")
        self.btn_import_profiles_excel = QPushButton("Импорт EXCEL")
        self.btn_demo_profiles_excel.setStyleSheet(
            "QPushButton { background-color: #e8f5e9; color: #1b5e20; font-weight: bold; "
            "padding: 6px 14px; border: 1px solid #66bb6a; border-radius: 4px; }"
            "QPushButton:hover { background-color: #c8e6c9; }"
        )
        self.btn_import_profiles_excel.setStyleSheet(
            "QPushButton { background-color: #e3f2fd; color: #0d47a1; font-weight: bold; "
            "padding: 6px 14px; border: 1px solid #42a5f5; border-radius: 4px; }"
            "QPushButton:hover { background-color: #bbdefb; }"
        )
        ex_layout.addWidget(self.btn_demo_profiles_excel)
        ex_layout.addWidget(self.btn_import_profiles_excel)
        btn_row.addWidget(self._profiles_excel_group)
        btn_row.addStretch(1)

        self.btn_save = QPushButton("Сохранить (фасады)")
        btn_row.addWidget(self.btn_save)
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        self.btn_demo_profiles_excel.clicked.connect(self._on_demo_profiles_excel_clicked)
        self.btn_import_profiles_excel.clicked.connect(self._on_import_profiles_excel_clicked)

        self._tabs = tabs
        self._facades_only_widgets = (
            self._lbl_facades_pct,
            self.facades_percent_edit,
            self.facades_percent_apply,
            self.btn_save,
        )
        tabs.currentChanged.connect(self._on_prices_tab_changed)

        self._configure_facade_prices_tables()

        # Загрузка данных и сигналы
        self._load_facades()
        self._connect_selection_signals()
        self._switch_table('profiles')

        # Кнопки переключения таблиц
        self.btn_show_profiles.clicked.connect(lambda: self._switch_table('profiles'))
        self.btn_show_hinges.clicked.connect(lambda: self._switch_table('hinges'))
        self.btn_show_screws.clicked.connect(lambda: self._switch_table('screws'))
        self.btn_show_plates.clicked.connect(lambda: self._switch_table('plates'))
        self.btn_show_seal.clicked.connect(lambda: self._switch_table('seal'))
        self.btn_show_corners.clicked.connect(lambda: self._switch_table('corners'))
        self.btn_show_angle_seal.clicked.connect(lambda: self._switch_table('angle_seal'))

        # Сохранение и массовые изменения
        self.btn_save.clicked.connect(self._save_current_table_changes)
        self.block_percent_apply.clicked.connect(self._apply_percent_to_current_block)
        self.facades_percent_apply.clicked.connect(self._apply_percent_to_all_facades)

        self._on_prices_tab_changed(tabs.currentIndex())

    def _sync_profiles_excel_bar(self):
        """Демо/импорт Excel только для админа и только на подвкладке «Профили»."""
        is_glass = self._tabs.currentWidget() is getattr(self, "_glass_prices_tab", None)
        show = (
            not is_glass
            and getattr(self, "_facades_kind", "") == "profiles"
            and self._user.get("role") == ROLE_ADMIN
        )
        self._profiles_excel_group.setVisible(show)

    def _on_prices_tab_changed(self, index: int):
        """На вкладке стекла скрываем кнопки, относящиеся только к фасадам."""
        w = self._tabs.widget(index)
        is_glass = w is getattr(self, "_glass_prices_tab", None)
        for ob in getattr(self, "_facades_only_widgets", ()):
            ob.setVisible(not is_glass)
        self._profiles_excel_group.setVisible(False)
        self._sync_profiles_excel_bar()

    def _configure_facade_prices_tables(self):
        """Ширины по умолчанию: серия/№/цена компактно, название тянется; все колонки можно растягивать вручную."""
        stretch = QHeaderView.Stretch
        inter = QHeaderView.Interactive

        def apply_cols(table, stretch_idx, default_widths):
            hdr = table.horizontalHeader()
            hdr.setStretchLastSection(False)
            n = table.columnCount()
            for i in range(n):
                hdr.setSectionResizeMode(i, stretch if i == stretch_idx else inter)
            for i, w in default_widths.items():
                if 0 <= i < n:
                    hdr.resizeSection(i, int(w))

        apply_cols(self.profiles_table, 1, {0: 68, 2: 88, 3: 72, 4: 52, 5: 138})
        apply_cols(self.hinges_table, 2, {0: 42, 1: 72, 3: 64, 4: 48, 5: 110, 6: 136})
        apply_cols(self.screws_table, 2, {0: 42, 1: 72, 3: 64, 4: 48, 5: 110, 6: 136})
        apply_cols(self.plates_table, 2, {0: 40, 1: 70, 3: 82, 4: 64, 5: 48, 6: 108, 7: 136})
        apply_cols(self.seal_table, 2, {0: 42, 1: 72, 3: 64, 4: 48, 5: 110, 6: 136})
        apply_cols(self.corners_table, 1, {0: 72, 2: 64, 3: 48, 4: 100, 5: 88, 6: 136})
        apply_cols(self.angle_seal_table, 1, {0: 118, 2: 72, 3: 136})

    def _load_facades(self):
        """Заполнить таблицы данных для фасадов."""
        self._profiles = facades_get_all_profiles()
        self.profiles_table.setRowCount(len(self._profiles))
        for i, p in enumerate(self._profiles):
            it_series = QTableWidgetItem(str(p.get('series') or ''))
            it_name = QTableWidgetItem(str(p.get('name') or ''))
            it_color = QTableWidgetItem(str(p.get('color') or ''))
            it_price = QTableWidgetItem(_facade_price_display_text(p.get("price_per_meter")))
            it_photo = QTableWidgetItem(str(p.get('photo_number') or ''))
            it_upd = QTableWidgetItem(_fmt_price_updated_at(p.get("price_updated_at")))
            for it in (it_series, it_name, it_color, it_price, it_photo, it_upd):
                it.setData(Qt.UserRole, p.get('id'))
            self.profiles_table.setItem(i, 0, it_series)
            self.profiles_table.setItem(i, 1, it_name)
            self.profiles_table.setItem(i, 2, it_color)
            self.profiles_table.setItem(i, 3, it_price)
            self.profiles_table.setItem(i, 4, it_photo)
            self.profiles_table.setItem(i, 5, it_upd)
        self.profiles_table.setColumnHidden(4, True)  # № фото

        self._hinges = facades_get_all_hinges()
        self.hinges_table.setRowCount(len(self._hinges))
        for i, h in enumerate(self._hinges):
            it_number = QTableWidgetItem(str(h.get('number') or ''))
            it_series = QTableWidgetItem(str(h.get('series') or ''))
            it_name = QTableWidgetItem(str(h.get('name') or ''))
            it_price = QTableWidgetItem(_facade_price_display_text(h.get("price")))
            it_photo = QTableWidgetItem(str(h.get('photo_number') or ''))
            it_link = QTableWidgetItem(str(h.get('link') or ''))
            it_upd = QTableWidgetItem(_fmt_price_updated_at(h.get("price_updated_at")))
            for it in (it_number, it_series, it_name, it_price, it_photo, it_link, it_upd):
                it.setData(Qt.UserRole, h.get('id'))
            self.hinges_table.setItem(i, 0, it_number)
            self.hinges_table.setItem(i, 1, it_series)
            self.hinges_table.setItem(i, 2, it_name)
            self.hinges_table.setItem(i, 3, it_price)
            self.hinges_table.setItem(i, 4, it_photo)
            self.hinges_table.setItem(i, 5, it_link)
            self.hinges_table.setItem(i, 6, it_upd)
        self.hinges_table.setColumnHidden(4, True)
        # Винты к уголкам
        self._screws = facades_get_all_screws()
        self.screws_table.setRowCount(len(self._screws))
        for i, s in enumerate(self._screws):
            it_number = QTableWidgetItem(str(s.get('number') or ''))
            it_series = QTableWidgetItem(str(s.get('series') or ''))
            it_name = QTableWidgetItem(str(s.get('name') or ''))
            it_price = QTableWidgetItem(_facade_price_display_text(s.get("price")))
            it_photo = QTableWidgetItem(str(s.get('photo_number') or ''))
            it_link = QTableWidgetItem(str(s.get('link') or ''))
            it_upd = QTableWidgetItem(_fmt_price_updated_at(s.get("price_updated_at")))
            for it in (it_number, it_series, it_name, it_price, it_photo, it_link, it_upd):
                it.setData(Qt.UserRole, s.get('id'))
            self.screws_table.setItem(i, 0, it_number)
            self.screws_table.setItem(i, 1, it_series)
            self.screws_table.setItem(i, 2, it_name)
            self.screws_table.setItem(i, 3, it_price)
            self.screws_table.setItem(i, 4, it_photo)
            self.screws_table.setItem(i, 5, it_link)
            self.screws_table.setItem(i, 6, it_upd)
        self.screws_table.setColumnHidden(4, True)

        # Площадки
        self._plates = facades_get_all_plates()
        self.plates_table.setRowCount(len(self._plates))
        for i, p in enumerate(self._plates):
            it_number = QTableWidgetItem(str(p.get('number') or ''))
            it_series = QTableWidgetItem(str(p.get('series') or ''))
            it_name = QTableWidgetItem(str(p.get('name') or ''))
            it_color = QTableWidgetItem(str(p.get('color') or ''))
            it_price = QTableWidgetItem(_facade_price_display_text(p.get("price")))
            it_photo = QTableWidgetItem(str(p.get('photo_number') or ''))
            it_link = QTableWidgetItem(str(p.get('link') or ''))
            it_upd = QTableWidgetItem(_fmt_price_updated_at(p.get("price_updated_at")))
            for it in (it_number, it_series, it_name, it_color, it_price, it_photo, it_link, it_upd):
                it.setData(Qt.UserRole, p.get('id'))
            self.plates_table.setItem(i, 0, it_number)
            self.plates_table.setItem(i, 1, it_series)
            self.plates_table.setItem(i, 2, it_name)
            self.plates_table.setItem(i, 3, it_color)
            self.plates_table.setItem(i, 4, it_price)
            self.plates_table.setItem(i, 5, it_photo)
            self.plates_table.setItem(i, 6, it_link)
            self.plates_table.setItem(i, 7, it_upd)
        self.plates_table.setColumnHidden(5, True)

        # Уплотнитель
        self._seal = facades_get_all_seal()
        self.seal_table.setRowCount(len(self._seal))
        for i, s in enumerate(self._seal):
            it_number = QTableWidgetItem(str(s.get('number') or ''))
            it_series = QTableWidgetItem(str(s.get('series') or ''))
            it_name = QTableWidgetItem(str(s.get('name') or ''))
            it_price = QTableWidgetItem(_facade_price_display_text(s.get("price")))
            it_photo = QTableWidgetItem(str(s.get('photo_number') or ''))
            it_link = QTableWidgetItem(str(s.get('link') or ''))
            it_upd = QTableWidgetItem(_fmt_price_updated_at(s.get("price_updated_at")))
            for it in (it_number, it_series, it_name, it_price, it_photo, it_link, it_upd):
                it.setData(Qt.UserRole, s.get('id'))
            self.seal_table.setItem(i, 0, it_number)
            self.seal_table.setItem(i, 1, it_series)
            self.seal_table.setItem(i, 2, it_name)
            self.seal_table.setItem(i, 3, it_price)
            self.seal_table.setItem(i, 4, it_photo)
            self.seal_table.setItem(i, 5, it_link)
            self.seal_table.setItem(i, 6, it_upd)
        self.seal_table.setColumnHidden(4, True)

        # Уголки
        self._corners = facades_get_all_corners()
        self.corners_table.setRowCount(len(self._corners))
        for i, c in enumerate(self._corners):
            it_series = QTableWidgetItem(str(c.get('series') or ''))
            it_name = QTableWidgetItem(str(c.get('name') or ''))
            it_price = QTableWidgetItem(_facade_price_display_text(c.get("price")))
            it_photo = QTableWidgetItem(str(c.get('photo_number') or ''))
            it_link = QTableWidgetItem(str(c.get('link') or ''))
            it_supplier = QTableWidgetItem(str(c.get('supplier') or ''))
            it_upd = QTableWidgetItem(_fmt_price_updated_at(c.get("price_updated_at")))
            for it in (it_series, it_name, it_price, it_photo, it_link, it_supplier, it_upd):
                it.setData(Qt.UserRole, c.get('id'))
            self.corners_table.setItem(i, 0, it_series)
            self.corners_table.setItem(i, 1, it_name)
            self.corners_table.setItem(i, 2, it_price)
            self.corners_table.setItem(i, 3, it_photo)
            self.corners_table.setItem(i, 4, it_link)
            self.corners_table.setItem(i, 5, it_supplier)
            self.corners_table.setItem(i, 6, it_upd)
        self.corners_table.setColumnHidden(3, True)

        # Уголки + уплотнитель (сводная таблица)
        self._angle_seal = facades_get_all_angle_seal()
        self.angle_seal_table.setRowCount(len(self._angle_seal))
        for i, a in enumerate(self._angle_seal):
            it_type = QTableWidgetItem(str(a.get('item_type') or ''))
            it_variant = QTableWidgetItem(str(a.get('variant') or ''))
            it_price = QTableWidgetItem(_facade_price_display_text(a.get("price")))
            it_upd = QTableWidgetItem(_fmt_price_updated_at(a.get("price_updated_at")))
            for it in (it_type, it_variant, it_price, it_upd):
                it.setData(Qt.UserRole, a.get('id'))
            self.angle_seal_table.setItem(i, 0, it_type)
            self.angle_seal_table.setItem(i, 1, it_variant)
            self.angle_seal_table.setItem(i, 2, it_price)
            self.angle_seal_table.setItem(i, 3, it_upd)

    def _connect_selection_signals(self):
        """Подключить обновление превью при выборе строки в любой таблице."""
        self.profiles_table.itemSelectionChanged.connect(lambda: self._update_preview_from('profiles'))
        self.hinges_table.itemSelectionChanged.connect(lambda: self._update_preview_from('hinges'))
        self.screws_table.itemSelectionChanged.connect(lambda: self._update_preview_from('screws'))
        self.plates_table.itemSelectionChanged.connect(lambda: self._update_preview_from('plates'))
        self.seal_table.itemSelectionChanged.connect(lambda: self._update_preview_from('seal'))
        self.corners_table.itemSelectionChanged.connect(lambda: self._update_preview_from('corners'))
        self.angle_seal_table.itemSelectionChanged.connect(lambda: self._update_preview_from('angle_seal'))

    def _current_kind(self):
        if self.profiles_table.isVisible():
            return 'profiles'
        if self.hinges_table.isVisible():
            return 'hinges'
        if self.screws_table.isVisible():
            return 'screws'
        if self.plates_table.isVisible():
            return 'plates'
        if self.seal_table.isVisible():
            return 'seal'
        if self.corners_table.isVisible():
            return 'corners'
        if self.angle_seal_table.isVisible():
            return 'angle_seal'
        return None

    def _save_current_table_changes(self):
        kind = self._current_kind()
        if not kind:
            return
        mapping = {
            "profiles": (self.profiles_table, 3, facades_update_profile_price, self._profiles, "price_per_meter"),
            "hinges": (self.hinges_table, 3, facades_update_hinge_price, self._hinges, "price"),
            "screws": (self.screws_table, 3, facades_update_screw_price, self._screws, "price"),
            "plates": (self.plates_table, 4, facades_update_plate_price, self._plates, "price"),
            "seal": (self.seal_table, 3, facades_update_seal_price, self._seal, "price"),
            "corners": (self.corners_table, 2, facades_update_corner_price, self._corners, "price"),
            "angle_seal": (
                self.angle_seal_table,
                2,
                facades_update_angle_seal_price,
                self._angle_seal,
                "price",
            ),
        }
        table, price_col, updater, backing, price_key = mapping[kind]
        id_to_old = {}
        for obj in backing or []:
            oid = obj.get("id")
            if oid is not None:
                id_to_old[int(oid)] = obj.get(price_key)

        def _old_as_int(raw):
            if raw is None or raw == "":
                return None
            try:
                return int(math.ceil(float(raw)))
            except (TypeError, ValueError):
                return None

        rows = table.rowCount()
        updated = 0
        bad_rows = []
        for r in range(rows):
            item_price = table.item(r, price_col)
            if not item_price:
                continue
            any_item = table.item(r, 0) or item_price
            obj_id = any_item.data(Qt.UserRole)
            if not obj_id:
                continue
            oid = int(obj_id)
            new_price, ok = _facade_price_parse_optional(item_price.text())
            if not ok:
                bad_rows.append(r + 1)
                continue
            old_int = _old_as_int(id_to_old.get(oid))
            if new_price == old_int:
                continue
            updater(oid, new_price)
            updated += 1
        if bad_rows:
            QMessageBox.warning(
                self,
                "Сохранение",
                "Пропущены строки с неверной ценой (не число): %s"
                % ", ".join(str(x) for x in bad_rows[:30])
                + (" …" if len(bad_rows) > 30 else ""),
            )
        QMessageBox.information(self, "Сохранение", "Обновлено записей в БД: %d" % updated)
        self._load_facades()

    def _apply_percent_to_table(self, table, price_col, percent):
        rows = table.rowCount()
        for r in range(rows):
            item = table.item(r, price_col)
            if not item:
                continue
            text = (item.text() or '').strip().replace(',', '.')
            try:
                old_price = float(text)
            except ValueError:
                continue
            new_price = int(math.ceil(old_price * (1 + percent / 100.0)))
            item.setText(str(new_price))

    def _apply_percent_to_all_facades(self):
        text = (self.facades_percent_edit.text() or '').strip().replace(',', '.')
        if not text:
            return
        try:
            percent = float(text)
        except ValueError:
            QMessageBox.warning(self, "Ошибка", "Введите корректный процент.")
            return
        res = QMessageBox.question(
            self,
            "Подтверждение",
            "Изменить все цены фасадов на %s%%?" % text,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if res != QMessageBox.Yes:
            return
        self._apply_percent_to_table(self.profiles_table, 3, percent)
        self._apply_percent_to_table(self.hinges_table, 3, percent)
        self._apply_percent_to_table(self.screws_table, 3, percent)
        self._apply_percent_to_table(self.plates_table, 4, percent)
        self._apply_percent_to_table(self.seal_table, 3, percent)
        self._apply_percent_to_table(self.corners_table, 2, percent)
        self._apply_percent_to_table(self.angle_seal_table, 2, percent)

    def _on_demo_profiles_excel_clicked(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить демо Excel",
            "demo_profili_ceny.xlsx",
            "Excel (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path = "%s.xlsx" % path
        ok, err = facades_write_profiles_demo_excel(path)
        if not ok:
            QMessageBox.critical(self, "Демо Excel", err or "Ошибка записи файла")
            return
        QMessageBox.information(
            self,
            "Демо Excel",
            "Сохранено:\n%s\n\nИзмените колонку «Цена/м» и загрузите файл через «Импорт EXCEL»."
            % path,
        )

    def _on_import_profiles_excel_clicked(self):
        d = ContinentalImportDialog(self)
        d.exec_()
        self._load_facades()

    def _apply_percent_to_current_block(self):
        text = (self.block_percent_edit.text() or '').strip().replace(',', '.')
        if not text:
            return
        try:
            percent = float(text)
        except ValueError:
            QMessageBox.warning(self, "Ошибка", "Введите корректный процент.")
            return
        kind = self._current_kind()
        if not kind:
            return
        mapping = {
            'profiles': (self.profiles_table, 3),
            'hinges': (self.hinges_table, 3),
            'screws': (self.screws_table, 3),
            'plates': (self.plates_table, 4),
            'seal': (self.seal_table, 3),
            'corners': (self.corners_table, 2),
            'angle_seal': (self.angle_seal_table, 2),
        }
        table, price_col = mapping[kind]
        self._apply_percent_to_table(table, price_col, percent)

    def _switch_table(self, kind):
        """Показать только одну таблицу на вкладке фасадов."""
        mapping = {
            'profiles': self.profiles_table,
            'hinges': self.hinges_table,
            'screws': self.screws_table,
            'plates': self.plates_table,
            'seal': self.seal_table,
            'corners': self.corners_table,
            'angle_seal': self.angle_seal_table,
        }
        for name, table in mapping.items():
            table.setVisible(name == kind)
        self._facades_kind = kind
        self._sync_profiles_excel_bar()

    def _update_preview_from(self, kind):
        """Показать картинку и основную информацию по выделенной строке."""
        table = {
            'profiles': self.profiles_table,
            'hinges': self.hinges_table,
            'screws': self.screws_table,
            'plates': self.plates_table,
            'seal': self.seal_table,
            'corners': self.corners_table,
            'angle_seal': self.angle_seal_table,
        }.get(kind)
        if table is None:
            return
        row = table.currentRow()
        if row < 0:
            return

        photo_number = ''
        name = ''
        price = ''
        path = None
        extra_info = ""

        if kind == 'profiles' and 0 <= row < len(self._profiles):
            p = self._profiles[row]
            photo_number = str(p.get('photo_number') or '')
            name = str(p.get('name') or '')
            price = _facade_price_display_text(p.get('price_per_meter'))
            extra_info = "\nОбновлено: %s" % _fmt_price_updated_at(p.get("price_updated_at"))
            path = _fasad_img_path(
                photo_number,
                series=p.get("series"),
                name=p.get("name"),
            )
        elif kind == 'hinges' and 0 <= row < len(self._hinges):
            h = self._hinges[row]
            photo_number = str(h.get('photo_number') or '')
            name = str(h.get('name') or '')
            price = _facade_price_display_text(h.get('price'))
            extra_info = "\nОбновлено: %s" % _fmt_price_updated_at(h.get("price_updated_at"))
            path = resolve_hinge_image_path(photo_number, hinge=h)
        elif kind == 'screws' and hasattr(self, '_screws') and 0 <= row < len(self._screws):
            s = self._screws[row]
            photo_number = str(s.get('photo_number') or '')
            name = str(s.get('name') or '')
            price = _facade_price_display_text(s.get('price'))
            extra_info = "\nОбновлено: %s" % _fmt_price_updated_at(s.get("price_updated_at"))
            path = _img_path('img', photo_number)
        elif kind == 'plates' and hasattr(self, '_plates') and 0 <= row < len(self._plates):
            p = self._plates[row]
            photo_number = str(p.get('photo_number') or '')
            name = str(p.get('name') or '')
            price = _facade_price_display_text(p.get('price'))
            extra_info = "\nОбновлено: %s" % _fmt_price_updated_at(p.get("price_updated_at"))
            path = _img_path('img', photo_number)
        elif kind == 'seal' and hasattr(self, '_seal') and 0 <= row < len(self._seal):
            s = self._seal[row]
            photo_number = str(s.get('photo_number') or '')
            name = str(s.get('name') or '')
            price = _facade_price_display_text(s.get('price'))
            extra_info = "\nОбновлено: %s" % _fmt_price_updated_at(s.get("price_updated_at"))
            path = _img_path('img', photo_number)
        elif kind == 'corners' and hasattr(self, '_corners') and 0 <= row < len(self._corners):
            c = self._corners[row]
            photo_number = str(c.get('photo_number') or '')
            name = str(c.get('name') or '')
            price = _facade_price_display_text(c.get('price'))
            extra_info = "\nОбновлено: %s" % _fmt_price_updated_at(c.get("price_updated_at"))
            path = _img_path('img', photo_number)
        elif kind == 'angle_seal' and hasattr(self, '_angle_seal') and 0 <= row < len(self._angle_seal):
            a = self._angle_seal[row]
            item_type = str(a.get('item_type') or '')
            variant = str(a.get('variant') or '')
            unit = str(a.get('unit') or '')
            if unit:
                name = f"{item_type}: {variant} ({unit})"
            else:
                name = f"{item_type}: {variant}"
            price = _facade_price_display_text(a.get('price'))
            extra_info = "\nОбновлено: %s" % _fmt_price_updated_at(a.get("price_updated_at"))
            path = None
        else:
            extra_info = ""
            # Fallback: читаем прямо из таблицы
            it_photo = table.item(row, table.columnCount() - 3)
            photo_number = (it_photo.text() if it_photo else '').strip()
            it_name = table.item(row, 1)
            name = (it_name.text() if it_name else '').strip()
            it_price = table.item(row, 2)
            price = (it_price.text() if it_price else '').strip()
            path = _img_path('img', photo_number)

        # Обновляем превью
        if path and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.preview_label.setPixmap(scaled)
            else:
                self.preview_label.setPixmap(QPixmap())
                self.preview_label.setText("Нет изображения")
        else:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Нет изображения")

        self.info_label.setText(
            "Название: %s\nЦена: %s\n№ фото: %s%s"
            % (name or "—", price or "—", photo_number or "—", extra_info)
        )


