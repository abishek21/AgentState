"""Simple, dependency-free SQL migration runner.

Migrations are plain ``.sql`` files under ``db/migrations/<dialect>/``,
applied in filename order and tracked in a ``schema_migrations`` table so
they are only ever applied once per database.
"""
from __future__ import annotations

import logging
from importlib import resources
from typing import Iterable

logger = logging.getLogger("agentstate.db.migrations")


def _migration_files(dialect: str) -> list[tuple[str, str]]:
    """Return ``(filename, sql)`` tuples for a dialect, sorted by filename."""
    package = f"agentstate.db.migrations.{dialect}"
    files = []
    for entry in resources.files(package).iterdir():
        if entry.name.endswith(".sql"):
            files.append(entry.name)
    files.sort()
    out = []
    for name in files:
        sql = resources.files(package).joinpath(name).read_text(encoding="utf-8")
        out.append((name, sql))
    return out


def applied_versions(cursor, dialect: str) -> set[str]:
    placeholder = "?" if dialect == "sqlite" else "%s"
    create_sql = {
        "sqlite": (
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        ),
        "postgres": (
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        ),
    }[dialect]
    cursor.execute(create_sql)
    cursor.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def run_migrations(connection, dialect: str) -> list[str]:
    """Apply any migrations not yet recorded for this connection.

    Returns the list of newly-applied migration filenames.
    """
    cursor = connection.cursor()
    already = applied_versions(cursor, dialect)
    newly_applied: list[str] = []
    for name, sql in _migration_files(dialect):
        if name in already:
            continue
        logger.info("Applying migration %s (%s)", name, dialect)
        cursor.executescript(sql) if dialect == "sqlite" else cursor.execute(sql)
        if dialect == "sqlite":
            cursor.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
                (name,),
            )
        else:
            cursor.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (%s, now())",
                (name,),
            )
        newly_applied.append(name)
    connection.commit()
    cursor.close()
    return newly_applied
