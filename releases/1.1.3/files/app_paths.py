"""Пути приложения: работа и из exe (PyInstaller frozen), и при запуске из исходников."""
import os
import sys


def _frozen():
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_base_dir():
    """Корень приложения: папка с exe при сборке, иначе папка проекта. Для сохранения файлов и config."""
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_dir():
    """Папка с ресурсами (логотип и т.д.): при сборке — распакованные из exe, иначе — корень проекта."""
    if _frozen():
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))
