"""Add independent source-origin date metadata.

Revision ID: 003_source_date
Revises: 002_json_text_compat
Create Date: 2026-07-28
"""

from __future__ import annotations

from alembic import op

revision = "003_source_date"
down_revision = "002_json_text_compat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS source_date DATE")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sources_source_date ON sources(source_date)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_sources_imported_at ON sources(imported_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_sources_imported_at")
    op.execute("DROP INDEX IF EXISTS idx_sources_source_date")
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS source_date")
