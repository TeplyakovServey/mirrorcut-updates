"""In-memory cache: clients list loaded at startup, refreshed when needed."""
_clients_list = []


def load_clients():
    global _clients_list
    from db import models
    rows = models.get_all_clients()
    _clients_list = [r['name'] for r in rows]


def get_clients_list():
    return list(_clients_list)


def filter_clients_by_prefix(prefix):
    """Filter from memory (no DB). Case-insensitive startswith."""
    if not prefix:
        return list(_clients_list)
    p = prefix.strip().lower()
    return [n for n in _clients_list if n.lower().startswith(p)]


def refresh_clients():
    """Reload from DB (e.g. after adding a new client on order complete)."""
    load_clients()
