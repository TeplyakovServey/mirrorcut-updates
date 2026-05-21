# -*- coding: utf-8 -*-
"""
Каталог душевых: SQLite и папки выгрузки рядом с БД.

Новая выгрузка (см. LAYOUT.txt в корне бандла):
  dushevye.db, categories/<категория>/<000N_slug>/description.txt, images/01.jpg…,
  shared/furnitura/photos/ и shared/furnitura/descriptions/ (тот же шлейф имени, что у .jpg).
  Таблицы: categories, products (category_id, folder_relpath, …), product_images,
  kit_items, furnitura_photos.

Старая выгрузка (совместимость):
  av24_dushevye.db, dushevye_out\\…, subcategories, products.subcategory_id, main_image_relpath.

part_url вида urn:av24:kitline:… — текст из описания комплекта, отдельного фото нет.

В каталоге (PyQt) позиции с одинаковой «базой» slug av24 (последний сегмент — код отделки)
сводятся в один слот с переключателем цвета/покрытия по подписи из названия (после запятой).
"""
import os
import re
import sqlite3
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

try:
    from urllib.parse import unquote
except ImportError:
    from urllib import unquote  # type: ignore

from cfg_loader import app_cfg, get_base_dir, get_cfg_string, get_mirror_cut_root

_SHOWER_FOLDER = "\u0434\u0443\u0448\u0435\u0432\u044b\u0435"
_MEDIA_OUT = "dushevye_out"
_DB_FILES = ("dushevye.db", "av24_dushevye.db")
# Кэш по id(conn); сброс — clear_shower_caches_for_connection (перед conn.close).
_bundle_schema_by_id: Dict[int, int] = {}
_furn_map_by_conn_id: Dict[int, Dict[str, str]] = {}


def clear_shower_caches_for_connection(conn: Optional[sqlite3.Connection]) -> None:
    if conn is None:
        return
    i = id(conn)
    _bundle_schema_by_id.pop(i, None)
    _furn_map_by_conn_id.pop(i, None)


def _bundle_completeness(bundle_root: str) -> int:
    """Чем выше — тем полнее выгрузка (чтобы не взять папку только с БД без фото)."""
    if not bundle_root or not os.path.isdir(bundle_root):
        return 0
    score = 0
    if os.path.isdir(os.path.join(bundle_root, "shared", "furnitura", "photos")):
        score += 500
    if os.path.isdir(os.path.join(bundle_root, "categories")):
        score += 200
    if os.path.isdir(_dushevye_out_dir(bundle_root)):
        score += 50
    if _pick_db_in_dir(bundle_root):
        score += 10
    return score


def _pick_db_in_dir(folder: str) -> Optional[str]:
    if not folder or not os.path.isdir(folder):
        return None
    for name in _DB_FILES:
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            return p
    return None


def find_shower_bundle() -> Optional[Tuple[str, str]]:
    """
    (путь_к_db, корень_бандла). Корень — папка с dushevye.db или av24_dushevye.db.

    Если «душевые» есть и в MAIN_PROJECT, и в MIRROR_CUT, выбирается каталог с более
    полной выгрузкой (есть shared/furnitura/photos и categories), чтобы фото не терялись.
    """
    cfg = app_cfg()
    explicit = get_cfg_string(cfg, "paths", "shower_catalog_dir", "") if cfg else ""
    if explicit and os.path.isdir(explicit):
        db = _pick_db_in_dir(explicit)
        if db:
            return db, os.path.normpath(explicit)

    def _try_bundle(parent: str) -> Optional[Tuple[str, str]]:
        base = os.path.join(parent, _SHOWER_FOLDER)
        db = _pick_db_in_dir(base)
        if db:
            return db, os.path.normpath(base)
        return None

    candidates: List[Tuple[str, str]] = []
    for parent in (get_mirror_cut_root(), get_base_dir()):
        hit = _try_bundle(parent)
        if hit and hit not in candidates:
            candidates.append(hit)

    root = get_mirror_cut_root()
    for dirpath, _, filenames in os.walk(root):
        if "dushevye.db" in filenames:
            dbp = os.path.join(dirpath, "dushevye.db")
            br = os.path.normpath(dirpath)
            t = (dbp, br)
            if t not in candidates:
                candidates.append(t)
        elif "av24_dushevye.db" in filenames:
            dbp = os.path.join(dirpath, "av24_dushevye.db")
            br = os.path.normpath(dirpath)
            t = (dbp, br)
            if t not in candidates:
                candidates.append(t)

    if not candidates:
        return None

    def normkey(br: str) -> str:
        return os.path.normcase(os.path.abspath(br))

    best_by_key: Dict[str, Tuple[str, str]] = {}
    for db_path, bundle_root in candidates:
        k = normkey(bundle_root)
        sc = _bundle_completeness(bundle_root)
        prev = best_by_key.get(k)
        if prev is None or sc > _bundle_completeness(prev[1]):
            best_by_key[k] = (db_path, bundle_root)

    ranked = list(best_by_key.values())
    ranked.sort(key=lambda x: _bundle_completeness(x[1]), reverse=True)
    return ranked[0]


def bundle_schema(conn: sqlite3.Connection) -> int:
    """2 — новая схема (categories, product_images); 1 — старая (subcategories)."""
    i = id(conn)
    if i in _bundle_schema_by_id:
        return _bundle_schema_by_id[i]
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    names = {r[0] for r in cur.fetchall()}
    if "categories" in names and "product_images" in names:
        v = 2
    else:
        v = 1
    _bundle_schema_by_id[i] = v
    return v


def _sqlite_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table_name,),
    )
    return cur.fetchone() is not None


def _dushevye_out_dir(bundle_root: str) -> str:
    return os.path.join(bundle_root, _MEDIA_OUT)


def _furnitura_dirs(bundle_root: str, out_dir: str) -> List[str]:
    roots = []
    for d in (
        os.path.join(bundle_root, "shared", "furnitura", "photos"),
        os.path.join(out_dir, "furnitura_photos"),
        os.path.join(bundle_root, "furnitura_photos"),
        os.path.join(bundle_root, _MEDIA_OUT, "furnitura_photos"),
    ):
        if os.path.isdir(d) and d not in roots:
            roots.append(d)
    return roots


def _abs_media(bundle_root: str, relpath: Optional[str]) -> Optional[str]:
    """
    Абсолютный путь к файлу изображения.
    Новая выгрузка: пути в БД относительно корня бандла (categories\\…, shared\\…).
    Старая: dushevye_out\\ и пр.; см. LAYOUT.txt / старые правила.
    """
    if not relpath:
        return None
    raw = (relpath or "").strip("\ufeff \t")
    if not raw:
        return None
    if os.path.isabs(raw) and os.path.isfile(raw):
        return os.path.normpath(raw)

    variants: List[str] = []
    for r in (raw, unquote(raw.replace("\\", "/"))):
        if r and r not in variants:
            variants.append(r)

    out_dir = _dushevye_out_dir(bundle_root)
    furn_roots = _furnitura_dirs(bundle_root, out_dir)
    seen = set()
    sep = os.sep

    def add(cand: List[str], p: str) -> None:
        n = os.path.normpath(p)
        if n not in seen:
            seen.add(n)
            cand.append(n)

    all_cand: List[str] = []

    def collect_legacy(rel: str) -> None:
        rel = rel.replace("/", sep).strip().strip(sep)
        if not rel:
            return
        rel_norm = rel.replace("\\", "/")
        low = rel_norm.lower()
        if low.startswith(_MEDIA_OUT.lower() + "/") or low == _MEDIA_OUT.lower():
            add(all_cand, os.path.join(bundle_root, rel))
        if os.path.isdir(out_dir):
            add(all_cand, os.path.join(out_dir, rel))
        add(all_cand, os.path.join(bundle_root, rel))
        base_name = os.path.basename(rel)
        if base_name and base_name != rel:
            for fr in furn_roots:
                add(all_cand, os.path.join(fr, base_name))
        if base_name == rel:
            for fr in furn_roots:
                add(all_cand, os.path.join(fr, rel))

    for variant in variants:
        rel = variant.replace("/", sep).strip().strip(sep)
        if not rel:
            continue
        # Новая выгрузка: путь от корня бандла
        add(all_cand, os.path.join(bundle_root, rel))
        low = rel.replace("\\", "/").lower()
        if "shared" in low or "categories" in low:
            bn = os.path.basename(rel)
            if bn:
                sp = os.path.join(bundle_root, "shared", "furnitura", "photos", bn)
                add(all_cand, sp)
        collect_legacy(variant)

    for p in all_cand:
        if os.path.isfile(p):
            return p
    return None


def _furnitura_local_by_url(conn: sqlite3.Connection) -> Dict[str, str]:
    if not _sqlite_table_exists(conn, "furnitura_photos"):
        return {}
    cur = conn.cursor()
    cur.execute(
        "SELECT TRIM(part_url) AS u, local_relpath FROM furnitura_photos "
        "WHERE local_relpath IS NOT NULL AND TRIM(local_relpath) != ''"
    )
    m: Dict[str, str] = {}
    for u, loc in cur.fetchall():
        if not u or not loc:
            continue
        loc = loc.strip()
        u = u.strip()
        for key in (u, u.rstrip("/"), u.lower() if u.lower().startswith("http") else None):
            if key and key not in m:
                m[key] = loc
    return m


def _is_kitline_urn(part_url: Optional[str]) -> bool:
    u = (part_url or "").strip().lower()
    return u.startswith("urn:av24:kitline:")


def _normalize_finish_label(s: str) -> str:
    t = (s or "").strip().lower().replace("ё", "е")
    t = re.sub(r"\s+", " ", t)
    return t


def finish_label_from_part_name(part_name: str) -> str:
    """
    Короткая подпись отделки для переключателя: из хвоста названия после запятой,
    при «материал/покрытие» берётся покрытие.
    """
    n = (part_name or "").strip()
    if not n:
        return "—"
    tail = n.rsplit(",", 1)[-1].strip()
    if "/" in tail:
        return tail.rsplit("/", 1)[-1].strip()
    return tail


def _http_slug_variant_base(part_url: str) -> Optional[str]:
    """
    Для URL av24: последний сегмент пути вида «…-brcr» / «…-brbl» — общая база без кода отделки.
    Одинаковая база в одном комплекте = одна деталь, варианты цвета/покрытия.
    """
    u = (part_url or "").strip()
    if not u or not u.lower().startswith("http"):
        return None
    try:
        from urllib.parse import urlparse

        path = (urlparse(u).path or "").strip().rstrip("/")
    except Exception:
        return None
    if not path:
        return None
    seg = path.split("/")[-1]
    if not seg or "-" not in seg:
        return None
    base, last = seg.rsplit("-", 1)
    if not base or not last:
        return None
    if len(last) > 16 or len(last) < 2:
        return None
    if not last.replace(".", "").isalnum():
        return None
    if not any(ch.isalpha() for ch in last):
        return None
    return base.lower()


def _kit_variant_group_key(item: Dict[str, Any]) -> str:
    pu = (item.get("part_url") or "").strip()
    b = _http_slug_variant_base(pu)
    if b:
        return "u:" + b
    return "k:%s" % int(item.get("kit_id") or 0)


def build_kit_display_plan(kit_items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Слоты комплекта: в каждом слоте один или несколько вариантов (одна фурнитура — разные slug).
    palette — уникальные подписи отделки только из «множественных» слотов (для переключателя).
    """
    if not kit_items:
        return {"use_color_switch": False, "palette": [], "slots": []}

    buckets: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for it in kit_items:
        key = _kit_variant_group_key(it)
        buckets.setdefault(key, []).append(it)

    slots: List[List[Dict[str, Any]]] = []
    palette_keys: List[str] = []
    seen_pal: set[str] = set()

    for _key, variants in buckets.items():
        variants = sorted(variants, key=lambda x: int(x.get("kit_id") or 0))
        for v in variants:
            lab = finish_label_from_part_name(v.get("part_name") or "")
            v["_finish_label"] = lab
        slots.append(variants)
        if len(variants) > 1:
            for v in variants:
                nk = _normalize_finish_label(v.get("_finish_label") or "")
                if nk and nk not in seen_pal:
                    seen_pal.add(nk)
                    palette_keys.append(v.get("_finish_label") or "")

    use = any(len(s) > 1 for s in slots)
    palette_keys.sort(key=lambda s: _normalize_finish_label(s))
    return {"use_color_switch": use, "palette": palette_keys, "slots": slots}


def pick_kit_items_for_finish(slots: List[List[Dict[str, Any]]], finish: str) -> List[Dict[str, Any]]:
    """По выбранной отделке: из каждого слота берётся подходящий вариант или первый."""
    want = _normalize_finish_label(finish)
    out: List[Dict[str, Any]] = []
    for variants in slots:
        if len(variants) == 1:
            out.append(variants[0])
            continue
        best = variants[0]
        for v in variants:
            got = _normalize_finish_label(v.get("_finish_label") or "")
            if want and got == want:
                best = v
                break
        else:
            for v in variants:
                got = _normalize_finish_label(v.get("_finish_label") or "")
                if want and (want in got or got in want):
                    best = v
                    break
        out.append(best)
    return out


def _furnitura_description_relpath_from_image_relpath(image_relpath: Optional[str]) -> Optional[str]:
    """
    shared/furnitura/photos/foo.jpg → shared/furnitura/descriptions/foo.txt
    """
    if not image_relpath:
        return None
    s = image_relpath.replace("/", os.sep).strip().strip(os.sep)
    if not s:
        return None
    parts = s.split(os.sep)
    if not parts:
        return None
    repl = False
    out_parts: List[str] = []
    for p in parts:
        if p.lower() == "photos":
            out_parts.append("descriptions")
            repl = True
        else:
            out_parts.append(p)
    if not repl:
        return None
    stem, _ext = os.path.splitext(out_parts[-1])
    if not stem:
        return None
    out_parts[-1] = stem + ".txt"
    return os.sep.join(out_parts)


def _read_furnitura_description_file(bundle_root: str, image_relpath: Optional[str]) -> str:
    """Текст из descriptions/*.txt рядом с фото в photos, UTF-8."""
    rel = _furnitura_description_relpath_from_image_relpath(image_relpath)
    if not rel:
        return ""
    path = _abs_media(bundle_root, rel)
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return (fh.read() or "").strip()
    except OSError:
        return ""


def _merge_kit_descriptions(
    from_kit: str, from_furn_table: str, from_file: str
) -> str:
    """Приоритет: файл на диске, затем кит, затем furnitura_photos."""
    a = (from_file or "").strip()
    if a:
        return a
    b = (from_kit or "").strip()
    c = (from_furn_table or "").strip()
    if b and c and c not in b and b not in c:
        return b + "\n\n" + c
    return b or c


def _shower_category_title_excluded(title: Optional[str]) -> bool:
    """Не показывать в каталоге душевых отдельную категорию про закалённое стекло (листовая номенклатура)."""
    t = (title or "").strip().lower().replace("ё", "е")
    if not t:
        return False
    if "закаленное стекло" in t:
        return True
    if "закален" in t and "стекл" in t and "душев" in t:
        return True
    return False


def sanitize_shower_catalog_description(text: Optional[str]) -> str:
    """
    Убирает из текста карточки типовые юридические вставки (политика ПДн, cookie, 152-ФЗ и т.п.).
    Разбиение по пустым строкам; при одном сплошном абзаце — по предложениям.
    """
    raw = (text or "").strip()
    if not raw:
        return ""

    def _is_policy_chunk(chunk: str) -> bool:
        low = chunk.lower().replace("ё", "е")
        if "политик" in low and ("персональн" in low or "персональных" in low):
            return True
        if "персональн" in low and "обработк" in low and ("данн" in low or "данных" in low):
            return True
        if "конфиденциальн" in low and "политик" in low:
            return True
        if "cookie" in low and ("политик" in low or "персональн" in low or "соглас" in low):
            return True
        if re.search(r"152\s*[-−]?\s*фз", low, re.I) and (
            "персональн" in low or "данн" in low or "обработк" in low
        ):
            return True
        return False

    chunks = re.split(r"\n\s*\n+", raw)
    if len(chunks) == 1 and len(raw) > 500:
        chunks = re.split(r"(?<=[.!?…])\s+", raw)
    kept: List[str] = []
    for ch in chunks:
        s = ch.strip()
        if not s or _is_policy_chunk(s):
            continue
        kept.append(s)
    return "\n\n".join(kept).strip()


def load_subcategories(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    if bundle_schema(conn) >= 2:
        cur.execute(
            "SELECT c.id, c.title, c.folder_name, c.list_url, "
            "(SELECT pi.relpath FROM products p "
            " JOIN product_images pi ON pi.product_id = p.id "
            " WHERE p.category_id = c.id "
            " ORDER BY pi.is_main DESC, pi.position ASC, pi.id ASC LIMIT 1) AS thumb "
            "FROM categories c ORDER BY c.title"
        )
    else:
        cur.execute(
            "SELECT s.id, s.title, s.folder_name, s.list_url, "
            "(SELECT p.main_image_relpath FROM products p "
            " WHERE p.subcategory_id = s.id AND p.main_image_relpath IS NOT NULL "
            " AND TRIM(p.main_image_relpath) != '' "
            " ORDER BY COALESCE(p.name, ''), p.id LIMIT 1) AS thumb "
            "FROM subcategories s ORDER BY s.title"
        )
    rows = []
    for sid, title, folder_name, list_url, thumb in cur.fetchall():
        if _shower_category_title_excluded(title):
            continue
        rows.append(
            {
                "id": int(sid),
                "title": title or "",
                "folder_name": folder_name or "",
                "list_url": list_url or "",
                "thumb_relpath": thumb,
            }
        )
    return rows


def load_products_for_subcategory(
    conn: sqlite3.Connection, subcategory_id: int
) -> List[Dict[str, Any]]:
    """subcategory_id — для новой схемы это category_id (тот же смысл для UI)."""
    cur = conn.cursor()
    if bundle_schema(conn) >= 2:
        cur.execute(
            "SELECT p.id, p.name, p.product_url, p.description, p.folder_relpath, "
            "(SELECT pi.relpath FROM product_images pi WHERE pi.product_id = p.id "
            " ORDER BY pi.is_main DESC, pi.position ASC, pi.id ASC LIMIT 1) AS main_relpath "
            "FROM products p WHERE p.category_id = ? "
            "ORDER BY COALESCE(p.name, ''), p.id",
            (subcategory_id,),
        )
        out = []
        for pid, name, product_url, description, folder_relpath, main in cur.fetchall():
            out.append(
                {
                    "id": int(pid),
                    "name": name or "",
                    "product_url": product_url or "",
                    "description": description or "",
                    "folder_relpath": folder_relpath or "",
                    "main_image_relpath": main,
                }
            )
        return out

    cur.execute(
        "SELECT id, name, product_url, NULL, NULL, main_image_relpath FROM products "
        "WHERE subcategory_id = ? ORDER BY COALESCE(name, ''), id",
        (subcategory_id,),
    )
    out = []
    for pid, name, product_url, _d0, _d1, main in cur.fetchall():
        out.append(
            {
                "id": int(pid),
                "name": name or "",
                "product_url": product_url or "",
                "description": "",
                "folder_relpath": "",
                "main_image_relpath": main,
            }
        )
    return out


def load_product_image_paths(conn: sqlite3.Connection, product_id: int) -> List[str]:
    """Новая схема: все relpath из product_images (01.jpg, 02.jpg, …). Старая — пустой список."""
    if bundle_schema(conn) < 2:
        return []
    cur = conn.cursor()
    cur.execute(
        "SELECT relpath FROM product_images WHERE product_id = ? "
        "ORDER BY position ASC, id ASC",
        (int(product_id),),
    )
    return [str(r[0]) for r in cur.fetchall() if r[0]]


def _kit_image_file_ok(bundle_root: Optional[str], rel: Optional[str]) -> bool:
    if not rel or not str(rel).strip():
        return False
    if not bundle_root:
        return True
    ap = _abs_media(bundle_root, rel)
    return bool(ap and os.path.isfile(ap))


def load_shower_category_image_fallbacks(
    conn: sqlite3.Connection,
    category_id: int,
    *,
    prefer_product_id: Optional[int] = None,
) -> List[str]:
    """
    Пути картинок товаров категории (сначала — у prefer_product_id), для подстановки
    в позиции комплекта без своего фото.
    """
    out: List[str] = []
    seen: set[str] = set()
    pref = int(prefer_product_id) if prefer_product_id is not None else -1
    cid = int(category_id)
    cur = conn.cursor()

    def _add(rel: Optional[str]) -> None:
        r = (rel or "").strip()
        if r and r not in seen:
            seen.add(r)
            out.append(r)

    if bundle_schema(conn) >= 2 and _sqlite_table_exists(conn, "product_images"):
        cur.execute(
            "SELECT pi.relpath FROM product_images pi "
            "INNER JOIN products p ON p.id = pi.product_id "
            "WHERE p.category_id = ? "
            "ORDER BY CASE WHEN p.id = ? THEN 0 ELSE 1 END, p.id, "
            "pi.is_main DESC, pi.position ASC, pi.id ASC",
            (cid, pref),
        )
        for (rel,) in cur.fetchall():
            _add(rel)
    elif bundle_schema(conn) >= 2:
        cur.execute(
            "SELECT p.id, "
            "(SELECT pi.relpath FROM product_images pi WHERE pi.product_id = p.id "
            " ORDER BY pi.is_main DESC, pi.position ASC, pi.id ASC LIMIT 1) AS main_relpath "
            "FROM products p WHERE p.category_id = ? "
            "ORDER BY CASE WHEN p.id = ? THEN 0 ELSE 1 END, p.id",
            (cid, pref),
        )
        for _pid, main in cur.fetchall():
            _add(main)
    else:
        cur.execute(
            "SELECT id, main_image_relpath FROM products WHERE subcategory_id = ? "
            "ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, id",
            (cid, pref),
        )
        for _pid, main in cur.fetchall():
            _add(main)
    return out


def _apply_kit_item_image_fallbacks(
    items: List[Dict[str, Any]],
    bundle_root: Optional[str],
    pool: List[str],
) -> None:
    """Подставить image_relpath из pool, если своего нет или файл на диске не найден."""
    clean = [str(x).strip() for x in pool if x and str(x).strip()]
    if not clean:
        return
    i_rot = 0
    for it in items:
        if it.get("is_kitline_text"):
            continue
        cur_rel = (it.get("image_relpath") or "").strip() or None
        if _kit_image_file_ok(bundle_root, cur_rel):
            continue
        chosen = None
        for rp in clean:
            if _kit_image_file_ok(bundle_root, rp):
                chosen = rp
                break
        if chosen is None:
            chosen = clean[i_rot % len(clean)]
            i_rot += 1
        it["image_relpath"] = chosen


def load_kit_items(
    conn: sqlite3.Connection,
    product_id: int,
    *,
    bundle_root: Optional[str] = None,
    category_id: Optional[int] = None,
    prefer_product_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    ci = id(conn)
    furn_map = _furn_map_by_conn_id.get(ci)
    if furn_map is None:
        furn_map = _furnitura_local_by_url(conn)
        _furn_map_by_conn_id[ci] = furn_map

    has_furn = _sqlite_table_exists(conn, "furnitura_photos")
    if bundle_schema(conn) >= 2 and has_furn:
        cur.execute(
            "SELECT k.id, k.part_name, TRIM(k.part_url) AS part_url, k.quantity, "
            "k.part_description, f.part_description, "
            "NULLIF(TRIM(k.part_image_relpath), ''), "
            "NULLIF(TRIM(f.local_relpath), '') "
            "FROM kit_items k "
            "LEFT JOIN furnitura_photos f ON TRIM(f.part_url) = TRIM(k.part_url) "
            "WHERE k.product_id = ? ORDER BY k.id",
            (product_id,),
        )
        rows = cur.fetchall()
    elif bundle_schema(conn) >= 2:
        cur.execute(
            "SELECT k.id, k.part_name, TRIM(k.part_url) AS part_url, k.quantity, "
            "k.part_description, NULL, "
            "NULLIF(TRIM(k.part_image_relpath), ''), "
            "NULL "
            "FROM kit_items k WHERE k.product_id = ? ORDER BY k.id",
            (product_id,),
        )
        rows = cur.fetchall()
    elif has_furn:
        cur.execute(
            "SELECT k.id, k.part_name, TRIM(k.part_url) AS part_url, k.quantity, "
            "NULL, NULL, "
            "NULLIF(TRIM(k.part_image_relpath), ''), "
            "NULLIF(TRIM(f.local_relpath), '') "
            "FROM kit_items k "
            "LEFT JOIN furnitura_photos f ON TRIM(f.part_url) = TRIM(k.part_url) "
            "WHERE k.product_id = ? ORDER BY k.id",
            (product_id,),
        )
        rows = cur.fetchall()
    else:
        cur.execute(
            "SELECT k.id, k.part_name, TRIM(k.part_url) AS part_url, k.quantity, "
            "NULL, NULL, "
            "NULLIF(TRIM(k.part_image_relpath), ''), "
            "NULL "
            "FROM kit_items k WHERE k.product_id = ? ORDER BY k.id",
            (product_id,),
        )
        rows = cur.fetchall()

    out = []
    for kit_id, part_name, part_url, qty, part_desc, furn_tbl_desc, part_img, furn_local in rows:
        pu = (part_url or "").strip()
        rel = (part_img or furn_local or "").strip()
        if not rel and pu and not _is_kitline_urn(pu):
            rel = (
                furn_map.get(pu)
                or furn_map.get(pu.rstrip("/"))
                or (furn_map.get(pu.lower()) if pu.lower().startswith("http") else None)
                or ""
            )
        file_desc = ""
        if bundle_root and rel:
            file_desc = _read_furnitura_description_file(bundle_root, rel)
        merged = _merge_kit_descriptions(
            (part_desc or "").strip() if part_desc else "",
            (furn_tbl_desc or "").strip() if furn_tbl_desc else "",
            file_desc,
        )
        out.append(
            {
                "kit_id": int(kit_id) if kit_id is not None else 0,
                "part_name": part_name or "",
                "part_url": pu,
                "quantity": qty or "",
                "part_description": merged,
                "image_relpath": rel or None,
                "is_kitline_text": _is_kitline_urn(pu),
            }
        )
    if category_id is not None:
        fb = load_shower_category_image_fallbacks(
            conn,
            int(category_id),
            prefer_product_id=prefer_product_id if prefer_product_id is not None else int(product_id),
        )
        _apply_kit_item_image_fallbacks(out, bundle_root, fb)
    return out


def open_shower_connection() -> Optional[Tuple[sqlite3.Connection, str]]:
    found = find_shower_bundle()
    if not found:
        return None
    db_path, base = found
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn, base
