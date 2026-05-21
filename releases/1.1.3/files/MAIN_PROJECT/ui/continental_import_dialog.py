# -*- coding: utf-8 -*-
"""Импорт обновления цен профилей из Excel: простой шаблон (Серия, Название, Цвет, Цена/м) или Profil_new."""
import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QFileDialog,
    QTextEdit,
    QMessageBox,
    QProgressDialog,
    QApplication,
)

from db_main import facades_update_profile_prices_from_excel


class _DropLabel(QLabel):
    def __init__(self, on_file, parent=None):
        super().__init__(parent)
        self._on_file = on_file
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("border: 2px dashed #888; padding: 20px;")
        self.setText("Перетащите .xlsx сюда\n(колонки: Серия, Название, Цвет, Цена/м)\nили нажмите «Выбрать файл»")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls() or []
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path:
            self._on_file(path)


class ProfilePricesExcelImportDialog(QDialog):
    """Обновление цен профилей по Excel (демо-шаблон из окна «Цены» или старый Profil_new)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Импорт Excel — цены профилей")
        self.setMinimumSize(720, 520)
        self._file_path = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.drop_label = _DropLabel(self._set_file)
        layout.addWidget(self.drop_label)

        row = QHBoxLayout()
        self.path_label = QLabel("Файл: не выбран")
        row.addWidget(self.path_label, 1)
        btn_pick = QPushButton("Выбрать файл")
        btn_pick.clicked.connect(self._pick_file)
        row.addWidget(btn_pick)
        layout.addLayout(row)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        layout.addWidget(self.result_text, 1)

        actions = QHBoxLayout()
        self.btn_import = QPushButton("Обновить цены")
        self.btn_import.clicked.connect(self._import_file)
        actions.addWidget(self.btn_import)
        actions.addStretch()
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        actions.addWidget(btn_close)
        layout.addLayout(actions)

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите Excel файл",
            "",
            "Excel (*.xlsx *.xls);;All files (*.*)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path):
        self._file_path = path
        self.path_label.setText("Файл: %s" % path)

    def _import_file(self):
        path = (self._file_path or "").strip()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "Импорт", "Выберите корректный файл Excel.")
            return
        progress = QProgressDialog("Обновление цен профилей…", None, 0, 0, self)
        progress.setWindowTitle("Импорт Excel")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        QApplication.processEvents()

        report = facades_update_profile_prices_from_excel(path)
        progress.close()

        if report.get("error"):
            QMessageBox.critical(self, "Импорт", "Ошибка: %s" % report["error"])
            return

        lines = []
        lines.append("Строк в файле (после разбора цветов): %s" % report.get("source_rows", 0))
        lines.append("Обновлено записей в БД: %s" % report.get("updated", 0))
        lines.append("")
        nf = report.get("not_found") or []
        if nf:
            lines.append("Не найдено в БД (серия | название | цвет), первые 200:")
            for s in nf[:200]:
                lines.append("  — %s" % s)
            if len(nf) > 200:
                lines.append("  … и ещё %d" % (len(nf) - 200))
        else:
            lines.append("Все позиции из файла сопоставлены с БД.")
        self.result_text.setPlainText("\n".join(lines))
        QMessageBox.information(self, "Импорт", "Готово. Обновлено записей: %s" % report.get("updated", 0))


# Обратная совместимость импорта из prices_dialog
ContinentalImportDialog = ProfilePricesExcelImportDialog
