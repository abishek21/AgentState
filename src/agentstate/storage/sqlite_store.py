"""SQLite-backed ``StateStore`` (single-machine/dev mode)."""
from __future__ import annotations

import os
import sqlite3

from agentstate.storage.sql_store import SQLITE, SqlStateStore


def connect(path: str) -> SqlStateStore:
    if path != ":memory:":
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    store = SqlStateStore(conn, SQLITE)
    store.init_schema()
    return store
