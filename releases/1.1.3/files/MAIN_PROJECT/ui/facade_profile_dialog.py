# -*- coding: utf-8 -*-
"""Окно выбора профиля фасада: фильтры, список, фото 300×300, Назад / ОК / Вперед."""
import sys
import os
import re
import glob

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap, QColor

from cfg_loader import get_base_dir
from db_main import facades_get_all_profiles, facades_get_profile_by_id

_FASAD_IMG_EXTS_DEFAULT = ('.png', '.jpg', '.jpeg', '.webp')
_FASAD_IMG_EXTS_MDM = ('.jpg', '.jpeg', '.png', '.webp')


def _facade_series_is_harmony_plus(series):
    """Серия Harmony+ — оставляем только существующую логику имён файлов (цифры + slug)."""
    s = re.sub(r"\s+", "", (series or "").lower())
    return "harmony" in s


def _facade_series_use_extended_photo_lookup(series):
    """Nimbus / Prisma / Futuris / Turis: дополнительные шаблоны имён (часто .jpg и префикс серии в имени файла)."""
    if not (series or "").strip() or _facade_series_is_harmony_plus(series):
        return False
    s = (series or "").upper().replace(" ", "").replace("_", "")
    return any(k in s for k in ("NIMBUS", "PRISMA", "FUTURIS", "TURIS"))


def _series_tokens_for_file_prefixes(series):
    """Кандидаты префикса в имени файла (латиница/цифры из названия серии)."""
    s = (series or "").strip()
    if not s:
        return []
    raw = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_")
    out = []
    for t in (raw, raw.lower(), raw.upper()):
        if t and t not in out:
            out.append(t)
    alnum = re.sub(r"[^a-zA-Z0-9]+", "", s)
    if alnum and alnum.lower() not in [x.lower() for x in out]:
        out.append(alnum)
    return out


def _first_hit_in_img_dir(img_dir, stems, exts):
    for stem in stems:
        if not stem:
            continue
        for ext in exts:
            p = os.path.join(img_dir, stem + ext)
            if os.path.isfile(p):
                return p
    return None


# Доп. варианты имён файлов на диске для суффикса из БД (латиница в БД ↔ как сохранили картинку).
_PROFILE_PHOTO_SLUG_ALIASES = {
    'silver': ('serebro', 'silver', 'ser'),
    'gold': ('zoloto', 'gold', 'zolot'),
    'champagne': ('champagne', 'shampan', 'champan', 'champ'),
    'cognac': ('cognac', 'konjak', 'konyak'),
    'black': ('black', 'cherniy', 'chorniy', 'chern'),
    'white': ('white', 'belyi', 'beliy', 'bel'),
    'bronze': ('bronze', 'bronza'),
    'brown': ('brown', 'korich'),
    'graphite': ('graphite', 'grafit'),
    'anthracite': ('anthracite', 'antratsit'),
    'chrome': ('chrome', 'hrom'),
    'satin': ('satin', 'sat'),
    'nickel': ('nickel', 'nikel'),
    'matte': ('matte', 'mat', 'matov'),
    'coffee': ('coffee', 'kofe'),
}


def _facade_extended_profile_image_path(photo_number, series, img_dir):
    """
    Доп. поиск для Nimbus / Prisma / Futuris / Turis: префикс серии в имени, .jpg в приоритете,
    дефис вместо подчёркивания, «склейка» без разделителя.
    """
    n = str(photo_number or '').strip()
    if not n or not os.path.isdir(img_dir):
        return None
    exts = _FASAD_IMG_EXTS_MDM
    tokens = _series_tokens_for_file_prefixes(series)
    stems = []
    variants = [n, n.lower(), n.replace(' ', '_'), n.replace('_', '-')]
    for vn in variants:
        if not vn:
            continue
        for tok in tokens:
            stems.append('%s_%s' % (tok, vn))
            stems.append('%s%s' % (tok, vn))
            stems.append('%s-%s' % (tok, vn))
    hit = _first_hit_in_img_dir(img_dir, stems, exts)
    if hit:
        return hit

    m = re.match(r'^(\d+)(?:_(.+))?$', n)
    digits = m.group(1) if m else ''
    rest = (m.group(2) or '').strip() if m else ''
    if digits and tokens:
        rest_l = rest.lower()
        for ext in exts:
            for path in sorted(glob.glob(os.path.join(img_dir, '*' + ext))):
                if not os.path.isfile(path):
                    continue
                base_lc = os.path.splitext(os.path.basename(path))[0].lower()
                if digits not in base_lc:
                    continue
                if rest_l and rest_l not in base_lc:
                    continue
                if not any(t.lower() in base_lc for t in tokens):
                    continue
                return path
    return None


def _fasad_img_path(photo_number, series=None, name=None):
    """
    Картинка профиля в FASAD/img.

    Порядок как раньше + новые файлы по цвету:
    1) Точное имя из БД (как старый _warehouse_fasad_img_path: n.png / n.jpg …).
    2) Ведущие цифры из photo_number — один снимок на номер фото из Excel (12.png),
       если отдельного файла с суффиксом нет.
    3) Варианты с суффиксом цвета, алиасы, glob по префиксу.
    4) Для серий Nimbus / Prisma / Futuris / Turis — дополнительные шаблоны (не трогаем Harmony+).

    series, name — опционально; без них поведение как раньше для совместимости.
    """
    _ = name
    img_dir = os.path.join(get_base_dir(), 'FASAD', 'img')
    n = str(photo_number or '').strip()
    if not n:
        return None
    exts = _FASAD_IMG_EXTS_DEFAULT

    # 1) Старый складской / исходный вариант: имя = значение photo_number
    hit = _first_hit_in_img_dir(img_dir, [n, n.lower(), n.replace(' ', '_')], exts)
    if hit:
        return hit

    # 2) Старый каталог: только «№ фото» (цифры в начале) — общий файл для строки Excel
    m = re.match(r'^(\d+)', n)
    digits = m.group(1) if m else ''
    if digits:
        hit = _first_hit_in_img_dir(img_dir, [digits], exts)
        if hit:
            return hit

    # 3) Новые имена base_slug + алиасы
    stems = []
    if '_' in n:
        prefix, slug = n.split('_', 1)
        combo = '%s_%s' % (prefix, slug.lower())
        for t in (combo, n, n.lower()):
            if t and t not in stems:
                stems.append(t)
        for alt in _PROFILE_PHOTO_SLUG_ALIASES.get(slug.strip().lower(), ()):
            t = '%s_%s' % (prefix, alt)
            if t not in stems:
                stems.append(t)
    hit = _first_hit_in_img_dir(img_dir, stems, exts)
    if hit:
        return hit

    # 4) Любой файл «префикс_*» при совпадении суффикса или единственный кандидат
    if '_' in n and os.path.isdir(img_dir):
        prefix, slug = n.split('_', 1)
        slug_l = slug.lower()
        if prefix.isdigit():
            matches = []
            for ext in exts:
                matches.extend(glob.glob(os.path.join(img_dir, prefix + '_*' + ext)))
            matches = [p for p in matches if os.path.isfile(p)]
            for path in sorted(matches):
                base_lc = os.path.splitext(os.path.basename(path))[0].lower()
                if slug_l in base_lc or base_lc.endswith('_' + slug_l):
                    return path
            if len(matches) == 1:
                return matches[0]

    if series and _facade_series_use_extended_photo_lookup(series):
        hit = _facade_extended_profile_image_path(n, series, img_dir)
        if hit:
            return hit

    return os.path.join(img_dir, n + '.png')


class FacadeProfileSelectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выбор профиля")
        self.setMinimumSize(760, 620)
        self._profiles = []
        self._current_index = -1
        self._selected_profile = None
        self._build_ui()
        self._apply_filters()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        # Фильтры
        f_row = QHBoxLayout()
        f_row.addWidget(QLabel("Серия:"))
        self.edit_series = QLineEdit()
        self.edit_series.setPlaceholderText("фильтр")
        self.edit_series.setMaximumWidth(120)
        self.edit_series.textChanged.connect(self._apply_filters)
        f_row.addWidget(self.edit_series)
        f_row.addWidget(QLabel("Название:"))
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("фильтр")
        self.edit_name.setMaximumWidth(120)
        self.edit_name.textChanged.connect(self._apply_filters)
        f_row.addWidget(self.edit_name)
        f_row.addWidget(QLabel("Цвет:"))
        self.edit_color = QLineEdit()
        self.edit_color.setMaximumWidth(100)
        self.edit_color.textChanged.connect(self._apply_filters)
        f_row.addWidget(self.edit_color)
        f_row.addWidget(QLabel("Поставщик:"))
        self.edit_supplier = QLineEdit()
        self.edit_supplier.setMaximumWidth(100)
        self.edit_supplier.textChanged.connect(self._apply_filters)
        f_row.addWidget(self.edit_supplier)
        f_row.addStretch()
        layout.addLayout(f_row)

        self.status_label = QLabel("Список профилей — введите фильтры или оставьте пусто для показа всех.")
        layout.addWidget(self.status_label)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["ID", "Серия", "Название", "Цвет", "Цена/м"])
        self.table.setColumnHidden(0, True)  # ID не показываем пользователю
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setMinimumHeight(220)
        self.table.setSortingEnabled(True)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.table.setStyleSheet(
            "QTableWidget { gridline-color: #d9e6ff; selection-background-color: #d0ecff; }"
        )
        layout.addWidget(self.table)

        # Фото 300×300
        self.photo_label = QLabel()
        self.photo_label.setFixedSize(300, 300)
        self.photo_label.setAlignment(Qt.AlignCenter)
        self.photo_label.setStyleSheet("background: #f0f0f0; border: 1px solid #ccc;")
        self.photo_label.setText("Выберите профиль")
        layout.addWidget(self.photo_label, 0, Qt.AlignCenter)

        # Назад / ОК / Вперед
        btn_row = QHBoxLayout()
        self.btn_prev = QPushButton("Назад")
        self.btn_prev.clicked.connect(self._on_prev)
        btn_row.addWidget(self.btn_prev)
        btn_row.addStretch()
        self.btn_ok = QPushButton("ОК")
        self.btn_ok.clicked.connect(self._on_ok)
        btn_row.addWidget(self.btn_ok)
        btn_row.addStretch()
        self.btn_next = QPushButton("Вперед")
        self.btn_next.clicked.connect(self._on_next)
        btn_row.addWidget(self.btn_next)
        layout.addLayout(btn_row)

    def _apply_filters(self):
        series = self.edit_series.text().strip() or None
        name = self.edit_name.text().strip() or None
        color = self.edit_color.text().strip() or None
        supplier = self.edit_supplier.text().strip() or None
        self._profiles = facades_get_all_profiles(series=series, name=name, color=color, supplier=supplier)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self._profiles))
        for i, p in enumerate(self._profiles):
            self.table.setItem(i, 0, QTableWidgetItem(str(p.get('id', ''))))
            self.table.setItem(i, 1, QTableWidgetItem(str(p.get('series') or '')))
            self.table.setItem(i, 2, QTableWidgetItem(str(p.get('name') or '')))
            color_item = QTableWidgetItem(str(p.get('color') or ''))
            c_text = (p.get('color') or '').strip().lower()
            if 'черн' in c_text:
                color_item.setBackground(QColor(70, 70, 70))
            elif 'бел' in c_text:
                color_item.setBackground(QColor(245, 245, 245))
            elif 'сереб' in c_text:
                color_item.setBackground(QColor(205, 210, 218))
            elif 'золот' in c_text:
                color_item.setBackground(QColor(240, 216, 120))
            elif 'коньяк' in c_text:
                color_item.setBackground(QColor(198, 148, 104))
            elif 'шампан' in c_text:
                color_item.setBackground(QColor(232, 210, 166))
            self.table.setItem(i, 3, color_item)
            self.table.setItem(i, 4, QTableWidgetItem(str(p.get('price_per_meter') or '')))
        self.table.setSortingEnabled(True)
        self._current_index = 0 if self._profiles else -1
        if self._profiles:
            self.table.selectRow(0)
        n = len(self._profiles)
        if any([series, name, color, supplier]):
            self.status_label.setText("Показано профилей (по фильтру): %d" % n)
        else:
            self.status_label.setText("Показано профилей: %d — введите серию/название/цвет/поставщика для фильтрации." % n)
        self._update_photo_and_buttons()

    def _on_selection_changed(self):
        """Обновить фото при смене выделенной строки (клик или клавиши)."""
        row = self.table.currentRow()
        if row < 0:
            self._current_index = -1
        else:
            it = self.table.item(row, 0)
            id_str = it.text() if it else ''
            self._current_index = next((i for i, p in enumerate(self._profiles) if str(p.get('id')) == id_str), -1)
        self._update_photo_and_buttons()

    def _update_photo_and_buttons(self):
        self.btn_prev.setEnabled(self._current_index > 0 and len(self._profiles) > 0)
        self.btn_next.setEnabled(self._current_index >= 0 and self._current_index < len(self._profiles) - 1)
        self.btn_ok.setEnabled(self._current_index >= 0 and len(self._profiles) > 0)
        if self._current_index < 0 or self._current_index >= len(self._profiles):
            self.photo_label.setPixmap(QPixmap())
            self.photo_label.setText("Выберите профиль")
            return
        p = self._profiles[self._current_index]
        path = _fasad_img_path(
            p.get('photo_number'),
            series=p.get('series'),
            name=p.get('name'),
        )
        if path and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                scaled = pix.scaled(300, 300, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.photo_label.setPixmap(scaled)
            else:
                self.photo_label.setText("Нет изображения")
        else:
            self.photo_label.setPixmap(QPixmap())
            self.photo_label.setText("Фото %s" % (p.get('photo_number') or '—'))

    def _select_row_by_profile_index(self, idx):
        """Выбрать строку таблицы, в которой профиль с индексом idx (учёт сортировки)."""
        if idx < 0 or idx >= len(self._profiles):
            return
        target_id = str(self._profiles[idx].get('id'))
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it and it.text() == target_id:
                self.table.selectRow(r)
                break

    def _on_prev(self):
        if self._current_index > 0:
            self._current_index -= 1
            self._select_row_by_profile_index(self._current_index)
            self._update_photo_and_buttons()

    def _on_next(self):
        if self._current_index < len(self._profiles) - 1:
            self._current_index += 1
            self._select_row_by_profile_index(self._current_index)
            self._update_photo_and_buttons()

    def _on_ok(self):
        if self._current_index >= 0 and self._current_index < len(self._profiles):
            self._selected_profile = self._profiles[self._current_index]
        self.accept()

    def selected_profile(self):
        return self._selected_profile
