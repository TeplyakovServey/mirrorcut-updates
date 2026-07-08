# -*- coding: utf-8 -*-
"""Плитка 2×2: материал, форма, размеры, макет (квадрат 405×405, без внутреннего скролла)."""
from __future__ import annotations

import math
from typing import Optional
import os
import urllib.request

from PyQt5.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QDragEnterEvent, QDropEvent, QFont, QIntValidator, QMouseEvent, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QFormLayout,
    QVBoxLayout,
    QWidget,
)

from calc import palette as PAL
from calc.db_postgres import load_materials_tree, tree_lookup_price_status
from calc.corner_rounding import parse_thickness_mm
from calc.geometry import check_fit, compute_shape_metrics
from calc.upload_client import upload_sketch_file
from elements.calc_tile_style import glass_tile_children_qss, grid_span_width
from elements.test import materials_dict

SHAPES = ["Прямоугольник", "Круг", "Треугольник", "Овал", "Трапеция", "Сложная фигура"]
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif"}
MAX_BYTES = 2 * 1024 * 1024
CUSTOM_VARIANT_RULES = {
    "Зеркало матовое(сатин)": {"thicknesses": [4]},
    "Стекло окрашеное глянец": {"thicknesses": [4]},
}
HIDDEN_MATERIAL_TYPES = {"Стекло Альфа"}
PREFERRED_MATERIAL_FIRST = "Стекло прозрачное"


def _sort_material_names(names) -> list:
    """«Стекло прозрачное» — первым в списке материалов."""
    items = [m for m in names if m not in HIDDEN_MATERIAL_TYPES]
    pref = PREFERRED_MATERIAL_FIRST
    if pref in items:
        items.remove(pref)
        return [pref] + sorted(items, key=lambda x: str(x).lower())
    return sorted(items, key=lambda x: str(x).lower())


try:
    from ui.hold_delete_button import HoldDeleteButtonLTR
except ImportError:
    HoldDeleteButtonLTR = None  # type: ignore


def _is_silver_colorless_mirror_variant(text: str) -> bool:
    t = (text or "").strip()
    s = t.lower().replace(" ", "").replace("\\", "/")
    if "серебро" not in s:
        return False
    if "бесцвет" in s:
        return True
    return "б/цв" in s or "бцв" in s


def _default_mirror_variant_index(combo):
    """Бесцветное зеркало (серебро бесцветное) — по умолчанию, не бронза."""
    prefer = -1
    for i in range(combo.count()):
        t = (combo.itemText(i) or "").strip()
        if _is_silver_colorless_mirror_variant(t):
            if "бесцвет" in t.lower():
                return i
            if prefer < 0:
                prefer = i
    return prefer if prefer >= 0 else (0 if combo.count() else -1)


def _sort_mirror_variants(variants):
    items = list(variants or [])
    if not items:
        return []

    def _rank(v):
        if _is_silver_colorless_mirror_variant(v):
            return (0, 0 if "бесцвет" in (v or "").lower() else 1, (v or "").lower())
        return (1, 0, (v or "").lower())

    return sorted(items, key=_rank)

# 2 плитки сетки + один зазор (как в xx.py) — низ совпадает со 2-м рядом плиток.
BIG_SIDE_W = grid_span_width(2)
BIG_SIDE_H = BIG_SIDE_W
BIG_SIDE = BIG_SIDE_W
SKETCH_BOX = 76


class ImagePreviewDialog(QDialog):
    def __init__(self, path_or_url: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Просмотр макета")
        self.setFixedSize(720, 720)
        lay = QVBoxLayout(self)
        img = QLabel()
        img.setAlignment(Qt.AlignCenter)
        img.setMinimumSize(700, 620)
        pix = QPixmap()
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            try:
                with urllib.request.urlopen(path_or_url, timeout=30) as r:
                    data = r.read()
                pix.loadFromData(data)
            except Exception:
                pass
        else:
            pix.load(path_or_url)
        if not pix.isNull():
            pix = pix.scaled(700, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img.setPixmap(pix)
        lay.addWidget(img)
        btn_row = QHBoxLayout()
        save_btn = QPushButton("Сохранить как…")
        save_btn.clicked.connect(lambda: self._save_as(pix))
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

    def _save_as(self, pix: QPixmap):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить", "", "Images (*.png *.jpg *.jpeg)")
        if path and not pix.isNull():
            pix.save(path)


class SketchSlot(QLabel):
    """Одна зона: подсказка / превью после загрузки."""

    def __init__(self, glass_tile: "GlassProductTile"):
        super().__init__(glass_tile)
        self._glass = glass_tile
        self.setAcceptDrops(True)
        self.setFixedSize(SKETCH_BOX, SKETCH_BOX)
        self.setAlignment(Qt.AlignCenter)
        surf = PAL.GLASS_TILE_FILL
        self._empty_style = (
            "QLabel { border: 2px dashed #444; background: %s; color: #222; "
            "font-size: 8px; padding: 2px; }" % surf
        )
        self._fill_style = "QLabel { border: 1px solid #333; background: %s; }" % surf
        self.setStyleSheet(self._empty_style)
        self.setText("Перетащите\nмакет\nили клик")
        self.setWordWrap(True)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._glass._sketch_clicked()
        super().mousePressEvent(e)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self._glass._on_sketch_path(path, upload_now=False)
        e.acceptProposedAction()

    def show_empty(self):
        self.setStyleSheet(self._empty_style)
        self.clear()
        self.setText("Перетащите\nмакет\nили клик")

    def show_pixmap(self, pix: QPixmap):
        self.setStyleSheet(self._fill_style)
        self.setText("")
        if not pix.isNull():
            self.setPixmap(pix.scaled(SKETCH_BOX - 4, SKETCH_BOX - 4, Qt.KeepAspectRatio, Qt.SmoothTransformation))


class GlassProductTile(QFrame):
    """Квадрат 2×2 ячейки; полный пересчёт цен — по кнопке «Рассчитать» (сигнал calculateClicked)."""

    calculateClicked = pyqtSignal()
    """Лёгкое обновление подписей геометрии без БД."""
    localMetricsChanged = pyqtSignal()
    """Любое изменение параметров изделия: скрываем остальные блоки до нового «Рассчитать»."""
    configurationChanged = pyqtSignal()
    """Закалка / подгонка — не сбрасывают сетку; только пересчёт цен (см. xx.py)."""
    pricingOptionsChanged = pyqtSignal()
    """Запрос показать дополнительные плитки (2+)."""
    additionalRequested = pyqtSignal()
    """Удержание «Очистить» — сброс плитки и (в xx.py) всех блоков заказа."""
    clearHoldComplete = pyqtSignal()

    def __init__(
        self,
        parent=None,
        *,
        show_additional_button: bool = True,
        show_clear_button: bool = True,
        compact_height: bool = False,
    ):
        super().__init__(parent)
        self.setObjectName("glassProductTile")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._glass_border_ready = False
        self._apply_glass_border_style()
        glass_h = BIG_SIDE_W if compact_height else BIG_SIDE_H
        self.setFixedSize(BIG_SIDE_W, glass_h)

        self._mat_tree: dict = load_materials_tree()
        self._use_db = bool(self._mat_tree)
        self._local_sketch_path: str = ""
        self._sketch_url: str = ""
        self._last_metrics: dict | None = None
        self._is_custom_variant_active = False
        self._metrics_timer = QTimer(self)
        self._metrics_timer.setSingleShot(True)
        self._metrics_timer.setInterval(50)
        self._metrics_timer.timeout.connect(self._refresh_metrics_local_impl)
        self._config_timer = QTimer(self)
        self._config_timer.setSingleShot(True)
        self._config_timer.setInterval(120)
        self._config_timer.timeout.connect(self._emit_configuration_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(2)
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(2)

        r = 0
        lf = QFont("Arial", 8)
        for lb in ("Материал", "Цвет", "Толщ.", "Форма"):
            grid.addWidget(QLabel(lb), r, 0)
            r += 1
        self.combo_material = QComboBox()
        self.combo_material.setFont(lf)
        self.combo_variant = QComboBox()
        self.combo_variant.setFont(lf)
        self.combo_thickness = QComboBox()
        self.combo_thickness.setFont(lf)
        self.combo_shape = QComboBox()
        self.combo_shape.setFont(lf)
        self.combo_shape.addItems(SHAPES)
        grid.addWidget(self.combo_material, 0, 1, 1, 3)
        grid.addWidget(self.combo_variant, 1, 1, 1, 3)
        grid.addWidget(self.combo_thickness, 2, 1, 1, 3)
        grid.addWidget(self.combo_shape, 3, 1, 1, 3)

        self.stack_dims = QStackedWidget()
        self.stack_dims.setFixedHeight(22)
        self._page_rect = self._make_hw_page()
        self._page_circle = self._make_circle_page()
        self._page_tri = self._make_tri_page()
        self._page_trap = self._make_trap_page()
        self._page_complex = self._make_complex_page()
        for p in (
            self._page_rect,
            self._page_circle,
            self._page_tri,
            self._page_trap,
            self._page_complex,
        ):
            self.stack_dims.addWidget(p)
        grid.addWidget(QLabel("Размеры"), 4, 0)
        grid.addWidget(self.stack_dims, 4, 1, 1, 3)

        grid.addWidget(QLabel("Кол-во"), 5, 0)
        self.edit_qty = QLineEdit("1")
        self.edit_qty.setFixedWidth(48)
        self.edit_qty.setValidator(QIntValidator(1, 99999))
        self.edit_qty.setFont(lf)
        grid.addWidget(self.edit_qty, 5, 1)

        sk_row = QHBoxLayout()
        sk_row.setSpacing(4)
        self.sketch_slot = SketchSlot(self)
        sk_row.addWidget(self.sketch_slot)
        self.btn_clear_sketch = QPushButton("Удалить\nмакет")
        self.btn_clear_sketch.setFixedWidth(52)
        self.btn_clear_sketch.setFont(lf)
        self.btn_clear_sketch.clicked.connect(self._clear_sketch)
        sk_row.addWidget(self.btn_clear_sketch)
        sk_row.addStretch()
        grid.addLayout(sk_row, 6, 0, 1, 4)

        self.chk_temper = QCheckBox("Закалка")
        self.chk_fit = QCheckBox("Подгонка")
        self.chk_temper.setFont(lf)
        self.chk_fit.setFont(lf)
        hb2 = QHBoxLayout()
        hb2.addWidget(self.chk_temper)
        hb2.addWidget(self.chk_fit)
        hb2.addStretch()
        fit_block = QVBoxLayout()
        fit_block.setSpacing(2)
        fit_block.setContentsMargins(0, 0, 0, 0)
        fit_block.addLayout(hb2)
        self.lbl_fit_hint = QLabel(
            "Стоимость полировки, шлифовки и фацета выше на 50\u202f% "
            'из\u2011за услуги «подгонка размеров».'
        )
        self.lbl_fit_hint.setFont(QFont("Arial", 7))
        self.lbl_fit_hint.setWordWrap(True)
        self.lbl_fit_hint.setStyleSheet("color: %s; padding: 0 2px;" % PAL.TILE_TEXT)
        self.lbl_fit_hint.setVisible(False)
        fit_block.addWidget(self.lbl_fit_hint)
        grid.addLayout(fit_block, 7, 0, 1, 4)

        self.lbl_geom = QLabel("Фигура / охват: —")
        self.lbl_mat_cost = QLabel("Материал: «Рассчитать»")
        self.lbl_warn = QLabel("")
        for lb in (self.lbl_geom, self.lbl_mat_cost, self.lbl_warn):
            lb.setFont(lf)
            lb.setWordWrap(True)
        self.lbl_warn.setStyleSheet("color: %s;" % PAL.WARN_TEXT)
        grid.addWidget(self.lbl_geom, 8, 0, 1, 4)
        grid.addWidget(self.lbl_mat_cost, 9, 0, 1, 4)
        grid.addWidget(self.lbl_warn, 10, 0, 1, 4)

        self.custom_variant_box = QWidget()
        custom_form = QFormLayout(self.custom_variant_box)
        custom_form.setContentsMargins(0, 0, 0, 0)
        custom_form.setHorizontalSpacing(6)
        custom_form.setVerticalSpacing(2)
        self.edit_custom_color = QLineEdit()
        self.edit_custom_color.setPlaceholderText("Введите цвет")
        self.edit_custom_price = QLineEdit()
        self.edit_custom_price.setPlaceholderText("Введите цену за м²")
        self.edit_custom_price.setValidator(QIntValidator(1, 100000000))
        self.edit_custom_color.setFont(lf)
        self.edit_custom_price.setFont(lf)
        custom_form.addRow("Цвет (другой):", self.edit_custom_color)
        custom_form.addRow("Цена (₽/м²):", self.edit_custom_price)
        self.custom_variant_box.setVisible(False)
        grid.addWidget(self.custom_variant_box, 11, 0, 1, 4)

        self.btn_clear = None
        if show_clear_button and HoldDeleteButtonLTR is not None:
            self.btn_clear = HoldDeleteButtonLTR("Очистить", hold_ms=1000)
            self.btn_clear.setToolTip("Удерживайте 1 с — сброс всех блоков в начальное состояние")
            self.btn_clear.holdComplete.connect(self.clearHoldComplete.emit)
            grid.addWidget(self.btn_clear, 12, 0, 1, 4)

        self.btn_calc = QPushButton("Рассчитать")
        self.btn_calc.setStyleSheet("font-weight: bold; padding: 4px; background: #ddab22;")
        self.btn_calc.clicked.connect(self.calculateClicked.emit)
        calc_row = 13 if self.btn_clear is not None else 12
        grid.addWidget(self.btn_calc, calc_row, 0, 1, 4)
        if self.btn_clear is not None:
            ch = self.btn_calc.sizeHint().height()
            self.btn_clear.setMinimumHeight(max(ch, 28))
            self.btn_calc.setMinimumHeight(max(ch, 28))

        self.btn_more = QPushButton("Дополнительно")
        self.btn_more.setStyleSheet(
            "font-weight: bold; padding: 4px; background: #2e7d32; color: white;"
        )
        self.btn_more.clicked.connect(self.additionalRequested.emit)
        self.btn_more.setVisible(bool(show_additional_button))
        grid.addWidget(self.btn_more, calc_row + 1, 0, 1, 4)

        root.addLayout(grid)

        self._populate_materials()
        self.combo_material.currentIndexChanged.connect(self._on_material)
        self.combo_variant.currentIndexChanged.connect(self._on_variant)
        self.combo_thickness.currentIndexChanged.connect(self._on_thickness)
        self.combo_shape.currentIndexChanged.connect(self._on_shape)
        self.edit_qty.textChanged.connect(self._refresh_metrics_local)
        self.chk_temper.stateChanged.connect(self._on_temper_pricing_option)
        self.chk_fit.stateChanged.connect(self._on_fit_toggled)

        self.chk_template = QCheckBox("По шаблону")
        self.chk_template.setFont(lf)
        self.edit_template_pct = QLineEdit("30")
        self.edit_template_pct.setFixedWidth(40)
        self.edit_template_pct.setValidator(QIntValidator(30, 70))
        self.edit_template_pct.setFont(lf)
        self.edit_template_pct.setVisible(False)
        hb_tpl = QHBoxLayout()
        hb_tpl.setSpacing(4)
        hb_tpl.addWidget(self.chk_template)
        hb_tpl.addWidget(QLabel("%"))
        hb_tpl.addWidget(self.edit_template_pct)
        hb_tpl.addStretch()
        fit_block.addLayout(hb_tpl)
        self.chk_template.stateChanged.connect(self._on_template_changed)
        self.edit_template_pct.textChanged.connect(self._on_template_pct_changed)
        for w in (
            self._h_rect,
            self._w_rect,
            self._d_circle,
            self._tri_a,
            self._tri_b,
            self._tri_c,
            self._trap_bot,
            self._trap_top,
            self._trap_h,
            self._cx_w,
            self._cx_h,
        ):
            w.textChanged.connect(self._refresh_metrics_local)
        self._on_shape()
        self._sync_fit_hint()

    def _make_hw_page(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self._h_rect = QLineEdit()
        self._w_rect = QLineEdit()
        for e in (self._h_rect, self._w_rect):
            e.setValidator(QIntValidator(1, 100000))
            e.setMaximumWidth(52)
            e.setFont(QFont("Arial", 8))
        h.addWidget(QLabel("В"))
        h.addWidget(self._h_rect)
        h.addWidget(QLabel("Ш"))
        h.addWidget(self._w_rect)
        h.addStretch()
        return w

    def _make_circle_page(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self._d_circle = QLineEdit()
        self._d_circle.setValidator(QIntValidator(1, 100000))
        self._d_circle.setMaximumWidth(56)
        self._d_circle.setFont(QFont("Arial", 8))
        h.addWidget(QLabel("D"))
        h.addWidget(self._d_circle)
        h.addStretch()
        return w

    def _make_tri_page(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self._tri_a = QLineEdit()
        self._tri_b = QLineEdit()
        self._tri_c = QLineEdit()
        for e in (self._tri_a, self._tri_b, self._tri_c):
            e.setValidator(QIntValidator(1, 100000))
            e.setMaximumWidth(44)
            e.setFont(QFont("Arial", 8))
        h.addWidget(QLabel("A"))
        h.addWidget(self._tri_a)
        h.addWidget(QLabel("B"))
        h.addWidget(self._tri_b)
        h.addWidget(QLabel("C"))
        h.addWidget(self._tri_c)
        h.addStretch()
        return w

    def _make_trap_page(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self._trap_bot = QLineEdit()
        self._trap_top = QLineEdit()
        self._trap_h = QLineEdit()
        for e in (self._trap_bot, self._trap_top, self._trap_h):
            e.setValidator(QIntValidator(1, 100000))
            e.setMaximumWidth(44)
            e.setFont(QFont("Arial", 8))
        h.addWidget(QLabel("Н"))
        h.addWidget(self._trap_bot)
        h.addWidget(QLabel("В"))
        h.addWidget(self._trap_top)
        h.addWidget(QLabel("h"))
        h.addWidget(self._trap_h)
        h.addStretch()
        return w

    def _make_complex_page(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        self._cx_h = QLineEdit()
        self._cx_w = QLineEdit()
        for e in (self._cx_h, self._cx_w):
            e.setValidator(QIntValidator(1, 100000))
            e.setMaximumWidth(52)
            e.setFont(QFont("Arial", 8))
        h.addWidget(QLabel("В"))
        h.addWidget(self._cx_h)
        h.addWidget(QLabel("Ш"))
        h.addWidget(self._cx_w)
        h.addStretch()
        return w

    def _populate_materials(self):
        self.combo_material.blockSignals(True)
        self.combo_material.clear()
        self.combo_material.addItem("—")
        if self._use_db:
            mats = _sort_material_names(self._mat_tree.keys())
        else:
            mats = _sort_material_names(materials_dict.keys())
        self.combo_material.addItems(mats)
        self.combo_material.blockSignals(False)

    def select_preferred_default_material(self) -> None:
        """Выбрать «Стекло прозрачное» в комбобоксе, если есть в справочнике."""
        idx = self.combo_material.findText(PREFERRED_MATERIAL_FIRST)
        if idx >= 0:
            self.combo_material.setCurrentIndex(idx)
            self._on_material()

    def _on_material(self):
        self.combo_variant.blockSignals(True)
        self.combo_variant.clear()
        self.combo_thickness.clear()
        self.combo_variant.setEnabled(False)
        self.combo_thickness.setEnabled(False)
        mt = self.combo_material.currentText()
        if mt in ("—", ""):
            self.combo_variant.blockSignals(False)
            self._refresh_metrics_local()
            return
        if self._use_db and mt in self._mat_tree:
            vars_list = (
                _sort_mirror_variants(self._mat_tree[mt].keys())
                if mt == "Зеркало"
                else sorted(self._mat_tree[mt].keys())
            )
            self.combo_variant.addItems(vars_list)
            self.combo_variant.setEnabled(True)
            if mt == "Зеркало":
                idx = _default_mirror_variant_index(self.combo_variant)
                if idx >= 0:
                    self.combo_variant.setCurrentIndex(idx)
        elif not self._use_db and mt in materials_dict:
            vars_list = (
                _sort_mirror_variants(materials_dict[mt].keys())
                if mt == "Зеркало"
                else list(materials_dict[mt].keys())
            )
            self.combo_variant.addItems(vars_list)
            self.combo_variant.setEnabled(True)
            if mt == "Зеркало":
                idx = _default_mirror_variant_index(self.combo_variant)
                if idx >= 0:
                    self.combo_variant.setCurrentIndex(idx)
        if mt in CUSTOM_VARIANT_RULES and self.combo_variant.findText("другой") < 0:
            self.combo_variant.addItem("другой")
        self.combo_variant.blockSignals(False)
        self._on_variant()

    def _on_variant(self):
        self.combo_thickness.blockSignals(True)
        self.combo_thickness.clear()
        self.combo_thickness.setEnabled(False)
        mt = self.combo_material.currentText()
        var = self.combo_variant.currentText()
        if not var or mt in ("—", ""):
            self.combo_thickness.blockSignals(False)
            self._refresh_metrics_local()
            return
        if self._use_db and mt in self._mat_tree and var in self._mat_tree[mt]:
            ths = sorted({t[0] for t in self._mat_tree[mt][var]})
            self.combo_thickness.addItems([str(x) for x in ths])
            self.combo_thickness.setEnabled(bool(ths))
        elif not self._use_db and mt in materials_dict and var in materials_dict[mt]:
            th = sorted(materials_dict[mt][var])
            self.combo_thickness.addItems([str(x) for x in th])
            self.combo_thickness.setEnabled(True)
        elif var == "другой" and mt in CUSTOM_VARIANT_RULES:
            ths = CUSTOM_VARIANT_RULES[mt]["thicknesses"]
            self.combo_thickness.addItems([str(x) for x in ths])
            self.combo_thickness.setEnabled(bool(ths))
        self.combo_thickness.blockSignals(False)
        self._sync_custom_variant_ui()
        self._refresh_temper_availability()
        self._refresh_metrics_local()

    def _on_thickness(self):
        self._sync_custom_variant_ui()
        self._refresh_temper_availability()
        self._refresh_metrics_local()

    def _sync_custom_variant_ui(self):
        mt = self.combo_material.currentText()
        var = self.combo_variant.currentText()
        active = mt in CUSTOM_VARIANT_RULES and var == "другой"
        was_active = bool(self._is_custom_variant_active)
        self._is_custom_variant_active = bool(active)
        self.custom_variant_box.setVisible(self._is_custom_variant_active)
        if self._is_custom_variant_active and not was_active:
            # Каждый новый выбор варианта "другой" требует повторного ввода.
            self.edit_custom_color.clear()
            self.edit_custom_price.clear()

    def _refresh_temper_availability(self):
        mt = self.combo_material.currentText()
        var = self.combo_variant.currentText()
        th_txt = self.combo_thickness.currentText()
        th_mm = parse_thickness_mm(th_txt)
        if var == "другой" and mt in CUSTOM_VARIANT_RULES:
            self.chk_temper.setVisible(True)
            self.chk_temper.setEnabled(True)
            self.chk_temper.setToolTip("")
            return
        if not self._use_db or th_mm <= 0 or mt not in self._mat_tree:
            self.chk_temper.setVisible(True)
            self.chk_temper.setEnabled(True)
            self.chk_temper.setToolTip("")
            return
        row = tree_lookup_price_status(self._mat_tree, mt, var, th_mm)
        if row is None:
            self.chk_temper.setVisible(True)
            self.chk_temper.setEnabled(True)
            self.chk_temper.setToolTip("")
            return
        _price, status = row
        if int(status or 0) == 0:
            self.chk_temper.setChecked(False)
            self.chk_temper.setVisible(False)
            self.chk_temper.setEnabled(False)
            self.chk_temper.setToolTip("")
        else:
            self.chk_temper.setVisible(True)
            self.chk_temper.setEnabled(True)
            self.chk_temper.setToolTip("")

    def set_temper_impact_hint(self, percent: Optional[float], one_rub: Optional[int]) -> None:
        """Показывает влияние закалки на цену изделия прямо в подписи чекбокса."""
        if percent is None or one_rub is None or one_rub <= 0:
            self.chk_temper.setText("Закалка")
            return
        self.chk_temper.setText("Закалка (+%s%%, +%s ₽)" % (round(float(percent), 1), int(one_rub)))

    def _on_shape(self):
        sh = self.combo_shape.currentText()
        idx = {"Прямоугольник": 0, "Овал": 0, "Круг": 1, "Треугольник": 2, "Трапеция": 3, "Сложная фигура": 4}[sh]
        self.stack_dims.setCurrentIndex(idx)
        self._refresh_metrics_local()

    def _int(self, edit: QLineEdit) -> int:
        t = edit.text().strip()
        return int(t) if t.isdigit() else 0

    def _qty(self) -> int:
        t = self.edit_qty.text().strip()
        return int(t) if t.isdigit() else 1

    def _shape_values(self) -> dict:
        sh = self.combo_shape.currentText()
        q = self._qty()
        if sh in ("Прямоугольник", "Овал"):
            return {"quantity": q, "width": self._int(self._w_rect), "height": self._int(self._h_rect)}
        if sh == "Круг":
            return {"quantity": q, "diameter": self._int(self._d_circle)}
        if sh == "Треугольник":
            return {
                "quantity": q,
                "a": self._int(self._tri_a),
                "b": self._int(self._tri_b),
                "c": self._int(self._tri_c),
            }
        if sh == "Трапеция":
            return {
                "quantity": q,
                "b_bottom": self._int(self._trap_bot),
                "b_top": self._int(self._trap_top),
                "height_trap": self._int(self._trap_h),
            }
        return {"quantity": q, "width": self._int(self._cx_w), "height": self._int(self._cx_h)}

    def last_geometry_metrics(self):
        return self._last_metrics

    def _refresh_metrics_local(self):
        self._metrics_timer.start()

    def _refresh_metrics_local_impl(self):
        self.lbl_warn.setText("")
        sh = self.combo_shape.currentText()
        vals = self._shape_values()
        if sh == "Сложная фигура":
            if not self._sketch_url and not (self._local_sketch_path and os.path.isfile(self._local_sketch_path)):
                self.lbl_geom.setText("Фигура / охват: нужен макет")
                self._last_metrics = None
                self.localMetricsChanged.emit()
                self._config_timer.start()
                return
        m = compute_shape_metrics(sh, vals)
        if not m:
            self.lbl_geom.setText("Фигура / охват: проверьте треугольник")
            self._last_metrics = None
            self.localMetricsChanged.emit()
            self._config_timer.start()
            return
        w, h = vals.get("width", vals.get("diameter", 0)), vals.get("height", vals.get("diameter", 0))
        if sh in ("Прямоугольник", "Овал"):
            if w and h and w < 50 and h < 50:
                self.lbl_warn.setText("Сторона > 50 мм")
            elif w and h and not check_fit(w, h):
                self.lbl_warn.setText("Не влезает на лист 2250×3210")
        pm = m["Периметр (мм)"]
        am = m["Площадь (м²)"]
        bp = m["bbox_perimeter_mm"]
        ba = m["bbox_area_m2"]
        q = int(m.get("Количество (шт)") or 1)
        complex_shape = sh == "Сложная фигура"
        total_p = m.get("Общий периметр (мм)", pm * q)
        total_s = m.get("Общая площадь (м²)", round(am * q, 4))
        if q > 1:
            if complex_shape:
                line1 = "Фиг.: P=%s S=%s | Охват: P=%s S=%s" % (
                    pm,
                    round(am, 4),
                    int(bp),
                    round(ba, 4),
                )
            else:
                line1 = "Фиг.: P=%s мм S=%s" % (pm, round(am, 4))
            line2 = "Всего %s шт.: P=%s S=%s" % (q, total_p, total_s)
            self.lbl_geom.setText("%s\n%s" % (line1, line2))
        elif complex_shape:
            self.lbl_geom.setText(
                "Фиг.: P=%s мм S=%s | Охват: P=%s мм S=%s"
                % (pm, round(am, 4), int(bp), round(ba, 4))
            )
        else:
            self.lbl_geom.setText("Фиг.: P=%s мм S=%s" % (pm, round(am, 4)))
        self._last_metrics = m
        self.localMetricsChanged.emit()
        self._config_timer.start()

    def _notify_configuration_changed(self):
        self._config_timer.start()

    def _emit_configuration_changed(self):
        self.configurationChanged.emit()

    def _apply_glass_border_style(self):
        b = PAL.GLASS_TILE_BORDER_READY if self._glass_border_ready else PAL.GLASS_TILE_BORDER_IDLE
        self.setStyleSheet(
            "#glassProductTile { border: 3px solid %s; background-color: %s; border-radius: 3px; }"
            % (b, PAL.GLASS_TILE_FILL)
            + glass_tile_children_qss()
        )

    def set_glass_border_highlight(self, pricing_done: bool) -> None:
        """Рамка первого блока: после успешного «Рассчитать» — акцентный цвет из палитры."""
        self._glass_border_ready = bool(pricing_done)
        self._apply_glass_border_style()

    def _sync_fit_hint(self):
        self.lbl_fit_hint.setVisible(self.chk_fit.isChecked())
        self.edit_template_pct.setVisible(self.chk_template.isChecked())

    def _on_temper_pricing_option(self, _state=None):
        self.pricingOptionsChanged.emit()

    def _on_fit_toggled(self, _state=None):
        self._sync_fit_hint()
        self.pricingOptionsChanged.emit()

    def _on_template_changed(self, _state=None):
        self._sync_fit_hint()
        self.pricingOptionsChanged.emit()

    def _on_template_pct_changed(self, *_args):
        if self.chk_template.isChecked():
            self.pricingOptionsChanged.emit()

    def is_ready_for_pricing(self) -> bool:
        mt = self.combo_material.currentText()
        if mt in ("", "—"):
            return False
        if not (self.combo_variant.currentText() or "").strip():
            return False
        th_txt = self.combo_thickness.currentText()
        if parse_thickness_mm(th_txt) <= 0:
            return False
        if self._is_custom_variant_active:
            if not (self.edit_custom_color.text() or "").strip():
                return False
            ptxt = (self.edit_custom_price.text() or "").strip()
            if not ptxt.isdigit() or int(ptxt) <= 0:
                return False
        sh = self.combo_shape.currentText()
        vals = self._shape_values()
        if sh == "Сложная фигура":
            if not self._sketch_url and not (
                self._local_sketch_path and os.path.isfile(self._local_sketch_path)
            ):
                return False
        m = compute_shape_metrics(sh, vals)
        return bool(m and int(m.get("Периметр (мм)") or 0) > 0)

    def _sketch_clicked(self):
        if self._local_sketch_path and os.path.isfile(self._local_sketch_path):
            src = self._sketch_url or self._local_sketch_path
            ImagePreviewDialog(src, self).exec_()
        else:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Макет",
                "",
                "Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif)",
            )
            if path:
                self._on_sketch_path(path, upload_now=False)

    def _on_sketch_path(self, path: str, upload_now: bool = False):
        if not os.path.isfile(path):
            return
        if os.path.getsize(path) > MAX_BYTES:
            QMessageBox.warning(self, "Файл", "Не более 2 МБ")
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in IMAGE_EXTS:
            QMessageBox.warning(self, "Файл", "Нужен формат изображения")
            return
        self._local_sketch_path = path
        self._sketch_url = ""
        pix = QPixmap(path)
        self.sketch_slot.show_pixmap(pix)
        if upload_now:
            self._try_upload()
        self._refresh_metrics_local()

    def _try_upload(self):
        if not (self._local_sketch_path and os.path.isfile(self._local_sketch_path)):
            return
        res = upload_sketch_file(self._local_sketch_path)
        if res.get("ok"):
            self._sketch_url = res.get("url") or ""
            self.lbl_warn.setText("Макет на сервере")
        else:
            err = (res.get("error") or "ошибка").strip()
            if err.isdigit():
                err = "HTTP %s" % err
            if len(err) > 120:
                err = err[:120] + "…"
            self.lbl_warn.setText("Сервер: %s" % err)
        self._notify_configuration_changed()

    def ensure_sketch_uploaded(self):
        """Вызывать перед сохранением просчёта."""
        if self._local_sketch_path and not self._sketch_url:
            self._try_upload()

    def _clear_sketch(self):
        self._local_sketch_path = ""
        self._sketch_url = ""
        self.sketch_slot.show_empty()
        self._refresh_metrics_local()

    def build_selected(self, upload_sketch: bool = False) -> dict:
        if upload_sketch:
            self.ensure_sketch_uploaded()
        sh = self.combo_shape.currentText()
        vals = self._shape_values()
        m = compute_shape_metrics(sh, vals)
        material_data = {"Форма": sh, "Подгонка размеров": self.chk_fit.isChecked()}
        material_data["Изготовление по шаблону"] = self.chk_template.isChecked()
        try:
            material_data["Процент шаблон (%)"] = int((self.edit_template_pct.text() or "0").strip())
        except ValueError:
            material_data["Процент шаблон (%)"] = 0
        if m:
            material_data.update(
                {
                    "Периметр (мм)": m["Периметр (мм)"],
                    "Площадь (м²)": m["Площадь (м²)"],
                    "Количество (шт)": m["Количество (шт)"],
                    "Общий периметр (мм)": m["Общий периметр (мм)"],
                    "Общая площадь (м²)": m["Общая площадь (м²)"],
                }
            )
            for key in (
                "Ширина (мм)",
                "Высота (мм)",
                "Диаметр (мм)",
                "Сторона A (мм)",
                "Сторона B (мм)",
                "Сторона C (мм)",
                "Трапеция низ (мм)",
                "Трапеция верх (мм)",
                "Трапеция высота (мм)",
                "Кромка верх (мм)",
                "Кромка низ (мм)",
                "Кромка лево (мм)",
                "Кромка право (мм)",
            ):
                if key in m:
                    material_data[key] = m[key]
        sketch = self._sketch_url or self._local_sketch_path
        if sketch:
            material_data["Файл"] = sketch
        mt = self.combo_material.currentText()
        var = self.combo_variant.currentText()
        th_txt = self.combo_thickness.currentText()
        th_mm = parse_thickness_mm(th_txt)
        mat_params: dict = {
            "Тип материала": mt if mt != "—" else "",
            "Цвет / Вариант": var,
            "Толщина (мм)": th_mm,
            "Закалка": self.chk_temper.isChecked(),
        }
        if self._is_custom_variant_active:
            custom_color = (self.edit_custom_color.text() or "").strip()
            custom_price_txt = (self.edit_custom_price.text() or "").strip()
            if not custom_color:
                raise ValueError("Для варианта «другой» укажите цвет.")
            if not custom_price_txt.isdigit() or int(custom_price_txt) <= 0:
                raise ValueError("Для варианта «другой» укажите цену целым числом.")
            mat_params["Цвет / Вариант"] = custom_color
            mat_params["Цена за м²"] = int(custom_price_txt)
            mat_params["status_zakalka"] = 1
            return {"Параметры изделия": material_data, "Параметры материала": mat_params}
        if self._use_db and mt != "—" and var and th_mm > 0:
            row = tree_lookup_price_status(self._mat_tree, mt, var, th_mm)
            if row:
                price, status = row
                mat_params["Цена за м²"] = math.ceil(float(price or 0))
                mat_params["status_zakalka"] = int(status or 0)
        return {"Параметры изделия": material_data, "Параметры материала": mat_params}

    def set_material_cost_label(self, text: str):
        self.lbl_mat_cost.setText(text)

    def _clear_all_dimension_fields(self) -> None:
        for e in (
            self._h_rect,
            self._w_rect,
            self._d_circle,
            self._tri_a,
            self._tri_b,
            self._tri_c,
            self._trap_bot,
            self._trap_top,
            self._trap_h,
            self._cx_w,
            self._cx_h,
        ):
            e.blockSignals(True)
            e.clear()
            e.blockSignals(False)

    def reset_to_defaults(self) -> None:
        """Сброс плитки стекла в начальное состояние (блок остаётся на экране)."""
        blockers = (
            self.combo_material,
            self.combo_variant,
            self.combo_thickness,
            self.combo_shape,
            self.edit_qty,
            self.chk_temper,
            self.chk_fit,
            self.chk_template,
            self.edit_template_pct,
        )
        for w in blockers:
            w.blockSignals(True)
        self.combo_material.setCurrentIndex(0)
        self.combo_variant.clear()
        self.combo_thickness.clear()
        self.combo_variant.setEnabled(False)
        self.combo_thickness.setEnabled(False)
        if self.combo_shape.count():
            self.combo_shape.setCurrentIndex(0)
        self.edit_qty.setText("1")
        self.chk_temper.setChecked(False)
        self.chk_fit.setChecked(False)
        self.chk_template.setChecked(False)
        self.edit_template_pct.setText("30")
        self._is_custom_variant_active = False
        self.custom_variant_box.setVisible(False)
        self.edit_custom_color.clear()
        self.edit_custom_price.clear()
        for w in blockers:
            w.blockSignals(False)
        self._clear_all_dimension_fields()
        self._clear_sketch()
        self._glass_border_ready = False
        self._apply_glass_border_style()
        self.chk_temper.setText("Закалка")
        self.lbl_geom.setText("Фигура / охват: —")
        self.lbl_mat_cost.setText("Материал: укажите материал и размеры")
        self.lbl_warn.setText("")
        self._sync_fit_hint()
        self._on_shape()
        self._notify_configuration_changed()

    def apply_from_saved_data(self, izd: Optional[dict], matp: Optional[dict]) -> None:
        """Заполнить виджеты из сохранённых блоков Параметры изделия / материала (упрощённое восстановление)."""
        izd = izd if isinstance(izd, dict) else {}
        matp = matp if isinstance(matp, dict) else {}

        mt = str(matp.get("Тип материала") or "").strip()
        var = str(matp.get("Цвет / Вариант") or "").strip()
        th_mm = matp.get("Толщина (мм)")
        th_txt = ""
        try:
            if th_mm is not None and float(th_mm) > 0:
                th_txt = str(int(round(float(th_mm))))
        except (TypeError, ValueError):
            th_txt = ""

        self.combo_material.blockSignals(True)
        self.combo_variant.blockSignals(True)
        self.combo_thickness.blockSignals(True)
        self.combo_shape.blockSignals(True)

        im = self.combo_material.findText(mt)
        if im >= 0:
            self.combo_material.setCurrentIndex(im)
        self.combo_material.blockSignals(False)

        self._on_material()

        self.combo_variant.blockSignals(True)
        iv = self.combo_variant.findText(var)
        if iv >= 0:
            self.combo_variant.setCurrentIndex(iv)
        self.combo_variant.blockSignals(False)

        self._on_variant()

        self.combo_thickness.blockSignals(True)
        if th_txt:
            it = self.combo_thickness.findText(th_txt)
            if it >= 0:
                self.combo_thickness.setCurrentIndex(it)
        self.combo_thickness.blockSignals(False)

        self.chk_temper.blockSignals(True)
        self.chk_temper.setChecked(bool(matp.get("Закалка")))
        self.chk_temper.blockSignals(False)
        self._refresh_temper_availability()

        self.chk_fit.blockSignals(True)
        self.chk_fit.setChecked(bool(izd.get("Подгонка размеров")))
        self.chk_fit.blockSignals(False)

        self.chk_template.blockSignals(True)
        self.chk_template.setChecked(bool(izd.get("Изготовление по шаблону")))
        self.chk_template.blockSignals(False)
        try:
            pct = int(izd.get("Процент шаблон (%)") or 30)
        except (TypeError, ValueError):
            pct = 30
        pct = max(30, min(70, pct))
        self.edit_template_pct.blockSignals(True)
        self.edit_template_pct.setText(str(pct))
        self.edit_template_pct.blockSignals(False)
        self.edit_template_pct.setVisible(self.chk_template.isChecked())

        sh = str(izd.get("Форма") or "Прямоугольник").strip()
        ishape = self.combo_shape.findText(sh)
        if ishape >= 0:
            self.combo_shape.setCurrentIndex(ishape)
        self.combo_shape.blockSignals(False)

        q = izd.get("Количество (шт)")
        try:
            qv = max(1, int(q)) if q is not None else 1
        except (TypeError, ValueError):
            qv = 1
        self.edit_qty.blockSignals(True)
        self.edit_qty.setText(str(qv))
        self.edit_qty.blockSignals(False)

        def _set_int(edit: QLineEdit, key: str):
            v = izd.get(key)
            if v is None:
                return
            try:
                edit.setText(str(int(round(float(v)))))
            except (TypeError, ValueError):
                pass

        if sh in ("Прямоугольник", "Овал"):
            _set_int(self._h_rect, "Высота (мм)")
            _set_int(self._w_rect, "Ширина (мм)")
        elif sh == "Круг":
            _set_int(self._d_circle, "Диаметр (мм)")
        elif sh == "Треугольник":
            _set_int(self._tri_a, "Сторона A (мм)")
            _set_int(self._tri_b, "Сторона B (мм)")
            _set_int(self._tri_c, "Сторона C (мм)")
        elif sh == "Трапеция":
            _set_int(self._trap_bot, "Трапеция низ (мм)")
            _set_int(self._trap_top, "Трапеция верх (мм)")
            _set_int(self._trap_h, "Трапеция высота (мм)")
        elif sh == "Сложная фигура":
            _set_int(self._cx_h, "Высота (мм)")
            _set_int(self._cx_w, "Ширина (мм)")
            sketch = izd.get("Файл")
            sketch = str(sketch or "").strip()
            if sketch.startswith("http://") or sketch.startswith("https://"):
                self._local_sketch_path = ""
                self._sketch_url = sketch
                pix = QPixmap()
                try:
                    with urllib.request.urlopen(sketch, timeout=20) as r:
                        pix.loadFromData(r.read())
                except Exception:
                    pass
                if not pix.isNull():
                    self.sketch_slot.show_pixmap(pix)
                else:
                    self.sketch_slot.show_empty()
            elif sketch and os.path.isfile(sketch):
                self._on_sketch_path(sketch, upload_now=False)

        self._on_shape()
        self._sync_fit_hint()
        self._refresh_metrics_local()
