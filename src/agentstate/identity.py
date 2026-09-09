"""Project identity resolution.

Two clones of the same Git repository must resolve to the *same*
AgentState project without relying on local filesystem paths. We use,
in order of precedence:

1. An explicit ``AGENTSTATE_PROJECT_ID`` env var or ``--project-id`` flag.
2. The local ``.agentstate/config.json`` written by ``agentstate init``.
3. A fingerprint derived from the repository's git remote URL, looked up
   in (and if necessary created in) the shared store.

The remote URL fingerprint is what lets a fresh clone on another machine
join the same project automatically, without copying any local config.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

CONFIG_DIRNAME = ".agentstate"
CONFIG_FILENAME = "config.json"


@dataclass
class RepoConfig:
    project_id: str
    remote_fingerprint: Optional[str]
    database_url: Optional[str]
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "project_id": self.project_id,
            "remote_fingerprint": self.remote_fingerprint,
            "database_url": self.database_url,
            "created_at": self.created_at,
        }


def config_path(repo_root: str) -> str:
    return os.path.join(repo_root, CONFIG_DIRNAME, CONFIG_FILENAME)


def find_repo_root(start: Optional[str] = None) -> str:
    """Return the git repo root, or ``start``/cwd if not inside a git repo."""
    cwd = start or os.getcwd()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return cwd


def get_remote_url(repo_root: str, remote: str = "origin") -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", remote],
            cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            url = out.stdout.strip()
            return url or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def get_current_commit(repo_root: str) -> Optional[str]:
    return _git(repo_root, ["rev-parse", "HEAD"])


def get_current_branch(repo_root: str) -> Optional[str]:
    return _git(repo_root, ["rev-parse", "--abbrev-ref", "HEAD"])


def _git(repo_root: str, args: list[str]) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            value = out.stdout.strip()
            return value or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def normalize_remote_url(url: str) -> str:
    """Normalize a git remote URL into a stable fingerprint.

    Handles both SSH (``git@github.com:org/repo.git``) and HTTPS
    (``https://user@github.com/org/repo.git``) forms, strips credentials,
    protocol, trailing ``.git``, and lowercases the host, so the same
    remote produces the same fingerprint regardless of the clone method.
    """
    u = url.strip()
    u = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", u)  # strip scheme
    u = re.sub(r"^[^@/]+@", "", u)  # strip user@ / token@
    u = u.replace(":", "/", 1) if re.match(r"^[^/]+:[^/]", u) else u  # ssh host:path -> host/path
    u = u.rstrip("/")
    if u.endswith(".git"):
        u = u[: -len(".git")]
    host, _, path = u.partition("/")
    return f"{host.lower()}/{path}"


def load_config(repo_root: str) -> Optional[RepoConfig]:
    path = config_path(repo_root)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return RepoConfig(
        project_id=data["project_id"],
        remote_fingerprint=data.get("remote_fingerprint"),
        database_url=data.get("database_url"),
        created_at=data.get("created_at"),
    )


def save_config(repo_root: str, config: RepoConfig) -> str:
    directory = os.path.join(repo_root, CONFIG_DIRNAME)
    os.makedirs(directory, exist_ok=True)
    path = config_path(repo_root)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config.to_dict(), f, indent=2)
        f.write("\n")
    return path


def resolve_project_id(
    repo_root: Optional[str] = None,
    explicit_project_id: Optional[str] = None,
) -> Optional[str]:
    """Best-effort project id resolution for MCP tool calls.

    Order: explicit arg > ``AGENTSTATE_PROJECT_ID`` env > local config file.
    Returns ``None`` if nothing is resolvable (caller should error clearly).
    """
    if explicit_project_id:
        return explicit_project_id
    env_id = os.environ.get("AGENTSTATE_PROJECT_ID")
    if env_id:
        return env_id
    root = repo_root or find_repo_root()
    cfg = load_config(root)
    return cfg.project_id if cfg else None
