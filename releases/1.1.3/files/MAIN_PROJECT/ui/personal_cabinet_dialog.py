# -*- coding: utf-8 -*-
"""Личный кабинет: смена логина; смена пароля (старый + новый дважды). ФИО — редактирование только у админа."""
import sys
import os

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QHBoxLayout,
    QMessageBox,
    QLabel,
)
from db_main import self_service_update_profile, get_user_by_id, ROLE_ADMIN
from cfg_loader import app_cfg, color
from window_branding import apply_window_icon


class PersonalCabinetDialog(QDialog):
    def __init__(self, user, parent=None):
        super().__init__(parent)
        self._user_id = int(user.get("id") or 0)
        self._is_admin = str(user.get("role") or "").strip() == ROLE_ADMIN
        self._updated = None
        self.setWindowTitle("Личный кабинет")
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        if self._is_admin:
            hint = QLabel("Вы администратор: можете менять фамилию, имя, логин и пароль.")
        else:
            hint = QLabel(
                "Фамилию и имя может изменить только администратор. Обратитесь к админу или попросите правку в «Все пользователи»."
            )
        hint.setWordWrap(True)
        lay.addWidget(hint)
        fl = QFormLayout()
        self.surname_show = QLineEdit()
        self.surname_show.setReadOnly(not self._is_admin)
        self.name_show = QLineEdit()
        self.name_show.setReadOnly(not self._is_admin)
        self.login_edit = QLineEdit()
        self.old_password = QLineEdit()
        self.old_password.setEchoMode(QLineEdit.Password)
        self.old_password.setPlaceholderText("только при смене пароля")
        self.new_password = QLineEdit()
        self.new_password.setEchoMode(QLineEdit.Password)
        self.new_password.setPlaceholderText("новый пароль")
        self.new_password2 = QLineEdit()
        self.new_password2.setEchoMode(QLineEdit.Password)
        self.new_password2.setPlaceholderText("повтор нового пароля")
        fl.addRow("Фамилия:", self.surname_show)
        fl.addRow("Имя:", self.name_show)
        fl.addRow("Логин:", self.login_edit)
        fl.addRow("Старый пароль:", self.old_password)
        fl.addRow("Новый пароль:", self.new_password)
        fl.addRow("Новый пароль ещё раз:", self.new_password2)
        lay.addLayout(fl)
        row = QHBoxLayout()
        row.addStretch()
        btn_save = QPushButton("Сохранить")
        btn_save.clicked.connect(self._save)
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.reject)
        row.addWidget(btn_save)
        row.addWidget(btn_close)
        lay.addLayout(row)
        self._load_from_user(user)
        bg = color("login_bg") if app_cfg() else "#E8F4FC"
        self.setStyleSheet("QDialog { background-color: %s; }" % bg)
        apply_window_icon(self)

    def _load_from_user(self, user):
        self.surname_show.setText(str(user.get("surname") or "").strip())
        self.name_show.setText(str(user.get("name") or "").strip())
        self.login_edit.setText(str(user.get("login") or "").strip())

    def _save(self):
        ok, err = self_service_update_profile(
            self._user_id,
            self.login_edit.text().strip(),
            self.old_password.text(),
            self.new_password.text(),
            self.new_password2.text(),
            surname=self.surname_show.text(),
            name=self.name_show.text(),
        )
        if not ok:
            QMessageBox.warning(self, "Личный кабинет", err or "Не удалось сохранить.")
            return
        self._updated = get_user_by_id(self._user_id)
        QMessageBox.information(self, "Личный кабинет", "Изменения сохранены.")
        self.accept()

    def get_updated_user(self):
        return self._updated
