from PyQt5.QtWidgets import (
    QApplication, QWidget, QFrame, QVBoxLayout, QGridLayout,
    QLabel, QComboBox, QLineEdit, QCheckBox, QSpacerItem, QSizePolicy
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QIntValidator  # Ограничение ввода для поля "Монтаж"
import sys

from elements.calc_tile_style import apply_service_tile_frame


class Uslugi(QWidget):
    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)

        self.frame = QFrame(self)
        self.frame.setGeometry(0, 0, 200, 200)
        self.frame.setObjectName("uslugiInner")
        self.frame.setFrameShape(QFrame.NoFrame)
        self.frame.setStyleSheet("#uslugiInner { border: none; background: transparent; }")

        self.frame1 = QFrame(self)
        self.frame1.setGeometry(10, 10, 180, 20)
        self.frame1.setFrameShape(QFrame.NoFrame)
        self.frame1.setStyleSheet(
            "background-color: #dadada; color: black; font-weight: bold; font-size: 11px; border: none;"
        )

        layout = QVBoxLayout(self.frame1)
        # Название фрейма сверху по центру
        self.label = QLabel("услуги".upper(), self.frame1)
        self.label.setAlignment(Qt.AlignCenter)  # Центрируем текст внутри QLabel

        # Устанавливаем шрифт и размер
        font = QFont("Arial", 16, QFont.Bold)  # Выберите нужный шрифт, размер и вес
        self.label.setFont(font)  # Применяем шрифт к QLabel

        # Добавляем лейбл в layout
        layout.addWidget(self.label)
        layout.setContentsMargins(0, 0, 0, 0)




        # Выпадающий список "Замер"
        self.measure_label = QLabel("Замер:", self.frame)
        self.measure_combo = QComboBox(self.frame)
        self.measure_combo.addItems(["", "1", "2", "3"])  # "" — пустое значение по умолчанию

        # Поле для ввода "Монтаж" (только целые числа)
        self.install_label = QLabel("Монтаж:", self.frame)
        self.install_input = QLineEdit(self.frame)
        self.install_input.setPlaceholderText("0 руб")
        self.install_input.setFixedWidth(100)
        self.install_input.setAlignment(Qt.AlignCenter)
        self.install_input.setValidator(QIntValidator())  # Разрешает только целые числа

        # Чекбокс "Упаковка в бумагу"
        self.packaging_checkbox = QCheckBox("Упаковка в бумагу", self.frame)

        layout = QVBoxLayout(self.frame)
        layout.setContentsMargins(5, 40, 5, 5)
        layout.setSpacing(3)

        #layout.addWidget(self.title_label)
        layout.addWidget(self.measure_label)
        layout.addWidget(self.measure_combo)
        layout.addWidget(self.install_label)
        layout.addWidget(self.install_input)
        layout.addWidget(self.packaging_checkbox)
        layout.addSpacerItem(QSpacerItem(10, 10, QSizePolicy.Minimum, QSizePolicy.Expanding))


    def get_info(self):
        """Получает все выбранные значения"""
        measure = self.measure_combo.currentText() if self.measure_combo.currentText() else None
        install = self.install_input.text()
        packaging = self.packaging_checkbox.isChecked()

        return {
            "Замер": int(measure) if measure else None,
            "Монтаж": int(install) if install.isdigit() else None,
            "Упаковка в бумагу": packaging
        }

class MainApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Database UI")
        self.setGeometry(100, 100, 600, 600)

        self.initUI()

    def initUI(self):
        grid = QGridLayout()
        grid.setSpacing(5)  # Минимальные отступы между элементами
        self.setLayout(grid)
        self.setObjectName("xxx")
        self.setStyleSheet("#xxx { border: 3px solid black; background-color: #5f8c84;}")

        # Добавляем виджет услуг
        self.uslugi_widget = Uslugi()
        grid.addWidget(self.uslugi_widget, 1, 2)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    mainApp = MainApp()
    mainApp.show()
    sys.exit(app.exec_())