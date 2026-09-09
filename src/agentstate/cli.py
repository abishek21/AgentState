"""AgentState CLI.

    agentstate init      -- connect this repo to an AgentState project
    agentstate status    -- current project state, recent activity
    agentstate log       -- chronological state changes
    agentstate diff      -- meaningful changes in understanding over time
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import click

from agentstate.config import open_store, resolve_database_url
from agentstate.engine import Engine
from agentstate.identity import (
    RepoConfig,
    find_repo_root,
    get_remote_url,
    load_config,
    normalize_remote_url,
    save_config,
)
from agentstate.models import RecordType


def _get_engine(repo_root: str) -> Engine:
    db_url = resolve_database_url(repo_root)
    return Engine(store=open_store(db_url))


def _require_project(repo_root: str) -> str:
    cfg = load_config(repo_root)
    if not cfg:
        click.echo(
            "This repository is not connected to AgentState yet. Run `agentstate init` first.",
            err=True,
        )
        sys.exit(1)
    return cfg.project_id


def _parse_since(value: str) -> str:
    """Accept an ISO timestamp or a simple duration like '1d', '3h', '30m'."""
    import re

    m = re.fullmatch(r"(\d+)([dhm])", value.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {"d": timedelta(days=n), "h": timedelta(hours=n), "m": timedelta(minutes=n)}[unit]
        return (datetime.now(timezone.utc) - delta).isoformat()
    return value


@click.group()
def cli() -> None:
    """AgentState: git remembers the code, AgentState remembers why."""


@cli.command()
@click.option("--database-url", default=None, help="Shared backend, e.g. postgresql://... (defaults to local sqlite).")
@click.option("--project-id", default=None, help="Join an existing project by id instead of auto-detecting.")
@click.option("--name", default=None, help="Human-friendly project name.")
@click.option("--remote", default="origin", help="Git remote to derive the project fingerprint from.")
def init(database_url: Optional[str], project_id: Optional[str], name: Optional[str], remote: str) -> None:
    """Initialize/connect this repository to an AgentState project.

    Resolves project identity from the git remote URL so a second clone
    (e.g. on a remote GPU machine) pointed at the same database URL joins
    the same project automatically.
    """
    repo_root = find_repo_root()
    remote_url = get_remote_url(repo_root, remote)
    fingerprint = normalize_remote_url(remote_url) if remote_url else None
    if not fingerprint:
        click.echo(
            f"Warning: no git remote '{remote}' found; project identity will rely on "
            "the explicit project id or local config only.",
            err=True,
        )

    db_url = database_url or resolve_database_url(repo_root)
    store = open_store(db_url)
    try:
        project = store.get_or_create_project(fingerprint, name=name, project_id=project_id)
    finally:
        store.close()

    config = RepoConfig(
        project_id=project.id,
        remote_fingerprint=fingerprint,
        database_url=db_url,
        created_at=project.created_at.isoformat(),
    )
    path = save_config(repo_root, config)
    click.echo(f"Connected repository to AgentState project '{project.id}'.")
    click.echo(f"Config written to {path}")
    click.echo(f"Database: {db_url}")
    if fingerprint:
        click.echo(f"Remote fingerprint: {fingerprint}")
    click.echo(
        "\nAdd an MCP server entry pointing coding agents at this project, e.g.:\n"
        + json.dumps(
            {
                "agentstate": {
                    "command": "agentstate-mcp",
                    "env": {
                        "AGENTSTATE_DATABASE_URL": db_url,
                        "AGENTSTATE_PROJECT_ID": project.id,
                        "AGENTSTATE_AGENT": "<set per client, e.g. copilot / claude-code>",
                    },
                }
            },
            indent=2,
        )
    )


@cli.command()
@click.option("--limit", default=5, help="Max records to show per type.")
def status(limit: int) -> None:
    """Show current project state: active decisions, open tasks, recent findings."""
    repo_root = find_repo_root()
    project_id = _require_project(repo_root)
    engine = _get_engine(repo_root)
    try:
        state = engine.get_project_state(project_id, limit_per_type=limit)
    finally:
        engine.store.close()

    if not state:
        click.echo("No project state recorded yet.")
        return
    for record_type in [t.value for t in RecordType]:
        records = state.get(record_type, [])
        if not records:
            continue
        click.echo(f"\n== {record_type.upper()} ({len(records)}) ==")
        for r in records:
            click.echo(f"  [{r['id'][:8]}] {r['title']}  (status={r['status']})")
            if r.get("related_commit"):
                click.echo(f"      commit: {r['related_commit'][:12]}")


@cli.command(name="log")
@click.option("--since", default=None, help="ISO timestamp or duration like '1d', '3h'.")
@click.option("--type", "types", multiple=True, help="Filter by record type; can repeat.")
@click.option("--limit", default=50)
def log_cmd(since: Optional[str], types: tuple[str, ...], limit: int) -> None:
    """Show chronological state changes (like `git log` for project understanding)."""
    repo_root = find_repo_root()
    project_id = _require_project(repo_root)
    engine = _get_engine(repo_root)
    try:
        since_iso = _parse_since(since) if since else None
        history = engine.get_history(project_id, since=since_iso, types=list(types) or None, limit=limit)
    finally:
        engine.store.close()

    if not history:
        click.echo("No state recorded yet.")
        return
    for r in history:
        marker = "superseded" if r["status"] == "superseded" else r["status"]
        click.echo(f"{r['created_at']}  [{r['type']}] {r['title']}  ({marker})")


@cli.command()
@click.option("--since", required=True, help="ISO timestamp or duration like '1d', '3h'.")
@click.option("--until", default=None, help="ISO timestamp (defaults to now).")
def diff(since: str, until: Optional[str]) -> None:
    """Show meaningful changes in project understanding between two points in time."""
    repo_root = find_repo_root()
    project_id = _require_project(repo_root)
    engine = _get_engine(repo_root)
    try:
        result = engine.diff(project_id, since=_parse_since(since), until=until)
    finally:
        engine.store.close()

    click.echo(f"Changes since {result['since']}:")
    if result["revisions"]:
        click.echo("\nRevised decisions/state:")
        for r in result["revisions"]:
            click.echo(f"  - superseded {r['supersedes_id'][:8]} -> + {r['title']} ({r['id'][:8]})")
    new_only = [r for r in result["new_records"] if not r["supersedes_id"]]
    if new_only:
        click.echo("\nNew records:")
        for r in new_only:
            click.echo(f"  + [{r['type']}] {r['title']}")
    if result["superseded_records"]:
        click.echo("\nSuperseded in this window:")
        for r in result["superseded_records"]:
            click.echo(f"  - [{r['type']}] {r['title']}")
    if not result["revisions"] and not new_only and not result["superseded_records"]:
        click.echo("  (no changes)")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
