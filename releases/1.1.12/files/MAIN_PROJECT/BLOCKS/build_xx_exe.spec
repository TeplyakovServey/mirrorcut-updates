# -*- mode: python ; coding: utf-8 -*-
# Сборка: cd MAIN_PROJECT/BLOCKS  &&  pyinstaller build_xx_exe.spec
# Требуется: pip install pyinstaller PyQt5 PyQtWebEngine psycopg2-binary

import os
import sys

sys.setrecursionlimit(sys.getrecursionlimit() * 5)

spec_dir = os.path.abspath(SPECPATH)
mirror_root = os.path.abspath(os.path.join(spec_dir, "..", ".."))
xx_py = os.path.join(spec_dir, "xx.py")
kad_json = os.path.join(spec_dir, "data", "kad_ring.json")

datas = []
if os.path.isfile(kad_json):
    datas.append((kad_json, "data"))
logo_dir = os.path.join(mirror_root, "logo")
if os.path.isdir(logo_dir):
    datas.append((logo_dir, "logo"))
lock_dir = os.path.join(mirror_root, "MAIN_PROJECT", "lock")
if os.path.isdir(lock_dir):
    datas.append((lock_dir, "MAIN_PROJECT/lock"))

block_cipher = None

a = Analysis(
    [xx_py],
    pathex=[spec_dir, mirror_root],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "window_branding",
        "app_paths",
        "config",
        "db",
        "db.connection",
        "db.models",
        "psycopg2",
        "psycopg2.extras",
        "PyQt5.QtWebEngineWidgets",
        "PyQt5.QtWebChannel",
        "PyQt5.QtPrintSupport",
        "PyQt5.QtNetwork",
        "shapely",
        "shapely.geometry",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "torchvision",
        "matplotlib",
        "cv2",
        "IPython",
        "jupyter",
        "notebook",
        "jedi",
        "parso",
        "nbformat",
        "pytest",
        "pydantic",
        "pydantic_core",
        "pygame",
        "sklearn",
        "scipy",
        "pandas",
        "tensorflow",
        "tf_keras",
        "keras",
        "tensorboard",
        "plotly",
        "statsmodels",
        "patsy",
        "altair",
        "pyarrow",
        "streamlit",
        "grpc",
        "h5py",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ArsenalMirrorCalc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
