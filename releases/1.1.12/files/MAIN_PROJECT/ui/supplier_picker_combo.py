# -*- coding: utf-8 -*-
"""Выпадающий список поставщиков с поиском по всем полям (кэш в памяти)."""
from __future__ import annotations

from PyQt5.QtWidgets import QComboBox, QCompleter
from PyQt5.QtCore import Qt, QStringListModel

from db import models as db_models


def _supplier_haystack(s):
    from ui.suppliers_dialog import SUPPLIER_TYPE_LABELS

    ctype = str(s.get("supplier_type") or "").strip().lower()
    parts = [
        str(s.get("name") or ""),
        str(s.get("phone") or ""),
        str(s.get("email") or ""),
        str(s.get("inn") or ""),
        str(s.get("kpp") or ""),
        str(s.get("okpo") or ""),
        str(s.get("ogrn") or ""),
        str(s.get("first_name") or ""),
        str(s.get("last_name") or ""),
        str(s.get("notes") or ""),
        SUPPLIER_TYPE_LABELS.get(ctype, ctype),
        str(s.get("id") or ""),
    ]
    return " ".join(parts).lower()


def _supplier_label(s):
    name = db_models._supplier_display_name(s) or s.get("name") or "—"
    inn = (s.get("inn") or "").strip()
    if inn:
        return "%s · ИНН %s" % (name, inn)
    return name


class SupplierPickerCombo(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []
        self._id_by_label = {}
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.setMaxVisibleItems(20)
        self._model = QStringListModel()
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self.setCompleter(self._completer)
        le = self.lineEdit()
        if le is not None:
            le.textEdited.connect(self._on_text_edited)
        self.reload()

    def reload(self):
        self._rows = db_models.get_all_suppliers() or []
        labels = []
        self._id_by_label = {}
        for s in self._rows:
            lab = _supplier_label(s)
            labels.append(lab)
            self._id_by_label[lab] = int(s["id"])
        self.blockSignals(True)
        self.clear()
        self.addItem("", None)
        for lab in sorted(labels, key=lambda x: x.lower()):
            self.addItem(lab, self._id_by_label[lab])
        self._model.setStringList(labels)
        self.blockSignals(False)

    def _on_text_edited(self, text):
        q = (text or "").strip().lower()
        tokens = [t for t in q.split() if t] if q else []
        q_digits = "".join(ch for ch in q if ch.isdigit())
        out = []
        for s in self._rows:
            hay = _supplier_haystack(s)
            ok = (not tokens) or all(t in hay for t in tokens)
            if not ok and len(q_digits) >= 3:
                inn_d = "".join(ch for ch in str(s.get("inn") or "") if ch.isdigit())
                ph_d = "".join(ch for ch in str(s.get("phone") or "") if ch.isdigit())
                ok = (q_digits in inn_d) or (q_digits in ph_d)
            if ok:
                out.append(_supplier_label(s))
        self._model.setStringList(sorted(set(out), key=lambda x: x.lower()))

    def supplier_id(self):
        data = self.currentData()
        if data is not None:
            try:
                return int(data)
            except (TypeError, ValueError):
                pass
        lab = (self.currentText() or "").strip()
        return self._id_by_label.get(lab)

    def set_supplier_id(self, supplier_id):
        if not supplier_id:
            self.setCurrentIndex(0)
            return
        sid = int(supplier_id)
        for i in range(self.count()):
            if self.itemData(i) == sid:
                self.setCurrentIndex(i)
                return

    def is_valid_selection(self):
        return self.supplier_id() is not None
