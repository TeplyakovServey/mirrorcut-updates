# -*- coding: utf-8 -*-
"""Выбор петли для фасада: список из БД, фото 300×300, кнопка «Обновить цену» по ссылке (парсер MDM)."""
import sys
import os

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap

from cfg_loader import get_base_dir
from db_main import (
    facades_get_all_hinges,
    facades_update_hinge_price,
    facades_ensure_harmony_plus_hinges_if_empty,
)
from ui.facade_profile_dialog import (
    _FASAD_IMG_EXTS_MDM,
    _facade_series_use_extended_photo_lookup,
    _first_hit_in_img_dir,
    _series_tokens_for_file_prefixes,
)


def hinge_uses_primary_image_catalog(hinge):
    """True — стиль HARMONY PLUS (в приоритете только JPG в img_hinges/img)."""
    if not hinge:
        return False
    if hinge.get("catalog_primary") is True:
        return True
    ser = str(hinge.get("series") or "").strip().upper()
    nm = str(hinge.get("name") or "").strip().upper()
    return ("HARMONY PLUS" in ser) or ("HARMONY PLUS" in nm)


def resolve_hinge_image_path(photo_number, *, others_catalog=False, hinge=None):
    """
    Путь к картинке петли: FASAD/img_hinges и FASAD/img (как в окне «Цены»).
    Если передана строка hinge из БД — «Другие петли» / HARMONY определяются по catalog_primary и серии.
    Для серий Nimbus / Prisma / Futuris / Turis — доп. имена файлов (как у профилей).
    """
    n = str(photo_number or "").strip()
    if not n:
        return None
    if hinge is not None:
        others_catalog = not hinge_uses_primary_image_catalog(hinge)

    base_dir = get_base_dir()
    folders = ("img_hinges", "img")
    exts = (".png", ".jpg", ".jpeg", ".webp") if others_catalog else (".jpg", ".jpeg")

    for folder in folders:
        base = os.path.join(base_dir, "FASAD", folder)
        for ext in exts:
            p = os.path.join(base, n + ext)
            if os.path.isfile(p):
                return p

    for stem in (n.lower(), n.replace(" ", "_"), n.replace("_", "-")):
        if stem == n:
            continue
        for folder in folders:
            base = os.path.join(base_dir, "FASAD", folder)
            for ext in exts:
                p = os.path.join(base, stem + ext)
                if os.path.isfile(p):
                    return p

    ser = (hinge or {}).get("series") if hinge else None
    if hinge and _facade_series_use_extended_photo_lookup(ser):
        stems = []
        variants = [n, n.lower(), n.replace(" ", "_"), n.replace("_", "-")]
        for vn in variants:
            if not vn:
                continue
            for tok in _series_tokens_for_file_prefixes(ser):
                stems.extend(("%s_%s" % (tok, vn), "%s%s" % (tok, vn), "%s-%s" % (tok, vn)))
        for folder in folders:
            img_dir = os.path.join(base_dir, "FASAD", folder)
            hit = _first_hit_in_img_dir(img_dir, stems, _FASAD_IMG_EXTS_MDM)
            if hit:
                return hit

    fallback = ".png" if others_catalog else ".jpg"
    return os.path.join(base_dir, "FASAD", "img_hinges", n + fallback)


def _hinge_img_path(photo_number, *, others_catalog=False, hinge=None):
    """Совместимость: при наличии hinge предпочтительно передавать hinge=…"""
    return resolve_hinge_image_path(photo_number, others_catalog=others_catalog, hinge=hinge)


def _fetch_price(url):
    """Возвращает (price_float, None) при успехе или (None, error_message) при ошибке."""
    try:
        if _mp not in sys.path:
            sys.path.insert(0, _mp)
        from FASAD.mdm_parser import fetch_price_from_mdm_url
        price = fetch_price_from_mdm_url(url)
        return (price, None) if price is not None else (None, "Цена на странице не найдена (парсер не распознал формат).")
    except Exception as e:
        return None, str(e)


class FacadeHingeSelectDialog(QDialog):
    COL_NAME = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выбор петли")
        self.setMinimumSize(640, 480)
        self._all_hinges = []
        self._hinges = []
        self._selected = None
        self._catalog_filter = "primary"
        self._build_ui()
        self._apply_window_geometry()

    def _apply_window_geometry(self):
        """Около 80% ширины доступного экрана, по центру."""
        scr = QApplication.primaryScreen()
        if scr is None:
            return
        ag = scr.availableGeometry()
        w = max(640, int(ag.width() * 0.8))
        h = max(480, int(ag.height() * 0.65))
        x = ag.x() + max(0, (ag.width() - w) // 2)
        y = ag.y() + max(0, (ag.height() - h) // 2)
        self.setGeometry(x, y, w, h)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.setMinimumSize(640, 480)
        # Поиск: подстрока в серии, названии, цвете, поставщике
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Поиск:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("№, серия, название, цвет, поставщик — введите часть текста")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_search)
        search_row.addWidget(self.search_edit)
        self.btn_reset = QPushButton("Сброс")
        self.btn_reset.clicked.connect(lambda: self.search_edit.clear())
        search_row.addWidget(self.btn_reset)
        self.btn_other_hinges = QPushButton("Другие петли…")
        self.btn_other_hinges.setCheckable(True)
        self.btn_other_hinges.setToolTip("Показать прежний каталог (не HARMONY PLUS). По умолчанию — только основной каталог.")
        self.btn_other_hinges.toggled.connect(self._on_catalog_toggle)
        search_row.addWidget(self.btn_other_hinges)
        layout.addLayout(search_row)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["№", "Серия", "Название", "Цвет", "Поставщик", "Цена", "Номер фото", "Обновлено"]
        )
        hdr = self.table.horizontalHeader()
        hdr.setStretchLastSection(False)
        for col in range(self.table.columnCount()):
            if col == self.COL_NAME:
                hdr.setSectionResizeMode(col, QHeaderView.Stretch)
            else:
                hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setMinimumHeight(180)
        self.table.setSortingEnabled(True)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.table)

        # Фото петли 300×300 (как в диалоге профиля)
        self.photo_label = QLabel()
        self.photo_label.setFixedSize(300, 300)
        self.photo_label.setAlignment(Qt.AlignCenter)
        self.photo_label.setStyleSheet("background: #f0f0f0; border: 1px solid #ccc;")
        self.photo_label.setText("Выберите петлю")
        layout.addWidget(self.photo_label, 0, Qt.AlignCenter)

        btn_row = QHBoxLayout()
        self.btn_refresh_price = QPushButton("Обновить цену по ссылке")
        self.btn_refresh_price.clicked.connect(self._on_refresh_price)
        btn_row.addWidget(self.btn_refresh_price)
        btn_row.addStretch()
        ok_btn = QPushButton("ОК")
        ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)
        self._reload_catalog_list()

    def _apply_search(self):
        """Фильтр по подстроке в серии, названии, цвете, поставщике. Пустая строка — показать все."""
        q = (self.search_edit.text() or '').strip().lower()
        if not q:
            self._hinges = list(self._all_hinges)
        else:
            self._hinges = [
                h for h in self._all_hinges
                if q in ' '.join([
                    str(h.get('number') or ''),
                    str(h.get('series') or ''),
                    str(h.get('name') or ''),
                    str(h.get('color') or ''),
                    str(h.get('supplier') or ''),
                ]).lower()
            ]
        self._fill_table()

    def _fill_table(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._hinges))
        for i, h in enumerate(self._hinges):
            self.table.setItem(i, 0, QTableWidgetItem(str(h.get('number') or '')))
            self.table.setItem(i, 1, QTableWidgetItem(str(h.get('series') or '')))
            self.table.setItem(i, 2, QTableWidgetItem(str(h.get('name') or '')))
            self.table.setItem(i, 3, QTableWidgetItem(str(h.get('color') or '')))
            self.table.setItem(i, 4, QTableWidgetItem(str(h.get('supplier') or '')))
            price_item = QTableWidgetItem(str(h.get('price')) if h.get('price') is not None else '—')
            if h.get('price') is not None:
                price_item.setData(Qt.UserRole, float(h.get('price')))
            self.table.setItem(i, 5, price_item)
            self.table.setItem(i, 6, QTableWidgetItem(str(h.get('photo_number') or '')))
            up = h.get('price_updated_at')
            self.table.setItem(i, 7, QTableWidgetItem(up.strftime('%d.%m.%Y %H:%M') if hasattr(up, 'strftime') else str(up or '—')))
        self.table.setSortingEnabled(True)
        if self._hinges:
            self.table.selectRow(0)
        self._update_photo()

    def _reload_catalog_list(self):
        self._all_hinges = facades_get_all_hinges(catalog_filter=self._catalog_filter)
        if self._catalog_filter == "primary" and not self._all_hinges:
            facades_ensure_harmony_plus_hinges_if_empty()
            self._all_hinges = facades_get_all_hinges(catalog_filter=self._catalog_filter)
        self._apply_search()

    def _on_catalog_toggle(self, checked):
        """False — основной каталог (HARMONY PLUS); True — остальные петли из БД."""
        self._catalog_filter = "others" if checked else "primary"
        self.btn_other_hinges.setText("Основной каталог" if checked else "Другие петли…")
        self._reload_catalog_list()

    def _load_hinges(self):
        """Перезагрузить из БД и обновить список (после обновления цены)."""
        self._reload_catalog_list()

    def _current_hinge(self):
        """Выбранная петля (по № в первой колонке, чтобы учитывать сортировку)."""
        row = self.table.currentRow()
        if row < 0:
            return None
        it = self.table.item(row, 0)
        num_str = (it.text() if it else '').strip()
        for h in self._hinges:
            if str(h.get('number') or '').strip() == num_str:
                return h
        return None

    def _on_selection_changed(self):
        """Обновить фото при смене выделенной строки."""
        self._update_photo()

    def _update_photo(self):
        h = self._current_hinge()
        if h is None:
            self.photo_label.setPixmap(QPixmap())
            self.photo_label.setText("Выберите петлю")
            return
        path = resolve_hinge_image_path(h.get("photo_number"), hinge=h)
        if path and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.photo_label.setPixmap(scaled)
            else:
                self.photo_label.setPixmap(QPixmap())
                self.photo_label.setText("Нет изображения")
        else:
            self.photo_label.setPixmap(QPixmap())
            self.photo_label.setText("Нет изображения" if h.get('photo_number') else "Выберите петлю")

    def _on_refresh_price(self):
        h = self._current_hinge()
        if h is None:
            QMessageBox.information(self, "Цена", "Выберите петлю в таблице.")
            return
        link = (h.get('link') or '').strip()
        if not link:
            QMessageBox.warning(self, "Цена", "У выбранной петли нет ссылки.")
            return
        QMessageBox.information(self, "Обновление", "Загрузка цены с сайта…")
        price, err = _fetch_price(link)
        if price is None:
            QMessageBox.warning(
                self, "Цена",
                "Не удалось получить цену по ссылке.\n\n%s\n\nПроверьте URL и доступность сайта. При необходимости измените парсер в FASAD/mdm_parser.py."
                % (err or "Неизвестная ошибка")
            )
            return
        facades_update_hinge_price(h['id'], price)
        QMessageBox.information(self, "Цена", "Цена обновлена: %.2f ₽" % price)
        self._load_hinges()

    def _on_ok(self):
        self._selected = self._current_hinge()
        self.accept()

    def selected_hinge(self):
        return self._selected
