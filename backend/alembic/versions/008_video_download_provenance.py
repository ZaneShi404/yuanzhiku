"""Add video download provenance records.

Revision ID: 008_video_download_provenance
Revises: 007_video_media
Create Date: 2026-08-13
"""

from __future__ import annotations

from alembic import op

revision = "008_video_download_provenance"
down_revision = "007_video_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS video_download_provenance ("
        "id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id) UNIQUE, "
        "platform TEXT NOT NULL, url_sanitized TEXT NOT NULL, yt_dlp_version TEXT NOT NULL, "
        "format_profile TEXT NOT NULL, cookie_used INTEGER NOT NULL, config_hash TEXT NOT NULL, "
        "created_at TIMESTAMPTZ NOT NULL)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS video_download_provenance")
