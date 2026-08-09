"""Index permanent-delete audit retention queries.

Revision ID: 004_audit_retention_index
Revises: 003_source_date
Create Date: 2026-07-28
"""

from __future__ import annotations

from alembic import op

revision = "004_audit_retention_index"
down_revision = "003_source_date"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_type_created_at ON audit_events(event_type, created_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_events_type_created_at")
