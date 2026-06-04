# -*- coding: utf-8 -*-
"""
Дельта-обновления кладут .py рядом с MirrorCut.exe, а PyInstaller по умолчанию
берёт модули из _internal (замороженный архив). Meta path hook отдаёт приоритет
файлам на диске в каталоге установки.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import sys
from typing import List, Optional, Sequence


def frozen_install_root() -> Optional[str]:
    if not getattr(sys, "frozen", False):
        return None
    return os.path.dirname(os.path.abspath(sys.executable))


# Пакеты с sys.path = …/MAIN_PROJECT/BLOCKS (см. run.py): calc.*, elements.* и т.д.
_BLOCKS_NAMESPACE_ROOTS = frozenset(
    {
        "calc",
        "elements",
        "sql",
    }
)


def _rel_paths_for_module(fullname: str) -> List[str]:
    """
    Пути .py относительно корня установки (рядом с exe).
    Сначала MAIN_PROJECT/… — иначе ui.* подхватится из корневого ui/.
    Для calc/elements — ещё MAIN_PROJECT/BLOCKS/… (дельта кладёт файлы туда).
    """
    parts = fullname.split(".")
    if not parts:
        return []
    rel_py = "/".join(parts) + ".py"
    out: List[str] = []
    if len(parts) > 1:
        out.append("MAIN_PROJECT/" + rel_py)
        if parts[0] in _BLOCKS_NAMESPACE_ROOTS:
            out.append("MAIN_PROJECT/BLOCKS/" + rel_py)
    out.append(rel_py)
    if parts[0] in _BLOCKS_NAMESPACE_ROOTS:
        out.append("MAIN_PROJECT/BLOCKS/" + rel_py)
    return out


def _purge_pycache_for_file(py_path: str) -> None:
    parent = os.path.dirname(py_path)
    cache = os.path.join(parent, "__pycache__")
    if not os.path.isdir(cache):
        return
    base = os.path.splitext(os.path.basename(py_path))[0]
    for fn in os.listdir(cache):
        if fn.startswith(base + ".") and fn.endswith(".pyc"):
            try:
                os.remove(os.path.join(cache, fn))
            except OSError:
                pass


def purge_pycache_for_rel(install_root: str, rel: str) -> None:
    rel = rel.replace("\\", "/").lstrip("/")
    for base in (install_root, os.path.join(install_root, "_internal")):
        py_path = os.path.join(base, *rel.split("/"))
        if os.path.isfile(py_path):
            _purge_pycache_for_file(py_path)


def install_roots_for_apply(install_root: str) -> Sequence[str]:
    """Куда писать файлы дельты: корень установки и зеркало в _internal."""
    roots = [os.path.abspath(install_root)]
    internal = os.path.join(roots[0], "_internal")
    if os.path.isdir(internal):
        roots.append(internal)
    return roots


class _DeltaOverlayFinder:
    def __init__(self, install_root: str) -> None:
        self._roots = install_roots_for_apply(install_root)

    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002
        # Не перехватывать stdlib/PyQt — меньше задержка старта exe.
        if fullname and "." in fullname:
            top = fullname.split(".", 1)[0]
            if top not in (
                "calc",
                "elements",
                "sql",
                "ui",
                "logic",
                "db",
                "db_main",
                "cfg_loader",
                "delta_import_overlay",
                "update_client",
                "window_branding",
                "app_paths",
                "app_state",
                "config",
            ) and not fullname.startswith("MAIN_PROJECT."):
                return None
        for rel in _rel_paths_for_module(fullname):
            for root in self._roots:
                candidate = os.path.join(root, *rel.split("/"))
                if os.path.isfile(candidate):
                    return importlib.util.spec_from_file_location(
                        fullname,
                        candidate,
                        loader=importlib.machinery.SourceFileLoader(fullname, candidate),
                    )
        return None


def install_delta_import_overlay() -> None:
    root = frozen_install_root()
    if not root:
        return
    for f in sys.meta_path:
        if isinstance(f, _DeltaOverlayFinder) and getattr(f, "_roots", None):
            return
    finder = _DeltaOverlayFinder(root)
    sys.meta_path.insert(0, finder)


def bootstrap_delta_import_overlay_from_disk(exe_dir: Optional[str] = None) -> bool:
    """
    Загрузить этот модуль с диска (дельта) и включить overlay.
    Нужно для exe, собранного до появления overlay в замороженном run.py:
    внутри _internal остаётся старый код, а обновлённые .py лежат рядом с exe.
    """
    if not getattr(sys, "frozen", False):
        install_delta_import_overlay()
        return True
    root = os.path.abspath(exe_dir or frozen_install_root() or "")
    if not root:
        return False
    for rel in (
        "MAIN_PROJECT/delta_import_overlay.py",
        os.path.join("_internal", "MAIN_PROJECT", "delta_import_overlay.py"),
    ):
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            continue
        try:
            spec = importlib.util.spec_from_file_location("_mc_delta_overlay_disk", path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.install_delta_import_overlay()
            return True
        except Exception:
            continue
    try:
        install_delta_import_overlay()
        return True
    except Exception:
        return False
