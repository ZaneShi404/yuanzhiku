CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (sha256 TEXT PRIMARY KEY, byte_size BIGINT NOT NULL, stored_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY, source_type TEXT NOT NULL, title TEXT NOT NULL, author TEXT, language TEXT NOT NULL,
  notes TEXT, rights TEXT, categories_json TEXT NOT NULL, tags_json TEXT NOT NULL, processing_state TEXT NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, deleted_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS source_metadata_revisions (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), ordinal INTEGER NOT NULL,
  snapshot_json TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, UNIQUE(source_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_metadata_revisions_source ON source_metadata_revisions(source_id, ordinal DESC);
CREATE TABLE IF NOT EXISTS content_versions (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
  ordinal INTEGER NOT NULL, original_name TEXT NOT NULL, media_type TEXT, completeness TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(source_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_versions_source ON content_versions(source_id);
CREATE TABLE IF NOT EXISTS source_relations (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), related_source_id TEXT NOT NULL REFERENCES sources(id),
  relation_type TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, UNIQUE(source_id, related_source_id, relation_type)
);
CREATE TABLE IF NOT EXISTS representations (
  id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id), kind TEXT NOT NULL,
  parser_name TEXT NOT NULL, config_hash TEXT NOT NULL, parent_representation_id TEXT REFERENCES representations(id),
  text_content TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_representations_version ON representations(content_version_id);
CREATE TABLE IF NOT EXISTS search_chunks (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), content_version_id TEXT NOT NULL REFERENCES content_versions(id),
  representation_id TEXT NOT NULL REFERENCES representations(id), ordinal INTEGER NOT NULL, text_content TEXT NOT NULL,
  text_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, UNIQUE(representation_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_search_chunks_version ON search_chunks(content_version_id);
CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id), artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
  representation_id TEXT NOT NULL REFERENCES representations(id), parser_config_hash TEXT NOT NULL, locator_json TEXT NOT NULL,
  excerpt TEXT NOT NULL, excerpt_hash TEXT NOT NULL, is_validated BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_version ON evidence(content_version_id);
CREATE TABLE IF NOT EXISTS citations (id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence(id), created_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge (id TEXT PRIMARY KEY, kind TEXT NOT NULL, statement TEXT NOT NULL, status TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, published_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS knowledge_evidence (knowledge_id TEXT NOT NULL REFERENCES knowledge(id), evidence_id TEXT NOT NULL REFERENCES evidence(id), PRIMARY KEY(knowledge_id, evidence_id));
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT REFERENCES sources(id), content_version_id TEXT REFERENCES content_versions(id),
  artifact_sha256 TEXT REFERENCES artifacts(sha256), config_hash TEXT, payload_json TEXT NOT NULL, priority INTEGER NOT NULL,
  state TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0, message TEXT, attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 2, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
  heartbeat_at TIMESTAMPTZ, started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, cancel_requested_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(state, priority DESC, created_at ASC);
CREATE TABLE IF NOT EXISTS job_attempts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), attempt_number INTEGER NOT NULL, state TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL, ended_at TIMESTAMPTZ, outcome TEXT);
CREATE TABLE IF NOT EXISTS audit_events (id TEXT PRIMARY KEY, event_type TEXT NOT NULL, entity_id TEXT, result TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS external_cards (id TEXT PRIMARY KEY, card_type TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, author TEXT, notes TEXT, tags_json TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL, UNIQUE(card_type, url));
CREATE TABLE IF NOT EXISTS topics (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS topic_sources (topic_id TEXT NOT NULL REFERENCES topics(id), source_id TEXT NOT NULL REFERENCES sources(id), PRIMARY KEY(topic_id, source_id));
CREATE TABLE IF NOT EXISTS backups (id TEXT PRIMARY KEY, archive_name TEXT NOT NULL UNIQUE, manifest_sha256 TEXT NOT NULL, state TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL);
INSERT INTO schema_migrations(version, applied_at) VALUES (1, NOW()) ON CONFLICT (version) DO NOTHING;
