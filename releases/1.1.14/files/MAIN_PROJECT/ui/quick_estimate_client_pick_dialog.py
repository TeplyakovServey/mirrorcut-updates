# -*- coding: utf-8 -*-
"""Шаг «Клиент» для быстрого просчёта после выбора категории: поиск в справочнике + история быстрых просчётов."""

import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QCompleter
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QMessageBox,
)

from db import models as db_models
from ui.quick_client_create_dialog import open_quick_client_create_dialog


def _digits_only(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _phone_hint_from_quick_row(q: dict, edit_phone_text: str) -> str:
    t = (edit_phone_text or "").strip()
    if t:
        return t
    ci = (q.get("contact_info") or "").strip()
    if not ci:
        return ""
    for part in (p.strip() for p in ci.split("·")):
        if part and any(c.isdigit() for c in part):
            return part
    return ci


def _phone_prefill_for_search(q: dict) -> str:
    """Подставляем телефон в поле поиска только если это похоже на номер (иначе фильтр по телефону скрывает всю базу)."""
    raw = _phone_hint_from_quick_row(q, "").strip()
    if len(_digits_only(raw)) >= 10:
        return raw
    return ""


def open_quick_estimate_client_dialog(parent):
    """
    Возвращает тот же dict, что open_quick_client_create_dialog, или None при отмене.
    """
    d = QDialog(parent)
    d.setWindowTitle("Клиент — быстрый просчёт")
    d.setMinimumWidth(520)
    lay = QVBoxLayout(d)

    lay.addWidget(
        QLabel(
            "Поиск по <b>справочнику клиентов</b> и по именам из <b>быстрых просчётов</b>. "
            "Выберите строку или создайте клиента быстрым форматом."
        )
    )
    lay.addWidget(QLabel("Начните вводить имя:"))

    edit = QLineEdit()
    edit.setPlaceholderText("Имя клиента…")
    lay.addWidget(edit)

    lst = QListWidget()
    lst.setMinimumHeight(220)
    lay.addWidget(lst)

    result_holder = {"meta": None}

    def refresh():
        lst.clear()
        pref = (edit.text() or "").strip()
        if len(pref) < 1:
            return
        try:
            sug = db_models.list_quick_estimate_client_suggestions(pref, limit=50)
        except Exception:
            sug = []
        for s in sug:
            lab = (s.get("label") or s.get("name") or "").strip()
            name = (s.get("name") or "").strip()
            if not lab or not name:
                continue
            cid = s.get("client_id")
            if cid is not None:
                try:
                    cid = int(cid)
                except (TypeError, ValueError):
                    cid = None
            qcid = s.get("quick_client_id")
            if qcid is not None:
                try:
                    qcid = int(qcid)
                except (TypeError, ValueError):
                    qcid = None
            if cid is not None:
                disp = lab + " · справочник"
            elif qcid is not None:
                disp = lab + " · быстрый просчёт"
            else:
                disp = lab
            it = QListWidgetItem(disp)
            it.setData(Qt.UserRole, cid)
            it.setData(Qt.UserRole + 1, qcid)
            it.setData(Qt.UserRole + 2, name)
            lst.addItem(it)

        try:
            from logic.keyboard_layout import install_client_search_layout_helper
        except ImportError:
            from MAIN_PROJECT.logic.keyboard_layout import install_client_search_layout_helper
        install_client_search_layout_helper(edit)
        edit.textChanged.connect(lambda _t: refresh())

    def on_new_quick():
        initial = (edit.text() or "").strip()
        meta = open_quick_client_create_dialog(parent, initial_name=initial)
        if meta:
            result_holder["meta"] = meta
            d.accept()

    def on_accept_row():
        it = lst.currentItem()
        if it is None:
            nm = (edit.text() or "").strip()
            if nm:
                cid = db_models.get_client_id_by_name(nm)
                if cid:
                    meta = db_models.quick_estimate_meta_from_client_id(int(cid))
                    if meta:
                        result_holder["meta"] = meta
                        d.accept()
                        return
            QMessageBox.information(
                d,
                "Клиент",
                "Выберите клиента в списке или нажмите «Новый (быстрый формат)».",
            )
            return
        cid = it.data(Qt.UserRole)
        qcid = it.data(Qt.UserRole + 1)
        name = (it.data(Qt.UserRole + 2) or "").strip()
        if cid is not None:
            try:
                cid = int(cid)
            except (TypeError, ValueError):
                cid = None
        if qcid is not None:
            try:
                qcid = int(qcid)
            except (TypeError, ValueError):
                qcid = None
        if cid is not None:
            meta = db_models.quick_estimate_meta_from_client_id(cid)
            if meta:
                result_holder["meta"] = meta
                d.accept()
            return
        if qcid is not None:
            meta = db_models.quick_estimate_meta_from_quick_client_id(qcid)
            if meta:
                result_holder["meta"] = meta
                d.accept()
            return
        meta = open_quick_client_create_dialog(parent, initial_name=name or (edit.text() or "").strip())
        if meta:
            result_holder["meta"] = meta
            d.accept()

    lst.itemDoubleClicked.connect(lambda _i: on_accept_row())

    row = QHBoxLayout()
    btn_new = QPushButton("Новый (быстрый формат)")
    btn_new.setStyleSheet("QPushButton { font-weight: bold; padding: 8px 12px; }")
    btn_new.clicked.connect(on_new_quick)
    row.addWidget(btn_new)
    row.addStretch()
    btn_ok = QPushButton("Далее")
    btn_ok.clicked.connect(on_accept_row)
    row.addWidget(btn_ok)
    btn_cancel = QPushButton("Отмена")
    btn_cancel.clicked.connect(d.reject)
    row.addWidget(btn_cancel)
    lay.addLayout(row)

    if d.exec_() != QDialog.Accepted:
        return None
    return result_holder["meta"]


def open_quick_estimate_transfer_client_dialog(parent, quick_row: dict):
    """Перед переводом quick->order: показываем текущие данные и даём выбрать/создать клиента."""
    d = QDialog(parent)
    d.setWindowTitle("Перевод в работу — клиент")
    d.setMinimumWidth(620)
    lay = QVBoxLayout(d)
    q = dict(quick_row or {})
    lay.addWidget(
        QLabel(
            "Проверьте данные и выберите клиента из базы "
            "или создайте нового клиента (обычный)."
        )
    )
    cur = QLabel(
        "Текущий клиент: <b>%s</b> · источник: %s · контакт: %s"
        % (
            (q.get("client_name") or "—"),
            (q.get("lead_source") or "—"),
            (q.get("contact_info") or "—"),
        )
    )
    cur.setWordWrap(True)
    lay.addWidget(cur)

    row = QHBoxLayout()
    edit_name = QLineEdit()
    edit_name.setPlaceholderText("Имя клиента…")
    row.addWidget(edit_name, 2)
    edit_phone = QLineEdit()
    edit_phone.setPlaceholderText("телефон…")
    row.addWidget(edit_phone, 1)
    edit_inn = QLineEdit()
    edit_inn.setPlaceholderText("ИНН…")
    row.addWidget(edit_inn, 1)
    lay.addLayout(row)

    edit_name.blockSignals(True)
    edit_phone.blockSignals(True)
    edit_inn.blockSignals(True)
    edit_name.setText((q.get("client_name") or "").strip())
    edit_phone.setText(_phone_prefill_for_search(q))
    edit_inn.setText("")
    edit_name.blockSignals(False)
    edit_phone.blockSignals(False)
    edit_inn.blockSignals(False)

    lst = QListWidget()
    lst.setMinimumHeight(240)
    lay.addWidget(lst)
    result_holder = {"meta": None}

    all_clients = db_models.get_all_clients() or []
    phone_hints = []
    inn_hints = []
    for c in all_clients:
        nm = str(c.get("name") or "").strip()
        ph = str(c.get("phone") or "").strip()
        inn = str(c.get("inn") or "").strip()
        if ph:
            phone_hints.append("%s · %s" % (ph, nm or "клиент"))
        if inn:
            inn_hints.append("%s · %s" % (inn, nm or "клиент"))
    cp = QCompleter(sorted(set(phone_hints)), edit_phone)
    cp.setCaseSensitivity(Qt.CaseInsensitive)
    cp.setFilterMode(Qt.MatchContains)
    cp.setCompletionMode(QCompleter.PopupCompletion)
    edit_phone.setCompleter(cp)
    ci = QCompleter(sorted(set(inn_hints)), edit_inn)
    ci.setCaseSensitivity(Qt.CaseInsensitive)
    ci.setFilterMode(Qt.MatchContains)
    ci.setCompletionMode(QCompleter.PopupCompletion)
    edit_inn.setCompleter(ci)

    preselect_state = {"done": False}

    def refresh():
        lst.clear()
        qn_raw = (edit_name.text() or "").strip()
        qn = qn_raw.lower()
        qp = (edit_phone.text() or "").strip().lower()
        qi = (edit_inn.text() or "").strip().lower()
        qpd = _digits_only(qp)
        qidg = _digits_only(qi)

        def row_matches(c, *, search_prefiltered_name: bool):
            """search_prefiltered_name=True — имя уже отфильтровано через get_clients_search (ILIKE)."""
            name = str(c.get("name") or "").strip()
            disp = (db_models._client_display_name(c) or name).strip()
            phone = str(c.get("phone") or "").strip()
            inn = str(c.get("inn") or "").strip()
            if not search_prefiltered_name and qn:
                low_name = name.lower()
                low_disp = disp.lower()
                if qn not in low_name and qn not in low_disp:
                    return False
            if len(qpd) >= 3:
                pd = _digits_only(phone)
                if qpd not in pd and qp not in phone.lower():
                    return False
            if len(qidg) >= 3:
                idd = _digits_only(inn)
                if qidg not in idd and qi not in inn.lower():
                    return False
            return True

        try:
            pin_id = int(q.get("client_id")) if q.get("client_id") is not None else None
        except (TypeError, ValueError):
            pin_id = None
        if pin_id is None and (q.get("client_name") or "").strip():
            try:
                pin_id = db_models.get_client_id_by_name((q.get("client_name") or "").strip())
            except Exception:
                pin_id = None
            if pin_id is not None:
                try:
                    pin_id = int(pin_id)
                except (TypeError, ValueError):
                    pin_id = None

        if qn_raw:
            try:
                base = list(db_models.get_clients_search(qn_raw) or [])
            except Exception:
                base = list(db_models.get_all_clients() or [])
            name_pref = True
        else:
            base = list(db_models.get_all_clients() or [])
            name_pref = False

        seen = set()
        ordered = []

        if pin_id:
            pin_row = db_models.get_client_by_id(pin_id)
            if pin_row:
                pin_ok = row_matches(pin_row, search_prefiltered_name=True)
                ordered.append((pin_row, not pin_ok))
                seen.add(pin_id)

        for c in base:
            cid = c.get("id")
            try:
                cid = int(cid) if cid is not None else None
            except (TypeError, ValueError):
                cid = None
            if cid in seen:
                continue
            if row_matches(c, search_prefiltered_name=name_pref):
                ordered.append((c, False))
                if cid is not None:
                    seen.add(cid)

        for c, pinned_mismatch in ordered:
            name = str(c.get("name") or "").strip()
            disp = (db_models._client_display_name(c) or name).strip()
            phone = str(c.get("phone") or "").strip()
            inn = str(c.get("inn") or "").strip()
            label = disp or name or "—"
            txt = "%s · тел: %s · ИНН: %s" % (label, phone or "—", inn or "—")
            if pinned_mismatch:
                txt = "★ %s — клиент из просчёта (не проходит фильтр)" % txt
            it = QListWidgetItem(txt)
            it.setData(Qt.UserRole, c.get("id"))
            lst.addItem(it)

        if not preselect_state["done"] and pin_id:
            preselect_state["done"] = True
            for i in range(lst.count()):
                it2 = lst.item(i)
                try:
                    rid = int(it2.data(Qt.UserRole))
                except (TypeError, ValueError):
                    continue
                if rid == pin_id:
                    lst.setCurrentRow(i)
                    lst.scrollToItem(it2)
                    break

    edit_name.textChanged.connect(lambda _t: refresh())
    edit_phone.textChanged.connect(lambda _t: refresh())
    edit_inn.textChanged.connect(lambda _t: refresh())
    refresh()

    def accept_selected():
        it = lst.currentItem()
        if it is None:
            QMessageBox.information(d, "Клиент", "Выберите клиента в списке или создайте нового.")
            return
        cid = it.data(Qt.UserRole)
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            cid = None
        if not cid:
            return
        meta = db_models.quick_estimate_meta_from_client_id(cid)
        if not meta:
            return
        result_holder["meta"] = meta
        d.accept()

    def create_new_normal():
        initial = (edit_name.text() or (q.get("client_name") or "")).strip()
        phone_guess = _phone_hint_from_quick_row(q, edit_phone.text())
        try:
            from ui._mirror_dialogs import _load_dialog
        except Exception:
            QMessageBox.warning(
                parent,
                "Клиент",
                "Не удалось открыть форму создания клиента.",
            )
            return
        saved_ui = sys.modules.pop("ui", None)
        try:
            NewClientDialog = _load_dialog("new_client_dialog", "NewClientDialog")
            if NewClientDialog is None:
                QMessageBox.warning(parent, "Клиент", "Модуль формы клиента не найден.")
                return
            try:
                mk = int(q.get("markup_percent") or 0)
            except (TypeError, ValueError):
                mk = 0
            dlg = NewClientDialog(
                parent,
                initial_name=initial,
                initial_phone=phone_guess,
                initial_source=(q.get("lead_source") or "").strip(),
                initial_markup_percent=mk,
            )
            if dlg.exec_() != QDialog.Accepted:
                return
            cid = dlg.get_saved_client_id() if hasattr(dlg, "get_saved_client_id") else None
            if not cid:
                return
            meta = db_models.quick_estimate_meta_from_client_id(int(cid))
            if not meta:
                return
            result_holder["meta"] = meta
            d.accept()
        finally:
            if saved_ui is not None:
                sys.modules["ui"] = saved_ui

    lst.itemDoubleClicked.connect(lambda _i: accept_selected())
    br = QHBoxLayout()
    b_new = QPushButton("Создать нового клиента")
    b_new.clicked.connect(create_new_normal)
    br.addWidget(b_new)
    br.addStretch()
    b_ok = QPushButton("Продолжить")
    b_ok.clicked.connect(accept_selected)
    br.addWidget(b_ok)
    b_cancel = QPushButton("Отмена")
    b_cancel.clicked.connect(d.reject)
    br.addWidget(b_cancel)
    lay.addLayout(br)
    if d.exec_() != QDialog.Accepted:
        return None
    return result_holder["meta"]
