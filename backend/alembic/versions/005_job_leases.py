"""Fence job leases and close attempts by their claim token.

Revision ID: 005_job_leases
Revises: 004_audit_retention_index
Create Date: 2026-07-29
"""

from __future__ import annotations

from alembic import op

revision = "005_job_leases"
down_revision = "004_audit_retention_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_token TEXT")
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ")
    op.execute("ALTER TABLE job_attempts ADD COLUMN IF NOT EXISTS lease_token TEXT")
    op.execute("UPDATE jobs SET retry_count=GREATEST(attempt_count - 1, 0) WHERE retry_count=0 AND attempt_count>0")
    op.execute(
        "WITH ranked AS ("
        "SELECT id, ROW_NUMBER() OVER (PARTITION BY job_id ORDER BY attempt_number, started_at, id) AS ordinal "
        "FROM job_attempts"
        ") UPDATE job_attempts SET attempt_number=ranked.ordinal FROM ranked WHERE job_attempts.id=ranked.id"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_jobs_running_lease ON jobs(state, lease_expires_at)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_job_attempts_job_attempt_number ON job_attempts(job_id, attempt_number)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_job_attempts_job_attempt_number")
    op.execute("DROP INDEX IF EXISTS idx_jobs_running_lease")
    op.execute("ALTER TABLE job_attempts DROP COLUMN IF EXISTS lease_token")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS lease_expires_at")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS lease_token")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS retry_count")
