# -*- coding: utf-8 -*-
"""Настройки MAIN_PROJECT: папки (этикетки, раскрой), размер шрифта и макс. высота плиток."""
import sys
import os
_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QFileDialog, QFormLayout, QDialogButtonBox,
    QMessageBox,
)
from PyQt5.QtCore import Qt

from cfg_loader import get_base_dir, load_cfg, set_app_cfg, app_cfg, get_cfg_string, get_cfg_int


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(12)

        # Папка этикеток
        row_labels = QHBoxLayout()
        self.edit_labels_dir = QLineEdit()
        self.edit_labels_dir.setReadOnly(True)
        self.edit_labels_dir.setPlaceholderText("не задана — папка программы")
        self.edit_labels_dir.setMinimumWidth(280)
        row_labels.addWidget(self.edit_labels_dir)
        self.btn_choose_labels = QPushButton("Выбрать…")
        self.btn_choose_labels.clicked.connect(self._on_choose_labels)
        row_labels.addWidget(self.btn_choose_labels)
        form.addRow("Папка этикеток:", row_labels)

        # Папка раскроя (PDF)
        row_cutting = QHBoxLayout()
        self.edit_cutting_dir = QLineEdit()
        self.edit_cutting_dir.setReadOnly(True)
        self.edit_cutting_dir.setPlaceholderText("не задана — заказы/просчёты в подпапках")
        self.edit_cutting_dir.setMinimumWidth(280)
        row_cutting.addWidget(self.edit_cutting_dir)
        self.btn_choose_cutting = QPushButton("Выбрать…")
        self.btn_choose_cutting.clicked.connect(self._on_choose_cutting)
        row_cutting.addWidget(self.btn_choose_cutting)
        form.addRow("Корневая папка PDF:", row_cutting)

        # Размер шрифта в плитках
        self.spin_font_size = QSpinBox()
        self.spin_font_size.setRange(6, 14)
        self.spin_font_size.setSuffix(" пт")
        self.spin_font_size.setMinimumWidth(80)
        form.addRow("Размер шрифта в плитках:", self.spin_font_size)

        # Макс. высота плитки
        self.spin_max_height = QSpinBox()
        self.spin_max_height.setRange(60, 200)
        self.spin_max_height.setSuffix(" px")
        self.spin_max_height.setMinimumWidth(80)
        form.addRow("Макс. высота плитки:", self.spin_max_height)

        self._lbl_update = QLabel()
        self._lbl_update.setWordWrap(True)
        self._lbl_update.setStyleSheet("color: #333; font-size: 12px;")
        form.addRow("Версия (обновления):", self._lbl_update)
        row_rb = QHBoxLayout()
        self._btn_rollback = QPushButton("Откатить последнее обновление")
        self._btn_rollback.clicked.connect(self._on_rollback_update)
        row_rb.addWidget(self._btn_rollback)
        form.addRow("", row_rb)

        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_values()

    def _load_values(self):
        cfg = load_cfg()
        base = get_base_dir()
        labels = get_cfg_string(cfg, 'paths', 'labels_pdf_dir', '') if cfg else ''
        cutting = get_cfg_string(cfg, 'paths', 'pdf_output_dir', '') if cfg else ''
        if not cutting and cfg:
            cutting = get_cfg_string(cfg, 'paths', 'cutting_pdf_dir', '') or ''
        self.edit_labels_dir.setText(labels or "")
        self.edit_cutting_dir.setText(cutting or "")
        self.spin_font_size.setValue(get_cfg_int(cfg, 'ui', 'tile_font_size', 8) if cfg else 8)
        self.spin_max_height.setValue(get_cfg_int(cfg, 'ui', 'tile_max_height', 95) if cfg else 95)
        try:
            from update_client import read_install_version, get_install_root

            vr = read_install_version(get_install_root())
            self._lbl_update.setText(vr)
        except Exception:
            self._lbl_update.setText("—")

    def _on_rollback_update(self):
        try:
            from update_client import rollback_last_update, get_install_root

            ok, msg = rollback_last_update(get_install_root())
        except Exception as ex:
            ok, msg = False, str(ex)
        if ok:
            QMessageBox.information(self, "Обновления", msg)
        else:
            QMessageBox.warning(self, "Обновления", msg)

    def _on_choose_labels(self):
        start = (self.edit_labels_dir.text() or "").strip() or get_base_dir()
        path = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения этикеток", start)
        if path:
            self.edit_labels_dir.setText(path)

    def _on_choose_cutting(self):
        start = (self.edit_cutting_dir.text() or "").strip() or get_base_dir()
        path = QFileDialog.getExistingDirectory(
            self,
            "Выберите корневую папку для PDF (внутри будут «заказы» и «просчёты»)",
            start,
        )
        if path:
            self.edit_cutting_dir.setText(path)

    def _save_and_accept(self):
        path = os.path.join(get_base_dir(), 'app.cfg')
        cfg = app_cfg() or load_cfg()
        if not cfg:
            from configparser import ConfigParser
            cfg = ConfigParser()
        for section in ('paths', 'ui'):
            if not cfg.has_section(section):
                cfg.add_section(section)
        cfg.set('paths', 'labels_pdf_dir', self.edit_labels_dir.text().strip())
        pdf_root = self.edit_cutting_dir.text().strip()
        cfg.set('paths', 'pdf_output_dir', pdf_root)
        cfg.set('paths', 'cutting_pdf_dir', pdf_root)
        cfg.set('ui', 'tile_font_size', str(self.spin_font_size.value()))
        cfg.set('ui', 'tile_max_height', str(self.spin_max_height.value()))
        try:
            with open(path, 'w', encoding='utf-8') as f:
                cfg.write(f)
        except Exception:
            pass
        set_app_cfg(load_cfg())
        self.accept()
