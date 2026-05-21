# -*- coding: utf-8 -*-
"""Парсинг ISO-времени с портала и отображение в часовом поясе Москвы."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

_MSK: Optional["ZoneInfo"] = None

_MONTHS_RU_GEN = [
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]


def moscow_tz():
    global _MSK
    if ZoneInfo is None:
        return None
    if _MSK is None:
        _MSK = ZoneInfo("Europe/Moscow")
    return _MSK


def parse_iso_to_moscow(val) -> Optional[datetime]:
    """Разбор строки от API (UTC или с оффсетом) в aware datetime в Europe/Moscow."""
    s = (str(val or "")).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        pass
    if dt is None:
        s2 = s.replace("T", " ", 1)
        if len(s2) >= 19:
            try:
                dt = datetime.fromisoformat(s2[:19])
            except ValueError:
                return None
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    tz = moscow_tz()
    if tz is None:
        return dt
    return dt.astimezone(tz)


def format_iso_datetime_msk_long(val, *, msk_suffix: bool = False) -> str:
    """«8 мая 17:07» в Москве; при msk_suffix добавляется « МСК»."""
    dt = parse_iso_to_moscow(val)
    if not dt:
        return ""
    m = _MONTHS_RU_GEN[dt.month] if 1 <= dt.month <= 12 else ""
    out = "%d %s %02d:%02d" % (dt.day, m or "—", dt.hour, dt.minute)
    if msk_suffix:
        out += " МСК"
    return out


def format_iso_datetime_msk_short(val, *, msk_suffix: bool = True) -> str:
    """«08.05.2026 17:07» в Москве (для плиток и подсказок)."""
    dt = parse_iso_to_moscow(val)
    if not dt:
        return ""
    out = "%02d.%02d.%04d %02d:%02d" % (dt.day, dt.month, dt.year, dt.hour, dt.minute)
    if msk_suffix:
        out += " МСК"
    return out
