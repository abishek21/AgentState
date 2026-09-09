"""Shared SQL implementation of ``StateStore``.

A single implementation drives both SQLite and PostgreSQL through a small
``Dialect`` object that captures the handful of real differences
(parameter placeholders, upsert syntax, full-text search, timestamp
defaults). This avoids duplicating business logic per backend.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

from agentstate.db.migrations_runner import run_migrations
from agentstate.models import (
    Project,
    StateRecord,
    StateRecordInput,
    iso,
    new_id,
    utcnow,
)
from agentstate.storage.base import StateStore

logger = logging.getLogger("agentstate.storage")

RECORD_COLUMNS = [
    "id", "project_id", "type", "title", "content", "status",
    "created_at", "updated_at", "created_by_agent", "created_by_session",
    "related_commit", "related_branch", "related_task_id",
    "evidence", "tags", "extra", "supersedes_id", "superseded_by_id",
]


@dataclass
class Dialect:
    name: str
    placeholder: str

    def ph(self, n: int) -> str:
        if self.placeholder == "?":
            return ", ".join(["?"] * n)
        return ", ".join(f"${i}" if self.name == "asyncpg" else "%s" for i in range(n))

    def p(self, i: int = 0) -> str:
        return "?" if self.placeholder == "?" else "%s"

    def search_clause(self) -> str:
        if self.name == "postgres":
            return "to_tsvector('english', title || ' ' || content) @@ plainto_tsquery('english', {p})"
        return "(title LIKE {p} OR content LIKE {p})"


SQLITE = Dialect(name="sqlite", placeholder="?")
POSTGRES = Dialect(name="postgres", placeholder="%s")


def _row_to_dict(cursor, row) -> dict[str, Any]:
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


class SqlStateStore(StateStore):
    """``StateStore`` backed by a DB-API 2.0 connection (sqlite3 or psycopg)."""

    def __init__(self, connection, dialect: Dialect):
        self._conn = connection
        self._d = dialect

    # -- schema -----------------------------------------------------------
    def init_schema(self) -> None:
        applied = run_migrations(self._conn, self._d.name)
        if applied:
            logger.info("Applied migrations: %s", ", ".join(applied))

    # -- projects -----------------------------------------------------------
    def get_or_create_project(
        self, remote_fingerprint: Optional[str], name: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Project:
        cur = self._conn.cursor()
        p = self._d.p()
        if project_id:
            cur.execute(f"SELECT * FROM projects WHERE id = {p}", (project_id,))
            row = cur.fetchone()
            if row:
                return Project.from_row(_row_to_dict(cur, row))
            # Explicit id requested but doesn't exist yet: create it with that id.
            return self._insert_project(cur, project_id, name, remote_fingerprint)

        if remote_fingerprint:
            cur.execute(
                f"SELECT * FROM projects WHERE remote_fingerprint = {p}",
                (remote_fingerprint,),
            )
            row = cur.fetchone()
            if row:
                return Project.from_row(_row_to_dict(cur, row))

        return self._insert_project(cur, new_id(), name, remote_fingerprint)

    def _insert_project(self, cur, pid, name, remote_fingerprint) -> Project:
        ph = self._d.ph(4)
        cur.execute(
            f"INSERT INTO projects (id, name, remote_fingerprint, created_at) VALUES ({ph})",
            (pid, name, remote_fingerprint, iso(utcnow())),
        )
        self._conn.commit()
        return self.get_project(pid)

    def get_project(self, project_id: str) -> Optional[Project]:
        cur = self._conn.cursor()
        cur.execute(f"SELECT * FROM projects WHERE id = {self._d.p()}", (project_id,))
        row = cur.fetchone()
        return Project.from_row(_row_to_dict(cur, row)) if row else None

    # -- records -----------------------------------------------------------
    def create(self, record: StateRecordInput) -> StateRecord:
        from agentstate.models import DEFAULT_STATUS, RecordType

        rid = new_id()
        now = iso(utcnow())
        status = record.status or DEFAULT_STATUS.get(RecordType(record.type), "active")
        values = (
            rid, record.project_id, record.type, record.title, record.content, status,
            now, now, record.created_by_agent, record.created_by_session,
            record.related_commit, record.related_branch, record.related_task_id,
            json.dumps(record.evidence or []), json.dumps(record.tags or []),
            json.dumps(record.extra or {}), record.supersedes_id, None,
        )
        ph = self._d.ph(len(RECORD_COLUMNS))
        cols = ", ".join(RECORD_COLUMNS)
        cur = self._conn.cursor()
        cur.execute(f"INSERT INTO state_records ({cols}) VALUES ({ph})", values)
        self._conn.commit()
        return self.get(rid)

    def update(self, record_id: str, **fields: Any) -> StateRecord:
        if not fields:
            return self.get(record_id)
        json_fields = {"evidence", "tags", "extra"}
        set_parts = []
        values: list[Any] = []
        for k, v in fields.items():
            if k in json_fields and not isinstance(v, str):
                v = json.dumps(v)
            set_parts.append(f"{k} = {self._d.p()}")
            values.append(v)
        set_parts.append(f"updated_at = {self._d.p()}")
        values.append(iso(utcnow()))
        values.append(record_id)
        cur = self._conn.cursor()
        cur.execute(
            f"UPDATE state_records SET {', '.join(set_parts)} WHERE id = {self._d.p()}",
            values,
        )
        self._conn.commit()
        return self.get(record_id)

    def get(self, record_id: str) -> Optional[StateRecord]:
        cur = self._conn.cursor()
        cur.execute(f"SELECT * FROM state_records WHERE id = {self._d.p()}", (record_id,))
        row = cur.fetchone()
        return StateRecord.from_row(_row_to_dict(cur, row)) if row else None

    def supersede(self, old_id: str, new_record: StateRecordInput) -> tuple[StateRecord, StateRecord]:
        old = self.get(old_id)
        if old is None:
            raise ValueError(f"cannot supersede unknown record {old_id}")
        new_record.supersedes_id = old_id
        new = self.create(new_record)
        old = self.update(old_id, status="superseded", superseded_by_id=new.id)
        return old, new

    def search(
        self,
        project_id: str,
        query: str,
        types: Optional[list[str]] = None,
        limit: int = 20,
        include_stale: bool = True,
    ) -> list[StateRecord]:
        p = self._d.p()
        clauses = [f"project_id = {p}"]
        values: list[Any] = [project_id]
        if query:
            if self._d.name == "postgres":
                clauses.append(self._d.search_clause().format(p=p))
                values.append(query)
            else:
                clauses.append(self._d.search_clause().format(p=p))
                like = f"%{query}%"
                values.extend([like, like])
        if types:
            placeholders = self._d.ph(len(types))
            clauses.append(f"type IN ({placeholders})")
            values.extend(types)
        if not include_stale:
            clauses.append("superseded_by_id IS NULL")
        where = " AND ".join(clauses)
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT * FROM state_records WHERE {where} ORDER BY created_at DESC LIMIT {int(limit)}",
            values,
        )
        rows = cur.fetchall()
        return [StateRecord.from_row(_row_to_dict(cur, r)) for r in rows]

    def get_current_state(
        self, project_id: str, types: Optional[list[str]] = None, limit: int = 100,
    ) -> list[StateRecord]:
        from agentstate.models import STALE_STATUSES

        p = self._d.p()
        clauses = [f"project_id = {p}", "superseded_by_id IS NULL"]
        values: list[Any] = [project_id]
        stale_ph = self._d.ph(len(STALE_STATUSES))
        clauses.append(f"status NOT IN ({stale_ph})")
        values.extend(sorted(STALE_STATUSES))
        if types:
            clauses.append(f"type IN ({self._d.ph(len(types))})")
            values.extend(types)
        where = " AND ".join(clauses)
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT * FROM state_records WHERE {where} ORDER BY created_at DESC LIMIT {int(limit)}",
            values,
        )
        rows = cur.fetchall()
        return [StateRecord.from_row(_row_to_dict(cur, r)) for r in rows]

    def get_history(
        self,
        project_id: str,
        since: Optional[str] = None,
        until: Optional[str] = None,
        types: Optional[list[str]] = None,
        limit: int = 200,
    ) -> list[StateRecord]:
        p = self._d.p()
        clauses = [f"project_id = {p}"]
        values: list[Any] = [project_id]
        if since:
            clauses.append(f"created_at >= {p}")
            values.append(since)
        if until:
            clauses.append(f"created_at <= {p}")
            values.append(until)
        if types:
            clauses.append(f"type IN ({self._d.ph(len(types))})")
            values.extend(types)
        where = " AND ".join(clauses)
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT * FROM state_records WHERE {where} ORDER BY created_at ASC LIMIT {int(limit)}",
            values,
        )
        rows = cur.fetchall()
        return [StateRecord.from_row(_row_to_dict(cur, r)) for r in rows]

    def close(self) -> None:
        self._conn.close()
