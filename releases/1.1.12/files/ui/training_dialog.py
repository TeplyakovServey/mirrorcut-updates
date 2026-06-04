# -*- coding: utf-8 -*-
"""Режим обучения: лист реального размера, случайные детали (оптимальная раскладка алгоритмом); кнопка «Сброс» — пустой лист и детали сбоку для перетаскивания. Сохранение только деталей на листе."""
import sys
import os
import random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QMessageBox,
    QWidget, QSpinBox, QScrollArea, QGroupBox, QFormLayout, QCheckBox,
    QSlider,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from db import models
from ui.layout_edit_dialog import LayoutEditCanvas
from ui.cutting_result_dialog import ChooseVariantDialog
from logic.cutting_algorithm import repack_pieces_on_sheet, compute_layout_variants_for_one_sheet

# Сколько образцов нужно для нейросети (прогресс «N из M»)
TARGET_TRAINING_SAMPLES = 1000
# Зона «сбоку» для деталей в режиме Сброс (мм)
STAGING_LEFT_MM = 350


def _random_piece_sizes_fit(sheet_w, sheet_h, count, max_area_ratio=0.8, min_side=80):
    """Случайные размеры (w, h) для count деталей: суммарная площадь не более max_area_ratio листа, все помещаются."""
    sheet_area = sheet_w * sheet_h
    target_area = sheet_area * max_area_ratio
    avg_piece = target_area / max(count, 1)
    max_side = int(min(sheet_w, sheet_h) * 0.45)
    max_side = max(max_side, min_side + 20)
    for _ in range(15):
        sizes = []
        for _ in range(count):
            w = random.randint(min_side, min(max_side, sheet_w - 2))
            h = random.randint(min_side, min(max_side, sheet_h - 2))
            sizes.append((w, h))
        total = sum(a * b for a, b in sizes)
        if total <= target_area:
            return sizes
        scale = (target_area / total) ** 0.5
        sizes = [(max(min_side, int(a * scale)), max(min_side, int(b * scale))) for a, b in sizes]
        total = sum(a * b for a, b in sizes)
        if total <= target_area:
            return sizes
    return [(min_side, min_side)] * count


def _layout_by_algorithm(sheet_w, sheet_h, piece_sizes):
    """Разместить детали на листе через repack_pieces_on_sheet. Возвращает список dict с x,y,w,h или None."""
    if not piece_sizes:
        return None
    pieces = [{'w': w, 'h': h, 'rotated': False} for w, h in piece_sizes]
    result = repack_pieces_on_sheet(sheet_w, sheet_h, pieces, min_h=0, min_w=0)
    if not result or len(result) != len(piece_sizes):
        return None
    return [{'x': int(p.get('x', 0)), 'y': int(p.get('y', 0)), 'w': int(p['w']), 'h': int(p['h']), 'rotated': bool(p.get('rotated', False))} for p in result]


def _pieces_on_sheet(pieces, sheet_w, sheet_h, inset_x=0):
    """Детали, целиком лежащие на листе (между inset_x и inset_x+sheet_w по x)."""
    x2 = inset_x + sheet_w
    return [p for p in pieces if inset_x <= p['x'] and p['y'] >= 0 and p['x'] + p['w'] <= x2 and p['y'] + p['h'] <= sheet_h]


def _pack_pieces_in_staging(staging_w_mm, sheet_h_mm, pieces, margin=12, gap=6):
    """Разложить детали в отдельном поле слева без наложения: рядами, при нехватке высоты — вторая колонка в той же зоне."""
    col1_x = -staging_w_mm + margin
    col2_x = -staging_w_mm // 2 + gap
    x_right_col1 = -staging_w_mm // 2 - gap
    x_right = -gap
    x, y = col1_x, margin
    row_h = 0
    result = []
    for p in pieces:
        w, h = p['w'], p['h']
        x_end = x_right_col1 if x < col2_x else x_right
        if x + w + gap > x_end:
            x = col2_x if x < col2_x else col1_x
            y += row_h + gap
            row_h = 0
            x_end = x_right_col1 if x < col2_x else x_right
        if y + h > sheet_h_mm - margin:
            if x >= col2_x:
                x = col1_x
                y = margin
                row_h = 0
            else:
                x = col2_x
                y = margin
                row_h = 0
            x_end = x_right_col1 if x < col2_x else x_right
        result.append(dict(p, x=x, y=y))
        row_h = max(row_h, h)
        x += w + gap
    return result


class TrainingDialog(QDialog):
    """Обучение: генерация по алгоритму, режим «Сброс» — детали сбоку, перетаскивание на лист. Сохраняются только детали на листе."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Обучение раскладке")
        self.setMinimumSize(900, 650)
        layout = QVBoxLayout(self)
        params = QGroupBox("Параметры")
        params_layout = QFormLayout(params)
        self.sheet_width_spin = QSpinBox()
        self.sheet_width_spin.setRange(400, 4000)
        self.sheet_width_spin.setValue(2440)
        self.sheet_width_spin.setSuffix(" мм")
        self.sheet_height_spin = QSpinBox()
        self.sheet_height_spin.setRange(400, 4000)
        self.sheet_height_spin.setValue(1220)
        self.sheet_height_spin.setSuffix(" мм")
        params_layout.addRow("Ширина листа:", self.sheet_width_spin)
        params_layout.addRow("Высота листа:", self.sheet_height_spin)
        self.btn_from_warehouse = QPushButton("Взять размер из склада")
        self.btn_from_warehouse.clicked.connect(self._load_sheet_from_warehouse)
        params_layout.addRow("", self.btn_from_warehouse)
        self.check_random_count = QCheckBox("Случайное количество деталей (3–15)")
        self.check_random_count.setChecked(True)
        params_layout.addRow("", self.check_random_count)
        self.piece_count_spin = QSpinBox()
        self.piece_count_spin.setRange(3, 25)
        self.piece_count_spin.setValue(8)
        params_layout.addRow("Количество деталей:", self.piece_count_spin)
        self.label_chosen_count = QLabel("")
        params_layout.addRow("", self.label_chosen_count)
        row_btn = QHBoxLayout()
        self.btn_generate = QPushButton("Сгенерировать")
        self.btn_generate.setToolTip("Создать случайные детали и разместить по лучшему алгоритму. Можно поправить и сохранить или нажать «Сброс» и расставлять вручную.")
        self.btn_generate.clicked.connect(self._generate)
        self.btn_other_options = QPushButton("Другие варианты")
        self.btn_other_options.setToolTip("Показать 4 варианта раскладки (как в основном раскрое). Выберите удобный и при необходимости поправьте.")
        self.btn_other_options.clicked.connect(self._other_options)
        self.btn_other_options.setEnabled(False)
        self.btn_drop = QPushButton("Сброс")
        self.btn_drop.setToolTip("Пустой лист, все детали переносятся в жёлтый сектор «Не для этого листа». Перетащите нужные на лист; детали в жёлтом секторе при сохранении не учитываются.")
        self.btn_drop.clicked.connect(self._drop)
        self.btn_drop.setEnabled(False)
        self.btn_save = QPushButton("Сохранить")
        self.btn_save.setToolTip("Сохранить образец (только детали на листе) в общую базу для обучения нейросети.")
        self.btn_save.clicked.connect(self._save_sample)
        self.btn_save.setEnabled(False)
        row_btn.addWidget(self.btn_generate)
        row_btn.addWidget(self.btn_other_options)
        row_btn.addWidget(self.btn_drop)
        row_btn.addWidget(self.btn_save)
        params_layout.addRow("", row_btn)
        self.label_unplaced = QLabel("")
        self.label_unplaced.setStyleSheet("color: #a04040;")
        params_layout.addRow("", self.label_unplaced)
        self.label_progress = QLabel("")
        self.label_progress.setStyleSheet("font-weight: bold; color: #1a5276;")
        self._update_progress_label()
        params_layout.addRow("", self.label_progress)
        layout.addWidget(params)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Масштаб:"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setMinimum(50)
        self.zoom_slider.setMaximum(400)
        self.zoom_slider.setValue(100)
        self.zoom_slider.setTickPosition(QSlider.TicksBelow)
        self.zoom_slider.setTickInterval(50)
        self.zoom_slider.valueChanged.connect(self._on_zoom_changed)
        zoom_row.addWidget(self.zoom_slider, 1)
        self.zoom_label = QLabel("100%")
        zoom_row.addWidget(self.zoom_label)
        layout.addLayout(zoom_row)
        self.canvas = LayoutEditCanvas(self)
        self.canvas.set_remnant_threshold(0, 0)
        self.canvas.layout_changed.connect(self._on_layout_changed)
        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(400)
        layout.addWidget(scroll, 1)
        self.setStyleSheet("""
            QDialog { background-color: #E8EEF5; }
            QPushButton { background-color: #4682B4; color: white; border-radius: 5px; padding: 8px 14px; }
            QPushButton:hover { background-color: #5A9BD5; }
            QPushButton:disabled { background-color: #aaa; }
        """)

    def _on_zoom_changed(self, value):
        self.canvas.set_zoom_factor(value / 100.0)
        self.zoom_label.setText("%d%%" % value)

    def _load_sheet_from_warehouse(self):
        sheets = models.get_all_full_sheets()
        if not sheets:
            QMessageBox.information(self, "Склад", "Нет целых листов на складе. Укажите размер вручную.")
            return
        first = sheets[0]
        w = first.get('width_mm') or 2440
        h = first.get('height_mm') or 1220
        self.sheet_width_spin.setValue(int(w))
        self.sheet_height_spin.setValue(int(h))

    def _get_count(self):
        if self.check_random_count.isChecked():
            return random.randint(3, 15)
        return self.piece_count_spin.value()

    def _generate(self):
        sw = self.sheet_width_spin.value()
        sh = self.sheet_height_spin.value()
        count = self._get_count()
        self.label_chosen_count.setText("Деталей на листе: %d" % count)
        piece_sizes = _random_piece_sizes_fit(sw, sh, count, max_area_ratio=0.8)
        pieces = _layout_by_algorithm(sw, sh, piece_sizes)
        if not pieces:
            gap = 2
            x, y, row_h = 0, 0, 0
            pieces = []
            for w, h in piece_sizes:
                if x + w + gap > sw:
                    x = 0
                    y += row_h + gap
                    row_h = 0
                if y + h > sh:
                    break
                pieces.append({'x': x, 'y': y, 'w': w, 'h': h, 'rotated': False})
                row_h = max(row_h, h)
                x += w + gap
        if not pieces:
            QMessageBox.warning(self, "Обучение", "Не удалось разместить детали. Уменьшите количество или размер листа.")
            return
        layout_dict = {'sheet_width': sw, 'sheet_height': sh, 'pieces': pieces}
        self.canvas.set_layout(layout_dict)
        self.btn_save.setEnabled(True)
        self.btn_drop.setEnabled(True)
        self.btn_other_options.setEnabled(True)
        self._update_unplaced_label()

    def _other_options(self):
        """Показать 4 варианта раскладки (как в основном раскрое), применить выбранный."""
        layout = self.canvas.get_layout()
        if not layout or not layout.get('pieces'):
            QMessageBox.information(self, "Обучение", "Сначала нажмите «Сгенерировать».")
            return
        sw = int(layout.get('sheet_width') or 0)
        sh = int(layout.get('sheet_height') or 0)
        pieces = list(layout.get('pieces') or [])
        if not pieces or sw < 100 or sh < 100:
            QMessageBox.warning(self, "Обучение", "Нет деталей или некорректный размер листа.")
            return
        try:
            variants = compute_layout_variants_for_one_sheet(sw, sh, pieces, min_h=0, min_w=0, thickness_mm=4)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        if not variants:
            QMessageBox.warning(self, "Другие варианты", "Не удалось построить варианты.")
            return
        if len(variants) < 2:
            QMessageBox.information(
                self, "Другие варианты", "Доступен только один отличный вариант — откроется окно выбора."
            )
        variant_results = []
        for v in variants[:4]:
            lay = {
                'sheet_width': v['sheet_width'],
                'sheet_height': v['sheet_height'],
                'pieces': v.get('pieces') or [],
                'business_rects': v.get('business_rects') or [],
                'waste_rects': v.get('waste_rects') or [],
            }
            variant_results.append({'layouts': [lay]})
        d = ChooseVariantDialog(variant_results, self)
        if d.exec_() != d.Accepted:
            return
        chosen = d.get_chosen()
        if not chosen or not chosen.get('layouts'):
            return
        lay = chosen['layouts'][0]
        new_pieces = lay.get('pieces') or []
        if len(new_pieces) != len(pieces):
            return
        new_sw = int(lay.get('sheet_width') or sw)
        new_sh = int(lay.get('sheet_height') or sh)
        layout_dict = {'sheet_width': new_sw, 'sheet_height': new_sh, 'pieces': new_pieces}
        self.canvas.set_layout(layout_dict)
        self.sheet_width_spin.setValue(new_sw)
        self.sheet_height_spin.setValue(new_sh)
        self._update_unplaced_label()
        QMessageBox.information(self, "Другие варианты", "Применён выбранный вариант. При необходимости поправьте и сохраните.")

    def _drop(self):
        layout = self.canvas.get_layout()
        if not layout or not layout.get('pieces'):
            QMessageBox.information(self, "Обучение", "Сначала нажмите «Сгенерировать».")
            return
        sw = layout.get('sheet_width') or 0
        sh = layout.get('sheet_height') or 0
        pieces = list(layout.get('pieces') or [])
        pieces = _pack_pieces_in_staging(STAGING_LEFT_MM, sh, pieces, margin=12, gap=6)
        layout_dict = {
            'sheet_width': sw,
            'sheet_height': sh,
            'staging_left_mm': STAGING_LEFT_MM,
            'pieces': pieces,
        }
        self.canvas.set_layout(layout_dict)
        self._update_unplaced_label()

    def _on_layout_changed(self):
        self._update_unplaced_label()

    def _update_unplaced_label(self):
        layout = self.canvas.get_layout() if self.canvas else None
        if not layout:
            self.label_unplaced.setText("")
            return
        sw = layout.get('sheet_width') or 0
        sh = layout.get('sheet_height') or 0
        pieces = layout.get('pieces') or []
        on_sheet = _pieces_on_sheet(pieces, sw, sh, inset_x=0)
        n_out = len(pieces) - len(on_sheet)
        if n_out > 0:
            self.label_unplaced.setText("Вне листа (лучше на другой лист): %d дет." % n_out)
        else:
            self.label_unplaced.setText("")

    def _save_sample(self):
        layout = self.canvas.get_layout()
        if not layout:
            QMessageBox.warning(self, "Обучение", "Нет раскладки. Нажмите «Сгенерировать».")
            return
        sw = layout.get('sheet_width') or 0
        sh = layout.get('sheet_height') or 0
        pieces = layout.get('pieces') or []
        on_sheet = _pieces_on_sheet(pieces, sw, sh, inset_x=0)
        if not on_sheet:
            QMessageBox.warning(self, "Обучение", "На листе нет деталей. Разместите детали на листе и нажмите «Сохранить». Детали вне листа не сохраняются (считаются «на другой лист»).")
            return
        if sw < 100 or sh < 100:
            QMessageBox.warning(self, "Обучение", "Некорректный размер листа.")
            return
        try:
            models.insert_layout_training_sample(sw, sh, on_sheet, source='training_tab')
            self._update_progress_label()
            if len(pieces) != len(on_sheet):
                QMessageBox.information(self, "Обучение", "Сохранено %d дет. на листе. %d дет. вне листа не сохранены (для другого листа)." % (len(on_sheet), len(pieces) - len(on_sheet)))
            else:
                QMessageBox.information(self, "Обучение", "Образец сохранён. Спасибо за вклад в обучение нейросети.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _update_progress_label(self):
        try:
            n = models.get_layout_training_count()
            m = TARGET_TRAINING_SAMPLES
            self.label_progress.setText("Создано образцов: %d из %d (для работы нейросети нужно не менее %d)" % (n, m, m))
        except Exception:
            self.label_progress.setText("Создано образцов: —")
