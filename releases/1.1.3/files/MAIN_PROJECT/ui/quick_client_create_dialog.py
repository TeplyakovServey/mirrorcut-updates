# -*- coding: utf-8 -*-
"""Unified quick client creation form for fast flows."""

from PyQt5.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox

from db import models as db_models

LEAD_SOURCES = ["авито", "сайт", "яндекс карточка", "ксу", "2gis", "мебельщик", "дизайнер"]


def _pricing_tier_from_markup(markup_percent: int) -> str:
    p = int(markup_percent or 0)
    if p == 30:
        return "b2c30"
    if p == 50:
        return "b2c50"
    return "b2b"


def open_quick_client_create_dialog(
    parent,
    *,
    initial_name: str = "",
    default_markup: int = 0,
    default_source: str = "",
    default_phone: str = "",
    default_extra_contact: str = "",
    save_to_quick_table_only: bool = False,
):
    d = QDialog(parent)
    d.setWindowTitle("Новый клиент (быстрый формат)")
    d.setMinimumWidth(540)
    fl = QFormLayout(d)

    name_edit = QLineEdit()
    name_edit.setText((initial_name or "").strip())
    name_edit.setPlaceholderText("Имя клиента")
    fl.addRow("Клиент*:", name_edit)

    markup_combo = QComboBox()
    markup_combo.addItem("+0%", 0)
    markup_combo.addItem("+30%", 30)
    markup_combo.addItem("+50%", 50)
    markup_combo.addItem("Другая", "custom")
    idx = 0
    dm = int(default_markup or 0)
    for i in range(markup_combo.count()):
        v = markup_combo.itemData(i)
        if isinstance(v, int) and int(v) == dm:
            idx = i
            break
    markup_combo.setCurrentIndex(idx)
    custom_markup = QSpinBox()
    custom_markup.setRange(0, 500)
    custom_markup.setValue(dm if dm not in (0, 30, 50) else 0)
    custom_markup.setVisible(markup_combo.currentData() == "custom")
    markup_combo.currentIndexChanged.connect(
        lambda _i: custom_markup.setVisible(markup_combo.currentData() == "custom")
    )
    fl.addRow("Наценка*:", markup_combo)
    fl.addRow("Своя наценка, %:", custom_markup)

    source_combo = QComboBox()
    for s in LEAD_SOURCES:
        source_combo.addItem(s, s)
    if (default_source or "").strip():
        pos = source_combo.findData((default_source or "").strip())
        if pos >= 0:
            source_combo.setCurrentIndex(pos)
    fl.addRow("Источник*:", source_combo)

    phone_edit = QLineEdit()
    phone_edit.setInputMask("+7 (000) 000-00-00;_")
    phone_edit.setText((default_phone or "").strip())
    phone_edit.setPlaceholderText("Телефон (необязательно)")
    fl.addRow("Телефон:", phone_edit)

    extra_edit = QLineEdit()
    extra_edit.setText((default_extra_contact or "").strip())
    extra_edit.setPlaceholderText("Другая контактная информация (необязательно)")
    fl.addRow("Другая связь:", extra_edit)

    bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    fl.addRow(bb)

    if d.exec_() != QDialog.Accepted:
        return None

    name = (name_edit.text() or "").strip()
    if not name:
        return None
    if markup_combo.currentData() == "custom":
        markup_percent = int(custom_markup.value() or 0)
    else:
        markup_percent = int(markup_combo.currentData() or 0)
    source = str(source_combo.currentData() or "").strip()
    phone = (phone_edit.text() or "").replace("_", "").strip()
    extra = (extra_edit.text() or "").strip()

    if save_to_quick_table_only:
        qid = db_models.insert_mirror_quick_client(
            name,
            phone=phone,
            extra_contact=extra,
            lead_source=source,
            markup_percent=int(markup_percent or 0),
        )
        return {
            "client_id": None,
            "quick_client_id": int(qid) if qid is not None else None,
            "client_name": name,
            "markup_percent": int(markup_percent),
            "lead_source": source,
            "phone": phone,
            "extra_contact": extra,
        }

    cid = db_models.get_client_id_by_name(name)
    if not cid:
        cid = db_models.insert_client_full(
            "legal",
            name,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            phone,
            "",
            "",
            "",
            source,
            extra,
            _pricing_tier_from_markup(markup_percent),
        )

    return {
        "client_id": int(cid),
        "quick_client_id": None,
        "client_name": name,
        "markup_percent": int(markup_percent),
        "lead_source": source,
        "phone": phone,
        "extra_contact": extra,
    }
