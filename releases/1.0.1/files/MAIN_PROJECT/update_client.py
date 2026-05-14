# -*- coding: utf-8 -*-
"""
Дельта-обновления десктопа по manifest.json (HTTPS), журнал в _mirrorcut_state/update_journal.json.

Схема манифеста: см. windows_installer/delta_manifest.schema.json
Пути в манифесте — относительно корня установки (каталог MirrorCut.exe).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

STATE_DIR_NAME = "_mirrorcut_state"
INSTALLATION_JSON = "installation.json"
INSTALL_VERSION_FILE = "install_version.txt"
JOURNAL_FILE = "update_journal.json"


def get_install_root() -> str:
    """Корень установки (рядом с MirrorCut.exe)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # разработка: родитель MAIN_PROJECT
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _state_dir(install_root: str) -> str:
    return os.path.join(install_root, STATE_DIR_NAME)


def read_install_version(install_root: Optional[str] = None) -> str:
    root = install_root or get_install_root()
    p = os.path.join(root, INSTALL_VERSION_FILE)
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                v = (f.read() or "").strip()
                if v:
                    return v.splitlines()[0].strip()
        except Exception:
            pass
    inst = os.path.join(_state_dir(root), INSTALLATION_JSON)
    if os.path.isfile(inst):
        try:
            with open(inst, encoding="utf-8") as f:
                data = json.load(f)
            v = (data.get("installed_version") or "").strip()
            if v:
                return v
        except Exception:
            pass
    return "1.0.0"


def normalize_rel_path(rel: str) -> str:
    rel = (rel or "").replace("\\", "/").strip()
    if not rel or rel.startswith("/"):
        raise ValueError("Недопустимый rel_path")
    parts = [p for p in rel.split("/") if p and p != "."]
    if ".." in parts:
        raise ValueError("Недопустимый rel_path")
    out = "/".join(parts)
    if out.split("/")[0].lower() == STATE_DIR_NAME.lower():
        raise ValueError("Нельзя изменять служебный каталог")
    return out


def _safe_target(install_root: str, rel: str) -> str:
    rel_n = normalize_rel_path(rel)
    target = os.path.normpath(os.path.join(install_root, *rel_n.split("/")))
    root_n = os.path.normpath(os.path.abspath(install_root))
    if not (target == root_n or target.startswith(root_n + os.sep)):
        raise ValueError("Выход за пределы install_root")
    return target


def compare_versions(a: str, b: str) -> int:
    """-1 если a<b, 0 если равны, 1 если a>b (простой semver: числовые сегменты)."""

    def seg_tuple(s: str) -> Tuple[int, ...]:
        out: List[int] = []
        for part in (s or "0").strip().split("."):
            try:
                out.append(int(part))
            except ValueError:
                out.append(0)
        return tuple(out)

    ta, tb = seg_tuple(a), seg_tuple(b)
    n = max(len(ta), len(tb))
    ta2 = ta + (0,) * (n - len(ta))
    tb2 = tb + (0,) * (n - len(tb))
    if ta2 < tb2:
        return -1
    if ta2 > tb2:
        return 1
    return 0


def _http_get_bytes(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "MirrorCut-Update/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest_from_bytes(raw: bytes) -> Dict[str, Any]:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest: ожидается объект JSON")
    return data


def _validate_manifest(m: Dict[str, Any]) -> None:
    if "version" not in m or "files" not in m:
        raise ValueError("manifest: нужны поля version и files")
    if not isinstance(m["files"], list):
        raise ValueError("manifest.files: ожидается массив")


def _journal_path(install_root: str) -> str:
    return os.path.join(_state_dir(install_root), JOURNAL_FILE)


def _load_journal(install_root: str) -> List[Dict[str, Any]]:
    p = _journal_path(install_root)
    if not os.path.isfile(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return list(data["entries"])
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_journal(install_root: str, entries: List[Dict[str, Any]]) -> None:
    sd = _state_dir(install_root)
    os.makedirs(sd, exist_ok=True)
    p = _journal_path(install_root)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f, ensure_ascii=False, indent=2)


def rollback_last_update(install_root: Optional[str] = None) -> Tuple[bool, str]:
    """Откат последней записи журнала."""
    root = install_root or get_install_root()
    entries = _load_journal(root)
    if not entries:
        return False, "Нет применённых обновлений в журнале."
    last = entries.pop()
    backup_root = last.get("backup_root") or ""
    if not backup_root or not os.path.isdir(backup_root):
        entries.append(last)
        return False, "Бэкап не найден, откат отменён."

    replaced = list(last.get("replaced") or [])
    added = list(last.get("added") or [])
    deleted = list(last.get("deleted") or [])
    from_version = (last.get("from_version") or "0.0.0").strip()

    try:
        for rel in replaced:
            src = os.path.join(backup_root, "replaced", *normalize_rel_path(rel).split("/"))
            dst = _safe_target(root, rel)
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        for rel in added:
            dst = _safe_target(root, rel)
            if os.path.isfile(dst):
                os.remove(dst)
        for rel in deleted:
            src = os.path.join(backup_root, "deleted", *normalize_rel_path(rel).split("/"))
            dst = _safe_target(root, rel)
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
    except Exception as ex:
        entries.append(last)
        return False, str(ex)

    iv = os.path.join(root, INSTALL_VERSION_FILE)
    try:
        with open(iv, "w", encoding="utf-8") as f:
            f.write(from_version + "\n")
    except Exception:
        pass
    inst = os.path.join(_state_dir(root), INSTALLATION_JSON)
    if os.path.isfile(inst):
        try:
            with open(inst, encoding="utf-8") as f:
                meta = json.load(f)
            meta["installed_version"] = from_version
            meta["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
            with open(inst, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    _save_journal(root, entries)
    return True, "Откат выполнен. Рекомендуется перезапустить программу."


def apply_manifest(
    install_root: str,
    manifest: Dict[str, Any],
    *,
    skip_version_check: bool = False,
    local_version: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Скачать файлы по manifest, проверить sha256, заменить атомарно (через temp), записать журнал.
    """
    root = os.path.abspath(install_root)
    _validate_manifest(manifest)
    to_version = str(manifest["version"]).strip()
    if not to_version:
        return False, "Пустая version в манифесте."

    loc = (local_version or read_install_version(root)).strip() or "0.0.0"
    if not skip_version_check and compare_versions(to_version, loc) <= 0:
        return False, "Манифест не новее локальной версии."

    min_c = (manifest.get("min_client") or "").strip()
    if min_c and compare_versions(loc, min_c) < 0:
        return False, "Локальная версия ниже min_client манифеста."

    files = manifest.get("files") or []
    deletes = list(manifest.get("delete") or [])

    for it in files:
        if not isinstance(it, dict):
            return False, "Некорректный элемент files"
        rp = normalize_rel_path(str(it.get("rel_path") or ""))
        it["_rel"] = rp

    for d in deletes:
        normalize_rel_path(str(d))

    ts = time.strftime("%Y%m%d_%H%M%S")
    state_dir = _state_dir(root)
    os.makedirs(state_dir, exist_ok=True)
    backup_root = os.path.join(state_dir, "backups", "%s__%s" % (to_version, ts))
    replaced_sub = os.path.join(backup_root, "replaced")
    deleted_sub = os.path.join(backup_root, "deleted")
    os.makedirs(replaced_sub, exist_ok=True)
    os.makedirs(deleted_sub, exist_ok=True)

    replaced_list: List[str] = []
    added_list: List[str] = []
    deleted_list: List[str] = []

    tmpdir = tempfile.mkdtemp(prefix="mc_upd_", dir=state_dir)
    try:
        # удаления: сначала бэкап
        for rel in deletes:
            rel = normalize_rel_path(str(rel))
            target = _safe_target(root, rel)
            if os.path.isfile(target):
                bp = os.path.join(deleted_sub, *rel.split("/"))
                os.makedirs(os.path.dirname(bp), exist_ok=True)
                shutil.copy2(target, bp)
                deleted_list.append(rel)

        # скачивание и проверка
        staged: List[Tuple[str, str]] = []
        for it in files:
            rel = it["_rel"]
            url = (it.get("url") or "").strip()
            if not url:
                return False, "У элемента files нет url: %s" % rel
            expect_sha = (it.get("sha256") or "").strip().lower()
            try:
                expect_size = int(it.get("size", -1))
            except (TypeError, ValueError):
                expect_size = -1
            if not expect_sha:
                return False, "Нет sha256 для %s" % rel

            tpath = os.path.join(tmpdir, *rel.split("/"))
            os.makedirs(os.path.dirname(tpath), exist_ok=True)
            data = _http_get_bytes(url)
            if expect_size >= 0 and len(data) != expect_size:
                return False, "Размер не совпадает для %s" % rel
            digest = hashlib.sha256(data).hexdigest().lower()
            if digest != expect_sha:
                return False, "SHA-256 не совпадает для %s" % rel
            with open(tpath, "wb") as out:
                out.write(data)
            staged.append((rel, tpath))

        # удалить файлы из delete
        for rel in deletes:
            rel = normalize_rel_path(str(rel))
            target = _safe_target(root, rel)
            if os.path.isfile(target):
                os.remove(target)

        # установка новых файлов
        for rel, tpath in staged:
            target = _safe_target(root, rel)
            existed = os.path.isfile(target)
            if existed:
                bp = os.path.join(replaced_sub, *rel.split("/"))
                os.makedirs(os.path.dirname(bp), exist_ok=True)
                shutil.copy2(target, bp)
                replaced_list.append(rel)
            else:
                added_list.append(rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.isfile(target):
                os.remove(target)
            shutil.move(tpath, target)

    except (urllib.error.URLError, OSError, ValueError) as ex:
        shutil.rmtree(backup_root, ignore_errors=True)
        return False, str(ex)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # журнал
    entry = {
        "from_version": loc,
        "to_version": to_version,
        "ts": datetime.now(timezone.utc).isoformat(),
        "backup_root": backup_root,
        "replaced": replaced_list,
        "added": added_list,
        "deleted": deleted_list,
    }
    entries = _load_journal(root)
    entries.append(entry)
    _save_journal(root, entries)

    try:
        with open(os.path.join(root, INSTALL_VERSION_FILE), "w", encoding="utf-8") as f:
            f.write(to_version + "\n")
    except Exception:
        pass
    inst = os.path.join(state_dir, INSTALLATION_JSON)
    if os.path.isfile(inst):
        try:
            with open(inst, encoding="utf-8") as f:
                meta = json.load(f)
            meta["installed_version"] = to_version
            meta["last_update_at"] = entry["ts"]
            with open(inst, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return True, "Обновление до %s установлено. Перезапустите программу." % to_version


def check_and_apply_updates_interactive(parent=None) -> bool:
    """
    Сравнить локальную версию с активной в БД; при необходимости скачать manifest_url и применить.
    Вызывается до окна логина (run.py) или с родителем-сплэшем.
    Возвращает True, если нужен перезапуск (успешное обновление).
    """
    try:
        from db import models as db_models
    except Exception:
        return False

    install_root = get_install_root()
    local_v = read_install_version(install_root)
    row = None
    try:
        row = db_models.get_active_desktop_release("mirrorcut")
    except Exception:
        return False
    if not row:
        return False

    url = (row.get("manifest_url") or "").strip()
    remote_v = (row.get("version") or "").strip()
    if not url or not remote_v:
        return False
    if compare_versions(remote_v, local_v) <= 0:
        return False

    try:
        raw = _http_get_bytes(url, timeout=30.0)
        manifest = _load_manifest_from_bytes(raw)
    except urllib.error.HTTPError as ex:
        from PyQt5.QtWidgets import QMessageBox

        if ex.code == 404:
            txt = (
                "В базе указана версия %s, но файл манифеста по ссылке не найден (404).\n\n"
                "Опубликуйте в mirrorcut-updates каталог releases/%s/ (manifest.json) "
                "или исправьте manifest_url в таблице mirror_desktop_app_release.\n\n"
                "Вход в программу без обновления."
                % (remote_v, remote_v)
            )
            if parent is not None:
                QMessageBox.information(parent, "Обновление", txt)
            return False
        if parent is not None:
            QMessageBox.warning(parent, "Обновление", "Не удалось загрузить манифест:\n%s" % ex)
        return False
    except Exception as ex:
        from PyQt5.QtWidgets import QMessageBox

        if parent is not None:
            QMessageBox.warning(parent, "Обновление", "Не удалось загрузить манифест:\n%s" % ex)
        return False

    mv = str(manifest.get("version") or "").strip() or remote_v
    if compare_versions(mv, local_v) <= 0:
        return False

    from PyQt5.QtWidgets import QMessageBox

    r = QMessageBox.question(
        parent,
        "Доступно обновление",
        "Доступна версия %s (у вас %s). Установить сейчас?" % (mv, local_v),
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.Yes,
    )
    if r != QMessageBox.Yes:
        return False

    ok, msg = apply_manifest(install_root, manifest, local_version=local_v)
    if ok:
        QMessageBox.information(parent, "Обновление", msg)
        return True
    QMessageBox.warning(parent, "Обновление", msg)
    return False


_RELEASE_NOTES_DISMISSED_KEY = "release_notes_dismissed_for"


def get_release_notes_dismissed_for(install_root: Optional[str] = None) -> str:
    root = install_root or get_install_root()
    p = os.path.join(_state_dir(root), INSTALLATION_JSON)
    if not os.path.isfile(p):
        return ""
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return (data.get(_RELEASE_NOTES_DISMISSED_KEY) or "").strip()
    except Exception:
        return ""


def set_release_notes_dismissed_for(install_root: Optional[str] = None, version: str = "") -> None:
    root = install_root or get_install_root()
    sd = _state_dir(root)
    os.makedirs(sd, exist_ok=True)
    p = os.path.join(sd, INSTALLATION_JSON)
    data: Dict[str, Any] = {}
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[_RELEASE_NOTES_DISMISSED_KEY] = (version or "").strip()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def maybe_show_release_notes(parent=None) -> None:
    """
    Один раз после обновления: если локальная версия = активной в БД и есть release_notes_url,
    показать окно заметок, пока пользователь не закрыл для этой версии.
    """
    try:
        from db import models as db_models

        from ui.release_notes_preview_dialog import ReleaseNotesPreviewDialog
    except Exception:
        return

    install_root = get_install_root()
    local_v = read_install_version(install_root)
    try:
        row = db_models.get_active_desktop_release("mirrorcut")
    except Exception:
        return
    if not row:
        return
    db_v = (row.get("version") or "").strip()
    if not db_v or local_v != db_v:
        return
    if get_release_notes_dismissed_for(install_root) == local_v:
        return

    mj = row.get("manifest_json")
    if mj is None:
        return
    if isinstance(mj, str):
        try:
            mj = json.loads(mj)
        except Exception:
            return
    if not isinstance(mj, dict):
        return
    notes_url = (mj.get("release_notes_url") or "").strip()
    if not notes_url:
        return

    try:
        raw = _http_get_bytes(notes_url, timeout=25.0)
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return
    html = (data.get("html") or "").strip()
    if not html:
        return
    bg = ReleaseNotesPreviewDialog.validate_hex(str(data.get("canvas_bg") or ""), "#1e3a5f")
    base = notes_url.rsplit("/", 1)[0] + "/"
    dlg = ReleaseNotesPreviewDialog(
        parent,
        html,
        bg,
        notes_base_url=base,
        frameless=True,
    )
    dlg.exec_()
    set_release_notes_dismissed_for(install_root, local_v)


def login_version_summary() -> str:
    """Одна строка для экрана входа: только локальная версия (без запросов к БД)."""
    return "Версия %s" % read_install_version()
