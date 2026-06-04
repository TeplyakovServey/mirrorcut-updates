# -*- coding: utf-8 -*-
"""Быстрые просчеты: черновик и перенос в работу."""
import json
import os
import sys
from datetime import datetime

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtCore import QDateTime
from PyQt5.QtWidgets import (
    QComboBox, QDateTimeEdit, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QListWidget, QListWidgetItem, QFormLayout, QDialogButtonBox,
    QSpinBox
)

from db import models as db_models
from ui.quick_client_create_dialog import open_quick_client_create_dialog

LEAD_SOURCES = ["авито", "сайт", "яндекс карточка", "ксу", "2gis", "мебельщик", "дизайнер"]


class QuickEstimateDialog(QDialog):
    def __init__(self, user, parent=None):
        super().__init__(parent)
        self._user = user or {}
        self._client_id = None
        self._rows = []
        self.setWindowTitle("Быстрый просчет")
        self.resize(980, 620)
        self._build_ui()
        self._load_rows()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        f1 = QHBoxLayout()
        self.category = QComboBox()
        self.category.addItem("Стекло / зеркало", "glass")
        self.category.addItem("Фасады", "facade")
        f1.addWidget(QLabel("Категория:"))
        f1.addWidget(self.category)
        self.markup = QComboBox()
        self.markup.addItem("+0%", 0)
        self.markup.addItem("+30%", 30)
        self.markup.addItem("+50%", 50)
        f1.addWidget(QLabel("Наценка:"))
        f1.addWidget(self.markup)
        self.lead = QComboBox()
        for s in LEAD_SOURCES:
            self.lead.addItem(s, s)
        f1.addWidget(QLabel("Источник:"))
        f1.addWidget(self.lead)
        lay.addLayout(f1)

        f2 = QHBoxLayout()
        self.client = QLineEdit()
        self.client.setPlaceholderText("Имя клиента")
        self.client.textChanged.connect(self._on_client_text)
        f2.addWidget(QLabel("Клиент*:"))
        f2.addWidget(self.client, 2)
        self.contact = QLineEdit()
        self.contact.setPlaceholderText("Телефон/контакт (необязательно)")
        f2.addWidget(QLabel("Контакт:"))
        f2.addWidget(self.contact, 2)
        self.estimate_dt = QDateTimeEdit()
        self.estimate_dt.setCalendarPopup(True)
        self.estimate_dt.setDateTime(QDateTime.currentDateTime())
        f2.addWidget(QLabel("Дата и время:"))
        f2.addWidget(self.estimate_dt)
        lay.addLayout(f2)

        self.client_list = QListWidget()
        self.client_list.setMaximumHeight(110)
        self.client_list.itemClicked.connect(self._pick_client)
        self.client_list.hide()
        lay.addWidget(self.client_list)

        btns = QHBoxLayout()
        bnew_client = QPushButton("Создать клиента")
        bnew_client.clicked.connect(self._new_client)
        btns.addWidget(bnew_client)
        bsave = QPushButton("Сохранить быстрый просчет")
        bsave.clicked.connect(self._save_estimate)
        btns.addWidget(bsave)
        btransfer = QPushButton("Добавить в работу")
        btransfer.clicked.connect(self._transfer_selected)
        btns.addWidget(btransfer)
        bcalc = QPushButton("Открыть калькулятор")
        bcalc.clicked.connect(self._open_calc)
        btns.addWidget(bcalc)
        btns.addStretch()
        lay.addLayout(btns)

        self.tbl = QTableWidget(0, 8)
        self.tbl.setHorizontalHeaderLabels(["ID", "Категория", "Клиент", "Источник", "Наценка", "Статус", "Автор", "Дата"])
        self.tbl.doubleClicked.connect(self._transfer_selected)
        lay.addWidget(self.tbl, 1)

    def _on_client_text(self, text):
        self._client_id = None
        pref = (text or "").strip()
        self.client_list.clear()
        if not pref:
            self.client_list.hide()
            return
        rows = db_models.get_clients_by_prefix(pref) or []
        for r in rows[:20]:
            self.client_list.addItem(QListWidgetItem(str(r.get("name") or "")))
        self.client_list.setVisible(bool(rows))

    def _pick_client(self, item):
        nm = (item.text() or "").strip()
        self.client.setText(nm)
        self.client_list.hide()
        self._client_id = db_models.get_client_id_by_name(nm)

    def _new_client(self):
        data = open_quick_client_create_dialog(
            self,
            initial_name=(self.client.text() or "").strip(),
            default_markup=int(self.markup.currentData() or 0),
            default_source=str(self.lead.currentData() or ""),
            default_extra_contact=(self.contact.text() or "").strip(),
        )
        if not data:
            QMessageBox.warning(self, "Клиент", "Введите имя клиента.")
            return
        self._client_id = int(data.get("client_id"))
        self.client.setText(str(data.get("client_name") or ""))
        self._set_markup_value(int(data.get("markup_percent") or 0))
        src = str(data.get("lead_source") or "")
        for i in range(self.lead.count()):
            if self.lead.itemData(i) == src:
                self.lead.setCurrentIndex(i)
                break
        phone_v = str(data.get("phone") or "").strip()
        extra_v = str(data.get("extra_contact") or "").strip()
        parts = []
        if phone_v:
            parts.append("Телефон: %s" % phone_v)
        if extra_v:
            parts.append("Контакт: %s" % extra_v)
        self.contact.setText("; ".join(parts))

    def _set_markup_value(self, percent: int):
        p = int(percent or 0)
        for i in range(self.markup.count()):
            if int(self.markup.itemData(i) or 0) == p:
                self.markup.setCurrentIndex(i)
                return
        self.markup.addItem("+%s%%" % p, p)
        self.markup.setCurrentIndex(self.markup.count() - 1)

    def _ensure_client(self):
        name = (self.client.text() or "").strip()
        if not name:
            QMessageBox.warning(self, "Клиент", "Укажите имя клиента.")
            return None, None
        cid = self._client_id or db_models.get_client_id_by_name(name)
        if not cid:
            cid = db_models.insert_client(name)
        self._client_id = int(cid)
        return int(cid), name

    def _save_estimate(self):
        cid, name = self._ensure_client()
        if not cid:
            return
        payload = {
            "category": self.category.currentData(),
            "markup_percent": int(self.markup.currentData() or 0),
            "lead_source": self.lead.currentData(),
            "contact_info": (self.contact.text() or "").strip(),
        }
        qid = db_models.create_quick_estimate(
            category=self.category.currentData(),
            client_id=cid,
            client_name=name,
            lead_source=self.lead.currentData(),
            contact_info=(self.contact.text() or "").strip(),
            markup_percent=int(self.markup.currentData() or 0),
            estimate_at=self.estimate_dt.dateTime().toPyDateTime(),
            created_by_user_id=self._user.get("id"),
            created_by_login=self._user.get("login"),
            created_by_role=self._user.get("role"),
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
        if not qid:
            QMessageBox.warning(self, "Быстрый просчет", "Не удалось сохранить.")
            return
        self._load_rows()

    def _load_rows(self):
        self._rows = list(db_models.list_quick_estimates() or [])
        self.tbl.setRowCount(len(self._rows))
        for i, r in enumerate(self._rows):
            self.tbl.setItem(i, 0, QTableWidgetItem(str(r.get("id") or "")))
            self.tbl.item(i, 0).setData(0x0100, r.get("id"))
            self.tbl.setItem(i, 1, QTableWidgetItem("Стекло / зеркало" if r.get("category") == "glass" else "Фасады"))
            self.tbl.setItem(i, 2, QTableWidgetItem(str(r.get("client_name") or "—")))
            self.tbl.setItem(i, 3, QTableWidgetItem(str(r.get("lead_source") or "")))
            self.tbl.setItem(i, 4, QTableWidgetItem("+%s%%" % int(r.get("markup_percent") or 0)))
            self.tbl.setItem(i, 5, QTableWidgetItem(str(r.get("status") or "")))
            self.tbl.setItem(i, 6, QTableWidgetItem(str(r.get("created_by_login") or "")))
            dt = r.get("estimate_at")
            dts = dt.strftime("%d.%m.%Y %H:%M") if hasattr(dt, "strftime") else str(dt or "")
            self.tbl.setItem(i, 7, QTableWidgetItem(dts))

    def _selected_qe_id(self):
        r = self.tbl.currentRow()
        if r < 0:
            return None
        it = self.tbl.item(r, 0)
        if not it:
            return None
        return it.data(0x0100)

    def _transfer_selected(self):
        qid = self._selected_qe_id()
        if not qid:
            QMessageBox.information(self, "Быстрый просчет", "Выберите запись в таблице.")
            return
        try:
            oid = db_models.transfer_quick_estimate_to_order(int(qid))
        except Exception as e:
            QMessageBox.warning(self, "Перенос", str(e))
            return
        self._load_rows()

    def _open_calc(self):
        cat = self.category.currentData()
        if cat == "facade":
            from ui.facade_order_dialog import FacadeOrderDialog
            FacadeOrderDialog(self).exec_()
            return
        from ui.glass_mirror_calc_dialog import GlassMirrorCalcDialog
        GlassMirrorCalcDialog(self, order_id=None, append_new=True).exec_()
