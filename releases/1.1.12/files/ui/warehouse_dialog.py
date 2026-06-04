"""Окно «Склад»: вкладки Целые листы / Остатки, адаптивная плитка (размер от окна), размеры внутри по осям, материал по центру."""
import sys
import os
import time
import math
import html as html_std
import json
import subprocess
import tempfile
import uuid
import copy
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget, QScrollArea,
    QFrame, QLabel, QGridLayout, QDialogButtonBox, QSizePolicy,
    QLineEdit, QSpinBox, QDateEdit, QFormLayout, QMessageBox,
    QPushButton, QComboBox, QCompleter, QDoubleSpinBox, QTextEdit, QTextBrowser,
    QStackedWidget, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)
from collections import defaultdict
from PyQt5.QtCore import Qt, QSize, QRect, QDate, QLocale, pyqtSignal, QEvent, QTimer, QByteArray, QUrl
from PyQt5.QtGui import QPainter, QColor, QPen, QFont, QPixmap, QCursor

from db import models
from ui.cutting_canvas import CuttingCanvas
from window_branding import apply_fraction_window_geometry
try:
    from MAIN_PROJECT.db_main import (
        facades_get_all_profiles,
        facades_get_all_hinges,
        facades_get_profile_by_id,
        facades_get_profiles_by_ids,
    )
except Exception:
    facades_get_all_profiles = None
    facades_get_all_hinges = None
    facades_get_profile_by_id = None
    facades_get_profiles_by_ids = None
try:
    from MAIN_PROJECT.cfg_loader import get_base_dir as _mp_get_base_dir
except Exception:
    _mp_get_base_dir = None


def _remnant_qr_url(unique_number):
    """URL для QR остатка стекла. Пакет `logic` при запуске из MAIN_PROJECT — это MAIN_PROJECT/logic (без qr_utils)."""
    try:
        from logic.qr_utils import remnant_qr_url as _fn

        return _fn(unique_number)
    except ImportError:
        import importlib.util

        _mirror_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _path = os.path.join(_mirror_root, "logic", "qr_utils.py")
        if not os.path.isfile(_path):
            raise ImportError("Не найден logic/qr_utils.py по пути %s" % _path)
        _spec = importlib.util.spec_from_file_location("_mirror_cut_qr_utils", _path)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod.remnant_qr_url(unique_number)


def _warehouse_labels_export_dir():
    """Папка для «Скачать PDF»: data/labels_export при наличии get_base_dir, иначе Загрузки или temp."""
    try:
        if _mp_get_base_dir:
            d = os.path.join(_mp_get_base_dir(), "labels_export")
            os.makedirs(d, exist_ok=True)
            return d
    except Exception:
        pass
    try:
        dl = os.path.join(os.path.expanduser("~"), "Downloads")
        if os.path.isdir(dl):
            return dl
    except Exception:
        pass
    return tempfile.gettempdir()


def _unique_label_pdf_path(prefix):
    fn = "warehouse_%s_%s_%s.pdf" % (prefix, datetime.now().strftime("%Y%m%d_%H%M%S"), uuid.uuid4().hex[:8])
    return os.path.join(_warehouse_labels_export_dir(), fn)


def _open_pdf_file(path):
    path = os.path.normpath(path)
    if not os.path.isfile(path):
        raise OSError("Файл не найден: %s" % path)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _import_write_glass_remnant_labels_pdf():
    try:
        from logic.production_instructions import write_glass_remnant_labels_pdf as fn

        return fn
    except Exception:
        import importlib.util

        mp = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "MAIN_PROJECT", "logic", "production_instructions.py"))
        if not os.path.isfile(mp):
            raise ImportError("Не найден production_instructions.py")
        spec = importlib.util.spec_from_file_location("_wh_pi_glass", mp)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(mod)
        return mod.write_glass_remnant_labels_pdf


def _import_write_profile_labels_pdf():
    try:
        from logic.production_instructions import write_profile_labels_pdf as fn

        return fn
    except Exception:
        import importlib.util

        mp = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "MAIN_PROJECT", "logic", "production_instructions.py"))
        if not os.path.isfile(mp):
            raise ImportError("Не найден production_instructions.py")
        spec = importlib.util.spec_from_file_location("_wh_pi_prof", mp)
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(mod)
        return mod.write_profile_labels_pdf


def _glass_deletion_actor_from_parent(widget):
    """Логин и подпись для архива/истории при удалении остатка со склада (из главного окна с _user)."""
    p = widget
    while p is not None:
        u = getattr(p, "_user", None)
        if isinstance(u, dict):
            login = (str(u.get("login") or "")).strip()[:128]
            role = (str(u.get("role") or "")).strip()[:64]
            if login or u.get("id") is not None:
                disp = ("%s (%s)" % (login, role)).strip(" ()") if (login and role) else (login or role or "")
                return (login or None), (disp[:255] if disp else None)
        p = p.parent()
    return None, None


def _warehouse_fasad_img_path(photo_number):
    if not _mp_get_base_dir:
        return None
    base = os.path.join(_mp_get_base_dir(), 'FASAD', 'img')
    n = str(photo_number or '').strip()
    if not n:
        return None
    for ext in ('.png', '.jpg', '.jpeg'):
        p = os.path.join(base, n + ext)
        if os.path.isfile(p):
            return p
    return os.path.join(base, n + '.png')


def _warehouse_profile_img_path(profile_row):
    """Тот же поиск файла, что в калькуляторе/ценах (серии Nimbus, Prisma, …)."""
    if not profile_row:
        return None
    try:
        from MAIN_PROJECT.ui.facade_profile_dialog import _fasad_img_path

        return _fasad_img_path(
            profile_row.get("photo_number"),
            series=profile_row.get("series"),
            name=profile_row.get("name"),
        )
    except Exception:
        return _warehouse_fasad_img_path(profile_row.get("photo_number"))


TILES_PER_ROW = 6
TILE_PAD = 8
TILE_DIM_HEIGHT = 28
REMNANT_TILES_PER_ROW = 10  # компактные плитки остатков — больше в ряд

_HIDDEN_WAREHOUSE_MT = frozenset({"Стекло Альфа"})


def _is_silver_colorless_mirror_variant(text: str) -> bool:
    s = (text or "").lower().replace(" ", "").replace("\\", "/")
    if "серебро" not in s:
        return False
    if "бесцвет" in s:
        return True
    return "б/цв" in s or "бцв" in s


def _sort_mirror_variants(variants):
    """Для зеркала: «серебро бесцветное» первым в списке цветов."""
    items = list(variants or [])
    if not items:
        return []

    def _rank(v):
        if _is_silver_colorless_mirror_variant(v):
            return (0, 0 if "бесцвет" in (v or "").lower() else 1, (v or "").lower())
        return (1, 0, (v or "").lower())

    return sorted(items, key=_rank)


def _open_supplier_card_safe(supplier_id, parent):
    if not supplier_id:
        return
    try:
        from MAIN_PROJECT.ui.supplier_card_dialog import open_supplier_card

        open_supplier_card(int(supplier_id), parent)
    except Exception:
        try:
            import importlib.util

            mp = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "MAIN_PROJECT", "ui", "supplier_card_dialog.py")
            )
            if os.path.isfile(mp):
                spec = importlib.util.spec_from_file_location("_wh_sup_card", mp)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                mod.open_supplier_card(int(supplier_id), parent)
        except Exception as e:
            QMessageBox.warning(parent, "Поставщик", "Не удалось открыть карточку: %s" % e)


def _try_load_materials_tree():
    try:
        from MAIN_PROJECT.BLOCKS.calc.db_postgres import load_materials_tree as _fn
    except Exception:
        try:
            from calc.db_postgres import load_materials_tree as _fn
        except Exception:
            return {}
    try:
        return _fn() or {}
    except Exception:
        return {}


def _try_resolve_sheet_material():
    try:
        from MAIN_PROJECT.mirror_cut_prefill import _resolve_sheet_material as _fn
        return _fn
    except Exception:
        pass

    def _fallback(mt, var, catalog_names):
        mt = (mt or "").strip()
        var = (var or "").strip()
        if not mt and not var:
            return ""
        seq = []
        if mt and var:
            seq.extend(("%s %s" % (mt, var), "%s (%s)" % (mt, var)))
        if var:
            seq.append(var)
        if mt:
            seq.append(mt)
        nset = set(catalog_names or [])
        for c in seq:
            if c in nset:
                return c
        cl = [x.lower() for x in seq]
        for n in catalog_names or []:
            if n.lower() in cl:
                return n
        best = ""
        for n in catalog_names or []:
            nl = n.lower()
            ok = (not mt or mt.lower() in nl) and (not var or var.lower() in nl)
            if ok and len(n) > len(best):
                best = n
        if best:
            return best
        return seq[0] if seq else ""

    return _fallback


def _norm_variant_thickness_tree(acc):
    return {mt: {v: sorted(ts) for v, ts in vd.items()} for mt, vd in acc.items()}


def _build_glass_catalog_material_tree(mat_tree, resolve_fn):
    if not mat_tree:
        return {}, []
    names = sorted(
        set(models.get_all_material_names()) | set(models.get_allowed_sheet_material_names())
    )
    acc = {}
    for mt, variants in mat_tree.items():
        if mt in _HIDDEN_WAREHOUSE_MT:
            continue
        for var, triples in (variants or {}).items():
            resolved = resolve_fn(mt, var, names) or ""
            if not resolved:
                resolved = ("%s %s" % (mt, var)).strip() if var else (mt or "").strip()
            if not resolved:
                continue
            for th, _p, _s in triples or []:
                try:
                    thi = int(th)
                except (TypeError, ValueError):
                    continue
                if thi <= 0:
                    continue
                acc.setdefault(mt, {}).setdefault(var, set()).add(thi)
    return _norm_variant_thickness_tree(acc), names


class GlassWarehouseMaterialPicker(QWidget):
    """Выбор материала как в заказе «стекло/зеркало»: тип → цвет/вариант → толщина. Иначе — прежний плоский список."""

    selectionChanged = pyqtSignal()

    def __init__(self, parent=None, mode="sheet", quick_add_cut=False):
        super().__init__(parent)
        self._mode = mode
        self._quick_add_cut = bool(quick_add_cut)
        self._tree = {}
        self._catalog_names = ()
        self._allowed_flat = []
        self._resolve_fn = _try_resolve_sheet_material()
        self._rebuild_model()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._hint = QLabel(
            "Категории как при расчёте заказа «Стекло / зеркало». Если каталог недоступен — ниже простой список."
        )
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color:#555;font-size:11px;")
        root.addWidget(self._hint)

        self.stack = QStackedWidget()
        self.page_h = QWidget()
        hlay = QFormLayout(self.page_h)
        self.combo_mt = QComboBox()
        self.combo_var = QComboBox()
        self.combo_th = QComboBox()
        hlay.addRow("Тип материала:", self.combo_mt)
        hlay.addRow("Цвет / вариант:", self.combo_var)
        hlay.addRow("Толщина (мм):", self.combo_th)

        self.page_l = QWidget()
        llay = QFormLayout(self.page_l)
        self.legacy_name = QComboBox()
        self.legacy_name.setEditable(True)
        self.legacy_name.setInsertPolicy(QComboBox.NoInsert)
        self.legacy_th = QSpinBox()
        self.legacy_th.setRange(1, 30)
        self.legacy_th.setValue(4)
        self.legacy_th.setSuffix(" мм")
        llay.addRow("Материал:", self.legacy_name)
        llay.addRow("Толщина (мм):", self.legacy_th)

        self.stack.addWidget(self.page_h)
        self.stack.addWidget(self.page_l)
        root.addWidget(self.stack)

        self.combo_mt.currentIndexChanged.connect(self._on_mt)
        self.combo_var.currentIndexChanged.connect(self._on_var)
        self.combo_th.currentIndexChanged.connect(self.selectionChanged.emit)
        self.legacy_name.currentTextChanged.connect(self.selectionChanged.emit)
        self.legacy_th.valueChanged.connect(self.selectionChanged.emit)

        self._apply_stack_and_legacy_lists()

    def _rebuild_model(self):
        raw = _try_load_materials_tree()
        # Целые листы и остатки — одно и то же дерево из каталога materials (как в расчёте заказа).
        self._tree, self._catalog_names = _build_glass_catalog_material_tree(raw, self._resolve_fn)
        if self._mode == "sheet":
            self._allowed_flat = models.get_allowed_sheet_material_names()
        else:
            self._allowed_flat = models.get_all_material_names()

    def _apply_stack_and_legacy_lists(self):
        if self._tree:
            self.stack.setCurrentWidget(self.page_h)
            self._hint.setVisible(True)
            if self._mode == "sheet":
                self._hint.setText(
                    "Как при добавлении остатка и в заказе «Стекло / зеркало»: тип → цвет/вариант → толщина."
                )
            else:
                self._hint.setText(
                    "Те же категории, что при расчёте заказа. Ниже можно подставить размер с целого листа на складе."
                )
            self.combo_mt.blockSignals(True)
            self.combo_mt.clear()
            self.combo_mt.addItem("")
            self.combo_mt.addItems(sorted(self._tree.keys()))
            self.combo_mt.blockSignals(False)
            self._on_mt()
        else:
            self.stack.setCurrentWidget(self.page_l)
            if self._mode == "sheet":
                self._hint.setText(
                    "Каталог materials недоступен — выбор материала из списка порогов отходов, как раньше."
                )
            else:
                self._hint.setText(
                    "Каталог materials недоступен — материал из наименований, уже бывших на складе."
                )
            self.legacy_name.blockSignals(True)
            self.legacy_name.clear()
            self.legacy_name.addItem("")
            for n in self._allowed_flat:
                self.legacy_name.addItem(n)
            self.legacy_name.setCurrentIndex(0)
            self.legacy_name.blockSignals(False)
            comp = QCompleter(self._allowed_flat)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            self.legacy_name.setCompleter(comp)
            if self._mode == "sheet":
                self.legacy_name.lineEdit().setPlaceholderText("Материал из списка порогов отходов")
            else:
                self.legacy_name.lineEdit().setPlaceholderText("Материал со склада")

    def uses_hierarchical(self):
        return bool(self._tree)

    def _on_mt(self):
        self.combo_var.blockSignals(True)
        self.combo_th.blockSignals(True)
        self.combo_var.clear()
        self.combo_th.clear()
        mt = self.combo_mt.currentText()
        if mt and mt in self._tree:
            vars_list = (
                _sort_mirror_variants(self._tree[mt].keys())
                if mt == "Зеркало"
                else sorted(self._tree[mt].keys())
            )
            self.combo_var.addItems(vars_list)
            if mt == "Зеркало":
                prefer = -1
                for i in range(self.combo_var.count()):
                    t = (self.combo_var.itemText(i) or "").strip()
                    if _is_silver_colorless_mirror_variant(t):
                        if "бесцвет" in t.lower():
                            self.combo_var.setCurrentIndex(i)
                            prefer = -2
                            break
                        if prefer < 0:
                            prefer = i
                if prefer >= 0:
                    self.combo_var.setCurrentIndex(prefer)
        self.combo_var.blockSignals(False)
        self.combo_th.blockSignals(False)
        self._on_var()

    def _on_var(self):
        self.combo_th.blockSignals(True)
        self.combo_th.clear()
        mt = self.combo_mt.currentText()
        var = self.combo_var.currentText()
        if mt in self._tree and var in self._tree[mt]:
            for th in self._tree[mt][var]:
                self.combo_th.addItem(str(th))
        self.combo_th.blockSignals(False)
        self.selectionChanged.emit()

    def get_material_name(self):
        if self.stack.currentWidget() is self.page_h:
            mt = (self.combo_mt.currentText() or "").strip()
            var = (self.combo_var.currentText() or "").strip()
            if not mt or not var:
                return ""
            return (
                (self._resolve_fn(mt, var, self._catalog_names) or "").strip()
                or ("%s %s" % (mt, var)).strip()
                or mt
            )
        return (self.legacy_name.currentText() or "").strip()

    def get_thickness_mm(self):
        if self.stack.currentWidget() is self.page_h:
            try:
                return max(1, int((self.combo_th.currentText() or "0").strip()))
            except ValueError:
                return 4
        return max(1, int(self.legacy_th.value()))

    @staticmethod
    def _names_equivalent(a, b):
        """Совпадение имён материала: регистр, пробелы, варианты написания «окрашен…»."""
        a = " ".join((a or "").strip().split())
        b = " ".join((b or "").strip().split())
        if not a or not b:
            return False
        if a == b:
            return True
        al, bl = a.lower(), b.lower()
        if al == bl:
            return True

        def _strip_paint_spelling(s):
            return (
                s.replace("окрашенное", "окраш")
                .replace("окрашеное", "окраш")
                .replace("окрашенный", "окраш")
            )

        if _strip_paint_spelling(al) == _strip_paint_spelling(bl):
            return True
        return False

    def apply_resolved_name(self, name: str, thickness_mm: int) -> bool:
        """Подставить тип/вариант/толщину по строке из заказа или раскроя, если она есть в каталоге."""
        name = (name or "").strip()
        try:
            th = int(thickness_mm)
        except (TypeError, ValueError):
            th = 4
        if not name:
            return False
        if self.stack.currentWidget() is self.page_l:
            self.legacy_name.blockSignals(True)
            self.legacy_name.setEditText(name)
            self.legacy_th.setValue(max(1, th))
            self.legacy_name.blockSignals(False)
            self.selectionChanged.emit()
            return True
        if not self._tree:
            return False
        for mt, vdict in self._tree.items():
            for var, ths in vdict.items():
                if th not in ths:
                    continue
                resolved = (
                    (self._resolve_fn(mt, var, self._catalog_names) or "").strip()
                    or ("%s %s" % (mt, var)).strip()
                    or (mt or "").strip()
                )
                if not self._names_equivalent(resolved, name):
                    continue
                im = self.combo_mt.findText(mt)
                if im < 0:
                    continue
                self.combo_mt.blockSignals(True)
                self.combo_mt.setCurrentIndex(im)
                self.combo_mt.blockSignals(False)
                self._on_mt()
                iv = self.combo_var.findText(var)
                if iv < 0:
                    continue
                self.combo_var.blockSignals(True)
                self.combo_var.setCurrentIndex(iv)
                self.combo_var.blockSignals(False)
                self._on_var()
                it = self.combo_th.findText(str(th))
                if it < 0:
                    continue
                self.combo_th.blockSignals(True)
                self.combo_th.setCurrentIndex(it)
                self.combo_th.blockSignals(False)
                self.selectionChanged.emit()
                return True
        return False

    def validate_sheet(self):
        """Целый лист: тот же каскад, что у остатка; в режиме без каталога — имя только из порогов отходов."""
        ok, err = self.validate_remnant()
        if not ok:
            return False, err
        if self.stack.currentWidget() is self.page_l:
            name = self.get_material_name()
            if self._quick_add_cut and name:
                return True, ""
            if name not in self._allowed_flat:
                return (
                    False,
                    "Материал «%s» нет в списке порогов отходов. Выберите из списка." % name,
                )
        return True, ""

    def validate_remnant(self):
        if self.stack.currentWidget() is self.page_h:
            mt = (self.combo_mt.currentText() or "").strip()
            var = (self.combo_var.currentText() or "").strip()
            th_txt = (self.combo_th.currentText() or "").strip()
            if not mt or not var or not th_txt:
                return False, "Выберите тип материала, цвет/вариант и толщину."
        elif not (self.legacy_name.currentText() or "").strip():
            return False, "Выберите или введите материал."
        return True, ""


def _has_profile_stock_api():
    required = (
        'get_profile_stock',
        'insert_profile_stock',
        'delete_profile_stock_and_archive',
        'get_deleted_profile_stock',
    )
    return all(hasattr(models, x) for x in required)


def _fmt_stock_created_at(row):
    dt = row.get('created_at')
    if dt is None:
        return '—'
    if hasattr(dt, 'strftime'):
        try:
            return dt.strftime('%d.%m.%Y %H:%M')
        except Exception:
            return str(dt)[:16]
    return str(dt)[:16]


def _h_esc(x):
    if x is None:
        return ""
    return str(x).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _glass_sheet_source_line(archive):
    if not archive:
        return ""
    st = (archive.get("sheet_type") or "").strip().lower()
    sid = archive.get("sheet_id")
    if st == "full":
        return "Целый со склада"
    if st == "remnant" and sid is not None:
        try:
            rem = models.get_remnant_by_id(int(sid))
        except (TypeError, ValueError):
            rem = None
        if rem:
            num = rem.get("label_number") or rem.get("unique_number")
            return "Остаток со склада № %s" % (num if num is not None else "?")
        return "Остаток со склада"
    return ""


def _glass_one_detail_html(d, remnant_by_id, piece_display_number=None):
    kind = (d.get("item_kind") or "").strip()
    try:
        w = int(d.get("width_mm") or 0)
        h = int(d.get("height_mm") or 0)
    except (TypeError, ValueError):
        w = h = 0
    size = "%d×%d мм" % (w, h)
    if kind == "waste":
        return "<li>%s</li>" % _h_esc("Неделовой отход %s" % size)
    if kind == "piece":
        rec = (d.get("recipient") or "").strip()
        if piece_display_number is not None:
            line = "Изделие K%s %s — %s" % (piece_display_number, size, rec or "—")
        else:
            line = "Изделие %s — %s" % (size, rec or "—")
        return "<li>%s</li>" % _h_esc(line)
    rid = d.get("remnant_id")
    info = remnant_by_id.get(rid) if rid and isinstance(remnant_by_id, dict) else None
    label_no = None
    if info:
        label_no = info.get("label_number") or info.get("unique_number")
    thick = info.get("thickness_mm") if info else None
    tsuf = (" (%s мм)" % thick) if (thick is not None and thick != "") else ""
    if label_no is not None:
        line = "Остаток %s%s → на склад № %s" % (size, tsuf, label_no)
    else:
        line = "Остаток %s%s → на склад" % (size, tsuf)
    return "<li>%s</li>" % _h_esc(line)


def _glass_build_detail_list_html(cut_details, order=None, piece_offset=0):
    if not cut_details:
        return ""
    by_id = {}
    for d in cut_details:
        rid = d.get("remnant_id")
        if rid:
            r = models.get_remnant_by_id(rid)
            if r:
                by_id[rid] = r
    piece_idx = int(piece_offset or 0)
    k_base = None
    if order:
        try:
            k_base = int(order.get("k_number")) if order.get("k_number") is not None else None
        except (TypeError, ValueError):
            k_base = None
    parts = []
    for d in cut_details:
        if (d.get("item_kind") or "") == "piece":
            piece_idx += 1
            disp_no = (k_base + piece_idx - 1) if k_base is not None else piece_idx
            parts.append(_glass_one_detail_html(d, by_id, piece_display_number=disp_no))
        else:
            parts.append(_glass_one_detail_html(d, by_id))
    if not parts:
        return ""
    return "<ul style='margin:4px 0;padding-left:20px;'>%s</ul>" % "".join(parts)


def _parse_hist_details_json(dj):
    if dj is None or dj == "":
        return {}
    if isinstance(dj, dict):
        return dj
    try:
        return json.loads(dj) if isinstance(dj, str) else {}
    except Exception:
        return {}


def _glass_remnant_journal_event_html(h, cut_archive=None, cut_details=None):
    """Одна запись mirror_remnant_history — текст как на WEB /remnant (для created — лист и выкройки)."""
    oid = h.get("order_id")
    at = str(h.get("action_type") or "").strip()
    ui = h.get("user_info")
    user_parts = []
    if ui is not None and ui != "":
        if isinstance(ui, dict):
            for k in ("login", "display", "name", "role"):
                v = ui.get(k)
                if v:
                    user_parts.append(str(v))
            if not user_parts:
                user_parts.append(json.dumps(ui, ensure_ascii=False)[:120])
        else:
            user_parts.append(str(ui).strip())
    user_s = ", ".join(user_parts) if user_parts else ""
    det = _parse_hist_details_json(h.get("details_json"))
    ord_row = None
    if oid is not None:
        try:
            ord_row = models.get_order_for_labels(int(oid))
        except (TypeError, ValueError):
            ord_row = None
    client = ""
    if ord_row:
        client = (ord_row.get("client_name") or ord_row.get("o_client_name") or "").strip()
    okind = str((ord_row or {}).get("order_kind") or "").strip().lower()
    if "facade" in okind:
        kind_ru = "фасадный заказ"
    else:
        kind_ru = "заказ стекло / зеркало"
    lines = []
    if at == "created":
        if det.get("from_cut_layout"):
            same_chain = (
                cut_archive
                and cut_details
                and oid is not None
                and str(oid) == str((cut_archive or {}).get("order_id"))
            )
            if same_chain:
                lines.append(
                    "Остаток <b>создан на складе</b> при сохранении раскроя в %s <b>№ %s</b>."
                    % (kind_ru, _h_esc(oid if oid is not None else "—"))
                )
                if client:
                    lines.append("Клиент: <b>%s</b>." % _h_esc(client))
                inner = _glass_cut_sheet_inner_html(cut_archive, cut_details)
                card = (
                    "<div style='margin:8px 0 0 0;padding:10px;background:#f0f7ff;border-left:4px solid #3b82f6;border-radius:6px;'>"
                    "%s</div>" % inner
                )
                body = "<br/>".join(lines) + card
                if user_s:
                    body += "<br/>Отметка в системе: %s." % _h_esc(user_s[:220])
                return body
            lines.append(
                "Остаток <b>создан на складе</b> при сохранении чернового раскроя в %s <b>№ %s</b>."
                % (kind_ru, _h_esc(oid if oid is not None else "—"))
            )
            if client:
                lines.append("Клиент: <b>%s</b>." % _h_esc(client))
        elif det.get("from_order_complete"):
            fo = det.get("from_cut") or oid
            lines.append(
                "Остаток <b>зафиксирован</b> при завершении %s <b>№ %s</b> — перенос в архив реза."
                % (kind_ru, _h_esc(fo if fo is not None else "—"))
            )
            if client:
                lines.append("Клиент: <b>%s</b>." % _h_esc(client))
        else:
            lines.append("Событие «создание остатка» по %s <b>№ %s</b>." % (kind_ru, _h_esc(oid if oid is not None else "—")))
            if client:
                lines.append("Клиент: <b>%s</b>." % _h_esc(client))
            if det:
                lines.append("Детали: %s." % _h_esc(json.dumps(det, ensure_ascii=False)[:400]))
    elif at == "created_from_cancelled_order":
        o_del = det.get("order_id") or oid
        lines.append(
            "Остаток создан при <b>удалении заказа</b> № %s — изделия из архива раскроя переведены на склад."
            % _h_esc(o_del if o_del is not None else "—")
        )
        if client:
            lines.append("Клиент (по заказу): <b>%s</b>." % _h_esc(client))
        if det.get("cut_archive_id"):
            lines.append("Архив реза (ссылка в БД): %s." % _h_esc(det.get("cut_archive_id")))
    else:
        lines.append("Тип события: <b>%s</b>." % _h_esc(at or "—"))
        if oid is not None:
            lines.append("%s № %s." % (kind_ru, _h_esc(oid)))
        if client:
            lines.append("Клиент: <b>%s</b>." % _h_esc(client))
        if det:
            lines.append("Доп. данные: %s." % _h_esc(json.dumps(det, ensure_ascii=False)[:400]))
    if user_s:
        lines.append("Отметка в системе: %s." % _h_esc(user_s[:220]))
    return "<br/>".join(lines)


def _glass_client_cut_lines_fragment(cut_archive, cut_details):
    """Строки «Рез для клиента … — дата» как на странице WEB /remnant (без ссылок)."""
    if not cut_details or not cut_archive:
        return ""
    date_str = ""
    if cut_archive.get("cut_date"):
        dt = cut_archive["cut_date"]
        date_str = dt.strftime("%d.%m.%Y %H:%M") if hasattr(dt, "strftime") else str(dt)
    by_recipient = {}
    for det in cut_details:
        if (det.get("item_kind") or "").strip() != "piece":
            continue
        rec = (det.get("recipient") or "").strip() or "—"
        try:
            w = int(det.get("width_mm") or 0)
            h = int(det.get("height_mm") or 0)
        except (TypeError, ValueError):
            w = h = 0
        size = "%d×%d" % (w, h) if (w or h) else ""
        if rec not in by_recipient:
            by_recipient[rec] = []
        if size:
            by_recipient[rec].append(size)
    main_name = (cut_archive.get("client_name") or "").strip()
    lines = []
    for rec, sizes in by_recipient.items():
        sizes_str = ", ".join(sizes) if sizes else ""
        if len(by_recipient) == 1:
            line_text = "Рез для клиента %s — %s" % (rec or "—", date_str)
        else:
            line_text = "%s %s мм — %s" % (rec or "—", sizes_str, date_str)
        lines.append(_h_esc(line_text))
    if not lines and main_name:
        lines.append(_h_esc("Рез для клиента %s — %s" % (main_name, date_str)))
    if not lines:
        return ""
    return "<div style='margin:6px 0 4px 0;font-size:12px;color:#334155;'>" + "<br/>".join(lines) + "</div>"


def _glass_cut_sheet_inner_html(archive, cut_details):
    """Шапка листа + рез для клиента + список вырезанного (как WEB /remnant)."""
    if not archive or not cut_details:
        return ""
    order = models.get_order_for_labels(archive.get("order_id")) if archive.get("order_id") else None
    sheet_name = archive.get("sheet_name") or "Лист"
    sh = int(archive.get("sheet_height_mm") or 0)
    sw = int(archive.get("sheet_width_mm") or 0)
    date_str = ""
    if archive.get("cut_date"):
        dt = archive["cut_date"]
        date_str = dt.strftime("%d.%m.%Y %H:%M") if hasattr(dt, "strftime") else str(dt)
    client = (archive.get("client_name") or "").strip() or "—"
    order_id = archive.get("order_id")
    src = _glass_sheet_source_line(archive)
    head_parts = []
    if src:
        head_parts.append(_h_esc(src))
    head_parts.append(
        "Лист %s %d×%d мм · %s · Заказ #%s · Клиент: %s"
        % (_h_esc(sheet_name), sw, sh, _h_esc(date_str), _h_esc(order_id), _h_esc(client))
    )
    header = " · ".join(head_parts)
    client_frag = _glass_client_cut_lines_fragment(archive, cut_details)
    details_html = _glass_build_detail_list_html(cut_details, order)
    out = "<b>%s</b>%s" % (header, client_frag)
    if details_html:
        out += (
            "<div style='margin-top:8px;font-size:12px;color:#0f172a;'><b>Что вырезано из листа:</b></div>%s"
            % details_html
        )
    return out


def _glass_cut_block_html(archive, cut_details):
    if not archive or not cut_details:
        return ""
    inner = _glass_cut_sheet_inner_html(archive, cut_details)
    return (
        "<div style='margin:10px 0;padding:10px;background:#f0f7ff;border-left:4px solid #3b82f6;border-radius:6px;'>"
        "%s</div>" % inner
    )


def _glass_remnant_history_html(remnant):
    rid = remnant.get("id")
    if rid is None:
        return "<p>Нет идентификатора остатка.</p>"
    num_disp = remnant.get("label_number") or remnant.get("unique_number") or "—"
    cut_archive, cut_details = models.get_cut_archive_by_remnant_id(rid)
    used_archive, used_details = models.get_cut_archive_where_remnant_used_as_sheet(rid)
    blocks = []
    blocks.append(
        "<p style='color:#444;font-size:13px;'>Остаток стекла <b>№ %s</b> · %s · %s мм · %d×%d мм</p>"
        % (
            _h_esc(num_disp),
            _h_esc(remnant.get("name") or "—"),
            _h_esc(remnant.get("thickness_mm") or "—"),
            int(remnant.get("width_mm") or 0),
            int(remnant.get("height_mm") or 0),
        )
    )
    blocks.append("<h3 style='margin:12px 0 6px 0;'>Откуда лист и что вырезано (текст)</h3>")
    if cut_archive and cut_details:
        blocks.append(_glass_cut_block_html(cut_archive, cut_details))
    if used_archive and used_details:
        u_order = models.get_order_for_labels(used_archive.get("order_id")) if used_archive.get("order_id") else None
        ds = ""
        if used_archive.get("cut_date"):
            dt = used_archive["cut_date"]
            ds = dt.strftime("%d.%m.%Y %H:%M") if hasattr(dt, "strftime") else str(dt)
        head = "Остаток № %s использован в резе · %s · Заказ #%s · Клиент: %s" % (
            _h_esc(num_disp),
            _h_esc(ds),
            _h_esc(used_archive.get("order_id")),
            _h_esc((used_archive.get("client_name") or "").strip() or "—"),
        )
        client_frag = _glass_client_cut_lines_fragment(used_archive, used_details)
        details_ul = _glass_build_detail_list_html(used_details, u_order)
        inner_used = "<b>%s</b>%s" % (head, client_frag)
        if details_ul:
            inner_used += (
                "<div style='margin-top:8px;font-size:12px;color:#0f172a;'><b>Что вырезано из этого листа:</b></div>%s"
                % details_ul
            )
        blocks.append(
            "<div style='margin:10px 0;padding:10px;background:#fffbeb;border-left:4px solid #d97706;border-radius:6px;'>"
            "%s</div>" % inner_used
        )
    elif not (cut_archive and cut_details):
        blocks.append(
            "<p style='padding:8px;background:#ecfdf5;border-radius:6px;'>Остаток № %s — на складе (архив создания пока не привязан).</p>"
            % _h_esc(num_disp)
        )
    else:
        blocks.append(
            "<p style='padding:8px;background:#ecfdf5;border-radius:6px;'>Остаток № %s — на складе.</p>" % _h_esc(num_disp)
        )
    return "".join(blocks)


class HoldFillButton(QPushButton):
    """Кнопка: удержание ~1 с с вертикальной заливкой снизу вверх (цвет по умолчанию зелёный)."""

    holdComplete = pyqtSignal()

    def __init__(self, text, hold_ms=1000, fill_color=None, parent=None):
        super().__init__(text, parent)
        self._hold_ms = max(200, int(hold_ms))
        self._fill = fill_color if fill_color is not None else QColor(34, 197, 94)
        self._progress = 0.0
        self._active = False
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._on_tick)
        self.setMinimumHeight(36)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._active = True
            self._progress = 0.0
            self._timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._active:
            self._timer.stop()
            self._active = False
            self._progress = 0.0
            self.update()
        super().mouseReleaseEvent(event)

    def _on_tick(self):
        if not self._active:
            return
        self._progress += 40.0 / float(self._hold_ms)
        if self._progress >= 1.0:
            self._timer.stop()
            self._active = False
            self._progress = 0.0
            self.holdComplete.emit()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(237, 242, 247))
        p.drawRoundedRect(r, 5, 5)
        if self._progress > 0:
            fh = int(r.height() * self._progress)
            if fh > 0:
                p.setBrush(self._fill)
                p.drawRoundedRect(QRect(r.left(), r.bottom() - fh, r.width(), fh), 5, 5)
        p.setPen(QColor(30, 41, 59))
        p.setFont(self.font())
        p.drawText(r, Qt.AlignCenter, self.text())


class HoldDeleteButtonLTR(QPushButton):
    """Удержание ~1 с: красная заливка слева направо, затем holdComplete."""

    holdComplete = pyqtSignal()

    def __init__(self, text="Удалить", hold_ms=1000, parent=None):
        super().__init__(text, parent)
        self._hold_ms = max(200, int(hold_ms))
        self._fill = QColor(220, 38, 38)
        self._progress = 0.0
        self._active = False
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._on_tick)
        self.setMinimumHeight(22)
        self.setMaximumHeight(26)
        self.setStyleSheet("HoldDeleteButtonLTR { font-size: 9px; color: #374151; padding: 2px 4px; }")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._active = True
            self._progress = 0.0
            self._timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._active:
            self._timer.stop()
            self._active = False
            self._progress = 0.0
            self.update()
        super().mouseReleaseEvent(event)

    def _on_tick(self):
        if not self._active:
            return
        self._progress += 40.0 / float(self._hold_ms)
        if self._progress >= 1.0:
            self._timer.stop()
            self._active = False
            self._progress = 0.0
            self.holdComplete.emit()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.setPen(QPen(QColor(200, 210, 220)))
        p.setBrush(QColor(249, 250, 251))
        p.drawRoundedRect(r.adjusted(0, 0, -1, -1), 4, 4)
        if self._progress > 0:
            fw = int(r.width() * self._progress)
            if fw > 0:
                p.setPen(Qt.NoPen)
                p.setBrush(self._fill)
                p.drawRoundedRect(QRect(r.left(), r.top(), fw, r.height()), 4, 4)
        p.setPen(QColor(30, 41, 59))
        p.setFont(self.font())
        p.drawText(r.adjusted(4, 0, -4, 0), Qt.AlignCenter, self.text())


class HoldDeleteButtonRTL(QPushButton):
    """Удержание ~1 с: красная заливка справа налево, затем holdComplete."""

    holdComplete = pyqtSignal()

    def __init__(self, text="Удалить", hold_ms=1000, parent=None):
        super().__init__(text, parent)
        self._hold_ms = max(200, int(hold_ms))
        self._fill = QColor(220, 38, 38)
        self._progress = 0.0
        self._active = False
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._on_tick)
        self.setMinimumHeight(34)
        self.setMinimumWidth(120)
        self.setStyleSheet(
            "HoldDeleteButtonRTL { font-size: 12px; font-weight: 600; color: #374151; padding: 6px 14px; }"
            "HoldDeleteButtonRTL:disabled { color: #9ca3af; }"
        )

    def mousePressEvent(self, event):
        if not self.isEnabled():
            super().mousePressEvent(event)
            return
        if event.button() == Qt.LeftButton:
            self._active = True
            self._progress = 0.0
            self._timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._active:
            self._timer.stop()
            self._active = False
            self._progress = 0.0
            self.update()
        super().mouseReleaseEvent(event)

    def _on_tick(self):
        if not self._active:
            return
        self._progress += 40.0 / float(self._hold_ms)
        if self._progress >= 1.0:
            self._timer.stop()
            self._active = False
            self._progress = 0.0
            self.holdComplete.emit()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.setPen(QPen(QColor(200, 210, 220)))
        p.setBrush(QColor(249, 250, 251))
        p.drawRoundedRect(r.adjusted(0, 0, -1, -1), 5, 5)
        if self._progress > 0:
            fw = int(r.width() * self._progress)
            if fw > 0:
                p.setPen(Qt.NoPen)
                p.setBrush(self._fill)
                p.drawRoundedRect(QRect(r.right() - fw + 1, r.top(), fw, r.height()), 5, 5)
        p.setPen(QColor(30, 41, 59))
        p.setFont(self.font())
        p.drawText(r.adjusted(6, 0, -6, 0), Qt.AlignCenter, self.text())


class QueueHoldRemoveCard(QPushButton):
    """Карточка очереди: удержание — красная заливка, затем сигнал удаления по uid."""

    removeUid = pyqtSignal(int)

    def __init__(self, lines_text, uid, hold_ms=900, parent=None):
        super().__init__(lines_text, parent)
        self._uid = int(uid)
        self._hold_ms = max(200, int(hold_ms))
        self._fill = QColor(239, 68, 68)
        self._progress = 0.0
        self._active = False
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._on_tick)
        self.setMinimumHeight(52)
        self.setStyleSheet("QueueHoldRemoveCard { text-align: left; padding: 8px 10px; font-size: 11px; }")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._active = True
            self._progress = 0.0
            self._timer.start()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._active:
            self._timer.stop()
            self._active = False
            self._progress = 0.0
            self.update()
        super().mouseReleaseEvent(event)

    def _on_tick(self):
        if not self._active:
            return
        self._progress += 40.0 / float(self._hold_ms)
        if self._progress >= 1.0:
            self._timer.stop()
            self._active = False
            self._progress = 0.0
            self.removeUid.emit(self._uid)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.setPen(QPen(QColor(180, 198, 220)))
        p.setBrush(QColor(248, 250, 252))
        p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 6, 6)
        if self._progress > 0:
            fh = int(r.height() * self._progress)
            if fh > 0:
                p.setPen(Qt.NoPen)
                p.setBrush(self._fill)
                p.drawRoundedRect(QRect(r.left(), r.bottom() - fh, r.width(), fh), 6, 6)
        p.setPen(QColor(30, 41, 59))
        p.setFont(self.font())
        p.drawText(r.adjusted(10, 0, -10, 0), Qt.AlignVCenter | Qt.AlignLeft, self.text())


class ProfileStockHistoryDialog(QDialog):
    """История списаний профиля с фасадных заказов."""

    def __init__(self, profile_ref_id, subtitle, parent=None):
        super().__init__(parent)
        self.setWindowTitle("История использования профиля")
        self.setMinimumSize(720, 480)
        lay = QVBoxLayout(self)
        head = QLabel("<b>%s</b><br/><span style='color:#555;'>Списания со склада при расчёте фасадов</span>" % (subtitle.replace('<', '')))
        head.setWordWrap(True)
        lay.addWidget(head)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setStyleSheet("QTextEdit { font-family: Segoe UI, Arial; font-size: 12px; }")
        if not hasattr(models, 'get_profile_stock_usage_by_ref'):
            text.setPlainText("Таблица истории недоступна. Обновите приложение и перезапустите.")
        else:
            rows = []
            err = None
            try:
                rows = models.get_profile_stock_usage_by_ref(int(profile_ref_id), 250) or []
            except Exception as e:
                err = e
            if err is not None:
                text.setPlainText("Ошибка загрузки: %s" % err)
            elif not rows:
                text.setHtml("<p style='color:#555;'>Записей пока нет — профиль ещё не списывался с фасадных заказов.</p>")
            else:
                lines = []
                side_ru = {'top': 'верх', 'bottom': 'низ', 'left': 'лево', 'right': 'право'}
                for r in rows:
                    dt = r.get('used_at')
                    dt_s = dt.strftime('%d.%m.%Y %H:%M') if dt and hasattr(dt, 'strftime') else str(dt or '—')
                    client = (r.get('client_name') or '—').strip() or '—'
                    sk = (r.get('side') or '').lower()
                    side = side_ru.get(sk, r.get('side') or '—')
                    fw = r.get('facade_width_mm')
                    fh = r.get('facade_height_mm')
                    sz = ('%s×%s мм' % (fw, fh)) if fw and fh else '—'
                    lines.append(
                        "<p style='margin:8px 0; padding:8px; background:#f4f8ff; border-radius:6px; border-left:4px solid #6a9ee8;'>"
                        "<b>%s</b> &nbsp; сторона <b>%s</b><br/>"
                        "Клиент: %s<br/>"
                        "Фасад: %s &nbsp;·&nbsp; рез: %s мм &nbsp;·&nbsp; остаток после: %s мм</p>"
                        % (
                            dt_s,
                            side,
                            client.replace('<', ''),
                            sz,
                            r.get('required_mm') if r.get('required_mm') is not None else '—',
                            r.get('remnant_mm') if r.get('remnant_mm') is not None else '—',
                        )
                    )
                text.setHtml("".join(lines))
        lay.addWidget(text, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)


class _ProfileCutChainBarWidget(QFrame):
    """Интерактивная «балка» цепочки резов: клик по сегменту — карточка с заказом, мм, типом."""

    def __init__(self, segments, canvas_w, canvas_h, parent=None):
        super().__init__(parent)
        self._segs = list(segments or [])
        self._cw = max(120, int(canvas_w))
        self._ch = max(60, int(canvas_h))
        self.setFixedSize(self._cw, self._ch)
        self.setCursor(QCursor(Qt.PointingHandCursor))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#fafbff"))
        p.setPen(QPen(QColor("#c5cae9"), 1))
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        for s in self._segs:
            x = int(s.get("x") or 0)
            y = int(s.get("y") or 0)
            w = max(1, int(s.get("w") or 1))
            h = max(1, int(s.get("h") or 1))
            col = QColor(str(s.get("fill") or "#adb5bd"))
            if not col.isValid():
                col = QColor("#adb5bd")
            p.fillRect(x, y, w, h, col)
            p.setPen(QPen(QColor("#222222"), 1))
            p.drawRect(x, y, w, h)
        p.end()

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        pos = e.pos()
        for s in reversed(self._segs):
            r = QRect(int(s.get("x") or 0), int(s.get("y") or 0), max(1, int(s.get("w") or 1)), max(1, int(s.get("h") or 1)))
            if r.contains(pos):
                tip = str(s.get("tip") or "").replace("<", "").replace("&lt;", "")
                QMessageBox.information(self, "Сегмент цепочки резов", tip or "Нет данных.")
                return
        super().mousePressEvent(e)


class ProfileStockRowHistoryDialog(QDialog):
    """История по конкретной строке склада (stock id): журнал mirror_profile_remnant_history + списания mirror_profile_stock_usage."""

    def _open_mirror_order(self, oid: int):
        row = models.get_order(int(oid))
        if not row:
            QMessageBox.warning(self, "Заказ", "Заказ №%s не найден." % oid)
            return
        try:
            from ui.glass_order_overview_dialog import GlassOrderOverviewDialog

            GlassOrderOverviewDialog(row, self, summary_only=True).exec_()
        except Exception as e:
            QMessageBox.warning(self, "Заказ", "Не удалось открыть карточку: %s" % e)

    def _open_mirror_client(self, cid: int):
        row = models.get_client_by_id(int(cid))
        if not row:
            QMessageBox.warning(self, "Клиент", "Клиент не найден.")
            return
        try:
            from ui.new_client_dialog import NewClientDialog

            NewClientDialog(self, edit_client=dict(row)).exec_()
        except Exception as e:
            QMessageBox.warning(self, "Клиент", "Не удалось открыть карточку: %s" % e)

    def _on_history_anchor(self, url):
        s = (url.toString() if isinstance(url, QUrl) else str(url or "")).strip()
        if s.startswith("mirror://order/"):
            try:
                oid = int(s.rsplit("/", 1)[-1])
            except (TypeError, ValueError):
                return
            self._open_mirror_order(oid)
            return
        if s.startswith("mirror://client/"):
            try:
                cid = int(s.rsplit("/", 1)[-1])
            except (TypeError, ValueError):
                return
            self._open_mirror_client(cid)

    def __init__(self, stock_row, parent=None):
        super().__init__(parent)
        sid = stock_row.get("id")
        self.setWindowTitle("История позиции склада № %s" % (sid if sid is not None else "—"))
        self.setMinimumSize(720, 480)
        lay = QVBoxLayout(self)
        sub = "%s — %s · %s мм · %s" % (
            stock_row.get("series") or "",
            stock_row.get("name") or "—",
            stock_row.get("length_mm") if stock_row.get("length_mm") is not None else "—",
            "остаток" if stock_row.get("is_remnant") else "целый",
        )
        head = QLabel(
            "<b>%s</b><br/><span style='color:#555;'>События по этой строке и списания, где consumed_stock_id = эта позиция</span>"
            % (sub.replace("<", ""))
        )
        head.setWordWrap(True)
        lay.addWidget(head)
        text = QTextBrowser()
        text.setReadOnly(True)
        text.setOpenExternalLinks(False)
        text.anchorClicked.connect(self._on_history_anchor)
        text.setStyleSheet("QTextBrowser { font-family: Segoe UI, Arial; font-size: 12px; }")
        viz = {}
        if sid is not None and hasattr(models, "get_profile_stock_visualization"):
            try:
                viz = models.get_profile_stock_visualization(int(sid), svg_width=700) or {}
            except Exception:
                viz = {}
        segs = viz.get("click_segments") or []
        if segs:
            viz_lbl = QLabel("Шкала цепочки резов (нажмите на цветной сегмент)")
            viz_lbl.setStyleSheet("font-weight:600;color:#1a237e;margin-top:4px;")
            lay.addWidget(viz_lbl)
            viz_wrap = QWidget()
            vwl = QVBoxLayout(viz_wrap)
            vwl.setContentsMargins(0, 0, 0, 0)
            ch = int(viz.get("click_canvas_height") or 120)
            cw = int(viz.get("click_canvas_width") or 700)
            vwl.addWidget(_ProfileCutChainBarWidget(segs, cw, ch))
            leg = QLabel(
                "<span style='font-size:11px;color:#555'>"
                "<span style='color:#5c7cfa'>■</span> снято "
                "<span style='color:#dc3545'>■</span> отход "
                "<span style='color:#34a853'>■</span> остаток</span>"
            )
            vwl.addWidget(leg)
            lay.addWidget(viz_wrap)
        elif (viz.get("svg_html") or "").strip():
            viz_lbl = QLabel("Цепочка резов")
            viz_lbl.setStyleSheet("font-weight:600;color:#1a237e;margin-top:4px;")
            lay.addWidget(viz_lbl)
            tb = QTextBrowser()
            tb.setReadOnly(True)
            tb.setFixedHeight(80)
            tb.setHtml(viz.get("svg_html") or "")
            lay.addWidget(tb)
        if not hasattr(models, "get_profile_history_rich"):
            text.setPlainText("Функция истории недоступна. Обновите приложение.")
        elif sid is None:
            text.setPlainText("Нет stock id.")
        else:
            try:
                rich = models.get_profile_history_rich(int(sid)) or {}
            except Exception as e:
                text.setPlainText("Ошибка: %s" % e)
                rich = None
            if rich is not None:
                hist = rich.get("history") or []
                usage = rich.get("usage") or []
                cut_events = rich.get("cut_events") or []
                if not hist and not usage and not cut_events:
                    ref_id = stock_row.get("ref_id")
                    hint = ""
                    if ref_id is not None and hasattr(models, "get_profile_stock_usage_by_ref"):
                        try:
                            by_ref = models.get_profile_stock_usage_by_ref(int(ref_id), 80) or []
                        except Exception:
                            by_ref = []
                        if by_ref:
                            hint = (
                                "<p style='color:#555;'>По этой строке событий пока нет. "
                                "Ниже — все списания по типу профиля (ref %s) при расчёте фасадов:</p>" % ref_id
                            )
                            lines = []
                            for r in by_ref:
                                dt = r.get("used_at")
                                dt_s = dt.strftime("%d.%m.%Y %H:%M") if dt and hasattr(dt, "strftime") else str(dt or "—")
                                lines.append(
                                    "<p style='margin:6px 0;padding:6px;background:#f8f9fa;border-radius:6px'>%s — клиент %s, рез %s мм, остаток %s мм</p>"
                                    % (
                                        dt_s,
                                        (r.get("client_name") or "—").replace("<", ""),
                                        r.get("required_mm") if r.get("required_mm") is not None else "—",
                                        r.get("remnant_mm") if r.get("remnant_mm") is not None else "—",
                                    )
                                )
                            text.setHtml(hint + "".join(lines))
                        else:
                            text.setHtml(
                                "<p style='color:#555;'>Записей пока нет. После списания профиля при расчёте фасада или резерва со склада здесь появятся события.</p>"
                            )
                    else:
                        text.setHtml(
                            "<p style='color:#555;'>Записей пока нет. После списания профиля при расчёте фасада или резерва со склада здесь появятся события.</p>"
                        )
                else:
                    parts = []
                    if cut_events:
                        parts.append("<h3 style='margin:12px 0 6px 0;'>События реза/склада</h3>")
                        for ev in cut_events:
                            et_raw = str(ev.get("event_type") or "")
                            if "label_created" in et_raw:
                                continue
                            dt = ev.get("created_at")
                            dt_s = dt.strftime("%d.%m.%Y %H:%M") if dt and hasattr(dt, "strftime") else str(dt or "—")
                            det_ev = ""
                            if hasattr(models, "format_profile_cut_event_html"):
                                try:
                                    det_ev = models.format_profile_cut_event_html(dict(ev))
                                except Exception:
                                    det_ev = ""
                            if not det_ev:
                                actor = (ev.get("actor_display") or ev.get("actor_login") or "—").replace("<", "")
                                reason = (ev.get("reason_text") or "").replace("<", "")
                                det_ev = "%s · %s%s" % (
                                    (ev.get("event_type") or "—").replace("<", ""),
                                    actor,
                                    (" · причина: " + reason) if reason else "",
                                )
                            parts.append(
                                "<p style='margin:6px 0;padding:8px;background:#eef2ff;border-left:4px solid #6366f1;border-radius:4px'>"
                                "<b>%s</b><br/><span style='color:#333;font-size:12px;line-height:1.4'>%s</span></p>"
                                % (dt_s, det_ev)
                            )
                    if hist:
                        parts.append("<h3 style='margin:12px 0 6px 0;'>Журнал</h3>")
                        for h in hist:
                            at_h = str(h.get("action_type") or "").strip()
                            det_html = ""
                            if hasattr(models, "format_profile_remnant_history_details_html"):
                                try:
                                    det_html = models.format_profile_remnant_history_details_html(dict(h))
                                except Exception:
                                    det_html = ""
                            if not det_html:
                                det_raw = h.get("details_json") or "{}"
                                det_html = str(det_raw).replace("<", "")[:500]
                            if at_h == "label_created":
                                parts.append(
                                    "<p style='margin:6px 0;padding:8px;background:#f4f8ff;border-left:4px solid #6a9ee8;border-radius:4px'>"
                                    "<span style='color:#444;font-size:13px;line-height:1.4'>%s</span></p>" % (det_html,)
                                )
                                continue
                            dt = h.get("created_at")
                            dt_s = dt.strftime("%d.%m.%Y %H:%M") if dt and hasattr(dt, "strftime") else str(dt or "—")
                            oid = h.get("order_id")
                            cid = h.get("order_client_id")
                            cn = (h.get("client_name") or "—").replace("<", "")
                            if oid is not None:
                                try:
                                    ord_cell = '<a href="mirror://order/%s">№%s</a>' % (int(oid), int(oid))
                                except (TypeError, ValueError):
                                    ord_cell = html_std.escape(str(oid))
                            else:
                                ord_cell = "—"
                            if cid and cn and cn != "—":
                                try:
                                    cli_cell = '<a href="mirror://client/%s">%s</a>' % (int(cid), html_std.escape(cn))
                                except (TypeError, ValueError):
                                    cli_cell = html_std.escape(cn)
                            else:
                                cli_cell = html_std.escape(cn)
                            parts.append(
                                "<p style='margin:6px 0;padding:8px;background:#f4f8ff;border-left:4px solid #6a9ee8;border-radius:4px'>"
                                "<b>%s</b> · %s<br/><span style='color:#444;font-size:12px;line-height:1.35'>%s</span><br/>"
                                "<span style='color:#666;font-size:11px'>Заказ: %s · клиент: %s</span></p>"
                                % (
                                    dt_s,
                                    (h.get("action_type") or "—").replace("<", ""),
                                    det_html,
                                    ord_cell,
                                    cli_cell,
                                )
                            )
                    if usage:
                        parts.append("<h3 style='margin:12px 0 6px 0;'>Списания (расчёт фасада)</h3>")
                        for u in usage:
                            dt = u.get("used_at")
                            dt_s = dt.strftime("%d.%m.%Y %H:%M") if dt and hasattr(dt, "strftime") else str(dt or "—")
                            summ = ""
                            if hasattr(models, "profile_facade_usage_reason_ru"):
                                try:
                                    summ = models.profile_facade_usage_reason_ru(dict(u))
                                except Exception:
                                    summ = ""
                            if not summ:
                                summ = "%s · клиент %s · фасад %s×%s мм · рез %s мм · остаток после %s мм" % (
                                    (u.get("side") or "—").replace("<", ""),
                                    (u.get("client_name") or "—").replace("<", ""),
                                    u.get("facade_width_mm") or "—",
                                    u.get("facade_height_mm") or "—",
                                    u.get("required_mm") if u.get("required_mm") is not None else "—",
                                    u.get("remnant_mm") if u.get("remnant_mm") is not None else "—",
                                )
                            parts.append(
                                "<p style='margin:6px 0;padding:8px;background:#f0fdf4;border-left:4px solid #22c55e;border-radius:4px'>"
                                "<b>%s</b><br/><span style='color:#333;font-size:12px;line-height:1.4'>%s</span></p>"
                                % (dt_s, html_std.escape(summ))
                            )
                    text.setHtml("".join(parts))
        lay.addWidget(text, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)


class CompactProfileStockRow(QFrame):
    """Одна компактная строка склада профилей: миниатюра + текст + дата + кнопка «История» для остатков."""

    def __init__(
        self,
        row,
        is_remnant,
        parent=None,
        on_history=None,
        profile_by_ref=None,
        label_by_stock=None,
        on_hold_delete=None,
    ):
        super().__init__(parent)
        self.setStyleSheet(
            "CompactProfileStockRow { background: #f6f9ff; border: 1px solid #b8ccef; border-radius: 5px; }"
        )
        use_hold_delete = bool(on_hold_delete)
        self.setMaximumHeight(92 if use_hold_delete else 58)
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(2)
        lay = QHBoxLayout()
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(8)
        thumb = QLabel()
        thumb.setFixedSize(36, 36)
        thumb.setAlignment(Qt.AlignCenter)
        thumb.setStyleSheet("background:#e4eaf5;border:1px solid #c5d2e8;border-radius:3px;")
        photo_no = None
        if row.get('item_type') == 'profile' and row.get('ref_id'):
            rid = int(row['ref_id'])
            pr = None
            if profile_by_ref is not None and rid in profile_by_ref:
                pr = profile_by_ref[rid]
            elif facades_get_profile_by_id:
                try:
                    pr = facades_get_profile_by_id(rid)
                except Exception:
                    pr = None
            if pr:
                photo_no = pr.get('photo_number')
        path = _warehouse_profile_img_path(pr) if pr else _warehouse_fasad_img_path(photo_no)
        if path and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(36, 36, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        else:
            thumb.setText("—")
        lay.addWidget(thumb)
        t = "Профиль" if row.get('item_type') == 'profile' else "Петля"
        nm = (row.get('name') or '—')[:32]
        ser = (row.get('series') or '')[:18]
        ln = row.get('length_mm')
        ln_s = str(ln) if ln is not None else '—'
        qty = row.get('quantity') or 1
        dt_s = _fmt_stock_created_at(row)
        lab_txt = ""
        if is_remnant and row.get("id") is not None:
            try:
                lr = None
                if isinstance(label_by_stock, dict):
                    lr = label_by_stock.get(int(row["id"]))
                if lr is None:
                    lr = models.get_profile_label_by_stock_id(int(row["id"]))
                if lr:
                    uq = (lr.get("unique_number") or "").strip()
                    num = lr.get("label_number")
                    if uq:
                        disp = uq.replace("<", "")
                        ul = disp.upper()
                        if not ul.startswith("S") and not ul.startswith("P"):
                            disp = "S%s" % disp.lstrip("S").lstrip("s")
                        elif ul.startswith("P") and len(disp) > 1 and disp[1:].isdigit():
                            disp = "S%s" % disp[1:]
                        lab_txt = " · <span style='color:#0f4c81;font-weight:600'>%s</span>" % (disp,)
                    elif num is not None:
                        lab_txt = " · <span style='color:#0f4c81;font-weight:600'>S%s</span>" % (int(num),)
            except Exception:
                pass
        line = "<span style='font-weight:600;'>%s</span> %s · %s · %s мм · ×%s · <span style='color:#666;'>%s</span>%s" % (
            t, ser, nm, ln_s, qty, dt_s, lab_txt)
        lbl = QLabel(line)
        lbl.setTextFormat(Qt.RichText)
        lbl.setStyleSheet("font-size:11px;")
        lay.addWidget(lbl, 1)
        col = (row.get('color') or '')[:24]
        ctag = QLabel(col or '—')
        c_low = (col or '').lower()
        st = "font-size:10px;padding:2px 6px;border-radius:4px;"
        if 'черн' in c_low:
            st += "background:#333;color:#fff;"
        elif 'бел' in c_low:
            st += "background:#f5f5f5;color:#222;"
        elif 'сереб' in c_low:
            st += "background:#c9d0d8;color:#222;"
        elif 'золот' in c_low:
            st += "background:#e8d080;color:#222;"
        elif 'коньяк' in c_low:
            st += "background:#c49a72;color:#222;"
        elif 'шампан' in c_low:
            st += "background:#e5d5a8;color:#222;"
        else:
            st += "background:#dde8ff;color:#223;"
        ctag.setStyleSheet(st)
        lay.addWidget(ctag)
        if is_remnant and row.get('item_type') == 'profile' and row.get('ref_id') and on_history:
            hb = QPushButton("История")
            hb.setFixedHeight(24)
            hb.setToolTip("Где и для какого заказа списывался этот профиль")
            hb.clicked.connect(lambda: on_history(row))
            lay.addWidget(hb)
        main_lay.addLayout(lay)
        if use_hold_delete:
            del_btn = HoldDeleteButtonLTR()
            if is_remnant:
                del_btn.setToolTip("Удерживайте ~1 с — остаток профиля уйдёт в архив удалённых")
            else:
                del_btn.setToolTip("Удерживайте ~1 с — позиция уйдёт в архив удалённых (вся строка склада)")
            del_btn.holdComplete.connect(lambda: on_hold_delete(row))
            main_lay.addWidget(del_btn)


class RemnantCompactTile(QFrame):
    """Плитка остатка: номер с этикетки в верхней полосе, размеры ниже. Клик — история резов."""
    clicked = None

    def __init__(self, name, thickness_mm, w_mm, h_mm, extra=None, parent=None):
        super().__init__(parent)
        self.name = name or ""
        self.thickness_mm = thickness_mm
        self.w_mm = w_mm
        self.h_mm = h_mm
        self.extra = extra or {}
        self.setMinimumSize(100, 72)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            "RemnantCompactTile { background: #f0f8ff; border: 1px solid #B0C4DE; border-radius: 4px; } "
            "RemnantCompactTile:hover { background: #E0EFFF; }"
        )

    def sizeHint(self):
        return QSize(120, 72)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.clicked:
            self.clicked(self.extra)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        qp = QPainter(self)
        qp.setRenderHint(QPainter.TextAntialiasing)
        r = self.rect()
        band_h = 17
        top_rect = QRect(r.left(), r.top(), r.width(), band_h)
        qp.setPen(Qt.NoPen)
        qp.setBrush(QColor(214, 230, 248))
        qp.drawRect(top_rect)
        qp.setPen(QPen(QColor(70, 100, 140)))
        sticker = self.extra.get("unique_number")
        if sticker is None or str(sticker).strip() == "":
            sticker = self.extra.get("label_number")
        if sticker is not None:
            qp.setFont(QFont("Arial", 9, QFont.Bold))
            qp.drawText(top_rect.adjusted(5, 0, -5, 0), Qt.AlignVCenter | Qt.AlignLeft, "№ %s" % sticker)
        qp.setPen(QColor(0, 0, 0))
        qp.setFont(QFont("Arial", 9))
        below = r.adjusted(6, band_h + 3, -6, -4)
        fm = qp.fontMetrics()
        line_h = max(fm.height(), 12)
        size_line = "%d × %d мм" % (int(self.w_mm), int(self.h_mm))
        thick_line = ""
        try:
            t = int(self.thickness_mm) if self.thickness_mm is not None else 0
            if t > 0:
                thick_line = "%d мм" % t
        except (TypeError, ValueError):
            pass
        y0 = below.top()
        w = below.width()
        left = below.left()
        qp.drawText(
            QRect(left, y0, w, line_h),
            Qt.AlignHCenter | Qt.AlignTop | Qt.TextDontClip,
            size_line,
        )
        if thick_line:
            qp.setPen(QColor(60, 60, 60))
            qp.drawText(
                QRect(left, y0 + line_h, w, line_h),
                Qt.AlignHCenter | Qt.AlignTop | Qt.TextDontClip,
                thick_line,
            )


class GlassRemnantStockTile(QFrame):
    """Карточка остатка стекла на складе: плитка + снизу кнопка удаления с удержанием (красный прогресс слева направо)."""

    def __init__(self, name, thickness_mm, w_mm, h_mm, extra, parent=None, on_open_history=None, on_hold_delete=None):
        super().__init__(parent)
        self.setStyleSheet(
            "GlassRemnantStockTile { background: #f0f8ff; border: 1px solid #B0C4DE; border-radius: 5px; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(3)
        self._body = RemnantCompactTile(name, thickness_mm, w_mm, h_mm, extra, self)
        self._body.setStyleSheet(
            "RemnantCompactTile { background: transparent; border: none; } "
            "RemnantCompactTile:hover { background: #e8f4ff; border-radius: 3px; }"
        )
        self._body.clicked = on_open_history
        lay.addWidget(self._body)
        if on_hold_delete:
            self._del_btn = HoldDeleteButtonLTR()
            self._del_btn.setToolTip("Удерживайте ~1 с — остаток уйдёт в архив удалённых")
            self._del_btn.holdComplete.connect(lambda: on_hold_delete(extra))
            lay.addWidget(self._del_btn)
        self.setMinimumWidth(100)
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)


class TileWidget(QFrame):
    """Плитка: пропорциональный прямоугольник сверху; размеры и название — под ним (вынос). Увеличенная площадь плитки."""
    clicked = None

    def __init__(self, name, w_mm, h_mm, extra=None, parent=None, thickness_mm=None):
        super().__init__(parent)
        self.name = name
        self.w_mm = w_mm
        self.h_mm = h_mm
        try:
            self.thickness_mm = int(thickness_mm) if thickness_mm is not None else None
        except (TypeError, ValueError):
            self.thickness_mm = None
        self.extra = extra or {}
        self.setMinimumSize(90, 120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("TileWidget { background: #f0f8ff; border: 1px solid #B0C4DE; border-radius: 6px; } TileWidget:hover { background: #E0EFFF; }")

    def sizeHint(self):
        return QSize(160, 180)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.clicked:
            self.clicked(self.extra)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        qp = QPainter(self)
        qp.setRenderHint(QPainter.Antialiasing)
        qp.setRenderHint(QPainter.TextAntialiasing)
        r = self.rect()
        # Область под рисунок — верхняя часть; снизу — полоса для размеров и названия
        dim_h = min(TILE_DIM_HEIGHT, r.height() // 4)
        draw_rect = r.adjusted(TILE_PAD, TILE_PAD, -TILE_PAD, -TILE_PAD - dim_h)
        if draw_rect.width() <= 0 or draw_rect.height() <= 0:
            return
        w_mm = max(1, self.w_mm)
        h_mm = max(1, self.h_mm)
        scale = min(draw_rect.width() / w_mm, draw_rect.height() / h_mm)
        w_px = w_mm * scale
        h_px = h_mm * scale
        x0 = draw_rect.x() + (draw_rect.width() - w_px) / 2
        y0 = draw_rect.y() + (draw_rect.height() - h_px) / 2
        qp.setPen(QPen(QColor(70, 130, 180), 2))
        qp.setBrush(QColor(200, 220, 240))
        qp.drawRect(int(x0), int(y0), int(w_px), int(h_px))
        # Размеры и название — под прямоугольником (вынос)
        qp.setPen(QColor(0, 0, 0))
        qp.setFont(QFont("Arial", 10))
        below_y = draw_rect.y() + draw_rect.height() + 4
        dims = "%d × %d мм" % (int(w_mm), int(h_mm))
        if self.thickness_mm is not None and self.thickness_mm > 0:
            dims += " · %d мм" % int(self.thickness_mm)
        qp.drawText(int(r.x()), int(below_y), int(r.width()), 16, Qt.AlignCenter, dims)
        if self.name:
            nm = (self.name[:20] + '…') if len(self.name) > 20 else self.name
            qp.setFont(QFont("Arial", 9))
            qp.drawText(int(r.x()), int(below_y + 14), int(r.width()), 14, Qt.AlignCenter, nm)


class FullSheetStockTile(QFrame):
    """Целый лист на складе: плитка + снизу «Удалить» с удержанием (красный прогресс слева направо)."""

    def __init__(self, name, w_mm, h_mm, extra, parent=None, on_hold_delete=None):
        super().__init__(parent)
        self.setStyleSheet(
            "FullSheetStockTile { background: #f0f8ff; border: 1px solid #B0C4DE; border-radius: 6px; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(3)
        th = (extra or {}).get("thickness_mm")
        self._body = TileWidget(name, w_mm, h_mm, extra, self, thickness_mm=th)
        self._body.setCursor(Qt.ArrowCursor)
        self._body.setStyleSheet(
            "TileWidget { background: transparent; border: none; } "
            "TileWidget:hover { background: #e8f4ff; border-radius: 4px; }"
        )
        lay.addWidget(self._body, 1)
        if on_hold_delete:
            self._del_btn = HoldDeleteButtonLTR()
            self._del_btn.setToolTip(
                "Удерживайте ~1 с — позиция снимется со склада (если в карточке несколько листов ×N, удалится вся строка)"
            )
            self._del_btn.holdComplete.connect(lambda: on_hold_delete(extra))
            lay.addWidget(self._del_btn)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)


class CutDiagramDialog(QDialog):
    """Диалог со схемой раскроя (как резали лист: изделия, остатки, мусор)."""
    def __init__(self, layout_dict, title="Схема раскроя", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(500, 450)
        layout = QVBoxLayout(self)
        self.canvas = CuttingCanvas(self, fit_to_view=True)
        self.canvas.set_layouts([layout_dict] if layout_dict else [])
        layout.addWidget(self.canvas)
        layout.addWidget(QDialogButtonBox(QDialogButtonBox.Ok, accepted=self.accept))


class CutHistoryDialog(QDialog):
    """История остатка: полный лист(а) на схеме, источник листа, заказы; без журнала событий."""

    def __init__(self, remnant, parent=None):
        super().__init__(parent)
        self.remnant = remnant
        rid = remnant.get("id")
        self.setWindowTitle(
            "История резов — %s" % (remnant.get("unique_number") or remnant.get("label_number") or remnant.get("name"))
        )
        self.setMinimumSize(760, 520)
        layout = QVBoxLayout(self)
        self._stages = []
        try:
            self._stages = models.get_remnant_visual_story_stages(int(rid)) if rid is not None else []
        except Exception:
            self._stages = []

        if self._stages:
            tabs = QTabWidget()
            for i, st in enumerate(self._stages):
                lay = copy.deepcopy(st.get("layout") or {})
                lay["_highlight_rects_mm"] = list(st.get("highlight_rects") or [])
                page = QWidget()
                vl = QVBoxLayout(page)
                src = (st.get("sheet_source") or "").strip()
                if src:
                    vl.addWidget(QLabel("Источник листа: %s" % src))
                sub = (st.get("subtitle") or "").strip()
                if sub:
                    vl.addWidget(QLabel(sub))
                oid = st.get("order_id")
                row_o = QHBoxLayout()
                if oid:
                    btn = QPushButton("Заказ №%s — карточка заказа" % oid)
                    btn.setToolTip("Открыть сводку заказа стекло/зеркало")
                    btn.clicked.connect(lambda _=False, o=oid: self._open_mirror_order(int(o)))
                    row_o.addWidget(btn)
                row_o.addStretch()
                vl.addLayout(row_o)
                sc = QScrollArea()
                sc.setWidgetResizable(True)
                cv = CuttingCanvas(sc, fit_to_view=True)
                cv.setMinimumSize(520, 380)
                cv.set_layouts([lay])
                sc.setWidget(cv)
                vl.addWidget(sc, 1)
                tab_title = (st.get("title") or "Шаг %d" % (i + 1))[:42]
                tabs.addTab(page, tab_title)
            layout.addWidget(tabs, 1)
            note = QTextBrowser()
            note.setReadOnly(True)
            note.setMaximumHeight(120)
            note.setOpenExternalLinks(False)
            try:
                note.setHtml(_glass_remnant_history_html(dict(remnant)))
            except Exception as e:
                note.setPlainText(str(e))
            layout.addWidget(note)
        else:
            text = QTextEdit()
            text.setReadOnly(True)
            text.setStyleSheet("QTextEdit { font-family: Segoe UI, Arial; font-size: 12px; }")
            try:
                text.setHtml(_glass_remnant_history_html(dict(remnant)))
            except Exception as e:
                text.setPlainText("Ошибка построения истории: %s" % e)
            layout.addWidget(text, 1)
            # Кнопка «Схема раскроя (из журнала)» здесь больше не показывается:
            # для истории резов по стеклу достаточно текста (и вкладок со схемами, если они есть).
        layout.addWidget(QDialogButtonBox(QDialogButtonBox.Ok, accepted=self.accept))

    def _open_mirror_order(self, oid: int):
        row = models.get_order(int(oid))
        if not row:
            QMessageBox.warning(self, "Заказ", "Заказ №%s не найден." % oid)
            return
        try:
            from ui.glass_order_overview_dialog import GlassOrderOverviewDialog

            GlassOrderOverviewDialog(row, self, summary_only=True).exec_()
        except Exception as e:
            QMessageBox.warning(self, "Заказ", "Не удалось открыть карточку: %s" % e)

    def _on_show_diagram(self):
        layout_dict = models.get_remnant_creation_layout(self.remnant.get("id"))
        if not layout_dict:
            QMessageBox.information(
                self,
                "Схема раскроя",
                "Схема раскроя для этого остатка не сохранена (остаток создан до добавления этой функции или данные недоступны).",
            )
            return
        d = CutDiagramDialog(layout_dict, title="Схема раскроя — как резали этот лист", parent=self)
        d.exec_()


def _parse_money_line(text):
    """Число из поля цены: пробелы убрать, запятую заменить на точку."""
    s = (text or "").strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    while len(s) > 1 and s[-1] == ".":
        s = s[:-1]
    if not s or s == ".":
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if v < 0:
        return None
    return v


def _ceil_money_kopecks(amount):
    """Округление вверх до копеек (2 знака)."""
    return math.ceil(round(float(amount) * 100, 6)) / 100.0


def _format_money_line(amount):
    if amount is None or amount <= 0:
        return ""
    v = round(float(amount), 2)
    if abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    return "%.2f" % v


def _make_sheet_price_spin(parent=None):
    """QDoubleSpinBox: копейки, запятая → точка в строке ввода."""
    spin = QDoubleSpinBox(parent)
    spin.setRange(0, 999999999.99)
    spin.setDecimals(2)
    spin.setSingleStep(1.0)
    spin.setSpecialValueText(" ")
    spin.setValue(0)
    spin.setLocale(QLocale(QLocale.English, QLocale.UnitedStates))
    le = spin.lineEdit()
    if le is not None:
        le.textChanged.connect(lambda _t, s=spin: _sheet_price_spin_fix_comma(s))
    return spin


def _sheet_price_spin_fix_comma(spin):
    le = spin.lineEdit()
    if le is None or spin.signalsBlocked():
        return
    t = le.text() or ""
    if "," not in t:
        return
    pos = le.cursorPosition()
    new = t.replace(",", ".")
    spin.blockSignals(True)
    le.blockSignals(True)
    try:
        le.setText(new)
        le.setCursorPosition(min(pos, len(new)))
        try:
            spin.setValue(float(new) if new else 0.0)
        except ValueError:
            pass
    finally:
        le.blockSignals(False)
        spin.blockSignals(False)


def _create_supplier_picker_widget(parent=None):
    try:
        from MAIN_PROJECT.ui.supplier_picker_combo import SupplierPickerCombo

        return SupplierPickerCombo(parent)
    except Exception:
        pass
    try:
        import importlib.util

        mp = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "MAIN_PROJECT", "ui", "supplier_picker_combo.py"))
        if os.path.isfile(mp):
            spec = importlib.util.spec_from_file_location("_wh_supplier_picker", mp)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.SupplierPickerCombo(parent)
    except Exception:
        pass
    w = QLineEdit(parent)
    w.setPlaceholderText("Поставщик (справочник недоступен)")
    return w


def _play_success_beep():
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_OK)
    except Exception:
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.beep()


class MaterialReceiptHistoryDialog(QDialog):
    """История поступлений по материалу (drill-down со склада)."""

    def __init__(self, material_name, thickness_mm, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Поступления: %s" % (material_name or "—"))
        self.setMinimumSize(720, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("%s — %s мм" % (material_name or "—", int(thickness_mm or 4))))
        tbl = QTableWidget(0, 8)
        tbl.setHorizontalHeaderLabels(
            ["Дата", "Накладная", "Дата накл.", "Размер", "Кол-во", "₽/лист", "Поставщик", "Коммент."]
        )
        tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        rows = models.get_full_sheet_receipt_lines(material_name, thickness_mm=thickness_mm, limit=500)
        tbl.setRowCount(len(rows))
        for i, row in enumerate(rows):
            ad = row.get("arrival_date")
            ad_s = ad.strftime("%d.%m.%Y") if ad and hasattr(ad, "strftime") else "—"
            inv = row.get("invoice_date")
            inv_s = inv.strftime("%d.%m.%Y") if inv and hasattr(inv, "strftime") else "—"
            w, h = int(row.get("width_mm") or 0), int(row.get("height_mm") or 0)
            tbl.setItem(i, 0, QTableWidgetItem(ad_s))
            tbl.setItem(i, 1, QTableWidgetItem(str(row.get("warehouse_number") or "—")))
            tbl.setItem(i, 2, QTableWidgetItem(inv_s))
            tbl.setItem(i, 3, QTableWidgetItem("%d × %d" % (w, h)))
            tbl.setItem(i, 4, QTableWidgetItem(str(row.get("quantity") or 0)))
            tbl.setItem(i, 5, QTableWidgetItem("%.2f" % float(row.get("cost") or 0)))
            tbl.setItem(i, 6, QTableWidgetItem(str(row.get("supplier") or "—")))
            tbl.setItem(i, 7, QTableWidgetItem(str(row.get("comment") or "—")[:60]))
        lay.addWidget(tbl, 1)
        btn = QPushButton("Закрыть")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)


class AddSheetDialog(QDialog):
    """Окно добавления целого листа: материал как в заказе стекло/зеркало (тип → вариант → толщина) или плоский список; размеры, дата, поставщик, …"""

    @staticmethod
    def _create_supplier_picker():
        return _create_supplier_picker_widget()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._price_sync = False
        self._price_source = None  # 'm2' | 'sheet' — какое поле редактирует пользователь
        self.setWindowTitle("Добавить целые листы")
        self.setMinimumWidth(440)
        layout = QFormLayout(self)
        self._mat_pick = GlassWarehouseMaterialPicker(self, mode="sheet")
        layout.addRow(self._mat_pick)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(0, 50000)
        self.height_spin.setValue(0)
        self.height_spin.setSpecialValueText(" ")
        self.height_spin.setSuffix(" мм")
        layout.addRow("Высота (мм):", self.height_spin)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(0, 50000)
        self.width_spin.setValue(0)
        self.width_spin.setSpecialValueText(" ")
        self.width_spin.setSuffix(" мм")
        layout.addRow("Ширина (мм):", self.width_spin)
        self.arrival_date = QDateEdit()
        self.arrival_date.setCalendarPopup(True)
        self.arrival_date.setDate(QDate.currentDate())
        layout.addRow("Дата поступления:", self.arrival_date)
        self.invoice_date = QDateEdit()
        self.invoice_date.setCalendarPopup(True)
        self.invoice_date.setDate(QDate.currentDate())
        layout.addRow("Дата накладной:", self.invoice_date)
        sup_row = QHBoxLayout()
        self._supplier_picker = self._create_supplier_picker()
        sup_row.addWidget(self._supplier_picker, 1)
        self._btn_new_supplier = QPushButton("+")
        self._btn_new_supplier.setFixedWidth(36)
        self._btn_new_supplier.setToolTip("Новый поставщик")
        self._btn_new_supplier.clicked.connect(self._on_new_supplier)
        sup_row.addWidget(self._btn_new_supplier)
        sup_wrap = QWidget()
        sup_wrap.setLayout(sup_row)
        layout.addRow("Поставщик:", sup_wrap)
        self.price_m2_spin = _make_sheet_price_spin(self)
        self.price_m2_spin.setSuffix(" ₽/м²")
        self.price_sheet_spin = _make_sheet_price_spin(self)
        self.price_sheet_spin.setSuffix(" ₽")
        price_hint = QLabel(
            "Сначала ширина и высота листа — от них пересчёт. Можно 1000,50 или 1000.50."
        )
        price_hint.setWordWrap(True)
        price_hint.setStyleSheet("color:#555;font-size:11px;")
        layout.addRow(price_hint)
        layout.addRow("Цена за м²:", self.price_m2_spin)
        layout.addRow("Цена за лист:", self.price_sheet_spin)
        self.warehouse_number_edit = QLineEdit()
        self.warehouse_number_edit.setPlaceholderText("Номер накладной")
        layout.addRow("Номер накладной:", self.warehouse_number_edit)
        self.quantity_spin = QSpinBox()
        self.quantity_spin.setRange(1, 99999)
        self.quantity_spin.setValue(1)
        layout.addRow("Количество:", self.quantity_spin)
        self._batch_total_lbl = QLabel("")
        self._batch_total_lbl.setStyleSheet("color:#1b5e20;font-weight:600;")
        layout.addRow("Итого по накладной:", self._batch_total_lbl)
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("Комментарий (необязательно)")
        layout.addRow("Комментарий:", self.comment_edit)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

        self.height_spin.valueChanged.connect(self._on_sheet_dims_changed)
        self.width_spin.valueChanged.connect(self._on_sheet_dims_changed)
        self.price_m2_spin.valueChanged.connect(self._on_price_m2_changed)
        self.price_sheet_spin.valueChanged.connect(self._on_price_sheet_changed)
        self.quantity_spin.valueChanged.connect(self._update_batch_total_label)
        if hasattr(self._supplier_picker, "currentIndexChanged"):
            self._supplier_picker.currentIndexChanged.connect(self._on_supplier_changed)
        self._update_batch_total_label()
        self._on_supplier_changed()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            fw = self.focusWidget()
            if fw is not None and not isinstance(fw, QPushButton):
                self.focusNextChild()
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_new_supplier(self):
        try:
            from ui.new_client_dialog import NewClientDialog
        except Exception as ex:
            QMessageBox.warning(self, "Поставщик", "Не удалось открыть форму: %s" % ex)
            return
        d = NewClientDialog(self, entity="supplier")
        if d.exec_() != QDialog.Accepted:
            return
        if hasattr(self._supplier_picker, "reload"):
            self._supplier_picker.reload()
        sid = d.get_saved_client_id()
        if sid and hasattr(self._supplier_picker, "set_supplier_id"):
            self._supplier_picker.set_supplier_id(int(sid))
        self._on_supplier_changed()

    def _on_supplier_changed(self, *_a):
        sid = None
        if hasattr(self._supplier_picker, "supplier_id"):
            sid = self._supplier_picker.supplier_id()
        row = models.get_supplier_by_id(int(sid)) if sid else None
        unknown = models.is_unknown_supplier_row(row)
        for w in (self.warehouse_number_edit, self.invoice_date):
            w.setEnabled(not unknown)
            if unknown:
                w.setToolTip("Для поставщика «Неопознанный» номер накладной и дата не требуются")
            else:
                w.setToolTip("")

    def _update_batch_total_label(self, *_a):
        sheet = float(self.price_sheet_spin.value())
        qty = int(self.quantity_spin.value())
        if sheet > 0 and qty > 1:
            self._batch_total_lbl.setText("%.2f ₽" % (sheet * qty))
        elif sheet > 0:
            self._batch_total_lbl.setText("%.2f ₽" % sheet)
        else:
            self._batch_total_lbl.setText("—")

    def _sheet_area_m2(self):
        w = self.width_spin.value()
        h = self.height_spin.value()
        if w <= 0 or h <= 0:
            return 0.0
        return (w * h) / 1_000_000.0

    def _set_price_spin(self, spin, value):
        spin.blockSignals(True)
        try:
            spin.setValue(float(value) if value and value > 0 else 0.0)
        finally:
            spin.blockSignals(False)

    def _sync_price_from_m2(self):
        area = self._sheet_area_m2()
        m2 = float(self.price_m2_spin.value())
        if m2 <= 0:
            self._price_sync = True
            self._set_price_spin(self.price_sheet_spin, 0)
            self._price_sync = False
            return
        if area <= 0:
            return
        self._price_sync = True
        self._set_price_spin(self.price_sheet_spin, _ceil_money_kopecks(m2 * area))
        self._price_sync = False
        self._update_batch_total_label()

    def _sync_price_from_sheet(self):
        area = self._sheet_area_m2()
        sheet = float(self.price_sheet_spin.value())
        if sheet <= 0:
            self._price_sync = True
            self._set_price_spin(self.price_m2_spin, 0)
            self._price_sync = False
            return
        if area <= 0:
            return
        self._price_sync = True
        self._set_price_spin(self.price_m2_spin, _ceil_money_kopecks(sheet / area))
        self._price_sync = False
        self._update_batch_total_label()

    def _on_sheet_dims_changed(self, *_a):
        if self._price_sync:
            return
        if self._price_source == "sheet":
            self._sync_price_from_sheet()
        else:
            self._sync_price_from_m2()

    def _on_price_m2_changed(self, *_a):
        if self._price_sync:
            return
        self._price_source = "m2"
        self._sync_price_from_m2()

    def _on_price_sheet_changed(self, *_a):
        if self._price_sync:
            return
        self._price_source = "sheet"
        self._sync_price_from_sheet()

    def _accept(self):
        ok, err = self._mat_pick.validate_sheet()
        if not ok:
            QMessageBox.warning(self, "Ошибка", err)
            return
        name = self._mat_pick.get_material_name()
        th_mm = self._mat_pick.get_thickness_mm()
        h = self.height_spin.value()
        w = self.width_spin.value()
        if h <= 0 or w <= 0:
            QMessageBox.warning(self, "Ошибка", "Введите высоту и ширину (целые числа больше 0).")
            return
        supplier_id = None
        supplier = ""
        unknown = False
        if hasattr(self._supplier_picker, "supplier_id"):
            supplier_id = self._supplier_picker.supplier_id()
            if not supplier_id:
                QMessageBox.warning(self, "Ошибка", "Выберите поставщика из списка.")
                return
            row = models.get_supplier_by_id(supplier_id)
            supplier = (models._supplier_display_name(row) if row else "") or ""
            unknown = models.is_unknown_supplier_row(row)
        else:
            supplier = (self._supplier_picker.text() or "").strip()
            if not supplier:
                QMessageBox.warning(self, "Ошибка", "Заполните поставщика.")
                return
        sheet_val = float(self.price_sheet_spin.value())
        m2_val = float(self.price_m2_spin.value())
        if sheet_val > 0:
            cost = sheet_val
        elif m2_val > 0:
            area = self._sheet_area_m2()
            if area <= 0:
                QMessageBox.warning(self, "Ошибка", "Укажите размеры листа, чтобы посчитать цену за лист.")
                return
            cost = _ceil_money_kopecks(m2_val * area)
        else:
            cost = 0
        warehouse_number = (self.warehouse_number_edit.text() or "").strip()
        inv_date = self.invoice_date.date()
        invoice_date = inv_date.toPyDate() if inv_date.isValid() else None
        if not unknown:
            if not warehouse_number:
                QMessageBox.warning(self, "Ошибка", "Заполните номер накладной.")
                return
        else:
            warehouse_number = warehouse_number or None
            invoice_date = None
        qty = self.quantity_spin.value()
        if qty < 1:
            QMessageBox.warning(self, "Ошибка", "Количество должно быть целым числом не меньше 1.")
            return
        # Стоимость и размеры — числа (проверка целости для размеров уже через SpinBox)
        self._data = {
            'name': name,
            'height_mm': h,
            'width_mm': w,
            'thickness_mm': th_mm,
            'arrival_date': self.arrival_date.date(),
            'invoice_date': invoice_date,
            'supplier': supplier,
            'supplier_id': supplier_id,
            'cost': cost,
            'warehouse_number': warehouse_number,
            'quantity': qty,
            'comment': (self.comment_edit.text() or "").strip() or None,
        }
        self.accept()

    def get_data(self):
        return getattr(self, '_data', None)


class AddRemnantDialog(QDialog):
    """Добавить остаток: материал как в заказе (тип → вариант → толщина), размер вручную или с целого листа на складе."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить остаток")
        self.setMinimumWidth(440)
        layout = QFormLayout(self)
        self._mat_pick = GlassWarehouseMaterialPicker(self, mode="remnant")
        layout.addRow(self._mat_pick)
        self.size_from_sheet = QComboBox()
        self.size_from_sheet.setToolTip(
            "Подставить ширину и высоту с имеющегося целого листа (тот же материал и толщина)"
        )
        self.size_from_sheet.addItem("— размер вручную —", None)
        layout.addRow("Размер с целого листа:", self.size_from_sheet)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 50000)
        self.width_spin.setValue(500)
        self.width_spin.setSuffix(" мм")
        layout.addRow("Ширина (мм):", self.width_spin)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 50000)
        self.height_spin.setValue(500)
        self.height_spin.setSuffix(" мм")
        layout.addRow("Высота (мм):", self.height_spin)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)
        self._mat_pick.selectionChanged.connect(self._refresh_size_templates)
        self.size_from_sheet.currentIndexChanged.connect(self._on_size_template_picked)
        self._refresh_size_templates()

    def _refresh_size_templates(self):
        self.size_from_sheet.blockSignals(True)
        self.size_from_sheet.clear()
        self.size_from_sheet.addItem("— размер вручную —", None)
        name = self._mat_pick.get_material_name()
        th = self._mat_pick.get_thickness_mm()
        if name and th:
            rows = models.get_full_sheets_by_material_and_thickness(name, th) or []
            seen = set()
            for row in rows:
                w = int(row.get("width_mm") or 0)
                h = int(row.get("height_mm") or 0)
                if w <= 0 or h <= 0:
                    continue
                key = (w, h)
                if key in seen:
                    continue
                seen.add(key)
                q = int(row.get("quantity") or 0)
                extra = (" · ×%s" % q) if q > 1 else ""
                self.size_from_sheet.addItem("%d × %d мм (целый лист)%s" % (w, h, extra), (w, h))
        self.size_from_sheet.blockSignals(False)

    def _on_size_template_picked(self, index):
        if index < 0:
            return
        data = self.size_from_sheet.currentData()
        if data and isinstance(data, (tuple, list)) and len(data) >= 2:
            self.width_spin.setValue(int(data[0]))
            self.height_spin.setValue(int(data[1]))

    def _accept(self):
        ok, err = self._mat_pick.validate_remnant()
        if not ok:
            QMessageBox.warning(self, "Ошибка", err)
            return
        name = self._mat_pick.get_material_name()
        th = self._mat_pick.get_thickness_mm()
        w = self.width_spin.value()
        h = self.height_spin.value()
        if w <= 0 or h <= 0:
            QMessageBox.warning(self, "Ошибка", "Укажите ширину и высоту больше 0.")
            return
        num = models.get_next_label_number()
        unique_num = str(num)
        url = _remnant_qr_url(unique_num)
        try:
            models.insert_remnant(name, h, w, unique_num, url, thickness_mm=th, label_number=num)
            QMessageBox.information(self, "Добавлено", "Остаток добавлен на склад.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить: %s" % e)


class AddRemnantBatchDialog(QDialog):
    """Несколько остатков стекла: очередь с предпросмотром номеров, удержание «Сохранить», PDF этикеток."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить остатки стекла")
        self.setMinimumWidth(500)
        self._queue = []
        self._next_uid = 1
        self._saved_pdf_labels = []
        root = QVBoxLayout(self)
        form = QFormLayout()
        self._mat_pick = GlassWarehouseMaterialPicker(self, mode="remnant")
        form.addRow(self._mat_pick)
        self.size_from_sheet = QComboBox()
        self.size_from_sheet.setToolTip("Подставить размер с целого листа (тот же материал и толщина)")
        self.size_from_sheet.addItem("— размер вручную —", None)
        form.addRow("Размер с целого листа:", self.size_from_sheet)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 50000)
        self.width_spin.setValue(500)
        self.width_spin.setSuffix(" мм")
        form.addRow("Ширина (мм):", self.width_spin)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(1, 50000)
        self.height_spin.setValue(500)
        self.height_spin.setSuffix(" мм")
        form.addRow("Высота (мм):", self.height_spin)
        root.addLayout(form)
        row_plus = QHBoxLayout()
        btn_plus = QPushButton("+")
        btn_plus.setFixedWidth(44)
        btn_plus.setToolTip("Добавить в очередь (все обязательные поля должны быть заполнены)")
        btn_plus.clicked.connect(self._enqueue)
        row_plus.addWidget(btn_plus)
        row_plus.addWidget(QLabel("Добавить текущую позицию в очередь"))
        row_plus.addStretch()
        root.addLayout(row_plus)
        self._queue_scroll = QScrollArea()
        self._queue_scroll.setWidgetResizable(True)
        self._queue_host = QWidget()
        self._queue_lay = QVBoxLayout(self._queue_host)
        self._queue_lay.setAlignment(Qt.AlignTop)
        self._queue_scroll.setWidget(self._queue_host)
        self._queue_scroll.setMinimumHeight(140)
        root.addWidget(self._queue_scroll, 1)
        self._btn_save = HoldFillButton("Сохранить (удерживайте ~1 с)", hold_ms=1000)
        self._btn_save.holdComplete.connect(self._save_all)
        root.addWidget(self._btn_save)
        pdf_row = QHBoxLayout()
        self._btn_pdf_save = QPushButton("Скачать PDF")
        self._btn_pdf_save.setToolTip(
            "Сохранить этикетки в папку экспорта (или «Загрузки») с уникальным именем файла"
        )
        self._btn_pdf_save.setEnabled(False)
        self._btn_pdf_save.clicked.connect(self._export_pdf_download)
        self._btn_pdf_open = QPushButton("Открыть PDF")
        self._btn_pdf_open.setToolTip("Сформировать PDF и открыть в программе просмотра")
        self._btn_pdf_open.setEnabled(False)
        self._btn_pdf_open.clicked.connect(self._export_pdf_open)
        pdf_row.addWidget(self._btn_pdf_save)
        pdf_row.addWidget(self._btn_pdf_open)
        root.addLayout(pdf_row)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)
        self._mat_pick.selectionChanged.connect(self._refresh_size_templates)
        self.size_from_sheet.currentIndexChanged.connect(self._on_size_template_picked)
        self._refresh_size_templates()

    def _refresh_size_templates(self):
        self.size_from_sheet.blockSignals(True)
        self.size_from_sheet.clear()
        self.size_from_sheet.addItem("— размер вручную —", None)
        name = self._mat_pick.get_material_name()
        th = self._mat_pick.get_thickness_mm()
        if name and th:
            rows = models.get_full_sheets_by_material_and_thickness(name, th) or []
            seen = set()
            for row in rows:
                w = int(row.get("width_mm") or 0)
                h = int(row.get("height_mm") or 0)
                if w <= 0 or h <= 0:
                    continue
                key = (w, h)
                if key in seen:
                    continue
                seen.add(key)
                q = int(row.get("quantity") or 0)
                extra = (" · ×%s" % q) if q > 1 else ""
                self.size_from_sheet.addItem("%d × %d мм (целый лист)%s" % (w, h, extra), (w, h))
        self.size_from_sheet.blockSignals(False)

    def _on_size_template_picked(self, index):
        if index < 0:
            return
        data = self.size_from_sheet.currentData()
        if data and isinstance(data, (tuple, list)) and len(data) >= 2:
            self.width_spin.setValue(int(data[0]))
            self.height_spin.setValue(int(data[1]))

    def _enqueue(self):
        ok, err = self._mat_pick.validate_remnant()
        if not ok:
            QMessageBox.warning(self, "Ошибка", err)
            return
        name = self._mat_pick.get_material_name()
        th = self._mat_pick.get_thickness_mm()
        w = self.width_spin.value()
        h = self.height_spin.value()
        if w <= 0 or h <= 0:
            QMessageBox.warning(self, "Ошибка", "Укажите ширину и высоту больше 0.")
            return
        uid = self._next_uid
        self._next_uid += 1
        self._queue.append({"uid": uid, "name": name, "th": th, "w": w, "h": h})
        self._rebuild_queue()

    def _rebuild_queue(self):
        while self._queue_lay.count():
            it = self._queue_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        n = len(self._queue)
        nums = models.peek_next_label_numbers(n) if n else []
        for i, item in enumerate(self._queue):
            lbl = nums[i] if i < len(nums) else "?"
            txt = "Этикетка № %s\n%s\n%d мм · %d × %d мм" % (lbl, item["name"][:42], item["th"], item["w"], item["h"])
            c = QueueHoldRemoveCard(txt, item["uid"])
            c.removeUid.connect(self._remove_uid)
            self._queue_lay.addWidget(c)

    def _remove_uid(self, uid):
        self._queue = [x for x in self._queue if x["uid"] != uid]
        self._rebuild_queue()

    def _save_all(self):
        if not self._queue:
            QMessageBox.warning(self, "Очередь пуста", "Нажмите «+», чтобы добавить позиции.")
            return
        labels = []
        try:
            for item in self._queue:
                num = models.get_next_label_number()
                un = str(num)
                url = _remnant_qr_url(un)
                rid = models.insert_remnant(
                    item["name"], item["h"], item["w"], un, url, thickness_mm=item["th"], label_number=num
                )
                rem = models.get_remnant_by_id(rid) if rid else None
                if rem:
                    labels.append(
                        {
                            "remnant_id": rid,
                            "display_no": str(rem.get("label_number") or rem.get("unique_number") or ""),
                            "label_number": rem.get("label_number"),
                            "unique_number": rem.get("unique_number"),
                            "name": rem.get("name") or item["name"],
                            "width_mm": rem.get("width_mm"),
                            "height_mm": rem.get("height_mm"),
                            "thickness_mm": rem.get("thickness_mm"),
                        }
                    )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", "Сохранение прервано: %s" % e)
            return
        self._saved_pdf_labels = labels
        ok = bool(labels)
        self._btn_pdf_save.setEnabled(ok)
        self._btn_pdf_open.setEnabled(ok)
        self._queue = []
        self._rebuild_queue()
        QMessageBox.information(
            self,
            "Готово",
            "Сохранено остатков: %d. Ниже — «Скачать PDF» или «Открыть PDF», затем можно закрыть окно."
            % len(labels),
        )

    def _export_pdf_download(self):
        if not self._saved_pdf_labels:
            QMessageBox.warning(self, "PDF", "Сначала удерживающим нажатием сохраните очередь.")
            return
        try:
            write_fn = _import_write_glass_remnant_labels_pdf()
        except Exception as e:
            QMessageBox.warning(self, "PDF", "Модуль печати недоступен: %s" % e)
            return
        path = _unique_label_pdf_path("glass_remnants")
        try:
            write_fn(self._saved_pdf_labels, path)
            QMessageBox.information(self, "PDF", "Файл сохранён:\n%s" % path)
        except Exception as e:
            QMessageBox.critical(self, "PDF", str(e))

    def _export_pdf_open(self):
        if not self._saved_pdf_labels:
            QMessageBox.warning(self, "PDF", "Сначала удерживающим нажатием сохраните очередь.")
            return
        try:
            write_fn = _import_write_glass_remnant_labels_pdf()
        except Exception as e:
            QMessageBox.warning(self, "PDF", "Модуль печати недоступен: %s" % e)
            return
        fd, path = tempfile.mkstemp(suffix=".pdf", prefix="mirror_glass_labels_")
        os.close(fd)
        try:
            write_fn(self._saved_pdf_labels, path)
            _open_pdf_file(path)
        except Exception as e:
            try:
                os.unlink(path)
            except OSError:
                pass
            QMessageBox.critical(self, "PDF", str(e))


class AddProfileRemnantBatchDialog(QDialog):
    """Несколько остатков профиля: очередь, номера S… в БД при сохранении, PDF этикеток."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Добавить остатки профиля")
        self.setMinimumSize(520, 560)
        self._queue = []
        self._next_uid = 1
        self._saved_pdf_labels = []
        self._selected_profile = None
        root = QVBoxLayout(self)
        self.btn_pick_profile = QPushButton("Выбрать профиль (таблица и фото)…")
        self.btn_pick_profile.clicked.connect(self._on_pick_profile)
        root.addWidget(self.btn_pick_profile)
        self.lbl_profile_text = QLabel("Профиль не выбран.")
        self.lbl_profile_text.setWordWrap(True)
        self.lbl_profile_text.setStyleSheet("color:#333;font-size:12px;")
        root.addWidget(self.lbl_profile_text)
        self.photo_profile = QLabel()
        self.photo_profile.setFixedSize(180, 180)
        self.photo_profile.setAlignment(Qt.AlignCenter)
        self.photo_profile.setStyleSheet("background:#f0f0f0;border:1px solid #ccc;")
        root.addWidget(self.photo_profile, 0, Qt.AlignCenter)
        form = QFormLayout()
        self.len_mm = QSpinBox()
        self.len_mm.setRange(1, 50000)
        self.len_mm.setValue(1000)
        self.len_mm.setSuffix(" мм")
        form.addRow("Длина остатка (мм):", self.len_mm)
        root.addLayout(form)
        row_plus = QHBoxLayout()
        btn_plus = QPushButton("+")
        btn_plus.setFixedWidth(44)
        btn_plus.setToolTip("Добавить в очередь (профиль и длина)")
        btn_plus.clicked.connect(self._enqueue)
        row_plus.addWidget(btn_plus)
        row_plus.addWidget(QLabel("Добавить в очередь"))
        row_plus.addStretch()
        root.addLayout(row_plus)
        self._queue_scroll = QScrollArea()
        self._queue_scroll.setWidgetResizable(True)
        self._queue_host = QWidget()
        self._queue_lay = QVBoxLayout(self._queue_host)
        self._queue_lay.setAlignment(Qt.AlignTop)
        self._queue_scroll.setWidget(self._queue_host)
        self._queue_scroll.setMinimumHeight(120)
        root.addWidget(self._queue_scroll, 1)
        self._btn_save = HoldFillButton("Сохранить (удерживайте ~1 с)", hold_ms=1000)
        self._btn_save.holdComplete.connect(self._save_all)
        root.addWidget(self._btn_save)
        pdf_row = QHBoxLayout()
        self._btn_pdf_save = QPushButton("Скачать PDF")
        self._btn_pdf_save.setToolTip(
            "Сохранить этикетки в папку экспорта (или «Загрузки») с уникальным именем файла"
        )
        self._btn_pdf_save.setEnabled(False)
        self._btn_pdf_save.clicked.connect(self._export_pdf_download)
        self._btn_pdf_open = QPushButton("Открыть PDF")
        self._btn_pdf_open.setToolTip("Сформировать PDF и открыть в программе просмотра")
        self._btn_pdf_open.setEnabled(False)
        self._btn_pdf_open.clicked.connect(self._export_pdf_open)
        pdf_row.addWidget(self._btn_pdf_save)
        pdf_row.addWidget(self._btn_pdf_open)
        root.addLayout(pdf_row)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _on_pick_profile(self):
        try:
            from MAIN_PROJECT.ui.facade_profile_dialog import FacadeProfileSelectDialog
        except Exception as e:
            QMessageBox.warning(self, "Профиль", "Не удалось открыть каталог: %s" % e)
            return
        d = FacadeProfileSelectDialog(self)
        if d.exec_() != QDialog.Accepted:
            return
        self._selected_profile = d.selected_profile()
        self._update_profile_preview()

    def _update_profile_preview(self):
        p = self._selected_profile
        if not p:
            self.lbl_profile_text.setText("Профиль не выбран.")
            self.photo_profile.setPixmap(QPixmap())
            self.photo_profile.setText("")
            return
        self.lbl_profile_text.setText(
            "<b>%s</b> · %s · %s"
            % (
                (p.get("series") or "—").replace("<", ""),
                (p.get("name") or "—").replace("<", ""),
                (p.get("color") or "—").replace("<", ""),
            )
        )
        self.lbl_profile_text.setTextFormat(Qt.RichText)
        path = _warehouse_profile_img_path(p)
        if path and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                self.photo_profile.setPixmap(pix.scaled(180, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.photo_profile.setText("")
                return
        self.photo_profile.setPixmap(QPixmap())
        self.photo_profile.setText("Нет фото")

    def _enqueue(self):
        if not self._selected_profile:
            QMessageBox.warning(self, "Профиль", "Сначала выберите профиль.")
            return
        p = self._selected_profile
        mm = max(1, int(self.len_mm.value() or 0))
        uid = self._next_uid
        self._next_uid += 1
        self._queue.append(
            {
                "uid": uid,
                "ref_id": p.get("id"),
                "series": p.get("series") or "",
                "name": p.get("name") or "",
                "color": p.get("color") or "",
                "length_mm": mm,
            }
        )
        self._rebuild_queue()

    def _rebuild_queue(self):
        while self._queue_lay.count():
            it = self._queue_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        n = len(self._queue)
        nums = models.peek_next_label_numbers(n) if n else []
        for i, item in enumerate(self._queue):
            num = nums[i] if i < len(nums) else None
            disp_s = "S%s" % num if num is not None else "S?"
            txt = "%s (как на этикетке)\n%s %s\n%s · %d мм" % (
                disp_s,
                (item["series"] or "")[:20],
                (item["name"] or "")[:28],
                (item["color"] or "")[:20],
                item["length_mm"],
            )
            c = QueueHoldRemoveCard(txt, item["uid"])
            c.removeUid.connect(self._remove_uid)
            self._queue_lay.addWidget(c)

    def _remove_uid(self, uid):
        self._queue = [x for x in self._queue if x["uid"] != uid]
        self._rebuild_queue()

    def _save_all(self):
        if not self._queue:
            QMessageBox.warning(self, "Очередь пуста", "Добавьте позиции кнопкой +.")
            return
        labels = []
        try:
            for item in self._queue:
                sid = models.insert_profile_stock(
                    "profile",
                    item["ref_id"],
                    item["series"],
                    item["name"],
                    item["color"],
                    item["length_mm"],
                    1,
                    is_remnant=True,
                )
                if sid:
                    models.ensure_profile_label_number(int(sid))
                lr = models.get_profile_label_by_stock_id(int(sid)) if sid else None
                row = models.get_profile_stock_row(int(sid)) if sid else None
                if lr and row:
                    uq = (lr.get("unique_number") or "").strip() or ("S%s" % (lr.get("label_number") or ""))
                    labels.append(
                        {
                            "display_no": uq,
                            "label_number": lr.get("label_number"),
                            "unique_number": uq,
                            "scan_code": uq,
                            "label_prefix": "S",
                            "stock_id": int(sid) if sid else None,
                            "series": row.get("series") or "",
                            "name": row.get("name") or "",
                            "color": row.get("color") or "",
                            "length_mm": row.get("length_mm"),
                            "qr_url": lr.get("qr_url"),
                            "source": "warehouse_remnant_batch",
                        }
                    )
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", "Сохранение прервано: %s" % e)
            return
        self._saved_pdf_labels = labels
        ok = bool(labels)
        self._btn_pdf_save.setEnabled(ok)
        self._btn_pdf_open.setEnabled(ok)
        self._queue = []
        self._rebuild_queue()
        QMessageBox.information(
            self,
            "Готово",
            "Сохранено остатков профиля: %d. Ниже — «Скачать PDF» или «Открыть PDF», затем можно закрыть окно."
            % len(labels),
        )

    def _export_pdf_download(self):
        if not self._saved_pdf_labels:
            QMessageBox.warning(self, "PDF", "Сначала удерживающим нажатием сохраните очередь.")
            return
        try:
            write_fn = _import_write_profile_labels_pdf()
        except Exception as e:
            QMessageBox.warning(self, "PDF", "Модуль печати недоступен: %s" % e)
            return
        path = _unique_label_pdf_path("profile_remnants")
        try:
            write_fn(self._saved_pdf_labels, path)
            QMessageBox.information(self, "PDF", "Файл сохранён:\n%s" % path)
        except Exception as e:
            QMessageBox.critical(self, "PDF", str(e))

    def _export_pdf_open(self):
        if not self._saved_pdf_labels:
            QMessageBox.warning(self, "PDF", "Сначала удерживающим нажатием сохраните очередь.")
            return
        try:
            write_fn = _import_write_profile_labels_pdf()
        except Exception as e:
            QMessageBox.warning(self, "PDF", "Модуль печати недоступен: %s" % e)
            return
        fd, path = tempfile.mkstemp(suffix=".pdf", prefix="mirror_profile_labels_")
        os.close(fd)
        try:
            write_fn(self._saved_pdf_labels, path)
            _open_pdf_file(path)
        except Exception as e:
            try:
                os.unlink(path)
            except OSError:
                pass
            QMessageBox.critical(self, "PDF", str(e))


class DeleteRemnantDialog(QDialog):
    """Удалить остаток: выбор материала → список карточек → удержание «Удалить» ~1 с на карточке → архив и снятие со склада."""
    def __init__(self, parent=None, on_deleted=None):
        super().__init__(parent)
        self.on_deleted = on_deleted  # callback после удаления для обновления списка
        self.setWindowTitle("Удалить остаток")
        self.setMinimumSize(500, 400)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Материал:"))
        self.material_combo = QComboBox()
        self.material_combo.setEditable(False)
        names = models.get_all_material_names()
        self.material_combo.addItem("")
        for n in names:
            self.material_combo.addItem(n)
        layout.addWidget(self.material_combo)
        layout.addWidget(
            QLabel("Остатки по выбранному материалу. Удаление: удерживайте кнопку «Удалить» на карточке (~1 с, красная полоса).")
        )
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.grid = QGridLayout(self.content)
        for c in range(REMNANT_TILES_PER_ROW):
            self.grid.setColumnStretch(c, 1)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        self.material_combo.currentTextChanged.connect(self._fill_remnants)
        self._fill_remnants()

    def _fill_remnants(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        name = (self.material_combo.currentText() or "").strip()
        if not name:
            return
        remnants = models.get_remnants_by_material(name)
        # По толщине, затем по площади (убывание)
        remnants = sorted(
            remnants,
            key=lambda r: (int(r.get('thickness_mm') or 4), -(r.get('height_mm') or 0) * (r.get('width_mm') or 0)),
        )
        for i, r in enumerate(remnants):
            w = r.get('width_mm') or 0
            h = r.get('height_mm') or 0
            th = int(r.get('thickness_mm') or 4)
            nm = r.get('name') or name
            tile = GlassRemnantStockTile(
                nm, th, w, h, dict(r), self, on_open_history=None, on_hold_delete=self._on_hold_delete_remnant_in_dialog
            )
            row, col = i // REMNANT_TILES_PER_ROW, i % REMNANT_TILES_PER_ROW
            self.grid.addWidget(tile, row, col)
            if self.grid.rowStretch(row) == 0:
                self.grid.setRowStretch(row, 0)

    def _on_hold_delete_remnant_in_dialog(self, remnant):
        rid = remnant.get("id")
        if rid is None:
            return
        try:
            act_login, act_disp = _glass_deletion_actor_from_parent(self)
            if models.delete_remnant_and_archive(int(rid), deleted_by_login=act_login, deleted_by_display=act_disp):
                if self.on_deleted:
                    self.on_deleted()
                self._fill_remnants()
            else:
                QMessageBox.warning(self, "Ошибка", "Остаток не найден или уже удалён.")
        except ValueError as e:
            QMessageBox.warning(self, "Удаление", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", "Не удалось удалить: %s" % e)


class WarehouseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Склад")
        self.setMinimumSize(900, 520)
        apply_fraction_window_geometry(self, 0.8)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()  # Главные вкладки: Стекло / Профиль

        # ---------- Вкладка Стекло ----------
        glass_tab = QWidget()
        glass_layout = QVBoxLayout(glass_tab)
        self._wh_glass_tree = None
        self._wh_glass_catalog_names = ()
        glass_filter_row = QHBoxLayout()
        glass_filter_row.addWidget(QLabel("Тип стекла / зеркала:"))
        self._glass_filter_type = QComboBox()
        self._glass_filter_type.setMinimumWidth(200)
        glass_filter_row.addWidget(self._glass_filter_type)
        glass_filter_row.addWidget(QLabel("Цвет / вариант:"))
        self._glass_filter_color = QComboBox()
        self._glass_filter_color.setMinimumWidth(180)
        glass_filter_row.addWidget(self._glass_filter_color)
        glass_filter_row.addWidget(QLabel("Толщина (мм):"))
        self._glass_filter_thickness = QComboBox()
        self._glass_filter_thickness.setMinimumWidth(120)
        glass_filter_row.addWidget(self._glass_filter_thickness)
        self._glass_btn_hold_delete = HoldDeleteButtonRTL("Удалить", hold_ms=1000)
        self._glass_btn_hold_delete.setEnabled(False)
        self._glass_btn_hold_delete.setToolTip(
            "Выделите строку в таблице и удерживайте кнопку ~1 с (красная полоса справа налево)"
        )
        self._glass_btn_hold_delete.holdComplete.connect(self._on_glass_table_hold_delete)
        glass_filter_row.addWidget(self._glass_btn_hold_delete)
        glass_filter_row.addStretch()
        glass_layout.addLayout(glass_filter_row)
        self._glass_filter_type.currentIndexChanged.connect(self._on_glass_filter_type_changed)
        self._glass_filter_color.currentIndexChanged.connect(self._on_glass_color_filter_changed)
        self._glass_filter_thickness.currentIndexChanged.connect(self._on_glass_filter_changed)
        self._init_glass_filter_combos()

        self.glass_tabs = QTabWidget()

        # Подвкладка «Целые листы»
        full_tab = QWidget()
        full_tab_layout = QVBoxLayout(full_tab)
        full_btn_row = QHBoxLayout()
        btn_add_sheets = QPushButton("Добавить листы")
        btn_add_sheets.clicked.connect(self._on_add_sheet)
        full_btn_row.addWidget(btn_add_sheets)
        self.btn_deleted_full_archive = QPushButton("Архив удалённых")
        self.btn_deleted_full_archive.setToolTip(
            "Кто, когда и что удалил со склада (целые листы и остатки)"
        )
        self.btn_deleted_full_archive.clicked.connect(self._on_show_deleted_archive)
        full_btn_row.addWidget(self.btn_deleted_full_archive)
        full_btn_row.addStretch()
        full_tab_layout.addLayout(full_btn_row)
        self._full_table = QTableWidget(0, 10)
        self._full_table.setHorizontalHeaderLabels(
            [
                "Материал",
                "Толщина",
                "Размер",
                "Кол-во",
                "Дата",
                "Поставщик",
                "₽/лист",
                "₽/м²",
                "Накладная",
                "Комментарий",
            ]
        )
        self._full_table.verticalHeader().setVisible(False)
        self._setup_glass_table_columns(
            self._full_table,
            {0: 320, 1: 68, 2: 128, 3: 52, 4: 96, 5: 150, 6: 76, 7: 76, 8: 100, 9: 200},
        )
        self._full_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._full_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._full_table.setToolTip(
            "Сводка целых листов на складе (по материалу, толщине и размеру).\n"
            "Двойной клик — таблица всех поступлений этого материала и толщины из БД."
        )
        self._full_table.cellClicked.connect(self._on_full_table_cell_clicked)
        self._full_table.cellDoubleClicked.connect(self._on_full_table_cell_double_clicked)
        self._full_table.itemSelectionChanged.connect(self._update_glass_delete_btn_state)
        full_tab_layout.addWidget(self._full_table, 1)
        self._sheets_cache = []
        self.glass_tabs.addTab(full_tab, "Целые листы")

        # Подвкладка «Остатки»
        rem_tab = QWidget()
        rem_tab_layout = QVBoxLayout(rem_tab)
        rem_btn_row = QHBoxLayout()
        self.btn_add_remnant = QPushButton("Добавить")
        self.btn_add_remnant.setToolTip("Добавить остаток: материал, толщина, размер")
        self.btn_add_remnant.clicked.connect(self._on_add_remnant)
        rem_btn_row.addWidget(self.btn_add_remnant)
        self.btn_deleted_archive = QPushButton("Архив удалённых")
        self.btn_deleted_archive.setToolTip(
            "Кто, когда и что удалил со склада (остатки и целые листы)"
        )
        self.btn_deleted_archive.clicked.connect(self._on_show_deleted_archive)
        rem_btn_row.addWidget(self.btn_deleted_archive)
        rem_btn_row.addStretch()
        rem_tab_layout.addLayout(rem_btn_row)
        self._rem_table = QTableWidget(0, 6)
        self._rem_table.setHorizontalHeaderLabels(
            ["Материал", "Толщина", "№", "Размер", "Площадь м²", "Уник. №"]
        )
        self._rem_table.verticalHeader().setVisible(False)
        self._setup_glass_table_columns(
            self._rem_table,
            {0: 320, 1: 68, 2: 52, 3: 128, 4: 88, 5: 120},
        )
        self._rem_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._rem_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._rem_table.setToolTip(
            "Остатки стекла/зеркала после раскроя.\n"
            "Двойной клик — история реза: схемы листа, откуда взяли, связанные заказы."
        )
        self._rem_table.cellDoubleClicked.connect(self._on_rem_table_cell_double_clicked)
        self._rem_table.itemSelectionChanged.connect(self._update_glass_delete_btn_state)
        rem_tab_layout.addWidget(self._rem_table, 1)
        self._remnants_cache = []
        self.glass_tabs.addTab(rem_tab, "Остатки")
        self.glass_tabs.currentChanged.connect(lambda _i: self._update_glass_delete_btn_state())

        glass_layout.addWidget(self.glass_tabs)
        self.tabs.addTab(glass_tab, "Зеркало / Стекло")

        # ---------- Вкладка Профиль ----------
        profile_tab = QWidget()
        profile_layout = QVBoxLayout(profile_tab)
        self.profile_tabs = QTabWidget()

        prof_full_tab = QWidget()
        prof_full_layout = QVBoxLayout(prof_full_tab)
        prof_btn_row = QHBoxLayout()
        self.btn_add_profile_stock = QPushButton("Добавить")
        self.btn_add_profile_stock.clicked.connect(self._on_add_profile_stock)
        prof_btn_row.addWidget(self.btn_add_profile_stock)
        self.btn_delete_profile_stock = QPushButton("Удалить")
        self.btn_delete_profile_stock.clicked.connect(self._on_delete_profile_stock)
        prof_btn_row.addWidget(self.btn_delete_profile_stock)
        self.btn_deleted_profile_archive = QPushButton("Архив удалённых")
        self.btn_deleted_profile_archive.clicked.connect(self._on_show_profile_deleted_archive)
        prof_btn_row.addWidget(self.btn_deleted_profile_archive)
        prof_btn_row.addStretch()
        prof_full_layout.addLayout(prof_btn_row)
        self.prof_full_scroll = QScrollArea()
        self.prof_full_scroll.setWidgetResizable(True)
        self.prof_full_content = QWidget()
        self.prof_full_list = QVBoxLayout(self.prof_full_content)
        self.prof_full_list.setAlignment(Qt.AlignTop)
        self.prof_full_scroll.setWidget(self.prof_full_content)
        prof_full_layout.addWidget(self.prof_full_scroll, 1)
        self.profile_tabs.addTab(prof_full_tab, "Целые профили")

        prof_rem_tab = QWidget()
        prof_rem_layout = QVBoxLayout(prof_rem_tab)
        prof_rem_btn_row = QHBoxLayout()
        self.btn_add_profile_remnant = QPushButton("Добавить")
        self.btn_add_profile_remnant.clicked.connect(self._on_add_profile_remnant)
        prof_rem_btn_row.addWidget(self.btn_add_profile_remnant)
        self.btn_delete_profile_remnant = QPushButton("Удалить")
        self.btn_delete_profile_remnant.clicked.connect(self._on_delete_profile_stock)
        prof_rem_btn_row.addWidget(self.btn_delete_profile_remnant)
        self.btn_deleted_profile_remnant_archive = QPushButton("Архив удалённых")
        self.btn_deleted_profile_remnant_archive.clicked.connect(self._on_show_profile_deleted_archive)
        prof_rem_btn_row.addWidget(self.btn_deleted_profile_remnant_archive)
        prof_rem_btn_row.addStretch()
        prof_rem_layout.addLayout(prof_rem_btn_row)
        self.prof_rem_scroll = QScrollArea()
        self.prof_rem_scroll.setWidgetResizable(True)
        self.prof_rem_content = QWidget()
        self.prof_rem_main_layout = QVBoxLayout(self.prof_rem_content)
        self.prof_rem_main_layout.setAlignment(Qt.AlignTop)
        self.prof_rem_scroll.setWidget(self.prof_rem_content)
        prof_rem_layout.addWidget(self.prof_rem_scroll, 1)
        self.profile_tabs.addTab(prof_rem_tab, "Остатки")

        prof_waste_tab = QWidget()
        prof_waste_layout = QVBoxLayout(prof_waste_tab)
        self.prof_waste_scroll = QScrollArea()
        self.prof_waste_scroll.setWidgetResizable(True)
        self.prof_waste_content = QWidget()
        self.prof_waste_list = QVBoxLayout(self.prof_waste_content)
        self.prof_waste_list.setAlignment(Qt.AlignTop)
        self.prof_waste_scroll.setWidget(self.prof_waste_content)
        prof_waste_layout.addWidget(self.prof_waste_scroll, 1)
        self.profile_tabs.addTab(prof_waste_tab, "Мусор")

        profile_layout.addWidget(self.profile_tabs)
        self.tabs.addTab(profile_tab, "Профиль")

        layout.addWidget(self.tabs)
        # Ленивая загрузка: при открытии только «Стекло → Целые листы». Остальное — по переключению вкладок.
        self._wh_loaded = {
            "glass_full": False,
            "glass_rem": False,
            "prof_full": False,
            "prof_rem": False,
            "prof_waste": False,
        }
        self._profile_stock_dirty = False
        self._profile_labels_cache = {}
        self._profile_labels_cache_ts = 0.0
        self.tabs.currentChanged.connect(self._on_wh_main_tab_changed)
        self.glass_tabs.currentChanged.connect(self._on_wh_glass_subtab_changed)
        self.profile_tabs.currentChanged.connect(self._on_wh_profile_subtab_changed)
        self._on_wh_glass_subtab_changed(self.glass_tabs.currentIndex())

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, lambda: apply_fraction_window_geometry(self, 0.8))
        if self._profile_stock_dirty and self._wh_loaded.get("prof_full"):
            self._load_profile_full()
        if self._profile_stock_dirty and self._wh_loaded.get("prof_rem"):
            self._load_profile_remnants()
        if self._profile_stock_dirty and self._wh_loaded.get("prof_waste"):
            self._load_profile_waste()
        self._profile_stock_dirty = False

    def _on_wh_main_tab_changed(self, index):
        if index == 0:
            self._on_wh_glass_subtab_changed(self.glass_tabs.currentIndex())
        elif index == 1:
            self._on_wh_profile_subtab_changed(self.profile_tabs.currentIndex())

    def _on_wh_glass_subtab_changed(self, index):
        if index == 0:
            if not self._wh_loaded["glass_full"]:
                self._load_full()
                self._wh_loaded["glass_full"] = True
        else:
            if not self._wh_loaded["glass_rem"]:
                self._load_remnants()
                self._wh_loaded["glass_rem"] = True

    def _on_wh_profile_subtab_changed(self, index):
        if index == 0:
            if not self._wh_loaded["prof_full"]:
                self._load_profile_full()
                self._wh_loaded["prof_full"] = True
        elif index == 1:
            if not self._wh_loaded["prof_rem"]:
                self._load_profile_remnants()
                self._wh_loaded["prof_rem"] = True
        else:
            if not self._wh_loaded["prof_waste"]:
                self._load_profile_waste()
                self._wh_loaded["prof_waste"] = True

    def _load_profile_waste(self):
        while self.prof_waste_list.count():
            item = self.prof_waste_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not hasattr(models, "list_profile_waste"):
            info = QLabel("Таблица мусора профилей недоступна. Перезапустите приложение.")
            info.setStyleSheet("font-size: 13px; color: #223; padding: 8px;")
            self.prof_waste_list.addWidget(info)
            return
        rows = models.list_profile_waste(1000) or []
        if not rows:
            info = QLabel("Записей мусора профилей пока нет.")
            info.setStyleSheet("font-size: 13px; color: #223; padding: 8px;")
            self.prof_waste_list.addWidget(info)
            return
        for r in rows:
            txt = (
                "ID %s · %s / %s · мусор %s мм · заказ %s · причина: %s"
                % (
                    r.get("id") or "—",
                    r.get("series") or "",
                    r.get("name") or "—",
                    r.get("waste_mm") or "—",
                    r.get("order_id") or "—",
                    r.get("reason") or "—",
                )
            )
            lb = QLabel(txt)
            lb.setStyleSheet("font-size: 12px; color: #112; background:#eef2f7; border:1px solid #ccd6e0; border-radius:8px; padding:8px;")
            self.prof_waste_list.addWidget(lb)

    def _on_profile_stock_history(self, row):
        ProfileStockRowHistoryDialog(row, self).exec_()

    def _load_profile_full(self):
        while self.prof_full_list.count():
            item = self.prof_full_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not _has_profile_stock_api():
            info = QLabel("Модуль проф-склада не инициализирован. Перезапустите приложение.")
            info.setStyleSheet("font-size: 13px; color: #223; padding: 8px;")
            self.prof_full_list.addWidget(info)
            return
        rows = models.get_profile_stock(is_remnant=False)
        if not rows:
            info = QLabel("Склад профилей пуст. Нажмите «Добавить».")
            info.setStyleSheet("font-size: 13px; color: #223; padding: 8px;")
            self.prof_full_list.addWidget(info)
            return
        ref_ids = []
        for p in rows:
            if str(p.get("item_type") or "") == "profile" and p.get("ref_id") is not None:
                try:
                    ref_ids.append(int(p["ref_id"]))
                except (TypeError, ValueError):
                    pass
        profile_by_ref = {}
        if facades_get_profiles_by_ids and ref_ids:
            try:
                profile_by_ref = facades_get_profiles_by_ids(ref_ids)
            except Exception:
                profile_by_ref = {}
        for p in rows:
            self.prof_full_list.addWidget(
                CompactProfileStockRow(
                    p,
                    is_remnant=False,
                    parent=self,
                    on_history=None,
                    profile_by_ref=profile_by_ref,
                    label_by_stock=self._profile_labels_cache,
                    on_hold_delete=self._on_profile_hold_delete,
                )
            )
        self._profile_stock_dirty = False

    def _load_profile_remnants(self):
        while self.prof_rem_main_layout.count():
            item = self.prof_rem_main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not _has_profile_stock_api():
            info = QLabel("Модуль проф-склада не инициализирован. Перезапустите приложение.")
            info.setStyleSheet("font-size: 13px; color: #223; padding: 8px;")
            self.prof_rem_main_layout.addWidget(info)
            return
        rows = models.get_profile_stock(is_remnant=True)
        if not rows:
            info = QLabel("Остатков профилей пока нет.")
            info.setStyleSheet("font-size: 13px; color: #223; padding: 8px;")
            self.prof_rem_main_layout.addWidget(info)
            return
        prof_stock_ids = []
        for r in rows:
            if r.get("id") and str(r.get("item_type") or "") == "profile":
                try:
                    prof_stock_ids.append(int(r["id"]))
                except (TypeError, ValueError):
                    pass
        try:
            if prof_stock_ids:
                existing_lbl = models.get_profile_labels_by_stock_ids(prof_stock_ids) or {}
                need_labels = [
                    s
                    for s in prof_stock_ids
                    if s not in existing_lbl or existing_lbl[s].get("label_number") is None
                ]
                if need_labels:
                    models.ensure_profile_label_numbers_bulk(need_labels)
                self._profile_labels_cache = models.get_profile_labels_by_stock_ids(prof_stock_ids) or {}
                self._profile_labels_cache_ts = time.time()
        except Exception:
            self._profile_labels_cache = {}
            self._profile_labels_cache_ts = time.time()
        ref_ids = []
        for p in rows:
            if str(p.get("item_type") or "") == "profile" and p.get("ref_id") is not None:
                try:
                    ref_ids.append(int(p["ref_id"]))
                except (TypeError, ValueError):
                    pass
        profile_by_ref = {}
        if facades_get_profiles_by_ids and ref_ids:
            try:
                profile_by_ref = facades_get_profiles_by_ids(ref_ids)
            except Exception:
                profile_by_ref = {}
        for r in rows:
            self.prof_rem_main_layout.addWidget(
                CompactProfileStockRow(
                    r,
                    is_remnant=True,
                    parent=self,
                    on_history=self._on_profile_stock_history,
                    profile_by_ref=profile_by_ref,
                    label_by_stock=self._profile_labels_cache,
                    on_hold_delete=self._on_profile_hold_delete,
                )
            )
        self._profile_stock_dirty = False

    def _on_add_profile_stock(self):
        if not _has_profile_stock_api():
            QMessageBox.warning(self, "Профиль", "Функции проф-склада не найдены. Перезапустите приложение.")
            return
        d = AddProfileStockDialog(self, is_remnant_mode=False)
        if d.exec_() != d.Accepted:
            return
        data = d.get_data()
        if not data:
            return
        if str(data.get('item_type') or '').strip().lower() == 'profile':
            qty = max(1, int(data.get('quantity') or 1))
            length_mm = max(1, int(data.get('length_mm') or 5920))
            sid = models.insert_profile_stock(
                'profile',
                data.get('ref_id'),
                data.get('series'),
                data.get('name'),
                data.get('color'),
                length_mm,
                qty,
                is_remnant=False,
            )
            try:
                if sid:
                    models.add_profile_remnant_history(
                        sid,
                        None,
                        'created_on_stock',
                        {
                            'quantity': qty,
                            'nominal_length_mm': length_mm,
                            'stored_length_mm': length_mm,
                        },
                    )
            except Exception:
                pass
        else:
            models.insert_profile_stock(
                data.get('item_type'),
                data.get('ref_id'),
                data.get('series'),
                data.get('name'),
                data.get('color'),
                data.get('length_mm'),
                data.get('quantity', 1),
                is_remnant=False,
            )
        self._profile_stock_dirty = True
        self._load_profile_full()
        self._load_profile_remnants()

    def _on_add_profile_remnant(self):
        if not _has_profile_stock_api():
            QMessageBox.warning(self, "Профиль", "Функции проф-склада не найдены. Перезапустите приложение.")
            return
        d = AddProfileRemnantBatchDialog(self)
        d.exec_()
        self._profile_stock_dirty = True
        self._load_profile_full()
        self._load_profile_remnants()

    def _on_delete_profile_stock(self):
        if not _has_profile_stock_api():
            QMessageBox.warning(self, "Профиль", "Функции проф-склада не найдены. Перезапустите приложение.")
            return
        d = DeleteProfileStockDialog(self, on_deleted=self._on_profile_stock_changed)
        d.exec_()

    def _on_profile_stock_changed(self):
        self._profile_labels_cache = {}
        self._profile_labels_cache_ts = 0.0
        self._profile_stock_dirty = True
        self._load_profile_full()
        self._load_profile_remnants()
        self._load_profile_waste()

    def _on_show_profile_deleted_archive(self):
        if not _has_profile_stock_api():
            QMessageBox.warning(self, "Профиль", "Функции проф-склада не найдены. Перезапустите приложение.")
            return
        d = ProfileDeletedArchiveDialog(self)
        d.exec_()

    def _on_add_sheet(self):
        d = AddSheetDialog(self)
        if d.exec_() != d.Accepted:
            return
        data = d.get_data()
        if not data:
            return
        try:
            arr = data['arrival_date']
            if hasattr(arr, 'toPyDate'):
                arr = arr.toPyDate()
            inv = data.get('invoice_date')
            if inv is not None and hasattr(inv, 'toPyDate'):
                inv = inv.toPyDate()
            models.insert_full_sheet(
                data['name'], data['height_mm'], data['width_mm'],
                arrival_date=arr,
                supplier=data['supplier'],
                supplier_id=data.get('supplier_id'),
                cost=data['cost'],
                warehouse_number=data['warehouse_number'], quantity=data['quantity'],
                comment=data.get('comment'),
                thickness_mm=data.get('thickness_mm', 4),
                invoice_date=inv,
            )
            _play_success_beep()
            self._load_full()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", "Не удалось сохранить: %s" % e)

    def _open_material_receipt_history(self, material_name, thickness_mm):
        d = MaterialReceiptHistoryDialog(material_name, thickness_mm, self)
        d.exec_()

    def _glass_mat_tree(self):
        if self._wh_glass_tree is None:
            raw = _try_load_materials_tree()
            self._wh_glass_tree, self._wh_glass_catalog_names = _build_glass_catalog_material_tree(
                raw, _try_resolve_sheet_material()
            )
        return self._wh_glass_tree

    @staticmethod
    def _setup_glass_table_columns(tbl, widths_by_col):
        hdr = tbl.horizontalHeader()
        for i in range(hdr.count()):
            hdr.setSectionResizeMode(i, QHeaderView.Interactive)
        for col, w in widths_by_col.items():
            hdr.resizeSection(int(col), int(w))

    def _init_glass_filter_combos(self):
        tree = self._glass_mat_tree()
        self._glass_filter_type.blockSignals(True)
        self._glass_filter_type.clear()
        self._glass_filter_type.addItem("Все типы", None)
        for mt in sorted(tree.keys()):
            self._glass_filter_type.addItem(mt, mt)
        self._glass_filter_type.blockSignals(False)
        self._repopulate_glass_color_combo()
        self._repopulate_glass_thickness_combo()

    def _repopulate_glass_color_combo(self):
        mt = self._glass_filter_type.currentData()
        tree = self._glass_mat_tree()
        variants = set()
        if mt is None:
            for vdict in tree.values():
                variants.update(vdict.keys())
        else:
            variants.update((tree.get(mt) or {}).keys())
        if mt and "зеркал" in str(mt).lower():
            var_list = _sort_mirror_variants(list(variants))
        else:
            var_list = sorted(variants, key=lambda x: (x or "").lower())
        self._glass_filter_color.blockSignals(True)
        self._glass_filter_color.clear()
        self._glass_filter_color.addItem("Все цвета", None)
        for var in var_list:
            if var:
                self._glass_filter_color.addItem(var, var)
        self._glass_filter_color.blockSignals(False)

    def _repopulate_glass_thickness_combo(self):
        mt = self._glass_filter_type.currentData()
        var = self._glass_filter_color.currentData()
        tree = self._glass_mat_tree()
        ths = set()
        if mt is None:
            for vdict in tree.values():
                if var is None:
                    for ts in vdict.values():
                        ths.update(ts)
                elif var in vdict:
                    ths.update(vdict[var])
        else:
            vdict = tree.get(mt) or {}
            if var is None:
                for ts in vdict.values():
                    ths.update(ts)
            elif var in vdict:
                ths.update(vdict[var])
        self._glass_filter_thickness.blockSignals(True)
        self._glass_filter_thickness.clear()
        self._glass_filter_thickness.addItem("Все толщины", None)
        for th in sorted(ths):
            self._glass_filter_thickness.addItem(str(th), th)
        self._glass_filter_thickness.blockSignals(False)

    def _glass_row_resolved_variant(self, name, mt_filter):
        name_l = (name or "").strip().lower()
        if not name_l:
            return None
        tree = self._glass_mat_tree()
        resolve = _try_resolve_sheet_material()
        names = self._wh_glass_catalog_names or ()
        if mt_filter:
            for var in (tree.get(mt_filter) or {}):
                resolved = (resolve(mt_filter, var, names) or ("%s %s" % (mt_filter, var)).strip()).lower()
                if name_l == resolved:
                    return var
            return None
        for mt, vdict in tree.items():
            for var in vdict:
                resolved = (resolve(mt, var, names) or ("%s %s" % (mt, var)).strip()).lower()
                if name_l == resolved:
                    return var
        return None

    def _glass_row_matches_color(self, name, mt_filter, color_filter):
        if not color_filter:
            return True
        var = self._glass_row_resolved_variant(name, mt_filter)
        if var is not None:
            return var == color_filter
        return str(color_filter).lower() in (name or "").lower()

    def _glass_row_matches_type(self, name, mt_filter):
        if not mt_filter:
            return True
        name_l = (name or "").strip().lower()
        mt_l = str(mt_filter).strip().lower()
        if not name_l:
            return False
        tree = self._glass_mat_tree()
        resolve = _try_resolve_sheet_material()
        names = self._wh_glass_catalog_names or ()
        for var in (tree.get(mt_filter) or {}):
            resolved = (resolve(mt_filter, var, names) or ("%s %s" % (mt_filter, var)).strip()).lower()
            if name_l == resolved:
                return True
        if name_l.startswith(mt_l):
            return True
        return mt_l in name_l

    def _glass_row_passes_filter(self, row):
        th_filter = self._glass_filter_thickness.currentData()
        mt_filter = self._glass_filter_type.currentData()
        color_filter = self._glass_filter_color.currentData()
        th = int(row.get("thickness_mm") or 4)
        if th_filter is not None and th != int(th_filter):
            return False
        name = (row.get("name") or "").strip()
        if not self._glass_row_matches_type(name, mt_filter):
            return False
        return self._glass_row_matches_color(name, mt_filter, color_filter)

    def _on_glass_filter_type_changed(self, _index=0):
        self._repopulate_glass_color_combo()
        self._repopulate_glass_thickness_combo()
        self._on_glass_filter_changed()

    def _on_glass_color_filter_changed(self, _index=0):
        self._repopulate_glass_thickness_combo()
        self._on_glass_filter_changed()

    def _on_glass_filter_changed(self, _index=0):
        if self._wh_loaded.get("glass_full"):
            self._render_full_table()
        if self._wh_loaded.get("glass_rem"):
            self._render_rem_table()

    def _active_glass_stock_table(self):
        if self.glass_tabs.currentIndex() == 0:
            return self._full_table
        return self._rem_table

    def _update_glass_delete_btn_state(self):
        tbl = self._active_glass_stock_table()
        sel = tbl.selectionModel().selectedRows() if tbl and tbl.selectionModel() else []
        self._glass_btn_hold_delete.setEnabled(bool(sel))

    def _on_glass_table_hold_delete(self):
        tbl = self._active_glass_stock_table()
        if tbl is None:
            return
        sel = tbl.selectionModel().selectedRows() if tbl.selectionModel() else []
        if not sel:
            return
        row = sel[0].row()
        it = tbl.item(row, 0)
        if not it:
            return
        if self.glass_tabs.currentIndex() == 0:
            payload = it.data(Qt.UserRole)
            if not isinstance(payload, dict):
                return
            sheet = payload.get("delete_sheet")
            if not sheet:
                return
            self._on_full_sheet_hold_delete(sheet)
        else:
            remnant = it.data(Qt.UserRole)
            if isinstance(remnant, dict):
                self._on_remnant_hold_delete(remnant)

    @staticmethod
    def _clear_vbox_layout(lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            sub = item.layout()
            if sub:
                WarehouseDialog._clear_vbox_layout(sub)

    def _load_full(self):
        sheets = models.get_all_full_sheets()
        self._sheets_cache = [s for s in sheets if (s.get("quantity") or 0) > 0]
        self._render_full_table()

    def _render_full_table(self):
        tbl = self._full_table
        tbl.setRowCount(0)
        buckets = defaultdict(list)
        for s in self._sheets_cache:
            if not self._glass_row_passes_filter(s):
                continue
            key = (
                (s.get("name") or "").strip(),
                int(s.get("thickness_mm") or 4),
            )
            buckets[key].append(s)
        rows_out = []
        for (name, th) in sorted(buckets.keys(), key=lambda k: (k[0], k[1])):
            items = buckets[(name, th)]
            by_size = defaultdict(list)
            for s in items:
                sk = (int(s.get("width_mm") or 0), int(s.get("height_mm") or 0))
                by_size[sk].append(s)
            for (w, h), grp in sorted(by_size.items(), key=lambda x: -(x[0][0] * x[0][1])):
                merged = dict(grp[0])
                merged["quantity"] = sum(int(x.get("quantity") or 0) for x in grp)
                merged["_full_sheet_group_ids"] = [
                    int(x["id"]) for x in grp if x.get("id") is not None
                ]
                rows_out.append((name, th, w, h, merged))
        tbl.setRowCount(len(rows_out))
        for ri, (name, th, w, h, row) in enumerate(rows_out):
            area = (w * h) / 1_000_000.0 if w > 0 and h > 0 else 0
            cost = float(row.get("cost") or 0)
            m2 = (cost / area) if area > 0 and cost > 0 else 0
            it_mat = QTableWidgetItem(name or "—")
            it_mat.setData(Qt.UserRole, {"name": name, "th": th, "delete_sheet": row})
            tbl.setItem(ri, 0, it_mat)
            tbl.setItem(ri, 1, QTableWidgetItem("%d" % th))
            tbl.setItem(ri, 2, QTableWidgetItem("%d × %d мм" % (w, h)))
            tbl.setItem(ri, 3, QTableWidgetItem(str(row.get("quantity") or 0)))
            ad = row.get("arrival_date")
            ad_s = ad.strftime("%d.%m.%Y") if ad and hasattr(ad, "strftime") else "—"
            tbl.setItem(ri, 4, QTableWidgetItem(ad_s))
            sup = (row.get("supplier") or "—").strip() or "—"
            it_sup = QTableWidgetItem(sup)
            sid = row.get("supplier_id")
            if sid:
                it_sup.setData(Qt.UserRole, int(sid))
                it_sup.setForeground(QColor(21, 101, 192))
                f = it_sup.font()
                f.setUnderline(True)
                it_sup.setFont(f)
            tbl.setItem(ri, 5, it_sup)
            tbl.setItem(ri, 6, QTableWidgetItem("%.2f" % cost if cost else "—"))
            tbl.setItem(ri, 7, QTableWidgetItem("%.2f" % m2 if m2 else "—"))
            tbl.setItem(ri, 8, QTableWidgetItem(str(row.get("warehouse_number") or "—")))
            tbl.setItem(ri, 9, QTableWidgetItem(str(row.get("comment") or "—")[:80]))
        self._update_glass_delete_btn_state()

    def _on_full_table_cell_clicked(self, row, col):
        if col != 5:
            return
        it = self._full_table.item(row, col)
        if it:
            sid = it.data(Qt.UserRole)
            if sid:
                _open_supplier_card_safe(sid, self)

    def _on_full_table_cell_double_clicked(self, row, _col):
        it = self._full_table.item(row, 0)
        if not it:
            return
        data = it.data(Qt.UserRole)
        if isinstance(data, dict):
            name, th = data.get("name"), data.get("th")
        elif data:
            name, th = data
        else:
            return
        self._open_material_receipt_history(name, th)

    def _load_remnants(self):
        remnants = models.get_all_remnants()
        self._remnants_cache = [
            r for r in remnants if not (r or {}).get("reserved_for_cut_order_id")
        ]
        self._render_rem_table()

    def _render_rem_table(self):
        tbl = self._rem_table
        tbl.setRowCount(0)
        rows_out = []
        for r in self._remnants_cache:
            if not self._glass_row_passes_filter(r):
                continue
            name = (r.get("name") or "").strip() or "Без названия"
            th = int(r.get("thickness_mm") or 4)
            w = int(r.get("width_mm") or 0)
            h = int(r.get("height_mm") or 0)
            area = w * h / 1_000_000.0
            rows_out.append((name, th, w, h, area, r))
        rows_out.sort(key=lambda x: (x[0], x[1], -(x[4])))
        tbl.setRowCount(len(rows_out))
        for ri, (name, th, w, h, area, r) in enumerate(rows_out):
            it_mat = QTableWidgetItem(name)
            it_mat.setData(Qt.UserRole, dict(r))
            tbl.setItem(ri, 0, it_mat)
            tbl.setItem(ri, 1, QTableWidgetItem("%d" % th))
            tbl.setItem(ri, 2, QTableWidgetItem(str(r.get("label_number") or r.get("unique_number") or "—")))
            tbl.setItem(ri, 3, QTableWidgetItem("%d × %d мм" % (w, h)))
            tbl.setItem(ri, 4, QTableWidgetItem("%.4f" % area))
            tbl.setItem(ri, 5, QTableWidgetItem(str(r.get("unique_number") or "—")))
        self._update_glass_delete_btn_state()

    def _on_rem_table_cell_double_clicked(self, row, _col):
        it = self._rem_table.item(row, 0)
        if not it:
            return
        remnant = it.data(Qt.UserRole)
        if isinstance(remnant, dict):
            self._on_remnant_click(remnant)

    def _on_remnant_click(self, remnant):
        d = CutHistoryDialog(remnant, self)
        d.exec_()

    def _on_remnant_hold_delete(self, remnant):
        rid = remnant.get("id")
        if rid is None:
            return
        try:
            act_login, act_disp = _glass_deletion_actor_from_parent(self)
            if models.delete_remnant_and_archive(int(rid), deleted_by_login=act_login, deleted_by_display=act_disp):
                self._load_remnants()
            else:
                QMessageBox.warning(self, "Ошибка", "Остаток не найден или уже удалён.")
        except ValueError as e:
            QMessageBox.warning(self, "Удаление", str(e))
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", "Не удалось удалить: %s" % e)

    def _on_profile_hold_delete(self, row):
        sid = row.get("id")
        if sid is None:
            return
        act_login, act_disp = _glass_deletion_actor_from_parent(self)
        try:
            if models.delete_profile_stock_and_archive(
                int(sid), deleted_by_login=act_login, deleted_by_display=act_disp
            ):
                self._load_profile_full()
                self._load_profile_remnants()
            else:
                QMessageBox.warning(self, "Ошибка", "Позиция не найдена или уже удалена.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", "Не удалось удалить: %s" % e)

    def _on_full_sheet_hold_delete(self, sheet):
        gids = sheet.get("_full_sheet_group_ids")
        act_login, act_disp = _glass_deletion_actor_from_parent(self)
        try:
            if gids:
                for sid in gids:
                    if not models.delete_full_sheet_and_archive(
                        int(sid), deleted_by_login=act_login, deleted_by_display=act_disp
                    ):
                        QMessageBox.warning(self, "Ошибка", "Лист №%s не найден или уже удалён." % sid)
                        return
            else:
                sid = sheet.get("id")
                if sid is None:
                    return
                if not models.delete_full_sheet_and_archive(
                    int(sid), deleted_by_login=act_login, deleted_by_display=act_disp
                ):
                    QMessageBox.warning(self, "Ошибка", "Лист не найден или уже удалён.")
                    return
            self._load_full()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", "Не удалось удалить лист: %s" % e)

    def _on_add_remnant(self):
        d = AddRemnantBatchDialog(self)
        d.exec_()
        self._load_remnants()

    def _on_delete_remnant(self):
        d = DeleteRemnantDialog(self, on_deleted=self._load_remnants)
        d.exec_()

    @staticmethod
    def _format_deleted_at(dt):
        if not dt:
            return "—"
        try:
            return dt.strftime("%d.%m.%Y %H:%M") if hasattr(dt, "strftime") else str(dt)
        except Exception:
            return str(dt)

    def _on_show_deleted_archive(self):
        remnants = models.get_deleted_remnants(500) or []
        sheets = models.get_deleted_full_sheets(500) or []
        d = QDialog(self)
        d.setWindowTitle("Архив удалённых (стекло / зеркало)")
        d.setMinimumSize(900, 480)
        lay = QVBoxLayout(d)
        tbl = QTableWidget(0, 6)
        tbl.setHorizontalHeaderLabels(
            ["Вид", "Материал / описание", "Размер (мм)", "Толщина", "Удалил", "Дата и время"]
        )
        tbl.verticalHeader().setVisible(False)
        tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        tbl.horizontalHeader().setStretchLastSection(True)
        tbl.setColumnWidth(0, 110)
        tbl.setColumnWidth(1, 280)
        tbl.setColumnWidth(2, 120)
        tbl.setColumnWidth(3, 72)
        tbl.setColumnWidth(4, 180)
        entries = []
        for r in sheets:
            w = r.get("width_mm") or 0
            h = r.get("height_mm") or 0
            qty = r.get("quantity") or 1
            desc = (r.get("name") or "")[:80]
            if int(qty) > 1:
                desc = "%s (×%s)" % (desc, qty)
            who = r.get("deleted_by_display") or r.get("deleted_by_login") or "—"
            entries.append(
                (
                    r.get("deleted_at"),
                    [
                        "Целый лист",
                        desc,
                        "%d×%d" % (int(w), int(h)),
                        str(r.get("thickness_mm") or "—"),
                        str(who)[:80],
                        self._format_deleted_at(r.get("deleted_at")),
                    ],
                )
            )
        for r in remnants:
            w = r.get("width_mm") or 0
            h = r.get("height_mm") or 0
            who = r.get("deleted_by_display") or r.get("deleted_by_login") or "—"
            entries.append(
                (
                    r.get("deleted_at"),
                    [
                        "Остаток",
                        (r.get("name") or "")[:80],
                        "%d×%d" % (int(w), int(h)),
                        str(r.get("thickness_mm") or "—"),
                        str(who)[:80],
                        self._format_deleted_at(r.get("deleted_at")),
                    ],
                )
            )
        entries.sort(key=lambda x: x[0] or "", reverse=True)
        tbl.setRowCount(len(entries))
        for i, (_, cols) in enumerate(entries):
            for j, val in enumerate(cols):
                tbl.setItem(i, j, QTableWidgetItem(str(val)))
        if not entries:
            lay.addWidget(QLabel("Нет записей. Удалённые позиции появятся здесь после удержания «Удалить» на складе."))
        else:
            lay.addWidget(tbl, 1)
        ok_btn = QDialogButtonBox(QDialogButtonBox.Ok)
        ok_btn.accepted.connect(d.accept)
        lay.addWidget(ok_btn)
        d.exec_()


class AddProfileStockDialog(QDialog):
    """Профиль — выбор как в заказе фасада (таблица + фото); петля — список."""

    def __init__(self, parent=None, is_remnant_mode=False):
        super().__init__(parent)
        self._is_remnant_mode = bool(is_remnant_mode)
        self.setWindowTitle("Добавить остаток профиля" if self._is_remnant_mode else "Добавить на склад профилей")
        self.setMinimumSize(520, 560)
        self._data = None
        self._selected_profile = None
        self._hinges = []
        root = QVBoxLayout(self)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Тип:"))
        self.cmb_item_type = QComboBox()
        self.cmb_item_type.addItems(["Профиль", "Петля"])
        type_row.addWidget(self.cmb_item_type)
        type_row.addStretch()
        root.addLayout(type_row)

        self.profile_panel = QWidget()
        pp = QVBoxLayout(self.profile_panel)
        pp.setSpacing(6)
        self.btn_pick_profile = QPushButton("Выбрать профиль (таблица и фото)…")
        self.btn_pick_profile.clicked.connect(self._on_pick_profile)
        pp.addWidget(self.btn_pick_profile)
        self.lbl_profile_text = QLabel("Профиль не выбран — нажмите кнопку выше.")
        self.lbl_profile_text.setWordWrap(True)
        self.lbl_profile_text.setStyleSheet("color:#333; font-size:12px;")
        pp.addWidget(self.lbl_profile_text)
        self.photo_profile = QLabel()
        self.photo_profile.setFixedSize(220, 220)
        self.photo_profile.setAlignment(Qt.AlignCenter)
        self.photo_profile.setStyleSheet("background:#f0f0f0; border:1px solid #ccc;")
        pp.addWidget(self.photo_profile, 0, Qt.AlignCenter)
        root.addWidget(self.profile_panel)

        self.hinge_panel = QWidget()
        hp = QFormLayout(self.hinge_panel)
        self.cmb_profile = QComboBox()
        self.cmb_profile.setEditable(False)
        hp.addRow("Петля:", self.cmb_profile)
        root.addWidget(self.hinge_panel)

        form = QFormLayout()
        self.len_mm = QSpinBox()
        self.len_mm.setRange(1, 50000)
        self.len_mm.setValue(1000 if self._is_remnant_mode else 6000)
        self.len_mm.setSuffix(" мм")
        form.addRow("Длина (профиль, мм):", self.len_mm)
        self.qty = QSpinBox()
        self.qty.setRange(1, 10000)
        self.qty.setValue(1)
        form.addRow("Количество:", self.qty)
        root.addLayout(form)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._on_accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        self.cmb_item_type.currentIndexChanged.connect(self._on_type_changed)
        self._on_type_changed()

    def _on_type_changed(self):
        is_hinge = self.cmb_item_type.currentIndex() == 1
        self.profile_panel.setVisible(not is_hinge)
        self.hinge_panel.setVisible(is_hinge)
        self.len_mm.setEnabled(not is_hinge)
        if is_hinge:
            self._reload_hinges()

    def _reload_hinges(self):
        self.cmb_profile.clear()
        self._hinges = (facades_get_all_hinges() if facades_get_all_hinges else []) or []
        for h in self._hinges:
            self.cmb_profile.addItem(
                "%s | %s | %s" % (h.get('series') or '', h.get('name') or '', h.get('color') or ''),
                h.get('id')
            )

    def _update_profile_preview(self):
        p = self._selected_profile
        if not p:
            self.lbl_profile_text.setText("Профиль не выбран — нажмите кнопку выше.")
            self.photo_profile.setPixmap(QPixmap())
            self.photo_profile.setText("")
            return
        self.lbl_profile_text.setText(
            "<b>Серия:</b> %s<br/><b>Название:</b> %s<br/><b>Цвет:</b> %s<br/><b>Поставщик:</b> %s<br/><b>Цена/м:</b> %s"
            % (
                p.get('series') or '—',
                p.get('name') or '—',
                p.get('color') or '—',
                p.get('supplier') or '—',
                p.get('price_per_meter') or '—',
            )
        )
        self.lbl_profile_text.setTextFormat(Qt.RichText)
        path = _warehouse_profile_img_path(p)
        if path and os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                self.photo_profile.setPixmap(pix.scaled(220, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.photo_profile.setText("")
                return
        self.photo_profile.setPixmap(QPixmap())
        self.photo_profile.setText("Нет фото")

    def _on_pick_profile(self):
        try:
            from MAIN_PROJECT.ui.facade_profile_dialog import FacadeProfileSelectDialog
        except Exception as e:
            QMessageBox.warning(self, "Профиль", "Не удалось открыть каталог профилей:\n%s" % e)
            return
        d = FacadeProfileSelectDialog(self)
        if d.exec_() != QDialog.Accepted:
            return
        self._selected_profile = d.selected_profile()
        self._update_profile_preview()

    def _on_accept(self):
        is_hinge = self.cmb_item_type.currentIndex() == 1
        if is_hinge:
            idx = self.cmb_profile.currentIndex()
            if idx < 0:
                QMessageBox.warning(self, "Ошибка", "Выберите петлю.")
                return
            row = self._hinges[idx] if idx < len(self._hinges) else None
            if not row:
                return
            self._data = {
                'item_type': 'hinge',
                'ref_id': row.get('id'),
                'series': row.get('series') or '',
                'name': row.get('name') or '',
                'color': row.get('color') or '',
                'length_mm': None,
                'quantity': self.qty.value(),
            }
            self.accept()
            return
        if not self._selected_profile:
            QMessageBox.warning(self, "Ошибка", "Выберите профиль в каталоге (таблица и фото).")
            return
        row = self._selected_profile
        mm = max(1, int(self.len_mm.value() or 0))
        self._data = {
            'item_type': 'profile',
            'ref_id': row.get('id'),
            'series': row.get('series') or '',
            'name': row.get('name') or '',
            'color': row.get('color') or '',
            'length_mm': mm,
            'quantity': self.qty.value(),
        }
        self.accept()

    def get_data(self):
        return self._data


class DeleteProfileStockDialog(QDialog):
    def __init__(self, parent=None, on_deleted=None):
        super().__init__(parent)
        self.on_deleted = on_deleted
        self.setWindowTitle("Удалить со склада профилей")
        self.setMinimumSize(680, 420)
        lay = QVBoxLayout(self)
        self.grid = QGridLayout()
        lay.addLayout(self.grid)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        lay.addWidget(close)
        self._fill()

    def _fill(self):
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        rows = models.get_profile_stock(is_remnant=None)
        for i, r in enumerate(rows):
            txt = "%s | %s | %s | %s мм | x%s" % (
                r.get('item_type') or '',
                r.get('name') or '',
                r.get('color') or '',
                r.get('length_mm') if r.get('length_mm') is not None else '—',
                r.get('quantity') or 1,
            )
            b = QPushButton(txt)
            b.clicked.connect(lambda _, rid=r.get('id'): self._on_delete(rid))
            self.grid.addWidget(b, i // 2, i % 2)

    def _on_delete(self, rid):
        if QMessageBox.question(self, "Подтверждение", "Удалить запись со склада?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        act_login, act_disp = _glass_deletion_actor_from_parent(self)
        if models.delete_profile_stock_and_archive(
            rid, deleted_by_login=act_login, deleted_by_display=act_disp
        ):
            if self.on_deleted:
                self.on_deleted()
            self._fill()


class ProfileDeletedArchiveDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Архив удалённых профилей")
        self.setMinimumSize(700, 420)
        lay = QVBoxLayout(self)
        rows = models.get_deleted_profile_stock(500)
        text = []
        if not rows:
            text.append("Нет записей.")
        else:
            text.append("Тип | Название | Цвет | Длина(мм) | Кол-во | Удалил | Дата удаления")
            text.append("")
            for r in rows:
                dt = r.get('deleted_at')
                dt_str = dt.strftime('%d.%m.%Y %H:%M') if hasattr(dt, 'strftime') else str(dt or '—')
                who = r.get('deleted_by_display') or r.get('deleted_by_login') or '—'
                text.append(
                    "%s | %s | %s | %s | %s | %s | %s" % (
                        r.get('item_type') or '',
                        r.get('name') or '',
                        r.get('color') or '',
                        r.get('length_mm') if r.get('length_mm') is not None else '—',
                        r.get('quantity') or 1,
                        who,
                        dt_str,
                    )
                )
        lbl = QLabel("\n".join(text))
        lbl.setWordWrap(True)
        lbl.setStyleSheet("padding:8px;")
        lay.addWidget(lbl)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
