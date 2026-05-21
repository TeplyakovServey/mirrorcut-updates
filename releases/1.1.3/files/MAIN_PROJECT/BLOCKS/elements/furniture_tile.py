# -*- coding: utf-8 -*-
"""Плитка «Фурнитура»: справочник из БД (или встроенный список), поиск по подстроке, превью, количество, цены."""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt, QStringListModel, pyqtSignal
from PyQt5.QtGui import QFont, QIntValidator, QMouseEvent, QPixmap
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDesktopWidget,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from calc.db_postgres import fetch_furniture_catalog
from elements.calc_tile_style import (
    apply_service_tile_frame,
    style_cost_label,
    style_tile_header,
)

_PREVIEW_BG = "#5D8F93"
_IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_FONT_SM = QFont("Arial", 7)


def _blocks_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _furniture_img_dir() -> str:
    return os.path.join(_blocks_dir(), "img_fur")


def _resolve_furniture_image(photo_base: str) -> str | None:
    base = (photo_base or "").strip()
    if not base:
        return None
    d = _furniture_img_dir()
    for ext in _IMG_EXTS:
        p = os.path.join(d, base + ext)
        if os.path.isfile(p):
            return p
    return None


def _display_row_label(r: dict) -> str:
    n = (r.get("name") or "").strip()
    c = (r.get("color") or "").strip()
    tm = r.get("thickness_mm")
    try:
        tmv = int(tm) if tm is not None else 0
    except (TypeError, ValueError):
        tmv = 0
    if c:
        base = ("%s %s" % (n, c)).strip()
    else:
        base = n or c
    if tmv > 0:
        return ("%s, %s мм" % (base, tmv)).strip()
    return base or n or "—"


def _normalize_catalog_row(r: dict) -> dict:
    out = {str(k).lower(): v for k, v in r.items()}
    if "is_shelf_holder" not in out:
        out["is_shelf_holder"] = False
    if "source_url" not in out:
        out["source_url"] = ""
    return out


class FurnitureFullImageDialog(QDialog):
    """Модальное окно: изображение 400×400, по центру доступного экрана."""

    _IMG_SIDE = 400

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Фурнитура — изображение")
        self.setModal(True)
        self._norm_path = os.path.normpath(image_path)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self._img = QLabel()
        self._img.setAlignment(Qt.AlignCenter)
        self._img.setFixedSize(self._IMG_SIDE, self._IMG_SIDE)
        self._img.setStyleSheet("background-color: %s;" % _PREVIEW_BG)
        pix = QPixmap(self._norm_path)
        if pix.isNull():
            self._img.setText("Файл не найден\nили не удалось загрузить")
            self._img.setWordWrap(True)
        else:
            self._img.setPixmap(
                pix.scaled(
                    self._IMG_SIDE,
                    self._IMG_SIDE,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        lay.addWidget(self._img)
        row = QHBoxLayout()
        row.addStretch()
        btn = QPushButton("Закрыть")
        btn.clicked.connect(self.accept)
        row.addWidget(btn)
        row.addStretch()
        lay.addLayout(row)
        self.setFixedSize(self._IMG_SIDE + 24, self._IMG_SIDE + 56)

    def showEvent(self, event):
        super().showEvent(event)
        ref = self.parentWidget() or self
        screen = QDesktopWidget().availableGeometry(ref)
        fg = self.frameGeometry()
        fg.moveCenter(screen.center())
        self.move(fg.topLeft())


class _ClickablePreview(QLabel):
    """Превью с курсором «рука»; клик открывает полноразмерное изображение."""

    def __init__(self, on_click, parent=None):
        super().__init__(parent)
        self._on_click = on_click
        self.setAlignment(Qt.AlignCenter)

    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.LeftButton and self._on_click:
            self._on_click()
        super().mousePressEvent(ev)


class FurnitureTile(QWidget):
    """Сигнал полного пересчёта; furnitureQtyChanged — только смена количества (без БД)."""

    furnitureChanged = pyqtSignal()
    furnitureQtyChanged = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        apply_service_tile_frame(self)

        self._rows: list[dict] = []
        self._id_to_row: dict[int, dict] = {}
        self._display_to_id: dict[str, int] = {}
        self._preview_image_path: str | None = None
        self._unit_rub_cached: int | None = None
        self._order_glass_qty_cached: int = 1
        self._filter_thickness_mm: int = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(3, 2, 3, 2)
        root.setSpacing(1)

        t = QLabel("ФУРНИТУРА")
        style_tile_header(t)
        root.addWidget(t)

        self.chk = QCheckBox("Нужна фурнитура")
        self.chk.setFont(_FONT_SM)
        root.addWidget(self.chk)

        self.combo = QComboBox()
        self.combo.setFont(_FONT_SM)
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.NoInsert)
        self.combo.setMaxVisibleItems(12)
        self.combo.setMaximumHeight(36)
        le = self.combo.lineEdit()
        le.setPlaceholderText("Поиск…")
        le.setFont(_FONT_SM)
        root.addWidget(self.combo)

        self.preview = _ClickablePreview(self._open_full_image_dialog, self)
        self.preview.setFixedHeight(72)
        self.preview.setStyleSheet(
            "background-color: %s; border-radius: 2px;" % _PREVIEW_BG
        )
        self.preview.setScaledContents(False)
        self.preview.setToolTip("Нажмите, чтобы открыть крупнее")
        root.addWidget(self.preview)

        qrow = QHBoxLayout()
        qrow.setSpacing(3)
        ql = QLabel("Кол-во:")
        ql.setFont(_FONT_SM)
        qrow.addWidget(ql)
        self.edit_qty = QLineEdit("1")
        self.edit_qty.setFixedWidth(40)
        self.edit_qty.setValidator(QIntValidator(1, 99999))
        self.edit_qty.setFont(_FONT_SM)
        self.edit_qty.setMaximumHeight(18)
        qrow.addWidget(self.edit_qty)
        qrow.addStretch()
        root.addLayout(qrow)

        self.cost_label = QLabel("—")
        self.cost_label.setWordWrap(True)
        self.cost_label.setFont(_FONT_SM)
        style_cost_label(self.cost_label)
        self.cost_label.setMaximumHeight(48)
        root.addWidget(self.cost_label)

        self._model = QStringListModel()
        self._completer = QCompleter(self._model, self.combo)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setMaxVisibleItems(10)
        try:
            self._completer.setFilterMode(Qt.MatchContains)
        except AttributeError:
            pass
        self.combo.setCompleter(self._completer)
        self._completer.activated[str].connect(self._on_completer_picked)
        try:
            self._completer.highlighted[str].connect(self._show_preview_for_display)
        except Exception:
            pass

        le.textEdited.connect(self._on_line_edited)
        le.textChanged.connect(self._emit_changed)
        self.combo.currentIndexChanged.connect(self._on_combo_index)
        self.chk.toggled.connect(self._on_chk)
        self.edit_qty.textChanged.connect(self._on_qty_text)

        self._reload_catalog()
        self._on_chk(False)

    def _open_full_image_dialog(self):
        path = self._preview_image_path
        if not path or not os.path.isfile(path):
            return
        win = self.window()
        dlg = FurnitureFullImageDialog(path, win if win is not None else self)
        dlg.exec_()

    def _emit_changed(self, *_a):
        self.furnitureChanged.emit()

    def _on_qty_text(self, *_a):
        self.furnitureQtyChanged.emit()

    def _current_selected_furniture_id(self) -> int | None:
        if not self.chk.isChecked():
            return None
        text = (self.combo.currentText() or "").strip()
        fid = self._display_to_id.get(text)
        if fid is None and text:
            idx = self.combo.currentIndex()
            if idx >= 0 and self.combo.itemText(idx).strip() == text:
                d = self.combo.itemData(idx)
                if d is not None:
                    fid = int(d)
        return fid

    def set_filter_thickness_mm(self, mm: int) -> None:
        """Толщина материала: полкодержатели только при 6 или 8 мм и только своей толщины."""
        try:
            mm = int(mm or 0)
        except (TypeError, ValueError):
            mm = 0
        if self._filter_thickness_mm == mm:
            return
        prev_id = self._current_selected_furniture_id()
        self._filter_thickness_mm = mm
        self._reload_catalog()
        if prev_id is not None and prev_id not in self._id_to_row:
            self._clear_furniture_selection()

    def set_pricing_cache(self, unit_rub: int | None, order_glass_qty: int) -> None:
        """Кэш цены за шт. и кол-ва изделий в заказе — для быстрого пересчёта только количества фурнитуры."""
        self._unit_rub_cached = int(unit_rub) if unit_rub is not None else None
        try:
            self._order_glass_qty_cached = max(1, int(order_glass_qty or 1))
        except (TypeError, ValueError):
            self._order_glass_qty_cached = 1

    def get_cached_unit_rub(self) -> int | None:
        return self._unit_rub_cached

    def get_catalog_row_for_id(self, fid: int) -> dict | None:
        r = self._id_to_row.get(int(fid))
        return dict(r) if r else None

    def _clear_furniture_selection(self):
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(-1)
        self.combo.setEditText("")
        self.combo.blockSignals(False)
        self._preview_image_path = None
        self.preview.clear()
        self.preview.setCursor(Qt.ArrowCursor)
        self.furnitureChanged.emit()

    def _reload_catalog(self):
        raw = fetch_furniture_catalog(thickness_mm=self._filter_thickness_mm)
        self._rows = [_normalize_catalog_row(r) for r in raw]
        self._id_to_row = {int(r["id"]): r for r in self._rows}
        displays = []
        self._display_to_id = {}
        self.combo.blockSignals(True)
        self.combo.clear()
        for r in self._rows:
            d = _display_row_label(r)
            displays.append(d)
            rid = int(r["id"])
            self.combo.addItem(d, rid)
            self._display_to_id[d] = rid
        self._model.setStringList(displays)
        self.combo.blockSignals(False)
        if not self._rows:
            self._preview_image_path = None
            self.preview.setPixmap(QPixmap())
            self.preview.setCursor(Qt.ArrowCursor)

    def _on_chk(self, on: bool):
        self.combo.setEnabled(on)
        self.edit_qty.setEnabled(on)
        if not on:
            self.set_pricing_cache(None, 1)
            self._preview_image_path = None
            self.preview.setPixmap(QPixmap())
            self.preview.setCursor(Qt.ArrowCursor)
            self.cost_label.setText("—")
        else:
            self._refresh_preview_from_selection()
        self.furnitureChanged.emit()

    def _on_line_edited(self, text: str):
        self._completer.setCompletionPrefix(text or "")
        self._completer.complete()
        self.furnitureChanged.emit()

    def _on_completer_picked(self, display: str):
        display = (display or "").strip()
        fid = self._display_to_id.get(display)
        if fid is None:
            return
        self.combo.blockSignals(True)
        for i in range(self.combo.count()):
            if self.combo.itemData(i) == fid:
                self.combo.setCurrentIndex(i)
                break
        self.combo.setEditText(display)
        self.combo.blockSignals(False)
        self._apply_preview_for_row(self._id_to_row.get(fid))
        self.furnitureChanged.emit()

    def _on_combo_index(self, _idx: int):
        if not self.chk.isChecked():
            return
        self._refresh_preview_from_selection()
        self.furnitureChanged.emit()

    def _show_preview_for_display(self, display: str):
        display = (display or "").strip()
        fid = self._display_to_id.get(display)
        row = self._id_to_row.get(fid) if fid is not None else None
        self._apply_preview_for_row(row)

    def _refresh_preview_from_selection(self):
        if not self.chk.isChecked():
            return
        text = (self.combo.currentText() or "").strip()
        fid = self._display_to_id.get(text)
        if fid is None and text:
            idx = self.combo.currentIndex()
            if idx >= 0 and self.combo.itemText(idx).strip() == text:
                d = self.combo.itemData(idx)
                fid = int(d) if d is not None else None
        row = self._id_to_row.get(int(fid)) if fid is not None else None
        self._apply_preview_for_row(row)

    def _apply_preview_for_row(self, row: dict | None):
        self._preview_image_path = None
        if not row:
            self.preview.setPixmap(QPixmap())
            self.preview.setCursor(Qt.ArrowCursor)
            return
        path = _resolve_furniture_image(row.get("photo_base") or "")
        if not path:
            self.preview.setPixmap(QPixmap())
            self.preview.setCursor(Qt.ArrowCursor)
            return
        pm = QPixmap(path)
        if pm.isNull():
            self.preview.setPixmap(QPixmap())
            self.preview.setCursor(Qt.ArrowCursor)
            return
        self._preview_image_path = path
        self.preview.setPixmap(
            pm.scaled(184, 66, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.preview.setCursor(Qt.PointingHandCursor)

    def set_cost_lines(self, lines: list[str]):
        if not lines:
            self.cost_label.setText("—")
        else:
            self.cost_label.setText("\n".join(lines))

    def get_payload(self) -> dict:
        if not self.chk.isChecked():
            return {"Включено": False}
        text = (self.combo.currentText() or "").strip()
        fid = self._display_to_id.get(text)
        if fid is None and text:
            idx = self.combo.currentIndex()
            if idx >= 0 and self.combo.itemText(idx).strip() == text:
                d = self.combo.itemData(idx)
                fid = int(d) if d is not None else None
        q = 1
        try:
            q = max(1, int(self.edit_qty.text() or 1))
        except ValueError:
            q = 1
        if fid is None:
            return {
                "Включено": True,
                "id": None,
                "Строка": text,
                "Количество": q,
            }
        row = self._id_to_row.get(int(fid))
        if not row:
            return {"Включено": True, "id": None, "Строка": text, "Количество": q}
        return {
            "Включено": True,
            "id": int(fid),
            "Строка": _display_row_label(row),
            "Название": row.get("name") or "",
            "Цвет": row.get("color") or "",
            "Количество": q,
            "Фото (база)": row.get("photo_base") or "",
            "Изделие полка": bool(row.get("is_shelf_holder")),
        }

    def reset_to_defaults(self):
        self.chk.blockSignals(True)
        self.chk.setChecked(False)
        self.chk.blockSignals(False)
        self.combo.blockSignals(True)
        if self.combo.count():
            self.combo.setCurrentIndex(0)
        self.combo.setEditText("")
        self.combo.blockSignals(False)
        self.edit_qty.blockSignals(True)
        self.edit_qty.setText("1")
        self.edit_qty.blockSignals(False)
        self.set_pricing_cache(None, 1)
        self._filter_thickness_mm = 0
        self._reload_catalog()
        self._preview_image_path = None
        self.preview.setPixmap(QPixmap())
        self.preview.setCursor(Qt.ArrowCursor)
        self.cost_label.setText("—")
        self._on_chk(False)

    def furniture_needed(self) -> bool:
        if not self.chk.isChecked():
            return False
        p = self.get_payload()
        return bool(p.get("id"))
