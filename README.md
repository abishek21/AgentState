# AgentState

> Git remembers the code. AgentState remembers why the project is the way it is.

AgentState is a small local/developer tool that lets coding agents (VS Code
Copilot, Claude Code, agents on a remote GPU box, etc.) share **structured
project understanding** — findings, decisions, experiments, tasks, issues,
hypotheses — across sessions and machines, through an MCP server + CLI +
a shared backend.

It is **not** a chat-history store or RAG-over-transcripts system. It
deliberately distinguishes raw conversation from a small set of
structured, provenanced state records, and it tracks which records are
*current* vs. *superseded* so agents don't act on stale decisions.

## 1. Architecture

```
 Agent A (laptop, Copilot)          Agent B (remote GPU, Claude Code)
        │  MCP (stdio)                       │  MCP (stdio)
        ▼                                    ▼
 agentstate-mcp (local process)      agentstate-mcp (local process)
        │                                    │
        └───────────────┬────────────────────┘
                         ▼
              shared backend (PostgreSQL,
              or SQLite for single-machine dev)
```

* Each coding agent spawns its **own local MCP server process**
  (`agentstate-mcp`), the standard MCP integration model — there is no
  bespoke VS-Code-specific extension and no custom network protocol.
* All local server processes for a project point at the **same database**
  via `AGENTSTATE_DATABASE_URL`. That's what makes laptop ↔ remote-GPU
  state sharing work: the database is the source of truth, not any one
  agent's memory.
* `agentstate` (CLI) and `agentstate-mcp` (MCP server) share the same
  `StateStore` / `Engine` code — the CLI is just another client of the
  same backend, useful for humans (`agentstate status`) and CI.

### Repository structure

```
src/agentstate/
  models.py          typed state schema (StateRecord, RecordType, ...)
  storage/
    base.py          StateStore abstract interface
    sql_store.py      shared SQL implementation (dialect-driven)
    sqlite_store.py    SQLite connection (dev / single machine)
    postgres_store.py  PostgreSQL connection (shared / cross-machine)
  db/
    migrations_runner.py   tiny dependency-free migration runner
    migrations/sqlite/*.sql
    migrations/postgres/*.sql
  identity.py        project identity (git remote fingerprint, .agentstate/config.json)
  config.py          resolves DATABASE_URL from env / local config
  engine.py          get_context / get_project_state / search / diff logic
  mcp_server.py       MCP tool definitions (FastMCP)
  cli.py             `agentstate init|status|log|diff`
tests/               storage + supersede + identity tests
docker-compose.yml   local PostgreSQL for shared/dev use
.env.example
```

## 2. State schema

Every record is one of six types (`RecordType`): `finding`, `decision`,
`experiment`, `task`, `issue`, `hypothesis`. All types share one table
(`state_records`) with the same provenance fields, which keeps queries
(`search`, `get_current_state`, `get_history`) uniform while still being
structured (not one blob of text):

| field | meaning |
|---|---|
| `id` | uuid |
| `project_id` | which AgentState project this belongs to |
| `type` | finding / decision / experiment / task / issue / hypothesis |
| `title`, `content` | human-readable summary + detail |
| `status` | e.g. `active`, `open`, `in_progress`, `done`, `superseded`, `resolved` |
| `created_at`, `updated_at` | timestamps |
| `created_by_agent`, `created_by_session` | which agent/session recorded it |
| `related_commit`, `related_branch` | git state at time of recording |
| `related_task_id` | link to the task this record relates to |
| `evidence` | list of references (experiment ids, urls, ...) |
| `tags` | free-form labels used for retrieval |
| `extra` | type-specific structured payload (e.g. experiment config/result/environment) |
| `supersedes_id` / `superseded_by_id` | belief-revision links |

**Currency model:** a record `is_current` iff its `status` is not one of
`{superseded, resolved, done, closed, rejected}` **and** it has no
`superseded_by_id`. `StateStore.supersede(old_id, new_record)` atomically
creates the new record and marks the old one `superseded`, linked both
ways — so the original decision remains available via `get_history()`,
but `get_current_state()`/`get_context()` will only surface the latest
one. This directly implements the Monday/Tuesday `batch_size` example
from the design brief (see `tests/test_supersede.py`).

## 3. MCP tools

Exposed by `agentstate-mcp` (see `src/agentstate/mcp_server.py`):

| tool | purpose |
|---|---|
| `get_context(task, limit=10)` | compact, task-relevant state (not a full dump) |
| `get_project_state(limit_per_type=10)` | current understanding, grouped by type |
| `search_state(query, types=None, limit=20)` | keyword search over all history |
| `record_finding(title, content, evidence?, tags?, ...)` | record something learned |
| `record_decision(title, content, supersedes_id?, ...)` | record a decision, optionally superseding a previous one |
| `record_experiment(title, config, result, environment?, ...)` | record an experiment |
| `record_issue(title, content, ...)` | record a blocking problem |
| `record_hypothesis(title, content, ...)` | record an untested hypothesis |
| `create_task(title, content, ...)` | create outstanding work |
| `update_task(task_id, status, note?)` | update task status/result |

All record-creating tools accept `related_commit`/`related_branch`
(auto-filled from the local git repo if omitted), `related_task_id`,
`evidence`, and `tags`.

## 4. Project identity

Two clones of the same repository must resolve to the same AgentState
project **without relying on filesystem path**. Resolution, in order:

1. `AGENTSTATE_PROJECT_ID` env var (explicit join).
2. `.agentstate/config.json` in the repo (written by `agentstate init`).
3. A **normalized git remote URL fingerprint** (`identity.normalize_remote_url`),
   looked up in the `projects` table of the shared database; if no
   project exists yet for that fingerprint, one is created.

This means: run `agentstate init` on the laptop, then run
`agentstate init --database-url <same shared DB>` on the GPU clone of the
same repo — it will find the existing project via the git remote
fingerprint and join it automatically, no manual project id copying
required. `agentstate init` also prints an MCP config snippet with the
resolved `AGENTSTATE_PROJECT_ID` and `AGENTSTATE_DATABASE_URL` to paste
into the client's MCP configuration.

## 5. Storage

`StateStore` (in `storage/base.py`) is the abstraction business logic
depends on: `create`, `update`, `get`, `supersede`, `search`,
`get_current_state`, `get_history`. Two backends implement it:

* **SQLite** (`storage/sqlite_store.py`) — zero-setup, single machine,
  used by default (`.agentstate/state.db`) when no shared database is
  configured.
* **PostgreSQL** (`storage/postgres_store.py`, optional `psycopg`
  dependency) — the real MVP shared backend for laptop ↔ GPU workflows.
  Search uses PostgreSQL's built-in full-text search
  (`to_tsvector`/`plainto_tsquery`) — **no vector database** is
  introduced; `pgvector` is the documented upgrade path if semantic
  retrieval later proves necessary.

Both share one query implementation (`storage/sql_store.py`) via a small
`Dialect` adapter (placeholder style, full-text search clause), so
backend-specific code is minimal and the two behave identically.

Schema changes are tracked as plain, ordered `.sql` files per dialect
under `db/migrations/{sqlite,postgres}/`, applied by a tiny
dependency-free runner that records applied versions in a
`schema_migrations` table (`db/migrations_runner.py`).

## 6. Authentication (MVP)

For the MVP, the MCP server runs **locally** per agent/client (the
standard MCP model) and connects directly to the shared database using
the database's own credentials (`AGENTSTATE_DATABASE_URL`, e.g. a
PostgreSQL user/password). Access control = database access control.
This is intentionally simple and suitable for personal/small-team use;
it is *not* a multi-tenant authorization system. A proper per-agent
token/authorization layer is future work if AgentState grows beyond a
single user/team sharing one database.

## 7. CLI

```bash
agentstate init [--database-url URL] [--project-id ID] [--name NAME]
agentstate status [--limit N]
agentstate log [--since 1d|ISO] [--type finding] [--limit N]
agentstate diff --since 1d|ISO [--until ISO]
```

* `init` — resolve/create the project identity for this repo and write
  `.agentstate/config.json`.
* `status` — current state (open tasks, active decisions, recent
  findings/experiments), excluding superseded/done records.
* `log` — chronological history of all recorded state (current +
  superseded), like `git log` for project understanding.
* `diff` — new records, superseded records, and revised decisions between
  two points in time.

## 8. Getting started

### Local single-machine mode (SQLite, zero setup)

```bash
pip install -e .              # from this repo (not yet published to PyPI)
cd your-project
agentstate init                # creates .agentstate/config.json + .agentstate/state.db
agentstate status
```

### Shared mode (laptop ↔ remote GPU via PostgreSQL)

```bash
# once, on any machine that can reach the shared Postgres instance:
docker compose up -d postgres

# on the laptop, inside the target repo (fill in real host/credentials):
export AGENTSTATE_DATABASE_URL=postgresql://HOST:5432/agentstate
agentstate init --name my-project

# on the GPU box, a separate clone of the SAME repo (same git remote):
export AGENTSTATE_DATABASE_URL=postgresql://HOST:5432/agentstate
agentstate init   # joins the same project automatically via remote-URL fingerprint
```

Then register the MCP server with each coding agent (the exact
configuration file differs by client — VS Code Copilot's `mcp.json`,
Claude Code's `.mcp.json`, etc. — but the shape is the same):

```json
{
  "agentstate": {
    "command": "agentstate-mcp",
    "env": {
      "AGENTSTATE_DATABASE_URL": "postgresql://HOST:5432/agentstate",
      "AGENTSTATE_PROJECT_ID": "<id printed by agentstate init>",
      "AGENTSTATE_AGENT": "copilot-laptop"
    }
  }
}
```

`agentstate init` prints this snippet (with the resolved values filled
in) at the end of its output.

### End-to-end example (matches the workflow in the design brief)

```text
# Session A (laptop, agent calls MCP tools during normal work):
record_finding(title="baseline throughput", content="142 tok/s", tags=["perf"])
record_finding(title="attention share", content="~31% of runtime", tags=["attention"])
record_decision(title="investigate FlashAttention", content="...")
create_task(title="Optimize attention", content="...")

# Session B (remote GPU, different agent, same project):
get_context("Continue the attention optimization")
  -> returns the open task + the FlashAttention decision + the two findings
     (not the full chat transcript)
record_experiment(title="FlashAttention swap",
                  config={"attention_impl": "flash_attention_2"},
                  result={"throughput_tok_s": 171, "peak_hbm_gb": 18.2})
update_task(task_id, status="done", note="171 tok/s, peak HBM decreased")

# Session C (laptop again):
search_state("attention")
  -> shows the GPU experiment result and the completed task, with provenance
     (created_by_agent="claude-gpu")
```

This exact flow (`get_context` → record → `search_state`/`status`) was
exercised manually against both the SQLite and PostgreSQL backends
during development; `tests/` covers the storage/supersede/identity logic
that it depends on.

## 9. Assumptions status

See the design brief for the full list; status after implementing the
vertical slice:

* **Confirmed** — MCP servers are spawned locally per client via stdio
  and configured with `command`/`env`, so both local and remote
  environments can run one pointed at a shared database (assumptions 1, 3, 12).
* **Confirmed** — structured records with explicit currency
  (`is_current`) are sufficient to answer "what should I do next" without
  raw transcripts, validated by the manual end-to-end run above
  (assumption 6).
* **Confirmed** — git remote URL + local config is enough to resolve
  project identity across clones without a central path/user directory
  (assumption 5 partially, 4 fully for the DB-credential-sharing case).
* **Partially confirmed / depends on agent behavior** — whether a given
  coding agent *reliably* calls the record_* tools during normal work
  (assumption 2, 7, 9) is a product behavior outside this repo's control;
  MCP tool descriptions are written to prompt correct usage, but this
  needs real dogfooding to confirm.
* **Unknown / needs real usage** — whether developers actually stop
  maintaining `HANDOFF.md` after using this (assumption 8, 10) can only
  be answered by the 20-agent-switch test described in the brief.

## 10. Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,postgres]"
pytest
```

`tests/` covers: storage CRUD, the supersede/currency state-transition
model (including chained supersession), and project-identity fingerprint
normalization across HTTPS/SSH remote URL forms.
