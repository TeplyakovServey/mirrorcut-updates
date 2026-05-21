# -*- coding: utf-8 -*-
"""Плитка «Замер»: адрес / КАД как у доставки, даты, файлы, сохранение."""
from __future__ import annotations

import os
import urllib.request
from typing import List, Optional

from PyQt5.QtCore import Qt, QDate, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QDialog,
)

from calc import zamer_api_client
from calc.db_postgres import (
    fetch_blocks_zamer_files,
    get_raw_connection,
    insert_blocks_zamer,
    insert_blocks_zamer_file,
)
from calc.upload_client import upload_sketch_file
from elements.calc_tile_style import (
    TILE_SIDE_PX,
    apply_service_tile_frame_fixed_width,
    style_compact_button,
    style_cost_label,
    style_tile_header,
)
from elements.delivery_dialog import DeliveryOutsideDialog


def _resolved_remote_url(url: str) -> str:
    return zamer_api_client.resolve_zamer_file_url(str(url or ""))


def _download_remote_bytes(url: str, timeout: int = 30) -> bytes:
    u = _resolved_remote_url(url)
    if not u:
        return b""
    try:
        return zamer_api_client.portal_fetch_url_bytes(u, timeout=timeout)
    except Exception:
        try:
            with urllib.request.urlopen(u, timeout=timeout) as r:
                return r.read()
        except Exception:
            return b""


class _ThumbDialog(QDialog):
    def __init__(self, src: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Файл замера")
        self.resize(640, 640)
        lay = QVBoxLayout(self)
        img = QLabel()
        img.setAlignment(Qt.AlignCenter)
        pix = QPixmap()
        if src.startswith("http://") or src.startswith("https://"):
            raw = _download_remote_bytes(src, timeout=30)
            if raw:
                pix.loadFromData(raw)
        elif os.path.isfile(src):
            pix.load(src)
        if not pix.isNull():
            pix = pix.scaled(620, 620, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img.setPixmap(pix)
        lay.addWidget(img)
        b = QPushButton("Закрыть")
        b.clicked.connect(self.accept)
        lay.addWidget(b)


def _zamer_service_type_code(measure: bool, install: bool, delivery: bool) -> str:
    """Короткий код услуги для колонки service_type (совместимость с вебом)."""
    if delivery and not measure and not install:
        return "delivery"
    if measure and install and delivery:
        return "mid"
    if measure and delivery and not install:
        return "m_d"
    if install and delivery and not measure:
        return "i_d"
    if measure and install:
        return "both"
    if install:
        return "install"
    if delivery:
        return "delivery"
    return "measure"


def _zamer_primary_file_kind(measure: bool, install: bool, delivery: bool) -> str:
    """Тип файла для blocks_zamer_file при загрузке из плитки."""
    if delivery and not measure and not install:
        return "delivery"
    if install and not measure and not delivery:
        return "install"
    return "measure"


class ZamerTile(QWidget):
    """Ширина 3 плитки; услуги «замер / доставка / монтаж» + выезд внутри/вне КАД."""

    saved = pyqtSignal()
    visitChanged = pyqtSignal()

    MODE_INSIDE = 1
    MODE_OUTSIDE = 2

    def __init__(self, client_strip, parent=None):
        super().__init__(parent)
        self._client_strip = client_strip
        self._zamer_id: Optional[int] = None
        self._local_paths: List[str] = []
        self._remote_urls: List[str] = []
        self._service_measure = False
        self._service_install = False
        self._service_delivery = False
        self._saved_checkbox_hide_measure = False
        self._saved_checkbox_hide_install = False
        self._saved_checkbox_hide_delivery = False
        self._visit_mode = self.MODE_INSIDE
        self._visit_data: dict = {}
        self._price_rub: Optional[int] = None
        self._price_lines: List[str] = []

        # Три колонки сетки по ширине (раньше «замер» + отдельная плитка «доставка»).
        w = int(TILE_SIDE_PX * 3) + 16
        apply_service_tile_frame_fixed_width(self, w)

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(2)

        t = QLabel("ЗАМЕР | ДОСТАВКА | МОНТАЖ")
        style_tile_header(t)
        root.addWidget(t)

        lf = QFont("Arial", 7)

        self._grp_visit = QButtonGroup(self)
        self._rb_in = QRadioButton("В пределах КАД")
        self._rb_out = QRadioButton("Вне КАД")
        for r in (self._rb_in, self._rb_out):
            r.setFont(lf)
            self._grp_visit.addButton(r)
        self._rb_in.setChecked(True)

        self.chk_measure = QCheckBox("замер")
        self.chk_install = QCheckBox("монтаж")
        self.chk_delivery = QCheckBox("доставка")
        self.chk_measure.setFont(lf)
        self.chk_install.setFont(lf)
        self.chk_delivery.setFont(lf)
        self.chk_measure.toggled.connect(self._on_service_toggled)
        self.chk_install.toggled.connect(self._on_service_toggled)
        self.chk_delivery.toggled.connect(self._on_service_toggled)

        self._addr_in = QLineEdit()
        self._addr_in.setPlaceholderText("Адрес (КАД)")
        self._addr_in.setFont(lf)
        self._addr_in.textChanged.connect(self._on_inside_addr)

        self.btn_map = QPushButton("Карта…")
        style_compact_button(self.btn_map)
        self.btn_map.setMaximumHeight(22)
        self.btn_map.clicked.connect(self._open_outside_dialog)

        self.lbl_visit = QLabel("—")
        self.lbl_visit.setWordWrap(True)
        self.lbl_visit.setMaximumHeight(44)
        self.lbl_visit.setFont(lf)
        style_cost_label(self.lbl_visit)

        kad_inner = QWidget()
        kad_inner.setObjectName("ServiceTileBody")
        kad_inner.setAttribute(Qt.WA_StyledBackground, True)
        kad_lay = QVBoxLayout(kad_inner)
        kad_lay.setContentsMargins(0, 0, 0, 0)
        kad_lay.setSpacing(2)
        kad_lay.addWidget(self._addr_in)
        kad_lay.addWidget(self.btn_map)
        kad_lay.addWidget(self.lbl_visit)
        self._kad_widget = kad_inner

        left_col = QVBoxLayout()
        left_col.setSpacing(2)
        left_col.setContentsMargins(0, 0, 2, 0)
        row_service = QHBoxLayout()
        row_service.setSpacing(6)
        row_service.addWidget(self.chk_measure)
        row_service.addWidget(self.chk_install)
        row_service.addWidget(self.chk_delivery)
        row_service.addStretch(1)
        left_col.addLayout(row_service)
        left_col.addWidget(self._rb_in)
        left_col.addWidget(self._rb_out)
        left_col.addWidget(kad_inner)
        left_col.addStretch(1)

        self.edit_addr = QLineEdit()
        self.edit_addr.setPlaceholderText("Адрес объекта (общий)")
        self.edit_addr.setFont(lf)

        self.dt_from = QDateEdit()
        self.dt_to = QDateEdit()
        for d in (self.dt_from, self.dt_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("dd.MM.yy")
            d.setDate(QDate.currentDate())
            d.setFont(lf)
            d.setFixedWidth(68)

        self.phone = QLineEdit()
        self.phone.setInputMask("+7 (000) 000-00-00;_")
        self.phone.setFont(lf)
        self.phone.setMaximumWidth(118)

        self.chk_match = QCheckBox("Как у клиента")
        self.chk_match.setFont(lf)
        self.chk_match.toggled.connect(self._on_match_toggled)

        self.extra = QTextEdit()
        self.extra.setFixedHeight(22)
        self.extra.setFont(lf)

        row_dates = QHBoxLayout()
        row_dates.setSpacing(4)
        _ls = QLabel("С")
        _ls.setFont(lf)
        _lp = QLabel("По")
        _lp.setFont(lf)
        row_dates.addWidget(_ls)
        row_dates.addWidget(self.dt_from)
        row_dates.addWidget(_lp)
        row_dates.addWidget(self.dt_to)
        row_dates.addStretch(1)

        row_phone = QHBoxLayout()
        row_phone.setSpacing(4)
        _lt = QLabel("Тел.")
        _lt.setFont(lf)
        row_phone.addWidget(_lt)
        row_phone.addWidget(self.phone)
        row_phone.addWidget(self.chk_match)

        row_opl = QHBoxLayout()
        row_opl.setSpacing(4)
        _lo = QLabel("Оплата:")
        _lo.setFont(lf)
        row_opl.addWidget(_lo)
        self._oplata = QComboBox()
        self._oplata.setFont(lf)
        self._oplata.addItems(["не указано", "оплачено", "не оплачено"])
        self._oplata.setMaxVisibleItems(4)
        row_opl.addWidget(self._oplata, 1)

        row_files = QHBoxLayout()
        row_files.setSpacing(3)
        self.btn_files = QPushButton("Файлы…")
        self.btn_save = QPushButton("Сохр.")
        self.btn_dl = QPushButton("Скачать")
        self.btn_dl.setToolTip("Скачать все файлы замера")
        for b in (self.btn_files, self.btn_save, self.btn_dl):
            b.setFont(lf)
            b.setMaximumHeight(22)
        self.btn_files.clicked.connect(self._pick_files)
        self.btn_save.clicked.connect(self._save_zamer)
        self.btn_dl.clicked.connect(self._download_all)
        row_files.addWidget(self.btn_files)
        row_files.addWidget(self.btn_save)
        row_files.addWidget(self.btn_dl)

        right_col = QVBoxLayout()
        right_col.setSpacing(2)
        right_col.setContentsMargins(2, 0, 0, 0)
        row_extra = QHBoxLayout()
        row_extra.setSpacing(4)
        _ld = QLabel("Доп.")
        _ld.setFont(lf)
        row_extra.addWidget(_ld)
        row_extra.addWidget(self.extra, 1)

        right_col.addWidget(self.edit_addr)
        right_col.addLayout(row_dates)
        right_col.addLayout(row_phone)
        right_col.addLayout(row_opl)
        right_col.addLayout(row_extra)
        right_col.addLayout(row_files)

        self._thumb_scroll = QScrollArea()
        self._thumb_scroll.setObjectName("ZamerThumbScroll")
        self._thumb_scroll.setAttribute(Qt.WA_StyledBackground, True)
        self._thumb_scroll.setFixedHeight(22)
        self._thumb_scroll.setWidgetResizable(True)
        self._thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._thumb_host = QWidget()
        self._thumb_lay = QHBoxLayout(self._thumb_host)
        self._thumb_lay.setContentsMargins(0, 0, 0, 0)
        self._thumb_scroll.setWidget(self._thumb_host)
        right_col.addWidget(self._thumb_scroll)

        split = QHBoxLayout()
        split.setSpacing(6)
        split.setContentsMargins(0, 0, 0, 0)
        lw = QWidget()
        lw.setObjectName("ZamerSplitLeft")
        lw.setAttribute(Qt.WA_StyledBackground, True)
        lw.setLayout(left_col)
        lw.setMinimumWidth(168)
        rw = QWidget()
        rw.setObjectName("ZamerSplitRight")
        rw.setAttribute(Qt.WA_StyledBackground, True)
        rw.setLayout(right_col)
        self._right_panel = rw
        split.addWidget(lw, 0)
        split.addWidget(rw, 1)
        root.addLayout(split, 1)

        self._visit_data = {
            "Адрес": "",
            "Внутри КАД": True,
            "Расстояние до КАД": None,
            "Расстояние маршрута м": None,
            "lat": None,
            "lon": None,
            "Маршрут координаты": None,
        }
        self._grp_visit.buttonClicked.connect(lambda _b: self._on_visit_radio_changed())
        self.edit_addr.textChanged.connect(self._sync_addr_lines)

        self._poll = QTimer(self)
        self._poll.setInterval(12000)
        self._poll.timeout.connect(self._poll_files)

        self._visit_mode = self.MODE_INSIDE
        self._apply_visit_ui()
        self._refresh_visit_lbl()

        cs = self._client_strip
        if cs is not None and hasattr(cs, "clientIdentityChanged"):
            cs.clientIdentityChanged.connect(self._on_client_strip_identity_changed)

    def has_any_service(self) -> bool:
        return bool(self._service_measure or self._service_install or self._service_delivery)

    def _on_service_toggled(self, _on: bool):
        self._service_measure = bool(self.chk_measure.isChecked())
        self._service_install = bool(self.chk_install.isChecked())
        self._service_delivery = bool(self.chk_delivery.isChecked())
        self._apply_visit_ui()
        self._refresh_visit_lbl()
        self.visitChanged.emit()

    def _on_visit_radio_changed(self):
        if not self.has_any_service():
            self._apply_visit_ui()
            self._refresh_visit_lbl()
            self.visitChanged.emit()
            return
        prev = self._visit_mode
        if self._rb_in.isChecked():
            self._visit_mode = self.MODE_INSIDE
            if prev != self.MODE_INSIDE:
                self._addr_in.blockSignals(True)
                self._addr_in.clear()
                self._addr_in.blockSignals(False)
            self._visit_data = {
                "Адрес": (self._addr_in.text() or "").strip(),
                "Внутри КАД": True,
                "Расстояние до КАД": None,
                "Расстояние маршрута м": None,
                "lat": None,
                "lon": None,
                "Маршрут координаты": None,
            }
            self.edit_addr.blockSignals(True)
            self.edit_addr.setText(self._visit_data["Адрес"])
            self.edit_addr.blockSignals(False)
            self._price_rub = None
            self._price_lines = []
        else:
            self._visit_mode = self.MODE_OUTSIDE
            if not self._visit_data or self._visit_data.get("Внутри КАД"):
                self._visit_data = {
                    "Адрес": "",
                    "Внутри КАД": False,
                    "Расстояние до КАД": None,
                    "Расстояние маршрута м": None,
                    "lat": None,
                    "lon": None,
                    "Маршрут координаты": None,
                }
            self._price_rub = None
            self._price_lines = []
            QTimer.singleShot(0, self._open_outside_dialog)
        self._apply_visit_ui()
        self._refresh_visit_lbl()
        self.visitChanged.emit()

    def _on_inside_addr(self, text: str):
        if self._visit_mode != self.MODE_INSIDE or not self.has_any_service():
            return
        t = (text or "").strip()
        if not isinstance(self._visit_data, dict):
            self._visit_data = {}
        self._visit_data["Адрес"] = t
        self.edit_addr.blockSignals(True)
        self.edit_addr.setText(t)
        self.edit_addr.blockSignals(False)
        self.visitChanged.emit()

    def _sync_addr_lines(self, text: str):
        if not self.has_any_service():
            return
        if self._visit_mode == self.MODE_INSIDE:
            self._addr_in.blockSignals(True)
            self._addr_in.setText((text or "").strip())
            self._addr_in.blockSignals(False)
            self._on_inside_addr(self._addr_in.text())

    def _apply_visit_ui(self):
        active = self.has_any_service()
        self._kad_widget.setVisible(active)
        if hasattr(self, "_right_panel"):
            self._right_panel.setVisible(active)
        if not active:
            return
        inside = self._visit_mode == self.MODE_INSIDE
        self._addr_in.setVisible(inside)
        self.btn_map.setVisible(not inside)
        if inside:
            addr = ""
            if isinstance(self._visit_data, dict):
                addr = (self._visit_data.get("Адрес") or "").strip()
            self._addr_in.blockSignals(True)
            self._addr_in.setText(addr)
            self._addr_in.blockSignals(False)

    def _open_outside_dialog(self):
        if not self.has_any_service():
            return
        if self._visit_mode != self.MODE_OUTSIDE:
            return
        dlg = DeliveryOutsideDialog(
            self._visit_data if self._visit_data else None,
            self,
            window_title="Замер: вне КАД",
        )
        if dlg.exec_() == QDialog.Accepted:
            self._visit_data = dlg.get_result()
            addr = (self._visit_data.get("Адрес") or "").strip()
            self.edit_addr.blockSignals(True)
            self.edit_addr.setText(addr)
            self.edit_addr.blockSignals(False)
            self.visitChanged.emit()

    def _refresh_visit_lbl(self):
        if not self.has_any_service():
            self.lbl_visit.setText("Услуга не выбрана")
            return
        if self._visit_mode == self.MODE_INSIDE:
            parts = ["В пределах КАД"]
            if self._price_lines:
                parts.extend(self._price_lines)
            elif self._price_rub is not None:
                parts.append("Итого выезд: %s ₽" % self._price_rub)
            self.lbl_visit.setText("\n".join(parts))
            return
        parts = []
        addr = (self._visit_data.get("Адрес") or "").strip() if isinstance(self._visit_data, dict) else ""
        if addr:
            parts.append(addr[:56] + ("…" if len(addr) > 58 else ""))
        dkm = self._visit_data.get("Расстояние до КАД") if isinstance(self._visit_data, dict) else None
        if dkm is not None:
            parts.append("До границы КАД (тариф): %s км" % dkm)
        dm = self._visit_data.get("Расстояние маршрута м") if isinstance(self._visit_data, dict) else None
        if dm is not None:
            try:
                parts.append("Длина маршрута: %.2f км" % (float(dm) / 1000.0))
            except (TypeError, ValueError):
                pass
        if self._price_lines:
            parts.extend(self._price_lines)
        elif self._price_rub is not None:
            parts.append("Итого выезд: %s ₽" % self._price_rub)
        elif self._visit_mode == self.MODE_OUTSIDE and dkm is None:
            parts.append("Укажите точку на карте")
        self.lbl_visit.setText("\n".join(parts) if parts else "—")

    def set_visit_price_detail(self, rub: Optional[int], lines: Optional[List[str]] = None):
        self._price_rub = rub
        self._price_lines = list(lines) if lines else []
        self._refresh_visit_lbl()

    def is_configured_for_visit_price(self) -> bool:
        if not self.has_any_service():
            return False
        if self._visit_mode == self.MODE_INSIDE:
            return True
        return self._visit_data.get("Расстояние до КАД") is not None if isinstance(self._visit_data, dict) else False

    def to_selected_block(self) -> dict:
        vd = {
            "Замер": bool(self._service_measure),
            "Монтаж": bool(self._service_install),
            "Доставка": bool(self._service_delivery),
            "Адрес": (self.edit_addr.text() or "").strip(),
            "Данные выезда": dict(self._visit_data) if isinstance(self._visit_data, dict) else {},
            "date_from": self.dt_from.date().toString("yyyy-MM-dd"),
            "date_to": self.dt_to.date().toString("yyyy-MM-dd"),
            "phone": (self.phone.text() or "").replace("_", "").strip(),
            "extra_text": self.extra.toPlainText().strip(),
            "matches_client": self.chk_match.isChecked(),
            "Оплата": self._oplata.currentText(),
        }
        if self._zamer_id is not None:
            vd["portal_zamer_id"] = int(self._zamer_id)
        return {"Активирован": True, "Данные": vd}

    def apply_saved_block(self, blk) -> None:
        if not isinstance(blk, dict) or not blk.get("Активирован"):
            return
        zd = blk.get("Данные")
        if not isinstance(zd, dict):
            return
        op = (zd.get("Оплата") or "").strip()
        if op in ("оплачено", "не оплачено", "не указано"):
            self._oplata.blockSignals(True)
            self._oplata.setCurrentText(op)
            self._oplata.blockSignals(False)
        zid = zd.get("portal_zamer_id")
        if zid is not None:
            try:
                self._zamer_id = int(zid)
            except (TypeError, ValueError):
                pass
        addr = (zd.get("Адрес") or "").strip()
        self.edit_addr.blockSignals(True)
        self.edit_addr.setText(addr)
        self.edit_addr.blockSignals(False)
        df = (zd.get("date_from") or "").strip()
        dt = (zd.get("date_to") or "").strip()
        if df:
            d0 = QDate.fromString(df[:10], "yyyy-MM-dd")
            if d0.isValid():
                self.dt_from.setDate(d0)
        if dt:
            d1 = QDate.fromString(dt[:10], "yyyy-MM-dd")
            if d1.isValid():
                self.dt_to.setDate(d1)
        ph = (zd.get("phone") or "").strip()
        if ph:
            self.phone.setText(ph)
        ex = (zd.get("extra_text") or "").strip()
        if ex:
            self.extra.setPlainText(ex)
        self.chk_match.blockSignals(True)
        self.chk_match.setChecked(bool(zd.get("matches_client")))
        self.chk_match.blockSignals(False)
        vd = zd.get("Данные выезда")
        if isinstance(vd, dict) and vd:
            self._visit_data = dict(vd)
        zm, im, dl = zd.get("Замер"), zd.get("Монтаж"), zd.get("Доставка")
        self._saved_checkbox_hide_measure = zm is True
        self._saved_checkbox_hide_install = im is True
        self._saved_checkbox_hide_delivery = dl is True
        self._service_measure = bool(zm)
        self._service_install = bool(im)
        self._service_delivery = bool(dl)
        # Старые сохранения без ключей услуг — по умолчанию включён замер; чекбокс не скрываем (ещё не «уже в заказе»).
        keys_absent = ("Замер" not in zd) and ("Монтаж" not in zd) and ("Доставка" not in zd)
        if keys_absent and not bool(zd.get("Без замера")):
            self._service_measure = True
            self._saved_checkbox_hide_measure = False
        self.chk_measure.blockSignals(True)
        self.chk_install.blockSignals(True)
        self.chk_delivery.blockSignals(True)
        self.chk_measure.setChecked(self._service_measure)
        self.chk_install.setChecked(self._service_install)
        self.chk_delivery.setChecked(self._service_delivery)
        self.chk_measure.blockSignals(False)
        self.chk_install.blockSignals(False)
        self.chk_delivery.blockSignals(False)
        if (zd.get("Данные выезда") or {}).get("Внутри КАД", True):
            self._grp_visit.blockSignals(True)
            self._rb_in.setChecked(True)
            self._grp_visit.blockSignals(False)
            self._visit_mode = self.MODE_INSIDE
        else:
            self._grp_visit.blockSignals(True)
            self._rb_out.setChecked(True)
            self._grp_visit.blockSignals(False)
            self._visit_mode = self.MODE_OUTSIDE
        self._apply_visit_ui()
        self._refresh_visit_lbl()
        self.sync_matches_client_from_db()
        if self._zamer_id:
            self._poll.start()
            self._refresh_remote_list()

    def hide_service_checkboxes_already_enabled(self, separate_delivery_activated: bool = False) -> None:
        """Услуга уже явно включена в сохранённом блоке — скрываем чекбокс (добавляют только недостающее)."""
        if self._saved_checkbox_hide_measure:
            self.chk_measure.setVisible(False)
        if self._saved_checkbox_hide_install:
            self.chk_install.setVisible(False)
        if self._saved_checkbox_hide_delivery or separate_delivery_activated:
            self.chk_delivery.setVisible(False)

    def apply_measure_checkbox_order_policy(self, order_status: str) -> None:
        """После статуса «оплачен» замер через это окно не добавляют — только доставка/монтаж."""
        st = str(order_status or "").strip().lower()
        if st not in ("draft", "paid"):
            self.chk_measure.setVisible(False)

    def apply_cta_slot_visibility(self, want_measure: bool, want_install: bool, want_delivery: bool) -> None:
        """Какие услуги предлагает CTA: показать слот, если услугу ещё можно добавить (не скрыто как «уже в блоке»)."""
        self.chk_measure.setVisible(bool(want_measure) and not self._saved_checkbox_hide_measure)
        self.chk_install.setVisible(bool(want_install) and not self._saved_checkbox_hide_install)
        self.chk_delivery.setVisible(bool(want_delivery) and not self._saved_checkbox_hide_delivery)

    def merge_legacy_saved_delivery(self, dblk) -> None:
        """Старые просчёты с отдельной плиткой «Доставка»: подмешать данные, не затирая уже загруженный замер."""
        if not isinstance(dblk, dict) or not dblk.get("Активирован"):
            return
        bd = dblk.get("Данные")
        if not isinstance(bd, dict):
            return
        self.chk_delivery.blockSignals(True)
        self.chk_delivery.setChecked(True)
        self.chk_delivery.blockSignals(False)
        self._service_delivery = True
        op = (bd.get("Оплата") or "").strip()
        if op in ("оплачено", "не оплачено", "не указано"):
            self._oplata.blockSignals(True)
            self._oplata.setCurrentText(op)
            self._oplata.blockSignals(False)
        vd = self._visit_data if isinstance(self._visit_data, dict) else {}
        addr0 = str(vd.get("Адрес") or "").strip()
        has_km = vd.get("Расстояние до КАД") is not None
        empty_visit = (not addr0) and (not has_km) and (vd.get("Расстояние маршрута м") is None)
        if not empty_visit:
            self._refresh_visit_lbl()
            self.visitChanged.emit()
            return
        inside = bd.get("Внутри КАД")
        if inside is True or (
            inside is None
            and not bd.get("Расстояние до КАД")
            and not bd.get("Расстояние маршрута м")
        ):
            self._grp_visit.blockSignals(True)
            self._rb_in.setChecked(True)
            self._grp_visit.blockSignals(False)
            self._visit_mode = self.MODE_INSIDE
            self._visit_data = {
                "Адрес": (bd.get("Адрес") or "").strip(),
                "Внутри КАД": True,
                "Расстояние до КАД": None,
                "Расстояние маршрута м": None,
                "lat": bd.get("lat"),
                "lon": bd.get("lon"),
                "Маршрут координаты": bd.get("Маршрут координаты"),
            }
            self._addr_in.blockSignals(True)
            self._addr_in.setText(self._visit_data.get("Адрес") or "")
            self._addr_in.blockSignals(False)
        else:
            self._grp_visit.blockSignals(True)
            self._rb_out.setChecked(True)
            self._grp_visit.blockSignals(False)
            self._visit_mode = self.MODE_OUTSIDE
            self._visit_data = {
                k: bd.get(k)
                for k in (
                    "Адрес",
                    "Внутри КАД",
                    "Расстояние до КАД",
                    "Расстояние маршрута м",
                    "lat",
                    "lon",
                    "Маршрут координаты",
                )
            }
            self._visit_data["Внутри КАД"] = False
        self._apply_visit_ui()
        self._refresh_visit_lbl()
        self.visitChanged.emit()

    def _current_save_address(self) -> str:
        a = (self.edit_addr.text() or "").strip()
        return a

    @staticmethod
    def _primary_address_from_client_row(row: dict) -> str:
        if not isinstance(row, dict):
            return ""
        v = row.get("actual_address") or row.get("legal_address") or row.get("address") or ""
        return str(v).strip()

    @staticmethod
    def _format_phone_input_from_raw(ph: str) -> str:
        ph = (ph or "").strip()
        if not ph:
            return ""
        digits = "".join(ch for ch in ph if ch.isdigit())
        if len(digits) >= 10:
            d = digits[-10:]
            return "+7 (%s) %s-%s-%s" % (d[:3], d[3:6], d[6:8], d[8:10])
        return ph

    def _fill_contact_from_client_row(self, row: dict) -> None:
        """Заполнить адрес и телефон из одной строки клиента (уже загруженной из БД)."""
        addr = self._primary_address_from_client_row(row)
        if addr:
            self.edit_addr.setText(addr)
            if self.has_any_service() and self._visit_mode == self.MODE_INSIDE:
                self._addr_in.setText(addr)
        ph_raw = (row.get("phone") or row.get("tel") or "").strip()
        if ph_raw:
            self.phone.setText(self._format_phone_input_from_raw(ph_raw))

    def sync_matches_client_from_db(self) -> None:
        """Один запрос: если «как у клиента» и у клиента в БД есть адрес — подтянуть адрес и телефон."""
        if not self.chk_match.isChecked():
            return
        pl = self._client_strip.get_payload()
        cid = pl.get("id")
        if not cid:
            return
        try:
            from db import models

            row = models.get_client_by_id(cid)
        except Exception:
            return
        if not row:
            return
        if not self._primary_address_from_client_row(row):
            return
        self._fill_contact_from_client_row(row)

    def _on_client_strip_identity_changed(self) -> None:
        self.sync_matches_client_from_db()

    def _on_match_toggled(self, on: bool):
        if not on:
            return
        pl = self._client_strip.get_payload()
        cid = pl.get("id")
        if not cid:
            QMessageBox.warning(
                self,
                "Замер",
                "Не выбран клиент. Укажите клиента в строке сверху, затем снова включите «Как у клиента».",
            )
            self.chk_match.blockSignals(True)
            self.chk_match.setChecked(False)
            self.chk_match.blockSignals(False)
            return
        try:
            from db import models

            row = models.get_client_by_id(cid)
            if not row:
                return
            addr = self._primary_address_from_client_row(row)
            if addr:
                self._fill_contact_from_client_row(row)
            else:
                ph_raw = (row.get("phone") or row.get("tel") or "").strip()
                if ph_raw:
                    self.phone.setText(self._format_phone_input_from_raw(ph_raw))
        except Exception:
            pass

    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Изображения",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif)",
        )
        if paths:
            self._local_paths.extend(paths)
            self._rebuild_thumbs()

    def _rebuild_thumbs(self):
        """Локальные превью сразу; удалённые — по одному через очередь таймеров (окно не замирает на N× сеть)."""
        self._thumb_load_gen = getattr(self, "_thumb_load_gen", 0) + 1
        gen = self._thumb_load_gen
        while self._thumb_lay.count():
            it = self._thumb_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        for p in self._local_paths:
            pix = QPixmap(p)
            b = QPushButton()
            b.setFixedSize(28, 28)
            if not pix.isNull():
                b.setIcon(QIcon(pix.scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            b.setStyleSheet("border:1px solid #888;")
            b.clicked.connect(lambda _=False, path=p: self._open_thumb(path))
            self._thumb_lay.addWidget(b)
        urls = list(self._remote_urls)
        if not urls:
            self._thumb_lay.addStretch(1)
            return

        def _pump(i: int):
            if gen != self._thumb_load_gen:
                return
            if i >= len(urls):
                self._thumb_lay.addStretch(1)
                return
            u = urls[i]
            b = QPushButton()
            b.setFixedSize(28, 28)
            b.setStyleSheet("border:1px solid #4a90d9;")
            b.setToolTip("загрузка…")
            b.clicked.connect(lambda _=False, url=u: self._open_thumb(url))
            self._thumb_lay.addWidget(b)

            def _load():
                if gen != self._thumb_load_gen:
                    return
                pix = QPixmap()
                raw = _download_remote_bytes(u, timeout=15)
                if raw:
                    pix.loadFromData(raw)
                if not pix.isNull():
                    b.setIcon(QIcon(pix.scaled(24, 24, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
                    b.setToolTip("")
                QTimer.singleShot(0, lambda: _pump(i + 1))

            QTimer.singleShot(0, _load)

        QTimer.singleShot(0, lambda: _pump(0))

    def _open_thumb(self, src: str):
        _ThumbDialog(src, self).exec_()

    def _save_zamer(self):
        addr = self._current_save_address()
        if not addr:
            QMessageBox.warning(self, "Замер | Монтаж", "Укажите адрес.")
            return
        if not self.has_any_service():
            QMessageBox.warning(
                self, "Замер | Монтаж", "Отметьте услугу: замер, монтаж и/или доставка."
            )
            return
        pl = self._client_strip.get_payload()
        if self.chk_match.isChecked() and not pl.get("id"):
            QMessageBox.warning(
                self,
                "Замер | Монтаж",
                "Отмечено «Как у клиента», но клиент не выбран. Выберите клиента в строке сверху.",
            )
            return
        cid = pl.get("id")
        cid_i = int(cid) if cid else None
        df = self.dt_from.date().toString("yyyy-MM-dd")
        dt = self.dt_to.date().toString("yyyy-MM-dd")
        phone = (self.phone.text() or "").replace("_", "").strip()
        extra = self.extra.toPlainText().strip()
        st_code = _zamer_service_type_code(
            bool(self._service_measure),
            bool(self._service_install),
            bool(self._service_delivery),
        )
        file_kind = _zamer_primary_file_kind(
            bool(self._service_measure),
            bool(self._service_install),
            bool(self._service_delivery),
        )
        payload = {
            "client_id": cid_i,
            "address": addr,
            "date_from": df,
            "date_to": dt,
            "phone": phone,
            "matches_client": self.chk_match.isChecked(),
            "extra_text": extra,
            "is_measure": bool(self._service_measure),
            "is_install": bool(self._service_install),
            "is_delivery": bool(self._service_delivery),
            "service_type": st_code,
        }
        zid = None
        used_pg = False
        conn = get_raw_connection()
        if conn:
            try:
                zid = insert_blocks_zamer(
                    conn,
                    client_id=cid_i,
                    address=addr,
                    date_from=df,
                    date_to=dt,
                    phone=phone,
                    matches_client=self.chk_match.isChecked(),
                    extra_text=extra,
                    is_measure=bool(self._service_measure),
                    is_install=bool(self._service_install),
                    is_delivery=bool(self._service_delivery),
                    service_type=st_code,
                )
                if zid is None:
                    conn.rollback()
                else:
                    for path in self._local_paths:
                        up = {}
                        if zamer_api_client.api_enabled():
                            up = zamer_api_client.zamer_upload_file(
                                int(zid), path, file_kind=file_kind, mark_complete=False
                            )
                            url = (up or {}).get("url") or (up or {}).get("file_url") or ""
                        else:
                            up = upload_sketch_file(path)
                            url = (up or {}).get("url") or ""
                        if url:
                            insert_blocks_zamer_file(
                                conn, zid, url, "", "desktop", file_kind=file_kind
                            )
                    conn.commit()
                    used_pg = True
            except Exception as e:
                conn.rollback()
                QMessageBox.critical(self, "Замер | Монтаж", str(e))
                zid = None
            finally:
                conn.close()

        if zid is None and zamer_api_client.api_enabled():
            r = zamer_api_client.zamer_create(payload)
            if r and r.get("id") is not None:
                try:
                    zid = int(r["id"])
                except (TypeError, ValueError):
                    zid = None

        if zid is None:
            QMessageBox.warning(
                self,
                "Замер | Монтаж",
                "Не удалось сохранить заявку (нет связи с PostgreSQL или API). "
                "Проверьте MC_PG_* / config и секцию [zamer_api] в app.cfg (или MC_ZAMER_*).",
            )
            return

        if not used_pg and self._local_paths and zamer_api_client.api_enabled():
            for path in self._local_paths:
                up = zamer_api_client.zamer_upload_file(
                    zid, path, file_kind=file_kind, mark_complete=False
                )
                url = (up or {}).get("url") or ""
                if not url and not (up or {}).get("ok"):
                    QMessageBox.warning(
                        self,
                        "Замер | Монтаж",
                        "Файл: %s" % ((up or {}).get("error") or "ошибка загрузки"),
                    )
        self._zamer_id = zid
        self._local_paths.clear()
        self._poll.start()
        self.saved.emit()
        QMessageBox.information(self, "Замер | Монтаж", "Сохранено, № %s" % zid)
        self._refresh_remote_list()

    def _refresh_remote_list(self):
        if not self._zamer_id:
            self._remote_urls.clear()
            self._rebuild_thumbs()
            return
        urls: List[str] = []
        if zamer_api_client.api_enabled():
            for row in zamer_api_client.zamer_list_local_files_from_api(self._zamer_id):
                u = row.get("file_url") or row.get("url") or ""
                if u:
                    urls.append(_resolved_remote_url(u))
        else:
            conn = get_raw_connection()
            if conn:
                try:
                    for row in fetch_blocks_zamer_files(conn, self._zamer_id):
                        u = (row.get("file_url") or "").strip()
                        if u:
                            urls.append(_resolved_remote_url(u))
                finally:
                    conn.close()
        self._remote_urls = urls
        self._rebuild_thumbs()

    def _poll_files(self):
        if self._zamer_id:
            self._refresh_remote_list()

    def _download_all(self):
        if not self._remote_urls:
            QMessageBox.information(self, "Замер | Монтаж", "Нет файлов для скачивания.")
            return
        d = QFileDialog.getExistingDirectory(self, "Папка для файлов")
        if not d:
            return
        for i, u in enumerate(self._remote_urls):
            data = _download_remote_bytes(u, timeout=60)
            if not data:
                QMessageBox.warning(self, "Замер | Монтаж", "Ошибка загрузки: %s" % (u or "файл"))
                return
            name = "zamer_%s_%s" % (i, os.path.basename(u.split("?")[0]) or "file.bin")
            path = os.path.join(d, name)
            with open(path, "wb") as f:
                f.write(data)
        QMessageBox.information(self, "Замер | Монтаж", "Файлы сохранены.")

    def reset_to_defaults(self) -> None:
        self._poll.stop()
        self._zamer_id = None
        self._local_paths.clear()
        self._remote_urls.clear()
        self.edit_addr.clear()
        self._addr_in.clear()
        self.phone.clear()
        self.extra.clear()
        self._oplata.blockSignals(True)
        self._oplata.setCurrentIndex(0)
        self._oplata.blockSignals(False)
        self.chk_match.setChecked(False)
        self.chk_measure.blockSignals(True)
        self.chk_install.blockSignals(True)
        self.chk_delivery.blockSignals(True)
        self.chk_measure.setChecked(False)
        self.chk_install.setChecked(False)
        self.chk_delivery.setChecked(False)
        self.chk_measure.blockSignals(False)
        self.chk_install.blockSignals(False)
        self.chk_delivery.blockSignals(False)
        self._service_measure = False
        self._service_install = False
        self._service_delivery = False
        self._saved_checkbox_hide_measure = False
        self._saved_checkbox_hide_install = False
        self._saved_checkbox_hide_delivery = False
        self._grp_visit.blockSignals(True)
        self._rb_in.setChecked(True)
        self._grp_visit.blockSignals(False)
        self._visit_mode = self.MODE_INSIDE
        self._visit_data = {}
        self._price_rub = None
        self._price_lines = []
        self._apply_visit_ui()
        self._refresh_visit_lbl()
        self._rebuild_thumbs()

    def has_saved_zamer(self) -> bool:
        return self._zamer_id is not None

    def highlight_zamer_used(self) -> bool:
        if self.has_saved_zamer():
            return True
        if self.has_any_service() and self.is_configured_for_visit_price():
            return True
        return False
