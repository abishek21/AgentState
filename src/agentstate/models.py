"""Typed data model for AgentState project state.

State is distinct from raw chat history: every record is one of a small
set of structured types with explicit provenance, so agents retrieve
*understanding*, not transcripts.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class RecordType(str, Enum):
    FINDING = "finding"
    DECISION = "decision"
    EXPERIMENT = "experiment"
    TASK = "task"
    ISSUE = "issue"
    HYPOTHESIS = "hypothesis"


# Default/allowed status values per type. Status is a plain string so the
# schema can evolve, but these are the values the CLI/MCP tools use.
DEFAULT_STATUS = {
    RecordType.FINDING: "active",
    RecordType.DECISION: "active",
    RecordType.EXPERIMENT: "completed",
    RecordType.TASK: "open",
    RecordType.ISSUE: "open",
    RecordType.HYPOTHESIS: "open",
}

# Statuses that mean "no longer the current understanding" and therefore
# should be excluded from get_project_state()/get_context() by default.
STALE_STATUSES = {"superseded", "resolved", "done", "closed", "rejected"}


@dataclass
class Project:
    id: str
    name: Optional[str]
    remote_fingerprint: Optional[str]
    created_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Project":
        return cls(
            id=row["id"],
            name=row["name"],
            remote_fingerprint=row["remote_fingerprint"],
            created_at=_parse_dt(row["created_at"]),
        )


@dataclass
class StateRecord:
    id: str
    project_id: str
    type: str
    title: str
    content: str
    status: str
    created_at: datetime
    updated_at: datetime
    created_by_agent: Optional[str] = None
    created_by_session: Optional[str] = None
    related_commit: Optional[str] = None
    related_branch: Optional[str] = None
    related_task_id: Optional[str] = None
    evidence: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    supersedes_id: Optional[str] = None
    superseded_by_id: Optional[str] = None

    @property
    def is_current(self) -> bool:
        return self.status not in STALE_STATUSES and self.superseded_by_id is None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "project_id": self.project_id,
            "type": self.type,
            "title": self.title,
            "content": self.content,
            "status": self.status,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
            "created_by_agent": self.created_by_agent,
            "created_by_session": self.created_by_session,
            "related_commit": self.related_commit,
            "related_branch": self.related_branch,
            "related_task_id": self.related_task_id,
            "evidence": self.evidence,
            "tags": self.tags,
            "extra": self.extra,
            "supersedes_id": self.supersedes_id,
            "superseded_by_id": self.superseded_by_id,
            "is_current": self.is_current,
        }
        return d

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "StateRecord":
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            type=row["type"],
            title=row["title"],
            content=row["content"],
            status=row["status"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
            created_by_agent=row.get("created_by_agent"),
            created_by_session=row.get("created_by_session"),
            related_commit=row.get("related_commit"),
            related_branch=row.get("related_branch"),
            related_task_id=row.get("related_task_id"),
            evidence=_loads(row.get("evidence"), []),
            tags=_loads(row.get("tags"), []),
            extra=_loads(row.get("extra"), {}),
            supersedes_id=row.get("supersedes_id"),
            superseded_by_id=row.get("superseded_by_id"),
        )


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class StateRecordInput:
    """Fields accepted when creating a new state record."""

    project_id: str
    type: str
    title: str
    content: str
    status: Optional[str] = None
    created_by_agent: Optional[str] = None
    created_by_session: Optional[str] = None
    related_commit: Optional[str] = None
    related_branch: Optional[str] = None
    related_task_id: Optional[str] = None
    evidence: Optional[list[str]] = None
    tags: Optional[list[str]] = None
    extra: Optional[dict[str, Any]] = None
    supersedes_id: Optional[str] = None
