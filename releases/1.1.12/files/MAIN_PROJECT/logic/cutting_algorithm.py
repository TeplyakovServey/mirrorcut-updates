# -*- coding: utf-8 -*-
"""
Раскрой: всегда грузим реализацию из дерева FINAL_WINDOW/logic (или logic/ у установки),
чтобы не подхватывался устаревший bytecode/копия без исправлений.
"""
from __future__ import annotations

import importlib.util
import os
import sys


def _resolve_cutting_algorithm_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.normpath(os.path.join(here, "..", "..", "logic", "cutting_algorithm.py")),
        os.path.normpath(os.path.join(here, "..", "..", "..", "logic", "cutting_algorithm.py")),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    raise ImportError(
        "Не найден cutting_algorithm.py (ожидался FINAL_WINDOW/logic или logic/ у установки)."
    )


def _load_impl():
    path = _resolve_cutting_algorithm_path()
    mod_name = "logic._cutting_algorithm_impl"
    mod = sys.modules.get(mod_name)
    if mod is not None and getattr(mod, "__file__", None) == path:
        return mod
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError("Не удалось загрузить %s" % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    mod.__cutting_algorithm_source__ = path
    return mod


_impl = _load_impl()
__cutting_algorithm_source__ = getattr(_impl, "__cutting_algorithm_source__", None)
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})
__doc__ = getattr(_impl, "__doc__", None)
