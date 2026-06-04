from elements.calc_tile_style import apply_service_tile_frame
from settings import *


class Gravirovka(QWidget):
    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)

        self.frame = QFrame(self)
        self.frame.setGeometry(0, 0, 200, 200)
        self.frame.setObjectName("gravInner")
        self.frame.setFrameShape(QFrame.NoFrame)
        self.frame.setStyleSheet("#gravInner { border: none; background: transparent; }")

        self.frame1 = QFrame(self.frame)
        self.frame1.setGeometry(10, 10, 180, 20)
        self.frame1.setFrameShape(QFrame.NoFrame)
        self.frame1.setStyleSheet(
            "background-color: #dadada; color: black; font-weight: bold; font-size: 12px; border: none;"
        )

        layout = QVBoxLayout(self.frame1)
        self.label = QLabel("ГРАВИРОВКА", self.frame1)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setFont(QFont("Arial", 16, QFont.Bold))
        layout.addWidget(self.label)
        layout.setContentsMargins(0, 0, 0, 0)

        self.items_list = QListWidget(self.frame)
        self.items_list.setGeometry(10, 35, 180, 70)
        self.items_list.setFrameShape(QFrame.NoFrame)
        self.items_list.setStyleSheet("border: none; background: #ffffff;")
        self.items_list.installEventFilter(self)


        self.combo_type = QComboBox(self.frame)
        self.combo_type.setGeometry(10, 120, 90, 20)
        self.combo_type.addItems(["прямая", "2", "3"])

        self.area_input = QLineEdit(self.frame)
        self.area_input.setEnabled(False)
        self.area_input.setGeometry(105, 120, 85, 20)
        self.area_input.setValidator(QDoubleValidator(0.0, 9999.99, 2))



        self.width_combo = QComboBox(self.frame)
        self.width_combo.setGeometry(10, 165, 60, 20)
        self.width_combo.addItems(["4", "6", "10"])

        self.length_input = QLineEdit(self.frame)
        self.length_input.setGeometry(75, 165, 60, 20)
        self.length_input.setValidator(QDoubleValidator(0.0, 9999.99, 2))

        self.add_button = QPushButton("+", self.frame)
        self.add_button.setGeometry(145, 165, 45, 20)

        self.add_button.setStyleSheet("background-color: #ddab22; color: black; font-weight: bold; font-size: 17px;")
        self.add_button.clicked.connect(self.add_item)

        text = QLabel('тип                          площадь', self.frame)
        text.move(10, 105)
        #self.add_button1.clicked.connect(self.add_item)
        text1 = QLabel(' ширина       длина', self.frame)
        text1.move(10, 150)

        self.items = []



    def eventFilter(self, obj, event):
        if obj == self.items_list and event.type() == event.KeyPress and event.key() == Qt.Key_Delete:
            selected_item = self.items_list.currentRow()
            if selected_item != -1:
                self.items_list.takeItem(selected_item)
                del self.items[selected_item]
        return super().eventFilter(obj, event)

    def add_item(self):
        type_value = self.combo_type.currentText()
        area_value = self.area_input.text()
        width_value = self.width_combo.currentText()
        length_value = self.length_input.text()

        if not length_value:
            QMessageBox.warning(self, "Ошибка", "Введите длину!")
            return

        item_text = f"Тип: {type_value}, Ширина: {width_value}, Длина: {length_value}"
        list_item = QListWidgetItem(item_text)
        self.items_list.addItem(list_item)
        self.items.append({
            "type": type_value,
            "area": area_value,
            "width": width_value,
            "length": length_value
        })

        self.combo_type.setCurrentIndex(0)


        self.width_combo.setCurrentIndex(0)
        self.length_input.clear()

    def delete_item(self):
        selected_item = self.items_list.currentRow()
        if selected_item != -1:
            self.items_list.takeItem(selected_item)
            del self.items[selected_item]

    def get_info(self):
        return self.items

class MainApp(QWidget):
    def __init__(self, elements: dict):
        super().__init__()
        self.elements = elements
        self.setWindowTitle("Database UI")
        self.setGeometry(100, 100, 600, 600)

        self.initUI()



    def create_frame(self, title=None):

        frame = QFrame(self)
        frame.setFrameShape(QFrame.Box)
        frame.setFixedSize(200, 200)  # Делаем фреймы квадратными и компактными
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignTop)

        frame.setLayout(layout)
        return frame, layout

    def initUI(self):
        grid = QGridLayout()
        grid.setSpacing(5)  # Минимальные отступы между элементами
        self.setLayout(grid)
        self.setObjectName("xxx")
        self.setStyleSheet("#xxx { border: 3px solid black; background-color: #5f8c84;}")

        grid.addWidget(Gravirovka(), 0, 2)





if __name__ == '__main__':
    app = QApplication(sys.argv)

    elements = {}
    mainApp = MainApp(elements)
    mainApp.show()
    sys.exit(app.exec_())