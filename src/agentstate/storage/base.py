"""Storage abstraction for AgentState.

``StateStore`` is the interface business logic (MCP tools, CLI) depends
on. Two concrete backends are provided: SQLite (single-machine/dev mode)
and PostgreSQL (shared, cross-machine mode). Both share the same SQL
logic through a small ``Dialect`` adapter, so there is exactly one place
the query behaviour is defined.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from agentstate.models import Project, StateRecord, StateRecordInput


class StateStore(ABC):
    """Abstract persistence + retrieval interface for project state."""

    @abstractmethod
    def init_schema(self) -> None:
        """Create/upgrade the schema (idempotent)."""

    @abstractmethod
    def get_or_create_project(
        self, remote_fingerprint: Optional[str], name: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> Project:
        """Resolve the project identity for a repository.

        If ``project_id`` is given it is used/joined directly. Otherwise the
        project is looked up by ``remote_fingerprint``; if none exists yet a
        new project is created.
        """

    @abstractmethod
    def get_project(self, project_id: str) -> Optional[Project]:
        ...

    @abstractmethod
    def create(self, record: StateRecordInput) -> StateRecord:
        """Create a new state record."""

    @abstractmethod
    def update(self, record_id: str, **fields: Any) -> StateRecord:
        """Update mutable fields (e.g. status, content, extra) on a record."""

    @abstractmethod
    def get(self, record_id: str) -> Optional[StateRecord]:
        ...

    @abstractmethod
    def supersede(self, old_id: str, new_record: StateRecordInput) -> tuple[StateRecord, StateRecord]:
        """Create ``new_record`` and mark ``old_id`` as superseded by it.

        Returns ``(old_record, new_record)`` after the update.
        """

    @abstractmethod
    def search(
        self,
        project_id: str,
        query: str,
        types: Optional[list[str]] = None,
        limit: int = 20,
        include_stale: bool = True,
    ) -> list[StateRecord]:
        """Full-text-ish search across historical + current records."""

    @abstractmethod
    def get_current_state(
        self, project_id: str, types: Optional[list[str]] = None, limit: int = 100,
    ) -> list[StateRecord]:
        """Return only records that represent the *current* understanding."""

    @abstractmethod
    def get_history(
        self,
        project_id: str,
        since: Optional[str] = None,
        until: Optional[str] = None,
        types: Optional[list[str]] = None,
        limit: int = 200,
    ) -> list[StateRecord]:
        """Return all records (current + superseded) in chronological order."""

    @abstractmethod
    def close(self) -> None:
        ...
