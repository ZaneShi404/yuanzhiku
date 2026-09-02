"""Split fixed categories into domains (multi) x genres (single) on sources.

Revision ID: 010_artifact_cleanup_queue
Revises: 009_source_taxonomy
Create Date: 2026-09-02
"""

from __future__ import annotations

from alembic import op

revision = "010_artifact_cleanup_queue"
down_revision = "009_source_taxonomy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Additive migration (hardening plan Task 11): durable cleanup tasks for
    # permanent-delete artifact removal. No existing table or row is modified.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS artifact_cleanup_tasks (
            sha256 TEXT PRIMARY KEY,
            source_id TEXT,
            reason TEXT NOT NULL,
            state TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS artifact_cleanup_tasks")
