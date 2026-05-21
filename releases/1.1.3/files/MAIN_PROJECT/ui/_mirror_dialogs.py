# -*- coding: utf-8 -*-
"""Загрузка диалогов из MIRROR_CUT/ui: склад, настройки, редактор таблиц.

Важно: диалоги загружаются «лениво» (по запросу), чтобы не ломать импорт MAIN_PROJECT.ui.

В собранном приложении корень через два «..» от __file__ не всегда совпадает с каталогом,
где лежит ui/ (PyInstaller, _internal). Поэтому сначала тот же путь, что и у приложения:
mirror_cut_imports_first + importlib.import_module('ui.…'). Запасной вариант — файл по
get_mirror_cut_root() из cfg_loader.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys


def _load_dialog(module_name, class_name):
    rel = "ui.%s" % module_name
    try:
        from mirror_cut_sys_path import mirror_cut_imports_first

        with mirror_cut_imports_first():
            mod = importlib.import_module(rel)
            return getattr(mod, class_name)
    except Exception:
        pass

    try:
        from cfg_loader import get_mirror_cut_root

        root = get_mirror_cut_root()
    except Exception:
        root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    path = os.path.join(root, "ui", module_name + ".py")
    if not os.path.isfile(path):
        return None
    spec = importlib.util.spec_from_file_location("mirror_ui_" + module_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    old_path = sys.path.copy()
    saved_ui = sys.modules.pop("ui", None)
    try:
        sys.path.insert(0, root)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return getattr(mod, class_name)
    finally:
        sys.path = old_path
        if saved_ui is not None:
            sys.modules["ui"] = saved_ui


def load_warehouse_dialog():
    """Вернуть класс WarehouseDialog из MIRROR_CUT/ui (или None)."""
    return _load_dialog("warehouse_dialog", "WarehouseDialog")


def load_table_editor_dialog():
    """Вернуть класс TableEditorDialog из MIRROR_CUT/ui (или None)."""
    return _load_dialog("table_editor", "TableEditorDialog")


def load_cutting_result_dialog():
    """Вернуть класс CuttingResultDialog из MIRROR_CUT/ui (или None)."""
    return _load_dialog("cutting_result_dialog", "CuttingResultDialog")
