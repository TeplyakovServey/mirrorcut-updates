# -*- coding: utf-8 -*-
import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QDragEnterEvent, QDropEvent, QFont, QMouseEvent, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from calc.db_postgres import fetch_photo_print_price, insert_photo_print_upload
from elements.calc_tile_style import apply_service_tile_frame, style_cost_label, style_tile_header
from elements.glass_product_tile import ImagePreviewDialog

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
MAX_BYTES = 2 * 1024 * 1024


class _PhotoThumbLabel(QLabel):
    """Клик — полноэкранный просмотр, как макет на главной плитке."""

    def __init__(self, get_path, parent=None):
        super().__init__(parent)
        self._get_path = get_path
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.LeftButton:
            p = self._get_path()
            if p and os.path.isfile(p):
                ImagePreviewDialog(p, self.window()).exec_()
        super().mousePressEvent(e)


class PhotoPickArea(QFrame):
    """Клик / DnD, подсветка при наведении, превью + сброс."""

    imageChanged = pyqtSignal()

    def __init__(self, photo_tile: "Photo"):
        super().__init__(photo_tile)
        self._photo = photo_tile
        self.setAcceptDrops(True)
        self.setFixedHeight(76)
        self.setCursor(Qt.PointingHandCursor)
        self._style_idle = (
            "QFrame#PhotoPickArea { border: 2px dashed #555; background: #e8f5e9; border-radius: 4px; }"
        )
        self._style_hover = (
            "QFrame#PhotoPickArea { border: 2px dashed #1a6b2e; background: #d4efd9; border-radius: 4px; }"
        )
        self._style_filled = (
            "QFrame#PhotoPickArea { border: 1px solid #333; background: #fff; border-radius: 4px; }"
        )
        self.setObjectName("PhotoPickArea")
        self.setStyleSheet(self._style_idle)

        self._stack = QStackedWidget(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.addWidget(self._stack)

        self._page_empty = QWidget()
        el = QVBoxLayout(self._page_empty)
        self._hint = QLabel("Нажмите или перетащите\nфото сюда")
        self._hint.setAlignment(Qt.AlignCenter)
        self._hint.setWordWrap(True)
        self._hint.setFont(QFont("Arial", 11, QFont.Bold))
        el.addWidget(self._hint)

        self._page_filled = QWidget()
        fl = QHBoxLayout(self._page_filled)
        fl.setContentsMargins(4, 2, 4, 2)
        self._thumb = _PhotoThumbLabel(lambda: getattr(self._photo, "_local_path", ""))
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setMinimumSize(96, 56)
        self._thumb.setMaximumSize(120, 64)
        self._thumb.setScaledContents(False)
        self._thumb.setToolTip("Нажмите — просмотр в полном размере")
        fl.addWidget(self._thumb, 1)
        self._btn_clear = QPushButton("✕")
        self._btn_clear.setFixedSize(26, 26)
        self._btn_clear.setFont(QFont("Arial", 12, QFont.Bold))
        self._btn_clear.setToolTip("Удалить фото")
        self._btn_clear.clicked.connect(self._clear_image)
        fl.addWidget(self._btn_clear, 0, Qt.AlignTop)

        self._stack.addWidget(self._page_empty)
        self._stack.addWidget(self._page_filled)
        self._stack.setCurrentWidget(self._page_empty)

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent):
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self._photo.load_image_path(path)
        e.acceptProposedAction()

    def enterEvent(self, e):
        if self._stack.currentWidget() is self._page_empty:
            self.setStyleSheet(self._style_hover)
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self._stack.currentWidget() is self._page_empty:
            self.setStyleSheet(self._style_idle)
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._stack.currentWidget() is self._page_empty:
            self._open_dialog()
        super().mousePressEvent(e)

    def _open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Фото для печати",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if path:
            self._photo.load_image_path(path)

    def _clear_image(self):
        self._photo.clear_image()

    def show_empty(self):
        self._stack.setCurrentWidget(self._page_empty)
        self._thumb.clear()
        self.setStyleSheet(self._style_idle)
        self.setCursor(Qt.PointingHandCursor)

    def show_thumb(self, pix: QPixmap):
        self._stack.setCurrentWidget(self._page_filled)
        self.setStyleSheet(self._style_filled)
        self.setCursor(Qt.ArrowCursor)
        if not pix.isNull():
            self._thumb.setPixmap(
                pix.scaled(self._thumb.maximumSize(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )


class Photo(QWidget):
    imageChanged = pyqtSignal()

    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        t = QLabel("ФОТОПЕЧАТЬ")
        style_tile_header(t)
        lay.addWidget(t)

        self.chk = QCheckBox("Нужна фотопечать")
        self.chk.setFont(QFont("Arial", 8))
        lay.addWidget(self.chk)

        self.pick = PhotoPickArea(self)
        lay.addWidget(self.pick)

        self.lbl_price = QLabel("Цена м²: —")
        self.lbl_price.setFont(QFont("Arial", 7))
        lay.addWidget(self.lbl_price)

        self.cost_label = QLabel("—")
        self.cost_label.setWordWrap(True)
        style_cost_label(self.cost_label)
        lay.addWidget(self.cost_label)
        lay.addStretch()

        self._local_path = ""
        self._db_upload_id = None
        self._refresh_price_label()

    def _refresh_price_label(self):
        p = fetch_photo_print_price()
        self.lbl_price.setText("Цена за м²: %s ₽" % p if p else "БД недоступна")

    def load_image_path(self, path: str):
        ext = os.path.splitext(path)[1].lower()
        if ext not in IMAGE_EXTS:
            return
        try:
            sz = os.path.getsize(path)
        except OSError:
            return
        if sz > MAX_BYTES:
            return
        self._local_path = path
        pix = QPixmap(path)
        if not pix.isNull():
            self.pick.show_thumb(pix)
        try:
            with open(path, "rb") as f:
                data = f.read()
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
            uid = insert_photo_print_upload(data, mime_type=mime, file_name=os.path.basename(path))
            self._db_upload_id = uid
        except Exception:
            self._db_upload_id = None
        self.imageChanged.emit()

    def clear_image(self):
        self._local_path = ""
        self._db_upload_id = None
        self.pick.show_empty()
        self.imageChanged.emit()

    def reset_to_defaults(self):
        self.chk.blockSignals(True)
        self.chk.setChecked(False)
        self.chk.blockSignals(False)
        self.clear_image()
        self.cost_label.setText("—")

    def is_enabled_service(self) -> bool:
        return self.chk.isChecked()

    def get_extra_payload(self) -> dict:
        return {
            "Локальный файл": self._local_path or None,
            "ID в БД": self._db_upload_id,
        }

    def set_cost_text(self, text: str):
        self.cost_label.setText(text)
