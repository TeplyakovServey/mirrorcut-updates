# -*- coding: utf-8 -*-
"""Просмотр таблиц инвентаризации и утерь (все роли)."""
from __future__ import annotations

import os
import sys

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QHBoxLayout,
    QWidget,
    QScrollArea,
    QGridLayout,
    QLabel,
    QFrame,
    QMessageBox,
)

from db import models as db_models
from window_branding import apply_window_icon


class _InventoryTypeCard(QFrame):
    """Карточка типа стекла/профиля: зелёная — в охвате, серая — снято, красная — тип уже закрыт ранее."""

    def __init__(
        self,
        label: str,
        domain: str,
        type_key: str,
        is_completed: bool,
        parent=None,
        on_toggle=None,
    ):
        super().__init__(parent)
        self._domain = domain
        self._type_key = type_key
        self._completed = bool(is_completed)
        self._selected = not self._completed
        self._on_toggle = on_toggle
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setMaximumHeight(42)
        self.setFrameStyle(QFrame.Box | QFrame.Plain)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(0)
        self._lab = QLabel(label)
        self._lab.setWordWrap(True)
        self._lab.setAlignment(Qt.AlignCenter)
        f = self._lab.font()
        f.setPointSize(12)
        self._lab.setFont(f)
        lay.addWidget(self._lab)
        self._apply_style()

    def _apply_style(self):
        if self._completed:
            self.setStyleSheet(
                "QFrame { border: 2px solid #ef4444; border-radius: 6px; background: #1e293b; }"
            )
            self._lab.setStyleSheet("color: #fecaca;")
        elif self._selected:
            self.setStyleSheet(
                "QFrame { border: 2px solid #22c55e; border-radius: 6px; background: #1e293b; }"
            )
            self._lab.setStyleSheet("color: #e2e8f0; font-weight: 600;")
        else:
            self.setStyleSheet(
                "QFrame { border: 2px solid #64748b; border-radius: 6px; background: #1e293b; }"
            )
            self._lab.setStyleSheet("color: #e2e8f0;")

    def mousePressEvent(self, event):
        if self._completed:
            return
        self._selected = not self._selected
        self._apply_style()
        if self._on_toggle:
            self._on_toggle()
        super().mousePressEvent(event)

    def is_completed(self):
        return self._completed

    def is_selected_for_campaign(self):
        return self._selected and not self._completed

    def set_selected(self, val: bool):
        if self._completed:
            return
        self._selected = bool(val)
        self._apply_style()

    def domain(self):
        return self._domain

    def type_key(self):
        return self._type_key


def _fmt_ts(val):
    if hasattr(val, "strftime"):
        return val.strftime("%d.%m.%Y %H:%M:%S")
    return str(val or "")


def _glass_kind_ru(sk):
    if sk == "piece_k":
        return "Изделие (K)"
    if sk == "remnant":
        return "Остаток стекла"
    return str(sk or "")


def _fmt_glass_desc(r):
    d = r.get("details") or {}
    sk = r.get("source_kind")
    parts = []
    sub = (r.get("subtitle") or "").strip()
    if sub:
        parts.append(sub)
    if sk == "piece_k":
        w, h = d.get("width_mm"), d.get("height_mm")
        if w and h:
            parts.append("%s×%s мм" % (w, h))
        m = d.get("material")
        if m:
            parts.append(str(m))
        t = d.get("thickness_mm")
        if t not in (None, "", 0):
            parts.append("толщ. %s мм" % t)
        oid = d.get("order_id")
        if oid:
            parts.append("заказ №%s" % oid)
    elif sk == "remnant":
        nm = d.get("name")
        if nm:
            parts.append(str(nm))
        w, h = d.get("width_mm"), d.get("height_mm")
        if w and h:
            parts.append("%s×%s мм" % (w, h))
        t = d.get("thickness_mm")
        if t not in (None, "", 0):
            parts.append("толщ. %s мм" % t)
    return " · ".join([p for p in parts if p])


def _fmt_profile_desc(r):
    d = r.get("details") or {}
    parts = []
    sub = (r.get("subtitle") or "").strip()
    if sub:
        parts.append(sub)
    bits = []
    if d.get("series"):
        bits.append(str(d["series"]))
    if d.get("name"):
        bits.append(str(d["name"]))
    if d.get("color"):
        bits.append(str(d["color"]))
    if bits:
        parts.append(", ".join(bits))
    lm = d.get("length_mm")
    if lm not in (None, "", 0):
        try:
            parts.append("%s мм" % int(lm))
        except (TypeError, ValueError):
            parts.append("%s мм" % lm)
    if d.get("is_remnant"):
        parts.append("остаток профиля")
    return " · ".join([p for p in parts if p])


class InventoryTablesDialog(QDialog):
    def __init__(self, parent=None, current_user=None):
        super().__init__(parent)
        self._user = current_user or {}
        self.setWindowTitle("Инвентаризация и утери")
        apply_window_icon(self)
        self.resize(1100, 620)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()

        self._glass_type_cards: list[_InventoryTypeCard] = []
        self._profile_type_cards: list[_InventoryTypeCard] = []
        start_tab = QWidget()
        start_outer = QVBoxLayout(start_tab)
        self._lbl_inv_status = QLabel("")
        self._lbl_inv_status.setWordWrap(True)
        start_outer.addWidget(self._lbl_inv_status)
        self._lbl_inv_progress = QLabel("")
        self._lbl_inv_progress.setWordWrap(True)
        self._lbl_inv_progress.setStyleSheet("color: #cbd5e1;")
        start_outer.addWidget(self._lbl_inv_progress)

        start_sub = QTabWidget()
        glass_page = QWidget()
        gpv = QVBoxLayout(glass_page)
        g_scroll = QScrollArea()
        g_scroll.setWidgetResizable(True)
        g_inner = QWidget()
        self._glass_grid = QGridLayout(g_inner)
        self._glass_grid.setSpacing(6)
        self._inv_grid_cols = 6
        g_scroll.setWidget(g_inner)
        gpv.addWidget(g_scroll)
        start_sub.addTab(glass_page, "Стекло")

        prof_page = QWidget()
        ppv = QVBoxLayout(prof_page)
        p_scroll = QScrollArea()
        p_scroll.setWidgetResizable(True)
        p_inner = QWidget()
        self._profile_grid = QGridLayout(p_inner)
        self._profile_grid.setSpacing(6)
        p_scroll.setWidget(p_inner)
        ppv.addWidget(p_scroll)
        start_sub.addTab(prof_page, "Фасады")

        start_outer.addWidget(start_sub)
        hb_inv = QHBoxLayout()
        self._btn_select_all_types = QPushButton("Выделить все")
        self._btn_select_all_types.setToolTip("Повторное нажатие снимает выделение со всех доступных типов")
        self._btn_select_all_types.clicked.connect(self._on_select_all_inventory_types)
        self._btn_start_inventory = QPushButton("Начать инвентаризацию")
        self._btn_start_inventory.clicked.connect(self._on_start_inventory_campaign)
        self._btn_pause_resume_inventory = QPushButton("Пауза")
        self._btn_pause_resume_inventory.clicked.connect(self._on_pause_resume_inventory_campaign)
        self._btn_cancel_inventory = QPushButton("Отменить инвентаризацию")
        self._btn_cancel_inventory.clicked.connect(self._on_cancel_inventory_campaign)
        hb_inv.addWidget(self._btn_select_all_types)
        hb_inv.addWidget(self._btn_start_inventory)
        hb_inv.addWidget(self._btn_pause_resume_inventory)
        hb_inv.addWidget(self._btn_cancel_inventory)
        hb_inv.addStretch()
        start_outer.addLayout(hb_inv)
        tabs.addTab(start_tab, "Начать инвентаризацию")

        self._tbl_scans = QTableWidget(0, 10)
        self._tbl_scans.setHorizontalHeaderLabels(
            ["id", "тип", "stock_id", "номер", "размер", "campaign_id", "сессия", "user_id", "логин", "дата"]
        )
        self._tbl_loss = QTableWidget(0, 8)
        self._tbl_loss.setHorizontalHeaderLabels(
            ["id", "тип", "stock_id", "номер", "причина", "сессия", "логин", "дата"]
        )
        self._tbl_qr_glass = QTableWidget(0, 7)
        self._tbl_qr_glass.setHorizontalHeaderLabels(
            [
                "код этикетки",
                "вид",
                "заголовок",
                "дата и время",
                "ФИО",
                "логин",
                "описание изделия",
            ]
        )
        self._tbl_qr_profile = QTableWidget(0, 6)
        self._tbl_qr_profile.setHorizontalHeaderLabels(
            ["код этикетки", "заголовок", "дата и время", "ФИО", "логин", "описание (профиль)"]
        )
        qr_wrap = QWidget()
        qr_lay = QVBoxLayout(qr_wrap)
        qr_lay.setContentsMargins(0, 0, 0, 0)
        qr_sub = QTabWidget()
        qr_sub.addTab(self._tbl_qr_glass, "Стекло")
        qr_sub.addTab(self._tbl_qr_profile, "Профили")
        qr_lay.addWidget(qr_sub)
        tabs.addTab(self._tbl_scans, "Сканирования")
        tabs.addTab(self._tbl_loss, "Утери")
        tabs.addTab(qr_wrap, "Сгенерированные QR")
        lay.addWidget(tabs)
        self._history_tables_loaded = False
        self._last_inventory_campaign_timer_id = None
        hb = QHBoxLayout()
        b = QPushButton("Обновить")
        b.clicked.connect(self._reload)
        b_clear = QPushButton("Очистить историю инвентаризации + QR")
        b_clear.clicked.connect(self._on_purge_inventory_history)
        hb.addStretch()
        hb.addWidget(b_clear)
        hb.addWidget(b)
        lay.addLayout(hb)
        self._reload_inventory_type_cards()
        tabs.currentChanged.connect(self._on_inventory_main_tab_changed)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self._on_inventory_timer_tick)
        self._status_timer.start()

    def _capture_inventory_campaign_baseline_for_timer(self):
        """Чтобы таймер не делал полный пересбор карточек без смены кампании."""
        active = None
        try:
            active = db_models.get_active_inventory_campaign()
        except Exception:
            active = None
        if not active:
            try:
                active = db_models.get_latest_inventory_campaign(statuses=["paused"])
            except Exception:
                active = None
        if active and active.get("id") is not None:
            self._last_inventory_campaign_timer_id = int(active["id"])
        else:
            self._last_inventory_campaign_timer_id = None

    def _on_inventory_main_tab_changed(self, index: int):
        """Таблицы истории грузим только при первом заходе на вкладки со сканами/утерями/QR."""
        if index >= 1 and not self._history_tables_loaded:
            self._history_tables_loaded = True
            QTimer.singleShot(0, self._reload_history_tables)

    def _on_inventory_timer_tick(self):
        active = None
        try:
            active = db_models.get_active_inventory_campaign()
        except Exception:
            active = None
        if not active:
            try:
                active = db_models.get_latest_inventory_campaign(statuses=["paused"])
            except Exception:
                active = None
        cur_id = int(active["id"]) if active and active.get("id") is not None else None
        if cur_id != self._last_inventory_campaign_timer_id:
            self._last_inventory_campaign_timer_id = cur_id
            self._reload_inventory_type_cards()
            return
        if active:
            self._lbl_inv_progress.setText(self._campaign_progress_text(active))

    def _campaign_progress_text(self, campaign_row):
        if not campaign_row:
            return ""
        state = db_models.inventory_campaign_state_payload(campaign_row) or {}
        g_scope = db_models.inventory_campaign_json_key_list(campaign_row, "glass_type_keys")
        p_scope = db_models.inventory_campaign_json_key_list(campaign_row, "profile_type_keys")
        still = state.get("types_still_seeking") or {}
        g_pending = set(still.get("glass") or [])
        p_pending = set(still.get("profile") or [])
        materials_total = len(g_scope) + len(p_scope)
        materials_done = (len(g_scope) - len(g_pending)) + (len(p_scope) - len(p_pending))
        return (
            "Прогресс кампании: материалов %s из %s · "
            "стекло %s из %s · профили %s из %s."
            % (
                max(0, int(materials_done)),
                max(0, int(materials_total)),
                int(state.get("glass_scanned_closed") or 0),
                int(state.get("glass_total") or 0),
                int(state.get("profile_scanned_closed") or 0),
                int(state.get("profile_total") or 0),
            )
        )

    def _set_campaign_controls(self, campaign_row):
        has_campaign = campaign_row is not None and str(campaign_row.get("status") or "") in ("active", "paused")
        self._btn_pause_resume_inventory.setVisible(has_campaign)
        self._btn_cancel_inventory.setVisible(has_campaign)
        if not has_campaign:
            return
        st = str(campaign_row.get("status") or "")
        self._btn_pause_resume_inventory.setText("Продолжить инвентаризацию" if st == "paused" else "Пауза")

    def _clear_grid(self, grid: QGridLayout):
        while grid.count():
            it = grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

    def _reload_inventory_type_cards(self):
        self._clear_grid(self._glass_grid)
        self._clear_grid(self._profile_grid)
        self._glass_type_cards = []
        self._profile_type_cards = []
        done = db_models.list_inventory_completed_type_keys()
        active = None
        try:
            active = db_models.get_active_inventory_campaign()
        except Exception:
            active = None
        if not active:
            try:
                active = db_models.get_latest_inventory_campaign(statuses=["paused"])
            except Exception:
                active = None

        self._set_campaign_controls(active)
        if active:
            st = str(active.get("status") or "")
            st_txt = "на паузе" if st == "paused" else "выполняется"
            self._lbl_inv_status.setText(
                "Сейчас %s инвентаризация (кампания № %s)."
                % (st_txt, active.get("id"))
            )
            self._lbl_inv_progress.setText(self._campaign_progress_text(active))
            self._btn_start_inventory.setEnabled(False)
        else:
            self._lbl_inv_status.setText(
                "Выберите типы (зелёная рамка — войдут в кампанию). Красная рамка — тип уже был полностью инвентаризирован ранее."
            )
            self._lbl_inv_progress.setText("")
            self._btn_start_inventory.setEnabled(True)

        gc = self._inv_grid_cols
        glass_keys = db_models.inventory_glass_type_keys_on_stock()
        for i, key in enumerate(glass_keys):
            comp = ("glass", key) in done
            card = _InventoryTypeCard(
                key, "glass", key, comp, on_toggle=self._sync_select_all_button
            )
            self._glass_type_cards.append(card)
            self._glass_grid.addWidget(card, i // gc, i % gc)

        prof_keys = db_models.inventory_profile_type_keys_on_stock()
        for i, key in enumerate(prof_keys):
            comp = ("profile", key) in done
            card = _InventoryTypeCard(
                key, "profile", key, comp, on_toggle=self._sync_select_all_button
            )
            self._profile_type_cards.append(card)
            self._profile_grid.addWidget(card, i // gc, i % gc)

        self._sync_select_all_button()
        self._capture_inventory_campaign_baseline_for_timer()

    def _actionable_type_cards(self):
        return [c for c in self._glass_type_cards + self._profile_type_cards if not c.is_completed()]

    def _sync_select_all_button(self):
        act = self._actionable_type_cards()
        if not act:
            self._btn_select_all_types.setText("Выделить все")
            return
        all_on = all(c.is_selected_for_campaign() for c in act)
        self._btn_select_all_types.setText("Снять выделение" if all_on else "Выделить все")

    def _on_select_all_inventory_types(self):
        act = self._actionable_type_cards()
        if not act:
            return
        all_on = all(c.is_selected_for_campaign() for c in act)
        if all_on:
            for c in act:
                c.set_selected(False)
        else:
            for c in act:
                c.set_selected(True)
        self._sync_select_all_button()

    def _on_start_inventory_campaign(self):
        existing = db_models.get_active_inventory_campaign()
        if not existing:
            existing = db_models.get_latest_inventory_campaign(statuses=["paused"])
        if existing:
            if str(existing.get("status") or "") == "paused":
                QMessageBox.warning(self, "Инвентаризация", "Есть кампания на паузе. Сначала продолжите или отмените её.")
            else:
                QMessageBox.warning(self, "Инвентаризация", "Уже есть активная кампания.")
            return
        g_sel = [c.type_key() for c in self._glass_type_cards if c.is_selected_for_campaign()]
        p_sel = [c.type_key() for c in self._profile_type_cards if c.is_selected_for_campaign()]
        if not g_sel and not p_sel:
            QMessageBox.warning(self, "Инвентаризация", "Выберите хотя бы один тип (зелёные карточки).")
            return
        uid = self._user.get("id")
        login = str(self._user.get("login") or "")
        row, err = db_models.create_inventory_campaign(g_sel, p_sel, started_by_user_id=uid, started_by_login=login)
        if err == "active_exists":
            QMessageBox.warning(self, "Инвентаризация", "Уже запущена другая кампания.")
            return
        if err == "empty_scope":
            QMessageBox.warning(self, "Инвентаризация", "Пустой список типов.")
            return
        if not row:
            QMessageBox.warning(self, "Инвентаризация", "Не удалось создать кампанию.")
            return
        QMessageBox.information(
            self,
            "Инвентаризация",
            "Кампания № %s запущена. На веб-портале производства сотрудникам доступна только страница инвентаризации."
            % row.get("id"),
        )
        self._reload_inventory_type_cards()

    def _on_pause_resume_inventory_campaign(self):
        camp = db_models.get_active_inventory_campaign()
        if camp and str(camp.get("status") or "") == "active":
            ok = db_models.pause_inventory_campaign(int(camp.get("id") or 0))
            if not ok:
                QMessageBox.warning(self, "Инвентаризация", "Не удалось поставить кампанию на паузу.")
                return
            QMessageBox.information(
                self,
                "Инвентаризация",
                "Кампания поставлена на паузу. Блокировка в WEB_SERVICE снята.",
            )
            self._reload()
            return
        camp = db_models.get_latest_inventory_campaign(statuses=["paused"])
        if not camp:
            QMessageBox.warning(self, "Инвентаризация", "Нет кампании для продолжения.")
            return
        ok, err = db_models.resume_inventory_campaign(int(camp.get("id") or 0))
        if not ok:
            if err == "active_exists":
                QMessageBox.warning(self, "Инвентаризация", "Есть другая активная кампания.")
            else:
                QMessageBox.warning(self, "Инвентаризация", "Не удалось продолжить кампанию.")
            return
        QMessageBox.information(self, "Инвентаризация", "Кампания возобновлена.")
        self._reload()

    def _on_cancel_inventory_campaign(self):
        camp = db_models.get_active_inventory_campaign() or db_models.get_latest_inventory_campaign(statuses=["paused"])
        if not camp:
            QMessageBox.warning(self, "Инвентаризация", "Нет активной/приостановленной кампании для отмены.")
            return
        cid = int(camp.get("id") or 0)
        q = QMessageBox.question(
            self,
            "Отмена инвентаризации",
            "Отменить кампанию № %s?\n"
            "Будут удалены все сканирования/утери/сверка целых листов этой кампании,\n"
            "а выбранные для неё типы вернутся в состояние «не инвентаризировано»."
            % cid,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if q != QMessageBox.Yes:
            return
        ok = db_models.cancel_inventory_campaign_and_wipe_progress(
            cid,
            summary={"cancel_reason": "manual_cancel_from_desktop"},
        )
        if not ok:
            QMessageBox.warning(self, "Инвентаризация", "Не удалось отменить кампанию.")
            return
        QMessageBox.information(self, "Инвентаризация", "Кампания отменена и данные этой сессии очищены.")
        self._reload()

    def _reload_history_tables(self):
        """Тяжёлые таблицы — после первого кадра UI, чтобы окно не зависало."""
        scans = db_models.list_inventory_scans(2000) or []
        self._tbl_scans.setRowCount(len(scans))
        for i, r in enumerate(scans):
            self._tbl_scans.setItem(i, 0, QTableWidgetItem(str(r.get("id") or "")))
            self._tbl_scans.setItem(i, 1, QTableWidgetItem(str(r.get("item_type") or "")))
            self._tbl_scans.setItem(i, 2, QTableWidgetItem(str(r.get("stock_ref_id") or "")))
            self._tbl_scans.setItem(i, 3, QTableWidgetItem(str(r.get("unique_number") or "")))
            self._tbl_scans.setItem(i, 4, QTableWidgetItem(str(r.get("size_text") or "")))
            self._tbl_scans.setItem(i, 5, QTableWidgetItem(str(r.get("campaign_id") or "")))
            self._tbl_scans.setItem(i, 6, QTableWidgetItem(str(r.get("session_key") or "")))
            self._tbl_scans.setItem(i, 7, QTableWidgetItem(str(r.get("actor_user_id") or "")))
            self._tbl_scans.setItem(i, 8, QTableWidgetItem(str(r.get("actor_login") or "")))
            ca = r.get("scanned_at")
            ds = ca.strftime("%d.%m.%Y %H:%M") if hasattr(ca, "strftime") else str(ca or "")
            self._tbl_scans.setItem(i, 9, QTableWidgetItem(ds))
        loss = db_models.list_inventory_losses(1500) or []
        self._tbl_loss.setRowCount(len(loss))
        for i, r in enumerate(loss):
            self._tbl_loss.setItem(i, 0, QTableWidgetItem(str(r.get("id") or "")))
            self._tbl_loss.setItem(i, 1, QTableWidgetItem(str(r.get("item_type") or "")))
            self._tbl_loss.setItem(i, 2, QTableWidgetItem(str(r.get("stock_ref_id") or "")))
            self._tbl_loss.setItem(i, 3, QTableWidgetItem(str(r.get("unique_number") or "")))
            self._tbl_loss.setItem(i, 4, QTableWidgetItem(str(r.get("reason_text") or "")))
            self._tbl_loss.setItem(i, 5, QTableWidgetItem(str(r.get("session_key") or "")))
            self._tbl_loss.setItem(i, 6, QTableWidgetItem(str(r.get("actor_login") or "")))
            ca = r.get("created_at")
            ds = ca.strftime("%d.%m.%Y %H:%M") if hasattr(ca, "strftime") else str(ca or "")
            self._tbl_loss.setItem(i, 7, QTableWidgetItem(ds))

        g_rows = db_models.list_generated_qr_log(tab="glass", limit=2000) or []
        self._tbl_qr_glass.setRowCount(len(g_rows))
        for i, r in enumerate(g_rows):
            self._tbl_qr_glass.setItem(i, 0, QTableWidgetItem(str(r.get("label_code") or "")))
            self._tbl_qr_glass.setItem(i, 1, QTableWidgetItem(_glass_kind_ru(r.get("source_kind"))))
            self._tbl_qr_glass.setItem(i, 2, QTableWidgetItem(str(r.get("title") or "")))
            self._tbl_qr_glass.setItem(i, 3, QTableWidgetItem(_fmt_ts(r.get("created_at"))))
            self._tbl_qr_glass.setItem(i, 4, QTableWidgetItem(str(r.get("actor_name") or "")))
            self._tbl_qr_glass.setItem(i, 5, QTableWidgetItem(str(r.get("actor_login") or "")))
            self._tbl_qr_glass.setItem(i, 6, QTableWidgetItem(_fmt_glass_desc(r)))

        p_rows = db_models.list_generated_qr_log(tab="profile", limit=2000) or []
        self._tbl_qr_profile.setRowCount(len(p_rows))
        for i, r in enumerate(p_rows):
            self._tbl_qr_profile.setItem(i, 0, QTableWidgetItem(str(r.get("label_code") or "")))
            self._tbl_qr_profile.setItem(i, 1, QTableWidgetItem(str(r.get("title") or "")))
            self._tbl_qr_profile.setItem(i, 2, QTableWidgetItem(_fmt_ts(r.get("created_at"))))
            self._tbl_qr_profile.setItem(i, 3, QTableWidgetItem(str(r.get("actor_name") or "")))
            self._tbl_qr_profile.setItem(i, 4, QTableWidgetItem(str(r.get("actor_login") or "")))
            self._tbl_qr_profile.setItem(i, 5, QTableWidgetItem(_fmt_profile_desc(r)))

    def _reload(self):
        self._reload_inventory_type_cards()
        self._reload_history_tables()
        self._history_tables_loaded = True

    def _on_purge_inventory_history(self):
        active = db_models.get_active_inventory_campaign()
        if active:
            QMessageBox.warning(
                self,
                "Очистка истории",
                "Сначала завершите или отмените активную инвентаризацию (кампания № %s)." % active.get("id"),
            )
            return
        q = QMessageBox.question(
            self,
            "Очистка истории",
            "Удалить всю историю инвентаризации, утерь, завершённых кампаний и журнал сгенерированных QR?\n"
            "Действие необратимо.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if q != QMessageBox.Yes:
            return
        try:
            db_models.purge_inventory_and_qr_history(include_generated_qr=True)
        except Exception as e:
            QMessageBox.warning(self, "Очистка истории", "Ошибка очистки: %s" % e)
            return
        QMessageBox.information(self, "Очистка истории", "История инвентаризации и QR очищена.")
        self._reload()
