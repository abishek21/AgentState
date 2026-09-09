-- AgentState initial schema (SQLite dialect)

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT,
    remote_fingerprint TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_records (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by_agent TEXT,
    created_by_session TEXT,
    related_commit TEXT,
    related_branch TEXT,
    related_task_id TEXT REFERENCES state_records(id),
    evidence TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    extra TEXT NOT NULL DEFAULT '{}',
    supersedes_id TEXT REFERENCES state_records(id),
    superseded_by_id TEXT REFERENCES state_records(id)
);

CREATE INDEX IF NOT EXISTS idx_state_records_project ON state_records(project_id);
CREATE INDEX IF NOT EXISTS idx_state_records_type ON state_records(project_id, type);
CREATE INDEX IF NOT EXISTS idx_state_records_status ON state_records(project_id, status);
CREATE INDEX IF NOT EXISTS idx_state_records_created_at ON state_records(project_id, created_at);
