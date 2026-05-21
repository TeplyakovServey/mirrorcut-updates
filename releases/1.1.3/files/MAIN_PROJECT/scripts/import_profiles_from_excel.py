#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Полный импорт профилей фасадов из Profil_new.xlsx (replace=True — таблица очищается)."""
import os
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
_mp = os.path.dirname(_this_dir)
_mirror_root = os.path.dirname(_mp)
if _mp in sys.path:
    sys.path.remove(_mp)
sys.path.insert(0, _mp)
if _mirror_root not in sys.path:
    sys.path.insert(1, _mirror_root)

# Путь к Excel по умолчанию — корень MIRROR_CUT
DEFAULT_XLSX = os.path.join(_mirror_root, "Profil_new.xlsx")


def main():
    xlsx = (sys.argv[1] if len(sys.argv) > 1 else "").strip() or DEFAULT_XLSX
    from db_main import ensure_facades_tables, facades_import_profiles_from_excel

    ensure_facades_tables()
    n, err = facades_import_profiles_from_excel(xlsx, replace=True)
    print(n, err)
    return 0 if err is None else 1


if __name__ == "__main__":
    sys.exit(main())
