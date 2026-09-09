"""High-level project-state operations shared by the MCP server and CLI.

This is where "compact retrieval" and "current vs. superseded" behaviour
live, on top of the plain ``StateStore`` CRUD interface.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from agentstate.models import RecordType, StateRecord, StateRecordInput
from agentstate.storage.base import StateStore

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text or "")}


def _relevance(query_tokens: set[str], record: StateRecord) -> float:
    text_tokens = _tokens(record.title) | _tokens(record.content) | {t.lower() for t in record.tags}
    if not query_tokens:
        overlap = 0
    else:
        overlap = len(query_tokens & text_tokens)
    score = float(overlap)
    if record.is_current:
        score += 0.5
    return score


@dataclass
class Engine:
    store: StateStore

    # -- recording -----------------------------------------------------
    def record(self, record: StateRecordInput) -> StateRecord:
        return self.store.create(record)

    def record_decision(
        self, project_id: str, title: str, content: str,
        supersedes_id: Optional[str] = None, **kwargs: Any,
    ) -> StateRecord:
        payload = StateRecordInput(
            project_id=project_id, type=RecordType.DECISION.value,
            title=title, content=content, **kwargs,
        )
        if supersedes_id:
            _old, new = self.store.supersede(supersedes_id, payload)
            return new
        return self.store.create(payload)

    def update_status(self, record_id: str, status: str, note: Optional[str] = None) -> StateRecord:
        fields: dict[str, Any] = {"status": status}
        if note:
            existing = self.store.get(record_id)
            fields["content"] = (existing.content + f"\n\n[update] {note}") if existing else note
        return self.store.update(record_id, **fields)

    # -- retrieval -------------------------------------------------------
    def get_project_state(self, project_id: str, limit_per_type: int = 10) -> dict[str, list[dict]]:
        """Compact snapshot of the *current* understanding of the project."""
        current = self.store.get_current_state(project_id, limit=500)
        by_type: dict[str, list[StateRecord]] = {}
        for rec in current:
            by_type.setdefault(rec.type, []).append(rec)
        return {
            t: [r.to_dict() for r in recs[:limit_per_type]]
            for t, recs in by_type.items()
        }

    def get_context(self, project_id: str, task: str, limit: int = 10) -> dict[str, Any]:
        """Compact, task-relevant subset of project state (not a full dump).

        Ranks current (non-superseded) records by keyword overlap with the
        task description plus recency/currency, and returns the top
        ``limit`` overall plus the open tasks (which are always relevant).
        """
        current = self.store.get_current_state(project_id, limit=500)
        query_tokens = _tokens(task)
        scored = sorted(
            current, key=lambda r: (_relevance(query_tokens, r), r.created_at), reverse=True,
        )
        open_tasks = [r for r in current if r.type == RecordType.TASK.value and r.status == "open"]
        top = scored[:limit]
        # Always surface open tasks even if keyword overlap is low.
        ids_in_top = {r.id for r in top}
        for t in open_tasks:
            if t.id not in ids_in_top and len(top) < limit + len(open_tasks):
                top.append(t)
        return {
            "task": task,
            "open_tasks": [t.to_dict() for t in open_tasks],
            "relevant_state": [r.to_dict() for r in top],
        }

    def search_state(
        self, project_id: str, query: str, types: Optional[list[str]] = None, limit: int = 20,
    ) -> list[dict]:
        results = self.store.search(project_id, query, types=types, limit=limit)
        return [r.to_dict() for r in results]

    def get_history(
        self, project_id: str, since: Optional[str] = None, until: Optional[str] = None,
        types: Optional[list[str]] = None, limit: int = 200,
    ) -> list[dict]:
        return [r.to_dict() for r in self.store.get_history(project_id, since=since, until=until, types=types, limit=limit)]

    def diff(self, project_id: str, since: str, until: Optional[str] = None) -> dict[str, Any]:
        """Meaningful changes in project understanding between two points in time."""
        history = self.store.get_history(project_id, since=since, until=until, limit=1000)
        added = [r for r in history if r.supersedes_id is None]
        superseded = [r for r in history if r.status == "superseded"]
        changed_decisions = [r for r in history if r.supersedes_id is not None]
        return {
            "since": since,
            "until": until,
            "new_records": [r.to_dict() for r in added],
            "superseded_records": [r.to_dict() for r in superseded],
            "revisions": [r.to_dict() for r in changed_decisions],
        }
