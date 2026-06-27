# -*- coding: utf-8 -*-
"""Несколько изделий в одном заказе (JSON blocks_calc_json): разбор, итог, сохранение."""
from __future__ import annotations

import copy
import json
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

_SCHEMA_V2 = 2
_KIND_GLASS = "glass_mirror"
_KIND_FACADE = "facade"
_PRODUCT_STATUS_DEFAULT = "draft"

ORDER_LEVEL_ROW_NAMES = frozenset({"Доставка", "Замер (выезд)", "Монтаж"})

# Оплата по позиции (значение в JSON payment_type)
PAYMENT_UNPAID = "unpaid"
PAYMENT_COD = "cod"
PAYMENT_BANK = "bank_transfer"
PAYMENT_QR = "qr"
PAYMENT_CASH = "cash"
PAYMENT_CARD = "card"

PAYMENT_TYPE_LABELS_RU = {
    PAYMENT_UNPAID: "не оплачен",
    PAYMENT_COD: "оплата при получении",
    PAYMENT_BANK: "оплата по Б/Н",
    PAYMENT_QR: "оплата по QR",
    PAYMENT_CASH: "оплата наличными",
    PAYMENT_CARD: "оплата на карту",
}

# Доплата по позиции (значение в JSON surcharge_*).
SURCHARGE_UNPAID = "unpaid"

# Схема раскроя стекла/зеркала по позиции
CUT_SCHEME_NONE = "none"
CUT_SCHEME_CREATED = "scheme_created"

# Корень bundle (schema_version >= 2): раскрой физически в mirror_cut_results у другого заказа
# (например лист «в работе» закреплён за заказом-хостом).
BUNDLE_ROOT_CUT_STORAGE_ORDER_ID = "cut_storage_order_id"

# Производство: стекло изготовлено (для фасада — наполнение; для стекло/зеркало — изделие)
PRODUCTION_GLASS_NONE = "none"
PRODUCTION_GLASS_MADE = "glass_made"


def _blocks_dir():
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(os.path.dirname(here), "BLOCKS"))


def _collect_rows(selected: Dict[str, Any]):
    """collect_line_items из BLOCKS без обязательного MainApp."""
    import sys

    bd = _blocks_dir()
    if bd not in sys.path:
        sys.path.insert(0, bd)
    from calc.order_summary import collect_line_items

    return collect_line_items(selected, None)


def parse_bundle(raw: Optional[str]) -> Tuple[int, List[Dict[str, Any]]]:
    """
    Возвращает (schema_version, products) где каждый продукт:
    { 'id': str, 'kind': str, 'payload': dict }
    """
    if not raw or not str(raw).strip():
        return 1, []
    try:
        d = json.loads(str(raw))
    except Exception:
        return 1, []
    if not isinstance(d, dict):
        return 1, []
    ver = int(d.get("schema_version") or 1)
    if ver >= _SCHEMA_V2 and isinstance(d.get("products"), list):
        out = []
        for it in d["products"]:
            if not isinstance(it, dict):
                continue
            pid = str(it.get("id") or "").strip() or str(uuid.uuid4())
            kind = str(it.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
            pl = it.get("payload")
            if isinstance(pl, dict):
                out.append(
                    {
                        "id": pid,
                        "kind": kind,
                        "status": str(it.get("status") or "").strip() or _PRODUCT_STATUS_DEFAULT,
                        "payload": pl,
                        "payment_type": str(it.get("payment_type") or PAYMENT_UNPAID).strip() or PAYMENT_UNPAID,
                        "surcharge_amount": int(it.get("surcharge_amount") or 0),
                        "surcharge_paid": bool(it.get("surcharge_paid") or False),
                        "surcharge_payment_type": str(it.get("surcharge_payment_type") or SURCHARGE_UNPAID).strip()
                        or SURCHARGE_UNPAID,
                        "cut_scheme_status": str(it.get("cut_scheme_status") or CUT_SCHEME_NONE).strip()
                        or CUT_SCHEME_NONE,
                        "production_glass_status": str(it.get("production_glass_status") or PRODUCTION_GLASS_NONE).strip()
                        or PRODUCTION_GLASS_NONE,
                    }
                )
        return ver, out
    # legacy: один сплошной payload = одно изделие
    return 1, [
        {
            "id": "legacy",
            "kind": _KIND_GLASS,
            "status": _PRODUCT_STATUS_DEFAULT,
            "payload": copy.deepcopy(d),
            "payment_type": PAYMENT_UNPAID,
            "surcharge_amount": 0,
            "surcharge_paid": False,
            "surcharge_payment_type": SURCHARGE_UNPAID,
            "cut_scheme_status": CUT_SCHEME_NONE,
            "production_glass_status": PRODUCTION_GLASS_NONE,
        }
    ]


def bundle_root_meta_preserve(raw_json: Optional[str]) -> Dict[str, Any]:
    """Сохранить при пересборке bundle корневые поля, не входящие в список products."""
    if not raw_json or not str(raw_json).strip():
        return {}
    try:
        d = json.loads(str(raw_json))
        if not isinstance(d, dict):
            return {}
        v = d.get(BUNDLE_ROOT_CUT_STORAGE_ORDER_ID)
        if v is None:
            return {}
        try:
            return {BUNDLE_ROOT_CUT_STORAGE_ORDER_ID: int(v)}
        except (TypeError, ValueError):
            return {}
    except Exception:
        return {}


def set_bundle_cut_storage_order_id(raw_json: Optional[str], host_order_id: Optional[int]) -> str:
    """Указать заказ-хост для строк mirror_cut_results или снять привязку (host_order_id is None)."""
    ver, products = parse_bundle(raw_json)
    meta = bundle_root_meta_preserve(raw_json)
    if host_order_id is None:
        meta.pop(BUNDLE_ROOT_CUT_STORAGE_ORDER_ID, None)
    else:
        meta[BUNDLE_ROOT_CUT_STORAGE_ORDER_ID] = int(host_order_id)
    return bundle_to_json(max(ver, _SCHEMA_V2), products, root_meta=meta or None)


def bundle_to_json(
    schema_version: int,
    products: List[Dict[str, Any]],
    *,
    root_meta: Optional[Dict[str, Any]] = None,
) -> str:
    if schema_version >= _SCHEMA_V2:
        body: Dict[str, Any] = {"schema_version": _SCHEMA_V2, "products": products}
        if root_meta:
            k = BUNDLE_ROOT_CUT_STORAGE_ORDER_ID
            if k in root_meta and root_meta[k] is not None:
                try:
                    body[k] = int(root_meta[k])
                except (TypeError, ValueError):
                    pass
    else:
        if len(products) == 1:
            body = copy.deepcopy(products[0]["payload"])
        else:
            body = {"schema_version": _SCHEMA_V2, "products": products}
    return json.dumps(body, ensure_ascii=False, indent=2)


def product_sum_excluding_order_level(payload: Dict[str, Any]) -> int:
    rows, _, _ = _collect_rows(payload)
    return sum(r[1] for r in rows if r[0] not in ORDER_LEVEL_ROW_NAMES)


def order_level_amounts_once(payloads: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    """Первые ненулевые Доставка, Замер (выезд) и Монтаж по изделиям — один раз на заказ."""
    dlv, zam, mon = 0, 0, 0
    for sel in payloads:
        rows, _, _ = _collect_rows(sel)
        for name, rub, _det in rows:
            if name == "Доставка" and rub and not dlv:
                dlv = int(rub)
            if name == "Замер (выезд)" and rub and not zam:
                zam = int(rub)
            if name == "Монтаж" and rub and not mon:
                mon = int(rub)
        if dlv and zam and mon:
            break
    return dlv, zam, mon


def bundle_grand_total_rub(products: List[Dict[str, Any]]) -> int:
    glass_payloads: List[Dict[str, Any]] = []
    extra = 0
    for p in products:
        pl = p.get("payload")
        if not isinstance(pl, dict):
            continue
        k = str(p.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
        if k == _KIND_GLASS:
            glass_payloads.append(pl)
            continue
        for key in ("_total_rub", "total_rub", "Итого_руб"):
            if key in pl:
                try:
                    extra += int(pl.get(key) or 0)
                except (TypeError, ValueError):
                    pass
                break
    if not glass_payloads and extra == 0:
        return 0
    sub = sum(product_sum_excluding_order_level(p) for p in glass_payloads)
    dlv, zam, mon = order_level_amounts_once(glass_payloads)
    return sub + dlv + zam + mon + extra


def bundle_product_headline(products: List[Dict[str, Any]]) -> str:
    n = len(products)
    if n <= 0:
        return "Нет изделий"
    if n == 1:
        return "1 изделие"
    return "%s изделия" % n


def strip_order_level_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Копия payload без доставки/замера на уровне заказа (для 2+ изделия при сохранении)."""
    p = copy.deepcopy(payload)
    if "Доставка" in p and isinstance(p["Доставка"], dict):
        p["Доставка"] = {"Активирован": False, "Данные": None}
    if "Замер" in p and isinstance(p["Замер"], dict):
        p["Замер"] = {"Активирован": False, "Данные": {}}
    return p


def merge_payload_into_bundle(
    raw_json: Optional[str],
    product_id: Optional[str],
    new_payload: Dict[str, Any],
    append_new: bool,
    payload_kind: Optional[str] = None,
) -> str:
    """Обновить или добавить изделие; доставку/замер со 2-го изделия обнуляем (один раз на заказ)."""
    ver, products = parse_bundle(raw_json)
    ver = max(ver, _SCHEMA_V2)

    kind_for_new = (payload_kind or "").strip() or _KIND_GLASS

    new_pl = copy.deepcopy(new_payload)
    if append_new:
        if len(products) > 0:
            new_pl = strip_order_level_from_payload(new_pl)
        pid = str(uuid.uuid4())
        products.append(
            {
                "id": pid,
                "kind": kind_for_new,
                "status": _PRODUCT_STATUS_DEFAULT,
                "payload": new_pl,
                "payment_type": PAYMENT_UNPAID,
                "surcharge_amount": 0,
                "surcharge_paid": False,
                "surcharge_payment_type": SURCHARGE_UNPAID,
                "cut_scheme_status": CUT_SCHEME_NONE,
                "production_glass_status": PRODUCTION_GLASS_NONE,
            }
        )
    else:
        pid = (str(product_id).strip() if product_id else "") or ""
        found = False
        if pid:
            for i, pr in enumerate(products):
                if str(pr.get("id") or "") == pid:
                    keep_kind = str(pr.get("kind") or "").strip() or kind_for_new
                    products[i] = {
                        "id": pid,
                        "kind": keep_kind,
                    "status": str(pr.get("status") or "").strip() or _PRODUCT_STATUS_DEFAULT,
                        "payload": new_pl,
                        "payment_type": str(pr.get("payment_type") or PAYMENT_UNPAID).strip() or PAYMENT_UNPAID,
                        "surcharge_amount": int(pr.get("surcharge_amount") or 0),
                        "surcharge_paid": bool(pr.get("surcharge_paid") or False),
                        "surcharge_payment_type": str(pr.get("surcharge_payment_type") or SURCHARGE_UNPAID).strip()
                        or SURCHARGE_UNPAID,
                        "cut_scheme_status": str(pr.get("cut_scheme_status") or CUT_SCHEME_NONE).strip()
                        or CUT_SCHEME_NONE,
                        "production_glass_status": str(pr.get("production_glass_status") or PRODUCTION_GLASS_NONE).strip()
                        or PRODUCTION_GLASS_NONE,
                    }
                    found = True
                    break
        elif products:
            pid0 = str(products[0].get("id") or uuid.uuid4())
            pr0 = products[0]
            products[0] = {
                "id": pid0,
                "kind": pr0.get("kind") or _KIND_GLASS,
                "status": str(pr0.get("status") or "").strip() or _PRODUCT_STATUS_DEFAULT,
                "payload": new_pl,
                "payment_type": str(pr0.get("payment_type") or PAYMENT_UNPAID).strip() or PAYMENT_UNPAID,
                "surcharge_amount": int(pr0.get("surcharge_amount") or 0),
                "surcharge_paid": bool(pr0.get("surcharge_paid") or False),
                "surcharge_payment_type": str(pr0.get("surcharge_payment_type") or SURCHARGE_UNPAID).strip()
                or SURCHARGE_UNPAID,
                "cut_scheme_status": str(pr0.get("cut_scheme_status") or CUT_SCHEME_NONE).strip() or CUT_SCHEME_NONE,
                "production_glass_status": str(pr0.get("production_glass_status") or PRODUCTION_GLASS_NONE).strip()
                or PRODUCTION_GLASS_NONE,
            }
            found = True
        if not found:
            pid_new = pid or str(uuid.uuid4())
            products.append(
                {
                    "id": pid_new,
                    "kind": kind_for_new,
                    "status": _PRODUCT_STATUS_DEFAULT,
                    "payload": new_pl,
                    "payment_type": PAYMENT_UNPAID,
                    "surcharge_amount": 0,
                    "surcharge_paid": False,
                    "surcharge_payment_type": SURCHARGE_UNPAID,
                    "cut_scheme_status": CUT_SCHEME_NONE,
                    "production_glass_status": PRODUCTION_GLASS_NONE,
                }
            )

    return bundle_to_json(_SCHEMA_V2, products, root_meta=bundle_root_meta_preserve(raw_json) or None)


def infer_order_kind_for_db(products: List[Dict[str, Any]]) -> str:
    """Одно поле mirror_orders.order_kind по составу изделий (значения как в db.models)."""
    if not products:
        return "glass_mirror"
    kinds = set()
    for p in products:
        k = str(p.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
        kinds.add(k)
    if len(kinds) > 1:
        return "mixed"
    only = list(kinds)[0]
    if only == _KIND_GLASS:
        return "glass_mirror"
    if only == _KIND_FACADE:
        return "facade"
    return "mixed"


def remove_product_from_bundle(raw_json: Optional[str], product_id: str) -> str:
    ver, products = parse_bundle(raw_json)
    products = [p for p in products if p.get("id") != product_id]
    return bundle_to_json(max(ver, _SCHEMA_V2), products, root_meta=bundle_root_meta_preserve(raw_json) or None)


def apply_order_status_to_products(raw_json: Optional[str], order_status: str) -> str:
    """Синхронизировать статус заказа во все изделия bundle (оплата/раскрой/производство стекла не трогаем)."""
    ver, products = parse_bundle(raw_json)
    st = str(order_status or "").strip()
    for p in products:
        p["status"] = st
        p.setdefault("payment_type", PAYMENT_UNPAID)
        p.setdefault("surcharge_amount", 0)
        p.setdefault("surcharge_paid", False)
        p.setdefault("surcharge_payment_type", SURCHARGE_UNPAID)
        p.setdefault("cut_scheme_status", CUT_SCHEME_NONE)
        p.setdefault("production_glass_status", PRODUCTION_GLASS_NONE)
    return bundle_to_json(max(ver, _SCHEMA_V2), products, root_meta=bundle_root_meta_preserve(raw_json) or None)


def product_is_paid(product: Dict[str, Any]) -> bool:
    return str(product.get("payment_type") or PAYMENT_UNPAID).strip() != PAYMENT_UNPAID


def bundle_payment_aggregate(products: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Сводка оплаты по заказу: state unpaid | partial | full, paid/total counts,
    lines_ru — строки «сумма + способ» по убыванию суммы (только при full).
    """
    total = len(products)
    paid = [p for p in products if product_is_paid(p)]
    n_paid = len(paid)
    if total <= 0:
        return {"state": "unpaid", "paid_count": 0, "total_count": 0, "lines_ru": []}
    if n_paid == 0:
        return {"state": "unpaid", "paid_count": 0, "total_count": total, "lines_ru": []}
    if n_paid < total:
        return {"state": "partial", "paid_count": n_paid, "total_count": total, "lines_ru": []}
    by_method: Dict[str, int] = {}
    for p in paid:
        m = str(p.get("payment_type") or PAYMENT_UNPAID).strip()
        pl = p.get("payload") if isinstance(p.get("payload"), dict) else {}
        k = str(p.get("kind") or _KIND_GLASS).strip() or _KIND_GLASS
        if k == _KIND_GLASS:
            amt = product_sum_excluding_order_level(pl)
        else:
            amt = 0
            for key in ("_total_rub", "total_rub", "Итого_руб"):
                if key in pl:
                    try:
                        amt = int(pl.get(key) or 0)
                    except (TypeError, ValueError):
                        amt = 0
                    break
            if not amt:
                try:
                    amt = int(product_sum_excluding_order_level(pl))
                except (TypeError, ValueError):
                    amt = 0
        by_method[m] = by_method.get(m, 0) + int(amt)
    lines = []
    for m, s in sorted(by_method.items(), key=lambda x: -x[1]):
        lab = PAYMENT_TYPE_LABELS_RU.get(m, m)
        lines.append("%s — %s" % (_format_rub(s), lab))
    return {"state": "full", "paid_count": n_paid, "total_count": total, "lines_ru": lines}


def bundle_surcharge_aggregate(products: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Сводка доплат: сумма, оплачено/всего (по позициям с суммой), и разбивка по способам."""
    total_amount = 0
    paid_amount = 0
    positions_total = 0
    positions_paid = 0
    by_method: Dict[str, int] = {}
    for p in products or []:
        try:
            amt = int(p.get("surcharge_amount") or 0)
        except (TypeError, ValueError):
            amt = 0
        if amt <= 0:
            continue
        positions_total += 1
        total_amount += amt
        if bool(p.get("surcharge_paid") or False):
            positions_paid += 1
            paid_amount += amt
            m = str(p.get("surcharge_payment_type") or SURCHARGE_UNPAID).strip() or SURCHARGE_UNPAID
            by_method[m] = by_method.get(m, 0) + amt
    lines = []
    for m, s in sorted(by_method.items(), key=lambda x: -x[1]):
        lines.append("%s — %s" % (_format_rub(s), PAYMENT_TYPE_LABELS_RU.get(m, m)))
    return {
        "total_amount": int(total_amount),
        "paid_amount": int(paid_amount),
        "positions_paid": int(positions_paid),
        "positions_total": int(positions_total),
        "lines_ru": lines,
    }


def _format_rub(n: int) -> str:
    try:
        v = int(n)
    except (TypeError, ValueError):
        v = 0
    return ("%d" % v).replace(",", " ")


def bundle_cut_scheme_counts(products: List[Dict[str, Any]], db_models: Any) -> Tuple[int, int]:
    """Число изделий со схемой раскроя / число изделий со стеклом, подлежащим раскрою."""
    if not products or not db_models:
        return 0, 0
    from mirror_cut_prefill import order_bundle_has_cuttable_glass

    def _product_qty(p: Dict[str, Any]) -> int:
        pl = p.get("payload") if isinstance(p.get("payload"), dict) else {}
        if str(p.get("kind") or "").strip() == "facade":
            try:
                return max(1, int(pl.get("Количество") or 1))
            except (TypeError, ValueError):
                return 1
        izd = pl.get("Параметры изделия") if isinstance(pl.get("Параметры изделия"), dict) else {}
        if not izd:
            bs = pl.get("blocks_selected") if isinstance(pl.get("blocks_selected"), dict) else {}
            izd = bs.get("Изделие") if isinstance(bs.get("Изделие"), dict) else {}
            if not izd and isinstance(bs.get("Материал"), dict):
                izd = bs.get("Материал")
        try:
            return max(1, int((izd or {}).get("Количество (шт)") or 1))
        except (TypeError, ValueError):
            return 1

    y = 0
    x = 0
    for p in products:
        if not order_bundle_has_cuttable_glass([p], db_models):
            continue
        q = _product_qty(p)
        y += q
        if str(p.get("cut_scheme_status") or CUT_SCHEME_NONE).strip() == CUT_SCHEME_CREATED:
            x += q
    return x, y


def set_products_cut_scheme_status(raw_json: Optional[str], product_ids: List[str], status: str) -> str:
    ver, products = parse_bundle(raw_json)
    ids = {str(i).strip() for i in product_ids if str(i).strip()}
    st = str(status or CUT_SCHEME_NONE).strip() or CUT_SCHEME_NONE
    for p in products:
        if str(p.get("id") or "") in ids:
            p["cut_scheme_status"] = st
            p.setdefault("payment_type", PAYMENT_UNPAID)
            p.setdefault("production_glass_status", PRODUCTION_GLASS_NONE)
    return bundle_to_json(max(ver, _SCHEMA_V2), products, root_meta=bundle_root_meta_preserve(raw_json) or None)


def set_products_status(raw_json: Optional[str], product_ids: List[str], status: str) -> str:
    """Поле status у изделий bundle (например paid / in_progress после вывода в производство)."""
    ver, products = parse_bundle(raw_json)
    ids = {str(i).strip() for i in product_ids if str(i).strip()}
    st = str(status or "").strip() or _PRODUCT_STATUS_DEFAULT
    for p in products:
        if str(p.get("id") or "") in ids:
            p["status"] = st
            p.setdefault("payment_type", PAYMENT_UNPAID)
            p.setdefault("surcharge_amount", 0)
            p.setdefault("surcharge_paid", False)
            p.setdefault("surcharge_payment_type", SURCHARGE_UNPAID)
            p.setdefault("cut_scheme_status", CUT_SCHEME_NONE)
            p.setdefault("production_glass_status", PRODUCTION_GLASS_NONE)
    return bundle_to_json(max(ver, _SCHEMA_V2), products, root_meta=bundle_root_meta_preserve(raw_json) or None)


def set_product_payment_type(raw_json: Optional[str], product_id: str, payment_type: str) -> str:
    ver, products = parse_bundle(raw_json)
    pid = str(product_id or "").strip()
    pt = str(payment_type or PAYMENT_UNPAID).strip() or PAYMENT_UNPAID
    for p in products:
        if str(p.get("id") or "") == pid:
            p["payment_type"] = pt
            p.setdefault("surcharge_amount", 0)
            p.setdefault("surcharge_paid", False)
            p.setdefault("surcharge_payment_type", SURCHARGE_UNPAID)
            p.setdefault("cut_scheme_status", CUT_SCHEME_NONE)
            p.setdefault("production_glass_status", PRODUCTION_GLASS_NONE)
            break
    return bundle_to_json(max(ver, _SCHEMA_V2), products, root_meta=bundle_root_meta_preserve(raw_json) or None)


def set_product_surcharge(
    raw_json: Optional[str],
    product_id: str,
    surcharge_amount: int,
    surcharge_paid: bool,
    surcharge_payment_type: str,
) -> str:
    ver, products = parse_bundle(raw_json)
    pid = str(product_id or "").strip()
    try:
        amt = int(surcharge_amount or 0)
    except (TypeError, ValueError):
        amt = 0
    amt = max(0, amt)
    pt = str(surcharge_payment_type or SURCHARGE_UNPAID).strip() or SURCHARGE_UNPAID
    for p in products:
        if str(p.get("id") or "") == pid:
            p["surcharge_amount"] = amt
            p["surcharge_paid"] = bool(surcharge_paid and amt > 0)
            p["surcharge_payment_type"] = pt if amt > 0 else SURCHARGE_UNPAID
            p.setdefault("payment_type", PAYMENT_UNPAID)
            p.setdefault("cut_scheme_status", CUT_SCHEME_NONE)
            p.setdefault("production_glass_status", PRODUCTION_GLASS_NONE)
            break
    return bundle_to_json(max(ver, _SCHEMA_V2), products, root_meta=bundle_root_meta_preserve(raw_json) or None)


def set_product_production_glass_status(raw_json: Optional[str], product_index_1based: int, status: str) -> str:
    """product_index_1based как в API производства."""
    ver, products = parse_bundle(raw_json)
    i = int(product_index_1based) - 1
    if 0 <= i < len(products):
        products[i]["production_glass_status"] = str(status or PRODUCTION_GLASS_NONE).strip() or PRODUCTION_GLASS_NONE
        products[i].setdefault("payment_type", PAYMENT_UNPAID)
        products[i].setdefault("surcharge_amount", 0)
        products[i].setdefault("surcharge_paid", False)
        products[i].setdefault("surcharge_payment_type", SURCHARGE_UNPAID)
        products[i].setdefault("cut_scheme_status", CUT_SCHEME_NONE)
    return bundle_to_json(max(ver, _SCHEMA_V2), products, root_meta=bundle_root_meta_preserve(raw_json) or None)


def clear_cut_scheme_on_all_products(raw_json: Optional[str]) -> str:
    ver, products = parse_bundle(raw_json)
    for p in products:
        p["cut_scheme_status"] = CUT_SCHEME_NONE
    meta = bundle_root_meta_preserve(raw_json)
    meta.pop(BUNDLE_ROOT_CUT_STORAGE_ORDER_ID, None)
    return bundle_to_json(max(ver, _SCHEMA_V2), products, root_meta=meta or None)


def revert_products_cut_state_paid_preserve_payment(raw_json: Optional[str], product_ids: List[str]) -> str:
    """
    Сброс схемы раскроя и production_glass; status изделия = paid.
    Способ оплаты по позиции, доплаты и прочие поля не меняются.
    """
    ver, products = parse_bundle(raw_json)
    ids = {str(i).strip() for i in (product_ids or []) if str(i).strip()}
    for p in products:
        if str(p.get("id") or "") not in ids:
            continue
        p["status"] = "paid"
        p["cut_scheme_status"] = CUT_SCHEME_NONE
        p["production_glass_status"] = PRODUCTION_GLASS_NONE
    return bundle_to_json(max(ver, _SCHEMA_V2), products, root_meta=bundle_root_meta_preserve(raw_json) or None)


def first_product_payload_json(raw_json: Optional[str]) -> Optional[str]:
    """Строка JSON одного изделия для MainApp (legacy или первый продукт)."""
    _ver, products = parse_bundle(raw_json)
    if not products:
        return None
    try:
        return json.dumps(products[0]["payload"], ensure_ascii=False, indent=2)
    except Exception:
        return None


def payload_for_product_id(raw_json: Optional[str], product_id: str) -> Optional[Dict[str, Any]]:
    _ver, products = parse_bundle(raw_json)
    for pr in products:
        if pr.get("id") == product_id:
            pl = pr.get("payload")
            return copy.deepcopy(pl) if isinstance(pl, dict) else None
    return None


# Статусы «изделие снято с производства» — как квадратики в списке заказов (все зелёные → заказ «Изготовлен»).
_ORDER_UNIT_TERMINAL_STATUSES = frozenset({"made", "shipped", "completed"})


def bundle_product_units_qty(pr: Optional[Dict[str, Any]]) -> int:
    """Количество штук по позиции bundle (как в колонке статуса заказа)."""
    pr = pr or {}
    pl = pr.get("payload") if isinstance(pr.get("payload"), dict) else {}
    if str(pr.get("kind") or "").strip() == "facade":
        try:
            return max(1, int(pl.get("Количество") or 1))
        except (TypeError, ValueError):
            return 1
    izd = pl.get("Параметры изделия") if isinstance(pl.get("Параметры изделия"), dict) else {}
    if not izd:
        bs = pl.get("blocks_selected") if isinstance(pl.get("blocks_selected"), dict) else {}
        izd = bs.get("Изделие") if isinstance(bs.get("Изделие"), dict) else {}
        if not izd and isinstance(bs.get("Материал"), dict):
            izd = bs.get("Материал")
    try:
        return max(1, int((izd or {}).get("Количество (шт)") or 1))
    except (TypeError, ValueError):
        return 1


def bundle_order_units_total_qty(products: Optional[List[Dict[str, Any]]]) -> int:
    if not products:
        return 0
    return int(sum(bundle_product_units_qty(p) for p in products))


def bundle_status_unit_counts(
    products: List[Dict[str, Any]],
    *,
    order_fallback_status: str,
    order_id: Optional[int] = None,
    facade_production_events: Optional[List[Any]] = None,
    count_facade_assembled: Optional[Callable[[int, int, Optional[List[Any]]], int]] = None,
) -> Tuple[Dict[str, int], int]:
    """
    Раскладка единиц изделий по статусам (как квадратики в таблице заказов).
    count_facade_assembled(oid, instance_1based, events_or_none) — из db.models при наличии фасадов.
    """
    counts: Dict[str, int] = {}
    total = 0
    fallback = str(order_fallback_status or "draft").strip() or "draft"
    oid = order_id
    for idx, pr in enumerate(products or []):
        pst = str((pr or {}).get("status") or fallback).strip() or fallback
        q = bundle_product_units_qty(pr or {})
        kind = str((pr or {}).get("kind") or "").strip()
        pgs = str((pr or {}).get("production_glass_status") or "").strip().lower()
        if kind == "facade" and oid is not None and count_facade_assembled is not None:
            try:
                done_n = int(count_facade_assembled(int(oid), int(idx) + 1, facade_production_events))
            except Exception:
                done_n = 0
            done_n = max(0, min(done_n, int(q)))
            if int(q) > 1:
                idle_st = (
                    pst
                    if pst not in ("made", "shipped", "completed")
                    else "in_progress"
                )
                for _j in range(int(q)):
                    st = "made" if _j < done_n else idle_st
                    counts[st] = int(counts.get(st) or 0) + 1
                    total += 1
                continue
            st = (
                "made"
                if (
                    done_n >= 1
                    or pst in ("made", "shipped", "completed")
                )
                else pst
            )
            counts[st] = int(counts.get(st) or 0) + 1
            total += 1
            continue
        if kind != "facade" and pgs == PRODUCTION_GLASS_MADE:
            pst_l = (pst or "").strip().lower()
            if pst_l in ("shipped", "completed", "cancelled"):
                counts[pst_l] = int(counts.get(pst_l) or 0) + int(q)
            else:
                counts["made"] = int(counts.get("made") or 0) + int(q)
            total += int(q)
            continue
        counts[pst] = int(counts.get(pst) or 0) + int(q)
        total += int(q)
    return counts, total


def bundle_all_units_in_terminal_order_statuses(
    products: List[Dict[str, Any]],
    *,
    order_fallback_status: str,
    order_id: Optional[int] = None,
    facade_production_events: Optional[List[Any]] = None,
    count_facade_assembled: Optional[Callable[[int, int, Optional[List[Any]]], int]] = None,
) -> bool:
    counts, tot = bundle_status_unit_counts(
        products,
        order_fallback_status=order_fallback_status,
        order_id=order_id,
        facade_production_events=facade_production_events,
        count_facade_assembled=count_facade_assembled,
    )
    if tot <= 0:
        return False
    return sum(int(counts.get(s) or 0) for s in _ORDER_UNIT_TERMINAL_STATUSES) == tot
