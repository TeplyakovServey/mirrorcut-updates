# -*- coding: utf-8 -*-
"""Сериализация состояния просчёта (ключи как в Streamlit selected)."""
from __future__ import annotations

import json
from typing import Any, Dict


def order_payload_to_json(selected: Dict[str, Any]) -> str:
    """Убираем несериализуемые объекты; datetime → isoformat при необходимости."""

    def norm(o):
        if isinstance(o, dict):
            return {str(k): norm(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [norm(x) for x in o]
        if isinstance(o, (str, int, float, bool)) or o is None:
            return o
        return str(o)

    return json.dumps(norm(selected), ensure_ascii=False, indent=2)


def order_payload_from_json(s: str) -> Dict[str, Any]:
    return json.loads(s)
