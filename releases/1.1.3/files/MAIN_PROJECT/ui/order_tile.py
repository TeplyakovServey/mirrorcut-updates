# -*- coding: utf-8 -*-
"""Компактный блок заказа: минимум по высоте, дата, клиент, позиции, итог."""
from __future__ import annotations

import json
import sys
import os
from functools import lru_cache
from datetime import datetime
from typing import Any, Optional
_mp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_root = os.path.dirname(_mp)
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt5.QtWidgets import (
    QFrame, QVBoxLayout, QLabel, QHBoxLayout, QSizePolicy,
    QPushButton,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont

from cfg_loader import color, tile_font_size, tile_max_height
from db_main import order_status_to_ru, ORDER_STATUS_SHIPPED, ORDER_STATUS_DRAFT

try:
    from db.models import ORDER_KIND_GLASS_MIRROR
except Exception:
    ORDER_KIND_GLASS_MIRROR = "glass_mirror"

try:
    from logic.blocks_bundle import parse_bundle
except Exception:
    parse_bundle = None

try:
    from calc import zamer_api_client
except Exception:
    zamer_api_client = None

_ZAMER_ST_RU = {
    "new": "портал: новый",
    "agreed": "портал: согласовано",
    "in_progress": "портал: в работе",
    "completed": "портал: завершён",
}


def _zamer_dict_for_hints(order_data: dict):
    """Блок «Замер» из того же изделия, что и portal_zamer_id (иначе первое с активным замером)."""
    raw = order_data.get("blocks_calc_json")
    if not raw or not str(raw).strip():
        return None
    if parse_bundle:
        try:
            prods = _bundle_products_cached(str(raw))
            fallback = None
            for pr in prods:
                pl = pr.get("payload")
                if not isinstance(pl, dict):
                    continue
                z = pl.get("Замер")
                if not isinstance(z, dict) or not z.get("Активирован"):
                    continue
                zd = z.get("Данные")
                if isinstance(zd, dict) and zd.get("portal_zamer_id") is not None:
                    return z
                if fallback is None:
                    fallback = z
            if fallback is not None:
                return fallback
        except Exception:
            pass
    pl = _first_payload_from_raw(raw)
    z = pl.get("Замер") if isinstance(pl, dict) else None
    return z if isinstance(z, dict) else None


def portal_zamer_id_from_order(order_data: dict):
    """ID заявки на Django-портале из blocks_calc_json (первое изделие с активным замером)."""
    raw = order_data.get("blocks_calc_json")
    if not raw or not parse_bundle:
        return None
    try:
        prods = _bundle_products_cached(str(raw))
        for pr in prods:
            pl = pr.get("payload")
            if not isinstance(pl, dict):
                continue
            z = pl.get("Замер")
            if not isinstance(z, dict) or not z.get("Активирован"):
                continue
            zd = z.get("Данные")
            if not isinstance(zd, dict):
                continue
            zid = zd.get("portal_zamer_id")
            if zid is not None:
                try:
                    return int(zid)
                except (TypeError, ValueError):
                    continue
    except Exception:
        pass
    return None


def _short_assigned_at(iso_s: str) -> str:
    try:
        from ui.portal_time import format_iso_datetime_msk_short

        t = format_iso_datetime_msk_short(str(iso_s), msk_suffix=True)
        if t:
            return t
    except Exception:
        pass
    s = (iso_s or "").strip().replace("Z", "+00:00")
    if not s:
        return ""
    if "T" in s:
        d, t = s.split("T", 1)
        t = t.split("+")[0].split("-")[0] if t else ""
        t = t[:5] if len(t) >= 5 else t
        parts = d.split("-")
        if len(parts) == 3:
            return "%s.%s.%s %s" % (parts[2], parts[1], parts[0], t)
    return s[:16]


def _first_payload_from_raw(raw) -> dict:
    if not raw or not str(raw).strip():
        return {}
    return _first_payload_cached(str(raw))


@lru_cache(maxsize=512)
def _bundle_products_cached(raw_text: str):
    if not raw_text or not raw_text.strip() or not parse_bundle:
        return ()
    try:
        _v, products = parse_bundle(raw_text)
        if not isinstance(products, list):
            return ()
        return tuple(p for p in products if isinstance(p, dict))
    except Exception:
        return ()


@lru_cache(maxsize=512)
def _first_payload_cached(raw_text: str) -> dict:
    if not raw_text or not raw_text.strip():
        return {}
    if parse_bundle:
        try:
            products = _bundle_products_cached(raw_text)
            for pr in products:
                pl = pr.get("payload")
                if isinstance(pl, dict):
                    return pl
        except Exception:
            pass
    try:
        d = json.loads(raw_text)
    except Exception:
        return {}
    return d if isinstance(d, dict) else {}


def _portal_row_service_flags(portal_row: dict) -> tuple[bool, bool, bool]:
    """Как ui.zamer_portal_fulfillment._service_flags_from_row: camelCase + service_type + замер по умолчанию."""
    if not isinstance(portal_row, dict):
        return (False, False, False)
    m = bool(portal_row.get("is_measure") or portal_row.get("isMeasure"))
    i = bool(portal_row.get("is_install") or portal_row.get("isInstall"))
    d = bool(portal_row.get("is_delivery") or portal_row.get("isDelivery"))
    if m or i or d:
        return (m, i, d)
    st = str(portal_row.get("service_type") or "").strip().lower()
    if st == "delivery":
        return (False, False, True)
    if st == "install":
        return (False, True, False)
    if st == "both":
        return (True, True, False)
    if st == "m_d":
        return (True, False, True)
    if st == "i_d":
        return (False, True, True)
    if st == "mid":
        return (True, True, True)
    return (True, False, False)


def _portal_services_short(row: dict) -> str:
    """Краткая метка услуг заявки с портала (замер / доставка / монтаж)."""
    if not isinstance(row, dict):
        return ""
    m, i, d = _portal_row_service_flags(row)
    parts = []
    if m:
        parts.append("замер")
    if d:
        parts.append("доставка")
    if i:
        parts.append("монтаж")
    if not parts:
        return ""
    return "усл.: " + "+".join(parts)


def _parse_date_iso(s) -> Any:
    s = (str(s).strip()[:10] if s is not None else "") or ""
    if len(s) < 8:
        return None
    if len(s) >= 10 and s[4:5] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    if len(s) >= 10 and s[2:3] == ".":
        try:
            return datetime.strptime(s[:10], "%d.%m.%Y").date()
        except ValueError:
            return None
    return None


def _pair_from_strings(df, dt):
    d0 = _parse_date_iso(df)
    d1 = _parse_date_iso(dt)
    return d0, d1


def order_service_intervals_for_filter(order_data: dict, zamer_pg_row: Optional[dict]) -> dict:
    """Интервалы дат для фильтра таблицы заказов: ключи measure, delivery, install → (date|None, date|None)."""
    empty = (None, None)
    out = {"measure": empty, "delivery": empty, "install": empty}
    z = _zamer_dict_for_hints(order_data or {})
    zd = z.get("Данные") if isinstance(z, dict) else None
    if isinstance(zd, dict) and isinstance(z, dict) and z.get("Активирован"):
        d0, d1 = _pair_from_strings(zd.get("date_from"), zd.get("date_to"))
        if zd.get("Замер"):
            out["measure"] = (d0, d1)
        if zd.get("Монтаж"):
            out["install"] = (d0, d1)
        if zd.get("Доставка"):
            out["delivery"] = (d0, d1)
    if out["delivery"] == empty:
        pl = _first_payload_from_raw((order_data or {}).get("blocks_calc_json"))
        dv = pl.get("Доставка") if isinstance(pl, dict) else None
        if isinstance(dv, dict) and dv.get("Активирован"):
            dd = dv.get("Данные")
            if isinstance(dd, dict):
                out["delivery"] = _pair_from_strings(dd.get("date_from"), dd.get("date_to"))
    pg = zamer_pg_row if isinstance(zamer_pg_row, dict) else None
    if pg:
        d0, d1 = _pair_from_strings(pg.get("date_from"), pg.get("date_to"))
        pm, pi, pd = _portal_row_service_flags(pg)
        if out["measure"] == empty and pm:
            out["measure"] = (d0, d1)
        if out["install"] == empty and pi:
            out["install"] = (d0, d1)
        if out["delivery"] == empty and pd:
            out["delivery"] = (d0, d1)
    return out


_RU_MONTH_SHORT = (
    "",
    "янв",
    "фев",
    "мар",
    "апр",
    "май",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
)
_FILE_KIND_BY_SVC = {"measure": "measure", "delivery": "delivery", "install": "install"}
# Доп. значения file_kind с портала (основное — measure/delivery/install).
_FILE_KIND_ALIASES = {
    "measure": frozenset(("measure", "zamer", "visit", "замер")),
    "delivery": frozenset(("delivery", "доставка")),
    "install": frozenset(("install", "монтаж", "mount")),
}

# Терминальные статусы заявки на портале (не только completed).
_PORTAL_DONE_STATUSES = frozenset(
    ("completed", "done", "closed", "finished", "complete", "resolved", "archived")
)


def _portal_raw_status(portal_row: dict) -> str:
    v = portal_row.get("status")
    if v is None:
        v = portal_row.get("Status")
    return str(v or "").strip().lower()


def _portal_status_terminal(portal_row: Optional[dict]) -> bool:
    if not isinstance(portal_row, dict):
        return False
    s = _portal_raw_status(portal_row)
    if s in _PORTAL_DONE_STATUSES:
        return True
    if "заверш" in s or "выполн" in s:
        return True
    return False


def _portal_files_list(portal_row: dict) -> list:
    for key in ("files", "Files", "attachments", "Attachments", "zamer_files"):
        v = portal_row.get(key)
        if isinstance(v, list) and v:
            return [x for x in v if isinstance(x, dict)]
    return []


def _file_row_kind(f: dict) -> str:
    for key in ("file_kind", "fileKind", "kind", "type", "category", "service"):
        v = f.get(key)
        if v is not None and str(v).strip():
            return str(v).strip().lower()
    return ""


def _file_row_has_url(f: dict) -> bool:
    for key in ("resolved_url", "url", "file_url", "fileUrl", "href", "path"):
        v = f.get(key)
        if isinstance(v, str) and v.strip():
            return True
    return False


def _bundle_measure_visit_no_install(order_data: Optional[dict]) -> bool:
    """В расчёте активирован замер без монтажа — однофазная заявка: завершение портала = замер сдан."""
    if not order_data:
        return False
    z = _zamer_dict_for_hints(order_data)
    zd = z.get("Данные") if isinstance(z, dict) else None
    if not isinstance(zd, dict) or not isinstance(z, dict) or not z.get("Активирован"):
        return False
    return bool(zd.get("Замер")) and not bool(zd.get("Монтаж"))


def _portal_row_for_lines(
    portal_row: Optional[dict], blocks_zamer_row: Optional[dict]
) -> Optional[dict]:
    """Пока нет ответа API — подписи в ячейке из строки blocks_zamer (та же БД, что и портал)."""
    if isinstance(portal_row, dict):
        return portal_row
    if isinstance(blocks_zamer_row, dict):
        return blocks_zamer_row
    return None


def _portal_db_file_flags_done(portal_row: dict, service: str) -> bool:
    """Флаги из get_blocks_zamer_rows* (EXISTS по blocks_zamer_file)."""
    if service == "measure" and bool(portal_row.get("has_measure_file")):
        return True
    if service == "delivery" and bool(portal_row.get("has_delivery_file")):
        return True
    if service == "install" and bool(portal_row.get("has_install_file")):
        return True
    return False


def _portal_in_progress_assigned_any(
    portal_row: Optional[dict], blocks_zamer_row: Optional[dict]
) -> bool:
    return _portal_in_progress_assigned(portal_row) or _portal_in_progress_assigned(blocks_zamer_row)


def _portal_progress_assignee(portal_row: Optional[dict], blocks_zamer_row: Optional[dict]) -> tuple[str, str]:
    for row in (portal_row, blocks_zamer_row):
        if not isinstance(row, dict):
            continue
        if (str(row.get("status") or "").strip().lower()) != "in_progress":
            continue
        al = (row.get("assigned_to_login") or "").strip()
        an = (row.get("assigned_to_name") or "").strip()
        if al or an:
            return al, an
    return "", ""


def _fmt_date_short_card(d) -> str:
    if d is None:
        return ""
    try:
        return "%d %s %02d" % (d.day, _RU_MONTH_SHORT[d.month], d.year % 100)
    except Exception:
        return ""


def _fmt_slot_compact(df, dt) -> str:
    d0 = _parse_date_iso(df)
    d1 = _parse_date_iso(dt)
    a = _fmt_date_short_card(d0)
    b = _fmt_date_short_card(d1)
    if a and b:
        return "%s – %s" % (a, b) if a != b else a
    return a or b or ""


def _portal_file_done_for_service(
    portal_row: Optional[dict], service: str, order_data: Optional[dict] = None
) -> bool:
    if not isinstance(portal_row, dict):
        return False
    if _portal_db_file_flags_done(portal_row, service):
        return True
    fk = _FILE_KIND_BY_SVC.get(service)
    aliases = _FILE_KIND_ALIASES.get(service) or frozenset()
    if fk:
        for f in _portal_files_list(portal_row):
            k = _file_row_kind(f)
            if k == fk or k in aliases:
                return True
            if service == "measure" and fk == "measure" and not k and _file_row_has_url(f):
                return True
    if not _portal_status_terminal(portal_row):
        return False
    m, i, d = _portal_row_service_flags(portal_row)
    if service == "measure" and m:
        return True
    if service == "install" and i:
        return True
    if service == "delivery" and d:
        return True
    if service == "measure" and order_data is not None and _bundle_measure_visit_no_install(order_data):
        return True
    return False


def _service_needed(
    order_data: dict,
    portal_row: Optional[dict],
    service: str,
    blocks_zamer_row: Optional[dict] = None,
) -> bool:
    z = _zamer_dict_for_hints(order_data or {})
    zd = z.get("Данные") if isinstance(z, dict) else None
    pl = _first_payload_from_raw((order_data or {}).get("blocks_calc_json"))
    pr = portal_row if isinstance(portal_row, dict) else None
    pg = blocks_zamer_row if isinstance(blocks_zamer_row, dict) else None
    rows = [x for x in (pr, pg) if isinstance(x, dict)]
    if service == "measure":
        if isinstance(z, dict) and z.get("Активирован") and isinstance(zd, dict) and zd.get("Замер"):
            return True
        for r in rows:
            if r.get("is_measure") or r.get("isMeasure"):
                return True
        return False
    if service == "install":
        if isinstance(z, dict) and z.get("Активирован") and isinstance(zd, dict) and zd.get("Монтаж"):
            return True
        for r in rows:
            if r.get("is_install") or r.get("isInstall"):
                return True
        return False
    if isinstance(z, dict) and z.get("Активирован") and isinstance(zd, dict) and zd.get("Доставка"):
        return True
    dv = pl.get("Доставка") if isinstance(pl, dict) else None
    if isinstance(dv, dict) and dv.get("Активирован"):
        return True
    for r in rows:
        if r.get("is_delivery") or r.get("isDelivery"):
            return True
    return False


def _portal_in_progress_assigned(portal_row: Optional[dict]) -> bool:
    if not isinstance(portal_row, dict):
        return False
    if (str(portal_row.get("status") or "").strip().lower()) != "in_progress":
        return False
    return bool((str(portal_row.get("assigned_to_login") or "")).strip())


def _compact_service_lines(
    order_data: dict,
    portal_row: Optional[dict],
    service: str,
    blocks_zamer_row: Optional[dict] = None,
) -> list:
    """Короткие строки без слов «замер/доставка/монтаж» в начале."""
    out: list = []
    z = _zamer_dict_for_hints(order_data or {})
    zd = z.get("Данные") if isinstance(z, dict) else None
    pl = _first_payload_from_raw((order_data or {}).get("blocks_calc_json"))
    pr = _portal_row_for_lines(portal_row, blocks_zamer_row)

    if service == "measure":
        if isinstance(zd, dict):
            slot = _fmt_slot_compact(zd.get("date_from"), zd.get("date_to"))
            if slot:
                out.append(slot)
            op = (zd.get("Оплата") or "").strip()
            if op and op != "не указано":
                out.append("опл.+" if op == "оплачено" else "опл.−")
        if pr and (pr.get("is_measure") or pr.get("isMeasure")):
            ps = _fmt_slot_compact(pr.get("date_from"), pr.get("date_to"))
            if ps and ps not in out:
                out.append(ps)
    elif service == "install":
        if isinstance(zd, dict):
            slot = _fmt_slot_compact(zd.get("date_from"), zd.get("date_to"))
            if slot:
                out.append(slot)
            op = (zd.get("Оплата") or "").strip()
            if op and op != "не указано":
                out.append("опл.+" if op == "оплачено" else "опл.−")
        if pr and (pr.get("is_install") or pr.get("isInstall")):
            ps = _fmt_slot_compact(pr.get("date_from"), pr.get("date_to"))
            if ps and ps not in out:
                out.append(ps)
    else:
        if isinstance(zd, dict) and isinstance(z, dict) and z.get("Активирован") and zd.get("Доставка"):
            slot = _fmt_slot_compact(zd.get("date_from"), zd.get("date_to"))
            if slot:
                out.append(slot)
        dv = pl.get("Доставка") if isinstance(pl, dict) else None
        if isinstance(dv, dict) and dv.get("Активирован"):
            dd = dv.get("Данные")
            if isinstance(dd, dict):
                if dd.get("Внутри КАД", True):
                    out.append("КАД")
                else:
                    out.append("вне КАД")
                op = (dd.get("Оплата") or "").strip()
                if op and op != "не указано":
                    out.append("опл.+" if op == "оплачено" else "опл.−")
        if pr and (pr.get("is_delivery") or pr.get("isDelivery")):
            ps = _fmt_slot_compact(pr.get("date_from"), pr.get("date_to"))
            if ps:
                out.append(ps)

    st = _portal_raw_status(pr) if pr else ""
    if st:
        out.append(_ZAMER_ST_RU.get(st, st)[:18])
    if _portal_status_terminal(pr) and pr:
        ua = pr.get("updated_at")
        if ua:
            short = _short_assigned_at(str(ua))
            if short and short not in out:
                out.append(short)
    return out[:4] if out else ["…"]


def order_service_cell_payload(
    order_data: dict,
    portal_row: Optional[dict],
    service: str,
    blocks_zamer_row: Optional[dict] = None,
) -> dict:
    """Состояние ячейки услуги: none | pending | progress | done + короткие строки."""
    needed = _service_needed(order_data, portal_row, service, blocks_zamer_row)
    if not needed:
        msg = "не нужна" if service == "delivery" else "не нужен"
        return {"state": "none", "lines": [msg], "assignee": None}
    api_done = _portal_file_done_for_service(portal_row, service, order_data)
    db_done = _portal_file_done_for_service(blocks_zamer_row, service, order_data)
    if api_done or db_done:
        return {
            "state": "done",
            "lines": _compact_service_lines(order_data, portal_row, service, blocks_zamer_row),
            "assignee": None,
        }
    if _portal_in_progress_assigned_any(portal_row, blocks_zamer_row):
        al, an = _portal_progress_assignee(portal_row, blocks_zamer_row)
        lines = list(_compact_service_lines(order_data, portal_row, service, blocks_zamer_row))
        shown = an or al
        if shown:
            short_al = shown[:18] + ("…" if len(shown) > 18 else "")
            lines.insert(0, short_al)
        return {"state": "progress", "lines": lines, "assignee": shown or None}
    return {
        "state": "pending",
        "lines": _compact_service_lines(order_data, portal_row, service, blocks_zamer_row),
        "assignee": None,
    }


def order_service_sort_tuple(
    order_data: dict,
    portal_row: Optional[dict],
    service: str,
    blocks_zamer_row: Optional[dict] = None,
):
    p = order_service_cell_payload(order_data, portal_row, service, blocks_zamer_row)
    rank = {"none": 0, "pending": 1, "progress": 2, "done": 3}.get(p.get("state"), 0)
    return (rank, " ".join(p.get("lines") or []).lower())


_SVC_TITLE_RU = {"measure": "Замер", "delivery": "Доставка", "install": "Монтаж"}


def order_service_cell_tooltip(
    order_data: dict,
    portal_row: Optional[dict],
    service: str,
    blocks_zamer_row: Optional[dict] = None,
) -> str:
    """Полная подсказка: слоты с датой (день + месяц словом), портал, кто в работе, кто выложил файл."""
    title = _SVC_TITLE_RU.get(service, service)
    disp = _portal_row_for_lines(portal_row, blocks_zamer_row)
    p = order_service_cell_payload(order_data, portal_row, service, blocks_zamer_row)
    st = p.get("state") or "pending"
    lines: list = ["%s — %s" % (title, {"none": "не нужна", "pending": "ожидание", "progress": "в работе", "done": "готово"}.get(st, st))]

    if st == "none":
        lines.append("По расчёту или порталу услуга не требуется.")
        return "\n".join(lines)

    api_done = (
        _portal_file_done_for_service(portal_row, service, order_data)
        if isinstance(portal_row, dict)
        else False
    )
    db_done = (
        _portal_file_done_for_service(blocks_zamer_row, service, order_data)
        if isinstance(blocks_zamer_row, dict)
        else False
    )
    if st == "done" and db_done and not api_done:
        lines.append("Статус «готово» по данным локальной БД (blocks_zamer); ответ API портала ещё не подгружался или отличается.")

    z = _zamer_dict_for_hints(order_data or {})
    zd = z.get("Данные") if isinstance(z, dict) else None
    if isinstance(zd, dict):
        slot = _fmt_slot_compact(zd.get("date_from"), zd.get("date_to"))
        if slot:
            lines.append("В расчёте слот: %s" % slot)

    if disp:
        pm, pi, pd = _portal_row_service_flags(disp)
        if pm and service == "measure":
            ps = _fmt_slot_compact(disp.get("date_from"), disp.get("date_to"))
            if ps:
                lines.append("Портал (замер), слот: %s" % ps)
        if pd and service == "delivery":
            ps = _fmt_slot_compact(disp.get("date_from"), disp.get("date_to"))
            if ps:
                lines.append("Портал (доставка), слот: %s" % ps)
        if pi and service == "install":
            ps = _fmt_slot_compact(disp.get("date_from"), disp.get("date_to"))
            if ps:
                lines.append("Портал (монтаж), слот: %s" % ps)

        pst = _portal_raw_status(disp)
        if pst:
            lines.append("Статус заявки: %s" % _ZAMER_ST_RU.get(pst, pst))
        al = (disp.get("assigned_to_login") or "").strip()
        an = (disp.get("assigned_to_name") or "").strip()
        if al:
            lines.append("Назначено / в работе у: %s" % (an or al))
        aa = disp.get("assigned_at")
        if aa:
            lines.append("Принято в работу: %s" % _short_assigned_at(str(aa)))
        if st == "done" and _portal_status_terminal(disp):
            uat = disp.get("updated_at")
            if uat:
                lines.append("Завершение заявки: %s" % _short_assigned_at(str(uat)))

        if st == "done":
            fk = _FILE_KIND_BY_SVC.get(service)
            aliases = _FILE_KIND_ALIASES.get(service) or frozenset()
            for f in _portal_files_list(disp):
                k = _file_row_kind(f)
                if k != (fk or "") and k not in aliases:
                    if not (
                        service == "measure"
                        and fk == "measure"
                        and not k
                        and _file_row_has_url(f)
                    ):
                        continue
                who = (f.get("uploaded_by") or "").strip()
                when = f.get("created_at")
                wh = ""
                if when:
                    try:
                        wh = _short_assigned_at(str(when))
                    except Exception:
                        wh = str(when)[:16]
                lines.append("Выложено%s: %s" % ((" (%s)" % wh) if wh else "", who or "—"))

    cell_lines = p.get("lines") or []
    if cell_lines:
        lines.append("—")
        lines.extend(cell_lines)
    return "\n".join(lines)


def _portal_bits_from_api_row(row: dict) -> list:
    bits = []
    if not isinstance(row, dict):
        return bits
    svc = _portal_services_short(row)
    if svc:
        bits.append(svc)
    code = (row.get("status") or "").strip()
    bits.append(_ZAMER_ST_RU.get(code.lower(), ("портал: %s" % code) if code else ""))
    al = (row.get("assigned_to_login") or "").strip()
    an = (row.get("assigned_to_name") or "").strip()
    if al:
        bits.append("исп.: %s" % (an or al))
    aa = row.get("assigned_at") or ""
    if aa:
        bits.append("принят %s" % _short_assigned_at(str(aa)))
    if (row.get("status") or "").strip().lower() == "completed":
        ua = row.get("updated_at")
        if ua:
            bits.append("завершён %s" % _short_assigned_at(str(ua)))
    return bits


def zamer_delivery_hints_from_order_row(order_data: dict, *, fetch_portal: bool = False) -> tuple:
    """Короткие строки для плитки/таблицы: (замер, доставка, монтаж); доставка/монтаж подмешивают портал как замер."""
    raw = order_data.get("blocks_calc_json")
    if not raw or not str(raw).strip():
        return "—", "—", "—"
    pl = _first_payload_from_raw(raw)
    if not pl:
        return "—", "—", "—"
    zid_api = portal_zamer_id_from_order(order_data) if fetch_portal else None
    portal_row = None
    if (
        zid_api is not None
        and fetch_portal
        and zamer_api_client
        and zamer_api_client.api_enabled()
    ):
        try:
            portal_row = zamer_api_client.zamer_get(int(zid_api))
        except Exception:
            portal_row = None
        if not isinstance(portal_row, dict):
            portal_row = None
    zm = ""
    z = _zamer_dict_for_hints(order_data)
    if not isinstance(z, dict) or not z.get("Активирован"):
        if zid_api is not None and fetch_portal and portal_row is not None:
            bits = _portal_bits_from_api_row(portal_row)
            zm = ", ".join(x for x in bits if x) or "портал"
        else:
            zm = "не нужен"
    else:
        zd = z.get("Данные")
        if not isinstance(zd, dict):
            zm = "—"
        elif zd.get("Без замера"):
            zm = "без выезда"
        else:
            bits = []
            df = (zd.get("date_from") or "").strip()
            dt = (zd.get("date_to") or "").strip()
            if df or dt:
                bits.append("%s…%s" % (df or "—", dt or "—"))
            op = (zd.get("Оплата") or "").strip()
            if op and op != "не указано":
                bits.append("опл.%s" % ("+" if op == "оплачено" else "-"))
            if fetch_portal and portal_row is not None:
                bits.extend(x for x in _portal_bits_from_api_row(portal_row) if x)
            zm = ", ".join(x for x in bits if x) or "нужен"
    dl = ""
    dv = pl.get("Доставка")
    if not isinstance(dv, dict) or not dv.get("Активирован"):
        dl = "не нужна"
    else:
        dd = dv.get("Данные")
        if not isinstance(dd, dict):
            dl = "—"
        elif dd.get("Внутри КАД", True):
            dl = "КАД"
        else:
            dl = "вне КАД" if dd.get("Расстояние до КАД") is not None else "вне КАД (адрес)"
        if isinstance(dv.get("Данные"), dict):
            op = (dv["Данные"].get("Оплата") or "").strip()
            if op and op != "не указано":
                dl += ", опл.%s" % ("+" if op == "оплачено" else "-")
    if fetch_portal and portal_row is not None and (
        portal_row.get("is_delivery") or portal_row.get("isDelivery")
    ):
        df = (str(portal_row.get("date_from") or "").strip()[:10] or "—")
        dt = (str(portal_row.get("date_to") or "").strip()[:10] or "—")
        pbits = []
        if df != "—" or dt != "—":
            pbits.append("портал %s…%s" % (df, dt))
        pbits.extend(x for x in _portal_bits_from_api_row(portal_row) if x)
        if pbits:
            extra = ", ".join(pbits)
            dl = extra if dl == "не нужна" else (dl + " · " + extra)
    ih = ""
    z_ok = isinstance(z, dict) and z.get("Активирован")
    zd_m = z.get("Данные") if z_ok else None
    if z_ok and isinstance(zd_m, dict) and zd_m.get("Монтаж"):
        bits_m = []
        df = (zd_m.get("date_from") or "").strip()
        dt = (zd_m.get("date_to") or "").strip()
        if df or dt:
            bits_m.append("%s…%s" % (df or "—", dt or "—"))
        op = (zd_m.get("Оплата") or "").strip()
        if op and op != "не указано":
            bits_m.append("опл.%s" % ("+" if op == "оплачено" else "-"))
        if fetch_portal and portal_row is not None:
            bits_m.extend(x for x in _portal_bits_from_api_row(portal_row) if x)
        ih = ", ".join(x for x in bits_m if x) or "нужен"
    elif fetch_portal and portal_row is not None and (
        portal_row.get("is_install") or portal_row.get("isInstall")
    ):
        bits_i = _portal_bits_from_api_row(portal_row)
        ih = ", ".join(x for x in bits_i if x) or "портал"
    else:
        ih = "не нужен"
    return zm, dl, ih


def _zamer_summary_from_blocks_json(raw) -> str:
    if not raw or not str(raw).strip():
        return ""
    try:
        d = json.loads(str(raw))
    except Exception:
        return ""
    if not isinstance(d, dict):
        return ""
    if parse_bundle:
        try:
            _v, products = parse_bundle(str(raw))
            for pr in products:
                pl = pr.get("payload")
                if isinstance(pl, dict):
                    z = pl.get("Замер")
                    if isinstance(z, dict) and z.get("Активирован"):
                        d = pl
                        break
        except Exception:
            pass
    z = d.get("Замер")
    if not isinstance(z, dict) or not z.get("Активирован"):
        return ""
    zd = z.get("Данные")
    if not isinstance(zd, dict) or zd.get("Без замера"):
        return ""
    vd = zd.get("Данные выезда") if isinstance(zd.get("Данные выезда"), dict) else {}
    bits = []
    rq = vd.get("Расстояние до КАД") or vd.get("Расстояние маршрута м")
    if rq is not None:
        try:
            if float(rq) > 100:
                bits.append("маршрут ~%.2f км" % (float(rq) / 1000.0))
            else:
                bits.append("%s км до КАД" % rq)
        except (TypeError, ValueError):
            bits.append(str(rq))
    df = (zd.get("date_from") or vd.get("Интервал от") or "").strip()
    dt = (zd.get("date_to") or vd.get("Интервал до") or "").strip()
    # Снимок может хранить даты в блоке доп. текстом замера — показываем хотя бы слот JSON
    if df or dt:
        bits.append("слот %s … %s" % (df or "—", dt or "—"))
    ext = (zd.get("extra_text") or "").strip()
    if ext and ("заверш" in ext.lower() or "готов" in ext.lower()):
        return "Замер завершён — см. расчёт"
    return ("Замер: " + "; ".join(bits)) if bits else "Замер — см. расчёт"


class PulsingDot(QLabel):
    def __init__(self, color_hex, parent=None):
        super().__init__(parent)
        self.setFixedSize(6, 6)
        self._color_hex = color_hex or '#9CA3AF'
        self._bright = True
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._toggle)
        self._timer.start(450)
        self._update_style()

    def _toggle(self):
        self._bright = not self._bright
        self._update_style()

    def _update_style(self):
        c = self._color_hex
        if self._bright and len(c) == 7:
            r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
            c2 = '#%02x%02x%02x' % (min(255, r + 40), min(255, g + 40), min(255, b + 40))
        else:
            c2 = c
        self.setStyleSheet("background: %s; border-radius: 3px; border: none;" % c2)

    def stop(self):
        self._timer.stop()


STATUS_COLOR_KEYS = {
    'draft': 'status_draft', 'paid': 'status_paid', 'in_progress': 'status_in_progress',
    'made': 'status_made', 'checked_qr': 'status_checked_qr', 'shipped': 'status_shipped',
    'completed': 'status_shipped',
}


def _format_item_line(it):
    name = str(it.get('material_name') or '—').strip()
    h, w = int(it.get('height_mm') or 0), int(it.get('width_mm') or 0)
    qty = int(it.get('quantity') or 1)
    return "%s %d×%d ×%d — ₽" % (name[:20], w, h, qty)


class OrderTile(QFrame):
    clicked = pyqtSignal(object)
    client_clicked = pyqtSignal(object, object)

    def __init__(self, order_data, items=None, tile_index=0, parent=None):
        super().__init__(parent)
        self.order_data = order_data
        items = items or []
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(180)
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(4, 2, 4, 2)

        status = order_data.get('status') or 'draft'
        is_completed = status in (ORDER_STATUS_SHIPPED, 'completed')
        if is_completed:
            bg = color('order_tile_completed_bg')
        else:
            bg = color('order_tile_bg')
        border = color('order_tile_completed_border') if is_completed else color('order_tile_border')
        self.setStyleSheet("""
            OrderTile { background-color: %s; border: 1px solid %s; border-radius: 4px; }
            OrderTile:hover { background-color: %s; border: 1px solid %s; }
            QLabel { background-color: transparent; }
        """ % (bg, border, bg, border))

        fs = max(6, min(11, tile_font_size() - 1))
        f8 = QFont()
        f8.setPointSize(fs)
        f9 = QFont()
        f9.setPointSize(min(14, fs + 1))

        # Строка 1: клиент (ссылка) + дата
        top = QHBoxLayout()
        top.setSpacing(4)
        client_name = (str(order_data.get('client_name') or '').strip()) or '—'
        client_id = order_data.get('client_id')
        self._btn_client = QPushButton(client_name[:30] + ('…' if len(client_name) > 30 else ''))
        self._btn_client.setFlat(True)
        self._btn_client.setCursor(Qt.PointingHandCursor)
        self._btn_client.setFont(f9)
        self._btn_client.setStyleSheet(
            "QPushButton { color: #1a365d; border: none; padding: 0; background: transparent; } "
            "QPushButton:hover { color: #2563eb; background: transparent; }"
        )
        self._btn_client.clicked.connect(lambda: self.client_clicked.emit(client_id, client_name))
        self._btn_client.setToolTip("Заказы клиента")
        top.addWidget(self._btn_client)
        top.addStretch()
        created = order_data.get('created_at')
        date_str = created.strftime('%d.%m %H:%M') if hasattr(created, 'strftime') else str(created or '—')[:12]
        lbl_date = QLabel(date_str)
        lbl_date.setFont(f8)
        lbl_date.setStyleSheet("color: #555; background: transparent;")
        top.addWidget(lbl_date)
        layout.addLayout(top)

        line_h = max(10, fs + 4)

        glass_total_rub = None
        glass_n = 0
        kind_caption = None
        if parse_bundle:
            try:
                from logic.blocks_bundle import bundle_grand_total_rub, parse_bundle as _pb

                _v, _prods = _pb(order_data.get("blocks_calc_json"))
                glass_n = len(_prods)
                if glass_n:
                    glass_total_rub = bundle_grand_total_rub(_prods)
                    kinds = {
                        str(pr.get("kind") or "glass_mirror").strip() or "glass_mirror"
                        for pr in _prods
                    }
                    if len(kinds) == 1:
                        k = list(kinds)[0]
                        if k == "glass_mirror":
                            kind_caption = "Стекло / зеркало"
                        elif k == "facade":
                            kind_caption = "Фасады"
                        else:
                            kind_caption = k
                    else:
                        kind_caption = "Смешанный заказ"
            except Exception:
                glass_total_rub = None
                glass_n = 0
        if not kind_caption:
            ol = str(order_data.get("order_kind") or "")
            if ol == ORDER_KIND_GLASS_MIRROR:
                kind_caption = "Стекло / зеркало"
            elif ol == "facade":
                kind_caption = "Фасады"
            elif ol == "mixed":
                kind_caption = "Смешанный заказ"
        if kind_caption:
            kind_lbl = QLabel(kind_caption)
            kind_lbl.setFont(f8)
            kind_lbl.setStyleSheet("color: #1565c0; font-weight: bold;")
            kind_lbl.setMaximumHeight(line_h + 2)
            layout.addWidget(kind_lbl)
            if glass_n:
                cnt_lbl = QLabel("%s" % ("1 изделие" if glass_n == 1 else ("%d изделий" % glass_n)))
                cnt_lbl.setFont(f8)
                cnt_lbl.setStyleSheet("color: #1a365d; font-weight: bold;")
                cnt_lbl.setMaximumHeight(line_h + 2)
                layout.addWidget(cnt_lbl)

        zm, dv, iv = zamer_delivery_hints_from_order_row(order_data, fetch_portal=False)
        svc = ("%s · %s · %s" % (zm, dv, iv))[:100] + (
            "…" if len(zm) + len(dv) + len(iv) > 100 else ""
        )
        svc_lbl = QLabel(svc)
        svc_lbl.setFont(f8)
        svc_lbl.setStyleSheet("color: #37474f;")
        svc_lbl.setWordWrap(False)
        svc_lbl.setMaximumHeight(line_h + 4)
        svc_lbl.setToolTip(
            "%s\n%s\n%s\n(портал — кнопка «Обновить» на главной при настроенном API)"
            % (zm, dv, iv)
        )
        layout.addWidget(svc_lbl)

        # Позиции: компактные строки (макс. 3, остальное "и ещё N")
        for idx, it in enumerate(items[:3]):
            line = QLabel(_format_item_line(it))
            line.setFont(f8)
            line.setStyleSheet("color: #333; margin: 0; padding: 0;")
            line.setMaximumHeight(line_h)
            layout.addWidget(line)
        if len(items) > 3:
            extra = QLabel("… и ещё %d" % (len(items) - 3))
            extra.setFont(f8)
            extra.setStyleSheet("color: #666;")
            extra.setMaximumHeight(line_h)
            layout.addWidget(extra)
        if not items:
            no_pos = QLabel("— поз.")
            no_pos.setFont(f8)
            no_pos.setStyleSheet("color: #888;")
            no_pos.setMaximumHeight(max(10, line_h - 2))
            layout.addWidget(no_pos)

        # Итого + статус
        bot = QHBoxLayout()
        bot.setSpacing(4)
        if glass_total_rub is not None:
            total_lbl = QLabel("Итого: %s ₽" % glass_total_rub)
        else:
            total_lbl = QLabel("Итого: — ₽")
        total_lbl.setFont(f9)
        total_lbl.setStyleSheet("color: #1a365d; font-weight: bold;")
        bot.addWidget(total_lbl)
        bot.addStretch()
        self._dot = PulsingDot(color(STATUS_COLOR_KEYS.get(status, 'status_draft')), self)
        bot.addWidget(self._dot)
        lbl_status = QLabel(order_status_to_ru(status))
        lbl_status.setFont(f8)
        bot.addWidget(lbl_status)
        layout.addLayout(bot)

        max_h = max(120, min(240, tile_max_height() + 20))
        self.setMaximumHeight(max_h)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip("Клик — детали. Клик по клиенту — карточка клиента.")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.childAt(event.pos()) != self._btn_client:
            self.clicked.emit(self.order_data)
        super().mousePressEvent(event)
