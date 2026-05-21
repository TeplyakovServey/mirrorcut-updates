# -*- coding: utf-8 -*-
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIntValidator
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QGridLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from elements.calc_tile_style import apply_service_tile_frame, style_tile_header


class Srochnost(QWidget):
    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(3, 2, 3, 2)
        root.setSpacing(4)

        self.label = QLabel("СРОЧНОСТЬ")
        style_tile_header(self.label)
        root.addWidget(self.label)

        body = QWidget()
        body.setObjectName("ServiceTileBody")
        body.setAttribute(Qt.WA_StyledBackground, True)
        g = QGridLayout(body)
        g.setContentsMargins(2, 0, 2, 0)
        g.setVerticalSpacing(4)

        self.sroch_g = QButtonGroup(self)
        self.time_14 = QRadioButton("14 дн.")
        self.time_7 = QRadioButton("7 дн.")
        self.time_3 = QRadioButton("3 дн.")
        self.time_0 = QRadioButton("Сегодня")
        for b in (self.time_14, self.time_7, self.time_3, self.time_0):
            self.sroch_g.addButton(b)
        g.addWidget(self.time_14, 0, 0)
        g.addWidget(self.time_7, 0, 1)
        g.addWidget(self.time_3, 1, 0)
        g.addWidget(self.time_0, 1, 1)

        self.discount = QRadioButton("Скидка")
        self.surcharge = QRadioButton("Наценка")
        self.price_group = QButtonGroup(self)
        self.price_group.addButton(self.discount)
        self.price_group.addButton(self.surcharge)
        self.price_group.buttonClicked.connect(self.toggle_inputs)

        self.rubles_input = QLineEdit()
        self.rubles_input.setPlaceholderText("Рубли")
        self.rubles_input.setFixedWidth(80)
        self.rubles_input.setAlignment(Qt.AlignCenter)
        self.rubles_input.setValidator(QIntValidator())
        self.rubles_input.setEnabled(False)
        self.rubles_input.textChanged.connect(self.clear_percent_input)

        self.percent_input = QLineEdit()
        self.percent_input.setPlaceholderText("%")
        self.percent_input.setFixedWidth(80)
        self.percent_input.setAlignment(Qt.AlignCenter)
        self.percent_input.setEnabled(False)
        self.percent_input.textChanged.connect(self.clear_rubles_input)
        self.percent_input.textChanged.connect(self.validate_percent_input)

        g.addWidget(QLabel("Изменение цены:"), 2, 0, 1, 2, Qt.AlignCenter)
        g.addWidget(self.rubles_input, 3, 0)
        g.addWidget(self.percent_input, 3, 1)
        g.addWidget(self.discount, 4, 0)
        g.addWidget(self.surcharge, 4, 1)

        root.addWidget(body)

    def validate_percent_input(self):
        text = self.percent_input.text()
        valid_text = "".join([c for c in text if c.isdigit() or c in ",."])
        self.percent_input.setText(valid_text)

    def clear_percent_input(self):
        if self.rubles_input.text():
            self.percent_input.blockSignals(True)
            self.percent_input.clear()
            self.percent_input.blockSignals(False)

    def clear_rubles_input(self):
        if self.percent_input.text():
            self.rubles_input.blockSignals(True)
            self.rubles_input.clear()
            self.rubles_input.blockSignals(False)

    def toggle_inputs(self):
        self.rubles_input.setEnabled(True)
        self.percent_input.setEnabled(True)

    def get_info(self):
        selected_sroch = None
        for btn in self.sroch_g.buttons():
            if btn.isChecked():
                selected_sroch = btn.text()

        selected_price = None
        for btn in self.price_group.buttons():
            if btn.isChecked():
                selected_price = btn.text()

        rubles = self.rubles_input.text()
        percent = self.percent_input.text().replace(",", ".")
        return {
            "Срочность": selected_sroch,
            "Тип изменения цены": selected_price,
            "Рубли": int(rubles) if rubles.isdigit() else None,
            "Проценты": float(percent) if percent.replace(".", "").isdigit() else None,
        }


if __name__ == "__main__":
    import sys

    app = QApplication(sys.argv)
    w = Srochnost()
    w.show()
    sys.exit(app.exec_())
