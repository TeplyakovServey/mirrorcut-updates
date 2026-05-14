# -*- coding: utf-8 -*-
"""Окно входа: логин, пароль, Войти, Регистрация."""
import sys
import os
_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

import datetime

from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QMessageBox,
    QFormLayout,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QWidget,
)
from PyQt5.QtGui import QPixmap, QTransform, QPainter, QFont, QFontMetrics, QColor, QFontInfo
from PyQt5.QtCore import Qt

from window_branding import apply_window_icon
from cfg_loader import app_cfg, color
from ui.arsenal_splash_widget import login_glass_card_size, login_glass_panel_qss
from db_main import (
    get_user_by_login,
    check_password,
    create_user,
    ROLE_MANAGER,
    ACCOUNT_ORIGIN_WEB,
)


_MONTHS_RU = (
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
)
# Тот же наклон, что и у картинки miss (против часовой — отрицательный градус).
_MISS_LOGO_ROTATE_DEG = -3.0


def _today_ru_day_month() -> str:
    d = datetime.date.today()
    return "%d %s" % (d.day, _MONTHS_RU[d.month])


def _miss_pixmap_with_date_corner(
    pm_scaled: QPixmap,
    rotate_deg: float,
    margin_x: int = 11,
    margin_y: int = 10,
) -> QPixmap:
    """Дата в левом верхнем углу картинки, чёрным; затем общий поворот как у лого."""
    w, h = pm_scaled.width(), pm_scaled.height()
    composite = QPixmap(w, h)
    composite.fill(Qt.transparent)
    p = QPainter(composite)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    p.drawPixmap(0, 0, pm_scaled)
    p.setPen(QColor(0, 0, 0))
    font = QFont("Segoe UI", 5)
    if not QFontInfo(font).exactMatch():
        font = QFont("Arial", 5)
    font.setBold(True)
    p.setFont(font)
    fm = QFontMetrics(font)
    txt = _today_ru_day_month()
    p.drawText(margin_x, margin_y + fm.ascent(), txt)
    p.end()
    tr = QTransform()
    tr.rotate(rotate_deg)
    return composite.transformed(tr, Qt.SmoothTransformation)


class RegisterDialog(QDialog):
    """Регистрация: только менеджер (офис). Роли цеха/монтажа и полный доступ назначаются отдельно."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Регистрация")
        self.setMinimumWidth(360)
        fl = QFormLayout(self)
        self.surname_edit = QLineEdit()
        self.surname_edit.setPlaceholderText("Фамилия")
        fl.addRow("Фамилия:", self.surname_edit)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Имя")
        fl.addRow("Имя:", self.name_edit)
        self.login_edit = QLineEdit()
        self.login_edit.setPlaceholderText("Логин")
        fl.addRow("Логин:", self.login_edit)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("Пароль")
        fl.addRow("Пароль:", self.password_edit)
        self.password2_edit = QLineEdit()
        self.password2_edit.setEchoMode(QLineEdit.Password)
        self.password2_edit.setPlaceholderText("Подтверждение пароля")
        fl.addRow("Подтверждение:", self.password2_edit)
        btn = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn.accepted.connect(self.do_register)
        btn.rejected.connect(self.reject)
        fl.addRow(btn)
        bg = color('login_bg') if app_cfg() else '#E8F4FC'
        self.setStyleSheet("QDialog { background-color: %s; }" % bg)
        apply_window_icon(self)

    def do_register(self):
        surname = self.surname_edit.text().strip()
        name = self.name_edit.text().strip()
        login = self.login_edit.text().strip()
        p1 = self.password_edit.text()
        p2 = self.password2_edit.text()
        if not login:
            QMessageBox.warning(self, "Регистрация", "Введите логин.")
            return
        if not p1 or p1 != p2:
            QMessageBox.warning(self, "Регистрация", "Пароли не совпадают или пусты.")
            return
        uid = create_user(surname, name, login, p1, ROLE_MANAGER)
        if uid is None:
            QMessageBox.warning(self, "Регистрация", "Пользователь с таким логином уже существует.")
            return
        QMessageBox.information(
            self,
            "Регистрация",
            "Вы зарегистрированы. Ждите одобрения администратора для входа.",
        )
        self.accept()


class LoginDialog(QDialog):
    """Вход: логин, пароль, Войти, Регистрация. При успехе accept() и get_user() возвращает данные пользователя."""
    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.Window | Qt.WindowStaysOnTopHint,
        )
        self.setWindowTitle("Вход в систему!")
        self.setObjectName("LoginDialogRoot")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._user = None

        gw, gh = login_glass_card_size()
        app = QApplication.instance()
        scr = app.primaryScreen() if app else None
        if scr is not None:
            self.setGeometry(scr.geometry())
        else:
            self.resize(1024, 768)

        geo = scr.geometry() if scr is not None else None
        fw, fh = gw, gh
        if geo is not None:
            fw = min(gw, max(280, geo.width()))
            fh = min(gh, max(220, geo.height()))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        mid = QHBoxLayout()
        mid.addStretch(1)
        glass = QFrame(self)
        glass.setObjectName("LoginGlassPanel")
        glass.setFixedSize(fw, fh)
        inner = QVBoxLayout(glass)
        inner.setContentsMargins(28, 22, 28, 22)
        inner.setSpacing(12)

        miss_path = os.path.join(_root, "miss.png")
        if not os.path.isfile(miss_path):
            miss_path = os.path.join(_mp, "miss.png")
        pm_miss = QPixmap(miss_path)
        if not pm_miss.isNull():
            max_logo_w = max(1, int(round(fw * 0.23)))
            pm_miss = pm_miss.scaledToWidth(max_logo_w, Qt.SmoothTransformation)
            pm_miss = _miss_pixmap_with_date_corner(
                pm_miss, _MISS_LOGO_ROTATE_DEG, margin_x=11, margin_y=10
            )
            logo_row = QHBoxLayout()
            logo_row.setContentsMargins(0, 0, 4, 2)
            miss_wrap = QWidget(glass)
            miss_wrap.setFixedSize(pm_miss.width(), pm_miss.height())
            self._miss_logo = QLabel(miss_wrap)
            self._miss_logo.setObjectName("LoginMissLogo")
            self._miss_logo.setPixmap(pm_miss)
            self._miss_logo.setGeometry(0, 0, pm_miss.width(), pm_miss.height())
            hit_miss = QPushButton(miss_wrap)
            hit_miss.setObjectName("LoginMissHit")
            hit_miss.setGeometry(0, 0, pm_miss.width(), pm_miss.height())
            hit_miss.setFlat(True)
            hit_miss.setFocusPolicy(Qt.NoFocus)
            hit_miss.setAutoDefault(False)
            hit_miss.setDefault(False)
            hit_miss.setCursor(Qt.ArrowCursor)
            hit_miss.setStyleSheet(
                "QPushButton#LoginMissHit { background: transparent; border: none; }"
            )
            hit_miss.clicked.connect(self.reject)
            logo_row.addStretch(1)
            logo_row.addWidget(miss_wrap, 0, Qt.AlignRight | Qt.AlignTop)
            inner.addLayout(logo_row)

        title = QLabel("Вход в систему")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "color: rgba(255,255,255,0.96); font-size: 18px; font-weight: 700; "
            "letter-spacing: 0.5px; background: transparent; border: none;"
        )
        inner.addWidget(title)

        try:
            from update_client import login_version_summary

            ver_line = login_version_summary()
        except Exception:
            ver_line = ""
        if not ver_line:
            try:
                from update_client import read_install_version

                ver_line = "Версия %s" % read_install_version()
            except Exception:
                ver_line = ""
        self._lbl_versions = QLabel(ver_line)
        self._lbl_versions.setWordWrap(True)
        self._lbl_versions.setAlignment(Qt.AlignCenter)
        self._lbl_versions.setStyleSheet(
            "color: rgba(255,255,255,0.82); font-size: 11px; background: transparent; border: none;"
        )
        inner.addWidget(self._lbl_versions)

        form = QFormLayout()
        form.setSpacing(10)
        form.setHorizontalSpacing(12)
        self.login_edit = QLineEdit()
        self.login_edit.setPlaceholderText("Логин")
        lab_l = QLabel("Логин")
        lab_l.setStyleSheet("color: rgba(255,255,255,0.88); background: transparent; border: none;")
        form.addRow(lab_l, self.login_edit)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("Пароль")
        lab_p = QLabel("Пароль")
        lab_p.setStyleSheet("color: rgba(255,255,255,0.88); background: transparent; border: none;")
        form.addRow(lab_p, self.password_edit)
        inner.addLayout(form)

        row = QHBoxLayout()
        row.setSpacing(12)
        row.addStretch(1)
        btn_login = QPushButton("Войти")
        btn_login.setDefault(True)
        btn_login.setAutoDefault(True)
        btn_login.clicked.connect(self.do_login)
        row.addWidget(btn_login)
        btn_reg = QPushButton("Регистрация")
        btn_reg.clicked.connect(self.do_register)
        row.addWidget(btn_reg)
        row.addStretch(1)
        inner.addLayout(row)

        self.login_edit.returnPressed.connect(self.do_login)
        self.password_edit.returnPressed.connect(self.do_login)

        mid.addWidget(glass)
        mid.addStretch(1)
        outer.addLayout(mid)
        outer.addStretch(1)

        glass_qss = login_glass_panel_qss()
        inner_qss = """
            QFrame#LoginGlassPanel QLabel { background: transparent; border: none; }
            QFrame#LoginGlassPanel QLineEdit {
                background-color: rgba(255,255,255,0.14);
                border: 1px solid rgba(255,255,255,0.32);
                border-radius: 6px;
                color: #ffffff;
                padding: 9px 10px;
                font-size: 14px;
                selection-background-color: rgba(2,136,209,0.55);
            }
            QFrame#LoginGlassPanel QLineEdit:focus {
                border: 1px solid rgba(255,255,255,0.52);
            }
            QFrame#LoginGlassPanel QPushButton {
                background-color: rgba(255,255,255,0.22);
                color: #ffffff;
                border: 1px solid rgba(255,255,255,0.38);
                border-radius: 8px;
                padding: 9px 18px;
                font-weight: 600;
                font-size: 13px;
            }
            QFrame#LoginGlassPanel QPushButton:hover {
                background-color: rgba(255,255,255,0.32);
            }
            QFrame#LoginGlassPanel QPushButton:pressed {
                background-color: rgba(255,255,255,0.16);
            }
            QFrame#LoginGlassPanel QPushButton#LoginMissHit {
                background: transparent;
                border: none;
                padding: 0;
            }
            QFrame#LoginGlassPanel QPushButton#LoginMissHit:hover,
            QFrame#LoginGlassPanel QPushButton#LoginMissHit:pressed {
                background: transparent;
                border: none;
            }
        """
        self.setStyleSheet(
            "QDialog#LoginDialogRoot { background: transparent; border: none; }\n"
            + glass_qss
            + inner_qss
        )
        apply_window_icon(self)

    def showEvent(self, event):  # noqa: N802
        # Принудительно раскрываем на весь экран без визуальных отступов по краям.
        app = QApplication.instance()
        scr = app.primaryScreen() if app else None
        if scr is not None:
            self.setGeometry(scr.geometry())
        self.setWindowState(self.windowState() | Qt.WindowFullScreen)
        super().showEvent(event)

    def do_login(self):
        login = self.login_edit.text().strip()
        password = (self.password_edit.text() or "").strip()
        if not login:
            QMessageBox.warning(self, "Вход", "Введите логин.")
            return
        user = get_user_by_login(login)
        if not user:
            QMessageBox.warning(self, "Вход", "Неверный логин или пароль.")
            return
        if not check_password(user, password):
            QMessageBox.warning(self, "Вход", "Неверный логин или пароль.")
            return
        origin = (user.get("account_origin") or "").strip().lower()
        if origin == ACCOUNT_ORIGIN_WEB:
            QMessageBox.warning(
                self,
                "Вход",
                "Эта учётная запись зарегистрирована на сайте (WEB_SERVICE).\n"
                "В главную программу вход невозможен — используйте веб-кабинет по адресу сервиса.",
            )
            return
        if user.get('blocked'):
            QMessageBox.warning(self, "Вход", "Учётная запись заблокирована.")
            return
        if not user.get('approved'):
            QMessageBox.warning(
                self, "Вход",
                "Учётная запись ожидает подтверждения менеджером с полным доступом.",
            )
            return
        self._user = user
        self.accept()

    def do_register(self):
        d = RegisterDialog(self)
        d.exec_()

    def get_user(self):
        return self._user
