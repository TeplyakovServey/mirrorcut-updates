"""Модальное окно настроек: папки этикеток и моделей (PDF), минимальный размер «мелкой» детали."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QFileDialog, QFormLayout, QDialogButtonBox,
)
from PyQt5.QtCore import Qt

from app_paths import get_base_dir

def _app_dir():
    return get_base_dir()


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(480)
        try:
            from user_settings import (
                get_labels_dir, set_labels_dir,
                get_models_dir, set_models_dir,
                get_small_piece_mm, set_small_piece_mm,
            )
            self._get_labels = get_labels_dir
            self._set_labels = set_labels_dir
            self._get_models = get_models_dir
            self._set_models = set_models_dir
            self._get_small = get_small_piece_mm
            self._set_small = set_small_piece_mm
            self._has_settings = True
        except Exception:
            self._get_labels = self._set_labels = self._get_models = self._set_models = None
            self._get_small = lambda: 200
            self._set_small = lambda v: None
            self._has_settings = False

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

        # Папка моделей (PDF)
        row_models = QHBoxLayout()
        self.edit_models_dir = QLineEdit()
        self.edit_models_dir.setReadOnly(True)
        self.edit_models_dir.setPlaceholderText("не задана — папка программы")
        self.edit_models_dir.setMinimumWidth(280)
        row_models.addWidget(self.edit_models_dir)
        self.btn_choose_models = QPushButton("Выбрать…")
        self.btn_choose_models.clicked.connect(self._on_choose_models)
        row_models.addWidget(self.btn_choose_models)
        form.addRow("Папка моделей (PDF):", row_models)

        # Мин. размер детали
        self.spin_small_piece = QSpinBox()
        self.spin_small_piece.setRange(50, 1000)
        self.spin_small_piece.setSuffix(" мм")
        self.spin_small_piece.setMinimumWidth(100)
        form.addRow("Мин. размер детали (порог «мелкой»):", self.spin_small_piece)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_values()

    def _load_values(self):
        if self._has_settings and self._get_labels:
            self.edit_labels_dir.setText(self._get_labels() or "")
        else:
            self.edit_labels_dir.setText("")
        if self._has_settings and self._get_models:
            self.edit_models_dir.setText(self._get_models() or "")
        else:
            self.edit_models_dir.setText("")
        self.spin_small_piece.setValue(self._get_small() if callable(self._get_small) else 200)

    def _on_choose_labels(self):
        if not self._set_labels:
            return
        start = (self._get_labels() or _app_dir()) if self._get_labels else _app_dir()
        path = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения этикеток", start)
        if path:
            self._set_labels(path)
            self.edit_labels_dir.setText(path)

    def _on_choose_models(self):
        if not self._set_models:
            return
        start = (self._get_models() or _app_dir()) if self._get_models else _app_dir()
        path = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения карт раскроя (PDF)", start)
        if path:
            self._set_models(path)
            self.edit_models_dir.setText(path)

    def _save_and_accept(self):
        if self._set_small:
            self._set_small(self.spin_small_piece.value())
        self.accept()
