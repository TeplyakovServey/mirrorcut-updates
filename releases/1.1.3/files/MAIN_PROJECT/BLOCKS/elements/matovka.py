from elements.calc_tile_style import apply_service_tile_frame, style_tile_header
from settings import *


class FrameMatovka(QWidget):
    def __init__(self):
        super().__init__()
        apply_service_tile_frame(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(2)

        title = QLabel("МАТОВКА")
        style_tile_header(title)
        outer.addWidget(title)

        self.layout = QGridLayout()
        self.combo = QComboBox()
        self.combo.addItems(["1", "2", "3"])

        self.radio0 = QRadioButton("С двух сторон")
        self.radio1 = QRadioButton("c лицевой")
        self.radio2 = QRadioButton("с тыльной")
        self.radio3 = QRadioButton("рис мат")
        self.radio4 = QRadioButton("фон мат")

        self.layout.addWidget(self.combo, 0, 0, 3, 3)
        self.layout.addWidget(self.radio1, 3, 0)
        self.layout.addWidget(self.radio2, 3, 1)
        self.layout.addWidget(self.radio0, 2, 0, 1, 2)
        self.layout.addWidget(self.radio3, 4, 0)
        self.layout.addWidget(self.radio4, 4, 1)

        self.group1 = QButtonGroup(self)
        self.group1.addButton(self.radio0)
        self.group1.addButton(self.radio1)
        self.group1.addButton(self.radio2)

        self.group2 = QButtonGroup(self)
        self.group2.addButton(self.radio3)
        self.group2.addButton(self.radio4)

        self.checkbox1 = QCheckBox("полимер")
        self.layout.addWidget(self.checkbox1, 5, 0)

        outer.addLayout(self.layout)
