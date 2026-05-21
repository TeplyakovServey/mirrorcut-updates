import os
import threading

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor
from psycopg2 import InterfaceError, OperationalError
from contextlib import contextmanager
import config
from . import trace as db_trace

_pool = None
_pool_lock = threading.Lock()


def _pool_ping_enabled() -> bool:
    return (os.environ.get("MC_PG_POOL_PING") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _pool_disabled() -> bool:
    return (os.environ.get("MC_PG_USE_POOL") or "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def _pool_maxconn() -> int:
    try:
        n = int((os.environ.get("MC_PG_POOL_MAX") or "32").strip() or "32")
    except ValueError:
        n = 32
    return max(2, min(64, n))


def _pool_minconn(maxconn: int) -> int:
    """Минимум соединений в пуле при старте. Раньше по умолчанию было 4 — при удалённом PG
    пул при первом get_connection() открывал 4 TCP-сессии подряд (~4× время одного коннекта)."""
    try:
        n = int((os.environ.get("MC_PG_POOL_MIN") or "1").strip() or "1")
    except ValueError:
        n = 1
    n = max(1, min(16, n))
    return min(n, maxconn)


def _connect_timeout_sec() -> int:
    """Таймаут установки TCP к PostgreSQL (сек). Не ускоряет успешный коннект, но не даёт висеть вечно."""
    try:
        n = int((os.environ.get("MC_PG_CONNECT_TIMEOUT") or "15").strip() or "15")
    except ValueError:
        n = 15
    return max(2, min(120, n))


def _get_pool():
    """Пул переиспользует TCP/авторизацию к PostgreSQL — те же транзакции и commit, что и у прямого connect."""
    global _pool
    if _pool is not None:
        return _pool
    with _pool_lock:
        if _pool is None:
            kw = dict(
                dbname=config.DB_CONFIG["dbname"],
                user=config.DB_CONFIG["user"],
                password=config.DB_CONFIG["password"],
                host=config.DB_CONFIG["host"],
                port=config.DB_CONFIG["port"],
                cursor_factory=db_trace.cursor_factory_class(),
                connect_timeout=_connect_timeout_sec(),
                # Keep idle TCP connections alive to reduce stale pooled sockets.
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=3,
            )
            maxc = _pool_maxconn()
            minc = _pool_minconn(maxc)
            _pool = pg_pool.ThreadedConnectionPool(minc, maxc, **kw)
        return _pool


def _connect_direct():
    return psycopg2.connect(
        dbname=config.DB_CONFIG["dbname"],
        user=config.DB_CONFIG["user"],
        password=config.DB_CONFIG["password"],
        host=config.DB_CONFIG["host"],
        port=config.DB_CONFIG["port"],
        cursor_factory=db_trace.cursor_factory_class(),
        connect_timeout=_connect_timeout_sec(),
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )


def _ping_connection(conn):
    if conn is None or getattr(conn, "closed", 1):
        raise InterfaceError("connection is closed")
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()


@contextmanager
def get_connection():
    """Соединение из пула: commit при успехе, rollback при ошибке, возврат в пул."""
    with db_trace.connection_span():
        if _pool_disabled():
            conn = _connect_direct()
            try:
                if _pool_ping_enabled():
                    # Reconnect once if DB has dropped an idle socket.
                    try:
                        _ping_connection(conn)
                    except (OperationalError, InterfaceError):
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = _connect_direct()
                yield conn
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except InterfaceError:
                    pass
                raise
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            return
        p = _get_pool()
        conn = None
        do_ping = _pool_ping_enabled()
        for _ in range(2):
            conn = p.getconn()
            try:
                if do_ping:
                    _ping_connection(conn)
                break
            except (OperationalError, InterfaceError):
                try:
                    p.putconn(conn, close=True)
                except TypeError:
                    # Older psycopg2 versions may not support close kwarg.
                    try:
                        conn.close()
                    except Exception:
                        pass
                except Exception:
                    pass
                conn = None
                continue
        if conn is None:
            raise OperationalError("failed to acquire a healthy database connection from pool")
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except InterfaceError:
                pass
            raise
        finally:
            try:
                p.putconn(conn)
            except Exception:
                pass


def check_tables_exist(table_names):
    """
    Check which of the given table names already exist in the database.
    Returns a set of existing table names.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY(%s)
                """,
                (list(table_names),)
            )
            return {row['table_name'] for row in cur.fetchall()}
