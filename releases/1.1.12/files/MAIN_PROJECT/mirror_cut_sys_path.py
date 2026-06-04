# -*- coding: utf-8 -*-
"""Доступ к модулям раскроя в корне репозитория (MIRROR_CUT/ui, MIRROR_CUT/logic).

MAIN_PROJECT тоже содержит пакеты ui и logic. Один sys.path недостаточно: после первого
import пакет закрепляется в sys.modules и ищет подмодули только по своему __path__.
Поэтому на время операции добавляем в конец __path__ каталоги mirror/ui и mirror/logic —
приоритет у файлов MAIN_PROJECT, отсутствующие модули (create_cut_dialog, labels, …)
подхватываются из корня репозитория."""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager

from cfg_loader import get_base_dir, get_mirror_cut_root


def _abspath_norm(p):
    return os.path.normpath(os.path.abspath(p))


def _extend_package_path(pkg_name, extra_dir):
    """Добавить extra_dir в конец pkg.__path__. Возвращает (pkg, list для восстановления) или (None, None)."""
    pkg = sys.modules.get(pkg_name)
    if pkg is None:
        return None, None
    paths = getattr(pkg, "__path__", None)
    if paths is None:
        return None, None
    old = list(paths)
    extra = _abspath_norm(extra_dir)
    normalized_old = [_abspath_norm(p) for p in old]
    if extra in normalized_old:
        return pkg, old
    pkg.__path__ = old + [extra]
    return pkg, old


@contextmanager
def mirror_cut_imports_first():
    mirror = _abspath_norm(get_mirror_cut_root())
    main = _abspath_norm(get_base_dir())
    blocks = _abspath_norm(os.path.join(main, "BLOCKS"))
    mirror_ui = os.path.join(mirror, "ui")
    mirror_logic = os.path.join(mirror, "logic")

    old_sys_path = list(sys.path)
    head = [mirror, main, blocks]
    tail = [p for p in old_sys_path if p and _abspath_norm(str(p)) not in {mirror, main, blocks}]
    sys.path[:] = head + tail

    _ui_pkg, _ui_old = _extend_package_path("ui", mirror_ui)
    _logic_pkg, _logic_old = _extend_package_path("logic", mirror_logic)

    try:
        yield
    finally:
        if _ui_pkg is not None and _ui_old is not None:
            _ui_pkg.__path__ = _ui_old
        if _logic_pkg is not None and _logic_old is not None:
            _logic_pkg.__path__ = _logic_old
        sys.path[:] = old_sys_path
