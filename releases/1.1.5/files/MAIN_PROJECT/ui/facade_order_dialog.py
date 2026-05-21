# -*- coding: utf-8 -*-
"""Диалог расчёта фасадов: клиент, размеры, канвас фасада, профили, петли, слайдшоу playboy."""
import sys
import os
import time
import math

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QFrame,
    QWidget,
    QSplitter,
    QTabWidget,
    QScrollArea,
    QMessageBox,
    QToolTip,
    QInputDialog,
    QComboBox,
    QRadioButton,
    QFileDialog,
    QSizePolicy,
    QDialogButtonBox,
    QProgressBar,
    QCheckBox,
)
from PyQt5.QtCore import Qt, QTimer, QRect, QPoint, pyqtSignal, QObject, QEvent
from PyQt5.QtGui import (
    QPainter,
    QFont,
    QFontMetrics,
    QPixmap,
    QColor,
    QPolygon,
    QPen,
    QBrush,
    QImage,
)

import json
import re
import uuid
from typing import Any, Optional, Tuple
from collections import defaultdict

from cfg_loader import color, get_base_dir, get_mirror_cut_root
from db import models as db_models

from db_main import facades_get_all_angle_seal
from logic.production_instructions import _norm_side_key
from logic.blocks_bundle import (
    product_sum_excluding_order_level,
    parse_bundle,
    bundle_to_json,
    infer_order_kind_for_db,
)
from ui.facade_profile_dialog import _fasad_img_path
from ui.facade_hinge_dialog import resolve_hinge_image_path

from ui.facade_fitting_widgets import HoleMarkerWidget, FittingSwitchLabeled, MarkerLockButton
from ui.facade_constants import FITTING_SIDE_LABELS_RU
_blocks_dir = os.path.normpath(os.path.join(_mp, "BLOCKS"))
if _blocks_dir not in sys.path:
    sys.path.insert(0, _blocks_dir)
from elements.zamer_tile import ZamerTile
from calc.delivery_calc import delivery_price_rub, fetch_delivery_prices

# Поля внутри виджета для подписей профиля (доля от меньшей стороны виджета)
CANVAS_MARGIN_FR = 0.065
LABEL_H = 40

HINGE_SUPPLIERS_FITTING = (
    "МДМ (Pulse)",
    "Blum",
    "Hettich",
    "Boyard",
    "Китай",
    "другое",
)

# Первый пункт комбобокса — не сохраняется как поставщик (нужен осознанный выбор).
FITTING_SUPPLIER_PROMPT = "— выберите поставщика петли —"


def _is_valid_fitting_supplier_choice(text: str) -> bool:
    v = (text or "").strip()
    if not v or v in ("—", "–", "-") or v == FITTING_SUPPLIER_PROMPT:
        return False
    return v in HINGE_SUPPLIERS_FITTING


def _normalize_prisadka_list(prs):
    """Из payload: список записей присадки (по одной на сторону в логике UI)."""
    if isinstance(prs, list):
        return [dict(x) for x in prs if isinstance(x, dict) and (x.get("сторона") or x.get("side"))]
    if isinstance(prs, dict) and (prs.get("сторона") or prs.get("side")):
        return [dict(prs)]
    return []


def _prisadka_validate_suppliers(fittings, facade_tab_no: Optional[int] = None) -> tuple[bool, str]:
    """У каждой стороны с отверстиями должен быть указан поставщик петли из списка."""
    prefix = ("Фасад %d: " % int(facade_tab_no)) if facade_tab_no else ""
    for e in _normalize_prisadka_list(fittings):
        holes = e.get("отверстия") or e.get("holes") or []
        if not holes:
            continue
        sup = e.get("поставщик_петли") or e.get("supplier") or ""
        if not _is_valid_fitting_supplier_choice(str(sup)):
            side = e.get("сторона") or e.get("side") or "?"
            ru = FITTING_SIDE_LABELS_RU.get(side, str(side))
            shown = (str(sup).strip() or "—")[:80]
            return (
                False,
                "%sСторона «%s»: для присадки нужно указать поставщика петли "
                "(поле «Петля (поставщик)»). Сейчас в расчёте: «%s». "
                "Откройте режим присадки, выберите сторону и значение из списка, затем «Сохранить»."
                % (prefix, ru, shown),
            )
    return True, ""


class _FacadeClientProxy:
    def __init__(self, dlg):
        self._dlg = dlg

    def get_payload(self):
        cid = getattr(self._dlg, "_client_id", None)
        if cid is None and hasattr(self._dlg, "get_payload"):
            try:
                alt = self._dlg.get_payload()
                if isinstance(alt, dict) and alt.get("id") is not None:
                    cid = alt.get("id")
            except Exception:
                cid = None
        if hasattr(self._dlg, "client_edit"):
            name = (self._dlg.client_edit.text() or "").strip()
        else:
            od = getattr(self._dlg, "_order_data", None)
            name = (od.get("client_name") or "").strip() if isinstance(od, dict) else ""
        pl = {"id": cid, "Имя": name}
        qcid = getattr(self._dlg, "_quick_client_id", None)
        if qcid is not None:
            pl["quick_client_id"] = qcid
        return pl


class _FacadeServicesDialog(QDialog):
    """Услуги замер / доставка / монтаж на одной плитке (сводка заказа — только ZamerTile, без дублирующей панели)."""

    def __init__(
        self,
        owner,
        zamer_block,
        delivery_block,
        parent=None,
        *,
        order_status: Optional[str] = None,
        service_wants: Optional[Tuple[bool, bool, bool]] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Замер / доставка / монтаж")
        self._order_status = order_status
        self._service_wants = service_wants
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        self._zamer = ZamerTile(_FacadeClientProxy(owner), self)
        self._zamer.reset_to_defaults()
        self._zamer.apply_saved_block(zamer_block)
        self._zamer.merge_legacy_saved_delivery(delivery_block)
        self._zamer.hide_service_checkboxes_already_enabled(separate_delivery_activated=False)
        if self._service_wants is not None:
            wm, wi, wd = self._service_wants
            self._zamer.apply_cta_slot_visibility(wm, wi, wd)
        if self._order_status is not None:
            self._zamer.apply_measure_checkbox_order_policy(self._order_status)
        if self._service_wants is None:
            self._zamer.chk_measure.setVisible(True)
            self._zamer.chk_install.setVisible(True)
            self._zamer.chk_delivery.setVisible(True)

        lay.addWidget(self._zamer, 0)
        btn_save = QPushButton("Сохранить")
        btn_save.setMinimumHeight(38)
        btn_save.setStyleSheet(
            "QPushButton { background:#1976d2; color:white; font-weight:bold; border-radius:6px; padding:8px; }"
            "QPushButton:hover { background:#1565c0; }"
        )
        btn_save.clicked.connect(self._try_accept)
        lay.addWidget(btn_save, 0)
        self.adjustSize()
        sh = self.sizeHint()
        w = max(480, min(sh.width(), 900))
        h = max(380, min(sh.height(), 980))
        self.setMinimumSize(w, min(h, 980))
        self.resize(w, h)

    def _try_accept(self):
        z = self._zamer
        if self._service_wants is not None:
            if not (
                z.chk_measure.isChecked()
                or z.chk_install.isChecked()
                or z.chk_delivery.isChecked()
            ):
                QMessageBox.warning(
                    self,
                    "Услуги",
                    "На плитке отметьте хотя бы одну услугу: замер, доставку или монтаж.",
                )
                return
        self.accept()

    def blocks(self):
        return self._zamer.to_selected_block(), {"Активирован": False, "Данные": None}


class _HoldToDeleteRow(QWidget):
    """Виджет long-press удаления: наводим — красное поле, удерживаем 1 сек — удаляем."""

    def __init__(self, *, delete_callback, hold_seconds: float = 1.0, parent=None):
        super().__init__(parent)
        self._delete_callback = delete_callback
        self._hold_seconds = float(hold_seconds)

        self._holding = False
        self._deleting = False
        self._start_t = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._on_tick)

        self.setMouseTracking(True)
        self.setAutoFillBackground(True)
        self._hovering = False
        # Прозрачность прямоугольника (без отдельного progress-bar).
        self._hover_alpha = 60
        self._hold_alpha = 160
        self._set_alpha_bg(0)

        self._vlay = QVBoxLayout(self)
        self._vlay.setContentsMargins(0, 0, 0, 0)
        self._vlay.setSpacing(2)

        self._row = QWidget()
        hl = QHBoxLayout(self._row)
        hl.setContentsMargins(0, 3, 0, 3)
        hl.setSpacing(6)

        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(44, 44)
        self.icon_lbl.setStyleSheet("background: transparent; border: none;")
        hl.addWidget(self.icon_lbl)

        self.text_lbl = QLabel()
        self.text_lbl.setWordWrap(True)
        self.text_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.text_lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.text_lbl.setMinimumWidth(0)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        text_col.addWidget(self.text_lbl)

        self.cost_lbl = QLabel()
        self.cost_lbl.setFont(QFont("Arial", 13, QFont.Bold))
        self.cost_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.cost_lbl.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
        text_col.addWidget(self.cost_lbl, 0, Qt.AlignLeft | Qt.AlignTop)
        hl.addLayout(text_col, 1)

        self._vlay.addWidget(self._row)

    def _set_alpha_bg(self, alpha: int) -> None:
        alpha = max(0, min(255, int(alpha)))
        if alpha <= 0:
            self.setStyleSheet(
                "background-color: rgba(244,67,54,0); border: 1px solid rgba(244,67,54,0);"
            )
        else:
            # Тон красного постоянный, меняем только прозрачность.
            self.setStyleSheet(
                "background-color: rgba(244,67,54,%d); border: 1px solid rgba(244,67,54,%d);"
                % (alpha, min(255, alpha + 50))
            )

    def set_icon_pixmap(self, pix: QPixmap | None) -> None:
        if pix and not pix.isNull():
            self.icon_lbl.setPixmap(pix)
        else:
            self.icon_lbl.setStyleSheet("background: rgba(0,0,0,0.03); border: none;")

    def set_text(self, txt: str) -> None:
        self.text_lbl.setText(txt)

    def set_cost(self, txt: str) -> None:
        self.cost_lbl.setText(txt)

    def enterEvent(self, event):
        if not self._deleting:
            self._hovering = True
            self._set_alpha_bg(self._hover_alpha)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovering = False
        if not self._holding and not self._deleting:
            self._set_alpha_bg(0)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if self._deleting:
            return
        if event.button() == Qt.LeftButton:
            self._holding = True
            self._deleting = False
            self._start_t = time.monotonic()
            self._set_alpha_bg(self._hover_alpha)
            self._timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._timer.isActive():
            self._timer.stop()
        self._holding = False
        if not self._deleting:
            # Возвращаемся к «нулевому» состоянию удержания: прозрачность как при hover.
            self._set_alpha_bg(self._hover_alpha if self._hovering else 0)
        super().mouseReleaseEvent(event)

    def _on_tick(self):
        if not self._holding or self._deleting:
            return
        elapsed = time.monotonic() - self._start_t
        ratio = min(1.0, elapsed / max(0.001, self._hold_seconds))
        alpha = self._hover_alpha + (self._hold_alpha - self._hover_alpha) * ratio
        self._set_alpha_bg(alpha)
        if ratio >= 1.0:
            self._timer.stop()
            self._deleting = True
            self._holding = False
            self._set_alpha_bg(self._hold_alpha)
            QTimer.singleShot(0, self._delete_callback)


PROFILE_GLASS_INSET_MM = {
    "F1-33": 5,
    "F1-37": 5,
    "F1-30": 2,
    "F1-17": 29,
    "F1-09": 29,
    "F1-31": 5,
    "F1-12": 17,
    "F1-13": 92,
    "F1-14": 35,
    "F1-19": 27,
}


def _profile_frame_color(color_text):
    c = (color_text or '').strip().lower()
    if 'черн' in c:
        return QColor(48, 48, 52)
    if 'бел' in c:
        return QColor(235, 235, 238)
    if 'сереб' in c or 'серебр' in c:
        return QColor(176, 182, 192)
    if 'золот' in c or 'бронз' in c:
        return QColor(184, 140, 72)
    if 'коньяк' in c:
        return QColor(158, 110, 78)
    if 'шампан' in c:
        return QColor(212, 192, 148)
    return QColor(130, 138, 152)


def _glass_fill_color(color_text):
    c = (color_text or '').strip().lower()
    if not c:
        return QColor(190, 220, 245, 110)
    if 'бронз' in c or 'gold' in c or 'золот' in c:
        return QColor(191, 146, 88, 120)
    if 'сер' in c or 'графит' in c or 'темно' in c:
        return QColor(140, 155, 170, 120)
    if 'освет' in c or 'crystal' in c or 'бесцв' in c or 'прозрач' in c:
        return QColor(198, 226, 248, 105)
    if 'мат' in c or 'сатин' in c:
        return QColor(214, 220, 224, 135)
    if c in ('9005', 'black'):
        return QColor(80, 92, 105, 120)
    if c in ('9010', '9003', 'white'):
        return QColor(228, 234, 238, 95)
    return QColor(190, 220, 245, 110)


def _profile_code_from_profile(p):
    txt = " ".join(
        [
            str((p or {}).get("series") or ""),
            str((p or {}).get("name") or ""),
        ]
    )
    m = re.search(r"\bF1-\d{2}\b", txt, flags=re.IGNORECASE)
    return (m.group(0).upper() if m else "")


def _cutout_contrast_fill_pen(profile_dict):
    """Контрастная пара заливка / обводка для выреза под петлю относительно цвета профиля."""
    col = _profile_frame_color((profile_dict or {}).get('color'))
    luma = 0.299 * col.red() + 0.587 * col.green() + 0.114 * col.blue()
    if luma < 95:
        return QColor(255, 236, 160, 245), QColor(100, 70, 10)
    return QColor(34, 38, 46, 235), QColor(255, 214, 130)


def _label_wordwrap_height_px(
    font: QFont,
    width_px: int,
    text: str,
    align=Qt.AlignHCenter | Qt.TextWordWrap,
) -> int:
    """Высота многострочного текста с переносом (для подписей профиля на канвасе)."""
    if not (text or "").strip() or width_px < 8:
        return 0
    fm = QFontMetrics(font)
    br = fm.boundingRect(QRect(0, 0, width_px, 50000), align, text)
    return br.height() + 8


def _hole_cutout_type_label(h: dict) -> str:
    t = (
        str(
            h.get("тип")
            or h.get("тип_выреза")
            or h.get("cutout_type")
            or h.get("type")
            or h.get("Тип")
            or h.get("kind")
            or h.get("название")
            or ""
        )
        .strip()[:22]
    )
    if not t or t.lower() in ("hole", "отверстие"):
        return "присадка"
    return t


class FacadeCanvas(QWidget):
    """Канвас фасада: рама с торцами 45°, цвет профиля, стекло по центру, подписи без наведения."""
    side_clicked = pyqtSignal(object)
    glass_clicked = pyqtSignal()
    fitting_side_picked = pyqtSignal(str)
    cutout_detail_clicked = pyqtSignal(dict)
    fitting_offsets_changed = pyqtSignal()
    hole_locks_changed = pyqtSignal()

    def __init__(self, parent=None, *, font_scale: float = 1.0):
        super().__init__(parent)
        self._font_scale = max(0.75, min(2.5, float(font_scale)))
        self.setMinimumSize(200, 200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._width_mm = 400
        self._height_mm = 600
        self._side_profiles = {}
        self._rect = None
        self.setMouseTracking(True)
        self._fitting_on = False
        self._fitting_wait_side = False
        self._fitting_edit_side = None
        self._hole_offsets_mm = []
        self._saved_fittings = []
        self._cutout_regions = []
        self._hover_fitting_side = None
        self._marker_by_index = {}
        self._hole_locks = []
        self._marker_supplier_hint = ""
        self._glass_info = None
        # Кэш картинок уголков (исходные 200x200, масштабируем под текущий размер фасада).
        self._corner_pixmap_cache = {}
        # Уплотнитель (в папке FASAD/img/фурнитура: одна папка с двумя картинками черный/прозрачный).
        self._seal_variant = "прозрачный"
        self._seal_pixmap_cache: dict[str, QPixmap | None] = {}
        self._seal_variant_to_path: dict[str, str] | None = None

    def set_seal_variant(self, variant: str | None) -> None:
        v = str(variant or "").strip().lower()
        if not v:
            v = "прозрачный"
        # Нормализуем на значения из БД/ТЗ
        if "черн" in v:
            self._seal_variant = "черный"
        else:
            self._seal_variant = "прозрачный"
        self._seal_pixmap_cache.clear()
        self.update()

    def set_dimensions(self, width_mm, height_mm):
        self._width_mm = max(1, int(width_mm))
        self._height_mm = max(1, int(height_mm))
        self._rebuild_cutout_regions()
        self._reposition_hole_markers()
        self.update()

    def set_side_profiles(self, side_profiles):
        self._side_profiles = dict(side_profiles or {})
        self._rebuild_cutout_regions()
        self._reposition_hole_markers()
        self.update()

    def set_fitting_mode(self, on, wait_side_pick):
        self._fitting_on = bool(on)
        self._fitting_wait_side = bool(wait_side_pick) and self._fitting_on
        if not self._fitting_on:
            self._fitting_wait_side = False
            self._clear_hole_markers()
            self._fitting_edit_side = None
            self._hole_offsets_mm = []
        elif self._fitting_wait_side:
            self._clear_hole_markers()
            self._fitting_edit_side = None
            self._hole_offsets_mm = []
        self._hover_fitting_side = None
        self.update()

    def end_fitting_edit_session(self):
        """После сохранения присадки: убрать маркеры, снова ждать выбор стороны."""
        self._clear_hole_markers()
        self._fitting_edit_side = None
        self._hole_offsets_mm = []
        self._fitting_wait_side = bool(self._fitting_on)
        self.update()

    def set_fitting_edit(self, side, offsets_mm):
        """Показать маркеры присадки на стороне side; offsets_mm — список отступов от начала стороны (мм)."""
        self._clear_hole_markers()
        self._fitting_edit_side = side
        self._hole_offsets_mm = [int(x) for x in (offsets_mm or [])]
        self._fitting_wait_side = False
        self._hole_locks = [True] * len(self._hole_offsets_mm)
        for i in range(len(self._hole_offsets_mm)):
            w = HoleMarkerWidget(i, self, self)
            w.set_offset_mm(self._hole_offsets_mm[i], block_signal=True)
            w.set_lock_closed(True)
            w.set_supplier_hint(self._marker_supplier_hint)
            w.ok_clicked.connect(self._on_marker_ok)
            w.lock_toggled.connect(self._on_marker_lock_toggled)
            w.show()
            self._marker_by_index[i] = w
        self._update_all_marker_vres_ranks()
        self._reposition_hole_markers()
        self.hole_locks_changed.emit()
        self.update()

    def get_fitting_offsets_mm(self):
        return list(self._hole_offsets_mm)

    def _clear_hole_markers(self):
        for w in list(self._marker_by_index.values()):
            w.deleteLater()
        self._marker_by_index.clear()
        self._hole_locks = []

    def get_hole_locks(self):
        return list(self._hole_locks)

    def set_all_hole_locks(self, closed):
        """closed=True — все замки закрыты (симметричное движение)."""
        closed = bool(closed)
        self._hole_locks = [closed] * len(self._hole_offsets_mm)
        for w in self._marker_by_index.values():
            if hasattr(w, "set_lock_closed"):
                w.set_lock_closed(closed)
        self.hole_locks_changed.emit()

    def _hole_locks_effective(self):
        """Длина всегда совпадает с числом отверстий; недостающие слоты = открытый замок."""
        n = len(self._hole_offsets_mm)
        L = list(self._hole_locks or [])
        if len(L) < n:
            L.extend([False] * (n - len(L)))
        return L[:n]

    def _all_hole_locks_closed(self):
        locks = self._hole_locks_effective()
        return bool(self._hole_offsets_mm) and len(locks) == len(self._hole_offsets_mm) and all(locks)

    def _sync_marker_spins(self):
        for i, w in self._marker_by_index.items():
            if 0 <= i < len(self._hole_offsets_mm):
                w.set_offset_mm(self._hole_offsets_mm[i], block_signal=True)

    def _on_marker_lock_toggled(self, index, closed):
        if 0 <= index < len(self._hole_locks):
            self._hole_locks[index] = bool(closed)
        self.hole_locks_changed.emit()

    def _apply_symmetric_offsets(self, changed_index, mm):
        """Все замки закрыты: зеркало от краёв + равномерные промежуточные отверстия."""
        side = self._fitting_edit_side
        if side is None:
            return
        L = self._side_length_mm(side)
        lo, hi = 20, max(21, L - 20)
        n = len(self._hole_offsets_mm)
        if n < 2:
            if n == 1:
                self._hole_offsets_mm[0] = max(lo, min(hi, int(mm)))
                self._sync_marker_spins()
            return
        mm = max(lo, min(hi, int(mm)))
        if n == 2:
            if changed_index == 0:
                o0, o1 = mm, L - mm
            else:
                o1 = mm
                o0 = L - o1
            o0 = max(lo, min(hi, int(round(o0))))
            o1 = max(lo, min(hi, int(round(o1))))
            if o0 > o1:
                o0, o1 = o1, o0
            self._hole_offsets_mm[0] = o0
            self._hole_offsets_mm[1] = o1
            self._sync_marker_spins()
            return
        mid = (n - 1) // 2
        if n % 2 == 1 and changed_index == mid:
            return
        if changed_index == 0:
            o0 = mm
        elif changed_index == n - 1:
            o0 = L - mm
        else:
            k = changed_index
            den = (n - 1) - 2 * k
            if den == 0:
                return
            o0 = int(round((mm * (n - 1) - k * L) / float(den)))
        o0 = max(lo, min(hi, o0))
        o_last = int(round(L - o0))
        o_last = max(lo, min(hi, o_last))
        if o0 > o_last:
            o0, o_last = o_last, o0
        for kk in range(n):
            v = int(round(o0 + (o_last - o0) * kk / float(n - 1)))
            self._hole_offsets_mm[kk] = max(lo, min(hi, v))
        self._sync_marker_spins()

    def _apply_mirror_locked_pair(self, dragged_idx, mm):
        """Зеркало пары с закрытыми замками: сумма отступов двух отверстий не меняется.
        Центр пары на профиле остаётся тем же, что был при «зелёных» позициях — нет прыжка к краям (L−x).
        Подходит для 2…8 отверстий и любого числа пар среди открытых."""
        partner = self._physical_locked_mirror_partner(dragged_idx)
        side = self._fitting_edit_side
        if side is None or partner == dragged_idx:
            return
        L = self._side_length_mm(side)
        lo, hi = 20, max(21, L - 20)
        a = int(self._hole_offsets_mm[dragged_idx])
        b = int(self._hole_offsets_mm[partner])
        s = a + b
        nd = max(lo, min(hi, int(round(mm))))
        np = s - nd
        np = max(lo, min(hi, int(round(np))))
        nd2 = s - np
        nd2 = max(lo, min(hi, int(round(nd2))))
        self._hole_offsets_mm[dragged_idx] = nd2
        self._hole_offsets_mm[partner] = np
        self._sync_marker_spins()

    def _set_hole_offsets_user(self, index, mm, *, defer_sort=True):
        if self._fitting_edit_side is None or index not in self._marker_by_index:
            return
        side = self._fitting_edit_side
        n = len(self._hole_offsets_mm)
        locks_eff = self._hole_locks_effective()
        mm0 = int(self._clamp_offset_mm(side, int(mm)))
        partner = self._physical_locked_mirror_partner(index)
        if self._all_hole_locks_closed():
            ord0 = sorted(range(n), key=lambda i: (self._hole_offsets_mm[i], i))
            if ord0 != list(range(n)):
                r = ord0.index(index)
                self._hole_offsets_mm = [self._hole_offsets_mm[i] for i in ord0]
                self._hole_locks = [self._hole_locks[i] if i < len(self._hole_locks) else True for i in ord0]
                index = r
                self._rebuild_marker_widgets_keep_arrays()
            self._apply_symmetric_offsets(index, mm0)
        elif (
            0 <= index < n
            and partner >= 0
            and partner < n
            and partner != index
            and locks_eff[index]
            and locks_eff[partner]
        ):
            self._apply_mirror_locked_pair(index, mm0)
        else:
            self._hole_offsets_mm[index] = mm0
            self._sync_marker_spins()
        if not defer_sort:
            self._sort_holes_and_rebuild_markers()
        else:
            self._update_all_marker_vres_ranks()
        self._reposition_hole_markers()
        self.fitting_offsets_changed.emit()

    def _on_marker_ok(self, index):
        if 0 <= index < len(self._hole_offsets_mm):
            w = self._marker_by_index.get(index)
            if w:
                self._set_hole_offsets_user(index, int(w.spin.value()), defer_sort=False)

    def set_saved_fittings(self, data):
        """data: None | dict (одна запись) | list[dict] с ключами сторона, поставщик_петли, отверстия."""
        if data is None:
            self._saved_fittings = []
        elif isinstance(data, dict):
            self._saved_fittings = [dict(data)] if (data.get('сторона') or data.get('side')) else []
        else:
            self._saved_fittings = [
                dict(x)
                for x in (data or [])
                if isinstance(x, dict) and (x.get('сторона') or x.get('side'))
            ]
        self._rebuild_cutout_regions()
        self.update()

    def set_saved_fitting(self, data):
        """Обратная совместимость: одна запись как dict."""
        self.set_saved_fittings(data)

    def set_glass_info(self, glass_info):
        self._glass_info = dict(glass_info or {}) if isinstance(glass_info, dict) else None
        self.update()

    def refresh_fitting_marker_supplier_hints(self, supplier_text):
        self._marker_supplier_hint = (supplier_text or "").strip()
        t = self._marker_supplier_hint
        for w in self._marker_by_index.values():
            if hasattr(w, 'set_supplier_hint'):
                w.set_supplier_hint(t)

    def _physical_locked_mirror_partner(self, idx):
        """Партнёр для зеркала только среди отверстий с закрытым замком: по физическому порядку
        первая «красная» ↔ последняя «красная», вторая ↔ предпоследняя и т.д. (как при полном наборе,
        но только по подмножеству закрытых). Работает для 2…8 отверстий и любого набора открытых/закрытых;
        при нечётном числе закрытых центральный зеркалит сам с собой (пара не формируется)."""
        n = len(self._hole_offsets_mm)
        locks = self._hole_locks_effective()
        if n < 2 or idx < 0 or idx >= n:
            return idx
        order = sorted(range(n), key=lambda i: (self._hole_offsets_mm[i], i))
        locked_ranks = []
        for r in range(n):
            hi = order[r]
            if locks[hi]:
                locked_ranks.append(r)
        if len(locked_ranks) < 2:
            return idx
        try:
            rank_self = order.index(idx)
        except ValueError:
            return idx
        if rank_self not in locked_ranks:
            return idx
        pos = locked_ranks.index(rank_self)
        partner_rank = locked_ranks[len(locked_ranks) - 1 - pos]
        if partner_rank == rank_self:
            return idx
        return order[partner_rank]

    def _update_all_marker_vres_ranks(self):
        """Подписи #1.. по порядку вдоль профиля (после отступа, при равенстве — по индексу)."""
        n = len(self._hole_offsets_mm)
        if n == 0:
            return
        order = sorted(range(n), key=lambda i: (self._hole_offsets_mm[i], i))
        for rank, idx in enumerate(order, start=1):
            w = self._marker_by_index.get(idx)
            if w is not None and hasattr(w, "set_vres_rank"):
                w.set_vres_rank(rank)

    def _rebuild_marker_widgets_keep_arrays(self):
        """Пересоздать маркеры без смены _hole_offsets_mm / _hole_locks (уже упорядочены)."""
        for w in list(self._marker_by_index.values()):
            w.deleteLater()
        self._marker_by_index.clear()
        for i in range(len(self._hole_offsets_mm)):
            w = HoleMarkerWidget(i, self, self)
            w.set_offset_mm(self._hole_offsets_mm[i], block_signal=True)
            if i < len(self._hole_locks):
                w.set_lock_closed(self._hole_locks[i])
            else:
                w.set_lock_closed(True)
            w.set_supplier_hint(self._marker_supplier_hint)
            w.ok_clicked.connect(self._on_marker_ok)
            w.lock_toggled.connect(self._on_marker_lock_toggled)
            w.show()
            self._marker_by_index[i] = w
        self._update_all_marker_vres_ranks()
        self.hole_locks_changed.emit()

    def _sort_holes_and_rebuild_markers(self):
        """Слот i = i-е с начала стороны; подписи и пары замков совпадают с физическим порядком."""
        if self._fitting_edit_side is None:
            return
        n = len(self._hole_offsets_mm)
        if n <= 1:
            self._update_all_marker_vres_ranks()
            return
        order = sorted(range(n), key=lambda i: (self._hole_offsets_mm[i], i))
        if order == list(range(n)):
            self._update_all_marker_vres_ranks()
            return
        self._hole_offsets_mm = [self._hole_offsets_mm[i] for i in order]
        self._hole_locks = [self._hole_locks[i] if i < len(self._hole_locks) else True for i in order]
        self._rebuild_marker_widgets_keep_arrays()

    def finalize_hole_drag_order(self):
        """После отпускания мыши: упорядочить отверстия вдоль стороны и выровнять индексы 0..n-1."""
        if self._fitting_edit_side is None:
            return
        self._sort_holes_and_rebuild_markers()
        self._reposition_hole_markers()
        self.fitting_offsets_changed.emit()

    def ensure_holes_sorted_physical(self):
        """Перед сохранением присадки — гарантировать порядок в массиве."""
        if self._fitting_edit_side is None:
            return
        self._sort_holes_and_rebuild_markers()
        self._reposition_hole_markers()

    def _side_length_mm(self, side):
        return self._width_mm if side in ('top', 'bottom') else self._height_mm

    def _clamp_offset_mm(self, side, mm):
        L = self._side_length_mm(side)
        lo, hi = 20, max(21, L - 20)
        return int(max(lo, min(hi, mm)))

    def place_hole_marker_from_drag(self, index, top_left):
        if self._fitting_edit_side is None or index not in self._marker_by_index:
            return
        side = self._fitting_edit_side
        wdg = self._marker_by_index[index]
        mw, mh = wdg.width(), wdg.height()
        outer = self._get_rect()
        if not outer:
            return
        inner = self._glass_opening_rect(outer)
        cx = top_left.x() + mw // 2
        cy = top_left.y() + mh // 2
        if side in ('top', 'bottom'):
            cx = int(max(inner.left() + mw // 2, min(inner.right() - mw // 2, cx)))
            mm = int(round((cx - inner.left()) / max(1e-6, inner.width()) * self._width_mm))
        else:
            cy = int(max(inner.top() + mh // 2, min(inner.bottom() - mh // 2, cy)))
            mm = int(round((cy - inner.top()) / max(1e-6, inner.height()) * self._height_mm))
        mm = self._clamp_offset_mm(side, mm)
        self._set_hole_offsets_user(index, mm, defer_sort=True)

    def apply_hole_offset_from_spin(self, index, mm):
        if 0 <= index < len(self._hole_offsets_mm):
            self._set_hole_offsets_user(index, int(mm), defer_sort=False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_hole_markers()
        self._rebuild_cutout_regions()

    def _canvas_margin_px(self):
        s = min(self.width(), self.height())
        return max(6, int(s * CANVAS_MARGIN_FR))

    def _estimate_frame_px_for_labels(self):
        """Грубая оценка rw/rh фасада до расчёта отступов (для переноса подписей)."""
        margin = self._canvas_margin_px()
        stub = 80
        aw = max(52, self.width() - 2 * margin - stub)
        ah = max(52, self.height() - 2 * margin - stub)
        scale = min(aw / max(1.0, float(self._width_mm)), ah / max(1.0, float(self._height_mm)))
        return self._width_mm * scale, self._height_mm * scale

    def _label_margins_px(self):
        """Отступы вокруг рамы: учитывают многострочные подписи профилей (не обрезать текст)."""
        fs = self._font_scale
        width_band = int(round(26 * fs))
        base_label = int(round(min(LABEL_H, max(22, 40)) * fs))
        font = QFont("Arial", max(6, int(round(8 * fs))))
        rw_est, rh_est = self._estimate_frame_px_for_labels()
        rw_i = max(40, int(rw_est))
        rh_i = max(40, int(rh_est))

        def h_tb(side, prof):
            if not prof:
                return base_label
            txt = self._side_label_block(side, prof)
            return max(
                base_label,
                _label_wordwrap_height_px(font, rw_i, txt),
            )

        def h_lr(side, prof):
            if not prof:
                return base_label
            txt = self._side_label_block(side, prof)
            return max(
                base_label,
                _label_wordwrap_height_px(
                    font, rh_i, txt, Qt.AlignCenter | Qt.TextWordWrap
                ),
            )

        rt = 10
        if self._side_profiles.get("top"):
            rt = h_tb("top", self._side_profiles["top"]) + int(round(12 * fs))
        rb = width_band + int(round(18 * fs))
        if self._side_profiles.get("bottom"):
            rb = h_tb("bottom", self._side_profiles["bottom"]) + width_band + int(round(14 * fs))
        rl = 10
        if self._side_profiles.get("left"):
            rl = h_lr("left", self._side_profiles["left"]) + int(round(10 * fs))
        rr = 10
        if self._side_profiles.get("right"):
            rr = h_lr("right", self._side_profiles["right"]) + int(round(10 * fs))
        return rt, rb, rl, rr

    def _get_rect(self):
        if self._width_mm <= 0 or self._height_mm <= 0:
            return None
        margin = self._canvas_margin_px()
        rt, rb, rl, rr = self._label_margins_px()
        aw = max(52, self.width() - 2 * margin - rl - rr)
        ah = max(52, self.height() - 2 * margin - rt - rb)
        scale = min(aw / self._width_mm, ah / self._height_mm)
        rw = self._width_mm * scale
        rh = self._height_mm * scale
        x0 = margin + rl + (aw - rw) / 2
        y0 = margin + rt + (ah - rh) / 2
        return QRect(int(x0), int(y0), int(rw), int(rh))

    def _strip_for(self, outer):
        rw, rh = outer.width(), outer.height()
        return max(14, int(min(rw, rh) * 0.056))

    def _glass_opening_rect(self, outer):
        s = self._strip_for(outer)
        return outer.adjusted(s, s, -s, -s)

    def _zone_at(self, pos):
        outer = self._get_rect()
        if not outer or not outer.contains(pos):
            return None, None
        inner = self._glass_opening_rect(outer)
        if inner.contains(pos):
            return 'glass', None
        d_top = abs(pos.y() - inner.top())
        d_bot = abs(pos.y() - inner.bottom())
        d_left = abs(pos.x() - inner.left())
        d_right = abs(pos.x() - inner.right())
        m = min(d_top, d_bot, d_left, d_right)
        if m == d_top:
            return 'frame', 'top'
        if m == d_bot:
            return 'frame', 'bottom'
        if m == d_left:
            return 'frame', 'left'
        return 'frame', 'right'

    def _rebuild_cutout_regions(self):
        self._cutout_regions = []
        outer = self._get_rect()
        if not outer:
            return
        inner = self._glass_opening_rect(outer)
        for entry_index, sf in enumerate(self._saved_fittings or []):
            if not isinstance(sf, dict):
                continue
            side_raw = sf.get('сторона') or sf.get('side')
            sk = _norm_side_key(side_raw) or str(side_raw or "")
            supplier = sf.get('поставщик_петли') or sf.get('supplier') or '—'
            holes = sf.get('отверстия') or sf.get('holes') or []
            if not sk:
                continue
            for i, h in enumerate(holes):
                if not isinstance(h, dict):
                    continue
                off = int(h.get('отступ_мм') or h.get('offset_mm') or 0)
                r = self._cutout_hit_rect(sk, off, outer, inner)
                if r:
                    self._cutout_regions.append(
                        {
                            'rect': r,
                            'offset_mm': off,
                            'supplier': supplier,
                            'side': sk,
                            'index': i,
                            'entry_index': entry_index,
                            'hole_index': i,
                        }
                    )

    def _cutout_hit_rect(self, side, offset_mm, outer, inner):
        strip = self._strip_for(outer)
        px, py = 9, 6
        if side == 'top':
            cx = int(inner.left() + (offset_mm / max(1e-6, self._width_mm)) * inner.width())
            cy = int(outer.y() + strip / 2)
            return QRect(cx - px, cy - py, px * 2, py * 2)
        if side == 'bottom':
            cx = int(inner.left() + (offset_mm / max(1e-6, self._width_mm)) * inner.width())
            cy = int(outer.bottom() - strip / 2)
            return QRect(cx - px, cy - py, px * 2, py * 2)
        if side == 'left':
            cy = int(inner.top() + (offset_mm / max(1e-6, self._height_mm)) * inner.height())
            cx = int(outer.x() + strip / 2)
            return QRect(cx - py, cy - px, py * 2, px * 2)
        if side == 'right':
            cy = int(inner.top() + (offset_mm / max(1e-6, self._height_mm)) * inner.height())
            cx = int(outer.right() - strip / 2)
            return QRect(cx - py, cy - px, py * 2, px * 2)
        return None

    def _reposition_hole_markers(self):
        side = self._fitting_edit_side
        if not side or not self._marker_by_index:
            return
        outer = self._get_rect()
        if not outer:
            return
        inner = self._glass_opening_rect(outer)
        for i, w in self._marker_by_index.items():
            if i >= len(self._hole_offsets_mm):
                continue
            mm = self._hole_offsets_mm[i]
            mw, mh = w.width(), w.height()
            if side == 'top':
                cx = inner.left() + (mm / max(1e-6, self._width_mm)) * inner.width()
                x = int(cx - mw // 2)
                strip = self._strip_for(outer)
                y = int(outer.y() + strip / 2 - mh / 2)
            elif side == 'bottom':
                cx = inner.left() + (mm / max(1e-6, self._width_mm)) * inner.width()
                x = int(cx - mw // 2)
                strip = self._strip_for(outer)
                y = int(outer.y() + outer.height() - strip / 2 - mh / 2)
            elif side == 'left':
                cy = inner.top() + (mm / max(1e-6, self._height_mm)) * inner.height()
                strip = self._strip_for(outer)
                x = int(outer.x() + strip / 2 - mw / 2)
                y = int(cy - mh // 2)
            else:
                cy = inner.top() + (mm / max(1e-6, self._height_mm)) * inner.height()
                strip = self._strip_for(outer)
                x = int(inner.right() + strip / 2 - mw / 2)
                y = int(cy - mh // 2)
            x = int(max(0, min(self.width() - mw, x)))
            y = int(max(0, min(self.height() - mh, y)))
            w.move(x, y)

    def _pick_cutout_at(self, pos):
        for reg in reversed(self._cutout_regions):
            if reg['rect'].contains(pos):
                return reg
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            hit = self._pick_cutout_at(event.pos())
            if hit:
                self.cutout_detail_clicked.emit(
                    {
                        'offset_mm': hit['offset_mm'],
                        'supplier': hit['supplier'],
                        'side': hit['side'],
                        'index': hit['index'],
                        'entry_index': hit.get('entry_index', 0),
                        'hole_index': hit.get('hole_index', hit['index']),
                    }
                )
                event.accept()
                return
            zone, side = self._zone_at(event.pos())
            if self._fitting_on and self._fitting_wait_side:
                if zone == 'frame' and side and self._side_profiles.get(side):
                    self.fitting_side_picked.emit(side)
                    event.accept()
                    return
            if self._fitting_on and not self._fitting_wait_side:
                if zone == 'glass':
                    self.glass_clicked.emit()
                elif zone == 'frame' and side and self._side_profiles.get(side):
                    if self._fitting_edit_side is None or side != self._fitting_edit_side:
                        self.fitting_side_picked.emit(side)
                    event.accept()
                    return
            if zone == 'glass':
                self.glass_clicked.emit()
            elif zone == 'frame' and side and self._side_profiles.get(side):
                self.side_clicked.emit(side)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        cut = self._pick_cutout_at(event.pos())
        if cut:
            QToolTip.showText(
                event.globalPos(),
                "Вырез под петлю\nОтступ от края: %s мм\nПоставщик: %s"
                % (cut['offset_mm'], cut.get('supplier') or '—'),
            )
            if self._hover_fitting_side is not None:
                self._hover_fitting_side = None
                self.update()
            super().mouseMoveEvent(event)
            return
        zone, side = self._zone_at(event.pos())
        if self._fitting_on and self._fitting_wait_side and zone == 'frame' and side:
            prof = self._side_profiles.get(side)
            nh = self._hover_fitting_side
            self._hover_fitting_side = side if prof else None
            if nh != self._hover_fitting_side:
                self.update()
            if prof:
                ru = FITTING_SIDE_LABELS_RU.get(side, side)
                QToolTip.showText(
                    event.globalPos(),
                    "Сторона «%s» — присадка.\nНажмите, чтобы выбрать эту сторону профиля." % ru,
                )
            else:
                QToolTip.hideText()
            super().mouseMoveEvent(event)
            return
        self._hover_fitting_side = None
        if zone == 'glass':
            if not self._side_profiles:
                tip = "Стекло в проёме\nСначала выберите профиль фасада."
            else:
                tip = "Стекло в проёме\nНажмите, чтобы выбрать/изменить стекло."
            QToolTip.showText(event.globalPos(), tip)
        elif zone == 'frame' and side:
            prof = self._side_profiles.get(side)
            if prof:
                price_m = float(prof.get('price_per_meter') or 0)
                length_m = (self._width_mm / 1000.0) if side in ('top', 'bottom') else (self._height_mm / 1000.0)
                cost_side = length_m * price_m
                total = 0
                for s, p in self._side_profiles.items():
                    if p and p.get('id') == prof.get('id'):
                        lm = (self._width_mm / 1000.0) if s in ('top', 'bottom') else (self._height_mm / 1000.0)
                        total += lm * float(p.get('price_per_meter') or 0)
                tip = "%s\nСерия: %s\nЦвет: %s\nЦена/м: %.2f ₽\nЭта сторона: %.2f ₽\nВсего профиль: %.2f ₽" % (
                    prof.get('name') or '—',
                    prof.get('series') or '—',
                    prof.get('color') or '—',
                    price_m,
                    cost_side,
                    total,
                )
                QToolTip.showText(event.globalPos(), tip)
            else:
                QToolTip.hideText()
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_fitting_side = None
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def _strip_polygon(self, outer, inner, side):
        ox, oy, ow, oh = outer.x(), outer.y(), outer.width(), outer.height()
        ix, iy, iw, ih = inner.x(), inner.y(), inner.width(), inner.height()
        if side == 'top':
            return QPolygon(
                [QPoint(ox, oy), QPoint(ox + ow, oy), QPoint(ix + iw, iy), QPoint(ix, iy)]
            )
        if side == 'bottom':
            return QPolygon(
                [QPoint(ix, iy + ih), QPoint(ix + iw, iy + ih), QPoint(ox + ow, oy + oh), QPoint(ox, oy + oh)]
            )
        if side == 'left':
            return QPolygon(
                [QPoint(ox, oy), QPoint(ix, iy), QPoint(ix, iy + ih), QPoint(ox, oy + oh)]
            )
        return QPolygon(
            [QPoint(ix + iw, iy), QPoint(ox + ow, oy), QPoint(ox + ow, oy + oh), QPoint(ix + iw, iy + ih)]
        )

    def _draw_fitting_side_hover(self, p, outer, inner, side):
        poly = self._strip_polygon(outer, inner, side)
        if poly.isEmpty():
            return
        p.save()
        p.setPen(QPen(QColor(46, 125, 50), 2))
        p.setBrush(QBrush(QColor(76, 175, 80, 72)))
        p.drawPolygon(poly)
        p.restore()

    def _draw_saved_cutouts(self, p, outer, inner):
        p.setRenderHint(QPainter.Antialiasing)
        fs = self._font_scale
        cap_font = QFont("Arial", max(5, int(round(6.5 * fs))))
        for sf in self._saved_fittings or []:
            if not isinstance(sf, dict):
                continue
            side_raw = sf.get('сторона') or sf.get('side')
            sk = _norm_side_key(side_raw) or str(side_raw or "")
            holes = sf.get('отверстия') or sf.get('holes') or []
            if not sk:
                continue
            prof_here = self._side_profiles.get(sk) or {}
            fill, pen = _cutout_contrast_fill_pen(prof_here)
            for h in holes:
                if not isinstance(h, dict):
                    continue
                off = int(h.get('отступ_мм') or h.get('offset_mm') or 0)
                r = self._cutout_hit_rect(sk, off, outer, inner)
                if not r:
                    continue
                p.setPen(QPen(pen, 2))
                p.setBrush(QBrush(fill))
                p.drawRoundedRect(r, 3, 3)
                p.setPen(QPen(QColor(20, 24, 32), 1))
                p.setFont(cap_font)
                cap = "%s мм\n%s" % (off, _hole_cutout_type_label(h))
                if sk == "bottom":
                    p.drawText(
                        QRect(r.center().x() - 52, r.bottom() + 2, 104, 40),
                        Qt.AlignHCenter | Qt.TextWordWrap,
                        cap,
                    )
                elif sk == "top":
                    p.drawText(
                        QRect(r.center().x() - 52, r.top() - 42, 104, 40),
                        Qt.AlignHCenter | Qt.TextWordWrap,
                        cap,
                    )
                elif sk == "left":
                    p.drawText(
                        QRect(r.right() + 3, r.center().y() - 20, 76, 44),
                        Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap,
                        cap,
                    )
                else:
                    p.drawText(
                        QRect(r.left() - 79, r.center().y() - 20, 76, 44),
                        Qt.AlignRight | Qt.AlignVCenter | Qt.TextWordWrap,
                        cap,
                    )

    def _draw_profile_side(self, p, outer, inner, side, prof):
        if not prof:
            return
        base = _profile_frame_color(prof.get("color"))
        col = QColor(base.red(), base.green(), base.blue(), 238)
        darker = QColor(
            max(0, base.red() - 50),
            max(0, base.green() - 50),
            max(0, base.blue() - 50),
        )
        ox, oy, ow, oh = outer.x(), outer.y(), outer.width(), outer.height()
        ix, iy, iw, ih = inner.x(), inner.y(), inner.width(), inner.height()
        poly = None
        if side == 'top':
            poly = QPolygon(
                [
                    QPoint(ox, oy),
                    QPoint(ox + ow, oy),
                    QPoint(ix + iw, iy),
                    QPoint(ix, iy),
                ]
            )
        elif side == 'bottom':
            poly = QPolygon(
                [
                    QPoint(ix, iy + ih),
                    QPoint(ix + iw, iy + ih),
                    QPoint(ox + ow, oy + oh),
                    QPoint(ox, oy + oh),
                ]
            )
        elif side == 'left':
            poly = QPolygon(
                [
                    QPoint(ox, oy),
                    QPoint(ix, iy),
                    QPoint(ix, iy + ih),
                    QPoint(ox, oy + oh),
                ]
            )
        else:
            poly = QPolygon(
                [
                    QPoint(ix + iw, iy),
                    QPoint(ox + ow, oy),
                    QPoint(ox + ow, oy + oh),
                    QPoint(ix + iw, iy + ih),
                ]
            )
        p.setPen(QPen(darker, 1))
        p.setBrush(QBrush(col))
        p.drawPolygon(poly)

    def _side_label_block(self, side, prof):
        if not prof:
            return ''
        cap = FITTING_SIDE_LABELS_RU.get(side, side)
        ser = (prof.get('series') or '')[:22]
        nm = (prof.get('name') or '')[:26]
        cl = (prof.get('color') or '')[:18]
        return "%s\n%s\n%s\n%s" % (cap, ser, nm, cl)

    def _profile_series_is_prisma(self, profile: dict) -> bool:
        series = str((profile or {}).get('series') or '').strip().upper()
        return 'PRISMA' in series

    def _corner_code_for_profiles(self, a: dict | None, b: dict | None) -> str:
        """Выбор уголка: если хотя бы один профиль PRISMA — F3-031, иначе F3-021."""
        if self._profile_series_is_prisma(a or {}) or self._profile_series_is_prisma(b or {}):
            return 'F3-031'
        return 'F3-021'

    def _corner_pixmap(self, corner_code: str, pos_key: str) -> QPixmap | None:
        key = (corner_code, pos_key)
        if key in self._corner_pixmap_cache:
            return self._corner_pixmap_cache[key]

        # Структура: MAIN_PROJECT/FASAD/img/фурнитура/{F3-021|F3-031}/{LB|LT|RB|RT}.png
        path = os.path.join(
            get_base_dir(), 'FASAD', 'img', 'фурнитура', corner_code, f'{pos_key}.png'
        )
        if not os.path.isfile(path):
            self._corner_pixmap_cache[key] = None
            return None

        pm = QPixmap(path)
        if pm.isNull():
            self._corner_pixmap_cache[key] = None
            return None
        self._corner_pixmap_cache[key] = pm
        return pm

    def _seal_icon_dir(self) -> str | None:
        """Папка с уплотнителем. Приоритет: .../фурнитура/уплотнитель."""
        base = os.path.join(get_base_dir(), "FASAD", "img", "фурнитура")
        if not os.path.isdir(base):
            return None
        # Явный путь, чтобы не перепутать с папкой винтов.
        explicit = os.path.join(base, "уплотнитель")
        if os.path.isdir(explicit):
            return explicit
        candidates: list[str] = []
        for name in os.listdir(base):
            p = os.path.join(base, name)
            if not os.path.isdir(p):
                continue
            if name in ("F3-021", "F3-031"):
                continue
            lname = str(name).lower()
            # Никогда не берём винты как уплотнитель.
            if "винт" in lname:
                continue
            # Если нашли подпапку с "уплотн", отдаём её сразу.
            if "уплотн" in lname:
                return p
            candidates.append(p)
        if not candidates:
            return None
        # если вдруг несколько — берём ту, где больше png
        best = None
        best_n = -1
        for p in candidates:
            try:
                n = sum(1 for f in os.listdir(p) if str(f).lower().endswith(".png"))
            except Exception:
                n = 0
            if n > best_n:
                best_n = n
                best = p
        return best

    def _seal_variant_files(self) -> dict[str, str]:
        """
        Определяем какие картинки в папке уплотнителя — 'черный' и 'прозрачный'.
        Критерий: у черного ниже средняя яркость (RGB) на непустых (alpha>10) пикселях.
        """
        if self._seal_variant_to_path:
            return self._seal_variant_to_path

        seal_dir = self._seal_icon_dir()
        out: dict[str, str] = {"черный": "", "прозрачный": ""}
        if not seal_dir:
            self._seal_variant_to_path = out
            return out

        try:
            files = [
                os.path.join(seal_dir, f)
                for f in os.listdir(seal_dir)
                if str(f).lower().endswith(".png") and os.path.isfile(os.path.join(seal_dir, f))
            ]
        except Exception:
            files = []

        if len(files) < 2:
            self._seal_variant_to_path = out
            return out

        def _img_mean_brightness(path: str) -> float:
            try:
                img = QImage(path)
                if img.isNull():
                    return 1e9
                img = img.convertToFormat(QImage.Format_ARGB32)
                w, h = img.width(), img.height()
                total_b = 0.0
                cnt = 0
                step = max(1, int(min(w, h) / 120))  # ускоряем: берём примерно ~120 пикселей по меньшей стороне
                for y in range(0, h, step):
                    for x in range(0, w, step):
                        c = img.pixelColor(x, y)
                        a = c.alpha()
                        if a <= 10:
                            continue
                        b = (c.red() + c.green() + c.blue()) / 3.0
                        total_b += b
                        cnt += 1
                return (total_b / cnt) if cnt else 1e9
            except Exception:
                return 1e9

        # берём 2 файла с наименьшей разницей по размеру? пока проще: сортируем по яркости
        scored = sorted([( _img_mean_brightness(p), p) for p in files], key=lambda t: t[0])
        black_path = scored[0][1]
        # прозрачный — оставшийся с большей яркостью (обычно)
        transparent_path = scored[-1][1]
        out["черный"] = black_path
        out["прозрачный"] = transparent_path
        self._seal_variant_to_path = out
        return out

    def _seal_pixmap(self, variant: str) -> QPixmap | None:
        key = variant
        if key in self._seal_pixmap_cache:
            return self._seal_pixmap_cache[key]

        files = self._seal_variant_files()
        path = files.get(variant) or ""
        if not path or not os.path.isfile(path):
            self._seal_pixmap_cache[key] = None
            return None
        pm = QPixmap(path)
        if pm.isNull():
            self._seal_pixmap_cache[key] = None
            return None
        self._seal_pixmap_cache[key] = pm
        return pm

    def _draw_seal_icons(self, p: QPainter, outer: QRect) -> None:
        """Мини-иконки уплотнителя на углах (там, где есть соответствующие профили)."""
        if not self._side_profiles:
            return
        v = self._seal_variant or "прозрачный"
        pm = self._seal_pixmap(v)
        if not pm:
            return

        strip = self._strip_for(outer)
        seal_px = max(16, int(strip * 0.95))
        seal_px = min(seal_px, max(16, min(outer.width(), outer.height()) // 2))
        scaled = pm.scaled(seal_px, seal_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        top = self._side_profiles.get("top")
        bottom = self._side_profiles.get("bottom")
        left = self._side_profiles.get("left")
        right = self._side_profiles.get("right")

        # Считаем углы как раньше для уголков: нужны два прилегающих профиля.
        if top and left:
            p.drawPixmap(outer.left(), outer.top(), scaled)
        if bottom and left:
            x = outer.left()
            y = outer.top() + outer.height() - scaled.height()
            p.drawPixmap(x, y, scaled)
        if top and right:
            x = outer.left() + outer.width() - scaled.width()
            y = outer.top()
            p.drawPixmap(x, y, scaled)
        if bottom and right:
            x = outer.left() + outer.width() - scaled.width()
            y = outer.top() + outer.height() - scaled.height()
            p.drawPixmap(x, y, scaled)

    def _draw_corners(self, p: QPainter, outer: QRect) -> None:
        """Рисуем уголки только там, где стоят прилегающие профили."""
        if not self._side_profiles:
            return

        strip = self._strip_for(outer)
        corner_px = max(18, int(strip * 1.4))
        corner_px = min(corner_px, max(18, min(outer.width(), outer.height()) // 2))

        top = self._side_profiles.get('top')
        bottom = self._side_profiles.get('bottom')
        left = self._side_profiles.get('left')
        right = self._side_profiles.get('right')

        # LT: top + left
        if top and left:
            code = self._corner_code_for_profiles(top, left)
            pm = self._corner_pixmap(code, 'LT')
            if pm:
                scaled = pm.scaled(corner_px, corner_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                p.drawPixmap(outer.left(), outer.top(), scaled)

        # LB: bottom + left
        if bottom and left:
            code = self._corner_code_for_profiles(bottom, left)
            pm = self._corner_pixmap(code, 'LB')
            if pm:
                scaled = pm.scaled(corner_px, corner_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = outer.left()
                y = outer.top() + outer.height() - scaled.height()
                p.drawPixmap(x, y, scaled)

        # RT: top + right
        if top and right:
            code = self._corner_code_for_profiles(top, right)
            pm = self._corner_pixmap(code, 'RT')
            if pm:
                scaled = pm.scaled(corner_px, corner_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = outer.left() + outer.width() - scaled.width()
                y = outer.top()
                p.drawPixmap(x, y, scaled)

        # RB: bottom + right
        if bottom and right:
            code = self._corner_code_for_profiles(bottom, right)
            pm = self._corner_pixmap(code, 'RB')
            if pm:
                scaled = pm.scaled(corner_px, corner_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = outer.left() + outer.width() - scaled.width()
                y = outer.top() + outer.height() - scaled.height()
                p.drawPixmap(x, y, scaled)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._width_mm <= 0 or self._height_mm <= 0:
            return
        self._rect = self._get_rect()
        outer = self._rect
        inner = self._glass_opening_rect(outer)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(250, 252, 255))
        p.fillRect(outer, Qt.white)
        for side in ('top', 'bottom', 'left', 'right'):
            self._draw_profile_side(p, outer, inner, side, self._side_profiles.get(side))

        # Уголки: визуально скрепляют те профили, которые реально стоят на сторонах.
        self._draw_corners(p, outer)

        glass_color = _glass_fill_color((self._glass_info or {}).get('Цвет'))
        glass = glass_color
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glass))
        p.drawRect(inner)
        p.setPen(QPen(QColor(120, 160, 200), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(inner)
        p.setPen(QPen(Qt.darkGray, 1))
        p.drawRect(outer)

        if self._fitting_on and self._fitting_wait_side and self._hover_fitting_side:
            self._draw_fitting_side_hover(p, outer, inner, self._hover_fitting_side)
        self._draw_saved_cutouts(p, outer, inner)

        p.setPen(Qt.black)
        fs = self._font_scale
        side_font = QFont("Arial", max(6, int(round(8 * fs))))
        p.setFont(side_font)
        label_min = int(round(min(LABEL_H, max(22, 40)) * fs))
        left_band_w = label_min
        right_band_w = label_min
        width_line_h = max(16, int(round(22 * fs)))
        width_y0 = self.height() - width_line_h - max(2, int(round(4 * fs)))
        if self._side_profiles.get('top'):
            txt_top = self._side_label_block('top', self._side_profiles['top'])
            h_top = max(label_min, _label_wordwrap_height_px(side_font, outer.width(), txt_top))
            y_top = outer.y() - h_top - 4
            if y_top < 4:
                y_top = 4
                h_top = max(18, outer.y() - y_top - 2)
            p.drawText(
                outer.x(),
                y_top,
                outer.width(),
                h_top,
                Qt.AlignHCenter | Qt.TextWordWrap,
                txt_top,
            )
        if self._side_profiles.get('bottom'):
            txt_bot = self._side_label_block('bottom', self._side_profiles['bottom'])
            h_bot_need = max(label_min, _label_wordwrap_height_px(side_font, outer.width(), txt_bot))
            gap_above_width = 6
            max_bottom = width_y0 - gap_above_width
            y_bot = max(outer.bottom() + 4, max_bottom - h_bot_need)
            h_bot = max(18, min(h_bot_need, max_bottom - y_bot))
            p.drawText(
                outer.x(),
                y_bot,
                outer.width(),
                h_bot,
                Qt.AlignHCenter | Qt.TextWordWrap,
                txt_bot,
            )
        if self._side_profiles.get('left'):
            txt_l = self._side_label_block('left', self._side_profiles['left'])
            left_band_w = max(
                label_min,
                _label_wordwrap_height_px(
                    side_font, outer.height(), txt_l, Qt.AlignCenter | Qt.TextWordWrap
                ),
            )
            p.save()
            p.translate(outer.x() - left_band_w - max(4, int(round(6 * fs))), outer.y() + outer.height() // 2)
            p.rotate(-90)
            p.drawText(
                -outer.height() // 2,
                0,
                outer.height(),
                left_band_w,
                Qt.AlignCenter | Qt.TextWordWrap,
                txt_l,
            )
            p.restore()
        if self._side_profiles.get('right'):
            txt_r = self._side_label_block('right', self._side_profiles['right'])
            right_band_w = max(
                label_min,
                _label_wordwrap_height_px(
                    side_font, outer.height(), txt_r, Qt.AlignCenter | Qt.TextWordWrap
                ),
            )
            p.save()
            p.translate(
                outer.x() + outer.width() + right_band_w + max(4, int(round(6 * fs))),
                outer.y() + outer.height() // 2,
            )
            p.rotate(90)
            p.drawText(
                -outer.height() // 2,
                0,
                outer.height(),
                right_band_w,
                Qt.AlignCenter | Qt.TextWordWrap,
                txt_r,
            )
            p.restore()

        p.setFont(QFont("Arial", max(7, int(round(10 * fs))), QFont.Bold))
        width_text = "%d мм" % self._width_mm
        p.drawText(outer.x(), width_y0, outer.width(), width_line_h, Qt.AlignCenter, width_text)
        p.save()
        hl_tx = max(8, int(round(10 * fs)))
        if self._side_profiles.get('left'):
            hl_tx = max(hl_tx, int(outer.x() - 2 * left_band_w - max(10, int(round(14 * fs)))))
        p.translate(hl_tx, outer.y() + outer.height() // 2)
        p.rotate(-90)
        height_text = "%d мм" % self._height_mm
        p.drawText(-outer.height() // 2, 0, outer.height(), width_line_h, Qt.AlignCenter, height_text)
        p.restore()

        glass_font = QFont("Arial", max(7, int(round(9 * fs))))
        p.setFont(glass_font)
        p.setPen(QColor(80, 110, 150))
        if self._glass_info:
            g_name = (self._glass_info.get('Название') or 'Стекло').strip()
            g_color = (self._glass_info.get('Цвет') or '—').strip()
            g_th = self._glass_info.get('Толщина (мм)')
            txt = "%s\n%s, %s мм\n(нажмите для изменения)" % (g_name, g_color, g_th if g_th is not None else "—")
        else:
            txt = "Стекло\n(нажмите для выбора)"
        g_rect = inner.adjusted(8, 8, -8, -8)
        p.drawText(g_rect, Qt.AlignCenter | Qt.TextWordWrap, txt)
        p.end()


class PlayboySlideshow(QWidget):
    """Компактная смена картинок из папки playboy каждые 1.5 с (меньше — чтобы не давить на подписи)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pw, self._ph = 118, 90
        self.setFixedSize(self._pw, self._ph)
        self._images_path = os.path.join(get_mirror_cut_root(), "playboy")
        self._files = []
        self._index = 0
        self._pixmap = None
        self._load_list()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next_image)
        self._timer.start(1500)
        if self._files:
            self._next_image()

    def _load_list(self):
        if not os.path.isdir(self._images_path):
            return
        for f in os.listdir(self._images_path):
            low = f.lower()
            if low.endswith(('.png', '.jpg', '.jpeg')):
                self._files.append(os.path.join(self._images_path, f))

    def _next_image(self):
        if not self._files:
            return
        self._index = (self._index + 1) % len(self._files)
        path = self._files[self._index]
        try:
            self._pixmap = QPixmap(path)
        except Exception:
            self._pixmap = None
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._pixmap and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(self._pw, self._ph, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            x = (self._pw - scaled.width()) // 2
            y = (self._ph - scaled.height()) // 2
            p = QPainter(self)
            p.drawPixmap(x, y, scaled)
            p.end()


class FacadeCutoutDetailDialog(QDialog):
    """Карточка выреза: данные + удаление одного отверстия."""

    def __init__(self, parent, info):
        super().__init__(parent)
        self.setWindowTitle("Вырез под петлю")
        self._deleted = False
        side = info.get('side') or ''
        ru = FITTING_SIDE_LABELS_RU.get(side, side)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Сторона: %s" % ru))
        lay.addWidget(QLabel("Отступ от края: %s мм" % info.get('offset_mm')))
        lay.addWidget(QLabel("Поставщик петли: %s" % (info.get('supplier') or '—')))
        row = QHBoxLayout()
        btn_del = QPushButton("Удалить вырез")
        btn_del.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; padding: 6px 12px; border-radius: 4px; }"
        )
        btn_del.clicked.connect(self._on_delete)
        row.addWidget(btn_del)
        row.addStretch()
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.reject)
        row.addWidget(btn_close)
        lay.addLayout(row)

    def _on_delete(self):
        self._deleted = True
        self.accept()

    def was_deleted(self):
        return self._deleted


class FacadeOrderDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        linked_order_id=None,
        product_id=None,
        append_new: bool = False,
        quick_client_preset=None,
        quick_estimate_mode=False,
    ):
        super().__init__(parent)
        self.setWindowTitle("Расчёт фасадов")
        self.setMinimumSize(1000, 700)
        self.resize(1260, 820)
        self._client_id = None
        self._quick_client_id = None
        self._quick_estimate_mode = bool(quick_estimate_mode)
        self._quick_client_preset = quick_client_preset if isinstance(quick_client_preset, dict) else None
        self._linked_order_id = linked_order_id
        self._product_id = product_id
        self._append_new = bool(append_new)
        self._side_profiles = {}
        self._hinges = []  # list of {hinge_dict, quantity}
        self._saved_fittings = []
        self._fitting_switch_on = False
        self._fitting_selected_side = None
        self._fitting_snapshot_offsets = []
        self._did_maximize = False
        self._glass_selection = None
        # Уплотнитель: по умолчанию прозрачный.
        self._seal_variant = "прозрачный"
        self._screw_variant = "серебро"
        self._angle_seal_price_cache: dict[str, float] | None = None
        self._seal_price_cache: dict[str, float] | None = None
        self._screw_price_cache: dict[str, float] | None = None
        self._screw_icon_cache: dict[str, str | None] = {}
        self._facade_tabs: list[dict[str, Any]] = []
        self._active_facade_tab_idx = 0
        self._tab_sync_guard = False
        self._materials_panel_built = False
        self._services_zamer_block = {"Активирован": False, "Данные": None}
        self._services_delivery_block = {"Активирован": False, "Данные": None}
        self._glass_main_app = None
        self._glass_modal_host = None
        self._glass_embed_layout = None
        self._glass_active_pick_dialog = None
        self._cutout_pending_info = None
        self._build_ui()
        self._apply_quick_client_preset()
        self._apply_linked_order_context()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Верхняя строка: клиент + playboy справа
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel("Клиент:"))
        self.client_edit = QLineEdit()
        self.client_edit.setPlaceholderText(
            "Справочник или быстрый клиент…"
            if self._quick_estimate_mode
            else "Введите имя или начало имени для подсказок"
        )
        self.client_edit.setMinimumWidth(280)
        self.client_edit.textChanged.connect(self._on_client_text_changed)
        top_row.addWidget(self.client_edit)
        self.btn_create_client = QPushButton(
            "Новый (быстрый клиент)" if self._quick_estimate_mode else "Создать клиента"
        )
        self.btn_create_client.clicked.connect(self._on_create_client)
        top_row.addWidget(self.btn_create_client)
        top_row.addStretch()
        self.playboy_widget = PlayboySlideshow(self)
        top_row.addWidget(self.playboy_widget)
        layout.addLayout(top_row)

        self.client_list = QListWidget()
        self.client_list.setMaximumHeight(100)
        self.client_list.setVisible(False)
        self.client_list.itemClicked.connect(self._on_client_selected)
        layout.addWidget(self.client_list)

        self._mixed_banner = QFrame()
        self._mixed_banner.setVisible(False)
        self._mixed_banner.setStyleSheet(
            "QFrame { background:#fff8e1; border:1px solid #ffb300; border-radius:6px; padding:6px; }"
        )
        mb_lay = QHBoxLayout(self._mixed_banner)
        mb_lay.setContentsMargins(8, 6, 8, 6)
        self._lbl_mixed_order = QLabel("")
        self._lbl_mixed_order.setWordWrap(True)
        self._btn_mixed_overview = QPushButton("Все изделия заказа (стекло/зеркало)")
        self._btn_mixed_overview.clicked.connect(self._on_open_full_order_overview)
        mb_lay.addWidget(self._lbl_mixed_order, 1)
        mb_lay.addWidget(self._btn_mixed_overview, 0)
        layout.addWidget(self._mixed_banner)

        self._facade_tabs_bar = QTabWidget()
        self._facade_tabs_bar.setVisible(False)
        self._facade_tabs_bar.currentChanged.connect(self._on_facade_tab_changed)
        layout.addWidget(self._facade_tabs_bar)

        # Размеры
        dim_row = QHBoxLayout()
        dim_row.addWidget(QLabel("Высота (мм):"))
        self.spin_height = QSpinBox()
        self.spin_height.setRange(1, 5000)
        self.spin_height.setValue(600)
        self.spin_height.valueChanged.connect(self._on_dimensions_changed)
        dim_row.addWidget(self.spin_height)
        dim_row.addWidget(QLabel("Ширина (мм):"))
        self.spin_width = QSpinBox()
        self.spin_width.setRange(1, 5000)
        self.spin_width.setValue(400)
        self.spin_width.valueChanged.connect(self._on_dimensions_changed)
        dim_row.addWidget(self.spin_width)
        dim_row.addWidget(QLabel("Количество:"))
        self.spin_quantity = QSpinBox()
        self.spin_quantity.setRange(1, 1000)
        self.spin_quantity.setValue(1)
        self.spin_quantity.valueChanged.connect(self._on_quantity_changed)
        dim_row.addWidget(self.spin_quantity)
        dim_row.addStretch()
        layout.addLayout(dim_row)

        self.lbl_fitting_banner = QLabel("")
        self.lbl_fitting_banner.setWordWrap(True)
        self.lbl_fitting_banner.setVisible(False)
        self.lbl_fitting_banner.setStyleSheet(
            "background-color: #e8f5e9; color: #1b5e20; padding: 8px 10px; "
            "border: 1px solid #a5d6a7; border-radius: 6px; font-weight: bold;"
        )
        layout.addWidget(self.lbl_fitting_banner)

        self._left_wrap = QWidget()
        left_col = QVBoxLayout(self._left_wrap)
        left_col.setSpacing(8)
        self._left_wrap.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._left_wrap.setFixedWidth(280)
        self.btn_add_profile = QPushButton("Добавить профиль")
        self.btn_add_profile.clicked.connect(self._on_add_profile)
        left_col.addWidget(self.btn_add_profile)
        self.btn_add_hinges = QPushButton("Добавить петли")
        self.btn_add_hinges.clicked.connect(self._on_add_hinges)
        self.btn_add_hinges.setEnabled(False)  # включим после сохранения отверстий (присадка)
        left_col.addWidget(self.btn_add_hinges)

        # Уплотнитель вокруг фасада (по периметру)
        self.lbl_seal_variant = QLabel("Уплотнитель:")
        self.lbl_seal_variant.setWordWrap(True)
        left_col.addWidget(self.lbl_seal_variant)

        seal_radio_row = QHBoxLayout()
        self.rb_seal_transparent = QRadioButton("прозрачный")
        self.rb_seal_black = QRadioButton("черный")
        self.rb_seal_transparent.setChecked(True)
        self.rb_seal_transparent.toggled.connect(self._on_seal_variant_changed)
        self.rb_seal_black.toggled.connect(self._on_seal_variant_changed)
        seal_radio_row.addWidget(self.rb_seal_transparent)
        seal_radio_row.addWidget(self.rb_seal_black)
        seal_radio_row.addStretch()
        left_col.addLayout(seal_radio_row)

        self.lbl_screw_variant = QLabel("Винты для уголков:")
        self.lbl_screw_variant.setWordWrap(True)
        left_col.addWidget(self.lbl_screw_variant)
        screw_radio_row = QHBoxLayout()
        self.rb_screw_silver = QRadioButton("серебро")
        self.rb_screw_gold = QRadioButton("золото")
        self.rb_screw_silver.setChecked(True)
        self.rb_screw_silver.toggled.connect(self._on_screw_variant_changed)
        self.rb_screw_gold.toggled.connect(self._on_screw_variant_changed)
        screw_radio_row.addWidget(self.rb_screw_silver)
        screw_radio_row.addWidget(self.rb_screw_gold)
        screw_radio_row.addStretch()
        left_col.addLayout(screw_radio_row)

        self.lbl_services = QLabel("Замер / монтаж / доставка:")
        self.lbl_services.setWordWrap(True)
        left_col.addWidget(self.lbl_services)
        services_row = QHBoxLayout()
        self.btn_services = QPushButton("Замер | Доставка | Монтаж")
        self.btn_services.clicked.connect(self._open_services_modal)
        self.btn_services.setFixedWidth(220)
        self.btn_services.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.btn_services.setStyleSheet(
            "QPushButton { background:#4fc3f7; color:#003b57; font-weight:bold; border-radius:6px; padding:8px; }"
        )
        services_row.addWidget(self.btn_services, 0, Qt.AlignLeft)
        self.btn_services_clear = QPushButton("✕")
        self.btn_services_clear.setFixedWidth(34)
        self.btn_services_clear.clicked.connect(self._clear_services_selection)
        self.btn_services_clear.setVisible(False)
        services_row.addWidget(self.btn_services_clear)
        services_row.addStretch(1)
        left_col.addLayout(services_row)
        self.lbl_services_summary = QLabel("Не выбрано")
        self.lbl_services_summary.setWordWrap(True)
        left_col.addWidget(self.lbl_services_summary)

        self.fitting_switch_bar = FittingSwitchLabeled(self)
        self.fitting_switch_bar.toggled.connect(self._on_fitting_toggled)
        left_col.addWidget(self.fitting_switch_bar)

        self.lbl_fitting_hint = QLabel("На схеме выберите сторону профиля.")
        self.lbl_fitting_hint.setWordWrap(True)
        self.lbl_fitting_hint.setVisible(False)
        left_col.addWidget(self.lbl_fitting_hint)

        self.lbl_fitting_side = QLabel("")
        self.lbl_fitting_side.setWordWrap(True)
        self.lbl_fitting_side.setVisible(False)
        left_col.addWidget(self.lbl_fitting_side)

        self._fitting_params = QWidget()
        fp_lay = QVBoxLayout(self._fitting_params)
        fp_lay.setContentsMargins(0, 0, 0, 0)
        fp_lay.setSpacing(4)
        holes_row = QHBoxLayout()
        holes_row.addWidget(QLabel("Отверстий:"))
        self.spin_fitting_holes = QSpinBox()
        self.spin_fitting_holes.setRange(2, 8)
        self.spin_fitting_holes.setValue(2)
        self.spin_fitting_holes.setEnabled(False)
        self.spin_fitting_holes.valueChanged.connect(self._on_fitting_hole_count_changed)
        holes_row.addWidget(self.spin_fitting_holes)
        holes_row.addSpacing(10)
        holes_row.addWidget(QLabel("Все замки:"))
        self.btn_master_hole_locks = MarkerLockButton(self)
        self.btn_master_hole_locks.setToolTip(
            "Все замки закрыты: полная симметрия — все отверстия вместе (зеркало краёв и равномерная сетка).\n"
            "Не все закрыты: пары «красных» по порядку вдоль профиля; внутри пары при движении сохраняется сумма отступов "
            "(центр пары не прыгает — можно сначала расставить «зелёных», потом закрыть пару и двигать).\n"
            "Все открыты: каждое отверстие только само за себя."
        )
        self.btn_master_hole_locks.setEnabled(False)
        self.btn_master_hole_locks.toggled.connect(self._on_master_hole_locks_toggled)
        holes_row.addWidget(self.btn_master_hole_locks)
        holes_row.addStretch()
        fp_lay.addLayout(holes_row)
        fp_lay.addWidget(QLabel("Петля (поставщик):"))
        self.cmb_fitting_hinge = QComboBox()
        self.cmb_fitting_hinge.addItem(FITTING_SUPPLIER_PROMPT)
        self.cmb_fitting_hinge.addItems(list(HINGE_SUPPLIERS_FITTING))
        self.cmb_fitting_hinge.setCurrentIndex(0)
        self.cmb_fitting_hinge.setEnabled(False)
        self.cmb_fitting_hinge.currentTextChanged.connect(self._on_fitting_supplier_hint_changed)
        fp_lay.addWidget(self.cmb_fitting_hinge)
        fit_btn_row = QHBoxLayout()
        self.btn_fitting_save = QPushButton("Сохранить")
        self.btn_fitting_save.setToolTip("Записать отверстия в расчёт (не путать с сохранением заказа внизу)")
        self.btn_fitting_save.setEnabled(False)
        self.btn_fitting_save.clicked.connect(self._on_save_fitting)
        fit_btn_row.addWidget(self.btn_fitting_save)
        self.btn_fitting_supply = QPushButton("Поставщик")
        self.btn_fitting_supply.setToolTip("Открыть список поставщика петли")
        self.btn_fitting_supply.setEnabled(False)
        self.btn_fitting_supply.clicked.connect(self._on_fitting_supply_clicked)
        fit_btn_row.addWidget(self.btn_fitting_supply)
        self.btn_fitting_cancel = QPushButton("Отмена")
        self.btn_fitting_cancel.setToolTip("Сбросить правки отверстий на этой стороне (без сохранения в расчёт)")
        self.btn_fitting_cancel.setEnabled(False)
        self.btn_fitting_cancel.clicked.connect(self._on_fitting_cancel_edit)
        fit_btn_row.addWidget(self.btn_fitting_cancel)
        fp_lay.addLayout(fit_btn_row)
        self.btn_fitting_clear_holes = QPushButton("Снять отверстия")
        self.btn_fitting_clear_holes.setToolTip(
            "Убрать маркеры и сохранённую присадку только на выбранной стороне"
        )
        self.btn_fitting_clear_holes.setEnabled(False)
        self.btn_fitting_clear_holes.clicked.connect(self._on_fitting_clear_holes)
        fp_lay.addWidget(self.btn_fitting_clear_holes)
        self._fitting_params.setVisible(False)
        left_col.addWidget(self._fitting_params)

        left_col.addStretch()

        # Панель справа: какие материалы выбраны и сколько стоят.
        # Сделаем её «настоящей» растяжимой (без фиксированной ширины).
        self._materials_panel = QWidget()
        self._materials_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._materials_panel.setMinimumWidth(220)
        self._materials_panel.setMaximumWidth(560)
        mp_lay = QVBoxLayout(self._materials_panel)
        mp_lay.setContentsMargins(8, 8, 8, 8)
        mp_lay.setSpacing(6)
        mp_lay.setAlignment(Qt.AlignTop)
        self._lbl_materials_title = QLabel("Материалы")
        self._lbl_materials_title.setFont(QFont("Arial", 13, QFont.Bold))
        mp_lay.addWidget(self._lbl_materials_title)

        self._materials_rows_layout = QVBoxLayout()
        self._materials_rows_layout.setSpacing(10)
        self._materials_rows_layout.setAlignment(Qt.AlignTop)
        mp_lay.addLayout(self._materials_rows_layout)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        mp_lay.addWidget(line)

        self._lbl_facade_final_total = QLabel("Итого: — ₽")
        self._lbl_facade_final_total.setFont(QFont("Arial", 13, QFont.Bold))
        self._lbl_facade_final_total.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        mp_lay.addWidget(self._lbl_facade_final_total)

        self.facade_canvas = FacadeCanvas(self)
        self.facade_canvas.setMinimumSize(260, 260)
        self.facade_canvas.set_seal_variant(self._seal_variant)
        self.facade_canvas.side_clicked.connect(self._on_side_clicked)
        self.facade_canvas.glass_clicked.connect(self._on_glass_clicked)
        self.facade_canvas.fitting_side_picked.connect(self._on_fitting_side_picked)
        self.facade_canvas.cutout_detail_clicked.connect(self._on_cutout_detail_clicked)
        self.facade_canvas.fitting_offsets_changed.connect(self._update_fitting_controls_enabled)
        self.facade_canvas.hole_locks_changed.connect(self._sync_master_lock_button)

        left_model = QWidget()
        lm = QHBoxLayout(left_model)
        lm.setContentsMargins(0, 0, 0, 0)
        lm.setSpacing(0)
        lm.addWidget(self._left_wrap, 0)
        lm.addWidget(self.facade_canvas, 8)

        # Разделитель: материалы можно растягивать мышью.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setContentsMargins(0, 0, 0, 0)
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left_model)
        splitter.addWidget(self._materials_panel)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 5)
        self._work_splitter = splitter

        layout.addWidget(splitter, 1)

        self._cutout_action_bar = QFrame()
        self._cutout_action_bar.setVisible(False)
        self._cutout_action_bar.setStyleSheet(
            "QFrame { background:#fff3e0; border:1px solid #ff9800; border-radius:6px; }"
        )
        _cabl = QHBoxLayout(self._cutout_action_bar)
        _cabl.setContentsMargins(8, 6, 8, 6)
        self._cutout_action_labels = QLabel("")
        self._cutout_action_labels.setWordWrap(True)
        _cabl.addWidget(self._cutout_action_labels, 1)
        self._cutout_btn_delete = QPushButton("Удалить вырез")
        self._cutout_btn_delete.clicked.connect(self._on_cutout_bar_delete)
        _cabl.addWidget(self._cutout_btn_delete)
        self._cutout_btn_hide = QPushButton("Скрыть")
        self._cutout_btn_hide.clicked.connect(self._hide_cutout_action_bar)
        _cabl.addWidget(self._cutout_btn_hide)
        layout.addWidget(self._cutout_action_bar)

        bot = QHBoxLayout()
        self._btn_add_facade = QPushButton("Добавить фасад")
        self._btn_add_facade.clicked.connect(self._on_add_facade_tab)
        self._btn_add_facade.setMinimumHeight(40)
        self._btn_add_facade.setStyleSheet(
            "QPushButton { background-color: #5e35b1; color: white; font-weight: bold; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #d1c4e9; color: #4a148c; }"
        )
        bot.addWidget(self._btn_add_facade)
        self._btn_maket = QPushButton("Maket")
        self._btn_maket.clicked.connect(self._on_open_maket_preview)
        self._btn_maket.setMinimumHeight(40)
        self._btn_maket.setStyleSheet(
            "QPushButton { background-color: #7b1fa2; color: white; font-weight: bold; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #d1c4e9; color: #6a1b9a; }"
        )
        bot.addWidget(self._btn_maket)
        self._btn_pdf_client = QPushButton("PDF")
        self._btn_pdf_client.clicked.connect(self._on_export_facade_pdf_client)
        self._btn_pdf_client.setMinimumHeight(40)
        self._btn_pdf_client.setStyleSheet(
            "QPushButton { background-color: #00838f; color: white; font-weight: bold; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #b2ebf2; color: #006064; }"
        )
        bot.addWidget(self._btn_pdf_client)
        self._btn_pdf_worker = QPushButton("PDF для работников")
        self._btn_pdf_worker.clicked.connect(self._on_export_facade_pdf_worker)
        self._btn_pdf_worker.setMinimumHeight(40)
        self._btn_pdf_worker.setStyleSheet(
            "QPushButton { background-color: #ef6c00; color: white; font-weight: bold; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #ffe0b2; color: #e65100; }"
        )
        bot.addWidget(self._btn_pdf_worker)
        self._btn_labels_profiles = QPushButton("Этикетки профилей")
        self._btn_labels_profiles.clicked.connect(self._on_export_profile_labels)
        self._btn_labels_profiles.setMinimumHeight(40)
        self._btn_labels_profiles.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #c8e6c9; color: #1b5e20; }"
        )
        bot.addWidget(self._btn_labels_profiles)
        self._btn_profile_history = None
        if self._linked_order_id:
            _save_label = "Сохранить в заказ № %s" % self._linked_order_id
        else:
            _save_label = "Создать заказ и сохранить"
        self._btn_save_order = QPushButton(_save_label)
        self._btn_save_order.setMinimumHeight(40)
        self._btn_save_order.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: white; font-weight: bold; padding: 8px 16px; border-radius: 6px; }"
            "QPushButton:disabled { background-color: #bbdefb; color: #0d47a1; }"
        )
        self._btn_save_order.clicked.connect(self._on_save_to_order)
        bot.addWidget(self._btn_save_order)
        bot.addStretch()
        btn_close = QPushButton("Закрыть")
        btn_close.setStyleSheet(
            "QPushButton { background-color: #c62828; color: white; font-weight: bold; "
            "padding: 8px 16px; border-radius: 6px; border: 1px solid #8e0000; }"
            "QPushButton:hover { background-color: #b71c1c; }"
            "QPushButton:pressed { background-color: #8e0000; }"
        )
        btn_close.clicked.connect(self.reject)
        bot.addWidget(btn_close)
        layout.addLayout(bot)

        self._on_dimensions_changed()
        self._update_fitting_controls_enabled()
        self._sync_fitting_switch_availability()
        self._update_materials_summary_panel()
        self._update_add_hinges_enabled()
        self._init_facade_tabs_model()
        self._update_save_button_enabled()
        self._update_services_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._did_maximize:
            self._did_maximize = True
            self.showMaximized()
            QTimer.singleShot(0, self._set_splitter_min_material_width)
            QTimer.singleShot(120, self._set_splitter_min_material_width)

    def _set_splitter_min_material_width(self) -> None:
        sp = getattr(self, "_work_splitter", None)
        if sp is None:
            return
        min_mat = int(getattr(self, "_materials_panel", QWidget()).minimumWidth() or 280)
        # Жёстко прижимаем правую панель к минимуму.
        sp.setSizes([10_000, min_mat])

    def _apply_linked_order_context(self):
        oid = self._linked_order_id
        row = None
        if oid:
            try:
                row = db_models.get_order(int(oid))
            except Exception:
                row = None
        if not oid or not row:
            self._refresh_mixed_order_banner()
            return
        cn = (row.get("client_name") or "").strip()
        if cn:
            self.client_edit.setText(cn)
        cid = row.get("client_id")
        try:
            self._client_id = int(cid) if cid is not None else self._client_id
        except (TypeError, ValueError):
            pass
        qcid = row.get("quick_client_id")
        try:
            self._quick_client_id = int(qcid) if qcid is not None else None
        except (TypeError, ValueError):
            self._quick_client_id = None
        if self._product_id:
            try:
                from logic.blocks_bundle import payload_for_product_id

                pl = payload_for_product_id(row.get("blocks_calc_json"), str(self._product_id))
            except Exception:
                pl = None
            if isinstance(pl, dict):
                self._apply_facade_payload(pl)
                if self._facade_tabs:
                    self._facade_tabs[0]["product_id"] = str(self._product_id)
        elif not self._append_new:
            try:
                _ver, products = parse_bundle(row.get("blocks_calc_json"))
            except Exception:
                products = []
            facade = [p for p in products if str(p.get("kind") or "") == "facade" and isinstance(p.get("payload"), dict)]
            if facade:
                self._facade_tabs = []
                for p in facade:
                    pl = p.get("payload") or {}
                    self._facade_tabs.append(
                        {
                            "product_id": p.get("id"),
                            "width_mm": int(pl.get("Ширина_мм") or 400),
                            "height_mm": int(pl.get("Высота_мм") or 600),
                            "profiles": dict(pl.get("Профили_по_сторонам") or {}),
                            "hinges": list(pl.get("Петли") or []),
                            "fittings": list(pl.get("Присадка") or []),
                            "glass": dict(pl.get("Стекло") or {}) if isinstance(pl.get("Стекло"), dict) else None,
                            "seal_variant": str(((pl.get("Уплотнитель") or {}).get("Цвет") or "прозрачный")),
                            "screw_variant": str(((pl.get("Винты") or {}).get("Цвет") or "серебро")),
                            "zamer_block": self._json_safe(pl.get("Замер") or {"Активирован": False, "Данные": None}),
                            "delivery_block": self._json_safe(pl.get("Доставка") or {"Активирован": False, "Данные": None}),
                        }
                    )
                self._active_facade_tab_idx = 0
                self._refresh_facade_tabs_bar()
                self._apply_tab_state_to_ui(self._facade_tabs[0])
        self._refresh_mixed_order_banner()

    def _refresh_mixed_order_banner(self):
        oid = self._linked_order_id
        ban = getattr(self, "_mixed_banner", None)
        if ban is None:
            return
        show = False
        n_other = 0
        if oid:
            try:
                row = db_models.get_order(int(oid))
                if row:
                    _, prods = parse_bundle(row.get("blocks_calc_json"))
                    n_fac = sum(1 for p in prods if str(p.get("kind") or "").strip() == "facade")
                    n_other = max(0, len(prods) - n_fac)
                    show = n_fac > 0 and n_other > 0
            except Exception:
                pass
        ban.setVisible(show)
        if show and getattr(self, "_lbl_mixed_order", None):
            self._lbl_mixed_order.setText(
                "В заказе также изделия стекло/зеркало (%d шт.) — откройте полный состав." % n_other
            )

    def _on_open_full_order_overview(self):
        oid = self._linked_order_id
        if not oid:
            return
        try:
            row = db_models.get_order(int(oid))
        except Exception:
            row = None
        if not row:
            return
        from ui.glass_order_overview_dialog import GlassOrderOverviewDialog

        GlassOrderOverviewDialog(dict(row), self).exec_()

    def _json_safe(self, obj):
        try:
            return json.loads(json.dumps(obj, default=str))
        except Exception:
            return obj

    def _services_any_selected(self) -> bool:
        zd = self._services_zamer_block.get("Данные") if isinstance(self._services_zamer_block, dict) else {}
        has_zamer = bool(self._services_zamer_block.get("Активирован")) and bool(
            (zd or {}).get("Замер") or (zd or {}).get("Монтаж")
        )
        has_delivery = bool((self._services_delivery_block or {}).get("Активирован"))
        return bool(has_zamer or has_delivery)

    def _calc_service_prices(self):
        dp = getattr(self, "_delivery_prices_cache", None)
        if dp is None:
            dp = fetch_delivery_prices()
            self._delivery_prices_cache = dp
        delivery_total = 0
        dblk = self._services_delivery_block if isinstance(self._services_delivery_block, dict) else {}
        if dblk.get("Активирован") and isinstance(dblk.get("Данные"), dict):
            dd = dict(dblk.get("Данные") or {})
            inside = bool(dd.get("Внутри КАД", True))
            km = dd.get("Расстояние до КАД")
            if inside or km is not None:
                delivery_total = int(delivery_price_rub(dp, inside, km) or 0)
        zamer_visit_total = 0
        zblk = self._services_zamer_block if isinstance(self._services_zamer_block, dict) else {}
        zd = zblk.get("Данные") if isinstance(zblk.get("Данные"), dict) else {}
        if zblk.get("Активирован") and isinstance(zd, dict):
            need_visit = bool(zd.get("Замер")) or bool(zd.get("Монтаж")) or (not bool(zd.get("Без замера")))
            vd = zd.get("Данные выезда") or {}
            inside = bool(vd.get("Внутри КАД", True))
            km = vd.get("Расстояние до КАД")
            if need_visit and (inside or km is not None):
                zamer_visit_total = int(delivery_price_rub(dp, inside, km) or 0)
        return delivery_total, zamer_visit_total

    def _update_services_ui(self):
        if self._services_any_selected():
            self.btn_services.setStyleSheet(
                "QPushButton { background:#43a047; color:#ffffff; font-weight:bold; border-radius:6px; padding:8px; }"
            )
            self.btn_services_clear.setVisible(True)
        else:
            self.btn_services.setStyleSheet(
                "QPushButton { background:#4fc3f7; color:#003b57; font-weight:bold; border-radius:6px; padding:8px; }"
            )
            self.btn_services_clear.setVisible(False)
        lines = []
        zblk = self._services_zamer_block if isinstance(self._services_zamer_block, dict) else {}
        zd = zblk.get("Данные") if isinstance(zblk.get("Данные"), dict) else {}
        dblk = self._services_delivery_block if isinstance(self._services_delivery_block, dict) else {}
        dd = dblk.get("Данные") if isinstance(dblk.get("Данные"), dict) else {}
        delivery_total, zamer_visit_total = self._calc_service_prices()
        if bool((zd or {}).get("Замер")):
            lines.append("Замер: %s ₽" % zamer_visit_total)
        if bool((zd or {}).get("Монтаж")):
            lines.append("Монтаж: %s ₽" % zamer_visit_total)
        if dblk.get("Активирован"):
            lines.append("Доставка: %s ₽" % delivery_total)
        addr = (zd or {}).get("Адрес") or (dd or {}).get("Адрес") or ""
        if addr:
            lines.append("Адрес: %s" % str(addr).strip())
        self.lbl_services_summary.setText("\n".join(lines) if lines else "Не выбрано")

    def _open_services_modal(self):
        d = _FacadeServicesDialog(self, self._services_zamer_block, self._services_delivery_block, self)
        if self.isVisible():
            pg = self.frameGeometry()
            dg = d.frameGeometry()
            dg.moveCenter(pg.center())
            d.move(dg.topLeft())
        if d.exec_() != QDialog.Accepted:
            return
        zblk, dblk = d.blocks()
        self._services_zamer_block = self._json_safe(zblk or {"Активирован": False, "Данные": None})
        self._services_delivery_block = self._json_safe(dblk or {"Активирован": False, "Данные": None})
        self._update_services_ui()
        self._update_materials_summary_panel()

    def _clear_services_selection(self):
        self._services_zamer_block = {"Активирован": False, "Данные": None}
        self._services_delivery_block = {"Активирован": False, "Данные": None}
        self._update_services_ui()
        self._update_materials_summary_panel()

    def _new_facade_tab_state(self) -> dict[str, Any]:
        return {
            "product_id": None,
            "width_mm": int(self.spin_width.value()) if hasattr(self, "spin_width") else 400,
            "height_mm": int(self.spin_height.value()) if hasattr(self, "spin_height") else 600,
            "quantity": int(self.spin_quantity.value()) if hasattr(self, "spin_quantity") else 1,
            "profiles": {},
            "hinges": [],
            "fittings": [],
            "glass": None,
            "seal_variant": "прозрачный",
            "screw_variant": "серебро",
            "zamer_block": {"Активирован": False, "Данные": None},
            "delivery_block": {"Активирован": False, "Данные": None},
        }

    def _capture_ui_to_tab_state(self) -> dict[str, Any]:
        return {
            "product_id": (self._facade_tabs[self._active_facade_tab_idx].get("product_id") if self._facade_tabs else None),
            "width_mm": int(self.spin_width.value()),
            "height_mm": int(self.spin_height.value()),
            "quantity": int(self.spin_quantity.value()) if hasattr(self, "spin_quantity") else 1,
            "profiles": self._json_safe(self._side_profiles),
            "hinges": self._json_safe(self._hinges),
            "fittings": self._json_safe(self._saved_fittings),
            "glass": self._json_safe(self._glass_selection) if self._glass_selection else None,
            "seal_variant": str(self._seal_variant or "прозрачный"),
            "screw_variant": str(self._screw_variant or "серебро"),
            "zamer_block": self._json_safe(self._services_zamer_block),
            "delivery_block": self._json_safe(self._services_delivery_block),
        }

    def _apply_tab_state_to_ui(self, st: dict[str, Any]) -> None:
        self._tab_sync_guard = True
        try:
            self.spin_width.setValue(int(st.get("width_mm") or 400))
            self.spin_height.setValue(int(st.get("height_mm") or 600))
            if hasattr(self, "spin_quantity"):
                self.spin_quantity.setValue(max(1, int(st.get("quantity") or 1)))
            self._side_profiles = dict(st.get("profiles") or {})
            self._hinges = list(st.get("hinges") or [])
            self._saved_fittings = _normalize_prisadka_list(st.get("fittings"))
            self._glass_selection = dict(st.get("glass") or {}) if isinstance(st.get("glass"), dict) else None
            self._seal_variant = str(st.get("seal_variant") or "прозрачный")
            self._screw_variant = str(st.get("screw_variant") or "серебро")
            self._services_zamer_block = self._json_safe(st.get("zamer_block") or {"Активирован": False, "Данные": None})
            self._services_delivery_block = self._json_safe(st.get("delivery_block") or {"Активирован": False, "Данные": None})
            self._update_services_ui()
            if getattr(self, "rb_seal_black", None):
                self.rb_seal_black.setChecked(self._seal_variant == "черный")
            if getattr(self, "rb_screw_gold", None):
                self.rb_screw_gold.setChecked(self._screw_variant == "золото")
            self.facade_canvas.set_side_profiles(self._side_profiles)
            self.facade_canvas.set_saved_fittings(self._saved_fittings)
            self.facade_canvas.set_glass_info(self._glass_selection)
            self.facade_canvas.set_seal_variant(self._seal_variant)
            self._sync_hinges_quantity_to_cuts()
            self._sync_fitting_switch_availability()
            self._update_add_hinges_enabled()
            self._update_materials_summary_panel()
            self._update_save_button_enabled()
        finally:
            self._tab_sync_guard = False

    def _refresh_facade_tabs_bar(self) -> None:
        if not hasattr(self, "_facade_tabs_bar"):
            return
        self._facade_tabs_bar.blockSignals(True)
        self._facade_tabs_bar.clear()
        n = len(self._facade_tabs)
        self._facade_tabs_bar.setVisible(n >= 2)
        if n >= 2:
            for i in range(n):
                page = QWidget()
                self._facade_tabs_bar.addTab(page, "Фасад %d" % (i + 1))
            idx = max(0, min(self._active_facade_tab_idx, n - 1))
            self._facade_tabs_bar.setCurrentIndex(idx)
        self._facade_tabs_bar.blockSignals(False)

    def _init_facade_tabs_model(self) -> None:
        if self._facade_tabs:
            return
        self._facade_tabs = [self._capture_ui_to_tab_state()]
        self._active_facade_tab_idx = 0
        self._refresh_facade_tabs_bar()

    def _on_facade_tab_changed(self, idx: int) -> None:
        if self._tab_sync_guard:
            return
        if idx < 0 or idx >= len(self._facade_tabs):
            return
        if 0 <= self._active_facade_tab_idx < len(self._facade_tabs):
            self._facade_tabs[self._active_facade_tab_idx] = self._capture_ui_to_tab_state()
        self._active_facade_tab_idx = idx
        self._apply_tab_state_to_ui(self._facade_tabs[idx])

    def _on_add_facade_tab(self) -> None:
        if not self._facade_tabs:
            self._init_facade_tabs_model()
        if 0 <= self._active_facade_tab_idx < len(self._facade_tabs):
            self._facade_tabs[self._active_facade_tab_idx] = self._capture_ui_to_tab_state()
        st = self._new_facade_tab_state()
        self._facade_tabs.append(st)
        self._active_facade_tab_idx = len(self._facade_tabs) - 1
        self._refresh_facade_tabs_bar()
        if self._facade_tabs_bar.isVisible():
            self._facade_tabs_bar.setCurrentIndex(self._active_facade_tab_idx)
        self._apply_tab_state_to_ui(st)

    def _can_save_facade_order(self) -> bool:
        has_profile = bool(self._side_profiles)
        has_glass = isinstance(self._glass_selection, dict) and bool((self._glass_selection.get("Название") or "").strip())
        return bool(has_profile and has_glass)

    def _update_save_button_enabled(self) -> None:
        can = self._can_save_facade_order()
        if hasattr(self, "_btn_save_order"):
            self._btn_save_order.setEnabled(can)
        for attr in ("_btn_maket", "_btn_profile_history"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setEnabled(can)
        can_prod = bool(can and self._can_take_material_now())
        for attr in ("_btn_pdf_client", "_btn_pdf_worker", "_btn_labels_profiles"):
            btn = getattr(self, attr, None)
            if btn is None:
                continue
            btn.setVisible(can_prod)
            btn.setEnabled(can_prod)

    def _facade_total_rub(self):
        breakdown = self._compute_facade_breakdown()
        return int(breakdown.get("final_total_rub") or 0)

    def _client_markup_factor(self) -> float:
        qcid = getattr(self, "_quick_client_id", None)
        if qcid:
            try:
                row = db_models.get_mirror_quick_client_by_id(int(qcid)) or {}
                p = int(row.get("markup_percent") or 0)
                return 1.0 + max(0, p) / 100.0
            except Exception:
                pass
        cid = self._client_id
        if not cid:
            return 1.0
        try:
            row = db_models.get_client_by_id(int(cid)) or {}
            return float(db_models.client_price_factor(row))
        except Exception:
            return 1.0

    @staticmethod
    def _markup_ceil(value: float | int, factor: float) -> int:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 0
        if factor <= 1.0:
            return int(round(v))
        return int(math.ceil(v * factor))

    def _profile_series_is_prisma(self, profile: dict | None) -> bool:
        series = str((profile or {}).get("series") or "").strip().upper()
        return "PRISMA" in series

    def _corner_code_for_profiles(self, a: dict | None, b: dict | None) -> str:
        """Выбор уголка: если хотя бы один профиль PRISMA — F3-031, иначе F3-021."""
        if self._profile_series_is_prisma(a) or self._profile_series_is_prisma(b):
            return "F3-031"
        return "F3-021"

    def _get_angle_seal_prices(self) -> dict[str, float]:
        """
        Кэш: стоимость угловых соединителей по вариантам.
        Из таблицы facades_angle_seal (item_type='Угловой соединитель', variant='F3-021'/'F3-031').
        """
        if self._angle_seal_price_cache is not None:
            return self._angle_seal_price_cache
        out: dict[str, float] = {"F3-021": 0.0, "F3-031": 0.0}
        rows = facades_get_all_angle_seal()
        for r in rows or []:
            if (r.get("item_type") or "").strip() != "Угловой соединитель":
                continue
            v = str(r.get("variant") or "").strip()
            if v not in out:
                continue
            try:
                out[v] = float(r.get("price") or 0)
            except (TypeError, ValueError):
                out[v] = 0.0
        self._angle_seal_price_cache = out
        return out

    def _get_seal_prices_per_meter(self) -> dict[str, float]:
        """
        Стоимость уплотнителя (по метрам) из таблицы facades_angle_seal.
        item_type='Уплотнитель', variant='черный'/'прозрачный'.
        """
        if self._seal_price_cache is not None:
            return self._seal_price_cache
        out: dict[str, float] = {"черный": 0.0, "прозрачный": 0.0}
        rows = facades_get_all_angle_seal()
        for r in rows or []:
            if (r.get("item_type") or "").strip() != "Уплотнитель":
                continue
            v = str(r.get("variant") or "").strip().lower()
            if "черн" in v:
                out["черный"] = float(r.get("price") or 0)
            elif "прозрач" in v:
                out["прозрачный"] = float(r.get("price") or 0)
        self._seal_price_cache = out
        return out

    def _get_screw_prices(self) -> dict[str, float]:
        """Стоимость винтов по цветам из таблицы facades_angle_seal."""
        if self._screw_price_cache is not None:
            return self._screw_price_cache
        out: dict[str, float] = {"серебро": 0.0, "золото": 0.0}
        rows = facades_get_all_angle_seal()
        for r in rows or []:
            if (r.get("item_type") or "").strip() != "Винт":
                continue
            v = str(r.get("variant") or "").strip().lower()
            if "сереб" in v:
                out["серебро"] = float(r.get("price") or 0)
            elif "золот" in v:
                out["золото"] = float(r.get("price") or 0)
        self._screw_price_cache = out
        return out

    def _screw_icon_path(self, color_variant: str) -> str | None:
        key = "золото" if "золот" in str(color_variant or "").lower() else "серебро"
        if key in self._screw_icon_cache:
            return self._screw_icon_cache[key]
        base = os.path.join(get_base_dir(), "FASAD", "img")
        target = "gold.png" if key == "золото" else "silver.png"
        try:
            for dp, _dns, fns in os.walk(base):
                dpl = str(dp).lower()
                if "фурнитура" not in dpl or "винт" not in dpl:
                    continue
                for fn in fns or []:
                    if str(fn).lower() == target:
                        p = os.path.join(dp, fn)
                        if os.path.isfile(p):
                            self._screw_icon_cache[key] = p
                            return p
        except Exception:
            pass
        self._screw_icon_cache[key] = None
        return None

    def _compute_facade_breakdown(self) -> dict[str, Any]:
        width_mm = int(self.spin_width.value())
        height_mm = int(self.spin_height.value())
        quantity = max(1, int(self.spin_quantity.value()) if hasattr(self, "spin_quantity") else 1)

        # --- Профили ---
        profiles_cost_rows: list[dict[str, Any]] = []
        by_id: dict[int, dict[str, Any]] = {}
        for side, prof in (self._side_profiles or {}).items():
            if not isinstance(prof, dict):
                continue
            pid_raw = prof.get("id")
            try:
                pid = int(pid_raw)
            except (TypeError, ValueError):
                continue
            price_m = float(prof.get("price_per_meter") or 0)
            length_m = (width_mm / 1000.0) if side in ("top", "bottom") else (height_mm / 1000.0)
            side_cost = length_m * price_m
            row = by_id.get(pid)
            if not row:
                row = {
                    "profile": prof,
                    "count_sides": 0,
                    "total_rub": 0.0,
                    "length_m_by_side": {},
                    "total_length_m": 0.0,
                }
                by_id[pid] = row
            row["length_m_by_side"][side] = float(length_m)
            row["count_sides"] += 1
            row["total_rub"] += side_cost
        for pid, row in by_id.items():
            row["total_length_m"] = sum(float(x or 0) for x in (row.get("length_m_by_side") or {}).values())
            profiles_cost_rows.append({"id": pid, **row})
        profiles_cost_rows.sort(key=lambda r: (str(r.get("profile", {}).get("series") or ""), str(r.get("profile", {}).get("name") or "")))

        profiles_total_rub = sum(float(r.get("total_rub") or 0) for r in profiles_cost_rows)

        # --- Угловые соединители (уголки) ---
        corners_qty: dict[str, int] = {"F3-021": 0, "F3-031": 0}
        top = self._side_profiles.get("top") if isinstance(self._side_profiles, dict) else None
        bottom = self._side_profiles.get("bottom") if isinstance(self._side_profiles, dict) else None
        left = self._side_profiles.get("left") if isinstance(self._side_profiles, dict) else None
        right = self._side_profiles.get("right") if isinstance(self._side_profiles, dict) else None

        def _inc_if(a, b):
            if not (a and b and isinstance(a, dict) and isinstance(b, dict)):
                return
            code = self._corner_code_for_profiles(a, b)
            corners_qty[code] += 1

        _inc_if(top, left)     # LT
        _inc_if(bottom, left)  # LB
        _inc_if(top, right)    # RT
        _inc_if(bottom, right) # RB

        corner_prices = self._get_angle_seal_prices()
        corners_total_rub = 0.0
        for code, qty in corners_qty.items():
            corners_total_rub += qty * float(corner_prices.get(code) or 0)

        # --- Петли ---
        hinge_qty_by_id: dict[int, dict[str, Any]] = {}
        hinges_total_rub = 0.0
        for item in (self._hinges or []):
            if not isinstance(item, dict):
                continue
            hinge = item.get("hinge") or {}
            if not isinstance(hinge, dict):
                continue
            hid_raw = hinge.get("id")
            if hid_raw is None:
                continue
            try:
                hid = int(hid_raw)
            except (TypeError, ValueError):
                continue
            qty = int(item.get("quantity") or 0)
            if qty <= 0:
                continue
            price_one = float(hinge.get("price") or 0)
            row = hinge_qty_by_id.get(hid)
            if not row:
                row = {"hinge": hinge, "count": 0, "total_rub": 0.0}
                hinge_qty_by_id[hid] = row
            row["count"] += qty
            row["total_rub"] += qty * price_one
            hinges_total_rub += qty * price_one
        hinges_cost_rows = []
        for hid, row in hinge_qty_by_id.items():
            hinges_cost_rows.append({"id": hid, **row})
        hinges_cost_rows.sort(key=lambda r: str(r.get("hinge", {}).get("number") or ""))

        # --- Стекло ---
        glass_total_rub = 0
        blocks_selected = (self._glass_selection or {}).get("blocks_selected") if isinstance(self._glass_selection, dict) else None
        if isinstance(blocks_selected, dict):
            try:
                glass_total_rub = int(product_sum_excluding_order_level(blocks_selected) or 0)
            except Exception:
                glass_total_rub = 0

        # --- Уплотнитель (по периметру фасада) ---
        perimeter_m = 2 * (width_mm + height_mm) / 1000.0
        seal_prices = self._get_seal_prices_per_meter()
        seal_price_per_m = float(seal_prices.get(self._seal_variant or "прозрачный") or 0)
        seal_total_rub = perimeter_m * seal_price_per_m

        # --- Винты для уголков (2 шт на 1 уголок) ---
        screws_qty = 2 * int(sum(int(x or 0) for x in corners_qty.values()))
        screw_prices = self._get_screw_prices()
        screw_price_one = float(screw_prices.get(self._screw_variant or "серебро") or 0)
        screws_total_rub = screws_qty * screw_price_one

        dblk = self._services_delivery_block if isinstance(self._services_delivery_block, dict) else {}
        zblk = self._services_zamer_block if isinstance(self._services_zamer_block, dict) else {}
        delivery_total_rub, zamer_visit_total_rub = self._calc_service_prices()

        mkf = self._client_markup_factor()
        if mkf > 1.0:
            for r in profiles_cost_rows:
                r["total_rub"] = self._markup_ceil(r.get("total_rub") or 0, mkf)
            for r in hinges_cost_rows:
                r["total_rub"] = self._markup_ceil(r.get("total_rub") or 0, mkf)
            corners_total_rub = self._markup_ceil(corners_total_rub, mkf)
            glass_total_rub = self._markup_ceil(glass_total_rub, mkf)
            seal_price_per_m = self._markup_ceil(seal_price_per_m, mkf)
            seal_total_rub = self._markup_ceil(seal_total_rub, mkf)
            screw_price_one = self._markup_ceil(screw_price_one, mkf)
            screws_total_rub = self._markup_ceil(screws_total_rub, mkf)
            # Доставка и выезд замера/монтажа — без клиентской наценки

        profiles_total_rub = sum(float(r.get("total_rub") or 0) for r in profiles_cost_rows)
        hinges_total_rub = sum(float(r.get("total_rub") or 0) for r in hinges_cost_rows)
        # Масштабируем изделие на количество одинаковых фасадов.
        for r in profiles_cost_rows:
            r["total_rub"] = float(r.get("total_rub") or 0) * quantity
            r["total_length_m"] = float(r.get("total_length_m") or 0) * quantity
        for code in list(corners_qty.keys()):
            corners_qty[code] = int(corners_qty.get(code) or 0) * quantity
        for r in hinges_cost_rows:
            r["count"] = int(r.get("count") or 0) * quantity
            r["total_rub"] = float(r.get("total_rub") or 0) * quantity
        profiles_total_rub = float(profiles_total_rub) * quantity
        corners_total_rub = float(corners_total_rub) * quantity
        hinges_total_rub = float(hinges_total_rub) * quantity
        glass_total_rub = float(glass_total_rub) * quantity
        perimeter_m = float(perimeter_m) * quantity
        seal_total_rub = float(seal_total_rub) * quantity
        screws_qty = int(screws_qty) * quantity
        screws_total_rub = float(screws_total_rub) * quantity

        final_total_rub = (
            profiles_total_rub
            + corners_total_rub
            + hinges_total_rub
            + float(glass_total_rub or 0)
            + float(seal_total_rub or 0)
            + float(screws_total_rub or 0)
            + float(delivery_total_rub or 0)
            + float(zamer_visit_total_rub or 0)
        )
        return {
            "profiles_rows": profiles_cost_rows,
            "profiles_total_rub": profiles_total_rub,
            "corners_qty": corners_qty,
            "corners_total_rub": corners_total_rub,
            "hinges_rows": hinges_cost_rows,
            "hinges_total_rub": hinges_total_rub,
            "glass_total_rub": glass_total_rub,
            "seal_price_per_m": seal_price_per_m,
            "seal_perimeter_m": perimeter_m,
            "seal_total_rub": seal_total_rub,
            "screws_qty": screws_qty,
            "screw_price_one": screw_price_one,
            "screws_total_rub": screws_total_rub,
            "delivery_total_rub": int(delivery_total_rub),
            "zamer_visit_total_rub": int(zamer_visit_total_rub),
            "delivery_block": dblk,
            "zamer_block": zblk,
            "final_total_rub": final_total_rub,
            "quantity": quantity,
        }

    def _clear_layout(self, layout) -> None:
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w:
                w.setParent(None)
                w.deleteLater()
            # nested layout
            child_layout = item.layout()
            if child_layout:
                self._clear_layout(child_layout)

    def _update_materials_summary_panel(self) -> None:
        if not getattr(self, "_materials_panel_built", False):
            # отметим после первого билда (чтобы не падать на ранних стадиях)
            self._materials_panel_built = True
        if not hasattr(self, "_materials_rows_layout"):
            return

        self._clear_layout(self._materials_rows_layout)

        breakdown = self._compute_facade_breakdown()
        profiles_rows = breakdown.get("profiles_rows") or []
        corners_qty = breakdown.get("corners_qty") or {}
        hinges_rows = breakdown.get("hinges_rows") or []
        glass_total = int(breakdown.get("glass_total_rub") or 0)
        seal_perimeter_m = float(breakdown.get("seal_perimeter_m") or 0)
        seal_total_rub = int(round(float(breakdown.get("seal_total_rub") or 0)))
        screws_qty = int(breakdown.get("screws_qty") or 0)
        screws_total_rub = int(round(float(breakdown.get("screws_total_rub") or 0)))

        # Профили (уникальные id)
        for row in profiles_rows:
            prof = row.get("profile") or {}
            total_rub = int(round(float(row.get("total_rub") or 0)))
            total_length_m = float(row.get("total_length_m") or 0)
            lengths_by_side = row.get("length_m_by_side") or {}

            w = QWidget()
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            hl = QHBoxLayout(w)
            hl.setContentsMargins(0, 3, 0, 3)
            hl.setSpacing(6)

            icon_lbl = QLabel()
            icon_lbl.setFixedSize(44, 44)
            path = _fasad_img_path(
                prof.get("photo_number"),
                series=prof.get("series"),
                name=prof.get("name"),
            )
            pix = QPixmap(path) if path and os.path.isfile(path) else QPixmap()
            if pix and not pix.isNull():
                icon_lbl.setPixmap(pix.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                icon_lbl.setStyleSheet("background: #e0e0e0; border: 1px solid #cfcfcf;")
            hl.addWidget(icon_lbl)

            side_parts: list[str] = []
            for sk in ("top", "bottom", "left", "right"):
                if sk in lengths_by_side:
                    side_ru = FITTING_SIDE_LABELS_RU.get(sk, sk)
                    side_parts.append("%s %.2f м" % (side_ru, float(lengths_by_side.get(sk) or 0)))
            required_text = "; ".join(side_parts) if side_parts else "—"

            txt = QLabel(
                "Профиль: %s\nЦвет: %s\nИтого длина: %.2f м\nТребуется: %s"
                % ((prof.get("name") or prof.get("series") or "—"), prof.get("color") or "—", total_length_m, required_text)
            )
            txt.setFont(QFont("Arial", 12))
            txt.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            txt.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            txt.setMinimumWidth(0)
            txt.setWordWrap(True)
            txt_cost_col = QVBoxLayout()
            txt_cost_col.setContentsMargins(0, 0, 0, 0)
            txt_cost_col.setSpacing(2)
            txt_cost_col.addWidget(txt)

            cost_lbl = QLabel("%d ₽" % total_rub)
            cost_lbl.setFont(QFont("Arial", 13, QFont.Bold))
            cost_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            cost_lbl.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
            txt_cost_col.addWidget(cost_lbl, 0, Qt.AlignLeft | Qt.AlignTop)
            hl.addLayout(txt_cost_col, 1)

            self._materials_rows_layout.addWidget(w)

        # Уголки (могут быть оба варианта)
        corner_prices = self._get_angle_seal_prices()
        for code in ("F3-021", "F3-031"):
            qty = int(corners_qty.get(code) or 0)
            if qty <= 0:
                continue
            total_rub = int(round(qty * float(corner_prices.get(code) or 0)))

            w = QWidget()
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            hl = QHBoxLayout(w)
            hl.setContentsMargins(0, 3, 0, 3)
            hl.setSpacing(6)

            icon_lbl = QLabel()
            icon_lbl.setFixedSize(44, 44)
            corner_path = os.path.join(get_base_dir(), "FASAD", "img", "фурнитура", code, "LB.png")
            if os.path.isfile(corner_path):
                pix = QPixmap(corner_path)
                if pix and not pix.isNull():
                    icon_lbl.setPixmap(pix.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    icon_lbl.setStyleSheet("background: #e0e0e0; border: 1px solid #cfcfcf;")
            else:
                icon_lbl.setStyleSheet("background: #e0e0e0; border: 1px solid #cfcfcf;")
            hl.addWidget(icon_lbl)

            txt = QLabel("Угловой соединитель %s\nКол-во: %d шт" % (code, qty))
            txt.setFont(QFont("Arial", 12))
            txt.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            txt.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            txt.setMinimumWidth(0)
            txt.setWordWrap(True)
            txt_cost_col = QVBoxLayout()
            txt_cost_col.setContentsMargins(0, 0, 0, 0)
            txt_cost_col.setSpacing(2)
            txt_cost_col.addWidget(txt)

            cost_lbl = QLabel("%d ₽" % total_rub)
            cost_lbl.setFont(QFont("Arial", 13, QFont.Bold))
            cost_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            cost_lbl.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
            txt_cost_col.addWidget(cost_lbl, 0, Qt.AlignLeft | Qt.AlignTop)
            hl.addLayout(txt_cost_col, 1)

            self._materials_rows_layout.addWidget(w)

        # Винты (2 шт на уголок)
        if screws_qty > 0:
            w = QWidget()
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            hl = QHBoxLayout(w)
            hl.setContentsMargins(0, 3, 0, 3)
            hl.setSpacing(6)

            icon_lbl = QLabel()
            icon_lbl.setFixedSize(44, 44)
            screw_path = self._screw_icon_path(self._screw_variant)
            if screw_path and os.path.isfile(screw_path):
                spx = QPixmap(screw_path)
                if spx and not spx.isNull():
                    icon_lbl.setPixmap(spx.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    icon_lbl.setStyleSheet("background: #e0e0e0; border: 1px solid #cfcfcf;")
            else:
                icon_lbl.setStyleSheet("background: #e0e0e0; border: 1px solid #cfcfcf;")
            hl.addWidget(icon_lbl)

            txt = QLabel("Винт: %s\nКол-во: %d шт" % (self._screw_variant, screws_qty))
            txt.setFont(QFont("Arial", 12))
            txt.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            txt.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            txt.setMinimumWidth(0)
            txt.setWordWrap(True)
            txt_cost_col = QVBoxLayout()
            txt_cost_col.setContentsMargins(0, 0, 0, 0)
            txt_cost_col.setSpacing(2)
            txt_cost_col.addWidget(txt)

            cost_lbl = QLabel("%d ₽" % screws_total_rub)
            cost_lbl.setFont(QFont("Arial", 13, QFont.Bold))
            cost_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            cost_lbl.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
            txt_cost_col.addWidget(cost_lbl, 0, Qt.AlignLeft | Qt.AlignTop)
            hl.addLayout(txt_cost_col, 1)

            self._materials_rows_layout.addWidget(w)

        # Уплотнитель (периметр) — всегда одна строка
        w = QWidget()
        w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        hl = QHBoxLayout(w)
        hl.setContentsMargins(0, 3, 0, 3)
        hl.setSpacing(6)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(44, 44)
        seal_pm = self.facade_canvas._seal_pixmap(self._seal_variant) if getattr(self, "facade_canvas", None) else None
        if seal_pm:
            icon_lbl.setPixmap(seal_pm.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            icon_lbl.setStyleSheet("background: #e0e0e0; border: 1px solid #cfcfcf;")
        hl.addWidget(icon_lbl)

        seal_name = "черный" if self._seal_variant == "черный" else "прозрачный"
        txt = QLabel("Уплотнитель: %s\nПериметр: %.2f м" % (seal_name, seal_perimeter_m))
        txt.setFont(QFont("Arial", 12))
        txt.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        txt.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        txt.setMinimumWidth(0)
        txt.setWordWrap(True)
        txt_cost_col = QVBoxLayout()
        txt_cost_col.setContentsMargins(0, 0, 0, 0)
        txt_cost_col.setSpacing(2)
        txt_cost_col.addWidget(txt)

        cost_lbl = QLabel("%d ₽" % seal_total_rub)
        cost_lbl.setFont(QFont("Arial", 13, QFont.Bold))
        cost_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        cost_lbl.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
        txt_cost_col.addWidget(cost_lbl, 0, Qt.AlignLeft | Qt.AlignTop)
        hl.addLayout(txt_cost_col, 1)

        self._materials_rows_layout.addWidget(w)

        # Петли (уникальные id)
        for row in hinges_rows:
            hinge = row.get("hinge") or {}
            qty = int(row.get("count") or 0)
            total_rub = int(round(float(row.get("total_rub") or 0)))
            hid = None
            try:
                hid = int(row.get("id") or hinge.get("id") or 0)
            except Exception:
                hid = None

            def _delete_one_hinge(hid_local=hid):
                if hid_local is None:
                    return
                cur = []
                for it in (self._hinges or []):
                    hh = it.get("hinge") or {}
                    try:
                        if int(hh.get("id") or 0) == int(hid_local):
                            continue
                    except Exception:
                        pass
                    cur.append(it)
                self._hinges = cur
                self._update_materials_summary_panel()

            roww = _HoldToDeleteRow(delete_callback=_delete_one_hinge, hold_seconds=1.0)
            roww.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

            hpath = resolve_hinge_image_path(hinge.get("photo_number"), hinge=hinge)
            hpix = QPixmap(hpath) if hpath and os.path.isfile(hpath) else None
            if hpix and not hpix.isNull():
                roww.set_icon_pixmap(hpix.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                roww.set_icon_pixmap(None)

            roww.set_text("Петля: %s\nКол-во: %d" % ((hinge.get("name") or hinge.get("number") or "—"), qty))
            roww.text_lbl.setFont(QFont("Arial", 12))
            roww.set_cost("%d ₽" % total_rub)

            self._materials_rows_layout.addWidget(roww)

        # Стекло
        if isinstance(self._glass_selection, dict) and (self._glass_selection.get("Название") or "").strip():
            g_name = str(self._glass_selection.get("Название") or "Стекло").strip()
        else:
            g_name = "Стекло"
        if glass_total > 0 or (self._glass_selection is not None):
            w = QWidget()
            w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            hl = QHBoxLayout(w)
            hl.setContentsMargins(0, 3, 0, 3)
            hl.setSpacing(6)
            icon_lbl = QLabel()
            icon_lbl.setFixedSize(44, 44)
            g_color = str((self._glass_selection or {}).get("Цвет") or "")
            qc = _glass_fill_color(g_color)
            icon_lbl.setStyleSheet(
                "background-color: %s; border: 1px solid #cfcfcf;" % qc.name()
            )
            hl.addWidget(icon_lbl)

            g_th = self._glass_selection.get("Толщина (мм)") or 0
            try:
                g_th = int(g_th)
            except Exception:
                g_th = 0
            g_w = self._glass_selection.get("Ширина (мм)") or 0
            g_h = self._glass_selection.get("Высота (мм)") or 0
            try:
                g_w = int(g_w)
            except Exception:
                g_w = 0
            try:
                g_h = int(g_h)
            except Exception:
                g_h = 0

            g_gloss_line = "Отделка: глянец" if "глянец" in (g_name or "").lower() else ""
            th_line = "Толщина: %d мм" % g_th if g_th else "Толщина: —"
            size_line = "Размер: %d × %d мм" % (g_w, g_h) if (g_w > 0 and g_h > 0) else "Размер: —"

            txt_text = "Стекло:\n%s" % g_name
            if g_color:
                txt_text += "\nЦвет: %s" % g_color
            txt_text += "\n%s" % th_line
            if g_gloss_line:
                txt_text += "\n%s" % g_gloss_line
            txt_text += "\n%s" % size_line

            txt = QLabel(txt_text)
            txt.setFont(QFont("Arial", 12))
            txt.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            txt.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            txt.setMinimumWidth(0)
            txt.setWordWrap(True)
            txt_cost_col = QVBoxLayout()
            txt_cost_col.setContentsMargins(0, 0, 0, 0)
            txt_cost_col.setSpacing(2)
            txt_cost_col.addWidget(txt)

            cost_lbl = QLabel("%d ₽" % int(glass_total or 0))
            cost_lbl.setFont(QFont("Arial", 13, QFont.Bold))
            cost_lbl.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            cost_lbl.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
            txt_cost_col.addWidget(cost_lbl, 0, Qt.AlignLeft | Qt.AlignTop)
            hl.addLayout(txt_cost_col, 1)

            self._materials_rows_layout.addWidget(w)

        delivery_total = int(breakdown.get("delivery_total_rub") or 0)
        zamer_visit_total = int(breakdown.get("zamer_visit_total_rub") or 0)
        if delivery_total > 0:
            row = QLabel("Доставка: %d ₽" % delivery_total)
            row.setFont(QFont("Arial", 12, QFont.Bold))
            self._materials_rows_layout.addWidget(row)
        if zamer_visit_total > 0:
            row = QLabel("Замер/монтаж (выезд): %d ₽" % zamer_visit_total)
            row.setFont(QFont("Arial", 12, QFont.Bold))
            self._materials_rows_layout.addWidget(row)

        final_total = int(round(float(breakdown.get("final_total_rub") or 0)))
        if hasattr(self, "_lbl_facade_final_total"):
            self._lbl_facade_final_total.setText("Итого: %d ₽" % final_total)
        self._update_save_button_enabled()

    def _collect_facade_payload(self):
        return {
            "Тип": "Фасад",
            "Ширина_мм": int(self.spin_width.value()),
            "Высота_мм": int(self.spin_height.value()),
            "Количество": int(self.spin_quantity.value()) if hasattr(self, "spin_quantity") else 1,
            "Профили_по_сторонам": self._json_safe(self._side_profiles),
            "Петли": self._json_safe(self._hinges),
            "Присадка": self._json_safe(self._saved_fittings) if self._saved_fittings else None,
            "Стекло": self._json_safe(self._glass_selection) if self._glass_selection else None,
            "Уплотнитель": {"Цвет": self._seal_variant},
            "Винты": {"Цвет": self._screw_variant},
            "Замер": self._json_safe(self._services_zamer_block),
            "Доставка": self._json_safe(self._services_delivery_block),
            "_total_rub": self._facade_total_rub(),
        }

    def _apply_facade_payload(self, pl):
        if not isinstance(pl, dict):
            return
        try:
            self.spin_width.setValue(int(pl.get("Ширина_мм") or self.spin_width.value()))
            self.spin_height.setValue(int(pl.get("Высота_мм") or self.spin_height.value()))
            if hasattr(self, "spin_quantity"):
                self.spin_quantity.setValue(max(1, int(pl.get("Количество") or 1)))
        except (TypeError, ValueError):
            pass
        prof = pl.get("Профили_по_сторонам") or pl.get("Профили") or {}
        if isinstance(prof, dict):
            self._side_profiles = dict(prof)
            self.facade_canvas.set_side_profiles(self._side_profiles)
        hinges = pl.get("Петли")
        if isinstance(hinges, list):
            self._hinges = list(hinges)
        self._saved_fittings = _normalize_prisadka_list(pl.get("Присадка"))
        self.facade_canvas.set_saved_fittings(self._saved_fittings)
        self._sync_hinges_quantity_to_cuts()
        self._update_add_hinges_enabled()
        self._update_materials_summary_panel()
        glass = pl.get("Стекло")
        if isinstance(glass, dict):
            self._glass_selection = dict(glass)
        else:
            self._glass_selection = None
        self.facade_canvas.set_glass_info(self._glass_selection)

        seal = pl.get("Уплотнитель") or {}
        if isinstance(seal, dict):
            col = str(seal.get("Цвет") or "").strip().lower()
            if "черн" in col:
                self._seal_variant = "черный"
            else:
                self._seal_variant = "прозрачный"
            try:
                if getattr(self, "rb_seal_black", None):
                    self.rb_seal_black.setChecked(self._seal_variant == "черный")
            except Exception:
                pass
            self.facade_canvas.set_seal_variant(self._seal_variant)
            self._update_materials_summary_panel()
        screws = pl.get("Винты") or {}
        if isinstance(screws, dict):
            col = str(screws.get("Цвет") or "").strip().lower()
            if "золот" in col:
                self._screw_variant = "золото"
            else:
                self._screw_variant = "серебро"
            try:
                if getattr(self, "rb_screw_gold", None):
                    self.rb_screw_gold.setChecked(self._screw_variant == "золото")
            except Exception:
                pass
            self._update_materials_summary_panel()
        self._services_zamer_block = self._json_safe(pl.get("Замер") or {"Активирован": False, "Данные": None})
        self._services_delivery_block = self._json_safe(pl.get("Доставка") or {"Активирован": False, "Данные": None})
        self._update_services_ui()
        self._sync_fitting_switch_availability()
        self._update_materials_summary_panel()
        if self._facade_tabs:
            cur = self._capture_ui_to_tab_state()
            cur["product_id"] = self._product_id
            self._facade_tabs[self._active_facade_tab_idx] = cur
            self._refresh_facade_tabs_bar()

    def _sync_fitting_switch_availability(self):
        ok = bool(self._side_profiles)
        if not ok and self.fitting_switch_bar.isChecked():
            self.fitting_switch_bar.setChecked(False, emit=True)
        self.fitting_switch_bar.setEnabled(ok)

    def _upsert_saved_fitting_for_side(self, side, supplier, holes_payload):
        rec = {
            "сторона": side,
            "поставщик_петли": supplier,
            "отверстия": holes_payload,
        }
        for i, e in enumerate(self._saved_fittings):
            if (e.get("сторона") or e.get("side")) == side:
                self._saved_fittings[i] = rec
                break
        else:
            self._saved_fittings.append(rec)
        self.facade_canvas.set_saved_fittings(self._saved_fittings)
        self._sync_hinges_quantity_to_cuts()
        self._update_add_hinges_enabled()
        self._update_materials_summary_panel()

    def _remove_saved_fitting_for_side(self, side):
        self._saved_fittings = [
            e for e in (self._saved_fittings or []) if (e.get("сторона") or e.get("side")) != side
        ]
        self.facade_canvas.set_saved_fittings(self._saved_fittings)
        self._sync_hinges_quantity_to_cuts()
        self._update_add_hinges_enabled()
        self._update_materials_summary_panel()

    def _remove_cutout_at(self, entry_index, hole_index):
        if entry_index < 0 or entry_index >= len(self._saved_fittings or []):
            return
        entry = dict(self._saved_fittings[entry_index])
        holes = list(entry.get("отверстия") or entry.get("holes") or [])
        if hole_index < 0 or hole_index >= len(holes):
            return
        holes.pop(hole_index)
        if not holes:
            self._saved_fittings.pop(entry_index)
        else:
            key = "отверстия" if "отверстия" in entry else "holes"
            entry[key] = holes
            self._saved_fittings[entry_index] = entry
        self.facade_canvas.set_saved_fittings(self._saved_fittings)
        self._sync_hinges_quantity_to_cuts()
        self._update_add_hinges_enabled()
        self._update_materials_summary_panel()

    def _remove_all_cuts_for_side(self, side):
        self._remove_saved_fitting_for_side(side)

    def _on_fitting_clear_holes(self):
        if not self._fitting_switch_on or not self._fitting_selected_side:
            return
        side = self._fitting_selected_side
        self._remove_saved_fitting_for_side(side)
        self._fitting_selected_side = None
        self._fitting_snapshot_offsets = []
        self.lbl_fitting_side.setVisible(False)
        self.lbl_fitting_side.clear()
        self.facade_canvas.end_fitting_edit_session()
        self.facade_canvas.set_fitting_mode(True, True)
        self._update_fitting_hints()
        self._update_fitting_controls_enabled()

    def _default_fitting_offsets_mm(self, side, n):
        w = self.spin_width.value()
        h = self.spin_height.value()
        L = w if side in ('top', 'bottom') else h
        lo, hi = 20, max(21, L - 20)
        if n <= 0:
            return []
        if n == 1:
            return [(lo + hi) // 2]
        return [int(round(lo + (hi - lo) * (i + 1) / (n + 1))) for i in range(n)]

    def _update_fitting_hints(self):
        if not self._fitting_switch_on:
            self.lbl_fitting_hint.setVisible(False)
            self.lbl_fitting_banner.setVisible(False)
            return
        self.lbl_fitting_banner.setVisible(True)
        self.lbl_fitting_banner.setText(
            "Режим присадки активирован. Выберите сторону профиля на схеме и задайте отверстия."
        )
        self.lbl_fitting_hint.setVisible(True)
        if not self._fitting_selected_side:
            self.lbl_fitting_hint.setText("На схеме нажмите сторону профиля, где нужны отверстия.")
        else:
            self.lbl_fitting_hint.setText("Перетащите маркеры вдоль профиля или введите отступ (мм) и «ОК».")

    def _on_master_hole_locks_toggled(self, checked):
        if not self._fitting_selected_side:
            return
        self.facade_canvas.set_all_hole_locks(bool(checked))

    def _sync_master_lock_button(self):
        if not getattr(self, "btn_master_hole_locks", None):
            return
        locks = self.facade_canvas.get_hole_locks()
        self.btn_master_hole_locks.blockSignals(True)
        if not locks:
            self.btn_master_hole_locks.setChecked(True)
        else:
            self.btn_master_hole_locks.setChecked(all(locks))
        self.btn_master_hole_locks.blockSignals(False)

    def _update_fitting_controls_enabled(self):
        self._fitting_params.setVisible(self._fitting_switch_on)
        has_side = bool(self._fitting_selected_side)
        self.spin_fitting_holes.setEnabled(self._fitting_switch_on and has_side)
        self.cmb_fitting_hinge.setEnabled(self._fitting_switch_on and has_side)
        n = self.spin_fitting_holes.value()
        offs = self.facade_canvas.get_fitting_offsets_mm() if has_side else []
        sup_ok = _is_valid_fitting_supplier_choice(self.cmb_fitting_hinge.currentText())
        can_save = self._fitting_switch_on and has_side and len(offs) == n and n >= 2 and sup_ok
        self.btn_fitting_save.setEnabled(can_save)
        self.btn_fitting_supply.setEnabled(self._fitting_switch_on and has_side)
        self.btn_fitting_cancel.setEnabled(self._fitting_switch_on)
        self.btn_fitting_clear_holes.setEnabled(self._fitting_switch_on and has_side)
        if getattr(self, "btn_master_hole_locks", None):
            self.btn_master_hole_locks.setEnabled(self._fitting_switch_on and has_side and n >= 2)

    def _cuts_count(self) -> int:
        """Количество вырезов/отверстий под петли (сумма по всем сторонам)."""
        total = 0
        for entry in (self._saved_fittings or []):
            if not isinstance(entry, dict):
                continue
            holes = entry.get("отверстия") or entry.get("holes") or []
            if isinstance(holes, list):
                total += len(holes)
        return int(total)

    def _sync_hinges_quantity_to_cuts(self) -> None:
        """Количество петель всегда = количеству отверстий (cuts)."""
        cuts = self._cuts_count()
        if cuts <= 0:
            self._hinges = []
        else:
            for it in (self._hinges or []):
                if isinstance(it, dict):
                    it["quantity"] = cuts

    def _update_add_hinges_enabled(self) -> None:
        cuts = self._cuts_count()
        # Добавлять петли можно только если отверстия уже сохранены (присадка выполнена).
        can = cuts > 0
        if hasattr(self, "btn_add_hinges"):
            self.btn_add_hinges.setEnabled(bool(can))

    def _on_fitting_toggled(self, on):
        if on and not self._side_profiles:
            QMessageBox.information(
                self,
                "Присадка",
                "Сначала добавьте профиль хотя бы на одну сторону фасада.",
            )
            self.fitting_switch_bar.setChecked(False, emit=False)
            return
        self._fitting_switch_on = bool(on)
        if not on:
            self._fitting_selected_side = None
            self.lbl_fitting_side.setVisible(False)
            self.lbl_fitting_side.clear()
            self.facade_canvas.set_fitting_mode(False, False)
            self._update_fitting_hints()
            self._update_fitting_controls_enabled()
            return
        self.facade_canvas.set_fitting_mode(True, True)
        self._fitting_selected_side = None
        self.lbl_fitting_side.setVisible(False)
        self.lbl_fitting_side.clear()
        self._update_fitting_hints()
        self._update_fitting_controls_enabled()

    def _on_fitting_side_picked(self, side):
        if not self._fitting_switch_on:
            return
        if not self._side_profiles.get(side):
            return
        self._fitting_selected_side = side
        self.lbl_fitting_side.setText("Сторона: %s" % FITTING_SIDE_LABELS_RU.get(side, side))
        self.lbl_fitting_side.setVisible(True)
        self.facade_canvas.set_fitting_mode(True, False)
        existing = None
        for e in self._saved_fittings or []:
            if not isinstance(e, dict):
                continue
            if (e.get("сторона") or e.get("side")) == side:
                existing = e
                break
        if existing:
            holes = existing.get("отверстия") or existing.get("holes") or []
            offs = []
            for h in holes:
                if isinstance(h, dict):
                    try:
                        offs.append(int(h.get("отступ_мм") or h.get("offset_mm") or 0))
                    except (TypeError, ValueError):
                        pass
            sup = existing.get("поставщик_петли") or existing.get("supplier")
            if len(offs) >= 2:
                self.spin_fitting_holes.blockSignals(True)
                self.spin_fitting_holes.setValue(min(8, max(2, len(offs))))
                self.spin_fitting_holes.blockSignals(False)
                self._fitting_snapshot_offsets = list(offs)
                self.facade_canvas.set_fitting_edit(side, offs)
                self._set_fitting_supplier_combo_from_saved(sup)
            else:
                n = self.spin_fitting_holes.value()
                init_offs = self._default_fitting_offsets_mm(side, n)
                self._fitting_snapshot_offsets = list(init_offs)
                self.facade_canvas.set_fitting_edit(side, init_offs)
                self.cmb_fitting_hinge.setCurrentIndex(0)
        else:
            n = self.spin_fitting_holes.value()
            init_offs = self._default_fitting_offsets_mm(side, n)
            self._fitting_snapshot_offsets = list(init_offs)
            self.facade_canvas.set_fitting_edit(side, init_offs)
            self.cmb_fitting_hinge.setCurrentIndex(0)
        self.facade_canvas.refresh_fitting_marker_supplier_hints(self._fitting_supplier_hint_for_markers())
        self._sync_master_lock_button()
        self._update_fitting_hints()
        self._update_fitting_controls_enabled()

    def _on_fitting_supply_clicked(self):
        if not self._fitting_switch_on:
            return
        self.cmb_fitting_hinge.setFocus()
        self.cmb_fitting_hinge.showPopup()

    def _on_fitting_cancel_edit(self):
        """Сбросить выбор стороны и маркеры; снова выбрать сторону (сохранённая в расчёте присадка не меняется)."""
        if not self._fitting_switch_on:
            return
        self._fitting_selected_side = None
        self._fitting_snapshot_offsets = []
        self.lbl_fitting_side.setVisible(False)
        self.lbl_fitting_side.clear()
        self.facade_canvas.end_fitting_edit_session()
        self.facade_canvas.set_fitting_mode(True, True)
        self._update_fitting_hints()
        self._update_fitting_controls_enabled()

    def _fitting_supplier_hint_for_markers(self) -> str:
        t = (self.cmb_fitting_hinge.currentText() or "").strip()
        return t if _is_valid_fitting_supplier_choice(t) else ""

    def _set_fitting_supplier_combo_from_saved(self, supplier: object) -> None:
        s = (str(supplier) if supplier is not None else "").strip()
        if _is_valid_fitting_supplier_choice(s):
            idx = self.cmb_fitting_hinge.findText(s)
            if idx >= 0:
                self.cmb_fitting_hinge.setCurrentIndex(idx)
                return
        self.cmb_fitting_hinge.setCurrentIndex(0)

    def _on_fitting_supplier_hint_changed(self, _text=None):
        if self._fitting_switch_on and self._fitting_selected_side:
            self.facade_canvas.refresh_fitting_marker_supplier_hints(self._fitting_supplier_hint_for_markers())

    def _on_fitting_hole_count_changed(self, _n=None):
        if not self._fitting_switch_on or not self._fitting_selected_side:
            return
        side = self._fitting_selected_side
        n = self.spin_fitting_holes.value()
        new_offs = self._default_fitting_offsets_mm(side, n)
        self._fitting_snapshot_offsets = list(new_offs)
        self.facade_canvas.set_fitting_edit(side, new_offs)
        self.facade_canvas.refresh_fitting_marker_supplier_hints(self._fitting_supplier_hint_for_markers())
        self._sync_master_lock_button()
        self._update_fitting_controls_enabled()

    def _on_save_fitting(self):
        if not self._fitting_switch_on or not self._fitting_selected_side:
            return
        n = self.spin_fitting_holes.value()
        self.facade_canvas.ensure_holes_sorted_physical()
        offs = self.facade_canvas.get_fitting_offsets_mm()
        if len(offs) != n:
            QMessageBox.warning(self, "Присадка", "Число маркеров не совпадает с количеством отверстий.")
            return
        supplier = (self.cmb_fitting_hinge.currentText() or "").strip()
        if not _is_valid_fitting_supplier_choice(supplier):
            QMessageBox.warning(
                self,
                "Присадка",
                "Укажите поставщика петли: выберите значение в поле «Петля (поставщик)» "
                "(тип выреза зависит от системы петли). Без этого нельзя сохранить отверстия.",
            )
            return
        holes_payload = [{"отступ_мм": int(o)} for o in offs]
        self._upsert_saved_fitting_for_side(self._fitting_selected_side, supplier, holes_payload)
        self.facade_canvas.end_fitting_edit_session()
        self._fitting_selected_side = None
        self.lbl_fitting_side.setVisible(False)
        self.lbl_fitting_side.clear()
        self.facade_canvas.set_fitting_mode(True, True)
        self._update_fitting_hints()
        self._update_fitting_controls_enabled()

    def _on_cutout_detail_clicked(self, info):
        self._cutout_pending_info = dict(info)
        side = info.get("side") or ""
        ru = FITTING_SIDE_LABELS_RU.get(side, side)
        self._cutout_action_labels.setText(
            "<b>Вырез под петлю</b> · %s · отступ <b>%s</b> мм · поставщик: %s"
            % (ru, info.get("offset_mm"), info.get("supplier") or "—")
        )
        self._cutout_action_bar.setVisible(True)

    def _hide_cutout_action_bar(self):
        self._cutout_action_bar.setVisible(False)
        self._cutout_pending_info = None

    def _on_cutout_bar_delete(self):
        info = self._cutout_pending_info
        if not info:
            return
        self._remove_cutout_at(
            int(info.get("entry_index", 0)),
            int(info.get("hole_index", info.get("index", 0))),
        )
        self._hide_cutout_action_bar()

    def _tab_state_to_payload(self, st: dict[str, Any]) -> dict[str, Any]:
        return {
            "Тип": "Фасад",
            "Ширина_мм": int(st.get("width_mm") or 0),
            "Высота_мм": int(st.get("height_mm") or 0),
            "Количество": int(st.get("quantity") or 1),
            "Профили_по_сторонам": self._json_safe(st.get("profiles") or {}),
            "Петли": self._json_safe(st.get("hinges") or []),
            "Присадка": self._json_safe(st.get("fittings") or []),
            "Стекло": self._json_safe(st.get("glass") or {}) if isinstance(st.get("glass"), dict) else None,
            "Уплотнитель": {"Цвет": str(st.get("seal_variant") or "прозрачный")},
            "Винты": {"Цвет": str(st.get("screw_variant") or "серебро")},
            "Замер": self._json_safe(st.get("zamer_block") or {"Активирован": False, "Данные": None}),
            "Доставка": self._json_safe(st.get("delivery_block") or {"Активирован": False, "Данные": None}),
            "_total_rub": int(st.get("_total_rub") or 0),
        }

    def _all_tab_states_snapshot(self) -> list[dict[str, Any]]:
        if not self._facade_tabs:
            self._init_facade_tabs_model()
        if 0 <= self._active_facade_tab_idx < len(self._facade_tabs):
            self._facade_tabs[self._active_facade_tab_idx] = self._capture_ui_to_tab_state()
        out: list[dict[str, Any]] = []
        for st in self._facade_tabs:
            ss = dict(st)
            # Пересчитываем total через временное применение состояния
            old = self._capture_ui_to_tab_state()
            self._apply_tab_state_to_ui(ss)
            ss["_total_rub"] = self._facade_total_rub()
            self._apply_tab_state_to_ui(old)
            out.append(ss)
        return out

    def _tab_title(self, i: int) -> str:
        return "Фасад %d" % (i + 1)

    def _on_open_maket_preview(self):
        tabs = self._all_tab_states_snapshot()
        d = QDialog(self)
        d.setWindowTitle("Maket")
        d.resize(900, 700)
        lay = QVBoxLayout(d)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        v = QVBoxLayout(host)
        v.setSpacing(12)
        for i, st in enumerate(tabs):
            box = QFrame()
            box.setFrameShape(QFrame.Box)
            bl = QVBoxLayout(box)
            bl.addWidget(QLabel(self._tab_title(i)))
            pl = self._tab_state_to_payload(st)
            info = QLabel(
                "Размер: {w}×{h} мм\nКоличество: {q} шт\nПрофилей: {p}\nПетель: {hg}\nСтекло: {g}\nИтого: {t} ₽".format(
                    w=pl.get("Ширина_мм") or 0,
                    h=pl.get("Высота_мм") or 0,
                    q=pl.get("Количество") or 1,
                    p=len((pl.get("Профили_по_сторонам") or {})),
                    hg=sum(int((x or {}).get("quantity") or 0) for x in (pl.get("Петли") or [])),
                    g=(pl.get("Стекло") or {}).get("Название") or "—",
                    t=pl.get("_total_rub") or 0,
                )
            )
            info.setWordWrap(True)
            bl.addWidget(info)
            v.addWidget(box)
        v.addStretch()
        scroll.setWidget(host)
        lay.addWidget(scroll)
        btn = QDialogButtonBox(QDialogButtonBox.Close)
        btn.rejected.connect(d.reject)
        lay.addWidget(btn)
        d.exec_()

    def _export_facade_pdf(self, *, worker: bool = False) -> None:
        tabs = self._all_tab_states_snapshot()
        if not tabs:
            QMessageBox.information(self, "PDF", "Нет фасадов для экспорта.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить PDF",
            "Фасады_%s.pdf" % ("работники" if worker else "клиент"),
            "PDF Files (*.pdf)",
        )
        if not path:
            return
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.units import mm
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.pdfgen import canvas as rl_canvas
            from reportlab.lib.colors import Color, HexColor
            def _register_cyr_font() -> tuple[str, str]:
                name = "FacadeOrderFont"
                bold = "FacadeOrderFontBold"
                try:
                    if sys.platform == "win32":
                        fonts_dir = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
                        font_path = os.path.join(fonts_dir, "arial.ttf")
                        bold_path = os.path.join(fonts_dir, "arialbd.ttf")
                    else:
                        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
                        bold_path = font_path
                    if os.path.isfile(font_path) and os.path.isfile(bold_path):
                        pdfmetrics.registerFont(TTFont(name, font_path))
                        pdfmetrics.registerFont(TTFont(bold, bold_path))
                        return name, bold
                except Exception:
                    pass
                return "Helvetica", "Helvetica-Bold"
            font_name, font_bold = _register_cyr_font()
            def _ru_side(side):
                m = {"left": "Левая", "right": "Правая", "top": "Верхняя", "bottom": "Нижняя"}
                return m.get(str(side or "").lower(), str(side or "—"))
            def _glass_processing_lines(glass):
                sel = (glass or {}).get("blocks_selected") if isinstance(glass, dict) else {}
                if not isinstance(sel, dict):
                    return ["Обработка: нет данных"]
                lines = []
                pol = sel.get("Полировка") or {}
                if pol.get("Нужна полировка"):
                    sides = [k for k in ("Верх", "Низ", "Лево", "Право", "Кромка") if pol.get(k)]
                    lines.append("Полировка: %s" % (", ".join(sides) if sides else "да"))
                gr = sel.get("Шлифовка") or {}
                if gr.get("Нужна шлифовка"):
                    sides = [k for k in ("Верх", "Низ", "Лево", "Право", "Кромка") if gr.get(k)]
                    lines.append("Шлифовка: %s" % (", ".join(sides) if sides else "да"))
                fc = sel.get("Фацет") or {}
                if fc.get("Нужен"):
                    vals = []
                    for key, lab in (("Top", "верх"), ("Bottom", "низ"), ("Left", "лево"), ("Right", "право")):
                        try:
                            v = int(fc.get(key) or 0)
                        except (TypeError, ValueError):
                            v = 0
                        if v > 0:
                            vals.append("%s %s мм" % (lab, v))
                    lines.append("Фацет: %s" % (", ".join(vals) if vals else "да"))
                sb = sel.get("Пескоструй") or {}
                if sb.get("Пескоструй"):
                    lines.append("Пескоструй: %s%s" % ((sb.get("Тип") or "да"), " (двухсторонний)" if sb.get("Двухсторонний") else ""))
                fl = sel.get("Плёнка") or {}
                if fl.get("Использовать плёнку"):
                    lines.append("Пленка: %s" % (fl.get("Тип плёнки") or "да"))
                pk = sel.get("Покраска") or {}
                if pk.get("Использовать покраску"):
                    lines.append("Покраска: %s" % (pk.get("Цвет покраски") or "да"))
                ph = sel.get("Фотопечать") or {}
                if ph.get("Нужна"):
                    lines.append("Фотопечать: да")
                if not lines:
                    lines.append("Обработка: отсутствует")
                return lines
            def _draw_worker_scheme(c, pl, y_top):
                fw = float(pl.get("Ширина_мм") or 1.0)
                fh = float(pl.get("Высота_мм") or 1.0)
                block_w = 130 * mm
                block_h = 85 * mm
                x_block = (A4[0] - block_w) / 2.0
                y_block = y_top - block_h
                c.setStrokeColor(Color(0.85, 0.85, 0.85))
                c.rect(x_block, y_block, block_w, block_h, stroke=1, fill=0)
                c.setStrokeColor(Color(0, 0, 0))
                c.setFont(font_bold, 10)
                c.drawCentredString(x_block + block_w / 2.0, y_block + block_h - 5 * mm, "Схема фасада (вид спереди)")
                max_w = block_w - 28 * mm
                max_h = block_h - 26 * mm
                scale = min(max_w / max(1.0, fw), max_h / max(1.0, fh))
                rw = max(30 * mm, fw * scale)
                rh = max(20 * mm, fh * scale)
                x0 = x_block + (block_w - rw) / 2.0
                y0 = y_block + (block_h - rh) / 2.0 - 2 * mm
                c.setLineWidth(1.3)
                c.rect(x0, y0, rw, rh, stroke=1, fill=0)
                c.setFont(font_name, 8)
                c.drawCentredString(x0 + rw / 2.0, y0 + rh + 2.2 * mm, "Верх")
                c.drawCentredString(x0 + rw / 2.0, y0 - 3.2 * mm, "Низ")
                c.drawString(x0 - 10 * mm, y0 + rh / 2.0, "Лево")
                c.drawString(x0 + rw + 2 * mm, y0 + rh / 2.0, "Право")
                c.setFont(font_name, 7)
                c.drawCentredString(x0 + rw / 2.0, y0 + rh + 5.5 * mm, "Ш: %d мм" % int(fw))
                c.drawString(x0 + rw + 2 * mm, y0 + rh / 2.0 + 4 * mm, "В: %d мм" % int(fh))
                by_side = defaultdict(list)
                for rec in (pl.get("Присадка") or []):
                    side = rec.get("сторона") or rec.get("side") or ""
                    by_side[side].extend((rec.get("отверстия") or rec.get("holes") or []))
                prof_map_pdf = pl.get("Профили_по_сторонам") or {}
                for side_raw, arr in by_side.items():
                    sk = _norm_side_key(side_raw)
                    if not sk:
                        continue
                    prof_pdf = prof_map_pdf.get(sk) if isinstance(prof_map_pdf, dict) else None
                    if isinstance(prof_pdf, dict):
                        qc = _profile_frame_color(prof_pdf.get("color"))
                        hx = "#%02x%02x%02x" % (qc.red(), qc.green(), qc.blue())
                    else:
                        hx = "#64748b"
                    for hole in arr:
                        off = float(hole.get("отступ_мм") or hole.get("offset_mm") or 0.0)
                        ht = _hole_cutout_type_label(hole)
                        c.setFillColor(HexColor(hx))
                        c.setStrokeColor(Color(1, 1, 1))
                        c.setLineWidth(0.6)
                        if sk == "left":
                            px = x0
                            py = y0 + rh - (off / max(1.0, fh)) * rh
                            c.circle(px, py, 1.2 * mm, stroke=1, fill=1)
                            c.setStrokeColor(Color(0, 0, 0))
                            c.setFillColor(Color(0, 0, 0))
                            c.setFont(font_name, 7)
                            c.drawString(px + 3.2 * mm, py + 1.0 * mm, "%d мм" % int(off))
                            c.setFont(font_name, 6)
                            c.drawString(px + 3.2 * mm, py - 2.0 * mm, ht)
                        elif sk == "right":
                            px = x0 + rw
                            py = y0 + rh - (off / max(1.0, fh)) * rh
                            c.circle(px, py, 1.2 * mm, stroke=1, fill=1)
                            c.setStrokeColor(Color(0, 0, 0))
                            c.setFillColor(Color(0, 0, 0))
                            c.setFont(font_name, 7)
                            c.drawRightString(px - 3.2 * mm, py + 1.0 * mm, "%d мм" % int(off))
                            c.setFont(font_name, 6)
                            c.drawRightString(px - 3.2 * mm, py - 2.0 * mm, ht)
                        elif sk == "top":
                            px = x0 + (off / max(1.0, fw)) * rw
                            py = y0 + rh
                            c.circle(px, py, 1.2 * mm, stroke=1, fill=1)
                            c.setStrokeColor(Color(0, 0, 0))
                            c.setFillColor(Color(0, 0, 0))
                            c.setFont(font_name, 7)
                            c.drawCentredString(px, py - 3.5 * mm, "%d мм" % int(off))
                            c.setFont(font_name, 6)
                            c.drawCentredString(px, py - 6.2 * mm, ht)
                        elif sk == "bottom":
                            px = x0 + (off / max(1.0, fw)) * rw
                            py = y0
                            c.circle(px, py, 1.2 * mm, stroke=1, fill=1)
                            c.setStrokeColor(Color(0, 0, 0))
                            c.setFillColor(Color(0, 0, 0))
                            c.setFont(font_name, 7)
                            c.drawCentredString(px, py + 3.5 * mm, "%d мм" % int(off))
                            c.setFont(font_name, 6)
                            c.drawCentredString(px, py + 6.2 * mm, ht)
                        c.setLineWidth(1.3)
                return y_block - 3 * mm
            c = rl_canvas.Canvas(path, pagesize=A4)
            _W, H = A4
            y = H - 16 * mm
            for i, st in enumerate(tabs):
                if i > 0:
                    c.showPage()
                    y = H - 16 * mm
                if y < 45 * mm:
                    c.showPage()
                    y = H - 16 * mm
                pl = self._tab_state_to_payload(st)
                c.setFont(font_bold, 12)
                c.drawString(15 * mm, y, self._tab_title(i))
                y -= 6 * mm
                c.setFont(font_name, 10)
                c.drawString(15 * mm, y, "Размер фасада: %s x %s мм" % (pl.get("Ширина_мм"), pl.get("Высота_мм")))
                y -= 5 * mm
                glass = pl.get("Стекло") or {}
                mat_name = (glass.get("Название") or "—")
                mat_type = "Зеркало" if "зерк" in str(mat_name).lower() else "Стекло"
                c.drawString(15 * mm, y, "Материал наполнения: %s" % mat_type)
                y -= 5 * mm
                c.drawString(15 * mm, y, "Наименование: %s" % (mat_name or "—"))
                y -= 5 * mm
                c.drawString(
                    15 * mm,
                    y,
                    "Параметры: %s x %s мм, толщина %s мм, цвет: %s"
                    % (
                        glass.get("Ширина (мм)") or pl.get("Ширина_мм") or "—",
                        glass.get("Высота (мм)") or pl.get("Высота_мм") or "—",
                        glass.get("Толщина (мм)") or "—",
                        glass.get("Цвет") or "—",
                    ),
                )
                y -= 5 * mm
                for ln in _glass_processing_lines(glass):
                    c.drawString(18 * mm, y, "• %s" % ln)
                    y -= 4 * mm
                if not worker:
                    c.setFont(font_bold, 10)
                    c.drawString(15 * mm, y, "Итого по фасаду: %s ₽" % (pl.get("_total_rub") or 0))
                    c.setFont(font_name, 10)
                    y -= 6 * mm
                if worker:
                    c.drawString(15 * mm, y, "Профили и источник:")
                    y -= 5 * mm
                    can_take_material = self._can_take_material_now()
                    for side, pf in (pl.get("Профили_по_сторонам") or {}).items():
                        src = (pf or {}).get("_source_stock") or {}
                        lbl = src.get("label_number")
                        src_len = src.get("length_mm")
                        if can_take_material and (not lbl) and src.get("stock_id"):
                            try:
                                lbl = db_models.ensure_profile_label_number(src.get("stock_id"))
                            except Exception:
                                lbl = None
                        size_tail = (" (%s мм)" % int(src_len)) if src_len else ""
                        if src.get("kind") == "new":
                            src_text = "новый профиль (списание со склада — после подтверждения реза в сервисе)"
                        elif src.get("kind") == "warehouse_remnant":
                            src_text = "остаток со склада № %s%s" % ((lbl or src.get("stock_id") or "—"), size_tail)
                        else:
                            src_text = "со склада № %s%s" % ((lbl or src.get("stock_id") or "—"), size_tail)
                        c.drawString(20 * mm, y, "%s: %s [%s]" % (_ru_side(side), (pf or {}).get("name") or "—", src_text))
                        y -= 4 * mm
                    c.drawString(15 * mm, y, "Отверстия / присадка:")
                    y -= 4 * mm
                    for rec in (pl.get("Присадка") or []):
                        side = _ru_side(rec.get("сторона") or rec.get("side") or "—")
                        for h in (rec.get("отверстия") or rec.get("holes") or []):
                            c.drawString(20 * mm, y, "%s: %s мм от края" % (side, h.get("отступ_мм") or h.get("offset_mm") or "—"))
                            y -= 4 * mm
                    has_cutouts = any((rec.get("отверстия") or rec.get("holes") or []) for rec in (pl.get("Присадка") or []))
                    if has_cutouts:
                        y = _draw_worker_scheme(c, pl, y)
                    try:
                        from logic.production_instructions import facade_worker_pdf_extra_lines

                        def _emit_instruction_line(txt: str):
                            nonlocal y
                            s = str(txt).replace("\r", "").strip()
                            if not s:
                                return
                            c.setFont(font_name, 8.5)
                            max_w = 92
                            rest = s
                            while rest:
                                chunk = rest[:max_w]
                                if len(rest) > max_w:
                                    sp = chunk.rfind(" ")
                                    if sp > 50:
                                        chunk = rest[:sp]
                                        rest = rest[sp:].lstrip()
                                    else:
                                        rest = rest[max_w:]
                                else:
                                    rest = ""
                                if y < 42 * mm:
                                    c.showPage()
                                    y = H - 16 * mm
                                c.drawString(14 * mm, y, chunk)
                                y -= 3.6 * mm

                        c.setFont(font_bold, 10)
                        if y < 52 * mm:
                            c.showPage()
                            y = H - 16 * mm
                        c.drawString(15 * mm, y, "Порядок работ, склад и этикетки (подробно):")
                        y -= 5 * mm
                        for instr in facade_worker_pdf_extra_lines(pl):
                            _emit_instruction_line(instr)
                    except Exception:
                        pass
                y -= 4 * mm
            c.save()
        except Exception as e:
            QMessageBox.critical(self, "PDF", str(e))

    def _on_export_facade_pdf_client(self):
        self._export_facade_pdf(worker=False)

    def _on_export_facade_pdf_worker(self):
        self._export_facade_pdf(worker=True)

    def _on_export_profile_labels(self):
        if not self._can_take_material_now():
            QMessageBox.information(
                self,
                "Этикетки",
                "Печать этикеток доступна с «Оплачен» и для последующих статусов заказа («В работе» и далее).",
            )
            return
        tabs = self._all_tab_states_snapshot()
        labels = []
        for st in tabs:
            for _side, pf in (st.get("profiles") or {}).items():
                if not isinstance(pf, dict):
                    continue
                src = pf.get("_source_stock") or {}
                lbl = src.get("label_number")
                uniq = None
                if not lbl and src.get("stock_id"):
                    try:
                        lbl = db_models.ensure_profile_label_number(src.get("stock_id"))
                    except Exception:
                        lbl = None
                if src.get("stock_id"):
                    try:
                        row_lbl = db_models.get_profile_label_by_stock_id(src.get("stock_id"))
                        if row_lbl:
                            uniq = row_lbl.get("unique_number")
                            if not lbl:
                                lbl = row_lbl.get("label_number")
                    except Exception:
                        pass
                labels.append(
                    {
                        "name": pf.get("name") or pf.get("series") or "Профиль",
                        "color": pf.get("color") or "",
                        "source": src.get("kind") or "new",
                        "stock_id": src.get("stock_id"),
                        "label_number": lbl,
                        "unique_number": uniq,
                    }
                )
        if not labels:
            QMessageBox.information(self, "Этикетки", "Нет профилей для печати этикеток.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить этикетки профилей", "Этикетки_профилей.pdf", "PDF Files (*.pdf)")
        if not path:
            return
        try:
            from logic.production_instructions import write_profile_labels_pdf

            write_profile_labels_pdf(labels, path)
        except Exception as e:
            QMessageBox.critical(self, "Этикетки", str(e))

    def _on_show_profile_remnant_history(self):
        st = self._capture_ui_to_tab_state()
        rows = []
        for _side, pf in (st.get("profiles") or {}).items():
            if not isinstance(pf, dict):
                continue
            src = pf.get("_source_stock") or {}
            sid = src.get("stock_id")
            if not sid:
                continue
            try:
                hist = db_models.get_profile_remnant_history(sid) or []
            except Exception:
                hist = []
            for h in hist:
                rows.append(
                    "stock_id={sid} order={oid} client={cn} status={st} action={ac}".format(
                        sid=sid,
                        oid=h.get("order_id") or "—",
                        cn=h.get("client_name") or "—",
                        st=h.get("status") or "—",
                        ac=h.get("action_type") or "—",
                    )
                )
        if not rows:
            QMessageBox.information(self, "История остатков", "Для текущего фасада история остатков профилей не найдена.")
            return
        d = QDialog(self)
        d.setWindowTitle("История остатков профиля")
        d.resize(760, 420)
        lay = QVBoxLayout(d)
        area = QScrollArea()
        area.setWidgetResizable(True)
        host = QWidget()
        v = QVBoxLayout(host)
        for r in rows:
            l = QLabel(r)
            l.setWordWrap(True)
            v.addWidget(l)
        v.addStretch()
        area.setWidget(host)
        lay.addWidget(area)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        d.exec_()

    def _can_take_material_now(self) -> bool:
        """Материалы/этикетки: с «Оплачен» и дальше по цепочке (в т.ч. «В работе»)."""
        from db_main import order_status_allows_production_print

        oid = self._linked_order_id
        if not oid:
            return False
        try:
            row = db_models.get_order(int(oid)) or {}
        except Exception:
            return False
        return order_status_allows_production_print(row.get("status"))

    def _on_save_to_order(self):
        try:
            if not self._can_save_facade_order():
                QMessageBox.warning(self, "Сохранение", "Для сохранения фасада нужны и профиль, и выбранное стекло.")
                return
            tabs_pre = self._all_tab_states_snapshot()
            for ti, st in enumerate(tabs_pre, start=1):
                ok_p, msg_p = _prisadka_validate_suppliers(st.get("fittings") or [], facade_tab_no=ti)
                if not ok_p:
                    QMessageBox.warning(self, "Присадка", msg_p)
                    return
            cn = (self.client_edit.text() or "").strip()
            cid = self._client_id
            qcid = getattr(self, "_quick_client_id", None)
            if (not cid) and (not qcid) and cn:
                try:
                    cid = db_models.get_client_id_by_name(cn)
                except Exception:
                    cid = None
                if not cid:
                    try:
                        qcid = db_models.get_mirror_quick_client_id_by_name(cn)
                    except Exception:
                        qcid = None
            if not cid and not qcid:
                QMessageBox.warning(
                    self,
                    "Сохранение",
                    "Клиент обязателен: выберите клиента из справочника, из быстрого просчёта или создайте клиента.",
                )
                return
            if cid is not None:
                self._client_id = int(cid)
            if qcid is not None:
                self._quick_client_id = int(qcid)
            if self._quick_client_id and not cn:
                rqc = db_models.get_mirror_quick_client_by_id(int(self._quick_client_id)) or {}
                cn = (rqc.get("name") or "").strip()
                if cn:
                    self.client_edit.setText(cn)

            oid = self._linked_order_id
            if not oid:
                cb = db_models.mirror_order_created_by_from_qt_parent(self.parent())
                oid = db_models.create_order(
                    cn,
                    client_id=self._client_id,
                    quick_client_id=self._quick_client_id,
                    notes="MAIN_PROJECT: фасады",
                    order_kind=db_models.ORDER_KIND_FACADE,
                    created_by_user_id=cb[0],
                    created_by_login=cb[1] or None,
                    created_by_role=cb[2] or None,
                )
                self._linked_order_id = oid
                self._btn_save_order.setText("Сохранить в заказ № %s" % oid)

            row = db_models.get_order(int(oid))
            raw = row.get("blocks_calc_json") if row else None
            _ver, products = parse_bundle(raw)
            tabs = tabs_pre
            # Сохраняем все текущие фасадные вкладки, не фасадные продукты не трогаем.
            keep = [p for p in products if str(p.get("kind") or "") != "facade"]
            old_facades_by_id = {
                str(p.get("id") or ""): dict(p)
                for p in products
                if str(p.get("kind") or "") == "facade" and str(p.get("id") or "")
            }
            facade_products = []
            for st in tabs:
                pid = str(st.get("product_id") or uuid.uuid4())
                payload = self._tab_state_to_payload(st)
                prev = old_facades_by_id.get(pid) or {}
                next_row = {"id": pid, "kind": "facade", "payload": payload}
                # Сохраняем payment/status-поля текущей позиции при повторном редактировании фасада.
                for k in ("status", "payment_type", "surcharge_amount", "surcharge_paid", "surcharge_payment_type"):
                    if k in prev:
                        next_row[k] = prev.get(k)
                if "payment_type" not in next_row:
                    next_row["payment_type"] = "unpaid"
                facade_products.append(next_row)
            merged = bundle_to_json(2, keep + facade_products)
            db_models.update_order_blocks_calc(int(oid), merged)
            _, prods = parse_bundle(merged)
            db_models.update_order_kind(int(oid), infer_order_kind_for_db(prods))
            # История использования остатков профилей в заказе.
            for st in tabs:
                for _side, pf in (st.get("profiles") or {}).items():
                    if not isinstance(pf, dict):
                        continue
                    src = pf.get("_source_stock") or {}
                    sid = src.get("stock_id")
                    if sid:
                        try:
                            db_models.add_profile_remnant_history(
                                sid,
                                int(oid),
                                "used_in_facade_order",
                                {
                                    "profile_name": pf.get("name"),
                                    "series": pf.get("series"),
                                    "color": pf.get("color"),
                                    "facade_size": [st.get("width_mm"), st.get("height_mm")],
                                },
                            )
                        except Exception:
                            pass
                    nr = src.get("new_remnant_stock_id")
                    if nr:
                        try:
                            db_models.add_profile_remnant_history(
                                int(nr),
                                int(oid),
                                "remnant_from_facade_cut",
                                {
                                    "profile_name": pf.get("name"),
                                    "consumed_stock_id": sid,
                                    "rest_mm": src.get("rest_mm"),
                                    "facade_size": [st.get("width_mm"), st.get("height_mm")],
                                },
                            )
                        except Exception:
                            pass
        except Exception as e:
            QMessageBox.critical(self, "Сохранение", str(e))
            return
        self.accept()

    def _apply_quick_client_preset(self):
        p = self._quick_client_preset
        if not p:
            return
        try:
            cid = p.get("client_id")
            cid = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid = None
        qcid = p.get("quick_client_id")
        try:
            qcid = int(qcid) if qcid is not None else None
        except (TypeError, ValueError):
            qcid = None
        name = (p.get("client_name") or "").strip()
        if not name and not cid and not qcid:
            return
        self._client_id = cid
        self._quick_client_id = qcid
        if name:
            self.client_edit.setText(name)
        elif qcid:
            rqc = db_models.get_mirror_quick_client_by_id(int(qcid)) or {}
            self.client_edit.setText((rqc.get("name") or "").strip())
        self.client_edit.setReadOnly(True)
        self.btn_create_client.setVisible(False)
        self.client_list.setVisible(False)

    def _on_client_text_changed(self, text):
        if self._quick_client_preset:
            return
        prefix = (text or "").strip()
        self.client_list.setVisible(False)
        self._client_id = None
        self._quick_client_id = None
        if not prefix:
            self._update_materials_summary_panel()
            return
        try:
            if self._quick_estimate_mode:
                rows = db_models.list_quick_estimate_client_suggestions(prefix, limit=40) or []
                try:
                    qid = db_models.get_mirror_quick_client_id_by_name(prefix)
                    self._quick_client_id = int(qid) if qid is not None else None
                except Exception:
                    self._quick_client_id = None
            else:
                rows = [
                    {"label": str(r.get("name") or "").strip(), "name": str(r.get("name") or "").strip(), "client_id": r.get("id"), "quick_client_id": None}
                    for r in (db_models.get_clients_by_prefix(prefix) or [])
                    if r.get("name")
                ]
                try:
                    cid_exact = db_models.get_client_id_by_name(prefix)
                    self._client_id = int(cid_exact) if cid_exact is not None else None
                except Exception:
                    self._client_id = None
        except Exception:
            rows = []
        self.client_list.clear()
        for s in rows[:15]:
            lab = (s.get("label") or s.get("name") or "").strip()
            if not lab:
                continue
            it = QListWidgetItem(lab)
            cid = s.get("client_id")
            try:
                cid = int(cid) if cid is not None else None
            except (TypeError, ValueError):
                cid = None
            qcid = s.get("quick_client_id")
            try:
                qcid = int(qcid) if qcid is not None else None
            except (TypeError, ValueError):
                qcid = None
            it.setData(Qt.UserRole, cid)
            it.setData(Qt.UserRole + 1, qcid)
            it.setData(Qt.UserRole + 2, (s.get("name") or "").strip())
            self.client_list.addItem(it)
        self.client_list.setVisible(len(rows) > 0)
        self._update_materials_summary_panel()

    def _on_client_selected(self, item):
        if item:
            cid = item.data(Qt.UserRole)
            qcid = item.data(Qt.UserRole + 1)
            canon = (item.data(Qt.UserRole + 2) or "").strip()
            name = canon or (item.text() or "").strip()
            if " · быстрый" in name and not canon:
                name = name.replace(" · быстрый", "").strip()
            self.client_edit.setText(name)
            self.client_list.setVisible(False)
            try:
                self._client_id = int(cid) if cid is not None else None
            except (TypeError, ValueError):
                self._client_id = None
            try:
                self._quick_client_id = int(qcid) if qcid is not None else None
            except (TypeError, ValueError):
                self._quick_client_id = None
            self._update_materials_summary_panel()

    def _on_create_client(self):
        if self._quick_client_preset:
            QMessageBox.information(
                self,
                "Клиент",
                "В быстром просчёте клиент уже задан в начале (быстрый формат).",
            )
            return
        if self._quick_estimate_mode:
            from ui.quick_client_create_dialog import open_quick_client_create_dialog

            meta = open_quick_client_create_dialog(
                self,
                initial_name=(self.client_edit.text() or "").strip(),
                save_to_quick_table_only=True,
            )
            if meta:
                self._client_id = None
                qid = meta.get("quick_client_id")
                try:
                    self._quick_client_id = int(qid) if qid is not None else None
                except (TypeError, ValueError):
                    self._quick_client_id = None
                nm = (meta.get("client_name") or "").strip()
                if nm:
                    self.client_edit.setText(nm)
                self.client_list.setVisible(False)
            return
        from ui._mirror_dialogs import _load_dialog
        saved_ui = sys.modules.pop('ui', None)
        try:
            NewClientDialog = _load_dialog('new_client_dialog', 'NewClientDialog')
            if NewClientDialog is None:
                QMessageBox.warning(self, "Клиент", "Не удалось загрузить окно создания клиента.")
                return
            initial = (self.client_edit.text() or "").strip()
            d = NewClientDialog(self, initial_name=initial)
            if d.exec_() != QDialog.Accepted:
                return
            name = d.get_saved_name() if hasattr(d, 'get_saved_name') else None
            cid = d.get_saved_client_id() if hasattr(d, 'get_saved_client_id') else None
            if name:
                self.client_edit.setText(name)
                self.client_list.setVisible(False)
                try:
                    self._client_id = int(cid) if cid is not None else db_models.get_client_id_by_name(name)
                except (TypeError, ValueError):
                    self._client_id = db_models.get_client_id_by_name(name) if name else None
                except Exception:
                    self._client_id = None
                self._quick_client_id = None
                try:
                    self._update_materials_summary_panel()
                except Exception:
                    pass
        finally:
            if saved_ui is not None:
                sys.modules['ui'] = saved_ui

    def _on_dimensions_changed(self):
        if self._tab_sync_guard:
            return
        self.facade_canvas.set_dimensions(self.spin_width.value(), self.spin_height.value())
        if self._fitting_switch_on and self._fitting_selected_side:
            self._on_fitting_hole_count_changed()
        self._update_materials_summary_panel()

    def _on_quantity_changed(self, _v: int) -> None:
        if self._tab_sync_guard:
            return
        qty = max(1, int(self.spin_quantity.value()))
        if isinstance(self._glass_selection, dict):
            bs = self._glass_selection.get("blocks_selected")
            if isinstance(bs, dict):
                iz = bs.get("Изделие")
                if isinstance(iz, dict):
                    iz["Количество (шт)"] = qty
                    bs["Изделие"] = iz
                self._glass_selection["blocks_selected"] = bs
        self._update_materials_summary_panel()

    def _on_seal_variant_changed(self, _checked: bool = True) -> None:
        # Сигнал `toggled` приходит и от прозрачного, и от черного radio-button'а.
        if getattr(self, "rb_seal_black", None) and self.rb_seal_black.isChecked():
            self._seal_variant = "черный"
        else:
            self._seal_variant = "прозрачный"
        self.facade_canvas.set_seal_variant(self._seal_variant)
        self._update_materials_summary_panel()

    def _on_screw_variant_changed(self, _checked: bool = True) -> None:
        if getattr(self, "rb_screw_gold", None) and self.rb_screw_gold.isChecked():
            self._screw_variant = "золото"
        else:
            self._screw_variant = "серебро"
        self._update_materials_summary_panel()

    def _on_add_profile(self):
        from ui.facade_profile_dialog import FacadeProfileSelectDialog
        d = FacadeProfileSelectDialog(self)
        if d.exec_() != QDialog.Accepted:
            return
        profile = d.selected_profile()
        if not profile:
            return
        from ui.facade_sides_dialog import FacadeSidesDialog
        d2 = FacadeSidesDialog(profile, self.spin_width.value(), self.spin_height.value(), self)
        if d2.exec_() != QDialog.Accepted:
            return
        sides = d2.get_sides()
        can_take_material = self._can_take_material_now()
        for side in sides:
            prof_for_side = dict(profile)
            # Правила реза профиля: 45°, допуск 30 мм, толщина диска 5 мм.
            side_len = self.spin_width.value() if side in ('top', 'bottom') else self.spin_height.value()
            required_mm = int(side_len) + 30 + 5
            peek_row = None
            if can_take_material:
                try:
                    peek_row = db_models.peek_profile_stock_for_facade_cut(int(profile.get("id")), required_mm)
                except Exception:
                    peek_row = None
            if can_take_material and peek_row is None:
                QMessageBox.warning(
                    self,
                    "Склад профилей",
                    "Для стороны %s не найден подходящий профиль на складе "
                    "(нужная длина с допусками: %d мм).\n\n"
                    "Профиль можно добавить в расчёт — фактическое списание со склада выполнится "
                    "в производстве после подтверждения реза в веб-сервисе."
                    % (side, required_mm)
                )
            prof_for_side["_source_stock"] = {
                "kind": "new",
                "required_mm": required_mm,
                "stock_moves_on_service_confirm": True,
            }
            self._side_profiles[side] = prof_for_side
        self.facade_canvas.set_side_profiles(self._side_profiles)
        self._sync_fitting_switch_availability()
        self._update_materials_summary_panel()

    def _glass_inset_mm(self):
        vals = []
        for p in (self._side_profiles or {}).values():
            code = _profile_code_from_profile(p)
            if code in PROFILE_GLASS_INSET_MM:
                vals.append(int(PROFILE_GLASS_INSET_MM[code]))
        if not vals:
            return None
        # Если профили разные — берём максимальный зазор как безопасный.
        return max(vals)

    def _restore_glass_blocks_state(self, app, saved_sel):
        if not isinstance(saved_sel, dict):
            app._set_all_secondary_visible(False)
            app._apply_compact_window_size()
            return
        pol = saved_sel.get("Полировка") or {}
        pshape = (saved_sel.get("Параметры изделия") or {}).get("Форма") or "Прямоугольник"
        if pol.get("Нужна полировка"):
            if pshape in ("Круг", "Овал", "Сложная фигура"):
                app.polirovka.checkboxes[1].setChecked(bool(pol.get("Кромка")))
            else:
                app.polirovka.checkboxes[1].setChecked(bool(pol.get("Верх")))
                app.polirovka.checkboxes[2].setChecked(bool(pol.get("Лево")))
                app.polirovka.checkboxes[3].setChecked(bool(pol.get("Право")))
                app.polirovka.checkboxes[4].setChecked(bool(pol.get("Низ")))
        gr = saved_sel.get("Шлифовка") or {}
        if gr.get("Нужна шлифовка"):
            if pshape in ("Круг", "Овал", "Сложная фигура"):
                app.shlifovka.checkboxes[1].setChecked(bool(gr.get("Кромка")))
            else:
                app.shlifovka.checkboxes[1].setChecked(bool(gr.get("Верх")))
                app.shlifovka.checkboxes[2].setChecked(bool(gr.get("Лево")))
                app.shlifovka.checkboxes[3].setChecked(bool(gr.get("Право")))
                app.shlifovka.checkboxes[4].setChecked(bool(gr.get("Низ")))
        fc = saved_sel.get("Фацет") or {}
        if fc.get("Нужен"):
            for combo, key in (
                (app.facet.top, "Top"),
                (app.facet.bot, "Bottom"),
                (app.facet.left, "Left"),
                (app.facet.right, "Right"),
            ):
                try:
                    v = int(fc.get(key) or 0)
                except (TypeError, ValueError):
                    v = 0
                combo.setCurrentText(str(v) if v > 0 else "--")
        fl = saved_sel.get("Плёнка") or {}
        if fl.get("Использовать плёнку"):
            app.plenka.chk.setChecked(True)
            name = (fl.get("Тип плёнки") or "").strip()
            if name:
                for i in range(app.plenka.combo.count()):
                    d0 = app.plenka.combo.itemData(i)
                    if isinstance(d0, tuple) and d0 and str(d0[0]).strip() == name:
                        app.plenka.combo.setCurrentIndex(i)
                        break
        pk = saved_sel.get("Покраска") or {}
        if pk.get("Использовать покраску"):
            app.pokraska.chk.setChecked(True)
            cname = (pk.get("Цвет покраски") or "").strip()
            if cname:
                for i in range(app.pokraska.combo.count()):
                    d0 = app.pokraska.combo.itemData(i)
                    if isinstance(d0, tuple) and d0 and str(d0[0]).strip() == cname:
                        app.pokraska.combo.setCurrentIndex(i)
                        break
        sb = saved_sel.get("Пескоструй") or {}
        if sb.get("Пескоструй"):
            app.peskostroy.chk.setChecked(True)
            typ = (sb.get("Тип") or "").strip()
            for rb in app.peskostroy._radios:
                if rb.text() == typ:
                    rb.setChecked(True)
                    break
            app.peskostroy.chk_double.setChecked(bool(sb.get("Двухсторонний")))
            fpath = (sb.get("Файл") or "").strip()
            if fpath and os.path.isfile(fpath):
                try:
                    app.peskostroy.load_image_path(fpath)
                except Exception:
                    pass
        ph = saved_sel.get("Фотопечать") or {}
        if ph.get("Нужна"):
            app.photo.chk.setChecked(True)
            p0 = (ph.get("Локальный файл") or ph.get("Файл") or "").strip()
            if p0 and os.path.isfile(p0):
                try:
                    app.photo.load_image_path(p0)
                except Exception:
                    pass
        used_extra = any(
            [
                bool((saved_sel.get("Полировка") or {}).get("Нужна полировка")),
                bool((saved_sel.get("Шлифовка") or {}).get("Нужна шлифовка")),
                bool((saved_sel.get("Фацет") or {}).get("Нужен")),
                bool((saved_sel.get("Пескоструй") or {}).get("Пескоструй")),
                bool((saved_sel.get("Плёнка") or {}).get("Использовать плёнку")),
                bool((saved_sel.get("Покраска") or {}).get("Использовать покраску")),
                bool((saved_sel.get("Фотопечать") or {}).get("Нужна")),
            ]
        )
        app._set_all_secondary_visible(bool(used_extra))
        app._apply_compact_window_size()

    def _open_glass_pick_dialog(self):
        _blocks_dir = os.path.normpath(os.path.join(_mp, "BLOCKS"))
        if _blocks_dir not in sys.path:
            sys.path.insert(0, _blocks_dir)
        import xx as blocks_xx

        inset = self._glass_inset_mm()
        if inset is None:
            QMessageBox.information(
                self,
                "Стекло",
                "Для выбранного профиля не настроен зазор стекла.",
            )
            return
        w = int(self.spin_width.value()) - int(inset)
        h = int(self.spin_height.value()) - int(inset)
        qty = int(self.spin_quantity.value()) if hasattr(self, "spin_quantity") else 1
        if qty < 1:
            qty = 1
        if w <= 0 or h <= 0:
            QMessageBox.warning(
                self,
                "Стекло",
                "Размер стекла получился неположительный. Проверьте размеры фасада и профиль.",
            )
            return

        self._hide_cutout_action_bar()

        initial_payload = None
        if isinstance(self._glass_selection, dict):
            initial_payload = self._glass_selection.get("blocks_selected")
        if not isinstance(initial_payload, dict):
            initial_payload = {
                "Параметры изделия": {
                    "Форма": "Прямоугольник",
                    "Ширина (мм)": w,
                    "Высота (мм)": h,
                        "Количество (шт)": qty,
                },
                "Параметры материала": {},
            }

        if self._glass_main_app is None:
            self._glass_modal_host = QWidget()
            self._glass_embed_layout = QVBoxLayout(self._glass_modal_host)
            self._glass_embed_layout.setContentsMargins(0, 0, 0, 0)
            self._glass_embed_layout.setSpacing(0)
            initial_json = json.dumps(initial_payload, ensure_ascii=False)
            app = blocks_xx.MainApp(
                initial_blocks_json=initial_json,
                show_glass_additional_button=True,
            )
            self._glass_embed_layout.addWidget(app)

            app.client_strip.setVisible(False)
            app.srochno.setVisible(False)
            app.packaging.setVisible(False)
            app.zamer.setVisible(False)
            app._btn_preview.setVisible(False)
            app._btn_pdf.setVisible(False)
            app._btn_json.setVisible(False)
            app._btn_finish_calc.setVisible(False)
            if getattr(app, "_btn_save_linked", None) is not None:
                app._btn_save_linked.setVisible(False)
            app._actions_bar.setVisible(False)

            try:
                app.layout().addWidget(app.dopi, 3, 5)
            except Exception:
                pass

            deps = [
                wgt
                for wgt in getattr(app, "_dependent_widgets", ())
                if wgt
                not in (
                    app.client_strip,
                    app.srochno,
                    app.packaging,
                    app.zamer,
                    app._actions_bar,
                )
            ]
            app._dependent_widgets = tuple(deps)

            g0 = app.glass
            g0.combo_shape.setCurrentText("Прямоугольник")
            g0.combo_shape.setEnabled(False)
            g0.edit_qty.setText(str(qty))
            g0.edit_qty.setEnabled(False)
            g0._w_rect.setText(str(w))
            g0._h_rect.setText(str(h))
            g0._w_rect.setEnabled(False)
            g0._h_rect.setEnabled(False)
            g0.btn_calc.setVisible(False)
            g0.lbl_mat_cost.setText(
                "Размер стекла для фасада: %s × %s мм (профильный зазор %s мм)" % (h, w, inset)
            )

            btn_finish = QPushButton("Завершить выбор стекла")
            btn_finish.setStyleSheet(
                "QPushButton { background-color: #2e7d32; color: white; font-weight: bold; "
                "font-size: 12px; padding: 10px 12px; border-radius: 6px; }"
                "QPushButton:hover { background-color: #1b5e20; }"
            )
            btn_finish.setMinimumHeight(44)
            self._glass_embed_layout.addWidget(btn_finish)

            def _save_glass():
                inset2 = self._glass_inset_mm()
                if inset2 is None:
                    return
                w2 = int(self.spin_width.value()) - int(inset2)
                h2 = int(self.spin_height.value()) - int(inset2)
                gx = app.glass
                gx.combo_shape.setCurrentText("Прямоугольник")
                gx.edit_qty.setText(str(qty))
                gx._w_rect.setText(str(w2))
                gx._h_rect.setText(str(h2))
                if not gx.is_ready_for_pricing():
                    QMessageBox.warning(self, "Стекло", "Заполните материал/цвет/толщину для стекла.")
                    return
                try:
                    app._recalc_debounce.stop()
                    app._recalculate_impl(False)
                    selected = dict(app.selected or {})
                except Exception as e:
                    QMessageBox.warning(self, "Стекло", str(e))
                    return
                matp = selected.get("Параметры материала") or {}
                self._glass_selection = {
                    "Название": matp.get("Тип материала") or "",
                    "Цвет": matp.get("Цвет / Вариант") or "",
                    "Толщина (мм)": int(matp.get("Толщина (мм)") or 0),
                    "Закалка": bool(matp.get("Закалка")),
                    "Цена за м²": matp.get("Цена за м²"),
                    "Ширина (мм)": w2,
                    "Высота (мм)": h2,
                    "Профильный зазор (мм)": inset2,
                    "blocks_selected": selected,
                }
                self.facade_canvas.set_glass_info(self._glass_selection)
                self._update_materials_summary_panel()
                d = self._glass_active_pick_dialog
                if d:
                    d.accept()

            btn_finish.clicked.connect(_save_glass)
            self._glass_main_app = app
        else:
            app = self._glass_main_app
            g1 = app.glass
            g1._w_rect.setText(str(w))
            g1._h_rect.setText(str(h))
            g1.lbl_mat_cost.setText(
                "Размер стекла для фасада: %s × %s мм (профильный зазор %s мм)" % (h, w, inset)
            )

        self._restore_glass_blocks_state(app, initial_payload)

        glass_dlg = QDialog(self)
        glass_dlg.setAttribute(Qt.WA_DontShowOnScreen, True)
        glass_dlg.setWindowTitle("Выбор стекла для фасада")
        glass_dlg.setModal(True)
        v = QVBoxLayout(glass_dlg)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._glass_modal_host)

        class _GlassPickDlgSizeSync(QObject):
            def __init__(self, dlg, outer_lay, watched_app):
                super().__init__(dlg)
                self._dlg = dlg
                self._lay = outer_lay
                self._app = watched_app

            def eventFilter(self, obj, ev):
                if obj is self._app and ev.type() == QEvent.Resize:
                    QTimer.singleShot(0, self._fit)
                return False

            def _fit(self):
                dlg = self._dlg
                if dlg is None:
                    return
                self._lay.activate()
                dlg.resize(self._lay.sizeHint())
                ag = QApplication.desktop().availableGeometry(dlg)
                fg = dlg.frameGeometry()
                fg.moveCenter(ag.center())
                dlg.move(fg.topLeft())

        _geom_sync = _GlassPickDlgSizeSync(glass_dlg, v, app)
        app.installEventFilter(_geom_sync)
        _geom_sync._fit()
        self._glass_active_pick_dialog = glass_dlg
        try:
            glass_dlg.exec_()
        finally:
            app.removeEventFilter(_geom_sync)
            self._glass_active_pick_dialog = None
            self._glass_modal_host.setParent(None)

    def _on_side_clicked(self, side):
        if self._fitting_switch_on:
            return
        profile = self._side_profiles.get(side)
        if not profile:
            return
        from ui.facade_profile_info_dialog import FacadeProfileInfoDialog

        d = FacadeProfileInfoDialog(
            profile,
            side,
            self.spin_width.value(),
            self.spin_height.value(),
            self,
            delete_all_cuts_callback=lambda s=side: self._remove_all_cuts_for_side(s),
        )
        d.exec_()

    def _on_glass_clicked(self):
        if not self._side_profiles:
            QMessageBox.information(
                self,
                "Стекло",
                "Сначала выберите профиль фасада, затем можно выбрать стекло.",
            )
            return
        self._open_glass_pick_dialog()

    def _on_add_hinges(self):
        from ui.facade_hinge_dialog import FacadeHingeSelectDialog
        cuts = self._cuts_count()
        if cuts <= 0:
            QMessageBox.information(
                self,
                "Петли",
                "Сначала выполните присадку: задайте отверстия (вырезы под петли) на схеме.",
            )
            return
        d = FacadeHingeSelectDialog(self)
        if d.exec_() != QDialog.Accepted:
            return
        hinge = d.selected_hinge()
        if not hinge:
            return
        # По ТЗ: количество = числу вырезов/отверстий.
        self._hinges = [{"hinge": hinge, "quantity": cuts}]
        self._update_materials_summary_panel()
