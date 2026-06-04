# -*- coding: utf-8 -*-
"""Окно расчёта стекло/зеркало (BLOCKS MainApp), привязка к mirror_orders только после «Завершить расчёт»."""
import json
import sys
import os

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPalette
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QMessageBox,
    QPushButton,
    QHBoxLayout,
    QTabWidget,
    QWidget,
    QTabBar,
    QStackedWidget,
)


def _question_da_net(parent, title: str, text: str) -> bool:
    """Диалог «Да» (зелёная) / «Нет» (красная) вместо Yes/No."""
    box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.NoButton, parent)
    btn_yes = box.addButton("Да", QMessageBox.YesRole)
    btn_no = box.addButton("Нет", QMessageBox.NoRole)
    box.setDefaultButton(btn_no)
    btn_yes.setStyleSheet(
        "QPushButton { background-color: #2e7d32; color: #ffffff; font-weight: 700;"
        " padding: 6px 20px; border-radius: 6px; min-width: 72px; }"
        "QPushButton:hover { background-color: #1b5e20; }"
    )
    btn_no.setStyleSheet(
        "QPushButton { background-color: #c62828; color: #ffffff; font-weight: 700;"
        " padding: 6px 20px; border-radius: 6px; min-width: 72px; }"
        "QPushButton:hover { background-color: #b71c1c; }"
    )
    box.exec_()
    return box.clickedButton() is btn_yes

from db_main import ORDER_STATUS_DRAFT
from window_branding import center_widget_on_screen
from logic.blocks_bundle import (
    merge_payload_into_bundle,
    first_product_payload_json,
    payload_for_product_id,
    parse_bundle,
    infer_order_kind_for_db,
)


def _empty_glass_payload_json() -> str:
    return json.dumps(
        {
            "Параметры изделия": {
                "Форма": "Прямоугольник",
                "Количество (шт)": 1,
            },
            "Параметры материала": {},
        },
        ensure_ascii=False,
        indent=2,
    )


class GlassMirrorCalcDialog(QDialog):
    def __init__(
        self,
        parent,
        order_id=None,
        blocks_json=None,
        *,
        product_id=None,
        append_new: bool = False,
        save_to_draft: bool = True,
        quick_client_preset: dict | None = None,
        quick_estimate_mode: bool = False,
    ):
        super().__init__(parent)
        self._order_id = int(order_id) if order_id is not None else None
        self._product_id = product_id
        self._append_new = bool(append_new)
        self._save_to_draft = bool(save_to_draft)
        self._quick_estimate_mode = bool(quick_estimate_mode)
        self._quick_client_preset = quick_client_preset if isinstance(quick_client_preset, dict) else None
        self._allow_add_product = parent is None or parent.__class__.__name__ != "FacadeOrderDialog"
        if self._order_id is not None:
            self.setWindowTitle("Стекло / зеркало — заказ № %s" % self._order_id)
        else:
            self.setWindowTitle("Стекло / зеркало — новый заказ")
        self.resize(900, 700)

        # Сырой JSON из БД (для merge v2); одна строка заказа на всё окно (без повторного get_order).
        self._existing_bundle_json = None
        order_row = None
        try:
            from db import models as db_models

            if self._order_id is not None:
                order_row = db_models.get_order(self._order_id)
                if order_row:
                    self._existing_bundle_json = order_row.get("blocks_calc_json")
        except Exception:
            order_row = None

        if append_new:
            initial = _empty_glass_payload_json()
        elif product_id and not append_new:
            pl = payload_for_product_id(self._existing_bundle_json, str(product_id))
            initial = json.dumps(pl, ensure_ascii=False, indent=2) if pl else None
        elif blocks_json is not None:
            initial = blocks_json
        else:
            initial = first_product_payload_json(self._existing_bundle_json)

        _blocks_dir = os.path.normpath(os.path.join(_mp, "BLOCKS"))
        if _blocks_dir not in sys.path:
            sys.path.insert(0, _blocks_dir)

        import xx as blocks_xx  # каталог BLOCKS добавлен в sys.path выше

        self.setObjectName("mainGlassCalcDlg")
        self._blocks_xx = blocks_xx

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        # Без родителя до вставки в ClientStrip — иначе на Windows кнопка становится отдельным окном.
        self._btn_add_product = QPushButton("Добавить изделие")
        self._btn_add_product.setObjectName("glassAddProductBtn")
        self._btn_add_product.setAttribute(Qt.WA_DontShowOnScreen, True)
        self._btn_add_product.hide()
        self._btn_add_product.clicked.connect(self._open_append_product)
        self._btn_add_product.setEnabled(bool(self._allow_add_product))
        self._btn_add_product.setStyleSheet(
            "QPushButton#glassAddProductBtn {"
            "  background-color: #c8e6c9; color: #1b5e20; font-weight: 700;"
            "  padding: 2px 8px; border: 1px solid #81c784; border-radius: 6px;"
            "  min-height: 22px; max-height: 26px; font-size: 10px;"
            "}"
            "QPushButton#glassAddProductBtn:hover { background-color: #a5d6a7; border-color: #66bb6a; }"
            "QPushButton#glassAddProductBtn:pressed { background-color: #81c784; }"
        )
        if self._order_id is None:
            self._btn_add_product.setToolTip(
                "Открыть ещё одну вкладку с новым изделием. Номер заказа появится после первого «Завершить расчёт»."
            )
        else:
            self._btn_add_product.setToolTip("Добавить ещё одно изделие в этот заказ")

        self._tab_widget = QTabWidget()
        self._tab_widget.setObjectName("glassCalcTabs")
        self._tab_widget.setTabsClosable(True)
        self._tab_widget.tabCloseRequested.connect(self._on_tab_close_requested)
        self._tab_widget.currentChanged.connect(self._place_add_product_button)
        lay.addWidget(self._tab_widget, 1)

        # Стиль до первой вкладки: иначе Qt на Windows часто оставляет белую подложку у первой страницы.
        try:
            if _blocks_dir not in sys.path:
                sys.path.insert(0, _blocks_dir)
            from calc import palette as P

            bg = P.MAIN_WINDOW_BG
            # Вкладки в той же голубой гамме, что и калькулятор / полоска клиента (не тёмные «оверлеи»).
            tab_bg = P.TILE_SURFACE
            tab_fg = P.TILE_TEXT
            tab_border = P.CONTROL_BORDER
            tab_sel = P.GLASS_TILE_BORDER_READY
            self.setStyleSheet(
                "QDialog#mainGlassCalcDlg { background-color: %s; }"
                "QDialog#mainGlassCalcDlg QTabWidget#glassCalcTabs::pane { border: none; background-color: %s; }"
                "QDialog#mainGlassCalcDlg QWidget#glassCalcTabPage { background-color: %s; }"
                "QTabWidget#glassCalcTabs QTabBar { background-color: %s; }"
                "QTabWidget#glassCalcTabs QTabBar::tab { background: %s; color: %s; "
                "padding: 5px 14px; border-top-left-radius: 4px; border-top-right-radius: 4px; "
                "border: 1px solid %s; }"
                "QTabWidget#glassCalcTabs QTabBar::tab:selected { background: %s; color: #ffffff; "
                "border: 1px solid %s; }"
                "QTabWidget#glassCalcTabs QTabBar::tab:!selected:hover { background: %s; }"
                % (
                    bg,
                    bg,
                    bg,
                    bg,
                    tab_bg,
                    tab_fg,
                    tab_border,
                    tab_sel,
                    tab_sel,
                    P.TILE_HEADER_BG,
                )
            )
            c = QColor(bg)
            if c.isValid():
                tw_pal = self._tab_widget.palette()
                tw_pal.setColor(QPalette.Window, c)
                self._tab_widget.setPalette(tw_pal)
                self._tab_widget.setAutoFillBackground(True)
        except Exception:
            pass

        self._add_calc_tab(
            initial,
            str(product_id) if product_id else None,
            bool(append_new),
            tab_title="Изделие 1",
        )
        self._sync_tab_bar_visibility()
        self._apply_tab_close_buttons()
        self._apply_order_client_to_first_tab(order_row)
        self._apply_quick_client_preset()
        self._wire_primary_client_strip_sync()
        self._place_add_product_button(self._tab_widget.currentIndex())
        self._fit_dialog_to_calc()

    def _fit_dialog_to_calc(self) -> None:
        """Подогнать диалог под сетку калькулятора без лишних полей."""
        app = self._current_main_app()
        if app is not None:
            app._apply_compact_window_size()
        try:
            self.layout().activate()
            tab_extra = 0
            try:
                tab_extra = self._tab_widget.tabBar().sizeHint().height()
            except Exception:
                pass
            if app is not None:
                w = app.width() + 2
                h = app.height() + tab_extra + 2
            else:
                w = self.layout().sizeHint().width() + 2
                h = self.layout().sizeHint().height() + 2
            self.resize(max(400, w), max(300, h))
        except Exception:
            pass
        center_widget_on_screen(self)

    @property
    def _app(self):
        """Текущая вкладка: совместимость со старым кодом, обращавшимся к dialog._app."""
        return self._current_main_app()

    def _current_main_app(self):
        w = self._tab_widget.currentWidget()
        return getattr(w, "_glass_main_app", None) if w else None

    def _main_app_at(self, index: int):
        w = self._tab_widget.widget(index)
        return getattr(w, "_glass_main_app", None) if w else None

    def _client_payload_from_first_tab(self) -> dict:
        app0 = self._main_app_at(0)
        if app0 and app0.client_strip:
            pl = app0.client_strip.get_payload()
            return dict(pl) if isinstance(pl, dict) else {}
        return {}

    def _apply_order_client_to_first_tab(self, order_row) -> None:
        if self._order_id is None:
            return
        try:
            from db import models as db_models

            orow = order_row
            if orow is None:
                orow = db_models.get_order(int(self._order_id))
            if not orow:
                return
            qcid = orow.get("quick_client_id")
            try:
                qcid = int(qcid) if qcid is not None else None
            except (TypeError, ValueError):
                qcid = None
            cid = orow.get("client_id")
            try:
                cid = int(cid) if cid is not None else None
            except (TypeError, ValueError):
                cid = None
            if qcid is None and cid is None:
                return
            cname = (orow.get("client_name") or "").strip()
            if not cname and qcid is not None:
                rqc = db_models.get_mirror_quick_client_by_id(qcid) or {}
                cname = (rqc.get("name") or "").strip()
            if not cname and cid is not None:
                rc = db_models.get_client_by_id(cid) or {}
                cname = (rc.get("name") or "").strip()
            app0 = self._main_app_at(0)
            if app0:
                app0.client_strip.set_payload(
                    {"Имя": cname, "id": cid, "quick_client_id": qcid}
                )
        except Exception:
            pass

    def _apply_quick_client_preset(self) -> None:
        if not self._quick_client_preset or self._quick_estimate_mode:
            return
        try:
            cid = self._quick_client_preset.get("client_id")
            cname = (self._quick_client_preset.get("client_name") or "").strip()
            cid = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid = None
        if not (cid and cname):
            return
        pl = {"Имя": cname, "id": cid}
        app0 = self._main_app_at(0)
        if app0:
            app0.client_strip.set_payload(pl)
            app0.selected["Клиент"] = dict(pl)
            app0.client_strip.edit.setReadOnly(True)
            app0.client_strip.btn_new.setVisible(False)

    def _place_add_product_button(self, tab_index: int) -> None:
        """Кнопка «Добавить изделие» — справа от блока клиента на активной вкладке."""
        if not self._allow_add_product:
            self._btn_add_product.hide()
            return
        app = self._main_app_at(tab_index)
        if app and getattr(app, "client_strip", None):
            app.client_strip.set_trailing_widget(self._btn_add_product)
            self._btn_add_product.setVisible(True)
        else:
            self._btn_add_product.hide()

    def _wire_primary_client_strip_sync(self):
        app0 = self._main_app_at(0)
        if not app0 or not getattr(app0, "client_strip", None):
            return
        app0.client_strip.clientIdentityChanged.connect(self._sync_secondary_tabs_client_from_primary)

    def _sync_secondary_tabs_client_from_primary(self):
        if self._tab_widget.count() <= 1:
            return
        pl = self._client_payload_from_first_tab()
        if not pl:
            return
        for i in range(1, self._tab_widget.count()):
            app = self._main_app_at(i)
            if app and app.client_strip:
                app.client_strip.set_payload(dict(pl))
                app.selected["Клиент"] = dict(pl)

    def _apply_secondary_tab_client_lock(self, app):
        if not app or not getattr(app, "client_strip", None):
            return
        app.client_strip.edit.setReadOnly(True)
        app.client_strip.btn_new.setVisible(False)

    def _add_calc_tab(self, initial_blocks_json, product_id: str | None, append_new: bool, tab_title: str):
        blocks_xx = self._blocks_xx

        def finish_cb(_json_text: str):
            self._persist_all_tabs(close_dialog=True)

        container = QWidget()
        container.setObjectName("glassCalcTabPage")
        container.setAttribute(Qt.WA_StyledBackground, True)
        try:
            from calc import palette as P

            c = QColor(P.MAIN_WINDOW_BG)
            if c.isValid():
                pal = container.palette()
                pal.setColor(QPalette.Window, c)
                container.setPalette(pal)
                container.setAutoFillBackground(True)
        except Exception:
            pass
        lay_tab = QVBoxLayout(container)
        lay_tab.setContentsMargins(0, 0, 0, 0)
        lay_tab.setSpacing(0)

        app = blocks_xx.MainApp(
            linked_order_id=self._order_id,
            initial_blocks_json=initial_blocks_json,
            linked_finish_cb=finish_cb,
            linked_bundle_save_cb=lambda _a: self._persist_all_tabs(close_dialog=False),
            quick_estimate_mode=self._quick_estimate_mode,
            show_glass_additional_button=False,
            embedded_in_dialog=True,
        )
        container._glass_main_app = app
        container._tab_product_id = product_id
        container._tab_append_new = append_new
        lay_tab.addWidget(app, 0, Qt.AlignTop | Qt.AlignLeft)

        n = self._tab_widget.count()
        if n > 0:
            pl = self._client_payload_from_first_tab()
            if pl:
                app.client_strip.set_payload(pl)
                app.selected["Клиент"] = dict(pl)

        self._tab_widget.addTab(container, tab_title)
        if self._tab_widget.count() >= 2:
            self._apply_secondary_tab_client_lock(app)
        self._sync_tab_bar_visibility()
        self._apply_tab_close_buttons()
        self._tint_tabwidget_stack()
        if self._tab_widget.currentIndex() == n:
            self._place_add_product_button(n)
        self._fit_dialog_to_calc()

    def _tint_tabwidget_stack(self):
        """У QTabWidget внутренний QStackedWidget иногда остаётся белым только у первой вкладки (Windows)."""
        try:
            from calc import palette as P

            c = QColor(P.MAIN_WINDOW_BG)
            if not c.isValid():
                return
            for sw in self._tab_widget.findChildren(QStackedWidget):
                pal = sw.palette()
                pal.setColor(QPalette.Window, c)
                sw.setPalette(pal)
                sw.setAutoFillBackground(True)
        except Exception:
            pass

    def _sync_tab_bar_visibility(self):
        self._tab_widget.tabBar().setVisible(self._tab_widget.count() >= 2)

    def _apply_tab_close_buttons(self):
        """У первой вкладки нет «крестика»; у остальных — стандартное закрытие QTabWidget."""
        bar = self._tab_widget.tabBar()
        if self._tab_widget.count() == 0:
            return
        bar.setTabButton(0, QTabBar.RightSide, None)

    def _on_tab_close_requested(self, index: int):
        if index <= 0:
            return
        if self._tab_widget.count() <= 1:
            return
        if not _question_da_net(
            self,
            "Закрыть вкладку",
            "Закрыть это изделие без сохранения просчёта в заказ?",
        ):
            return
        w = self._tab_widget.widget(index)
        self._tab_widget.removeTab(index)
        if w is not None:
            w.deleteLater()
        self._sync_tab_bar_visibility()
        self._apply_tab_close_buttons()

    def _open_append_product(self):
        if not self._allow_add_product:
            return
        n = self._tab_widget.count()
        self._add_calc_tab(
            _empty_glass_payload_json(),
            None,
            True,
            tab_title="Изделие %d" % (n + 1),
        )
        self._tab_widget.setCurrentIndex(self._tab_widget.count() - 1)

    def _persist_all_tabs(self, close_dialog: bool) -> bool:
        """Пересчёт и merge всех вкладок в один blocks_calc_json; без модальных «успех»-окон."""
        from calc.serialize import order_payload_to_json
        from db import models as db_models

        app0 = self._main_app_at(0)
        if app0 is None:
            return False
        cli = app0.client_strip.get_payload() if app0.client_strip else {}
        if not (bool(cli.get("id")) or bool(cli.get("quick_client_id"))):
            QMessageBox.warning(
                self,
                "Клиент",
                "Выберите клиента на первой вкладке (справочник или быстрый просчёт).",
            )
            return False
        pl_cli = dict(cli) if isinstance(cli, dict) else {}

        rows = []
        n = self._tab_widget.count()
        for i in range(n):
            app = self._main_app_at(i)
            w = self._tab_widget.widget(i)
            if app is None or w is None:
                continue
            app.client_strip.set_payload(pl_cli)
            app.selected["Клиент"] = dict(pl_cli)
            if not app.glass.is_ready_for_pricing():
                QMessageBox.warning(
                    self,
                    "Расчёт",
                    "Вкладка «%s»: укажите материал, вариант, толщину и размеры (периметр > 0). "
                    "Для сложной фигуры загрузите макет."
                    % self._tab_widget.tabText(i),
                )
                return False
            app._recalc_debounce.stop()
            try:
                app._recalculate_impl(True)
            except Exception as e:
                QMessageBox.warning(self, "Расчёт", str(e))
                return False
            try:
                new_pl = json.loads(order_payload_to_json(app.selected))
            except Exception as e:
                QMessageBox.warning(self, "Расчёт", "Сборка данных: %s" % e)
                return False
            if not isinstance(new_pl, dict):
                QMessageBox.warning(self, "Расчёт", "Некорректный JSON просчёта.")
                return False
            tab_pid = getattr(w, "_tab_product_id", None)
            tab_append = getattr(w, "_tab_append_new", False)
            rows.append((new_pl, tab_pid, tab_append))

        if not rows:
            return False

        client_name = str(pl_cli.get("Имя") or "").strip()
        client_id = pl_cli.get("id")
        try:
            client_id = int(client_id) if client_id is not None else None
        except (TypeError, ValueError):
            client_id = None
        quick_client_id = pl_cli.get("quick_client_id")
        try:
            quick_client_id = int(quick_client_id) if quick_client_id is not None else None
        except (TypeError, ValueError):
            quick_client_id = None

        try:
            oid = self._order_id
            if oid is None:
                cb = db_models.mirror_order_created_by_from_qt_parent(self.parent())
                oid = db_models.create_order(
                    client_name,
                    client_id=client_id,
                    quick_client_id=quick_client_id,
                    notes="MAIN_PROJECT: стекло / зеркало",
                    order_kind=db_models.ORDER_KIND_GLASS_MIRROR,
                    created_by_user_id=cb[0],
                    created_by_login=cb[1] or None,
                    created_by_role=cb[2] or None,
                )
                self._order_id = int(oid)
                self.setWindowTitle("Стекло / зеркало — заказ № %s" % self._order_id)
                self._btn_add_product.setToolTip("")
                for j in range(self._tab_widget.count()):
                    aa = self._main_app_at(j)
                    if aa is not None:
                        aa._linked_order_id = self._order_id

            merged = self._existing_bundle_json
            for new_pl, tab_pid, tab_append in rows:
                merged = merge_payload_into_bundle(
                    merged,
                    str(tab_pid) if tab_pid else None,
                    new_pl,
                    bool(tab_append),
                )
            db_models.update_order_blocks_calc(self._order_id, merged)
            self._existing_bundle_json = merged
            try:
                _, prods = parse_bundle(merged)
                db_models.update_order_kind(self._order_id, infer_order_kind_for_db(prods))
            except Exception:
                pass
            if self._save_to_draft:
                db_models.set_order_status(self._order_id, ORDER_STATUS_DRAFT)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка сохранения", str(e))
            return False

        if close_dialog:
            self.accept()
        return True