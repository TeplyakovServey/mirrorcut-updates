"""Status board: last N orders, 'Show more' modal, order card with hold-to-complete button."""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QDialog, QDialogButtonBox, QGridLayout,
    QMessageBox, QSizePolicy, QFileDialog,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
import os
import shutil
import subprocess


# Свежий интерфейс: компактные карточки, читаемый текст
STYLE_CARD = """
    QFrame {
        background-color: #F5F8FA;
        border-radius: 6px;
        padding: 4px 6px;
        border: 1px solid #D1E0E8;
    }
    QFrame:hover { background-color: #E8F0F5; border-color: #A8C5D4; }
    QLabel { color: #2C3E50; font-size: 12px; }
"""
STYLE_IN_PROGRESS = "color: #1a5f2a; font-weight: 500; font-size: 11px;"
STYLE_COMPLETED = "color: #0d4d1a; font-weight: 500; font-size: 11px;"


class PulsingDot(QLabel):
    """Зелёная пульсирующая точка для статуса «В работе»."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._bright = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._toggle)
        self._timer.start(450)
        self._update_style()

    def _toggle(self):
        self._bright = not self._bright
        self._update_style()

    def _update_style(self):
        color = "#22c55e" if self._bright else "#16a34a"
        self.setStyleSheet("background: %s; border-radius: 5px; border: none;" % color)

    def stop(self):
        self._timer.stop()


class HoldButton(QPushButton):
    """Button that requires holding 2 seconds to trigger."""
    triggered = pyqtSignal()

    def __init__(self, text="Выполнено (удерживайте 2 сек)", parent=None, hold_ms=2000, default_text=None):
        super().__init__(text, parent)
        self._default_text = default_text or text
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_held)
        self._hold_ms = hold_ms
        self._progress = 0
        self.setMinimumHeight(36)

    def mousePressEvent(self, e):
        try:
            if e.button() == Qt.LeftButton:
                self._progress = 0
                self._timer.start(50)
            super().mousePressEvent(e)
        except RuntimeError:
            pass

    def mouseReleaseEvent(self, e):
        try:
            if e.button() == Qt.LeftButton:
                self._timer.stop()
                self._progress = 0
                self.setText(self._default_text)
            super().mouseReleaseEvent(e)
        except RuntimeError:
            pass

    def _on_held(self):
        self._progress += 50
        pct = min(100, self._progress * 100 // self._hold_ms)
        self.setText("Удерживайте... %d%%" % pct)
        if self._progress >= self._hold_ms:
            self._timer.stop()
            self._progress = 0
            self.triggered.emit()
            self.setText(self._default_text)


class OrderCard(QFrame):
    """Компактная карточка заказа на табло: номер, дата, статус (на одном листе могут быть разные получатели — клиент не показываем)."""
    clicked = pyqtSignal(object)

    def __init__(self, order_data, parent=None):
        super().__init__(parent)
        self.order_data = order_data
        self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(6, 3, 6, 3)
        oid = order_data.get('id') or '—'
        created = order_data.get('created_at')
        created = created.strftime('%d.%m %H:%M') if hasattr(created, 'strftime') else (str(created)[:12] if created else '—')
        status = order_data.get('status') or 'in_progress'
        status_text = "В работе" if status == 'in_progress' else "Выполнен"
        layout.addWidget(QLabel("#%s" % oid))
        layout.addWidget(QLabel(created), 1)
        status_row = QHBoxLayout()
        status_row.setSpacing(4)
        if status == 'in_progress':
            self._dot = PulsingDot(self)
            status_row.addWidget(self._dot)
        else:
            self._dot = None
        status_lbl = QLabel(status_text)
        status_lbl.setStyleSheet(STYLE_IN_PROGRESS if status == 'in_progress' else STYLE_COMPLETED)
        status_row.addWidget(status_lbl)
        layout.addLayout(status_row)
        self.setStyleSheet(STYLE_CARD)
        self.setLayout(layout)
        self.setMaximumHeight(36)

    def mousePressEvent(self, e):
        try:
            if e.button() == Qt.LeftButton:
                self.clicked.emit(self.order_data)
            super().mousePressEvent(e)
        except RuntimeError:
            # Виджет уже удалён (закрыли модалку, обновили список) — игнорируем событие
            pass


class OrderDetailDialog(QDialog):
    """Модальное окно заказа: миниатюра макета, макет(и), PDF, этикетка, смена статуса, удаление."""
    open_scheme_requested = pyqtSignal(object, list)  # order_data, layouts to show
    order_deleted = pyqtSignal(object)  # order_id

    def __init__(self, order_data, parent=None):
        super().__init__(parent)
        self.order_data = order_data
        self.setWindowTitle("Заказ #%s" % order_data.get('id'))
        self.setMinimumSize(520, 320)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        created = order_data.get('created_at')
        created_str = created.strftime('%d.%m.%Y %H:%M') if hasattr(created, 'strftime') else str(created or '')[:16]
        layout.addWidget(QLabel("Дата: %s  |  Статус: %s" % (
            created_str, 'В работе' if order_data.get('status') == 'in_progress' else 'Выполнен')))
        from db import models
        results = models.get_cut_results(order_data.get('id'))
        layout_dicts = []
        for r in results:
            lay = r.get('layout')
            if isinstance(lay, dict):
                layout_dicts.append(lay)
            elif lay:
                layout_dicts.append({'pieces': lay, 'waste_rects': [], 'business_rects': [], 'sheet_width': 0, 'sheet_height': 0})
        self._layout_dicts = layout_dicts
        btn_row = QHBoxLayout()
        if not layout_dicts:
            btn_scheme = QPushButton("Показать макет")
            btn_scheme.setEnabled(False)
            btn_scheme.setMinimumHeight(32)
            btn_row.addWidget(btn_scheme)
        elif len(layout_dicts) == 1:
            btn_scheme = QPushButton("Показать макет")
            btn_scheme.setMinimumHeight(32)
            btn_scheme.clicked.connect(lambda: self.open_scheme_requested.emit(self.order_data, list(self._layout_dicts)))
            btn_row.addWidget(btn_scheme)
        else:
            for i in range(len(layout_dicts)):
                btn = QPushButton("Показать макет %d" % (i + 1))
                btn.setMinimumHeight(32)
                btn.setToolTip("Открыть полную схему раскроя (все листы заказа)")
                btn.clicked.connect(
                    lambda checked=False: self.open_scheme_requested.emit(self.order_data, list(self._layout_dicts))
                )
                btn_row.addWidget(btn)
        self.btn_pdf = QPushButton("Скачать PDF")
        self.btn_pdf.setMinimumHeight(32)
        btn_row.addWidget(self.btn_pdf)
        self.btn_label = QPushButton("Печать этикетки")
        self.btn_label.setMinimumHeight(32)
        if order_data.get('status') != 'completed':
            self.btn_label.setEnabled(False)
            self.btn_label.setToolTip("Доступно только для выполненного заказа")
        btn_row.addWidget(self.btn_label)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        # Кнопка «Выполнено» с удержанием 2 сек
        self.hold_btn = HoldButton()
        self.hold_btn.triggered.connect(self._on_completed)
        if order_data.get('status') == 'completed':
            self.hold_btn.setEnabled(False)
            self.hold_btn.setText("Заказ выполнен")
        else:
            self.hold_btn.setMinimumHeight(32)
        layout.addWidget(self.hold_btn)
        # Кнопка «Удалить заказ» — красная, удержание 2 сек
        self._delete_btn = HoldButton(
            text="Удалить заказ (удерживайте 2 сек)",
            hold_ms=2000,
            default_text="Удалить заказ (удерживайте 2 сек)",
        )
        self._delete_btn.setMinimumHeight(32)
        self._delete_btn.setStyleSheet(
            "QPushButton { background-color: #DC3545; color: white; border-radius: 5px; padding: 6px 12px; }"
            "QPushButton:hover { background-color: #C82333; }"
            "QPushButton:disabled { background-color: #6c757d; }"
        )
        self._delete_btn.triggered.connect(self._on_delete_order)
        layout.addWidget(self._delete_btn)
        layout.addStretch(1)
        self.setStyleSheet("""
            QDialog { background-color: #E6F2FF; }
            QPushButton { background-color: #4682B4; color: white; border-radius: 5px; padding: 6px 12px; }
            QPushButton:hover { background-color: #5A9BD5; }
        """)

    def _on_completed(self):
        from db import models
        from app_state import refresh_clients
        order_id = self.order_data['id']
        ok, missing = models.verify_sheets_for_order(order_id)
        if not ok:
            QMessageBox.warning(
                self,
                "Нельзя выполнить заказ",
                "Часть задействованных листов отсутствует в складе:\n• %s\n\nПроверьте склад и повторите попытку." % ("\n• ".join(missing)),
            )
            return
        models.set_order_client_on_complete(order_id)
        models.apply_pending_cut(order_id)
        models.set_order_status(order_id, 'completed')
        refresh_clients()
        self.order_data['status'] = 'completed'
        self.hold_btn.setEnabled(False)
        self.hold_btn.setText("Заказ выполнен")
        QMessageBox.information(self, "Готово", "Заказ выполнен. Листы списаны, остатки записаны в склад.")
        self.accept()

    def _on_delete_order(self):
        from db import models
        def _open_path(path: str):
            p = str(path or "").strip()
            if not p:
                return
            try:
                os.startfile(p)  # type: ignore[attr-defined]
            except Exception:
                try:
                    subprocess.Popen([p], shell=True)
                except Exception:
                    pass

        order_id = self.order_data['id']
        if QMessageBox.Yes != QMessageBox.question(
            self, "Удалить заказ?",
            "Заказ #%s будет удалён навсегда. Продолжить?" % order_id,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ):
            return
        n = models.delete_order(order_id)
        if n:
            try:
                rep = models.get_last_delete_order_report(order_id) or {}
            except Exception:
                rep = {}
            msg = "Заказ удалён."
            if rep.get("converted_to_remnants"):
                msg = (
                    "Заказ удалён.\n"
                    "Клиентские куски переведены в остатки: %s шт.\n"
                    "PDF этикеток: %s"
                ) % (
                    int(rep.get("created_remnants_count") or 0),
                    str(rep.get("labels_pdf_path") or "—"),
                )
                box = QMessageBox(self)
                box.setWindowTitle("Удаление")
                box.setText(msg)
                btn_open = box.addButton("Открыть PDF", QMessageBox.ActionRole)
                btn_save = box.addButton("Скачать PDF как…", QMessageBox.ActionRole)
                box.addButton(QMessageBox.Ok)
                box.exec_()
                picked = box.clickedButton()
                pdf_path = str(rep.get("labels_pdf_path") or "").strip()
                if picked == btn_open:
                    _open_path(pdf_path)
                elif picked == btn_save:
                    target, _ = QFileDialog.getSaveFileName(
                        self,
                        "Скачать PDF",
                        os.path.basename(pdf_path) or "deleted_order_labels.pdf",
                        "PDF files (*.pdf)",
                    )
                    if target and pdf_path and os.path.isfile(pdf_path):
                        try:
                            shutil.copyfile(pdf_path, target)
                        except Exception as ex:
                            QMessageBox.warning(self, "Скачивание", "Не удалось сохранить PDF: %s" % ex)
            elif rep.get("restored_as_is"):
                msg = "Заказ удалён. Материал возвращён как был (без подтверждённого раскроя)."
                QMessageBox.information(self, "Удаление", msg)
            else:
                QMessageBox.information(self, "Удаление", msg)
            self.order_deleted.emit(order_id)
            self.accept()
        else:
            QMessageBox.warning(self, "Ошибка", "Не удалось удалить заказ.")


class StatusBoardWidget(QWidget):
    """Shows last 10 orders; 'Show more' opens full list."""
    order_clicked = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setSpacing(10)
        self.scroll.setWidget(self.cards_container)
        layout.addWidget(self.scroll)
        self.btn_more = QPushButton("Показать ещё")
        self.btn_more.clicked.connect(self._show_all)
        layout.addWidget(self.btn_more)
        self._orders = []
        self.setStyleSheet("background-color: #E6F2FF;")

    def set_orders(self, orders):
        self._orders = orders or []
        # Clear cards
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for o in self._orders[:10]:
            card = OrderCard(o, self)
            card.clicked.connect(self.order_clicked.emit)
            self.cards_layout.addWidget(card)
        self.cards_layout.addStretch()

    def _show_all(self):
        d = QDialog(self)
        d.setWindowTitle("Все заказы")
        d.setMinimumSize(600, 500)
        layout = QVBoxLayout(d)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        cont = QWidget()
        v = QVBoxLayout(cont)
        for o in self._orders:
            card = OrderCard(o, d)
            card.clicked.connect(lambda od=o: (self.order_clicked.emit(od), d.accept()))
            v.addWidget(card)
        v.addStretch()
        scroll.setWidget(cont)
        layout.addWidget(scroll)
        d.setStyleSheet("background-color: #E6F2FF;")
        d.exec_()


class FullListDialog(QDialog):
    """Full list of orders (in work + completed)."""
    order_clicked = pyqtSignal(object)

    def __init__(self, orders, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Все заказы")
        self.setMinimumSize(650, 550)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        cont = QWidget()
        v = QVBoxLayout(cont)
        in_work = [o for o in orders if o.get('status') == 'in_progress']
        completed = [o for o in orders if o.get('status') == 'completed']
        if in_work:
            v.addWidget(QLabel("В работе:"))
            for o in in_work:
                card = OrderCard(o, self)
                card.clicked.connect(lambda od=o: self._emit_and_close(od))
                v.addWidget(card)
        v.addWidget(QLabel("Выполненные:"))
        for o in completed:
            card = OrderCard(o, self)
            card.clicked.connect(lambda od=o: self._emit_and_close(od))
            v.addWidget(card)
        v.addStretch()
        scroll.setWidget(cont)
        layout.addWidget(scroll)
        self.setStyleSheet("background-color: #E6F2FF;")

    def _emit_and_close(self, od):
        self.order_clicked.emit(od)
        self.accept()
