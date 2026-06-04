from settings import *
materials_dict = {'Зеркало': {'серебро бесцветное': [4, 5, 6], 'серое': [4], 'бронза': [4], 'серебро осветленное (crystalvision)': [4]}, 'Стекло Альфа': {'xxxx': [4]}, 'Стекло прозрачное': {'б\\цв': [4, 5, 6, 8, 10], 'осветленное crystalvision': [4, 6, 8, 10]}, 'Стекло тонированное': {'серое': [4, 5, 6, 8, 10], 'бронза': [4, 5, 6, 8, 10], 'темно-серое': [4, 6, 8]}, 'Стекло матовое(сатин)': {'б\\цв': [4, 5, 6, 8, 10], 'серое': [4, 5, 6, 8, 10], 'бронза': [4, 5, 6, 8, 10], 'осветленное (crystalvision)': [4, 6, 8, 10]}, 'Стекло Лакомат': {'xxxx': [4]}, 'Стекло Comfort тонированное': {'серое': [4], 'бронза': [4]}, 'Стекло окрашеное глянец': {'1013': [4], '1014': [4], '1015': [4], '1023': [4], '1164': [4], '1202': [4], '1236': [4], '1435': [4], '1586': [4], '1604': [4], '2001': [4], '4006': [4], '5001': [4], '5002': [4], '7013': [4], '8017': [4], '8615': [4], '8715': [4], '8815': [4], '9003': [4], '9005': [4], '9010': [4], '0327': [4], '0667': [4]}, 'Зеркало матовое(сатин)': {'серебро бесцветное': [4], 'серое': [4], 'бронза': [4], 'серебро осветленное (crystalvision)': [4]}, 'Стекло окрашеное матовое': {'1015': [4], '1023': [4], '7000': [4], '8815': [4], '9010': [4]}}

class FrameTest1(QWidget):
    def __init__(self):
        super().__init__()

        self.frame = QFrame(self)
        self.setFixedSize(200, 200)
        self.frame.setObjectName("frame1")
        self.frame.setStyleSheet("#frame1 { border: 3px solid black; background-color: #c3ffd2; }")
        self.setStyleSheet("color: black; font-weight: bold")
        self.frame.setGeometry(0, 0, 200, 200)

        self.layout = QGridLayout()

        # Создаем выпадающие списки
        self.combo_material = QComboBox()
        self.combo_material.addItem("Выберите материал")  # Добавляем пустой выбор
        self.combo_material.addItems(materials_dict.keys())
        self.layout.addWidget(QLabel("Материал:"), 0, 0, 1, 1)
        self.layout.addWidget(self.combo_material, 1, 0, 1, 2)

        self.combo_color = QComboBox()
        self.combo_color.setEnabled(False)
        self.layout.addWidget(QLabel("Цвет:"), 2, 0, 1, 1)
        self.layout.addWidget(self.combo_color, 3, 0, 1, 2)

        self.combo_thickness = QComboBox()
        self.combo_thickness.setEnabled(False)
        self.layout.addWidget(QLabel("Толщина:"), 4, 0, 1, 1)
        self.layout.addWidget(self.combo_thickness, 5, 0, 1, 2)

        self.from_factor = QComboBox()
        self.from_factor.addItems(['Прямоугольник',
                                   'Круг',
                                   'Овал',
                                   'Трапеция',
                                   'Треугольник',
                                   'Фигурная'])

        self.layout.addWidget(QLabel("Форма:"), 6, 0, 1, 1)
        self.layout.addWidget(self.from_factor, 7, 0, 1, 2)

        # Чекбоксы
        self.checkbox1 = QCheckBox("Закалка")
        self.checkbox2 = QCheckBox("Без резки")
        self.layout.addWidget(self.checkbox1, 8, 0)
        self.layout.addWidget(self.checkbox2, 8, 1)

        self.frame.setLayout(self.layout)

        # Подключаем сигналы
        self.combo_material.currentIndexChanged.connect(self.update_colors)
        self.combo_color.currentIndexChanged.connect(self.update_thickness)

    def update_colors(self):
        """Обновляет доступные цвета в зависимости от выбранного материала."""
        selected_material = str(self.combo_material.currentText()).strip()
        self.combo_color.clear()
        self.combo_thickness.clear()
        self.combo_color.setEnabled(False)
        self.combo_thickness.setEnabled(False)

        if selected_material != "Выберите материал" and selected_material in materials_dict:
            self.combo_color.addItems(list(materials_dict[selected_material].keys()))
            self.combo_color.setEnabled(True)

    def update_thickness(self):
        """Обновляет доступные толщины в зависимости от выбранного цвета."""
        selected_material = str(self.combo_material.currentText()).strip()
        selected_color = str(self.combo_color.currentText()).strip()
        self.combo_thickness.clear()
        self.combo_thickness.setEnabled(False)

        if selected_material in materials_dict and selected_color in materials_dict[selected_material]:
            self.combo_thickness.addItems([str(t) for t in sorted(materials_dict[selected_material][selected_color])])
            self.combo_thickness.setEnabled(True)


class MainApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Database UI")
        self.setGeometry(100, 100, 300, 300)
        self.initUI()

    def initUI(self):
        grid = QGridLayout()
        grid.setSpacing(5)
        self.setLayout(grid)
        self.setObjectName("xxx")
        self.setStyleSheet("#xxx { border: 3px solid black; background-color: #5f8c84;}")
        grid.addWidget(FrameTest1(), 0, 0)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    mainApp = MainApp()
    mainApp.show()
    sys.exit(app.exec_())