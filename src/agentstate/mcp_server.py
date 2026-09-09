"""AgentState MCP server.

Runs as a local stdio process spawned by the coding agent (VS Code
Copilot, Claude Code, etc.) per the standard MCP integration model: each
client spawns its own local server, pointed at a shared backend via
``AGENTSTATE_DATABASE_URL``. This is what lets a laptop client and a
remote GPU client read/write the *same* project state.

Provenance (which agent/session recorded what) comes from environment
variables set in the client's MCP server config:

* ``AGENTSTATE_PROJECT_ID`` / ``.agentstate/config.json`` — which project.
* ``AGENTSTATE_AGENT`` — a label like ``"copilot"`` or ``"claude-code"``.
* ``AGENTSTATE_SESSION_ID`` — a per-session identifier (auto-generated if unset).
* ``AGENTSTATE_DATABASE_URL`` — shared backend connection string.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from agentstate.config import open_store, resolve_database_url
from agentstate.engine import Engine
from agentstate.identity import (
    find_repo_root,
    get_current_branch,
    get_current_commit,
    resolve_project_id,
)
from agentstate.models import RecordType, StateRecordInput

logging.basicConfig(level=os.environ.get("AGENTSTATE_LOG_LEVEL", "INFO"))
logger = logging.getLogger("agentstate.mcp")

mcp = FastMCP("agentstate")

_repo_root = find_repo_root()
_session_id = os.environ.get("AGENTSTATE_SESSION_ID") or uuid.uuid4().hex[:12]
_agent_name = os.environ.get("AGENTSTATE_AGENT", "unknown-agent")


def _engine() -> Engine:
    db_url = resolve_database_url(_repo_root)
    store = open_store(db_url)
    return Engine(store=store)


def _project_id() -> str:
    pid = resolve_project_id(_repo_root)
    if not pid:
        raise RuntimeError(
            "No AgentState project configured for this repository. "
            "Run `agentstate init` first (or set AGENTSTATE_PROJECT_ID)."
        )
    return pid


def _base_fields(related_commit: Optional[str], related_branch: Optional[str]) -> dict[str, Any]:
    return {
        "created_by_agent": _agent_name,
        "created_by_session": _session_id,
        "related_commit": related_commit or get_current_commit(_repo_root),
        "related_branch": related_branch or get_current_branch(_repo_root),
    }


@mcp.tool()
def get_context(task: str, limit: int = 10) -> dict:
    """Get a compact set of project state relevant to the current task.

    Use this at the start of a task (or when picking up someone else's
    work) instead of asking the user to re-explain context.
    """
    engine = _engine()
    try:
        return engine.get_context(_project_id(), task, limit=limit)
    finally:
        engine.store.close()


@mcp.tool()
def get_project_state(limit_per_type: int = 10) -> dict:
    """Get a compact snapshot of the project's current understanding,
    grouped by record type. Superseded/resolved/done records are excluded.
    """
    engine = _engine()
    try:
        return engine.get_project_state(_project_id(), limit_per_type=limit_per_type)
    finally:
        engine.store.close()


@mcp.tool()
def search_state(query: str, types: Optional[list[str]] = None, limit: int = 20) -> list[dict]:
    """Search all historical project state (current and superseded) by keyword."""
    engine = _engine()
    try:
        return engine.search_state(_project_id(), query, types=types, limit=limit)
    finally:
        engine.store.close()


@mcp.tool()
def record_finding(
    title: str,
    content: str,
    evidence: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    related_task_id: Optional[str] = None,
    related_commit: Optional[str] = None,
    related_branch: Optional[str] = None,
) -> dict:
    """Record something learned about the project (e.g. a measurement,
    a root cause, a benchmark result)."""
    engine = _engine()
    try:
        rec = engine.record(StateRecordInput(
            project_id=_project_id(), type=RecordType.FINDING.value,
            title=title, content=content, evidence=evidence, tags=tags,
            related_task_id=related_task_id,
            **_base_fields(related_commit, related_branch),
        ))
        return rec.to_dict()
    finally:
        engine.store.close()


@mcp.tool()
def record_decision(
    title: str,
    content: str,
    supersedes_id: Optional[str] = None,
    evidence: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    related_task_id: Optional[str] = None,
    related_commit: Optional[str] = None,
    related_branch: Optional[str] = None,
) -> dict:
    """Record a decision. If this replaces an earlier decision, pass its
    id as `supersedes_id` so the old one is marked superseded rather than
    both appearing as current."""
    engine = _engine()
    try:
        rec = engine.record_decision(
            _project_id(), title, content, supersedes_id=supersedes_id,
            evidence=evidence, tags=tags, related_task_id=related_task_id,
            **_base_fields(related_commit, related_branch),
        )
        return rec.to_dict()
    finally:
        engine.store.close()


@mcp.tool()
def record_experiment(
    title: str,
    config: dict,
    result: dict,
    environment: Optional[dict] = None,
    evidence: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    related_task_id: Optional[str] = None,
    related_commit: Optional[str] = None,
    related_branch: Optional[str] = None,
) -> dict:
    """Record an experiment: its configuration, result, and (optionally)
    the environment it ran in. `config`/`result`/`environment` are free-form
    JSON objects."""
    engine = _engine()
    try:
        content = f"config={config} result={result}"
        rec = engine.record(StateRecordInput(
            project_id=_project_id(), type=RecordType.EXPERIMENT.value,
            title=title, content=content, evidence=evidence, tags=tags,
            related_task_id=related_task_id,
            extra={"config": config, "result": result, "environment": environment or {}},
            **_base_fields(related_commit, related_branch),
        ))
        return rec.to_dict()
    finally:
        engine.store.close()


@mcp.tool()
def record_issue(
    title: str,
    content: str,
    evidence: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    related_task_id: Optional[str] = None,
    related_commit: Optional[str] = None,
    related_branch: Optional[str] = None,
) -> dict:
    """Record an open problem/issue blocking progress."""
    engine = _engine()
    try:
        rec = engine.record(StateRecordInput(
            project_id=_project_id(), type=RecordType.ISSUE.value,
            title=title, content=content, evidence=evidence, tags=tags,
            related_task_id=related_task_id,
            **_base_fields(related_commit, related_branch),
        ))
        return rec.to_dict()
    finally:
        engine.store.close()


@mcp.tool()
def record_hypothesis(
    title: str,
    content: str,
    evidence: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    related_task_id: Optional[str] = None,
    related_commit: Optional[str] = None,
    related_branch: Optional[str] = None,
) -> dict:
    """Record an untested hypothesis worth investigating later."""
    engine = _engine()
    try:
        rec = engine.record(StateRecordInput(
            project_id=_project_id(), type=RecordType.HYPOTHESIS.value,
            title=title, content=content, evidence=evidence, tags=tags,
            related_task_id=related_task_id,
            **_base_fields(related_commit, related_branch),
        ))
        return rec.to_dict()
    finally:
        engine.store.close()


@mcp.tool()
def create_task(
    title: str,
    content: str,
    tags: Optional[list[str]] = None,
    related_commit: Optional[str] = None,
    related_branch: Optional[str] = None,
) -> dict:
    """Create a task representing outstanding work."""
    engine = _engine()
    try:
        rec = engine.record(StateRecordInput(
            project_id=_project_id(), type=RecordType.TASK.value,
            title=title, content=content, tags=tags,
            **_base_fields(related_commit, related_branch),
        ))
        return rec.to_dict()
    finally:
        engine.store.close()


@mcp.tool()
def update_task(task_id: str, status: str, note: Optional[str] = None) -> dict:
    """Update a task's status (e.g. 'in_progress', 'done', 'blocked') and
    optionally append a note describing the result."""
    engine = _engine()
    try:
        rec = engine.update_status(task_id, status, note=note)
        return rec.to_dict()
    finally:
        engine.store.close()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
