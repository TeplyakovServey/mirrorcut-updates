# -*- coding: utf-8 -*-
"""Диалог «Клиенты»: таблица клиентов, кнопка «Заказы клиента» — заказы выбранного клиента."""
import sys
import os
_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QMessageBox, QLabel, QFormLayout, QGroupBox, QLineEdit, QAbstractItemView,
    QApplication, QCompleter,
)
from PyQt5.QtCore import Qt
from PyQt5.QtCore import QEvent, QObject, QTimer

from db_main import order_status_to_ru
from db import models as db_models


def _client_order_type_and_count(order_row):
    raw = order_row.get("blocks_calc_json")
    if not raw or not str(raw).strip():
        return "—", 0
    try:
        from logic.blocks_bundle import parse_bundle

        _, products = parse_bundle(str(raw))
    except Exception:
        return "—", 0
    if not products:
        return "—", 0
    kinds = {str(p.get("kind") or "glass_mirror").strip() or "glass_mirror" for p in products}
    if len(kinds) == 1:
        only = list(kinds)[0]
        type_label = "Стекло / зеркало" if only == "glass_mirror" else ("Фасады" if only == "facade" else only)
    else:
        type_label = "Смешанный"
    return type_label, len(products)


def _client_order_total(order_row):
    raw = order_row.get("blocks_calc_json")
    if not raw or not str(raw).strip():
        return None
    try:
        from logic.blocks_bundle import parse_bundle, bundle_grand_total_rub

        _, products = parse_bundle(str(raw))
        if not products:
            return None
        return bundle_grand_total_rub(products)
    except Exception:
        return None


def _str(v):
    return (str(v).strip() if v is not None else '') or '—'


def _fmt_date(v):
    if v is None:
        return "—"
    if hasattr(v, "strftime"):
        try:
            return v.strftime("%d.%m.%Y")
        except Exception:
            pass
    s = str(v).strip()
    return s or "—"


# Совпадает с ui/new_client_dialog: legal / ip / individual (не путать с legacy key «person»).
CLIENT_TYPE_LABELS = {
    "legal": "Юр. лицо",
    "ip": "ИП",
    "individual": "Физ. лицо",
    "person": "Физ. лицо",
}
GENDER_LABELS = {
    'male': 'Мужской',
    'female': 'Женский',
    'm': 'Мужской',
    'f': 'Женский',
}


def _gender_ru(v):
    if v is None:
        return '—'
    s = str(v).strip().lower()
    if not s:
        return '—'
    return GENDER_LABELS.get(s, (str(v).strip() or '—'))


def _try_refresh_main_orders_orders_list(start_widget):
    w = start_widget
    for _ in range(12):
        if w is None:
            break
        fn = getattr(w, '_load_orders', None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass
            return
        w = w.parent()


def open_order_as_in_main_table(order_id, parent=None):
    """То же окно, что и двойной клик по заказу в главной таблице: обзор блоков или карточка заказа."""
    dlg_parent = parent or QApplication.activeWindow()
    try:
        oid = int(order_id)
    except (TypeError, ValueError):
        QMessageBox.warning(dlg_parent, 'Заказ', 'Некорректный номер заказа.')
        return
    o = db_models.get_order(oid)
    if not o:
        QMessageBox.warning(dlg_parent, 'Заказ', 'Заказ не найден.')
        return
    has_bundle = False
    raw = o.get('blocks_calc_json')
    if raw and str(raw).strip():
        try:
            from logic.blocks_bundle import parse_bundle
            _, prods = parse_bundle(str(raw))
            has_bundle = len(prods) > 0
        except Exception:
            pass
    if has_bundle:
        from ui.glass_order_overview_dialog import GlassOrderOverviewDialog

        ov = GlassOrderOverviewDialog(o, dlg_parent)
        ov.exec_()
        if getattr(ov, "_main_orders_changed", False):
            _try_refresh_main_orders_orders_list(dlg_parent)
    else:
        from ui.order_detail_dialog import OrderDetailDialog

        OrderDetailDialog(oid, dlg_parent).exec_()
        _try_refresh_main_orders_orders_list(dlg_parent)
PRICING_LABELS = {
    'b2b': 'B2B',
    'b2c30': 'B2C 30',
    'b2c50': 'B2C 50',
}
PRICING_STYLES = {
    'b2b': "background:#e8f5e9; color:#1b5e20; border:1px solid #81c784;",
    'b2c30': "background:#fff3e0; color:#e65100; border:1px solid #ffb74d;",
    'b2c50': "background:#ffebee; color:#b71c1c; border:1px solid #ef9a9a;",
}


class _PhoneInputBehavior(QObject):
    """Поведение поля телефона: быстрый старт с +7 и возможность стереть код страны."""

    def eventFilter(self, obj, ev):  # noqa: N802
        if not isinstance(obj, QLineEdit):
            return False
        if ev.type() == QEvent.FocusIn:
            txt = obj.text() or ""
            if not txt.strip():
                obj.setText("+7 ")
                obj.setCursorPosition(2)
                return False
            if txt.startswith("+7"):
                obj.setCursorPosition(2)
                return False
        if ev.type() == QEvent.MouseButtonPress:
            txt = obj.text() or ""
            if txt.startswith("+7"):
                QTimer.singleShot(0, lambda w=obj: w.setCursorPosition(2))
                return False
        if ev.type() == QEvent.KeyPress:
            key = ev.key()
            txt = obj.text() or ""
            if key in (Qt.Key_Backspace, Qt.Key_Delete) and txt.startswith("+7") and obj.cursorPosition() <= 3:
                obj.clear()
                return True
        return False


def _markup_percent_from_tier(pricing_tier) -> int:
    t = str(pricing_tier or "").strip().lower()
    if t == "b2c30":
        return 30
    if t == "b2c50":
        return 50
    return 0


def _pricing_badge(pricing_tier: str) -> QLabel:
    tier = (pricing_tier or 'b2b').strip().lower()
    lbl = QLabel(PRICING_LABELS.get(tier, tier.upper() if tier else 'B2B'))
    lbl.setStyleSheet(
        "font-weight: bold; padding: 4px 10px; border-radius: 10px; "
        + PRICING_STYLES.get(tier, PRICING_STYLES['b2b'])
    )
    return lbl


class ClientCardDialog(QDialog):
    """Карточка клиента: полные данные (как в WebQR) и список заказов с возможностью открыть заказ."""
    def __init__(self, client_id, client_name, parent=None):
        super().__init__(parent)
        self._phone_input_behavior = _PhoneInputBehavior(self)
        self.setWindowTitle("Карточка клиента: %s" % (client_name or '—'))
        self.setMinimumSize(620, 520)
        layout = QVBoxLayout(self)

        client = None
        if client_id:
            try:
                client = db_models.get_client_by_id(client_id)
            except Exception:
                pass

        if client:
            self._client = dict(client)
            info_group = QGroupBox("Данные клиента")
            form = QFormLayout()
            form.setSpacing(6)
            ctype = client.get('client_type') or 'legal'
            tier = str(client.get('pricing_tier') or 'b2b').strip().lower()
            mk = _markup_percent_from_tier(tier)
            form.addRow("Тип клиента:", QLabel(CLIENT_TYPE_LABELS.get(ctype, ctype)))
            form.addRow("Сегмент цен:", _pricing_badge(tier))
            form.addRow(
                "Наценка к базе:",
                QLabel("+%s%%" % mk if mk else "0% (оптовая сетка B2B)"),
            )
            form.addRow("Наименование / ФИО:", QLabel(_str(client.get('name'))))
            if ctype != 'legal':
                form.addRow("Имя:", QLabel(_str(client.get('first_name'))))
                form.addRow("Фамилия:", QLabel(_str(client.get('last_name'))))
            form.addRow("Источник:", QLabel(_str(client.get('source'))))
            form.addRow("ИНН:", QLabel(_str(client.get('inn'))))
            form.addRow("КПП:", QLabel(_str(client.get('kpp'))))
            form.addRow("ОКПО:", QLabel(_str(client.get('okpo'))))
            form.addRow("ОГРН:", QLabel(_str(client.get('ogrn'))))
            ogrnip = _str(client.get('ogrnip'))
            if ogrnip != '—':
                form.addRow("ОГРНИП:", QLabel(ogrnip))
            ba = _str(client.get('bank_account'))
            if ba != '—':
                form.addRow("Р/с:", QLabel(ba))
            ca = _str(client.get('corr_account'))
            if ca != '—':
                form.addRow("К/с:", QLabel(ca))
            bn = _str(client.get('bank_name'))
            if bn != '—':
                form.addRow("Банк:", QLabel(bn))
            bik = _str(client.get('bik'))
            if bik != '—':
                form.addRow("БИК:", QLabel(bik))
            reg = _str(client.get("registration"))
            if reg != "—":
                reg_l = QLabel(reg)
                reg_l.setWordWrap(True)
                form.addRow("Регистрация:", reg_l)
            form.addRow("Дата регистрации:", QLabel(_fmt_date(client.get("registration_date"))))
            if ctype != "legal":
                ps = (client.get("passport_series") or "").strip()
                pn = (client.get("passport_number") or "").strip()
                doc = " ".join(x for x in (ps, pn) if x) or "—"
                form.addRow("Паспорт:", QLabel(doc))
                form.addRow("Дата рождения:", QLabel(_fmt_date(client.get("birth_date"))))
                form.addRow("Пол:", QLabel(_gender_ru(client.get("gender"))))
            form.addRow("Телефон:", QLabel(_str(client.get('phone'))))
            form.addRow("Email:", QLabel(_str(client.get('email'))))
            form.addRow("Юр. адрес:", QLabel(_str(client.get('legal_address'))))
            form.addRow("Факт. адрес:", QLabel(_str(client.get('actual_address'))))
            notes_lbl = QLabel(_str(client.get('notes')))
            notes_lbl.setWordWrap(True)
            form.addRow("Примечание:", notes_lbl)
            info_group.setLayout(form)
            layout.addWidget(info_group)
            row_btn = QHBoxLayout()
            btn_edit = QPushButton("Изменить данные клиента")
            btn_edit.clicked.connect(self._edit_client_data)
            row_btn.addWidget(btn_edit)
            btn_stat = QPushButton("Статистика")
            btn_stat.setToolTip("Графики и метрики по заказам и продажам клиента")
            btn_stat.clicked.connect(self._open_client_statistics)
            row_btn.addWidget(btn_stat)
            row_btn.addStretch()
            layout.addLayout(row_btn)
        else:
            self._client = None
            layout.addWidget(QLabel("Клиент не найден в базе (id=%s)." % client_id))

        orders = []
        if client_id:
            try:
                orders = db_models.get_orders_by_client_id(client_id) or []
            except Exception:
                pass

        layout.addWidget(QLabel("Заказы:"))
        self._orders = orders
        if not orders:
            layout.addWidget(QLabel("Нет заказов."))
        else:
            self.table = QTableWidget()
            self.table.setColumnCount(7)
            self.table.setHorizontalHeaderLabels(["ID", "Дата", "Статус", "Сумма ₽", "Тип", "Изделий", "K-номер"])
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.table.setRowCount(len(orders))
            for i, o in enumerate(orders):
                self.table.setItem(i, 0, QTableWidgetItem(str(o.get('id', ''))))
                created = o.get('created_at')
                if hasattr(created, 'strftime'):
                    created = created.strftime('%d.%m.%Y %H:%M')
                self.table.setItem(i, 1, QTableWidgetItem(str(created or '—')))
                self.table.setItem(i, 2, QTableWidgetItem(order_status_to_ru(o.get('status'))))
                total_rub = _client_order_total(o)
                type_label, products_count = _client_order_type_and_count(o)
                self.table.setItem(i, 3, QTableWidgetItem(str(total_rub if total_rub is not None else "—")))
                self.table.setItem(i, 4, QTableWidgetItem(type_label))
                self.table.setItem(i, 5, QTableWidgetItem(str(products_count if products_count else "—")))
                self.table.setItem(i, 6, QTableWidgetItem(str(o.get('k_number') or '—')))
            layout.addWidget(self.table)
            btn_open = QPushButton("Открыть выбранный заказ")
            btn_open.clicked.connect(self._open_order)
            layout.addWidget(btn_open)

    def _open_client_statistics(self):
        c = self._client or {}
        cid = c.get("id")
        if not cid:
            return
        try:
            from ui.client_statistics_dialog import ClientStatisticsDialog

            ClientStatisticsDialog(int(cid), str(c.get("name") or "—"), self).exec_()
        except Exception as ex:
            QMessageBox.warning(
                self,
                "Статистика",
                "Не удалось открыть окно статистики. Установите зависимости:\n"
                "pip install plotly pandas PyQtWebEngine\n\n%s" % ex,
            )

    def _edit_client_data(self):
        c = self._client or {}
        cid = c.get("id")
        if not cid:
            return
        # Полная форма (тип, сегмент цен, ИНН, ФИО, паспорт, адреса…) и валидация по типу —
        # общий модуль MIRROR_CUT/ui/new_client_dialog.py (см. _mirror_dialogs._load_dialog).
        from ui._mirror_dialogs import _load_dialog

        saved_ui = sys.modules.pop("ui", None)
        try:
            NewClientDialog = _load_dialog("new_client_dialog", "NewClientDialog")
            if NewClientDialog is None:
                QMessageBox.warning(
                    self,
                    "Клиент",
                    "Не удалось загрузить форму редактирования клиента.",
                )
                return
            ec = dict(c)
            if ec.get("client_type") == "person":
                ec["client_type"] = "individual"
            dlg = NewClientDialog(self, edit_client=ec)
            if dlg.exec_() != QDialog.Accepted:
                return
        finally:
            if saved_ui is not None:
                sys.modules["ui"] = saved_ui

        par = self.parent()
        if isinstance(par, ClientsDialog):
            try:
                par._all_clients = db_models.get_all_clients() or []
                par._rebuild_search_completers()
                par._apply_client_filter()
            except Exception:
                pass
        QMessageBox.information(self, "Клиент", "Данные сохранены.")
        self.accept()

    def _open_order(self):
        if not getattr(self, '_orders', None) or not getattr(self, 'table', None):
            return
        row = self.table.currentRow()
        if row < 0 or row >= len(self._orders):
            QMessageBox.information(self, "Заказ", "Выберите заказ в таблице.")
            return
        oid = self._orders[row].get('id')
        if oid is None:
            return
        open_order_as_in_main_table(oid, self)


class ClientOrdersDialog(QDialog):
    """Заказы выбранного клиента: список заказов с суммами и статусами."""
    def __init__(self, client_id, client_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Заказы клиента: %s" % (client_name or '—'))
        self.setMinimumSize(500, 350)
        layout = QVBoxLayout(self)
        orders = db_models.get_orders_by_client_id(client_id)
        self._orders = orders or []
        if not orders:
            layout.addWidget(QLabel("Нет заказов у этого клиента."))
            return
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(["ID", "Дата", "Статус", "Сумма ₽", "Тип", "Изделий", "K-номер"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setRowCount(len(orders))
        for i, o in enumerate(orders):
            self.table.setItem(i, 0, QTableWidgetItem(str(o.get('id', ''))))
            created = o.get('created_at')
            if hasattr(created, 'strftime'):
                created = created.strftime('%d.%m.%Y %H:%M')
            self.table.setItem(i, 1, QTableWidgetItem(str(created or '—')))
            self.table.setItem(i, 2, QTableWidgetItem(order_status_to_ru(o.get('status'))))
            total_rub = _client_order_total(o)
            type_label, products_count = _client_order_type_and_count(o)
            self.table.setItem(i, 3, QTableWidgetItem(str(total_rub if total_rub is not None else "—")))
            self.table.setItem(i, 4, QTableWidgetItem(type_label))
            self.table.setItem(i, 5, QTableWidgetItem(str(products_count if products_count else "—")))
            self.table.setItem(i, 6, QTableWidgetItem(str(o.get('k_number') or '—')))
        layout.addWidget(self.table)
        btn_open = QPushButton("Открыть выбранный заказ")
        btn_open.clicked.connect(self._open_order)
        layout.addWidget(btn_open)
        self._orders = orders

    def _open_order(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._orders):
            QMessageBox.information(self, "Заказ", "Выберите заказ в таблице.")
            return
        oid = self._orders[row].get('id')
        if oid is None:
            return
        open_order_as_in_main_table(oid, self)


class ClientsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._phone_input_behavior = _PhoneInputBehavior(self)
        self.setWindowTitle("Клиенты")
        self.setMinimumSize(880, 480)
        layout = QVBoxLayout(self)
        try:
            self._all_clients = db_models.get_all_clients() or []
        except Exception:
            self._all_clients = []

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Поиск:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("имя, телефон, email, ИНН, тип, сегмент…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_client_filter)
        search_row.addWidget(self._search, 1)
        layout.addLayout(search_row)

        search_row2 = QHBoxLayout()
        search_row2.addWidget(QLabel("Телефон:"))
        self._search_phone = QLineEdit()
        self._search_phone.setPlaceholderText("поиск по телефону…")
        self._search_phone.setClearButtonEnabled(True)
        self._search_phone.installEventFilter(self._phone_input_behavior)
        self._search_phone.textChanged.connect(self._apply_client_filter)
        search_row2.addWidget(self._search_phone, 1)
        search_row2.addWidget(QLabel("ИНН:"))
        self._search_inn = QLineEdit()
        self._search_inn.setPlaceholderText("поиск по ИНН…")
        self._search_inn.setClearButtonEnabled(True)
        self._search_inn.setInputMask("000000000000;_")
        self._search_inn.textChanged.connect(self._apply_client_filter)
        search_row2.addWidget(self._search_inn, 1)
        layout.addLayout(search_row2)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Наименование", "Тип", "Сегмент / наценка", "Телефон", "Email"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        btn_orders = QPushButton("Карточка и заказы")
        btn_orders.clicked.connect(self._on_orders_of_client)
        btn_row.addWidget(btn_orders)
        btn_row.addStretch()
        btn_create = QPushButton("Создать клиента")
        btn_create.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; "
            "padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:hover { background-color: #388e3c; }"
        )
        btn_create.clicked.connect(self._on_create_client)
        btn_row.addWidget(btn_create)
        layout.addLayout(btn_row)

        self._visible_clients = []
        self._rebuild_search_completers()
        self._apply_client_filter()

    def _rebuild_search_completers(self):
        phones = []
        inns = []
        for c in self._all_clients:
            nm = str(c.get("name") or "").strip()
            ph = str(c.get("phone") or "").strip()
            inn = str(c.get("inn") or "").strip()
            if ph:
                phones.append("%s · %s" % (ph, nm or "клиент"))
            if inn:
                inns.append("%s · %s" % (inn, nm or "клиент"))
        cp = QCompleter(sorted(set(phones)), self._search_phone)
        cp.setCaseSensitivity(Qt.CaseInsensitive)
        cp.setFilterMode(Qt.MatchContains)
        cp.setCompletionMode(QCompleter.PopupCompletion)
        self._search_phone.setCompleter(cp)
        ci = QCompleter(sorted(set(inns)), self._search_inn)
        ci.setCaseSensitivity(Qt.CaseInsensitive)
        ci.setFilterMode(Qt.MatchContains)
        ci.setCompletionMode(QCompleter.PopupCompletion)
        self._search_inn.setCompleter(ci)

    def _client_search_haystack(self, c):
        ctype = str(c.get("client_type") or "").strip().lower()
        tier = str(c.get("pricing_tier") or "").strip().lower()
        mk = _markup_percent_from_tier(tier)
        parts = [
            str(c.get("name") or ""),
            str(c.get("phone") or ""),
            str(c.get("email") or ""),
            str(c.get("inn") or ""),
            str(c.get("first_name") or ""),
            str(c.get("last_name") or ""),
            str(c.get("notes") or ""),
            str(c.get("source") or ""),
            _gender_ru(c.get("gender")),
            CLIENT_TYPE_LABELS.get(ctype, ctype),
            PRICING_LABELS.get(tier, tier),
            "%s%%" % mk,
            str(c.get("id") or ""),
        ]
        return " ".join(parts).lower()

    def _apply_client_filter(self):
        q = (self._search.text() or "").strip().lower()
        q_phone = (self._search_phone.text() or "").strip().lower()
        q_inn = (self._search_inn.text() or "").strip().lower()
        q_phone_digits = "".join(ch for ch in q_phone if ch.isdigit())
        q_inn_digits = "".join(ch for ch in q_inn if ch.isdigit())
        tokens = [t for t in q.split() if t] if q else []
        if not tokens and not q_phone and not q_inn:
            self._visible_clients = list(self._all_clients)
        else:
            self._visible_clients = []
            for c in self._all_clients:
                hay = self._client_search_haystack(c)
                if not all(t in hay for t in tokens):
                    continue
                if q_phone:
                    ph = str(c.get("phone") or "")
                    ph_digits = "".join(ch for ch in ph if ch.isdigit())
                    if not ((q_phone_digits and q_phone_digits in ph_digits) or (q_phone in ph.lower())):
                        continue
                if q_inn:
                    inn = str(c.get("inn") or "")
                    inn_digits = "".join(ch for ch in inn if ch.isdigit())
                    if not ((q_inn_digits and q_inn_digits in inn_digits) or (q_inn in inn.lower())):
                        continue
                self._visible_clients.append(c)
        self._fill_table()

    def _fill_table(self):
        clients = self._visible_clients
        self.table.setRowCount(len(clients))
        for i, c in enumerate(clients):
            cid = c.get("id")
            ctype = c.get("client_type") or "legal"
            tier = str(c.get("pricing_tier") or "b2b").strip().lower()
            mk = _markup_percent_from_tier(tier)
            seg = PRICING_LABELS.get(tier, tier)
            if mk:
                seg = "%s (+%s%%)" % (seg, mk)
            self.table.setItem(i, 0, QTableWidgetItem(str(cid or "")))
            self.table.setItem(i, 1, QTableWidgetItem(str(c.get("name") or "").strip()))
            self.table.setItem(
                i, 2, QTableWidgetItem(CLIENT_TYPE_LABELS.get(ctype, ctype))
            )
            self.table.setItem(i, 3, QTableWidgetItem(seg))
            self.table.setItem(i, 4, QTableWidgetItem(str(c.get("phone") or "")[:32]))
            self.table.setItem(i, 5, QTableWidgetItem(str(c.get("email") or "")[:48]))

    def _client_at_row(self, row):
        if row < 0 or row >= len(self._visible_clients):
            return None
        return self._visible_clients[row]

    def _open_client_card(self, c):
        if not c:
            QMessageBox.information(self, "Клиент", "Выберите клиента в таблице.")
            return
        cid = c.get("id")
        name = str(c.get("name") or "").strip()
        ClientCardDialog(cid, name, self).exec_()

    def _on_row_double_clicked(self, row, _col):
        self._open_client_card(self._client_at_row(row))

    def _on_orders_of_client(self):
        self._open_client_card(self._client_at_row(self.table.currentRow()))

    def _on_create_client(self):
        try:
            from ui._mirror_dialogs import _load_dialog

            NewClientDialog = _load_dialog("new_client_dialog", "NewClientDialog")
            if NewClientDialog is None:
                from ui.new_client_dialog import NewClientDialog
        except Exception:
            try:
                from ui.new_client_dialog import NewClientDialog
            except Exception as e:
                QMessageBox.warning(self, "Клиент", "Не удалось открыть форму: %s" % e)
                return
        d = NewClientDialog(self)
        if d.exec_() != QDialog.Accepted:
            return
        try:
            import app_state

            app_state.refresh_clients()
        except Exception:
            pass
        db_models._invalidate_clients_cache()
        try:
            self._all_clients = db_models.get_all_clients() or []
        except Exception:
            self._all_clients = []
        self._rebuild_search_completers()
        self._apply_client_filter()
