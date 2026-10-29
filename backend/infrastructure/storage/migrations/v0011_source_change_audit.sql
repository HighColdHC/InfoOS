CREATE TABLE source_change_audit (
    audit_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'enable', 'disable')),
    actor_kind TEXT NOT NULL CHECK (actor_kind IN ('mcp', 'web')),
    token_id TEXT,
    reason TEXT,
    before_config_version TEXT,
    after_config_version TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_source_change_audit_source ON source_change_audit (source_id, created_at DESC);
CREATE INDEX idx_source_change_audit_created ON source_change_audit (created_at DESC);
