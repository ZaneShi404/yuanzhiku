"""SQLite adapter. SQL is kept at this boundary, not in domain services."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit


def now() -> str:
    return datetime.now(UTC).isoformat()


def identifier() -> str:
    return uuid.uuid4().hex


def redact_url_userinfo(value: str) -> str:
    """Return a URL without userinfo for legacy records and portable exports."""
    parsed = urlsplit(value)
    if not (parsed.username or parsed.password):
        return value
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (
    sha256 TEXT PRIMARY KEY, byte_size INTEGER NOT NULL, stored_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, source_type TEXT NOT NULL, title TEXT NOT NULL, author TEXT,
    language TEXT NOT NULL, notes TEXT, rights TEXT, categories_json TEXT NOT NULL,
    tags_json TEXT NOT NULL, processing_state TEXT NOT NULL, imported_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS source_metadata_revisions (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), ordinal INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(source_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_metadata_revisions_source ON source_metadata_revisions(source_id, ordinal DESC);
CREATE TABLE IF NOT EXISTS content_versions (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
    ordinal INTEGER NOT NULL, original_name TEXT NOT NULL, media_type TEXT, completeness TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(source_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_versions_source ON content_versions(source_id);
CREATE TABLE IF NOT EXISTS source_relations (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), related_source_id TEXT NOT NULL REFERENCES sources(id),
    relation_type TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(source_id, related_source_id, relation_type)
);
CREATE TABLE IF NOT EXISTS representations (
    id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id), kind TEXT NOT NULL,
    parser_name TEXT NOT NULL, config_hash TEXT NOT NULL, parent_representation_id TEXT REFERENCES representations(id),
    text_content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_representations_version ON representations(content_version_id);
CREATE TABLE IF NOT EXISTS search_chunks (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), content_version_id TEXT NOT NULL REFERENCES content_versions(id),
    representation_id TEXT NOT NULL REFERENCES representations(id), ordinal INTEGER NOT NULL, text_content TEXT NOT NULL,
    text_hash TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(representation_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_search_chunks_version ON search_chunks(content_version_id);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id),
    artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256), representation_id TEXT NOT NULL REFERENCES representations(id),
    parser_config_hash TEXT NOT NULL, locator_json TEXT NOT NULL, excerpt TEXT NOT NULL, excerpt_hash TEXT NOT NULL,
    is_validated INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_version ON evidence(content_version_id);
CREATE TABLE IF NOT EXISTS citations (
    id TEXT PRIMARY KEY, evidence_id TEXT NOT NULL REFERENCES evidence(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, statement TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, published_at TEXT
);
CREATE TABLE IF NOT EXISTS knowledge_evidence (
    knowledge_id TEXT NOT NULL REFERENCES knowledge(id), evidence_id TEXT NOT NULL REFERENCES evidence(id),
    PRIMARY KEY(knowledge_id, evidence_id)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT REFERENCES sources(id), content_version_id TEXT REFERENCES content_versions(id),
    artifact_sha256 TEXT REFERENCES artifacts(sha256), config_hash TEXT, payload_json TEXT NOT NULL, priority INTEGER NOT NULL,
    state TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0, message TEXT, attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 2, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, heartbeat_at TEXT,
    started_at TEXT, completed_at TEXT, cancel_requested_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(state, priority DESC, created_at ASC);
CREATE TABLE IF NOT EXISTS job_attempts (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), attempt_number INTEGER NOT NULL,
    state TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT, outcome TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY, event_type TEXT NOT NULL, entity_id TEXT, result TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS external_cards (
    id TEXT PRIMARY KEY, card_type TEXT NOT NULL, url TEXT NOT NULL, title TEXT NOT NULL, author TEXT,
    notes TEXT, tags_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(card_type, url)
);
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS topic_sources (
    topic_id TEXT NOT NULL REFERENCES topics(id), source_id TEXT NOT NULL REFERENCES sources(id),
    PRIMARY KEY(topic_id, source_id)
);
CREATE TABLE IF NOT EXISTS backups (
    id TEXT PRIMARY KEY, archive_name TEXT NOT NULL UNIQUE, manifest_sha256 TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class SqliteRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.database_path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)", (now(),)
            )
            # Older databases allowed multiple records to point at one archive.
            # Retain the newest record before adding the uniqueness constraint.
            duplicate_names = connection.execute(
                "SELECT archive_name FROM backups GROUP BY archive_name HAVING COUNT(*) > 1"
            ).fetchall()
            for duplicate in duplicate_names:
                rows = connection.execute(
                    "SELECT id FROM backups WHERE archive_name=? ORDER BY created_at DESC, id DESC",
                    (duplicate["archive_name"],),
                ).fetchall()
                for row in rows[1:]:
                    connection.execute("DELETE FROM backups WHERE id=?", (row["id"],))
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_backups_archive_name ON backups(archive_name)")
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(2, ?)", (now(),)
            )
            defaults = {
                "parser_timeout_seconds": "86400",
                "parser_no_progress_seconds": "86400",
                "max_retry_attempts": "2",
                "last_backup_date": "",
            }
            for key, value in defaults.items():
                connection.execute(
                    "INSERT OR IGNORE INTO settings(key, value, updated_at) VALUES(?, ?, ?)", (key, value, now())
                )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def _rows(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        return [dict(row) for row in rows]

    def get_settings(self) -> dict[str, str]:
        with self.connection() as connection:
            return {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM settings")}

    def update_settings(self, values: dict[str, int]) -> dict[str, str]:
        with self.connection() as connection:
            stamp = now()
            for key, value in values.items():
                if value is not None:
                    connection.execute("UPDATE settings SET value=?, updated_at=? WHERE key=?", (str(value), stamp, key))
        return self.get_settings()

    def audit(self, event_type: str, entity_id: str | None, result: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO audit_events(id, event_type, entity_id, result, created_at) VALUES(?, ?, ?, ?, ?)",
                (identifier(), event_type, entity_id, result, now()),
            )

    def create_artifact(self, sha256: str, byte_size: int) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO artifacts(sha256, byte_size, stored_at) VALUES(?, ?, ?)",
                (sha256, byte_size, now()),
            )

    @staticmethod
    def _metadata_snapshot(row: sqlite3.Row) -> dict[str, Any]:
        fields = ("title", "author", "language", "notes", "rights", "categories_json", "tags_json")
        return {field: row[field] for field in fields}

    def _record_metadata_revision(self, connection: sqlite3.Connection, source_id: str, stamp: str) -> None:
        source = connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if source is None:
            return
        ordinal = connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 AS ordinal FROM source_metadata_revisions WHERE source_id=?",
            (source_id,),
        ).fetchone()["ordinal"]
        connection.execute(
            "INSERT INTO source_metadata_revisions(id,source_id,ordinal,snapshot_json,created_at) VALUES(?,?,?,?,?)",
            (identifier(), source_id, ordinal, json.dumps(self._metadata_snapshot(source), ensure_ascii=False), stamp),
        )

    @staticmethod
    def _configured_max_attempts(connection: sqlite3.Connection) -> int:
        row = connection.execute("SELECT value FROM settings WHERE key='max_retry_attempts'").fetchone()
        try:
            return max(0, min(10, int(row["value"]))) if row else 2
        except (TypeError, ValueError):
            return 2

    def create_source_with_version(
        self,
        *, source_type: str, title: str, author: str | None, language: str, notes: str | None,
        rights: str, categories: list[str], tags: list[str], artifact_sha256: str, original_name: str,
        media_type: str | None, byte_size: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        source_id = identifier()
        version_id = identifier()
        stamp = now()
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO artifacts(sha256, byte_size, stored_at) VALUES(?, ?, ?)",
                (artifact_sha256, byte_size, stamp),
            )
            connection.execute(
                """INSERT INTO sources(id,source_type,title,author,language,notes,rights,categories_json,tags_json,processing_state,imported_at,updated_at,deleted_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                (source_id, source_type, title, author, language, notes, rights, json.dumps(categories), json.dumps(tags), "queued", stamp, stamp),
            )
            connection.execute(
                """INSERT INTO content_versions(id,source_id,artifact_sha256,ordinal,original_name,media_type,completeness,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (version_id, source_id, artifact_sha256, 1, original_name, media_type, "pending", stamp),
            )
            self._record_metadata_revision(connection, source_id, stamp)
        return self.get_source(source_id) or {}, self.get_version(version_id) or {}

    def create_ingest(
        self,
        *, source_type: str, title: str, author: str | None, language: str, notes: str | None,
        rights: str, categories: list[str], tags: list[str], artifact_sha256: str, original_name: str,
        media_type: str | None, byte_size: int, job_payload: dict[str, Any], priority: int,
        audit_event: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Persist every logical ingest record in one transaction.

        The caller compensates the physical artifact only when this transaction
        fails and it created the content-addressed file itself.
        """
        source_id = identifier()
        version_id = identifier()
        job_id = identifier()
        stamp = now()
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO artifacts(sha256, byte_size, stored_at) VALUES(?, ?, ?)",
                (artifact_sha256, byte_size, stamp),
            )
            connection.execute(
                """INSERT INTO sources(id,source_type,title,author,language,notes,rights,categories_json,tags_json,processing_state,imported_at,updated_at,deleted_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                (source_id, source_type, title, author, language, notes, rights, json.dumps(categories), json.dumps(tags), "queued", stamp, stamp),
            )
            connection.execute(
                """INSERT INTO content_versions(id,source_id,artifact_sha256,ordinal,original_name,media_type,completeness,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (version_id, source_id, artifact_sha256, 1, original_name, media_type, "pending", stamp),
            )
            connection.execute(
                """INSERT INTO jobs(id,kind,source_id,content_version_id,artifact_sha256,config_hash,payload_json,priority,state,progress,message,attempt_count,max_attempts,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,? ,0,NULL,0,?,?,?)""",
                (job_id, "parse", source_id, version_id, artifact_sha256, None, json.dumps(job_payload), priority, "queued", self._configured_max_attempts(connection), stamp, stamp),
            )
            connection.execute(
                "INSERT INTO audit_events(id, event_type, entity_id, result, created_at) VALUES(?, ?, ?, ?, ?)",
                (identifier(), audit_event, source_id, "queued", stamp),
            )
            self._record_metadata_revision(connection, source_id, stamp)
            source = self._row(connection.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()) or {}
            version = self._row(connection.execute("SELECT * FROM content_versions WHERE id=?", (version_id,)).fetchone()) or {}
            job = self._row(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()) or {}
        return source, version, job

    def list_sources(self, include_deleted: bool = False) -> list[dict[str, Any]]:
        where = "" if include_deleted else "WHERE deleted_at IS NULL"
        with self.connection() as connection:
            return self._rows(connection.execute(f"SELECT * FROM sources {where} ORDER BY updated_at DESC, title COLLATE NOCASE ASC").fetchall())

    def get_source(self, source_id: str, include_deleted: bool = True) -> dict[str, Any] | None:
        with self.connection() as connection:
            query = "SELECT * FROM sources WHERE id=?" + ("" if include_deleted else " AND deleted_at IS NULL")
            return self._row(connection.execute(query, (source_id,)).fetchone())

    def get_version(self, version_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return self._row(connection.execute("SELECT * FROM content_versions WHERE id=?", (version_id,)).fetchone())

    def versions_for_source(self, source_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM content_versions WHERE source_id=? ORDER BY ordinal DESC", (source_id,)).fetchall())

    def update_source_metadata(self, source_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"title", "author", "language", "notes", "categories_json", "tags_json"}
        fields = [(key, value) for key, value in values.items() if key in allowed and value is not None]
        if not fields:
            return self.get_source(source_id)
        assignments = ", ".join(f"{key}=?" for key, _ in fields) + ", updated_at=?"
        with self.connection() as connection:
            stamp = now()
            updated = connection.execute(f"UPDATE sources SET {assignments} WHERE id=?", [value for _, value in fields] + [stamp, source_id]).rowcount
            if updated:
                self._record_metadata_revision(connection, source_id, stamp)
        return self.get_source(source_id)

    def update_rights(self, source_id: str, rights: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            stamp = now()
            updated = connection.execute("UPDATE sources SET rights=?, updated_at=? WHERE id=?", (rights, stamp, source_id)).rowcount
            if updated:
                self._record_metadata_revision(connection, source_id, stamp)
        return self.get_source(source_id)

    def metadata_revisions_for_source(self, source_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute(
                "SELECT * FROM source_metadata_revisions WHERE source_id=? ORDER BY ordinal DESC", (source_id,)
            ).fetchall())

    def add_relation(self, source_id: str, related_source_id: str, relation_type: str) -> dict[str, Any]:
        relation_id = identifier()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO source_relations(id,source_id,related_source_id,relation_type,created_at) VALUES(?,?,?,?,?)",
                (relation_id, source_id, related_source_id, relation_type, now()),
            )
            return self._row(connection.execute("SELECT * FROM source_relations WHERE id=?", (relation_id,)).fetchone()) or {}

    def relations_for_source(self, source_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM source_relations WHERE source_id=? OR related_source_id=?", (source_id, source_id)).fetchall())

    def update_processing(self, source_id: str, state: str) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE sources SET processing_state=?, updated_at=? WHERE id=?", (state, now(), source_id))

    def set_version_completeness(self, version_id: str, completeness: str) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE content_versions SET completeness=? WHERE id=?", (completeness, version_id))

    def create_representation(
        self, version_id: str, kind: str, parser_name: str, config_hash: str, text: str, parent_id: str | None = None
    ) -> dict[str, Any]:
        representation_id = identifier()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO representations(id,content_version_id,kind,parser_name,config_hash,parent_representation_id,text_content,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (representation_id, version_id, kind, parser_name, config_hash, parent_id, text, now()),
            )
            return self._row(connection.execute("SELECT * FROM representations WHERE id=?", (representation_id,)).fetchone()) or {}

    def representations_for_version(self, version_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM representations WHERE content_version_id=? ORDER BY created_at ASC", (version_id,)).fetchall())

    def get_representation(self, representation_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return self._row(connection.execute("SELECT * FROM representations WHERE id=?", (representation_id,)).fetchone())

    def create_search_chunks(self, source_id: str, version_id: str, representation_id: str, chunks: list[tuple[str, str]]) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        with self.connection() as connection:
            for ordinal, (text, text_hash) in enumerate(chunks):
                chunk_id = identifier()
                connection.execute(
                    """INSERT INTO search_chunks(id,source_id,content_version_id,representation_id,ordinal,text_content,text_hash,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (chunk_id, source_id, version_id, representation_id, ordinal, text, text_hash, now()),
                )
                created.append(self._row(connection.execute("SELECT * FROM search_chunks WHERE id=?", (chunk_id,)).fetchone()) or {})
        return created

    def search_chunks_for_representation(self, representation_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM search_chunks WHERE representation_id=? ORDER BY ordinal", (representation_id,)).fetchall())

    def create_evidence(
        self, *, version_id: str, artifact_sha256: str, representation_id: str, parser_config_hash: str,
        locator: dict[str, Any], excerpt: str, excerpt_hash: str, is_validated: bool = True
    ) -> dict[str, Any]:
        evidence_id = identifier()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO evidence(id,content_version_id,artifact_sha256,representation_id,parser_config_hash,locator_json,excerpt,excerpt_hash,is_validated,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (evidence_id, version_id, artifact_sha256, representation_id, parser_config_hash, json.dumps(locator, ensure_ascii=False), excerpt, excerpt_hash, int(is_validated), now()),
            )
            return self._row(connection.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()) or {}

    def evidence_for_representation(self, representation_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM evidence WHERE representation_id=? ORDER BY created_at", (representation_id,)).fetchall())

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return self._row(connection.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone())

    def create_citation(self, evidence_id: str) -> dict[str, Any]:
        citation_id = identifier()
        with self.connection() as connection:
            connection.execute("INSERT INTO citations(id,evidence_id,created_at) VALUES(?,?,?)", (citation_id, evidence_id, now()))
            return self._row(connection.execute("SELECT * FROM citations WHERE id=?", (citation_id,)).fetchone()) or {}

    def citations_for_evidence(self, evidence_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM citations WHERE evidence_id=? ORDER BY created_at", (evidence_id,)).fetchall())

    def citation_details(self, citation_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return self._row(connection.execute(
                """SELECT c.id, c.created_at, e.id AS evidence_id, e.locator_json, e.excerpt, e.representation_id,
                          s.id AS source_id, s.title, s.processing_state, r.kind AS representation_kind
                   FROM citations c JOIN evidence e ON e.id=c.evidence_id
                   JOIN content_versions v ON v.id=e.content_version_id
                   JOIN sources s ON s.id=v.source_id JOIN representations r ON r.id=e.representation_id WHERE c.id=?""",
                (citation_id,),
            ).fetchone())

    def create_knowledge(self, kind: str, statement: str, evidence_ids: list[str]) -> dict[str, Any]:
        knowledge_id = identifier()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO knowledge(id,kind,statement,status,created_at,published_at) VALUES(?,?,?,?,?,NULL)",
                (knowledge_id, kind, statement, "draft", now()),
            )
            for evidence_id in sorted(set(evidence_ids)):
                connection.execute("INSERT INTO knowledge_evidence(knowledge_id,evidence_id) VALUES(?,?)", (knowledge_id, evidence_id))
            return self._row(connection.execute("SELECT * FROM knowledge WHERE id=?", (knowledge_id,)).fetchone()) or {}

    def get_knowledge(self, knowledge_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = self._row(connection.execute("SELECT * FROM knowledge WHERE id=?", (knowledge_id,)).fetchone())
            if row is not None:
                row["evidence_ids"] = [r["evidence_id"] for r in connection.execute("SELECT evidence_id FROM knowledge_evidence WHERE knowledge_id=?", (knowledge_id,))]
            return row

    def list_knowledge(self, published_only: bool = False) -> list[dict[str, Any]]:
        with self.connection() as connection:
            where = "WHERE status='published'" if published_only else ""
            rows = self._rows(connection.execute(f"SELECT * FROM knowledge {where} ORDER BY created_at DESC").fetchall())
            for row in rows:
                row["evidence_ids"] = [item["evidence_id"] for item in connection.execute("SELECT evidence_id FROM knowledge_evidence WHERE knowledge_id=?", (row["id"],))]
            return rows

    def publish_knowledge(self, knowledge_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("UPDATE knowledge SET status='published', published_at=? WHERE id=?", (now(), knowledge_id))
        return self.get_knowledge(knowledge_id)

    def create_job(self, kind: str, source_id: str | None, version_id: str | None, artifact_sha256: str | None, config_hash: str | None, payload: dict[str, Any], priority: int = 0) -> dict[str, Any]:
        job_id = identifier()
        stamp = now()
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO jobs(id,kind,source_id,content_version_id,artifact_sha256,config_hash,payload_json,priority,state,progress,message,attempt_count,max_attempts,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,? ,0,NULL,0,?,?,?)""",
                (job_id, kind, source_id, version_id, artifact_sha256, config_hash, json.dumps(payload), priority, "queued", self._configured_max_attempts(connection), stamp, stamp),
            )
            return self._row(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()) or {}

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall())

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            return self._row(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def claim_next_job(self) -> dict[str, Any] | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT id FROM jobs WHERE state IN ('queued','retry_wait') ORDER BY priority DESC, created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            job_id = row["id"]
            stamp = now()
            updated = connection.execute(
                "UPDATE jobs SET state='running', attempt_count=attempt_count+1, started_at=?, heartbeat_at=?, updated_at=? WHERE id=? AND state IN ('queued','retry_wait')",
                (stamp, stamp, stamp, job_id),
            ).rowcount
            if not updated:
                return None
            current = self._row(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            assert current is not None
            connection.execute(
                "INSERT INTO job_attempts(id,job_id,attempt_number,state,started_at) VALUES(?,?,?,?,?)",
                (identifier(), job_id, current["attempt_count"], "running", stamp),
            )
            return current

    def update_job(self, job_id: str, *, state: str | None = None, progress: int | None = None, message: str | None = None, done: bool = False) -> None:
        fields: list[str] = ["updated_at=?", "heartbeat_at=?"]
        values: list[Any] = [now(), now()]
        if state is not None:
            fields.append("state=?")
            values.append(state)
        if progress is not None:
            fields.append("progress=?")
            values.append(max(0, min(100, progress)))
        if message is not None:
            fields.append("message=?")
            values.append(message)
        if done:
            fields.append("completed_at=?")
            values.append(now())
        values.append(job_id)
        with self.connection() as connection:
            connection.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id=?", values)
            if done:
                current = self._row(connection.execute("SELECT attempt_count,state FROM jobs WHERE id=?", (job_id,)).fetchone())
                if current:
                    connection.execute(
                        "UPDATE job_attempts SET state=?, ended_at=?, outcome=? WHERE job_id=? AND attempt_number=? AND ended_at IS NULL",
                        (current["state"], now(), current["state"], job_id, current["attempt_count"]),
                    )

    def touch_job(self, job_id: str) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE jobs SET heartbeat_at=?, updated_at=? WHERE id=?", (now(), now(), job_id))

    def request_cancel(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("UPDATE jobs SET cancel_requested_at=?, updated_at=? WHERE id=? AND state IN ('queued','retry_wait','running')", (now(), now(), job_id))
        return self.get_job(job_id)

    def retry_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("UPDATE jobs SET state='queued', progress=0, message=NULL, cancel_requested_at=NULL, priority=100, updated_at=? WHERE id=? AND state IN ('failed','blocked','cancelled')", (now(), job_id))
        return self.get_job(job_id)

    def job_cancel_requested(self, job_id: str) -> bool:
        with self.connection() as connection:
            row = connection.execute("SELECT cancel_requested_at FROM jobs WHERE id=?", (job_id,)).fetchone()
            return bool(row and row["cancel_requested_at"])

    def create_external_card(self, card_type: str, url: str, title: str, author: str | None, notes: str | None, tags: list[str]) -> dict[str, Any]:
        card_id = identifier()
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO external_cards(id,card_type,url,title,author,notes,tags_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (card_id, card_type, url, title, author, notes, json.dumps(sorted(set(tags))), now()),
            )
            return self._row(connection.execute("SELECT * FROM external_cards WHERE id=?", (card_id,)).fetchone()) or {}

    def list_external_cards(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = self._rows(connection.execute("SELECT * FROM external_cards ORDER BY created_at DESC").fetchall())
        for row in rows:
            row["url"] = redact_url_userinfo(row["url"])
        return rows

    def create_topic(self, name: str, source_ids: list[str]) -> dict[str, Any]:
        topic_id = identifier()
        with self.connection() as connection:
            connection.execute("INSERT INTO topics(id,name,created_at) VALUES(?,?,?)", (topic_id, name, now()))
            for source_id in sorted(set(source_ids)):
                connection.execute("INSERT INTO topic_sources(topic_id,source_id) VALUES(?,?)", (topic_id, source_id))
            return self._row(connection.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()) or {}

    def list_topics(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            topics = self._rows(connection.execute("SELECT * FROM topics ORDER BY name COLLATE NOCASE").fetchall())
            for topic in topics:
                topic["source_ids"] = [row["source_id"] for row in connection.execute("SELECT source_id FROM topic_sources WHERE topic_id=?", (topic["id"],))]
            return topics

    def soft_delete_source(self, source_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("UPDATE sources SET deleted_at=?, updated_at=? WHERE id=? AND deleted_at IS NULL", (now(), now(), source_id))
        return self.get_source(source_id)

    def restore_source(self, source_id: str) -> dict[str, Any] | None:
        with self.connection() as connection:
            connection.execute("UPDATE sources SET deleted_at=NULL, updated_at=? WHERE id=?", (now(), source_id))
        return self.get_source(source_id)

    def source_artifacts(self, source_id: str) -> list[str]:
        with self.connection() as connection:
            return [row["artifact_sha256"] for row in connection.execute("SELECT artifact_sha256 FROM content_versions WHERE source_id=?", (source_id,))]

    def delete_artifact_if_unreferenced(self, sha256: str) -> bool:
        """Remove only an artifact with no remaining logical reference.

        Returning true when no reference exists permits callers to compensate a
        newly-created file even when its transaction never inserted the DB row.
        """
        with self.connection() as connection:
            referenced = connection.execute(
                """SELECT 1 FROM content_versions WHERE artifact_sha256=?
                   UNION SELECT 1 FROM evidence WHERE artifact_sha256=?
                   UNION SELECT 1 FROM jobs WHERE artifact_sha256=? LIMIT 1""",
                (sha256, sha256, sha256),
            ).fetchone()
            if referenced:
                return False
            connection.execute("DELETE FROM artifacts WHERE sha256=?", (sha256,))
            return True

    def purge_source(self, source_id: str) -> list[str]:
        """Delete logical data and return artifacts no longer referenced by active sources."""
        with self.connection() as connection:
            source = connection.execute("SELECT deleted_at FROM sources WHERE id=?", (source_id,)).fetchone()
            if not source:
                raise KeyError("来源不存在")
            if source["deleted_at"] is None:
                raise ValueError("必须先软删除来源")
            hashes = [r["artifact_sha256"] for r in connection.execute("SELECT artifact_sha256 FROM content_versions WHERE source_id=?", (source_id,))]
            connection.execute("DELETE FROM knowledge_evidence WHERE evidence_id IN (SELECT id FROM evidence WHERE content_version_id IN (SELECT id FROM content_versions WHERE source_id=?))", (source_id,))
            connection.execute("DELETE FROM citations WHERE evidence_id IN (SELECT id FROM evidence WHERE content_version_id IN (SELECT id FROM content_versions WHERE source_id=?))", (source_id,))
            connection.execute("DELETE FROM evidence WHERE content_version_id IN (SELECT id FROM content_versions WHERE source_id=?)", (source_id,))
            connection.execute("DELETE FROM search_chunks WHERE content_version_id IN (SELECT id FROM content_versions WHERE source_id=?)", (source_id,))
            connection.execute("DELETE FROM representations WHERE content_version_id IN (SELECT id FROM content_versions WHERE source_id=?)", (source_id,))
            connection.execute("DELETE FROM job_attempts WHERE job_id IN (SELECT id FROM jobs WHERE source_id=?)", (source_id,))
            connection.execute("DELETE FROM jobs WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM source_relations WHERE source_id=? OR related_source_id=?", (source_id, source_id))
            connection.execute("DELETE FROM topic_sources WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM content_versions WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM source_metadata_revisions WHERE source_id=?", (source_id,))
            connection.execute("DELETE FROM sources WHERE id=?", (source_id,))
            orphaned: list[str] = []
            for sha256 in hashes:
                reference_count = connection.execute(
                    "SELECT COUNT(*) AS n FROM content_versions WHERE artifact_sha256=?", (sha256,)
                ).fetchone()["n"]
                if not reference_count:
                    connection.execute("DELETE FROM artifacts WHERE sha256=?", (sha256,))
                    orphaned.append(sha256)
            return orphaned

    def rows_for_export(self) -> dict[str, list[dict[str, Any]]]:
        tables = ["artifacts", "sources", "source_metadata_revisions", "content_versions", "source_relations", "representations", "search_chunks", "evidence", "citations", "knowledge", "knowledge_evidence", "external_cards", "topics", "topic_sources"]
        with self.connection() as connection:
            rows = {table: self._rows(connection.execute(f"SELECT * FROM {table}").fetchall()) for table in tables}
        for card in rows["external_cards"]:
            card["url"] = redact_url_userinfo(card["url"])
        return rows

    def create_backup_record(self, archive_name: str, manifest_sha256: str, state: str = "succeeded") -> dict[str, Any]:
        backup_id = identifier()
        with self.connection() as connection:
            connection.execute("INSERT INTO backups(id,archive_name,manifest_sha256,state,created_at) VALUES(?,?,?,?,?)", (backup_id, archive_name, manifest_sha256, state, now()))
            return self._row(connection.execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone()) or {}

    def delete_backup_record(self, backup_id: str) -> None:
        with self.connection() as connection:
            connection.execute("DELETE FROM backups WHERE id=?", (backup_id,))

    def update_backup_state(self, backup_id: str, state: str) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE backups SET state=? WHERE id=?", (state, backup_id))

    def list_backups(self) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return self._rows(connection.execute("SELECT * FROM backups ORDER BY created_at DESC").fetchall())
