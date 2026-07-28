"""Normalize PostgreSQL JSONB columns to the shared serialized-text contract.

Revision ID: 002_json_text_compat
Revises: 001_initial
Create Date: 2026-07-28
"""

from __future__ import annotations

from alembic import op

revision = "002_json_text_compat"
down_revision = "001_initial"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("sources", "categories_json"),
    ("sources", "tags_json"),
    ("source_metadata_revisions", "snapshot_json"),
    ("evidence", "locator_json"),
    ("jobs", "payload_json"),
    ("external_cards", "tags_json"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TEXT "
            f"USING {column}::TEXT"
        )


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE JSONB "
            f"USING {column}::JSONB"
        )
