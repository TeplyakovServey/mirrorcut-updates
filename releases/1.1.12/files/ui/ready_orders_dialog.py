"""Готовые заказы: список завершённых заказов, детали по клику (клиент, даты, листы, выкрои, площадь м²)."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox, QGroupBox, QScrollArea, QWidget, QGridLayout,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from db import models


def _fmt_dt(dt):
    if dt is None:
        return "—"
    return dt.strftime("%d.%m.%Y %H:%M") if hasattr(dt, 'strftime') else str(dt)[:16]


class ReadyOrderDetailDialog(QDialog):
    """Полная информация о готовом заказе: клиент, даты, из каких листов, что получено, выкрои, площадь м²."""

    def __init__(self, order_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Готовый заказ #%s" % order_id)
        self.setMinimumSize(520, 480)
        summary = models.order_ready_summary(order_id)
        if not summary:
            QMessageBox.warning(self, "Ошибка", "Заказ не найден.")
            return
        layout = QVBoxLayout(self)
        order = summary.get('order') or {}

        # Клиент и даты
        grp = QGroupBox("Клиент и даты")
        gl = QGridLayout(grp)
        k_num = order.get('k_number')
        if k_num:
            gl.addWidget(QLabel("Номер продукта (K):"), 0, 0)
            gl.addWidget(QLabel("K%s" % k_num), 0, 1)
        gl.addWidget(QLabel("Клиент:"), 1 if k_num else 0, 0)
        gl.addWidget(QLabel(summary.get('client_name') or "—"), 1 if k_num else 0, 1)
        gl.addWidget(QLabel("Создан:"), 2 if k_num else 1, 0)
        gl.addWidget(QLabel(_fmt_dt(summary.get('created_at'))), 2 if k_num else 1, 1)
        gl.addWidget(QLabel("Выполнен:"), 3 if k_num else 2, 0)
        gl.addWidget(QLabel(_fmt_dt(summary.get('accepted_at'))), 3 if k_num else 2, 1)
        layout.addWidget(grp)

        # Из каких листов
        grp2 = QGroupBox("Из каких листов")
        grp2_layout = QVBoxLayout(grp2)
        from_sheets = summary.get('from_sheets') or []
        grp2_layout.addWidget(QLabel(", ".join(from_sheets) if from_sheets else "—"))
        layout.addWidget(grp2)

        # Какие листы получены (остатки)
        grp3 = QGroupBox("Полученные листы (остатки)")
        grp3_layout = QVBoxLayout(grp3)
        obtained = summary.get('sheets_obtained') or []
        grp3_layout.addWidget(QLabel(", ".join(obtained) if obtained else "—"))
        layout.addWidget(grp3)

        # Деловые остатки
        grp4 = QGroupBox("Деловые остатки (размеры)")
        grp4_layout = QVBoxLayout(grp4)
        br = summary.get('business_rects') or []
        grp4_layout.addWidget(QLabel(", ".join(br) if br else "—"))
        layout.addWidget(grp4)

        # Выкрои и площадь
        cutouts = summary.get('cutouts') or []
        area_m2 = summary.get('total_area_m2') or 0
        grp5 = QGroupBox("Выкрои и площадь")
        grp5_layout = QVBoxLayout(grp5)
        if cutouts:
            text = "%d шт." % len(cutouts)
            sizes = ["%s×%s" % (c.get('w'), c.get('h')) for c in cutouts[:15]]
            text += " — " + ", ".join(sizes)
            if len(cutouts) > 15:
                text += " …"
            grp5_layout.addWidget(QLabel(text))
        grp5_layout.addWidget(QLabel("Суммарная площадь выкроек: %s м²" % ("%.4f" % area_m2)))
        layout.addWidget(grp5)

        layout.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)


class ReadyOrdersDialog(QDialog):
    """Список готовых заказов: номер, клиент, из листов, получено, деловые остатки, выкрои, площадь м²."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Готовые заказы")
        self.setMinimumSize(900, 500)
        layout = QVBoxLayout(self)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "K", "Заказ", "Клиент", "Из листов", "Получено листов", "Деловые остатки", "Выкрои", "Площадь, м²"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.cellDoubleClicked.connect(self._open_detail)
        layout.addWidget(self.table)

        hint = QLabel("Двойной клик по строке — подробности заказа.")
        hint.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(hint)

        self._fill()

    def _fill(self):
        orders = models.get_orders_completed()
        self.table.setRowCount(len(orders))
        for i, o in enumerate(orders):
            oid = o.get('id')
            k_num = o.get('k_number')
            self.table.setItem(i, 0, QTableWidgetItem("K%s" % k_num if k_num else "—"))
            self.table.setItem(i, 1, QTableWidgetItem(str(oid)))
            self.table.setItem(i, 2, QTableWidgetItem((o.get('client_name') or '—')[:40]))
            summary = models.order_ready_summary(oid) if oid else None
            if summary:
                from_s = ", ".join((summary.get('from_sheets') or [])[:3])
                if len(summary.get('from_sheets') or []) > 3:
                    from_s += "…"
                self.table.setItem(i, 3, QTableWidgetItem(from_s or "—"))
                obtained = summary.get('sheets_obtained') or []
                self.table.setItem(i, 4, QTableWidgetItem(str(len(obtained))))
                br = summary.get('business_rects') or []
                self.table.setItem(i, 5, QTableWidgetItem(str(len(br))))
                cut = summary.get('cutouts') or []
                self.table.setItem(i, 6, QTableWidgetItem(str(len(cut))))
                self.table.setItem(i, 7, QTableWidgetItem("%.4f" % (summary.get('total_area_m2') or 0)))
            else:
                self.table.setItem(i, 3, QTableWidgetItem("—"))
                self.table.setItem(i, 4, QTableWidgetItem("—"))
                self.table.setItem(i, 5, QTableWidgetItem("—"))
                self.table.setItem(i, 6, QTableWidgetItem("—"))
                self.table.setItem(i, 7, QTableWidgetItem("—"))
        if not orders:
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem("Нет готовых заказов"))
            for c in range(1, 8):
                self.table.setItem(0, c, QTableWidgetItem(""))

    def _on_selection(self):
        pass

    def _open_detail(self, row, col):
        item = self.table.item(row, 1)
        if not item:
            return
        try:
            oid = int(item.text())
        except ValueError:
            return
        if oid <= 0:
            return
        d = ReadyOrderDetailDialog(oid, self)
        d.exec_()
