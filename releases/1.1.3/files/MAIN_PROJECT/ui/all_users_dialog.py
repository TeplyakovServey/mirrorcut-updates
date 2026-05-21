# -*- coding: utf-8 -*-
"""Диалог «Все пользователи»: таблица, маскировка данных других админов, блокировка, редактирование, сохранение."""
import sys
import os

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QMessageBox,
    QHeaderView,
    QFormLayout,
    QLineEdit,
    QLabel,
    QDialogButtonBox,
)

from db_main import (
    get_all_users,
    set_blocked,
    role_label_desktop,
    account_origin_ru,
    ROLE_ADMIN,
    create_admin_user,
    is_boss_protected_user_id,
    update_user_credentials_admin,
)
from ui.boss_block_dialogs import show_boss_block_forbidden_sequence

_ADMIN_LOGIN_MASK = "****"
_ADMIN_PASSWORD_MASK = "секретик"


class _CreateAdminDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Создать администратора")
        self.setMinimumWidth(360)
        fl = QFormLayout(self)
        self.surname_edit = QLineEdit()
        fl.addRow("Фамилия:", self.surname_edit)
        self.name_edit = QLineEdit()
        fl.addRow("Имя:", self.name_edit)
        self.login_edit = QLineEdit()
        fl.addRow("Логин:", self.login_edit)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        fl.addRow("Пароль:", self.password_edit)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        fl.addRow(bb)


class AllUsersDialog(QDialog):
    def __init__(self, parent=None, current_user=None):
        super().__init__(parent)
        self._current_user = current_user or {}
        self._viewer_id = int(self._current_user.get("id") or 0)
        self.setWindowTitle("Все пользователи")
        self.setMinimumSize(780, 520)
        layout = QVBoxLayout(self)
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Имя", "Фамилия", "Логин", "Пароль", "Роль", "Статус / блок", "Источник"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        btn_block = QPushButton("Заблокировать")
        btn_block.clicked.connect(self._block)
        row.addWidget(btn_block)
        btn_unblock = QPushButton("Разблокировать")
        btn_unblock.clicked.connect(self._unblock)
        row.addWidget(btn_unblock)
        self._btn_create_admin = QPushButton("Создать админа")
        self._btn_create_admin.clicked.connect(self._create_admin)
        is_admin = str(self._current_user.get("role") or "") == ROLE_ADMIN
        self._btn_create_admin.setVisible(is_admin)
        row.addWidget(self._btn_create_admin)
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(QLabel("Изменение выбранного пользователя (пароль — только если заполнить поле):"))
        form = QFormLayout()
        self.edit_surname = QLineEdit()
        self.edit_name = QLineEdit()
        self.edit_login = QLineEdit()
        self.edit_password = QLineEdit()
        self.edit_password.setEchoMode(QLineEdit.Password)
        self.edit_password.setPlaceholderText("оставьте пустым, чтобы не менять пароль")
        form.addRow("Фамилия:", self.edit_surname)
        form.addRow("Имя:", self.edit_name)
        form.addRow("Логин:", self.edit_login)
        form.addRow("Пароль:", self.edit_password)
        layout.addLayout(form)
        save_row = QHBoxLayout()
        save_row.addStretch()
        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self._save_edits)
        save_row.addWidget(btn_save)
        save_row.addStretch()
        layout.addLayout(save_row)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self._fill()
        self._clear_edits()

    def _mask_row_for_table(self, u):
        uid = u.get("id")
        other_admin = (
            str(u.get("role") or "") == ROLE_ADMIN
            and int(uid or 0) != self._viewer_id
        )
        if other_admin:
            return _ADMIN_LOGIN_MASK, _ADMIN_PASSWORD_MASK
        return str(u.get("login") or ""), str(u.get("password_hash") or "")

    def _fill(self, select_user_id=None):
        users = get_all_users()
        self.table.setRowCount(len(users))
        for i, u in enumerate(users):
            self.table.setItem(i, 0, QTableWidgetItem(str(u.get("id", ""))))
            self.table.setItem(i, 1, QTableWidgetItem(str(u.get("name") or "").strip()))
            self.table.setItem(i, 2, QTableWidgetItem(str(u.get("surname") or "").strip()))
            login_disp, pwd_disp = self._mask_row_for_table(u)
            self.table.setItem(i, 3, QTableWidgetItem(login_disp))
            self.table.setItem(i, 4, QTableWidgetItem(pwd_disp))
            self.table.setItem(i, 5, QTableWidgetItem(str(role_label_desktop(u.get("role")))))
            status = []
            if u.get("approved"):
                status.append("подтверждён")
            else:
                status.append("не подтверждён")
            if u.get("blocked"):
                status.append("заблокирован")
            else:
                status.append("не заблокирован")
            self.table.setItem(i, 6, QTableWidgetItem(", ".join(status)))
            self.table.setItem(i, 7, QTableWidgetItem(account_origin_ru(u.get("account_origin"))))
        self._users = users
        if select_user_id is not None:
            for j, u in enumerate(self._users):
                if int(u.get("id") or 0) == int(select_user_id):
                    self.table.selectRow(j)
                    break

    def _current_user_id(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self._users):
            return None
        return self._users[row].get("id")

    def _on_selection_changed(self):
        uid = self._current_user_id()
        if uid is None:
            self._clear_edits()
            return
        row = self.table.currentRow()
        u = self._users[row]
        self.edit_surname.setText(str(u.get("surname") or "").strip())
        self.edit_name.setText(str(u.get("name") or "").strip())
        self.edit_login.setText(str(u.get("login") or "").strip())
        self.edit_password.clear()

    def _clear_edits(self):
        self.edit_surname.clear()
        self.edit_name.clear()
        self.edit_login.clear()
        self.edit_password.clear()

    def _save_edits(self):
        uid = self._current_user_id()
        if uid is None:
            QMessageBox.information(self, "Сохранение", "Выберите пользователя в таблице.")
            return
        pwd = self.edit_password.text()
        pwd_arg = pwd if pwd.strip() else None
        ok, err = update_user_credentials_admin(
            uid,
            self.edit_surname.text(),
            self.edit_name.text(),
            self.edit_login.text().strip(),
            pwd_arg,
        )
        if not ok:
            QMessageBox.warning(self, "Сохранение", err or "Не удалось сохранить.")
            return
        QMessageBox.information(self, "Сохранение", "Изменения сохранены.")
        self._fill(select_user_id=uid)

    def _block(self):
        uid = self._current_user_id()
        if uid is None:
            QMessageBox.information(self, "Блокировка", "Выберите пользователя.")
            return
        if is_boss_protected_user_id(uid):
            show_boss_block_forbidden_sequence(self)
            return
        if not set_blocked(uid, True):
            QMessageBox.warning(self, "Блокировка", "Не удалось заблокировать пользователя.")
            return
        QMessageBox.information(self, "Блокировка", "Пользователь заблокирован.")
        self._fill(select_user_id=uid)

    def _unblock(self):
        uid = self._current_user_id()
        if uid is None:
            QMessageBox.information(self, "Разблокировка", "Выберите пользователя.")
            return
        if not set_blocked(uid, False):
            QMessageBox.warning(self, "Разблокировка", "Не удалось разблокировать пользователя.")
            return
        QMessageBox.information(self, "Разблокировка", "Пользователь разблокирован.")
        self._fill(select_user_id=uid)

    def _create_admin(self):
        if str(self._current_user.get("role") or "") != ROLE_ADMIN:
            return
        d = _CreateAdminDialog(self)
        if d.exec_() != QDialog.Accepted:
            return
        surname = d.surname_edit.text().strip()
        name = d.name_edit.text().strip()
        login = d.login_edit.text().strip()
        password = d.password_edit.text()
        if not login or not password:
            QMessageBox.warning(self, "Создать админа", "Укажите логин и пароль.")
            return
        uid = create_admin_user(surname, name, login, password)
        if uid is None:
            QMessageBox.warning(self, "Создать админа", "Пользователь с таким логином уже существует.")
            return
        QMessageBox.information(self, "Создать админа", "Администратор создан и может войти в систему.")
        self._fill(select_user_id=uid)
