# -*- coding: utf-8 -*-
"""
Редактор текста обновлений (PyQt5 QTextEdit): цвета, выравнивание, размер, картинки по drop.
Экспорт: release_notes.json + notes_media/ для releases/<ver>/.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from typing import Any, Dict, Optional, Tuple

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import (
    QColor,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextImageFormat,
)
from PyQt5.QtWidgets import (
    QColorDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from urllib.parse import quote

from .release_notes_preview_dialog import ReleaseNotesPreviewDialog

_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _scale_to_max_side(w: int, h: int, max_side: int = 300) -> Tuple[int, int]:
    if w <= 0 or h <= 0:
        return max_side, max_side
    m = max(w, h)
    if m <= max_side:
        return w, h
    s = max_side / float(m)
    return max(1, int(w * s)), max(1, int(h * s))


def _strip_qt_empty_paragraphs(inner: str) -> str:
    """
    QTextDocument.toHtml() для блока N>0 почти всегда добавляет пустой <p><br/></p>
    (разделитель абзаца). В QTextEdit это не заметно, в QTextBrowser — лишняя пустая строка.
    """
    s = (inner or "").strip()
    if not s:
        return ""
    s = re.sub(
        r'<p\s+[^>]*-qt-paragraph-type:\s*empty[^>]*>\s*(?:<br\s*/?>|)\s*</p>\s*',
        "",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"<p[^>]*>\s*(?:<br\s*/?>|&nbsp;|\u00a0)?\s*</p>\s*",
        "",
        s,
        flags=re.I,
    )
    return s.strip()


def _block_has_image(block) -> bool:
    it = block.begin()
    while not it.atEnd():
        if it.fragment().isValid():
            cf = it.fragment().charFormat()
            if cf.isImageFormat():
                return True
        it += 1
    return False


def _document_to_export_html(doc: QTextDocument) -> str:
    """Один блок = один div.rn-block с text-align (лево / центр / право) — так же в макете и у клиента."""
    parts: list[str] = []
    block = doc.firstBlock()
    while block.isValid():
        align = block.blockFormat().alignment()
        if align & Qt.AlignRight:
            ta = "right"
        elif align & Qt.AlignHCenter:
            ta = "center"
        else:
            ta = "left"
        cur = QTextCursor(block)
        cur.select(QTextCursor.BlockUnderCursor)
        frag = cur.selection()
        sub = QTextDocument()
        sub_tc = QTextCursor(sub)
        sub_tc.insertFragment(frag)
        # PyQt5: encoding — QByteArray | bytes, не str
        raw = sub.toHtml(b"utf-8")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        m = re.search(r"<body[^>]*>([\s\S]*)</body>", raw, re.I)
        inner = _strip_qt_empty_paragraphs(m.group(1) if m else raw)
        if not inner and not _block_has_image(block):
            block = block.next()
            continue
        if not inner:
            inner = "<br/>"
        parts.append('<div class="rn-block" style="text-align:%s;">%s</div>' % (ta, inner))
        block = block.next()
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" '
        '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-strict.dtd">'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="utf-8"/></head><body>'
        + "".join(parts)
        + "</body></html>"
    )


def _apply_swatch(btn: QPushButton, color: str) -> None:
    btn.setStyleSheet(
        "QPushButton { background-color: %s; border: 1px solid #555; border-radius: 4px; }" % color
    )


class _NoteTextEdit(QTextEdit):
    def __init__(self, owner: "RichReleaseNotesEditor") -> None:
        super().__init__()
        self._owner = owner

    def mousePressEvent(self, event) -> None:  # noqa: N802
        c = self.cursorForPosition(event.pos())
        fmt = c.charFormat()
        if fmt.isImageFormat():
            self._owner._select_image(c, fmt.toImageFormat())
        elif c.position() > 0:
            c2 = QTextCursor(c)
            c2.setPosition(c.position() - 1)
            if c2.charFormat().isImageFormat():
                self._owner._select_image(c2, c2.charFormat().toImageFormat())
            else:
                self._owner._clear_image_selection()
        else:
            self._owner._clear_image_selection()
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        self._owner._drag_enter(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._owner._drop(event)


class RichReleaseNotesEditor(QWidget):
    """Панель: тулбар + QTextEdit. Экспорт в каталог релиза для mirrorcut-updates."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._staging = tempfile.mkdtemp(prefix="mc_rnotes_")
        self._media_dir = os.path.join(self._staging, "notes_media")
        os.makedirs(self._media_dir, exist_ok=True)
        self._img_meta: Dict[str, Dict[str, Any]] = {}
        self._selected_img_basename: Optional[str] = None

        self._canvas_bg = "#1e3a5f"
        self._font_color = "#f5f5f5"
        self._font_bg = "#2d4f7c"
        self._font_pt = 16

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        tb = QHBoxLayout()
        for lab, attr in (("bg_color", "_canvas_bg"), ("font_color", "_font_color"), ("font_bg", "_font_bg")):
            tb.addWidget(QLabel(lab))
            sw = QPushButton()
            sw.setFixedSize(28, 28)
            sw.setProperty("which", attr)
            if attr == "_canvas_bg":
                sw.clicked.connect(self._pick_canvas)
            elif attr == "_font_color":
                sw.clicked.connect(self._pick_font_color)
            else:
                sw.clicked.connect(self._pick_font_bg)
            _apply_swatch(sw, getattr(self, attr))
            setattr(self, "_sw_" + attr, sw)
            tb.addWidget(sw)
        tb.addSpacing(12)
        for text, tip, mode in (
            ("По левому краю", "Выравнивание текущего блока или выделения по левому краю", Qt.AlignLeft),
            ("По центру", "По центру", Qt.AlignHCenter),
            ("По правому краю", "По правому краю", Qt.AlignRight),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda checked=False, m=mode: self._apply_alignment(m))
            tb.addWidget(b)
        tb.addSpacing(12)
        self._btn_preview = QPushButton("показать макет")
        self._btn_preview.setStyleSheet(
            "QPushButton { background-color: #e8944a; color: #111; padding: 6px 14px; border-radius: 8px; }"
        )
        self._btn_preview.clicked.connect(self._show_preview)
        tb.addWidget(self._btn_preview)
        tb.addSpacing(8)
        tb.addWidget(QLabel("Tt"))
        self._ed_size = QLineEdit(str(self._font_pt))
        self._ed_size.setFixedWidth(42)
        self._ed_size.setAlignment(Qt.AlignCenter)
        self._ed_size.editingFinished.connect(self._font_size_from_edit)
        tb.addWidget(self._ed_size)
        b_minus = QPushButton("−")
        b_minus.setFixedWidth(28)
        b_minus.clicked.connect(lambda: self._bump_font(-1))
        b_plus = QPushButton("+")
        b_plus.setFixedWidth(28)
        b_plus.clicked.connect(lambda: self._bump_font(1))
        tb.addWidget(b_minus)
        tb.addWidget(b_plus)
        tb.addStretch(1)
        lay.addLayout(tb)

        self._img_row = QWidget()
        ir = QHBoxLayout(self._img_row)
        ir.setContentsMargins(0, 4, 0, 0)
        ir.addWidget(QLabel("Картинка (клик по изображению): масштаб +%"))
        self._sp_img_plus = QSpinBox()
        self._sp_img_plus.setRange(0, 500)
        self._sp_img_plus.setFixedWidth(64)
        self._sp_img_plus.setToolTip("На сколько процентов увеличить относительно базового размера")
        ir.addWidget(self._sp_img_plus)
        ir.addWidget(QLabel("−%"))
        self._sp_img_minus = QSpinBox()
        self._sp_img_minus.setRange(0, 500)
        self._sp_img_minus.setFixedWidth(64)
        self._sp_img_minus.setToolTip("На сколько процентов уменьшить относительно базового размера")
        ir.addWidget(self._sp_img_minus)
        self._btn_img_apply = QPushButton("Применить к выбранной")
        self._btn_img_apply.clicked.connect(self._apply_image_scale_from_panel)
        ir.addWidget(self._btn_img_apply)
        ir.addStretch(1)
        self._img_row.hide()
        lay.addWidget(self._img_row)

        self._edit = _NoteTextEdit(self)
        self._edit.setAcceptRichText(True)
        self._edit.setPlaceholderText("Текст обновлений…")
        self._edit.setMinimumHeight(220)
        self._edit.setStyleSheet(
            "QTextEdit { background-color: #2a4a7a; color: #f0f0f0; border-radius: 12px; padding: 12px; "
            "font-size: 16px; }"
        )
        self._edit.textChanged.connect(self._sync_default_format)
        lay.addWidget(self._edit, 1)

        self._apply_default_char_format()

    def canvas_bg(self) -> str:
        return self._canvas_bg

    def set_canvas_bg(self, c: str) -> None:
        self._canvas_bg = ReleaseNotesPreviewDialog.validate_hex(c, self._canvas_bg)
        _apply_swatch(self._sw__canvas_bg, self._canvas_bg)

    def _pick_canvas(self) -> None:
        c = QColorDialog.getColor(QColor(self._canvas_bg), self, "bg_color")
        if c.isValid():
            self._canvas_bg = c.name()
            _apply_swatch(self._sw__canvas_bg, self._canvas_bg)

    def _pick_font_color(self) -> None:
        c = QColorDialog.getColor(QColor(self._font_color), self, "font_color")
        if c.isValid():
            self._font_color = c.name()
            _apply_swatch(self._sw__font_color, self._font_color)
            self._merge_sel_color()

    def _pick_font_bg(self) -> None:
        c = QColorDialog.getColor(QColor(self._font_bg), self, "font_bg")
        if c.isValid():
            self._font_bg = c.name()
            _apply_swatch(self._sw__font_bg, self._font_bg)
            self._merge_sel_background()

    def _merge_sel_color(self) -> None:
        c = self._edit.textCursor()
        if not c.hasSelection():
            self._apply_default_char_format()
            return
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._font_color))
        c.mergeCharFormat(fmt)

    def _merge_sel_background(self) -> None:
        """Фон выделения + ~10px визуально через padding в span нельзя в QTextCharFormat; задаём block/char background."""
        c = self._edit.textCursor()
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(self._font_bg))
        fmt.setProperty(QTextCharFormat.FullWidthSelection, True)
        if c.hasSelection():
            c.mergeCharFormat(fmt)
        else:
            c.mergeCharFormat(fmt)

    def _apply_alignment(self, mode: int) -> None:
        c = self._edit.textCursor()
        doc = self._edit.document()
        if c.hasSelection():
            start = c.selectionStart()
            end = c.selectionEnd()
            b0 = doc.findBlock(start)
            b1 = doc.findBlock(end)
            b = b0
            while b.isValid() and b.position() <= b1.position():
                cur = QTextCursor(b)
                nb = cur.blockFormat()
                nb.setAlignment(mode)
                cur.setBlockFormat(nb)
                b = b.next()
        else:
            block = c.block()
            cur = QTextCursor(block)
            nb = cur.blockFormat()
            nb.setAlignment(mode)
            cur.setBlockFormat(nb)

    def _bump_font(self, delta: int) -> None:
        try:
            self._font_pt = max(8, min(72, int(self._ed_size.text().strip() or self._font_pt) + delta))
        except ValueError:
            self._font_pt = max(8, min(72, self._font_pt + delta))
        self._ed_size.setText(str(self._font_pt))
        self._merge_font_size()

    def _font_size_from_edit(self) -> None:
        try:
            self._font_pt = max(8, min(72, int(self._ed_size.text().strip() or "16")))
        except ValueError:
            self._font_pt = 16
        self._ed_size.setText(str(self._font_pt))
        self._merge_font_size()

    def _merge_font_size(self) -> None:
        c = self._edit.textCursor()
        fmt = QTextCharFormat()
        fmt.setFontPointSize(float(self._font_pt))
        if c.hasSelection():
            c.mergeCharFormat(fmt)
        else:
            self._apply_default_char_format()

    def _apply_default_char_format(self) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(self._font_color))
        fmt.setBackground(QColor(self._font_bg))
        fmt.setFontPointSize(float(self._font_pt))
        self._edit.mergeCurrentCharFormat(fmt)

    def _sync_default_format(self) -> None:
        pass

    def _clear_image_selection(self) -> None:
        self._selected_img_basename = None
        self._img_row.hide()

    def _basename_from_image_url(self, url: str) -> str:
        if not url:
            return ""
        local = QUrl(url).toLocalFile()
        if local:
            path = local
        else:
            path = url.replace("file:///", "").replace("file://", "")
        return os.path.basename(path.replace("\\", "/").split("/")[-1].split("?")[0])

    def _select_image(self, cursor: QTextCursor, imgf: QTextImageFormat) -> None:
        base = self._basename_from_image_url(imgf.name())
        if not base:
            self._clear_image_selection()
            return
        self._selected_img_basename = base
        w, h = imgf.width(), imgf.height()
        if base not in self._img_meta:
            bw = int(w) if w > 0 else 300
            bh = int(h) if h > 0 else 300
            self._img_meta[base] = {"plus": 0.0, "minus": 0.0, "bw": bw, "bh": bh}
        m = self._img_meta[base]
        self._sp_img_plus.setValue(int(m.get("plus", 0)))
        self._sp_img_minus.setValue(int(m.get("minus", 0)))
        self._img_row.show()
        self._edit.setTextCursor(cursor)

    def _apply_image_scale_from_panel(self) -> None:
        base = self._selected_img_basename
        if not base or base not in self._img_meta:
            return
        plus = int(self._sp_img_plus.value())
        minus = int(self._sp_img_minus.value())
        m = self._img_meta[base]
        m["plus"] = float(plus)
        m["minus"] = float(minus)
        fac = max(0.12, 1.0 + plus / 100.0 - minus / 100.0)
        nw = max(1, int(int(m["bw"]) * fac))
        nh = max(1, int(int(m["bh"]) * fac))
        self._replace_image_size(base, nw, nh)

    def _replace_image_size(self, basename: str, nw: int, nh: int) -> None:
        doc = self._edit.document()
        block = doc.firstBlock()
        while block.isValid():
            it = block.begin()
            while it != block.end():
                fmt = it.fragment().charFormat()
                if fmt.isImageFormat():
                    imgf = fmt.toImageFormat()
                    bname = self._basename_from_image_url(imgf.name())
                    if bname == basename:
                        pos = it.fragment().position()
                        ln = max(1, it.fragment().length())
                        c = QTextCursor(doc)
                        c.setPosition(pos)
                        c.setPosition(pos + ln, QTextCursor.KeepAnchor)
                        nfmt = QTextImageFormat()
                        nfmt.setName(imgf.name())
                        nfmt.setWidth(nw)
                        nfmt.setHeight(nh)
                        c.removeSelectedText()
                        c.insertImage(nfmt)
                        self._edit.setTextCursor(c)
                        return
                it += 1
            block = block.next()

    def _inject_img_data_attrs(self, html: str) -> str:
        def repl(tag: str) -> str:
            sm = re.search(r'src\s*=\s*["\']([^"\']+)["\']', tag, re.I)
            if not sm:
                return tag
            name = os.path.basename(sm.group(1).replace("\\", "/").split("/")[-1].split("?")[0])
            if name not in self._img_meta:
                return tag
            mm = self._img_meta[name]
            tag2 = re.sub(r'\s*data-mc-(plus|minus|bw|bh)="[^"]*"\s*', "", tag, flags=re.I)
            inj = ' data-mc-plus="%d" data-mc-minus="%d" data-mc-bw="%d" data-mc-bh="%d"' % (
                int(mm.get("plus", 0)),
                int(mm.get("minus", 0)),
                int(mm.get("bw", 0)),
                int(mm.get("bh", 0)),
            )
            tag2 = tag2.rstrip()
            if tag2.endswith("/>"):
                return tag2[:-2].rstrip() + inj + " />"
            if tag2.endswith(">"):
                return tag2[:-1].rstrip() + inj + ">"
            return tag2 + inj

        return re.sub(r"<img\b[^>]+>", lambda m: repl(m.group(0)), html, flags=re.I)

    def _parse_img_meta_from_exported_html(self, html: str) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for m in re.finditer(r"<img\s+([^>]+)>", html, re.I):
            attrs = m.group(1)
            sm = re.search(r'src\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
            if not sm:
                continue
            name = os.path.basename(sm.group(1).replace("\\", "/").split("/")[-1].split("?")[0])

            def gv(pat: str) -> int:
                mm = re.search(pat, attrs, re.I)
                if not mm or not mm.group(1):
                    return 0
                try:
                    return int(mm.group(1))
                except ValueError:
                    return 0

            plus = gv(r'data-mc-plus="(\d+)"')
            minus = gv(r'data-mc-minus="(\d+)"')
            bw = gv(r'data-mc-bw="(\d+)"')
            bh = gv(r'data-mc-bh="(\d+)"')
            if bw <= 0 or bh <= 0:
                wi = re.search(r'\bwidth="(\d+)"', attrs, re.I)
                hi = re.search(r'\bheight="(\d+)"', attrs, re.I)
                bw = int(wi.group(1)) if wi else 300
                bh = int(hi.group(1)) if hi else 300
            out[name] = {"plus": float(plus), "minus": float(minus), "bw": int(bw), "bh": int(bh)}
        return out

    def _drag_enter(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                p = u.toLocalFile()
                if p and os.path.isfile(p) and os.path.splitext(p)[1].lower() in _IMAGE_EXT:
                    event.acceptProposedAction()
                    return
        event.ignore()

    def _drop(self, event) -> None:  # type: ignore[no-untyped-def]
        from PyQt5.QtGui import QPixmap

        for u in event.mimeData().urls():
            path = u.toLocalFile()
            if not path or not os.path.isfile(path):
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext not in _IMAGE_EXT:
                continue
            name = "img_%s%s" % (uuid.uuid4().hex[:10], ext)
            dest = os.path.join(self._media_dir, name)
            shutil.copy2(path, dest)
            pm = QPixmap(path)
            if pm.isNull():
                continue
            w, h = _scale_to_max_side(pm.width(), pm.height(), 300)
            url = QUrl.fromLocalFile(os.path.abspath(dest))
            fmt = QTextImageFormat()
            fmt.setName(url.toString())
            fmt.setWidth(w)
            fmt.setHeight(h)
            self._img_meta[name] = {"plus": 0.0, "minus": 0.0, "bw": w, "bh": h}
            c = self._edit.textCursor()
            bf_c = QTextBlockFormat()
            bf_c.setAlignment(Qt.AlignHCenter)
            c.setBlockFormat(bf_c)
            c.insertImage(fmt)
            bf_l = QTextBlockFormat()
            bf_l.setAlignment(Qt.AlignLeft)
            c.insertBlock(bf_l)
        event.acceptProposedAction()

    def _show_preview(self) -> None:
        html = _document_to_export_html(self._edit.document())
        html = self._inject_img_data_attrs(html)
        dlg = ReleaseNotesPreviewDialog(self, html, self._canvas_bg, base_path=self._staging)
        dlg.exec_()

    def to_plain_changelog(self) -> str:
        """Краткий текст для CHANGELOG.txt (plain)."""
        return self._edit.toPlainText().strip()

    def export_to_release_dir(self, rel_dir: str, base_url: str, version: str) -> Dict[str, Any]:
        """
        Копирует notes_media в rel_dir/notes_media/, пишет release_notes.json.
        Возвращает dict с ключами html, canvas_bg, release_notes_url (HTTPS к JSON).
        """
        rel_dir = os.path.abspath(rel_dir)
        out_media = os.path.join(rel_dir, "notes_media")
        os.makedirs(out_media, exist_ok=True)
        if os.path.isdir(self._media_dir):
            for fn in os.listdir(self._media_dir):
                s = os.path.join(self._media_dir, fn)
                if os.path.isfile(s):
                    shutil.copy2(s, os.path.join(out_media, fn))

        html = _document_to_export_html(self._edit.document())
        html = self._inject_img_data_attrs(html)
        # Заменить file:///.../notes_media/ на относительные notes_media/ для клиента
        staging_abs = os.path.abspath(self._media_dir).replace("\\", "/")
        html = html.replace("file:///" + staging_abs + "/", "notes_media/")
        html = html.replace(staging_abs + "/", "notes_media/")

        base = base_url.rstrip("/") + "/"
        payload = {
            "version": version.strip(),
            "canvas_bg": self._canvas_bg,
            "html": html,
        }
        jpath = os.path.join(rel_dir, "release_notes.json")
        with open(jpath, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        rel_enc = quote("release_notes.json", safe="")
        release_notes_url = "%s%s/%s" % (base, version.strip(), rel_enc)
        return {
            "release_notes_url": release_notes_url,
            "release_notes_bg": self._canvas_bg,
            "manifest_json_extra": {
                "release_notes_url": release_notes_url,
                "release_notes_bg": self._canvas_bg,
            },
        }

    def load_from_release_dir(self, rel_dir: str) -> bool:
        """Загрузить release_notes.json + media из каталога релиза."""
        jpath = os.path.join(rel_dir, "release_notes.json")
        if not os.path.isfile(jpath):
            return False
        try:
            with open(jpath, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return False
        html = (data.get("html") or "").strip()
        if not html:
            return False
        self._img_meta.clear()
        self._img_meta.update(self._parse_img_meta_from_exported_html(html))
        self._selected_img_basename = None
        self._img_row.hide()
        self._canvas_bg = ReleaseNotesPreviewDialog.validate_hex(
            str(data.get("canvas_bg") or ""), self._canvas_bg
        )
        _apply_swatch(self._sw__canvas_bg, self._canvas_bg)
        media = os.path.join(rel_dir, "notes_media")
        if os.path.isdir(media):
            shutil.rmtree(self._media_dir, ignore_errors=True)
            shutil.copytree(media, self._media_dir)
        # Подставить file URL для превью редактора
        html_local = html
        if os.path.isdir(self._media_dir):
            prefix = QUrl.fromLocalFile(os.path.abspath(self._media_dir) + os.sep).toString()
            html_local = html.replace("notes_media/", prefix)
        self._edit.setHtml(html_local)
        return True

    def cleanup_staging(self) -> None:
        shutil.rmtree(self._staging, ignore_errors=True)
