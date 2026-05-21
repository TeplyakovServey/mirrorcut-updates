"""Main window: Create cut button, status board (last 10), Show more."""
import sys
import os
import subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app_paths import get_base_dir

def _app_dir():
    return get_base_dir()

def _open_file(path):
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QMessageBox,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont

from window_branding import apply_window_icon
from ui.status_board import StatusBoardWidget, OrderDetailDialog, FullListDialog
from db import models


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ARSENAL & TEPLYAKOV — Крой стекольной продукции")
        self.setMinimumSize(700, 600)
        self.setStyleSheet("""
            QMainWindow { background-color: #E6F2FF; }
            QPushButton {
                background-color: #4682B4;
                color: white;
                border-radius: 6px;
                padding: 10px 20px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #5A9BD5; }
            QPushButton:pressed { background-color: #3A6B94; }
        """)
        central = QWidget()
        self.setCentralWidget(central)
        apply_window_icon(self)

        layout = QVBoxLayout(central)
        layout.setSpacing(15)

        self.btn_create_cut = QPushButton("Создать рез")
        self.btn_create_cut.setMinimumHeight(44)
        self.btn_create_cut.clicked.connect(self._on_create_cut)
        layout.addWidget(self.btn_create_cut)

        self.board = StatusBoardWidget(self)
        self.board.order_clicked.connect(self._on_order_clicked)
        layout.addWidget(self.board)

        self.btn_show_more = QPushButton("Показать ещё")
        self.btn_show_more.clicked.connect(self._on_show_more)
        layout.addWidget(self.btn_show_more)

        search_row = QHBoxLayout()
        self.btn_search = QPushButton("Поиск")
        self.btn_search.setMinimumHeight(40)
        self.btn_search.setToolTip("Поиск по клиенту, по номеру остатка или по K-номеру (K1, K2…)")
        self.btn_search.clicked.connect(self._on_search)
        search_row.addWidget(self.btn_search)
        search_row.addStretch()
        layout.addLayout(search_row)

        btn_row_bottom = QHBoxLayout()
        btn_row_bottom.setSpacing(12)
        self.btn_ready_orders = QPushButton("Готовые заказы")
        self.btn_ready_orders.setMinimumHeight(44)
        self.btn_ready_orders.setToolTip("Список выполненных заказов: клиент, листы, выкрои, площадь м²")
        self.btn_ready_orders.clicked.connect(self._on_ready_orders)
        btn_row_bottom.addWidget(self.btn_ready_orders)
        self.btn_warehouse = QPushButton("Склад")
        self.btn_warehouse.setMinimumHeight(44)
        self.btn_warehouse.clicked.connect(self._on_warehouse)
        btn_row_bottom.addWidget(self.btn_warehouse)
        self.btn_training = QPushButton("Обучение")
        self.btn_training.setMinimumHeight(44)
        self.btn_training.setToolTip("Обучение раскладке: лист реального размера, случайные детали, расстановка с привязкой. Образцы сохраняются в общую базу.")
        self.btn_training.clicked.connect(self._on_training)
        btn_row_bottom.addWidget(self.btn_training)
        self.btn_table_editor = QPushButton("Редактор таблиц")
        self.btn_table_editor.setMinimumHeight(44)
        self.btn_table_editor.clicked.connect(self._on_table_editor)
        btn_row_bottom.addWidget(self.btn_table_editor)
        self.btn_settings = QPushButton("Настройки")
        self.btn_settings.setMinimumHeight(44)
        self.btn_settings.clicked.connect(self._on_settings)
        btn_row_bottom.addWidget(self.btn_settings)
        btn_row_bottom.addStretch()
        layout.addLayout(btn_row_bottom)

        self._refresh_orders()

    def _on_settings(self):
        from ui.settings_dialog import SettingsDialog
        SettingsDialog(self).exec_()

    def _refresh_orders(self):
        orders = models.get_orders_recent(limit=50)
        # Sort: in_progress first, then by date desc
        orders = sorted(orders, key=lambda o: (0 if o.get('status') == 'in_progress' else 1, -(o.get('id') or 0)))
        self.board.set_orders(orders)

    def _on_create_cut(self):
        from ui.create_cut_dialog import CreateCutDialog
        d = CreateCutDialog(self)
        if d.exec_() == d.Accepted:
            self._refresh_orders()

    def _on_order_clicked(self, order_data):
        d = OrderDetailDialog(order_data, self)
        d.open_scheme_requested.connect(self._open_scheme_with_layouts)
        d.btn_pdf.clicked.connect(lambda: self._open_pdf(order_data))
        d.btn_label.clicked.connect(lambda: self._open_label(order_data))
        d.exec_()
        self._refresh_orders()

    def _open_scheme_with_layouts(self, order_data, layout_dicts):
        if not layout_dicts:
            QMessageBox.information(self, "Схема", "Нет данных раскладки.")
            return
        from ui.cutting_result_dialog import CuttingResultDialog
        d = CuttingResultDialog(layout_dicts, order_data, self)
        try:
            _onum = order_data.get("id") or order_data.get("order_id")
            if _onum is not None:
                d.setWindowTitle(
                    "Схема раскроя — все листы заказа (сохранённый раскрой) — заказ №%s" % _onum
                )
        except Exception:
            pass
        d.btn_pdf.clicked.connect(lambda: self._open_pdf(order_data, layouts_getter=lambda: d.layouts))
        d.print_labels_requested.connect(lambda: self._open_label(order_data))
        d.layout_updated.connect(self._refresh_orders)
        d.exec_()

    def _open_pdf(self, order_data, layouts_getter=None):
        from db.models import get_cut_results
        from logic.pdf_export import generate_cutting_pdf
        if layouts_getter and callable(layouts_getter):
            layouts = list(layouts_getter())
        else:
            results = get_cut_results(order_data['id'])
            layouts = []
            for r in results:
                lay = r.get('layout')
                if isinstance(lay, dict) and (lay.get('pieces') or lay.get('sheet_width')):
                    layouts.append(lay)
        # Filter to valid layout dicts if we got them from getter
        layouts = [lay for lay in layouts if isinstance(lay, dict) and (lay.get('pieces') or lay.get('sheet_width'))]
        if not layouts:
            QMessageBox.warning(self, "PDF", "Нет данных раскроя для экспорта.")
            return
        order_info = {
            'order_id': order_data.get('id'),
            'client_name': order_data.get('client_name'),
            'created_at': order_data.get('created_at'),
        }
        if not (order_info.get('client_name') or '').strip() and order_info.get('order_id'):
            from db import models
            o = models.get_order_for_labels(order_info['order_id'])
            if o:
                order_info['client_name'] = o.get('client_name')
                if order_info.get('created_at') is None:
                    order_info['created_at'] = o.get('created_at')
        try:
            from user_settings import get_models_dir
            folder = get_models_dir()
        except Exception:
            folder = None
        if not folder:
            folder = _app_dir()
        path = os.path.join(folder, "Карты_раскроя_заказ_%s.pdf" % (order_data.get('id') or "0"))
        try:
            generate_cutting_pdf(layouts, order_info, path)
            _open_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _open_label(self, order_data):
        from db import models
        order_id = order_data.get('id')
        order_fresh = models.get_order_for_labels(order_id) if order_id else None
        if not order_fresh or order_fresh.get('status') != 'paid':
            QMessageBox.information(
                self, "Этикетки",
                "Печать этикеток и списание материалов доступны только после статуса «Оплачен»."
            )
            return
        from db.models import get_remnant_ids_by_order_id, get_cut_results
        from datetime import datetime
        from logic.labels import generate_labels_pdf_multi
        try:
            from user_settings import get_labels_dir
            out_dir = get_labels_dir()
        except Exception:
            out_dir = None
        if not out_dir:
            out_dir = _app_dir()
        try:
            os.makedirs(out_dir, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Ошибка", "Не удалось создать папку: %s" % e)
            return
        try:
            if order_id and get_cut_results(order_id):
                models.sync_missing_remnant_records_for_order(int(order_id))
        except Exception:
            pass
        remnants = []
        for rid in get_remnant_ids_by_order_id(order_data['id']) or []:
            rem = models.get_remnant_by_id(rid)
            if not rem:
                continue
            label_no = rem.get('label_number')
            if label_no is None:
                label_no = models.ensure_remnant_label_number(rem['id'])
            rem['label_number'] = label_no
            remnants.append(rem)
        pieces = []
        order_for_labels = order_fresh  # уже загружен при проверке статуса
        k_number = (order_for_labels or order_data).get('k_number') if (order_for_labels or order_data) else order_data.get('k_number')
        order_date = (order_for_labels or order_data).get('accepted_at') or (order_for_labels or order_data).get('created_at') if (order_for_labels or order_data) else order_data.get('accepted_at') or order_data.get('created_at')
        # Имя клиента: для выполненного заказа — из архива раскроя (сохранено при выполнении, как на схеме); иначе из заказа/справочника
        if order_for_labels and order_for_labels.get('status') == 'completed' and order_id:
            client_name = (models.get_order_client_name_from_archive(order_id) or '').strip()
        else:
            client_name = (order_for_labels.get('client_name') or '') if order_for_labels else ''
            client_name = (client_name or '').strip()
        if not client_name and order_id:
            client_name = (models.get_order_client_name(order_id) or '').strip()
        if not client_name and order_id:
            cl_id = (order_for_labels or order_data).get('client_id')
            if cl_id:
                cl = models.get_client_by_id(cl_id)
                if cl:
                    client_name = (models._client_display_name(cl) or cl.get('name') or '').strip()
        if not client_name:
            client_name = (order_data.get('client_name') or '').strip()
        piece_number = 0
        for r in get_cut_results(order_id or order_data['id']) or []:
            lay = r.get('layout')
            if isinstance(lay, dict):
                mat = lay.get('material') or ''
                thick = lay.get('thickness_mm')
                for p in lay.get('pieces') or []:
                    piece_number += 1
                    piece = dict(p)
                    if not piece.get('name') and not piece.get('material'):
                        piece['name'] = mat
                    piece['piece_number'] = piece_number
                    if k_number is not None:
                        piece['k_number'] = k_number
                    piece['client_name'] = (client_name or '').strip()
                    piece['order_date'] = order_date
                    if thick is not None:
                        piece['thickness_mm'] = thick
                    pieces.append(piece)
        if not remnants and not pieces:
            QMessageBox.information(
                self,
                "Этикетки",
                "Нет данных для этикеток: сохраните раскрой листов в заказе (деловые остатки заводятся на склад при сохранении). "
                "Изделия — из сохранённой схемы; после «Выполнено» остатки также фиксируются в архиве реза.",
            )
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_name = "Этикетки_заказ_%s_%s.pdf" % (order_data.get('id', ''), stamp)
        filepath = os.path.join(out_dir, unique_name)
        try:
            generate_labels_pdf_multi(remnants, pieces, filepath)
        except Exception as e:
            QMessageBox.critical(self, "Этикетки", "Ошибка при сохранении PDF: %s" % e)
            return
        self._refresh_orders()
        _open_file(filepath)

    def _on_show_more(self):
        orders = models.get_orders_all()
        d = FullListDialog(orders, self)
        d.order_clicked.connect(self._on_order_clicked)
        d.exec_()
        self._refresh_orders()

    def _on_training(self):
        from ui.training_dialog import TrainingDialog
        TrainingDialog(self).exec_()

    def _on_search(self):
        from ui.search_dialog import SearchDialog
        SearchDialog(self).exec_()

    def _on_ready_orders(self):
        from ui.ready_orders_dialog import ReadyOrdersDialog
        ReadyOrdersDialog(self).exec_()

    def _on_warehouse(self):
        from ui.warehouse_dialog import WarehouseDialog
        WarehouseDialog(self).exec_()

    def _on_table_editor(self):
        from ui.table_editor import TableEditorDialog
        TableEditorDialog(self).exec_()
