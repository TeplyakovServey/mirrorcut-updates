# -*- coding: utf-8 -*-
"""Подключение к PostgreSQL: config.py корня MIRROR_CUT или переменные окружения."""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Optional, Tuple

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

def remote_db_price_int(value: Any, category: str = "materials") -> int:
    """Целые ₽ из БД (прайс стекла/обработки нормализован в таблицах, см. tools/apply_glass_prices_div_1_2.py)."""
    _ = category
    if value is None:
        return 0
    try:
        return max(0, int(round(float(value))))
    except (TypeError, ValueError):
        return 0


def _facet_row_remote_prices(row: dict) -> dict:
    out = dict(row)
    for k, v in list(out.items()):
        sk = str(k).lower()
        if sk.startswith("material_") and "mm" in sk:
            out[k] = remote_db_price_int(v, "facet_price")
    return out


def _drilling_row_remote_prices(row: dict) -> dict:
    out = dict(row)
    for k, v in list(out.items()):
        sk = str(k).lower()
        if sk.startswith("thickness_") and sk.endswith("mm"):
            out[k] = remote_db_price_int(v, "drilling_prices")
    return out


def _furniture_row_remote_prices(row: dict) -> dict:
    out = dict(row)
    for k in ("price_legal", "price_individual"):
        if k in out:
            out[k] = remote_db_price_int(out[k], "furniture")
    return out

try:
    import psycopg2
except ImportError:
    psycopg2 = None  # type: ignore

# Кэш прайса сверления: редко меняется, тяжёлый повтор при каждом открытии сводки не нужен.
_drilling_prices_cache: Optional[list] = None


def _config_tuple() -> Tuple[str, str, str, str, str]:
    host = os.environ.get("MC_PG_HOST", "").strip()
    port = os.environ.get("MC_PG_PORT", "5432").strip()
    dbname = os.environ.get("MC_PG_DB", "").strip()
    user = os.environ.get("MC_PG_USER", "").strip()
    password = os.environ.get("MC_PG_PASSWORD", "").strip()
    if host and dbname and user:
        return host, port, dbname, user, password
    try:
        import config as mc_config

        c = getattr(mc_config, "DB_CONFIG", {})
        return (
            str(c.get("host", "") or ""),
            str(c.get("port", "5432") or "5432"),
            str(c.get("dbname", "") or ""),
            str(c.get("user", "") or ""),
            str(c.get("password", "") or ""),
        )
    except Exception:
        return "", "5432", "", "", ""


def get_raw_connection(max_attempts: int = 4, base_delay_sec: float = 0.25):
    """
    Одно соединение на пересчёт. Повторы при временных сбоях (пул, сеть, «503» у прокси).
    """
    if not psycopg2:
        return None
    host, port, dbname, user, password = _config_tuple()
    if not user:
        return None
    for attempt in range(max(1, int(max_attempts))):
        try:
            return psycopg2.connect(
                dbname=dbname,
                user=user,
                password=password,
                host=host,
                port=port,
                connect_timeout=12,
            )
        except Exception:
            if attempt < max_attempts - 1:
                time.sleep(base_delay_sec * (attempt + 1))
    return None


def fetch_all_material_types() -> list:
    conn = get_raw_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT material_type FROM materials ORDER BY material_type")
            return [r[0] for r in cur.fetchall() if r[0]]
    finally:
        conn.close()


def fetch_variants(material_type: str) -> list:
    conn = get_raw_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT material_variant FROM materials WHERE material_type = %s ORDER BY material_variant",
                (material_type,),
            )
            return [r[0] for r in cur.fetchall() if r[0]]
    finally:
        conn.close()


def fetch_thicknesses(material_type: str, variant: str) -> list:
    conn = get_raw_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT thickness FROM materials
                WHERE material_type = %s AND material_variant = %s
                ORDER BY thickness
                """,
                (material_type, variant),
            )
            return [int(r[0]) for r in cur.fetchall() if r[0] is not None]
    finally:
        conn.close()


def fetch_material_row(material_type: str, variant: str, thickness: int) -> Optional[Tuple[Any, Any]]:
    conn = get_raw_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT price, status_zakalka FROM materials
                WHERE material_type = %s AND material_variant = %s AND thickness = %s
                """,
                (material_type, variant, thickness),
            )
            row = cur.fetchone()
            if not row:
                return None
            return (remote_db_price_int(row[0], "materials"), row[1])
    finally:
        conn.close()


def fetch_scalar(query: str, params: tuple, conn=None) -> Any:
    """Если передан conn — запрос в существующем соединении (один connect на весь пересчёт)."""
    own = conn is None
    if own:
        conn = get_raw_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        if own and conn:
            conn.close()


def fetch_facet_price_rows(conn=None) -> list:
    own = conn is None
    if own:
        conn = get_raw_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM facet_price")
            cols = [d[0] for d in cur.description]
            return [_facet_row_remote_prices(dict(zip(cols, row))) for row in cur.fetchall()]
    finally:
        if own and conn:
            conn.close()


def load_materials_tree() -> dict:
    """
    Один запрос: {material_type: {variant: [(thickness, price, status_zakalka), ...]}}
    """
    conn = get_raw_connection()
    if not conn:
        return {}
    tree = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT material_type, material_variant, thickness, price, status_zakalka
                FROM materials
                ORDER BY material_type, material_variant, thickness
                """
            )
            for mt, mv, th, price, st in cur.fetchall():
                if not mt or not mv:
                    continue
                tree.setdefault(mt, {}).setdefault(mv, []).append(
                    (int(th or 0), remote_db_price_int(price, "materials"), int(st or 0))
                )
        return tree
    except Exception:
        return {}
    finally:
        conn.close()


def tree_lookup_price_status(tree: dict, mt: str, variant: str, thickness: int) -> Optional[Tuple[int, int]]:
    if not tree or mt not in tree or variant not in tree[mt]:
        return None
    for th, price, st in tree[mt][variant]:
        if th == thickness:
            return price, st
    return None


def fetch_plenka_options() -> list:
    conn = get_raw_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, price FROM plenka ORDER BY id")
            return [(r[0], remote_db_price_int(r[1], "plenka")) for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_pokraska_options() -> list:
    conn = get_raw_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, price FROM pokraska ORDER BY id")
            return [(r[0], remote_db_price_int(r[1], "pokraska")) for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_photo_print_price(conn=None) -> int:
    v = fetch_scalar(
        "SELECT price FROM photo_print_price WHERE service = %s",
        ("Установка фотопечати",),
        conn=conn,
    )
    return remote_db_price_int(v, "photo_print")


# Если таблица manual_edge_processing_price ещё не создана (миграция не применена) — эти значения.
MANUAL_EDGE_PRICE_FALLBACK_RUB = {4: 300, 5: 320, 6: 350, 8: 450, 10: 450}


def fetch_manual_edge_price(thickness_mm: int, conn=None) -> Optional[int]:
    th = int(thickness_mm)
    own = conn is None
    if own:
        conn = get_raw_connection()
    if not conn:
        return MANUAL_EDGE_PRICE_FALLBACK_RUB.get(th)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT price_rub FROM manual_edge_processing_price WHERE thickness_mm = %s",
                (th,),
            )
            row = cur.fetchone()
            if row is not None and row[0] is not None:
                return remote_db_price_int(row[0], "manual_edge")
    except Exception:
        pass
    finally:
        if own and conn:
            conn.close()
    return MANUAL_EDGE_PRICE_FALLBACK_RUB.get(th)


def fetch_sandblasting_price(sand_type_key: str, conn=None) -> int:
    """sand_type_key: нижний регистр, например 'рисунок', 'полосы зсп'."""
    v = fetch_scalar(
        "SELECT price FROM sandblasting_price WHERE type = %s",
        (sand_type_key.lower().strip(),),
        conn=conn,
    )
    return remote_db_price_int(v, "sandblasting")


def insert_photo_print_upload(
    file_bytes: bytes, mime_type: str = "", file_name: str = "", conn=None
) -> Optional[int]:
    if not psycopg2:
        return None
    own = conn is None
    if own:
        conn = get_raw_connection()
    if not conn:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO photo_print_uploads (mime_type, file_name, data)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (mime_type or None, file_name or None, file_bytes),
            )
            row = cur.fetchone()
            rid = int(row[0]) if row else None
        if own:
            conn.commit()
        return rid
    except Exception:
        if own and conn:
            conn.rollback()
        return None
    finally:
        if own and conn:
            conn.close()


# Если таблица blocks_furniture ещё не создана — тот же справочник, что в 004_furniture.sql (id 1..11).
FURNITURE_FALLBACK = (
    {
        "id": 1,
        "name": "Держатель 5.01 Д, D=16 мм, (БГ), S=6-8 мм",
        "color": "Белый глянец",
        "price_legal": 250,
        "price_individual": 350,
        "photo_base": "5.01-BG-D_16-6_8",
    },
    {
        "id": 2,
        "name": "Держатель 5.01 Д, D=16 мм, (БГ)",
        "color": "Белый глянец",
        "price_legal": 250,
        "price_individual": 350,
        "photo_base": "5.01-BG-D_16",
    },
    {
        "id": 3,
        "name": "Держатель 5.01 Д, D=22 мм, (001)",
        "color": "Хром",
        "price_legal": 250,
        "price_individual": 350,
        "photo_base": "5-.01-D-tsvet-kor",
    },
    {
        "id": 4,
        "name": "Держатель 5.01 Д, D=16 мм, (ЧМ)",
        "color": "Черный матовый",
        "price_legal": 250,
        "price_individual": 350,
        "photo_base": "5.01-CHM-D_16-_6_8_",
    },
    {
        "id": 5,
        "name": "Держатель 5.01 Д, D=16 мм, (ЧМ), S=6-8 мм",
        "color": "Черный матовый",
        "price_legal": 250,
        "price_individual": 350,
        "photo_base": "5.01-CHM-D_16",
    },
    {
        "id": 6,
        "name": "Держатель 5.01 Д, D=16 мм, (001)",
        "color": "Хром",
        "price_legal": 250,
        "price_individual": 350,
        "photo_base": "5-.01-D-tsvet-kor",
    },
    {
        "id": 7,
        "name": "Держатель 5.10 Д, (001)",
        "color": "Хром",
        "price_legal": 250,
        "price_individual": 350,
        "photo_base": "5-.10-D-dorbotka-tsveta-",
    },
    {
        "id": 8,
        "name": "Держатель 5.01, D=16 мм",
        "color": "Никель",
        "price_legal": 250,
        "price_individual": 350,
        "photo_base": "5.01-D16_1",
    },
    {
        "id": 9,
        "name": "Держатель 5.01, D=16 мм",
        "color": "Никель матовый",
        "price_legal": 250,
        "price_individual": 350,
        "photo_base": "5.01-D16_3",
    },
    {
        "id": 10,
        "name": "Держатель 5.01, D=16 мм",
        "color": "Золото",
        "price_legal": 250,
        "price_individual": 350,
        "photo_base": "5.01-D16_2",
    },
    {
        "id": 11,
        "name": "Держатель 5.10 Д",
        "color": "Черный глянец",
        "price_legal": 250,
        "price_individual": 350,
        "photo_base": "derzh_5_10D_2021-chernyy",
    },
)


def _furniture_row_fallback(furniture_id: int) -> Optional[dict]:
    for r in FURNITURE_FALLBACK:
        if int(r["id"]) == int(furniture_id):
            return dict(r)
    return None


_FURNITURE_SELECT_EXT = (
    "id, name, color, price_legal, price_individual, photo_base, "
    "COALESCE(source_url, '') AS source_url, thickness_mm, "
    "COALESCE(is_shelf_holder, FALSE) AS is_shelf_holder"
)


def _query_furniture_catalog(conn, thickness_mm: int = 0) -> list:
    """Универсальная фурнитура (не полка, thickness_mm IS NULL). Полкодержатели — только
    если толщина материала 6 или 8 мм, и только строки с thickness_mm, равной этой толщине."""
    th = int(thickness_mm or 0)
    with conn.cursor() as cur:
        try:
            cur.execute(
                """
                SELECT """ + _FURNITURE_SELECT_EXT + """
                FROM blocks_furniture
                WHERE (
                    COALESCE(is_shelf_holder, FALSE) = FALSE
                    AND thickness_mm IS NULL
                )
                OR (
                    COALESCE(is_shelf_holder, FALSE) = TRUE
                    AND %s IN (6, 8)
                    AND thickness_mm IS NOT NULL
                    AND thickness_mm = %s
                )
                ORDER BY is_shelf_holder, name, color, thickness_mm NULLS LAST, id
                """,
                (th, th),
            )
        except Exception:
            cur.execute(
                """
                SELECT id, name, color, price_legal, price_individual, photo_base
                FROM blocks_furniture
                ORDER BY name, color, id
                """
            )
        cols = [d[0] for d in cur.description]
        return [
            {str(k).lower(): v for k, v in zip(cols, row)}
            for row in cur.fetchall()
        ]


def fetch_drilling_price_rows(conn=None) -> list:
    global _drilling_prices_cache
    if _drilling_prices_cache is not None and conn is None:
        return list(_drilling_prices_cache)
    own = conn is None
    if own:
        conn = get_raw_connection()
    if not conn:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM drilling_prices")
            cols = [d[0] for d in cur.description]
            rows = [_drilling_row_remote_prices(dict(zip(cols, row))) for row in cur.fetchall()]
        if own and rows:
            _drilling_prices_cache = list(rows)
        return rows
    finally:
        if own and conn:
            conn.close()


def fetch_furniture_catalog(conn=None, thickness_mm: Optional[int] = None) -> list:
    """
    Справочник фурнитуры. Поля: id, name, color, price_*, photo_base,
    source_url, thickness_mm, is_shelf_holder (после миграции 005_shelf_furniture.sql).

    thickness_mm: толщина материала (мм). Полкодержатели только при 6 или 8 и только
    с совпадающим thickness_mm; иначе в списке только универсальная фурнитура.
    При пустой таблице или ошибке — встроенный список (id 1..11, без полок).
    """
    th = int(thickness_mm or 0)
    own = conn is None
    if own:
        conn = get_raw_connection()
    if not conn:
        return [dict(r) for r in FURNITURE_FALLBACK]
    rows: list = []
    try:
        rows = _query_furniture_catalog(conn, th)
    except Exception:
        rows = []
    finally:
        if own and conn:
            conn.close()
    if rows:
        return [_furniture_row_remote_prices(r) for r in rows]
    return [dict(r) for r in FURNITURE_FALLBACK]


def fetch_furniture_row(furniture_id: int, conn=None) -> Optional[dict]:
    own = conn is None
    if own:
        conn = get_raw_connection()
    if not conn:
        return _furniture_row_fallback(furniture_id)
    row = None
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT """ + _FURNITURE_SELECT_EXT + """
                    FROM blocks_furniture WHERE id = %s
                    """,
                    (int(furniture_id),),
                )
            except Exception:
                cur.execute(
                    """
                    SELECT id, name, color, price_legal, price_individual, photo_base
                    FROM blocks_furniture WHERE id = %s
                    """,
                    (int(furniture_id),),
                )
            tup = cur.fetchone()
            if tup:
                cols = [d[0] for d in cur.description]
                row = {str(k).lower(): v for k, v in zip(cols, tup)}
    except Exception:
        row = None
    finally:
        if own and conn:
            conn.close()
    if row:
        return _furniture_row_remote_prices(row)
    return _furniture_row_fallback(furniture_id)


_UF_SKLEYKA_PRICE_FALLBACK = {
    "meter_by_thickness": {4: 1320, 5: 1320, 6: 1320, 8: 1320, 10: 1320},
    "hinge_paste_one_rub": 320,
    "hinge_remove_one_rub": 320,
}


def fetch_uf_skleyka_prices(conn=None) -> dict:
    """
    Тарифы УФ-склейки из blocks_uf_skleyka_prices (007_uf_skleyka_prices.sql).
    thickness_mm > 0 — цена за погонный метр; thickness_mm = 0 — строка с ценами за петлю.
    При отсутствии таблицы / ошибке — значения по умолчанию из ТЗ.
    """
    fb_m = dict(_UF_SKLEYKA_PRICE_FALLBACK["meter_by_thickness"])
    fb_hp = int(_UF_SKLEYKA_PRICE_FALLBACK["hinge_paste_one_rub"])
    fb_hr = int(_UF_SKLEYKA_PRICE_FALLBACK["hinge_remove_one_rub"])
    own = conn is None
    if own:
        conn = get_raw_connection()
    if not conn:
        return {
            "meter_by_thickness": fb_m,
            "hinge_paste_one_rub": fb_hp,
            "hinge_remove_one_rub": fb_hr,
        }
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT thickness_mm, price_per_meter_rub, hinge_paste_one_rub, hinge_remove_one_rub
                FROM blocks_uf_skleyka_prices
                """
            )
            rows = cur.fetchall()
        if not rows:
            return {
                "meter_by_thickness": fb_m,
                "hinge_paste_one_rub": fb_hp,
                "hinge_remove_one_rub": fb_hr,
            }
        meter: dict = {}
        hp, hr = fb_hp, fb_hr
        for tm, pm, hpp, hrr in rows:
            try:
                tmv = int(tm)
            except (TypeError, ValueError):
                continue
            if tmv > 0:
                meter[tmv] = remote_db_price_int(pm, "uf_skleyka")
            elif tmv == 0:
                hp = remote_db_price_int(hpp, "uf_skleyka")
                hr = remote_db_price_int(hrr, "uf_skleyka")
        if not meter:
            meter = fb_m
        return {
            "meter_by_thickness": meter,
            "hinge_paste_one_rub": hp,
            "hinge_remove_one_rub": hr,
        }
    except Exception:
        return {
            "meter_by_thickness": fb_m,
            "hinge_paste_one_rub": fb_hp,
            "hinge_remove_one_rub": fb_hr,
        }
    finally:
        if own and conn:
            conn.close()


_VIREZ_PRICE_FALLBACK = (
    {"category_code": "simple", "title_ru": "Простой", "price_rub": 1500},
    {"category_code": "medium", "title_ru": "Средний", "price_rub": 3000},
    {"category_code": "complex", "title_ru": "Сложный", "price_rub": 5000},
)


def fetch_virez_price_table(conn=None) -> list:
    """
    Справочник вырезов: category_code (simple/medium/complex), title_ru, price_rub.
    Таблица blocks_virez_prices (008_blocks_virez_prices.sql).
    """
    own = conn is None
    if own:
        conn = get_raw_connection()
    if not conn:
        return [dict(x) for x in _VIREZ_PRICE_FALLBACK]
    rows_out: list = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT category_code, title_ru, price_rub
                FROM blocks_virez_prices
                ORDER BY id
                """
            )
            for code, title, pr in cur.fetchall():
                rows_out.append(
                    {
                        "category_code": str(code or "").strip(),
                        "title_ru": str(title or "").strip(),
                        "price_rub": remote_db_price_int(pr, "virez"),
                    }
                )
        if not rows_out:
            return [dict(x) for x in _VIREZ_PRICE_FALLBACK]
        return rows_out
    except Exception:
        return [dict(x) for x in _VIREZ_PRICE_FALLBACK]
    finally:
        if own and conn:
            conn.close()


def fetch_packaging_prices(conn=None) -> dict[str, int]:
    """Таблица packaging_price: ключ — lower(packaging_type), значение — price (₽)."""
    own = conn is None
    if own:
        conn = get_raw_connection()
    out: dict[str, int] = {}
    if not conn:
        return out
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT packaging_type, price FROM packaging_price")
            for name, price in cur.fetchall():
                if name:
                    out[str(name).strip().lower()] = remote_db_price_int(price, "packaging")
    except Exception:
        pass
    finally:
        if own and conn:
            conn.close()
    return out


def insert_blocks_zamer(
    conn,
    *,
    client_id: Optional[int],
    address: str,
    date_from: Optional[str],
    date_to: Optional[str],
    phone: str,
    matches_client: bool,
    extra_text: str,
    is_measure: bool = True,
    is_install: bool = False,
    is_delivery: bool = False,
    service_type: str = "measure",
    status: str = "new",
) -> Optional[int]:
    """INSERT в blocks_zamer; нужен открытый conn и транзакция снаружи."""
    try:
        with conn.cursor() as cur:
            # Совместимость со старой схемой: добавляем колонки один раз при сохранении.
            cur.execute("ALTER TABLE blocks_zamer ADD COLUMN IF NOT EXISTS is_measure BOOLEAN NOT NULL DEFAULT TRUE")
            cur.execute("ALTER TABLE blocks_zamer ADD COLUMN IF NOT EXISTS is_install BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE blocks_zamer ADD COLUMN IF NOT EXISTS service_type VARCHAR(16) NOT NULL DEFAULT 'measure'")
            cur.execute("ALTER TABLE blocks_zamer ADD COLUMN IF NOT EXISTS is_delivery BOOLEAN NOT NULL DEFAULT FALSE")
            try:
                cur.execute(
                    "ALTER TABLE blocks_zamer ALTER COLUMN service_type TYPE VARCHAR(32) USING service_type::varchar(32)"
                )
            except Exception:
                pass
            cur.execute(
                """
                INSERT INTO blocks_zamer (
                    client_id, address, date_from, date_to, phone,
                    matches_client, extra_text, is_measure, is_install, is_delivery, service_type, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    client_id,
                    address or "",
                    date_from,
                    date_to,
                    phone or "",
                    matches_client,
                    extra_text or "",
                    bool(is_measure),
                    bool(is_install),
                    bool(is_delivery),
                    str(service_type or "measure")[:32],
                    (status or "new")[:20],
                ),
            )
            row = cur.fetchone()
            return int(row[0]) if row else None
    except Exception:
        return None


def insert_blocks_zamer_file(
    conn,
    zamer_id: int,
    file_url: str,
    comment: str = "",
    uploaded_by: str = "",
    file_kind: str = "measure",
) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE blocks_zamer_file ADD COLUMN IF NOT EXISTS file_kind VARCHAR(24) NOT NULL DEFAULT 'measure'"
            )
            fk = (file_kind or "measure").strip().lower()
            if fk not in ("measure", "delivery", "install"):
                fk = "measure"
            cur.execute(
                """
                INSERT INTO blocks_zamer_file (zamer_id, file_url, comment, uploaded_by, file_kind)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (int(zamer_id), file_url or "", comment or "", uploaded_by or "", fk),
            )
        return True
    except Exception:
        return False


def fetch_blocks_zamer_files(conn, zamer_id: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "ALTER TABLE blocks_zamer_file ADD COLUMN IF NOT EXISTS file_kind VARCHAR(24) NOT NULL DEFAULT 'measure'"
            )
            cur.execute(
                """
                SELECT id, file_url, comment, uploaded_by,
                       COALESCE(file_kind, 'measure') AS file_kind, created_at
                FROM blocks_zamer_file
                WHERE zamer_id = %s
                ORDER BY id
                """,
                (int(zamer_id),),
            )
            cols = [d[0] for d in cur.description] if cur.description else []
            for tup in cur.fetchall():
                rows.append({str(c): v for c, v in zip(cols, tup)})
    except Exception:
        pass
    return rows
