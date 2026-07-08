# -*- coding: utf-8 -*-
"""Пути и имена PDF: заказы/просчёты × категории изделий."""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, Optional

from cfg_loader import app_cfg, get_base_dir, get_cfg_string
from logic.blocks_bundle import infer_order_kind_for_db, parse_bundle


DOC_SUMMARY = "Сводка"
DOC_ESTIMATE = "Смета"
DOC_CUT = "Раскрой"
DOC_WORKER = "Задание_цех"
DOC_LABELS = "Этикетки"
DOC_FACADES = "Фасады"
DOC_BLOCKS = "Просчет"


def pdf_output_root() -> str:
    cfg = app_cfg()
    folder = ""
    if cfg:
        folder = get_cfg_string(cfg, "paths", "pdf_output_dir", "") or ""
        if not folder:
            folder = get_cfg_string(cfg, "paths", "cutting_pdf_dir", "") or ""
    if not folder:
        folder = get_base_dir()
    return folder


def _sanitize_filename_part(text: str, max_len: int = 48) -> str:
    s = (text or "").strip()
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s).strip("._")
    if not s:
        return "без_имени"
    if len(s) > max_len:
        s = s[:max_len].rstrip("._")
    return s


def category_folder_name(order_row: Dict[str, Any]) -> str:
    raw = order_row.get("blocks_calc_json")
    _, products = parse_bundle(raw if raw else None)
    kind = infer_order_kind_for_db(products)
    if kind == "facade":
        return "фасады"
    if kind == "mixed":
        return "смешанный"
    if str(order_row.get("order_kind") or "").strip().lower() == "sales":
        return "продажи"
    return "стекло_зеркало"


def is_quick_estimate_order(order_row: Dict[str, Any]) -> bool:
    oid = order_row.get("id")
    if oid is None:
        return False
    try:
        from db import models as db_models

        qids = set(db_models.sales_order_ids_in_draft_quick_estimates() or [])
        if int(oid) in qids:
            return True
    except Exception:
        pass
    notes = str(order_row.get("notes") or "")
    if "quick_estimate" in notes.lower():
        return True
    return str(order_row.get("status") or "").strip().lower() == "draft" and not (order_row.get("blocks_calc_json") or "").strip()


def order_doc_bucket(order_row: Dict[str, Any]) -> str:
    return "просчёты" if is_quick_estimate_order(order_row) else "заказы"


def order_number_part(order_row: Dict[str, Any]) -> str:
    kn = (order_row.get("k_number") or "").strip()
    if kn:
        return _sanitize_filename_part(kn, 24)
    oid = order_row.get("id")
    if oid is not None:
        return str(int(oid))
    return "0"


def client_name_part(order_row: Dict[str, Any]) -> str:
    name = (order_row.get("client_name") or "").strip()
    try:
        cid = order_row.get("client_id")
        if cid:
            from db import models as db_models

            crow = db_models.get_client_by_id(int(cid)) or {}
            nick = (crow.get("nickname") or "").strip()
            if nick:
                name = nick
    except Exception:
        pass
    return _sanitize_filename_part(name or "клиент", 40)


def order_date_part(order_row: Dict[str, Any]) -> str:
    ca = order_row.get("created_at")
    if ca is None:
        return datetime.now().strftime("%Y-%m-%d")
    if hasattr(ca, "strftime"):
        return ca.strftime("%Y-%m-%d")
    s = str(ca).strip()
    if len(s) >= 10:
        return s[:10]
    return datetime.now().strftime("%Y-%m-%d")


def build_pdf_filename(order_row: Dict[str, Any], doc_kind: str, *, suffix: str = "") -> str:
    bucket_ru = "просчет" if order_doc_bucket(order_row) == "просчёты" else "заказ"
    parts = [
        bucket_ru,
        order_date_part(order_row),
        client_name_part(order_row),
        order_number_part(order_row),
        _sanitize_filename_part(doc_kind, 32),
    ]
    if suffix:
        parts.append(_sanitize_filename_part(suffix, 16))
    return "_".join(parts) + ".pdf"


def resolve_pdf_path(
    order_row: Dict[str, Any],
    doc_kind: str,
    *,
    suffix: str = "",
    category: Optional[str] = None,
) -> str:
    root = pdf_output_root()
    bucket = order_doc_bucket(order_row)
    cat = category or category_folder_name(order_row)
    folder = os.path.join(root, bucket, cat)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        pass
    fname = build_pdf_filename(order_row, doc_kind, suffix=suffix)
    return os.path.join(folder, fname)


def resolve_pdf_path_for_blocks_export(
    client_name: str,
    *,
    is_estimate: bool = True,
    doc_kind: str = DOC_BLOCKS,
) -> str:
    """Локальный экспорт из калькулятора до сохранения заказа."""
    root = pdf_output_root()
    bucket = "просчёты" if is_estimate else "заказы"
    folder = os.path.join(root, bucket, "стекло_зеркало")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        pass
    parts = [
        "просчет" if is_estimate else "заказ",
        datetime.now().strftime("%Y-%m-%d"),
        _sanitize_filename_part(client_name or "клиент", 40),
        "новый",
        _sanitize_filename_part(doc_kind, 32),
    ]
    return os.path.join(folder, "_".join(parts) + ".pdf")
