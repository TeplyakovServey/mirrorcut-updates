"""Modal 'Create cut': client from memory cache, parts with preview, add/remove part, Calculate."""
import sys
import os
import subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QComboBox, QScrollArea, QWidget, QFrame, QMessageBox,
    QFileDialog, QGroupBox, QListWidget, QListWidgetItem, QSizePolicy, QRadioButton, QButtonGroup, QGridLayout,
    QCheckBox, QDialogButtonBox, QFormLayout,
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QBrush

from db import models
from ui.warehouse_dialog import GlassWarehouseMaterialPicker
from app_state import filter_clients_by_prefix, get_clients_list, refresh_clients
from logic.cutting_algorithm import (
    compute_cutting_layout_variants,
    recompute_free_rects_from_pieces,
    refresh_cut_segments_for_layout,
)
from logic.pdf_export import generate_cutting_pdf
import json

_MATERIAL_NAMES_CACHE = None
_CLIENT_NAMES_CACHE = None
_THICKNESSES_CACHE = {}
_STOCK_PRESENCE_CACHE = {}


def _cut_pdf_fallback_dir():
    try:
        from app_paths import get_base_dir
        return get_base_dir()
    except Exception:
        pass
    try:
        from cfg_loader import get_base_dir
        return get_base_dir()
    except Exception:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _open_cut_output_file(path):
    if sys.platform.startswith("win"):
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError:
            pass
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)


STYLE = """
    QDialog { background-color: #E6F2FF; }
    QGroupBox { font-weight: bold; padding: 10px; padding-top: 14px; border: 1px solid #7B9BC1; border-radius: 8px; margin-top: 6px; background-color: #F0F8FF; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
    QPushButton {
        background-color: #4682B4; color: white; border: none; border-radius: 5px;
        padding: 6px 14px; font-size: 12px; min-height: 22px; max-height: 28px;
    }
    QPushButton:hover { background-color: #5A9BD5; }
    QPushButton:pressed { background-color: #3A6B94; }
    QPushButton#primary { background-color: #2E7D32; min-height: 28px; max-height: 32px; }
    QPushButton#primary:hover { background-color: #388E3C; }
    QPushButton#danger { background-color: #B22222; min-height: 22px; max-height: 26px; }
    QPushButton#danger:hover { background-color: #CD5C5C; }
"""


# Полные подписи обработки кромок (без Пленки), на одной линии с размером
# При создании реза — 4 стороны с буквами кромок
EDGE_LABELS = {'grinding': 'Ш', 'polishing': 'П', 'facet': 'Ф'}
FACET_SIZES_MM = [5, 10, 15, 20, 25]


class FacetSizeDialog(QDialog):
    """Выбор размера фацета (мм): 5, 10, 15, 20, 25. Обязательный выбор, OK сохраняет."""
    def __init__(self, current_mm=15, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Размер фацета")
        self._value = int(current_mm) if current_mm in FACET_SIZES_MM else 15
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Выберите размер фацета (мм):"))
        self._group = QButtonGroup(self)
        self._radios_by_mm = {}
        for mm in FACET_SIZES_MM:
            rb = QRadioButton("%d мм" % mm)
            if mm == self._value:
                rb.setChecked(True)
            self._group.addButton(rb)
            self._radios_by_mm[mm] = rb
            layout.addWidget(rb)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_facet_mm(self):
        for mm, rb in self._radios_by_mm.items():
            if rb.isChecked():
                return mm
        return 15


class PartPreview(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(320, 320)
        self.setStyleSheet("background-color: #F0F8FF; border: 1px solid #4682B4; border-radius: 6px;")
        self._height_mm = 100
        self._width_mm = 100
        self._quantity = 1
        self._recipient = ""
        self._edge_treatment = {}

    def set_data(self, height_mm, width_mm, quantity, recipient="", edge_treatment=None):
        self._height_mm = max(1, int(height_mm or 100))
        self._width_mm = max(1, int(width_mm or 100))
        self._quantity = max(1, int(quantity or 1))
        self._recipient = recipient or ""
        self._edge_treatment = edge_treatment or {}
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._height_mm <= 0 or self._width_mm <= 0:
            return
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setRenderHint(QPainter.TextAntialiasing)
        outer = 42
        w = self.width() - 2 * outer
        h = self.height() - 2 * outer
        scale = min(w / self._width_mm, h / self._height_mm)
        rw = self._width_mm * scale
        rh = self._height_mm * scale
        x = outer + (w - rw) / 2
        y = outer + (h - rh) / 2
        qp.setPen(QPen(QColor(70, 130, 180), 2))
        qp.setBrush(QColor(176, 196, 222))
        qp.drawRect(int(x), int(y), int(rw), int(rh))
        qp.setPen(QColor(0, 0, 0))
        qp.setFont(QFont("Arial", 8))
        def _edge_label(side):
            v = self._edge_treatment.get(side)
            if v == 'facet':
                return "Ф %s" % (self._edge_treatment.get('facet_mm') or 15)
            return EDGE_LABELS.get(v) if v else None
        # Высота слева и справа (повёрнуто 90°), сразу на одной линии — размер и обработка
        h_str = str(self._height_mm)
        for side, x_pos in [('left', x - 10), ('right', x + rw + 4)]:
            label = _edge_label(side)
            line = h_str + (" " + label if label else "")
            qp.save()
            qp.translate(x_pos, y + rh / 2)
            qp.rotate(-90)
            qp.drawText(int(-8), int(4), line)
            qp.restore()
        # Ширина сверху и снизу, на одной линии — размер и обработка
        w_str = str(self._width_mm)
        for side, y_pos in [('top', y - 6), ('bottom', y + rh + 12)]:
            label = _edge_label(side)
            line = w_str + (" " + label if label else "")
            cx = x + rw / 2
            fm = qp.fontMetrics()
            tw = fm.horizontalAdvance(line)
            qp.drawText(int(cx - tw / 2), int(y_pos), line)
        # Внутри: получатель, кол-во
        qp.setFont(QFont("Arial", 9))
        if self._recipient:
            qp.drawText(int(x + 4), int(y + rh/2 - 8), self._recipient[:16])
        if self._quantity > 1:
            qp.drawText(int(x + 4), int(y + rh/2 + 8), "× %d" % self._quantity)
        qp.end()


class QuickAddFullSheetDialog(QDialog):
    """Добавление целого листа на склад из раскроя: тот же выбор материала, что на складе (каталог materials)."""

    def __init__(self, material_name: str, thickness_mm: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить лист на склад")
        lay = QFormLayout(self)
        lay.addRow(
            QLabel(
                "Материал — как в заказе и на складе (тип → вариант → толщина). "
                "Размеры — как у поставщика; после сохранения лист появится в списке выбора."
            )
        )
        self._mat_pick = GlassWarehouseMaterialPicker(self, mode="sheet", quick_add_cut=True)
        lay.addRow(self._mat_pick)
        name_clean = (material_name or "").strip()
        th_init = int(thickness_mm) if thickness_mm is not None else 4
        self._mat_pick.apply_resolved_name(name_clean, th_init)
        self.spin_h = QSpinBox()
        self.spin_h.setRange(100, 20000)
        self.spin_h.setValue(3210)
        lay.addRow("Высота листа (мм):", self.spin_h)
        self.spin_w = QSpinBox()
        self.spin_w.setRange(100, 20000)
        self.spin_w.setValue(2250)
        lay.addRow("Ширина листа (мм):", self.spin_w)
        self.qty_spin = QSpinBox()
        self.qty_spin.setRange(1, 999)
        self.qty_spin.setValue(1)
        lay.addRow("Количество листов:", self.qty_spin)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def _accept(self):
        ok, err = self._mat_pick.validate_sheet()
        if not ok:
            QMessageBox.warning(self, "Ошибка", err)
            return
        name = self._mat_pick.get_material_name()
        if not name:
            QMessageBox.warning(self, "Ошибка", "Укажите материал.")
            return
        h, w = self.spin_h.value(), self.spin_w.value()
        if h <= 0 or w <= 0:
            QMessageBox.warning(self, "Ошибка", "Укажите положительные размеры листа.")
            return
        self._data = {
            "name": name,
            "height_mm": h,
            "width_mm": w,
            "thickness_mm": self._mat_pick.get_thickness_mm(),
            "quantity": self.qty_spin.value(),
            "supplier": "Раскрой",
            "warehouse_number": "из расчёта",
            "comment": "Добавлено при раскрое",
        }
        self.accept()

    def get_data(self):
        return getattr(self, "_data", None)


class PartBlock(QFrame):
    remove_clicked = pyqtSignal()
    material_changed = pyqtSignal()

    def __init__(self, default_material="", parent=None, material_names=None):
        super().__init__(parent)
        self._bundle_locked = False
        self._locked_thickness_mm = None
        self._material_names_prefetched = list(material_names) if material_names else None
        self._stock_check_timer = QTimer(self)
        self._stock_check_timer.setSingleShot(True)
        self._stock_check_timer.timeout.connect(self._update_add_sheet_stock_visibility)
        self.setStyleSheet("PartBlock { background-color: #DCE8F5; border-radius: 8px; padding: 10px; margin: 4px; border: 1px solid #7B9BC1; }")
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Изделие"))
        self.btn_del = QPushButton("✕")
        self.btn_del.setObjectName("danger")
        self.btn_del.setFixedSize(26, 26)
        self.btn_del.setStyleSheet(STYLE)
        self.btn_del.setToolTip("Удалить изделие")
        self.btn_del.clicked.connect(self.remove_clicked.emit)
        top_row.addStretch()
        top_row.addWidget(self.btn_del)
        layout.addLayout(top_row)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Материал:"))
        self.material_combo = QComboBox()
        self.material_combo.setEditable(False)
        self.material_combo.setMinimumWidth(180)
        self._fill_materials(default_material)
        row1.addWidget(self.material_combo, 1)
        layout.addLayout(row1)
        row1b = QHBoxLayout()
        row1b.addWidget(QLabel("Толщина (мм):"))
        self.thickness_combo = QComboBox()
        self.thickness_combo.setEditable(False)
        self.thickness_combo.setMinimumWidth(80)
        row1b.addWidget(self.thickness_combo)
        self.btn_add_sheet_stock = QPushButton("Добавить лист…")
        self.btn_add_sheet_stock.setToolTip(
            "На складе нет листов с этим материалом и толщиной. Добавить целый лист с размерами."
        )
        self.btn_add_sheet_stock.clicked.connect(self._on_quick_add_full_sheet)
        self.btn_add_sheet_stock.setVisible(False)
        row1b.addWidget(self.btn_add_sheet_stock)
        row1b.addStretch()
        layout.addLayout(row1b)
        self._fill_thicknesses()
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Высота (мм):"))
        self.spin_h = QSpinBox()
        self.spin_h.setRange(1, 10000)
        self.spin_h.setValue(500)
        self.spin_h.valueChanged.connect(self._update_preview)
        row2.addWidget(self.spin_h)
        row2.addWidget(QLabel("Ширина (мм):"))
        self.spin_w = QSpinBox()
        self.spin_w.setRange(1, 10000)
        self.spin_w.setValue(700)
        self.spin_w.valueChanged.connect(self._update_preview)
        row2.addWidget(self.spin_w)
        row2.addWidget(QLabel("Кол-во:"))
        self.spin_qty = QSpinBox()
        self.spin_qty.setRange(1, 999)
        self.spin_qty.setValue(1)
        self.spin_qty.setMinimumWidth(52)
        self.spin_qty.valueChanged.connect(self._update_preview)
        row2.addWidget(self.spin_qty)
        btn_minus = QPushButton("−")
        btn_minus.setFixedWidth(32)
        btn_minus.clicked.connect(lambda: self.spin_qty.setValue(max(1, self.spin_qty.value() - 1)))
        row2.addWidget(btn_minus)
        self._qty_minus = btn_minus
        btn_plus = QPushButton("+")
        btn_plus.setFixedWidth(32)
        btn_plus.clicked.connect(lambda: self.spin_qty.setValue(min(999, self.spin_qty.value() + 1)))
        row2.addWidget(btn_plus)
        self._qty_plus = btn_plus
        row2.addStretch()
        layout.addLayout(row2)
        row3 = QHBoxLayout()
        self._lbl_recipient = QLabel("Получатель:")
        row3.addWidget(self._lbl_recipient)
        self.recipient_edit = QLineEdit()
        self.recipient_edit.setPlaceholderText("Введите имя или начните ввод для подсказок из БД")
        self.recipient_edit.textChanged.connect(self._on_recipient_text_changed)
        self.recipient_edit.textChanged.connect(self._update_preview)
        row3.addWidget(self.recipient_edit, 1)
        layout.addLayout(row3)
        self.recipient_list = QListWidget()
        self.recipient_list.setMaximumHeight(100)
        self.recipient_list.setVisible(False)
        self.recipient_list.itemClicked.connect(self._on_recipient_selected)
        layout.addWidget(self.recipient_list)
        self.btn_client_not_found = QPushButton("Клиент не найден — ввести")
        self.btn_client_not_found.setVisible(False)
        self.btn_client_not_found.clicked.connect(self._on_client_not_found)
        layout.addWidget(self.btn_client_not_found)
        # Кнопка выбора листа (активна только при указанных материале и размерах)
        self._chosen_sheet = None  # (sheet_id, sheet_type, order_id or None)
        choose_row = QHBoxLayout()
        self.btn_choose_sheet = QPushButton("Выбрать лист")
        self.btn_choose_sheet.setToolTip(
            "Укажите материал и размеры. В списке — склад, остатки и листы «в работе»; "
            "лист без выкроев выделен цветом — на него можно продолжить ту же схему раскроя."
        )
        self.btn_choose_sheet.setEnabled(False)
        self.btn_choose_sheet.clicked.connect(self._open_choose_sheet)
        choose_row.addWidget(self.btn_choose_sheet)
        self.label_chosen_sheet = QLabel("")
        self.label_chosen_sheet.setStyleSheet("color: #555; font-style: italic;")
        choose_row.addWidget(self.label_chosen_sheet, 1)
        layout.addLayout(choose_row)
        self.material_combo.currentTextChanged.connect(self._on_material_changed_for_thickness)
        self.material_combo.currentTextChanged.connect(self._update_choose_sheet_enabled)
        self.material_combo.currentTextChanged.connect(self.material_changed.emit)
        self.material_combo.currentTextChanged.connect(lambda _t: self._schedule_stock_visibility_update())
        self.spin_h.valueChanged.connect(self._update_choose_sheet_enabled)
        self.spin_w.valueChanged.connect(self._update_choose_sheet_enabled)
        self.thickness_combo.currentIndexChanged.connect(self._update_choose_sheet_enabled)
        self.thickness_combo.currentIndexChanged.connect(lambda _i: self._schedule_stock_visibility_update())
        # Обработка кромок: переключатели слева, справа, сверху и снизу от прямоугольника превью
        edge_grp = QGroupBox("Размер и обработка кромок по сторонам")
        edge_main = QGridLayout(edge_grp)
        EDGE_OPTIONS = [(None, '—'), ('grinding', 'Шлифовка'), ('polishing', 'Полировка'), ('facet', 'Фацет')]
        self._edge_groups = {}
        self._edge_radios = {}
        self._facet_mm = 15
        def _connect_edge_rb(side, val, rb):
            if val == 'facet':
                rb.toggled.connect(lambda checked, s=side: self._on_facet_toggled(checked, s))
            else:
                rb.toggled.connect(self._update_preview)
        # Верх: ряд переключателей для стороны «сверху»
        top_row = QHBoxLayout()
        top_row.addStretch()
        self._edge_groups['top'] = QButtonGroup(self)
        self._edge_radios['top'] = []
        for val, label in EDGE_OPTIONS:
            rb = QRadioButton(label)
            if val is None:
                rb.setChecked(True)
            self._edge_groups['top'].addButton(rb)
            self._edge_radios['top'].append((val, rb))
            _connect_edge_rb('top', val, rb)
            top_row.addWidget(rb)
        top_row.addStretch()
        edge_main.addLayout(top_row, 0, 1)
        # Слева от превью: колонка для стороны «слева»
        left_col = QVBoxLayout()
        left_col.addStretch()
        self._edge_groups['left'] = QButtonGroup(self)
        self._edge_radios['left'] = []
        for val, label in EDGE_OPTIONS:
            rb = QRadioButton(label)
            if val is None:
                rb.setChecked(True)
            self._edge_groups['left'].addButton(rb)
            self._edge_radios['left'].append((val, rb))
            _connect_edge_rb('left', val, rb)
            left_col.addWidget(rb)
        left_col.addStretch()
        edge_main.addLayout(left_col, 1, 0)
        # Превью по центру
        self.preview = PartPreview(self)
        edge_main.addWidget(self.preview, 1, 1, Qt.AlignCenter)
        # Справа от превью: колонка для стороны «справа»
        right_col = QVBoxLayout()
        right_col.addStretch()
        self._edge_groups['right'] = QButtonGroup(self)
        self._edge_radios['right'] = []
        for val, label in EDGE_OPTIONS:
            rb = QRadioButton(label)
            if val is None:
                rb.setChecked(True)
            self._edge_groups['right'].addButton(rb)
            self._edge_radios['right'].append((val, rb))
            _connect_edge_rb('right', val, rb)
            right_col.addWidget(rb)
        right_col.addStretch()
        edge_main.addLayout(right_col, 1, 2)
        # Низ: ряд переключателей для стороны «снизу»
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        self._edge_groups['bottom'] = QButtonGroup(self)
        self._edge_radios['bottom'] = []
        for val, label in EDGE_OPTIONS:
            rb = QRadioButton(label)
            if val is None:
                rb.setChecked(True)
            self._edge_groups['bottom'].addButton(rb)
            self._edge_radios['bottom'].append((val, rb))
            _connect_edge_rb('bottom', val, rb)
            bottom_row.addWidget(rb)
        bottom_row.addStretch()
        edge_main.addLayout(bottom_row, 2, 1)
        layout.addWidget(edge_grp)
        self._update_preview()
        self._schedule_stock_visibility_update(initial=True)

    def _fill_materials(self, default=""):
        global _MATERIAL_NAMES_CACHE
        names = self._material_names_prefetched
        if names is None:
            if _MATERIAL_NAMES_CACHE is None:
                _MATERIAL_NAMES_CACHE = list(models.get_all_material_names() or [])
            names = list(_MATERIAL_NAMES_CACHE)
        self.material_combo.clear()
        self.material_combo.addItem(default or "Выберите материал")
        for n in names:
            if n != (default or "Выберите материал"):
                self.material_combo.addItem(n)
        if default:
            idx = self.material_combo.findText(default)
            if idx >= 0:
                self.material_combo.setCurrentIndex(idx)
        self.material_combo.currentTextChanged.connect(self._update_preview)

    def _on_material_changed_for_thickness(self):
        self._fill_thicknesses()

    def _fill_thicknesses(self):
        """Заполнить список толщин только теми, что есть на складе для выбранного материала."""
        mat = (self.material_combo.currentText() or '').strip()
        if not mat or mat == 'Выберите материал':
            self.thickness_combo.clear()
            self.thickness_combo.addItem("—", 4)
            self.thickness_combo.setCurrentIndex(0)
            self._schedule_stock_visibility_update()
            return
        thicknesses = _THICKNESSES_CACHE.get(mat)
        if thicknesses is None:
            thicknesses = list(models.get_thicknesses_for_material(mat) or [])
            _THICKNESSES_CACHE[mat] = thicknesses
        prev = self._get_thickness_value()
        self.thickness_combo.clear()
        if not thicknesses:
            self.thickness_combo.addItem("— нет на складе —", 4)
            self.thickness_combo.setCurrentIndex(0)
            self._schedule_stock_visibility_update()
            return
        for th in thicknesses:
            self.thickness_combo.addItem("%d мм" % th, int(th))
        idx = self.thickness_combo.findData(prev if prev in thicknesses else thicknesses[0])
        self.thickness_combo.setCurrentIndex(max(0, idx))
        self._schedule_stock_visibility_update()

    def _get_thickness_value(self):
        """Текущая толщина (мм) из комбобокса."""
        v = self.thickness_combo.currentData()
        return int(v) if v is not None else 4

    def _update_add_sheet_stock_visibility(self):
        mat = (self.material_combo.currentText() or "").strip()
        if not mat or mat == "Выберите материал":
            self.btn_add_sheet_stock.setVisible(False)
            return
        try:
            th = self._get_thickness_value()
        except (TypeError, ValueError):
            th = 4
        key = (mat, int(th))
        has_stock = _STOCK_PRESENCE_CACHE.get(key)
        if has_stock is None:
            full = models.get_full_sheets_by_material_and_thickness(mat, th) or []
            rem = models.get_remnants_by_material_and_thickness(mat, th) or []
            in_work = models.get_sheets_in_work_for_material_thickness(mat, th) or []
            has_stock = bool(full or rem or in_work)
            _STOCK_PRESENCE_CACHE[key] = has_stock
        self.btn_add_sheet_stock.setVisible(not has_stock)

    def _schedule_stock_visibility_update(self, initial=False):
        # Дебаунс запросов склада, чтобы не тормозить открытие окна.
        self._stock_check_timer.start(220 if initial else 120)

    def _on_quick_add_full_sheet(self):
        global _THICKNESSES_CACHE, _STOCK_PRESENCE_CACHE, _MATERIAL_NAMES_CACHE
        mat = (self.material_combo.currentText() or "").strip()
        if not mat or mat == "Выберите материал":
            QMessageBox.warning(self, "Склад", "Сначала укажите материал.")
            return
        th = self._get_thickness_value()
        d = QuickAddFullSheetDialog(mat, th, self)
        if d.exec_() != QDialog.Accepted:
            return
        data = d.get_data()
        if not data:
            return
        try:
            models.insert_full_sheet(
                data["name"],
                data["height_mm"],
                data["width_mm"],
                arrival_date=date.today(),
                supplier=data.get("supplier"),
                cost=0,
                warehouse_number=data.get("warehouse_number"),
                quantity=data.get("quantity", 1),
                comment=data.get("comment"),
                thickness_mm=data["thickness_mm"],
            )
        except Exception as e:
            QMessageBox.critical(self, "Склад", "Не удалось добавить лист: %s" % e)
            return
        _THICKNESSES_CACHE.pop(mat, None)
        _STOCK_PRESENCE_CACHE.pop((mat, int(data["thickness_mm"])), None)
        _MATERIAL_NAMES_CACHE = None
        self._fill_thicknesses()
        if getattr(self, "_bundle_locked", False) and self._locked_thickness_mm is not None:
            th_keep = int(self._locked_thickness_mm)
            self.thickness_combo.blockSignals(True)
            idx = self.thickness_combo.findData(th_keep)
            if idx >= 0:
                self.thickness_combo.setCurrentIndex(idx)
            else:
                self.thickness_combo.addItem("%d мм" % th_keep, th_keep)
                self.thickness_combo.setCurrentIndex(self.thickness_combo.count() - 1)
            self.thickness_combo.blockSignals(False)
        self._update_add_sheet_stock_visibility()
        self.material_changed.emit()
        self._update_choose_sheet_enabled()
        QMessageBox.information(self, "Склад", "Лист добавлен на склад. Нажмите «Выбрать лист».")

    def _on_recipient_text_changed(self, text):
        prefix = (text or "").strip()
        if not prefix:
            self.recipient_list.setVisible(False)
            self.btn_client_not_found.setVisible(False)
            return
        names = filter_clients_by_prefix(prefix)
        self.recipient_list.clear()
        for n in names[:15]:
            self.recipient_list.addItem(n)
        self.recipient_list.setVisible(len(names) > 0)
        # Показать кнопку «Клиент не найден», если введённое имя не совпадает ни с одним клиентом
        self._update_not_found_button()

    def _update_not_found_button(self):
        global _CLIENT_NAMES_CACHE
        t = (self.recipient_edit.text() or "").strip()
        if not t:
            self.btn_client_not_found.setVisible(False)
            return
        if _CLIENT_NAMES_CACHE is None:
            _CLIENT_NAMES_CACHE = list(get_clients_list() or [])
        names = _CLIENT_NAMES_CACHE
        exact = any(n.strip().lower() == t.lower() for n in names)
        self.btn_client_not_found.setVisible(not exact)

    def _on_client_not_found(self):
        from ui.new_client_dialog import NewClientDialog
        global _CLIENT_NAMES_CACHE
        initial = (self.recipient_edit.text() or "").strip()
        d = NewClientDialog(self, initial_name=initial)
        if d.exec_() != QDialog.Accepted:
            return
        name = d.get_saved_name()
        if name:
            refresh_clients()
            _CLIENT_NAMES_CACHE = list(get_clients_list() or [])
            self.recipient_edit.setText(name)
            self.recipient_list.setVisible(False)
            self.btn_client_not_found.setVisible(False)
            self._update_preview()

    def _on_recipient_selected(self, item):
        self.recipient_edit.setText(item.text())
        self.recipient_list.setVisible(False)

    def _update_preview(self):
        self.preview.set_data(
            self.spin_h.value(), self.spin_w.value(),
            self.spin_qty.value(), self.recipient_edit.text(),
            self._get_edge_treatment(),
        )

    def _update_choose_sheet_enabled(self):
        mat = (self.material_combo.currentText() or '').strip()
        mat_ok = mat and mat != 'Выберите материал'
        size_ok = self.spin_h.value() > 0 and self.spin_w.value() > 0
        self.btn_choose_sheet.setEnabled(mat_ok and size_ok)
        if not (mat_ok and size_ok):
            self._chosen_sheet = None
            self.label_chosen_sheet.setText("")

    def _open_choose_sheet(self):
        material = (self.material_combo.currentText() or '').strip()
        if not material or material == 'Выберите материал':
            QMessageBox.warning(self, "Выбор листа", "Укажите материал.")
            return
        if self.spin_h.value() <= 0 or self.spin_w.value() <= 0:
            QMessageBox.warning(self, "Выбор листа", "Укажите размеры (высота и ширина).")
            return
        try:
            thickness = self._get_thickness_value()
        except (TypeError, ValueError):
            thickness = 4
        try:
            d = ChooseSheetDialog(material, thickness, self.spin_h.value(), self.spin_w.value(), self)
            if d.exec_() == d.Accepted:
                ch = d.get_chosen()
                if ch and len(ch) >= 3:
                    self._chosen_sheet = (int(ch[0]), ch[1], ch[2])
                    sid, stype, order_id = self._chosen_sheet
                    if order_id is not None:
                        self.label_chosen_sheet.setText("Заказ #%s, лист %s" % (order_id, stype))
                    else:
                        self.label_chosen_sheet.setText("Лист id %s (%s)" % (sid, stype))
                else:
                    self._chosen_sheet = None
                    self.label_chosen_sheet.setText("")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", "Не удалось открыть выбор листа: %s" % e)

    def _on_facet_toggled(self, checked, side):
        if not checked:
            self._update_preview()
            return
        d = FacetSizeDialog(self._facet_mm, self)
        if d.exec_() != QDialog.Accepted:
            self._edge_groups[side].setExclusive(False)
            self._edge_radios[side][0][1].setChecked(True)
            self._edge_groups[side].setExclusive(True)
            return
        self._facet_mm = d.get_facet_mm()
        self._update_preview()

    def _get_edge_treatment(self):
        out = {}
        for side in ['left', 'right', 'top', 'bottom']:
            for val, rb in self._edge_radios[side]:
                if rb.isChecked():
                    out[side] = val
                    break
            else:
                out[side] = None
        if any(out.get(s) == 'facet' for s in ['left', 'right', 'top', 'bottom']):
            out['facet_mm'] = self._facet_mm
        return out

    def set_edge_treatment_from_dict(self, et):
        if not et:
            self._update_preview()
            return
        fm = et.get('facet_mm')
        if fm is not None:
            try:
                self._facet_mm = int(fm)
            except (TypeError, ValueError):
                self._facet_mm = 15
        for side in ['left', 'right', 'top', 'bottom']:
            val = et.get(side)
            group = self._edge_groups.get(side)
            if not group:
                continue
            group.setExclusive(False)
            for v, rb in self._edge_radios[side]:
                # При автоподстановке из уже сохранённых данных не открываем
                # повторно диалог выбора фацета.
                rb.blockSignals(True)
                rb.setChecked(v == val)
                rb.blockSignals(False)
            group.setExclusive(True)
        self._update_preview()

    def apply_part_dict(self, d):
        mat = (d.get('material_name') or '').strip()
        self.material_combo.blockSignals(True)
        idx = self.material_combo.findText(mat) if mat else -1
        if idx >= 0:
            self.material_combo.setCurrentIndex(idx)
        elif mat:
            self.material_combo.addItem(mat)
            self.material_combo.setCurrentIndex(self.material_combo.count() - 1)
        self.material_combo.blockSignals(False)
        self._on_material_changed_for_thickness()
        th = int(d.get('thickness_mm') or 4)
        self.thickness_combo.blockSignals(True)
        idx_t = self.thickness_combo.findData(th)
        if idx_t >= 0:
            self.thickness_combo.setCurrentIndex(idx_t)
        else:
            self.thickness_combo.addItem("%d мм" % th, th)
            self.thickness_combo.setCurrentIndex(self.thickness_combo.count() - 1)
        self.thickness_combo.blockSignals(False)
        self.spin_h.setValue(max(1, int(d.get('height_mm') or 100)))
        self.spin_w.setValue(max(1, int(d.get('width_mm') or 100)))
        self.spin_qty.setValue(max(1, int(d.get('quantity') or 1)))
        rt = d.get('recipient_text')
        self.recipient_edit.setText((rt or '').strip())
        self.set_edge_treatment_from_dict(d.get('edge_treatment') or {})
        ch = d.get('chosen_sheet')
        if ch and isinstance(ch, (tuple, list)) and len(ch) >= 3:
            self._chosen_sheet = (int(ch[0]), ch[1], ch[2])
            oid = ch[2]
            if oid is not None:
                self.label_chosen_sheet.setText("Заказ #%s, лист %s" % (oid, ch[1]))
            else:
                self.label_chosen_sheet.setText("Лист id %s (%s)" % (ch[0], ch[1]))
        else:
            self._chosen_sheet = None
            self.label_chosen_sheet.setText("")
        self._update_choose_sheet_enabled()
        self._update_add_sheet_stock_visibility()

    def set_bundle_locked(self, client_for_labels=""):
        self._bundle_locked = True
        self._locked_thickness_mm = self._get_thickness_value()
        t = (client_for_labels or "").strip()
        if t:
            self.recipient_edit.setText(t)
        self._lbl_recipient.setVisible(False)
        self.recipient_edit.setVisible(False)
        self.recipient_list.setVisible(False)
        self.btn_client_not_found.setVisible(False)
        self.material_combo.setEnabled(False)
        self.thickness_combo.setEnabled(False)
        self.spin_h.setEnabled(False)
        self.spin_w.setEnabled(False)
        self.spin_qty.setEnabled(False)
        self._qty_minus.setEnabled(False)
        self._qty_plus.setEnabled(False)
        for side in self._edge_radios:
            for _v, rb in self._edge_radios[side]:
                rb.setEnabled(False)
        self.btn_del.setVisible(False)
        self.btn_choose_sheet.setEnabled(True)
        self.btn_add_sheet_stock.setEnabled(True)
        self._update_add_sheet_stock_visibility()
        self._update_preview()

    def get_data(self):
        mat = (self.material_combo.currentText() or '').strip()
        return {
            'material_name': mat,
            'thickness_mm': self._get_thickness_value(),
            'height_mm': self.spin_h.value(),
            'width_mm': self.spin_w.value(),
            'quantity': self.spin_qty.value(),
            'recipient_text': self.recipient_edit.text().strip() or None,
            'edge_treatment': self._get_edge_treatment(),
            'chosen_sheet': self._chosen_sheet,  # (sheet_id, sheet_type, order_id or None) or None
        }


class ChooseSheetDialog(QDialog):
    """Выбор листа: выпадающий список материал+толщина, список листов по площади (без миниатюр), клик — выбор, для остатков — история."""
    def __init__(
        self,
        material,
        thickness_mm,
        part_h_mm=0,
        part_w_mm=0,
        parent=None,
        part_rects_mm=None,
        exclude_in_work_pool_ids=None,
        exclude_remnant_ids=None,
        exclude_full_ids=None,
        replace_sheet_index=None,
        manual_sheet_indices=None,
        piece_uid_to_sheet_index=None,
        session_sheet_usage=None,
        constructor_quick_open=False,
    ):
        super().__init__(parent)
        self.setWindowTitle("Выбрать лист")
        self.setMinimumSize(420, 380)
        self._material = (material or '').strip()
        self._thickness = int(thickness_mm) if thickness_mm is not None else 4
        self._session_sheet_usage = {}
        if session_sheet_usage:
            for k, v in session_sheet_usage.items():
                if isinstance(k, tuple) and len(k) == 2:
                    st, sid = str(k[0]), int(k[1])
                    self._session_sheet_usage[(st, sid)] = int(v)
        self._constructor_quick_open = bool(
            constructor_quick_open and self._material
        )
        self._part_h_mm = max(0, int(part_h_mm or 0))
        self._part_w_mm = max(0, int(part_w_mm or 0))
        self._part_triplets = []
        if part_rects_mm:
            for t in part_rects_mm:
                if not t:
                    continue
                a, b = int(t[0] or 0), int(t[1] or 0)
                uid = None
                if len(t) >= 3 and t[2] is not None:
                    uid = str(t[2]).strip() or None
                if a > 0 and b > 0:
                    self._part_triplets.append((a, b, uid))
        self._part_rects_mm = [(a, b) for (a, b, _) in self._part_triplets] if self._part_triplets else None
        self._exclude_in_work_pool_ids = set(int(x) for x in (exclude_in_work_pool_ids or []) if x is not None)
        self._exclude_remnant_ids = set(int(x) for x in (exclude_remnant_ids or []) if x is not None)
        self._exclude_full_ids = set(int(x) for x in (exclude_full_ids or []) if x is not None)
        try:
            self._replace_sheet_index = (
                int(replace_sheet_index) if replace_sheet_index is not None else None
            )
        except (TypeError, ValueError):
            self._replace_sheet_index = None
        self._manual_sheet_indices = set(int(x) for x in (manual_sheet_indices or []) if isinstance(x, int))
        self._piece_uid_to_sheet = dict(piece_uid_to_sheet_index or {})
        self._chosen = None
        layout = QFormLayout(self)

        layout.addRow(QLabel("Материал и толщина:"))
        if self._constructor_quick_open:
            self.material_thickness_combo = None
            self._pairs = [(self._material, self._thickness)]
            lab_fix = QLabel("%s — %s мм" % (self._material, self._thickness))
            f = lab_fix.font()
            f.setBold(True)
            lab_fix.setFont(f)
            layout.addRow(lab_fix)
        else:
            self.material_thickness_combo = QComboBox()
            self.material_thickness_combo.setEditable(False)
            pairs = models.get_all_material_thickness_pairs()
            self._pairs = pairs
            self.material_thickness_combo.addItem("— все —", (None, None))
            for (name, thick) in pairs:
                self.material_thickness_combo.addItem("%s — %s мм" % (name, thick), (name, thick))
            self.material_thickness_combo.currentIndexChanged.connect(self._on_filter_changed)
            layout.addRow(self.material_thickness_combo)

        layout.addRow(QLabel("Лист (по площади):"))
        self.sheet_list = QListWidget()
        self.sheet_list.setMinimumHeight(220)
        self.sheet_list.setUniformItemSizes(True)
        self.sheet_list.setStyleSheet("QListWidget::item { min-height: 24px; max-height: 28px; padding: 2px 4px; }")
        self.sheet_list.doubleClicked.connect(self._on_double_click_sheet)
        layout.addRow(self.sheet_list)

        self.btn_add_full = QPushButton("Нет в списке — добавить целый лист на склад…")
        self.btn_add_full.clicked.connect(self._on_add_full_sheet)
        layout.addRow(self.btn_add_full)

        if self._constructor_quick_open:
            QTimer.singleShot(0, self._fill_list)
        else:
            self._set_combo_to_material_thickness()
            self._fill_list()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def _set_combo_to_material_thickness(self):
        if self.material_thickness_combo is None:
            return
        for i in range(self.material_thickness_combo.count()):
            val = self.material_thickness_combo.itemData(i)
            if val and len(val) == 2 and val[0] == self._material and val[1] == self._thickness:
                self.material_thickness_combo.setCurrentIndex(i)
                return
        if self._material:
            self.material_thickness_combo.addItem(
                "%s — %s мм" % (self._material, self._thickness),
                (self._material, self._thickness),
            )
            self.material_thickness_combo.setCurrentIndex(self.material_thickness_combo.count() - 1)
        else:
            self.material_thickness_combo.setCurrentIndex(0)

    @staticmethod
    def _rect_fits_in_sheet(ph, pw, sh, sw):
        return (ph <= sh and pw <= sw) or (ph <= sw and pw <= sh)

    def _exclude_steal_only_other_manual(self, sh, sw):
        """
        Не показывать лист, на который по размеру влезают только детали, уже лежащие на ДРУГИХ ручных
        листах (перенос сорвёт ручной лист без возможности заполнить его крупными деталями).
        """
        ri = self._replace_sheet_index
        if ri is None:
            return False
        if not self._manual_sheet_indices or not self._piece_uid_to_sheet or not self._part_triplets:
            return False
        if not any(t[2] for t in self._part_triplets):
            return False
        fit_uids = []
        for ph, pw, uid in self._part_triplets:
            if not uid:
                continue
            if self._rect_fits_in_sheet(ph, pw, sh, sw):
                fit_uids.append(uid)
        if not fit_uids:
            return False
        uniq = list(dict.fromkeys(fit_uids))
        for uid in uniq:
            if self._piece_uid_to_sheet.get(uid) == ri:
                return False
        for uid in uniq:
            si = self._piece_uid_to_sheet.get(uid)
            if si is None or si not in self._manual_sheet_indices:
                return False
        return True

    def _on_add_full_sheet(self):
        d = QuickAddFullSheetDialog(self._material, self._thickness, self)
        if d.exec_() != QDialog.Accepted:
            return
        data = d.get_data()
        if not data:
            return
        try:
            models.insert_full_sheet(
                data["name"],
                data["height_mm"],
                data["width_mm"],
                arrival_date=date.today(),
                supplier=data.get("supplier"),
                cost=0,
                warehouse_number=data.get("warehouse_number"),
                quantity=data.get("quantity", 1),
                comment=data.get("comment"),
                thickness_mm=data["thickness_mm"],
            )
        except Exception as e:
            QMessageBox.critical(self, "Склад", "Не удалось добавить лист: %s" % e)
            return
        self._material = (data.get("name") or self._material).strip()
        self._thickness = int(data.get("thickness_mm") or self._thickness)
        self._pairs = models.get_all_material_thickness_pairs()
        cur_m, cur_t = self._material, self._thickness
        if self.material_thickness_combo is not None:
            self.material_thickness_combo.blockSignals(True)
            self.material_thickness_combo.clear()
            self.material_thickness_combo.addItem("— все —", (None, None))
            for (name, thick) in self._pairs:
                self.material_thickness_combo.addItem("%s — %s мм" % (name, thick), (name, thick))
            self.material_thickness_combo.blockSignals(False)
        self._material = cur_m
        self._thickness = cur_t
        self._set_combo_to_material_thickness()
        self._fill_list()
        QMessageBox.information(self, "Склад", "Лист добавлен. Выберите его в списке и нажмите OK.")

    def _on_filter_changed(self):
        if self.material_thickness_combo is None:
            return
        idx = self.material_thickness_combo.currentIndex()
        if idx < 0:
            return
        val = self.material_thickness_combo.itemData(idx)
        if val and len(val) == 2:
            self._material = val[0] or ''
            self._thickness = val[1] if val[1] is not None else 4
        else:
            self._material = ''
            self._thickness = 4
        self._fill_list()

    def _layout_usage_count(self, sheet_type, sheet_id):
        """Сколько раз лист (тип + id) уже в текущей сессии/конструкторе — не показывать лишние экземпляры."""
        try:
            return int(self._session_sheet_usage.get((str(sheet_type), int(sheet_id)), 0))
        except (TypeError, ValueError):
            return 0

    def _on_double_click_sheet(self):
        row = self.sheet_list.currentRow()
        if row < 0:
            return
        item = self.sheet_list.item(row)
        data = item.data(Qt.UserRole) if item else None
        if not data or not isinstance(data, (tuple, list)) or len(data) < 2:
            return
        sheet_type = data[1] if len(data) > 1 else 'full'
        if sheet_type == 'remnant' and len(data) >= 4 and isinstance(data[3], dict) and data[3].get('id') is not None:
            from ui.warehouse_dialog import CutHistoryDialog
            remnant = data[3]
            d = CutHistoryDialog(remnant, self)
            d.exec_()

    def _fill_list(self):
        self.sheet_list.clear()

        def _can_use_sheet(sheet_h, sheet_w):
            sh = int(sheet_h or 0)
            sw = int(sheet_w or 0)
            if sh <= 0 or sw <= 0:
                return False
            if self._part_rects_mm:
                for ph, pw in self._part_rects_mm:
                    if (ph <= sh and pw <= sw) or (ph <= sw and pw <= sh):
                        return True
                return False
            ph = int(self._part_h_mm or 0)
            pw = int(self._part_w_mm or 0)
            if ph <= 0 or pw <= 0:
                return True
            return (ph <= sh and pw <= sw) or (ph <= sw and pw <= sh)

        try:
            if not self._material:
                # Показать всё: сгруппировать по материал + толщина (стандартные толщины 1,4,6,8,10 и др.)
                items = []
                for (name, thick) in self._pairs:
                    full = models.get_full_sheets_by_material_and_thickness(name, thick) or []
                    for f in full:
                        h, w = int(f.get('height_mm') or 0), int(f.get('width_mm') or 0)
                        if not _can_use_sheet(h, w):
                            continue
                        if self._exclude_steal_only_other_manual(h, w):
                            continue
                        if int(f.get('id') or 0) in self._exclude_full_ids:
                            continue
                        q = max(1, int(f.get('quantity') or 1))
                        used = self._layout_usage_count('full', int(f.get('id') or 0))
                        for _ in range(max(0, q - used)):
                            items.append((h * w, "Склад целый", h, w, int(f.get('id') or 0), 'full', None, None))
                    rem = models.get_remnants_by_material_and_thickness(name, thick) or []
                    for r in rem:
                        h, w = int(r.get('height_mm') or 0), int(r.get('width_mm') or 0)
                        if not _can_use_sheet(h, w):
                            continue
                        if self._exclude_steal_only_other_manual(h, w):
                            continue
                        if int(r.get('id') or 0) in self._exclude_remnant_ids:
                            continue
                        if self._layout_usage_count('remnant', int(r.get('id') or 0)) > 0:
                            continue
                        items.append((h * w, "Склад остаток", h, w, int(r.get('id') or 0), 'remnant', None, r))
                    in_work = models.get_sheets_in_work_for_material_thickness(name, thick) or []
                    for iw_idx, s in enumerate(in_work):
                        sh = int(s.get('sheet_height') or s.get('rect_h') or 0)
                        sw = int(s.get('sheet_width') or s.get('rect_w') or 0)
                        if not _can_use_sheet(sh, sw):
                            continue
                        if self._exclude_steal_only_other_manual(sh, sw):
                            continue
                        pool_id = models.in_work_pool_entry_id(s['order_id'], s.get('sheet_index', 0), iw_idx)
                        if self._layout_usage_count('in_work', pool_id) > 0:
                            continue
                        if pool_id in self._exclude_in_work_pool_ids:
                            continue
                        npc = int(s.get('planned_piece_count') or 0)
                        if s.get('no_cuts_yet'):
                            lbl = "В работе №%s (лист без реза)" % s.get('order_id')
                        elif npc > 0:
                            lbl = "В работе №%s (на листе уже %d изд.)" % (s.get('order_id'), npc)
                        else:
                            lbl = "В работе №%s" % s.get('order_id')
                        meta_rem = {
                            'no_cuts_yet': bool(s.get('no_cuts_yet')),
                            'planned_piece_count': npc,
                        }
                        items.append(
                            (
                                sh * sw,
                                lbl,
                                sh,
                                sw,
                                pool_id,
                                'in_work',
                                s.get('order_id'),
                                meta_rem,
                            )
                        )
                items.sort(key=lambda x: (-x[0], x[1], -x[2]))  # по площади убыв., затем тип, затем размер
                rem_ids_for_hist = []
                for t in items:
                    rem = t[7]
                    if isinstance(rem, dict) and rem.get("id") is not None:
                        rem_ids_for_hist.append(rem.get("id"))
                hist_id_set = models.remnant_ids_with_history(rem_ids_for_hist)
                for _area, label, h, w, sid, stype, oid, rem in items:
                    it = QListWidgetItem("%s  %d×%d мм" % (label, w, h))
                    rem_hist = rem if isinstance(rem, dict) and rem.get('id') is not None else None
                    it.setData(Qt.UserRole, (sid, stype, oid, rem_hist))
                    if isinstance(rem, dict) and rem.get('no_cuts_yet'):
                        it.setForeground(QBrush(QColor(160, 82, 0)))
                        it.setToolTip("Лист в заказе без выкроев — детали добавятся на ту же схему раскроя.")
                    elif isinstance(rem, dict) and int(rem.get('planned_piece_count') or 0) > 0:
                        it.setToolTip(
                            "На этом листе уже размещены изделия по схеме — новые добавятся в свободные места."
                        )
                    elif rem_hist and rem_hist.get('id') in hist_id_set:
                        it.setToolTip("Двойной клик — история резов")
                    self.sheet_list.addItem(it)
                return
            full = models.get_full_sheets_by_material_and_thickness(self._material, self._thickness) or []
            rem = models.get_remnants_by_material_and_thickness(self._material, self._thickness) or []
            in_work = models.get_sheets_in_work_for_material_thickness(self._material, self._thickness) or []
            rows = []
            for f in full:
                h, w = int(f.get('height_mm') or 0), int(f.get('width_mm') or 0)
                if not _can_use_sheet(h, w):
                    continue
                if self._exclude_steal_only_other_manual(h, w):
                    continue
                if int(f.get('id') or 0) in self._exclude_full_ids:
                    continue
                q = max(1, int(f.get('quantity') or 1))
                used = self._layout_usage_count('full', int(f.get('id') or 0))
                for _ in range(max(0, q - used)):
                    rows.append((h * w, "Склад целый", h, w, int(f.get('id') or 0), 'full', None, None))
            for r in rem:
                h, w = int(r.get('height_mm') or 0), int(r.get('width_mm') or 0)
                if not _can_use_sheet(h, w):
                    continue
                if self._exclude_steal_only_other_manual(h, w):
                    continue
                if int(r.get('id') or 0) in self._exclude_remnant_ids:
                    continue
                if self._layout_usage_count('remnant', int(r.get('id') or 0)) > 0:
                    continue
                rows.append((h * w, "Склад остаток", h, w, int(r.get('id') or 0), 'remnant', None, r))
            for iw_idx, s in enumerate(in_work):
                sh = int(s.get('sheet_height') or s.get('rect_h') or 0)
                sw = int(s.get('sheet_width') or s.get('rect_w') or 0)
                if not _can_use_sheet(sh, sw):
                    continue
                if self._exclude_steal_only_other_manual(sh, sw):
                    continue
                pool_id = models.in_work_pool_entry_id(s['order_id'], s.get('sheet_index', 0), iw_idx)
                if self._layout_usage_count('in_work', pool_id) > 0:
                    continue
                if pool_id in self._exclude_in_work_pool_ids:
                    continue
                npc = int(s.get('planned_piece_count') or 0)
                if s.get('no_cuts_yet'):
                    lbl = "В работе №%s (лист без реза)" % s.get('order_id')
                elif npc > 0:
                    lbl = "В работе №%s (на листе уже %d изд.)" % (s.get('order_id'), npc)
                else:
                    lbl = "В работе №%s" % s.get('order_id')
                meta_rem = {
                    'no_cuts_yet': bool(s.get('no_cuts_yet')),
                    'planned_piece_count': npc,
                }
                rows.append((sh * sw, lbl, sh, sw, pool_id, 'in_work', s.get('order_id'), meta_rem))
            rows.sort(key=lambda x: (-x[0], x[1], -x[2]))
            rem_ids_for_hist = []
            for t in rows:
                rem = t[7]
                if isinstance(rem, dict) and rem.get("id") is not None:
                    rem_ids_for_hist.append(rem.get("id"))
            hist_id_set = models.remnant_ids_with_history(rem_ids_for_hist)
            for _area, label, h, w, sid, stype, oid, rem in rows:
                it = QListWidgetItem("%s  %d×%d мм" % (label, w, h))
                rem_hist = rem if isinstance(rem, dict) and rem.get('id') is not None else None
                it.setData(Qt.UserRole, (sid, stype, oid, rem_hist))
                if isinstance(rem, dict) and rem.get('no_cuts_yet'):
                    it.setForeground(QBrush(QColor(160, 82, 0)))
                    it.setToolTip("Лист в заказе без выкроев — детали добавятся на ту же схему раскроя.")
                elif isinstance(rem, dict) and int(rem.get('planned_piece_count') or 0) > 0:
                    it.setToolTip(
                        "На этом листе уже размещены изделия по схеме — новые добавятся в свободные места."
                    )
                elif rem_hist and rem_hist.get('id') in hist_id_set:
                    it.setToolTip("Двойной клик — история резов")
                self.sheet_list.addItem(it)
        except Exception:
            self.sheet_list.addItem(QListWidgetItem("(Ошибка загрузки списка листов)"))

    def _accept(self):
        row = self.sheet_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Выбор", "Выберите лист из списка.")
            return
        item = self.sheet_list.item(row)
        data = item.data(Qt.UserRole) if item else None
        if not data or not isinstance(data, (tuple, list)) or len(data) < 2:
            QMessageBox.warning(self, "Выбор", "Выберите лист из списка (не строку об ошибке).")
            return
        sheet_id = int(data[0]) if data[0] is not None else 0
        sheet_type = data[1] if len(data) > 1 else 'full'
        order_id = data[2] if len(data) > 2 else None
        self._chosen = (sheet_id, sheet_type, order_id)
        self.accept()

    def get_chosen(self):
        return self._chosen


class CreateCutDialog(QDialog):
    def __init__(self, parent=None, pin_order_id=None, initial_parts=None, lock_parts_ui=False, bundle_client_name=""):
        super().__init__(parent)
        self._pin_order_id = pin_order_id
        self._lock_parts_ui = bool(lock_parts_ui)
        self._bundle_client_name = (bundle_client_name or "").strip()
        self._cut_saved_order_id = None
        self.setWindowTitle("Создать рез")
        self.setMinimumSize(920, 860)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # --- Детали ---
        details_grp = QGroupBox("Детали заказа")
        details_layout = QVBoxLayout(details_grp)
        self.parts_scroll = QScrollArea()
        self.parts_scroll.setWidgetResizable(True)
        self.parts_scroll.setMinimumHeight(300)
        self.parts_widget = QWidget()
        self.parts_layout = QVBoxLayout(self.parts_widget)
        self.part_blocks = []
        global _MATERIAL_NAMES_CACHE
        preloaded_materials = list(_MATERIAL_NAMES_CACHE) if _MATERIAL_NAMES_CACHE is not None else list(models.get_all_material_names() or [])
        _MATERIAL_NAMES_CACHE = list(preloaded_materials)
        if initial_parts:
            for p in initial_parts:
                self._add_part_block((p.get('material_name') or '').strip(), material_names=preloaded_materials)
                self.part_blocks[-1].apply_part_dict(p)
            if self._lock_parts_ui:
                cn = self._bundle_client_name
                for b in self.part_blocks:
                    b.set_bundle_locked(cn)
        else:
            self._add_part_block("", material_names=preloaded_materials)
        self.parts_scroll.setWidget(self.parts_widget)
        details_layout.addWidget(self.parts_scroll)
        self.btn_add_part = QPushButton("➕ Добавить изделие")
        self.btn_add_part.setObjectName("primary")
        self.btn_add_part.setStyleSheet(STYLE)
        self.btn_add_part.clicked.connect(self._add_part)
        details_layout.addWidget(self.btn_add_part)
        self.btn_add_part.setVisible(not self._lock_parts_ui)
        layout.addWidget(details_grp)

        self.btn_calc = QPushButton("Рассчитать раскрой")
        self.btn_calc.setObjectName("primary")
        self.btn_calc.setStyleSheet(STYLE)
        self.btn_calc.clicked.connect(self._calculate)
        layout.addWidget(self.btn_calc)

        self._first_material = ""
        self._update_add_part_button()

    def _update_add_part_button(self):
        """Кнопка «Добавить изделие» активна только если у последнего изделия выбран материал."""
        enabled = False
        if self.part_blocks:
            mat = (self.part_blocks[-1].get_data().get('material_name') or '').strip()
            enabled = bool(mat and mat != 'Выберите материал')
        self.btn_add_part.setEnabled(enabled)

    def _add_part_block(self, default_material, material_names=None):
        block = PartBlock(default_material, self, material_names=material_names)
        block.remove_clicked.connect(lambda b=block: self._remove_part_block(b))
        block.material_changed.connect(self._update_add_part_button)
        self.parts_layout.addWidget(block)
        self.part_blocks.append(block)

    def _remove_part_block(self, block):
        if self._lock_parts_ui:
            return
        if len(self.part_blocks) <= 1:
            QMessageBox.information(self, "Удаление", "Должна остаться хотя бы одна деталь.")
            return
        self.part_blocks.remove(block)
        block.setParent(None)
        block.deleteLater()
        self._update_add_part_button()

    def _add_part(self):
        if self._lock_parts_ui:
            return
        mat = self._first_material or ""
        if self.part_blocks:
            mat = (self.part_blocks[-1].get_data().get('material_name') or '').strip() or mat
        self._add_part_block(mat)
        self._update_add_part_button()
        # Прокрутить к новому блоку внизу после обновления раскладки (небольшая задержка, чтобы виджет успел отрисоваться)
        def _scroll_to_bottom():
            if self.part_blocks:
                self.parts_scroll.ensureWidgetVisible(self.part_blocks[-1], 0, 80)
            vb = self.parts_scroll.verticalScrollBar()
            vb.setValue(vb.maximum())
        QTimer.singleShot(50, _scroll_to_bottom)
        QTimer.singleShot(150, _scroll_to_bottom)

    def _calculate(self):
        items = [block.get_data() for block in self.part_blocks]
        for i, d in enumerate(items):
            mat = (d.get('material_name') or '').strip()
            if not mat or mat == 'Выберите материал':
                QMessageBox.warning(
                    self, "Материал",
                    "Укажите материал для каждого изделия. Изделие №%d: выберите материал из списка или введите название." % (i + 1)
                )
                return
        items = [d for d in items if (d.get('material_name') or '').strip() and (d.get('material_name') or '').strip() != 'Выберите материал']
        if not items:
            QMessageBox.warning(self, "Детали", "Добавьте хотя бы одну деталь с материалом.")
            return

        # Нормализуем items: добавляем thickness_mm
        for it in items:
            if 'thickness_mm' not in it:
                it['thickness_mm'] = 4

        # Фиксированный (якорный) лист на группу материал+толщина.
        # Пользователь может указать его явно через «Выбрать лист» в любом изделии группы.
        fixed_first_sheet = {}
        combine_order_id = None
        group_part_dims = {}
        for it in items:
            key = (it['material_name'], int(it.get('thickness_mm', 4)))
            ph = int(it.get('height_mm') or 0)
            pw = int(it.get('width_mm') or 0)
            cur = group_part_dims.get(key) or (0, 0)
            group_part_dims[key] = (max(cur[0], ph), max(cur[1], pw))
            ch = it.get('chosen_sheet')
            if ch and len(ch) >= 3:
                sheet_id, sheet_type, order_id = ch[0], ch[1], ch[2]
                fixed_first_sheet[key] = {'sheet_id': sheet_id, 'sheet_type': sheet_type}
                if order_id is not None:
                    combine_order_id = order_id
        # Для запуска из "раскрой по материалу" лист теперь можно выбрать один раз на группу,
        # иначе берём автоматический подбор (приоритет меньших обрезков/листов).
        if self._pin_order_id is not None:
            groups = sorted({(it['material_name'], int(it.get('thickness_mm', 4))) for it in items})
            for mat, thick in groups:
                key = (mat, thick)
                if key in fixed_first_sheet:
                    continue
                ans = QMessageBox.question(
                    self,
                    "Якорный лист",
                    "Материал: %s, %s мм.\nВыбрать якорный лист вручную для этой группы?\n\n"
                    "Нет — система выберет автоматически (с приоритетом меньших обрезков/листов)." % (mat, thick),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if ans != QMessageBox.Yes:
                    continue
                max_h, max_w = group_part_dims.get(key) or (0, 0)
                d = ChooseSheetDialog(mat, thick, max_h, max_w, self)
                if d.exec_() != QDialog.Accepted:
                    continue
                chosen = d.get_chosen()
                if not chosen or len(chosen) < 3:
                    continue
                fixed_first_sheet[key] = {'sheet_id': chosen[0], 'sheet_type': chosen[1]}
                if chosen[2] is not None:
                    combine_order_id = chosen[2]

        # Объединение с заказом: если выбран лист «в работе», добавляем изделия в тот заказ и пересчитываем
        order_id = None  # создаём заказ только если используем новый лист (остаток/целый)
        if combine_order_id is not None:
            existing = models.get_order_items(combine_order_id)
            items_for_layout = []
            for r in existing:
                qty = r.get('quantity') or 1
                for _ in range(qty):
                    items_for_layout.append({
                        'material_name': r.get('material_name') or '',
                        'height_mm': r.get('height_mm') or 0,
                        'width_mm': r.get('width_mm') or 0,
                        'quantity': 1,
                        'recipient_text': r.get('recipient_text'),
                        'edge_treatment': r.get('edge_treatment') or {},
                        'thickness_mm': r.get('thickness_mm', 4),
                    })
            for it in items:
                et = it.get('edge_treatment') or {}
                models.add_order_item(
                    combine_order_id, it['material_name'], it['height_mm'], it['width_mm'],
                    it['quantity'], it.get('recipient_text'),
                    edge_treatment_json=json.dumps(et) if et else None,
                    thickness_mm=it.get('thickness_mm', 4),
                )
                for _ in range(it.get('quantity', 1)):
                    items_for_layout.append({
                        'material_name': it['material_name'],
                        'height_mm': it['height_mm'],
                        'width_mm': it['width_mm'],
                        'quantity': 1,
                        'recipient_text': it.get('recipient_text'),
                        'edge_treatment': et,
                        'thickness_mm': it.get('thickness_mm', 4),
                    })
            items = items_for_layout
            order_id = combine_order_id
            # Не удаляем раскрой: листы «в работе» должны участвовать в раскладке (остатки и свободные места).
        # Иначе заказ не создаём до расчёта: создадим только если появятся новые листы (new_sheet_layouts)

        def get_sheets_for_material(mat, thickness_mm=None):
            # Всегда учитываем остатки и листы «в работе», чтобы не брать новый лист зря.
            thick = int(thickness_mm) if thickness_mm is not None else 4
            remnants = models.get_remnants_by_material_and_thickness(mat, thick)
            in_work = models.get_sheets_in_work_for_material_thickness(mat, thick)
            full = models.get_full_sheets_by_material_and_thickness(mat, thick)
            sheets = []
            for r in remnants:
                sheets.append({
                    'id': r['id'],
                    'width_mm': r['width_mm'],
                    'height_mm': r['height_mm'],
                    'sheet_type': 'remnant',
                    'thickness_mm': r.get('thickness_mm', thick),
                })
            for idx, s in enumerate(in_work):
                uid = -(int(s['order_id']) * 10000 + int(s.get('sheet_index', 0)) * 100 + idx)
                sheets.append({
                    'id': uid,
                    'width_mm': s['rect_w'],
                    'height_mm': s['rect_h'],
                    'sheet_type': 'in_work',
                    'thickness_mm': s['thickness_mm'],
                    'in_work_order_id': s['order_id'],
                    'in_work_sheet_index': s.get('sheet_index', 0),
                    'in_work_rect': {'x': s['rect_x'], 'y': s['rect_y'], 'w': s['rect_w'], 'h': s['rect_h']},
                })
            for f in full:
                qty = 1
                try:
                    qty = max(1, int(f.get('quantity') or 1))
                except Exception:
                    qty = 1
                for _ in range(qty):
                    sheets.append({
                        'id': f['id'],
                        'width_mm': f['width_mm'],
                        'height_mm': f['height_mm'],
                        'sheet_type': 'full',
                        'thickness_mm': f.get('thickness_mm', thick),
                    })
            type_rank = {'remnant': 0, 'in_work': 1, 'full': 2}
            sheets.sort(
                key=lambda s: (
                    type_rank.get(s.get('sheet_type') or 'full', 9),
                    (int(s.get('width_mm') or 0) * int(s.get('height_mm') or 0)),
                )
            )
            return sheets

        def get_threshold_for_material(mat, thickness_mm=None):
            return models.get_threshold_for_material(mat, thickness_mm)

        try:
            # Строим варианты раскроя и выбираем тот, где листы заполнены плотнее,
            # при прочих равных — меньше листов и меньше суммарная площадь листов.
            variants = compute_cutting_layout_variants(
                items, get_sheets_for_material, get_threshold_for_material,
                num_variants=4, fixed_first_sheet=fixed_first_sheet or None
            )
            def _variant_score(v):
                layouts = list((v or {}).get('layouts') or [])
                if not layouts:
                    return (-1.0, 0, 0.0)
                ratios = []
                total_sheet_area = 0
                for lay in layouts:
                    sw = int(lay.get('sheet_width') or 0)
                    sh = int(lay.get('sheet_height') or 0)
                    sa = max(1, sw * sh)
                    pa = 0
                    for p in (lay.get('pieces') or []):
                        pa += max(0, int(p.get('w') or 0)) * max(0, int(p.get('h') or 0))
                    ratios.append(float(pa) / float(sa))
                    total_sheet_area += sa
                avg_fill = sum(ratios) / float(len(ratios))
                return (avg_fill, -len(layouts), -total_sheet_area)
            result = max(variants, key=_variant_score) if variants else None
            if result:
                from logic.layout_learning import apply_layout_learning
                result = apply_layout_learning(result)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка расчёта", str(e))
            return

        if not result:
            QMessageBox.warning(self, "Расчёт", "Не удалось построить раскладку.")
            return
        if result.get('errors'):
            QMessageBox.warning(self, "Внимание", "\n".join(result['errors']))

        if not result.get('layouts'):
            if order_id is not None:
                QMessageBox.warning(self, "Расчёт", "Нет раскладки. Изделия добавлены в заказ.")
            else:
                QMessageBox.warning(self, "Расчёт", "Нет раскладки.")
            self.accept()
            return

        layouts_saved = []
        # remnants_created только из деловых остатков (business_rects); неделовые отходы не нумеруются и не сохраняются
        def _remnants_from_layout(layout):
            mat = layout.get('material') or ''
            th = layout.get('thickness_mm', 4)
            return [{'name': mat, 'height_mm': r['h'], 'width_mm': r['w'], 'thickness_mm': th} for r in (layout.get('business_rects') or [])]
        try:
            th = models.get_threshold_for_material(
                items[0]['material_name'] if items else '',
                items[0].get('thickness_mm', 4) if items else 4
            )
            min_h = (th or {}).get('min_height_mm', 0) or 0
            min_w = (th or {}).get('min_width_mm', 0) or 0
        except Exception:
            min_h, min_w = 0, 0
        added_to_orders = set()
        # Листы «в работе» — сливаем в существующие раскладки заказов
        new_sheet_layouts = []  # раскладки по новым листам (один лист — одна раскладка)
        for i, lay in enumerate(result.get('layouts', [])):
            if lay.get('in_work_order_id') is not None and lay.get('in_work_sheet_index') is not None:
                ow = lay['in_work_order_id']
                si = lay['in_work_sheet_index']
                rect = lay.get('in_work_rect') or {}
                ox = int(rect.get('x') or 0)
                oy = int(rect.get('y') or 0)
                rows = models.get_cut_results(ow)
                if si < len(rows) and isinstance(rows[si].get('layout'), dict):
                    existing = rows[si]['layout']
                    sw = existing.get('sheet_width') or 0
                    sh = existing.get('sheet_height') or 0
                    existing_pieces = list(existing.get('pieces') or [])
                    new_pieces = []
                    for p in lay.get('pieces') or []:
                        np = dict(p)
                        np['x'] = int(p.get('x') or 0) + ox
                        np['y'] = int(p.get('y') or 0) + oy
                        new_pieces.append(np)
                    merged_pieces = existing_pieces + new_pieces
                    business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, merged_pieces, min_h, min_w)
                    merged_layout = dict(existing, pieces=merged_pieces, business_rects=business_rects, waste_rects=waste_rects)
                    refresh_cut_segments_for_layout(merged_layout, min_h, min_w)
                    models.update_cut_result_layout(ow, si, merged_layout)
                    added_to_orders.add(ow)
                continue
            # Если алгоритм отдал раскладку на целый лист (full/remnant), а этот лист уже в работе в другом заказе — добавляем туда, а не создаём новую запись
            st, sid = lay.get('sheet_type'), lay.get('sheet_id')
            existing_usage = models.find_in_progress_sheet_usage(st, sid, exclude_order_id=order_id) if (st and sid is not None) else None
            if existing_usage:
                ow, si = existing_usage
                rows = models.get_cut_results(ow)
                if si >= 0 and si < len(rows) and isinstance(rows[si].get('layout'), dict):
                    existing = rows[si]['layout']
                    sw = existing.get('sheet_width') or 0
                    sh = existing.get('sheet_height') or 0
                    existing_pieces = list(existing.get('pieces') or [])
                    new_pieces = [dict(p, x=int(p.get('x') or 0), y=int(p.get('y') or 0)) for p in (lay.get('pieces') or [])]
                    merged_pieces = existing_pieces + new_pieces
                    business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, merged_pieces, min_h, min_w)
                    merged_layout = dict(existing, pieces=merged_pieces, business_rects=business_rects, waste_rects=waste_rects)
                    refresh_cut_segments_for_layout(merged_layout, min_h, min_w)
                    models.update_cut_result_layout(ow, si, merged_layout)
                    added_to_orders.add(ow)
                continue
            new_sheet_layouts.append(lay)
        # Один и тот же физический лист (sheet_type, sheet_id) — одна запись: объединяем изделия в одну раскладку
        by_sheet = {}
        for lay in new_sheet_layouts:
            key = (lay.get('sheet_type'), lay.get('sheet_id'))
            if key not in by_sheet:
                by_sheet[key] = []
            by_sheet[key].append(lay)
        layouts_saved = []
        for key, group in by_sheet.items():
            if len(group) == 1:
                lay = group[0]
                layouts_saved.append({
                    'sheet_type': lay['sheet_type'],
                    'sheet_id': lay['sheet_id'],
                    'layout': lay,
                    'remnants_created': _remnants_from_layout(lay),
                })
            else:
                sw = group[0].get('sheet_width') or 0
                sh = group[0].get('sheet_height') or 0
                all_pieces = []
                for lay in group:
                    all_pieces.extend(lay.get('pieces') or [])
                business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, all_pieces, min_h, min_w)
                merged = dict(group[0], pieces=all_pieces, business_rects=business_rects, waste_rects=waste_rects)
                refresh_cut_segments_for_layout(merged, min_h, min_w)
                layouts_saved.append({
                    'sheet_type': merged['sheet_type'],
                    'sheet_id': merged['sheet_id'],
                    'layout': merged,
                    'remnants_created': _remnants_from_layout(merged),
                })
        # Когда всё разместилось на листах «в работе» — новый заказ не создаём, позиции добавляем в существующий заказ
        if not new_sheet_layouts:
            if order_id is not None:
                # combine_order_id был задан — позиции уже в том заказе
                msg = "Все изделия размещены на свободных местах листов заказов в работе. Новые целые листы не использованы."
                if added_to_orders:
                    orders_str = ", ".join("№%s" % o for o in sorted(added_to_orders))
                    msg += "\n\nИзделия добавлены к заказу(ам): %s." % orders_str
                QMessageBox.information(self, "Раскрой", msg)
                self.accept()
                return
            # Новый заказ не создавали; все изделия ушли в существующие заказы — добавляем позиции туда
            if added_to_orders:
                target_order_id = min(added_to_orders)
                for it in items:
                    et = it.get('edge_treatment') or {}
                    models.add_order_item(
                        target_order_id, it['material_name'], it['height_mm'], it['width_mm'],
                        it['quantity'], it.get('recipient_text'),
                        edge_treatment_json=json.dumps(et) if et else None,
                        thickness_mm=it.get('thickness_mm', 4),
                    )
                orders_str = ", ".join("№%s" % o for o in sorted(added_to_orders))
                msg = "Все изделия размещены на свободных местах листов заказов в работе. Новые листы не использованы.\n\nПозиции добавлены в заказ(ы): %s." % orders_str
                QMessageBox.information(self, "Раскрой", msg)
                self.accept()
                return
            # На случай если added_to_orders пуст при пустых new_sheet_layouts (не должно быть)
            QMessageBox.information(self, "Раскрой", "Все изделия размещены на листах в работе.")
            self.accept()
            return

        # Есть новые листы — создаём заказ (если не combine). Клиент заказа = «Получатель» первого изделия; привязываем client_id по имени, чтобы в вебе отображались все данные (ФИЗ/ИП/юрлицо).
        if order_id is None:
            if self._pin_order_id is not None:
                try:
                    order_id = int(self._pin_order_id)
                except (TypeError, ValueError):
                    order_id = None
                if order_id is not None:
                    existing_rows = models.get_order_items(order_id) or []
                    if not existing_rows:
                        for it in items:
                            et = it.get('edge_treatment') or {}
                            models.add_order_item(
                                order_id, it['material_name'], it['height_mm'], it['width_mm'],
                                it['quantity'], it.get('recipient_text'),
                                edge_treatment_json=json.dumps(et) if et else None,
                                thickness_mm=it.get('thickness_mm', 4),
                            )
            if order_id is None:
                client_name = (items[0].get('recipient_text') or '').strip() if items else ''
                if not client_name:
                    QMessageBox.warning(
                        self,
                        "Раскрой",
                        "Укажите получателя (имя клиента) хотя бы у первого изделия — без клиента новый заказ не создаётся.",
                    )
                    return
                client_id = models.get_client_id_by_name(client_name) if client_name else None
                try:
                    order_id = models.create_order(client_name, client_id=client_id)
                except ValueError as e:
                    QMessageBox.warning(self, "Раскрой", str(e))
                    return
                for it in items:
                    et = it.get('edge_treatment') or {}
                    models.add_order_item(
                        order_id, it['material_name'], it['height_mm'], it['width_mm'],
                        it['quantity'], it.get('recipient_text'),
                        edge_treatment_json=json.dumps(et) if et else None,
                        thickness_mm=it.get('thickness_mm', 4),
                    )
        layouts_for_dialog = [x['layout'] for x in layouts_saved]
        from ui.cutting_result_dialog import CuttingResultDialog
        order_row = models.get_order(order_id) if order_id else None
        order_info = {
            'id': order_id,
            'order_id': order_id,
            'client_name': (order_row.get('client_name') or '').strip() if order_row else ((items[0].get('recipient_text') or '').strip() if items else ''),
            'created_at': order_row.get('created_at') if order_row else None,
        }
        if combine_order_id is not None:
            order_info['combined_with'] = combine_order_id
        if added_to_orders:
            orders_str = ", ".join("№%s" % o for o in sorted(added_to_orders))
            QMessageBox.information(self, "Раскрой", "Часть изделий добавлена к заказу(ам) в работе: %s. Остальное — на листах ниже." % orders_str)
        res_dlg = CuttingResultDialog(layouts_for_dialog, order_info, self, results_payload=layouts_saved)
        def save_pdf():
            try:
                from user_settings import get_models_dir
                folder = get_models_dir()
            except Exception:
                folder = None
            folder = folder or _cut_pdf_fallback_dir()
            path = os.path.join(folder, "Карты_раскроя_заказ_%s.pdf" % (order_id or "0"))
            generate_cutting_pdf(res_dlg.layouts, order_info, path)
            QMessageBox.information(self, "PDF", "Сохранено: %s" % path)
            _open_cut_output_file(path)
        res_dlg.btn_pdf.clicked.connect(save_pdf)
        res_dlg.exec_()
        self.accept()
