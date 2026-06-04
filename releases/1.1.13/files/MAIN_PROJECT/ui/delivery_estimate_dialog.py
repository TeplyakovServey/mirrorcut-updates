# -*- coding: utf-8 -*-
"""Просчёт замер / доставка / монтаж без сохранения в заказ."""
from __future__ import annotations

import os
import sys

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

_blocks_dir = os.path.normpath(os.path.join(_mp, "BLOCKS"))
if _blocks_dir not in sys.path:
    sys.path.insert(0, _blocks_dir)

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QDialog, QVBoxLayout

from calc.delivery_calc import delivery_price_rub, fetch_delivery_prices
from elements.zamer_tile import ZamerTile
from window_branding import apply_window_icon, available_screen_geometry


class _EstimateClientProxy:
    """Заглушка client_strip: без привязки к клиенту заказа."""

    def get_payload(self):
        return {"id": None, "Имя": ""}


class DeliveryEstimateDialog(QDialog):
    """Окно просчёта — только плитка «Замер | доставка | монтаж»; цены на самой плитке."""

    _FRAME_PAD_W = 14
    _FRAME_PAD_H = 38

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Просчёт доставки")
        self.setModal(True)
        apply_window_icon(self)

        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(30)
        self._fit_timer.timeout.connect(self._resize_to_tile)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(0)

        self._zamer = ZamerTile(_EstimateClientProxy(), self)
        self._zamer.blockSignals(True)
        self._zamer.reset_to_defaults()
        self._zamer.chk_measure.setVisible(True)
        self._zamer.chk_install.setVisible(True)
        self._zamer.chk_delivery.setVisible(True)
        self._apply_estimate_only_chrome()
        self._zamer.blockSignals(False)
        self._zamer.visitChanged.connect(self._on_tile_changed)
        root.addWidget(self._zamer, 0, Qt.AlignCenter)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._resize_to_tile()

    def _on_tile_changed(self) -> None:
        self._refresh_visit_price_on_tile()
        self._fit_timer.start()

    def _apply_estimate_only_chrome(self) -> None:
        z = self._zamer
        z.btn_save.hide()
        z.btn_files.hide()
        z.btn_dl.hide()
        z.chk_match.hide()
        try:
            z._poll.stop()
        except Exception:
            pass

    def _resize_to_tile(self) -> None:
        if not self.isVisible():
            return
        self._zamer.adjustSize()
        lay = self.layout()
        m = lay.contentsMargins() if lay else None
        ml = m.left() if m else 6
        mr = m.right() if m else 6
        mt = m.top() if m else 6
        mb = m.bottom() if m else 6
        tw = max(self._zamer.sizeHint().width(), self._zamer.width(), 200)
        th = max(self._zamer.sizeHint().height(), self._zamer.height(), 120)
        w = tw + ml + mr + self._FRAME_PAD_W
        h = th + mt + mb + self._FRAME_PAD_H
        self.setMinimumSize(w, h)
        self.setMaximumSize(w, h)
        self.resize(w, h)
        geo = available_screen_geometry(self)
        if geo is not None:
            fg = self.frameGeometry()
            fg.moveCenter(geo.center())
            self.move(fg.topLeft())

    @staticmethod
    def _visit_inside_and_km(zd: dict):
        vd = zd.get("Данные выезда") if isinstance(zd.get("Данные выезда"), dict) else {}
        inside = bool(vd.get("Внутри КАД", True))
        km = vd.get("Расстояние до КАД")
        try:
            km_i = int(km) if km is not None else None
        except (TypeError, ValueError):
            km_i = None
        return inside, km_i

    @staticmethod
    def _visit_tariff_lines(prices: dict, inside: bool, km, vd: dict) -> tuple:
        if inside:
            pin = int(prices.get("В пределах КАД", 0) or 0)
            pr = delivery_price_rub(prices, True, None)
            return pr, ["Тариф в пределах КАД: %s ₽" % pin, "Итого выезд: %s ₽" % pr]
        if km is None:
            return None, []
        base = int(prices.get("За КАД база", 0) or 0)
        pk = int(prices.get("За 1 км", 0) or 0)
        km_i = int(km)
        pr = delivery_price_rub(prices, False, km_i)
        lines = []
        addr = (vd.get("Адрес") or "").strip() if isinstance(vd, dict) else ""
        if addr:
            lines.append(addr[:56] + ("…" if len(addr) > 58 else ""))
        dm = vd.get("Расстояние маршрута м") if isinstance(vd, dict) else None
        if dm is not None:
            try:
                lines.append("Длина маршрута: %.2f км" % (float(dm) / 1000.0))
            except (TypeError, ValueError):
                pass
        lines.extend(
            [
                "До границы КАД (тариф): %s км" % km_i,
                "База за пределами КАД: %s ₽" % base,
                "Пробег: %s км × %s ₽/км = %s ₽" % (km_i, pk, km_i * pk),
                "Итого выезд: %s ₽" % pr,
            ]
        )
        return pr, lines

    def _refresh_visit_price_on_tile(self) -> None:
        z = self._zamer
        if not z.has_any_service():
            z.set_visit_price_detail(None, [])
            return
        blk = z.to_selected_block()
        zd = blk.get("Данные") if isinstance(blk.get("Данные"), dict) else {}
        needs_route = bool(zd.get("Доставка") or zd.get("Замер"))
        if not needs_route:
            z.set_visit_price_detail(None, [])
            return
        if not z.is_configured_for_visit_price():
            z.set_visit_price_detail(None, [])
            return
        try:
            prices = fetch_delivery_prices()
        except Exception:
            z.set_visit_price_detail(None, [])
            return
        inside, km = self._visit_inside_and_km(zd)
        vd = zd.get("Данные выезда") if isinstance(zd.get("Данные выезда"), dict) else {}
        pr, tlines = self._visit_tariff_lines(prices, inside, km, vd)
        z.set_visit_price_detail(pr, tlines)
