"""Диалог схемы раскроя: канвас, PDF, последовательность резов, печать этикеток, изменение макета вручную."""
import copy

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QComboBox, QLabel, QWidget, QMessageBox,
    QListWidget, QListWidgetItem, QDialogButtonBox, QGroupBox, QGridLayout, QFrame,
    QScrollArea, QTabWidget, QSizePolicy,
)
from PyQt5.QtCore import QEvent, Qt, pyqtSignal, QTimer
from ui.cutting_canvas import CuttingCanvas


def _summary_for_result(result, index):
    """Краткое описание варианта раскроя для списка выбора."""
    layouts = result.get('layouts') or []
    n_sheets = len(layouts)
    total_remnants = 0
    max_remnant_m2 = 0
    sum_business_m2 = 0.0
    total_waste_m2 = 0
    for lay in layouts:
        for r in lay.get('business_rects') or []:
            total_remnants += 1
            a = (r.get('w') or 0) * (r.get('h') or 0)
            sum_business_m2 += a
            if a > max_remnant_m2:
                max_remnant_m2 = a
        for r in lay.get('waste_rects') or []:
            total_waste_m2 += (r.get('w') or 0) * (r.get('h') or 0)
    max_remnant_m2 /= 1e6
    sum_business_m2 /= 1e6
    total_waste_m2 /= 1e6
    return (
        "Вариант %d: %d лист(ов), %d деловых остатков, макс. %.2f м², сумма деловых %.2f м², отходы %.2f м²"
        % (index + 1, n_sheets, total_remnants, max_remnant_m2, sum_business_m2, total_waste_m2)
    )


def _short_label_for_result(result):
    """Подпись под миниатюрой: число деловых остатков, макс. и суммарная площадь деловых (м²)."""
    layouts = result.get('layouts') or []
    total_remnants = 0
    max_remnant_m2 = 0
    sum_business_m2 = 0.0
    for lay in layouts:
        for r in lay.get('business_rects') or []:
            total_remnants += 1
            a = (r.get('w') or 0) * (r.get('h') or 0)
            sum_business_m2 += a
            if a > max_remnant_m2:
                max_remnant_m2 = a
    max_remnant_m2 /= 1e6
    sum_business_m2 /= 1e6
    return (
        "Остатков: %d · макс. %.2f м² · сумма деловых %.2f м²"
        % (total_remnants, max_remnant_m2, sum_business_m2)
    )


class VariantCell(QFrame):
    """Ячейка 300×300 с миниатюрой раскроя и подписью; клик по ячейке выбирает вариант."""
    cell_clicked = pyqtSignal(int)

    def __init__(self, result, index, parent=None, dark_theme=False):
        super().__init__(parent)
        self._result = result
        self._index = index
        self._selected = False
        self._dark_theme = bool(dark_theme)
        self.setObjectName("variantCell")
        self.setFixedSize(302, 340)
        self.setFrameStyle(QFrame.Box | QFrame.Plain)
        self.setLineWidth(2)
        self._update_border()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        self.canvas = CuttingCanvas(parent=self, fit_to_view=True, preview_mode=True)
        self.canvas.set_layouts(result.get('layouts') or [])
        self.canvas.setFixedSize(300, 300)
        if self._dark_theme:
            self.canvas.setStyleSheet("background-color: #000000;")
        layout.addWidget(self.canvas)
        self.label = QLabel(_short_label_for_result(result))
        self.label.setWordWrap(True)
        if self._dark_theme:
            self.label.setStyleSheet("color: #C8D9EE; background: transparent; font-size: 12px;")
        layout.addWidget(self.label)

    def _update_border(self):
        if self._dark_theme:
            if self._selected:
                self.setStyleSheet(
                    "QFrame#variantCell { border: 3px solid #4A76A8; background-color: #111111; border-radius: 6px; }"
                )
            else:
                self.setStyleSheet(
                    "QFrame#variantCell { border: 2px solid #444444; background-color: #0a0a0a; border-radius: 6px; }"
                )
            return
        if self._selected:
            self.setStyleSheet("QFrame#variantCell { border: 3px solid #4682B4; background-color: #E6F2FF; border-radius: 6px; }")
        else:
            self.setStyleSheet("QFrame#variantCell { border: 2px solid #ccc; background-color: #f8f8f8; border-radius: 6px; }")

    def set_selected(self, selected):
        self._selected = bool(selected)
        self._update_border()

    def mousePressEvent(self, event):
        if event.button() == 1:
            self.cell_clicked.emit(self._index)
        super().mousePressEvent(event)


class ChooseVariantDialog(QDialog):
    """Выбор одного из до 4 вариантов раскроя: сетка 2×2 с миниатюрами и подписями."""

    def __init__(self, variant_results, parent=None, dark_theme=False):
        super().__init__(parent)
        self.setWindowTitle("Выберите вариант раскроя")
        self._results = list(variant_results)[:4]
        self._chosen = None
        self._selected_index = 0
        self._cells = []
        self._dark_theme = bool(dark_theme)
        if self._dark_theme:
            self.setStyleSheet(
                """
                QDialog { background-color: #000000; }
                QWidget#variantGridHost {
                    background-color: #000000;
                    border: none;
                }
                QPushButton#variantBtnSelect {
                    background-color: #1E5935;
                    color: #ffffff;
                    border: none;
                    border-radius: 8px;
                    padding: 3px 14px;
                    font-size: 12px;
                    font-weight: 600;
                    min-height: 22px;
                    max-height: 26px;
                }
                QPushButton#variantBtnSelect:hover { background-color: #277548; }
                QPushButton#variantBtnSelect:pressed { background-color: #164228; }
                QPushButton#variantBtnClose {
                    background-color: #C84040;
                    color: #ffffff;
                    border: none;
                    border-radius: 8px;
                    padding: 3px 14px;
                    font-size: 12px;
                    font-weight: 600;
                    min-height: 22px;
                    max-height: 26px;
                }
                QPushButton#variantBtnClose:hover { background-color: #D85A5A; }
                QPushButton#variantBtnClose:pressed { background-color: #A83535; }
                """
            )
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        if self._dark_theme:
            host = QWidget()
            host.setObjectName("variantGridHost")
            host_lay = QVBoxLayout(host)
            host_lay.setContentsMargins(0, 0, 0, 0)
            grid = QGridLayout()
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(8)
            host_lay.addLayout(grid)
            layout.addWidget(host, 1)
        else:
            grp = QGroupBox("До четырёх вариантов компоновки и резов. Клик по миниатюре — выбор.")
            layout.addWidget(grp)
            grid = QGridLayout(grp)
        for i in range(min(4, len(self._results))):
            cell = VariantCell(self._results[i], i, self, dark_theme=self._dark_theme)
            cell.cell_clicked.connect(self._on_cell_clicked)
            self._cells.append(cell)
            grid.addWidget(cell, i // 2, i % 2)
        self._update_selection()
        if self._dark_theme:
            row = QHBoxLayout()
            row.addStretch(1)
            btn_sel = QPushButton("выбрать")
            btn_sel.setObjectName("variantBtnSelect")
            btn_sel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            btn_sel.setCursor(Qt.PointingHandCursor)
            btn_sel.clicked.connect(self._accept)
            btn_close = QPushButton("закрыть")
            btn_close.setObjectName("variantBtnClose")
            btn_close.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            btn_close.setCursor(Qt.PointingHandCursor)
            btn_close.clicked.connect(self.reject)
            row.addWidget(btn_sel)
            row.addWidget(btn_close)
            layout.addLayout(row)
        else:
            btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            btns.accepted.connect(self._accept)
            btns.rejected.connect(self.reject)
            layout.addWidget(btns)

    def _on_cell_clicked(self, index):
        self._selected_index = index
        self._update_selection()

    def _update_selection(self):
        for i, c in enumerate(self._cells):
            c.set_selected(i == self._selected_index)

    def _accept(self):
        if 0 <= self._selected_index < len(self._results):
            self._chosen = self._results[self._selected_index]
        self.accept()

    def get_chosen(self):
        return self._chosen


class ChooseSheetForVariantsDialog(QDialog):
    """Выбор листа для вариантов раскроя: показываем номер листа, материал и размер."""
    def __init__(self, layouts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выберите лист для вариантов раскроя")
        self.setMinimumSize(400, 280)
        self._layouts = list(layouts) if layouts else []
        self._selected_index = 0
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Для какого листа показать варианты раскладки? Изменится только выбранный лист."))
        self.list_widget = QListWidget()
        for i, lay in enumerate(self._layouts):
            mat = lay.get('material') or '—'
            w = lay.get('sheet_width') or 0
            h = lay.get('sheet_height') or 0
            thick = lay.get('thickness_mm', 4)
            item = QListWidgetItem("Лист %d: %s, %d мм, %d × %d мм" % (i + 1, mat, thick, w, h))
            self.list_widget.addItem(item)
        self.list_widget.setCurrentRow(0)
        layout.addWidget(self.list_widget)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_selected_sheet_index(self):
        return self.list_widget.currentRow()


class CuttingResultDialog(QDialog):
    print_labels_requested = pyqtSignal()
    layout_updated = pyqtSignal()  # после сохранения ручного изменения — обновить макеты у родителя

    def __init__(
        self,
        layouts,
        order_info=None,
        parent=None,
        results_payload=None,
        preview_mode=False,
        view_only=False,
    ):
        super().__init__(parent)
        self.order_info = order_info or {}
        self._results_payload = list(results_payload or [])
        self._view_only = bool(view_only)
        # view_only — только запрет правок; схему рисуем как в рабочем режиме (preview_mode=False),
        # иначе канвас в «превью» не подписывает листы и пустой зарезервированный лист выглядит как баг.
        self._preview_mode = bool(preview_mode)
        merged = list(layouts) if layouts else []
        oid = self.order_info.get('id') or self.order_info.get('order_id')
        if oid and not self._preview_mode:
            try:
                from db import models

                rows = models.get_cut_results(int(oid))
                if rows:
                    db_layouts = []
                    for r in rows:
                        lay = r.get('layout')
                        if isinstance(lay, dict):
                            db_layouts.append(lay)
                    if db_layouts:
                        merged = db_layouts
            except Exception:
                pass
        self.layouts = merged
        if self._preview_mode:
            title = "Схема раскроя (просмотр)"
        else:
            title = "Схема раскроя — Заказ #%s" % self.order_info.get('id', '')
            if self.order_info.get('combined_with'):
                title += " (объединён с заказом #%s)" % self.order_info['combined_with']
        self.setWindowTitle(title)
        self.setMinimumSize(800, 600)
        layout = QVBoxLayout(self)
        self._tab_canvases = []
        self._tab_scrolls = []
        self.tabs = QTabWidget(self)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)
        self.canvas = None
        self.scroll = None
        self._rebuild_tabs()
        try:
            from db import models
            oid = self.order_info.get('id') or self.order_info.get('order_id')
            if oid:
                nums = models.get_remnant_display_numbers_by_order_id(oid)
                if nums:
                    for canv in self._tab_canvases:
                        canv.set_remnant_display_numbers(nums)
        except Exception:
            pass
        btn_row = QHBoxLayout()
        self.btn_pdf = QPushButton("Сохранить PDF")
        self.btn_other_options = QPushButton("Другие варианты раскроя")
        self.btn_other_options.setToolTip("Показать 3–4 варианта раскроя (разные резы по осям X/Y, ориентация листа). Выберите нужный и примените.")
        self.btn_other_options.clicked.connect(self._recalculate_other_options)
        self.btn_sequence = QPushButton("Последовательность резов")
        self.btn_sequence.clicked.connect(self._open_sequence)
        self.btn_label = QPushButton("Печать этикеток")
        if self.order_info.get('status') != 'paid':
            self.btn_label.setEnabled(False)
            self.btn_label.setToolTip("Доступно только после статуса «Оплачен»")
        self.btn_edit = QPushButton("Изменить макет")
        self.btn_edit.setToolTip("Выбрать изделие, повернуть 90°, перетащить с привязкой к краям")
        self.btn_edit.clicked.connect(self._open_edit)
        self.btn_save_scheme = QPushButton("Готово" if self._preview_mode else "Сохранить схему")
        self.btn_save_scheme.setToolTip(
            "Закрыть и применить раскладку к сессии." if self._preview_mode
            else "Записать все открытые макеты листов в заказ (после правок или выбора варианта)"
        )
        if self._preview_mode:
            self.btn_save_scheme.clicked.connect(self._preview_apply_and_close)
        else:
            self.btn_save_scheme.clicked.connect(self._save_all_layouts_to_db)
        self.btn_cancel_cut = QPushButton("Отменить раскрой")
        self.btn_cancel_cut.setToolTip(
            "Сразу отменить сохранённый раскрой, если возможно (без лишних окон); "
            "заказ и изделия — «Оплачен». Если нельзя — будет сообщение с причиной."
        )
        self.btn_cancel_cut.clicked.connect(self._cancel_saved_cut)
        if self._preview_mode:
            self.btn_cancel_cut.setVisible(False)
            self.btn_other_options.setToolTip(
                "Показать варианты раскладки для выбранного листа (как в основном раскрое). "
                "Применяется к открытой сессии без записи в БД."
            )
        _st_edit = str(self.order_info.get("status") or "").strip().lower()
        if _st_edit == "made":
            _tip_made = "Заказ в статусе «Изготовлен» — изменять раскрой нельзя."
            self.btn_edit.setEnabled(False)
            self.btn_edit.setToolTip(_tip_made)
            self.btn_save_scheme.setEnabled(False)
            self.btn_save_scheme.setToolTip(_tip_made)
            self.btn_cancel_cut.setEnabled(False)
            self.btn_cancel_cut.setToolTip(_tip_made)
            self.btn_other_options.setEnabled(False)
            self.btn_other_options.setToolTip(_tip_made)
        btn_row.addWidget(self.btn_pdf)
        btn_row.addWidget(self.btn_save_scheme)
        btn_row.addWidget(self.btn_cancel_cut)
        btn_row.addWidget(self.btn_other_options)
        btn_row.addWidget(self.btn_sequence)
        btn_row.addWidget(self.btn_label)
        btn_row.addWidget(self.btn_edit)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        self.btn_label.clicked.connect(self.print_labels_requested.emit)
        self.setStyleSheet("""
            QDialog { background-color: #E6F2FF; }
            QPushButton { background-color: #4682B4; color: white; border-radius: 5px; padding: 8px 14px; }
            QPushButton:hover { background-color: #5A9BD5; }
        """)
        if self._view_only:
            _oid_disp = self.order_info.get("id") or self.order_info.get("order_id") or ""
            self.setWindowTitle(
                "Схема раскроя — все листы заказа (сохранённый раскрой) — №%s" % _oid_disp
                if _oid_disp
                else "Схема раскроя — все листы заказа (сохранённый раскрой)"
            )
            # PDF и этикетки оставляем — их подключает родитель (MainWindow) или вызывающий код.
            self.btn_other_options.setVisible(False)
            self.btn_edit.setVisible(False)
            self.btn_cancel_cut.setVisible(False)
            self.btn_save_scheme.setEnabled(True)
            self.btn_save_scheme.setText("Закрыть")
            self.btn_save_scheme.setToolTip("Закрыть окно просмотра")
            try:
                self.btn_save_scheme.clicked.disconnect()
            except TypeError:
                pass
            self.btn_save_scheme.clicked.connect(self.accept)

    def _rebuild_tabs(self):
        self.tabs.blockSignals(True)
        self.tabs.clear()
        self._tab_canvases = []
        self._tab_scrolls = []
        if not self.layouts:
            holder = QWidget()
            lay = QVBoxLayout(holder)
            lay.addWidget(QLabel("Нет раскладки"))
            lay.addStretch(1)
            self.tabs.addTab(holder, "Лист 1")
            self.canvas = None
            self.scroll = None
            self.tabs.blockSignals(False)
            return
        for i, one_layout in enumerate(self.layouts):
            tab = QWidget()
            tab_lay = QVBoxLayout(tab)
            # В диалоге всегда полная схема (размеры, легенда, fit по окну). Режим preview_mode у диалога
            # нужен только для кнопок «Готово»/сессии, а не для упрощённого канваса — иначе лист обрезается.
            canv = CuttingCanvas(
                parent=None,
                fit_to_view=True,
                preview_mode=False,
            )
            canv.set_layouts([one_layout])
            scr = QScrollArea()
            scr.setWidget(canv)
            scr.setWidgetResizable(False)
            scr.setMinimumHeight(400)
            tab_lay.addWidget(scr)
            self._tab_canvases.append(canv)
            self._tab_scrolls.append(scr)
            self.tabs.addTab(tab, "Лист %d" % (i + 1))
        self.tabs.setCurrentIndex(0)
        self.canvas = self._tab_canvases[0]
        self.scroll = self._tab_scrolls[0]
        self.tabs.blockSignals(False)
        QTimer.singleShot(0, self._sync_canvas_to_viewport)

    def _on_tab_changed(self, index):
        if 0 <= index < len(self._tab_canvases):
            self.canvas = self._tab_canvases[index]
            self.scroll = self._tab_scrolls[index]
            QTimer.singleShot(0, self._sync_canvas_to_viewport)

    def _sync_canvas_to_viewport(self):
        """Масштаб по размеру viewport (один лист влезает), размер канваса = контент → прокрутка при нескольких листах."""
        if not self.layouts or self.scroll is None or self.canvas is None:
            return
        vp = self.scroll.viewport()
        if vp and vp.width() > 50 and vp.height() > 50:
            self.canvas.set_viewport_size(vp.width(), vp.height())
            hint = self.canvas.sizeHint()
            if hint.isValid() and hint.width() > 0 and hint.height() > 0:
                self.canvas.setFixedSize(hint)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._sync_canvas_to_viewport)

    def showEvent(self, event):
        super().showEvent(event)
        if event.type() == QEvent.Show:
            self.showMaximized()
            QTimer.singleShot(0, self._sync_canvas_to_viewport)

    def _open_sequence(self):
        from ui.cut_sequence_dialog import CutSequenceDialog
        d = CutSequenceDialog(self.layouts, self.order_info, self)
        d.exec_()

    def _recalculate_layout(self):
        """Пересчитать раскрой: 4 варианта, автоматически выбран лучший по деловой площади."""
        from db import models
        order_id = self.order_info.get('id')
        if not order_id:
            QMessageBox.warning(self, "Пересчёт", "Не указан заказ.")
            return
        row0 = models.get_order(int(order_id)) or {}
        if (row0.get("status") or "").strip().lower() == "made":
            QMessageBox.information(
                self,
                "Пересчёт",
                "Заказ в статусе «Изготовлен» — изменять раскрой нельзя.",
            )
            return
        rows = models.get_order_items(order_id)
        if not rows:
            QMessageBox.warning(self, "Пересчёт", "В заказе нет изделий.")
            return
        items = []
        for r in rows:
            items.append({
                'material_name': r.get('material_name') or '',
                'height_mm': r.get('height_mm') or 0,
                'width_mm': r.get('width_mm') or 0,
                'quantity': r.get('quantity') or 1,
                'recipient_text': r.get('recipient_text'),
                'edge_treatment': r.get('edge_treatment') or {},
                'thickness_mm': r.get('thickness_mm', 4),
            })
        def get_sheets_for_material(mat, thickness_mm=None):
            # Приоритет: остатки → листы в работе (свободные места) → целые листы. Всегда учитываем «в работе».
            thick = int(thickness_mm) if thickness_mm is not None else 4
            remnants = models.get_remnants_by_material_and_thickness(mat, thick)
            in_work = models.get_sheets_in_work_for_material_thickness(mat, thick)
            full = models.get_full_sheets_by_material_and_thickness(mat, thick)
            if not remnants and not full and not in_work and thickness_mm is None:
                remnants = models.get_remnants_by_material(mat)
                full = models.get_full_sheets_by_material(mat)
            sheets = []
            for r in remnants:
                sheets.append({'id': r['id'], 'width_mm': r['width_mm'], 'height_mm': r['height_mm'], 'sheet_type': 'remnant', 'thickness_mm': r.get('thickness_mm', thick)})
            for idx, s in enumerate(in_work):
                uid = models.in_work_pool_entry_id(s['order_id'], s.get('sheet_index', 0), idx)
                row = {
                    'id': uid,
                    'width_mm': s['rect_w'],
                    'height_mm': s['rect_h'],
                    'sheet_type': 'in_work',
                    'thickness_mm': s['thickness_mm'],
                    'in_work_order_id': s['order_id'],
                    'in_work_sheet_index': s.get('sheet_index', 0),
                    'in_work_rect': {'x': s['rect_x'], 'y': s['rect_y'], 'w': s['rect_w'], 'h': s['rect_h']},
                }
                if s.get('saved_layout') is not None:
                    row['saved_layout'] = copy.deepcopy(s['saved_layout'])
                sheets.append(row)
            for f in full:
                sheets.append({'id': f['id'], 'width_mm': f['width_mm'], 'height_mm': f['height_mm'], 'sheet_type': 'full', 'thickness_mm': f.get('thickness_mm', thick)})
            type_rank = {'in_work': 0, 'remnant': 1, 'full': 2}
            sheets.sort(
                key=lambda s: (
                    type_rank.get(s.get('sheet_type') or 'full', 9),
                    (int(s.get('width_mm') or 0) * int(s.get('height_mm') or 0)),
                )
            )
            return sheets
        def get_threshold_for_material(mat, thickness_mm=None):
            return models.get_threshold_for_material(mat, thickness_mm)
        from logic.cutting_algorithm import compute_cutting_layout_variants
        try:
            # Строим варианты и выбираем более плотное заполнение листов.
            variants = compute_cutting_layout_variants(items, get_sheets_for_material, get_threshold_for_material, num_variants=4)
            def _variant_score(v):
                layouts = list((v or {}).get('layouts') or [])
                if not layouts:
                    return (-1.0, 0, 0.0)
                ratios = []
                total_sheet_area = 0
                for lay in layouts:
                    sw = int(lay.get('sheet_width') or 0)
                    sh = int(lay.get('sheet_height') or 0)
                    sa = max(1, sw * sh)
                    pa = 0
                    for p in (lay.get('pieces') or []):
                        pa += max(0, int(p.get('w') or 0)) * max(0, int(p.get('h') or 0))
                    ratios.append(float(pa) / float(sa))
                    total_sheet_area += sa
                avg_fill = sum(ratios) / float(len(ratios))
                return (avg_fill, -len(layouts), -total_sheet_area)
            result = max(variants, key=_variant_score) if variants else None
            if result:
                from logic.layout_learning import apply_layout_learning
                result = apply_layout_learning(result)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка пересчёта", str(e))
            return
        if not result:
            QMessageBox.warning(self, "Пересчёт", "Не удалось построить раскладку.")
            return
        self._apply_recalc_result(result, order_id, "Раскрой пересчитан: выбран лучший из 4 вариантов (приоритет — деловой остаток).")

    def _recalculate_other_options(self):
        """Показать варианты раскроя. Если листов несколько — сначала выбор листа, затем 4 варианта только для него; применяется только выбранный лист."""
        if self._preview_mode:
            self._recalculate_other_options_preview()
            return
        from db import models
        from logic.cutting_algorithm import compute_cutting_layout_variants, compute_layout_variants_for_one_sheet
        order_id = self.order_info.get('id')
        if not order_id:
            QMessageBox.warning(self, "Другие варианты", "Не указан заказ.")
            return
        row_o = models.get_order(int(order_id)) or {}
        if (row_o.get("status") or "").strip().lower() == "made":
            QMessageBox.information(
                self,
                "Другие варианты",
                "Заказ в статусе «Изготовлен» — изменять раскрой нельзя.",
            )
            return
        if not self.layouts:
            QMessageBox.warning(self, "Другие варианты", "Нет данных раскладки.")
            return
        sheet_index = 0
        if len(self.layouts) > 1:
            d_sheet = ChooseSheetForVariantsDialog(self.layouts, self)
            if d_sheet.exec_() != d_sheet.Accepted:
                return
            sheet_index = d_sheet.get_selected_sheet_index()
            if sheet_index < 0 or sheet_index >= len(self.layouts):
                return
        lay = self.layouts[sheet_index]
        pieces = list(lay.get('pieces') or [])
        sw = lay.get('sheet_width') or 0
        sh = lay.get('sheet_height') or 0
        if not pieces or sw <= 0 or sh <= 0:
            QMessageBox.warning(self, "Другие варианты", "На выбранном листе нет изделий или не заданы размеры.")
            return
        material = lay.get('material') or ''
        thickness_mm = lay.get('thickness_mm', 4)
        min_h, min_w = 0, 0
        try:
            th = models.get_threshold_for_material(material, thickness_mm)
            if th:
                min_h = th.get('min_height_mm') or 0
                min_w = th.get('min_width_mm') or 0
        except Exception:
            pass
        try:
            variant_layouts = compute_layout_variants_for_one_sheet(sw, sh, pieces, min_h, min_w, thickness_mm)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        if not variant_layouts:
            QMessageBox.warning(self, "Другие варианты", "Не удалось построить варианты для этого листа.")
            return
        if len(variant_layouts) < 2:
            QMessageBox.information(
                self,
                "Другие варианты",
                "Найден только один отличный вариант раскладки для этого листа — его можно подтвердить в окне выбора.",
            )
        variant_results = []
        for vlay in variant_layouts:
            full_layout = dict(lay, pieces=vlay['pieces'], business_rects=vlay['business_rects'], waste_rects=vlay['waste_rects'], sheet_width=vlay['sheet_width'], sheet_height=vlay['sheet_height'], rotated=vlay.get('rotated', False))
            variant_results.append({'layouts': [full_layout]})
        d = ChooseVariantDialog(variant_results, self)
        if d.exec_() != d.Accepted:
            return
        chosen = d.get_chosen()
        if not chosen or not chosen.get('layouts'):
            return
        chosen_lay = chosen['layouts'][0]
        # Не переносим направления резов со старой раскладки: при смене схемы резы строятся заново по выбранному варианту.
        new_layout = {k: v for k, v in lay.items() if k not in ('pieces', 'business_rects', 'waste_rects', 'sheet_width', 'sheet_height', 'rotated', 'cut_segments', 'cut_rows')}
        new_layout.update({
            'pieces': chosen_lay['pieces'],
            'business_rects': chosen_lay['business_rects'],
            'waste_rects': chosen_lay['waste_rects'],
            'sheet_width': chosen_lay['sheet_width'],
            'sheet_height': chosen_lay['sheet_height'],
            'rotated': chosen_lay.get('rotated', False),
        })
        if chosen_lay.get('cut_segments') is not None:
            new_layout['cut_segments'] = list(chosen_lay['cut_segments'])
        if chosen_lay.get('cut_rows') is not None:
            new_layout['cut_rows'] = chosen_lay['cut_rows']
        if models.update_cut_result_layout(order_id, sheet_index, new_layout):
            updated = models.get_cut_results(order_id)
            self.layouts = [r.get('layout') for r in updated if isinstance(r.get('layout'), dict)]
            self._rebuild_tabs()
            self._sync_canvas_to_viewport()
            self.layout_updated.emit()
            QMessageBox.information(self, "Другие варианты", "Применён выбранный вариант раскладки для листа %d." % (sheet_index + 1))
        else:
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить раскладку.")

    def _recalculate_other_options_preview(self):
        """Другие варианты раскладки в режиме сессии (без заказа / без записи в БД)."""
        from db import models
        from logic.cutting_algorithm import compute_layout_variants_for_one_sheet
        if not self.layouts:
            QMessageBox.warning(self, "Другие варианты", "Нет данных раскладки.")
            return
        sheet_index = 0
        if len(self.layouts) > 1:
            d_sheet = ChooseSheetForVariantsDialog(self.layouts, self)
            if d_sheet.exec_() != d_sheet.Accepted:
                return
            sheet_index = d_sheet.get_selected_sheet_index()
            if sheet_index < 0 or sheet_index >= len(self.layouts):
                return
        lay = self.layouts[sheet_index]
        pieces = list(lay.get('pieces') or [])
        sw = lay.get('sheet_width') or 0
        sh = lay.get('sheet_height') or 0
        if not pieces or sw <= 0 or sh <= 0:
            QMessageBox.warning(self, "Другие варианты", "На выбранном листе нет изделий или не заданы размеры.")
            return
        material = lay.get('material') or ''
        thickness_mm = lay.get('thickness_mm', 4)
        min_h, min_w = 0, 0
        try:
            th = models.get_threshold_for_material(material, thickness_mm)
            if th:
                min_h = th.get('min_height_mm') or 0
                min_w = th.get('min_width_mm') or 0
        except Exception:
            pass
        try:
            variant_layouts = compute_layout_variants_for_one_sheet(sw, sh, pieces, min_h, min_w, thickness_mm)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        if not variant_layouts:
            QMessageBox.warning(self, "Другие варианты", "Не удалось построить варианты для этого листа.")
            return
        if len(variant_layouts) < 2:
            QMessageBox.information(
                self,
                "Другие варианты",
                "Найден только один отличный вариант — подтвердите его в окне выбора.",
            )
        variant_results = []
        for vlay in variant_layouts:
            full_layout = dict(
                lay,
                pieces=vlay['pieces'],
                business_rects=vlay['business_rects'],
                waste_rects=vlay['waste_rects'],
                sheet_width=vlay['sheet_width'],
                sheet_height=vlay['sheet_height'],
                rotated=vlay.get('rotated', False),
            )
            variant_results.append({'layouts': [full_layout]})
        d = ChooseVariantDialog(variant_results, self)
        if d.exec_() != d.Accepted:
            return
        chosen = d.get_chosen()
        if not chosen or not chosen.get('layouts'):
            return
        chosen_lay = chosen['layouts'][0]
        new_layout = {k: v for k, v in lay.items() if k not in (
            'pieces', 'business_rects', 'waste_rects', 'sheet_width', 'sheet_height', 'rotated',
            'cut_segments', 'cut_rows',
        )}
        new_layout.update({
            'pieces': chosen_lay['pieces'],
            'business_rects': chosen_lay['business_rects'],
            'waste_rects': chosen_lay['waste_rects'],
            'sheet_width': chosen_lay['sheet_width'],
            'sheet_height': chosen_lay['sheet_height'],
            'rotated': chosen_lay.get('rotated', False),
        })
        if chosen_lay.get('cut_segments') is not None:
            new_layout['cut_segments'] = list(chosen_lay['cut_segments'])
        if chosen_lay.get('cut_rows') is not None:
            new_layout['cut_rows'] = chosen_lay['cut_rows']
        self.layouts[sheet_index] = new_layout
        self._rebuild_tabs()
        self._sync_canvas_to_viewport()
        self.layout_updated.emit()
        QMessageBox.information(
            self, "Другие варианты",
            "Применён выбранный вариант раскладки для листа %d (сессия)." % (sheet_index + 1),
        )

    def _apply_recalc_result(self, result, order_id, success_msg):
        from db import models
        from logic.cutting_algorithm import recompute_free_rects_from_pieces, refresh_cut_segments_for_layout
        import copy
        if result.get('errors'):
            QMessageBox.warning(self, "Внимание", "\n".join(result['errors']))
        if not result.get('layouts'):
            QMessageBox.warning(self, "Пересчёт", "Нет раскладки. Измените состав заказа или наличие листов.")
            return
        orow_chk = models.get_order(int(order_id)) or {}
        if (orow_chk.get("status") or "").strip().lower() == "made":
            QMessageBox.information(
                self,
                "Пересчёт",
                "Заказ в статусе «Изготовлен» — изменять раскрой нельзя.",
            )
            return
        # Деловые остатки (business_rects) — нумеруются; неделовые отходы не сохраняются
        def _remnants_from_layout(layout):
            mat = layout.get('material') or ''
            th = layout.get('thickness_mm', 4)
            return [{'name': mat, 'height_mm': r['h'], 'width_mm': r['w'], 'thickness_mm': th} for r in (layout.get('business_rects') or [])]
        existing_rows = models.get_cut_results(order_id)
        results_to_save = [{
            'sheet_type': r.get('sheet_type') or 'full',
            'sheet_id': r.get('sheet_id'),
            'layout': copy.deepcopy(r.get('layout') or {}),
            'remnants_created': list(r.get('remnants_created') or []),
        } for r in existing_rows]
        min_h, min_w = 0, 0
        new_sheet_layouts = []  # не in_work — потом объединим по одному листу
        for lay in result.get('layouts', []):
            lay_clean = dict(lay)
            lay_clean.pop('cut_segments', None)
            lay_clean.pop('cut_rows', None)
            ow = lay.get('in_work_order_id')
            si = lay.get('in_work_sheet_index')
            if ow is not None and si is not None:
                rect = lay.get('in_work_rect') or {}
                ox, oy = int(rect.get('x') or 0), int(rect.get('y') or 0)
                if ow == order_id and si < len(results_to_save):
                    existing_layout = results_to_save[si]['layout']
                    material = existing_layout.get('material') or ''
                    try:
                        th = models.get_threshold_for_material(material, existing_layout.get('thickness_mm', 4))
                        if th:
                            min_h = th.get('min_height_mm') or 0
                            min_w = th.get('min_width_mm') or 0
                    except Exception:
                        pass
                    new_pieces = []
                    for p in lay.get('pieces') or []:
                        np = dict(p)
                        np['x'] = int(p.get('x') or 0) + ox
                        np['y'] = int(p.get('y') or 0) + oy
                        new_pieces.append(np)
                    merged_pieces = list(existing_layout.get('pieces') or []) + new_pieces
                    sw = existing_layout.get('sheet_width') or 0
                    sh = existing_layout.get('sheet_height') or 0
                    business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, merged_pieces, min_h, min_w)
                    existing_layout['pieces'] = merged_pieces
                    existing_layout['business_rects'] = business_rects
                    existing_layout['waste_rects'] = waste_rects
                    refresh_cut_segments_for_layout(existing_layout, min_h, min_w)
                elif ow != order_id:
                    rows_other = models.get_cut_results(ow)
                    if si < len(rows_other) and isinstance(rows_other[si].get('layout'), dict):
                        existing = rows_other[si]['layout']
                        sw = existing.get('sheet_width') or 0
                        sh = existing.get('sheet_height') or 0
                        try:
                            th = models.get_threshold_for_material(existing.get('material') or '', existing.get('thickness_mm', 4))
                            min_h = (th or {}).get('min_height_mm') or 0
                            min_w = (th or {}).get('min_width_mm') or 0
                        except Exception:
                            min_h, min_w = 0, 0
                        new_pieces = [dict(p, x=int(p.get('x') or 0)+ox, y=int(p.get('y') or 0)+oy) for p in lay.get('pieces') or []]
                        merged_pieces = list(existing.get('pieces') or []) + new_pieces
                        business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, merged_pieces, min_h, min_w)
                        merged_layout = dict(existing, pieces=merged_pieces, business_rects=business_rects, waste_rects=waste_rects)
                        refresh_cut_segments_for_layout(merged_layout, min_h, min_w)
                        models.update_cut_result_layout(ow, si, merged_layout)
                continue
            new_sheet_layouts.append(lay_clean)
        # Один физический лист — одна запись: объединяем раскладки с одним (sheet_type, sheet_id)
        by_sheet = {}
        for lay in new_sheet_layouts:
            key = (lay.get('sheet_type'), lay.get('sheet_id'))
            if key not in by_sheet:
                by_sheet[key] = []
            by_sheet[key].append(lay)
        for key, group in by_sheet.items():
            if len(group) == 1:
                lay = group[0]
                results_to_save.append({
                    'sheet_type': lay.get('sheet_type') or 'full',
                    'sheet_id': lay.get('sheet_id'),
                    'layout': lay,
                    'remnants_created': _remnants_from_layout(lay),
                })
            else:
                sw = group[0].get('sheet_width') or 0
                sh = group[0].get('sheet_height') or 0
                mh, mw = min_h, min_w
                try:
                    th = models.get_threshold_for_material(group[0].get('material') or '', group[0].get('thickness_mm', 4))
                    if th:
                        mh = th.get('min_height_mm') or 0
                        mw = th.get('min_width_mm') or 0
                except Exception:
                    pass
                all_pieces = []
                for lay in group:
                    all_pieces.extend(lay.get('pieces') or [])
                business_rects, waste_rects = recompute_free_rects_from_pieces(sw, sh, all_pieces, mh, mw)
                merged = dict(group[0], pieces=all_pieces, business_rects=business_rects, waste_rects=waste_rects)
                refresh_cut_segments_for_layout(merged, mh, mw)
                results_to_save.append({
                    'sheet_type': merged.get('sheet_type') or 'full',
                    'sheet_id': merged.get('sheet_id'),
                    'layout': merged,
                    'remnants_created': _remnants_from_layout(merged),
                })
        for r in results_to_save:
            r['layout'].pop('cut_segments', None)
            r['layout'].pop('cut_rows', None)
        models.delete_cut_results(order_id)
        models.save_cut_results(order_id, results_to_save)
        self.layouts = [r['layout'] for r in results_to_save]
        try:
            models.sync_bundle_after_mirror_cut_save(int(order_id), self.layouts)
        except Exception:
            pass
        self._rebuild_tabs()
        self._sync_canvas_to_viewport()
        self.layout_updated.emit()
        QMessageBox.information(self, "Пересчёт", success_msg)

    def _save_all_layouts_to_db(self):
        """Сохранить все текущие layout в mirror_cut_results (как после «Изменить макет»)."""
        from db import models
        order_id = self.order_info.get('id')
        if not order_id:
            QMessageBox.warning(self, "Сохранение", "Не указан заказ.")
            return
        if not self.layouts:
            QMessageBox.warning(self, "Сохранение", "Нет схем для сохранения.")
            return
        rows = models.get_cut_results(order_id) or []
        if rows:
            if len(rows) != len(self.layouts):
                QMessageBox.warning(
                    self,
                    "Сохранение",
                    "Число листов в заказе (%d) не совпадает с открытой схемой (%d). "
                    "Закройте окно и откройте схему из заказа снова."
                    % (len(rows), len(self.layouts)),
                )
                return
            to_save = []
            for i, lay in enumerate(self.layouts):
                base = dict(rows[i] or {})
                if not isinstance(lay, dict):
                    lay = {}
                base["layout"] = lay
                to_save.append(base)
            # Важно: сохраняем через save_cut_results, чтобы:
            # 1) откатить старый резерв исходных листов;
            # 2) заново зарезервировать актуальные источники после правок схемы;
            # 3) синхронизировать деловые остатки на складе.
            try:
                models.save_cut_results(order_id, to_save)
            except RuntimeError as e:
                QMessageBox.warning(self, "Сохранение", str(e))
                return
        else:
            if not self._results_payload or len(self._results_payload) != len(self.layouts):
                QMessageBox.warning(
                    self,
                    "Сохранение",
                    "Нет исходных данных для первичного сохранения схемы. Пересчитайте раскрой заново.",
                )
                return
            to_save = []
            for i, lay in enumerate(self.layouts):
                base = dict(self._results_payload[i] or {})
                base["layout"] = lay
                to_save.append(base)
            try:
                models.save_cut_results(order_id, to_save)
            except RuntimeError as e:
                QMessageBox.warning(self, "Сохранение", str(e))
                return
            # После явного сохранения схемы раскроя заказ должен попасть в производство.
            try:
                row_st = models.get_order(order_id) or {}
                st = (row_st.get("status") or "").strip().lower()
                if st in ("draft", "paid"):
                    models.set_order_status(order_id, "in_progress")
            except Exception:
                pass
        try:
            oid = int(order_id)
            nums = models.get_remnant_display_numbers_by_order_id(oid)
            if nums:
                for canv in self._tab_canvases:
                    canv.set_remnant_display_numbers(nums)
        except Exception:
            pass
        # После сохранения схемы: bundle (в работе по кускам + cut_scheme_created).
        try:
            models.sync_bundle_after_mirror_cut_save(int(order_id), self.layouts)
        except Exception:
            pass
        self.layout_updated.emit()
        self.accept()

    def _cancel_saved_cut(self):
        from db import models
        order_id = self.order_info.get('id')
        if not order_id:
            QMessageBox.warning(self, "Отмена раскроя", "Не указан заказ.")
            return
        ok, msg = models.cancel_cut_results_if_allowed(int(order_id))
        if not ok:
            QMessageBox.warning(self, "Отмена раскроя", msg)
            return
        self.layouts = []
        self._rebuild_tabs()
        self.layout_updated.emit()
        self.accept()

    def _preview_apply_and_close(self):
        self.layout_updated.emit()
        self.accept()

    def _open_edit(self):
        from ui.layout_edit_dialog import LayoutEditDialog
        from db import models
        if not self.layouts:
            return
        order_id = self.order_info.get('id')
        if not self._preview_mode and not order_id:
            return
        if not self._preview_mode:
            orow = models.get_order(int(order_id)) or {}
            if (orow.get("status") or "").strip().lower() == "made":
                QMessageBox.information(
                    self,
                    "Макет",
                    "Заказ в статусе «Изготовлен» — изменять раскрой нельзя.",
                )
                return
        if len(self.layouts) == 1:
            sheet_index = 0
            lay = self.layouts[0]
        else:
            from PyQt5.QtWidgets import QInputDialog
            sheet_index, ok = QInputDialog.getInt(
                self, "Выберите лист", "Номер листа (1 — %d):" % len(self.layouts),
                1, 1, len(self.layouts)
            )
            if not ok:
                return
            sheet_index = sheet_index - 1
            lay = self.layouts[sheet_index]
        material = lay.get('material') or ''
        d = LayoutEditDialog(
            lay, sheet_index, order_id, material, self,
            persist_to_db=not self._preview_mode,
            session_mode=self._preview_mode,
        )
        if d.exec_() == d.Accepted:
            if self._preview_mode:
                saved = getattr(d, '_saved_layout', None) or d.canvas.get_layout()
                if isinstance(saved, dict) and 0 <= sheet_index < len(self.layouts):
                    self.layouts[sheet_index] = saved
            else:
                updated = models.get_cut_results(order_id)
                self.layouts = []
                for r in updated:
                    l = r.get('layout')
                    if isinstance(l, dict):
                        self.layouts.append(l)
            self._rebuild_tabs()
            self._sync_canvas_to_viewport()
            self.layout_updated.emit()
