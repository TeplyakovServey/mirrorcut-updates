# -*- coding: utf-8 -*-
"""Боковая панель промежуточного расчёта в просчёте стекла."""
from __future__ import annotations

from PyQt5.QtCore import Qt, QSettings
from PyQt5.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_SETTINGS_KEY = "glass_calc_summary_collapsed"


class CalcSummaryPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded_width = 300
        self._collapsed = bool(QSettings().value(_SETTINGS_KEY, False, type=bool))
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._handle = QFrame()
        self._handle.setFixedWidth(10)
        self._handle.setCursor(Qt.PointingHandCursor)
        self._handle.setToolTip("Свернуть / развернуть панель расчёта")
        self._handle.mouseReleaseEvent = lambda _e: self.toggle_collapsed()
        self._apply_handle_style(False)
        root.addWidget(self._handle)

        self._content = QFrame()
        self._content.setMinimumWidth(self._expanded_width)
        self._content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        try:
            from calc import palette as P

            bg = P.TILE_SURFACE
            border = P.CONTROL_BORDER
        except Exception:
            bg = "#dff6fc"
            border = "#90caf9"
        self._content.setStyleSheet(
            "QFrame { background-color: %s; border-left: 2px solid %s; }" % (bg, border)
        )
        cl = QVBoxLayout(self._content)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(4)

        title = QLabel("Расчёт")
        title.setStyleSheet("font-weight: 700; color: #1a365d;")
        cl.addWidget(title)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Услуга", "₽", "Комментарий"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setStretchLastSection(True)
        hdr.setMinimumSectionSize(40)
        self._table.setColumnWidth(0, 240)
        self._table.setColumnWidth(1, 56)
        self._table.setColumnWidth(2, 120)
        vhdr = self._table.verticalHeader()
        vhdr.setVisible(True)
        vhdr.setSectionResizeMode(QHeaderView.Interactive)
        vhdr.setDefaultSectionSize(22)
        vhdr.setMinimumSectionSize(18)
        self._table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.NoSelection)
        self._table.setWordWrap(True)
        cl.addWidget(self._table, 1)

        self._lbl_item = QLabel("Итого по изделию: —")
        self._lbl_item.setWordWrap(True)
        self._lbl_item.setStyleSheet("font-size: 17px; color: #1a365d; padding-left: 12px;")
        cl.addWidget(self._lbl_item)
        self._lbl_geom = QLabel("")
        self._lbl_geom.setWordWrap(True)
        self._lbl_geom.setStyleSheet("font-size: 15px; color: #455a64; padding-left: 12px;")
        cl.addWidget(self._lbl_geom)
        self._lbl_order = QLabel("Итого по заказу: —")
        self._lbl_order.setWordWrap(True)
        self._lbl_order.setStyleSheet("font-weight: 700; font-size: 17px; color: #1b5e20; padding-left: 12px;")
        cl.addWidget(self._lbl_order)

        root.addWidget(self._content, 1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._apply_collapsed_state()

    @staticmethod
    def _normalize_comment(note: str) -> str:
        s = (note or "").strip()
        if not s or s == "—" or s.replace("—", "").strip() == "":
            return ""
        if s.lower() == "стоимость материала":
            return ""
        return s

    def _apply_table_row_heights(self) -> None:
        self._table.resizeRowsToContents()

    def _apply_handle_style(self, hover: bool) -> None:
        col = "#64b5f6" if hover else "#90caf9"
        self._handle.setStyleSheet(
            "QFrame { background-color: %s; border-left: 2px solid #1976d2; }" % col
        )

    def enterEvent(self, event):
        self._apply_handle_style(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._apply_handle_style(False)
        super().leaveEvent(event)

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        QSettings().setValue(_SETTINGS_KEY, self._collapsed)
        self._apply_collapsed_state()

    def _apply_collapsed_state(self) -> None:
        self._content.setVisible(not self._collapsed)
        if self._collapsed:
            self.setFixedWidth(10)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        else:
            self.setMinimumWidth(self._expanded_width + 10)
            self.setMaximumWidth(16777215)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def refresh_from_apps(self, apps, active_index: int = 0) -> None:
        from calc.geometry import izd_area_m2_for_tariff, izd_perimeter_mm_for_display
        from calc.order_summary import collect_line_items

        apps = [a for a in (apps or []) if a is not None]
        if not apps:
            self._table.setRowCount(0)
            self._lbl_item.setText("Итого по изделию: —")
            self._lbl_geom.setText("")
            self._lbl_order.setText("Итого по заказу: —")
            return

        idx = max(0, min(int(active_index), len(apps) - 1))
        active = apps[idx]
        try:
            rows, total, _w = collect_line_items(active.selected, active, conn=None)
        except Exception:
            rows, total = [], 0

        self._table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            if isinstance(row, (list, tuple)) and len(row) >= 2:
                name = str(row[0] or "")
                price = row[1]
                note = str(row[2] if len(row) > 2 else "")
            elif isinstance(row, dict):
                name = str(row.get("name") or row.get("Услуга") or "")
                price = row.get("price") or row.get("₽") or 0
                note = str(row.get("note") or row.get("Комментарий") or "")
            else:
                name, price, note = str(row), 0, ""
            note = self._normalize_comment(note)
            self._table.setItem(r, 0, QTableWidgetItem(name))
            self._table.setItem(r, 1, QTableWidgetItem(str(int(price) if price else 0)))
            self._table.setItem(r, 2, QTableWidgetItem(note))
        self._apply_table_row_heights()

        izd = (active.selected or {}).get("Параметры изделия") or {}
        q = int(izd.get("Количество (шт)") or 1)
        per_one = int(round(int(total) / q)) if q > 1 else int(total)
        self._lbl_item.setText("Итого по изделию: %s ₽" % int(total))
        s_tot = izd_area_m2_for_tariff(izd)
        p_tot = izd_perimeter_mm_for_display(izd)
        geom_parts = []
        if s_tot is not None:
            geom_parts.append("S=%s м²" % round(float(s_tot), 4))
        if p_tot is not None:
            geom_parts.append("P=%s мм" % int(p_tot))
        if q > 1:
            geom_parts.append("%s шт." % q)
        self._lbl_geom.setText(" · ".join(geom_parts))

        order_total = 0
        for app in apps:
            try:
                _r, t, _w2 = collect_line_items(app.selected, app, conn=None)
                order_total += int(t or 0)
            except Exception:
                pass
        if len(apps) > 1:
            self._lbl_order.setText(
                "Итого по заказу (%s изд.): %s ₽" % (len(apps), int(order_total))
            )
        else:
            self._lbl_order.setText("Итого по заказу: %s ₽" % int(order_total))
