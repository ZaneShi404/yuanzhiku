"""Initial PostgreSQL schema equivalent to the local SQLite model.

Revision ID: 001_initial
Revises:
Create Date: 2026-07-28
"""

from __future__ import annotations

from pathlib import Path

from alembic import op

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    migration = Path(__file__).resolve().parents[2] / "migrations" / "postgresql" / "001_initial.sql"
    for statement in migration.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    tables = (
        "topic_sources", "topics", "external_cards", "backups", "audit_events", "job_attempts", "jobs",
        "knowledge_evidence", "knowledge", "citations", "evidence", "search_chunks", "representations",
        "source_relations", "content_versions", "source_metadata_revisions", "sources", "artifacts", "settings",
    )
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
