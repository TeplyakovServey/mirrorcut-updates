import copy
import html as html_module
import json
import os
import threading
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Set, Tuple

from .connection import get_connection

_inventory_catalog_cache_lock = threading.Lock()
_inventory_catalog_cache = {"expires_at": 0.0, "entries": None}


def _inventory_catalog_cache_ttl_seconds() -> float:
    raw = (os.environ.get("MC_INVENTORY_CATALOG_CACHE_SEC") or "3").strip()
    try:
        ttl = float(raw)
    except (TypeError, ValueError):
        ttl = 3.0
    return max(0.0, min(30.0, ttl))


def invalidate_inventory_catalog_cache() -> None:
    with _inventory_catalog_cache_lock:
        _inventory_catalog_cache["expires_at"] = 0.0
        _inventory_catalog_cache["entries"] = None


# --- Profile stock (facade profiles/hinges) ---
def _ensure_profile_stock_tables():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_stock (
                    id SERIAL PRIMARY KEY,
                    item_type VARCHAR(16) NOT NULL DEFAULT 'profile',
                    ref_id INTEGER,
                    series VARCHAR(255) DEFAULT '',
                    name VARCHAR(255) NOT NULL DEFAULT '',
                    color VARCHAR(255) DEFAULT '',
                    length_mm INTEGER DEFAULT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    is_remnant BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_deleted_profile_stock (
                    id SERIAL PRIMARY KEY,
                    stock_id INTEGER,
                    item_type VARCHAR(16) NOT NULL DEFAULT 'profile',
                    ref_id INTEGER,
                    series VARCHAR(255) DEFAULT '',
                    name VARCHAR(255) NOT NULL DEFAULT '',
                    color VARCHAR(255) DEFAULT '',
                    length_mm INTEGER DEFAULT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    is_remnant BOOLEAN NOT NULL DEFAULT FALSE,
                    deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )


def insert_profile_stock(item_type, ref_id, series, name, color, length_mm=None, quantity=1, is_remnant=False):
    _ensure_profile_stock_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_profile_stock
                   (item_type, ref_id, series, name, color, length_mm, quantity, is_remnant)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    str(item_type or 'profile'),
                    ref_id,
                    str(series or '').strip(),
                    str(name or '').strip(),
                    str(color or '').strip(),
                    int(length_mm) if length_mm is not None else None,
                    max(1, int(quantity or 1)),
                    bool(is_remnant),
                )
            )
            row = cur.fetchone()
            return row['id'] if row else None


def get_profile_stock(is_remnant=None):
    _ensure_profile_stock_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            q = """SELECT id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant, created_at
                   FROM mirror_profile_stock WHERE 1=1"""
            params = []
            if is_remnant is not None:
                q += " AND is_remnant = %s"
                params.append(bool(is_remnant))
            q += " ORDER BY is_remnant, name, color, length_mm"
            cur.execute(q, params or None)
            return cur.fetchall()


def list_profile_stock_for_batch_planner(profile_ref_id=None, profile_color=None, item_type="profile"):
    """
    Склад профилей для автоплана партии: те же фильтры, что в WEB_SERVICE (ref_id, цвет),
    без загрузки всего mirror_profile_stock.
    """
    _ensure_profile_stock_tables()
    it = str(item_type or "profile").strip() or "profile"
    q = (
        """SELECT id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant, created_at
           FROM mirror_profile_stock
           WHERE item_type = %s"""
    )
    params: List[Any] = [it]
    if profile_ref_id is not None:
        try:
            rid = int(profile_ref_id)
        except (TypeError, ValueError):
            rid = None
        if rid is not None:
            q += " AND ref_id = %s"
            params.append(rid)
    color_f = str(profile_color or "").strip()
    if color_f:
        q += " AND lower(coalesce(trim(color), '')) = lower(trim(%s))"
        params.append(color_f)
    q += " ORDER BY is_remnant, name, color, length_mm"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(q, params)
            return cur.fetchall() or []


def list_profile_stock_matching_inventory_type_keys(type_keys):
    """
    Строки mirror_profile_stock, у которых profile_stock_inventory_type_key(row) входит в type_keys.
    Узкий SELECT вместо полного get_profile_stock() + фильтр в Python (кампании инвентаризации).
    """
    keys = [str(x).strip() for x in (type_keys or []) if str(x).strip()]
    if not keys:
        return []
    _ensure_profile_stock_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant, created_at
                FROM mirror_profile_stock
                WHERE (
                    NULLIF(TRIM(series), '') IS NOT NULL
                    AND TRIM(series) = ANY(%s)
                ) OR (
                    (NULLIF(TRIM(series), '') IS NULL OR TRIM(series) = '')
                    AND TRIM(COALESCE(name, '')) = ANY(%s)
                )
                ORDER BY is_remnant, name, color, length_mm
                """,
                (keys, keys),
            )
            return cur.fetchall() or []


def get_profile_stock_row(stock_id):
    _ensure_profile_stock_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant, created_at
                   FROM mirror_profile_stock WHERE id = %s""",
                (int(stock_id),),
            )
            return cur.fetchone()


def get_profile_stock_rows_by_ids(stock_ids):
    """Пакетная загрузка строк mirror_profile_stock по id (для цепочек и списков)."""
    ids = []
    seen = set()
    for sid in list(stock_ids or []):
        try:
            si = int(sid)
        except (TypeError, ValueError):
            continue
        if si < 1 or si in seen:
            continue
        seen.add(si)
        ids.append(si)
    if not ids:
        return {}
    _ensure_profile_stock_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant, created_at
                   FROM mirror_profile_stock WHERE id = ANY(%s)""",
                (ids,),
            )
            out: Dict[int, Any] = {}
            for r in cur.fetchall() or []:
                try:
                    out[int(r["id"])] = r
                except (TypeError, ValueError, KeyError):
                    continue
            return out


def delete_profile_stock_and_archive(stock_id):
    _ensure_profile_stock_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant
                   FROM mirror_profile_stock WHERE id = %s""",
                (int(stock_id),)
            )
            row = cur.fetchone()
            if not row:
                return False
            cur.execute(
                """INSERT INTO mirror_deleted_profile_stock
                   (stock_id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    row.get('id'),
                    row.get('item_type'),
                    row.get('ref_id'),
                    row.get('series'),
                    row.get('name'),
                    row.get('color'),
                    row.get('length_mm'),
                    row.get('quantity'),
                    row.get('is_remnant'),
                )
            )
            cur.execute("DELETE FROM mirror_profile_stock WHERE id = %s", (int(stock_id),))
            return True


def get_deleted_profile_stock(limit=500):
    _ensure_profile_stock_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, stock_id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant, deleted_at
                   FROM mirror_deleted_profile_stock ORDER BY deleted_at DESC LIMIT %s""",
                (int(limit),)
            )
            return cur.fetchall()


def get_deleted_profile_stock_by_stock_id(stock_id):
    _ensure_profile_stock_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, stock_id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant, deleted_at
                   FROM mirror_deleted_profile_stock WHERE stock_id = %s ORDER BY deleted_at DESC LIMIT 1""",
                (int(stock_id),),
            )
            return cur.fetchone()


def _ensure_profile_label_tables():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_label (
                    id SERIAL PRIMARY KEY,
                    stock_id INTEGER NOT NULL,
                    label_number INTEGER UNIQUE,
                    unique_number VARCHAR(64) UNIQUE,
                    qr_url TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_remnant_history (
                    id SERIAL PRIMARY KEY,
                    stock_id INTEGER,
                    order_id INTEGER,
                    action_type VARCHAR(32) NOT NULL DEFAULT 'used',
                    details_json TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )


def _profile_stock_row_on_cursor(cur, stock_id):
    cur.execute(
        """SELECT id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant, created_at
           FROM mirror_profile_stock WHERE id = %s""",
        (int(stock_id),),
    )
    return cur.fetchone() or {}


def _ensure_profile_label_number_with_cursor(cur, stock_id, *, emit_cut_event=True):
    """Логика ensure_profile_label_number в рамках одного курсора (без лишних соединений)."""
    sid = int(stock_id)
    st = _profile_stock_row_on_cursor(cur, sid)
    is_rem = bool(st.get("is_remnant"))
    if not is_rem:
        return None
    cur.execute("SELECT label_number, unique_number, qr_url FROM mirror_profile_label WHERE stock_id = %s", (sid,))
    row = cur.fetchone()
    if row and row.get("label_number") is not None:
        num = int(row.get("label_number"))
        uniq = str(row.get("unique_number") or "").strip() or ("S%s" % num)
        if uniq.upper().startswith("P") and len(uniq) > 1 and uniq[1:].isdigit():
            uniq = "S%s" % uniq[1:]
        elif not uniq:
            uniq = "S%s" % num
        old_uniq = str(row.get("unique_number") or "").strip()
        qr = str(row.get("qr_url") or "").strip()
        try:
            from logic.qr_utils import profile_qr_url

            qr_new = profile_qr_url(uniq)
        except Exception:
            qr_new = qr or ""
        qr_bad = False
        try:
            qrl = str(qr or "").strip().lower()
            if "/remnant/profile/" in qrl:
                qr_bad = True
            if str(qr_new or "").strip() and str(qr or "").strip() != str(qr_new or "").strip():
                qr_bad = True
        except Exception:
            qr_bad = False
        if uniq != old_uniq or not qr or qr_bad:
            cur.execute(
                "UPDATE mirror_profile_label SET unique_number = %s, qr_url = %s WHERE stock_id = %s",
                (uniq, qr_new or qr, sid),
            )
        return num
    _ensure_label_counter_above_existing(cur)
    cur.execute("UPDATE mirror_label_counter SET value = value + 1 RETURNING value")
    nrow = cur.fetchone()
    num = int((nrow or {}).get("value") or 1)
    uniq = "S%s" % num
    try:
        from logic.qr_utils import profile_qr_url

        qr = profile_qr_url(uniq)
    except Exception:
        try:
            from logic.qr_utils import profile_qr_url as _pqu

            qr = _pqu(uniq)
        except Exception:
            qr = ""
    cur.execute(
        "INSERT INTO mirror_profile_label (stock_id, label_number, unique_number, qr_url) VALUES (%s, %s, %s, %s)",
        (sid, num, uniq, qr),
    )
    det_lbl = {"label_number": num, "unique_number": uniq, "qr_url": qr}
    try:
        if st:
            det_lbl["profile_name"] = (st.get("name") or st.get("series") or "").strip()
            det_lbl["profile_color"] = (st.get("color") or "").strip()
            if st.get("length_mm") is not None:
                det_lbl["length_mm"] = st.get("length_mm")
            det_lbl["is_remnant"] = bool(st.get("is_remnant"))
    except Exception:
        pass
    add_profile_remnant_history(
        sid,
        None,
        "label_created",
        det_lbl,
        db_cursor=cur,
        emit_cut_event=emit_cut_event,
    )
    return num


def ensure_profile_label_number(stock_id):
    """Гарантировать номер этикетки для остатка профиля на складе (только S-префикс)."""
    _ensure_profile_label_tables()
    _ensure_profile_stock_tables()
    sid = int(stock_id)
    with get_connection() as conn:
        with conn.cursor() as cur:
            return _ensure_profile_label_number_with_cursor(cur, sid, emit_cut_event=True)


def ensure_profile_label_numbers_bulk(stock_ids):
    ids = []
    seen = set()
    for sid in list(stock_ids or []):
        try:
            si = int(sid)
        except (TypeError, ValueError):
            continue
        if si <= 0 or si in seen:
            continue
        seen.add(si)
        ids.append(si)
    if not ids:
        return {}
    _ensure_profile_label_tables()
    _ensure_profile_stock_tables()
    out = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            for si in ids:
                try:
                    cur.execute("SAVEPOINT sp_profile_lbl_b")
                    out[si] = _ensure_profile_label_number_with_cursor(cur, si, emit_cut_event=False)
                    cur.execute("RELEASE SAVEPOINT sp_profile_lbl_b")
                except Exception:
                    try:
                        cur.execute("ROLLBACK TO SAVEPOINT sp_profile_lbl_b")
                    except Exception:
                        pass
                    out[si] = None
    return out


def get_profile_label_by_stock_id(stock_id):
    _ensure_profile_label_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, stock_id, label_number, unique_number, qr_url, created_at FROM mirror_profile_label WHERE stock_id = %s",
                (int(stock_id),),
            )
            return cur.fetchone()


def get_profile_labels_by_stock_ids(stock_ids):
    _ensure_profile_label_tables()
    ids = []
    seen = set()
    for sid in list(stock_ids or []):
        try:
            si = int(sid)
        except (TypeError, ValueError):
            continue
        if si <= 0 or si in seen:
            continue
        seen.add(si)
        ids.append(si)
    if not ids:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, stock_id, label_number, unique_number, qr_url, created_at
                FROM mirror_profile_label
                WHERE stock_id = ANY(%s)
                """,
                (ids,),
            )
            out = {}
            for row in (cur.fetchall() or []):
                sid = row.get("stock_id")
                if sid is None:
                    continue
                try:
                    out[int(sid)] = row
                except (TypeError, ValueError):
                    continue
            return out


def get_profile_label_by_unique_number(unique_number):
    _ensure_profile_label_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, stock_id, label_number, unique_number, qr_url, created_at FROM mirror_profile_label WHERE unique_number = %s",
                (str(unique_number or "").strip(),),
            )
            return cur.fetchone()


def get_profile_label_by_label_number(label_number):
    _ensure_profile_label_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, stock_id, label_number, unique_number, qr_url, created_at FROM mirror_profile_label WHERE label_number = %s",
                (int(label_number),),
            )
            return cur.fetchone()


def resolve_profile_label_row_by_scan_code(q):
    """Сопоставить строку из QR/поиска с записью mirror_profile_label (остатки профиля с префиксом S)."""
    q = str(q or "").strip()
    if not q:
        return None
    qu = q.upper().replace(" ", "")
    row = get_profile_label_by_unique_number(q)
    if row:
        return row
    if len(q) >= 2 and q[0] in "Ss" and q[1:].isdigit():
        n = int(q[1:])
        row = get_profile_label_by_unique_number("S%d" % n)
        if row:
            return row
        row = get_profile_label_by_label_number(n)
        if row:
            return row
    # Старые этикетки с кодом P+N (до унификации на S); в БД уже может быть S+n.
    if len(q) >= 2 and q[0] in "Pp" and q[1:].isdigit():
        n = int(q[1:])
        row = get_profile_label_by_unique_number("P%d" % n)
        if row:
            return row
        row = get_profile_label_by_unique_number("S%d" % n)
        if row:
            return row
        row = get_profile_label_by_label_number(n)
        if row:
            return row
    if q.isdigit():
        row = get_profile_label_by_label_number(int(q))
        if row:
            return row
    return None


def add_profile_remnant_history(
    stock_id,
    order_id,
    action_type,
    details_json=None,
    *,
    db_cursor=None,
    emit_cut_event=True,
):
    _ensure_profile_label_tables()
    payload = json.dumps(details_json or {}, ensure_ascii=False)
    vals = (
        int(stock_id) if stock_id is not None else None,
        int(order_id) if order_id else None,
        str(action_type or "used"),
        payload,
    )
    sql = (
        "INSERT INTO mirror_profile_remnant_history (stock_id, order_id, action_type, details_json) "
        "VALUES (%s, %s, %s, %s)"
    )
    if db_cursor is not None:
        db_cursor.execute(sql, vals)
    else:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, vals)
    if emit_cut_event:
        try:
            det = details_json if isinstance(details_json, dict) else {}
            add_profile_cut_event(
                stock_id=(int(stock_id) if stock_id is not None else None),
                order_id=(int(order_id) if order_id is not None else None),
                batch_id=(int(det.get("batch_id")) if det.get("batch_id") is not None else None),
                event_type="history:%s" % str(action_type or "used")[:32],
                reason_text=str(det.get("reason_text") or det.get("reason") or ""),
                actor_user_id=(int(det.get("actor_user_id")) if det.get("actor_user_id") is not None else None),
                actor_login=str(det.get("actor_login") or det.get("confirmed_by_login") or det.get("confirmed_by") or ""),
                actor_role=str(det.get("actor_role") or det.get("confirmed_by_role") or ""),
                payload_json=det,
            )
        except Exception:
            pass


def profile_login_norm(login: str) -> str:
    return str(login or "").strip().lower()


def display_names_for_logins(logins: Iterable[str]) -> Dict[str, str]:
    """Фамилия и имя из main_users по логину (ключ — нормализованный логин). При ошибке — пустой словарь."""
    keys = sorted({profile_login_norm(x) for x in (logins or []) if str(x or "").strip()})
    if not keys:
        return {}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT LOWER(TRIM(login)) AS lk,
                           TRIM(CONCAT_WS(' ', NULLIF(TRIM(surname), ''), NULLIF(TRIM(name), ''))) AS disp
                    FROM main_users
                    WHERE LOWER(TRIM(login)) = ANY(%s)
                    """,
                    (keys,),
                )
                rows = cur.fetchall() or []
        out: Dict[str, str] = {}
        for r in rows:
            lk = str((r or {}).get("lk") or "").strip().lower()
            disp = str((r or {}).get("disp") or "").strip()
            if lk:
                out[lk] = disp or lk
        return out
    except Exception:
        return {}


def collect_actor_logins_from_remnant_history_rows(rows) -> Set[str]:
    out: Set[str] = set()
    for r in rows or []:
        lg = str((r or {}).get("hist_actor_login") or "").strip()
        if lg:
            out.add(lg)
        det = _parse_profile_history_details_json((r or {}).get("details_json"))
        for k in (
            "confirmed_by_login",
            "confirmed_by",
            "batch_created_by_login",
            "actor_login",
            "created_by_login",
        ):
            v = str(det.get(k) or "").strip()
            if v:
                out.add(v)
    return out


def enrich_profile_remnant_history_rows(rows, extra_logins: Iterable[str] = None):
    """Добавляет hist_actor_display и _actor_name_map (фамилия имя из main_users)."""
    rows = [dict(r) for r in (rows or [])]
    logins = collect_actor_logins_from_remnant_history_rows(rows)
    for x in extra_logins or []:
        if str(x or "").strip():
            logins.add(str(x).strip())
    m = display_names_for_logins(logins)
    for r in rows:
        hl = str(r.get("hist_actor_login") or "").strip()
        disp = (m.get(profile_login_norm(hl)) or "").strip()
        r["hist_actor_display"] = disp or hl
        r["_actor_name_map"] = m
    return rows


def actor_display_from_map(name_map: Dict[str, str], login: str) -> str:
    lg = str(login or "").strip()
    if not lg:
        return ""
    m = name_map or {}
    return (m.get(profile_login_norm(lg)) or "").strip() or lg


def find_remnant_stock_id_after_consuming_source(source_stock_id, profile_ref_id, remnant_mm):
    """
    Позиция склада-остатка, появившаяся после реза с исходного source_stock_id (по журналу remnant_from_profile_cut).
    """
    if source_stock_id is None or remnant_mm is None:
        return None
    try:
        sid = int(source_stock_id)
        rem = int(remnant_mm)
    except (TypeError, ValueError):
        return None
    if sid < 1 or rem < 200:
        return None
    try:
        rid = int(profile_ref_id) if profile_ref_id is not None else None
    except (TypeError, ValueError):
        rid = None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT h.stock_id, h.details_json
                FROM mirror_profile_remnant_history h
                INNER JOIN mirror_profile_stock s ON s.id = h.stock_id
                WHERE h.action_type = 'remnant_from_profile_cut'
                  AND s.is_remnant = TRUE
                  AND s.length_mm = %s
                  AND (%s IS NULL OR s.ref_id = %s)
                ORDER BY h.id DESC
                LIMIT 80
                """,
                (rem, rid, rid),
            )
            found = cur.fetchall() or []
    for r in found:
        det = _parse_profile_history_details_json(r.get("details_json"))
        try:
            if int(det.get("source_stock_id") or 0) == sid:
                return int(r.get("stock_id"))
        except (TypeError, ValueError):
            continue
    return None


def profile_stock_consumed_was_remnant(consumed_stock_id) -> bool:
    """True если списанная позиция была остатком; False — целый брус; при отсутствии данных — True (не показываем «использован целиком»)."""
    if consumed_stock_id is None:
        return True
    try:
        sid = int(consumed_stock_id)
    except (TypeError, ValueError):
        return True
    if sid < 1:
        return True
    row = get_profile_stock_row(sid)
    if row:
        return bool(row.get("is_remnant"))
    try:
        del_row = get_deleted_profile_stock_by_stock_id(sid)
    except Exception:
        del_row = None
    if del_row:
        return bool(del_row.get("is_remnant"))
    return True


def profile_facade_usage_reason_ru(u) -> str:
    """Текст для таймлайна / карточки: списание под фасад по строке mirror_profile_stock_usage."""
    u = u or {}
    cid = u.get("consumed_stock_id")
    ref = u.get("profile_ref_id")
    rem = u.get("remnant_mm")
    fw = u.get("facade_width_mm")
    fh = u.get("facade_height_mm")
    req = u.get("required_mm")
    was_remnant = profile_stock_consumed_was_remnant(cid)
    child_id = find_remnant_stock_id_after_consuming_source(cid, ref, rem)
    child_label = ""
    if child_id:
        try:
            lab = get_profile_label_by_stock_id(int(child_id))
        except Exception:
            lab = None
        if lab:
            child_label = str(lab.get("unique_number") or "").strip() or (
                "S%s" % lab.get("label_number") if lab.get("label_number") is not None else ""
            )
        if not child_label:
            child_label = "S%s" % child_id if child_id else ""
    parts = []
    parts.append(
        "Фасад %s×%s мм · рез %s мм"
        % (
            fw if fw not in (None, "") else "—",
            fh if fh not in (None, "") else "—",
            req if req not in (None, "") else "—",
        )
    )
    if not was_remnant:
        parts.append("Остаток: использован")
        if child_label:
            parts.append("Текущий остаток профиля %s" % child_label)
        if rem not in (None, ""):
            parts.append("длина %s мм" % rem)
    else:
        parts.append("Остаток после списания: %s мм" % (rem if rem not in (None, "") else "—"))
    return " · ".join(parts)


_PROFILE_HISTORY_TAIL_RU = {
    "label_created": "Создание этикетки склада",
    "remnant_from_profile_cut": "Остаток после реза на производстве",
    "remnant_from_reserve": "Остаток после резерва",
    "profile_bar_consumed": "Списание бруса",
    "reserve_source_consumed": "Списание при резерве",
    "used": "Операция склада",
}


def profile_timeline_event_type_ru(event_type: str) -> str:
    et = str(event_type or "").strip()
    if et == "cut_segment":
        return "Резка (партия на производстве)"
    if et == "facade_usage":
        return "Списание под расчёт фасада"
    if et.startswith("history:"):
        tail = et[8:].strip()
        return _PROFILE_HISTORY_TAIL_RU.get(tail, "Журнал: %s" % tail)
    return _PROFILE_HISTORY_TAIL_RU.get(et, et or "—")


def profile_plan_output_type_ru(kind: str) -> str:
    k = str(kind or "").strip().lower()
    return {
        "piece": "заготовка",
        "remnant": "остаток",
        "waste": "отход",
        "assembly": "сборка",
        "cut": "рез",
        "stock": "склад",
    }.get(k, k or "—")


def _get_profile_remnant_history_rows_raw(stock_id):
    _ensure_profile_label_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT h.id, h.stock_id, h.order_id, h.action_type, h.details_json, h.created_at,
                          COALESCE(c.name, o.client_name, '') AS client_name, o.status,
                          o.client_id AS order_client_id,
                          (SELECT e.actor_login FROM mirror_profile_cut_events e
                            WHERE e.stock_id = h.stock_id
                              AND e.event_type = ('history:' || h.action_type)
                              AND e.created_at BETWEEN h.created_at - interval '30 seconds'
                                                   AND h.created_at + interval '30 seconds'
                            ORDER BY abs(extract(epoch from (e.created_at - h.created_at))), e.id DESC
                            LIMIT 1) AS hist_actor_login
                   FROM mirror_profile_remnant_history h
                   LEFT JOIN mirror_orders o ON o.id = h.order_id
                   LEFT JOIN mirror_clients c ON c.id = o.client_id
                   WHERE h.stock_id = %s
                   ORDER BY h.created_at DESC""",
                (int(stock_id),),
            )
            return cur.fetchall() or []


def _get_profile_remnant_history_rows_ancestor_walk(stock_id):
    """
    Только поля журнала для обхода цепочки parent→child (без JOIN заказов и без
    подзапроса к mirror_profile_cut_events на каждую строку).
    """
    _ensure_profile_label_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT h.id, h.stock_id, h.order_id, h.action_type, h.details_json, h.created_at
                   FROM mirror_profile_remnant_history h
                   WHERE h.stock_id = %s
                   ORDER BY h.created_at DESC""",
                (int(stock_id),),
            )
            return cur.fetchall() or []


def get_profile_remnant_history(stock_id):
    return enrich_profile_remnant_history_rows([dict(r) for r in _get_profile_remnant_history_rows_raw(int(stock_id))])


def get_profile_history_rich(stock_id):
    """Расширенная история профиля: события + привязка к списаниям (клиент/размер/остаток)."""
    sid = int(stock_id)
    raw_hist = _get_profile_remnant_history_rows_raw(sid) or []
    cut_events_raw = list_profile_cut_events_by_stock(sid, limit=300) or []
    logins = collect_actor_logins_from_remnant_history_rows(raw_hist)
    for ev in cut_events_raw:
        a = str((ev or {}).get("actor_login") or "").strip()
        if a:
            logins.add(a)
    m = display_names_for_logins(logins)
    hist = []
    for r in raw_hist:
        h = dict(r)
        hl = str(h.get("hist_actor_login") or "").strip()
        h["hist_actor_display"] = (m.get(profile_login_norm(hl)) or "").strip() or hl
        h["_actor_name_map"] = m
        hist.append(h)
    cut_events = []
    for ev in cut_events_raw:
        e = dict(ev)
        e["_actor_name_map"] = m
        al = str(e.get("actor_login") or "").strip()
        e["actor_display"] = (m.get(profile_login_norm(al)) or "").strip() or al
        cut_events.append(e)
    usage = []
    _ensure_profile_stock_usage_table()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, consumed_stock_id, profile_ref_id, series, name, color, side,
                          required_mm, remnant_mm, client_name, facade_width_mm, facade_height_mm, used_at
                   FROM mirror_profile_stock_usage
                   WHERE consumed_stock_id = %s
                   ORDER BY used_at DESC""",
                (sid,),
            )
            usage = cur.fetchall() or []
    return {"history": hist, "usage": usage, "cut_events": cut_events}


def profile_cut_status_summary(stock_id):
    sid = int(stock_id)
    events = list_profile_cut_events_by_stock(sid, limit=200) or []
    status = "active"
    reason = ""
    source = None
    for ev in events:
        et = str(ev.get("event_type") or "").lower()
        rtxt = str(ev.get("reason_text") or "").strip()
        if "rejection:damaged" in et or "inventory:damaged" in et:
            status = "damaged"
            reason = rtxt or reason
            source = ev
            break
        if "rejection:lost" in et or "inventory:lost" in et:
            status = "lost"
            reason = rtxt or reason
            source = ev
            break
    if source is None and events:
        source = events[0]
    return {"status": status, "reason_text": reason, "last_event": source}


def get_profile_history_timeline(stock_id, limit=200):
    sid = int(stock_id)
    events = list_profile_cut_events_by_stock(sid, limit=limit) or []
    timeline = []
    for ev in events:
        pl = _parse_profile_history_details_json(ev.get("payload_json"))
        timeline.append(
            {
                "id": ev.get("id"),
                "created_at": ev.get("created_at"),
                "event_type": ev.get("event_type"),
                "order_id": ev.get("order_id"),
                "batch_id": ev.get("batch_id"),
                "reason_text": ev.get("reason_text"),
                "actor_login": ev.get("actor_login"),
                "actor_role": ev.get("actor_role"),
                "payload_json": pl,
            }
        )
    summary = profile_cut_status_summary(sid)
    return {"timeline": timeline, "status": summary.get("status"), "reason_text": summary.get("reason_text"), "last_event": summary.get("last_event")}


def profile_stock_ancestor_chain(stock_id, max_depth=40):
    """
    Цепочка stock_id от корня склада к текущему остатку: [root_id, …, stock_id].
    Связь по событию remnant_from_profile_cut → details.source_stock_id.
    """
    try:
        cur_id = int(stock_id)
    except (TypeError, ValueError):
        return []
    if cur_id < 1:
        return []
    chain = [cur_id]
    seen = {cur_id}
    for _ in range(max_depth):
        hist = [dict(r) for r in (_get_profile_remnant_history_rows_ancestor_walk(cur_id) or [])]
        parent = None
        for h in reversed(list(hist)):
            if str(h.get("action_type") or "").strip() != "remnant_from_profile_cut":
                continue
            det = _parse_profile_history_details_json(h.get("details_json"))
            try:
                p = int(det.get("source_stock_id") or 0)
            except (TypeError, ValueError):
                p = 0
            if p > 0 and p not in seen:
                parent = p
                break
        if not parent:
            break
        chain.insert(0, parent)
        seen.add(parent)
        cur_id = parent
    return chain


def _profile_remnant_edge_details(child_stock_id, parent_stock_id):
    """Детали одного шага parent → child из журнала остатка."""
    try:
        cid = int(child_stock_id)
        pid = int(parent_stock_id)
    except (TypeError, ValueError):
        return {}
    for h in reversed(list([dict(r) for r in (_get_profile_remnant_history_rows_ancestor_walk(cid) or [])])):
        if str(h.get("action_type") or "").strip() != "remnant_from_profile_cut":
            continue
        det = _parse_profile_history_details_json(h.get("details_json"))
        try:
            if int(det.get("source_stock_id") or 0) != pid:
                continue
        except (TypeError, ValueError):
            continue
        out = dict(det)
        out["_order_id"] = h.get("order_id")
        out["_created_at"] = h.get("created_at")
        return out
    return {}


def get_profile_stock_visualization(stock_id, svg_width=680, bar_h=20, bar_gap=10):
    """
    Данные для SVG «балка»: по каждому шагу цепочки — доли снято / отход / оставшийся брус.
    Возвращает dict: chain, bars, svg_html, svg_xml, click_segments (для кликов в Qt).
    """
    try:
        sid = int(stock_id)
    except (TypeError, ValueError):
        return {"chain": [], "bars": [], "svg_xml": "", "svg_html": "", "click_segments": []}
    chain = profile_stock_ancestor_chain(sid)
    if len(chain) < 2:
        row = get_profile_stock_row(sid) or {}
        cur_len = int(row.get("length_mm") or 0)
        tip = (
            "Позиция без записи «остаток из реза» в журнале — это целый приходной брус или история ещё не создана."
            if cur_len
            else "Нет данных для шкалы."
        )
        return {
            "chain": chain,
            "bars": [],
            "svg_xml": "",
            "svg_html": (
                "<p style='color:#666;font-size:12px;margin:4px 0'>%s</p>"
                % html_module.escape(tip)
            ),
            "click_segments": [],
        }
    bars = []
    parts_svg = []
    click_segments = []
    y = 28
    margin_x = 16
    inner_w = max(120, int(svg_width) - 2 * margin_x)
    colors = {"cut": "#5c7cfa", "waste": "#dc3545", "remain": "#34a853"}

    for i in range(len(chain) - 1):
        parent_id, child_id = chain[i], chain[i + 1]
        edge = _profile_remnant_edge_details(child_id, parent_id)
        Lp = int(edge.get("parent_length_mm") or edge.get("origin_length_mm") or 0)
        cons = int(edge.get("consumed_mm") or 0)
        rest = int(edge.get("rest_mm") or 0)
        if Lp <= 0:
            Lp = max(1, cons + rest)
        waste = max(0, Lp - cons - rest)
        oid = edge.get("_order_id")
        created = edge.get("_created_at")
        title_base = "Заказ %s · %s" % (oid or "—", created or "—")
        bar_parts = [
            {"mm": cons, "kind": "cut", "label": "Снято с бруса №%s" % parent_id},
            {"mm": waste, "kind": "waste", "label": "Пил/отход"},
            {"mm": rest, "kind": "remain", "label": "Остаток → склад №%s" % child_id},
        ]
        bars.append(
            {
                "parent_stock_id": parent_id,
                "child_stock_id": child_id,
                "parent_length_mm": Lp,
                "consumed_mm": cons,
                "waste_mm": waste,
                "rest_mm": rest,
                "order_id": oid,
                "created_at": str(created) if created is not None else "",
            }
        )
        parts_svg.append(
            '<text x="%d" y="%d" font-size="11" fill="#333">Шаг %d: брус №%s (%d мм) → №%s</text>'
            % (margin_x, y - 6, i + 1, parent_id, Lp, child_id)
        )
        x0 = float(margin_x)
        for bp in bar_parts:
            mm = int(bp.get("mm") or 0)
            if mm <= 0:
                continue
            w_px = max(1.0, inner_w * (mm / float(Lp)))
            col = colors.get(bp.get("kind"), "#adb5bd")
            tip = "%s | %s | %s мм | %s" % (title_base, bp.get("label") or "", mm, bp.get("kind") or "")
            tip_esc = html_module.escape(tip, quote=True)
            parts_svg.append(
                '<rect x="%.2f" y="%d" width="%.2f" height="%d" fill="%s" stroke="#222" stroke-width="0.5">'
                '<title>%s</title></rect>' % (x0, y, w_px, bar_h, col, tip_esc)
            )
            click_segments.append(
                {
                    "x": int(round(x0)),
                    "y": int(y),
                    "w": max(1, int(round(w_px))),
                    "h": int(bar_h),
                    "fill": col,
                    "tip": tip,
                    "kind": str(bp.get("kind") or ""),
                    "order_id": oid,
                }
            )
            x0 += w_px
        y += bar_h + bar_gap

    svg_h = max(60, y + 12)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">'
        '<text x="%d" y="14" font-size="12" font-weight="bold" fill="#1a237e">Цепочка резов (клик по цветному сегменту — карточка деталей)</text>'
        "%s"
        "</svg>"
        % (svg_width, svg_h, svg_width, svg_h, margin_x, "".join(parts_svg))
    )
    legend = (
        '<p style="font-size:11px;color:#555;margin:6px 0 0 0">'
        '<span style="color:#5c7cfa">■</span> снято '
        '<span style="color:#dc3545">■</span> отход '
        '<span style="color:#34a853">■</span> остаток на складе</p>'
    )
    return {
        "chain": chain,
        "bars": bars,
        "svg_xml": svg,
        "svg_html": "<div>%s%s</div>" % (svg, legend),
        "click_segments": click_segments,
        "click_canvas_height": int(svg_h),
        "click_canvas_width": int(svg_width),
    }


def build_profile_stock_bar_svg_html(stock_id, svg_width=680):
    """Только HTML+SVG для встраивания в QTextBrowser / шаблоны WEB."""
    return (get_profile_stock_visualization(stock_id, svg_width=svg_width) or {}).get("svg_html") or ""


def _parse_profile_history_details_json(details_json):
    if details_json is None:
        return {}
    if isinstance(details_json, dict):
        return dict(details_json)
    if isinstance(details_json, str):
        try:
            return json.loads(details_json) if details_json.strip() else {}
        except Exception:
            return {"_raw": details_json}
    return {}


def format_profile_remnant_history_details_html(row):
    """Человекочитаемое описание строки mirror_profile_remnant_history для окна склада."""
    row = row or {}
    at = str(row.get("action_type") or "").strip()
    det = _parse_profile_history_details_json(row.get("details_json"))
    nm = row.get("_actor_name_map") or {}

    def _dn(login):
        return actor_display_from_map(nm, login)

    if at == "remnant_from_profile_cut":
        parts = []
        bid = det.get("batch_id")
        if bid is not None:
            try:
                parts.append("<b>Партия резки №%s</b>" % int(bid))
            except (TypeError, ValueError):
                parts.append("<b>Партия резки</b>")
        cb = (det.get("confirmed_by_login") or det.get("confirmed_by") or "").strip()
        if cb:
            parts.append("Подтвердил рез на производстве: %s" % html_module.escape(_dn(cb)))
        bcl = (det.get("batch_created_by_login") or "").strip()
        if bcl:
            parts.append("Создал партию: %s" % html_module.escape(_dn(bcl)))
        pname = (det.get("profile_name") or "").strip()
        pc = (det.get("profile_color") or "").strip()
        if pname or pc:
            parts.append("%s %s" % (html_module.escape(pname or "—"), html_module.escape(pc)))
        sl = (det.get("source_label") or "").strip()
        if sl:
            parts.append("Брус в плане: %s" % html_module.escape(sl))
        cuts = det.get("cuts") if isinstance(det.get("cuts"), list) else []
        cut_bits = []
        for c in cuts:
            if not isinstance(c, dict):
                continue
            try:
                pm = int((c or {}).get("piece_mm") or 0)
                cl = int((c or {}).get("cut_loss_mm") or 0)
            except (TypeError, ValueError):
                pm, cl = 0, 0
            if pm > 0:
                cut_bits.append("%d мм (потери %d мм)" % (pm, cl))
        if cut_bits:
            parts.append("Снято заготовок с этого бруса: " + html_module.escape(", ".join(cut_bits)))
        try:
            cons = det.get("consumed_mm")
            rest = det.get("rest_mm")
            if cons is not None or rest is not None:
                parts.append(
                    "Итого с позиции склада: −%s мм · деловой остаток после: %s мм"
                    % (html_module.escape(str(cons if cons is not None else "—")), html_module.escape(str(rest if rest is not None else "—")))
                )
        except Exception:
            pass
        plen = det.get("parent_length_mm")
        if plen is not None:
            try:
                parts.append("Длина исходного бруса до реза: %s мм" % html_module.escape(str(int(plen))))
            except (TypeError, ValueError):
                parts.append("Длина исходного бруса до реза: %s" % html_module.escape(str(plen)))
        items = det.get("batch_items") if isinstance(det.get("batch_items"), list) else []
        side_ru = {"top": "верх", "bottom": "низ", "left": "лево", "right": "право"}
        if items:
            rows = [
                "<tr style='background:#e8eef8;'><th>Заказ</th><th>Клиент</th><th>Изд.</th><th>Фасад</th><th>Сторона</th><th>Длина</th><th>Размер</th></tr>"
            ]
            for it in items[:80]:
                if not isinstance(it, dict):
                    continue
                oid = it.get("order_id")
                cln = (it.get("client_name") or "—").strip()
                fl = (it.get("facade_label") or "—").strip()
                pidx = it.get("product_index")
                sk = str(it.get("side_key") or "").strip().lower()
                skd = side_ru.get(sk, sk or "—")
                req = it.get("required_mm")
                fw = it.get("facade_width_mm")
                fh = it.get("facade_height_mm")
                dim = ("—" if fw in (None, "", 0) else str(fw)) + "×" + ("—" if fh in (None, "", 0) else str(fh))
                rows.append(
                    "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s мм</td></tr>"
                    % (
                        html_module.escape(str(oid if oid is not None else "—")),
                        html_module.escape(cln or "—"),
                        html_module.escape(str(pidx if pidx is not None else "—")),
                        html_module.escape(fl or "—"),
                        html_module.escape(skd),
                        html_module.escape(str(req if req is not None else "—")),
                        html_module.escape(dim),
                    )
                )
            if len(items) > 80:
                rows.append("<tr><td colspan='7' style='color:#666'>… ещё позиций: %d</td></tr>" % (len(items) - 80))
            parts.append(
                "<div style='margin-top:8px'><b>Все изделия в партии резки:</b></div>"
                "<table style='width:100%;border-collapse:collapse;font-size:11px;margin-top:4px' cellpadding='3'>"
                + "".join(rows)
                + "</table>"
            )
        else:
            parts.append(
                "<span style='color:#64748b'>Состав партии в записи не сохранён (старые данные до обновления программы).</span>"
            )
        return "<br/>".join(parts)
    if at == "reserve_source_consumed":
        return html_module.escape(
            "Списание при резерве: новый остаток stock_id=%s, снято %s мм, профиль ref=%s"
            % (
                det.get("new_remnant_stock_id"),
                det.get("required_mm"),
                det.get("profile_ref_id"),
            )
        )
    if at == "remnant_from_reserve":
        return html_module.escape(
            "Остаток после резерва: с позиции %s, длина %s мм (ref %s)"
            % (det.get("source_stock_id"), det.get("rest_mm"), det.get("profile_ref_id"))
        )
    if at == "label_created":
        uniq = str(det.get("unique_number") or "—").strip()
        num = det.get("label_number")
        try:
            num_s = str(int(num)) if num is not None else "—"
        except (TypeError, ValueError):
            num_s = str(num or "—")
        actor_login = str(row.get("hist_actor_login") or det.get("actor_login") or det.get("created_by_login") or "").strip()
        who_disp = (str(row.get("hist_actor_display") or "").strip() or _dn(actor_login) or actor_login).strip()
        who = html_module.escape(who_disp) if who_disp else "—"
        ca = row.get("created_at")
        if ca and hasattr(ca, "strftime"):
            dt_s = html_module.escape(ca.strftime("%d.%m.%Y %H:%M"))
        else:
            dt_s = html_module.escape(str(ca or "—"))
        return (
            "Создана этикетка склада: <b>%s</b> (№ %s). Создал: <b>%s</b> · %s"
            % (html_module.escape(uniq), html_module.escape(num_s), who, dt_s)
        )
    try:
        return html_module.escape(json.dumps(det, ensure_ascii=False)[:3500])
    except Exception:
        return html_module.escape(str(det)[:3500])


def profile_remnant_provenance_chain(stock_id, max_depth=25):
    """Цепочка происхождения: текущий профиль ← исходный брус (по журналу remnant_from_*)."""
    chain_ids: List[int] = []
    cycle_sid = None
    sid = int(stock_id)
    seen = set()
    for _ in range(max(max_depth, 1)):
        if sid < 1:
            break
        if sid in seen:
            cycle_sid = sid
            break
        seen.add(sid)
        chain_ids.append(sid)
        hist = [dict(r) for r in (_get_profile_remnant_history_rows_ancestor_walk(sid) or [])]
        hist.sort(key=lambda x: (x.get("created_at") is None, x.get("created_at") or ""))
        parent = None
        for h in hist:
            at = (h.get("action_type") or "").strip()
            if at in ("remnant_from_profile_cut", "remnant_from_reserve"):
                det = _parse_profile_history_details_json(h.get("details_json"))
                p = det.get("source_stock_id")
                if p is not None:
                    try:
                        parent = int(p)
                    except (TypeError, ValueError):
                        parent = None
                if parent:
                    break
        if not parent:
            break
        sid = parent
    if not chain_ids:
        return []
    stocks = get_profile_stock_rows_by_ids(chain_ids) if chain_ids else {}
    labels = get_profile_labels_by_stock_ids(chain_ids) if chain_ids else {}
    chain = []
    for sid in chain_ids:
        st = stocks.get(sid) or {}
        lab = labels.get(sid)
        un = (lab.get("unique_number") or "").strip() if lab else ""
        chain.append(
            {
                "stock_id": sid,
                "length_mm": st.get("length_mm"),
                "is_remnant": st.get("is_remnant"),
                "series": st.get("series"),
                "name": st.get("name"),
                "color": st.get("color"),
                "unique_number": un,
                "label_number": lab.get("label_number") if lab else None,
            }
        )
    if cycle_sid is not None:
        chain.append({"stock_id": cycle_sid, "cycle": True, "note": "повтор в цепочке"})
    return chain


def list_profile_plan_segments_for_stock(source_stock_id, limit=40):
    """Планы резки (партии), где этот stock_id был исходной заготовкой."""
    _ensure_profile_cut_workflow_tables()
    sid = int(source_stock_id)
    lim = max(1, min(200, int(limit or 40)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT seg.id AS segment_id, seg.plan_id, seg.source_stock_id, seg.source_label,
                       seg.source_length_mm, seg.remnant_mm, seg.cuts_json, seg.outputs_json,
                       seg.created_at AS segment_created_at,
                       p.batch_id, b.order_id, b.status AS batch_status, b.profile_name, b.profile_color,
                       b.created_by_login, b.created_by_role, b.created_at AS batch_created_at,
                       o.client_id AS order_client_id
                FROM mirror_profile_cut_plan_segments seg
                INNER JOIN mirror_profile_cut_plans p ON p.id = seg.plan_id
                INNER JOIN mirror_profile_cut_batches b ON b.id = p.batch_id
                LEFT JOIN mirror_orders o ON o.id = b.order_id
                WHERE seg.source_stock_id = %s
                ORDER BY seg.id DESC
                LIMIT %s
                """,
                (sid, lim),
            )
            return cur.fetchall() or []


def list_profile_cut_corrections_for_stock(source_stock_id, limit=80):
    _ensure_profile_cut_workflow_tables()
    sid = int(source_stock_id)
    lim = max(1, min(500, int(limit or 80)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, batch_id, plan_id, source_stock_id, expected_mm, correction_mm, actual_mm,
                          delta_mm, delta_pct, actor_login, actor_role, created_at
                   FROM mirror_profile_cut_corrections
                   WHERE source_stock_id = %s
                   ORDER BY id DESC
                   LIMIT %s""",
                (sid, lim),
            )
            return cur.fetchall() or []


def list_profile_cut_rejections_for_stock(source_stock_id, limit=80):
    _ensure_profile_cut_workflow_tables()
    sid = int(source_stock_id)
    lim = max(1, min(500, int(limit or 80)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, batch_id, plan_id, source_stock_id, action_type, reason_text, surviving_mm,
                          actor_login, actor_role, created_at
                   FROM mirror_profile_cut_rejections
                   WHERE source_stock_id = %s
                   ORDER BY id DESC
                   LIMIT %s""",
                (sid, lim),
            )
            return cur.fetchall() or []


def get_profile_remnants_by_order_id(order_id):
    _ensure_profile_label_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT stock_id FROM mirror_profile_remnant_history WHERE order_id = %s ORDER BY stock_id",
                (int(order_id),),
            )
            return [r.get("stock_id") for r in (cur.fetchall() or []) if r.get("stock_id") is not None]


def get_profile_remnant_history_by_order_id(order_id):
    """История остатков профиля по заказу с деталями действий."""
    _ensure_profile_label_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, stock_id, order_id, action_type, details_json, created_at
                   FROM mirror_profile_remnant_history
                   WHERE order_id = %s
                   ORDER BY id""",
                (int(order_id),),
            )
            return cur.fetchall() or []


def _ensure_profile_stock_usage_table():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_stock_usage (
                    id SERIAL PRIMARY KEY,
                    consumed_stock_id INTEGER,
                    profile_ref_id INTEGER,
                    series VARCHAR(255) DEFAULT '',
                    name VARCHAR(255) DEFAULT '',
                    color VARCHAR(255) DEFAULT '',
                    side VARCHAR(16) DEFAULT '',
                    required_mm INTEGER,
                    remnant_mm INTEGER,
                    client_name VARCHAR(512) DEFAULT '',
                    facade_width_mm INTEGER,
                    facade_height_mm INTEGER,
                    used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )


def _ensure_profile_waste_table():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_waste (
                    id SERIAL PRIMARY KEY,
                    source_stock_id INTEGER,
                    profile_ref_id INTEGER,
                    series VARCHAR(255) DEFAULT '',
                    name VARCHAR(255) DEFAULT '',
                    color VARCHAR(255) DEFAULT '',
                    waste_mm INTEGER,
                    order_id INTEGER,
                    reason VARCHAR(255) DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )


def add_profile_waste(source_stock_id, profile_ref_id, series, name, color, waste_mm, order_id=None, reason=""):
    _ensure_profile_waste_table()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_profile_waste
                   (source_stock_id, profile_ref_id, series, name, color, waste_mm, order_id, reason)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    int(source_stock_id) if source_stock_id is not None else None,
                    int(profile_ref_id) if profile_ref_id is not None else None,
                    str(series or "")[:255],
                    str(name or "")[:255],
                    str(color or "")[:255],
                    int(waste_mm) if waste_mm is not None else None,
                    int(order_id) if order_id is not None else None,
                    str(reason or "")[:255],
                ),
            )


def list_profile_waste(limit=500):
    _ensure_profile_waste_table()
    lim = max(1, min(5000, int(limit)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, source_stock_id, profile_ref_id, series, name, color, waste_mm, order_id, reason, created_at
                   FROM mirror_profile_waste
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (lim,),
            )
            return cur.fetchall() or []


def _ensure_profile_cut_workflow_tables():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_cut_batches (
                    id SERIAL PRIMARY KEY,
                    order_id INTEGER NOT NULL,
                    profile_ref_id INTEGER,
                    profile_name VARCHAR(255) DEFAULT '',
                    profile_color VARCHAR(255) DEFAULT '',
                    status VARCHAR(32) NOT NULL DEFAULT 'created',
                    notes TEXT DEFAULT '',
                    created_by_user_id INTEGER,
                    created_by_login VARCHAR(128) DEFAULT '',
                    created_by_role VARCHAR(32) DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_cut_batch_items (
                    id SERIAL PRIMARY KEY,
                    batch_id INTEGER NOT NULL,
                    order_id INTEGER NOT NULL,
                    product_index INTEGER,
                    side_key VARCHAR(16) DEFAULT '',
                    required_mm INTEGER NOT NULL,
                    facade_width_mm INTEGER,
                    facade_height_mm INTEGER,
                    facade_label VARCHAR(64) DEFAULT '',
                    selected BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_cut_plans (
                    id SERIAL PRIMARY KEY,
                    batch_id INTEGER NOT NULL,
                    version_no INTEGER NOT NULL DEFAULT 1,
                    algorithm VARCHAR(64) DEFAULT '',
                    params_json TEXT DEFAULT '{}',
                    summary_json TEXT DEFAULT '{}',
                    created_by_user_id INTEGER,
                    created_by_login VARCHAR(128) DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_cut_plan_segments (
                    id SERIAL PRIMARY KEY,
                    plan_id INTEGER NOT NULL,
                    source_stock_id INTEGER,
                    source_kind VARCHAR(64) NOT NULL DEFAULT 'new',
                    source_length_mm INTEGER,
                    source_label VARCHAR(64) DEFAULT '',
                    cuts_json TEXT DEFAULT '[]',
                    outputs_json TEXT DEFAULT '[]',
                    waste_mm INTEGER DEFAULT 0,
                    remnant_mm INTEGER,
                    remnant_stock_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_piece_labels (
                    id SERIAL PRIMARY KEY,
                    batch_id INTEGER NOT NULL,
                    plan_id INTEGER,
                    piece_kind VARCHAR(16) NOT NULL DEFAULT 'assembly',
                    label_prefix VARCHAR(4) NOT NULL DEFAULT 'A',
                    label_number INTEGER NOT NULL,
                    unique_number VARCHAR(64) UNIQUE,
                    piece_mm INTEGER,
                    source_stock_id INTEGER,
                    qr_url TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_cut_corrections (
                    id SERIAL PRIMARY KEY,
                    batch_id INTEGER NOT NULL,
                    plan_id INTEGER,
                    source_stock_id INTEGER,
                    expected_mm INTEGER,
                    correction_mm INTEGER NOT NULL DEFAULT 0,
                    actual_mm INTEGER,
                    delta_mm INTEGER,
                    delta_pct NUMERIC(10,4),
                    actor_user_id INTEGER,
                    actor_login VARCHAR(128) DEFAULT '',
                    actor_role VARCHAR(32) DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_cut_rejections (
                    id SERIAL PRIMARY KEY,
                    batch_id INTEGER NOT NULL,
                    plan_id INTEGER,
                    source_stock_id INTEGER,
                    action_type VARCHAR(16) NOT NULL DEFAULT 'lost',
                    reason_text TEXT DEFAULT '',
                    surviving_mm INTEGER,
                    actor_user_id INTEGER,
                    actor_login VARCHAR(128) DEFAULT '',
                    actor_role VARCHAR(32) DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_cut_batch_runtime (
                    batch_id INTEGER PRIMARY KEY,
                    stage_no INTEGER NOT NULL DEFAULT 1,
                    stage_payload_json TEXT DEFAULT '{}',
                    current_step_index INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_facade_finish_labels (
                    id SERIAL PRIMARY KEY,
                    public_number INTEGER NOT NULL UNIQUE,
                    batch_id INTEGER NOT NULL,
                    order_id INTEGER NOT NULL,
                    product_index INTEGER NOT NULL,
                    facade_label VARCHAR(64) NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (batch_id, order_id, product_index, facade_label)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_profile_cut_events (
                    id SERIAL PRIMARY KEY,
                    stock_id INTEGER,
                    order_id INTEGER,
                    batch_id INTEGER,
                    event_type VARCHAR(40) NOT NULL DEFAULT 'history_event',
                    reason_text TEXT DEFAULT '',
                    actor_user_id INTEGER,
                    actor_login VARCHAR(128) DEFAULT '',
                    actor_role VARCHAR(32) DEFAULT '',
                    payload_json TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_cut_events_stock_created ON mirror_profile_cut_events(stock_id, created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_cut_events_order_created ON mirror_profile_cut_events(order_id, created_at DESC)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_cut_events_batch ON mirror_profile_cut_events(batch_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_profile_cut_events_type ON mirror_profile_cut_events(event_type)")
            try:
                cur.execute(
                    "ALTER TABLE mirror_profile_cut_plan_segments ALTER COLUMN source_kind TYPE VARCHAR(64)"
                )
            except Exception:
                pass
            try:
                cur.execute(
                    "UPDATE mirror_profile_cut_plan_segments SET source_kind = %s WHERE source_kind = %s",
                    ("warehouse_remnant", "warehouse_remnan"),
                )
            except Exception:
                pass


def add_profile_cut_event(
    stock_id=None,
    order_id=None,
    batch_id=None,
    event_type="history_event",
    reason_text="",
    actor_user_id=None,
    actor_login="",
    actor_role="",
    payload_json=None,
):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mirror_profile_cut_events
                (stock_id, order_id, batch_id, event_type, reason_text, actor_user_id, actor_login, actor_role, payload_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    int(stock_id) if stock_id is not None else None,
                    int(order_id) if order_id is not None else None,
                    int(batch_id) if batch_id is not None else None,
                    str(event_type or "history_event")[:40],
                    str(reason_text or ""),
                    int(actor_user_id) if actor_user_id is not None else None,
                    str(actor_login or "")[:128],
                    str(actor_role or "")[:32],
                    json.dumps(payload_json or {}, ensure_ascii=False),
                ),
            )
            return (cur.fetchone() or {}).get("id")


def list_profile_cut_events_by_stock(stock_id, limit=200):
    _ensure_profile_cut_workflow_tables()
    lim = max(1, min(5000, int(limit)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.stock_id, e.order_id, e.batch_id, e.event_type, e.reason_text,
                       e.actor_user_id, e.actor_login, e.actor_role, e.payload_json, e.created_at,
                       COALESCE(NULLIF(TRIM(mc.name), ''), NULLIF(TRIM(mo.client_name), ''), '') AS join_client_name
                FROM mirror_profile_cut_events e
                LEFT JOIN mirror_orders mo ON mo.id = e.order_id
                LEFT JOIN mirror_clients mc ON mc.id = mo.client_id
                WHERE e.stock_id = %s
                ORDER BY e.created_at DESC, e.id DESC
                LIMIT %s
                """,
                (int(stock_id), lim),
            )
            return cur.fetchall() or []


def list_profile_cut_events_by_stocks_bulk(stock_ids, limit_per_stock=400):
    """События резки по нескольким stock_id одним запросом (без N+1)."""
    _ensure_profile_cut_workflow_tables()
    ids = []
    seen = set()
    for sid in list(stock_ids or []):
        try:
            si = int(sid)
        except (TypeError, ValueError):
            continue
        if si < 1 or si in seen:
            continue
        seen.add(si)
        ids.append(si)
    if not ids:
        return {}
    lim_per = max(1, min(5000, int(limit_per_stock)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.id, e.stock_id, e.order_id, e.batch_id, e.event_type, e.reason_text,
                       e.actor_user_id, e.actor_login, e.actor_role, e.payload_json, e.created_at,
                       COALESCE(NULLIF(TRIM(mc.name), ''), NULLIF(TRIM(mo.client_name), ''), '') AS join_client_name
                FROM mirror_profile_cut_events e
                LEFT JOIN mirror_orders mo ON mo.id = e.order_id
                LEFT JOIN mirror_clients mc ON mc.id = mo.client_id
                WHERE e.stock_id = ANY(%s)
                ORDER BY e.stock_id, e.created_at DESC, e.id DESC
                """,
                (ids,),
            )
            rows = cur.fetchall() or []
    buckets: Dict[int, List[Any]] = defaultdict(list)
    for r in rows:
        try:
            sid = int(r.get("stock_id"))
        except (TypeError, ValueError):
            continue
        if len(buckets[sid]) >= lim_per:
            continue
        buckets[sid].append(r)
    return dict(buckets)


def list_profile_plan_segments_for_stocks_bulk(stock_ids, limit_per_stock=120):
    """Сегменты планов резки, где указанные stock_id были источником — одним запросом."""
    _ensure_profile_cut_workflow_tables()
    ids = []
    seen = set()
    for sid in list(stock_ids or []):
        try:
            si = int(sid)
        except (TypeError, ValueError):
            continue
        if si < 1 or si in seen:
            continue
        seen.add(si)
        ids.append(si)
    if not ids:
        return {}
    lim_per = max(1, min(200, int(limit_per_stock)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT seg.id AS segment_id, seg.plan_id, seg.source_stock_id, seg.source_label,
                       seg.source_length_mm, seg.remnant_mm, seg.cuts_json, seg.outputs_json,
                       seg.created_at AS segment_created_at,
                       p.batch_id, b.order_id, b.status AS batch_status, b.profile_name, b.profile_color,
                       b.created_by_login, b.created_by_role, b.created_at AS batch_created_at,
                       o.client_id AS order_client_id
                FROM mirror_profile_cut_plan_segments seg
                INNER JOIN mirror_profile_cut_plans p ON p.id = seg.plan_id
                INNER JOIN mirror_profile_cut_batches b ON b.id = p.batch_id
                LEFT JOIN mirror_orders o ON o.id = b.order_id
                WHERE seg.source_stock_id = ANY(%s)
                ORDER BY seg.source_stock_id, seg.id DESC
                """,
                (ids,),
            )
            rows = cur.fetchall() or []
    buckets: Dict[int, List[Any]] = defaultdict(list)
    for r in rows:
        try:
            sid = int(r.get("source_stock_id"))
        except (TypeError, ValueError):
            continue
        if len(buckets[sid]) >= lim_per:
            continue
        buckets[sid].append(r)
    return dict(buckets)


def get_profile_stock_usage_by_consumed_stock_ids(consumed_stock_ids):
    """Строки mirror_profile_stock_usage для набора consumed_stock_id одним запросом."""
    _ensure_profile_stock_usage_table()
    ids = []
    seen = set()
    for sid in list(consumed_stock_ids or []):
        try:
            si = int(sid)
        except (TypeError, ValueError):
            continue
        if si < 1 or si in seen:
            continue
        seen.add(si)
        ids.append(si)
    if not ids:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, consumed_stock_id, profile_ref_id, series, name, color, side,
                          required_mm, remnant_mm, client_name, facade_width_mm, facade_height_mm, used_at
                   FROM mirror_profile_stock_usage
                   WHERE consumed_stock_id = ANY(%s)
                   ORDER BY consumed_stock_id, used_at DESC, id DESC""",
                (ids,),
            )
            rows = cur.fetchall() or []
    out: Dict[int, List[Any]] = defaultdict(list)
    for r in rows or []:
        try:
            cid = int(r.get("consumed_stock_id"))
        except (TypeError, ValueError):
            continue
        out[cid].append(r)
    return dict(out)


def get_orders_timeline_fields_by_ids(order_ids):
    """Минимальные поля заказа для таймлайна: client_id, client_name (один запрос)."""
    ids = []
    seen = set()
    for oid in list(order_ids or []):
        try:
            oi = int(oid)
        except (TypeError, ValueError):
            continue
        if oi < 1 or oi in seen:
            continue
        seen.add(oi)
        ids.append(oi)
    if not ids:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.client_id,
                       COALESCE(NULLIF(TRIM(c.name), ''), NULLIF(TRIM(o.client_name), ''), '') AS client_name
                FROM mirror_orders o
                LEFT JOIN mirror_clients c ON c.id = o.client_id
                WHERE o.id = ANY(%s)
                """,
                (ids,),
            )
            rows = cur.fetchall() or []
    return {int(r["id"]): dict(r) for r in rows or []}


def _profile_stock_is_remnant_flags_map(stock_ids) -> Dict[int, bool]:
    """Флаг is_remnant как в profile_stock_consumed_was_remnant (в т.ч. удалённые позиции)."""
    ids = []
    seen = set()
    for sid in list(stock_ids or []):
        if sid is None:
            continue
        try:
            si = int(sid)
        except (TypeError, ValueError):
            continue
        if si < 1 or si in seen:
            continue
        seen.add(si)
        ids.append(si)
    if not ids:
        return {}
    _ensure_profile_stock_tables()
    active: Dict[int, bool] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, is_remnant FROM mirror_profile_stock WHERE id = ANY(%s)",
                (ids,),
            )
            for r in cur.fetchall() or []:
                try:
                    active[int(r["id"])] = bool(r.get("is_remnant"))
                except (TypeError, ValueError):
                    continue
    missing = [i for i in ids if i not in active]
    deleted: Dict[int, bool] = {}
    if missing:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT ON (stock_id) stock_id, is_remnant
                    FROM mirror_deleted_profile_stock
                    WHERE stock_id = ANY(%s)
                    ORDER BY stock_id, deleted_at DESC
                    """,
                    (missing,),
                )
                for r in cur.fetchall() or []:
                    try:
                        deleted[int(r["stock_id"])] = bool(r.get("is_remnant"))
                    except (TypeError, ValueError):
                        continue
    out: Dict[int, bool] = {}
    for i in ids:
        if i in active:
            out[i] = bool(active[i])
        elif i in deleted:
            out[i] = bool(deleted[i])
        else:
            out[i] = True
    return out


def annotate_profile_facade_usage_rows_with_reason_ru(usage_rows) -> None:
    """
    Заполняет usage_summary_ru у каждой строки usage (как profile_facade_usage_reason_ru),
    с пакетными запросами к БД.
    """
    rows = [u for u in (usage_rows or []) if isinstance(u, dict)]
    if not rows:
        return
    consumed: List[int] = []
    for u in rows:
        cid = u.get("consumed_stock_id")
        try:
            ci = int(cid) if cid is not None else 0
        except (TypeError, ValueError):
            ci = 0
        if ci > 0:
            consumed.append(ci)
    rem_flags = _profile_stock_is_remnant_flags_map(consumed)
    triple_keys = []
    for u in rows:
        cid = u.get("consumed_stock_id")
        rem = u.get("remnant_mm")
        ref = u.get("profile_ref_id")
        try:
            ci = int(cid) if cid is not None else 0
        except (TypeError, ValueError):
            ci = 0
        try:
            rm = int(rem) if rem is not None else None
        except (TypeError, ValueError):
            rm = None
        if ci > 0 and rm is not None and rm >= 200:
            triple_keys.append((ci, ref, rm))
    child_by_key: Dict[Tuple[int, Any, int], Any] = {}
    for key in set(triple_keys):
        child_by_key[key] = find_remnant_stock_id_after_consuming_source(key[0], key[1], key[2])
    child_ids = []
    seen_ch = set()
    for v in child_by_key.values():
        if v is None:
            continue
        try:
            vi = int(v)
        except (TypeError, ValueError):
            continue
        if vi > 0 and vi not in seen_ch:
            seen_ch.add(vi)
            child_ids.append(vi)
    labels = get_profile_labels_by_stock_ids(child_ids) if child_ids else {}

    def _child_label(child_id):
        if not child_id:
            return ""
        try:
            ci = int(child_id)
        except (TypeError, ValueError):
            return ""
        lab = labels.get(ci)
        if lab:
            child_label = str((lab.get("unique_number") or "")).strip() or (
                "S%s" % lab.get("label_number") if lab.get("label_number") is not None else ""
            )
            if not child_label:
                child_label = "S%s" % ci
            return child_label
        return "S%s" % ci

    for u in rows:
        cid = u.get("consumed_stock_id")
        ref = u.get("profile_ref_id")
        rem = u.get("remnant_mm")
        fw = u.get("facade_width_mm")
        fh = u.get("facade_height_mm")
        req = u.get("required_mm")
        try:
            ci = int(cid) if cid is not None else 0
        except (TypeError, ValueError):
            ci = 0
        was_remnant = rem_flags.get(ci, True) if ci > 0 else True
        try:
            rm = int(rem) if rem is not None else None
        except (TypeError, ValueError):
            rm = None
        child_id = None
        if ci > 0 and rm is not None and rm >= 200:
            child_id = child_by_key.get((ci, ref, rm))
        child_label = _child_label(child_id)
        parts = []
        parts.append(
            "Фасад %s×%s мм · рез %s мм"
            % (
                fw if fw not in (None, "") else "—",
                fh if fh not in (None, "") else "—",
                req if req not in (None, "") else "—",
            )
        )
        if not was_remnant:
            parts.append("Остаток: использован")
            if child_label:
                parts.append("Текущий остаток профиля %s" % child_label)
            if rem not in (None, ""):
                parts.append("длина %s мм" % rem)
        else:
            parts.append("Остаток после списания: %s мм" % (rem if rem not in (None, "") else "—"))
        u["usage_summary_ru"] = " · ".join(parts)


def format_profile_cut_event_html(ev):
    """Одно событие mirror_profile_cut_events — текст для окна истории склада (заказ, клиент, партия, мм)."""
    if not ev:
        return ""
    et = str(ev.get("event_type") or "").strip()
    pl = _parse_profile_history_details_json(ev.get("payload_json"))
    oid = ev.get("order_id")
    bid = ev.get("batch_id")
    client = (ev.get("join_client_name") or pl.get("client_name") or "").strip()
    dt = ev.get("created_at")
    dt_s = dt.strftime("%d.%m.%Y %H:%M") if dt and hasattr(dt, "strftime") else str(dt or "—")
    actor = (ev.get("actor_login") or "").strip()
    disp = (str(ev.get("actor_display") or "").strip() or actor).strip()
    who = ""
    if disp:
        who = html_module.escape(disp)

    def _oid_txt():
        if oid is None:
            return "—"
        try:
            return str(int(oid))
        except (TypeError, ValueError):
            return str(oid)

    def _bid_txt():
        if bid is None:
            return ""
        try:
            return str(int(bid))
        except (TypeError, ValueError):
            return str(bid)

    lines = []
    if "label_created" in et:
        uniq = pl.get("unique_number") or "—"
        num = pl.get("label_number")
        try:
            num_s = str(int(num)) if num is not None else str(num or "—")
        except (TypeError, ValueError):
            num_s = str(num or "—")
        who_txt = who if who else "—"
        return "Создана этикетка склада: <b>%s</b> (№ %s). Создал: <b>%s</b> · %s" % (
            html_module.escape(str(uniq)),
            html_module.escape(num_s),
            who_txt,
            html_module.escape(str(dt_s)),
        )

    if "remnant_from_facade_cut" in et or et.startswith("history:remnant_from"):
        lines.append("<b>Остаток после реза фасада</b> (партия / заказ)")
        if _bid_txt():
            lines.append("Партия резки: <b>%s</b>" % html_module.escape(_bid_txt()))
        lines.append("Заказ: <b>%s</b> · клиент: <b>%s</b>" % (_oid_txt(), html_module.escape(client or "—")))
        cuts = pl.get("cuts") if isinstance(pl.get("cuts"), list) else []
        if cuts:
            bits = []
            for c in cuts[:40]:
                if not isinstance(c, dict):
                    continue
                pm = int((c or {}).get("piece_mm") or 0)
                cl = int((c or {}).get("cut_loss_mm") or 0)
                if pm > 0:
                    bits.append("%d мм (пил %d)" % (pm, cl))
            if bits:
                lines.append("Заготовки с бруса: " + html_module.escape(", ".join(bits)))
        if pl.get("consumed_mm") is not None or pl.get("rest_mm") is not None:
            lines.append(
                "Снято с позиции: %s мм · деловой остаток: %s мм"
                % (
                    html_module.escape(str(pl.get("consumed_mm") if pl.get("consumed_mm") is not None else "—")),
                    html_module.escape(str(pl.get("rest_mm") if pl.get("rest_mm") is not None else "—")),
                )
            )
        return "<br/>".join(lines)

    # Универсально: тип, заказ, клиент, партия, причина, JSON-кратко
    lines.append("<b>%s</b>" % html_module.escape(et or "событие"))
    lines.append("Заказ: <b>%s</b> · клиент: <b>%s</b>" % (_oid_txt(), html_module.escape(client or "—")))
    if _bid_txt():
        lines.append("Партия: <b>%s</b>" % html_module.escape(_bid_txt()))
    rtxt = str(ev.get("reason_text") or "").strip()
    if rtxt:
        lines.append("Комментарий: %s" % html_module.escape(rtxt[:500]))
    if who:
        lines.append("Кто: %s" % who)
    if pl:
        try:
            snippet = json.dumps(pl, ensure_ascii=False)[:900]
            lines.append("<span style='color:#555;font-size:11px'>%s</span>" % html_module.escape(snippet))
        except Exception:
            pass
    return "<br/>".join(lines)


def backfill_profile_cut_events_from_history(limit=5000):
    _ensure_profile_label_tables()
    _ensure_profile_cut_workflow_tables()
    lim = max(1, min(200000, int(limit)))
    inserted = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT h.id, h.stock_id, h.order_id, h.action_type, h.details_json, h.created_at
                FROM mirror_profile_remnant_history h
                LEFT JOIN mirror_profile_cut_events e
                  ON e.stock_id = h.stock_id
                 AND e.order_id IS NOT DISTINCT FROM h.order_id
                 AND e.event_type = 'history:' || h.action_type
                 AND e.created_at = h.created_at
                WHERE e.id IS NULL
                ORDER BY h.id
                LIMIT %s
                """,
                (lim,),
            )
            rows = cur.fetchall() or []
            for h in rows:
                det = _parse_profile_history_details_json(h.get("details_json"))
                cur.execute(
                    """
                    INSERT INTO mirror_profile_cut_events
                    (stock_id, order_id, batch_id, event_type, reason_text, actor_user_id, actor_login, actor_role, payload_json, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        int(h.get("stock_id")) if h.get("stock_id") is not None else None,
                        int(h.get("order_id")) if h.get("order_id") is not None else None,
                        int(det.get("batch_id")) if det.get("batch_id") is not None else None,
                        "history:%s" % str(h.get("action_type") or "used")[:32],
                        str(det.get("reason") or det.get("reason_text") or ""),
                        int(det.get("actor_user_id")) if det.get("actor_user_id") is not None else None,
                        str(det.get("actor_login") or det.get("confirmed_by_login") or ""),
                        str(det.get("actor_role") or det.get("confirmed_by_role") or ""),
                        json.dumps(det or {}, ensure_ascii=False),
                        h.get("created_at"),
                    ),
                )
                inserted += 1
    return inserted


def create_profile_cut_batch(order_id, profile_ref_id=None, profile_name="", profile_color="", notes="", created_by_user_id=None, created_by_login="", created_by_role=""):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_profile_cut_batches
                   (order_id, profile_ref_id, profile_name, profile_color, notes, created_by_user_id, created_by_login, created_by_role)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    int(order_id),
                    int(profile_ref_id) if profile_ref_id is not None else None,
                    str(profile_name or "")[:255],
                    str(profile_color or "")[:255],
                    str(notes or ""),
                    int(created_by_user_id) if created_by_user_id is not None else None,
                    str(created_by_login or "")[:128],
                    str(created_by_role or "")[:32],
                ),
            )
            row = cur.fetchone() or {}
            return row.get("id")


def add_profile_cut_batch_item(batch_id, order_id, product_index, side_key, required_mm, facade_width_mm=None, facade_height_mm=None, facade_label="", selected=True):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_profile_cut_batch_items
                   (batch_id, order_id, product_index, side_key, required_mm, facade_width_mm, facade_height_mm, facade_label, selected)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    int(batch_id),
                    int(order_id),
                    int(product_index) if product_index is not None else None,
                    str(side_key or "")[:16],
                    int(required_mm),
                    int(facade_width_mm) if facade_width_mm is not None else None,
                    int(facade_height_mm) if facade_height_mm is not None else None,
                    str(facade_label or "")[:64],
                    bool(selected),
                ),
            )
            row = cur.fetchone() or {}
            return row.get("id")


def list_profile_cut_batches(status=None, limit=300):
    _ensure_profile_cut_workflow_tables()
    lim = max(1, min(5000, int(limit)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            q = """SELECT id, order_id, profile_ref_id, profile_name, profile_color, status, notes,
                          created_by_user_id, created_by_login, created_by_role, created_at, updated_at
                   FROM mirror_profile_cut_batches WHERE 1=1"""
            params = []
            if status is not None:
                q += " AND status = %s"
                params.append(str(status))
            q += " ORDER BY id DESC LIMIT %s"
            params.append(lim)
            cur.execute(q, params)
            return cur.fetchall() or []


def get_profile_cut_batch(batch_id):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, order_id, profile_ref_id, profile_name, profile_color, status, notes,
                          created_by_user_id, created_by_login, created_by_role, created_at, updated_at
                   FROM mirror_profile_cut_batches
                   WHERE id = %s""",
                (int(batch_id),),
            )
            return cur.fetchone()


def set_profile_cut_batch_status(batch_id, status):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mirror_profile_cut_batches SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (str(status or "created")[:32], int(batch_id)),
            )


def list_profile_cut_batch_items(batch_id):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, batch_id, order_id, product_index, side_key, required_mm, facade_width_mm,
                          facade_height_mm, facade_label, selected, created_at
                   FROM mirror_profile_cut_batch_items
                   WHERE batch_id = %s
                   ORDER BY id""",
                (int(batch_id),),
            )
            return cur.fetchall() or []


def update_profile_cut_batch_item_required_mm(item_id, required_mm):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mirror_profile_cut_batch_items SET required_mm = %s WHERE id = %s",
                (int(required_mm), int(item_id)),
            )
            return cur.rowcount


def update_profile_cut_batch_items_required_mm_batch(updates: List[Tuple[int, int]]) -> int:
    """Пакетное обновление required_mm по id позиции (одна транзакция)."""
    if not updates:
        return 0
    _ensure_profile_cut_workflow_tables()
    total = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for item_id, required_mm in updates:
                try:
                    iid = int(item_id)
                    mm = int(required_mm)
                except (TypeError, ValueError):
                    continue
                if iid < 1:
                    continue
                cur.execute(
                    "UPDATE mirror_profile_cut_batch_items SET required_mm = %s WHERE id = %s",
                    (mm, iid),
                )
                total += int(cur.rowcount or 0)
    return total


def list_active_facade_production_busy_keys(limit_batches: int = 800) -> Set[str]:
    """
    Ключи «занятых» изделий для резки фасада: "order_id|product_index|facade_label".
    Один SQL вместо N+1 (список batch → items по каждому).
    """
    _ensure_profile_cut_workflow_tables()
    lim = max(1, min(2000, int(limit_batches)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT i.order_id, i.product_index, i.facade_label
                FROM mirror_profile_cut_batch_items i
                INNER JOIN (
                    SELECT id FROM mirror_profile_cut_batches
                    WHERE COALESCE(notes, '') = 'created_from_production'
                      AND status IN ('created', 'planned', 'cut_done')
                    ORDER BY id DESC
                    LIMIT %s
                ) b ON b.id = i.batch_id
                WHERE i.order_id > 0
                  AND i.product_index > 0
                  AND TRIM(COALESCE(i.facade_label, '')) <> ''
                """,
                (lim,),
            )
            rows = cur.fetchall() or []
    keys: Set[str] = set()
    for r in rows:
        oid = int(r.get("order_id") or 0)
        pidx = int(r.get("product_index") or 0)
        fl = str(r.get("facade_label") or "").strip()
        if oid > 0 and pidx > 0 and fl:
            keys.add("%s|%s|%s" % (oid, pidx, fl))
    return keys


def create_profile_cut_plan(batch_id, algorithm="", params_json=None, summary_json=None, created_by_user_id=None, created_by_login=""):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(version_no), 0) AS v FROM mirror_profile_cut_plans WHERE batch_id = %s",
                (int(batch_id),),
            )
            v = int((cur.fetchone() or {}).get("v") or 0) + 1
            cur.execute(
                """INSERT INTO mirror_profile_cut_plans
                   (batch_id, version_no, algorithm, params_json, summary_json, created_by_user_id, created_by_login)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id, version_no""",
                (
                    int(batch_id),
                    int(v),
                    str(algorithm or "")[:64],
                    json.dumps(params_json or {}, ensure_ascii=False),
                    json.dumps(summary_json or {}, ensure_ascii=False),
                    int(created_by_user_id) if created_by_user_id is not None else None,
                    str(created_by_login or "")[:128],
                ),
            )
            return cur.fetchone() or {}


def get_latest_profile_cut_plan(batch_id):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, batch_id, version_no, algorithm, params_json, summary_json, created_by_user_id, created_by_login, created_at
                   FROM mirror_profile_cut_plans
                   WHERE batch_id = %s
                   ORDER BY id DESC
                   LIMIT 1""",
                (int(batch_id),),
            )
            return cur.fetchone()


def replace_profile_cut_plan_segments(plan_id, segments):
    _ensure_profile_cut_workflow_tables()
    segs = list(segments or [])
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mirror_profile_cut_plan_segments WHERE plan_id = %s", (int(plan_id),))
            for s in segs:
                s = s if isinstance(s, dict) else {}
                cur.execute(
                    """INSERT INTO mirror_profile_cut_plan_segments
                       (plan_id, source_stock_id, source_kind, source_length_mm, source_label, cuts_json, outputs_json,
                        waste_mm, remnant_mm, remnant_stock_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        int(plan_id),
                        int(s.get("source_stock_id")) if s.get("source_stock_id") is not None else None,
                        str(s.get("source_kind") or "new")[:64],
                        int(s.get("source_length_mm")) if s.get("source_length_mm") is not None else None,
                        str(s.get("source_label") or "")[:64],
                        json.dumps(s.get("cuts") or [], ensure_ascii=False),
                        json.dumps(s.get("outputs") or [], ensure_ascii=False),
                        int(s.get("waste_mm") or 0),
                        int(s.get("remnant_mm")) if s.get("remnant_mm") is not None else None,
                        int(s.get("remnant_stock_id")) if s.get("remnant_stock_id") is not None else None,
                    ),
                )


def list_profile_cut_plan_segments(plan_id):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, plan_id, source_stock_id, source_kind, source_length_mm, source_label, cuts_json,
                          outputs_json, waste_mm, remnant_mm, remnant_stock_id, created_at
                   FROM mirror_profile_cut_plan_segments
                   WHERE plan_id = %s
                   ORDER BY id""",
                (int(plan_id),),
            )
            return cur.fetchall() or []


def list_facade_production_batches_with_plans(include_completed=False, limit=300):
    """
    Карточки фасадных партий для WEB: один проход по БД (батчи + последний план + сегменты + позиции),
    без N+1 запросов на каждый batch_id.
    """
    _ensure_profile_cut_workflow_tables()
    lim = max(1, min(500, int(limit)))
    notes_val = "created_from_production"
    if include_completed:
        st_any = ("created", "planned", "cut_done", "completed", "assembled")
    else:
        st_any = ("created", "planned", "cut_done")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, order_id, profile_ref_id, profile_name, profile_color, status, notes,
                          created_by_user_id, created_by_login, created_by_role, created_at, updated_at
                   FROM mirror_profile_cut_batches
                   WHERE COALESCE(notes, '') = %s AND status = ANY(%s)
                   ORDER BY id DESC
                   LIMIT %s""",
                (notes_val, list(st_any), lim),
            )
            batches = cur.fetchall() or []
            if not batches:
                return []
            batch_ids = [int(b["id"]) for b in batches if b.get("id") is not None]
            if not batch_ids:
                return []
            cur.execute(
                """SELECT DISTINCT ON (batch_id)
                          id, batch_id, version_no, algorithm, params_json, summary_json,
                          created_by_user_id, created_by_login, created_at
                   FROM mirror_profile_cut_plans
                   WHERE batch_id = ANY(%s)
                   ORDER BY batch_id, id DESC""",
                (batch_ids,),
            )
            plan_rows = cur.fetchall() or []
            plans_by_batch = {int(r["batch_id"]): dict(r) for r in plan_rows if r.get("batch_id") is not None}
            plan_ids = [int(r["id"]) for r in plan_rows if r.get("id") is not None]
            seg_by_plan = {}
            if plan_ids:
                cur.execute(
                    """SELECT id, plan_id, source_stock_id, source_kind, source_length_mm, source_label, cuts_json,
                              outputs_json, waste_mm, remnant_mm, remnant_stock_id, created_at
                       FROM mirror_profile_cut_plan_segments
                       WHERE plan_id = ANY(%s)
                       ORDER BY plan_id, id""",
                    (plan_ids,),
                )
                for s in cur.fetchall() or []:
                    pid = int(s.get("plan_id") or 0)
                    if pid < 1:
                        continue
                    seg_by_plan.setdefault(pid, []).append(dict(s))
            cur.execute(
                """SELECT id, batch_id, order_id, product_index, side_key, required_mm, facade_width_mm,
                          facade_height_mm, facade_label, selected, created_at
                   FROM mirror_profile_cut_batch_items
                   WHERE batch_id = ANY(%s)
                   ORDER BY batch_id, id""",
                (batch_ids,),
            )
            items_by_batch = {}
            for it in cur.fetchall() or []:
                bid = int(it.get("batch_id") or 0)
                if bid < 1:
                    continue
                items_by_batch.setdefault(bid, []).append(dict(it))
            out = []
            for b in batches:
                bid = int(b.get("id") or 0)
                pl = plans_by_batch.get(bid)
                pid = int(pl.get("id") or 0) if pl else 0
                segs = seg_by_plan.get(pid, []) if pid else []
                out.append(
                    {
                        "batch": dict(b),
                        "plan": dict(pl) if pl else {},
                        "segments": segs,
                        "items": items_by_batch.get(bid, []),
                    }
                )
            return out


def _next_profile_piece_label_number(cur, prefix):
    cur.execute(
        "SELECT COALESCE(MAX(label_number), 0) AS n FROM mirror_profile_piece_labels WHERE label_prefix = %s",
        (str(prefix or "A")[:4],),
    )
    return int((cur.fetchone() or {}).get("n") or 0) + 1


def create_profile_piece_label(batch_id, plan_id=None, piece_kind="assembly", label_prefix="A", piece_mm=None, source_stock_id=None):
    _ensure_profile_cut_workflow_tables()
    pref = str(label_prefix or "A").strip().upper()[:4] or "A"
    with get_connection() as conn:
        with conn.cursor() as cur:
            n = _next_profile_piece_label_number(cur, pref)
            uniq = "%s%s" % (pref, n)
            qr_url = ""
            try:
                from logic.qr_utils import profile_qr_url
                qr_url = profile_qr_url(uniq)
            except Exception:
                qr_url = ""
            cur.execute(
                """INSERT INTO mirror_profile_piece_labels
                   (batch_id, plan_id, piece_kind, label_prefix, label_number, unique_number, piece_mm, source_stock_id, qr_url)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id, batch_id, plan_id, piece_kind, label_prefix, label_number, unique_number, piece_mm, source_stock_id, qr_url, created_at""",
                (
                    int(batch_id),
                    int(plan_id) if plan_id is not None else None,
                    str(piece_kind or "assembly")[:16],
                    pref,
                    int(n),
                    uniq,
                    int(piece_mm) if piece_mm is not None else None,
                    int(source_stock_id) if source_stock_id is not None else None,
                    qr_url,
                ),
            )
            return cur.fetchone() or {}


def create_profile_piece_labels_plan_autoplan_rows(batch_id, plan_id, segments, include_waste_labels=False):
    """
    Создаёт этикетки A/S (и опционально W) для всех сегментов плана одной транзакцией
    (запросы MAX по префиксам, затем INSERT), эквивалентно циклу create_profile_piece_label.
    """
    _ensure_profile_cut_workflow_tables()
    bid = int(batch_id)
    pid = int(plan_id)
    specs: List[Tuple[str, str, Any, Any]] = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        sid_raw = seg.get("source_stock_id")
        try:
            sid_int = int(sid_raw) if sid_raw is not None else None
        except (TypeError, ValueError):
            sid_int = None
        for outp in seg.get("outputs") or []:
            if not isinstance(outp, dict):
                continue
            specs.append(("assembly", "A", outp.get("piece_mm"), sid_int))
        try:
            rmm = int(seg.get("remnant_mm") or 0)
        except (TypeError, ValueError):
            rmm = 0
        if rmm >= 200:
            specs.append(("stock", "S", seg.get("remnant_mm"), sid_int))
        if include_waste_labels:
            try:
                wmm = int(seg.get("waste_mm") or 0)
            except (TypeError, ValueError):
                wmm = 0
            if wmm > 0:
                specs.append(("waste", "W", seg.get("waste_mm"), sid_int))
    if not specs:
        return []
    qr_fn = None
    try:
        from logic.qr_utils import profile_qr_url as _pqr

        qr_fn = _pqr
    except Exception:
        qr_fn = None

    out_rows = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(MAX(label_number), 0) AS n FROM mirror_profile_piece_labels WHERE label_prefix = %s",
                ("A",),
            )
            next_a = int((cur.fetchone() or {}).get("n") or 0) + 1
            cur.execute(
                "SELECT COALESCE(MAX(label_number), 0) AS n FROM mirror_profile_piece_labels WHERE label_prefix = %s",
                ("S",),
            )
            next_s = int((cur.fetchone() or {}).get("n") or 0) + 1
            next_w = 1
            if include_waste_labels:
                cur.execute(
                    "SELECT COALESCE(MAX(label_number), 0) AS n FROM mirror_profile_piece_labels WHERE label_prefix = %s",
                    ("W",),
                )
                next_w = int((cur.fetchone() or {}).get("n") or 0) + 1
            for piece_kind, pref, piece_mm, source_stock_id in specs:
                pk = str(piece_kind or "")[:16]
                pr = str(pref or "A").strip().upper()[:4] or "A"
                if pr == "A":
                    n = next_a
                elif pr == "W":
                    n = next_w
                else:
                    n = next_s
                uniq = "%s%s" % (pr, n)
                qr_url = ""
                if qr_fn:
                    try:
                        qr_url = qr_fn(uniq)
                    except Exception:
                        qr_url = ""
                cur.execute(
                    """INSERT INTO mirror_profile_piece_labels
                       (batch_id, plan_id, piece_kind, label_prefix, label_number, unique_number, piece_mm, source_stock_id, qr_url)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       RETURNING id, batch_id, plan_id, piece_kind, label_prefix, label_number, unique_number, piece_mm, source_stock_id, qr_url, created_at""",
                    (
                        bid,
                        pid,
                        pk,
                        pr,
                        int(n),
                        uniq,
                        int(piece_mm) if piece_mm is not None else None,
                        int(source_stock_id) if source_stock_id is not None else None,
                        qr_url,
                    ),
                )
                row = cur.fetchone() or {}
                if row:
                    out_rows.append(row)
                if pr == "A":
                    next_a += 1
                elif pr == "W":
                    next_w += 1
                else:
                    next_s += 1
    return out_rows


def list_profile_piece_labels_by_batch(batch_id):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, batch_id, plan_id, piece_kind, label_prefix, label_number, unique_number, piece_mm, source_stock_id, qr_url, created_at
                   FROM mirror_profile_piece_labels
                   WHERE batch_id = %s
                   ORDER BY id""",
                (int(batch_id),),
            )
            return cur.fetchall() or []


def list_profile_piece_labels_by_plan(plan_id):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, batch_id, plan_id, piece_kind, label_prefix, label_number, unique_number, piece_mm, source_stock_id, qr_url, created_at
                   FROM mirror_profile_piece_labels
                   WHERE plan_id = %s
                   ORDER BY id""",
                (int(plan_id),),
            )
            return cur.fetchall() or []


def delete_profile_piece_labels_by_batch(batch_id):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mirror_profile_piece_labels WHERE batch_id = %s", (int(batch_id),))


def get_or_create_facade_finish_label(batch_id, order_id, product_index, facade_label=""):
    """Короткий порядковый номер этикетки готового фасада (скан WEB_QR /facade/<n>)."""
    _ensure_profile_cut_workflow_tables()
    bid = int(batch_id)
    oid = int(order_id)
    pidx = int(product_index)
    fl = str(facade_label or "").strip()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, public_number, batch_id, order_id, product_index, facade_label, created_at
                FROM mirror_facade_finish_labels
                WHERE batch_id = %s AND order_id = %s AND product_index = %s AND facade_label = %s
                """,
                (bid, oid, pidx, fl),
            )
            row = cur.fetchone()
            if row:
                return dict(row)
            cur.execute("SELECT COALESCE(MAX(public_number), 0) + 1 AS n FROM mirror_facade_finish_labels")
            nxt = int((cur.fetchone() or {}).get("n") or 1)
            cur.execute(
                """
                INSERT INTO mirror_facade_finish_labels (public_number, batch_id, order_id, product_index, facade_label)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, public_number, batch_id, order_id, product_index, facade_label, created_at
                """,
                (nxt, bid, oid, pidx, fl),
            )
            return dict(cur.fetchone() or {})


def ensure_facade_finish_labels_bulk(
    entries: Iterable[Tuple[int, int, int, str]],
) -> Dict[Tuple[int, int, int, str], Dict[str, Any]]:
    """
    Одна транзакция: для каждого (batch_id, order_id, product_index, facade_label)
    возвращает строку этикетки (создаёт недостающие с последовательными public_number).
    """
    _ensure_profile_cut_workflow_tables()
    seen: Dict[Tuple[int, int, int, str], bool] = {}
    ordered_keys: List[Tuple[int, int, int, str]] = []
    for tup in entries or []:
        try:
            bid = int(tup[0])
            oid = int(tup[1])
            pidx = int(tup[2])
        except (TypeError, ValueError, IndexError):
            continue
        fl = str(tup[3] if len(tup) > 3 else "").strip()
        if bid < 1 or oid < 1 or pidx < 1 or not fl:
            continue
        key = (bid, oid, pidx, fl)
        if key in seen:
            continue
        seen[key] = True
        ordered_keys.append(key)
    if not ordered_keys:
        return {}
    batch_ids = sorted({k[0] for k in ordered_keys})
    out: Dict[Tuple[int, int, int, str], Dict[str, Any]] = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, public_number, batch_id, order_id, product_index, facade_label, created_at
                FROM mirror_facade_finish_labels
                WHERE batch_id = ANY(%s)
                """,
                (batch_ids,),
            )
            for row in cur.fetchall() or []:
                rr = dict(row)
                k = (
                    int(rr.get("batch_id") or 0),
                    int(rr.get("order_id") or 0),
                    int(rr.get("product_index") or 0),
                    str(rr.get("facade_label") or "").strip(),
                )
                if k[0] > 0 and k[1] > 0 and k[2] > 0 and k[3]:
                    out[k] = rr
            missing = [k for k in ordered_keys if k not in out]
            if missing:
                cur.execute("SELECT COALESCE(MAX(public_number), 0) AS mx FROM mirror_facade_finish_labels")
                nxt = int((cur.fetchone() or {}).get("mx") or 0) + 1
                for k in missing:
                    bid, oid, pidx, fl = k
                    cur.execute(
                        """
                        INSERT INTO mirror_facade_finish_labels (public_number, batch_id, order_id, product_index, facade_label)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id, public_number, batch_id, order_id, product_index, facade_label, created_at
                        """,
                        (nxt, bid, oid, pidx, fl),
                    )
                    row = cur.fetchone()
                    if row:
                        out[k] = dict(row)
                    nxt += 1
    return out


def get_facade_finish_label_by_public_number(public_number):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, public_number, batch_id, order_id, product_index, facade_label, created_at
                FROM mirror_facade_finish_labels
                WHERE public_number = %s
                """,
                (int(public_number),),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def count_order_layout_glass_pieces(order_id):
    """Сколько различных номеров K (крупные изделия) на картах раскроя — как в logic.pdf_export."""
    oid = int(order_id)
    if oid < 1:
        return 0
    row = get_order(oid)
    if not row:
        return 0
    k0 = row.get("k_number")
    try:
        k_base = int(k0) if k0 is not None else None
    except (TypeError, ValueError):
        k_base = None
    rows = get_cut_results(oid) or []
    layouts = [cr.get("layout") for cr in rows if isinstance(cr.get("layout"), dict)]
    try:
        from logic.pdf_export import pdf_k_display_numbers_for_order_layouts

        return len(pdf_k_display_numbers_for_order_layouts(layouts, k_base))
    except Exception:
        return 0


def find_order_ids_for_piece_k_scan(k_display, limit=80):
    """Поиск заказов, в сохранённом раскрое которых есть изделие с номером K на этикетке (как в PDF), без опоры только на mirror_orders.k_number."""
    kd = int(k_display)
    lim = max(1, min(200, int(limit or 80)))
    out = []
    seen = set()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT order_id FROM mirror_cut_results
                GROUP BY order_id
                ORDER BY MAX(id) DESC
                LIMIT 4000
                """
            )
            oids = [int(r["order_id"]) for r in (cur.fetchall() or []) if r.get("order_id")]
    for oid in oids:
        if oid in seen:
            continue
        if piece_k_display_in_order_cut_results(oid, kd):
            out.append(oid)
            seen.add(oid)
            if len(out) >= lim:
                break
    return out


def piece_k_display_in_order_cut_results(order_id, kd) -> bool:
    """Совпадает ли номер K на карте раскроя (PDF) с kd для заказа — та же логика, что logic.pdf_export."""
    oid = int(order_id)
    if oid < 1:
        return False
    row = get_order(oid)
    if not row:
        return False
    k0 = row.get("k_number")
    try:
        k_base = int(k0) if k0 is not None else None
    except (TypeError, ValueError):
        k_base = None
    rows = get_cut_results(oid) or []
    layouts = [cr.get("layout") for cr in rows if isinstance(cr.get("layout"), dict)]
    try:
        from logic.pdf_export import pdf_k_display_numbers_for_order_layouts

        return int(kd) in pdf_k_display_numbers_for_order_layouts(layouts, k_base)
    except Exception:
        return False


def find_order_ids_for_piece_k_display(k_display, limit=40):
    """Заказы, на карте раскроя которых есть изделие K k_display (нумерация как в PDF)."""
    kd = int(k_display)
    lim = max(1, min(200, int(limit or 40)))
    out = []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.k_number FROM mirror_orders o
                WHERE EXISTS (SELECT 1 FROM mirror_cut_results cr WHERE cr.order_id = o.id)
                ORDER BY o.id DESC
                LIMIT 4000
                """
            )
            rows = cur.fetchall() or []
    for r in rows:
        oid = int(r.get("id") or 0)
        if oid < 1:
            continue
        if piece_k_display_in_order_cut_results(oid, kd):
            out.append(oid)
        if len(out) >= lim:
            break
    return out


def get_profile_cut_batch_runtime(batch_id):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT batch_id, stage_no, stage_payload_json, current_step_index, updated_at
                   FROM mirror_profile_cut_batch_runtime
                   WHERE batch_id = %s""",
                (int(batch_id),),
            )
            row = cur.fetchone() or {}
            if not row:
                return {}
            payload = row.get("stage_payload_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
            row["stage_payload_json"] = payload if isinstance(payload, dict) else {}
            return row


def upsert_profile_cut_batch_runtime(batch_id, stage_no=None, stage_payload=None, current_step_index=None):
    _ensure_profile_cut_workflow_tables()
    existing = get_profile_cut_batch_runtime(int(batch_id)) or {}
    st = int(stage_no if stage_no is not None else (existing.get("stage_no") or 1))
    payload = stage_payload if isinstance(stage_payload, dict) else (existing.get("stage_payload_json") or {})
    step = int(current_step_index if current_step_index is not None else (existing.get("current_step_index") or 0))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mirror_profile_cut_batch_runtime (batch_id, stage_no, stage_payload_json, current_step_index)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (batch_id)
                DO UPDATE SET
                    stage_no = EXCLUDED.stage_no,
                    stage_payload_json = EXCLUDED.stage_payload_json,
                    current_step_index = EXCLUDED.current_step_index,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (int(batch_id), st, json.dumps(payload or {}, ensure_ascii=False), step),
            )


def insert_profile_cut_correction(batch_id, plan_id, source_stock_id, expected_mm, correction_mm, actual_mm, actor_user_id=None, actor_login="", actor_role=""):
    _ensure_profile_cut_workflow_tables()
    exp = int(expected_mm) if expected_mm is not None else None
    corr = int(correction_mm or 0)
    act = int(actual_mm) if actual_mm is not None else None
    delta = (act - exp) if (act is not None and exp is not None) else None
    delta_pct = None
    if delta is not None and exp not in (None, 0):
        try:
            delta_pct = float(delta) * 100.0 / float(exp)
        except Exception:
            delta_pct = None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_profile_cut_corrections
                   (batch_id, plan_id, source_stock_id, expected_mm, correction_mm, actual_mm, delta_mm, delta_pct, actor_user_id, actor_login, actor_role)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    int(batch_id),
                    int(plan_id) if plan_id is not None else None,
                    int(source_stock_id) if source_stock_id is not None else None,
                    exp,
                    corr,
                    act,
                    delta,
                    delta_pct,
                    int(actor_user_id) if actor_user_id is not None else None,
                    str(actor_login or "")[:128],
                    str(actor_role or "")[:32],
                ),
            )
            row = cur.fetchone() or {}
            cid = row.get("id")
    try:
        add_profile_cut_event(
            stock_id=(int(source_stock_id) if source_stock_id is not None else None),
            order_id=None,
            batch_id=(int(batch_id) if batch_id is not None else None),
            event_type="correction_applied",
            reason_text="Коррекция после реза профиля",
            actor_user_id=(int(actor_user_id) if actor_user_id is not None else None),
            actor_login=str(actor_login or ""),
            actor_role=str(actor_role or ""),
            payload_json={
                "plan_id": (int(plan_id) if plan_id is not None else None),
                "expected_mm": exp,
                "correction_mm": corr,
                "actual_mm": act,
                "delta_mm": delta,
                "delta_pct": float(delta_pct) if delta_pct is not None else None,
            },
        )
    except Exception:
        pass
    return cid


def list_profile_cut_corrections(batch_id=None, limit=1000):
    _ensure_profile_cut_workflow_tables()
    lim = max(1, min(10000, int(limit)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            q = """SELECT id, batch_id, plan_id, source_stock_id, expected_mm, correction_mm, actual_mm, delta_mm, delta_pct,
                          actor_user_id, actor_login, actor_role, created_at
                   FROM mirror_profile_cut_corrections
                   WHERE 1=1"""
            params = []
            if batch_id is not None:
                q += " AND batch_id = %s"
                params.append(int(batch_id))
            q += " ORDER BY id DESC LIMIT %s"
            params.append(lim)
            cur.execute(q, params)
            return cur.fetchall() or []


def insert_profile_cut_rejection(batch_id, plan_id, source_stock_id, action_type, reason_text="", surviving_mm=None, actor_user_id=None, actor_login="", actor_role=""):
    _ensure_profile_cut_workflow_tables()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_profile_cut_rejections
                   (batch_id, plan_id, source_stock_id, action_type, reason_text, surviving_mm, actor_user_id, actor_login, actor_role)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    int(batch_id),
                    int(plan_id) if plan_id is not None else None,
                    int(source_stock_id) if source_stock_id is not None else None,
                    str(action_type or "lost")[:16],
                    str(reason_text or ""),
                    int(surviving_mm) if surviving_mm is not None else None,
                    int(actor_user_id) if actor_user_id is not None else None,
                    str(actor_login or "")[:128],
                    str(actor_role or "")[:32],
                ),
            )
            row = cur.fetchone() or {}
            rid = row.get("id")
    try:
        at = str(action_type or "lost").strip().lower()
        ev_type = "rejection:%s" % ("damaged" if at == "damaged" else "lost")
        add_profile_cut_event(
            stock_id=(int(source_stock_id) if source_stock_id is not None else None),
            order_id=None,
            batch_id=(int(batch_id) if batch_id is not None else None),
            event_type=ev_type,
            reason_text=str(reason_text or ""),
            actor_user_id=(int(actor_user_id) if actor_user_id is not None else None),
            actor_login=str(actor_login or ""),
            actor_role=str(actor_role or ""),
            payload_json={
                "plan_id": (int(plan_id) if plan_id is not None else None),
                "surviving_mm": (int(surviving_mm) if surviving_mm is not None else None),
                "rejection_id": rid,
            },
        )
    except Exception:
        pass
    return rid


def list_profile_cut_rejections(batch_id=None, limit=1000):
    _ensure_profile_cut_workflow_tables()
    lim = max(1, min(10000, int(limit)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            q = """SELECT id, batch_id, plan_id, source_stock_id, action_type, reason_text, surviving_mm,
                          actor_user_id, actor_login, actor_role, created_at
                   FROM mirror_profile_cut_rejections
                   WHERE 1=1"""
            params = []
            if batch_id is not None:
                q += " AND batch_id = %s"
                params.append(int(batch_id))
            q += " ORDER BY id DESC LIMIT %s"
            params.append(lim)
            cur.execute(q, params)
            return cur.fetchall() or []


def insert_profile_stock_usage(
    consumed_stock_id,
    profile_ref_id,
    series,
    name,
    color,
    side,
    required_mm,
    remnant_mm,
    client_name,
    facade_width_mm,
    facade_height_mm,
):
    """Запись списания профиля со склада при расчёте фасада (для истории)."""
    _ensure_profile_stock_usage_table()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_profile_stock_usage
                   (consumed_stock_id, profile_ref_id, series, name, color, side,
                    required_mm, remnant_mm, client_name, facade_width_mm, facade_height_mm)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    int(consumed_stock_id) if consumed_stock_id is not None else None,
                    int(profile_ref_id) if profile_ref_id is not None else None,
                    str(series or '')[:255],
                    str(name or '')[:255],
                    str(color or '')[:255],
                    str(side or '')[:16],
                    int(required_mm) if required_mm is not None else None,
                    int(remnant_mm) if remnant_mm is not None else None,
                    str(client_name or '')[:512],
                    int(facade_width_mm) if facade_width_mm is not None else None,
                    int(facade_height_mm) if facade_height_mm is not None else None,
                )
            )


def get_profile_stock_usage_by_ref(profile_ref_id, limit=200):
    """История списаний по каталожному id профиля (ref_id)."""
    _ensure_profile_stock_usage_table()
    lim = max(1, min(500, int(limit)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, consumed_stock_id, profile_ref_id, series, name, color, side,
                          required_mm, remnant_mm, client_name, facade_width_mm, facade_height_mm, used_at
                   FROM mirror_profile_stock_usage
                   WHERE profile_ref_id = %s
                   ORDER BY used_at DESC
                   LIMIT %s""",
                (int(profile_ref_id), lim),
            )
            return cur.fetchall()


def peek_profile_stock_for_facade_cut(profile_ref_id, required_mm):
    """
    Только чтение: первый подходящий ряд склада по тем же правилам, что reserve_profile_from_stock,
    без списания. Для подсказки в UI до фактического реза в производстве.
    """
    _ensure_profile_stock_tables()
    req = int(max(1, int(required_mm or 0)))
    with get_connection() as conn:
        with conn.cursor() as cur:
            for rem in (True, False):
                cur.execute(
                    """SELECT id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant
                       FROM mirror_profile_stock
                       WHERE item_type='profile' AND ref_id=%s AND is_remnant=%s AND quantity > 0 AND length_mm IS NOT NULL
                       ORDER BY length_mm ASC""",
                    (int(profile_ref_id), rem),
                )
                rows = cur.fetchall() or []
                for r in rows:
                    length = int(r.get("length_mm") or 0)
                    if length < req:
                        continue
                    rest = length - req
                    if rest < 0:
                        continue
                    if rest > 0 and rest < 200 and length > 0 and (float(req) / float(length)) > 0.10:
                        continue
                    return dict(r)
    return None


def reserve_profile_from_stock(profile_ref_id, required_mm, order_id=None):
    """
    Зарезервировать профиль со склада по правилам:
    1) пробуем остатки (is_remnant=TRUE) минимальной длины, которые подходят;
    2) если нет — пробуем целые (is_remnant=FALSE) минимальной длины;
    Условия пригодности: длина >= required_mm и (остаток >= 200 мм как у реза в batch, или срез <= 10% длины).
    Новый остаток на складе создаётся при rest >= 200 мм (согласовано с consume_profile_stock_row).
    Пишет mirror_profile_remnant_history для исходной позиции и нового остатка.
    Возвращает dict с выбранной записью и остатком, либо None.
    """
    _ensure_profile_stock_tables()
    req = int(max(1, required_mm))
    oid_hist = int(order_id) if order_id is not None else None
    out = None
    with get_connection() as conn:
        with conn.cursor() as cur:
            for rem in (True, False):
                cur.execute(
                    """SELECT id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant
                       FROM mirror_profile_stock
                       WHERE item_type='profile' AND ref_id=%s AND is_remnant=%s AND quantity > 0 AND length_mm IS NOT NULL
                       ORDER BY length_mm ASC""",
                    (int(profile_ref_id), rem)
                )
                rows = cur.fetchall() or []
                for r in rows:
                    length = int(r.get('length_mm') or 0)
                    if length < req:
                        continue
                    rest = length - req
                    if rest < 0:
                        continue
                    # как раньше: можно взять брус, если остаток >= 200 мм или срез «мелкий» (≤10% длины); rest==0 — ровно по длине
                    if rest > 0 and rest < 200 and length > 0 and (float(req) / float(length)) > 0.10:
                        continue
                    source_stock_id = int(r.get("id"))
                    # резерв: уменьшаем quantity, при необходимости создаём новый остаток
                    cur.execute(
                        "UPDATE mirror_profile_stock SET quantity = quantity - 1 WHERE id = %s",
                        (source_stock_id,),
                    )
                    cur.execute(
                        "DELETE FROM mirror_profile_stock WHERE id = %s AND quantity <= 0",
                        (source_stock_id,),
                    )
                    new_remnant_stock_id = None
                    if rest >= 200:
                        cur.execute(
                            """INSERT INTO mirror_profile_stock
                               (item_type, ref_id, series, name, color, length_mm, quantity, is_remnant)
                               VALUES ('profile', %s, %s, %s, %s, %s, 1, TRUE)
                               RETURNING id""",
                            (
                                int(r.get('ref_id')) if r.get('ref_id') is not None else None,
                                str(r.get('series') or ''),
                                str(r.get('name') or ''),
                                str(r.get('color') or ''),
                                int(rest),
                            )
                        )
                        nrow = cur.fetchone() or {}
                        try:
                            new_remnant_stock_id = int(nrow.get("id")) if nrow.get("id") is not None else None
                        except (TypeError, ValueError):
                            new_remnant_stock_id = None
                    elif rest > 0:
                        try:
                            add_profile_waste(
                                source_stock_id,
                                r.get('ref_id'),
                                r.get('series'),
                                r.get('name'),
                                r.get('color'),
                                rest,
                                oid_hist,
                                "Неделовой остаток профиля после реза",
                            )
                        except Exception:
                            pass
                    out = {
                        'source': dict(r),
                        'required_mm': req,
                        'rest_mm': rest,
                        'new_remnant_stock_id': new_remnant_stock_id,
                    }
                    break
                if out:
                    break
    if out:
        try:
            src = out.get("source") or {}
            sid = int(src.get("id") or 0)
            nr = out.get("new_remnant_stock_id")
            rest = int(out.get("rest_mm") or 0)
            if sid:
                add_profile_remnant_history(
                    sid,
                    oid_hist,
                    "reserve_source_consumed",
                    {
                        "required_mm": out.get("required_mm"),
                        "rest_mm": rest,
                        "new_remnant_stock_id": nr,
                        "profile_ref_id": int(profile_ref_id),
                    },
                )
            if nr:
                add_profile_remnant_history(
                    int(nr),
                    oid_hist,
                    "remnant_from_reserve",
                    {"source_stock_id": sid, "rest_mm": rest, "profile_ref_id": int(profile_ref_id)},
                )
        except Exception:
            pass
    return out


def consume_profile_stock_row(stock_id, consumed_mm, remnant_mm=None, order_id=None, reason="profile_cut_batch", history_extra=None):
    """Consume one stock row unit and optionally create remnant/waste.
    history_extra — доп. поля в details_json для события remnant_from_profile_cut (партия, резчик, состав)."""
    _ensure_profile_stock_tables()
    sid = int(stock_id)
    cons = int(consumed_mm or 0)
    rem = int(remnant_mm) if remnant_mm is not None else None
    he = dict(history_extra) if isinstance(history_extra, dict) else {}
    stock_usage_record = he.pop("stock_usage_record", None)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant
                   FROM mirror_profile_stock WHERE id = %s""",
                (sid,),
            )
            row = cur.fetchone() or {}
            if not row:
                return {"ok": False, "error": "stock_not_found"}
            try:
                src_det = {
                    "consumed_mm": cons,
                    "remnant_mm_after_cut": rem,
                    "origin_length_mm": int(row.get("length_mm") or 0),
                    "reason": str(reason or ""),
                    "ref_id": row.get("ref_id"),
                    "profile_name": row.get("name"),
                    "profile_color": row.get("color"),
                    "series": row.get("series"),
                }
                if he:
                    src_det.update(he)
                add_profile_remnant_history(
                    sid,
                    int(order_id) if order_id else None,
                    "profile_bar_consumed",
                    src_det,
                )
            except Exception:
                pass
            cur.execute("UPDATE mirror_profile_stock SET quantity = quantity - 1 WHERE id = %s", (sid,))
            cur.execute("SELECT quantity FROM mirror_profile_stock WHERE id = %s", (sid,))
            q_after = cur.fetchone() or {}
            qty_now = int(q_after.get("quantity") or 0)
            if qty_now <= 0:
                try:
                    cur.execute(
                        """INSERT INTO mirror_deleted_profile_stock
                           (stock_id, item_type, ref_id, series, name, color, length_mm, quantity, is_remnant)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            row.get("id"),
                            row.get("item_type"),
                            row.get("ref_id"),
                            row.get("series"),
                            row.get("name"),
                            row.get("color"),
                            row.get("length_mm"),
                            row.get("quantity"),
                            row.get("is_remnant"),
                        ),
                    )
                except Exception:
                    pass
            cur.execute("DELETE FROM mirror_profile_stock WHERE id = %s AND quantity <= 0", (sid,))
            new_rem_id = None
            if rem is not None and rem >= 200:
                cur.execute(
                    """INSERT INTO mirror_profile_stock
                       (item_type, ref_id, series, name, color, length_mm, quantity, is_remnant)
                       VALUES ('profile', %s, %s, %s, %s, %s, 1, TRUE)
                       RETURNING id""",
                    (
                        int(row.get("ref_id")) if row.get("ref_id") is not None else None,
                        str(row.get("series") or ""),
                        str(row.get("name") or ""),
                        str(row.get("color") or ""),
                        int(rem),
                    ),
                )
                n = cur.fetchone() or {}
                new_rem_id = n.get("id")
                try:
                    det = {
                        "source_stock_id": sid,
                        "consumed_mm": cons,
                        "rest_mm": rem,
                        "parent_length_mm": int(row.get("length_mm") or 0),
                        "reason": str(reason or ""),
                    }
                    if he:
                        det.update(he)
                    add_profile_remnant_history(
                        int(new_rem_id),
                        int(order_id) if order_id else None,
                        "remnant_from_profile_cut",
                        det,
                    )
                except Exception:
                    pass
            elif rem is not None and rem > 0:
                try:
                    add_profile_waste(
                        sid,
                        row.get("ref_id"),
                        row.get("series"),
                        row.get("name"),
                        row.get("color"),
                        rem,
                        int(order_id) if order_id else None,
                        reason,
                    )
                except Exception:
                    pass
            try:
                if isinstance(stock_usage_record, dict) and stock_usage_record.get("enabled"):
                    insert_profile_stock_usage(
                        sid,
                        int(row.get("ref_id")) if row.get("ref_id") is not None else None,
                        str(row.get("series") or ""),
                        str(row.get("name") or ""),
                        str(row.get("color") or ""),
                        str(stock_usage_record.get("side") or "batch")[:16],
                        int(stock_usage_record.get("required_mm") if stock_usage_record.get("required_mm") is not None else cons),
                        rem,
                        str(stock_usage_record.get("client_name") or "")[:512],
                        int(stock_usage_record["facade_width_mm"]) if stock_usage_record.get("facade_width_mm") is not None else None,
                        int(stock_usage_record["facade_height_mm"]) if stock_usage_record.get("facade_height_mm") is not None else None,
                    )
            except Exception:
                pass
            return {"ok": True, "new_remnant_stock_id": new_rem_id}


# --- Full sheets ---
def get_all_full_sheets():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, height_mm, width_mm, thickness_mm, arrival_date, supplier, cost, warehouse_number, quantity, comment
                   FROM mirror_full_sheets ORDER BY name"""
            )
            return cur.fetchall()


def get_full_sheets_by_name_prefix(prefix):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, height_mm, width_mm, thickness_mm, arrival_date, supplier, cost, warehouse_number, quantity, comment
                   FROM mirror_full_sheets WHERE name ILIKE %s AND quantity > 0 ORDER BY name""",
                (prefix + '%',)
            )
            return cur.fetchall()


def get_full_sheets_by_material(material_name):
    """Exact match by material name (for cutting algorithm). Only rows with quantity > 0."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, height_mm, width_mm, thickness_mm, quantity FROM mirror_full_sheets
                   WHERE name = %s AND quantity > 0 ORDER BY height_mm * width_mm DESC""",
                (material_name,)
            )
            return cur.fetchall()


def get_full_sheets_by_material_and_thickness(material_name, thickness_mm):
    """Листы по материалу и толщине (для выбора конкретного листа). quantity > 0."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, height_mm, width_mm, thickness_mm, quantity FROM mirror_full_sheets
                   WHERE name = %s AND thickness_mm = %s AND quantity > 0 ORDER BY height_mm * width_mm DESC""",
                (material_name, int(thickness_mm))
            )
            return cur.fetchall()


def get_full_sheet_by_id(sheet_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, height_mm, width_mm, thickness_mm, arrival_date, supplier, cost, warehouse_number, quantity, comment
                   FROM mirror_full_sheets WHERE id = %s""",
                (sheet_id,)
            )
            return cur.fetchone()


def insert_full_sheet(name, height_mm, width_mm, arrival_date=None, supplier=None, cost=0, warehouse_number=None, quantity=1, comment=None, thickness_mm=4):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_full_sheets (name, height_mm, width_mm, thickness_mm, arrival_date, supplier, cost, warehouse_number, quantity, comment)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (name, int(height_mm), int(width_mm), int(thickness_mm) if thickness_mm is not None else 4, arrival_date, supplier, float(cost) if cost is not None else 0, warehouse_number, int(quantity) if quantity is not None else 1, comment)
            )
            return cur.fetchone()['id']


def decrement_full_sheet_quantity(sheet_id):
    """Уменьшить количество на 1 (при использовании листа). Не удаляет строку."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mirror_full_sheets SET quantity = GREATEST(0, quantity - 1) WHERE id = %s",
                (sheet_id,)
            )


def increment_full_sheet_quantity(sheet_id):
    """Вернуть лист на склад (+1 к quantity), напр. при отмене чернового раскроя."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mirror_full_sheets SET quantity = quantity + 1 WHERE id = %s",
                (int(sheet_id),),
            )


def delete_full_sheet(sheet_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mirror_full_sheets WHERE id = %s", (sheet_id,))


def update_full_sheet(sheet_id, name, height_mm, width_mm, arrival_date=None, supplier=None, cost=None, warehouse_number=None, quantity=None, comment=None, thickness_mm=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            if thickness_mm is not None:
                cur.execute(
                    """UPDATE mirror_full_sheets SET name=%s, height_mm=%s, width_mm=%s, thickness_mm=%s, arrival_date=%s, supplier=%s, cost=%s, warehouse_number=%s, quantity=%s, comment=%s WHERE id=%s""",
                    (name, height_mm, width_mm, int(thickness_mm), arrival_date, supplier, cost, warehouse_number, quantity, comment, sheet_id)
                )
            else:
                cur.execute(
                    """UPDATE mirror_full_sheets SET name=%s, height_mm=%s, width_mm=%s, arrival_date=%s, supplier=%s, cost=%s, warehouse_number=%s, quantity=%s, comment=%s WHERE id=%s""",
                    (name, height_mm, width_mm, arrival_date, supplier, cost, warehouse_number, quantity, comment, sheet_id)
                )


# --- Remnants ---
def get_next_label_number():
    """Следующий порядковый номер этикетки (1, 2, 3...) — без повторений. Счётчик синхронизируется с существующими номерами в БД."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            _ensure_label_counter_above_existing(cur)
            cur.execute("UPDATE mirror_label_counter SET value = value + 1 RETURNING value")
            row = cur.fetchone()
            return (row['value'] if row else 0) or 1


def peek_next_label_numbers(count):
    """Следующие count номеров, как при count вызовах get_next_label_number(), без изменения счётчика.
    Один счётчик для стекла и профиля (mirror_label_counter)."""
    n = int(count or 0)
    if n < 1:
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            _ensure_label_counter_above_existing(cur)
            cur.execute("SELECT value FROM mirror_label_counter LIMIT 1")
            row = cur.fetchone()
            base = int((row.get("value") if row else 0) or 0)
            return [base + i for i in range(1, n + 1)]


def get_next_k_number():
    """Следующий номер продукта для клиента (K1, K2, K3...)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE mirror_k_counter SET value = value + 1 RETURNING value")
            row = cur.fetchone()
            return (row['value'] if row else 0) or 1


def allocate_k_range(count: int) -> int:
    """Забронировать диапазон K-номеров длиной count. Возвращает стартовый номер (Kstart)."""
    n = int(count or 0)
    if n < 1:
        n = 1
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE mirror_k_counter SET value = value + %s RETURNING value", (n,))
            row = cur.fetchone() or {}
            end_val = int((row.get("value") or 0) or 1)
            return max(1, end_val - n + 1)


def ensure_order_k_number_reserved(order_id: int, piece_count: int = 1) -> int:
    """Гарантировать k_number у заказа, выделяя диапазон под все изделия (сквозные K на схеме/этикетках)."""
    oid = int(order_id)
    if oid < 1:
        return 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT k_number FROM mirror_orders WHERE id = %s", (oid,))
            row = cur.fetchone() or {}
            k_num = row.get("k_number")
            if k_num is not None:
                try:
                    return int(k_num)
                except (TypeError, ValueError):
                    pass
            start = allocate_k_range(int(piece_count or 1))
            cur.execute("UPDATE mirror_orders SET k_number = %s WHERE id = %s", (int(start), oid))
            return int(start)


def get_all_remnants():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, height_mm, width_mm, thickness_mm, unique_number, qr_url, created_at, label_number, reserved_for_cut_order_id FROM mirror_remnants ORDER BY created_at DESC"
            )
            return cur.fetchall()


def get_remnants_by_material(name):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, height_mm, width_mm, thickness_mm, unique_number, qr_url, label_number, reserved_for_cut_order_id FROM mirror_remnants WHERE name = %s ORDER BY height_mm * width_mm DESC",
                (name,)
            )
            return cur.fetchall()


def get_remnants_by_material_and_thickness(name, thickness_mm):
    """Остатки по материалу и толщине (для выбора конкретного листа)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, height_mm, width_mm, thickness_mm, unique_number, qr_url, label_number, reserved_for_cut_order_id FROM mirror_remnants WHERE name = %s AND thickness_mm = %s ORDER BY height_mm * width_mm DESC",
                (name, int(thickness_mm))
            )
            return cur.fetchall()


def get_remnant_by_unique_number(unique_number):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, height_mm, width_mm, thickness_mm, unique_number, qr_url, created_at, label_number, reserved_for_cut_order_id FROM mirror_remnants WHERE unique_number = %s",
                (unique_number,)
            )
            return cur.fetchone()


def get_remnant_by_id(remnant_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, height_mm, width_mm, thickness_mm, unique_number, qr_url, created_at, label_number, reserved_for_cut_order_id FROM mirror_remnants WHERE id = %s",
                (remnant_id,)
            )
            return cur.fetchone()


def _ensure_label_counter_above_existing(cur):
    """Поднять счётчик этикеток выше всех существующих numeric unique_number и label_number.
       Если в mirror_label_counter нет строк — вставить одну (иначе UPDATE ничего не сделает и всегда вернётся 1)."""
    cur.execute("""
        SELECT COALESCE(MAX(v), 0)::bigint AS m FROM (
            SELECT label_number::bigint AS v FROM mirror_remnants WHERE label_number IS NOT NULL
            UNION ALL
            SELECT unique_number::bigint AS v FROM mirror_remnants WHERE unique_number ~ '^[0-9]+$'
        ) t
    """)
    row = cur.fetchone()
    max_used = int((row['m'] if row and row['m'] is not None else 0) or 0)
    cur.execute(
        "INSERT INTO mirror_label_counter (value) SELECT %s WHERE NOT EXISTS (SELECT 1 FROM mirror_label_counter LIMIT 1)",
        (max_used,)
    )
    cur.execute("UPDATE mirror_label_counter SET value = GREATEST(value, %s) RETURNING value", (max_used,))
    cur.fetchone()

def insert_remnant(name, height_mm, width_mm, unique_number, qr_url=None, thickness_mm=4, label_number=None):
    """Добавить остаток. unique_number и qr_url задаёт вызывающий код.
       Если передан label_number — он используется как номер этикетки (счётчик не увеличивается).
       Иначе счётчик синхронизируется и увеличивается, значение записывается в label_number."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            if label_number is not None:
                label_num = int(label_number)
                unum = unique_number
                url = qr_url
            else:
                _ensure_label_counter_above_existing(cur)
                cur.execute("UPDATE mirror_label_counter SET value = value + 1 RETURNING value")
                row = cur.fetchone()
                label_num = (row['value'] if row else 0) or 1
                unum = str(label_num)
                try:
                    from logic.qr_utils import remnant_qr_url
                    url = remnant_qr_url(unum)
                except Exception:
                    url = (qr_url or '').rstrip('/') + '/' + unum
            cur.execute(
                "INSERT INTO mirror_remnants (name, height_mm, width_mm, thickness_mm, unique_number, qr_url, label_number) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (name, height_mm, width_mm, int(thickness_mm) if thickness_mm is not None else 4, unum, url, label_num)
            )
            return cur.fetchone()['id']


def ensure_remnant_label_number(remnant_id):
    """Если у остатка нет label_number — присвоить следующий порядковый номер. Возвращает номер."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT label_number FROM mirror_remnants WHERE id = %s", (remnant_id,))
            row = cur.fetchone()
            if not row:
                return None
            if row.get('label_number') is not None:
                return row['label_number']
            cur.execute("UPDATE mirror_label_counter SET value = value + 1 RETURNING value")
            r2 = cur.fetchone()
            num = (r2['value'] if r2 else 0) or 1
            cur.execute("UPDATE mirror_remnants SET label_number = %s WHERE id = %s", (num, remnant_id))
            return num


def delete_remnant(remnant_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mirror_remnants WHERE id = %s", (remnant_id,))


def insert_deleted_remnant_archive(
    name,
    height_mm,
    width_mm,
    thickness_mm,
    unique_number=None,
    label_number=None,
    original_remnant_id=None,
    created_in_cut_archive_id=None,
    deleted_by_login=None,
    deleted_by_display=None,
):
    """Записать удалённый остаток в архив (таблица mirror_deleted_remnants). Вызывать перед delete_remnant. original_remnant_id — id в mirror_remnants до удаления. created_in_cut_archive_id — id архива реза, в котором этот остаток был создан (для отображения истории)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_deleted_remnants (name, height_mm, width_mm, thickness_mm, unique_number, label_number, original_remnant_id, created_in_cut_archive_id, deleted_by_login, deleted_by_display)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    name,
                    int(height_mm),
                    int(width_mm),
                    int(thickness_mm) if thickness_mm is not None else 4,
                    unique_number,
                    label_number,
                    original_remnant_id,
                    created_in_cut_archive_id,
                    str(deleted_by_login or "")[:128] or None,
                    str(deleted_by_display or "")[:255] or None,
                ),
            )


def get_deleted_remnants(limit=500):
    """Список записей архива удалённых остатков (для просмотра). По убыванию даты удаления."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, height_mm, width_mm, thickness_mm, unique_number, label_number, deleted_at, original_remnant_id, created_in_cut_archive_id,
                          deleted_by_login, deleted_by_display
                   FROM mirror_deleted_remnants ORDER BY deleted_at DESC LIMIT %s""",
                (int(limit),)
            )
            return cur.fetchall()


def get_deleted_remnant_by_unique_number(unique_id):
    """Найти запись в архиве удалённых остатков по unique_number (для страницы «полностью использован»)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, height_mm, width_mm, thickness_mm, unique_number, label_number, deleted_at, original_remnant_id, created_in_cut_archive_id,
                          deleted_by_login, deleted_by_display
                   FROM mirror_deleted_remnants WHERE unique_number = %s ORDER BY deleted_at DESC LIMIT 1""",
                (unique_id,)
            )
            return cur.fetchone()


def delete_remnant_and_archive(remnant_id, deleted_by_login=None, deleted_by_display=None):
    """Скопировать остаток в архив удалённых, затем удалить из mirror_remnants. Возвращает True при успехе."""
    rem = get_remnant_by_id(remnant_id)
    if not rem:
        return False
    ro = rem.get("reserved_for_cut_order_id")
    if ro is not None:
        try:
            ro_i = int(ro)
        except (TypeError, ValueError):
            ro_i = None
        if ro_i:
            raise ValueError(
                "Остаток зарезервирован под раскрой заказа №%s. Сначала отмените или измените раскрой этого заказа."
                % ro_i
            )
    rid = int(remnant_id)
    insert_deleted_remnant_archive(
        rem.get('name') or '',
        rem.get('height_mm') or 0,
        rem.get('width_mm') or 0,
        rem.get('thickness_mm'),
        rem.get('unique_number'),
        rem.get('label_number'),
        original_remnant_id=rid,
        deleted_by_login=deleted_by_login,
        deleted_by_display=deleted_by_display,
    )
    delete_remnant(remnant_id)
    return True


def update_remnant(remnant_id, name, height_mm, width_mm, thickness_mm=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            if thickness_mm is not None:
                cur.execute(
                    "UPDATE mirror_remnants SET name=%s, height_mm=%s, width_mm=%s, thickness_mm=%s WHERE id=%s",
                    (name, height_mm, width_mm, int(thickness_mm), remnant_id)
                )
            else:
                cur.execute(
                    "UPDATE mirror_remnants SET name=%s, height_mm=%s, width_mm=%s WHERE id=%s",
                    (name, height_mm, width_mm, remnant_id)
                )


# --- Business waste threshold (по паре материал + толщина) ---
def get_all_thresholds():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, thickness_mm, min_height_mm, min_width_mm FROM mirror_business_waste_threshold ORDER BY name, thickness_mm"
            )
            return cur.fetchall()


def get_allowed_sheet_material_names():
    """Список наименований материалов для целых листов (из порогов отходов — только они разрешены при добавлении листа)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT name FROM mirror_business_waste_threshold ORDER BY name"
            )
            return [row['name'] for row in cur.fetchall()]


def get_threshold_for_material(name, thickness_mm=None):
    """Порог для материала и толщины. Если thickness_mm не указан — возвращается первая подходящая запись по name."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            if thickness_mm is not None:
                cur.execute(
                    "SELECT min_height_mm, min_width_mm FROM mirror_business_waste_threshold WHERE name = %s AND thickness_mm = %s",
                    (name, int(thickness_mm))
                )
            else:
                cur.execute(
                    "SELECT min_height_mm, min_width_mm FROM mirror_business_waste_threshold WHERE name = %s ORDER BY thickness_mm LIMIT 1",
                    (name,)
                )
            return cur.fetchone()


def insert_threshold(name, min_height_mm, min_width_mm, thickness_mm=4):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mirror_business_waste_threshold (name, thickness_mm, min_height_mm, min_width_mm) VALUES (%s, %s, %s, %s) RETURNING id",
                (name, int(thickness_mm) if thickness_mm is not None else 4, min_height_mm, min_width_mm)
            )
            return cur.fetchone()['id']


def update_threshold(threshold_id, name, min_height_mm, min_width_mm, thickness_mm=4):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mirror_business_waste_threshold SET name=%s, thickness_mm=%s, min_height_mm=%s, min_width_mm=%s WHERE id=%s",
                (name, int(thickness_mm) if thickness_mm is not None else 4, min_height_mm, min_width_mm, threshold_id)
            )


def delete_threshold(threshold_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mirror_business_waste_threshold WHERE id = %s", (threshold_id,))


# --- Clients ---
CLIENT_SELECT = """id, client_type, name, inn, kpp, okpo, ogrn, registration,
    first_name, last_name, passport_series, passport_number, birth_date, gender,
    phone, email, legal_address, actual_address, source, notes, registration_date, pricing_tier"""


def _client_columns():
    return CLIENT_SELECT.replace('\n', ' ').strip()


_CLIENTS_CACHE_TTL_SEC = 15.0
_clients_cache_rows = None
_clients_cache_ts = 0.0
_ORDERS_CACHE_TTL_SEC = 5.0
_orders_cache_rows = None
_orders_cache_ts = 0.0
_orders_cache_limit = None


def _invalidate_clients_cache() -> None:
    global _clients_cache_rows, _clients_cache_ts
    _clients_cache_rows = None
    _clients_cache_ts = 0.0


def _invalidate_orders_cache() -> None:
    global _orders_cache_rows, _orders_cache_ts, _orders_cache_limit
    _orders_cache_rows = None
    _orders_cache_ts = 0.0
    _orders_cache_limit = None


def _get_orders_cached(limit=None, force: bool = False):
    global _orders_cache_rows, _orders_cache_ts, _orders_cache_limit
    lim = int(limit) if limit is not None else None
    now = time.time()
    if (
        (not force)
        and _orders_cache_rows is not None
        and _orders_cache_limit == lim
        and (now - _orders_cache_ts) <= _ORDERS_CACHE_TTL_SEC
    ):
        return list(_orders_cache_rows)
    with get_connection() as conn:
        with conn.cursor() as cur:
            sql = (
                """
                SELECT o.id, o.client_id, o.created_at, o.status, o.accepted_at, o.notes, o.k_number,
                       COALESCE(c.name, o.client_name, '') AS client_name,
                       o.order_kind, o.blocks_calc_json,
                       o.created_by_user_id, o.created_by_login, o.created_by_role,
                       u.name AS creator_name, u.surname AS creator_surname
                FROM mirror_orders o
                LEFT JOIN mirror_clients c ON c.id = o.client_id
                LEFT JOIN main_users u ON u.id = o.created_by_user_id
                ORDER BY o.created_at DESC
                """
            )
            if lim is not None and lim > 0:
                cur.execute(sql + " LIMIT %s", (lim,))
            else:
                cur.execute(sql)
            rows = cur.fetchall() or []
    _orders_cache_rows = list(rows)
    _orders_cache_ts = now
    _orders_cache_limit = lim
    return list(rows)


def _get_clients_cached(force: bool = False):
    global _clients_cache_rows, _clients_cache_ts
    now = time.time()
    if (not force) and _clients_cache_rows is not None and (now - _clients_cache_ts) <= _CLIENTS_CACHE_TTL_SEC:
        return list(_clients_cache_rows)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT " + _client_columns() + " FROM mirror_clients ORDER BY name")
            rows = cur.fetchall() or []
    _clients_cache_rows = list(rows)
    _clients_cache_ts = now
    return list(rows)


def get_all_clients():
    return _get_clients_cached()


def get_clients_by_prefix(prefix):
    pref = (prefix or '').strip().lower()
    if not pref:
        return _get_clients_cached()
    return [r for r in _get_clients_cached() if (r.get('name') or '').strip().lower().startswith(pref)]


def get_clients_search(query):
    """Поиск клиентов: имя/примечания (ILIKE), при ≥3 цифрах в запросе — также телефон и ИНН (только цифры)."""
    if not (query or '').strip():
        return []
    raw = (query or '').strip()
    q_digits = ''.join(c for c in raw if c.isdigit())
    needle = raw.lower()
    out = []
    for r in _get_clients_cached():
        nm = (r.get('name') or '').lower()
        notes = (r.get('notes') or '').lower()
        inn = (r.get('inn') or '').lower()
        phone_digits = ''.join(ch for ch in str(r.get('phone') or '') if ch.isdigit())
        if (needle in nm) or (needle in notes) or (needle in inn):
            out.append(r)
            continue
        if len(q_digits) >= 3:
            inn_digits = ''.join(ch for ch in str(r.get('inn') or '') if ch.isdigit())
            if (q_digits in phone_digits) or (q_digits in inn_digits):
                out.append(r)
    return out


def get_client_by_id(client_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT " + _client_columns() + " FROM mirror_clients WHERE id = %s",
                (client_id,)
            )
            return cur.fetchone()


def get_client_id_by_name(name):
    """Найти id клиента по имени (trim с обеих сторон — как в карточке и в поле ввода)."""
    nm = (name or "").strip()
    if not nm:
        return None
    nm_l = nm.lower()
    found = None
    for row in _get_clients_cached():
        if (row.get("name") or "").strip().lower() == nm_l:
            found = row
    return found["id"] if found else None


def _markup_percent_from_pricing_tier(tier):
    t = str(tier or "").strip().lower()
    if t == "b2c30":
        return 30
    if t == "b2c50":
        return 50
    return 0


def quick_estimate_meta_from_client_id(client_id):
    """Поля для строки быстрого просчёта из карточки клиента справочника."""
    try:
        cid = int(client_id)
    except (TypeError, ValueError):
        return None
    row = get_client_by_id(cid)
    if not row:
        return None
    cname = (_client_display_name(row) or (row.get("name") or "")).strip()
    return {
        "client_id": cid,
        "quick_client_id": None,
        "client_name": cname,
        "markup_percent": _markup_percent_from_pricing_tier(row.get("pricing_tier")),
        "lead_source": (row.get("source") or "").strip()[:64],
        "phone": (row.get("phone") or "").strip(),
        "extra_contact": (row.get("email") or "").strip()[:255],
    }


def quick_estimate_meta_from_quick_client_id(qc_id):
    row = get_mirror_quick_client_by_id(qc_id)
    if not row:
        return None
    n = (row.get("name") or "").strip()
    parts = []
    if (row.get("phone") or "").strip():
        parts.append(row["phone"].strip())
    if (row.get("extra_contact") or "").strip():
        parts.append(row["extra_contact"].strip())
    return {
        "client_id": None,
        "quick_client_id": int(row["id"]),
        "client_name": n,
        "markup_percent": int(row.get("markup_percent") or 0),
        "lead_source": (row.get("lead_source") or "").strip()[:64],
        "phone": (row.get("phone") or "").strip(),
        "extra_contact": (row.get("extra_contact") or "").strip()[:255],
        "contact_info": (" · ".join(parts))[:255],
    }


def _pricing_tier_from_markup(markup_percent: int) -> str:
    p = int(markup_percent or 0)
    if p == 30:
        return "b2c30"
    if p == 50:
        return "b2c50"
    return "b2b"


def insert_mirror_quick_client(
    name,
    phone="",
    extra_contact="",
    lead_source="",
    markup_percent=0,
):
    nm = (name or "").strip()
    if not nm:
        raise ValueError("Имя клиента обязательно.")
    tier = _pricing_tier_from_markup(int(markup_percent or 0))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_quick_clients
                   (name, phone, extra_contact, lead_source, markup_percent, pricing_tier)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    nm,
                    (phone or "").strip()[:128],
                    (extra_contact or "").strip()[:255],
                    (lead_source or "").strip()[:64],
                    int(markup_percent or 0),
                    tier,
                ),
            )
            row = cur.fetchone()
            return int(row["id"]) if row else None


def get_mirror_quick_client_by_id(qc_id):
    try:
        qid = int(qc_id)
    except (TypeError, ValueError):
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, phone, extra_contact, lead_source, markup_percent, pricing_tier,
                          created_at, updated_at
                   FROM mirror_quick_clients WHERE id = %s""",
                (qid,),
            )
            return cur.fetchone()


def get_mirror_quick_client_id_by_name(name):
    if not (name or "").strip():
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM mirror_quick_clients WHERE name = %s ORDER BY id DESC LIMIT 1",
                ((name or "").strip(),),
            )
            row = cur.fetchone()
            return int(row["id"]) if row else None


def get_mirror_quick_clients_search(query):
    """Поиск клиентов быстрого просчёта по подстроке имени (как get_clients_search)."""
    if not (query or "").strip():
        return []
    q = ("%" + (query or "").strip().replace(" ", "%") + "%",)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, phone, extra_contact, lead_source, markup_percent, pricing_tier
                   FROM mirror_quick_clients
                   WHERE name ILIKE %s OR COALESCE(extra_contact,'') ILIKE %s
                   ORDER BY name
                   LIMIT 50""",
                (q[0], q[0]),
            )
            return cur.fetchall()


def list_quick_estimate_client_suggestions(prefix, limit=40):
    """
    Подсказки: справочник mirror_clients + клиенты только из mirror_quick_clients (без дублей имён).
    Каждая запись: name, label (для списка/комплитера), client_id, quick_client_id.
    Если имя уже есть в справочнике — строка из quick не показывается (один клиент, привязка к справочнику).
    """
    pref = (prefix or "").strip()
    if not pref:
        return []
    lim = max(5, min(int(limit or 40), 80))
    out = []
    regular_lower = set()
    try:
        for r in (get_clients_search(pref) or [])[: lim + 20]:
            n = str(r.get("name") or "").strip()
            if not n:
                continue
            cid = r.get("id")
            try:
                cid = int(cid) if cid is not None else None
            except (TypeError, ValueError):
                cid = None
            regular_lower.add(n.lower())
            out.append({"name": n, "label": n, "client_id": cid, "quick_client_id": None})
    except Exception:
        pass
    try:
        for r in (get_mirror_quick_clients_search(pref) or [])[: lim + 20]:
            n = str(r.get("name") or "").strip()
            if not n:
                continue
            if n.lower() in regular_lower:
                continue
            try:
                qid = int(r.get("id"))
            except (TypeError, ValueError):
                continue
            out.append({"name": n, "label": n, "client_id": None, "quick_client_id": qid})
    except Exception:
        pass
    out.sort(key=lambda x: (x.get("label") or x.get("name") or "").lower())
    return out[:lim]


def insert_client(name):
    """Legacy: create client with only name (other fields default empty)."""
    return insert_client_full(
        'legal', name or '', None, None, None, None, None, None, None, None, None, None, None,
        '', '', '', '', None, None
    )


def _client_display_name(row):
    """Имя для отображения в списках: для физ. лица — фамилия и имя."""
    if row.get('client_type') == 'individual' and (row.get('first_name') or row.get('last_name')):
        return ('%s %s' % (row.get('last_name') or '', row.get('first_name') or '')).strip() or row.get('name') or ''
    return row.get('name') or ''


def insert_client_full(
    client_type, name,
    inn, kpp, okpo, ogrn, registration,
    first_name, last_name, passport_series, passport_number, birth_date, gender,
    phone, email, legal_address, actual_address, source=None, notes=None, pricing_tier='b2b'
):
    """Create client. For individual, name can be empty if first_name+last_name set; we store display name in name."""
    if client_type == 'individual' and not (name or '').strip() and (first_name or last_name):
        name = ('%s %s' % ((last_name or '').strip(), (first_name or '').strip())).strip()
    name = (name or '').strip() or '—'
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_clients (
                    client_type, name, inn, kpp, okpo, ogrn, registration,
                    first_name, last_name, passport_series, passport_number, birth_date, gender,
                    phone, email, legal_address, actual_address, source, notes, pricing_tier
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    (client_type or 'legal'),
                    name,
                    (inn or '').strip() or None, (kpp or '').strip() or None,
                    (okpo or '').strip() or None, (ogrn or '').strip() or None, (registration or '').strip() or None,
                    (first_name or '').strip() or None, (last_name or '').strip() or None,
                    (passport_series or '').strip() or None, (passport_number or '').strip() or None,
                    birth_date, (gender or '').strip() or None,
                    (phone or '').strip(), (email or '').strip(),
                    (legal_address or '').strip(), (actual_address or '').strip(),
                    (source or '').strip() or None, (notes or '').strip() or None,
                    (pricing_tier or 'b2b').strip().lower(),
                )
            )
            cid = cur.fetchone()['id']
            _invalidate_clients_cache()
            return cid


def get_or_create_client(name):
    """Return (client_id, created). If name exists return its id and False; else create and return new id and True."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM mirror_clients WHERE name = %s", (name.strip(),))
            row = cur.fetchone()
            if row:
                return row['id'], False
            cur.execute(
                """INSERT INTO mirror_clients (client_type, name, phone, email, legal_address, actual_address)
                   VALUES ('legal', %s, '', '', '', '') RETURNING id""",
                (name.strip(),)
            )
            cid = cur.fetchone()['id']
            _invalidate_clients_cache()
            return cid, True


def update_client(client_id, name):
    """Update only name (legacy)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE mirror_clients SET name=%s WHERE id=%s", (name, client_id))
    _invalidate_clients_cache()


def update_client_full(
    client_id, client_type, name,
    inn, kpp, okpo, ogrn, registration,
    first_name, last_name, passport_series, passport_number, birth_date, gender,
    phone, email, legal_address, actual_address, source=None, notes=None, pricing_tier=None
):
    if client_type == 'individual' and not (name or '').strip() and (first_name or last_name):
        name = ('%s %s' % ((last_name or '').strip(), (first_name or '').strip())).strip()
    name = (name or '').strip() or '—'
    if not pricing_tier:
        try:
            cur_row = get_client_by_id(client_id) or {}
            pricing_tier = (cur_row.get('pricing_tier') or 'b2b')
        except Exception:
            pricing_tier = 'b2b'
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE mirror_clients SET
                    client_type=%s, name=%s, inn=%s, kpp=%s, okpo=%s, ogrn=%s, registration=%s,
                    first_name=%s, last_name=%s, passport_series=%s, passport_number=%s, birth_date=%s, gender=%s,
                    phone=%s, email=%s, legal_address=%s, actual_address=%s, source=%s, notes=%s, pricing_tier=%s WHERE id=%s""",
                (
                    client_type or 'legal', name,
                    (inn or '').strip() or None, (kpp or '').strip() or None,
                    (okpo or '').strip() or None, (ogrn or '').strip() or None, (registration or '').strip() or None,
                    (first_name or '').strip() or None, (last_name or '').strip() or None,
                    (passport_series or '').strip() or None, (passport_number or '').strip() or None,
                    birth_date, (gender or '').strip() or None,
                    (phone or '').strip(), (email or '').strip(),
                    (legal_address or '').strip(), (actual_address or '').strip(),
                    (source or '').strip() or None, (notes or '').strip() or None,
                    (pricing_tier or 'b2b').strip().lower(),
                    client_id,
                )
            )
    _invalidate_clients_cache()


def client_price_factor(client_row: dict | None) -> float:
    tier = str((client_row or {}).get('pricing_tier') or 'b2b').strip().lower()
    if tier == 'b2c30':
        return 1.30
    if tier == 'b2c50':
        return 1.50
    return 1.0


def delete_client(client_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mirror_clients WHERE id = %s", (client_id,))
    _invalidate_clients_cache()


# --- Orders ---
# Тип заказа для интеграции с блоками MAIN_PROJECT/BLOCKS (xx.py)
ORDER_KIND_GLASS_MIRROR = 'glass_mirror'
ORDER_KIND_FACADE = 'facade'
ORDER_KIND_MIXED = 'mixed'

# Раскрой на листах учитываем для этих статусов (до отгрузки / выполнения / отмены).
# Заказы, по которым ещё допустима привязка/перерасчёт раскроя на листах «в работе» в выборе листа.
# «made» (изготовлен) не включаем — схема раскроя по такому заказу менять нельзя, лист не показываем в списке.
_ORDER_STATUSES_ACTIVE_FOR_CUT = (
    'draft',
    'paid',
    'in_progress',
    'checked_qr',
)


def update_order_kind(order_id, order_kind):
    """Установить тип заказа (glass_mirror / facade / mixed — см. MAIN_PROJECT.logic.blocks_bundle)."""
    ok = (order_kind or '').strip()
    if not ok:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mirror_orders SET order_kind = %s WHERE id = %s",
                (ok, int(order_id)),
            )
    _invalidate_orders_cache()


def mirror_order_created_by_from_qt_parent(parent):
    """Для create_order: (user_id, login, role) из окна с _user (главное окно, быстрый просчёт и т.д.).

    Поднимаемся по parent() — иначе при открытии калькулятора из цепочки
    «фасад → обзор заказа → стекло» родитель — FacadeOrderDialog без _user, и в БД уходит пустой автор.
    """
    seen = set()
    p = parent
    while p is not None:
        pid = id(p)
        if pid in seen:
            break
        seen.add(pid)
        u = getattr(p, "_user", None)
        if isinstance(u, dict):
            uid = u.get("id")
            try:
                uid = int(uid) if uid is not None else None
            except (TypeError, ValueError):
                uid = None
            login = (str(u.get("login") or "")).strip()[:128]
            role = (str(u.get("role") or "")).strip()[:64]
            if uid is not None or login:
                return uid, login, role
        try:
            p = p.parent()
        except Exception:
            break
    return None, "", ""


def create_order(
    client_name,
    client_id=None,
    notes=None,
    order_kind=None,
    quick_client_id=None,
    *,
    created_by_user_id=None,
    created_by_login=None,
    created_by_role=None,
):
    """Create order with client_name (client_id и/или quick_client_id)."""
    cid = None
    if client_id is not None:
        try:
            cid = int(client_id)
        except (TypeError, ValueError):
            cid = None
    qcid = None
    if quick_client_id is not None:
        try:
            qcid = int(quick_client_id)
        except (TypeError, ValueError):
            qcid = None
    cname = (client_name or "").strip() or None
    if cid is None and qcid is None and not cname:
        raise ValueError(
            "Нельзя создать заказ без клиента: выберите клиента в базе или укажите имя."
        )
    cb_uid = None
    if created_by_user_id is not None:
        try:
            cb_uid = int(created_by_user_id)
        except (TypeError, ValueError):
            cb_uid = None
    cb_login = ((created_by_login or "").strip() or "")[:128]
    cb_role = ((created_by_role or "").strip() or "")[:64]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_orders (
                       client_id, client_name, quick_client_id, status, notes, order_kind,
                       created_by_user_id, created_by_login, created_by_role
                   )
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    cid,
                    cname,
                    qcid,
                    "draft",
                    notes,
                    (order_kind or "").strip() or None,
                    cb_uid,
                    cb_login,
                    cb_role,
                ),
            )
            oid = cur.fetchone()["id"]
    _invalidate_orders_cache()
    return oid


def get_order(order_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, client_id, client_name, quick_client_id, created_at, status, accepted_at, notes, k_number,
                          order_kind, blocks_calc_json,
                          created_by_user_id, created_by_login, created_by_role
                   FROM mirror_orders WHERE id = %s""",
                (order_id,)
            )
            return cur.fetchone()


def get_order_for_labels(order_id):
    """Заказ с полями для этикеток: тот же запрос, что и список заказов (JOIN + client_name). Гарантированно возвращает client_name как на схеме."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT o.id, o.client_id, o.client_name AS o_client_name, o.created_at, o.status, o.accepted_at, o.notes, o.k_number,
                          COALESCE(c.name, o.client_name, '') AS client_name
                   FROM mirror_orders o
                   LEFT JOIN mirror_clients c ON c.id = o.client_id
                   WHERE o.id = %s""",
                (order_id,)
            )
            return cur.fetchone()


def get_order_client_name(order_id):
    """Имя клиента по заказу (из справочника или из поля заказа). Для этикеток и схем раскроя."""
    row = get_order_for_labels(order_id) if order_id else None
    if not row:
        return ''
    name = row.get('client_name') or row.get('o_client_name') or ''
    return (name or '').strip()


def get_order_client_name_from_archive(order_id):
    """Имя клиента из архива раскроя (то же, что на схеме листа). Используется для этикеток, если в заказе пусто."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT client_name FROM mirror_cut_archive WHERE order_id = %s AND client_name IS NOT NULL AND client_name != '' LIMIT 1""",
                (order_id,)
            )
            row = cur.fetchone()
            return (row.get('client_name') or '').strip() if row else ''


def get_order_by_k_number(k_num):
    """Найти заказ по номеру продукта для клиента (K1, K2...). k_num — целое число."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.client_id, o.client_name, o.created_at, o.status, o.accepted_at, o.notes, o.k_number,
                       COALESCE(c.name, o.client_name, '') AS client_name
                FROM mirror_orders o
                LEFT JOIN mirror_clients c ON c.id = o.client_id
                WHERE o.k_number = %s
                """,
                (int(k_num),)
            )
            return cur.fetchone()


def get_orders_recent(limit=10):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.client_id, o.created_at, o.status, o.accepted_at, o.notes,
                       COALESCE(c.name, o.client_name, '') AS client_name
                FROM mirror_orders o
                LEFT JOIN mirror_clients c ON c.id = o.client_id
                ORDER BY o.created_at DESC
                LIMIT %s
                """,
                (limit,)
            )
            return cur.fetchall()


def get_orders_all(limit=None, force_refresh: bool = False):
    """Все заказы (как в главной таблице программы). limit — необязательно, последние по дате."""
    return _get_orders_cached(limit=limit, force=bool(force_refresh))


def get_mirror_order_list_row(order_id):
    """Одна строка mirror_orders в том же виде, что элемент get_orders_all() (JOIN клиента и создателя)."""
    oid = int(order_id)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.client_id, o.created_at, o.status, o.accepted_at, o.notes, o.k_number,
                       COALESCE(c.name, o.client_name, '') AS client_name,
                       o.order_kind, o.blocks_calc_json,
                       o.created_by_user_id, o.created_by_login, o.created_by_role,
                       u.name AS creator_name, u.surname AS creator_surname
                FROM mirror_orders o
                LEFT JOIN mirror_clients c ON c.id = o.client_id
                LEFT JOIN main_users u ON u.id = o.created_by_user_id
                WHERE o.id = %s
                """,
                (oid,),
            )
            return cur.fetchone()


def get_mirror_orders_blocks_snapshot_bulk(order_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Снимок status + blocks_calc_json для строк таблицы заказов (веб-сервис меняет JSON без уведомления UI)."""
    ids = sorted({int(x) for x in (order_ids or []) if x is not None})
    if not ids:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, blocks_calc_json FROM mirror_orders WHERE id IN %s",
                (tuple(ids),),
            )
            rows = cur.fetchall() or []
    out: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        try:
            oid = int(r.get("id"))
        except (TypeError, ValueError):
            continue
        out[oid] = {"status": r.get("status"), "blocks_calc_json": r.get("blocks_calc_json")}
    return out


def update_order_blocks_calc(order_id, blocks_json_text):
    """Сохранить JSON просчёта блоков стекло/зеркало (MAIN_PROJECT/BLOCKS)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mirror_orders SET blocks_calc_json = %s WHERE id = %s",
                (blocks_json_text, int(order_id)),
            )
    try:
        from db.zamer_portal_sync import _extract_first_activated_zamer, sync_blocks_zamer_for_order

        if _extract_first_activated_zamer(blocks_json_text):
            sync_blocks_zamer_for_order(int(order_id), blocks_json_text)
    except Exception:
        pass
    _invalidate_orders_cache()


def patch_order_bundle_product_fields(order_id, product_index_1based, fields: dict):
    """Обновить поля одного изделия в blocks_calc_json (индекс с 1, как в API производства)."""
    try:
        from MAIN_PROJECT.logic.blocks_bundle import parse_bundle, bundle_to_json
    except Exception:
        from logic.blocks_bundle import parse_bundle, bundle_to_json  # type: ignore

    row = get_order(int(order_id))
    if not row:
        return False
    raw = row.get("blocks_calc_json")
    ver, products = parse_bundle(raw if raw is not None else None)
    i = int(product_index_1based) - 1
    if i < 0 or i >= len(products):
        return False
    for k, v in (fields or {}).items():
        if v is not None:
            products[i][str(k)] = v
    update_order_blocks_calc(int(order_id), bundle_to_json(max(ver, 2), products))
    return True


def get_orders_by_client_name_exact(client_name):
    """Заказы с точным совпадением отображаемого имени клиента (справочник или поле заказа), без учёта регистра."""
    cn = (client_name or "").strip()
    if not cn:
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.client_id, o.created_at, o.status, o.accepted_at, o.notes, o.k_number,
                       o.order_kind, o.blocks_calc_json,
                       COALESCE(c.name, o.client_name, '') AS client_name,
                       o.created_by_user_id, o.created_by_login, o.created_by_role,
                       u.name AS creator_name, u.surname AS creator_surname
                FROM mirror_orders o
                LEFT JOIN mirror_clients c ON c.id = o.client_id
                LEFT JOIN main_users u ON u.id = o.created_by_user_id
                WHERE LOWER(TRIM(COALESCE(c.name, o.client_name, ''))) = LOWER(TRIM(%s))
                ORDER BY o.created_at DESC
                """,
                (cn,),
            )
            return cur.fetchall()


def get_orders_by_client_name(client_name, *, exclude_completed: bool = False):
    """Все заказы, где клиент (имя из заказа или из справочника) совпадает. client_name — подстрока, поиск без учёта регистра.

    exclude_completed: не возвращать заказы со статусами completed и cancelled (для WEB_QR и т.п.).
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            where = "WHERE LOWER(COALESCE(c.name, o.client_name, '')) LIKE LOWER(%s)"
            params = ['%' + (client_name or '').strip() + '%']
            if exclude_completed:
                where += (
                    " AND LOWER(TRIM(COALESCE(o.status, ''))) NOT IN ('completed', 'cancelled')"
                )
            cur.execute(
                """
                SELECT o.id, o.client_id, o.created_at, o.status, o.accepted_at, o.notes, o.k_number,
                       COALESCE(c.name, o.client_name, '') AS client_name
                FROM mirror_orders o
                LEFT JOIN mirror_clients c ON c.id = o.client_id
                """
                + where
                + """
                ORDER BY o.created_at DESC
                """,
                tuple(params),
            )
            return cur.fetchall()


def get_orders_by_client_id(client_id):
    """Все заказы клиента по client_id (из справочника)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.client_id, o.created_at, o.status, o.accepted_at, o.notes, o.k_number,
                       o.order_kind, o.blocks_calc_json,
                       COALESCE(c.name, o.client_name, '') AS client_name,
                       o.created_by_user_id, o.created_by_login, o.created_by_role,
                       u.name AS creator_name, u.surname AS creator_surname
                FROM mirror_orders o
                LEFT JOIN mirror_clients c ON c.id = o.client_id
                LEFT JOIN main_users u ON u.id = o.created_by_user_id
                WHERE o.client_id = %s
                ORDER BY o.created_at DESC
                """,
                (client_id,)
            )
            return cur.fetchall()


def get_orders_by_client_id_in_range(client_id, created_from=None, created_to_exclusive=None):
    """
    Заказы клиента с фильтром по дате создания (для статистики).
    created_from / created_to_exclusive — datetime или None (без границы);
    верхняя граница не включается (как SQL < created_to_exclusive).
    """
    cid = int(client_id)
    wh = ["o.client_id = %s"]
    params = [cid]
    if created_from is not None:
        wh.append("o.created_at >= %s")
        params.append(created_from)
    if created_to_exclusive is not None:
        wh.append("o.created_at < %s")
        params.append(created_to_exclusive)
    where_sql = " AND ".join(wh)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.client_id, o.created_at, o.status, o.accepted_at, o.notes, o.k_number,
                       o.order_kind, o.blocks_calc_json,
                       COALESCE(c.name, o.client_name, '') AS client_name,
                       o.created_by_user_id, o.created_by_login, o.created_by_role,
                       u.name AS creator_name, u.surname AS creator_surname
                FROM mirror_orders o
                LEFT JOIN mirror_clients c ON c.id = o.client_id
                LEFT JOIN main_users u ON u.id = o.created_by_user_id
                WHERE """
                + where_sql
                + """
                ORDER BY o.created_at DESC
                """,
                tuple(params),
            )
            return cur.fetchall() or []


def list_sales_orders_for_client_id(client_id, created_from=None, created_to_exclusive=None):
    """Продажи (mirror_sales_orders) по client_id с опциональным фильтром по created_at."""
    cid = int(client_id)
    wh = ["o.client_id = %s"]
    params = [cid]
    if created_from is not None:
        wh.append("o.created_at >= %s")
        params.append(created_from)
    if created_to_exclusive is not None:
        wh.append("o.created_at < %s")
        params.append(created_to_exclusive)
    where_sql = " AND ".join(wh)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.client_id, o.quick_client_id, o.client_name AS o_client_name, o.status, o.notes, o.total_rub,
                       o.created_at, o.updated_at,
                       COALESCE(c.name, qc.name, o.client_name, '') AS client_name
                FROM mirror_sales_orders o
                LEFT JOIN mirror_clients c ON c.id = o.client_id
                LEFT JOIN mirror_quick_clients qc ON qc.id = o.quick_client_id
                WHERE """
                + where_sql
                + """
                ORDER BY o.created_at DESC, o.id DESC
                """,
                tuple(params),
            )
            return cur.fetchall() or []


def get_remnant_by_label_number(label_number):
    """Остаток по номеру этикетки (деловой остаток)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, height_mm, width_mm, thickness_mm, unique_number, qr_url, created_at, label_number, reserved_for_cut_order_id FROM mirror_remnants WHERE label_number = %s",
                (int(label_number),)
            )
            return cur.fetchone()


def get_orders_in_progress():
    """Заказы для учёта раскроя на листах: draft … in_progress и checked_qr; без «изготовлен» (made)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.client_id, o.created_at, o.status, o.blocks_calc_json, COALESCE(c.name, o.client_name, '') AS client_name
                FROM mirror_orders o
                LEFT JOIN mirror_clients c ON c.id = o.client_id
                WHERE o.status = ANY(%s) ORDER BY o.id
                """,
                (list(_ORDER_STATUSES_ACTIVE_FOR_CUT),)
            )
            rows = cur.fetchall() or []
            out = []
            for r in rows:
                oid = r.get("id")
                if oid is None:
                    continue
                # Заказ с активным замером не должен идти в раскрой/работу, пока нет файлов замера.
                if _order_has_active_measure_in_bundle(r.get("blocks_calc_json")) and not _order_measure_files_present(cur, int(oid)):
                    continue
                out.append(r)
            return out


def in_work_pool_entry_id(order_id, sheet_index, list_index):
    """
    Стабильный id слота «в работе» в пуле раскроя (тот же, что в CutMaterialSessionDialog._get_sheets_fn).
    list_index — порядковый номер строки в списке get_sheets_in_work_for_material_thickness (0,1,…).
    """
    return -(int(order_id) * 10000 + int(sheet_index) * 100 + int(list_index))


def get_sheets_in_work_for_material_thickness(material_name, thickness_mm, min_rect_side=20):
    """
    Листы «в работе» из заказов draft…in_progress: пустые (ещё без изделий на схеме) и с уже
    размещённым планом (есть pieces) — на них новая сессия дополняет свободные места.

    Возвращает список dict: order_id, sheet_index, rect_*, sheet_width/height, thickness_mm,
    no_cuts_yet, planned_piece_count, saved_layout (копия layout из БД при наличии изделий).
    """
    orders = get_orders_in_progress()
    oids = [o['id'] for o in orders if o.get('id') is not None]
    cut_by_order = get_cut_results_bulk(oids)
    out = []
    mat_need = ((material_name or '').strip() or '').lower()
    for o in orders:
        order_id = o['id']
        rows = cut_by_order.get(order_id, [])
        for sheet_index, cr in enumerate(rows):
            lay = cr.get('layout') or {}
            if not isinstance(lay, dict):
                continue
            mat_lay = ((lay.get('material') or '').strip() or '').lower()
            if mat_need and mat_lay != mat_need:
                continue
            thick = int(lay.get('thickness_mm') or 4)
            sid = lay.get('sheet_id')
            stype = lay.get('sheet_type') or 'full'
            if stype == 'full' and sid:
                sh = get_full_sheet_by_id(sid)
                if sh:
                    thick = int(sh.get('thickness_mm') or 4)
            elif stype == 'remnant' and sid:
                sh = get_remnant_by_id(sid)
                if sh:
                    thick = int(sh.get('thickness_mm') or 4)
            if thick != int(thickness_mm):
                continue
            pieces_lay = lay.get('pieces') or []
            business_rects = lay.get('business_rects')
            if not business_rects and pieces_lay:
                try:
                    from logic.cutting_algorithm import recompute_free_rects_from_pieces
                    sw = int(lay.get('sheet_width') or 0)
                    sh_h = int(lay.get('sheet_height') or 0)
                    if sw > 0 and sh_h > 0:
                        business_rects, _ = recompute_free_rects_from_pieces(sw, sh_h, pieces_lay, 0, 0)
                except Exception:
                    business_rects = []
            # Лист только с «дырами» без pieces (нестандартное состояние) — не даём в пул сессии.
            if not pieces_lay and business_rects:
                continue
            sw = int(lay.get('sheet_width') or 0)
            sh_h = int(lay.get('sheet_height') or 0)
            if sw < min_rect_side or sh_h < min_rect_side:
                continue
            entry = {
                'order_id': order_id,
                'sheet_index': sheet_index,
                'sheet_id': sid,
                'rect_x': 0,
                'rect_y': 0,
                'rect_w': sw,
                'rect_h': sh_h,
                'sheet_width': sw,
                'sheet_height': sh_h,
                'thickness_mm': thick,
                'in_work': True,
                'no_cuts_yet': not bool(pieces_lay),
                'planned_piece_count': len(pieces_lay),
            }
            if pieces_lay:
                entry['saved_layout'] = copy.deepcopy(lay)
            out.append(entry)
    return out


def find_in_work_pool_sheet_descriptor(material_name, thickness_mm, pool_id):
    """
    Найти словарь листа «в работе» для пула раскроя по id из ChooseSheetDialog (in_work_pool_entry_id).
    Нужно, если между открытием диалога и OK список склада перестроился и next() не нашёл строку.
    """
    try:
        pid = int(pool_id)
    except (TypeError, ValueError):
        return None
    rows = get_sheets_in_work_for_material_thickness(material_name, thickness_mm) or []
    for idx, s in enumerate(rows):
        uid = in_work_pool_entry_id(s['order_id'], s.get('sheet_index', 0), idx)
        if uid != pid:
            continue
        desc = {
            'id': uid,
            'width_mm': s['rect_w'],
            'height_mm': s['rect_h'],
            'sheet_type': 'in_work',
            'thickness_mm': s['thickness_mm'],
            'in_work_order_id': s['order_id'],
            'in_work_sheet_index': s.get('sheet_index', 0),
            'in_work_rect': {
                'x': s['rect_x'],
                'y': s['rect_y'],
                'w': s['rect_w'],
                'h': s['rect_h'],
            },
        }
        if s.get('saved_layout') is not None:
            desc['saved_layout'] = copy.deepcopy(s['saved_layout'])
        if s.get('planned_piece_count') is not None:
            desc['planned_piece_count'] = int(s['planned_piece_count'])
        desc['no_cuts_yet'] = bool(s.get('no_cuts_yet', True))
        return desc
    return None


def set_order_client_on_complete(order_id):
    """Resolve client_name to client_id and update order (call when marking completed)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT client_name FROM mirror_orders WHERE id = %s", (order_id,))
            row = cur.fetchone()
            if not row or not (row.get('client_name') or '').strip():
                return
            name = (row['client_name'] or '').strip()
            cur.execute("SELECT id FROM mirror_clients WHERE name = %s", (name,))
            existing = cur.fetchone()
            if existing:
                cid = existing['id']
            else:
                cur.execute("INSERT INTO mirror_clients (name) VALUES (%s) RETURNING id", (name,))
                cid = cur.fetchone()['id']
            cur.execute("UPDATE mirror_orders SET client_id = %s, client_name = NULL WHERE id = %s", (cid, order_id))


def _order_has_active_measure_in_bundle(blocks_calc_json):
    try:
        from MAIN_PROJECT.logic.blocks_bundle import parse_bundle

        _ver, products = parse_bundle(blocks_calc_json)
    except Exception:
        products = []
    for pr in products or []:
        pl = pr.get("payload") if isinstance(pr, dict) else {}
        if not isinstance(pl, dict):
            continue
        z = pl.get("Замер")
        if not (isinstance(z, dict) and bool(z.get("Активирован"))):
            continue
        zd = z.get("Данные") if isinstance(z.get("Данные"), dict) else {}
        # Блокируем только если действительно включена услуга «Замер».
        # Наличие активного блока (для портала/камер/доставки/монтажа) само по себе
        # не должно требовать файлов именно замера.
        if bool(zd.get("Замер")):
            return True
        # Обратная совместимость со старыми сохранениями:
        # если флаги услуг отсутствуют, а «Без замера» не отмечено — считаем, что замер нужен.
        if (
            not bool(zd.get("Монтаж"))
            and not bool(zd.get("Доставка"))
            and (not bool(zd.get("Без замера")))
            and ("Замер" not in zd)
            and ("Монтаж" not in zd)
            and ("Доставка" not in zd)
        ):
            return True
    return False


def _order_measure_files_present(cur, order_id):
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM blocks_zamer z
            JOIN blocks_zamer_file f ON f.zamer_id = z.id
            WHERE z.mirror_order_id = %s
              AND COALESCE(z.is_measure, FALSE) = TRUE
              AND LOWER(COALESCE(TRIM(f.file_kind), 'measure')) = 'measure'
        ) AS ok
        """,
        (int(order_id),),
    )
    rr = cur.fetchone() or {}
    return bool(rr.get("ok"))


def _order_measure_lock_reason_with_cur(cur, order_id, blocks_calc_json):
    """Текст блокировки смены статуса по замеру или None. cur — тот же курсор, что и SELECT заказа (без второго коннекта)."""
    oid = int(order_id)
    if not _order_has_active_measure_in_bundle(blocks_calc_json):
        return None
    if _order_measure_files_present(cur, oid):
        return None
    return "Замер обязателен: сначала загрузите данные/файлы замера в WEB_SERVICE."


def get_order_measure_lock_reason(order_id):
    """
    Возвращает текст причины блокировки по замеру или None, если блокировки нет.
    Правило: если в заказе активирован замер, до получения файлов замера статус и действия блокируются.
    """
    oid = int(order_id)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, blocks_calc_json FROM mirror_orders WHERE id = %s", (oid,))
            row = cur.fetchone() or {}
            if not row:
                return None
            return _order_measure_lock_reason_with_cur(cur, oid, row.get("blocks_calc_json"))


def _get_next_k_number_with_cur(cur):
    """Следующий K-номер в рамках уже открытого курсора (без отдельного get_connection)."""
    cur.execute("UPDATE mirror_k_counter SET value = value + 1 RETURNING value")
    row = cur.fetchone()
    return (row["value"] if row else 0) or 1


def set_order_status(order_id, status, *, sync_bundle_product_status=True):
    """Статус заказа в mirror_orders. По умолчанию дублирует статус во все изделия bundle (как раньше)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status, blocks_calc_json FROM mirror_orders WHERE id = %s", (order_id,))
            old = cur.fetchone() or {}
            cur_status = str((old or {}).get("status") or "").strip().lower()
            new_status = str(status or "").strip().lower()
            if new_status and new_status != cur_status:
                lock_reason = _order_measure_lock_reason_with_cur(
                    cur, int(order_id), old.get("blocks_calc_json")
                )
                if lock_reason:
                    raise ValueError(lock_reason)
            blocks_json = old.get("blocks_calc_json")
            if sync_bundle_product_status:
                try:
                    from MAIN_PROJECT.logic.blocks_bundle import apply_order_status_to_products
                except Exception:
                    try:
                        from logic.blocks_bundle import apply_order_status_to_products  # type: ignore
                    except Exception:
                        apply_order_status_to_products = None  # type: ignore
                try:
                    if apply_order_status_to_products is not None:
                        blocks_json = apply_order_status_to_products(blocks_json, status)
                except Exception:
                    pass
            if status == 'completed':
                cur.execute("SELECT k_number FROM mirror_orders WHERE id = %s", (order_id,))
                row = cur.fetchone()
                k_num = row.get('k_number') if row else None
                if k_num is None:
                    k_num = _get_next_k_number_with_cur(cur)
                    cur.execute(
                        "UPDATE mirror_orders SET status = %s, blocks_calc_json = %s, accepted_at = COALESCE(accepted_at, CURRENT_TIMESTAMP), k_number = %s WHERE id = %s",
                        (status, blocks_json, k_num, order_id)
                    )
                else:
                    cur.execute(
                        "UPDATE mirror_orders SET status = %s, blocks_calc_json = %s, accepted_at = COALESCE(accepted_at, CURRENT_TIMESTAMP) WHERE id = %s",
                        (status, blocks_json, order_id)
                    )
            else:
                cur.execute(
                    "UPDATE mirror_orders SET status = %s, blocks_calc_json = %s WHERE id = %s",
                    (status, blocks_json, order_id),
                )
            try:
                cur.execute(
                    "UPDATE blocks_zamer SET updated_at = NOW() WHERE mirror_order_id = %s",
                    (int(order_id),),
                )
            except Exception:
                pass
    _invalidate_orders_cache()


def add_production_event(order_id, event_type, actor_user_id=None, actor_login="", actor_role="", source="desktop", details=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mirror_production_events
                (order_id, event_type, actor_user_id, actor_login, actor_role, source, details_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    int(order_id),
                    str(event_type or "").strip(),
                    actor_user_id,
                    str(actor_login or "").strip(),
                    str(actor_role or "").strip(),
                    str(source or "desktop").strip(),
                    json.dumps(details or {}, ensure_ascii=False),
                ),
            )
            row = cur.fetchone() or {}
            return row.get("id")


def list_production_events(order_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, order_id, event_type, actor_user_id, actor_login, actor_role, source, details_json, created_at
                FROM mirror_production_events
                WHERE order_id = %s
                ORDER BY created_at DESC, id DESC
                """,
                (int(order_id),),
            )
            rows = cur.fetchall() or []
    out = []
    for r in rows:
        rr = dict(r)
        dj = rr.get("details_json")
        if isinstance(dj, str):
            try:
                rr["details_json"] = json.loads(dj)
            except Exception:
                rr["details_json"] = {}
        out.append(rr)
    return out


def list_production_events_for_orders(order_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    """События производства для набора заказов: order_id -> список (как list_production_events, порядок DESC)."""
    ids = sorted({int(x) for x in (order_ids or []) if int(x) > 0})
    if not ids:
        return {}
    if len(ids) > 600:
        ids = ids[:600]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, order_id, event_type, actor_user_id, actor_login, actor_role, source, details_json, created_at
                FROM mirror_production_events
                WHERE order_id = ANY(%s)
                ORDER BY order_id ASC, created_at DESC, id DESC
                """,
                (ids,),
            )
            rows = cur.fetchall() or []
    by_oid: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        rr = dict(r)
        dj = rr.get("details_json")
        if isinstance(dj, str):
            try:
                rr["details_json"] = json.loads(dj)
            except Exception:
                rr["details_json"] = {}
        oid = int(rr.get("order_id") or 0)
        if oid > 0:
            by_oid[oid].append(rr)
    return dict(by_oid)


def count_facade_instance_assembled_events(order_id, product_index_1based, production_events=None):
    """Число отмеченных на вебе/в цехе событий сборки экземпляра фасада для позиции заказа (индекс изделия с 1).

    production_events — если передан (результат list_production_events для заказа), не делаем повторный запрос
    (при нескольких фасадах в заказе раньше было N одинаковых SELECT по mirror_production_events).
    """
    oid = int(order_id)
    pi = int(product_index_1based)
    if oid < 1 or pi < 1:
        return 0
    n = 0
    evs = production_events if production_events is not None else list_production_events(oid)
    for ev in evs or []:
        if str(ev.get("event_type") or "") != "facade_instance_assembled":
            continue
        d = ev.get("details_json")
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                d = {}
        if not isinstance(d, dict):
            continue
        if int(d.get("product_index") or 0) != pi:
            continue
        n += 1
    return n


def update_mirror_order_status(order_id, new_status):
    """Только поле status заказа + сброс кэша списка."""
    st = str(new_status or "").strip().lower()
    if not st:
        return False
    oid = int(order_id)
    if oid < 1:
        return False
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mirror_orders SET status = %s WHERE id = %s",
                (st, oid),
            )
    _invalidate_orders_cache()
    return True


def sync_order_status_made_if_all_products_complete(order_id):
    """Если по каждой позиции bundle производственная готовность достигнута — статус заказа «made» (изготовлен)."""
    oid = int(order_id)
    if oid < 1:
        return False
    row = get_order(oid)
    if not row:
        return False
    cur_st = str(row.get("status") or "").strip().lower()
    if cur_st in ("made", "checked_qr", "shipped", "completed"):
        return False
    try:
        from MAIN_PROJECT.logic.blocks_bundle import parse_bundle
    except Exception:
        from logic.blocks_bundle import parse_bundle  # type: ignore

    raw = row.get("blocks_calc_json")
    ver, products = parse_bundle(raw if raw is not None else None)
    if not products:
        return False
    order_fallback = cur_st or "draft"
    done = frozenset({"made", "checked_qr", "shipped", "completed"})
    facade_events = None
    if any(str((p or {}).get("kind") or "").strip() == "facade" for p in (products or [])):
        facade_events = list_production_events(oid) or []
    for idx, p in enumerate(products):
        if not isinstance(p, dict):
            continue
        pi = idx + 1
        kind = str(p.get("kind") or "").strip()
        pst = str(p.get("status") or "").strip() or order_fallback
        if kind == "facade":
            pl = p.get("payload") if isinstance(p.get("payload"), dict) else {}
            try:
                qty = max(1, int(pl.get("Количество") or 1))
            except (TypeError, ValueError):
                qty = 1
            assembled = count_facade_instance_assembled_events(oid, pi, production_events=facade_events)
            if assembled >= qty:
                continue
            if pst in done:
                continue
            return False
        else:
            if pst not in done:
                return False
    return update_mirror_order_status(oid, "made")


def get_order_ids_with_cut_results(order_ids):
    """Множество id заказов, по которым есть хотя бы одна запись раскроя."""
    ids = [int(x) for x in order_ids if x is not None]
    if not ids:
        return frozenset()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT order_id FROM mirror_cut_results
                WHERE order_id = ANY(%s)
                """,
                (ids,),
            )
            rows = cur.fetchall() or []
    return frozenset(int(r["order_id"]) for r in rows if r.get("order_id") is not None)


def get_order_ids_with_measure_files_done(order_ids):
    """Заказы, по которым уже загружен хотя бы один файл замера (measure) — один запрос вместо N EXISTS."""
    ids = []
    seen = set()
    for x in order_ids or []:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i < 1 or i in seen:
            continue
        seen.add(i)
        ids.append(i)
    if not ids:
        return frozenset()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT z.mirror_order_id AS order_id
                FROM blocks_zamer z
                JOIN blocks_zamer_file f ON f.zamer_id = z.id
                WHERE z.mirror_order_id = ANY(%s)
                  AND COALESCE(z.is_measure, FALSE) = TRUE
                  AND LOWER(COALESCE(TRIM(f.file_kind), 'measure')) = 'measure'
                """,
                (ids,),
            )
            rows = cur.fetchall() or []
    return frozenset(int(r["order_id"]) for r in rows if r.get("order_id") is not None)


def list_orders_production_queue(limit=300):
    """Заказы для дашборда цеха: оплачен / в работе, с расчётом и сохранённой схемой раскроя."""
    lim = max(1, min(int(limit or 300), 2000))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, client_name, status, k_number, created_at, order_kind, blocks_calc_json
                FROM mirror_orders
                WHERE status IN ('paid', 'in_progress')
                  AND blocks_calc_json IS NOT NULL
                  AND btrim(blocks_calc_json::text) NOT IN ('', '{}', 'null')
                ORDER BY id DESC
                LIMIT %s
                """,
                (max(lim * 3, 300),),
            )
            rows = cur.fetchall() or []
    if not rows:
        return []
    try:
        from MAIN_PROJECT.logic.blocks_bundle import CUT_SCHEME_CREATED, parse_bundle
    except Exception:
        try:
            from logic.blocks_bundle import CUT_SCHEME_CREATED, parse_bundle  # type: ignore
        except Exception:
            CUT_SCHEME_CREATED = "scheme_created"
            parse_bundle = None
    ids = [int(r.get("id")) for r in rows if r.get("id") is not None]
    ids_with_cut = get_order_ids_with_cut_results(ids)
    needing_measure_files = set()
    for r in rows:
        oid = r.get("id")
        if oid is None:
            continue
        if _order_has_active_measure_in_bundle(r.get("blocks_calc_json")):
            needing_measure_files.add(int(oid))
    measure_files_ok = (
        get_order_ids_with_measure_files_done(list(needing_measure_files)) if needing_measure_files else frozenset()
    )
    out = []
    for r in rows:
        oid = r.get("id")
        if oid is None:
            continue
        ioid = int(oid)
        if ioid in needing_measure_files and ioid not in measure_files_ok:
            continue
        has_scheme = ioid in ids_with_cut
        if not has_scheme and parse_bundle:
            raw = r.get("blocks_calc_json")
            try:
                _v, products = parse_bundle(str(raw) if raw is not None else None)
                has_scheme = any(
                    str(p.get("cut_scheme_status") or "").strip() == CUT_SCHEME_CREATED
                    for p in (products or [])
                )
            except Exception:
                has_scheme = False
        if has_scheme:
            out.append(r)
            if len(out) >= lim:
                break
    return out


def _restore_paid_after_cut_revert(order_id):
    """
    После отмены раскроя: заказ в «Оплачен», у всех изделий в bundle status = paid.
    payment_type и прочие поля оплаты не затираются (apply_order_status_to_products только setdefault).
    Обход проверки замера: оплата уже была до вывода в работу/раскрой.
    """
    oid = int(order_id)
    if oid < 1:
        return
    try:
        from MAIN_PROJECT.logic.blocks_bundle import apply_order_status_to_products
    except Exception:
        try:
            from logic.blocks_bundle import apply_order_status_to_products  # type: ignore
        except Exception:
            apply_order_status_to_products = None
    if apply_order_status_to_products is None:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT blocks_calc_json FROM mirror_orders WHERE id = %s", (oid,))
            row = cur.fetchone() or {}
            raw = row.get("blocks_calc_json")
            try:
                new_json = apply_order_status_to_products(raw, "paid")
            except Exception:
                new_json = raw
            cur.execute(
                "UPDATE mirror_orders SET status = %s, blocks_calc_json = %s WHERE id = %s",
                ("paid", new_json, oid),
            )
            try:
                cur.execute(
                    "UPDATE blocks_zamer SET updated_at = NOW() WHERE mirror_order_id = %s",
                    (oid,),
                )
            except Exception:
                pass
    _invalidate_orders_cache()


def revert_order_cut_draft(order_id):
    """
    Отмена чернового раскроя: удалить cut_results, деловые остатки от раскроя,
    сбросить cut_scheme_status в bundle. Нельзя, если стекло отмечено изготовленным
    или остаток от раскроя уже использован как лист в другом заказе.
    Возвращает (ok: bool, message: str).
    """
    try:
        from MAIN_PROJECT.logic.blocks_bundle import (
            CUT_SCHEME_CREATED,
            PRODUCTION_GLASS_MADE,
            clear_cut_scheme_on_all_products,
            parse_bundle,
        )
    except Exception:
        try:
            from logic.blocks_bundle import (  # type: ignore
                CUT_SCHEME_CREATED,
                PRODUCTION_GLASS_MADE,
                clear_cut_scheme_on_all_products,
                parse_bundle,
            )
        except Exception as e:
            return False, "import:%s" % e

    oid = int(order_id)
    order = get_order(oid)
    if not order:
        return False, "Заказ не найден."
    if (order.get("status") or "").strip() == "completed":
        return False, "Заказ завершён — отмена раскроя недоступна."
    raw = order.get("blocks_calc_json")
    _ver, products = parse_bundle(raw if raw is not None else None)
    for p in products:
        if str(p.get("production_glass_status") or "").strip() == PRODUCTION_GLASS_MADE:
            return False, "Производство отметило стекло как изготовленное — отмена невозможна."
    rows = get_cut_results(oid) or []
    if rows:
        for r in rows:
            for item in r.get("remnants_created") or []:
                if not isinstance(item, dict):
                    continue
                rid = item.get("remnant_id")
                if not rid:
                    continue
                try:
                    rid_i = int(rid)
                except (TypeError, ValueError):
                    continue
                hit = find_in_progress_sheet_usage("remnant", rid_i, exclude_order_id=oid)
                if hit:
                    return False, "Деловой остаток №%s уже использован в раскрое заказа №%s." % (rid_i, hit[0])
        _restore_reserved_sources_from_cut_results(oid)
        _delete_remnants_from_cut_layout(oid)
        delete_cut_results(oid)
        new_json = clear_cut_scheme_on_all_products(raw)
        update_order_blocks_calc(oid, new_json)
        _restore_paid_after_cut_revert(oid)
        return True, "Раскрой отменён: резерв целых листов и остатков снят, схемы по изделиям сброшены."

    host = _bundle_cut_storage_order_id_from_raw(raw)
    if host is None:
        candidates = find_cut_host_orders_for_source_order(oid, limit=80)
        if len(candidates) == 1:
            host = candidates[0]
        elif len(candidates) > 1:
            return (
                False,
                "Раскрой найден на нескольких заказах (%s). Откройте сводку заказа-хоста или обратитесь к администратору."
                % ", ".join("№%s" % h for h in candidates[:10]),
            )
    if host and int(host) != oid:
        ok_strip, msg = strip_satellite_cut_pieces_from_host(int(host), oid)
        if ok_strip:
            new_json = clear_cut_scheme_on_all_products(raw)
            update_order_blocks_calc(oid, new_json)
            _restore_paid_after_cut_revert(oid)
            return True, msg + " Отметки схемы по этому заказу сброшены."
        orphaned_scheme = any(
            str(p.get("cut_scheme_status") or "").strip() == CUT_SCHEME_CREATED for p in products
        )
        if orphaned_scheme:
            new_json = clear_cut_scheme_on_all_products(raw)
            update_order_blocks_calc(oid, new_json)
            _restore_paid_after_cut_revert(oid)
            return True, "Сброшены отметки схемы по изделиям. (%s)" % (msg or "Детали на листах хоста не изменены.")
        return False, msg or "Нет сохранённого раскроя."

    orphaned_scheme = any(
        str(p.get("cut_scheme_status") or "").strip() == CUT_SCHEME_CREATED for p in products
    )
    if orphaned_scheme:
        new_json = clear_cut_scheme_on_all_products(raw)
        update_order_blocks_calc(oid, new_json)
        _restore_paid_after_cut_revert(oid)
        return True, "Сохранённого раскроя в базе не было — сброшены только отметки схемы по изделиям."

    return False, "Нет сохранённого раскроя."


def insert_inventory_scan(
    item_type,
    stock_ref_id=None,
    unique_number="",
    size_text="",
    session_key="",
    actor_user_id=None,
    actor_login="",
    campaign_id=None,
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mirror_inventory_scans
                (item_type, stock_ref_id, unique_number, size_text, session_key, campaign_id, actor_user_id, actor_login)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    str(item_type or "")[:16],
                    stock_ref_id,
                    str(unique_number or "")[:64],
                    str(size_text or "")[:128],
                    str(session_key or "")[:64],
                    int(campaign_id) if campaign_id is not None else None,
                    actor_user_id,
                    str(actor_login or "")[:128],
                ),
            )
            return (cur.fetchone() or {}).get("id")


def insert_generated_qr_log_batch(entries, actor_user_id=None, actor_login="", actor_name=""):
    """Журнал этикеток из веб-генератора QR (после успешной сборки PDF)."""
    if not entries:
        return
    with get_connection() as conn:
        with conn.cursor() as cur:
            for e in entries:
                if not isinstance(e, dict):
                    continue
                cur.execute(
                    """
                    INSERT INTO mirror_generated_qr_log
                    (source_kind, label_code, title, subtitle, actor_user_id, actor_login, actor_name, details_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        str(e.get("source_kind") or "")[:16],
                        str(e.get("label_code") or "")[:128],
                        str(e.get("title") or "")[:256],
                        str(e.get("subtitle") or "")[:512],
                        actor_user_id,
                        str(actor_login or "")[:128],
                        str(actor_name or "")[:256],
                        json.dumps(e.get("details") or {}, ensure_ascii=False),
                    ),
                )


def list_generated_qr_log(*, tab="glass", limit=3000):
    """tab: 'glass' — изделия K и остатки стекла; 'profile' — профили."""
    if str(tab or "").strip().lower() == "profile":
        kinds = ["profile"]
    else:
        kinds = ["piece_k", "remnant"]
    lim = max(1, min(int(limit or 3000), 20000))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_kind, label_code, title, subtitle,
                       actor_user_id, actor_login, actor_name, details_json, created_at
                FROM mirror_generated_qr_log
                WHERE source_kind = ANY(%s)
                ORDER BY id DESC
                LIMIT %s
                """,
                (kinds, lim),
            )
            rows = cur.fetchall() or []
    out = []
    for r in rows:
        rr = dict(r)
        dj = rr.get("details_json")
        if isinstance(dj, str) and dj.strip():
            try:
                rr["details"] = json.loads(dj)
            except Exception:
                rr["details"] = {}
        else:
            rr["details"] = {}
        rr.pop("details_json", None)
        out.append(rr)
    return out


def purge_inventory_and_qr_history(include_generated_qr=True):
    """
    Полная очистка истории инвентаризаций.
    Удаляет кампании, сканы, утери, отметки завершённых типов и сверки целых листов.
    При include_generated_qr=True также очищает журнал сгенерированных QR.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mirror_inventory_scans")
            cur.execute("DELETE FROM mirror_inventory_losses")
            cur.execute("DELETE FROM mirror_inventory_type_completion")
            try:
                cur.execute("DELETE FROM mirror_inventory_full_sheet_checks")
            except Exception:
                pass
            cur.execute("DELETE FROM mirror_inventory_campaigns")
            if include_generated_qr:
                cur.execute("DELETE FROM mirror_generated_qr_log")
    return True


def list_inventory_scans(limit=2000, session_key=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            if session_key:
                cur.execute(
                    """
                    SELECT id, item_type, stock_ref_id, unique_number, size_text, session_key, campaign_id,
                           actor_user_id, actor_login, scanned_at
                    FROM mirror_inventory_scans
                    WHERE session_key = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (str(session_key)[:64], int(limit)),
                )
            else:
                cur.execute(
                    """
                    SELECT id, item_type, stock_ref_id, unique_number, size_text, session_key, campaign_id,
                           actor_user_id, actor_login, scanned_at
                    FROM mirror_inventory_scans
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (int(limit),),
                )
            return cur.fetchall() or []


def insert_inventory_loss(
    item_type,
    stock_ref_id=None,
    unique_number="",
    reason_text="",
    session_key="",
    actor_user_id=None,
    actor_login="",
    campaign_id=None,
):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mirror_inventory_losses
                (item_type, stock_ref_id, unique_number, reason_text, session_key, campaign_id, actor_user_id, actor_login)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    str(item_type or "")[:16],
                    stock_ref_id,
                    str(unique_number or "")[:64],
                    str(reason_text or ""),
                    str(session_key or "")[:64],
                    int(campaign_id) if campaign_id is not None else None,
                    actor_user_id,
                    str(actor_login or "")[:128],
                ),
            )
            lid = (cur.fetchone() or {}).get("id")
    try:
        if str(item_type or "").strip().lower() == "profile":
            event_kind = "inventory:damaged"
            low = str(reason_text or "").lower()
            if "утер" in low or "lost" in low:
                event_kind = "inventory:lost"
            add_profile_cut_event(
                stock_id=(int(stock_ref_id) if stock_ref_id is not None else None),
                order_id=None,
                batch_id=None,
                event_type=event_kind,
                reason_text=str(reason_text or ""),
                actor_user_id=(int(actor_user_id) if actor_user_id is not None else None),
                actor_login=str(actor_login or ""),
                actor_role="",
                payload_json={
                    "inventory_loss_id": lid,
                    "session_key": str(session_key or "")[:64],
                    "campaign_id": int(campaign_id) if campaign_id is not None else None,
                    "unique_number": str(unique_number or "")[:64],
                },
            )
    except Exception:
        pass
    return lid


def list_inventory_losses(limit=500):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, item_type, stock_ref_id, unique_number, reason_text, session_key, campaign_id,
                       actor_user_id, actor_login, created_at
                FROM mirror_inventory_losses
                ORDER BY id DESC
                LIMIT %s
                """,
                (int(limit),),
            )
            return cur.fetchall() or []


def _ensure_inventory_full_sheet_checks_table():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS mirror_inventory_full_sheet_checks (
                    id SERIAL PRIMARY KEY,
                    campaign_id INTEGER NOT NULL,
                    type_key VARCHAR(512) NOT NULL,
                    expected_qty INTEGER NOT NULL DEFAULT 0,
                    found_qty INTEGER NOT NULL DEFAULT 0,
                    missing_qty INTEGER NOT NULL DEFAULT 0,
                    session_key VARCHAR(64) DEFAULT '',
                    actor_user_id INTEGER NULL,
                    actor_login VARCHAR(128) DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (campaign_id, type_key)
                )
                """
            )


def upsert_inventory_full_sheet_check(
    campaign_id,
    type_key,
    expected_qty,
    found_qty,
    missing_qty,
    session_key="",
    actor_user_id=None,
    actor_login="",
):
    _ensure_inventory_full_sheet_checks_table()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mirror_inventory_full_sheet_checks
                (campaign_id, type_key, expected_qty, found_qty, missing_qty, session_key, actor_user_id, actor_login, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (campaign_id, type_key) DO UPDATE SET
                    expected_qty = EXCLUDED.expected_qty,
                    found_qty = EXCLUDED.found_qty,
                    missing_qty = EXCLUDED.missing_qty,
                    session_key = EXCLUDED.session_key,
                    actor_user_id = EXCLUDED.actor_user_id,
                    actor_login = EXCLUDED.actor_login,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """,
                (
                    int(campaign_id),
                    str(type_key or "")[:512],
                    max(0, int(expected_qty or 0)),
                    max(0, int(found_qty or 0)),
                    max(0, int(missing_qty or 0)),
                    str(session_key or "")[:64],
                    actor_user_id,
                    str(actor_login or "")[:128],
                ),
            )
            row = cur.fetchone() or {}
            return row.get("id")


def list_inventory_full_sheet_checks(campaign_id):
    _ensure_inventory_full_sheet_checks_table()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, campaign_id, type_key, expected_qty, found_qty, missing_qty, session_key, actor_user_id, actor_login, updated_at
                FROM mirror_inventory_full_sheet_checks
                WHERE campaign_id = %s
                ORDER BY type_key
                """,
                (int(campaign_id),),
            )
            return cur.fetchall() or []

def list_inventory_scan_unique_numbers(session_key=None):
    """Уникальные номера уже внесённые в инвентаризацию (для дедупликации)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            if session_key:
                cur.execute(
                    "SELECT DISTINCT unique_number FROM mirror_inventory_scans WHERE session_key = %s AND unique_number <> ''",
                    (str(session_key)[:64],),
                )
            else:
                cur.execute("SELECT DISTINCT unique_number FROM mirror_inventory_scans WHERE unique_number <> ''")
            return {str(r["unique_number"]) for r in (cur.fetchall() or []) if r.get("unique_number")}


def profile_stock_inventory_type_key(row) -> str:
    """Ключ типа профиля для инвентаризации: серия, иначе имя (без учёта цвета)."""
    if not row:
        return ""
    s = str(row.get("series") or "").strip()
    if s:
        return s
    return str(row.get("name") or "").strip()


def inventory_catalog_entries():
    """
    Плоский каталог позиций с полем type_key: для стекла — name материала, для профиля — series или name.
    kind: glass | profile; number — код этикетки / склада.
    """
    ttl = _inventory_catalog_cache_ttl_seconds()
    if ttl > 0:
        now = time.time()
        with _inventory_catalog_cache_lock:
            cached_entries = _inventory_catalog_cache.get("entries")
            expires_at = float(_inventory_catalog_cache.get("expires_at") or 0.0)
            if cached_entries is not None and expires_at > now:
                return [dict(x) for x in cached_entries]

    out = []
    for r in get_all_remnants() or []:
        num = str((r.get("unique_number") or "")).strip()
        if not num:
            continue
        tk = str(r.get("name") or "").strip()
        if not tk:
            continue
        out.append(
            {
                "kind": "glass",
                "number": num,
                "stock_ref_id": r.get("id"),
                "size_text": "%s×%s" % (r.get("width_mm") or "—", r.get("height_mm") or "—"),
                "type_key": tk,
            }
        )
    for fs in get_all_full_sheets() or []:
        try:
            qty = int(fs.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        tk = str(fs.get("name") or "").strip()
        if not tk:
            continue
        wh = str(fs.get("warehouse_number") or "").strip()
        num = wh if wh else "L%s" % (fs.get("id") or "")
        out.append(
            {
                "kind": "glass",
                "number": num,
                "stock_ref_id": fs.get("id"),
                "size_text": "%s×%s" % (fs.get("width_mm") or "—", fs.get("height_mm") or "—"),
                "type_key": tk,
            }
        )
    prof_rows = get_profile_stock() or []
    prof_ids = []
    for row in prof_rows:
        sid = row.get("id")
        if sid is None:
            continue
        try:
            prof_ids.append(int(sid))
        except (TypeError, ValueError):
            continue
    ensure_profile_label_numbers_bulk(prof_ids)
    labels_by_stock = get_profile_labels_by_stock_ids(prof_ids)
    for row in prof_rows:
        sid = row.get("id")
        if sid is None:
            continue
        lab = labels_by_stock.get(int(sid)) or {}
        num = str(lab.get("unique_number") or "").strip()
        if not num:
            continue
        tk = profile_stock_inventory_type_key(row)
        if not tk:
            continue
        lm = row.get("length_mm")
        out.append(
            {
                "kind": "profile",
                "number": num,
                "stock_ref_id": int(sid),
                "size_text": ("%s мм" % lm) if lm is not None else "—",
                "type_key": tk,
            }
        )
    if ttl > 0:
        with _inventory_catalog_cache_lock:
            _inventory_catalog_cache["entries"] = [dict(x) for x in out]
            _inventory_catalog_cache["expires_at"] = time.time() + ttl
    return out


def inventory_catalog_lists():
    """
    Ожидаемые позиции для инвентаризации: стекло (остатки + целые листы с quantity>0),
    профиль (склад, с номером этикетки).
    Каждый элемент: { 'kind': 'glass'|'profile', 'number': str, 'stock_ref_id': int|None, 'size_text': str }.
    """
    glass = []
    profiles = []
    for e in inventory_catalog_entries():
        item = {
            "kind": e["kind"],
            "number": e["number"],
            "stock_ref_id": e.get("stock_ref_id"),
            "size_text": e.get("size_text") or "",
        }
        if e["kind"] == "glass":
            glass.append(item)
        else:
            profiles.append(item)
    return {"glass": glass, "profile": profiles}


def inventory_glass_type_keys_on_stock():
    """Уникальные name по остаткам (с номером) и листам с quantity>0. Без полного каталога этикеток."""
    keys = set()
    for r in get_all_remnants() or []:
        num = str((r.get("unique_number") or "")).strip()
        if not num:
            continue
        tk = str(r.get("name") or "").strip()
        if tk:
            keys.add(tk)
    for fs in get_all_full_sheets() or []:
        try:
            qty = int(fs.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        tk = str(fs.get("name") or "").strip()
        if tk:
            keys.add(tk)
    return sorted(keys)


def inventory_profile_type_keys_on_stock():
    """Уникальные series/name по складу профилей. Без bulk-нумерации этикеток — для быстрого UI."""
    keys = set()
    for row in get_profile_stock() or []:
        tk = profile_stock_inventory_type_key(row)
        if tk:
            keys.add(tk)
    return sorted(keys)


def list_inventory_completed_type_keys():
    """Пары (domain, type_key) для «красных» карточек на десктопе."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT domain, type_key FROM mirror_inventory_type_completion
                    """
                )
                return {(str(r["domain"]), str(r["type_key"])) for r in (cur.fetchall() or [])}
            except Exception:
                return set()


def get_active_inventory_campaign():
    with get_connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT * FROM mirror_inventory_campaigns
                    WHERE status = 'active'
                    ORDER BY id DESC
                    LIMIT 1
                    """
                )
                return cur.fetchone()
            except Exception:
                return None


def get_latest_inventory_campaign(statuses=None):
    """
    Последняя кампания по id с фильтром статусов.
    По умолчанию: active + paused (для десктопного управления паузой/отменой).
    """
    sts = [str(s).strip().lower() for s in (statuses or ["active", "paused"]) if str(s).strip()]
    if not sts:
        return None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM mirror_inventory_campaigns
                WHERE status = ANY(%s)
                ORDER BY id DESC
                LIMIT 1
                """,
                (sts,),
            )
            return cur.fetchone()


def get_inventory_campaign_by_id(cid):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM mirror_inventory_campaigns WHERE id = %s", (int(cid),))
            return cur.fetchone()


def create_inventory_campaign(glass_type_keys, profile_type_keys, started_by_user_id=None, started_by_login=""):
    if get_active_inventory_campaign():
        return None, "active_exists"
    glist = [str(x).strip() for x in (glass_type_keys or []) if str(x).strip()]
    plist = [str(x).strip() for x in (profile_type_keys or []) if str(x).strip()]
    if not glist and not plist:
        return None, "empty_scope"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO mirror_inventory_campaigns
                (status, glass_type_keys, profile_type_keys, started_by_user_id, started_by_login)
                VALUES ('active', %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    json.dumps(glist, ensure_ascii=False),
                    json.dumps(plist, ensure_ascii=False),
                    started_by_user_id,
                    str(started_by_login or "")[:128],
                ),
            )
            rid = (cur.fetchone() or {}).get("id")
    return get_inventory_campaign_by_id(int(rid)), None


def pause_inventory_campaign(campaign_id):
    """Ставит активную кампанию на паузу."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE mirror_inventory_campaigns
                SET status = 'paused'
                WHERE id = %s AND status = 'active'
                RETURNING id
                """,
                (int(campaign_id),),
            )
            return bool(cur.fetchone())


def resume_inventory_campaign(campaign_id):
    """Снимает с паузы. Если есть другая active-кампания — не возобновляет."""
    if get_active_inventory_campaign():
        return False, "active_exists"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE mirror_inventory_campaigns
                SET status = 'active'
                WHERE id = %s AND status = 'paused'
                RETURNING id
                """,
                (int(campaign_id),),
            )
            return (bool(cur.fetchone()), None)


def cancel_inventory_campaign_and_wipe_progress(campaign_id, summary=None):
    """
    Отмена текущей кампании:
    - статус -> cancelled,
    - удаляются все сканы/утери/сверки целых листов по campaign_id,
    - удаляются привязанные completion-маркеры (на случай ручных правок).
    """
    cid = int(campaign_id)
    sj = json.dumps(summary, ensure_ascii=False) if summary is not None else "{}"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mirror_inventory_scans WHERE campaign_id = %s", (cid,))
            cur.execute("DELETE FROM mirror_inventory_losses WHERE campaign_id = %s", (cid,))
            try:
                cur.execute("DELETE FROM mirror_inventory_full_sheet_checks WHERE campaign_id = %s", (cid,))
            except Exception:
                pass
            cur.execute("DELETE FROM mirror_inventory_type_completion WHERE last_completed_campaign_id = %s", (cid,))
            cur.execute(
                """
                UPDATE mirror_inventory_campaigns
                SET status = 'cancelled', completed_at = CURRENT_TIMESTAMP, summary_json = %s
                WHERE id = %s AND status IN ('active', 'paused')
                RETURNING id
                """,
                (sj, cid),
            )
            return bool(cur.fetchone())


def cancel_inventory_campaign(campaign_id, summary=None):
    sj = json.dumps(summary, ensure_ascii=False) if summary is not None else "{}"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE mirror_inventory_campaigns
                SET status = 'cancelled', completed_at = CURRENT_TIMESTAMP, summary_json = %s
                WHERE id = %s AND status = 'active'
                """,
                (sj, int(campaign_id)),
            )


def inventory_campaign_json_key_list(campaign_row, key):
    if not campaign_row:
        return []
    raw = campaign_row.get(key) or "[]"
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    try:
        return [str(x).strip() for x in (json.loads(raw) if raw else []) if str(x).strip()]
    except Exception:
        return []


def inventory_catalog_entries_for_campaign(campaign_row):
    gkeys = set(inventory_campaign_json_key_list(campaign_row, "glass_type_keys"))
    pkeys = set(inventory_campaign_json_key_list(campaign_row, "profile_type_keys"))
    out = []
    if gkeys:
        for r in get_all_remnants() or []:
            num = str((r.get("unique_number") or "")).strip()
            if not num:
                continue
            tk = str(r.get("name") or "").strip()
            if not tk or tk not in gkeys:
                continue
            out.append(
                {
                    "kind": "glass",
                    "number": num,
                    "stock_ref_id": r.get("id"),
                    "size_text": "%s×%s" % (r.get("width_mm") or "—", r.get("height_mm") or "—"),
                    "type_key": tk,
                    "stock_kind": "remnant",
                }
            )
        for fs in get_all_full_sheets() or []:
            try:
                qty = int(fs.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue
            tk = str(fs.get("name") or "").strip()
            if not tk or tk not in gkeys:
                continue
            wh = str(fs.get("warehouse_number") or "").strip()
            num = wh if wh else "L%s" % (fs.get("id") or "")
            out.append(
                {
                    "kind": "glass",
                    "number": num,
                    "stock_ref_id": fs.get("id"),
                    "size_text": "%s×%s" % (fs.get("width_mm") or "—", fs.get("height_mm") or "—"),
                    "type_key": tk,
                    "stock_kind": "full_sheet",
                    "full_sheet_qty": qty,
                }
            )
    if pkeys:
        prof_rows = list_profile_stock_matching_inventory_type_keys(pkeys) or []
        prof_rows_filtered = []
        prof_ids = []
        for row in prof_rows:
            tk = profile_stock_inventory_type_key(row)
            if not tk or tk not in pkeys:
                continue
            sid = row.get("id")
            if sid is None:
                continue
            try:
                sid_i = int(sid)
            except (TypeError, ValueError):
                continue
            prof_rows_filtered.append((row, tk, sid_i))
            prof_ids.append(sid_i)
        if prof_ids:
            labels_by_stock = get_profile_labels_by_stock_ids(prof_ids)
            for row, tk, sid_i in prof_rows_filtered:
                lab = labels_by_stock.get(sid_i) or {}
                num = str(lab.get("unique_number") or "").strip()
                if not num:
                    continue
                lm = row.get("length_mm")
                out.append(
                    {
                        "kind": "profile",
                        "number": num,
                        "stock_ref_id": sid_i,
                        "size_text": ("%s мм" % lm) if lm is not None else "—",
                        "type_key": tk,
                    }
                )
    return out


def inventory_campaign_expected_pairs(campaign_row):
    pairs = set()
    for e in inventory_catalog_entries_for_campaign(campaign_row):
        pairs.add((str(e["kind"]), str(e["number"]).strip()))
    return pairs


def inventory_campaign_closed_pairs(campaign_id):
    pairs = set()
    cid = int(campaign_id)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT item_type, unique_number
                FROM (
                    SELECT item_type, unique_number
                    FROM mirror_inventory_scans
                    WHERE campaign_id = %s AND COALESCE(unique_number, '') <> ''
                    UNION ALL
                    SELECT item_type, unique_number
                    FROM mirror_inventory_losses
                    WHERE campaign_id = %s AND COALESCE(unique_number, '') <> ''
                ) q
                """,
                (cid, cid),
            )
            for r in cur.fetchall() or []:
                pairs.add((str(r["item_type"]).strip().lower(), str(r["unique_number"]).strip()))
    return pairs


def inventory_types_still_seeking(campaign_row):
    """Типы (стекло/профиль), по которым ещё есть незакрытые позиции."""
    cid = int(campaign_row.get("id") or 0)
    if cid < 1:
        return {"glass": [], "profile": []}
    closed = inventory_campaign_closed_pairs(cid)
    g_pending = set()
    p_pending = set()
    for e in inventory_catalog_entries_for_campaign(campaign_row):
        pair = (e["kind"], str(e["number"]).strip())
        if pair in closed:
            continue
        if e["kind"] == "glass":
            g_pending.add(e["type_key"])
        else:
            p_pending.add(e["type_key"])
    return {"glass": sorted(g_pending), "profile": sorted(p_pending)}


def inventory_pending_numbers_for_campaign(campaign_row, kind_filter=None, type_key_filter=None):
    """
    Незакрытые номера. kind_filter: glass | profile | None (все).
    type_key_filter: строка или None (все типы внутри kind).
    """
    cid = int(campaign_row.get("id") or 0)
    if cid < 1:
        return []
    closed = inventory_campaign_closed_pairs(cid)
    out = []
    kf = str(kind_filter).strip().lower() if kind_filter else None
    tf = str(type_key_filter).strip() if type_key_filter else None
    for e in inventory_catalog_entries_for_campaign(campaign_row):
        if kf and e["kind"] != kf:
            continue
        if tf and e["type_key"] != tf:
            continue
        pair = (e["kind"], str(e["number"]).strip())
        if pair not in closed:
            out.append(
                {
                    "kind": e["kind"],
                    "number": e["number"],
                    "type_key": e["type_key"],
                    "size_text": e.get("size_text") or "",
                }
            )
    return out


def _record_inventory_type_completions(campaign_id, campaign_row):
    glist = inventory_campaign_json_key_list(campaign_row, "glass_type_keys")
    plist = inventory_campaign_json_key_list(campaign_row, "profile_type_keys")
    with get_connection() as conn:
        with conn.cursor() as cur:
            for tk in glist:
                cur.execute(
                    """
                    INSERT INTO mirror_inventory_type_completion
                    (domain, type_key, last_completed_campaign_id, completed_at)
                    VALUES ('glass', %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (domain, type_key) DO UPDATE SET
                        last_completed_campaign_id = EXCLUDED.last_completed_campaign_id,
                        completed_at = EXCLUDED.completed_at
                    """,
                    (str(tk)[:512], int(campaign_id)),
                )
            for tk in plist:
                cur.execute(
                    """
                    INSERT INTO mirror_inventory_type_completion
                    (domain, type_key, last_completed_campaign_id, completed_at)
                    VALUES ('profile', %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (domain, type_key) DO UPDATE SET
                        last_completed_campaign_id = EXCLUDED.last_completed_campaign_id,
                        completed_at = EXCLUDED.completed_at
                    """,
                    (str(tk)[:512], int(campaign_id)),
                )


def try_complete_inventory_campaign(campaign_id):
    """Если все ожидаемые пары закрыты — завершает кампанию и фиксирует типы. Возвращает dict."""
    row = get_inventory_campaign_by_id(int(campaign_id))
    if not row or str(row.get("status") or "") != "active":
        return {"completed": False, "reason": "not_active"}
    exp = inventory_campaign_expected_pairs(row)
    closed = inventory_campaign_closed_pairs(int(campaign_id))
    if not exp:
        summary = {"expected_total": 0, "note": "no_matching_stock"}
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE mirror_inventory_campaigns
                    SET status = 'completed', completed_at = CURRENT_TIMESTAMP, summary_json = %s
                    WHERE id = %s AND status = 'active'
                    """,
                    (json.dumps(summary, ensure_ascii=False), int(campaign_id)),
                )
        _record_inventory_type_completions(int(campaign_id), row)
        return {"completed": True, "summary": summary}
    if not exp <= closed:
        return {
            "completed": False,
            "remaining_count": len(exp - closed),
        }
    summary = {
        "expected_total": len(exp),
        "closed_total": len(closed & exp),
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE mirror_inventory_campaigns
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP, summary_json = %s
                WHERE id = %s AND status = 'active'
                """,
                (json.dumps(summary, ensure_ascii=False), int(campaign_id)),
            )
    _record_inventory_type_completions(int(campaign_id), row)
    return {"completed": True, "summary": summary}


def inventory_campaign_state_payload(campaign_row):
    """Сводка для API /production/inventory."""
    cid = int(campaign_row.get("id") or 0)
    exp = inventory_campaign_expected_pairs(campaign_row)
    closed = inventory_campaign_closed_pairs(cid)
    glass_exp = {p for p in exp if p[0] == "glass"}
    prof_exp = {p for p in exp if p[0] == "profile"}
    glass_closed = len(glass_exp & closed)
    prof_closed = len(prof_exp & closed)
    seeking = inventory_types_still_seeking(campaign_row)
    return {
        "campaign_id": cid,
        "status": campaign_row.get("status"),
        "glass_total": len(glass_exp),
        "glass_scanned_closed": glass_closed,
        "profile_total": len(prof_exp),
        "profile_scanned_closed": prof_closed,
        "total_expected": len(exp),
        "total_closed": len(exp & closed),
        "is_complete": (exp <= closed) if exp else False,
        "types_still_seeking": seeking,
    }


def resolve_inventory_scan_number(kind: str, number: str):
    """
    kind: glass | profile. number — как в QR или введено вручную.
    Возвращает dict с keys kind, number, stock_ref_id, size_text или None.
    """
    n = str(number or "").strip()
    if not n:
        return None
    k = str(kind or "").strip().lower()
    if k == "profile":
        lab = get_profile_label_by_unique_number(n)
        if not lab:
            return None
        sid = lab.get("stock_id")
        row = get_profile_stock_row(int(sid)) if sid is not None else None
        lm = (row or {}).get("length_mm")
        return {
            "kind": "profile",
            "number": str(lab.get("unique_number") or n),
            "stock_ref_id": int(sid) if sid is not None else None,
            "size_text": ("%s мм" % lm) if lm is not None else "—",
        }
    if k == "glass":
        for r in get_all_remnants() or []:
            if str(r.get("unique_number") or "").strip() == n:
                return {
                    "kind": "glass",
                    "number": n,
                    "stock_ref_id": r.get("id"),
                    "size_text": "%s×%s" % (r.get("width_mm") or "—", r.get("height_mm") or "—"),
                }
        for fs in get_all_full_sheets() or []:
            wh = str(fs.get("warehouse_number") or "").strip()
            alt = "L%s" % (fs.get("id") or "")
            if wh == n or alt == n or str(fs.get("id") or "") == n:
                try:
                    qty = int(fs.get("quantity") or 0)
                except (TypeError, ValueError):
                    qty = 0
                if qty <= 0:
                    return None
                return {
                    "kind": "glass",
                    "number": n,
                    "stock_ref_id": fs.get("id"),
                    "size_text": "%s×%s" % (fs.get("width_mm") or "—", fs.get("height_mm") or "—"),
                }
        return None
    return None


def get_blocks_zamer_rows_by_mirror_order_ids(order_ids):
    """Последняя заявка blocks_zamer по каждому mirror_order_id (даты/флаги портала для фильтров и подсказок)."""
    ids = [int(x) for x in order_ids if x is not None]
    if not ids:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (mirror_order_id)
                    z.mirror_order_id, z.date_from, z.date_to, z.is_measure, z.is_install, z.is_delivery, z.status,
                    COALESCE(z.assigned_to_login, '') AS assigned_to_login,
                    COALESCE(TRIM(CONCAT(COALESCE(u.name,''), ' ', COALESCE(u.surname,''))), '') AS assigned_to_name,
                    EXISTS (
                        SELECT 1 FROM blocks_zamer_file f
                        WHERE f.zamer_id = z.id
                          AND LOWER(COALESCE(TRIM(f.file_kind), 'measure')) = 'measure'
                    ) AS has_measure_file,
                    EXISTS (
                        SELECT 1 FROM blocks_zamer_file f
                        WHERE f.zamer_id = z.id
                          AND LOWER(TRIM(COALESCE(f.file_kind, ''))) = 'delivery'
                    ) AS has_delivery_file,
                    EXISTS (
                        SELECT 1 FROM blocks_zamer_file f
                        WHERE f.zamer_id = z.id
                          AND LOWER(TRIM(COALESCE(f.file_kind, ''))) = 'install'
                    ) AS has_install_file
                FROM blocks_zamer z
                LEFT JOIN main_users u ON LOWER(TRIM(u.login)) = LOWER(TRIM(z.assigned_to_login))
                WHERE z.mirror_order_id = ANY(%s)
                ORDER BY z.mirror_order_id, z.id DESC
                """,
                (ids,),
            )
            rows = cur.fetchall() or []
    out = {}
    for r in rows:
        rr = dict(r)
        oid = rr.get("mirror_order_id")
        if oid is not None:
            out[int(oid)] = rr
    return out


def get_orders_completed():
    """Заказы со статусом completed (готовые заказы)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.id, o.client_id, o.client_name, o.created_at, o.status, o.accepted_at, o.notes, o.k_number,
                       COALESCE(c.name, o.client_name, '') AS client_name
                FROM mirror_orders o
                LEFT JOIN mirror_clients c ON c.id = o.client_id
                WHERE o.status = %s
                ORDER BY o.accepted_at DESC NULLS LAST, o.id DESC
                """,
                ('completed',)
            )
            return cur.fetchall()


def order_cutout_area_m2(order_id):
    """Суммарная площадь выкроек заказа в м² (из layout pieces), до 4 знаков после запятой."""
    rows = get_cut_results(order_id)
    total_mm2 = 0
    for r in rows:
        lay = r.get('layout') if isinstance(r.get('layout'), dict) else {}
        for p in lay.get('pieces') or []:
            w = int(p.get('w') or p.get('width_mm') or 0)
            h = int(p.get('h') or p.get('height_mm') or 0)
            total_mm2 += w * h
    return round(total_mm2 / 1_000_000.0, 4)


def order_ready_summary(order_id):
    """Сводка по готовому заказу: листы-источники, созданные остатки, деловые остатки, выкрои, площадь м²."""
    order = get_order(order_id)
    if not order:
        return None
    rows = get_cut_results(order_id)
    from_sheets = []
    sheets_obtained = []
    business_rects_list = []
    cutouts = []
    for r in rows:
        lay = r.get('layout') if isinstance(r.get('layout'), dict) else {}
        st = r.get('sheet_type') or lay.get('sheet_type')
        sid = r.get('sheet_id') if r.get('sheet_id') is not None else lay.get('sheet_id')
        if st == 'full' and sid:
            sh = get_full_sheet_by_id(sid)
            from_sheets.append(sh.get('name', 'Лист #%s' % sid) if sh else 'Лист #%s' % sid)
        elif st == 'remnant' and sid:
            rem = get_remnant_by_id(sid)
            from_sheets.append(rem.get('name', 'Остаток #%s' % sid) if rem else 'Остаток #%s' % sid)
        else:
            from_sheets.append('Новый лист %s×%s' % (lay.get('sheet_width'), lay.get('sheet_height')))
        for rc in r.get('remnants_created') or []:
            sheets_obtained.append(rc.get('name', '') or '%s×%s' % (rc.get('height_mm'), rc.get('width_mm')))
        for br in lay.get('business_rects') or []:
            business_rects_list.append('%s×%s' % (br.get('w'), br.get('h')))
        for p in lay.get('pieces') or []:
            cutouts.append({'w': p.get('w') or p.get('width_mm'), 'h': p.get('h') or p.get('height_mm')})
    area_m2 = order_cutout_area_m2(order_id)
    return {
        'order': order,
        'client_name': order.get('client_name') or '',
        'created_at': order.get('created_at'),
        'accepted_at': order.get('accepted_at'),
        'from_sheets': from_sheets,
        'sheets_obtained': sheets_obtained,
        'business_rects': business_rects_list,
        'cutouts': cutouts,
        'total_area_m2': area_m2,
    }


def delete_order(order_id):
    """Удалить заказ навсегда вместе с записями, которые видит веб-панель."""
    oid = int(order_id)
    report = {
        "order_id": oid,
        "restored_as_is": False,
        "converted_to_remnants": False,
        "created_remnants_count": 0,
        "labels_pdf_path": None,
        "note": "",
    }
    # 1) Если есть только черновой раскрой (материал ещё не подтверждён в архиве),
    # возвращаем «как было»: резерв листа назад + удаление деловых остатков черновика.
    archives = get_cut_archives_by_order_id(oid) or []
    if not archives:
        try:
            _restore_reserved_sources_from_cut_results(oid)
            _delete_remnants_from_cut_layout(oid)
            report["restored_as_is"] = True
        except Exception:
            pass
    else:
        # 2) Если раскрой уже подтверждён/выполнен: клиентские куски переводим в остатки склада
        # и печатаем PDF с уникальными этикетками остатков.
        try:
            from logic.qr_utils import remnant_qr_url
            from logic.labels import generate_labels_pdf_multi
            import datetime
            import os
            import re

            created_rows = []
            th_re = re.compile(r"(\d+)\s*мм", re.IGNORECASE)
            for arch_item in archives:
                arch = arch_item[0] if isinstance(arch_item, (tuple, list)) and len(arch_item) > 0 else None
                details = arch_item[1] if isinstance(arch_item, (tuple, list)) and len(arch_item) > 1 else []
                if not isinstance(arch, dict):
                    continue
                mat_name = str(arch.get("sheet_name") or "Материал").strip() or "Материал"
                th = 4
                m_th = th_re.search(mat_name)
                if m_th:
                    try:
                        th = max(1, int(m_th.group(1)))
                    except Exception:
                        th = 4
                for d in (details or []):
                    if not isinstance(d, dict):
                        continue
                    if str(d.get("item_kind") or "").strip().lower() != "piece":
                        continue
                    try:
                        w = int(d.get("width_mm") or 0)
                        h = int(d.get("height_mm") or 0)
                    except Exception:
                        continue
                    if w <= 0 or h <= 0:
                        continue
                    num = get_next_label_number()
                    un = str(num)
                    rid = insert_remnant(
                        mat_name,
                        int(h),
                        int(w),
                        un,
                        remnant_qr_url(un),
                        thickness_mm=int(th),
                        label_number=int(num),
                    )
                    add_remnant_history(
                        rid,
                        oid,
                        "created_from_cancelled_order",
                        None,
                        json.dumps(
                            {
                                "from_order_delete": True,
                                "order_id": oid,
                                "cut_archive_id": arch.get("id"),
                                "source_kind": "piece",
                            },
                            ensure_ascii=False,
                        ),
                    )
                    created_rows.append(
                        {
                            "unique_number": un,
                            "name": mat_name,
                            "height_mm": int(h),
                            "width_mm": int(w),
                            "label_number": int(num),
                            "thickness_mm": int(th),
                        }
                    )
            if created_rows:
                out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_deleted_order_labels")
                os.makedirs(out_dir, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                pdf_path = os.path.join(out_dir, "order_%s_deleted_remnants_%s.pdf" % (oid, ts))
                generate_labels_pdf_multi(created_rows, [], pdf_path)
                report["labels_pdf_path"] = pdf_path
                report["created_remnants_count"] = len(created_rows)
                report["converted_to_remnants"] = True
                report["note"] = "Клиентские куски переведены в остатки и этикетки сохранены в PDF."
        except Exception:
            pass

    with get_connection() as conn:
        with conn.cursor() as cur:
            # WEB_SERVICE / портал: удаляем привязанные заявки blocks_zamer целиком,
            # чтобы в очередях Замер/Монтаж/Доставка не оставались «осиротевшие» строки.
            try:
                cur.execute("SELECT to_regclass('public.blocks_zamer') AS t")
                t = (cur.fetchone() or {}).get("t")
                if t:
                    cur.execute("DELETE FROM blocks_zamer WHERE mirror_order_id = %s", (oid,))
            except Exception:
                # В некоторых окружениях таблица может отсутствовать.
                pass

            # В истории остатков order_id хранится как ссылка без ON DELETE SET NULL,
            # поэтому перед удалением заказа отвязываем историю вручную.
            cur.execute(
                "UPDATE mirror_remnant_history SET order_id = NULL WHERE order_id = %s",
                (oid,),
            )
            cur.execute("DELETE FROM mirror_orders WHERE id = %s", (oid,))
            rc = cur.rowcount
    globals()["_last_delete_order_report"] = report
    _invalidate_orders_cache()
    return rc


def get_last_delete_order_report(order_id=None):
    rep = globals().get("_last_delete_order_report") or {}
    if not isinstance(rep, dict):
        return {}
    if order_id is None:
        return dict(rep)
    try:
        if int(rep.get("order_id") or 0) != int(order_id):
            return {}
    except Exception:
        return {}
    return dict(rep)


def delete_all_orders():
    """Удалить все заказы (каскадно: позиции, результаты раскроя, архив резов). Возвращает число удалённых заказов."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM mirror_orders")
            n = cur.fetchone()['n'] or 0
            cur.execute("UPDATE mirror_remnant_history SET order_id = NULL WHERE order_id IS NOT NULL")
            cur.execute("DELETE FROM mirror_orders")
            return n


def get_order_items(order_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, order_id, material_name, height_mm, width_mm, quantity, recipient_text, edge_treatment_json, thickness_mm FROM mirror_order_items WHERE order_id = %s",
                (order_id,)
            )
            rows = cur.fetchall()
            for r in rows:
                if r.get('edge_treatment_json'):
                    try:
                        r['edge_treatment'] = json.loads(r['edge_treatment_json']) if isinstance(r['edge_treatment_json'], str) else r['edge_treatment_json']
                    except Exception:
                        r['edge_treatment'] = {}
                else:
                    r['edge_treatment'] = {}
            return rows


def get_order_items_bulk(order_ids):
    """Все строки mirror_order_items для набора заказов одним запросом → { order_id: [ rows ... ] }."""
    if not order_ids:
        return {}

    def _decorate_row(r):
        if r.get('edge_treatment_json'):
            try:
                r['edge_treatment'] = json.loads(r['edge_treatment_json']) if isinstance(r['edge_treatment_json'], str) else r['edge_treatment_json']
            except Exception:
                r['edge_treatment'] = {}
        else:
            r['edge_treatment'] = {}

    ids = sorted({int(x) for x in order_ids if x is not None})
    if not ids:
        return {}
    placeholders = ','.join(['%s'] * len(ids))
    sql = (
        'SELECT id, order_id, material_name, height_mm, width_mm, quantity, recipient_text, edge_treatment_json, thickness_mm '
        'FROM mirror_order_items WHERE order_id IN (' + placeholders + ')'
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(ids))
            rows = cur.fetchall()
    out = {}
    for r in rows:
        oid = r.get('order_id')
        if oid is None:
            continue
        _decorate_row(r)
        out.setdefault(oid, []).append(r)
    return out


def add_order_item(order_id, material_name, height_mm, width_mm, quantity=1, recipient_text=None, edge_treatment_json=None, thickness_mm=4):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mirror_order_items (order_id, material_name, height_mm, width_mm, quantity, recipient_text, edge_treatment_json, thickness_mm) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (order_id, material_name, height_mm, width_mm, quantity, recipient_text, edge_treatment_json, int(thickness_mm) if thickness_mm is not None else 4)
            )
            iid = cur.fetchone()['id']
    _invalidate_orders_cache()
    return iid


# --- Remnant history (for QR page) ---
def add_remnant_history(remnant_id, order_id, action_type, user_info=None, details_json=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mirror_remnant_history (remnant_id, order_id, action_type, user_info, details_json) VALUES (%s, %s, %s, %s, %s)",
                (remnant_id, order_id, action_type, user_info, details_json)
            )


def get_remnant_history(remnant_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT h.id, h.remnant_id, h.order_id, h.action_type, h.user_info, h.details_json, h.created_at
                FROM mirror_remnant_history h
                WHERE h.remnant_id = %s
                ORDER BY h.created_at DESC
                """,
                (remnant_id,)
            )
            return cur.fetchall()


def remnant_ids_with_history(remnant_ids):
    """Множество id остатков, у которых есть хотя бы одна запись в mirror_remnant_history.
    Один round-trip вместо N вызовов get_remnant_history при заполнении списка листов."""
    ids = []
    for x in remnant_ids or []:
        try:
            if x is not None:
                ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not ids:
        return set()
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT remnant_id FROM mirror_remnant_history WHERE remnant_id = ANY(%s)",
                (ids,),
            )
            return {int(row["remnant_id"]) for row in cur.fetchall()}


def get_remnant_ids_by_order_id(order_id):
    """Return list of remnant_id created for this order (from history, after order completed)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT remnant_id FROM mirror_remnant_history WHERE order_id = %s ORDER BY remnant_id",
                (order_id,)
            )
            return [row['remnant_id'] for row in cur.fetchall()]


def get_remnant_display_numbers_by_order_id(order_id):
    """Список номеров/подписей для деловых остатков в порядке создания (лист за листом, как в раскрое). Для PDF и схемы."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT remnant_id FROM mirror_remnant_history WHERE order_id = %s AND action_type = 'created' ORDER BY id""",
                (order_id,)
            )
            ids = [row['remnant_id'] for row in cur.fetchall()]
    out = []
    for rid in ids:
        r = get_remnant_by_id(rid)
        if r:
            out.append(str(r.get('label_number') or r.get('unique_number') or r.get('id') or ''))
        else:
            out.append('')
    return out


def get_remnant_creation_layout(remnant_id):
    """Вернуть layout (схему раскроя), в котором был создан этот остаток, или None. Из истории action_type='created', details_json.layout."""
    for row in get_remnant_history(remnant_id):
        if row.get('action_type') != 'created':
            continue
        dj = row.get('details_json')
        if not dj:
            continue
        try:
            details = json.loads(dj) if isinstance(dj, str) else dj
        except Exception:
            continue
        layout = details.get('layout') if isinstance(details, dict) else None
        if layout and isinstance(layout, dict) and (layout.get('sheet_width') or layout.get('pieces') is not None):
            return layout
    return None


def _normalize_cut_layout_mm(lay):
    """Копия layout с гарантированными sheet_width / sheet_height (мм) для канваса/SVG."""
    if not isinstance(lay, dict):
        return {}
    try:
        raw = json.dumps(lay, default=str)
        out = json.loads(raw)
    except Exception:
        out = dict(lay)
    sw = int(out.get("sheet_width") or out.get("sheet_width_mm") or 0)
    sh = int(out.get("sheet_height") or out.get("sheet_height_mm") or 0)
    out["sheet_width"] = sw
    out["sheet_height"] = sh
    return out


def _highlight_rects_for_remnant_in_layout(lay, remnant_id):
    """Прямоугольники деловых остатков на листе, соответствующие remnant_id (по remnants_created)."""
    if not isinstance(lay, dict):
        return []
    rid = int(remnant_id)
    brs = list(lay.get("business_rects") or [])
    rc = list(lay.get("remnants_created") or [])
    out = []
    for i, it in enumerate(rc):
        if not isinstance(it, dict):
            continue
        try:
            if int(it.get("remnant_id") or 0) != rid:
                continue
        except (TypeError, ValueError):
            continue
        if i < len(brs) and isinstance(brs[i], dict):
            out.append(
                {
                    "x": int(brs[i].get("x") or 0),
                    "y": int(brs[i].get("y") or 0),
                    "w": int(brs[i].get("w") or 0),
                    "h": int(brs[i].get("h") or 0),
                }
            )
    return out


def _cut_result_layout_for_created_remnant(remnant_id, archive_row):
    """Найти строку mirror_cut_results с layout, где в remnants_created фигурирует remnant_id."""
    if not archive_row or not archive_row.get("order_id"):
        return None
    try:
        oid = int(archive_row["order_id"])
    except (TypeError, ValueError):
        return None
    rows = get_cut_results(oid) or []
    pairs = get_cut_archives_by_order_id(oid) or []
    arch_ids = [p[0].get("id") for p in pairs if p and p[0]]
    try:
        idx = arch_ids.index(archive_row.get("id"))
    except ValueError:
        idx = None
    if idx is not None and idx < len(rows):
        lay = rows[idx].get("layout") if isinstance(rows[idx].get("layout"), dict) else {}
        for it in lay.get("remnants_created") or []:
            if isinstance(it, dict) and int(it.get("remnant_id") or 0) == int(remnant_id):
                return lay
    for r in rows:
        lay = r.get("layout") if isinstance(r.get("layout"), dict) else {}
        for it in lay.get("remnants_created") or []:
            if isinstance(it, dict) and int(it.get("remnant_id") or 0) == int(remnant_id):
                return lay
    return None


def _cut_result_layout_using_remnant_sheet(remnant_id, used_archive_row):
    """Layout реза, где этот остаток был исходным листом (sheet_type remnant, sheet_id)."""
    if not used_archive_row or not used_archive_row.get("order_id"):
        return None
    try:
        oid = int(used_archive_row["order_id"])
    except (TypeError, ValueError):
        return None
    rows = get_cut_results(oid) or []
    rid = int(remnant_id)
    for r in rows:
        lay = r.get("layout") if isinstance(r.get("layout"), dict) else {}
        st = (r.get("sheet_type") or lay.get("sheet_type") or "").strip().lower()
        sid = r.get("sheet_id") if r.get("sheet_id") is not None else lay.get("sheet_id")
        try:
            sid_i = int(sid) if sid is not None else -1
        except (TypeError, ValueError):
            sid_i = -1
        if st == "remnant" and sid_i == rid:
            return lay
    return None


def _sheet_source_ru_for_archive(arch):
    """Краткая строка источника листа (как в складе / вебе)."""
    if not arch:
        return ""
    st = str(arch.get("sheet_type") or "").strip().lower()
    sid = arch.get("sheet_id")
    if st == "full":
        return "Целый лист со склада"
    if st == "remnant" and sid is not None:
        try:
            rem = get_remnant_by_id(int(sid))
        except (TypeError, ValueError):
            rem = None
        if rem:
            num = rem.get("label_number") or rem.get("unique_number")
            return "Остаток со склада № %s" % (num if num is not None else "?")
        return "Остаток со склада"
    return ""


def get_remnant_visual_story_stages(remnant_id):
    """
    Цепочка листов для истории остатка: (1) исходный лист, где остаток получен как деловой прямоугольник;
    (2) при необходимости — следующий рез, где этот остаток был листом.
    Каждый элемент: title, subtitle, order_id, client_name, layout (нормализованный), highlight_rects (мм), sheet_source.
    """
    try:
        rid = int(remnant_id)
    except (TypeError, ValueError):
        return []
    rem = get_remnant_by_id(rid)
    stages = []
    arch, _det = get_cut_archive_by_remnant_id(rid)
    lay_created = get_remnant_creation_layout(rid) or _cut_result_layout_for_created_remnant(rid, arch or {})
    if lay_created and arch:
        oid = arch.get("order_id")
        ord_row = get_order_for_labels(int(oid)) if oid is not None else None
        client = (arch.get("client_name") or (ord_row or {}).get("client_name") or (ord_row or {}).get("o_client_name") or "").strip() or "—"
        src = _sheet_source_ru_for_archive(arch)
        sw = int(arch.get("sheet_width_mm") or 0)
        sh = int(arch.get("sheet_height_mm") or 0)
        dt = arch.get("cut_date")
        date_s = dt.strftime("%d.%m.%Y %H:%M") if dt and hasattr(dt, "strftime") else (str(dt) if dt else "")
        title = "Лист %s×%s мм — заказ №%s" % (sw, sh, oid or "—")
        sub = "Клиент: %s%s" % (
            client,
            (" · " + date_s) if date_s else "",
        )
        norm = _normalize_cut_layout_mm(lay_created)
        highlights = _highlight_rects_for_remnant_in_layout(norm, rid)
        stages.append(
            {
                "stage": "created_here",
                "title": title,
                "subtitle": sub,
                "sheet_source": src,
                "order_id": int(oid) if oid is not None else None,
                "client_name": client,
                "layout": norm,
                "highlight_rects": highlights,
            }
        )
    elif lay_created and not arch:
        oid_hist = None
        for row in get_remnant_history(rid) or []:
            if (row.get("action_type") or "").strip() == "created":
                oid_hist = row.get("order_id")
                break
        ord_row = get_order_for_labels(int(oid_hist)) if oid_hist is not None else None
        client = ((ord_row or {}).get("client_name") or (ord_row or {}).get("o_client_name") or "").strip() or "—"
        norm = _normalize_cut_layout_mm(lay_created)
        highlights = _highlight_rects_for_remnant_in_layout(norm, rid)
        sw = int(norm.get("sheet_width") or 0)
        sh = int(norm.get("sheet_height") or 0)
        stages.append(
            {
                "stage": "created_here",
                "title": "Лист %s×%s мм — заказ №%s (схема из журнала)" % (sw, sh, oid_hist or "—"),
                "subtitle": "Клиент: %s" % client,
                "sheet_source": "",
                "order_id": int(oid_hist) if oid_hist is not None else None,
                "client_name": client,
                "layout": norm,
                "highlight_rects": highlights,
            }
        )
    used_arch, _ud = get_cut_archive_where_remnant_used_as_sheet(rid)
    if used_arch:
        lay2 = _cut_result_layout_using_remnant_sheet(rid, used_arch)
        if lay2:
            oid2 = used_arch.get("order_id")
            ord2 = get_order_for_labels(int(oid2)) if oid2 is not None else None
            client2 = (used_arch.get("client_name") or (ord2 or {}).get("client_name") or (ord2 or {}).get("o_client_name") or "").strip() or "—"
            lab = (rem.get("label_number") or rem.get("unique_number") or "?") if rem else "?"
            src2 = "Остаток со склада № %s как лист" % lab
            sw2 = int(used_arch.get("sheet_width_mm") or 0)
            sh2 = int(used_arch.get("sheet_height_mm") or 0)
            dt2 = used_arch.get("cut_date")
            date_s2 = dt2.strftime("%d.%m.%Y %H:%M") if dt2 and hasattr(dt2, "strftime") else (str(dt2) if dt2 else "")
            title2 = "Рез с этого остатка — лист %s×%s мм · заказ №%s" % (sw2, sh2, oid2 or "—")
            sub2 = "Клиент: %s%s" % (client2, (" · " + date_s2) if date_s2 else "")
            norm2 = _normalize_cut_layout_mm(lay2)
            stages.append(
                {
                    "stage": "cut_from_remnant_sheet",
                    "title": title2,
                    "subtitle": sub2,
                    "sheet_source": src2,
                    "order_id": int(oid2) if oid2 is not None else None,
                    "client_name": client2,
                    "layout": norm2,
                    "highlight_rects": [],
                }
            )
    return stages


# --- Cut results ---
def _delete_remnants_from_cut_layout(order_id):
    """Удалить остатки склада, созданные при сохранении чернового раскроя (метка from_cut_layout в истории)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT remnant_id FROM mirror_remnant_history
                   WHERE order_id = %s AND action_type = 'created'
                     AND details_json IS NOT NULL AND details_json LIKE %s""",
                (order_id, '%from_cut_layout%'),
            )
            ids = [row['remnant_id'] for row in cur.fetchall()]
    for rid in ids:
        delete_remnant(rid)


def _layout_source_reserved(row):
    lay = row.get('layout') if isinstance(row.get('layout'), dict) else {}
    return bool(lay.get('_source_reserved'))


def _reserve_remnant_for_cut(remnant_id, order_id):
    """Пометить остаток как занятый раскроем заказа (аналог −1 для целого листа)."""
    oid = int(order_id)
    rid = int(remnant_id)
    if not get_remnant_by_id(rid):
        raise ValueError("Остаток №%s не найден на складе." % rid)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE mirror_remnants SET reserved_for_cut_order_id = %s
                   WHERE id = %s AND (reserved_for_cut_order_id IS NULL OR reserved_for_cut_order_id = %s)""",
                (oid, rid, oid),
            )
            if cur.rowcount < 1:
                cur.execute("SELECT reserved_for_cut_order_id FROM mirror_remnants WHERE id = %s", (rid,))
                row = cur.fetchone() or {}
                other = row.get("reserved_for_cut_order_id")
                if other is not None:
                    raise ValueError(
                        "Остаток №%s уже зарезервирован под раскрой заказа №%s." % (rid, int(other))
                    )
                raise ValueError("Не удалось зарезервировать остаток №%s." % rid)


def _release_remnant_cut_reserve(remnant_id, order_id):
    oid = int(order_id)
    rid = int(remnant_id)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE mirror_remnants SET reserved_for_cut_order_id = NULL
                   WHERE id = %s AND reserved_for_cut_order_id IS NOT DISTINCT FROM %s""",
                (rid, oid),
            )


def _restore_reserved_sources_from_cut_results(order_id):
    """Вернуть в склад источники листов, которые были зарезервированы на этапе сохранения раскроя."""
    oid = int(order_id)
    rows = get_cut_results(oid) or []
    for r in rows:
        if not _layout_source_reserved(r):
            continue
        st = (r.get('sheet_type') or '').strip().lower()
        lay = r.get('layout') if isinstance(r.get('layout'), dict) else {}
        sid = r.get('sheet_id')
        if sid is None and lay:
            sid = lay.get('sheet_id')
        if sid is None:
            continue
        try:
            sid_i = int(sid)
        except Exception:
            continue
        if st == 'full':
            increment_full_sheet_quantity(sid_i)
        elif st == 'remnant':
            _release_remnant_cut_reserve(sid_i, oid)


def _reserve_sources_for_results(order_id, results_list):
    """Зарезервировать исходные листы под сохранённый раскрой (для заказов в работе)."""
    oid = int(order_id)
    for r in (results_list or []):
        if not isinstance(r, dict):
            continue
        st = (r.get('sheet_type') or '').strip().lower()
        lay_d = r.get('layout') if isinstance(r.get('layout'), dict) else {}
        sid = r.get('sheet_id')
        if sid is None and lay_d:
            sid = lay_d.get('sheet_id')
        if sid is None:
            continue
        try:
            sid_i = int(sid)
        except Exception:
            continue
        if st == 'full':
            decrement_full_sheet_quantity(sid_i)
            lay_mut = r.get('layout')
            if isinstance(lay_mut, dict):
                lay_mut['_source_reserved'] = True
        elif st == 'remnant':
            _reserve_remnant_for_cut(sid_i, oid)
            lay_mut = r.get('layout')
            if isinstance(lay_mut, dict):
                lay_mut['_source_reserved'] = True


def cancel_cut_results_if_allowed(order_id):
    """Отменить сохранённый раскрой, если по нему ещё не было производственных действий.
    Возвращает (ok: bool, message: str).
    """
    oid = int(order_id)
    order = get_order(oid) or {}
    status = (order.get('status') or '').strip().lower()
    if status in ('completed', 'shipped', 'closed'):
        return (False, "Отмена недоступна: заказ уже завершён.")
    if status == 'made':
        return (False, "Отмена недоступна: заказ в статусе «Изготовлен».")
    rows = get_cut_results(oid) or []
    if not rows:
        return (False, "Сохранённого раскроя нет.")
    if get_cut_archives_by_order_id(oid):
        return (False, "Отмена недоступна: раскрой уже подтверждён в архиве.")
    if list_production_events(oid):
        return (False, "Отмена недоступна: по заказу уже есть производственные действия.")
    _restore_reserved_sources_from_cut_results(oid)
    _delete_remnants_from_cut_layout(oid)
    delete_cut_results(oid)
    try:
        from MAIN_PROJECT.logic.blocks_bundle import clear_cut_scheme_on_all_products
    except Exception:
        try:
            from logic.blocks_bundle import clear_cut_scheme_on_all_products  # type: ignore
        except Exception:
            clear_cut_scheme_on_all_products = None
    if clear_cut_scheme_on_all_products:
        order_after = get_order(oid) or {}
        raw = order_after.get("blocks_calc_json")
        if raw:
            new_json = clear_cut_scheme_on_all_products(raw)
            update_order_blocks_calc(oid, new_json)
    _restore_paid_after_cut_revert(oid)
    return (True, "Раскрой отменён: резерв снят, черновые остатки удалены, статусы схем изделий сброшены.")


def _enrich_results_list_with_remnant_ids(order_id, results_list):
    """Перед записью cut_results: для деловых остатков без remnant_id — строка mirror_remnants + история."""
    from logic.qr_utils import remnant_qr_url

    wanted_ids = set()
    for r in results_list or []:
        for item in r.get('remnants_created') or []:
            if not isinstance(item, dict):
                continue
            rid = item.get('remnant_id')
            if rid is None:
                continue
            try:
                wanted_ids.add(int(rid))
            except (TypeError, ValueError):
                continue
    existing_remnant_ids = set()
    if wanted_ids:
        ids_list = list(wanted_ids)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM mirror_remnants WHERE id = ANY(%s)",
                    (ids_list,),
                )
                existing_remnant_ids = {int(row['id']) for row in (cur.fetchall() or [])}

    for r in results_list:
        rc = r.get('remnants_created') or []
        new_rc = []
        for item in rc:
            if not isinstance(item, dict):
                continue
            rid_raw = item.get('remnant_id')
            rid_ok = False
            if rid_raw is not None:
                try:
                    rid_ok = int(rid_raw) in existing_remnant_ids
                except (TypeError, ValueError):
                    rid_ok = False
            if rid_ok:
                new_rc.append(dict(item))
                continue
            name = item.get('name')
            h = item.get('height_mm')
            w = item.get('width_mm')
            if not name or h is None or w is None:
                new_rc.append(dict(item))
                continue
            num = get_next_label_number()
            un = str(num)
            url = remnant_qr_url(un)
            rid = insert_remnant(
                name,
                int(h),
                int(w),
                un,
                url,
                thickness_mm=item.get('thickness_mm', 4),
                label_number=num,
            )
            add_remnant_history(
                rid,
                order_id,
                'created',
                None,
                json.dumps({'from_cut_layout': True}),
            )
            new_rc.append(dict(item, remnant_id=rid))
        r['remnants_created'] = new_rc


def save_cut_results(order_id, results_list):
    """Полная замена раскроя по заказу: старые строки cut_results удаляются (без дублей при повторном сохранении).
    До статуса «выполнено» деловые остатки заводятся на склад (remnants + история с from_cut_layout)."""
    order = get_order(order_id)
    status = (order or {}).get('status') or ''
    if (status or '').strip().lower() == 'made':
        raise RuntimeError("Изменение раскроя недоступно: заказ в статусе «Изготовлен».")
    if status != 'completed':
        _restore_reserved_sources_from_cut_results(order_id)
        _delete_remnants_from_cut_layout(order_id)
    delete_cut_results(order_id)
    if status != 'completed':
        _enrich_results_list_with_remnant_ids(order_id, results_list)
        _reserve_sources_for_results(order_id, results_list)
    for r in results_list or []:
        if not isinstance(r, dict):
            continue
        lay = r.get('layout')
        if isinstance(lay, dict):
            if r.get('sheet_id') is None and lay.get('sheet_id') is not None:
                r['sheet_id'] = lay.get('sheet_id')
            st = (r.get('sheet_type') or '').strip()
            if not st and lay.get('sheet_type'):
                r['sheet_type'] = lay.get('sheet_type')
    rows_params = []
    for r in results_list or []:
        if not isinstance(r, dict):
            continue
        lay = r.get('layout')
        layout_json = json.dumps(lay) if isinstance(lay, (list, dict)) else r.get('layout_json', '[]')
        rem = r.get('remnants_created') or []
        rem_json = json.dumps(rem) if isinstance(rem, (list, dict)) else (r.get('remnants_created_json') or '[]')
        rows_params.append(
            (
                order_id,
                r.get('sheet_type'),
                r.get('sheet_id'),
                layout_json,
                rem_json,
            )
        )
    with get_connection() as conn:
        with conn.cursor() as cur:
            if rows_params:
                cur.executemany(
                    "INSERT INTO mirror_cut_results (order_id, sheet_type, sheet_id, layout_json, remnants_created_json) VALUES (%s, %s, %s, %s, %s)",
                    rows_params,
                )
    _invalidate_orders_cache()


def _order_product_ids_from_cut_layouts(layouts, fallback_order_id: int):
    """По кускам в layout → {order_id: {bundle product id, …}} (piece_uid «oid:pid:k», bundle_product_id, source_order_id)."""
    from collections import defaultdict

    out = defaultdict(set)
    try:
        oid_fb = int(fallback_order_id)
    except (TypeError, ValueError):
        oid_fb = 0
    for lay in layouts or []:
        if not isinstance(lay, dict):
            continue
        for p in lay.get("pieces") or []:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("bundle_product_id") or "").strip()
            oid = None
            so = p.get("source_order_id")
            if so is not None:
                try:
                    oid = int(so)
                except (TypeError, ValueError):
                    oid = None
            uid = str(p.get("piece_uid") or "").strip()
            if not pid and uid:
                parts = uid.split(":")
                if len(parts) >= 2:
                    pid = parts[1].strip()
            if oid is None and uid:
                head = uid.split(":", 1)[0]
                if head.isdigit():
                    try:
                        oid = int(head)
                    except ValueError:
                        oid = None
            if oid is None:
                oid = oid_fb
            if pid and oid:
                out[oid].add(pid)
    return dict(out)


def sync_bundle_after_mirror_cut_save(order_id: int, session_layouts) -> None:
    """
    После save_cut_results: статус изделий «в работе» по кускам раскроя (в т.ч. несколько заказов);
    для заказа order_id — cut_scheme_created на всё режущееся стекло (как раньше в cut_commit).
    """
    try:
        from MAIN_PROJECT.logic.blocks_bundle import (
            CUT_SCHEME_CREATED,
            parse_bundle,
            set_products_cut_scheme_status,
            set_products_status,
        )
    except Exception:
        from logic.blocks_bundle import (  # type: ignore
            CUT_SCHEME_CREATED,
            parse_bundle,
            set_products_cut_scheme_status,
            set_products_status,
        )

    try:
        import mirror_cut_prefill as _mcp
    except Exception:
        _mcp = None
    order_bundle_has_cuttable_glass = getattr(_mcp, "order_bundle_has_cuttable_glass", None) if _mcp else None

    by_oid = _order_product_ids_from_cut_layouts(session_layouts, int(order_id))
    import sys

    _mdb = sys.modules[__name__]

    for oid, pids in (by_oid or {}).items():
        if not pids:
            continue
        try:
            oi = int(oid)
        except (TypeError, ValueError):
            continue
        row = get_order(oi) or {}
        raw = row.get("blocks_calc_json")
        new_json = set_products_status(raw, sorted(pids), "in_progress")
        if new_json != raw:
            update_order_blocks_calc(oi, new_json)

    try:
        oid_main = int(order_id)
    except (TypeError, ValueError):
        return
    ord_row = get_order(oid_main) or {}
    raw = ord_row.get("blocks_calc_json")
    _ver, products = parse_bundle(raw if raw is not None else None)
    pids = []
    if order_bundle_has_cuttable_glass:
        for p in products or []:
            if order_bundle_has_cuttable_glass([p], _mdb):
                pid = str(p.get("id") or "").strip()
                if pid:
                    pids.append(pid)
    if pids:
        new_json = set_products_cut_scheme_status(raw, pids, CUT_SCHEME_CREATED)
        update_order_blocks_calc(oid_main, new_json)


def sync_missing_remnant_records_for_order(order_id):
    """Создать записи склада для деловых остатков в cut_results, у которых ещё нет remnant_id (заказ не завершён)."""
    order = get_order(order_id)
    if not order or (order.get('status') or '') == 'completed':
        return
    from logic.qr_utils import remnant_qr_url

    rows = get_cut_results(order_id)
    for r in rows:
        rc = list(r.get('remnants_created') or [])
        changed = False
        new_rc = []
        for item in rc:
            if not isinstance(item, dict):
                new_rc.append(item)
                continue
            rid = item.get('remnant_id')
            if rid and get_remnant_by_id(int(rid)):
                new_rc.append(dict(item))
                continue
            name = item.get('name')
            h = item.get('height_mm')
            w = item.get('width_mm')
            if not name or h is None or w is None:
                new_rc.append(dict(item))
                continue
            num = get_next_label_number()
            un = str(num)
            url = remnant_qr_url(un)
            new_id = insert_remnant(
                name,
                int(h),
                int(w),
                un,
                url,
                thickness_mm=item.get('thickness_mm', 4),
                label_number=num,
            )
            add_remnant_history(
                new_id,
                order_id,
                'created',
                None,
                json.dumps({'from_cut_layout': True}),
            )
            new_rc.append(dict(item, remnant_id=new_id))
            changed = True
        if changed:
            row_id = r['id']
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE mirror_cut_results SET remnants_created_json = %s WHERE id = %s",
                        (json.dumps(new_rc), row_id),
                    )


def delete_cut_results(order_id):
    """Удалить все сохранённые раскрои по заказу (для пересчёта по новому алгоритму)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mirror_cut_results WHERE order_id = %s", (order_id,))


def _layout_dicts_from_cut_result_row(row):
    """Один или несколько словарей листа из строки mirror_cut_results (layout может быть dict или list[dict])."""
    if not isinstance(row, dict):
        return []
    lay = row.get("layout")
    if isinstance(lay, dict):
        return [lay]
    if isinstance(lay, list):
        return [x for x in lay if isinstance(x, dict)]
    return []


def _hydrate_cut_result_row(r):
    """Разобрать layout_json / remnants_created_json в поля layout / remnants_created (как в get_cut_results)."""
    if r.get('layout_json'):
        try:
            r['layout'] = json.loads(r['layout_json']) if isinstance(r['layout_json'], str) else r['layout_json']
        except Exception:
            r['layout'] = {}
    else:
        r['layout'] = {}
    if r.get('remnants_created_json'):
        try:
            r['remnants_created'] = (
                json.loads(r['remnants_created_json'])
                if isinstance(r['remnants_created_json'], str)
                else r['remnants_created_json']
            )
        except Exception:
            r['remnants_created'] = []
    else:
        r['remnants_created'] = []


def get_cut_results_bulk(order_ids):
    """
    Все строки mirror_cut_results для набора заказов одним запросом.
    Нужно для «листы в работе»: раньше на каждый in_progress заказ был отдельный get_cut_results()
    и отдельное соединение к удалённому PG (~100+ ms × N).
    """
    if not order_ids:
        return {}
    seen = set()
    ids = []
    for x in order_ids:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i <= 0 or i in seen:
            continue
        seen.add(i)
        ids.append(i)
    if not ids:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, order_id, sheet_type, sheet_id, layout_json, remnants_created_json
                FROM mirror_cut_results
                WHERE order_id = ANY(%s)
                ORDER BY order_id, id
                """,
                (ids,),
            )
            rows = cur.fetchall() or []
    out = {}
    for r in rows:
        _hydrate_cut_result_row(r)
        oid = r.get('order_id')
        if oid is None:
            continue
        try:
            oi = int(oid)
        except (TypeError, ValueError):
            continue
        out.setdefault(oi, []).append(r)
    for i in ids:
        out.setdefault(i, [])
    return out


def get_cut_results(order_id):
    try:
        oid = int(order_id)
    except (TypeError, ValueError):
        return []
    if oid <= 0:
        return []
    return list(get_cut_results_bulk([oid]).get(oid, []))


def _bundle_cut_storage_order_id_from_raw(raw):
    if not raw or not str(raw).strip():
        return None
    try:
        d = json.loads(str(raw))
        if not isinstance(d, dict):
            return None
        v = d.get("cut_storage_order_id")
        if v is None:
            return None
        h = int(v)
        return h if h > 0 else None
    except Exception:
        return None


def _piece_belongs_to_source_order(p, source_order_id):
    if not isinstance(p, dict):
        return False
    try:
        src = int(source_order_id)
    except (TypeError, ValueError):
        return False
    try:
        so = p.get("source_order_id")
        if so is not None and int(so) == src:
            return True
    except (TypeError, ValueError):
        pass
    uid = str(p.get("piece_uid") or "").strip()
    if uid and ":" in uid:
        head = uid.split(":", 1)[0]
        if head.isdigit() and int(head) == src:
            return True
    return False


def cut_layout_has_piece_for_source_order(layout_dict, source_order_id):
    if not isinstance(layout_dict, dict):
        return False
    for p in layout_dict.get("pieces") or []:
        if _piece_belongs_to_source_order(p, source_order_id):
            return True
    return False


def _cut_rows_contain_source_order(rows, source_order_id):
    for r in rows or []:
        for lay in _layout_dicts_from_cut_result_row(r):
            if cut_layout_has_piece_for_source_order(lay, source_order_id):
                return True
    return False


def find_cut_host_orders_for_source_order(source_order_id, limit=80):
    """Заказы, в сохранённом раскрое которых есть детали с source_order_id = source_order_id."""
    try:
        sid = int(source_order_id)
    except (TypeError, ValueError):
        return []
    if sid <= 0:
        return []
    lim = max(1, min(int(limit or 80), 500))
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT order_id
                FROM mirror_cut_results
                WHERE layout_json IS NOT NULL AND layout_json <> ''
                  AND EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(
                      COALESCE((layout_json::jsonb)->'pieces', '[]'::jsonb)
                    ) AS piece
                    WHERE NULLIF(trim(piece->>'source_order_id'), '') IS NOT NULL
                      AND (piece->>'source_order_id')::int = %s
                  )
                ORDER BY order_id ASC
                LIMIT %s
                """,
                (sid, lim),
            )
            fetched = cur.fetchall() or []
    out = []
    seen = set()
    for row in fetched:
        oid = row.get("order_id")
        try:
            oi = int(oid)
        except (TypeError, ValueError):
            continue
        if oi in seen:
            continue
        seen.add(oi)
        out.append(oi)
    return out


def get_cut_results_effective_for_order(order_id):
    """
    Строки mirror_cut_results для просмотра/отмены: по самому заказу или по заказу-хосту,
    если раскрой физически сохранён там (лист «в работе» у другого заказа).
    Возвращает (rows, storage_order_id) где storage_order_id — order_id в БД, откуда взяты rows.
    """
    try:
        oid = int(order_id)
    except (TypeError, ValueError):
        return [], 0
    if oid <= 0:
        return [], oid
    local = get_cut_results(oid) or []
    if local:
        return local, oid
    order_row = get_order(oid) or {}
    raw = order_row.get("blocks_calc_json")
    host = _bundle_cut_storage_order_id_from_raw(raw)
    if host and host != oid:
        hrows = get_cut_results(host) or []
        if hrows and _cut_rows_contain_source_order(hrows, oid):
            return hrows, host
    for hid in find_cut_host_orders_for_source_order(oid, limit=80):
        if hid == oid:
            continue
        hrows = get_cut_results(hid) or []
        if hrows and _cut_rows_contain_source_order(hrows, oid):
            return hrows, hid
    return [], oid


def get_cut_layouts_for_overview(order_id):
    """Список layout dict для сводки: только листы, где есть детали этого заказа, если раскрой на хосте."""
    rows, host_oid = get_cut_results_effective_for_order(order_id)
    oid = int(order_id)
    layouts = []
    for r in rows or []:
        layouts.extend(_layout_dicts_from_cut_result_row(r))
    if not layouts:
        return []
    if host_oid == oid:
        pick = layouts
    else:
        filtered = [lay for lay in layouts if cut_layout_has_piece_for_source_order(lay, oid)]
        pick = filtered if filtered else layouts
    return [copy.deepcopy(lay) for lay in pick]


def _restore_reserved_sources_from_cut_result_row(order_id, row):
    if not _layout_source_reserved(row):
        return
    oid = int(order_id)
    st = (row.get("sheet_type") or "").strip().lower()
    lay = row.get("layout") if isinstance(row.get("layout"), dict) else {}
    sid = row.get("sheet_id")
    if sid is None and lay:
        sid = lay.get("sheet_id")
    if sid is None:
        return
    try:
        sid_i = int(sid)
    except Exception:
        return
    if st == "full":
        increment_full_sheet_quantity(sid_i)
    elif st == "remnant":
        _release_remnant_cut_reserve(sid_i, oid)


def _cut_result_row_has_persisted_remnants(row):
    for it in row.get("remnants_created") or []:
        if not isinstance(it, dict):
            continue
        if it.get("remnant_id"):
            return True
    return False


def strip_satellite_cut_pieces_from_host(host_order_id, source_order_id):
    """
    Удалить с листов заказа-хоста детали, относящиеся к source_order_id (спутник).
    Возвращает (ok, message). Не трогает строки, где уже заведены remnant_id в remnants_created.
    """
    try:
        from logic.cutting_algorithm import recompute_free_rects_from_pieces
    except Exception as e:
        return False, "Модуль раскроя: %s" % e
    try:
        host = int(host_order_id)
        src = int(source_order_id)
    except (TypeError, ValueError):
        return False, "Некорректный номер заказа."
    if host <= 0 or src <= 0 or host == src:
        return False, "Некорректная пара заказов."
    rows = list(get_cut_results(host) or [])
    if not rows:
        return False, "У заказа №%s нет сохранённого раскроя." % host
    touched = []
    for si, r in enumerate(rows):
        lay = r.get("layout")
        if not isinstance(lay, dict):
            continue
        pieces = list(lay.get("pieces") or [])
        if not any(_piece_belongs_to_source_order(p, src) for p in pieces if isinstance(p, dict)):
            continue
        touched.append((si, r))
    if not touched:
        return False, "На листах заказа №%s нет деталей заказа №%s." % (host, src)
    for _si, r in touched:
        if _cut_result_row_has_persisted_remnants(r):
            return (
                False,
                "По раскрою заказа №%s уже заведены деловые остатки на складе — "
                "отмена только со сводки заказа №%s недоступна. Откройте заказ №%s."
                % (host, src, host),
            )
    for it in rows:
        for rc in it.get("remnants_created") or []:
            if not isinstance(rc, dict):
                continue
            rid = rc.get("remnant_id")
            if not rid:
                continue
            try:
                rid_i = int(rid)
            except (TypeError, ValueError):
                continue
            hit = find_in_progress_sheet_usage("remnant", rid_i, exclude_order_id=host)
            if hit:
                return False, "Остаток №%s уже использован в раскрое заказа №%s." % (rid_i, hit[0])
    plan_delete_ids = []
    plan_updates_by_row_id = []
    for _si, r in touched:
        lay = r.get("layout")
        if not isinstance(lay, dict):
            continue
        pieces = [
            p
            for p in (lay.get("pieces") or [])
            if isinstance(p, dict) and not _piece_belongs_to_source_order(p, src)
        ]
        row_id = int(r["id"])
        if pieces:
            mat = lay.get("material") or ""
            th = lay.get("thickness_mm", 4)
            try:
                thm = get_threshold_for_material(mat, th)
                mh = (thm or {}).get("min_height_mm", 0) or 0
                mw = (thm or {}).get("min_width_mm", 0) or 0
            except Exception:
                mh, mw = 0, 0
            sw = int(lay.get("sheet_width") or 0)
            sh = int(lay.get("sheet_height") or 0)
            br, wr = recompute_free_rects_from_pieces(sw, sh, pieces, mh, mw)
            new_lay = dict(lay, pieces=pieces, business_rects=br, waste_rects=wr)
            plan_updates_by_row_id.append((row_id, new_lay))
        else:
            plan_delete_ids.append(row_id)
    for del_row_id in plan_delete_ids:
        row = next((x for x in rows if int(x.get("id") or 0) == del_row_id), None)
        if row:
            _restore_reserved_sources_from_cut_result_row(host, row)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM mirror_cut_results WHERE id = %s", (del_row_id,))
    rows_after = list(get_cut_results(host) or [])
    for row_id, new_lay in plan_updates_by_row_id:
        idx = next(
            (i for i, x in enumerate(rows_after) if int(x.get("id") or 0) == int(row_id)),
            None,
        )
        if idx is None:
            continue
        update_cut_result_layout(host, idx, new_lay)
    _invalidate_orders_cache()
    return True, "Детали заказа №%s сняты с листов заказа №%s." % (src, host)


def _piece_matches_owner_bundle_product(p, owner_order_id, bundle_product_id, storage_order_id):
    """Деталь на листе storage_order_id принадлежит изделию bundle_product_id заказа owner_order_id."""
    if not isinstance(p, dict):
        return False
    if str(p.get("bundle_product_id") or "").strip() != str(bundle_product_id or "").strip():
        return False
    try:
        own = int(owner_order_id)
        st = int(storage_order_id)
    except (TypeError, ValueError):
        return False
    so = p.get("source_order_id")
    if so is not None:
        try:
            return int(so) == own
        except (TypeError, ValueError):
            return False
    uid = str(p.get("piece_uid") or "").strip()
    if uid and ":" in uid:
        head = uid.split(":", 1)[0]
        if head.isdigit() and int(head) == own:
            return True
    return own == st


def _cut_sheet_done_by_index_for_order(storage_order_id):
    """sheet_index (1-based) → раскрой по этому листу отмечен cut_task_done в журнале."""
    try:
        oid = int(storage_order_id)
    except (TypeError, ValueError):
        return {}
    if oid < 1:
        return {}
    try:
        from MAIN_PROJECT.logic.production_instructions import build_cut_tasks_for_order
    except Exception:
        try:
            from logic.production_instructions import build_cut_tasks_for_order  # type: ignore
        except Exception:
            return {}
    row = get_order(oid) or {}
    ev = list_production_events(oid) or []
    out = {}
    try:
        for t in build_cut_tasks_for_order(dict(row), ev) or []:
            try:
                si = int(t.get("sheet_index") or 0)
            except (TypeError, ValueError):
                continue
            if si >= 1:
                out[si] = bool(t.get("is_done"))
    except Exception:
        return {}
    return out


def strip_order_product_pieces_from_cut_storage(owner_order_id, bundle_product_id):
    """
    Убрать с сохранённых листов (mirror_cut_results у заказа-хранилища) все детали одного изделия.
    Если по листу уже стоит «рез выполнен» в производстве — отмена невозможна.
    Как strip_satellite_cut_pieces_from_host, но только для одного bundle_product_id заказа owner_order_id.
    Возвращает (ok, message).
    """
    try:
        from logic.cutting_algorithm import recompute_free_rects_from_pieces
    except Exception as e:
        return False, "Модуль раскроя: %s" % e
    try:
        own = int(owner_order_id)
        pid = str(bundle_product_id or "").strip()
    except (TypeError, ValueError):
        return False, "Некорректные параметры."
    if own < 1 or not pid:
        return False, "Некорректные параметры."
    rows_eff, stor = get_cut_results_effective_for_order(own)
    if not rows_eff:
        return True, ""
    try:
        stor = int(stor or own)
    except (TypeError, ValueError):
        stor = own
    rows = list(get_cut_results(stor) or [])
    if not rows:
        return True, ""
    sheet_done = _cut_sheet_done_by_index_for_order(stor)
    touched = []
    for si, r in enumerate(rows):
        lay = r.get("layout")
        if not isinstance(lay, dict):
            continue
        pieces = list(lay.get("pieces") or [])
        if not any(
            isinstance(p, dict) and _piece_matches_owner_bundle_product(p, own, pid, stor)
            for p in pieces
        ):
            continue
        if sheet_done.get(si + 1):
            return (
                False,
                "По листу %d в раскрое заказа №%s уже отмечен рез в производстве — "
                "удалить изделие без смены статусов других заказов нельзя."
                % (si + 1, stor),
            )
        touched.append((si, r))
    if not touched:
        return True, ""
    for _si, r in touched:
        if _cut_result_row_has_persisted_remnants(r):
            return (
                False,
                "По раскрою заказа №%s уже заведены деловые остатки на складе — удаление изделия недоступно."
                % stor,
            )
    for it in rows:
        for rc in it.get("remnants_created") or []:
            if not isinstance(rc, dict):
                continue
            rid = rc.get("remnant_id")
            if not rid:
                continue
            try:
                rid_i = int(rid)
            except (TypeError, ValueError):
                continue
            hit = find_in_progress_sheet_usage("remnant", rid_i, exclude_order_id=stor)
            if hit:
                return False, "Остаток №%s уже использован в раскрое заказа №%s." % (rid_i, hit[0])
    plan_delete_ids = []
    plan_updates_by_row_id = []
    for _si, r in touched:
        lay = r.get("layout")
        if not isinstance(lay, dict):
            continue
        pieces = [
            p
            for p in (lay.get("pieces") or [])
            if isinstance(p, dict)
            and not _piece_matches_owner_bundle_product(p, own, pid, stor)
        ]
        row_id = int(r["id"])
        if pieces:
            mat = lay.get("material") or ""
            th = lay.get("thickness_mm", 4)
            try:
                thm = get_threshold_for_material(mat, th)
                mh = (thm or {}).get("min_height_mm", 0) or 0
                mw = (thm or {}).get("min_width_mm", 0) or 0
            except Exception:
                mh, mw = 0, 0
            sw = int(lay.get("sheet_width") or 0)
            sh = int(lay.get("sheet_height") or 0)
            br, wr = recompute_free_rects_from_pieces(sw, sh, pieces, mh, mw)
            new_lay = dict(lay, pieces=pieces, business_rects=br, waste_rects=wr)
            plan_updates_by_row_id.append((row_id, new_lay))
        else:
            plan_delete_ids.append(row_id)
    for del_row_id in plan_delete_ids:
        row = next((x for x in rows if int(x.get("id") or 0) == del_row_id), None)
        if row:
            _restore_reserved_sources_from_cut_result_row(stor, row)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM mirror_cut_results WHERE id = %s", (del_row_id,))
    rows_after = list(get_cut_results(stor) or [])
    for row_id, new_lay in plan_updates_by_row_id:
        idx = next(
            (i for i, x in enumerate(rows_after) if int(x.get("id") or 0) == int(row_id)),
            None,
        )
        if idx is None:
            continue
        update_cut_result_layout(stor, idx, new_lay)
    _invalidate_orders_cache()
    return True, "Детали изделия сняты с листов раскроя (заказ №%s)." % stor


def delete_bundle_product_with_cut_cleanup(order_id, bundle_product_id):
    """
    Удалить одно изделие из bundle: при «чистом» листе — снять детали с раскроя, вернуть резервы,
    сбросить схему/стекло/статус изделия на paid (способ оплаты не трогаем).
    Если стекло по позиции уже glass_made — только удаление из JSON, раскрой не меняем.
    Возвращает (ok, message).
    """
    try:
        from MAIN_PROJECT.logic.blocks_bundle import (
            PRODUCTION_GLASS_MADE,
            parse_bundle,
            remove_product_from_bundle,
            revert_products_cut_state_paid_preserve_payment,
        )
    except Exception:
        try:
            from logic.blocks_bundle import (  # type: ignore
                PRODUCTION_GLASS_MADE,
                parse_bundle,
                remove_product_from_bundle,
                revert_products_cut_state_paid_preserve_payment,
            )
        except Exception as e:
            return False, "import:%s" % e
    try:
        oid = int(order_id)
    except (TypeError, ValueError):
        return False, "Некорректный заказ."
    if oid < 1:
        return False, "Некорректный заказ."
    pid = str(bundle_product_id or "").strip()
    if not pid:
        return False, "Нет id изделия."
    order = get_order(oid) or {}
    raw = order.get("blocks_calc_json")
    _ver, products = parse_bundle(raw if raw is not None else None)
    pr = next((p for p in (products or []) if str(p.get("id") or "") == pid), None)
    if not pr:
        return False, "Изделие не найдено в заказе."
    if str(pr.get("production_glass_status") or "").strip() == PRODUCTION_GLASS_MADE:
        merged = remove_product_from_bundle(raw, pid)
        update_order_blocks_calc(oid, merged)
        _invalidate_orders_cache()
        return (
            True,
            "Изделие удалено. По позиции стекло отмечено как изготовленное — раскрой и склад не менялись.",
        )
    ok_strip, msg_strip = strip_order_product_pieces_from_cut_storage(oid, pid)
    if not ok_strip:
        return False, msg_strip or "Не удалось обновить раскрой."
    raw2 = revert_products_cut_state_paid_preserve_payment(raw, [pid])
    merged = remove_product_from_bundle(raw2, pid)
    update_order_blocks_calc(oid, merged)
    _invalidate_orders_cache()
    return True, "Изделие удалено; раскрой и резервы листов обновлены, схема и статус позиции сброшены (оплата сохранена)."


def find_in_progress_sheet_usage(sheet_type, sheet_id, exclude_order_id=None):
    """
    Найти заказ в работе, у которого уже есть раскрой на листе (sheet_type, sheet_id),
    либо остаток зарезервирован под раскрой другого заказа (без совпадения в cut_results — редкий обрыв консистентности).
    Возвращает (order_id, sheet_index) или None. sheet_index == -1 только если занято полем reserved_for_cut_order_id.
    """
    if sheet_type is None and sheet_id is None:
        return None
    st_norm = str(sheet_type or "").strip().lower()
    try:
        sid_norm = int(sheet_id)
    except (TypeError, ValueError):
        sid_norm = None

    def _row_matches(rst, rsid):
        rsn = str(rst or "").strip().lower()
        if rsn != st_norm:
            return False
        try:
            ri = int(rsid) if rsid is not None else None
        except (TypeError, ValueError):
            ri = None
        if sid_norm is None or ri is None:
            return rsid == sheet_id
        return ri == sid_norm

    oprog = get_orders_in_progress()
    oids = [
        o['id'] for o in oprog
        if exclude_order_id is None or o['id'] != exclude_order_id
    ]
    cuts_by = get_cut_results_bulk(oids)
    for o in oprog:
        oid = o['id']
        if exclude_order_id is not None and oid == exclude_order_id:
            continue
        rows = cuts_by.get(oid, [])
        for si, r in enumerate(rows):
            rst = r.get('sheet_type') or (isinstance(r.get('layout'), dict) and r['layout'].get('sheet_type'))
            rsid = r.get('sheet_id') if r.get('sheet_id') is not None else (isinstance(r.get('layout'), dict) and r['layout'].get('sheet_id'))
            if _row_matches(rst, rsid):
                return (oid, si)

    if st_norm == "remnant" and sid_norm is not None:
        rem = get_remnant_by_id(sid_norm)
        if rem:
            ro = rem.get("reserved_for_cut_order_id")
            if ro is not None:
                try:
                    ro_i = int(ro)
                except (TypeError, ValueError):
                    ro_i = None
                if ro_i and (exclude_order_id is None or int(exclude_order_id) != ro_i):
                    return (ro_i, -1)
    return None


def _merge_remnants_created_preserving_ids(old_created, business_rects, material, thickness_mm):
    """Сопоставить новые business_rects со старыми remnants_created и сохранить remnant_id где размеры совпали."""
    old = [dict(x) for x in (old_created or []) if isinstance(x, dict)]
    out = []
    for r in business_rects or []:
        if not isinstance(r, dict):
            continue
        h, w = int(r.get('h') or 0), int(r.get('w') or 0)
        if h <= 0 or w <= 0:
            continue
        mat = (material or '').strip()
        matched_idx = None
        for i, o in enumerate(old):
            oh = int(o.get('height_mm') or 0)
            ow = int(o.get('width_mm') or 0)
            oname = (o.get('name') or '').strip()
            if oh == h and ow == w and (not oname or not mat or oname == mat):
                matched_idx = i
                break
        matched = old.pop(matched_idx) if matched_idx is not None else None
        item = {
            'name': material,
            'height_mm': h,
            'width_mm': w,
            'thickness_mm': int(thickness_mm) if thickness_mm is not None else 4,
        }
        if matched and matched.get('remnant_id'):
            item['remnant_id'] = int(matched['remnant_id'])
        out.append(item)
    return out


def update_cut_result_layout(order_id, sheet_index, layout_dict):
    """Обновить layout одного листа в cut_results (ручное редактирование макета). Обновляет и remnants_created из business_rects."""
    orow = get_order(order_id)
    if orow and (orow.get("status") or "") == "made":
        return False
    rows = get_cut_results(order_id)
    if sheet_index < 0 or sheet_index >= len(rows):
        return False
    row_id = rows[sheet_index]['id']
    material = layout_dict.get('material') or ''
    thick = layout_dict.get('thickness_mm', 4)
    old_created = rows[sheet_index].get('remnants_created') or []
    remnants_created = _merge_remnants_created_preserving_ids(
        old_created, layout_dict.get('business_rects') or [], material, thick
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mirror_cut_results SET layout_json = %s, remnants_created_json = %s WHERE id = %s",
                (json.dumps(layout_dict), json.dumps(remnants_created), row_id)
            )
    order = get_order(order_id)
    if order and (order.get('status') or '') != 'completed':
        sync_missing_remnant_records_for_order(order_id)
    return True


def verify_sheets_for_order(order_id):
    """
    Проверить, что все листы, задействованные в раскрое заказа, ещё существуют.
    Возвращает (ok: bool, missing: list of str). missing — описание отсутствующих листов.
    """
    rows = get_cut_results(order_id)
    missing = []
    for r in rows:
        lay = r.get('layout') if isinstance(r.get('layout'), dict) else {}
        sheet_type = r.get('sheet_type') or lay.get('sheet_type')
        sheet_id = r.get('sheet_id') if r.get('sheet_id') is not None else lay.get('sheet_id')
        if not sheet_type or sheet_id is None:
            continue
        if sheet_type == 'full':
            row = get_full_sheet_by_id(sheet_id)
            if not row:
                missing.append("Полный лист №%s" % sheet_id)
        else:
            row = get_remnant_by_id(sheet_id)
            if not row:
                missing.append("Остаток №%s" % sheet_id)
    return (len(missing) == 0, missing)


def insert_cut_archive(order_id, client_name, sheet_type, sheet_id, sheet_name, sheet_height_mm, sheet_width_mm):
    """Запись в архив резов: один лист, из которого резали."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_cut_archive (order_id, client_name, sheet_type, sheet_id, sheet_name, sheet_height_mm, sheet_width_mm)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (order_id, client_name, sheet_type, sheet_id, sheet_name, int(sheet_height_mm), int(sheet_width_mm))
            )
            return cur.fetchone()['id']


def insert_cut_archive_detail(cut_archive_id, item_kind, width_mm, height_mm, recipient=None, remnant_id=None):
    """Элемент архива: изделие (piece) или остаток (remnant)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_cut_archive_detail (cut_archive_id, item_kind, width_mm, height_mm, recipient, remnant_id)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (cut_archive_id, item_kind, int(width_mm), int(height_mm), recipient, remnant_id)
            )


def get_cut_archive_by_remnant_id(remnant_id):
    """Найти запись архива резов, в которой создан этот остаток. Возвращает (cut_archive dict, [details])."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.id, a.order_id, a.client_name, a.cut_date, a.sheet_type, a.sheet_id, a.sheet_name, a.sheet_height_mm, a.sheet_width_mm
                   FROM mirror_cut_archive a
                   JOIN mirror_cut_archive_detail d ON d.cut_archive_id = a.id
                   WHERE d.remnant_id = %s
                   ORDER BY a.id DESC LIMIT 1""",
                (remnant_id,)
            )
            row = cur.fetchone()
            if not row:
                return (None, [])
            cur.execute(
                """SELECT id, item_kind, width_mm, height_mm, recipient, remnant_id
                   FROM mirror_cut_archive_detail WHERE cut_archive_id = %s ORDER BY id""",
                (row['id'],)
            )
            details = cur.fetchall()
            return (dict(row), [dict(d) for d in details] if details else [])


def get_cut_archives_by_order_id(order_id):
    """Все записи архива резов по заказу (каждый лист — отдельная запись). Возвращает список [(archive dict, [details]), ...]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, order_id, client_name, cut_date, sheet_type, sheet_id, sheet_name, sheet_height_mm, sheet_width_mm
                   FROM mirror_cut_archive WHERE order_id = %s ORDER BY id""",
                (order_id,)
            )
            rows = cur.fetchall()
            out = []
            for row in rows:
                cur.execute(
                    """SELECT id, item_kind, width_mm, height_mm, recipient, remnant_id
                       FROM mirror_cut_archive_detail WHERE cut_archive_id = %s ORDER BY id""",
                    (row['id'],)
                )
                details = cur.fetchall()
                out.append((dict(row), [dict(d) for d in details]))
            return out


def get_cut_archive_by_id(archive_id):
    """Получить запись архива реза по id. Возвращает (archive dict, [details]) или (None, [])."""
    if not archive_id:
        return (None, [])
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, order_id, client_name, cut_date, sheet_type, sheet_id, sheet_name, sheet_height_mm, sheet_width_mm
                   FROM mirror_cut_archive WHERE id = %s""",
                (archive_id,)
            )
            row = cur.fetchone()
            if not row:
                return (None, [])
            cur.execute(
                """SELECT id, item_kind, width_mm, height_mm, recipient, remnant_id
                   FROM mirror_cut_archive_detail WHERE cut_archive_id = %s ORDER BY id""",
                (archive_id,)
            )
            details = cur.fetchall()
            return (dict(row), [dict(d) for d in details])


def get_cut_archive_where_remnant_used_as_sheet(remnant_id):
    """Рез, в котором этот остаток был использован как лист (sheet_type='remnant', sheet_id=remnant_id). Возвращает (archive, details) или (None, [])."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, order_id, client_name, cut_date, sheet_type, sheet_id, sheet_name, sheet_height_mm, sheet_width_mm
                   FROM mirror_cut_archive WHERE sheet_type = 'remnant' AND sheet_id = %s ORDER BY id DESC LIMIT 1""",
                (remnant_id,)
            )
            row = cur.fetchone()
            if not row:
                return (None, [])
            cur.execute(
                """SELECT id, item_kind, width_mm, height_mm, recipient, remnant_id
                   FROM mirror_cut_archive_detail WHERE cut_archive_id = %s ORDER BY id""",
                (row['id'],)
            )
            details = cur.fetchall()
            return (dict(row), [dict(d) for d in details])


def get_cut_archive_list_for_remnant(remnant_id):
    """Все записи архива, в которых фигурирует этот остаток (по remnant_id в деталях). Для окна «история резов»."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT a.id FROM mirror_cut_archive a
                   JOIN mirror_cut_archive_detail d ON d.cut_archive_id = a.id
                   WHERE d.remnant_id = %s ORDER BY a.id DESC""",
                (remnant_id,)
            )
            ids = [r['id'] for r in cur.fetchall()]
            if not ids:
                return []
            out = []
            for aid in ids:
                cur.execute(
                    """SELECT id, order_id, client_name, cut_date, sheet_type, sheet_id, sheet_name, sheet_height_mm, sheet_width_mm
                       FROM mirror_cut_archive WHERE id = %s""",
                    (aid,)
                )
                row = cur.fetchone()
                cur.execute(
                    """SELECT id, item_kind, width_mm, height_mm, recipient, remnant_id
                       FROM mirror_cut_archive_detail WHERE cut_archive_id = %s ORDER BY id""",
                    (aid,)
                )
                details = cur.fetchall()
                out.append({'archive': dict(row), 'details': [dict(d) for d in details]})
            return out


def _cut_archive_has_entries_for_order(order_id):
    """Уже списали листы по этому заказу (архив резов) — повторный apply_pending_cut не нужен."""
    try:
        oid = int(order_id)
    except (TypeError, ValueError):
        return False
    if oid < 1:
        return False
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM mirror_cut_archive WHERE order_id = %s LIMIT 1",
                (oid,),
            )
            return cur.fetchone() is not None


def cut_archive_exists_for_order(order_id):
    """Публичная обёртка: есть ли уже архив реза по заказу (apply_pending_cut уже выполнялся)."""
    return _cut_archive_has_entries_for_order(order_id)


def apply_pending_cut(order_id):
    """
    Списание склада по сохранённому раскрою: архив резов, удаление/уменьшение исходного листа или остатка,
    создание деловых остатков. Вызывается при «Заказ выполнен» в десктопе и при подтверждении «Изготовлен»
    в WEB_SERVICE (идемпотентно: повторный вызов для того же заказа не дублирует архив).
    """
    from logic.qr_utils import remnant_qr_url
    if _cut_archive_has_entries_for_order(order_id):
        return
    order = get_order(order_id)
    client_name = (order.get('client_name') or '').strip() if order else ''
    if not client_name and order and order.get('client_id'):
        cl = get_client_by_id(order['client_id'])
        if cl:
            client_name = cl.get('name') or ''
    rows = get_cut_results(order_id)
    for r in rows:
        lay = r.get('layout') if isinstance(r.get('layout'), dict) else {}
        sheet_type = r.get('sheet_type') or lay.get('sheet_type') or 'full'
        sheet_id = r.get('sheet_id') if r.get('sheet_id') is not None else lay.get('sheet_id')
        sheet_name = lay.get('material') or ''
        sh = lay.get('sheet_height') or 0
        sw = lay.get('sheet_width') or 0
        if not sheet_name and (sheet_type == 'full' and sheet_id):
            fs = get_full_sheet_by_id(sheet_id)
            if fs:
                sheet_name = fs.get('name') or ''
                sh = sh or fs.get('height_mm')
                sw = sw or fs.get('width_mm')
        if not sheet_name and (sheet_type == 'remnant' and sheet_id):
            rem = get_remnant_by_id(sheet_id)
            if rem:
                sheet_name = rem.get('name') or ''
                sh = sh or rem.get('height_mm')
                sw = sw or rem.get('width_mm')
        if not sheet_name:
            sheet_name = 'Лист'
        thick = int(lay.get('thickness_mm') or 4)
        sheet_name = (sheet_name or 'Лист').strip() + ' %d мм' % thick
        archive_id = insert_cut_archive(order_id, client_name, sheet_type, sheet_id, sheet_name, sh or 0, sw or 0)
        for p in lay.get('pieces') or []:
            insert_cut_archive_detail(
                archive_id, 'piece',
                p.get('w') or 0, p.get('h') or 0,
                recipient=p.get('recipient')
            )
        source_already_reserved = bool((lay or {}).get('_source_reserved'))
        if sheet_type and sheet_id and not source_already_reserved:
            if sheet_type == 'full':
                decrement_full_sheet_quantity(sheet_id)
                try:
                    sid_i = int(sheet_id)
                except (TypeError, ValueError):
                    sid_i = None
                if sid_i is not None:
                    fs_row = get_full_sheet_by_id(sid_i)
                    if fs_row and int(fs_row.get('quantity') or 0) <= 0:
                        delete_full_sheet(sid_i)
                        try:
                            invalidate_material_names_cache()
                        except Exception:
                            pass
            else:
                rem_to_archive = get_remnant_by_id(sheet_id)
                created_arch, _ = get_cut_archive_by_remnant_id(sheet_id)
                created_archive_id = created_arch.get('id') if created_arch else None
                if rem_to_archive:
                    insert_deleted_remnant_archive(
                        rem_to_archive.get('name') or '',
                        rem_to_archive.get('height_mm') or 0,
                        rem_to_archive.get('width_mm') or 0,
                        rem_to_archive.get('thickness_mm'),
                        rem_to_archive.get('unique_number'),
                        rem_to_archive.get('label_number'),
                        original_remnant_id=sheet_id,
                        created_in_cut_archive_id=created_archive_id,
                    )
                delete_remnant(sheet_id)
        for wr in (lay.get('waste_rects') or []):
            if not isinstance(wr, dict):
                continue
            ww = int(wr.get('w') or wr.get('width_mm') or 0)
            wh = int(wr.get('h') or wr.get('height_mm') or 0)
            if ww > 0 and wh > 0:
                insert_cut_archive_detail(archive_id, 'waste', ww, wh, None, None)
        # Только деловые остатки (remnants_created = business_rects). Уже заведённые при раскрое — переиспользуем remnant_id.
        for item in (r.get('remnants_created') or []):
            if not isinstance(item, dict):
                continue
            name = item.get('name')
            h = item.get('height_mm')
            w = item.get('width_mm')
            if not name or h is None or w is None:
                continue
            rid_existing = item.get('remnant_id')
            rem = get_remnant_by_id(int(rid_existing)) if rid_existing is not None else None
            if rem:
                rid = int(rid_existing)
                insert_cut_archive_detail(archive_id, 'remnant', int(w), int(h), None, rid)
                continue
            num = get_next_label_number()
            unique_num = str(num)
            url = remnant_qr_url(unique_num)
            rid = insert_remnant(name, int(h), int(w), unique_num, url, thickness_mm=item.get('thickness_mm', 4), label_number=num)
            insert_cut_archive_detail(archive_id, 'remnant', int(w), int(h), None, rid)
            add_remnant_history(
                rid,
                order_id,
                'created',
                None,
                json.dumps({'from_order_complete': True, 'from_cut': order_id, 'layout': lay}),
            )


# --- Materials list (distinct from full_sheets + remnants names) ---
def get_thicknesses_for_material(material_name):
    """Список толщин (мм) для материала: из целых листов и остатков. Для выбора толщины при «выбрать с конкретного листа»."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """(SELECT DISTINCT thickness_mm FROM mirror_full_sheets WHERE name = %s AND quantity > 0)
                  UNION
                  (SELECT DISTINCT thickness_mm FROM mirror_remnants WHERE name = %s)
                  ORDER BY thickness_mm""",
                (material_name, material_name)
            )
            return [row['thickness_mm'] for row in cur.fetchall()]


_material_names_cache_lock = threading.Lock()
_material_names_cache = {"expires_at": 0.0, "names": None}


def invalidate_material_names_cache():
    """Сброс кэша списка материалов (склад) — вызывать после изменений full_sheets/remnants, если нужна мгновенная консистентность."""
    with _material_names_cache_lock:
        _material_names_cache["expires_at"] = 0.0
        _material_names_cache["names"] = None


def _material_names_cache_ttl_seconds() -> float:
    raw = (os.environ.get("MC_MATERIAL_NAMES_CACHE_SEC") or "8").strip()
    try:
        ttl = float(raw)
    except (TypeError, ValueError):
        ttl = 8.0
    return max(0.0, min(120.0, ttl))


def get_all_material_names():
    """Имена материалов с склада. Короткий TTL-кэш: один и тот же UNION часто вызывается десятки раз за открытие сводки/таблицы."""
    ttl = _material_names_cache_ttl_seconds()
    now = time.time()
    if ttl > 0:
        with _material_names_cache_lock:
            exp = _material_names_cache["expires_at"]
            cached = _material_names_cache["names"]
            if cached is not None and now < exp:
                return list(cached)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT name FROM mirror_full_sheets UNION SELECT DISTINCT name FROM mirror_remnants ORDER BY name"
            )
            names = [row["name"] for row in cur.fetchall()]
    if ttl > 0:
        with _material_names_cache_lock:
            _material_names_cache["names"] = tuple(names)
            _material_names_cache["expires_at"] = now + ttl
    return names


def get_all_material_thickness_pairs():
    """Все пары (название материала, толщина мм) из целых листов и остатков. Для выпадающего списка выбора материала+толщина."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """(SELECT DISTINCT name, thickness_mm FROM mirror_full_sheets WHERE quantity > 0)
                  UNION
                  (SELECT DISTINCT name, thickness_mm FROM mirror_remnants)
                  ORDER BY name, thickness_mm"""
            )
            return [(row['name'], row['thickness_mm']) for row in cur.fetchall()]


def get_material_names_prefix(prefix):
    """Materials (from full_sheets and remnants) whose name starts with prefix (case insensitive)."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                (SELECT DISTINCT name FROM mirror_full_sheets WHERE name ILIKE %s)
                UNION
                (SELECT DISTINCT name FROM mirror_remnants WHERE name ILIKE %s)
                ORDER BY name
                """,
                (prefix + '%', prefix + '%')
            )
            return [row['name'] for row in cur.fetchall()]


# --- Layout training (for neural network / learning from user arrangements) ---

def insert_layout_training_sample(sheet_width_mm, sheet_height_mm, pieces, source='training_tab'):
    """
    Сохранить образец раскладки для обучения. pieces — список dict с ключами x, y, w, h (и опционально rotated).
    source: 'training_tab' (из режима «Обучение») или 'manual_edit' (сохранение после ручной правки в макете).
    """
    pieces_clean = [{'x': int(p.get('x', 0)), 'y': int(p.get('y', 0)), 'w': int(p.get('w', 0)), 'h': int(p.get('h', 0)), 'rotated': bool(p.get('rotated', False))} for p in pieces]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_layout_training (sheet_width_mm, sheet_height_mm, pieces_json, source)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (int(sheet_width_mm), int(sheet_height_mm), json.dumps(pieces_clean), str(source)[:32])
            )
            return cur.fetchone()['id']


def get_layout_training_samples(limit=500, source=None):
    """Список образцов для обучения. source=None — все, иначе фильтр по source."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            if source:
                cur.execute(
                    """SELECT id, sheet_width_mm, sheet_height_mm, pieces_json, source, created_at
                       FROM mirror_layout_training WHERE source = %s ORDER BY created_at DESC LIMIT %s""",
                    (source, int(limit))
                )
            else:
                cur.execute(
                    """SELECT id, sheet_width_mm, sheet_height_mm, pieces_json, source, created_at
                       FROM mirror_layout_training ORDER BY created_at DESC LIMIT %s""",
                    (int(limit),)
                )
            rows = cur.fetchall()
            for r in rows:
                if r.get('pieces_json'):
                    r['pieces'] = json.loads(r['pieces_json']) if isinstance(r['pieces_json'], str) else r['pieces_json']
                else:
                    r['pieces'] = []
            return rows


def get_layout_training_count():
    """Общее число образцов в таблице обучения."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM mirror_layout_training")
            return cur.fetchone()['c'] or 0


# --- Sales orders ---
SALES_STATUS_CALCULATED = 'calculated'
SALES_STATUS_PAID = 'paid'
SALES_STATUS_ASSEMBLED = 'assembled'
SALES_STATUS_SHIPPED = 'shipped'
SALES_STATUS_RECEIVED = 'received'

SALES_STATUS_RU = {
    SALES_STATUS_CALCULATED: 'Просчитан',
    SALES_STATUS_PAID: 'Оплачен',
    SALES_STATUS_ASSEMBLED: 'Собран',
    SALES_STATUS_SHIPPED: 'Отгружен',
    SALES_STATUS_RECEIVED: 'Получен клиентом',
}

SALES_STATUS_FLOW = [
    SALES_STATUS_CALCULATED,
    SALES_STATUS_PAID,
    SALES_STATUS_ASSEMBLED,
    SALES_STATUS_SHIPPED,
    SALES_STATUS_RECEIVED,
]


def sales_status_to_ru(status):
    return str(SALES_STATUS_RU.get(status, status or '—'))


def create_sales_order(client_name, client_id=None, notes=None, status=SALES_STATUS_CALCULATED, quick_client_id=None):
    st = str(status or SALES_STATUS_CALCULATED).strip().lower()
    if st not in SALES_STATUS_RU:
        st = SALES_STATUS_CALCULATED
    qcid = None
    if quick_client_id is not None:
        try:
            qcid = int(quick_client_id)
        except (TypeError, ValueError):
            qcid = None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_sales_orders (client_id, client_name, quick_client_id, status, notes, total_rub)
                   VALUES (%s, %s, %s, %s, %s, 0) RETURNING id""",
                (
                    int(client_id) if client_id is not None else None,
                    (client_name or '').strip() or None,
                    qcid,
                    st,
                    notes,
                ),
            )
            row = cur.fetchone()
            return row['id'] if row else None


def get_sales_order(order_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT o.id, o.client_id, o.quick_client_id, o.client_name AS o_client_name, o.status, o.notes, o.total_rub,
                          o.created_at, o.updated_at,
                          COALESCE(c.name, qc.name, o.client_name, '') AS client_name
                   FROM mirror_sales_orders o
                   LEFT JOIN mirror_clients c ON c.id = o.client_id
                   LEFT JOIN mirror_quick_clients qc ON qc.id = o.quick_client_id
                   WHERE o.id = %s""",
                (int(order_id),),
            )
            return cur.fetchone()


def get_sales_order_items(order_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, sales_order_id, item_type, item_ref_id, item_name, color, qty, unit, unit_price_rub, line_total_rub
                   FROM mirror_sales_items
                   WHERE sales_order_id = %s
                   ORDER BY id""",
                (int(order_id),),
            )
            return cur.fetchall()


def list_sales_orders(status=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            q = (
                """SELECT o.id, o.client_id, o.quick_client_id, o.client_name AS o_client_name, o.status, o.notes, o.total_rub,
                          o.created_at, o.updated_at, COALESCE(c.name, qc.name, o.client_name, '') AS client_name
                   FROM mirror_sales_orders o
                   LEFT JOIN mirror_clients c ON c.id = o.client_id
                   LEFT JOIN mirror_quick_clients qc ON qc.id = o.quick_client_id
                   WHERE 1=1"""
            )
            params = []
            st = (status or '').strip().lower()
            if st:
                q += " AND o.status = %s"
                params.append(st)
            q += " ORDER BY o.created_at DESC, o.id DESC"
            cur.execute(q, params or None)
            return cur.fetchall()


def list_sales_items_counts_bulk(sales_order_ids):
    ids = [int(x) for x in (sales_order_ids or []) if x is not None]
    if not ids:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT sales_order_id,
                          COUNT(*)::int AS items_count,
                          COALESCE(SUM(CASE WHEN item_type = 'delivery' THEN 1 ELSE 0 END), 0)::int AS delivery_count
                   FROM mirror_sales_items
                   WHERE sales_order_id = ANY(%s)
                   GROUP BY sales_order_id""",
                (ids,),
            )
            rows = cur.fetchall() or []
    out = {}
    for r in rows:
        sid = int(r.get("sales_order_id"))
        out[sid] = {
            "items_count": int(r.get("items_count") or 0),
            "delivery_count": int(r.get("delivery_count") or 0),
        }
    return out


def _sales_recalc_total(cur, order_id):
    cur.execute(
        """SELECT COALESCE(SUM(line_total_rub), 0) AS s
           FROM mirror_sales_items
           WHERE sales_order_id = %s""",
        (int(order_id),),
    )
    sm = (cur.fetchone() or {}).get('s') or 0
    cur.execute(
        "UPDATE mirror_sales_orders SET total_rub = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
        (int(sm), int(order_id)),
    )
    return int(sm)


def replace_sales_order_items(order_id, items):
    oid = int(order_id)
    rows = items or []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mirror_sales_items WHERE sales_order_id = %s", (oid,))
            for it in rows:
                item_type = str((it or {}).get('item_type') or '').strip().lower()
                if item_type not in ('profile', 'hinge', 'corner', 'seal', 'screw', 'delivery'):
                    continue
                qty = int((it or {}).get('qty') or 0)
                if qty <= 0:
                    continue
                unit = str((it or {}).get('unit') or 'pcs').strip().lower()
                if unit not in ('pcs', 'm'):
                    unit = 'pcs'
                unit_price = int((it or {}).get('unit_price_rub') or 0)
                line_total = int((it or {}).get('line_total_rub') or 0)
                cur.execute(
                    """INSERT INTO mirror_sales_items
                       (sales_order_id, item_type, item_ref_id, item_name, color, qty, unit, unit_price_rub, line_total_rub)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        oid,
                        item_type,
                        int((it or {}).get('item_ref_id')) if (it or {}).get('item_ref_id') is not None else None,
                        str((it or {}).get('item_name') or '')[:255],
                        str((it or {}).get('color') or '')[:255],
                        qty,
                        unit,
                        unit_price,
                        line_total,
                    ),
                )
            return _sales_recalc_total(cur, oid)


def update_sales_order(order_id, client_name=None, client_id=None, quick_client_id=None, notes=None, status=None, items=None):
    oid = int(order_id)
    with get_connection() as conn:
        with conn.cursor() as cur:
            sets = []
            params = []
            if client_name is not None:
                sets.append("client_name = %s")
                params.append((client_name or '').strip() or None)
            if client_id is not None:
                sets.append("client_id = %s")
                params.append(int(client_id))
            if quick_client_id is not None:
                try:
                    sets.append("quick_client_id = %s")
                    params.append(int(quick_client_id))
                except (TypeError, ValueError):
                    pass
            if notes is not None:
                sets.append("notes = %s")
                params.append(notes)
            if status is not None:
                st = str(status or '').strip().lower()
                if st in SALES_STATUS_RU:
                    sets.append("status = %s")
                    params.append(st)
            if sets:
                sets.append("updated_at = CURRENT_TIMESTAMP")
                cur.execute("UPDATE mirror_sales_orders SET " + ", ".join(sets) + " WHERE id = %s", params + [oid])
    if items is not None:
        replace_sales_order_items(oid, items)
    return get_sales_order(oid)


def _sales_consume_profiles_for_paid(cur, order_row):
    oid = int(order_row.get('id'))
    cur.execute(
        """SELECT id, item_type, item_ref_id, qty, unit
           FROM mirror_sales_items
           WHERE sales_order_id = %s AND item_type = 'profile'""",
        (oid,),
    )
    items = cur.fetchall() or []
    for it in items:
        ref_id = it.get('item_ref_id')
        if ref_id is None:
            continue
        qty = int(it.get('qty') or 0)
        if qty <= 0:
            continue
        unit = str(it.get('unit') or 'pcs').strip().lower()
        req_mm_per_unit = 6000 if unit == 'pcs' else 1000
        for _n in range(qty):
            reserved = reserve_profile_from_stock(int(ref_id), int(req_mm_per_unit), oid)
            if reserved is None:
                raise ValueError("Недостаточно профиля на складе для оплаты заказа продажи.")
            src = (reserved.get('source') or {})
            cur.execute(
                """INSERT INTO mirror_sales_profile_usage
                   (sales_order_id, sales_item_id, profile_ref_id, consumed_stock_id, mode, required_mm, rest_mm)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    oid,
                    int(it.get('id')),
                    int(ref_id),
                    int(src.get('id')) if src.get('id') is not None else None,
                    unit,
                    int(reserved.get('required_mm') or 0),
                    int(reserved.get('rest_mm') or 0),
                ),
            )


def update_sales_order_status(order_id, new_status):
    oid = int(order_id)
    ns = str(new_status or '').strip().lower()
    if ns not in SALES_STATUS_RU:
        raise ValueError("Недопустимый статус продажи.")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status FROM mirror_sales_orders WHERE id = %s",
                (oid,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("Заказ продажи не найден.")
            current = str(row.get('status') or SALES_STATUS_CALCULATED).strip().lower()
            if current == ns:
                return get_sales_order(oid)
            if current in SALES_STATUS_FLOW and ns in SALES_STATUS_FLOW:
                if SALES_STATUS_FLOW.index(ns) < SALES_STATUS_FLOW.index(current):
                    raise ValueError("Нельзя переводить заказ продажи в предыдущий статус.")
            if ns == SALES_STATUS_PAID and current != SALES_STATUS_PAID:
                _sales_consume_profiles_for_paid(cur, row)
            cur.execute(
                "UPDATE mirror_sales_orders SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (ns, oid),
            )
    return get_sales_order(oid)


def delete_sales_order(order_id):
    oid = int(order_id)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mirror_sales_profile_usage WHERE sales_order_id = %s", (oid,))
            cur.execute("DELETE FROM mirror_sales_items WHERE sales_order_id = %s", (oid,))
            cur.execute("DELETE FROM mirror_sales_orders WHERE id = %s", (oid,))


# --- Quick estimates ---
QUICK_ESTIMATE_STATUS_DRAFT = 'draft'
QUICK_ESTIMATE_STATUS_TRANSFERRED = 'transferred'


def create_quick_estimate(
    category,
    client_id,
    client_name,
    lead_source,
    contact_info,
    markup_percent,
    estimate_at,
    created_by_user_id,
    created_by_login,
    created_by_role,
    payload_json=None,
    quick_client_id=None,
):
    if estimate_at is None:
        from datetime import datetime
        estimate_at = datetime.now()
    qcid = None
    if quick_client_id is not None:
        try:
            qcid = int(quick_client_id)
        except (TypeError, ValueError):
            qcid = None
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mirror_quick_estimates
                   (category, client_id, quick_client_id, client_name, lead_source, contact_info, markup_percent, estimate_at,
                    created_by_user_id, created_by_login, created_by_role, payload_json, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    str(category or 'glass')[:32],
                    int(client_id) if client_id is not None else None,
                    qcid,
                    (client_name or '').strip(),
                    (lead_source or '').strip()[:64],
                    (contact_info or '').strip()[:255],
                    int(markup_percent or 0),
                    estimate_at,
                    int(created_by_user_id) if created_by_user_id is not None else None,
                    (created_by_login or '').strip()[:128],
                    (created_by_role or '').strip()[:64],
                    payload_json,
                    QUICK_ESTIMATE_STATUS_DRAFT,
                ),
            )
            row = cur.fetchone()
            return row['id'] if row else None


def sales_order_ids_in_draft_quick_estimates():
    """
    ID заказов mirror_sales_orders, которые сейчас привязаны только к черновику быстрого просчёта (category=sales).
    Их не следует дублировать в общей таблице заказов — они видны во вкладке «Быстрый просчёт».
    """
    rows = list_quick_estimates(status=QUICK_ESTIMATE_STATUS_DRAFT) or []
    out = set()
    for q in rows:
        if str(q.get("category") or "").strip().lower() != "sales":
            continue
        pl = _quick_estimate_payload_dict(q.get("payload_json")) or {}
        sid = pl.get("sales_order_id")
        try:
            out.add(int(sid))
        except (TypeError, ValueError):
            pass
    return out


def list_quick_estimates(status=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            q = (
                """SELECT id, category, client_id, quick_client_id, client_name, lead_source, contact_info, markup_percent, estimate_at,
                          created_by_user_id, created_by_login, created_by_role, payload_json, status, transferred_order_id,
                          created_at, updated_at
                   FROM mirror_quick_estimates
                   WHERE 1=1"""
            )
            params = []
            st = (status or '').strip().lower()
            if st:
                q += " AND status = %s"
                params.append(st)
            q += " ORDER BY estimate_at DESC, id DESC"
            cur.execute(q, params or None)
            return cur.fetchall()


def get_quick_estimate(qe_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, category, client_id, quick_client_id, client_name, lead_source, contact_info, markup_percent, estimate_at,
                          created_by_user_id, created_by_login, created_by_role, payload_json, status, transferred_order_id,
                          created_at, updated_at
                   FROM mirror_quick_estimates
                   WHERE id = %s""",
                (int(qe_id),),
            )
            return cur.fetchone()


def update_quick_estimate_payload(qe_id, payload_json):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mirror_quick_estimates SET payload_json = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (payload_json, int(qe_id)),
            )


def update_quick_estimate_client_meta(qe_id, client_id=None, quick_client_id=None, client_name=None, lead_source=None, contact_info=None, markup_percent=None):
    with get_connection() as conn:
        with conn.cursor() as cur:
            sets = []
            params = []
            if client_id is not None:
                sets.append("client_id = %s")
                params.append(int(client_id))
            else:
                sets.append("client_id = NULL")
            if quick_client_id is not None:
                sets.append("quick_client_id = %s")
                params.append(int(quick_client_id))
            else:
                sets.append("quick_client_id = NULL")
            if client_name is not None:
                sets.append("client_name = %s")
                params.append(str(client_name).strip())
            if lead_source is not None:
                sets.append("lead_source = %s")
                params.append(str(lead_source).strip()[:64])
            if contact_info is not None:
                sets.append("contact_info = %s")
                params.append(str(contact_info).strip()[:255])
            if markup_percent is not None:
                sets.append("markup_percent = %s")
                params.append(int(markup_percent))
            sets.append("updated_at = CURRENT_TIMESTAMP")
            # Нельзя собирать запрос через "% ... %": в SET уже есть литералы «... = %s» для psycopg2.
            q = "UPDATE mirror_quick_estimates SET " + ", ".join(sets) + " WHERE id = %s"
            params.append(int(qe_id))
            cur.execute(q, tuple(params))


def _quick_estimate_payload_dict(raw):
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        import json
        try:
            return json.loads(s)
        except Exception:
            return None
    return None


def transfer_quick_estimate_to_order(qe_id):
    import json

    q = get_quick_estimate(qe_id)
    if not q:
        raise ValueError("Быстрый просчет не найден.")
    cat = str(q.get('category') or 'glass').strip().lower()
    st = str(q.get('status') or '')

    if st == QUICK_ESTIMATE_STATUS_TRANSFERRED:
        if cat == 'sales':
            pl = _quick_estimate_payload_dict(q.get('payload_json')) or {}
            sid = pl.get('sales_order_id')
            try:
                sid = int(sid)
            except (TypeError, ValueError):
                sid = None
            if sid and get_sales_order(sid):
                return sid
        tid = q.get('transferred_order_id')
        if tid is not None:
            try:
                return int(tid)
            except (TypeError, ValueError):
                pass

    q_cid = q.get('client_id')
    try:
        q_cid = int(q_cid) if q_cid is not None else None
    except (TypeError, ValueError):
        q_cid = None
    q_qcid = q.get('quick_client_id')
    try:
        q_qcid = int(q_qcid) if q_qcid is not None else None
    except (TypeError, ValueError):
        q_qcid = None
    q_cname = (q.get('client_name') or '').strip() or None
    if not q_cname and q_qcid:
        rqc = get_mirror_quick_client_by_id(q_qcid)
        if rqc:
            q_cname = (rqc.get('name') or '').strip() or None
    if q_cid is None and q_qcid is None and not q_cname:
        raise ValueError(
            "В быстром просчёте не указан клиент — перенос в заказ невозможен."
        )

    payload = _quick_estimate_payload_dict(q.get('payload_json'))
    if not payload:
        raise ValueError(
            "В быстром просчёте нет сохранённого расчёта — перенос в заказ невозможен."
        )

    if cat == 'sales':
        sid = payload.get('sales_order_id')
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            sid = None
        if not sid:
            raise ValueError("В быстром просчёте продажи нет ссылки на заказ.")
        if not get_sales_order(sid):
            raise ValueError("Заказ продажи не найден.")
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE mirror_quick_estimates
                       SET status = %s, transferred_order_id = NULL, updated_at = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (QUICK_ESTIMATE_STATUS_TRANSFERRED, int(qe_id)),
                )
        return int(sid)

    kind = 'glass_mirror' if cat == 'glass' else 'facade'
    raw_p = q.get('payload_json')
    if isinstance(raw_p, str) and raw_p.strip():
        blocks_text = raw_p
    else:
        blocks_text = json.dumps(payload, ensure_ascii=False)
    oid = create_order(
        q_cname or "",
        client_id=q_cid,
        quick_client_id=q_qcid,
        notes="Перенесено из быстрого просчета",
        order_kind=kind,
        created_by_user_id=q.get("created_by_user_id"),
        created_by_login=q.get("created_by_login"),
        created_by_role=q.get("created_by_role"),
    )
    if blocks_text:
        update_order_blocks_calc(int(oid), blocks_text)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE mirror_quick_estimates
                   SET status = %s, transferred_order_id = %s, updated_at = CURRENT_TIMESTAMP
                   WHERE id = %s""",
                (QUICK_ESTIMATE_STATUS_TRANSFERRED, int(oid), int(qe_id)),
            )
    return int(oid)


def get_active_desktop_release(channel: str = "mirrorcut"):
    """
    Активная строка релиза десктопа для дельта-обновлений (mirror_desktop_app_release).
    Возвращает dict с ключами version, manifest_url, manifest_json, … или None.
    """
    ch = (channel or "mirrorcut").strip() or "mirrorcut"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, channel, version, manifest_url, manifest_json, released_at, active
                FROM mirror_desktop_app_release
                WHERE channel = %s AND active = TRUE AND COALESCE(TRIM(manifest_url), '') <> ''
                ORDER BY released_at DESC NULLS LAST, id DESC
                LIMIT 1
                """,
                (ch,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def upsert_active_desktop_release(
    version: str,
    manifest_url: str,
    *,
    channel: str = "mirrorcut",
    manifest_json=None,
) -> None:
    """
    Одна активная строка на канал: остальные deactivated, новая inserted.
    manifest_json — опционально; для дельты достаточно manifest_url.
    """
    ch = (channel or "mirrorcut").strip() or "mirrorcut"
    ver = (version or "").strip()
    url = (manifest_url or "").strip()
    if not ver or not url:
        raise ValueError("version и manifest_url обязательны")
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE mirror_desktop_app_release SET active = FALSE WHERE channel = %s",
                (ch,),
            )
            cur.execute(
                """
                INSERT INTO mirror_desktop_app_release (channel, version, manifest_url, manifest_json, active)
                VALUES (%s, %s, %s, %s, TRUE)
                """,
                (ch, ver, url, manifest_json),
            )
