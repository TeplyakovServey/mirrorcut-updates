#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка standalone .exe для калькулятора xx.py через PyInstaller и build_xx_exe.spec.

Запуск:
  cd MAIN_PROJECT/BLOCKS
  python build_xx_exe.py

Либо напрямую:
  pyinstaller build_xx_exe.spec

Результат: dist/ArsenalMirrorCalc.exe (onefile).

Требования: pip install pyinstaller PyQt5 PyQtWebEngine psycopg2-binary

Рядом с .exe можно положить config.py с DB_CONFIG или задать MC_PG_*.
"""
from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    blocks_dir = os.path.dirname(os.path.abspath(__file__))
    spec = os.path.join(blocks_dir, "build_xx_exe.spec")
    if not os.path.isfile(spec):
        print("Не найден:", spec)
        return 1
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", spec]
    print("Команда:", " ".join(cmd))
    return subprocess.call(cmd, cwd=blocks_dir)


if __name__ == "__main__":
    raise SystemExit(main())
