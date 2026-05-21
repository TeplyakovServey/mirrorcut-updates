# -*- coding: utf-8 -*-
"""Блок отчёта портала замера/доставки/монтажа в сводке заказа (файлы, комментарии, исполнитель)."""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
import time
import urllib.parse
import urllib.request
from datetime import datetime

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtCore import Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QPixmap, QIcon, QKeySequence
from PyQt5.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

try:
    from calc import zamer_api_client
except Exception:
    zamer_api_client = None

from ui.order_tile import portal_zamer_id_from_order
from ui.portal_time import format_iso_datetime_msk_long


class _ZamerDetailFetchThread(QThread):
    """GET /api/zamery/<id>/ в фоне — иначе до 25 с блокируют главный поток Qt."""

    # В PyQt5 pyqtSignal(dict) между потоками часто не доставляется; object — надёжно.
    finished_ok = pyqtSignal(object)
    finished_err = pyqtSignal(str)

    def __init__(self, zamer_id: int, parent=None):
        super().__init__(parent)
        self._zid = int(zamer_id)

    def run(self):
        try:
            from calc import zamer_api_client as zc
        except Exception as ex:
            self.finished_err.emit("Импорт клиента портала: %s" % ex)
            return
        try:
            row, err = zc.zamer_get_with_error(self._zid)
        except Exception as ex:
            self.finished_err.emit("Исключение при запросе к порталу: %s" % ex)
            return
        if isinstance(row, dict) and row.get("id") is not None:
            try:
                payload = json.dumps(row, default=str, ensure_ascii=False)
                self.finished_ok.emit(json.loads(payload))
            except Exception as ex:
                self.finished_err.emit("Не удалось передать ответ портала в интерфейс: %s" % ex)
            return
        self.finished_err.emit(err or "Нет данных заявки (пустой ответ или нет id).")


_KIND_RU = {"measure": "Замер", "delivery": "Доставка", "install": "Монтаж"}
_PAYMENT_METHOD_LABELS_RU = {
    "cash": "Наличные",
    "cashless": "Безналичные",
    "card_transfer": "Перевод на карту",
}
_IMG_RE = re.compile(r"\.(jpe?g|png|gif|webp)$", re.I)
_MONTHS_RU_GEN = [
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]


def _status_ru(code: str) -> str:
    c = (code or "").strip().lower()
    return {
        "new": "Новый",
        "agreed": "Согласовано",
        "in_progress": "В работе",
        "completed": "Завершён",
    }.get(c, code or "—")


def _service_flags_from_row(row: dict) -> tuple[bool, bool, bool]:
    """Возвращает флаги (measure, install, delivery) с fallback на service_type."""
    if not isinstance(row, dict):
        return (False, False, False)
    m = bool(row.get("is_measure"))
    i = bool(row.get("is_install"))
    d = bool(row.get("is_delivery"))
    if m or i or d:
        return (m, i, d)
    st = str(row.get("service_type") or "").strip().lower()
    if st == "delivery":
        return (False, False, True)
    if st == "install":
        return (False, True, False)
    if st == "both":
        return (True, True, False)
    if st == "m_d":
        return (True, False, True)
    if st == "i_d":
        return (False, True, True)
    if st == "mid":
        return (True, True, True)
    return (True, False, False)


def _service_title_ru(row: dict) -> str:
    m, i, d = _service_flags_from_row(row)
    names = []
    if m:
        names.append("Замер")
    if d:
        names.append("Доставка")
    if i:
        names.append("Монтаж")
    if not names:
        names = ["Услуга"]
    return " + ".join(names)


def _safe_url_text(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _is_image_url(url: str) -> bool:
    return bool(_IMG_RE.search((url or "").split("?", 1)[0]))


# True — не тянуть превью по HTTP (только текст/кнопки). False — как в проде, с миниатюрами.
PORTAL_SKIP_PREVIEW_IMAGE_DOWNLOADS = False


def _preview_image_bytes(url: str, timeout: int = 20) -> bytes:
    if PORTAL_SKIP_PREVIEW_IMAGE_DOWNLOADS:
        return b""
    return _download_bytes(url, timeout=timeout)


def _download_bytes(url: str, timeout: int = 20) -> bytes:
    if zamer_api_client and hasattr(zamer_api_client, "portal_fetch_url_bytes"):
        return zamer_api_client.portal_fetch_url_bytes(url, timeout=timeout)
    req = urllib.request.Request(url, method="GET")
    ctx = __import__("ssl").create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def _save_files_to_dir(files: list, parent=None) -> int:
    target = QFileDialog.getExistingDirectory(parent, "Выберите папку для сохранения файлов")
    if not target:
        return 0
    saved = 0
    for i, f in enumerate(files):
        if not isinstance(f, dict):
            continue
        raw_url = _safe_url_text(f.get("resolved_url"))
        if not raw_url:
            continue
        try:
            body = _download_bytes(raw_url)
            p = urllib.parse.urlparse(raw_url)
            base_name = os.path.basename((p.path or "").rstrip("/")) or ("file_%02d.bin" % (i + 1))
            stem, ext = os.path.splitext(base_name)
            if not ext:
                ext = ".bin"
            out_name = base_name
            out_path = os.path.join(target, out_name)
            n = 1
            while os.path.exists(out_path):
                out_name = "%s_%s%s" % (stem, n, ext)
                out_path = os.path.join(target, out_name)
                n += 1
            with open(out_path, "wb") as h:
                h.write(body)
            saved += 1
        except Exception:
            continue
    return saved


def _save_single_file(file_row: dict, parent=None) -> bool:
    if not isinstance(file_row, dict):
        return False
    raw_url = _safe_url_text(file_row.get("resolved_url"))
    if not raw_url:
        return False
    try:
        body = _download_bytes(raw_url)
        p = urllib.parse.urlparse(raw_url)
        base_name = os.path.basename((p.path or "").rstrip("/")) or "file.bin"
        target, _ = QFileDialog.getSaveFileName(parent, "Сохранить файл", base_name)
        if not target:
            return False
        with open(target, "wb") as h:
            h.write(body)
        return True
    except Exception:
        return False


class _PortalImageGalleryDialog(QDialog):
    def __init__(self, image_files: list, start_index: int = 0, parent=None):
        super().__init__(parent)
        self._files = [f for f in (image_files or []) if isinstance(f, dict)]
        self._idx = max(0, min(int(start_index), len(self._files) - 1)) if self._files else 0
        self.setWindowTitle("Просмотр файлов замера / доставки / монтажа")
        self.resize(980, 720)
        lay = QVBoxLayout(self)
        self._title = QLabel("")
        self._title.setStyleSheet("font-weight:600; color:#1565c0;")
        lay.addWidget(self._title)
        self._img = QLabel("Нет изображения")
        self._img.setStyleSheet("background:#fafafa; border:1px solid #cfd8dc;")
        self._img.setMinimumHeight(560)
        self._img.setAlignment(Qt.AlignCenter)
        lay.addWidget(self._img, 1)
        row = QHBoxLayout()
        self._btn_prev = QPushButton("← Назад")
        self._btn_next = QPushButton("Вперёд →")
        self._btn_open = QPushButton("Открыть в браузере")
        self._btn_save = QPushButton("Скачать все файлы")
        self._btn_close = QPushButton("Закрыть")
        self._btn_prev.clicked.connect(self._prev)
        self._btn_next.clicked.connect(self._next)
        self._btn_open.clicked.connect(self._open_current)
        self._btn_save.clicked.connect(self._save_all)
        self._btn_close.clicked.connect(self.accept)
        for b in (self._btn_prev, self._btn_next, self._btn_open, self._btn_save, self._btn_close):
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self._render()

    def _current(self):
        if not self._files:
            return None
        return self._files[self._idx]

    def _render(self):
        cur = self._current()
        if not cur:
            self._title.setText("Нет файлов")
            self._img.setText("Нет изображения")
            return
        url = _safe_url_text(cur.get("resolved_url"))
        cmt = _safe_url_text(cur.get("comment"))
        who = _safe_url_text(cur.get("uploaded_by"))
        self._title.setText(
            "Изображение %s/%s%s%s"
            % (
                self._idx + 1,
                len(self._files),
                (" · " + who) if who else "",
                (" · " + cmt) if cmt else "",
            )
        )
        try:
            raw = _preview_image_bytes(url, timeout=20)
            pm = QPixmap()
            ok = pm.loadFromData(raw) if raw else False
            if not ok or pm.isNull():
                self._img.setText(
                    "Превью отключено (диагностика). Откройте в браузере."
                    if PORTAL_SKIP_PREVIEW_IMAGE_DOWNLOADS
                    else "Не удалось загрузить изображение"
                )
                return
            self._img.setPixmap(pm.scaled(self._img.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            self._img.setText(
                "Превью отключено (диагностика). Откройте в браузере."
                if PORTAL_SKIP_PREVIEW_IMAGE_DOWNLOADS
                else "Не удалось загрузить изображение"
            )

    def resizeEvent(self, ev):  # noqa: N802
        super().resizeEvent(ev)
        self._render()

    def _prev(self):
        if not self._files:
            return
        self._idx = (self._idx - 1) % len(self._files)
        self._render()

    def _next(self):
        if not self._files:
            return
        self._idx = (self._idx + 1) % len(self._files)
        self._render()

    def _open_current(self):
        cur = self._current()
        if not cur:
            return
        u = _safe_url_text(cur.get("resolved_url"))
        if u:
            QDesktopServices.openUrl(QUrl(u))

    def _save_all(self):
        n = _save_files_to_dir(self._files, self)
        QMessageBox.information(self, "Скачивание", "Сохранено файлов: %s" % n)


class _PortalFullsizeImageDialog(QDialog):
    """Просмотр одного файла: масштаб так, чтобы длинная сторона ≤ 1000 px (исходник при сохранении не меняется)."""

    def __init__(self, file_row: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Просмотр изображения")
        url = _safe_url_text((file_row or {}).get("resolved_url"))
        lay = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignCenter)
        inner = QLabel()
        inner.setAlignment(Qt.AlignCenter)
        inner.setStyleSheet("QLabel { background: #1a1a1a; color: #eee; }")
        pm = QPixmap()
        if not url:
            inner.setText("Нет URL файла")
        else:
            try:
                raw = _download_bytes(url, timeout=90)
                if raw and pm.loadFromData(raw) and not pm.isNull():
                    w0, h0 = pm.width(), pm.height()
                    m = max(w0, h0)
                    if m > 1000 and m > 0:
                        pm_show = pm.scaled(
                            int(round(w0 * 1000 / m)),
                            int(round(h0 * 1000 / m)),
                            Qt.KeepAspectRatio,
                            Qt.SmoothTransformation,
                        )
                    else:
                        pm_show = pm
                    inner.setPixmap(pm_show)
                    inner.resize(pm_show.size())
                else:
                    inner.setText("Не удалось загрузить изображение")
            except Exception as ex:
                inner.setText(str(ex))
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        btn = QPushButton("Закрыть")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)
        if not pm.isNull():
            w0, h0 = pm.width(), pm.height()
            m = max(w0, h0)
            if m > 1000 and m > 0:
                dw = int(round(w0 * 1000 / m))
                dh = int(round(h0 * 1000 / m))
            else:
                dw, dh = w0, h0
            self.resize(min(1280, dw + 80), min(960, dh + 100))
        else:
            self.resize(520, 420)


def _portal_active_service_labels(row: dict) -> list[tuple[str, str]]:
    """(код услуги, подпись) — только активные, порядок: замер, доставка, монтаж."""
    m, i, d = _service_flags_from_row(row)
    out: list[tuple[str, str]] = []
    if m:
        out.append(("measure", "ЗАМЕР"))
    if d:
        out.append(("delivery", "ДОСТАВКА"))
    if i:
        out.append(("install", "МОНТАЖ"))
    return out


def _can_remove_service_kind(row: dict, service_code: str, status_code: str) -> bool:
    """
    Пока у заявки один общий status: при «completed» считаем услугу «закрытой»,
    если по этому виду уже есть файлы на портале; иначе снятие флага допустимо.
    """
    st = (status_code or "").strip().lower()
    if st != "completed":
        return True
    fbk = row.get("files_by_kind")
    if not isinstance(fbk, dict):
        return True
    lst = fbk.get(service_code) or []
    return not (isinstance(lst, list) and len(lst) > 0)


class _PortalHoldDeleteStrip(QWidget):
    """Удержание ~1 с: красная заливка (индикатор), затем удаление."""

    def __init__(self, caption: str, on_commit, parent=None):
        super().__init__(parent)
        self._on_commit = on_commit
        self._hold_seconds = 1.0
        self._holding = False
        self._deleting = False
        self._start_t = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._on_tick)
        self.setMouseTracking(True)
        self.setAutoFillBackground(True)
        self._hovering = False
        self._hover_alpha = 55
        self._hold_alpha = 210
        self._set_fill(0)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        self._lbl = QLabel(caption)
        self._lbl.setWordWrap(True)
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setStyleSheet("color:#eceff1; font-weight:600;")
        lay.addWidget(self._lbl)
        self.setMinimumHeight(42)
        self.setCursor(Qt.PointingHandCursor)

    def _set_fill(self, alpha: int) -> None:
        alpha = max(0, min(255, int(alpha)))
        if alpha <= 0:
            self.setStyleSheet(
                "QWidget { background-color: rgba(55,55,60,0.35); border: 1px solid rgba(0,0,0,0.25); "
                "border-radius:8px; }"
            )
        else:
            self.setStyleSheet(
                "QWidget { background-color: rgba(244,67,54,%d); border: 1px solid rgba(200,40,30,%d); "
                "border-radius:8px; }"
                % (alpha, min(255, alpha + 40))
            )

    def enterEvent(self, event):
        if not self._deleting:
            self._hovering = True
            self._set_fill(self._hover_alpha)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovering = False
        if not self._holding and not self._deleting:
            self._set_fill(0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if self._deleting:
            return
        if event.button() == Qt.LeftButton:
            self._holding = True
            self._deleting = False
            self._start_t = time.monotonic()
            self._set_fill(self._hover_alpha)
            self._timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._timer.isActive():
            self._timer.stop()
        self._holding = False
        if not self._deleting:
            self._set_fill(self._hover_alpha if self._hovering else 0)
        super().mouseReleaseEvent(event)

    def _on_tick(self):
        if not self._holding or self._deleting:
            return
        elapsed = time.monotonic() - self._start_t
        ratio = min(1.0, elapsed / max(0.001, self._hold_seconds))
        alpha = int(self._hover_alpha + (self._hold_alpha - self._hover_alpha) * ratio)
        self._set_fill(alpha)
        if ratio >= 1.0:
            self._timer.stop()
            self._deleting = True
            self._holding = False
            self._set_fill(self._hold_alpha)
            QTimer.singleShot(0, self._on_commit)


def portal_fulfillment_block_widget(order_data: dict, parent=None, *, on_remote_mutated=None):
    """Виджет с данными заявки с портала или None, если нет привязки portal_zamer_id."""
    zid = portal_zamer_id_from_order(order_data or {})
    if zid is None:
        return None

    fr = QFrame(parent)
    fl = QVBoxLayout(fr)
    fl.setSpacing(6)

    if not zamer_api_client or not zamer_api_client.api_enabled():
        fl.addWidget(
            QLabel(
                "Укажите URL портала и ключ API: в app.cfg секция [zamer_api] — "
                "api_url и api_key (или api_token), либо переменные MC_ZAMER_API_URL и MC_ZAMER_API_KEY."
            )
        )
        fl.addWidget(QLabel(""))
        return fr

    busy = QLabel("Загрузка заявки с портала…")
    busy.setWordWrap(True)
    busy.setStyleSheet("color:#546e7a; padding:6px;")
    fl.addWidget(busy)

    def _fetch_and_fill():
        th = _ZamerDetailFetchThread(int(zid), fr)

        def _ok(row):
            busy.deleteLater()
            if not isinstance(row, dict):
                lab = QLabel(
                    "Не удалось загрузить данные с портала.\n\n"
                    "Внутренняя ошибка: ожидался объект заявки, получено %s."
                    % type(row).__name__
                )
                lab.setWordWrap(True)
                fl.addWidget(lab)
                th.deleteLater()
                return
            try:
                _portal_append_after_fetch(
                    fr, fl, order_data, zid, parent, row, on_remote_mutated=on_remote_mutated
                )
            except Exception as ex:
                lab = QLabel(
                    "Данные с портала получены, но не удалось отрисовать карточку.\n\n%s\n\n%s"
                    % (ex, traceback.format_exc()[:1600])
                )
                lab.setWordWrap(True)
                fl.addWidget(lab)
            th.deleteLater()

        def _bad(msg: str):
            busy.deleteLater()
            lab = QLabel("Не удалось загрузить данные с портала.\n\n%s" % msg)
            lab.setWordWrap(True)
            fl.addWidget(lab)
            th.deleteLater()

        th.finished_ok.connect(_ok, Qt.QueuedConnection)
        th.finished_err.connect(_bad, Qt.QueuedConnection)
        th.start()

    QTimer.singleShot(0, _fetch_and_fill)
    return fr


def _portal_append_after_fetch(
    fr: QFrame,
    fl: QVBoxLayout,
    order_data: dict,
    zid: int,
    parent,
    row: dict,
    *,
    on_remote_mutated=None,
) -> None:
    def _fmt_ru_date_calendar(v) -> str:
        """Только календарная дата (слот date_from / date_to), без часового пояса."""
        s = (str(v or "")).strip()[:10]
        if len(s) < 10:
            return "—"
        try:
            d = datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            return "—"
        m = _MONTHS_RU_GEN[d.month] if 1 <= d.month <= 12 else ""
        return "%d %s" % (d.day, m or "—")

    def _fmt_ru_datetime_msk(v, *, msk_suffix: bool = False) -> str:
        t = format_iso_datetime_msk_long(v, msk_suffix=msk_suffix)
        return t if t else "—"

    def _chip(txt: str, bg="#0b0d12", fg="#f5f7ff") -> QLabel:
        lb = QLabel(txt)
        lb.setWordWrap(True)
        lb.setStyleSheet(
            "QLabel { background:%s; color:%s; border-radius:10px; padding:6px 10px; font-weight:600; }"
            % (bg, fg)
        )
        return lb

    status_code = str(row.get("status") or "").strip().lower()
    status_text = _status_ru(status_code)
    status_bg = {
        "new": "#9e9e9e",
        "in_progress": "#fb8c00",
        "agreed": "#7cb342",
        "completed": "#43a047",
    }.get(status_code, "#9e9e9e")
    fr.setStyleSheet(
        "QFrame { background:%s; border:1px solid rgba(0,0,0,0.18); border-radius:8px; }"
        % status_bg
    )

    assignee = ((row.get("assigned_to_name") or "").strip() or (row.get("assigned_to_login") or "").strip() or "не назначен")
    addr = (row.get("address") or "").strip() or "—"
    client = (order_data.get("client_name") or "").strip() or "—"
    created_at = _fmt_ru_datetime_msk(row.get("created_at"))
    date_from = _fmt_ru_date_calendar(row.get("date_from"))
    date_to = _fmt_ru_date_calendar(row.get("date_to"))
    agreed_from = _fmt_ru_datetime_msk(row.get("agreed_from_at") or row.get("agreed_at"))
    agreed_to = _fmt_ru_datetime_msk(row.get("agreed_to_at"))
    completed_at = _fmt_ru_datetime_msk(row.get("updated_at"), msk_suffix=True)
    payment_paid = bool(row.get("payment_paid"))
    hint_amt = (str(row.get("service_amount_hint") or "").strip())
    entered_amt = (str(row.get("payment_amount") or "").strip())
    payment_amount = hint_amt if hint_amt and hint_amt not in ("0", "0.00") else (entered_amt or "0")
    pm_raw = (str(row.get("payment_method") or "").strip().lower())
    payment_method = _PAYMENT_METHOD_LABELS_RU.get(pm_raw, pm_raw or "—")

    def _commit_remove(service_code: str):
        dlg_parent = parent or fr
        st_row = str(row.get("status") or "").strip().lower()
        if not _can_remove_service_kind(row, service_code, st_row):
            return
        oid = order_data.get("id")
        if oid is None:
            QMessageBox.warning(dlg_parent, "Услуги", "Нет номера заказа.")
            return
        try:
            from db import models as db_models
            from db.zamer_portal_sync import (
                _service_type_code,
                set_first_activated_zamer_service_flags,
                sync_blocks_zamer_for_order,
            )
        except Exception as ex:
            QMessageBox.warning(dlg_parent, "Услуги", "Модуль БД: %s" % ex)
            return
        m0, i0, d0 = _service_flags_from_row(row)
        new_m = m0 if service_code != "measure" else False
        new_i = i0 if service_code != "install" else False
        new_d = d0 if service_code != "delivery" else False
        delete_entire = not (new_m or new_i or new_d)
        try:
            r0 = db_models.get_order(int(oid))
            bundle0 = str((r0 or {}).get("blocks_calc_json") or "")
        except Exception as ex:
            QMessageBox.warning(dlg_parent, "Услуги", "Ошибка чтения заказа: %s" % ex)
            return

        if delete_entire:
            ok_del, err_del = zamer_api_client.zamer_delete(int(zid))
            if not ok_del:
                QMessageBox.warning(dlg_parent, "Портал", err_del or "Не удалось удалить заявку.")
                return
            new_bundle = set_first_activated_zamer_service_flags(
                bundle0, measure=False, install=False, delivery=False
            )
        else:
            stc = _service_type_code(new_m, new_i, new_d)
            _new_row, err_pt = zamer_api_client.zamer_patch_json(
                int(zid),
                {
                    "is_measure": new_m,
                    "is_install": new_i,
                    "is_delivery": new_d,
                    "service_type": stc,
                    "status": "new",
                },
            )
            if _new_row is None:
                QMessageBox.warning(dlg_parent, "Портал", err_pt)
                return
            new_bundle = set_first_activated_zamer_service_flags(
                bundle0, measure=new_m, install=new_i, delivery=new_d
            )
        try:
            db_models.update_order_blocks_calc(int(oid), new_bundle)
            sync_blocks_zamer_for_order(int(oid), new_bundle, blocks_zamer_status="new")
        except Exception as ex:
            QMessageBox.warning(dlg_parent, "Сохранение", str(ex))
            return
        if callable(on_remote_mutated):
            try:
                on_remote_mutated()
            except Exception:
                pass

    top2 = QHBoxLayout()
    top2.setSpacing(6)
    top2.addWidget(_chip("адрес: %s" % addr))
    top2.addWidget(_chip("клиент: %s" % client))
    fl.addLayout(top2)

    top3 = QHBoxLayout()
    top3.setSpacing(6)
    top3.addWidget(_chip("дата создания %s" % created_at))
    if status_code == "agreed":
        top3.addWidget(_chip("согласовано %s" % agreed_from))
    elif status_code == "completed":
        top3.addWidget(_chip("дата завершения %s" % completed_at))
    else:
        top3.addWidget(_chip("интервал дат %s — %s" % (date_from, date_to)))
    if status_code == "completed" and payment_paid:
        top3.addWidget(
            _chip(
                "получено %s · %s" % (payment_amount, payment_method),
                bg="#81c784",
                fg="#1b1b1b",
            )
        )
    fl.addLayout(top3)

    svc_blocks = _portal_active_service_labels(row)
    if svc_blocks:
        for code, title_ru in svc_blocks:
            svc_fr = QFrame()
            svc_fr.setStyleSheet(
                "QFrame { background: rgba(0,0,0,0.12); border: 1px solid rgba(0,0,0,0.22); border-radius: 8px; }"
            )
            svl = QVBoxLayout(svc_fr)
            svl.setSpacing(6)
            svl.setContentsMargins(8, 8, 8, 8)
            row_t = QHBoxLayout()
            row_t.setSpacing(6)
            row_t.addWidget(_chip("%s № %s" % (title_ru, zid)))
            row_t.addWidget(_chip("исполнитель: %s" % assignee))
            row_t.addWidget(_chip("статус %s" % status_text.lower(), bg=status_bg))
            svl.addLayout(row_t)
            if _can_remove_service_kind(row, code, status_code):
                svl.addWidget(
                    _PortalHoldDeleteStrip(
                        "Удалить «%s» с портала (удерживайте 1 с…)" % title_ru.lower(),
                        lambda sc=code: _commit_remove(sc),
                        svc_fr,
                    )
                )
            fl.addWidget(svc_fr)
    else:
        row_f = QHBoxLayout()
        row_f.setSpacing(6)
        row_f.addWidget(_chip("Заявка № %s" % zid))
        row_f.addWidget(_chip("исполнитель: %s" % assignee))
        row_f.addWidget(_chip("статус %s" % status_text.lower(), bg=status_bg))
        fl.addLayout(row_f)

    cm = (row.get("comment_manager") or "").strip()
    cm_shown_in_completed_gallery = False

    files = row.get("files") or []
    image_files = []
    for f in files:
        if not isinstance(f, dict):
            continue
        url = zamer_api_client.resolve_zamer_file_url(str(f.get("file_url") or "")) if zamer_api_client else ""
        f["resolved_url"] = _safe_url_text(url)
        if _is_image_url(f.get("resolved_url") or ""):
            image_files.append(f)

    if image_files and status_code == "completed":
        pic_wrap = QFrame()
        pic_wrap.setStyleSheet(
            "QFrame#PortalCompletedGallery { background: #52b26a; border: none; border-radius: 8px; }"
        )
        pic_wrap.setObjectName("PortalCompletedGallery")
        pvl = QVBoxLayout(pic_wrap)
        pvl.setContentsMargins(12, 12, 12, 12)
        pvl.setSpacing(12)

        idx = {"i": 0}
        preview_pix = {}

        GALLERY_IMG_H = 300
        CAPTION_H = 76

        lbl_count = QLabel()
        lbl_count.setAlignment(Qt.AlignCenter)
        lbl_count.setStyleSheet(
            "QLabel { color: #1565c0; font-size: 28px; font-weight: 700; background: transparent; border: none; }"
        )

        count_band = QWidget()
        count_band.setObjectName("PortalGalleryCountBand")
        count_band.setAttribute(Qt.WA_TranslucentBackground, True)
        count_band.setAutoFillBackground(False)
        count_band.setStyleSheet("#PortalGalleryCountBand { background: transparent; border: none; }")
        count_band.setFixedHeight(GALLERY_IMG_H)
        cbl = QVBoxLayout(count_band)
        cbl.setContentsMargins(0, 0, 0, 0)
        cbl.addStretch(1)
        cbl.addWidget(lbl_count, 0, Qt.AlignHCenter)
        cbl.addStretch(1)

        main_img = QLabel("картинка")
        main_img.setAlignment(Qt.AlignCenter)
        main_img.setFixedHeight(GALLERY_IMG_H)
        main_img.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        main_img.setStyleSheet(
            "QLabel { background: #000000; color: #ffffff; border: 2px solid #0d0d0d; border-radius: 4px; }"
        )
        main_img.setCursor(Qt.PointingHandCursor)
        main_img.setScaledContents(False)

        img_caption = QLabel("")
        img_caption.setWordWrap(True)
        img_caption.setFixedHeight(CAPTION_H)
        img_caption.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        img_caption.setStyleSheet(
            "QLabel { background: #000000; color: #f5f5f5; border: 2px solid #0d0d0d; "
            "border-radius: 4px; padding: 10px 12px; font-size: 12px; }"
        )

        _nav_btn_style = (
            "QPushButton { background: #000000; color: #2196F3; border: 2px solid #0d0d0d; "
            "border-radius: 4px; font-size: 38px; font-weight: 900; padding: 0px; }"
            "QPushButton:hover { background: #1a1a1a; }"
            "QPushButton:pressed { background: #333333; }"
        )
        btn_prev = QPushButton("←")
        btn_prev.setFixedSize(64, 64)
        btn_prev.setCursor(Qt.PointingHandCursor)
        btn_prev.setStyleSheet(_nav_btn_style)

        btn_save_one = QPushButton("сохранить")
        btn_save_all = QPushButton("сохранить все")
        _btn_dark = (
            "QPushButton { background: #000000; color: #ffffff; border: 2px solid #0d0d0d; "
            "border-radius: 4px; padding: 10px 14px; font-size: 13px; font-weight: 600; }"
            "QPushButton:hover { background: #222222; }"
            "QPushButton:pressed { background: #333333; }"
        )
        for b in (btn_save_one, btn_save_all):
            b.setCursor(Qt.PointingHandCursor)
            b.setMinimumHeight(44)
            b.setStyleSheet(_btn_dark)
        btn_next = QPushButton("→")
        btn_next.setFixedSize(64, 64)
        btn_next.setCursor(Qt.PointingHandCursor)
        btn_next.setStyleSheet(_nav_btn_style)

        right_band = QWidget()
        right_band.setObjectName("PortalGalleryRightBand")
        right_band.setAttribute(Qt.WA_TranslucentBackground, True)
        right_band.setAutoFillBackground(False)
        right_band.setStyleSheet("#PortalGalleryRightBand { background: transparent; border: none; }")
        right_band.setFixedHeight(GALLERY_IMG_H)
        rbl = QVBoxLayout(right_band)
        rbl.setContentsMargins(0, 0, 0, 0)
        rbl.addStretch(1)
        rbl.addWidget(btn_save_one)
        rbl.addWidget(btn_save_all)
        rbl.addStretch(1)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        grid.addWidget(count_band, 0, 0, alignment=Qt.AlignHCenter | Qt.AlignVCenter)
        grid.addWidget(main_img, 0, 1)
        grid.addWidget(right_band, 0, 2, alignment=Qt.AlignHCenter | Qt.AlignVCenter)
        grid.addWidget(btn_prev, 1, 0, alignment=Qt.AlignHCenter | Qt.AlignVCenter)
        grid.addWidget(img_caption, 1, 1)
        grid.addWidget(btn_next, 1, 2, alignment=Qt.AlignHCenter | Qt.AlignVCenter)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 68)
        grid.setColumnMinimumWidth(2, 68)

        pvl.addLayout(grid)

        if cm:
            work_comment = QLabel(cm)
            work_comment.setWordWrap(True)
            work_comment.setStyleSheet(
                "QLabel { background: #000000; color: #f5f5f5; border: 2px solid #0d0d0d; "
                "border-radius: 4px; padding: 10px 12px; font-size: 12px; }"
            )
            pvl.addWidget(work_comment)
            cm_shown_in_completed_gallery = True

        fl.addWidget(pic_wrap)

        def _get_pm(i):
            i = i % len(image_files)
            if i in preview_pix:
                return preview_pix[i]
            try:
                raw = _preview_image_bytes(image_files[i].get("resolved_url"), timeout=8)
                pm = QPixmap()
                if pm.loadFromData(raw) and not pm.isNull():
                    preview_pix[i] = pm
                    return pm
            except Exception:
                pass
            preview_pix[i] = QPixmap()
            return preview_pix[i]

        def _apply_main(lb: QLabel, pm: QPixmap, w: int, h: int):
            if pm.isNull():
                lb.setText("картинка")
                lb.setPixmap(QPixmap())
                return
            lb.setText("")
            w = max(int(w or 0), 1)
            h = max(int(h or 0), 1)
            lb.setPixmap(pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation))

        def _render_gallery():
            i0 = idx["i"] % len(image_files)
            n = len(image_files)
            lbl_count.setText("%d / %d" % (i0 + 1, n))
            cmt = (image_files[i0].get("comment") or "").strip()
            img_caption.setText(cmt if cmt else "без комментария")

            def _apply_pix():
                w = max(main_img.width(), 2)
                _apply_main(main_img, _get_pm(i0), w, GALLERY_IMG_H)

            QTimer.singleShot(0, _apply_pix)

        def _step(delta):
            idx["i"] = (idx["i"] + delta) % len(image_files)
            _render_gallery()

        def _open_fullsize():
            if not image_files:
                return
            cur = image_files[idx["i"] % len(image_files)]
            _PortalFullsizeImageDialog(dict(cur), parent).exec_()

        btn_prev.clicked.connect(lambda: _step(-1))
        btn_next.clicked.connect(lambda: _step(1))
        btn_save_one.clicked.connect(
            lambda: _save_single_file(image_files[idx["i"] % len(image_files)], parent)
        )
        btn_save_all.clicked.connect(lambda: _save_files_to_dir(list(image_files), parent))

        def _on_main_img_click(_ev):
            if _ev.button() == Qt.LeftButton:
                _open_fullsize()

        main_img.mousePressEvent = _on_main_img_click

        QShortcut(QKeySequence(Qt.Key_Left), pic_wrap).activated.connect(lambda: _step(-1))
        QShortcut(QKeySequence(Qt.Key_Right), pic_wrap).activated.connect(lambda: _step(1))
        _render_gallery()

    elif image_files:
        pic_wrap = QFrame()
        pic_wrap.setStyleSheet("QFrame { background:#ffffff; border:1px solid #c8d0da; border-radius:10px; }")
        pvl = QVBoxLayout(pic_wrap)
        pvl.setContentsMargins(8, 8, 8, 8)
        pvl.setSpacing(6)
        pvl.addWidget(QLabel("Фото и комментарии:"))
        idx = {"i": 0}
        img = QLabel("фото")
        img.setAlignment(Qt.AlignCenter)
        img.setMinimumHeight(180)
        img.setStyleSheet("QLabel { background:#f8fafc; border:1px solid #b0bec5; border-radius:8px; }")
        cap = QLabel("")
        cap.setWordWrap(True)
        cap.setStyleSheet("font-size:11px; color:#263238;")

        def _show(i):
            i = max(0, min(i, len(image_files) - 1))
            idx["i"] = i
            f = image_files[i]
            cap.setText((f.get("comment") or "").strip() or "без комментария")
            try:
                raw = _preview_image_bytes(f.get("resolved_url"), timeout=8)
                pm = QPixmap()
                if pm.loadFromData(raw) and not pm.isNull():
                    iw = max(img.width(), 320)
                    ih = max(img.height(), 180)
                    img.setPixmap(pm.scaled(iw, ih, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    img.setText(
                        "Превью временно отключено"
                        if PORTAL_SKIP_PREVIEW_IMAGE_DOWNLOADS
                        else "не удалось загрузить фото"
                    )
            except Exception:
                img.setText(
                    "Превью временно отключено"
                    if PORTAL_SKIP_PREVIEW_IMAGE_DOWNLOADS
                    else "не удалось загрузить фото"
                )

        nav = QHBoxLayout()
        b_prev = QPushButton("←")
        b_next = QPushButton("→")
        b_prev.setFixedWidth(32)
        b_next.setFixedWidth(32)
        b_prev.clicked.connect(lambda: _show((idx["i"] - 1) % len(image_files)))
        b_next.clicked.connect(lambda: _show((idx["i"] + 1) % len(image_files)))
        nav.addWidget(b_prev)
        nav.addWidget(img, 1)
        nav.addWidget(b_next)
        pvl.addLayout(nav)
        pvl.addWidget(cap)
        QTimer.singleShot(0, lambda: _show(0))
        fl.addWidget(pic_wrap)

    if isinstance(files, list) and files:
        skip_download_row = status_code == "completed" and bool(image_files)
        if skip_download_row:
            non_image = [f for f in files if isinstance(f, dict) and not _is_image_url(f.get("resolved_url") or "")]
            if non_image:
                btn_row = QHBoxLayout()
                btn_all = QPushButton("Скачать все файлы заявки…")
                btn_all.clicked.connect(
                    lambda _=False, ff=list(non_image): QMessageBox.information(
                        parent, "Скачивание", "Сохранено файлов: %s" % _save_files_to_dir(ff, parent)
                    )
                )
                btn_row.addWidget(btn_all)
                btn_row.addStretch()
                fl.addLayout(btn_row)
        else:
            btn_row = QHBoxLayout()
            btn_all = QPushButton("Скачать все файлы…")
            btn_all.clicked.connect(
                lambda _=False, ff=list(files): QMessageBox.information(
                    parent, "Скачивание", "Сохранено файлов: %s" % _save_files_to_dir(ff, parent)
                )
            )
            btn_row.addWidget(btn_all)
            if image_files:
                btn_gallery = QPushButton("Открыть галерею изображений…")
                btn_gallery.clicked.connect(
                    lambda _=False, ff=list(image_files): _PortalImageGalleryDialog(ff, 0, parent).exec_()
                )
                btn_row.addWidget(btn_gallery)
            btn_row.addStretch()
            fl.addLayout(btn_row)

    if cm and not cm_shown_in_completed_gallery:
        gc = QLabel(cm)
        gc.setWordWrap(True)
        gc.setStyleSheet(
            "QLabel { background:#05080e; color:#f5f5f5; border-radius:8px; padding:8px 10px; font-size:11px; }"
        )
        fl.addWidget(gc)
