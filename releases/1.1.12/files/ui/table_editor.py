"""Table editor: select table, search by prefix, add/delete/edit rows, save to DB."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QComboBox, QMessageBox, QHeaderView,
    QGroupBox, QFrame,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from db import models


TABLES_CONFIG = [
    ('mirror_full_sheets', 'Целые листы', ['name', 'height_mm', 'width_mm', 'thickness_mm'], 'id'),
    ('mirror_remnants', 'Остатки', ['name', 'height_mm', 'width_mm', 'thickness_mm', 'unique_number'], 'id'),
    ('mirror_business_waste_threshold', 'Пороги деловых отходов', ['name', 'thickness_mm', 'min_height_mm', 'min_width_mm'], 'id'),
    ('mirror_clients', 'Клиенты', ['client_type', 'name', 'phone', 'email', 'legal_address', 'actual_address', 'source', 'notes', 'registration_date'], 'id'),
]

BTN_STYLE = """
    QPushButton {
        background-color: #4682B4;
        color: white;
        border: none;
        border-radius: 6px;
        padding: 10px 18px;
        font-weight: bold;
        min-height: 20px;
    }
    QPushButton:hover { background-color: #5A9BD5; }
    QPushButton:pressed { background-color: #3A6B94; }
    QPushButton#danger { background-color: #B22222; }
    QPushButton#danger:hover { background-color: #CD5C5C; }
"""


class TableEditorDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Редактор таблиц")
        self.setMinimumSize(800, 550)
        self.setStyleSheet("""
            QDialog { background-color: #E6F2FF; }
            QGroupBox { font-weight: bold; padding-top: 10px; }
            QTableWidget { background: white; gridline-color: #B0C4DE; }
        """)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # --- Панель выбора таблицы и фильтра ---
        top_group = QGroupBox("Таблица и фильтр")
        top_layout = QVBoxLayout(top_group)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Таблица:"))
        self.table_combo = QComboBox()
        self.table_combo.setMinimumWidth(220)
        for _, label, _, _ in TABLES_CONFIG:
            self.table_combo.addItem(label)
        self.table_combo.currentIndexChanged.connect(self._on_table_changed)
        row1.addWidget(self.table_combo)
        row1.addStretch()
        top_layout.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Фильтр (поиск по началу строки, без учёта регистра):"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Введите несколько букв — отобразятся только подходящие строки")
        self.search_edit.setMinimumHeight(28)
        self.search_edit.textChanged.connect(self._filter_table)
        row2.addWidget(self.search_edit, 1)
        top_layout.addLayout(row2)
        layout.addWidget(top_group)

        # --- Таблица ---
        self.table = QTableWidget()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)

        # --- Кнопки: новая строка, удалить, сохранить ---
        btn_frame = QFrame()
        btn_frame.setStyleSheet("QFrame { background-color: #B0C4DE; border-radius: 8px; padding: 8px; }")
        btn_row = QHBoxLayout(btn_frame)
        btn_row.setSpacing(12)
        self.btn_add = QPushButton("➕ Добавить")
        self.btn_add.setStyleSheet(BTN_STYLE)
        self.btn_add.setMinimumHeight(36)
        self.btn_add.clicked.connect(self._add_row)
        self.btn_edit_client = QPushButton("Изменить клиента")
        self.btn_edit_client.setStyleSheet(BTN_STYLE)
        self.btn_edit_client.setMinimumHeight(36)
        self.btn_edit_client.clicked.connect(self._edit_client_row)
        self.btn_edit_client.setVisible(False)
        self.btn_delete = QPushButton("Удалить выбранную строку")
        self.btn_delete.setObjectName("danger")
        self.btn_delete.setStyleSheet(BTN_STYLE)
        self.btn_delete.setMinimumHeight(36)
        self.btn_delete.clicked.connect(self._delete_row)
        self.btn_save = QPushButton("💾 Сохранить изменения в БД")
        self.btn_save.setStyleSheet(BTN_STYLE)
        self.btn_save.setMinimumHeight(36)
        self.btn_save.clicked.connect(self._save)
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_edit_client)
        btn_row.addWidget(self.btn_delete)
        btn_row.addWidget(self.btn_save)
        btn_row.addStretch()
        layout.addWidget(btn_frame)

        self._current_table_key = None
        self._full_data = []
        self._on_table_changed(0)

    def _current_config(self):
        idx = self.table_combo.currentIndex()
        return TABLES_CONFIG[idx] if 0 <= idx < len(TABLES_CONFIG) else None

    def _on_table_changed(self, idx):
        cfg = self._current_config()
        if not cfg:
            return
        table_key, _, cols, _ = cfg
        self._current_table_key = table_key
        self._cols = cols
        self._pk = cfg[3]
        self.btn_edit_client.setVisible(table_key == 'mirror_clients')
        self._load_data()

    def _load_data(self):
        if self._current_table_key == 'mirror_full_sheets':
            rows = models.get_all_full_sheets()
        elif self._current_table_key == 'mirror_remnants':
            rows = models.get_all_remnants()
        elif self._current_table_key == 'mirror_business_waste_threshold':
            rows = models.get_all_thresholds()
        elif self._current_table_key == 'mirror_clients':
            rows = models.get_all_clients()
        else:
            rows = []
        self._full_data = list(rows)
        self._filter_table()

    def _filter_table(self):
        prefix = self.search_edit.text().strip().lower()
        if not prefix:
            data = self._full_data
        else:
            name_col = 'name' if 'name' in self._cols else self._cols[0]
            data = [r for r in self._full_data if str(r.get(name_col, '')).lower().startswith(prefix)]
        self._display_data(data)

    def _display_data(self, data):
        self.table.setRowCount(len(data))
        self.table.setColumnCount(len(self._cols) + 1)
        self.table.setHorizontalHeaderLabels([self._pk] + self._cols)
        for i, row in enumerate(data):
            self.table.setItem(i, 0, QTableWidgetItem(str(row.get(self._pk, ''))))
            for j, col in enumerate(self._cols):
                val = row.get(col)
                if val is not None:
                    if col == 'registration_date' and hasattr(val, 'strftime'):
                        val = val.strftime('%d.%m.%Y %H:%M')
                    elif col == 'client_type':
                        val = {'legal': 'Юр. лицо', 'ip': 'ИП', 'individual': 'Физ. лицо'}.get(str(val), str(val))
                    self.table.setItem(i, j + 1, QTableWidgetItem(str(val)))
                else:
                    self.table.setItem(i, j + 1, QTableWidgetItem(''))
        self._displayed_data = data

    def _add_row(self):
        if self._current_table_key == 'mirror_clients':
            from ui.new_client_dialog import NewClientDialog
            from app_state import refresh_clients
            d = NewClientDialog(self)
            if d.exec_() == QDialog.Accepted:
                refresh_clients()
                self._load_data()
            return
        row_pos = self.table.rowCount()
        self.table.insertRow(row_pos)
        self.table.setItem(row_pos, 0, QTableWidgetItem("(новый)"))
        for j in range(len(self._cols)):
            self.table.setItem(row_pos, j + 1, QTableWidgetItem(''))

    def _edit_client_row(self):
        if self._current_table_key != 'mirror_clients':
            return
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Изменить", "Выберите строку клиента.")
            return
        pk_item = self.table.item(row, 0)
        pk_val = (pk_item.text() if pk_item else '').strip()
        if not pk_val or pk_val == '(новый)':
            QMessageBox.warning(self, "Изменить", "Выберите существующего клиента.")
            return
        try:
            pk = int(pk_val)
        except ValueError:
            return
        client = models.get_client_by_id(pk)
        if not client:
            QMessageBox.warning(self, "Ошибка", "Клиент не найден.")
            return
        from ui.new_client_dialog import NewClientDialog
        from app_state import refresh_clients
        d = NewClientDialog(self, edit_client=client)
        if d.exec_() == QDialog.Accepted:
            refresh_clients()
            self._load_data()

    def _delete_row(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Удаление", "Выберите строку для удаления.")
            return
        pk_item = self.table.item(row, 0)
        pk_val = (pk_item.text() if pk_item else '').strip()
        if not pk_val or pk_val == '(новый)':
            self.table.removeRow(row)
            return
        if QMessageBox.question(self, "Удалить", "Удалить выбранную строку?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            pk = int(pk_val)
            if self._current_table_key == 'mirror_full_sheets':
                models.delete_full_sheet_and_archive(
                    pk,
                    deleted_by_login="table_editor",
                    deleted_by_display="Редактор таблиц",
                )
            elif self._current_table_key == 'mirror_remnants':
                models.delete_remnant_and_archive(
                    pk,
                    deleted_by_login="table_editor",
                    deleted_by_display="Редактор таблиц",
                )
            elif self._current_table_key == 'mirror_business_waste_threshold':
                models.delete_threshold(pk)
            elif self._current_table_key == 'mirror_clients':
                models.delete_client(pk)
            self._load_data()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))

    def _save(self):
        try:
            allowed_materials = models.get_allowed_sheet_material_names() if self._current_table_key == 'mirror_full_sheets' else None
            for row in range(self.table.rowCount()):
                pk_item = self.table.item(row, 0)
                pk_val = (pk_item.text() if pk_item else '').strip()
                vals = []
                for j in range(len(self._cols)):
                    item = self.table.item(row, j + 1)
                    vals.append((item.text() if item else '').strip())
                if not pk_val or pk_val == '(новый)':
                    if all(v == '' for v in vals):
                        continue
                    if self._current_table_key == 'mirror_full_sheets' and len(vals) >= 3 and vals[0]:
                        if allowed_materials is not None and vals[0] not in allowed_materials:
                            QMessageBox.warning(
                                self, "Ошибка",
                                "Материал «%s» не входит в список. Используйте только материалы из списка (Склад → Добавить листы)." % vals[0]
                            )
                            return
                        models.insert_full_sheet(vals[0], int(vals[1] or 0), int(vals[2] or 0), thickness_mm=int(vals[3]) if len(vals) > 3 and vals[3] else 4)
                    elif self._current_table_key == 'mirror_remnants' and len(vals) >= 3 and vals[0]:
                        from logic.qr_utils import remnant_qr_url
                        num = models.get_next_label_number()
                        unum = str(num)
                        models.insert_remnant(vals[0], int(vals[1] or 0), int(vals[2] or 0), unum, remnant_qr_url(unum), thickness_mm=int(vals[3]) if len(vals) > 3 and vals[3] else 4, label_number=num)
                    elif self._current_table_key == 'mirror_business_waste_threshold' and len(vals) >= 4 and vals[0]:
                        models.insert_threshold(vals[0], int(vals[2] or 0), int(vals[3] or 0), thickness_mm=int(vals[1]) if len(vals) > 1 and vals[1] else 4)
                    elif self._current_table_key == 'mirror_clients':
                        # Клиенты добавляются через диалог «Добавить»
                        pass
                else:
                    try:
                        pk = int(pk_val)
                    except ValueError:
                        continue
                    if self._current_table_key == 'mirror_full_sheets' and len(vals) >= 3:
                        if allowed_materials is not None and vals[0] not in allowed_materials:
                            QMessageBox.warning(
                                self, "Ошибка",
                                "Материал «%s» не входит в список. Используйте только материалы из списка." % vals[0]
                            )
                            return
                        models.update_full_sheet(pk, vals[0], int(vals[1] or 0), int(vals[2] or 0), thickness_mm=int(vals[3]) if len(vals) > 3 and vals[3] else None)
                    elif self._current_table_key == 'mirror_remnants' and len(vals) >= 3:
                        models.update_remnant(pk, vals[0], int(vals[1] or 0), int(vals[2] or 0), thickness_mm=int(vals[3]) if len(vals) > 3 and vals[3] else None)
                    elif self._current_table_key == 'mirror_business_waste_threshold' and len(vals) >= 4:
                        models.update_threshold(pk, vals[0], int(vals[2] or 0), int(vals[3] or 0), thickness_mm=int(vals[1]) if len(vals) > 1 and vals[1] else 4)
                    elif self._current_table_key == 'mirror_clients' and len(vals) >= 1:
                        existing = models.get_client_by_id(pk)
                        if existing:
                            ct = vals[0] if len(vals) > 0 else existing.get('client_type')
                            ct_map = {'Юр. лицо': 'legal', 'ИП': 'ip', 'Физ. лицо': 'individual'}
                            ct = ct_map.get(str(ct).strip(), ct)
                            models.update_client_full(
                                pk,
                                ct,
                                vals[1] if len(vals) > 1 else existing.get('name'),
                                existing.get('inn'), existing.get('kpp'), existing.get('okpo'),
                                existing.get('ogrn'), existing.get('registration'),
                                existing.get('first_name'), existing.get('last_name'),
                                existing.get('passport_series'), existing.get('passport_number'),
                                existing.get('birth_date'), existing.get('gender'),
                                vals[2] if len(vals) > 2 else existing.get('phone'),
                                vals[3] if len(vals) > 3 else existing.get('email'),
                                vals[4] if len(vals) > 4 else existing.get('legal_address'),
                                vals[5] if len(vals) > 5 else existing.get('actual_address'),
                                vals[6] if len(vals) > 6 and vals[6] else existing.get('source'),
                                vals[7] if len(vals) > 7 and vals[7] else existing.get('notes'),
                            )
            self._load_data()
            QMessageBox.information(self, "Сохранено", "Изменения записаны в базу данных.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))
