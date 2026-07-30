# -*- coding: utf-8 -*-
"""Экспорт файлов .G для стола PRAKTIK / Optima (GALIL) из layout раскроя MIRROR_CUT."""
from __future__ import annotations

import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_EDGE_MARGIN = 10
_SHEET_EDGE = 2
_P400_PAD = 32
_G_WASTE_LABEL = "ОТХОД"


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


@dataclass
class GExportContext:
    """Сквозной контекст нумерации по заказу (несколько листов в одном ZIP)."""

    remnant_display_numbers: List[Any] = field(default_factory=list)
    remnant_index: int = 0
    global_zone_no: int = 0


def g_files_zip_filename(order_id: Any) -> str:
    """Имя ZIP-архива: номер заказа в БД (orders.id), например 17.zip."""
    try:
        return "%d.zip" % int(order_id)
    except (TypeError, ValueError):
        return "0.zip"


def _resolve_thickness_mm(layout: Dict[str, Any]) -> int:
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


def _layout_y_to_p400_y(layout_y: int, height: int, sheet_height: int) -> int:
    """В P400 координата Y — от нижнего края листа (как на столе Optima)."""
    return int(sheet_height) - int(layout_y) - int(height)


def _small_piece_threshold_mm() -> int:
    try:
        from user_settings import get_small_piece_mm

        return int(get_small_piece_mm())
    except Exception:
        return 200


def _normalize_pieces(layout: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = layout.get("pieces") or layout.get("piece_list") or []
    if isinstance(raw, dict):
        raw = list(raw.values()) if raw else []
    return [p for p in raw if isinstance(p, dict)]


def _piece_group_numbers(pieces: List[Dict[str, Any]]) -> Dict[int, int]:
    """Один номер на группу одинаковых изделий (размер + получатель), как __3/__3 в эталоне."""
    out: Dict[int, int] = {}
    group_no = 0
    seen: Dict[Tuple[Any, ...], int] = {}
    for idx, p in enumerate(pieces):
        explicit = p.get("zone_no") or p.get("g_no") or p.get("display_no")
        if explicit is not None:
            try:
                out[idx] = int(explicit)
                continue
            except (TypeError, ValueError):
                pass
        key = (
            str(p.get("recipient") or "").strip(),
            _safe_int(p.get("w"), 0),
            _safe_int(p.get("h"), 0),
        )
        if key not in seen:
            group_no += 1
            seen[key] = group_no
        out[idx] = seen[key]
    return out


def _sorted_piece_indices(pieces: List[Dict[str, Any]]) -> List[int]:
    """Порядок P400 как на столе Optima: сверху вниз, слева направо."""
    return sorted(
        range(len(pieces)),
        key=lambda i: (_safe_int(pieces[i].get("y"), 0), _safe_int(pieces[i].get("x"), 0)),
    )


def _format_p400_tail(label: str, number: int) -> str:
    """Хвост P400: _ОТХОД_19 или __2 (без подписи клиента)."""
    label = (label or "").strip()
    num = max(1, _safe_int(number, 1))
    if label:
        tail = "_%s_%d" % (label, num)
    else:
        tail = "__%d" % num
    pad = max(0, _P400_PAD - len(tail))
    return tail + ("_" * pad)


def _format_p400_zone(
    x: int,
    layout_y: int,
    w: int,
    h: int,
    sheet_height: int,
    *,
    label: str = "",
    number: int = 1,
) -> str:
    y_p400 = _layout_y_to_p400_y(layout_y, h, sheet_height)
    core = "%d_%d_%d_%d_%d_%d" % (x, y_p400, w, h, w, h)
    return core + _format_p400_tail(label, number)


def _build_p400_zones(
    layout: Dict[str, Any],
    ctx: GExportContext,
) -> List[str]:
    """
    Все зоны листа для P4001…: изделия, деловые остатки, отходы (ОТХОД).
    Порядок: изделия → остатки → отходы (как в эталонных .G Optima).
    """
    sh = _safe_int(layout.get("sheet_height"), 0)
    if sh <= 0:
        return []

    pieces = _normalize_pieces(layout)
    group_nums = _piece_group_numbers(pieces)
    zones: List[str] = []

    for idx in _sorted_piece_indices(pieces):
        p = pieces[idx]
        w = _safe_int(p.get("w"), 0)
        h = _safe_int(p.get("h"), 0)
        if w <= 0 or h <= 0:
            continue
        x = _safe_int(p.get("x"), 0)
        y = _safe_int(p.get("y"), 0)
        ctx.global_zone_no += 1
        num = group_nums.get(idx, idx + 1)
        zones.append(_format_p400_zone(x, y, w, h, sh, label="", number=num))

    for i, r in enumerate(layout.get("business_rects") or []):
        if not isinstance(r, dict):
            continue
        w = _safe_int(r.get("w"), 0)
        h = _safe_int(r.get("h"), 0)
        if w <= 0 or h <= 0:
            continue
        x = _safe_int(r.get("x"), 0)
        y = _safe_int(r.get("y"), 0)
        ctx.global_zone_no += 1
        gi = ctx.remnant_index + i
        sticker = None
        if gi < len(ctx.remnant_display_numbers) and ctx.remnant_display_numbers[gi]:
            sticker = ctx.remnant_display_numbers[gi]
        num = _safe_int(sticker, ctx.global_zone_no) if sticker is not None else ctx.global_zone_no
        zones.append(_format_p400_zone(x, y, w, h, sh, label="", number=num))

    ctx.remnant_index += len(layout.get("business_rects") or [])

    for r in layout.get("waste_rects") or []:
        if not isinstance(r, dict):
            continue
        w = _safe_int(r.get("w"), 0)
        h = _safe_int(r.get("h"), 0)
        if w <= 0 or h <= 0:
            continue
        x = _safe_int(r.get("x"), 0)
        y = _safe_int(r.get("y"), 0)
        ctx.global_zone_no += 1
        explicit = r.get("zone_no") or r.get("display_no")
        if explicit is not None:
            try:
                num = int(explicit)
            except (TypeError, ValueError):
                num = ctx.global_zone_no
        else:
            num = ctx.global_zone_no
        zones.append(
            _format_p400_zone(
                x,
                y,
                w,
                h,
                sh,
                label=_G_WASTE_LABEL,
                number=num,
            )
        )

    return zones


def _zone_rects(layout: Dict[str, Any]) -> List[Dict[str, Any]]:
    rects: List[Dict[str, Any]] = []
    for p in _normalize_pieces(layout):
        rects.append(p)
    for key in ("business_rects", "waste_rects"):
        for r in layout.get(key) or []:
            if isinstance(r, dict):
                rects.append(r)
    return rects


def _fallback_cut_segments_from_zone_bounds(layout: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Резерв: все границы зон листа → гильотинные резы (как в эталонных .G Optima)."""
    sw = _safe_int(layout.get("sheet_width"), 0)
    sh = _safe_int(layout.get("sheet_height"), 0)
    if sw <= 0 or sh <= 0:
        return []
    rects = _zone_rects(layout)
    if not rects:
        return []
    xs = {0, sw}
    ys = {0, sh}
    for r in rects:
        xs.add(_safe_int(r.get("x"), 0))
        xs.add(_safe_int(r.get("x"), 0) + _safe_int(r.get("w"), 0))
        ys.add(_safe_int(r.get("y"), 0))
        ys.add(_safe_int(r.get("y"), 0) + _safe_int(r.get("h"), 0))
    segments: List[Dict[str, Any]] = []
    for y in sorted(ys):
        if y <= 0 or y >= sh:
            continue
        segments.append({"type": "H", "pos": y, "extent_lo": _SHEET_EDGE, "extent_hi": sw - _SHEET_EDGE})
    for x in sorted(xs):
        if x <= 0 or x >= sw:
            continue
        segments.append({"type": "V", "pos": x, "extent_lo": _SHEET_EDGE, "extent_hi": sh - _SHEET_EDGE})
    return segments


def _ensure_cut_segments(layout: Dict[str, Any]) -> None:
    if not layout.get("cut_segments"):
        try:
            from logic.cutting_algorithm import assign_chocolate_bar_cut_segments_to_layout

            assign_chocolate_bar_cut_segments_to_layout(layout)
        except Exception:
            pass
    if not layout.get("cut_segments"):
        layout["cut_segments"] = _fallback_cut_segments_from_zone_bounds(layout)


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


def make_g_export_context(order_info: Dict[str, Any] | None) -> GExportContext:
    ctx = GExportContext()
    oid = (order_info or {}).get("order_id") or (order_info or {}).get("id")
    try:
        oid_int = int(oid)
    except (TypeError, ValueError):
        return ctx
    if oid_int < 1:
        return ctx
    try:
        from db import models as db_models

        ctx.remnant_display_numbers = list(db_models.get_remnant_display_numbers_by_order_id(oid_int) or [])
    except Exception:
        pass
    return ctx


def layout_to_g_content(
    layout: Dict[str, Any],
    order_info: Dict[str, Any] | None = None,
    *,
    sheet_index: int = 1,
    batch_seq: int = 1,
    g_ctx: GExportContext | None = None,
) -> str:
    sw = _safe_int(layout.get("sheet_width"), 0)
    sh = _safe_int(layout.get("sheet_height"), 0)
    th = _resolve_thickness_mm(layout)
    pieces = _normalize_pieces(layout)
    p400_zones = _build_p400_zones(layout, g_ctx or GExportContext())
    p3009 = max(1, len(pieces)) if pieces else 1
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
    for i, zone in enumerate(p400_zones, start=1):
        header.append("N%02d P4%03d=%s" % (12 + i, i, zone))
    body = _gcode_lines_from_layout(layout)
    return "\n".join(header + [""] + body) + "\n"


def iter_g_files_for_layouts(
    layouts: List[Dict[str, Any]],
    order_info: Dict[str, Any] | None = None,
) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    ctx = make_g_export_context(order_info)
    for idx, lay in enumerate(layouts or [], start=1):
        if not isinstance(lay, dict):
            continue
        sw = _safe_int(lay.get("sheet_width"), 0)
        sh = _safe_int(lay.get("sheet_height"), 0)
        if sw <= 0 or sh <= 0:
            continue
        if not (_normalize_pieces(lay) or lay.get("cut_segments")):
            continue
        folder = _material_folder_name(str(lay.get("material") or ""), _resolve_thickness_mm(lay))
        fname = g_file_basename(order_info, idx, sw, sh, batch_seq=1)
        rel = "%s/%s" % (folder, fname)
        out.append(
            (
                rel,
                layout_to_g_content(lay, order_info, sheet_index=idx, batch_seq=1, g_ctx=ctx),
            )
        )
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
