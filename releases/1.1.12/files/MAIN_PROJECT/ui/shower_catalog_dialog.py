# -*- coding: utf-8 -*-
"""Каталог душевых: категории → товары с фото и фурнитурой; локальная SQLite и файлы рядом с БД."""
import os
import sys

_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _mp not in sys.path:
    sys.path.insert(0, _mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QScrollArea,
    QWidget,
    QGridLayout,
    QFrame,
    QSizePolicy,
    QMessageBox,
    QStackedWidget,
    QButtonGroup,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap, QFont, QImage

from cfg_loader import color
from logic.dushevye_data import (
    open_shower_connection,
    clear_shower_caches_for_connection,
    load_subcategories,
    load_products_for_subcategory,
    load_product_image_paths,
    load_kit_items,
    build_kit_display_plan,
    pick_kit_items_for_finish,
    sanitize_shower_catalog_description,
    _abs_media,
)


def _scale_pm(src: str, max_w: int, max_h: int) -> QPixmap:
    """
    Загрузка картинки с диска. На Windows QPixmap(str) часто даёт пустой pixmap для путей
    с не-ASCII (например папка «душевые») — читаем файл в байтах и loadFromData.
    """
    if not src:
        return QPixmap()
    img = QImage(src)
    if img.isNull() and os.path.isfile(src):
        try:
            with open(src, "rb") as fh:
                blob = fh.read()
        except OSError:
            blob = b""
        if blob:
            img = QImage()
            if not img.loadFromData(blob):
                img = QImage()
    if img.isNull():
        return QPixmap()
    pm = QPixmap.fromImage(img)
    return pm.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


class ShowerCatalogDialog(QDialog):
    """Две страницы: сетка категорий и список товаров выбранной категории с фурнитурой."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Душевые — каталог")
        self.setModal(True)
        self.setMinimumSize(960, 640)
        self._conn = None
        self._base = ""
        self._stack = None
        self._cat_scroll_inner = None
        self._prod_scroll_inner = None
        self._title_lbl = None
        opened = open_shower_connection()
        if not opened:
            QMessageBox.warning(
                self,
                "Душевые",
                "Не найден каталог: положите dushevye.db (или av24_dushevye.db) в папку «душевые» "
                "рядом с run.py (MAIN_PROJECT) или в корне MIRROR_CUT, либо укажите "
                "путь в app.cfg: [paths] shower_catalog_dir.",
            )
            self._missing = True
            return
        self._missing = False
        self._conn, self._base = opened
        self._build_ui()
        self._fill_categories()

    def closeEvent(self, event):  # noqa: N802
        if self._conn:
            try:
                clear_shower_caches_for_connection(self._conn)
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        super().closeEvent(event)

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        top = QHBoxLayout()
        close_btn = QPushButton("× Закрыть")
        close_btn.setStyleSheet(
            "QPushButton { background-color: #DC3545; color: white; padding: 8px 14px; "
            "font-weight: bold; border: none; border-radius: 4px; }"
        )
        close_btn.clicked.connect(self.reject)
        top.addWidget(close_btn)
        top.addStretch()
        lay.addLayout(top)

        self._stack = QStackedWidget()
        # --- страница категорий ---
        cat_page = QWidget()
        cat_outer = QVBoxLayout(cat_page)
        cap = QLabel("Выберите категорию")
        cap.setStyleSheet("font-weight: bold; font-size: 12pt; color: #1a365d;")
        cat_outer.addWidget(cap)
        cat_scroll = QScrollArea()
        cat_scroll.setWidgetResizable(True)
        cat_scroll.setFrameShape(QFrame.NoFrame)
        self._cat_scroll_inner = QWidget()
        self._cat_grid = QGridLayout(self._cat_scroll_inner)
        self._cat_grid.setSpacing(12)
        self._cat_grid.setContentsMargins(4, 4, 4, 4)
        cat_scroll.setWidget(self._cat_scroll_inner)
        cat_outer.addWidget(cat_scroll, 1)
        self._stack.addWidget(cat_page)

        # --- страница товаров ---
        prod_page = QWidget()
        prod_outer = QVBoxLayout(prod_page)
        head = QHBoxLayout()
        self._btn_back = QPushButton("← Назад")
        bs = (
            "QPushButton { padding: 8px 16px; border-radius: 4px; border: 1px solid #90caf9; "
            "background: #e3f2fd; font-weight: bold; color: #0d47a1; } "
            "QPushButton:hover { background: #bbdefb; }"
        )
        self._btn_back.setStyleSheet(bs)
        self._btn_back.clicked.connect(self._on_back)
        head.addWidget(self._btn_back)
        self._title_lbl = QLabel("")
        self._title_lbl.setStyleSheet("font-weight: bold; font-size: 11pt; color: #1a365d;")
        head.addWidget(self._title_lbl, 1)
        prod_outer.addLayout(head)
        prod_scroll = QScrollArea()
        prod_scroll.setWidgetResizable(True)
        prod_scroll.setFrameShape(QFrame.NoFrame)
        self._prod_scroll_inner = QWidget()
        self._prod_vbox = QVBoxLayout(self._prod_scroll_inner)
        self._prod_vbox.setSpacing(16)
        self._prod_vbox.setContentsMargins(4, 8, 4, 16)
        prod_scroll.setWidget(self._prod_scroll_inner)
        prod_outer.addWidget(prod_scroll, 1)
        self._stack.addWidget(prod_page)

        lay.addWidget(self._stack, 1)
        self.setStyleSheet(
            "QDialog { background-color: %s; } QScrollArea { background: transparent; }"
            % color("orders_area_bg")
        )

    def _on_back(self):
        self._stack.setCurrentIndex(0)

    def _clear_grid(self, grid: QGridLayout):
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _clear_vbox(self, vbox: QVBoxLayout):
        while vbox.count():
            item = vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _fill_categories(self):
        if self._missing:
            return
        self._clear_grid(self._cat_grid)
        cats = load_subcategories(self._conn)
        cols = 3
        for i, c in enumerate(cats):
            card = self._category_card(c)
            self._cat_grid.addWidget(card, i // cols, i % cols)

    def _category_card(self, c):
        btn = QPushButton()
        btn.setFlat(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.MinimumExpanding)
        btn.setStyleSheet(
            "QPushButton { background: #fff; border: 1px solid #cfd8dc; border-radius: 8px; text-align: left; } "
            "QPushButton:hover { border-color: #1976d2; }"
        )
        v = QVBoxLayout()
        v.setSpacing(6)
        v.setContentsMargins(8, 8, 8, 8)
        img = QLabel()
        img.setAlignment(Qt.AlignCenter)
        img.setMinimumHeight(160)
        img.setMaximumHeight(200)
        path = _abs_media(self._base, c.get("thumb_relpath"))
        if path:
            pm = _scale_pm(path, 280, 190)
            if not pm.isNull():
                img.setPixmap(pm)
        pm0 = img.pixmap()
        if pm0 is None or pm0.isNull():
            img.setText("Нет фото")
            img.setStyleSheet("color: #90a4ae; background: #eceff1; border-radius: 4px;")
        lbl = QLabel(c.get("title") or "—")
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setBold(True)
        lbl.setFont(f)
        lbl.setStyleSheet("color: #263238;")
        img.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        v.addWidget(img)
        v.addWidget(lbl)
        btn.setLayout(v)
        btn.setMinimumHeight(240)
        sid = int(c["id"])
        title = c.get("title") or ""
        btn.clicked.connect(lambda checked=False, s=sid, t=title: self._open_category(s, t))
        return btn

    def _open_category(self, subcategory_id: int, title: str):
        self._title_lbl.setText(title)
        self._clear_vbox(self._prod_vbox)
        prods = load_products_for_subcategory(self._conn, subcategory_id)
        if not prods:
            empty = QLabel("В этой категории нет товаров.")
            empty.setStyleSheet("color: #78909c; padding: 24px;")
            self._prod_vbox.addWidget(empty)
        else:
            for p in prods:
                self._prod_vbox.addWidget(self._product_block(p, subcategory_id))
        self._prod_vbox.addStretch(1)
        self._stack.setCurrentIndex(1)

    def _product_block(self, p, category_id: int):
        fr = QFrame()
        fr.setFrameStyle(QFrame.StyledPanel | QFrame.Plain)
        fr.setStyleSheet(
            "QFrame { background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 8px; }"
        )
        v = QVBoxLayout(fr)
        v.setSpacing(8)
        name = QLabel(p.get("name") or "—")
        name.setWordWrap(True)
        fn = QFont()
        fn.setBold(True)
        fn.setPointSize(10)
        name.setFont(fn)
        name.setStyleSheet("color: #1565c0;")
        v.addWidget(name)
        desc = sanitize_shower_catalog_description(p.get("description") or "")
        if desc:
            dlab = QLabel(desc[:900] + ("…" if len(desc) > 900 else ""))
            dlab.setWordWrap(True)
            dlab.setStyleSheet("font-size: 9pt; color: #546e7a; margin-bottom: 4px;")
            v.addWidget(dlab)
        main_path = _abs_media(self._base, p.get("main_image_relpath"))
        pic = QLabel()
        pic.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        if main_path:
            pm = _scale_pm(main_path, 520, 360)
            if not pm.isNull():
                pic.setPixmap(pm)
        pm1 = pic.pixmap()
        if pm1 is None or pm1.isNull():
            pic.setText("Нет фото товара")
            pic.setStyleSheet("color: #b0bec5; min-height: 120px;")
        v.addWidget(pic)
        gal_paths = load_product_image_paths(self._conn, int(p["id"]))
        if len(gal_paths) > 1:
            gal_row = QWidget()
            gh = QHBoxLayout(gal_row)
            gh.setContentsMargins(0, 0, 0, 0)
            gh.setSpacing(6)
            cap_g = QLabel("Все фото:")
            cap_g.setStyleSheet("font-size: 8pt; color: #90a4ae;")
            gh.addWidget(cap_g)
            for rp in gal_paths[:24]:
                ap = _abs_media(self._base, rp)
                gl = QLabel()
                gl.setFixedSize(64, 64)
                gl.setAlignment(Qt.AlignCenter)
                if ap:
                    gpm = _scale_pm(ap, 60, 60)
                    if not gpm.isNull():
                        gl.setPixmap(gpm)
                if gl.pixmap() is None or gl.pixmap().isNull():
                    gl.setText("×")
                    gl.setStyleSheet("color: #cfd8dc; font-size: 9pt;")
                gh.addWidget(gl, 0, Qt.AlignTop)
            gh.addStretch(1)
            gscroll = QScrollArea()
            gscroll.setWidgetResizable(True)
            gscroll.setMaximumHeight(88)
            gscroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            gscroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            gscroll.setFrameShape(QFrame.NoFrame)
            gscroll.setWidget(gal_row)
            v.addWidget(gscroll)
        fur_lbl = QLabel("Фурнитура")
        fur_lbl.setStyleSheet("font-weight: bold; color: #37474f; margin-top: 4px;")
        v.addWidget(fur_lbl)
        kit = load_kit_items(
            self._conn,
            int(p["id"]),
            bundle_root=self._base,
            category_id=int(category_id),
            prefer_product_id=int(p["id"]),
        )
        if not kit:
            v.addWidget(QLabel("Позиции комплекта не указаны."))
            return fr
        plan = build_kit_display_plan(kit)
        kit_row = QWidget()
        hlay = QHBoxLayout(kit_row)
        hlay.setContentsMargins(0, 0, 0, 0)
        hlay.setSpacing(10)

        def _visible_items(finish: str):
            if plan["use_color_switch"] and plan["palette"]:
                return pick_kit_items_for_finish(plan["slots"], finish)
            return [slot[0] for slot in plan["slots"]]

        def _refill(finish: str):
            while hlay.count():
                it = hlay.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.deleteLater()
            for item in _visible_items(finish):
                hlay.addWidget(self._kit_furniture_cell(item), 0, Qt.AlignTop)
            hlay.addStretch(1)

        if plan["use_color_switch"] and plan["palette"]:
            sw = QWidget()
            sw_l = QHBoxLayout(sw)
            sw_l.setContentsMargins(0, 2, 0, 6)
            sw_l.setSpacing(8)
            cap_c = QLabel("Цвет / отделка:")
            cap_c.setStyleSheet("font-size: 9pt; color: #546e7a; font-weight: bold;")
            sw_l.addWidget(cap_c, 0)
            btn_grp = QButtonGroup(self)
            btn_grp.setExclusive(True)
            pill = (
                "QPushButton { padding: 6px 12px; border-radius: 14px; border: 1px solid #b0bec5; "
                "background: #fff; font-size: 9pt; color: #37474f; } "
                "QPushButton:checked { background: #1976d2; color: #fff; border-color: #1565c0; font-weight: bold; } "
                "QPushButton:hover:!checked { background: #eceff1; }"
            )
            for i, lab in enumerate(plan["palette"]):
                b = QPushButton(lab)
                b.setCheckable(True)
                b.setCursor(Qt.PointingHandCursor)
                b.setStyleSheet(pill)
                btn_grp.addButton(b)
                sw_l.addWidget(b, 0)
                if i == 0:
                    b.setChecked(True)
            sw_l.addStretch(1)
            v.addWidget(sw)

            def _on_finish(btn):
                if btn is not None:
                    _refill(btn.text())

            btn_grp.buttonClicked.connect(_on_finish)
            _refill(plan["palette"][0])
        else:
            _refill("")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(280)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(kit_row)
        v.addWidget(scroll)
        return fr

    def _kit_furniture_cell(self, item: dict) -> QFrame:
        cell = QFrame()
        cell.setStyleSheet(
            "QFrame { background: #fafafa; border: 1px solid #eee; border-radius: 6px; }"
        )
        cv = QVBoxLayout(cell)
        cv.setSpacing(4)
        ip = _abs_media(self._base, item.get("image_relpath"))
        im = QLabel()
        im.setAlignment(Qt.AlignCenter)
        im.setFixedSize(72, 72)
        if item.get("is_kitline_text"):
            im.setText("из\nопис.")
            im.setToolTip(
                "Строка из описания комплекта (urn:av24:kitline:…), отдельного фото в каталоге нет."
            )
            im.setStyleSheet(
                "color: #78909c; font-size: 8pt; background: #eceff1; border-radius: 4px;"
            )
        elif ip:
            pm = _scale_pm(ip, 68, 68)
            if not pm.isNull():
                im.setPixmap(pm)
        pm2 = im.pixmap()
        if not item.get("is_kitline_text") and (pm2 is None or pm2.isNull()):
            im.setText("—")
            im.setStyleSheet("color: #cfd8dc; font-size: 10pt;")
            hint = (item.get("image_relpath") or "").strip()
            if not hint and (item.get("part_url") or "").lower().startswith("http"):
                hint = (item.get("part_url") or "").strip()
            if hint:
                im.setToolTip(
                    "Файл картинки не найден на диске по пути из БД (или фото ещё не скачивали):\n"
                    + hint[:400]
                )
        pn = QLabel(item.get("part_name") or "")
        pn.setWordWrap(True)
        pn.setMaximumWidth(200)
        pn.setStyleSheet("font-size: 9pt; color: #455a64;")
        pd = sanitize_shower_catalog_description(item.get("part_description") or "")
        pd_lab = None
        if pd:
            pd_lab = QLabel(pd[:900] + ("…" if len(pd) > 900 else ""))
            pd_lab.setWordWrap(True)
            pd_lab.setMaximumWidth(200)
            pd_lab.setStyleSheet("font-size: 8pt; color: #78909c;")
        q = (item.get("quantity") or "").strip()
        if q:
            pq = QLabel("Кол-во: %s" % q)
            pq.setStyleSheet("font-size: 8pt; color: #78909c;")
            cv.addWidget(im)
            cv.addWidget(pn)
            if pd_lab is not None:
                cv.addWidget(pd_lab)
            cv.addWidget(pq)
        else:
            cv.addWidget(im)
            cv.addWidget(pn)
            if pd_lab is not None:
                cv.addWidget(pd_lab)
        return cell

    def exec_(self):  # noqa: N802
        if getattr(self, "_missing", False):
            return QDialog.Rejected
        return super().exec_()
