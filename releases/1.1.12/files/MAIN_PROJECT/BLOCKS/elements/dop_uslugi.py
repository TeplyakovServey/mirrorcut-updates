# -*- coding: utf-8 -*-
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIntValidator
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from elements.calc_tile_style import apply_service_tile_frame, style_tile_header


class Dopi(QWidget):
    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(3, 2, 3, 2)
        root.setSpacing(4)

        self.label = QLabel("ДОПОЛНИТЕЛЬНО")
        style_tile_header(self.label)
        root.addWidget(self.label)

        self.items_list = QListWidget()
        self.items_list.setFrameShape(QFrame.NoFrame)
        self.items_list.setStyleSheet("border: none; background: #ffffff;")
        self.items_list.setMinimumHeight(88)
        self.items_list.installEventFilter(self)

        self.comment_input = QLineEdit()
        self.comment_input.setPlaceholderText("Комментарий")
        self.number_input = QLineEdit()
        self.number_input.setPlaceholderText("Стоимость")
        self.number_input.setValidator(QIntValidator())

        self.add_button = QPushButton("+")
        self.add_button.setFixedSize(40, 44)
        self.add_button.setStyleSheet(
            "background-color: #A0A0A0; color: black; font-weight: bold; font-size: 17px;"
        )
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self.add_item)

        row = QGridLayout()
        row.addWidget(self.comment_input, 0, 0)
        row.addWidget(self.number_input, 1, 0)
        row.addWidget(self.add_button, 0, 1, 2, 1)

        root.addWidget(self.items_list)
        root.addLayout(row)

        self.items = []
        self.comment_input.textChanged.connect(self.update_button_state)
        self.number_input.textChanged.connect(self.update_button_state)

    def update_button_state(self):
        if self.comment_input.text().strip() and self.number_input.text().strip():
            self.add_button.setEnabled(True)
            self.add_button.setStyleSheet(
                "background-color: #ddab22; color: black; font-weight: bold; font-size: 17px;"
            )
        else:
            self.add_button.setEnabled(False)
            self.add_button.setStyleSheet(
                "background-color: #A0A0A0; color: black; font-weight: bold; font-size: 17px;"
            )

    def eventFilter(self, obj, event):
        if obj == self.items_list and event.type() == event.KeyPress and event.key() == Qt.Key_Delete:
            selected_item = self.items_list.currentRow()
            if selected_item != -1:
                self.items_list.takeItem(selected_item)
                del self.items[selected_item]
        return super().eventFilter(obj, event)

    def add_item(self):
        comment = self.comment_input.text().strip()
        number = self.number_input.text().strip()
        if not comment or not number:
            return
        item_text = "%s - %s" % (comment, number)
        self.items_list.addItem(QListWidgetItem(item_text))
        self.items.append({"Комментарий": comment, "Число": int(number)})
        self.comment_input.clear()
        self.number_input.clear()
        self.update_button_state()

    def get_info(self):
        return self.items


if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    w = Dopi()
    w.show()
    sys.exit(app.exec_())
