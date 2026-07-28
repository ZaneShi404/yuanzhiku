BEGIN;
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (sha256 TEXT PRIMARY KEY, byte_size BIGINT NOT NULL, stored_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, source_type TEXT NOT NULL, title TEXT NOT NULL, author TEXT, language TEXT NOT NULL,
  notes TEXT, rights TEXT, categories_json JSONB NOT NULL, tags_json JSONB NOT NULL, processing_state TEXT NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, deleted_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS content_versions (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
  ordinal INTEGER NOT NULL, original_name TEXT NOT NULL, media_type TEXT, completeness TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(source_id, ordinal)
);
CREATE TABLE IF NOT EXISTS representations (
  id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id), kind TEXT NOT NULL,
  parser_name TEXT NOT NULL, config_hash TEXT NOT NULL, parent_representation_id TEXT REFERENCES representations(id),
  text_content TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id), artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
  representation_id TEXT NOT NULL REFERENCES representations(id), parser_config_hash TEXT NOT NULL, locator_json JSONB NOT NULL,
  excerpt TEXT NOT NULL, excerpt_hash TEXT NOT NULL, is_validated BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT REFERENCES sources(id), content_version_id TEXT REFERENCES content_versions(id),
  artifact_sha256 TEXT REFERENCES artifacts(sha256), config_hash TEXT, payload_json JSONB NOT NULL, priority INTEGER NOT NULL,
  state TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0, message TEXT, attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 2, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  heartbeat_at TIMESTAMPTZ, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, cancel_requested_at TIMESTAMPTZ
);
INSERT INTO schema_migrations(version, applied_at) VALUES (1, NOW()) ON CONFLICT (version) DO NOTHING;
COMMIT;
