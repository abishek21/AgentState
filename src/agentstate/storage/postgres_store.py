"""PostgreSQL-backed ``StateStore`` (shared, cross-machine mode).

Requires the optional ``psycopg`` dependency (``pip install
agentstate[postgres]``). Import is deferred so the SQLite-only path has
no hard dependency on it.
"""
from __future__ import annotations

from agentstate.storage.sql_store import POSTGRES, SqlStateStore


def connect(dsn: str) -> SqlStateStore:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - exercised only without extra
        raise RuntimeError(
            "PostgreSQL support requires the 'psycopg' package. "
            "Install with: pip install 'agentstate[postgres]'"
        ) from exc

    conn = psycopg.connect(dsn, autocommit=False)
    store = SqlStateStore(conn, POSTGRES)
    store.init_schema()
    return store
