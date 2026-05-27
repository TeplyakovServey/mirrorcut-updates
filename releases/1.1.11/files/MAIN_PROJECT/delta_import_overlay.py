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
    finder = _DeltaOverlayFinder(root)
    sys.meta_path.insert(0, finder)
