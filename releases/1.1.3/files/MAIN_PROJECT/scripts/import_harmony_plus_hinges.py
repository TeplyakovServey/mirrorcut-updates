#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Разовый импорт петель HARMONY PLUS в PostgreSQL (facades_hinges), цены с сайта МДМ."""
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


def main():
    from db_main import ensure_facades_tables, facades_import_harmony_plus_hinges_from_seed

    ensure_facades_tables()
    n, err = facades_import_harmony_plus_hinges_from_seed(delete_existing_primary=True)
    if err:
        print("Ошибка:", err)
        return 1
    print("Импортировано записей:", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
