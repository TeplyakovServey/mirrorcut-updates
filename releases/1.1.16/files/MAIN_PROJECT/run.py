#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Точка входа MAIN_PROJECT: загрузка cfg, БД, окно логина, затем главное окно."""
import sys
import os
import threading

# Корень MAIN_PROJECT и MIRROR_CUT — обязательно ставим MAIN_PROJECT первым, иначе подхватится ui из MIRROR_CUT
# Пакет calc лежит в BLOCKS/calc — каталог BLOCKS должен быть в path, иначе from calc... из ui/ не работает.
_this_dir = os.path.dirname(os.path.abspath(__file__))
_mirror_root = os.path.dirname(_this_dir)
# PyInstaller onedir: рядом с exe лежат MAIN_PROJECT/, db/, logic/ — __file__ может указывать в _internal.
if getattr(sys, "frozen", False):
    _exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    _mp = os.path.join(_exe_dir, "MAIN_PROJECT")
    if os.path.isdir(_mp):
        _this_dir = _mp
        _mirror_root = _exe_dir
    try:
        _ov_path = os.path.join(_exe_dir, "MAIN_PROJECT", "delta_import_overlay.py")
        if os.path.isfile(_ov_path):
            import importlib.util

            _spec = importlib.util.spec_from_file_location("_mc_delta_overlay_boot", _ov_path)
            if _spec is not None and _spec.loader is not None:
                _ov_mod = importlib.util.module_from_spec(_spec)
                _spec.loader.exec_module(_ov_mod)
                _ov_mod.bootstrap_delta_import_overlay_from_disk(_exe_dir)
            else:
                raise RuntimeError("no spec")
        else:
            from delta_import_overlay import bootstrap_delta_import_overlay_from_disk

            bootstrap_delta_import_overlay_from_disk(_exe_dir)
    except Exception:
        pass
_blocks_dir = os.path.join(_this_dir, "BLOCKS")
if _this_dir in sys.path:
    sys.path.remove(_this_dir)
sys.path.insert(0, _this_dir)
_insert = 1
if _blocks_dir not in sys.path:
    sys.path.insert(_insert, _blocks_dir)
    _insert += 1
if _mirror_root not in sys.path:
    sys.path.insert(_insert, _mirror_root)

# Загрузка конфига до импорта UI (цвета и т.д.)
from cfg_loader import load_cfg, set_app_cfg, get_base_dir


def main():
    # Обязательно до первого QApplication / QGuiApplication — иначе Qt WebEngine
    # (доставка/замер с картой в BLOCKS) предупреждает или работает некорректно.
    from PyQt5.QtCore import QCoreApplication, Qt

    # До QApplication: иначе заставка с QWebEngineView иногда остаётся пустой (Windows).
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)

    cfg = load_cfg()
    set_app_cfg(cfg)

    # До окна входа: main_users. Проверка дельта-версии и mirror_desktop_app_release — до логина (см. ниже).
    try:
        from db_main import ensure_main_tables

        ensure_main_tables()
    except Exception as e:
        from PyQt5.QtWidgets import QApplication, QMessageBox
        from window_branding import apply_app_icon

        app = QApplication(sys.argv)
        apply_app_icon(app)
        QMessageBox.critical(
            None,
            "Ошибка БД",
            "Не удалось инициализировать базу данных:\n%s" % e,
        )
        return 1

    from PyQt5.QtWidgets import QApplication, QMessageBox
    from ui.arsenal_splash_widget import create_arsenal_loading_splash
    from ui.login_dialog import LoginDialog
    from window_branding import apply_app_icon

    def _load_main_window_class():
        import importlib.util

        mp_ui = os.path.join(get_base_dir(), "ui", "main_window.py")
        if os.path.isfile(mp_ui):
            spec = importlib.util.spec_from_file_location(
                "mirrorcut_ui_main_window", mp_ui
            )
            if spec is not None and spec.loader is not None:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod.MainWindow
        from ui.main_window import MainWindow as _MW

        return _MW

    MainWindow = _load_main_window_class()

    app = QApplication(sys.argv)
    app.setApplicationName("MAIN_PROJECT — расчёт стоимости заказов")
    apply_app_icon(app)
    try:
        from window_branding import install_dialog_flash_guard

        install_dialog_flash_guard(app)
    except Exception:
        pass

    # Миграции таблицы релизов десктопа — до логина, чтобы проверить версию по БД.
    try:
        from db.migrations import ensure_tables as mirror_ensure_tables_prelogin

        mirror_ensure_tables_prelogin()
    except Exception as e:
        QMessageBox.critical(
            None,
            "Ошибка БД",
            "Не удалось подготовить таблицы дельта-обновлений:\n%s" % e,
        )
        return 1

    splash_upd = create_arsenal_loading_splash(app)
    splash_upd.show()
    splash_upd.raise_()
    splash_upd.activateWindow()
    app.processEvents()
    _kick_u = getattr(splash_upd, "ensure_animation_started", None)
    if callable(_kick_u):
        _kick_u()
    app.processEvents()

    try:
        from update_client import check_and_apply_updates_interactive

        if check_and_apply_updates_interactive(
            splash_upd,
            wait_status=None,
            quick_network=True,
        ):
            try:
                splash_upd.close()
            except Exception:
                pass
            return 0
    except Exception as e:
        try:
            import traceback
            from update_client import get_install_root, STATE_DIR_NAME

            root = get_install_root()
            sd = os.path.join(root, STATE_DIR_NAME)
            os.makedirs(sd, exist_ok=True)
            with open(os.path.join(sd, "prelogin_update_check_error.txt"), "w", encoding="utf-8") as f:
                f.write("%s\n\n" % e)
                traceback.print_exc(file=f)
        except Exception:
            pass
    try:
        splash_upd.close()
    except Exception:
        pass

    login = LoginDialog()
    if login.exec_() != LoginDialog.Accepted:
        return 0
    user = login.get_user()
    if not user:
        return 0

    # Заставка после логина — миграции заказов/фасадов и т.д.
    splash = create_arsenal_loading_splash(app)
    splash.show()
    splash.raise_()
    splash.activateWindow()
    app.processEvents()
    _kick = getattr(splash, "ensure_animation_started", None)
    if callable(_kick):
        _kick()
    app.processEvents()
    splash.repaint()
    app.processEvents()

    try:
        from db_main import (
            ensure_facades_tables,
            facades_ensure_import_from_excel_once,
            facades_ensure_harmony_plus_hinges_if_empty,
        )
        from db.migrations import ensure_tables as mirror_ensure_tables

        ensure_facades_tables()
        app.processEvents()
        mirror_ensure_tables()
        app.processEvents()
        # mirror_desktop_app_release уже могли поднять до логина — повторный вызов безопасен.
        facades_ensure_import_from_excel_once()
        app.processEvents()
        facades_ensure_harmony_plus_hinges_if_empty()
        app.processEvents()
    except Exception as e:
        from PyQt5.QtWidgets import QMessageBox

        try:
            splash.close()
        except Exception:
            pass
        QMessageBox.critical(
            None,
            "Ошибка БД",
            "Не удалось подготовить таблицы заказов и фасадов:\n%s" % e,
        )
        return 1

    # Пока не строится MainWindow, даём главному потоку обрабатывать QTimer/QVariantAnimation заставки
    # (иначе движения не видно — сразу начинается долгий __init__ окна).
    from PyQt5.QtCore import QEventLoop, QTimer

    _splash_anim_loop = QEventLoop()
    QTimer.singleShot(2200, _splash_anim_loop.quit)
    _splash_anim_loop.exec_()
    app.processEvents()

    try:
        import inspect

        _mw_params = inspect.signature(MainWindow.__init__).parameters
        if "startup_splash" in _mw_params:
            w = MainWindow(user, startup_splash=splash)
        else:
            try:
                splash.close()
            except Exception:
                pass
            w = MainWindow(user)
    except TypeError:
        try:
            splash.close()
        except Exception:
            pass
        w = MainWindow(user)
    try:
        def _warm_runtime_caches() -> None:
            try:
                import app_state
                from db import models as db_models

                app_state.load_clients(force=True)
                db_models.get_orders_recent(limit=50)
            except Exception:
                pass

        threading.Thread(target=_warm_runtime_caches, daemon=True).start()
    except Exception:
        pass
    return app.exec_()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        # Тихо завершаем GUI-приложение при ручной остановке из терминала.
        sys.exit(0)
