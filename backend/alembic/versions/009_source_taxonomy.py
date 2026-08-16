"""Split fixed categories into domains (multi) x genres (single) on sources.

Also merges the two 008 heads (video_download_provenance / video_frame_reason)
back into a single head so ``upgrade head`` has one target again.

Revision ID: 009_source_taxonomy
Revises: 008_video_download_provenance, 008_video_frame_reason
Create Date: 2026-08-15
"""

from __future__ import annotations

import json

from alembic import op
from sqlalchemy import text

revision = "009_source_taxonomy"
down_revision = ("008_video_download_provenance", "008_video_frame_reason")
branch_labels = None
depends_on = None

# 与 app.domain.models.split_legacy_categories 同一映射；迁移文件保持历史快照，不随应用代码漂移。
_LEGACY_CATEGORY_DOMAINS = ("technical", "business", "education", "news")
_LEGACY_CATEGORY_GENRES = ("interview", "podcast", "document")


def upgrade() -> None:
    op.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS domains_json TEXT NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS genres_json TEXT NOT NULL DEFAULT '[]'")
    bind = op.get_bind()
    if bind is not None:
        # 数据迁移（在线模式）：旧固定分类前四项→领域，后三项→体裁；未知值忽略，多体裁保留。
        rows = bind.execute(text("SELECT id, categories_json FROM sources")).fetchall()
        for row in rows:
            try:
                legacy = json.loads(row[1])
            except (TypeError, json.JSONDecodeError):
                legacy = []
            values = {item for item in legacy if isinstance(item, str)} if isinstance(legacy, list) else set()
            domains = sorted(values.intersection(_LEGACY_CATEGORY_DOMAINS))
            genres = sorted(values.intersection(_LEGACY_CATEGORY_GENRES))
            bind.execute(
                text("UPDATE sources SET domains_json=:domains, genres_json=:genres WHERE id=:id"),
                {"domains": json.dumps(domains), "genres": json.dumps(genres), "id": row[0]},
            )
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS categories_json")


def downgrade() -> None:
    op.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS categories_json TEXT NOT NULL DEFAULT '[]'")
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS domains_json")
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS genres_json")
