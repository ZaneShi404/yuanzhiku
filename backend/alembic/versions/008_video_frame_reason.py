"""Add keyframe sampling reason to video frames.

Revision ID: 008_video_frame_reason
Revises: 007_video_media
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op

revision = "008_video_frame_reason"
down_revision = "007_video_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE video_frames ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT 'even'")


def downgrade() -> None:
    op.execute("ALTER TABLE video_frames DROP COLUMN IF EXISTS reason")
