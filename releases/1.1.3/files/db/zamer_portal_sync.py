# -*- coding: utf-8 -*-
"""Синхронизация плитки «Замер» из blocks_calc_json → таблица blocks_zamer (портал / веб)."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .connection import get_connection


def _service_type_code(measure: bool, install: bool, delivery: bool) -> str:
    if not measure and not install and not delivery:
        return "none"
    if delivery and not measure and not install:
        return "delivery"
    if measure and install and delivery:
        return "mid"
    if measure and delivery and not install:
        return "m_d"
    if install and delivery and not measure:
        return "i_d"
    if measure and install:
        return "both"
    if install:
        return "install"
    if delivery:
        return "delivery"
    return "measure"


def _extract_first_activated_zamer(bundle_text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not bundle_text or not str(bundle_text).strip():
        return None
    try:
        d = json.loads(str(bundle_text))
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    ver = int(d.get("schema_version") or 1)
    if ver >= 2 and isinstance(d.get("products"), list):
        for it in d["products"]:
            if not isinstance(it, dict):
                continue
            pl = it.get("payload")
            if not isinstance(pl, dict):
                continue
            z = pl.get("Замер")
            if isinstance(z, dict) and z.get("Активирован"):
                zd = z.get("Данные")
                return zd if isinstance(zd, dict) else None
        return None
    z = d.get("Замер")
    if isinstance(z, dict) and z.get("Активирован"):
        zd = z.get("Данные")
        return zd if isinstance(zd, dict) else None
    return None


def _strip_portal_zamer_id_from_bundle(bundle_text: str) -> str:
    """Убрать portal_zamer_id из первого активного «Замер» (устаревший id после удаления черновика заказа)."""
    if not bundle_text or not str(bundle_text).strip():
        return str(bundle_text or "")
    try:
        d = json.loads(str(bundle_text))
    except Exception:
        return str(bundle_text)
    if not isinstance(d, dict):
        return str(bundle_text)
    ver = int(d.get("schema_version") or 1)
    if ver >= 2 and isinstance(d.get("products"), list):
        for it in d["products"]:
            if not isinstance(it, dict):
                continue
            pl = it.get("payload")
            if not isinstance(pl, dict):
                continue
            z = pl.get("Замер")
            if isinstance(z, dict) and z.get("Активирован"):
                dat = z.get("Данные")
                if isinstance(dat, dict) and "portal_zamer_id" in dat:
                    del dat["portal_zamer_id"]
                break
    else:
        z = d.get("Замер")
        if isinstance(z, dict) and z.get("Активирован"):
            dat = z.get("Данные")
            if isinstance(dat, dict) and "portal_zamer_id" in dat:
                del dat["portal_zamer_id"]
    return json.dumps(d, ensure_ascii=False, indent=2)


def _merge_portal_zamer_id_into_bundle(bundle_text: str, new_id: int) -> str:
    d = json.loads(str(bundle_text))
    if not isinstance(d, dict):
        return bundle_text
    nid = int(new_id)
    ver = int(d.get("schema_version") or 1)
    if ver >= 2 and isinstance(d.get("products"), list):
        for it in d["products"]:
            if not isinstance(it, dict):
                continue
            pl = it.get("payload")
            if not isinstance(pl, dict):
                continue
            z = pl.get("Замер")
            if isinstance(z, dict) and z.get("Активирован"):
                dat = z.get("Данные")
                if not isinstance(dat, dict):
                    dat = {}
                    z["Данные"] = dat
                dat["portal_zamer_id"] = nid
                break
    else:
        z = d.get("Замер")
        if isinstance(z, dict) and z.get("Активирован"):
            dat = z.get("Данные")
            if not isinstance(dat, dict):
                dat = {}
                z["Данные"] = dat
            dat["portal_zamer_id"] = nid
    return json.dumps(d, ensure_ascii=False, indent=2)


def _ensure_mirror_order_id_column(cur) -> None:
    cur.execute(
        """
        ALTER TABLE blocks_zamer
        ADD COLUMN IF NOT EXISTS mirror_order_id INTEGER NULL
        REFERENCES mirror_orders(id) ON DELETE SET NULL
        """
    )


def _client_address_fallback(cur, client_id: Optional[int]) -> str:
    if client_id is None:
        return ""
    try:
        cid = int(client_id)
    except (TypeError, ValueError):
        return ""
    cur.execute(
        """
        SELECT NULLIF(TRIM(COALESCE(actual_address, '')), '') AS aa,
               NULLIF(TRIM(COALESCE(legal_address, '')), '') AS la
        FROM mirror_clients WHERE id = %s
        """,
        (cid,),
    )
    r = cur.fetchone()
    if not r:
        return ""
    if isinstance(r, dict):
        aa = (r.get("aa") or "").strip()
        la = (r.get("la") or "").strip()
        return (aa or la).strip()
    aa = (r[0] or "").strip() if r[0] else ""
    la = (r[1] or "").strip() if len(r) > 1 and r[1] else ""
    return (aa or la).strip()


def _sync_blocks_zamer_no_active_tile(order_id: int, blocks_zamer_status: Optional[str]) -> None:
    """В расчёте нет активной плитки «Замер» — обнуляем флаги услуг в blocks_zamer по mirror_order_id."""
    oid = int(order_id)
    st_tok = (
        str(blocks_zamer_status).strip()[:32]
        if blocks_zamer_status is not None and str(blocks_zamer_status).strip()
        else None
    )
    stc = _service_type_code(False, False, False)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                _ensure_mirror_order_id_column(cur)
                if st_tok:
                    cur.execute(
                        """
                        UPDATE blocks_zamer SET
                            is_measure = FALSE,
                            is_install = FALSE,
                            is_delivery = FALSE,
                            service_type = %s,
                            status = %s,
                            updated_at = NOW()
                        WHERE mirror_order_id = %s
                        """,
                        (str(stc)[:32], st_tok, oid),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE blocks_zamer SET
                            is_measure = FALSE,
                            is_install = FALSE,
                            is_delivery = FALSE,
                            service_type = %s,
                            updated_at = NOW()
                        WHERE mirror_order_id = %s
                        """,
                        (str(stc)[:32], oid),
                    )
    except Exception:
        return


def sync_blocks_zamer_for_order(
    order_id: int,
    bundle_text: Optional[str],
    *,
    blocks_zamer_status: Optional[str] = None,
) -> None:
    """
    Если в расчёте активирована плитка «Замер» и есть адрес — upsert в blocks_zamer,
    проставить mirror_order_id. При первом создании записать portal_zamer_id обратно в JSON заказа.

    blocks_zamer_status: если задано (например new), при UPDATE существующей строки
    также обновить колонку status — нужно после снятия услуги с портала.
    """
    oid = int(order_id)
    bundle_work = str(bundle_text or "").strip()
    if not bundle_work:
        return
    zd = _extract_first_activated_zamer(bundle_work)
    if not zd:
        _sync_blocks_zamer_no_active_tile(oid, blocks_zamer_status)
        return
    addr = (zd.get("Адрес") or "").strip()
    m = bool(zd.get("Замер"))
    i = bool(zd.get("Монтаж"))
    deliv = bool(zd.get("Доставка"))
    if not m and not i and not deliv:
        return

    df = (zd.get("date_from") or "").strip()[:10] or None
    dt = (zd.get("date_to") or "").strip()[:10] or None
    phone = (zd.get("phone") or "").strip()
    extra = (zd.get("extra_text") or "").strip()
    matches = bool(zd.get("matches_client"))
    st_code = _service_type_code(m, i, deliv)
    portal_id = zd.get("portal_zamer_id")
    zid_existing: Optional[int] = None
    if portal_id is not None:
        try:
            zid_existing = int(portal_id)
        except (TypeError, ValueError):
            zid_existing = None

    with get_connection() as conn:
        with conn.cursor() as cur:
            _ensure_mirror_order_id_column(cur)
            cur.execute(
                "SELECT client_id FROM mirror_orders WHERE id = %s",
                (oid,),
            )
            orow = cur.fetchone() or {}
            cid = orow.get("client_id")
            if not addr:
                addr = _client_address_fallback(cur, cid)
            if not addr:
                return

            if zid_existing:
                cur.execute("SELECT 1 FROM blocks_zamer WHERE id = %s", (zid_existing,))
                if not cur.fetchone():
                    # Быстрый просчёт: в JSON остался portal_zamer_id от удалённого черновика заказа.
                    bundle_work = _strip_portal_zamer_id_from_bundle(bundle_work)
                    cur.execute(
                        "UPDATE mirror_orders SET blocks_calc_json = %s WHERE id = %s",
                        (bundle_work, oid),
                    )
                    zid_existing = None

            if zid_existing:
                st_new = (
                    str(blocks_zamer_status).strip()[:32]
                    if blocks_zamer_status is not None and str(blocks_zamer_status).strip()
                    else None
                )
                if st_new:
                    cur.execute(
                        """
                        UPDATE blocks_zamer SET
                            client_id = %s,
                            address = %s,
                            date_from = %s,
                            date_to = %s,
                            phone = %s,
                            matches_client = %s,
                            extra_text = %s,
                            is_measure = %s,
                            is_install = %s,
                            is_delivery = %s,
                            service_type = %s,
                            mirror_order_id = %s,
                            status = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (
                            cid,
                            addr,
                            df,
                            dt,
                            phone,
                            matches,
                            extra,
                            m,
                            i,
                            deliv,
                            str(st_code)[:32],
                            oid,
                            st_new,
                            zid_existing,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE blocks_zamer SET
                            client_id = %s,
                            address = %s,
                            date_from = %s,
                            date_to = %s,
                            phone = %s,
                            matches_client = %s,
                            extra_text = %s,
                            is_measure = %s,
                            is_install = %s,
                            is_delivery = %s,
                            service_type = %s,
                            mirror_order_id = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (
                            cid,
                            addr,
                            df,
                            dt,
                            phone,
                            matches,
                            extra,
                            m,
                            i,
                            deliv,
                            str(st_code)[:32],
                            oid,
                            zid_existing,
                        ),
                    )
                return

            cur.execute(
                """
                INSERT INTO blocks_zamer (
                    client_id, address, date_from, date_to, phone,
                    matches_client, extra_text, is_measure, is_install, is_delivery,
                    service_type, status, mirror_order_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    cid,
                    addr,
                    df,
                    dt,
                    phone,
                    matches,
                    extra,
                    m,
                    i,
                    deliv,
                    str(st_code)[:32],
                    "new",
                    oid,
                ),
            )
            row = cur.fetchone() or {}
            new_id = row.get("id")
            if new_id is None:
                return
            merged = _merge_portal_zamer_id_into_bundle(bundle_work, int(new_id))
            cur.execute(
                "UPDATE mirror_orders SET blocks_calc_json = %s WHERE id = %s",
                (merged, oid),
            )


def _first_activated_zamer_block_dict(bundle_dict: dict) -> Optional[Dict[str, Any]]:
    """Первый активированный блок «Замер» в bundle (ссылка на dict внутри JSON-дерева)."""
    if not isinstance(bundle_dict, dict):
        return None
    ver = int(bundle_dict.get("schema_version") or 1)
    if ver >= 2 and isinstance(bundle_dict.get("products"), list):
        for it in bundle_dict["products"]:
            if not isinstance(it, dict):
                continue
            pl = it.get("payload")
            if not isinstance(pl, dict):
                continue
            z = pl.get("Замер")
            if isinstance(z, dict) and z.get("Активирован"):
                return z
        return None
    z = bundle_dict.get("Замер")
    if isinstance(z, dict) and z.get("Активирован"):
        return z
    return None


def set_first_activated_zamer_service_flags(
    bundle_text: Optional[str],
    *,
    measure: bool,
    install: bool,
    delivery: bool,
) -> str:
    """
    Обновить флаги Замер/Монтаж/Доставка у первого активного блока «Замер».
    Если все три выключены — блок деактивируется (Данные сбрасываются).
    """
    if not bundle_text or not str(bundle_text).strip():
        return str(bundle_text or "")
    try:
        d = json.loads(str(bundle_text))
    except Exception:
        return str(bundle_text)
    if not isinstance(d, dict):
        return str(bundle_text)
    z = _first_activated_zamer_block_dict(d)
    if z is None:
        return json.dumps(d, ensure_ascii=False, indent=2)
    m, i, dv = bool(measure), bool(install), bool(delivery)
    if not (m or i or dv):
        z["Активирован"] = False
        z["Данные"] = None
        return json.dumps(d, ensure_ascii=False, indent=2)
    dat = z.get("Данные")
    if not isinstance(dat, dict):
        dat = {}
        z["Данные"] = dat
    dat["Замер"] = m
    dat["Монтаж"] = i
    dat["Доставка"] = dv
    dat["service_type"] = _service_type_code(m, i, dv)
    return json.dumps(d, ensure_ascii=False, indent=2)
