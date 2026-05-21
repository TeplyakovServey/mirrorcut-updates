# -*- coding: utf-8 -*-
"""Клиент: автодополнение по первым буквам (QCompleter), без списка в layout — без пустот по высоте."""
import os
import sys

from PyQt5.QtCore import QStringListModel, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCompleter,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from calc import palette as P


def _ensure_repo_root_on_path():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)


class ClientStrip(QWidget):
    """Смена выбранного клиента (id / быстрый клиент) — для немедленного пересчёта наценки в калькуляторе."""

    clientIdentityChanged = pyqtSignal()

    def __init__(self, parent=None, quick_estimate_mode: bool = False):
        super().__init__(parent)
        self._quick_estimate_mode = bool(quick_estimate_mode)
        self._quick_client_id = None
        self._suggestion_by_label = {}
        self._client_id = None
        _ensure_repo_root_on_path()
        try:
            from app_state import filter_clients_by_prefix, load_clients, refresh_clients

            load_clients()
            self._filter = filter_clients_by_prefix
            self._refresh = refresh_clients
        except Exception:
            self._filter = lambda _p: []
            self._refresh = lambda: None

        self.setObjectName("ClientStrip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(0)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Клиент:"))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(
            "Справочник или быстрый клиент…" if self._quick_estimate_mode else "Начните вводить имя…"
        )
        self.edit.textChanged.connect(self._on_text)
        row.addWidget(self.edit, 1)
        self.btn_new = QPushButton("Новый клиент")
        self.btn_new.clicked.connect(self._on_new_client)
        row.addWidget(self.btn_new)
        lay.addLayout(row)

        self._names_model = QStringListModel()
        self.completer = QCompleter(self)
        self.completer.setModel(self._names_model)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.setMaxVisibleItems(12)
        try:
            self.completer.setFilterMode(
                Qt.MatchContains if self._quick_estimate_mode else Qt.MatchStartsWith
            )
        except AttributeError:
            pass
        self.edit.setCompleter(self.completer)
        self.completer.activated[str].connect(self._on_completion_chosen)

        self.setStyleSheet(
            "#ClientStrip { background-color: %s; border: 2px solid #1a1a1a; border-radius: 4px; }"
            "#ClientStrip QLabel { background: transparent; color: #000000; }"
            "#ClientStrip QLineEdit { background-color: %s; color: #000000; border: 1px solid #333; border-radius: 2px; }"
            "#ClientStrip QPushButton { background-color: %s; color: #000000; border: 1px solid #333; border-radius: 2px; "
            "font-weight: bold; padding: 2px 8px; }"
            % (P.CLIENT_STRIP_BG, P.CLIENT_STRIP_BG, P.CLIENT_STRIP_BG)
        )
        self.setMaximumHeight(52)

    def _on_completion_chosen(self, text: str):
        if self._quick_estimate_mode:
            s = self._suggestion_by_label.get((text or "").strip())
            if isinstance(s, dict):
                self._client_id = s.get("client_id")
                if self._client_id is not None:
                    try:
                        self._client_id = int(self._client_id)
                    except (TypeError, ValueError):
                        self._client_id = None
                else:
                    self._client_id = None
                qid = s.get("quick_client_id")
                if qid is not None:
                    try:
                        self._quick_client_id = int(qid)
                    except (TypeError, ValueError):
                        self._quick_client_id = None
                else:
                    self._quick_client_id = None
                canon = (s.get("name") or "").strip()
                if canon:
                    self.edit.setText(canon)
                else:
                    self.edit.setText(text)
                self.clientIdentityChanged.emit()
                return
        self.edit.setText(text)
        self._resolve_id()
        self.clientIdentityChanged.emit()

    def _on_text(self, text):
        self._client_id = None
        self._quick_client_id = None
        prefix = (text or "").strip()
        if not prefix:
            self._names_model.setStringList([])
            self._suggestion_by_label = {}
            self.clientIdentityChanged.emit()
            return
        if self._quick_estimate_mode:
            try:
                from db import models

                sug = models.list_quick_estimate_client_suggestions(prefix, limit=40) or []
            except Exception:
                sug = []
            labels = []
            mp = {}
            for s in sug:
                lab = (s.get("label") or s.get("name") or "").strip()
                if not lab:
                    continue
                labels.append(lab)
                mp[lab] = s
            self._suggestion_by_label = mp
            self._names_model.setStringList(labels[:15])
            self.clientIdentityChanged.emit()
            return
        names = self._filter(prefix)
        self._names_model.setStringList(names[:15])
        self.clientIdentityChanged.emit()

    def _on_new_client(self):
        initial = (self.edit.text() or "").strip()
        if self._quick_estimate_mode:
            try:
                from ui.quick_client_create_dialog import open_quick_client_create_dialog
            except Exception:
                return
            meta = open_quick_client_create_dialog(
                self, initial_name=initial, save_to_quick_table_only=True
            )
            if not meta:
                return
            self._client_id = None
            qid = meta.get("quick_client_id")
            try:
                self._quick_client_id = int(qid) if qid is not None else None
            except (TypeError, ValueError):
                self._quick_client_id = None
            nm = (meta.get("client_name") or "").strip()
            if nm:
                self.edit.setText(nm)
            self._names_model.setStringList([])
            self._suggestion_by_label = {}
            self.clientIdentityChanged.emit()
            return
        try:
            from ui._mirror_dialogs import _load_dialog
        except Exception:
            return
        saved_ui = sys.modules.pop('ui', None)
        try:
            NewClientDialog = _load_dialog('new_client_dialog', 'NewClientDialog')
            if NewClientDialog is None:
                return
            d = NewClientDialog(self, initial_name=initial)
            if d.exec_() != QDialog.Accepted:
                return
            name = d.get_saved_name()
            cid = d.get_saved_client_id() if hasattr(d, "get_saved_client_id") else None
            if name:
                self._refresh()
                self.edit.setText(name)
                self._names_model.setStringList([])
                self._quick_client_id = None
                if cid is not None:
                    try:
                        self._client_id = int(cid)
                    except (TypeError, ValueError):
                        self._client_id = None
                        self._resolve_id()
                else:
                    self._resolve_id()
                self.clientIdentityChanged.emit()
        finally:
            if saved_ui is not None:
                sys.modules['ui'] = saved_ui

    def _resolve_id(self):
        name = (self.edit.text() or "").strip()
        self._client_id = None
        if self._quick_estimate_mode:
            self._quick_client_id = None
        if not name:
            return
        try:
            from db import models

            self._client_id = models.get_client_id_by_name(name)
            if self._quick_estimate_mode and not self._client_id:
                qid = models.get_mirror_quick_client_id_by_name(name)
                if qid is not None:
                    try:
                        self._quick_client_id = int(qid)
                    except (TypeError, ValueError):
                        self._quick_client_id = None
        except Exception:
            self._client_id = None

    def get_payload(self) -> dict:
        self._resolve_id()
        name = (self.edit.text() or "").strip()
        pl = {"Имя": name, "id": self._client_id}
        if self._quick_client_id is not None:
            pl["quick_client_id"] = self._quick_client_id
        return pl

    def set_payload(self, pl: dict) -> None:
        if not isinstance(pl, dict):
            return
        name = (pl.get("Имя") or pl.get("name") or "").strip()
        self.edit.blockSignals(True)
        self.edit.setText(name)
        self.edit.blockSignals(False)
        self._client_id = pl.get("id")
        if self._client_id is not None:
            try:
                self._client_id = int(self._client_id)
            except (TypeError, ValueError):
                self._client_id = None
        self._quick_client_id = pl.get("quick_client_id")
        if self._quick_client_id is not None:
            try:
                self._quick_client_id = int(self._quick_client_id)
            except (TypeError, ValueError):
                self._quick_client_id = None
        if not self._quick_estimate_mode:
            if not self._client_id and name:
                self._resolve_id()
        elif not self._client_id and not self._quick_client_id and name:
            self._resolve_id()
