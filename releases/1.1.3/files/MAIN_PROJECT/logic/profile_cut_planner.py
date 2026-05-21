from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Tuple


def _piece_sort_key(mm: int) -> Tuple[int, int]:
    return (-int(mm), int(mm))


def plan_profile_cuts(required_pieces_mm: List[int], sources: List[Dict[str, Any]], cut_loss_mm: int = 60) -> Dict[str, Any]:
    """Group-aware planner: frequent groups first, remnants-first, low waste."""
    pieces = [int(x) for x in (required_pieces_mm or []) if int(x) > 0]
    loss = max(0, int(cut_loss_mm or 0))
    grouped = Counter(pieces)
    # Groups sorted by count desc, then piece desc.
    group_order = sorted(grouped.items(), key=lambda kv: (-int(kv[1]), -int(kv[0])))
    segments: List[Dict[str, Any]] = []
    assigned: List[int] = []

    src_rows = []
    for s in sources or []:
        try:
            ln = int(s.get("length_mm") or 0)
        except (TypeError, ValueError):
            ln = 0
        if ln <= 0:
            continue
        src_rows.append(
            {
                "stock_id": s.get("stock_id"),
                "length_mm": ln,
                "is_remnant": bool(s.get("is_remnant")),
                "label": s.get("label") or s.get("source_label") or "",
                "source_kind": "warehouse_remnant" if bool(s.get("is_remnant")) else "warehouse_full",
            }
        )
    src_rows.sort(key=lambda r: (0 if r["is_remnant"] else 1, int(r["length_mm"])))
    used_src = set()

    def _best_source_for_piece(piece_mm: int):
        need = int(piece_mm) + loss
        remnant_candidates = []
        full_candidates = []
        for i, src in enumerate(src_rows):
            if i in used_src:
                continue
            cap = int(src["length_mm"] or 0)
            if cap < need:
                continue
            rem_after_one = cap - need
            # Всегда стараемся пустить в дело максимально короткий подходящий источник,
            # а неделовой хвост <200 мм штрафуем.
            micro_penalty = 300000 if 0 <= rem_after_one < 200 else 0
            score = micro_penalty + cap
            if src.get("is_remnant"):
                remnant_candidates.append((score, i))
            else:
                full_candidates.append((score, i))
        # Жесткий режим остатков:
        # если есть хотя бы один подходящий остаток, целые профили не используем.
        candidates = remnant_candidates if remnant_candidates else full_candidates
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    for piece_mm, qty in group_order:
        for _ in range(int(qty or 0)):
            idx = _best_source_for_piece(int(piece_mm))
            if idx is None:
                continue
            src = src_rows[idx]
            used_src.add(idx)
            cap = int(src["length_mm"])
            # Start with one piece from the current group.
            cuts: List[int] = [int(piece_mm)]
            used = int(piece_mm) + loss
            assigned.append(int(piece_mm))
            # Fill remainder greedily with any longest pieces.
            remaining_pool = []
            for mm, cnt in grouped.items():
                rem_cnt = int(cnt) - int(assigned.count(mm))
                if rem_cnt > 0:
                    remaining_pool.extend([int(mm)] * rem_cnt)
            remaining_pool.sort(reverse=True)
            for p in remaining_pool:
                need = int(p) + loss
                if used + need <= cap:
                    cuts.append(int(p))
                    used += need
                    assigned.append(int(p))
            rem = max(0, cap - used)
            waste = rem if rem < 200 else 0
            remnant = rem if rem >= 200 else 0
            segments.append(
                {
                    "source_stock_id": src.get("stock_id"),
                    "source_kind": src.get("source_kind"),
                    "source_length_mm": cap,
                    "source_label": src.get("label") or "",
                    "cuts": [{"piece_mm": x, "cut_loss_mm": loss} for x in cuts],
                    "outputs": [{"piece_mm": x, "target": "assembly"} for x in cuts],
                    "waste_mm": waste,
                    "remnant_mm": remnant,
                    "cut_count": len(cuts),
                }
            )

    requested_sorted = []
    for mm, cnt in grouped.items():
        requested_sorted.extend([int(mm)] * int(cnt))
    assigned_copy = list(assigned)
    unassigned = []
    for mm in requested_sorted:
        try:
            assigned_copy.remove(int(mm))
        except ValueError:
            unassigned.append(int(mm))

    return {
        "segments": segments,
        "groups": [{"piece_mm": int(mm), "count": int(cnt)} for mm, cnt in group_order],
        "unassigned_pieces_mm": unassigned,
        "assigned_count": len(assigned),
        "requested_count": len(pieces),
        "cut_loss_mm": loss,
        "ok": len(unassigned) == 0,
    }

