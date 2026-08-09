"""Add portable local video analysis and keyframe records.

Revision ID: 007_video_media
Revises: 006_evidence_bundles
Create Date: 2026-07-30
"""

from __future__ import annotations

from alembic import op

revision = "007_video_media"
down_revision = "006_evidence_bundles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS video_analyses ("
        "id TEXT PRIMARY KEY, content_version_id TEXT NOT NULL REFERENCES content_versions(id), "
        "analyzer_name TEXT NOT NULL, config_hash TEXT NOT NULL, metadata_json JSONB NOT NULL, "
        "created_at TIMESTAMPTZ NOT NULL, UNIQUE(content_version_id, analyzer_name, config_hash))"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS video_frames ("
        "id TEXT PRIMARY KEY, video_analysis_id TEXT NOT NULL REFERENCES video_analyses(id), "
        "artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256), ordinal INTEGER NOT NULL, "
        "time_ms INTEGER NOT NULL, width INTEGER, height INTEGER, created_at TIMESTAMPTZ NOT NULL, "
        "UNIQUE(video_analysis_id, ordinal))"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_video_analyses_version ON video_analyses(content_version_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_video_frames_analysis ON video_frames(video_analysis_id, ordinal)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS video_frames")
    op.execute("DROP TABLE IF EXISTS video_analyses")
