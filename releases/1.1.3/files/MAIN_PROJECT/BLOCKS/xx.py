# -*- coding: utf-8 -*-
import copy
import math
from typing import Optional

from PyQt5.QtWidgets import (
    QApplication,
    QWidget,
    QGridLayout,
    QFileDialog,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QSizePolicy,
)
from PyQt5.QtCore import Qt, QTimer

from calc import palette as P
from calc.db_postgres import (
    fetch_drilling_price_rows,
    fetch_furniture_row,
    fetch_packaging_prices,
    fetch_photo_print_price,
    fetch_uf_skleyka_prices,
    fetch_virez_price_table,
    get_raw_connection,
)
from calc.holes import compute_holes_line_details

from elements.polirovka_frame import Polirovka
from elements.peskostroy_frame import PeskostroyFrame
from elements.shlifovka import FrameShlifovka
from elements.facet import FrameFacet
from elements.otverst_frame import *
from elements.photo_frame import *
from elements.plenka_frame import *
from elements.pokraska_frame import *
from elements.virezi import Virez_Fame
from elements.uf_skleyka import *
from elements.prepair_angle import Angle_Frame, RECT_CORNERS, TRI_CORNERS
from elements.podgotov_uslugi import Pod_USLUGI
from elements.srochno import *
from elements.dop_uslugi import *
from elements.packaging_tile import PackagingTile
from elements.zamer_tile import ZamerTile
from elements.furniture_tile import FurnitureTile
from elements.glass_product_tile import GlassProductTile
from elements.client_strip import ClientStrip
from elements.calc_tile_style import TILE_SIDE_PX, set_service_tile_border_used

from calc.delivery_calc import delivery_price_rub, fetch_delivery_prices
from calc.pricing import (
    apply_template_surcharge_to_material_cost,
    compute_facet_cost,
    compute_grind_cost,
    compute_material_cost,
    compute_polish_cost,
    compute_temper_cost,
    grind_sides_from_facet_edge_mm,
)
from calc.pricing_packaging import compute_packaging_block
from calc.pricing_film import film_price_per_order
from calc.pricing_sandblast import compute_sandblasting_cost
from calc.corner_labels import corner_sort_keys, vertex_display
from calc.corner_rounding import compute_corner_rounding_detail, parse_thickness_mm
from calc.serialize import order_payload_from_json, order_payload_to_json


def _izd_height_mm(izd: dict) -> int:
    """Высота изделия из просчёта геометрии (мм); 0 если нет в данных."""
    v = izd.get("Высота (мм)")
    if v is None:
        return 0
    try:
        return max(0, int(round(float(v))))
    except (TypeError, ValueError):
        return 0


def _client_is_legal_for_pricing(client_block: dict) -> bool:
    """Юр. лицо / ИП — цена из колонки price_legal; иначе (в т.ч. клиент не выбран) — физ. лицо."""
    if (client_block or {}).get("quick_client_id"):
        return False
    cid = (client_block or {}).get("id")
    if not cid:
        return False
    try:
        from db import models

        row = models.get_client_by_id(cid)
        if not row:
            return False
        return (row.get("client_type") or "").strip() == "legal"
    except Exception:
        return False


def _client_markup_factor(client_block: dict) -> float:
    qcid = (client_block or {}).get("quick_client_id")
    if qcid:
        try:
            from db import models

            row = models.get_mirror_quick_client_by_id(int(qcid))
            if row:
                p = int(row.get("markup_percent") or 0)
                return 1.0 + max(0, p) / 100.0
        except Exception:
            pass
    cid = (client_block or {}).get("id")
    if not cid:
        return 1.0
    try:
        from db import models

        row = models.get_client_by_id(cid)
        return float(models.client_price_factor(row))
    except Exception:
        return 1.0


def _money_ceiled(value, factor: float) -> int:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if factor <= 1.0:
        return int(round(v))
    return int(math.ceil(v * factor))


def _money_key(key: str) -> bool:
    """Поля с суммами в ₽. Не отсекать «Цена за м² (₽)» из-за подстроки «м²» — иначе наценка не трогала часть материала."""
    k = str(key or "").lower()
    return (
        ("₽" in k)
        or ("руб" in k)
        or ("цена" in k)
        or ("стоимость" in k)
        or ("сумма" in k)
        or ("итого" in k)
    )


_SKIP_CLIENT_MARKUP_ROOT_KEYS = frozenset({"Доставка", "Замер"})


def _apply_client_markup_to_payload(
    node, factor: float, parent_key: str = "", skip_markup: bool = False
):
    """Наценка клиента на денежные поля; доставка и замер/выезд — без наценки (как договорные услуги)."""
    eff = 1.0 if skip_markup else factor
    if eff <= 1.0:
        return node
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            child_skip = skip_markup or (
                parent_key == "" and str(k) in _SKIP_CLIENT_MARKUP_ROOT_KEYS
            )
            if isinstance(v, (dict, list)):
                out[k] = _apply_client_markup_to_payload(v, factor, str(k), child_skip)
            elif isinstance(v, (int, float)) and (_money_key(k) or _money_key(parent_key)):
                out[k] = _money_ceiled(v, eff)
            else:
                out[k] = v
        return out
    if isinstance(node, list):
        return [
            _apply_client_markup_to_payload(v, factor, parent_key, skip_markup) for v in node
        ]
    return node


class MainApp(QWidget):
    def __init__(
        self,
        elements: Optional[dict] = None,
        *,
        linked_order_id: Optional[int] = None,
        initial_blocks_json: Optional[str] = None,
        linked_finish_cb=None,
        linked_bundle_save_cb=None,
        quick_estimate_mode: bool = False,
        show_glass_additional_button: bool = False,
    ):
        super().__init__()
        self.elements = elements if elements is not None else {}
        self._linked_order_id = linked_order_id
        self._linked_finish_cb = linked_finish_cb
        self._linked_bundle_save_cb = linked_bundle_save_cb
        self._quick_estimate_mode = bool(quick_estimate_mode)
        self._linked_skip_secondary_reset = bool((initial_blocks_json or "").strip())
        self._initial_selected_snapshot = None
        self.setWindowTitle("Arsenal mirror")
        self.setGeometry(100, 100, 1320, 860)

        self.selected = {}
        self._order_preview_dialog = None
        self.client_strip = ClientStrip(quick_estimate_mode=self._quick_estimate_mode)
        self.glass = GlassProductTile(show_additional_button=show_glass_additional_button)
        self.polirovka = Polirovka()
        self.shlifovka = FrameShlifovka()
        self.facet = FrameFacet()
        self.peskostroy = PeskostroyFrame()
        self.otverst = Otverst()
        self.photo = Photo()
        self.plenka = Plenka()
        self.pokraska = Pokraska()
        self.virez = Virez_Fame()
        self.uf = UF_Frame()
        self.angle = Angle_Frame()
        self.pod = Pod_USLUGI()
        self.srochno = Srochnost()
        self.packaging = PackagingTile()
        self.dopi = Dopi()
        self.furniture = FurnitureTile()
        self.zamer = ZamerTile(self.client_strip)
        self.client_strip.clientIdentityChanged.connect(self._schedule_recalculate_prices)

        self._holes_recalc_timer = QTimer(self)
        self._holes_recalc_timer.setSingleShot(True)
        self._holes_recalc_timer.setInterval(120)
        self._holes_recalc_timer.timeout.connect(self._run_recalculate_prices)
        self._recalc_debounce = QTimer(self)
        self._recalc_debounce.setSingleShot(True)
        self._recalc_debounce.setInterval(90)
        self._recalc_debounce.timeout.connect(self._run_recalculate_prices)

        self.initUI()
        self._pricing_cache = {}
        self._warm_pricing_cache()
        self._wire_recalc_signals()
        self._wire_edge_exclusive()
        self._wire_secondary_refresh_signals()
        self.glass.calculateClicked.connect(self._on_glass_calculate_pressed)
        self.glass.additionalRequested.connect(self._on_glass_additional_pressed)
        self.glass.localMetricsChanged.connect(self._on_glass_local_metrics)
        self.glass.configurationChanged.connect(self._on_glass_configuration_dirty)
        self.glass.pricingOptionsChanged.connect(self._on_glass_pricing_options)
        self.glass.combo_thickness.currentIndexChanged.connect(self._sync_furniture_thickness)
        self.glass.combo_material.currentIndexChanged.connect(
            lambda *_: QTimer.singleShot(100, self._sync_furniture_thickness)
        )
        self.glass.combo_variant.currentIndexChanged.connect(
            lambda *_: QTimer.singleShot(100, self._sync_furniture_thickness)
        )
        self._secondary_shown = False
        self._set_all_secondary_visible(False)
        self._recalculate_impl(False)
        self._apply_compact_window_size()

        if initial_blocks_json:
            self._apply_initial_blocks_json(initial_blocks_json)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_compact_window_size()

    def _apply_compact_window_size(self):
        """Размер как при полной сетке; скрытые плитки не уменьшают окно."""
        if self.layout() is None:
            return
        sh = getattr(self, "_secondary_shown", False)
        for w in getattr(self, "_dependent_widgets", ()):
            if w is not None:
                w.setVisible(True)
        self.layout().activate()
        m = self.layout().minimumSize()
        for w in getattr(self, "_dependent_widgets", ()):
            if w is not None:
                w.setVisible(sh)
        self.setFixedSize(max(m.width() + 12, 400), max(m.height() + 12, 400))

    def _sync_furniture_thickness(self):
        """Список фурнитуры (полкодержатели) зависит от толщины стекла в плитке."""
        try:
            th = parse_thickness_mm(self.glass.combo_thickness.currentText())
        except Exception:
            th = 0
        self.furniture.set_filter_thickness_mm(th)

    def _on_glass_local_metrics(self):
        m = self.glass.last_geometry_metrics()
        sh = self.glass.combo_shape.currentText()
        self.angle.set_shape(sh)
        try:
            b = self.glass.build_selected(False)
            self._sync_edge_tiles_from_izd(b.get("Параметры изделия", {}))
        except Exception:
            pass

    def _warm_pricing_cache(self):
        """Кеш справочников/прайсов для ускорения повторных пересчётов."""
        cache = {}
        conn = None
        try:
            conn = get_raw_connection()
            cache["drilling_rows"] = fetch_drilling_price_rows(conn=conn)
            cache["photo_price"] = fetch_photo_print_price(conn=conn)
            cache["uf_rates"] = fetch_uf_skleyka_prices(conn=conn)
            cache["virez_rows"] = fetch_virez_price_table(conn=conn)
            cache["packaging_prices"] = fetch_packaging_prices(conn=conn)
            cache["delivery_prices"] = fetch_delivery_prices(conn=conn)
        except Exception:
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        self._pricing_cache = cache

    def _sync_edge_tiles_from_izd(self, izd: dict):
        self.polirovka.sync_from_izd(izd)
        self.shlifovka.sync_from_izd(izd)
        self.facet.sync_from_izd(izd)
        sh = izd.get("Форма", "") or ""
        self.facet.set_facet_active(sh not in ("Круг", "Овал", "Сложная фигура"))

    def _wire_recalc_signals(self):
        for cb in self.polirovka.checkboxes.values():
            cb.stateChanged.connect(self._schedule_recalculate_prices)
        self.polirovka.button.clicked.connect(self._schedule_recalculate_prices)
        for cb in self.shlifovka.checkboxes.values():
            cb.stateChanged.connect(self._schedule_recalculate_prices)
        self.shlifovka.button.clicked.connect(self._schedule_recalculate_prices)
        for c in (self.facet.top, self.facet.left, self.facet.right, self.facet.bot):
            if hasattr(c, "currentIndexChanged"):
                c.currentIndexChanged.connect(self._schedule_recalculate_prices)
        self.facet.button.clicked.connect(self._schedule_recalculate_prices)
        self.peskostroy.chk.toggled.connect(self._schedule_recalculate_prices)
        self.peskostroy.chk_double.toggled.connect(self._schedule_recalculate_prices)
        self.peskostroy._grp.buttonClicked.connect(self._schedule_recalculate_prices)
        self.peskostroy.sandChanged.connect(self._schedule_recalculate_prices)
        self.plenka.chk.toggled.connect(self._schedule_recalculate_prices)
        self.plenka.combo.currentIndexChanged.connect(self._schedule_recalculate_prices)
        self.photo.chk.toggled.connect(self._schedule_recalculate_prices)
        self.photo.imageChanged.connect(self._schedule_recalculate_prices)
        self.pokraska.chk.toggled.connect(self._schedule_recalculate_prices)
        self.pokraska.combo.currentIndexChanged.connect(self._schedule_recalculate_prices)
        self.angle.cornersChanged.connect(self._schedule_recalculate_prices)
        self.otverst.holesChanged.connect(self._holes_recalc_timer.start)

    def _wire_edge_exclusive(self):
        from calc.edge_exclusion import (
            after_facet_fill_all,
            after_grind_select_all,
            after_polish_select_all,
            on_facet_side_changed,
            on_grind_side_toggled,
            on_polish_side_toggled,
        )

        self.polirovka.button.clicked.disconnect()
        self.polirovka.button.clicked.connect(self._on_polish_na_vse)
        self.shlifovka.button.clicked.disconnect()
        self.shlifovka.button.clicked.connect(self._on_grind_na_vse)
        self.facet.button.clicked.disconnect()
        self.facet.button.clicked.connect(self._on_facet_na_vse)

        for name, idx in (("Верх", 1), ("Лево", 2), ("Право", 3), ("Низ", 4)):
            cbp = self.polirovka.checkboxes[idx]
            cbp.stateChanged.disconnect()
            cbp.stateChanged.connect(lambda st, n=name: self._on_polish_side_state(n, st))
            cbg = self.shlifovka.checkboxes[idx]
            cbg.stateChanged.disconnect()
            cbg.stateChanged.connect(lambda st, n=name: self._on_grind_side_state(n, st))

        for side, combo in (
            ("Верх", self.facet.top),
            ("Низ", self.facet.bot),
            ("Лево", self.facet.left),
            ("Право", self.facet.right),
        ):
            combo.currentIndexChanged.disconnect()
            combo.currentIndexChanged.connect(lambda _i, s=side: self._on_facet_combo_changed(s))

    def _on_polish_na_vse(self):
        from calc.edge_exclusion import after_polish_select_all

        for cb in self.polirovka.checkboxes.values():
            cb.blockSignals(True)
        try:
            self.polirovka.select_all_checkboxes()
        finally:
            for cb in self.polirovka.checkboxes.values():
                cb.blockSignals(False)
        after_polish_select_all(self, self.polirovka.status)
        self._recalc_debounce.stop()
        self._recalculate_impl(False)

    def _on_grind_na_vse(self):
        from calc.edge_exclusion import after_grind_select_all

        for cb in self.shlifovka.checkboxes.values():
            cb.blockSignals(True)
        try:
            self.shlifovka.select_all_checkboxes()
        finally:
            for cb in self.shlifovka.checkboxes.values():
                cb.blockSignals(False)
        after_grind_select_all(self, self.shlifovka.status)
        self._recalc_debounce.stop()
        self._recalculate_impl(False)

    def _on_facet_na_vse(self):
        from calc.edge_exclusion import after_facet_fill_all

        _fc = (self.facet.top, self.facet.left, self.facet.right, self.facet.bot)
        for c in _fc:
            c.blockSignals(True)
        try:
            self.facet.btn_click()
        finally:
            for c in _fc:
                c.blockSignals(False)
        after_facet_fill_all(self)
        self._recalc_debounce.stop()
        self._recalculate_impl(False)

    def _on_polish_side_state(self, side_ru: str, state: int):
        from calc.edge_exclusion import on_polish_side_toggled

        on_polish_side_toggled(self, side_ru, state == Qt.Checked)
        self._schedule_recalculate_prices()

    def _on_grind_side_state(self, side_ru: str, state: int):
        from calc.edge_exclusion import on_grind_side_toggled

        on_grind_side_toggled(self, side_ru, state == Qt.Checked)
        self._schedule_recalculate_prices()

    def _on_facet_combo_changed(self, side_ru: str):
        from calc.edge_exclusion import on_facet_side_changed

        on_facet_side_changed(self, side_ru)
        self._schedule_recalculate_prices()

    def _schedule_recalculate_prices(self, *_args):
        """
        Слот для сигналов Qt. Раньше _recalculate подключали напрямую: toggled/stateChanged
        передавали bool/int в первый аргумент и он воспринимался как upload_sketch → лишние
        выгрузки на сервер и подвисания UI.
        """
        self._recalc_debounce.start()

    def _run_recalculate_prices(self):
        self._recalculate_impl(False)

    def _on_client_text_for_recalc(self, *_args):
        """Смена клиента меняет цену фурнитуры (юр/физ) — сбрасываем кэш и пересчитываем."""
        self.furniture.set_pricing_cache(None, 1)
        self._schedule_recalculate_prices()

    def _on_furniture_qty_light(self):
        """Только количество фурнитуры — без полного пересчёта и без запросов к БД."""
        if not getattr(self, "_secondary_shown", False):
            self._schedule_recalculate_prices()
            return
        fur = self.furniture.get_payload()
        if not fur.get("Включено") or not fur.get("id"):
            self._schedule_recalculate_prices()
            return
        unit = self.furniture.get_cached_unit_rub()
        if unit is None or unit <= 0:
            self._schedule_recalculate_prices()
            return
        izd = self.selected.get("Параметры изделия") or {}
        try:
            q_glass = max(1, int(izd.get("Количество (шт)") or 1))
        except (TypeError, ValueError):
            q_glass = 1
        qf = max(1, int(fur.get("Количество") or 1))
        one_piece = unit * qf
        all_order = one_piece * q_glass
        legal = _client_is_legal_for_pricing(self.selected.get("Клиент") or {})
        row = self.furniture.get_catalog_row_for_id(int(fur["id"]))
        is_shelf = bool(row and row.get("is_shelf_holder"))
        self.selected["Фурнитура"] = {
            **fur,
            "Тип цены": "юр. лицо / ИП" if legal else "физ. лицо",
            "Цена за единицу (₽)": unit,
            "За все в изделии (₽)": one_piece,
            "За все изделия в заказе (₽)": all_order,
            "Изделие полка": is_shelf,
        }
        self.furniture.set_pricing_cache(unit, q_glass)
        if isinstance(self.selected.get("Параметры изделия"), dict):
            self.selected["Параметры изделия"]["Полка"] = is_shelf
        lines = [
            "1 шт.: %s ₽" % unit,
            "За %s шт. в изделии: %s ₽" % (qf, one_piece),
        ]
        if q_glass > 1:
            lines.append("За заказ (%s изд.): %s ₽" % (q_glass, all_order))
        self.furniture.set_cost_lines(lines)

    def _wire_secondary_refresh_signals(self):
        """Обновить цвет рамок блоков без полного пересчёта, если панель уже открыта."""
        self.peskostroy.chk.toggled.connect(self._maybe_refresh_highlights)
        self.peskostroy._grp.buttonClicked.connect(self._maybe_refresh_highlights)
        self.peskostroy.chk_double.toggled.connect(self._maybe_refresh_highlights)
        self.peskostroy.sandChanged.connect(self._maybe_refresh_highlights)

        for b in self.srochno.sroch_g.buttons():
            b.toggled.connect(self._maybe_refresh_highlights)
        for b in self.srochno.price_group.buttons():
            b.toggled.connect(self._maybe_refresh_highlights)
        self.srochno.rubles_input.textChanged.connect(self._maybe_refresh_highlights)
        self.srochno.percent_input.textChanged.connect(self._maybe_refresh_highlights)

        self.packaging.packagingChanged.connect(self._schedule_recalculate_prices)
        self.packaging.packagingChanged.connect(self._maybe_refresh_highlights)
        self.furniture.furnitureChanged.connect(self._schedule_recalculate_prices)
        self.furniture.furnitureQtyChanged.connect(self._on_furniture_qty_light)
        self.client_strip.edit.textChanged.connect(self._on_client_text_for_recalc)

        for attr, _t in Pod_USLUGI._KEYS:
            getattr(self.pod, attr).clicked.connect(self._maybe_refresh_highlights)
            getattr(self.pod, attr).clicked.connect(self._schedule_recalculate_prices)

        self.virez.add_button.clicked.connect(self._maybe_refresh_highlights)
        self.virez.add_button.clicked.connect(self._schedule_recalculate_prices)
        self.virez.btn_delete.clicked.connect(self._maybe_refresh_highlights)
        self.virez.btn_delete.clicked.connect(self._schedule_recalculate_prices)
        self.uf.add_btn_hinge.clicked.connect(self._maybe_refresh_highlights)
        self.uf.add_btn_hinge.clicked.connect(self._schedule_recalculate_prices)
        self.uf.add_btn_segment.clicked.connect(self._maybe_refresh_highlights)
        self.uf.add_btn_segment.clicked.connect(self._schedule_recalculate_prices)
        self.uf.nakleit.textChanged.connect(self._maybe_refresh_highlights)
        self.uf.snyat.textChanged.connect(self._maybe_refresh_highlights)
        self.uf.kol_vo.textChanged.connect(self._maybe_refresh_highlights)
        self.uf.dlina.textChanged.connect(self._maybe_refresh_highlights)
        self.dopi.add_button.clicked.connect(self._maybe_refresh_highlights)
        m2 = self.virez.items_list.model()
        m2.rowsInserted.connect(lambda *a: self._maybe_refresh_highlights())
        m2.rowsInserted.connect(lambda *a: self._schedule_recalculate_prices())
        m2.rowsRemoved.connect(lambda *a: self._maybe_refresh_highlights())
        m2.rowsRemoved.connect(lambda *a: self._schedule_recalculate_prices())
        m3 = self.uf.items_list.model()
        m3.rowsInserted.connect(lambda *a: self._maybe_refresh_highlights())
        m3.rowsInserted.connect(lambda *a: self._schedule_recalculate_prices())
        m3.rowsRemoved.connect(lambda *a: self._maybe_refresh_highlights())
        m3.rowsRemoved.connect(lambda *a: self._schedule_recalculate_prices())
        m4 = self.dopi.items_list.model()
        m4.rowsInserted.connect(lambda *a: self._maybe_refresh_highlights())
        m4.rowsInserted.connect(lambda *a: self._schedule_recalculate_prices())
        m4.rowsRemoved.connect(lambda *a: self._maybe_refresh_highlights())
        m4.rowsRemoved.connect(lambda *a: self._schedule_recalculate_prices())
        self.dopi.comment_input.textChanged.connect(self._schedule_recalculate_prices)
        self.dopi.number_input.textChanged.connect(self._schedule_recalculate_prices)

        self.zamer.saved.connect(self._maybe_refresh_highlights)
        self.zamer.visitChanged.connect(self._schedule_recalculate_prices)
        self.zamer.visitChanged.connect(self._maybe_refresh_highlights)
        self.zamer.edit_addr.textChanged.connect(self._schedule_recalculate_prices)
        self.zamer.phone.textChanged.connect(self._schedule_recalculate_prices)
        self.zamer.extra.textChanged.connect(self._schedule_recalculate_prices)

    def _maybe_refresh_highlights(self):
        if getattr(self, "_secondary_shown", False):
            self._refresh_block_usage_highlights()

    def _set_all_secondary_visible(self, vis: bool):
        self._secondary_shown = vis
        for w in getattr(self, "_dependent_widgets", ()):
            if w is not None:
                w.setVisible(vis)

    def _on_glass_configuration_dirty(self):
        self.glass.set_glass_border_highlight(False)
        if getattr(self, "_linked_order_id", None) is not None and getattr(
            self, "_secondary_shown", False
        ):
            self._apply_compact_window_size()
            return
        self._set_all_secondary_visible(False)
        self._apply_compact_window_size()

    def _on_glass_pricing_options(self):
        """Закалка / подгонка: пересчёт без скрытия блоков (иначе сбрасывалась сетка услуг)."""
        if getattr(self, "_secondary_shown", False) and getattr(
            self.glass, "_glass_border_ready", False
        ):
            self._recalc_debounce.stop()
            self._recalculate_impl(False)

    def _on_glass_calculate_pressed(self):
        if not self.glass.is_ready_for_pricing():
            QMessageBox.warning(
                self,
                "Рассчёт",
                "Укажите материал, вариант, толщину и корректные размеры (периметр > 0). "
                "Для сложной фигуры загрузите макет.",
            )
            return
        # На «Рассчитать» всегда начинаем с чистого состояния дополнительных блоков,
        # чтобы не оставались чекбоксы/поля от предыдущего изделия.
        self._reset_secondary_widgets()
        self._recalc_debounce.stop()
        self._recalculate_impl(False)
        self._set_all_secondary_visible(True)
        self.glass.set_glass_border_highlight(True)
        self._refresh_block_usage_highlights()
        self._apply_compact_window_size()

    def _on_glass_additional_pressed(self):
        """
        Открыть дополнительные плитки без отдельной кнопки «Рассчитать».
        Используется в фасадном выборе стекла.
        """
        if not self.glass.is_ready_for_pricing():
            QMessageBox.warning(
                self,
                "Рассчёт",
                "Укажите материал, вариант, толщину и корректные размеры (периметр > 0). "
                "Для сложной фигуры загрузите макет.",
            )
            return
        self._recalc_debounce.stop()
        self._recalculate_impl(False)
        self._set_all_secondary_visible(True)
        self.glass.set_glass_border_highlight(True)
        self._refresh_block_usage_highlights()
        self._apply_compact_window_size()

    def _reset_secondary_widgets(self):
        self.polirovka.reset_to_defaults()
        self.shlifovka.reset_to_defaults()
        self.facet.reset_to_defaults()
        self.peskostroy.reset_to_defaults()
        self.otverst.reset_to_defaults()
        self.photo.reset_to_defaults()
        self.plenka.reset_to_defaults()
        self.pokraska.reset_to_defaults()
        self.virez.reset_to_defaults()
        self.uf.reset_to_defaults()
        self.dopi.items_list.clear()
        self.dopi.items.clear()
        self.dopi.comment_input.clear()
        self.dopi.number_input.clear()
        a = self.angle
        for k in list(a.rounding.keys()):
            a.rounding[k] = 0
        for k in list(a.cutting.keys()):
            a.cutting[k] = False
        a.cut_type = "С обработкой"
        a._refresh_summary()
        a.lbl_corner_money.setText("")
        self.pod.reset_to_defaults()
        sr = self.srochno
        sr.sroch_g.setExclusive(False)
        for b in sr.sroch_g.buttons():
            b.setChecked(False)
        sr.sroch_g.setExclusive(True)
        sr.price_group.setExclusive(False)
        for b in sr.price_group.buttons():
            b.setChecked(False)
        sr.price_group.setExclusive(True)
        sr.rubles_input.blockSignals(True)
        sr.rubles_input.clear()
        sr.rubles_input.blockSignals(False)
        sr.percent_input.blockSignals(True)
        sr.percent_input.clear()
        sr.percent_input.blockSignals(False)
        sr.rubles_input.setEnabled(False)
        sr.percent_input.setEnabled(False)
        self.packaging.reset_to_defaults()
        self.zamer.reset_to_defaults()
        self.furniture.reset_to_defaults()
        for w in (
            self.polirovka,
            self.shlifovka,
            self.facet,
            self.peskostroy,
            self.otverst,
            self.photo,
            self.plenka,
            self.pokraska,
            self.virez,
            self.uf,
            self.angle,
            self.pod,
            self.srochno,
            self.packaging,
            self.dopi,
            self.furniture,
            self.zamer,
        ):
            set_service_tile_border_used(w, False)

    def _facet_block_used(self) -> bool:
        return self.facet.facet_needed()

    def _peskostroy_used(self) -> bool:
        return self.peskostroy.chk.isChecked()

    def _angle_block_used(self) -> bool:
        a = self.angle
        sh = a._shape or ""
        if sh == "Прямоугольник":
            corners = RECT_CORNERS
        elif sh == "Треугольник":
            corners = TRI_CORNERS
        elif sh == "Трапеция":
            corners = RECT_CORNERS
        else:
            corners = []
        if any(int(a.rounding.get(k, 0) or 0) > 0 for k in corners):
            return True
        if any(a.cutting.get(k) for k in corners):
            return True
        return False

    def _srochno_used(self) -> bool:
        d = self.srochno.get_info()
        if not d:
            return False
        if d.get("Срочность"):
            return True
        if d.get("Тип изменения цены"):
            return True
        if (d.get("Рубли") or 0) > 0:
            return True
        p = d.get("Проценты")
        try:
            return p is not None and float(p) != 0.0
        except (TypeError, ValueError):
            return False

    def _packaging_used(self) -> bool:
        return self.packaging.is_any_selected()

    def _pod_used(self) -> bool:
        info = self.pod.get_info()
        return int(info.get("Итого (₽)", 0) or 0) > 0

    def _refresh_block_usage_highlights(self):
        set_service_tile_border_used(self.polirovka, self.polirovka.polish_needed())
        set_service_tile_border_used(self.shlifovka, self.shlifovka.grind_needed())
        set_service_tile_border_used(self.facet, self._facet_block_used())
        set_service_tile_border_used(self.otverst, len(self.otverst.get_holes()) > 0)
        set_service_tile_border_used(self.peskostroy, self._peskostroy_used())
        set_service_tile_border_used(self.photo, self.photo.is_enabled_service())
        pl = self.plenka.chk.isChecked() and bool(self.plenka.current_choice())
        set_service_tile_border_used(self.plenka, pl)
        pk = self.pokraska.chk.isChecked() and bool(self.pokraska.current_choice())
        set_service_tile_border_used(self.pokraska, pk)
        set_service_tile_border_used(self.virez, len(getattr(self.virez, "items", []) or []) > 0)
        set_service_tile_border_used(self.uf, self.uf.block_should_highlight_used())
        set_service_tile_border_used(self.angle, self._angle_block_used())
        set_service_tile_border_used(self.pod, self._pod_used())
        set_service_tile_border_used(self.srochno, self._srochno_used())
        set_service_tile_border_used(self.packaging, self._packaging_used())
        set_service_tile_border_used(self.zamer, self.zamer.highlight_zamer_used())
        set_service_tile_border_used(self.dopi, len(getattr(self.dopi, "items", []) or []) > 0)
        set_service_tile_border_used(self.furniture, self.furniture.furniture_needed())

    def initUI(self):
        grid = QGridLayout()
        grid.setSpacing(5)
        self.setLayout(grid)
        self.setObjectName("xxx")
        self.setStyleSheet(
            "#xxx { border: 3px solid %s; background-color: %s;}"
            % (P.TILE_BORDER_IDLE, P.MAIN_WINDOW_BG)
        )

        grid.addWidget(self.client_strip, 0, 0, 1, 6)

        for c in range(6):
            grid.setColumnMinimumWidth(c, TILE_SIDE_PX)

        grid.addWidget(self.glass, 1, 0, 2, 2, Qt.AlignTop | Qt.AlignLeft)
        grid.addWidget(self.polirovka, 1, 2, Qt.AlignTop | Qt.AlignLeft)
        grid.addWidget(self.shlifovka, 1, 3, Qt.AlignTop | Qt.AlignLeft)
        grid.addWidget(self.facet, 1, 4, Qt.AlignTop | Qt.AlignLeft)
        grid.addWidget(self.plenka, 1, 5, Qt.AlignTop | Qt.AlignLeft)

        grid.addWidget(self.otverst, 2, 2)
        grid.addWidget(self.virez, 2, 3)
        grid.addWidget(self.peskostroy, 2, 4)
        grid.addWidget(self.photo, 2, 5)

        grid.addWidget(self.uf, 3, 0)
        grid.addWidget(self.angle, 3, 1)
        grid.addWidget(self.pod, 3, 2)
        grid.addWidget(self.pokraska, 3, 3)
        grid.addWidget(self.furniture, 3, 4)
        grid.addWidget(self.srochno, 3, 5)

        # Нижний ряд: замер|доставка|монтаж (одна широкая плитка), упаковка, доп., кнопки.
        grid.addWidget(self.zamer, 4, 0, 1, 3, Qt.AlignLeft | Qt.AlignTop)
        grid.addWidget(self.packaging, 4, 3)
        grid.addWidget(self.dopi, 4, 4)

        self._btn_preview = QPushButton("Модель")
        self._btn_pdf = QPushButton("PDF")
        self._btn_json = QPushButton("JSON")
        self._btn_finish_calc = QPushButton("Завершить расчёт")
        self._btn_save_linked = None
        self._btn_preview.setToolTip("Показать модель заказа")
        self._btn_pdf.setToolTip("Сохранить PDF просчёта")
        self._btn_json.setToolTip("Сохранить JSON просчёта")
        self._btn_finish_calc.setToolTip(
            "Сохранить просчёт в заказ и вернуться в главное окно"
            if getattr(self, "_linked_finish_cb", None)
            else "После заполнения изделия — как «Рассчитать» на большой плитке стекла"
        )
        _btn_row = [
            self._btn_preview,
            self._btn_pdf,
            self._btn_json,
        ]
        if getattr(self, "_linked_order_id", None) is not None:
            self._btn_save_linked = QPushButton("Сохранить изменения")
            self._btn_save_linked.setToolTip("Записать просчёт в заказ, не закрывая окно")
            self._btn_save_linked.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self._btn_save_linked.setFixedWidth(int(TILE_SIDE_PX))
            self._btn_save_linked.setMaximumHeight(34)
            self._btn_save_linked.setStyleSheet(
                "QPushButton { background-color: #455a64; color: #ffffff; font-weight: bold; "
                "font-size: 10px; padding: 6px 8px; border-radius: 4px; }"
                "QPushButton:hover { background-color: #37474f; }"
            )
            self._btn_save_linked.clicked.connect(self._on_save_linked_changes)
            _btn_row.append(self._btn_save_linked)
        _btn_row.append(self._btn_finish_calc)
        for b in _btn_row:
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            b.setFixedWidth(int(TILE_SIDE_PX))
            b.setMaximumHeight(34)
        self._btn_preview.setStyleSheet(
            "QPushButton { background-color: #1976d2; color: #ffffff; font-weight: bold; "
            "font-size: 10px; padding: 6px 8px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #1565c0; }"
        )
        self._btn_pdf.setStyleSheet(
            "QPushButton { background-color: #fbc02d; color: #1a1a1a; font-weight: bold; "
            "font-size: 10px; padding: 6px 8px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #f9a825; }"
        )
        self._btn_json.setStyleSheet(
            "QPushButton { background-color: #6a1b9a; color: #ffffff; font-weight: bold; "
            "font-size: 10px; padding: 6px 8px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #4a148c; }"
        )
        self._btn_finish_calc.setStyleSheet(
            "QPushButton { background-color: #2e7d32; color: #ffffff; font-weight: bold; "
            "font-size: 10px; padding: 6px 8px; border-radius: 4px; }"
            "QPushButton:hover { background-color: #1b5e20; }"
        )
        self._btn_preview.clicked.connect(self._open_order_preview_dialog)
        self._btn_pdf.clicked.connect(self._export_order_pdf)
        self._btn_json.clicked.connect(self._save_json)
        self._btn_finish_calc.clicked.connect(self._on_finish_calc_toolbar_pressed)

        self._actions_bar = QWidget()
        self._actions_bar.setObjectName("mainActionsBar")
        self._actions_bar.setAttribute(Qt.WA_StyledBackground, True)
        self._actions_bar.setStyleSheet(
            "#mainActionsBar { background-color: %s; border: none; }" % P.MAIN_WINDOW_BG
        )
        actions_lay = QVBoxLayout(self._actions_bar)
        actions_lay.setContentsMargins(0, 0, 0, 0)
        actions_lay.setSpacing(6)
        actions_lay.addStretch(1)
        for b in _btn_row:
            actions_lay.addWidget(b, 0, Qt.AlignHCenter)
        actions_lay.addStretch(1)
        grid.addWidget(self._actions_bar, 4, 5, 1, 1, Qt.AlignCenter)

        deps = [
            self.polirovka,
            self.shlifovka,
            self.facet,
            self.otverst,
            self.peskostroy,
            self.photo,
            self.plenka,
            self.pokraska,
            self.virez,
            self.uf,
            self.angle,
            self.pod,
            self.srochno,
            self.packaging,
            self.dopi,
            self.zamer,
            self.furniture,
            self._actions_bar,
        ]
        self._dependent_widgets = deps

    def _open_order_preview_dialog(self):
        from elements.order_model_dialog import OrderPreviewDialog

        if self._order_preview_dialog is None:
            self._order_preview_dialog = OrderPreviewDialog(self, self)
        self._order_preview_dialog.show()
        self._order_preview_dialog.raise_()
        self._order_preview_dialog.activateWindow()

    def _export_order_pdf(self):
        from calc.order_pdf import write_blocks_order_pdf

        self._recalc_debounce.stop()
        try:
            self._recalculate_impl(False)
        except Exception as e:
            QMessageBox.warning(self, "PDF", "Пересчёт: %s" % e)
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "PDF просчёта", "blocks_order.pdf", "PDF (*.pdf)"
        )
        if not path:
            return
        try:
            write_blocks_order_pdf(path, self)
            QMessageBox.information(self, "PDF", "Файл сохранён:\n%s" % path)
        except Exception as e:
            QMessageBox.critical(self, "PDF", str(e))

    def _save_json(self):
        self._recalc_debounce.stop()
        self._recalculate_impl(True)
        path, _ = QFileDialog.getSaveFileName(self, "JSON просчёта", "order_calc.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(order_payload_to_json(self.selected))
            QMessageBox.information(self, "Готово", "Файл сохранён")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def get_order_json(self) -> str:
        self._recalc_debounce.stop()
        self._recalculate_impl(True)
        return order_payload_to_json(self.selected)

    def _apply_initial_blocks_json(self, raw: str):
        try:
            sel = order_payload_from_json(raw)
        except Exception:
            return
        if not isinstance(sel, dict):
            return
        self._initial_selected_snapshot = sel
        cli = sel.get("Клиент")
        self.client_strip.set_payload(cli if isinstance(cli, dict) else {})
        izd = sel.get("Параметры изделия") if isinstance(sel.get("Параметры изделия"), dict) else {}
        matp = sel.get("Параметры материала") if isinstance(sel.get("Параметры материала"), dict) else {}
        self.glass.configurationChanged.disconnect(self._on_glass_configuration_dirty)
        try:
            self.glass.apply_from_saved_data(izd, matp)
        finally:
            self.glass.configurationChanged.connect(self._on_glass_configuration_dirty)
        QTimer.singleShot(0, self._after_initial_payload_apply)

    def _merge_snapshot_after_recalc(self, snap: dict):
        izd_new = self.selected.get("Параметры изделия")
        mat_new = self.selected.get("Параметры материала")
        cli_new = self.selected.get("Клиент")
        self.selected.clear()
        self.selected.update(copy.deepcopy(snap))
        if izd_new is not None:
            self.selected["Параметры изделия"] = izd_new
        if mat_new is not None:
            self.selected["Параметры материала"] = mat_new
        if cli_new is not None:
            self.selected["Клиент"] = cli_new

    def _after_initial_payload_apply(self):
        if not self.glass.is_ready_for_pricing():
            self._apply_compact_window_size()
            return
        self._recalc_debounce.stop()
        snap = getattr(self, "_initial_selected_snapshot", None)
        if isinstance(snap, dict):
            try:
                self.zamer.apply_saved_block(snap.get("Замер"))
                self.zamer.merge_legacy_saved_delivery(snap.get("Доставка"))
            except Exception:
                pass
            try:
                vz = snap.get("Вырезы") or {}
                self.virez.load_from_saved(vz)
            except Exception:
                pass
        try:
            self._recalculate_impl(False)
        except Exception:
            pass
        if isinstance(snap, dict) and getattr(self, "_linked_skip_secondary_reset", False):
            self._merge_snapshot_after_recalc(snap)
        self._set_all_secondary_visible(True)
        self.glass.set_glass_border_highlight(True)
        try:
            self._refresh_block_usage_highlights()
        except Exception:
            pass
        self._apply_compact_window_size()

    def _on_finish_calc_toolbar_pressed(self):
        # В диалоге MAIN_PROJECT всегда есть callback — зелёная кнопка завершает расчёт и пишет в БД,
        # даже для нового заказа (ещё без номера в БД до этого шага).
        if getattr(self, "_linked_finish_cb", None) is not None:
            self._linked_finish_calc_and_exit()
            return
        self._on_glass_calculate_pressed()

    def _linked_finish_calc_and_exit(self):
        if not self.glass.is_ready_for_pricing():
            QMessageBox.warning(
                self,
                "Расчёт",
                "Укажите материал, вариант, толщину и корректные размеры (периметр > 0). "
                "Для сложной фигуры загрузите макет.",
            )
            return
        cli = self.client_strip.get_payload() if self.client_strip else {}
        ok = bool(cli.get("id")) or bool(cli.get("quick_client_id"))
        if not ok:
            QMessageBox.warning(
                self,
                "Клиент",
                "Для завершения расчёта выберите клиента из справочника или из клиентов быстрого просчёта "
                "(кнопка «Новый клиент» в быстром просчёте создаёт запись только для быстрого просчёта).",
            )
            return
        self._recalc_debounce.stop()
        try:
            self._recalculate_impl(True)
        except Exception as e:
            QMessageBox.warning(self, "Расчёт", str(e))
            return
        cb = getattr(self, "_linked_finish_cb", None)
        if cb:
            cb(order_payload_to_json(self.selected))

    def _on_save_linked_changes(self):
        oid = getattr(self, "_linked_order_id", None)
        if oid is None:
            return
        cb = getattr(self, "_linked_bundle_save_cb", None)
        if cb is not None:
            try:
                ok = bool(cb(self))
            except Exception as e:
                QMessageBox.critical(self, "БД", str(e))
                return
            if not ok:
                return
            return
        self._recalc_debounce.stop()
        try:
            self._recalculate_impl(True)
        except Exception as e:
            QMessageBox.warning(self, "Расчёт", str(e))
            return
        try:
            from db import models as db_models

            db_models.update_order_blocks_calc(int(oid), order_payload_to_json(self.selected))
        except Exception as e:
            QMessageBox.critical(self, "БД", str(e))
            return
        QMessageBox.information(self, "Сохранено", "Изменения записаны в заказ.")

    def _apply_selected_delivery_block_from_zamer(self, *, conn=None):
        """Совместимость JSON «Доставка»: отдельная плитка убрана — данные из плитки замера/доставки/монтажа.
        Отдельная строка с ценой «Доставка» в смете только если заказана одна доставка без замера и монтажа
        (иначе выезд учитывается в «Замер (выезд)»)."""
        zblk = self.zamer.to_selected_block()
        zd = zblk.get("Данные") if isinstance(zblk.get("Данные"), dict) else {}
        if not zd.get("Доставка"):
            self.selected["Доставка"] = {"Активирован": False, "Данные": None}
            return
        vd = zd.get("Данные выезда") if isinstance(zd.get("Данные выезда"), dict) else {}
        dd = dict(vd)
        dd["Оплата"] = zd.get("Оплата") or "не указано"
        only_delivery = bool(zd.get("Доставка")) and not zd.get("Замер") and not zd.get("Монтаж")
        if not only_delivery:
            dd.pop("Доставка цена", None)
            self.selected["Доставка"] = {"Активирован": False, "Данные": None}
            return
        if not self.zamer.is_configured_for_visit_price():
            dd["Доставка цена"] = None
            self.selected["Доставка"] = {"Активирован": True, "Данные": dd}
            return
        del_tab = self._pricing_cache.get("delivery_prices") or fetch_delivery_prices(conn=conn)
        inside = bool(dd.get("Внутри КАД", True))
        km = dd.get("Расстояние до КАД")
        pr = delivery_price_rub(del_tab, inside, km)
        dd["Доставка цена"] = pr
        self.selected["Доставка"] = {"Активирован": True, "Данные": dd}

    def _recalculate_impl(self, upload_sketch: bool = False):
        conn = None
        try:
            conn = get_raw_connection()
            self.selected["Клиент"] = self.client_strip.get_payload()
            base = self.glass.build_selected(upload_sketch=upload_sketch)
            if upload_sketch:
                self.peskostroy.upload_if_needed()
            self.selected["Параметры изделия"] = base["Параметры изделия"]
            self.selected["Параметры материала"] = base["Параметры материала"]
            izd = self.selected["Параметры изделия"]
            matp = self.selected["Параметры материала"]
            self.selected["Подготовительные услуги"] = self.pod.get_info()
            shape = izd.get("Форма", "")
            self._sync_edge_tiles_from_izd(izd)
            self.angle.set_shape(shape)
            r_blk, c_blk = self.angle.to_selected_blocks()
            q_izd = int(izd.get("Количество (шт)") or 1)
            th_mm = parse_thickness_mm(matp.get("Толщина (мм)"))
            self.furniture.set_filter_thickness_mm(th_mm)
            pv = izd.get("Периметр (мм)")
            per_mm = float(pv) if pv is not None else 0.0
            r_pcs, r_ex = [], []
            if (
                r_blk.get("Включено")
                and r_blk.get("Значения")
                and th_mm > 0
                and per_mm > 0
            ):
                tot_r, kriv_r, r_pcs, r_ex = compute_corner_rounding_detail(
                    r_blk["Значения"], per_mm, th_mm, conn=conn, shape=shape
                )
                r_blk["Стоимость за изделие"] = tot_r
                r_blk["Криволинейка"] = kriv_r
                r_blk["Общая стоимость"] = tot_r * q_izd
            else:
                tot_r = 0
                r_blk["Стоимость за изделие"] = 0
                r_blk["Криволинейка"] = False
                r_blk["Общая стоимость"] = 0
            self.selected["Скругление углов"] = r_blk
            self.selected["Срезать угол"] = c_blk
            c_one = int(c_blk.get("Цена за 1 изделие") or 0)
            r_one = int(r_blk.get("Стоимость за изделие") or 0)
            ppu_cut = 25 if self.angle.cut_type == "С обработкой" else 15
            cut_map = c_blk.get("Углы") or {}
            corner_lines = []
            if c_blk.get("Включено"):
                for k in corner_sort_keys(shape):
                    if cut_map.get(k):
                        corner_lines.append(
                            "%s: срез — %s ₽" % (vertex_display(shape, k), ppu_cut)
                        )
                if c_one > 0:
                    corner_lines.append("Срезы итого (за изд.): %s ₽" % c_one)
            if r_blk.get("Включено") and (r_pcs or r_ex):
                for key, rmm, rub in r_pcs:
                    corner_lines.append(
                        "%s: R %s мм — %s ₽"
                        % (vertex_display(shape, key), rmm, rub)
                    )
                for title, rub in r_ex:
                    corner_lines.append("%s: %s ₽" % (title, rub))
                if r_pcs and r_one > 0:
                    suf = " (криволинейка)" if r_blk.get("Криволинейка") else ""
                    corner_lines.append(
                        "Скругление итого (за изд.): %s ₽%s" % (r_one, suf)
                    )
            if q_izd > 1 and (r_one > 0 or c_one > 0):
                corner_lines.append("—")
                if r_one > 0:
                    corner_lines.append(
                        "Скругление за все изделия (%s шт.): %s ₽"
                        % (q_izd, r_one * q_izd)
                    )
                if c_one > 0:
                    corner_lines.append(
                        "Срезы за все изделия (%s шт.): %s ₽"
                        % (q_izd, c_one * q_izd)
                    )
                corner_lines.append(
                    "Углы вместе за все изделия: %s ₽"
                    % (r_one * q_izd + c_one * q_izd)
                )
            self.angle.set_corner_price_lines(corner_lines)

            mc = compute_material_cost(self.selected)
            # Разбивка материала на отдельные части для макета/PDF:
            # база (без подгонки/шаблона), подгонка, шаблон.
            sel_no_fit = copy.deepcopy(self.selected)
            izd_no_fit = sel_no_fit.get("Параметры изделия") or {}
            izd_no_fit["Подгонка размеров"] = False
            sel_no_fit["Параметры изделия"] = izd_no_fit
            mc_no_fit = compute_material_cost(sel_no_fit)
            tpl_on = bool(izd.get("Изготовление по шаблону"))
            tpl_pct = izd.get("Процент шаблон (%)")
            tpl_bad = ""
            if tpl_on:
                try:
                    p0 = int(tpl_pct)
                    if p0 < 30 or p0 > 70:
                        tpl_bad = "Процент «по шаблону» должен быть 30–70."
                except (TypeError, ValueError):
                    tpl_bad = "Процент «по шаблону» должен быть 30–70."
            apply_tpl = tpl_on and not tpl_bad
            mc_use, tpl_note = apply_template_surcharge_to_material_cost(mc, apply_tpl, tpl_pct)
            if tpl_bad:
                self.glass.lbl_warn.setText(tpl_bad)
            if mc_use:
                mat_base_one = int(mc_no_fit.get("Стоимость за изделие (₽)") if mc_no_fit else 0)
                mat_base_all = int(mc_no_fit.get("Общая стоимость (₽)") if mc_no_fit else 0)
                mat_fit_one = int(mc.get("Стоимость за изделие (₽)") if mc else 0)
                mat_fit_all = int(mc.get("Общая стоимость (₽)") if mc else 0)
                fit_add_one = max(0, mat_fit_one - mat_base_one)
                fit_add_all = max(0, mat_fit_all - mat_base_all)

                matp["Цена за м² (без подгонки/шаблона)"] = int(
                    mc_no_fit.get("Цена за м² (₽)") if mc_no_fit else 0
                )
                matp["Стоимость материала за изделие (база)"] = mat_base_one
                matp["Стоимость материала за все изделия (база)"] = mat_base_all
                matp["Подгонка за изделие"] = fit_add_one
                matp["Подгонка за все изделия"] = fit_add_all
                matp["Стоимость материала за изделие (без шаблона)"] = mat_fit_one
                matp["Стоимость материала за все изделия (без шаблона)"] = mat_fit_all
                matp["Стоимость материала за изделие"] = int(
                    mc_use.get("Стоимость за изделие (₽)") or 0
                )
                matp["Стоимость материала за все изделия"] = int(mc_use.get("Общая стоимость (₽)") or 0)
                q = izd.get("Количество (шт)", 1)
                if q and int(q) > 1:
                    txt = "Материал: за м² %s ₽, за изд. %s ₽, всего %s ₽" % (
                        mc_use["Цена за м² (₽)"],
                        mc_use["Стоимость за изделие (₽)"],
                        mc_use["Общая стоимость (₽)"],
                    )
                else:
                    txt = "Материал: за м² %s ₽, изделие %s ₽" % (
                        mc_use["Цена за м² (₽)"],
                        mc_use["Стоимость за изделие (₽)"],
                    )
                if tpl_note:
                    txt += tpl_note
                self.glass.set_material_cost_label(txt)
            else:
                self.glass.set_material_cost_label("Материал: укажите материал и размеры")

            # Закалка считается отдельной услугой и отдельной строкой в макете/PDF.
            temper_info = compute_temper_cost(self.selected, conn=conn)
            if temper_info:
                matp["Стоимость закалки за 1 шт"] = int(temper_info["Стоимость закалки за изделие (₽)"])
                matp["Стоимость закалки за все"] = int(
                    temper_info["Стоимость закалки за все изделия (₽)"]
                )
                matp["Комментарий закалки"] = temper_info.get("Комментарий закалки") or ""
                self.selected["Закалка"] = {
                    "Нужна": True,
                    "Цена за 1 изделие": int(temper_info["Стоимость закалки за изделие (₽)"]),
                    "Цена за все изделия": int(temper_info["Стоимость закалки за все изделия (₽)"]),
                    "Цена за м²": int(temper_info["Цена закалки за м² (₽)"]),
                    "Площадь (м²)": float(temper_info["Площадь для закалки (м²)"]),
                    "Комментарий": temper_info.get("Комментарий закалки") or "",
                }
                mat_one = int(matp.get("Стоимость материала за изделие") or 0)
                zak_one = int(temper_info["Стоимость закалки за изделие (₽)"])
                if mat_one > 0:
                    pct = (float(zak_one) / float(mat_one)) * 100.0
                    self.glass.set_temper_impact_hint(pct, zak_one)
                else:
                    self.glass.set_temper_impact_hint(None, None)
            else:
                matp["Стоимость закалки за 1 шт"] = 0
                matp["Стоимость закалки за все"] = 0
                matp["Комментарий закалки"] = ""
                self.selected["Закалка"] = {"Нужна": False}
                self.glass.set_temper_impact_hint(None, None)

            sides_p = self.polirovka.get_polish_sides()
            if self.polirovka.polish_needed() and izd.get("Периметр (мм)"):
                self.selected["Полировка"] = {"Нужна полировка": True, **sides_p}
                pc = compute_polish_cost(self.selected, sides_p, conn=conn)
                if pc:
                    self.selected["Полировка"].update(pc)
                    q = int(izd.get("Количество (шт)") or 1)
                    if q > 1:
                        self.polirovka.set_cost_text(
                            "За изд.: %s ₽, всего: %s ₽"
                            % (pc["Стоимость за изделие (₽)"], pc["Общая стоимость (₽)"])
                        )
                    else:
                        self.polirovka.set_cost_text("%s ₽ за изделие" % pc["Стоимость за изделие (₽)"])
                else:
                    self.polirovka.set_cost_text("—")
            else:
                self.selected["Полировка"] = {"Нужна полировка": False}
                self.polirovka.set_cost_text("—")

            fs = self.facet.get_facet_state(shape)
            th_mat = parse_thickness_mm(matp.get("Толщина (мм)"))
            facet_4_free = (
                bool(fs.get("Нужен"))
                and th_mat == 4
                and shape in ("Прямоугольник", "Треугольник", "Трапеция")
            )
            if facet_4_free:
                edge_mm = self.facet.get_facet_edge_mm_by_side(shape)
                sides_g = grind_sides_from_facet_edge_mm(shape, edge_mm)
                self.shlifovka.set_facet4_free_lock(sides_g)
            else:
                self.shlifovka.set_facet4_free_lock(None)
                sides_g = self.shlifovka.get_grind_sides()

            grind_active = facet_4_free or self.shlifovka.grind_needed()
            try:
                _per_mm = float(izd.get("Периметр (мм)") or 0)
            except (TypeError, ValueError):
                _per_mm = 0.0
            if grind_active and _per_mm > 0:
                self.selected["Шлифовка"] = {"Нужна шлифовка": True, **sides_g}
                gc = compute_grind_cost(
                    self.selected,
                    sides_g,
                    conn=conn,
                    zero_price_facet_4mm=facet_4_free,
                )
                if gc:
                    self.selected["Шлифовка"].update(gc)
                    q = int(izd.get("Количество (шт)") or 1)
                    g1 = int(gc["Стоимость за изделие (₽)"])
                    gfree = facet_4_free or gc.get("Бесплатно с фацетом 4 мм")
                    gsfx = " — бесплатно (фацет 4 мм)" if gfree else ""
                    if q > 1:
                        self.shlifovka.set_cost_text(
                            "За изд.: %s ₽, всего: %s ₽%s"
                            % (g1, gc["Общая стоимость (₽)"], gsfx)
                        )
                    else:
                        self.shlifovka.set_cost_text("%s ₽ за изделие%s" % (g1, gsfx))
                else:
                    self.shlifovka.set_cost_text("—")
            else:
                if not facet_4_free:
                    self.shlifovka.set_facet4_free_lock(None)
                self.selected["Шлифовка"] = {"Нужна шлифовка": False}
                self.shlifovka.set_cost_text("—")

            if fs.get("Нужен") and izd.get("Периметр (мм)"):
                fc = compute_facet_cost(self.selected, fs, conn=conn)
                self.selected["Фацет"] = dict(fs)
                if fc:
                    self.selected["Фацет"].update(fc)
                    q = int(izd.get("Количество (шт)") or 1)
                    if q > 1:
                        self.facet.set_cost_text(
                            "За изд.: %s ₽, всего: %s ₽"
                            % (fc["Стоимость за изделие (₽)"], fc["Общая стоимость (₽)"])
                        )
                    else:
                        self.facet.set_cost_text("%s ₽ за изделие" % fc["Стоимость за изделие (₽)"])
                else:
                    self.facet.set_cost_text("—")
            else:
                self.selected["Фацет"] = {"Нужен": False}
                self.facet.set_cost_text("—")

            th = parse_thickness_mm(matp.get("Толщина (мм)"))
            tempered = bool(matp.get("Закалка"))
            self.otverst.apply_tempered_zenk(tempered)
            holes = self.otverst.get_holes()
            self.selected["Отверстия"] = holes
            drill_rows = self._pricing_cache.get("drilling_rows") or fetch_drilling_price_rows(conn=conn)
            q = int(izd.get("Количество (шт)") or 1)
            if holes and th >= 4:
                details, _sub, markup_h, final_h, _qs = compute_holes_line_details(
                    drill_rows, holes, th, tempered
                )
                self.otverst.apply_price_breakdown(
                    details, _sub, markup_h, final_h, q
                )
                ht = "%s ₽ за изд." % final_h
                if markup_h:
                    ht += " (наценка 50%)"
                if q > 1:
                    ht += ", всего %s ₽" % (final_h * q)
                self.otverst.set_cost_text(ht)
            elif holes:
                self.otverst.clear_price_breakdown()
                self.otverst.set_cost_text("Толщина 4–10 мм")
            else:
                self.otverst.clear_price_breakdown()
                self.otverst.set_cost_text("—")

            film_info = {"Использовать плёнку": self.plenka.chk.isChecked()}
            pch = self.plenka.current_choice()
            if film_info["Использовать плёнку"] and pch:
                name, price_m2 = pch
                area = izd.get("Площадь (м²)")
                q = int(izd.get("Количество (шт)") or 1)
                if area is not None:
                    try:
                        af = float(area)
                        fp = film_price_per_order(af, q, int(price_m2))
                        if fp:
                            cost_one, cost_all = fp
                            film_info["Тип плёнки"] = name
                            film_info["Цена за метр"] = price_m2
                            film_info["Площадь (м²)"] = af
                            film_info["Цена за изделие"] = cost_one
                            film_info["Общая стоимость"] = cost_all
                            if q > 1:
                                self.plenka.set_cost_text(
                                    "За изд.: %s ₽, всего: %s ₽" % (cost_one, cost_all)
                                )
                            else:
                                self.plenka.set_cost_text("%s ₽ за изделие" % cost_one)
                        else:
                            self.plenka.set_cost_text("—")
                    except Exception:
                        self.plenka.set_cost_text("—")
                else:
                    self.plenka.set_cost_text("Нажмите «Рассчитать»")
            else:
                if not film_info["Использовать плёнку"]:
                    self.plenka.set_cost_text("—")
                else:
                    self.plenka.set_cost_text("Выберите тип плёнки")
            self.selected["Плёнка"] = film_info

            sand_payload = self.peskostroy.get_payload()
            self.selected["Пескоструй"] = sand_payload
            sc = compute_sandblasting_cost(self.selected, sand_payload, conn=conn)
            if sc:
                cost_one, cost_all = sc
                self.selected["Пескоструй"]["Цена за изделие"] = cost_one
                self.selected["Пескоструй"]["Общая стоимость"] = cost_all
                q = int(izd.get("Количество (шт)") or 1)
                if q > 1:
                    self.peskostroy.set_cost_text(
                        "За изд.: %s ₽, всего: %s ₽" % (cost_one, cost_all)
                    )
                else:
                    self.peskostroy.set_cost_text("%s ₽ за изделие" % cost_one)
            elif sand_payload.get("Пескоструй"):
                self.peskostroy.set_cost_text("Нажмите «Рассчитать»")
            else:
                self.peskostroy.set_cost_text("—")

            photo_info = {"Нужна": self.photo.is_enabled_service()}
            if photo_info["Нужна"]:
                pprice = int(self._pricing_cache.get("photo_price") or fetch_photo_print_price(conn=conn) or 0)
                area = izd.get("Площадь (м²)")
                q = int(izd.get("Количество (шт)") or 1)
                if area is None or float(area) <= 0:
                    self.photo.set_cost_text("Нажмите «Рассчитать»")
                else:
                    af = float(area)
                    area_use = max(af, 0.5)
                    cost_one = round(area_use * pprice)
                    cost_all = cost_one * q
                    photo_info["Площадь (м²)"] = area_use
                    photo_info["Цена за изделие"] = cost_one
                    photo_info["Общая стоимость"] = cost_all
                    if q > 1:
                        self.photo.set_cost_text(
                            "За изд.: %s ₽, всего: %s ₽" % (cost_one, cost_all)
                        )
                    else:
                        self.photo.set_cost_text("%s ₽ за изделие" % cost_one)
            else:
                self.photo.set_cost_text("—")
            photo_info.update(self.photo.get_extra_payload())
            self.selected["Фотопечать"] = photo_info

            paint_info = {"Использовать покраску": self.pokraska.chk.isChecked()}
            pchoice = self.pokraska.current_choice()
            if paint_info["Использовать покраску"] and pchoice:
                name, price_m2 = pchoice
                area = izd.get("Площадь (м²)")
                q = int(izd.get("Количество (шт)") or 1)
                if area is None:
                    self.pokraska.set_cost_text("Нажмите «Рассчитать»")
                else:
                    af = float(area)
                    if af < 0.5:
                        af = 0.5
                    c1 = int(math.ceil(price_m2 * af))
                    call = c1 * q
                    paint_info["Цвет покраски"] = name
                    paint_info["Цена"] = price_m2
                    paint_info["Цена за 1 изделие"] = c1
                    paint_info["Цена за все изделия"] = call
                    if q > 1:
                        self.pokraska.set_cost_text(
                            "За изд.: %s ₽, всего: %s ₽" % (c1, call)
                        )
                    else:
                        self.pokraska.set_cost_text("%s ₽ за изделие" % c1)
            elif paint_info["Использовать покраску"]:
                self.pokraska.set_cost_text("Выберите цвет")
            else:
                self.pokraska.set_cost_text("—")
            self.selected["Покраска"] = paint_info

            h_mm = _izd_height_mm(izd)
            uf_ok = h_mm > 70
            self.uf.set_interaction_enabled(uf_ok, h_mm)

            uf_rates = self._pricing_cache.get("uf_rates") or fetch_uf_skleyka_prices(conn=conn)
            th_glass = parse_thickness_mm(matp.get("Толщина (мм)"))
            pm = None
            if th_glass > 0:
                pm = uf_rates["meter_by_thickness"].get(int(th_glass))
            hp = int(uf_rates.get("hinge_paste_one_rub") or 0)
            hr = int(uf_rates.get("hinge_remove_one_rub") or 0)
            self.uf.update_hinge_rate_cache(hp, hr)

            uf_pay = self.uf.get_payload()
            uf_lines = uf_pay["Строки"]

            if not uf_ok:
                self.uf.set_cost_summary("—")
                self.selected["УФ склейка"] = {
                    "Доступно": False,
                    "Высота изделия (мм)": h_mm,
                }
            else:
                self.uf.rebuild_list_texts(pm, hp, hr)
                seg_rub = 0
                hinge_rub = 0
                line_err = ""
                for it in uf_lines:
                    t = it.get("type")
                    if not t and "количество" in it and "длина" in it:
                        t = "segment"
                    if t == "hinge":
                        nk = int(it.get("наклеить") or 0)
                        sn = int(it.get("снять") or 0)
                        hinge_rub += nk * hp + sn * hr
                    elif t == "segment":
                        if pm is None:
                            line_err = "Нет тарифа за м для толщины %s мм" % th_glass
                            break
                        q = int(it.get("количество") or 0)
                        L = int(it.get("длина") or 0)
                        if q > 0 and L > 0:
                            meters = (q * L) / 1000.0
                            seg_rub += int(round(meters * float(pm)))
                has_any = bool(uf_lines)
                if line_err:
                    self.uf.set_cost_summary(line_err)
                    self.selected["УФ склейка"] = {
                        "Доступно": True,
                        "Высота изделия (мм)": h_mm,
                        "Толщина материала (мм)": th_glass,
                        "Тариф за метр (₽)": pm,
                        "Строки": uf_lines,
                        "Сумма по сегментам (₽)": seg_rub,
                        "Сумма петель (₽)": hinge_rub,
                        "Ошибка": line_err,
                    }
                elif not has_any:
                    self.uf.set_cost_summary("—")
                    self.selected["УФ склейка"] = {
                        "Доступно": True,
                        "Высота изделия (мм)": h_mm,
                        "Толщина материала (мм)": th_glass,
                        "Пусто": True,
                    }
                else:
                    one = int(seg_rub + hinge_rub)
                    q_ord = max(1, int(izd.get("Количество (шт)") or 1))
                    all_rub = one * q_ord
                    self.selected["УФ склейка"] = {
                        "Доступно": True,
                        "Высота изделия (мм)": h_mm,
                        "Толщина материала (мм)": th_glass,
                        "Тариф за метр (₽)": pm,
                        "Строки": uf_lines,
                        "Сумма по сегментам (₽)": seg_rub,
                        "Сумма петель (₽)": hinge_rub,
                        "Цена за изделие (₽)": one,
                        "Цена за все изделия (₽)": all_rub,
                    }
                    if q_ord > 1:
                        self.uf.set_cost_summary(
                            "За изд.: %s ₽ | Всего (%s изд.): %s ₽" % (one, q_ord, all_rub)
                        )
                    else:
                        self.uf.set_cost_summary("%s ₽ за изделие" % one)

            virez_rows = self._pricing_cache.get("virez_rows") or fetch_virez_price_table(conn=conn)
            p_vz = {r["category_code"]: int(r["price_rub"] or 0) for r in virez_rows}
            t_vz = {
                r["category_code"]: (r.get("title_ru") or r["category_code"])
                for r in virez_rows
            }
            self.virez.rebuild_list_texts(p_vz, t_vz)
            vit = list(self.virez.items)
            virez_one = 0
            virez_err = ""
            for it in vit:
                code = Virez_Fame._item_code(it)
                qty = Virez_Fame._item_qty(it)
                if qty <= 0:
                    continue
                pu = int(p_vz.get(code, 0) or 0)
                if pu <= 0:
                    virez_err = "Нет цены для категории «%s» в БД" % code
                    break
                virez_one += pu * qty
            q_vz = max(1, int(izd.get("Количество (шт)") or 1))
            if virez_err:
                self.virez.set_cost_summary(virez_err)
                self.selected["Вырезы"] = {
                    "Строки": vit,
                    "Ошибка": virez_err,
                }
            elif not vit:
                self.virez.set_cost_summary("—")
                self.selected["Вырезы"] = {"Пусто": True}
            else:
                virez_all = virez_one * q_vz
                self.selected["Вырезы"] = {
                    "Строки": vit,
                    "Цена за изделие (₽)": virez_one,
                    "Цена за все изделия (₽)": virez_all,
                    "Прайс": virez_rows,
                }
                if q_vz > 1:
                    self.virez.set_cost_summary(
                        "За изд.: %s ₽ | Всего (%s изд.): %s ₽"
                        % (virez_one, q_vz, virez_all)
                    )
                else:
                    self.virez.set_cost_summary("%s ₽ за изделие" % virez_one)

            fur = self.furniture.get_payload()
            fur_row_for_shelf = None
            if not fur.get("Включено"):
                self.selected["Фурнитура"] = {"Включено": False}
                self.furniture.set_pricing_cache(None, 1)
                self.furniture.set_cost_lines([])
            elif not fur.get("id"):
                self.selected["Фурнитура"] = fur
                self.furniture.set_pricing_cache(None, 1)
                self.furniture.set_cost_lines(["Выберите позицию из списка"])
            else:
                fid = int(fur["id"])
                row_fr = self.furniture.get_catalog_row_for_id(fid) or fetch_furniture_row(
                    fid, conn=conn
                )
                if not row_fr:
                    self.selected["Фурнитура"] = {**fur, "Ошибка": "Нет строки в БД"}
                    self.furniture.set_pricing_cache(None, 1)
                    self.furniture.set_cost_lines(["Нет строки в БД"])
                else:
                    fur_row_for_shelf = row_fr
                    legal = _client_is_legal_for_pricing(self.selected.get("Клиент") or {})
                    unit = int(
                        row_fr["price_legal"]
                        if legal
                        else row_fr["price_individual"]
                    )
                    qf = max(1, int(fur.get("Количество") or 1))
                    q_glass = max(1, int(izd.get("Количество (шт)") or 1))
                    is_shelf = bool(row_fr.get("is_shelf_holder"))
                    if unit <= 0:
                        self.selected["Фурнитура"] = {
                            **fur,
                            "Тип цены": "юр. лицо / ИП" if legal else "физ. лицо",
                            "Цена за единицу (₽)": 0,
                            "Изделие полка": is_shelf,
                        }
                        self.furniture.set_pricing_cache(None, 1)
                        self.furniture.set_cost_lines(
                            [
                                "Цена в БД = 0.",
                                "Запустите парсинг:",
                                "005_parse_shelf_furniture_prices.py",
                            ]
                        )
                    else:
                        one_piece = unit * qf
                        all_order = one_piece * q_glass
                        self.selected["Фурнитура"] = {
                            **fur,
                            "Тип цены": "юр. лицо / ИП" if legal else "физ. лицо",
                            "Цена за единицу (₽)": unit,
                            "За все в изделии (₽)": one_piece,
                            "За все изделия в заказе (₽)": all_order,
                            "Изделие полка": is_shelf,
                        }
                        self.furniture.set_pricing_cache(unit, q_glass)
                        lines = [
                            "1 шт.: %s ₽" % unit,
                            "За %s шт. в изделии: %s ₽" % (qf, one_piece),
                        ]
                        if q_glass > 1:
                            lines.append(
                                "За заказ (%s изд.): %s ₽" % (q_glass, all_order)
                            )
                        self.furniture.set_cost_lines(lines)

            if isinstance(self.selected.get("Параметры изделия"), dict):
                self.selected["Параметры изделия"]["Полка"] = bool(
                    fur.get("Включено")
                    and fur.get("id")
                    and fur_row_for_shelf
                    and fur_row_for_shelf.get("is_shelf_holder")
                )

            raw_pack = self._pricing_cache.get("packaging_prices") or fetch_packaging_prices(conn=conn)
            plow = {str(k).lower(): int(v) for k, v in raw_pack.items()}
            if self.packaging.is_any_selected():
                pb = compute_packaging_block(izd, self.packaging.flags(), plow)
                if pb.get("Ошибка"):
                    self.packaging.set_cost_summary(pb["Ошибка"])
                    self.selected["Упаковка"] = {"Ошибка": pb["Ошибка"]}
                elif pb:
                    self.selected["Упаковка"] = pb
                    self.packaging.set_cost_summary(
                        "Итого: %s ₽" % int(pb.get("Общая стоимость упаковки (₽)", 0) or 0)
                    )
                else:
                    self.selected["Упаковка"] = {}
                    self.packaging.set_cost_summary("—")
            else:
                self.selected["Упаковка"] = {}
                self.packaging.set_cost_summary("—")

            self._apply_selected_delivery_block_from_zamer(conn=conn)

            zblk = self.zamer.to_selected_block()
            self.selected["Замер"] = zblk
            zd = zblk.get("Данные") if isinstance(zblk.get("Данные"), dict) else {}
            if zblk.get("Активирован") and zd:
                if (bool(zd.get("Замер")) or bool(zd.get("Монтаж")) or (not bool(zd.get("Без замера")))) and self.zamer.is_configured_for_visit_price():
                    del_tab = self._pricing_cache.get("delivery_prices") or fetch_delivery_prices(conn=conn)
                    vd = zd.get("Данные выезда") or {}
                    inside = bool(vd.get("Внутри КАД", True))
                    km = vd.get("Расстояние до КАД")
                    pr = delivery_price_rub(del_tab, inside, km)
                    zd["Замер цена выезда"] = pr
                    lines = []
                    if inside:
                        pin = int(del_tab.get("В пределах КАД", 0) or 0)
                        lines.append("Тариф в пределах КАД: %s ₽" % pin)
                        lines.append("Итого выезд: %s ₽" % pr)
                    else:
                        base = int(del_tab.get("За КАД база", 0) or 0)
                        pk = int(del_tab.get("За 1 км", 0) or 0)
                        km_i = int(km or 0)
                        run = km_i * pk
                        dm = vd.get("Расстояние маршрута м")
                        if dm is not None:
                            try:
                                lines.append(
                                    "Длина маршрута: %.2f км" % (float(dm) / 1000.0)
                                )
                            except (TypeError, ValueError):
                                pass
                        lines.append("База за пределами КАД: %s ₽" % base)
                        lines.append(
                            "Пробег: %s км × %s ₽/км = %s ₽" % (km_i, pk, run)
                        )
                        lines.append("Итого выезд: %s ₽" % pr)
                    self.selected["Замер"] = {"Активирован": True, "Данные": zd}
                    self.zamer.set_visit_price_detail(pr, lines)
                else:
                    zd["Замер цена выезда"] = None
                    self.selected["Замер"] = {"Активирован": True, "Данные": zd}
                    self.zamer.set_visit_price_detail(None, [])
            else:
                self.zamer.set_visit_price_detail(None, [])

            if getattr(self, "_secondary_shown", False):
                self._refresh_block_usage_highlights()

            dopi_items = self.dopi.get_info()
            dopi_sum = sum(int(x.get("Число", 0) or 0) for x in dopi_items)
            self.selected["Дополнительно"] = {
                "Строки": dopi_items,
                "Сумма (₽)": dopi_sum,
            }
            self.selected["Доп. начисления вне калькулятора (₽)"] = dopi_sum
            mkf = _client_markup_factor(self.selected.get("Клиент") or {})
            if mkf > 1.0:
                self.selected = _apply_client_markup_to_payload(self.selected, mkf)
                # Подпись «за м²» после наценки: до apply показывались базовые mc_use, обновим с полей matp.
                matp3 = self.selected.get("Параметры материала") or {}
                pm2 = int(matp3.get("Цена за м² (без подгонки/шаблона)") or matp3.get("Цена за м²") or 0)
                s1 = int(matp3.get("Стоимость материала за изделие") or 0)
                s_all = int(matp3.get("Стоимость материала за все изделия") or 0)
                if pm2 or s1 or s_all:
                    try:
                        qn = int((izd or {}).get("Количество (шт)") or 1)
                    except (TypeError, ValueError):
                        qn = 1
                    if qn > 1:
                        txt_mk = "Материал: за м² %s ₽, за изд. %s ₽, всего %s ₽" % (pm2, s1, s_all)
                    else:
                        txt_mk = "Материал: за м² %s ₽, изделие %s ₽" % (pm2, s1)
                    if tpl_note:
                        txt_mk += tpl_note
                    self.glass.set_material_cost_label(txt_mk)

            wt = self.glass.lbl_warn.text()
            if wt.startswith("Пересчёт:"):
                self.glass.lbl_warn.setText("")

        except Exception as e:
            msg = str(e).strip()
            if len(msg) > 160:
                msg = msg[:160] + "…"
            self.glass.lbl_warn.setText("Пересчёт: %s" % msg if msg else "Пересчёт: ошибка")
        finally:
            if conn is not None:
                conn.close()


if __name__ == "__main__":
    import os
    import sys

    _here = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        # PyInstaller: пакеты и ресурсы в _MEIPASS; config.py можно положить рядом с .exe
        _me = sys._MEIPASS
        if _me not in sys.path:
            sys.path.insert(0, _me)
        _exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if _exe_dir not in sys.path:
            sys.path.insert(0, _exe_dir)
    else:
        _mirror_root = os.path.abspath(os.path.join(_here, "..", ".."))
        if _mirror_root not in sys.path:
            sys.path.insert(0, _mirror_root)

    from PyQt5.QtCore import QCoreApplication, Qt

    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)

    from window_branding import apply_app_icon, apply_window_icon, center_widget_on_screen

    app = QApplication(sys.argv)
    apply_app_icon(app)
    mainApp = MainApp({})
    apply_window_icon(mainApp)
    center_widget_on_screen(mainApp)
    mainApp.show()
    sys.exit(app.exec_())
