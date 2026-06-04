"""Диалог создания/редактирования клиента: юр. лицо, ИП, физ. лицо. Вкладка «Документ» для ИП и физ. лица."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFormLayout, QMessageBox, QTextEdit, QGroupBox,
    QRadioButton, QButtonGroup, QStackedWidget, QWidget, QScrollArea,
    QComboBox, QDateEdit, QFrame,
)
from PyQt5.QtCore import Qt, QDate, QRegExp
from PyQt5.QtGui import QIntValidator, QRegExpValidator

from db import models

# Типы клиентов
TYPE_LEGAL = 'legal'      # Юридическое лицо
TYPE_IP = 'ip'           # Индивидуальный предприниматель
TYPE_INDIVIDUAL = 'individual'  # Физическое лицо
PRICING_B2B = 'b2b'
PRICING_B2C30 = 'b2c30'
PRICING_B2C50 = 'b2c50'

# ИНН: 10 цифр (юр. лицо) или 12 (ИП / физ. лицо)
INN_LENS = (10, 12)
KPP_LEN = 9
OKPO_LEN = 8
OGRN_LEN = 13


def _digits_validator(max_len):
    """Валидатор: только цифры, не более max_len (QIntValidator не подходит для >10 цифр)."""
    return QRegExpValidator(QRegExp(r'\d{0,%d}' % max_len))


def _inn_valid(inn: str) -> bool:
    s = (inn or "").strip()
    return s.isdigit() and len(s) in INN_LENS


def _normalize_ru_phone_digits(text):
    """Только цифры; 8… → 7…; если нет кода страны — добавляем 7; максимум 11 (7 + 10)."""
    d = "".join(c for c in (text or "") if c.isdigit())
    if not d:
        return ""
    if d[0] == "8":
        d = "7" + d[1:]
    elif d[0] != "7":
        d = "7" + d[:10]
    return d[:11]


def _format_ru_phone_display(digits):
    """Отображение: +7 (XXX) XXX-XX-XX; неполный ввод — по мере набора."""
    if not digits:
        return ""
    if digits[0] != "7":
        digits = _normalize_ru_phone_digits(digits)
        if not digits:
            return ""
    body = digits[1:]
    if not body:
        return "+7"
    if len(body) <= 3:
        return "+7 (%s%s" % (body, ")" if len(body) == 3 else "")
    if len(body) <= 6:
        return "+7 (%s) %s" % (body[:3], body[3:])
    if len(body) <= 8:
        return "+7 (%s) %s-%s" % (body[:3], body[3:6], body[6:])
    return "+7 (%s) %s-%s-%s" % (body[:3], body[3:6], body[6:8], body[8:10])


class RUPhoneLineEdit(QLineEdit):
    """Телефон РФ: формат +7 (…) при вводе; в БД сохраняем +7 и 10 цифр."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("+7 (999) 123-45-67")
        self._fmt_block = False
        self.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, _t):
        if self._fmt_block:
            return
        digits = _normalize_ru_phone_digits(self.text())
        shown = _format_ru_phone_display(digits)
        if shown == self.text():
            return
        self._fmt_block = True
        try:
            self.setText(shown)
            self.setCursorPosition(len(shown))
        finally:
            self._fmt_block = False

    def set_phone_from_storage(self, value):
        """Заполнить из БД (любая строка с цифрами)."""
        self._fmt_block = True
        try:
            digits = _normalize_ru_phone_digits(str(value or ""))
            self.setText(_format_ru_phone_display(digits))
        finally:
            self._fmt_block = False

    def phone_for_save(self):
        """Строка для сохранения: +79991234567 или пусто."""
        d = _normalize_ru_phone_digits(self.text())
        if len(d) == 11 and d.startswith("7"):
            return "+7%s" % d[1:]
        return (self.text() or "").strip()


class NewClientDialog(QDialog):
    """Форма клиента по типу: юр. лицо (ИНН 12, КПП 9), ИП (вкладка Документ: Регистрация, КПП, ОКПО, ОГРН), физ. лицо (вкладка Документ: ФИО, ИНН 12, паспорт, дата рождения, пол)."""

    def __init__(
        self,
        parent=None,
        initial_name="",
        edit_client=None,
        initial_phone="",
        initial_source="",
        initial_markup_percent=None,
        entity="client",
        edit_supplier=None,
    ):
        super().__init__(parent)
        self._entity = (entity or "client").strip().lower()
        self._is_supplier = self._entity == "supplier"
        self.edit_client = edit_client
        self.edit_supplier = edit_supplier
        if self._is_supplier and edit_supplier and not edit_client:
            self.edit_client = edit_supplier
        if self._is_supplier:
            self.setWindowTitle("Редактировать поставщика" if edit_supplier else "Новый поставщик")
        else:
            self.setWindowTitle("Редактировать клиента" if edit_client else "Новый клиент")
        self.setMinimumWidth(480)
        self.setMinimumHeight(520)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content_layout = QVBoxLayout(content)

        # --- Тип клиента (для поставщика всегда юр. лицо, блок не показываем) ---
        self.type_group = QButtonGroup(self)
        self.radio_legal = QRadioButton("Юридическое лицо")
        self.radio_ip = QRadioButton("Индивидуальный предприниматель (ИП)")
        self.radio_individual = QRadioButton("Физическое лицо")
        self.type_group.addButton(self.radio_legal)
        self.type_group.addButton(self.radio_ip)
        self.type_group.addButton(self.radio_individual)
        if not self._is_supplier:
            type_grp = QGroupBox("Тип клиента")
            type_layout = QVBoxLayout(type_grp)
            type_layout.addWidget(self.radio_legal)
            type_layout.addWidget(self.radio_ip)
            type_layout.addWidget(self.radio_individual)
            content_layout.addWidget(type_grp)

        # --- Ценовой сегмент ---
        pricing_grp = QGroupBox("Сегмент ценообразования")
        pricing_layout = QVBoxLayout(pricing_grp)
        self.pricing_group = QButtonGroup(self)
        self.radio_b2b = QRadioButton("B2B (без наценки)")
        self.radio_b2c30 = QRadioButton("B2C 30 (+30%)")
        self.radio_b2c50 = QRadioButton("B2C 50 (+50%)")
        self.pricing_group.addButton(self.radio_b2b)
        self.pricing_group.addButton(self.radio_b2c30)
        self.pricing_group.addButton(self.radio_b2c50)
        pricing_layout.addWidget(self.radio_b2b)
        pricing_layout.addWidget(self.radio_b2c30)
        pricing_layout.addWidget(self.radio_b2c50)
        self.radio_b2b.setChecked(True)
        self._pricing_grp = pricing_grp
        if not self._is_supplier:
            content_layout.addWidget(pricing_grp)

        # --- Основные поля (общие / юр. лицо) ---
        self.main_grp = QGroupBox("Основные данные")
        self.main_form = QFormLayout(self.main_grp)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Наименование организации")
        self.name_edit.setText(initial_name)
        self.main_form.addRow("Наименование (обяз.):", self.name_edit)
        self._lbl_name = self.main_form.labelForField(self.name_edit)

        # Юр. лицо / ИП: ИНН 12 цифр; юр. лицо ещё КПП 9
        self.inn_edit = QLineEdit()
        self.inn_edit.setPlaceholderText("10 или 12 цифр, обязательно")
        self.inn_edit.setMaxLength(12)
        self.inn_edit.setValidator(_digits_validator(12))
        self.main_form.addRow("ИНН (10–12 цифр, обяз.):", self.inn_edit)
        self.kpp_edit = QLineEdit()
        self.kpp_edit.setPlaceholderText("9 цифр")
        self.kpp_edit.setMaxLength(9)
        self.kpp_edit.setValidator(QIntValidator(0, 999999999))
        self.main_form.addRow("КПП (9 цифр, обяз.):", self.kpp_edit)

        content_layout.addWidget(self.main_grp)

        # --- Блок «Документ»: показывается только для ИП или физ. лица; для юр. лица скрыт ---
        self.doc_stack = QStackedWidget()
        # Страница ИП: Регистрация, КПП, ОКПО, ОГРН
        self.doc_ip = QWidget()
        self.doc_ip_layout = QFormLayout(self.doc_ip)
        self.registration_edit = QLineEdit()
        self.registration_edit.setPlaceholderText("По умолчанию: Россия")
        self.registration_edit.setText("Россия")
        self.doc_ip_layout.addRow("Регистрация (обяз.):", self.registration_edit)
        self.ip_kpp_edit = QLineEdit()
        self.ip_kpp_edit.setPlaceholderText("9 цифр")
        self.ip_kpp_edit.setMaxLength(9)
        self.ip_kpp_edit.setValidator(QIntValidator(0, 999999999))
        self.doc_ip_layout.addRow("КПП (9 цифр, обяз.):", self.ip_kpp_edit)
        self.okpo_edit = QLineEdit()
        self.okpo_edit.setPlaceholderText("8 цифр")
        self.okpo_edit.setMaxLength(8)
        self.okpo_edit.setValidator(QIntValidator(0, 99999999))
        self.doc_ip_layout.addRow("ОКПО (8 цифр, обяз.):", self.okpo_edit)
        self.ogrn_edit = QLineEdit()
        self.ogrn_edit.setPlaceholderText("13 цифр")
        self.ogrn_edit.setMaxLength(13)
        self.ogrn_edit.setValidator(_digits_validator(13))
        self.doc_ip_layout.addRow("ОГРН (13 цифр, обяз.):", self.ogrn_edit)
        self.doc_stack.addWidget(self.doc_ip)

        # Страница физ. лица: ФИО, ИНН 12, паспорт, дата рождения, пол
        self.doc_individual = QWidget()
        self.doc_ind_layout = QFormLayout(self.doc_individual)
        self.last_name_edit = QLineEdit()
        self.doc_ind_layout.addRow("Фамилия (обяз.):", self.last_name_edit)
        self.first_name_edit = QLineEdit()
        self.doc_ind_layout.addRow("Имя (обяз.):", self.first_name_edit)
        self.inn12_edit = QLineEdit()
        self.inn12_edit.setPlaceholderText("10 или 12 цифр")
        self.inn12_edit.setMaxLength(12)
        self.inn12_edit.setValidator(_digits_validator(12))
        self.doc_ind_layout.addRow("ИНН (10–12 цифр):", self.inn12_edit)
        self.passport_series_edit = QLineEdit()
        self.passport_series_edit.setMaxLength(4)
        self.passport_series_edit.setPlaceholderText("4 цифры")
        self.passport_series_edit.setValidator(QRegExpValidator(QRegExp(r"\d{4}")))
        self.doc_ind_layout.addRow("Паспорт, серия (4 цифры):", self.passport_series_edit)
        self.passport_number_edit = QLineEdit()
        self.passport_number_edit.setMaxLength(6)
        self.passport_number_edit.setPlaceholderText("6 цифр")
        self.passport_number_edit.setValidator(QRegExpValidator(QRegExp(r"\d{6}")))
        self.doc_ind_layout.addRow("Паспорт, номер (6 цифр):", self.passport_number_edit)
        self.birth_date_edit = QDateEdit()
        self.birth_date_edit.setCalendarPopup(True)
        self.birth_date_edit.setDate(QDate(1990, 1, 1))
        self.birth_date_edit.setSpecialValueText("—")
        self.doc_ind_layout.addRow("Дата рождения:", self.birth_date_edit)
        self.gender_combo = QComboBox()
        self.gender_combo.addItem("—", None)
        self.gender_combo.addItem("Мужской", "male")
        self.gender_combo.addItem("Женский", "female")
        self.doc_ind_layout.addRow("Пол:", self.gender_combo)
        self.doc_stack.addWidget(self.doc_individual)

        self.doc_stack.setVisible(False)
        content_layout.addWidget(self.doc_stack)

        # --- Контакты и адреса ---
        self.contact_grp = QGroupBox("Контакты и адреса")
        self.contact_form = QFormLayout(self.contact_grp)
        self.phone_edit = RUPhoneLineEdit()
        self.contact_form.addRow("Телефон (обяз.):", self.phone_edit)
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("Необязательно")
        self.contact_form.addRow("Email (необяз.):", self.email_edit)
        self.legal_address_edit = QLineEdit()
        self.contact_form.addRow("Юридический адрес (обяз.):", self.legal_address_edit)
        self.actual_address_edit = QLineEdit()
        self.contact_form.addRow("Фактический адрес (обяз.):", self.actual_address_edit)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("Необязательно")
        self.contact_form.addRow("Источник (необяз.):", self.source_edit)
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Заметки (необяз.)")
        self.notes_edit.setMaximumHeight(70)
        self.contact_form.addRow("Примечания:", self.notes_edit)
        content_layout.addWidget(self.contact_grp)
        self._lbl_legal_addr = self.contact_form.labelForField(self.legal_address_edit)
        self._lbl_actual_addr = self.contact_form.labelForField(self.actual_address_edit)

        self.bank_grp = QGroupBox("Банковские реквизиты")
        self.bank_form = QFormLayout(self.bank_grp)
        self.bank_account_edit = QLineEdit()
        self.bank_account_edit.setMaxLength(20)
        self.bank_account_edit.setValidator(_digits_validator(20))
        self.bank_account_edit.setPlaceholderText("20 цифр")
        self.bank_form.addRow("Р/с:", self.bank_account_edit)
        self.corr_account_edit = QLineEdit()
        self.corr_account_edit.setMaxLength(20)
        self.corr_account_edit.setValidator(_digits_validator(20))
        self.corr_account_edit.setPlaceholderText("20 цифр")
        self.bank_form.addRow("К/с:", self.corr_account_edit)
        self.bank_name_edit = QLineEdit()
        self.bank_form.addRow("Банк:", self.bank_name_edit)
        self.bik_edit = QLineEdit()
        self.bik_edit.setMaxLength(9)
        self.bik_edit.setValidator(_digits_validator(9))
        self.bik_edit.setPlaceholderText("9 цифр")
        self.bank_form.addRow("БИК:", self.bik_edit)
        self.bank_grp.setVisible(self._is_supplier)
        content_layout.addWidget(self.bank_grp)

        # Дата регистрации (только для отображения при редактировании)
        self.reg_date_label = QLabel("")
        self.reg_date_label.setStyleSheet("color: #666; font-size: 11px;")
        content_layout.addWidget(self.reg_date_label)

        content_layout.addStretch()
        layout.addWidget(scroll)
        scroll.setWidget(content)

        if not self._is_supplier:
            self.type_group.buttonClicked.connect(self._on_type_changed)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_save = QPushButton("Сохранить")
        btn_save.setDefault(True)
        btn_save.setAutoDefault(True)
        btn_save.setStyleSheet(
            "QPushButton { background-color: #2E7D32; color: white; border: none; border-radius: 6px; "
            "padding: 8px 20px; font-weight: bold; min-width: 120px; }"
            "QPushButton:hover { background-color: #388E3C; }"
            "QPushButton:pressed { background-color: #1B5E20; }"
        )
        btn_save.clicked.connect(self._save)
        btn_cancel = QPushButton("Отмена")
        btn_cancel.setStyleSheet(
            "QPushButton { background-color: #C62828; color: white; border: none; border-radius: 6px; "
            "padding: 8px 20px; font-weight: bold; min-width: 120px; }"
            "QPushButton:hover { background-color: #B71C1C; }"
            "QPushButton:pressed { background-color: #8E0000; }"
        )
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_save)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

        if edit_client:
            self._load_client(edit_client)
        else:
            self.radio_legal.setChecked(True)
            ip = (initial_phone or "").strip()
            if ip:
                self.phone_edit.set_phone_from_storage(ip)
            isrc = (initial_source or "").strip()
            if isrc:
                self.source_edit.setText(isrc)
            if not self._is_supplier and initial_markup_percent is not None:
                try:
                    mp = int(initial_markup_percent)
                except (TypeError, ValueError):
                    mp = 0
                if mp == 30:
                    self.radio_b2c30.setChecked(True)
                elif mp == 50:
                    self.radio_b2c50.setChecked(True)
                else:
                    self.radio_b2b.setChecked(True)
        if self._is_supplier:
            self.radio_legal.setChecked(True)
            self._on_type_changed()
        else:
            self._on_type_changed()

    def _phone_valid(self):
        d = _normalize_ru_phone_digits(self.phone_edit.text())
        return len(d) == 11 and d.startswith("7")

    def _current_type(self):
        if self._is_supplier:
            return TYPE_LEGAL
        if self.radio_legal.isChecked():
            return TYPE_LEGAL
        if self.radio_ip.isChecked():
            return TYPE_IP
        return TYPE_INDIVIDUAL

    def _on_type_changed(self):
        t = self._current_type()
        self.main_grp.setTitle("Основные данные" if t == TYPE_LEGAL else "Наименование / данные")
        if t == TYPE_LEGAL:
            self.name_edit.setVisible(True)
            if self._lbl_name:
                self._lbl_name.setVisible(True)
            self.main_form.labelForField(self.inn_edit).setText("ИНН (10–12 цифр):")
            self.main_form.labelForField(self.inn_edit).setVisible(True)
            self.inn_edit.setVisible(True)
            self.inn_edit.setMaxLength(12)
            self.kpp_edit.setVisible(True)
            self.main_form.labelForField(self.kpp_edit).setVisible(True)
            self.doc_stack.setVisible(False)
            self.name_edit.setPlaceholderText("Наименование организации")
            if self._lbl_legal_addr:
                self._lbl_legal_addr.setText("Юридический адрес (обяз.):")
                self._lbl_legal_addr.setVisible(True)
            self.legal_address_edit.setVisible(True)
            if self._lbl_actual_addr:
                self._lbl_actual_addr.setText("Фактический адрес (обяз.):")
                self._lbl_actual_addr.setVisible(True)
            self.actual_address_edit.setVisible(True)
            self.actual_address_edit.setPlaceholderText("")
            if self._lbl_name:
                self._lbl_name.setText("Наименование (обяз.):")
        elif t == TYPE_IP:
            self.name_edit.setVisible(True)
            if self._lbl_name:
                self._lbl_name.setVisible(True)
            self.main_form.labelForField(self.inn_edit).setText("ИНН (10–12 цифр):")
            self.main_form.labelForField(self.inn_edit).setVisible(True)
            self.inn_edit.setVisible(True)
            self.inn_edit.setMaxLength(12)
            self.kpp_edit.setVisible(False)
            self.main_form.labelForField(self.kpp_edit).setVisible(False)
            if not (self.registration_edit.text() or "").strip():
                self.registration_edit.setText("Россия")
            self.doc_stack.setVisible(True)
            self.doc_stack.setCurrentIndex(0)
            self.name_edit.setPlaceholderText("Наименование (ИП)")
            if self._lbl_legal_addr:
                self._lbl_legal_addr.setText("Юридический адрес (обяз.):")
                self._lbl_legal_addr.setVisible(True)
            self.legal_address_edit.setVisible(True)
            if self._lbl_actual_addr:
                self._lbl_actual_addr.setText("Фактический адрес (обяз.):")
                self._lbl_actual_addr.setVisible(True)
            self.actual_address_edit.setVisible(True)
            self.actual_address_edit.setPlaceholderText("")
            if self._lbl_name:
                self._lbl_name.setText("Наименование (обяз.):")
        else:
            self.main_form.labelForField(self.inn_edit).setVisible(False)
            self.inn_edit.setVisible(False)
            self.main_form.labelForField(self.kpp_edit).setVisible(False)
            self.kpp_edit.setVisible(False)
            self.doc_stack.setVisible(True)
            self.doc_stack.setCurrentIndex(1)
            self.main_grp.setTitle("Физ. лицо — ФИО и документ")
            self.name_edit.clear()
            self.name_edit.setPlaceholderText("")
            self.name_edit.setVisible(False)
            if self._lbl_name:
                self._lbl_name.setVisible(False)
            if self._lbl_legal_addr:
                self._lbl_legal_addr.setVisible(False)
            self.legal_address_edit.setVisible(False)
            self.legal_address_edit.clear()
            if self._lbl_actual_addr:
                self._lbl_actual_addr.setText("Адрес (необяз.):")
                self._lbl_actual_addr.setVisible(True)
            self.actual_address_edit.setVisible(True)
            self.actual_address_edit.setPlaceholderText("По желанию: адрес проживания или доставки")

    def _load_client(self, c):
        if self._is_supplier:
            self.radio_legal.setChecked(True)
            self._on_type_changed()
        else:
            t = c.get('supplier_type') or c.get('client_type') or 'legal'
            if t == TYPE_LEGAL:
                self.radio_legal.setChecked(True)
            elif t == TYPE_IP:
                self.radio_ip.setChecked(True)
            else:
                self.radio_individual.setChecked(True)
            self._on_type_changed()
        self.name_edit.setText(c.get('name') or '')
        self.inn_edit.setText(str(c.get('inn') or '') if c.get('inn') else '')
        self.kpp_edit.setText(str(c.get('kpp') or '') if c.get('kpp') else '')
        self.registration_edit.setText(c.get('registration') or '')
        self.ip_kpp_edit.setText(str(c.get('kpp') or '') if c.get('kpp') else '')
        self.okpo_edit.setText(str(c.get('okpo') or '') if c.get('okpo') else '')
        self.ogrn_edit.setText(str(c.get('ogrn') or '') if c.get('ogrn') else '')
        self.first_name_edit.setText(c.get('first_name') or '')
        self.last_name_edit.setText(c.get('last_name') or '')
        self.inn12_edit.setText(str(c.get('inn') or '') if c.get('inn') else '')
        self.passport_series_edit.setText(c.get('passport_series') or '')
        self.passport_number_edit.setText(c.get('passport_number') or '')
        bd = c.get('birth_date')
        if bd:
            try:
                from datetime import date
                if hasattr(bd, 'year'):
                    self.birth_date_edit.setDate(QDate(bd.year, bd.month, bd.day))
                else:
                    self.birth_date_edit.setDate(QDate(1990, 1, 1))
            except Exception:
                pass
        g = c.get('gender')
        for i in range(self.gender_combo.count()):
            if self.gender_combo.itemData(i) == g:
                self.gender_combo.setCurrentIndex(i)
                break
        self.phone_edit.set_phone_from_storage(c.get('phone') or '')
        self.email_edit.setText(c.get('email') or '')
        self.actual_address_edit.setText(c.get('actual_address') or '')
        if (c.get('client_type') or 'legal') == TYPE_INDIVIDUAL:
            self.legal_address_edit.clear()
        else:
            self.legal_address_edit.setText(c.get('legal_address') or '')
        self.source_edit.setText(c.get('source') or '')
        self.notes_edit.setPlainText(c.get('notes') or '')
        if self._is_supplier:
            self.bank_account_edit.setText(str(c.get('bank_account') or ''))
            self.corr_account_edit.setText(str(c.get('corr_account') or ''))
            self.bank_name_edit.setText(str(c.get('bank_name') or ''))
            self.bik_edit.setText(str(c.get('bik') or ''))
        rd = c.get('registration_date')
        if rd:
            self.reg_date_label.setText("Дата регистрации: %s" % (rd.strftime("%d.%m.%Y %H:%M") if hasattr(rd, 'strftime') else str(rd)))
        if not self._is_supplier:
            pt = str(c.get('pricing_tier') or PRICING_B2B).strip().lower()
            if pt == PRICING_B2C30:
                self.radio_b2c30.setChecked(True)
            elif pt == PRICING_B2C50:
                self.radio_b2c50.setChecked(True)
            else:
                self.radio_b2b.setChecked(True)

    def _current_pricing_tier(self):
        if self.radio_b2c30.isChecked():
            return PRICING_B2C30
        if self.radio_b2c50.isChecked():
            return PRICING_B2C50
        return PRICING_B2B

    def _validate_legal(self):
        name = (self.name_edit.text() or "").strip()
        inn = (self.inn_edit.text() or "").strip()
        kpp = (self.kpp_edit.text() or "").strip()
        legal = (self.legal_address_edit.text() or "").strip()
        actual = (self.actual_address_edit.text() or "").strip()
        if not name:
            QMessageBox.warning(self, "Ошибка", "Введите наименование организации.")
            return False
        if len(inn) not in INN_LENS or not _inn_valid(inn):
            QMessageBox.warning(self, "Ошибка", "ИНН юридического лица — 10 или 12 цифр.")
            return False
        if len(kpp) != KPP_LEN:
            QMessageBox.warning(self, "Ошибка", "КПП — 9 цифр.")
            return False
        if not self._phone_valid():
            QMessageBox.warning(
                self,
                "Ошибка",
                "Введите полный номер телефона: +7 и 10 цифр (можно начать с 8).",
            )
            return False
        if not legal:
            QMessageBox.warning(self, "Ошибка", "Введите юридический адрес.")
            return False
        if not actual:
            QMessageBox.warning(self, "Ошибка", "Введите фактический адрес.")
            return False
        return True

    def _validate_ip(self):
        name = (self.name_edit.text() or "").strip()
        inn = (self.inn_edit.text() or "").strip()
        reg = (self.registration_edit.text() or "").strip()
        kpp = (self.ip_kpp_edit.text() or "").strip()
        okpo = (self.okpo_edit.text() or "").strip()
        ogrn = (self.ogrn_edit.text() or "").strip()
        legal = (self.legal_address_edit.text() or "").strip()
        actual = (self.actual_address_edit.text() or "").strip()
        if not name:
            QMessageBox.warning(self, "Ошибка", "Введите наименование (ИП).")
            return False
        if len(inn) not in INN_LENS or not _inn_valid(inn):
            QMessageBox.warning(self, "Ошибка", "ИНН ИП — 10 или 12 цифр.")
            return False
        if not reg:
            QMessageBox.warning(self, "Ошибка", "Заполните поле «Регистрация».")
            return False
        if len(kpp) != KPP_LEN:
            QMessageBox.warning(self, "Ошибка", "КПП — 9 цифр.")
            return False
        if len(okpo) != OKPO_LEN:
            QMessageBox.warning(self, "Ошибка", "ОКПО — 8 цифр.")
            return False
        if len(ogrn) != OGRN_LEN:
            QMessageBox.warning(self, "Ошибка", "ОГРН — 13 цифр.")
            return False
        if not self._phone_valid():
            QMessageBox.warning(
                self,
                "Ошибка",
                "Введите полный номер телефона: +7 и 10 цифр (можно начать с 8).",
            )
            return False
        if not legal:
            QMessageBox.warning(self, "Ошибка", "Введите юридический адрес.")
            return False
        if not actual:
            QMessageBox.warning(self, "Ошибка", "Введите фактический адрес.")
            return False
        return True

    def _validate_individual(self):
        first = (self.first_name_edit.text() or "").strip()
        last = (self.last_name_edit.text() or "").strip()
        inn = (self.inn12_edit.text() or "").strip()
        passport_s = (self.passport_series_edit.text() or "").strip()
        passport_n = (self.passport_number_edit.text() or "").strip()
        if not last:
            QMessageBox.warning(self, "Ошибка", "Укажите фамилию.")
            return False
        if not first:
            QMessageBox.warning(self, "Ошибка", "Укажите имя.")
            return False
        if inn and not _inn_valid(inn):
            QMessageBox.warning(self, "Ошибка", "ИНН физического лица — 10 или 12 цифр.")
            return False
        if len(passport_s) != 4 or not passport_s.isdigit():
            QMessageBox.warning(self, "Ошибка", "Серия паспорта — ровно 4 цифры.")
            return False
        if len(passport_n) != 6 or not passport_n.isdigit():
            QMessageBox.warning(self, "Ошибка", "Номер паспорта — ровно 6 цифр.")
            return False
        if not self._phone_valid():
            QMessageBox.warning(
                self,
                "Ошибка",
                "Введите полный номер телефона: +7 и 10 цифр (можно начать с 8).",
            )
            return False
        return True

    def _save(self):
        if self._is_supplier:
            if not self._validate_legal():
                return
            t = TYPE_LEGAL
        else:
            t = self._current_type()
            if t == TYPE_LEGAL and not self._validate_legal():
                return
            if t == TYPE_IP and not self._validate_ip():
                return
            if t == TYPE_INDIVIDUAL and not self._validate_individual():
                return

        def str_or_none(e):
            s = (e.text() if hasattr(e, 'text') else e).strip() if e else ''
            return s or None
        def date_or_none(de):
            qd = de.date()
            if qd.isValid():
                return qd.toPyDate()
            return None

        name_org = (self.name_edit.text() or "").strip()
        inn = (self.inn_edit.text() or "").strip() or (self.inn12_edit.text() or "").strip()
        kpp = (self.kpp_edit.text() or "").strip() or (self.ip_kpp_edit.text() or "").strip()
        registration = (self.registration_edit.text() or "").strip() or None
        okpo = (self.okpo_edit.text() or "").strip() or None
        ogrn = (self.ogrn_edit.text() or "").strip() or None
        fn = (self.first_name_edit.text() or "").strip()
        ln = (self.last_name_edit.text() or "").strip()
        if t == TYPE_INDIVIDUAL:
            name = ("%s %s" % (ln, fn)).strip()
            first_name = fn or None
            last_name = ln or None
            passport_series = (self.passport_series_edit.text() or "").strip() or None
            passport_number = (self.passport_number_edit.text() or "").strip() or None
            birth_date = date_or_none(self.birth_date_edit)
            gender = self.gender_combo.currentData()
        else:
            name = name_org
            first_name = None
            last_name = None
            passport_series = None
            passport_number = None
            birth_date = None
            gender = None
        phone = self.phone_edit.phone_for_save()
        email = (self.email_edit.text() or "").strip()
        if t == TYPE_INDIVIDUAL:
            legal_address = ""
            actual_address = (self.actual_address_edit.text() or "").strip()
        else:
            legal_address = (self.legal_address_edit.text() or "").strip()
            actual_address = (self.actual_address_edit.text() or "").strip()
        source = str_or_none(self.source_edit) if self.source_edit else None
        notes = (self.notes_edit.toPlainText() or "").strip() or None
        bank_account = (self.bank_account_edit.text() or "").strip() or None if self._is_supplier else None
        corr_account = (self.corr_account_edit.text() or "").strip() or None if self._is_supplier else None
        bank_name = (self.bank_name_edit.text() or "").strip() or None if self._is_supplier else None
        bik = (self.bik_edit.text() or "").strip() or None if self._is_supplier else None
        pricing_tier = self._current_pricing_tier()

        try:
            if self._is_supplier:
                if self.edit_client:
                    models.update_supplier_full(
                        self.edit_client['id'], t, name,
                        inn or None, kpp or None, okpo, ogrn, registration,
                        first_name, last_name, passport_series, passport_number, birth_date, gender,
                        phone, email, legal_address, actual_address, source, notes,
                        bank_account, corr_account, bank_name, bik,
                    )
                else:
                    new_id = models.insert_supplier_full(
                        t, name, inn or None, kpp or None, okpo, ogrn, registration,
                        first_name, last_name, passport_series, passport_number, birth_date, gender,
                        phone, email, legal_address, actual_address, source, notes,
                        bank_account, corr_account, bank_name, bik,
                    )
                    self._saved_client_id = new_id
            elif self.edit_client:
                models.update_client_full(
                    self.edit_client['id'], t, name,
                    inn or None, kpp or None, okpo, ogrn, registration,
                    first_name, last_name, passport_series, passport_number, birth_date, gender,
                    phone, email, legal_address, actual_address, source, notes, pricing_tier
                )
            else:
                new_id = models.insert_client_full(
                    t, name, inn or None, kpp or None, okpo, ogrn, registration,
                    first_name, last_name, passport_series, passport_number, birth_date, gender,
                    phone, email, legal_address, actual_address, source, notes, pricing_tier
                )
                self._saved_client_id = new_id
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить: %s" % e)
            return
        if self.edit_client:
            self._saved_client_id = self.edit_client['id']
        sid = getattr(self, "_saved_client_id", None)
        if sid is not None:
            if self._is_supplier:
                row = models.get_supplier_by_id(int(sid))
                if row:
                    self._saved_name = (models._supplier_display_name(row) or row.get("name") or "").strip()
                else:
                    self._saved_name = name
            else:
                row = models.get_client_by_id(int(sid))
                if row:
                    self._saved_name = (models._client_display_name(row) or row.get("name") or "").strip()
                else:
                    self._saved_name = name
        else:
            self._saved_name = name
        self.accept()

    def get_saved_name(self):
        return getattr(self, "_saved_name", None)

    def get_saved_client_id(self):
        return getattr(self, "_saved_client_id", None)
