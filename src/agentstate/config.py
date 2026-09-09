"""Environment/config plumbing shared by the CLI and MCP server."""
from __future__ import annotations

import os
from typing import Optional

from agentstate.identity import find_repo_root, load_config
from agentstate.storage.base import StateStore

DEFAULT_SQLITE_PATH = os.path.join(".agentstate", "state.db")


def resolve_database_url(repo_root: Optional[str] = None) -> str:
    """Resolve the database URL to use, in order of precedence:

    1. ``AGENTSTATE_DATABASE_URL`` env var (works for both sqlite/postgres).
    2. ``database_url`` recorded in the repo's ``.agentstate/config.json``.
    3. A local SQLite file under ``.agentstate/state.db`` (dev mode default).
    """
    env_url = os.environ.get("AGENTSTATE_DATABASE_URL")
    if env_url:
        return env_url
    root = repo_root or find_repo_root()
    cfg = load_config(root)
    if cfg and cfg.database_url:
        return cfg.database_url
    return f"sqlite:///{os.path.join(root, DEFAULT_SQLITE_PATH)}"


def open_store(database_url: str) -> StateStore:
    if database_url.startswith("sqlite:///"):
        from agentstate.storage import sqlite_store

        path = database_url[len("sqlite:///"):]
        return sqlite_store.connect(path)
    if database_url.startswith("postgres://") or database_url.startswith("postgresql://"):
        from agentstate.storage import postgres_store

        return postgres_store.connect(database_url)
    raise ValueError(
        f"Unsupported database URL scheme: {database_url!r}. "
        "Use 'sqlite:///path/to/file.db' or 'postgresql://...'."
    )
