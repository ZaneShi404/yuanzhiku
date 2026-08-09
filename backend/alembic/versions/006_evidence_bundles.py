"""Make derived evidence bundles idempotent and structurally complete.

Revision ID: 006_evidence_bundles
Revises: 005_job_leases
Create Date: 2026-07-29
"""

from __future__ import annotations

import hashlib
import json

from alembic import op
from sqlalchemy import text


revision = "006_evidence_bundles"
down_revision = "005_job_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE evidence ADD COLUMN IF NOT EXISTS locator_hash TEXT")
    connection = op.get_bind()
    rows = connection.execute(text(
        "SELECT id, locator_json FROM evidence WHERE locator_hash IS NULL OR locator_hash=''"
    )).mappings().all()
    for row in rows:
        locator = json.loads(row["locator_json"])
        if not isinstance(locator, dict):
            raise ValueError("历史 evidence locator 无效")
        locator_json = json.dumps(locator, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        locator_hash = hashlib.sha256(locator_json.encode("utf-8")).hexdigest()
        connection.execute(
            text("UPDATE evidence SET locator_json=:locator_json, locator_hash=:locator_hash WHERE id=:id"),
            {"locator_json": locator_json, "locator_hash": locator_hash, "id": row["id"]},
        )
    op.execute("ALTER TABLE evidence ALTER COLUMN locator_hash SET NOT NULL")
    op.execute(
        "CREATE TEMP TABLE evidence_legacy_map ON COMMIT DROP AS "
        "SELECT id AS evidence_id, md5('evidence-bundle-legacy-representation:' || id) AS representation_id "
        "FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY representation_id, locator_hash, excerpt_hash "
        "ORDER BY created_at, id) AS ordinal FROM evidence) duplicate_evidence WHERE ordinal>1"
    )
    op.execute(
        "INSERT INTO representations(id,content_version_id,kind,parser_name,config_hash,parent_representation_id,text_content,created_at) "
        "SELECT legacy.representation_id,representation.content_version_id,'extraction_legacy',representation.parser_name,"
        "representation.config_hash,representation.id,representation.text_content,representation.created_at "
        "FROM evidence_legacy_map legacy JOIN evidence ON evidence.id=legacy.evidence_id "
        "JOIN representations representation ON representation.id=evidence.representation_id"
    )
    op.execute(
        "INSERT INTO search_chunks(id,source_id,content_version_id,representation_id,ordinal,text_content,text_hash,created_at) "
        "SELECT md5('evidence-bundle-legacy-chunk:' || legacy.evidence_id || ':' || chunk.id),chunk.source_id,"
        "chunk.content_version_id,legacy.representation_id,chunk.ordinal,chunk.text_content,chunk.text_hash,chunk.created_at "
        "FROM evidence_legacy_map legacy JOIN evidence ON evidence.id=legacy.evidence_id "
        "JOIN search_chunks chunk ON chunk.representation_id=evidence.representation_id"
    )
    op.execute(
        "UPDATE evidence SET representation_id=legacy.representation_id FROM evidence_legacy_map legacy "
        "WHERE evidence.id=legacy.evidence_id"
    )
    op.execute(
        "CREATE TEMP TABLE citation_legacy_map ON COMMIT DROP AS "
        "SELECT id AS citation_id, md5('evidence-bundle-legacy-citation-evidence:' || id) AS evidence_id, "
        "md5('evidence-bundle-legacy-citation-representation:' || id) AS representation_id "
        "FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY evidence_id ORDER BY created_at, id) AS ordinal "
        "FROM citations) duplicate_citations WHERE ordinal>1"
    )
    op.execute(
        "INSERT INTO representations(id,content_version_id,kind,parser_name,config_hash,parent_representation_id,text_content,created_at) "
        "SELECT legacy.representation_id,representation.content_version_id,'extraction_legacy',representation.parser_name,"
        "representation.config_hash,representation.id,representation.text_content,representation.created_at "
        "FROM citation_legacy_map legacy JOIN citations citation ON citation.id=legacy.citation_id "
        "JOIN evidence ON evidence.id=citation.evidence_id JOIN representations representation ON representation.id=evidence.representation_id"
    )
    op.execute(
        "INSERT INTO search_chunks(id,source_id,content_version_id,representation_id,ordinal,text_content,text_hash,created_at) "
        "SELECT md5('evidence-bundle-legacy-citation-chunk:' || legacy.citation_id || ':' || chunk.id),chunk.source_id,"
        "chunk.content_version_id,legacy.representation_id,chunk.ordinal,chunk.text_content,chunk.text_hash,chunk.created_at "
        "FROM citation_legacy_map legacy JOIN citations citation ON citation.id=legacy.citation_id "
        "JOIN evidence ON evidence.id=citation.evidence_id JOIN search_chunks chunk ON chunk.representation_id=evidence.representation_id"
    )
    op.execute(
        "INSERT INTO evidence(id,content_version_id,artifact_sha256,representation_id,parser_config_hash,locator_json,"
        "locator_hash,excerpt,excerpt_hash,is_validated,created_at) "
        "SELECT legacy.evidence_id,evidence.content_version_id,evidence.artifact_sha256,legacy.representation_id,"
        "evidence.parser_config_hash,evidence.locator_json,evidence.locator_hash,evidence.excerpt,evidence.excerpt_hash,"
        "evidence.is_validated,evidence.created_at FROM citation_legacy_map legacy "
        "JOIN citations citation ON citation.id=legacy.citation_id JOIN evidence ON evidence.id=citation.evidence_id"
    )
    op.execute(
        "UPDATE citations SET evidence_id=legacy.evidence_id FROM citation_legacy_map legacy "
        "WHERE citations.id=legacy.citation_id"
    )
    op.execute(
        "INSERT INTO citations(id,evidence_id,created_at) "
        "SELECT md5('evidence-bundle-generated-citation:' || evidence.id),evidence.id,evidence.created_at "
        "FROM evidence LEFT JOIN citations ON citations.evidence_id=evidence.id "
        "WHERE citations.id IS NULL"
    )
    op.execute(
        "WITH duplicate_extractions AS ("
        "SELECT id, ROW_NUMBER() OVER (PARTITION BY content_version_id, parser_name, config_hash "
        "ORDER BY created_at, id) AS ordinal FROM representations WHERE kind='extraction'"
        ") UPDATE representations SET kind='extraction_legacy' FROM duplicate_extractions "
        "WHERE representations.id=duplicate_extractions.id AND duplicate_extractions.ordinal>1"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_extraction_representation_identity "
        "ON representations(content_version_id, parser_name, config_hash) WHERE kind='extraction'"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_representation_locator_excerpt "
        "ON evidence(representation_id, locator_hash, excerpt_hash)"
    )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_citations_evidence ON citations(evidence_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_citations_evidence")
    op.execute("DROP INDEX IF EXISTS idx_evidence_representation_locator_excerpt")
    op.execute("DROP INDEX IF EXISTS idx_extraction_representation_identity")
    op.execute(
        "DO $$ DECLARE constraint_name TEXT; BEGIN "
        "FOR constraint_name IN SELECT ccu.constraint_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_catalog=tc.constraint_catalog "
        "AND ccu.constraint_schema=tc.constraint_schema AND ccu.constraint_name=tc.constraint_name "
        "WHERE tc.constraint_type='UNIQUE' AND ccu.table_schema=current_schema() "
        "AND ccu.table_name='evidence' AND ccu.column_name='locator_hash' "
        "LOOP EXECUTE format('ALTER TABLE evidence DROP CONSTRAINT %I', constraint_name); END LOOP; END $$"
    )
    op.execute(
        "DO $$ DECLARE constraint_name TEXT; BEGIN "
        "FOR constraint_name IN SELECT ccu.constraint_name FROM information_schema.table_constraints tc "
        "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_catalog=tc.constraint_catalog "
        "AND ccu.constraint_schema=tc.constraint_schema AND ccu.constraint_name=tc.constraint_name "
        "WHERE tc.constraint_type='UNIQUE' AND ccu.table_schema=current_schema() "
        "AND ccu.table_name='citations' AND ccu.column_name='evidence_id' "
        "LOOP EXECUTE format('ALTER TABLE citations DROP CONSTRAINT %I', constraint_name); END LOOP; END $$"
    )
    op.execute("ALTER TABLE evidence DROP COLUMN IF EXISTS locator_hash")
