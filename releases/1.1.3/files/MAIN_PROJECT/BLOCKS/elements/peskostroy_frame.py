# -*- coding: utf-8 -*-
import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont, QMouseEvent, QPixmap
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from calc.upload_client import upload_sketch_file
from elements.calc_tile_style import apply_service_tile_frame, style_cost_label, style_tile_header
from elements.glass_product_tile import ImagePreviewDialog

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
MAX_BYTES = 2 * 1024 * 1024
THUMB = 52


class _SandThumbLabel(QLabel):
    def __init__(self, get_path, parent=None):
        super().__init__(parent)
        self._get_path = get_path
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(THUMB, THUMB)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("QLabel { border: 1px solid #888; background: #fff; }")

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.LeftButton:
            p = self._get_path()
            if p and os.path.isfile(p):
                ImagePreviewDialog(p, self.window()).exec_()
        super().mousePressEvent(e)


class PeskostroyFrame(QWidget):
    sandChanged = pyqtSignal()

    SAND_TYPES = (
        "Сплошное матирование",
        "Рисунок",
        "Пескоструйная кнопка",
        "Полосы ЗСП",
    )

    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        t = QLabel("ПЕСКОСТРУЙ")
        style_tile_header(t)
        lay.addWidget(t)

        self.chk = QCheckBox("Нужен пескоструй")
        self.chk.setFont(QFont("Arial", 8))
        lay.addWidget(self.chk)

        self._grp = QButtonGroup(self)
        self._radios = []
        for name in self.SAND_TYPES:
            rb = QRadioButton(name)
            rb.setFont(QFont("Arial", 7))
            self._grp.addButton(rb)
            self._radios.append(rb)
            lay.addWidget(rb)
        if self._radios:
            self._radios[0].setChecked(True)

        self.chk_double = QCheckBox("Двухсторонний")
        self.chk_double.setFont(QFont("Arial", 7))
        lay.addWidget(self.chk_double)

        self._file_row = QWidget()
        fr = QHBoxLayout(self._file_row)
        fr.setContentsMargins(0, 0, 0, 0)
        self.btn_file = QPushButton("Файл…")
        self.btn_file.setFont(QFont("Arial", 7))
        self.btn_file.setToolTip("Необязательно: свой рисунок (на сервер — только при «Сохранить JSON»)")
        self.btn_file.clicked.connect(self._pick_file)
        self._thumb = _SandThumbLabel(self._local_file_path)
        self._thumb.setToolTip("Просмотр в полном размере")
        self._btn_clear_f = QPushButton("✕")
        self._btn_clear_f.setFixedSize(22, 22)
        self._btn_clear_f.setFont(QFont("Arial", 9, QFont.Bold))
        self._btn_clear_f.setToolTip("Убрать файл")
        self._btn_clear_f.clicked.connect(self._clear_file)
        fr.addWidget(self.btn_file)
        fr.addWidget(self._thumb)
        fr.addWidget(self._btn_clear_f)
        fr.addStretch()
        lay.addWidget(self._file_row)

        self.lbl_file = QLabel("—")
        self.lbl_file.setFont(QFont("Arial", 6))
        self.lbl_file.setWordWrap(True)
        lay.addWidget(self.lbl_file)

        self.cost_label = QLabel("—")
        self.cost_label.setWordWrap(True)
        style_cost_label(self.cost_label)
        lay.addWidget(self.cost_label)
        lay.addStretch()

        self._file_path = ""
        self._uploaded_url = ""
        self._update_thumb_empty()
        self.chk.toggled.connect(self._on_toggle)
        self._grp.buttonClicked.connect(self._update_type_widgets)
        self._on_toggle(self.chk.isChecked())
        self._update_type_widgets()

    def _local_file_path(self) -> str:
        return self._file_path or ""

    def _update_thumb_empty(self):
        self._thumb.clear()
        self._thumb.setText("")
        self._btn_clear_f.setEnabled(bool(self._file_path))

    def _set_thumb_from_path(self, path: str):
        self._file_path = path
        self._uploaded_url = ""
        pix = QPixmap(path)
        if not pix.isNull():
            self._thumb.setPixmap(
                pix.scaled(THUMB - 4, THUMB - 4, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
            self._thumb.setText("")
        else:
            self._update_thumb_empty()
        self._btn_clear_f.setEnabled(bool(path))
        self.lbl_file.setText(os.path.basename(path)[:40] if path else "—")

    def _clear_file(self):
        self._file_path = ""
        self._uploaded_url = ""
        self._update_thumb_empty()
        self.lbl_file.setText("—")
        self.sandChanged.emit()

    def upload_if_needed(self):
        """Загрузка на сервер только при сохранении просчёта (upload_sketch=True)."""
        if not self.chk.isChecked():
            return
        typ = self.selected_type_ru()
        if typ not in ("Рисунок", "Полосы ЗСП"):
            return
        if not self._file_path or not os.path.isfile(self._file_path):
            return
        if self._uploaded_url:
            return
        res = upload_sketch_file(self._file_path)
        if res.get("ok"):
            self._uploaded_url = (res.get("url") or "").strip()

    def _update_type_widgets(self):
        typ = self.selected_type_ru()
        m2 = typ in ("Сплошное матирование", "Рисунок", "Полосы ЗСП")
        self.chk_double.setVisible(m2)
        need_file = typ in ("Рисунок", "Полосы ЗСП")
        self._file_row.setVisible(need_file)
        self.lbl_file.setVisible(need_file)
        on = self.chk.isChecked()
        self.btn_file.setEnabled(on and need_file)
        self._btn_clear_f.setEnabled(on and need_file and bool(self._file_path))
        self.chk_double.setEnabled(on and m2)

    def _on_toggle(self, on: bool):
        for rb in self._radios:
            rb.setEnabled(on)
        if not on:
            self.cost_label.setText("—")
        self._update_type_widgets()

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Изображение",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not path:
            return
        if not os.path.isfile(path):
            return
        try:
            if os.path.getsize(path) > MAX_BYTES:
                QMessageBox.warning(self, "Файл", "Не более 2 МБ")
                return
        except OSError:
            return
        ext = os.path.splitext(path)[1].lower()
        if ext not in IMAGE_EXTS:
            QMessageBox.warning(self, "Файл", "Нужен формат изображения (PNG, JPG, …)")
            return
        self._set_thumb_from_path(path)
        self.sandChanged.emit()

    def reset_to_defaults(self):
        self.chk.blockSignals(True)
        self.chk.setChecked(False)
        self.chk.blockSignals(False)
        self.chk_double.setChecked(False)
        self._clear_file()
        if self._radios:
            self._radios[0].setChecked(True)
        self._on_toggle(False)

    def selected_type_ru(self) -> str:
        for rb in self._radios:
            if rb.isChecked():
                return rb.text()
        return self.SAND_TYPES[0]

    def get_payload(self) -> dict:
        typ = self.selected_type_ru() if self.chk.isChecked() else None
        file_ref = None
        if self.chk.isChecked() and typ in ("Рисунок", "Полосы ЗСП"):
            file_ref = self._uploaded_url or self._file_path or None
        return {
            "Пескоструй": self.chk.isChecked(),
            "Тип": typ,
            "Двухсторонний": self.chk_double.isChecked(),
            "Файл": file_ref,
        }

    def set_cost_text(self, text: str):
        self.cost_label.setText(text)
