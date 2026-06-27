"""In-memory cache: clients list loaded at startup, refreshed when needed."""
from __future__ import annotations

_clients_list: list[str] = []
_clients_lower: list[str] = []
_clients_tokens: list[list[str]] = []
_clients_by_name: dict[str, int] = {}
_clients_by_id: dict[int, str] = {}
_clients_loaded = False


def _tokenize_name(nl: str) -> list[str]:
    return [t for t in (nl or "").split() if t]


def _match_score(nl: str, tokens: list[str], pl: str) -> int:
    if not pl:
        return 0
    if nl.startswith(pl):
        return 0
    if tokens and tokens[0].startswith(pl):
        return 1
    if any(t.startswith(pl) for t in tokens):
        return 2
    if pl in nl:
        return 3
    return -1


def _prefix_variants(prefix: str) -> tuple[str, ...]:
    raw = (prefix or "").strip()
    if not raw:
        return ()
    try:
        from MAIN_PROJECT.logic.keyboard_layout import client_search_prefix_variants
    except ImportError:
        try:
            from logic.keyboard_layout import client_search_prefix_variants
        except ImportError:
            client_search_prefix_variants = None
    if client_search_prefix_variants:
        vs = client_search_prefix_variants(raw)
        if vs:
            return tuple(v.lower() for v in vs if (v or "").strip())
    return (raw.lower(),)


def _build_indexes(rows) -> None:
    global _clients_list, _clients_lower, _clients_tokens, _clients_by_name, _clients_by_id
    names: list[str] = []
    lowers: list[str] = []
    tokens: list[list[str]] = []
    by_name: dict[str, int] = {}
    by_id: dict[int, str] = {}
    for r in rows or []:
        name = (r.get("name") or "").strip()
        if not name:
            continue
        cid = r.get("id")
        nl = name.lower()
        names.append(name)
        lowers.append(nl)
        tokens.append(_tokenize_name(nl))
        if cid is not None:
            try:
                iid = int(cid)
            except (TypeError, ValueError):
                continue
            by_id[iid] = name
            by_name[nl] = iid
    _clients_list = names
    _clients_lower = lowers
    _clients_tokens = tokens
    _clients_by_name = by_name
    _clients_by_id = by_id


def load_clients(force: bool = False) -> None:
    global _clients_loaded
    if _clients_loaded and not force:
        return
    from db import models

    _build_indexes(models.get_all_clients() or [])
    _clients_loaded = True


def get_clients_list() -> list[str]:
    return list(_clients_list)


def filter_clients_by_prefix(prefix, limit=15) -> list[str]:
    """Filter from memory (no DB). Prefix + любое слово имени; EN→RU раскладка."""
    load_clients()
    if not prefix:
        return list(_clients_list)
    prefs = _prefix_variants(prefix)
    if not prefs:
        return list(_clients_list)
    lim = max(1, int(limit or 15))
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for name, nl, toks in zip(_clients_list, _clients_lower, _clients_tokens):
        if name in seen:
            continue
        best = -1
        for pl in prefs:
            sc = _match_score(nl, toks, pl)
            if sc >= 0 and (best < 0 or sc < best):
                best = sc
        if best >= 0:
            seen.add(name)
            scored.append((best, name))
    scored.sort(key=lambda x: (x[0], x[1].lower()))
    return [n for _s, n in scored[:lim]]


def get_client_id_by_name(name) -> int | None:
    """Resolve client id from in-memory cache (exact name, lower + раскладка)."""
    load_clients()
    nm = (name or "").strip()
    if not nm:
        return None
    for cand in (nm,) + _prefix_variants(nm):
        cid = _clients_by_name.get(cand.strip().lower())
        if cid is not None:
            return cid
    return None


def refresh_clients() -> None:
    """Reload from DB (e.g. after adding a new client or NOTIFY from other user)."""
    load_clients(force=True)
