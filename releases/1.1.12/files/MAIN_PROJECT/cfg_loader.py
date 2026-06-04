# -*- coding: utf-8 -*-
"""Загрузка app.cfg (INI). Путь к файлу — рядом с run.py или корень MAIN_PROJECT."""
import os
import sys

try:
    from configparser import ConfigParser
except ImportError:
    from ConfigParser import ConfigParser


def _frozen_install_root():
    if not getattr(sys, 'frozen', False):
        return None
    return os.path.dirname(os.path.abspath(sys.executable))


def _frozen_bundle_root():
    """Корень дерева приложения: сначала рядом с exe (дельта), затем _MEIPASS."""
    if not getattr(sys, 'frozen', False):
        return None
    ed = _frozen_install_root()
    if ed:
        mp = os.path.join(ed, 'MAIN_PROJECT')
        if os.path.isdir(mp):
            return ed
        if os.path.isdir(os.path.join(ed, 'db')):
            return ed
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass and os.path.isdir(os.path.join(meipass, 'MAIN_PROJECT')):
        return os.path.abspath(meipass)
    return ed


def get_base_dir():
    """Корень MAIN_PROJECT (папка, где run.py и app.cfg)."""
    if getattr(sys, 'frozen', False):
        br = _frozen_bundle_root()
        if br and os.path.isdir(os.path.join(br, 'MAIN_PROJECT')):
            return os.path.join(br, 'MAIN_PROJECT')
        ed = _frozen_install_root() or os.path.dirname(os.path.abspath(sys.executable))
        return os.path.join(ed, 'MAIN_PROJECT')
    return os.path.dirname(os.path.abspath(__file__))


def get_mirror_cut_root():
    """Корень дерева с раскроем: каталог, где есть app_state.py, ui/, logic/ (не всегда прямой родитель MAIN_PROJECT)."""
    if getattr(sys, 'frozen', False):
        br = _frozen_bundle_root()
        if br:
            return br
        ed = _frozen_install_root()
        if ed and os.path.isdir(os.path.join(ed, 'db')) and os.path.isdir(os.path.join(ed, 'MAIN_PROJECT')):
            return ed
    mp = os.path.normpath(os.path.abspath(get_base_dir()))
    p = mp
    for _ in range(12):
        parent = os.path.dirname(p)
        if parent == p:
            break
        if os.path.isfile(os.path.join(parent, 'app_state.py')):
            return parent
        p = parent
    return os.path.dirname(mp)


def load_cfg():
    """Загрузить app.cfg. Возвращает ConfigParser или None если файла нет."""
    path = os.path.join(get_base_dir(), 'app.cfg')
    if not os.path.isfile(path):
        return None
    cfg = ConfigParser()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cfg.read_file(f)
    except Exception:
        return None
    return cfg


def get_cfg_string(cfg, section, key, default=''):
    if not cfg or not cfg.has_section(section) or not cfg.has_option(section, key):
        return default
    return cfg.get(section, key).strip()


def get_cfg_int(cfg, section, key, default=0):
    try:
        return cfg.getint(section, key) if cfg and cfg.has_section(section) and cfg.has_option(section, key) else default
    except Exception:
        return default


# Глобальный загруженный конфиг (заполняется в run.py при старте)
_app_cfg = None


def set_app_cfg(cfg):
    global _app_cfg
    _app_cfg = cfg


def app_cfg():
    return _app_cfg


def color(section_key):
    """Вернуть HEX цвет из [colors]: section_key например 'main_window_bg'."""
    c = app_cfg()
    if not c or not c.has_section('colors') or not c.has_option('colors', section_key):
        return '#E8F4FC'
    return c.get('colors', section_key).strip() or '#E8F4FC'


def tile_font_size():
    """Размер шрифта в плитках заказов из [ui] tile_font_size (по умолчанию 8)."""
    return get_cfg_int(app_cfg(), 'ui', 'tile_font_size', 8)


def tile_max_height():
    """Максимальная высота плитки в пикселях из [ui] tile_max_height (по умолчанию 95)."""
    return get_cfg_int(app_cfg(), 'ui', 'tile_max_height', 95)
