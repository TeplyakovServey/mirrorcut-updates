# -*- coding: utf-8 -*-
"""Экспорт файлов .G для стола PRAKTIK / Optima (GALIL) из layout раскроя MIRROR_CUT."""
from __future__ import annotations

import io
import os
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple

_EDGE_MARGIN = 10
_SHEET_EDGE = 2
_P400_PAD = 32


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def g_files_zip_filename(order_id: Any) -> str:
    """Имя ZIP-архива: номер заказа в БД (orders.id), например 17.zip."""
    try:
        return "%d.zip" % int(order_id)
    except (TypeError, ValueError):
        return "0.zip"


def _resolve_thickness_mm(layout: Dict[str, Any]) -> int:
    """Толщина листа (мм) из сохранённого раскроя — для P3011, P3007 и папки материала."""
    th = _safe_int(layout.get("thickness_mm"), 0)
    if th > 0:
        return th
    for key in ("sheet_thickness_mm", "thickness"):
        th = _safe_int(layout.get(key), 0)
        if th > 0:
            return th
    for p in layout.get("pieces") or []:
        if not isinstance(p, dict):
            continue
        th = _safe_int(p.get("thickness_mm"), 0)
        if th > 0:
            return th
    return 4


def order_z_number(order_info: Dict[str, Any] | None) -> str:
    """Префикс Z2342 — номер заказа без ведущих нулей."""
    info = order_info or {}
    kn = info.get("k_number")
    if kn is not None and str(kn).strip() != "":
        try:
            return str(int(kn))
        except (TypeError, ValueError):
            pass
    oid = info.get("order_id") or info.get("id")
    try:
        return str(int(oid))
    except (TypeError, ValueError):
        return "0"


def _material_folder_name(material: str, thickness_mm: Any) -> str:
    mat = (material or "материал").strip() or "материал"
    th = _safe_int(thickness_mm, 0)
    th_s = str(th) if th > 0 else "?"
    safe = re.sub(r'[<>:"/\\|?*]', "_", mat).strip() or "материал"
    return "%s_%s" % (safe, th_s)


def g_file_basename(
    order_info: Dict[str, Any] | None,
    sheet_index: int,
    sheet_width: int,
    sheet_height: int,
    batch_seq: int = 1,
) -> str:
    z = order_z_number(order_info)
    return "Z%s_%d_%d_%d_%d.G" % (
        z,
        int(sheet_index),
        int(batch_seq),
        int(sheet_width),
        int(sheet_height),
    )


def _piece_p400_y(piece: Dict[str, Any]) -> int:
    """В P400x координата Y — как в layout (от верхнего края листа)."""
    return _safe_int(piece.get("y"), 0)


def _encode_piece_label(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        return raw.encode("cp1251", errors="replace").decode("cp1251")
    except Exception:
        return raw


def _format_p4001(piece: Dict[str, Any], sheet_height: int) -> str:
    x = _safe_int(piece.get("x"), 0)
    y = _piece_p400_y(piece)
    w = _safe_int(piece.get("w"), 0)
    h = _safe_int(piece.get("h"), 0)
    qty = max(1, _safe_int(piece.get("qty") or piece.get("quantity"), 1))
    label = _encode_piece_label(
        str(piece.get("recipient") or piece.get("label") or piece.get("product_label") or "")
    )
    core = "%d_%d_%d_%d_%d_%d" % (x, y, w, h, w, h)
    if label:
        tail = "_%s_%d" % (label, qty)
    else:
        tail = "__%d" % qty
    pad = max(0, _P400_PAD - len(tail))
    return core + tail + ("_" * pad)


def _ensure_cut_segments(layout: Dict[str, Any]) -> None:
    if layout.get("cut_segments"):
        return
    try:
        from logic.cutting_algorithm import assign_chocolate_bar_cut_segments_to_layout

        assign_chocolate_bar_cut_segments_to_layout(dict(layout))
    except Exception:
        pass


def _ordered_segments(layout: Dict[str, Any]) -> List[Dict[str, Any]]:
    _ensure_cut_segments(layout)
    segments = [s for s in (layout.get("cut_segments") or []) if isinstance(s, dict)]
    sw = _safe_int(layout.get("sheet_width"), 0)
    sh = _safe_int(layout.get("sheet_height"), 0)
    if not segments or sw <= 0 or sh <= 0:
        return segments
    try:
        from logic.cut_sequence import reorder_cut_segments_for_guillotine_simulation

        return list(reorder_cut_segments_for_guillotine_simulation(segments, sw, sh) or segments)
    except Exception:
        return segments


def _segment_to_cut_ops(seg: Dict[str, Any], sw: int, sh: int) -> Optional[Tuple[str, float, float, float]]:
    t = str(seg.get("type") or "H").strip().upper()
    pos = float(seg.get("pos", 0))
    lo = float(seg.get("extent_lo", 0))
    hi = float(seg.get("extent_hi", sw if t == "H" else sh))
    margin = float(_EDGE_MARGIN)
    if t == "H":
        gy = float(sh) - pos
        if lo <= margin + 0.01 and hi >= float(sw) - margin - 0.01:
            return ("H", gy, float(_SHEET_EDGE), float(sw - _SHEET_EDGE))
        return ("H", gy, lo + 0.5, hi - 0.5)
    gx = pos
    glo = float(sh) - hi
    ghi = float(sh) - lo
    if glo <= margin + 0.01 and ghi >= float(sh) - margin - 0.01:
        return ("V", gx, float(_SHEET_EDGE), float(sh - _SHEET_EDGE))
    return ("V", gx, glo + 0.5, ghi - 0.5)


def _gcode_lines_from_layout(layout: Dict[str, Any]) -> List[str]:
    sw = _safe_int(layout.get("sheet_width"), 0)
    sh = _safe_int(layout.get("sheet_height"), 0)
    if sw <= 0 or sh <= 0:
        return ["G17", "G92 X0 Y0", "G90", "M04", "M30"]
    ops: List[Tuple[str, float, float, float]] = []
    seen = set()
    for seg in _ordered_segments(layout):
        op = _segment_to_cut_ops(seg, sw, sh)
        if not op:
            continue
        key = tuple(round(v, 3) if isinstance(v, float) else v for v in op)
        if key in seen:
            continue
        seen.add(key)
        ops.append(op)
    h_ops = sorted([o for o in ops if o[0] == "H"], key=lambda t: (t[1], t[2]))
    v_ops = sorted([o for o in ops if o[0] == "V"], key=lambda t: (t[2], t[1]))
    lines = ["G17", "G92 X0 Y0", "G90"]
    first_h = True
    for _t, gy, x0, x1 in h_ops:
        if first_h:
            lines.append("G00 X%.0f Y%.0f" % (x0, gy))
            lines.append("M03")
            first_h = False
        else:
            lines.append("G00 X%.1f Y%.0f" % (x0, gy))
        lines.append("M07")
        if abs(x0 - round(x0)) < 0.01 and abs(x1 - round(x1)) < 0.01:
            lines.append("G01 X%.0f Y%.0f" % (x1, gy))
        else:
            lines.append("G01 X%.1f Y%.0f" % (x1, gy))
        lines.append("M08")
    for _t, gx, y0, y1 in v_ops:
        if abs(y0 - round(y0)) < 0.01:
            lines.append("G00 X%.0f Y%.0f" % (gx, y0))
        else:
            lines.append("G00 X%.0f Y%.1f" % (gx, y0))
        lines.append("M07")
        if abs(y1 - round(y1)) < 0.01:
            lines.append("G01 X%.0f Y%.0f" % (gx, y1))
        else:
            lines.append("G01 X%.0f Y%.1f" % (gx, y1))
        lines.append("M08")
    lines.extend(["M04", "G90G00X10Y10Z0", "M23", "M24", "M30"])
    return lines


def layout_to_g_content(
    layout: Dict[str, Any],
    order_info: Dict[str, Any] | None = None,
    *,
    sheet_index: int = 1,
    batch_seq: int = 1,
) -> str:
    sw = _safe_int(layout.get("sheet_width"), 0)
    sh = _safe_int(layout.get("sheet_height"), 0)
    th = _resolve_thickness_mm(layout)
    pieces = [p for p in (layout.get("pieces") or []) if isinstance(p, dict)]
    p3009 = len(pieces) if pieces else 1
    header = [
        "N01 P3000=%d" % sw,
        "N02 P3001=%d" % sh,
        "N03 P3002=0",
        "N04 P3003=0",
        "N05 P3004=0",
        "N06 P3005=0",
        "N07 P3006=1",
        "N08 P3007=%d*%d*%d" % (sh, sw, th),
        "N09 P3008=%d" % int(batch_seq),
        "N10 P3009=%d" % int(p3009),
        "N11 P3010=",
        "N12 P3011=%d" % th,
    ]
    for i, piece in enumerate(pieces, start=1):
        header.append("N%02d P4%03d=%s" % (12 + i, i, _format_p4001(piece, sh)))
    body = _gcode_lines_from_layout(layout)
    return "\n".join(header + [""] + body) + "\n"


def iter_g_files_for_layouts(
    layouts: List[Dict[str, Any]],
    order_info: Dict[str, Any] | None = None,
) -> List[Tuple[str, str]]:
    """
    Список (относительный_путь, содержимое) для всех листов.
    Папки: {материал}_{толщина}/Z....G
    """
    out: List[Tuple[str, str]] = []
    for idx, lay in enumerate(layouts or [], start=1):
        if not isinstance(lay, dict):
            continue
        sw = _safe_int(lay.get("sheet_width"), 0)
        sh = _safe_int(lay.get("sheet_height"), 0)
        if sw <= 0 or sh <= 0:
            continue
        if not (lay.get("pieces") or lay.get("cut_segments")):
            continue
        folder = _material_folder_name(str(lay.get("material") or ""), _resolve_thickness_mm(lay))
        fname = g_file_basename(order_info, idx, sw, sh, batch_seq=1)
        rel = "%s/%s" % (folder, fname)
        out.append((rel, layout_to_g_content(lay, order_info, sheet_index=idx, batch_seq=1)))
    return out


def write_g_files_to_dir(
    layouts: List[Dict[str, Any]],
    order_info: Dict[str, Any] | None,
    output_dir: str,
) -> List[str]:
    written: List[str] = []
    root = os.path.abspath(output_dir)
    os.makedirs(root, exist_ok=True)
    for rel, content in iter_g_files_for_layouts(layouts, order_info):
        path = os.path.join(root, *rel.replace("\\", "/").split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="cp1251", errors="replace", newline="\n") as f:
            f.write(content)
        written.append(path)
    return written


def build_g_files_zip_bytes(
    layouts: List[Dict[str, Any]],
    order_info: Dict[str, Any] | None = None,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, content in iter_g_files_for_layouts(layouts, order_info):
            zf.writestr(rel.replace("\\", "/"), content.encode("cp1251", errors="replace"))
    return buf.getvalue()


def write_g_files_zip(
    layouts: List[Dict[str, Any]],
    order_info: Dict[str, Any] | None,
    zip_path: str,
) -> str:
    data = build_g_files_zip_bytes(layouts, order_info)
    path = os.path.abspath(zip_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def order_info_from_row(order_row: Dict[str, Any] | None) -> Dict[str, Any]:
    row = order_row or {}
    return {
        "order_id": row.get("id"),
        "k_number": row.get("k_number"),
        "client_name": row.get("client_name"),
    }
