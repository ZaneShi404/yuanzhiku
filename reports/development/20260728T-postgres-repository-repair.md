# PostgreSQL Repository Repair

- Date: 2026-07-28
- Completed: 2026-07-28T20:28:21Z
- Requirements: `REQ-032`, `REQ-040..042`, `REQ-043`, `REQ-045`
- Test identifiers: `T-JOB-001`, `T-LIFE-001`, `T-BACK-001`, `T-COMP-001`

## Changes

- Replaced the deliberate PostgreSQL post-migration failure with a SQLAlchemy-backed `PostgresRepository` that implements the existing repository contract for sources, versions, rights, metadata revisions, representations, evidence, citations, knowledge, jobs, search data, taxonomy, external cards, soft delete/purge, and backup records.
- PostgreSQL initialization validates the configured connection, applies Alembic migrations, serializes concurrent API/worker migrations with a transaction-scoped PostgreSQL advisory lock, and does not construct or use SQLite for an explicit PostgreSQL URL.
- PostgreSQL job claiming uses `FOR UPDATE SKIP LOCKED` so Compose API and worker processes cannot claim the same queued job.
- Added a compatibility migration to normalize prior JSONB fields to the existing serialized JSON text contract used by services and portable exports.
- Moved service type dependencies to the repository port and moved reimport writes behind the port, retaining SQLite as the default local backend.
- PostgreSQL backups now contain a repeatable-read logical snapshot of settings, sources/content versions/rights/metadata, artifacts, representations/evidence/citations/knowledge, search/taxonomy/cards, durable jobs/attempts, and audit state plus artifacts. Exports remain portable logical content records; reimport remains conflict-checked. PostgreSQL restore requires a separate empty `target_database_url`; it never restores PostgreSQL records into SQLite. Existing SQLite backup archives remain restorable.
- Added PostgreSQL adapter dispatch coverage plus a conditional real-server workflow test. The server test exercises composition, import, worker parsing, evidence/citation/knowledge, search, topics, external cards, backup/export/reimport, and lifecycle purge. It runs only with a disposable `POSTGRES_TEST_URL`.

## Commands And Results

```text
PYTHONPATH="E:/源知库/backend" E:/源知库/.venv/Scripts/python.exe -m pytest -q E:/源知库/tests/unit/test_api.py E:/源知库/tests/unit/test_defect_fixes.py E:/源知库/tests/unit/test_database_url_selection.py E:/源知库/tests/unit/test_postgres_repository.py
29 passed, 1 skipped in 116.68s

PYTHONPATH="E:/源知库/backend" E:/源知库/.venv/Scripts/python.exe -m compileall -q E:/源知库/backend/app E:/源知库/backend/alembic
exit 0

git -C E:/源知库 diff --check
exit 0
```

## Limits

- `POSTGRES_TEST_URL` was not set, so the real PostgreSQL integration workflow was skipped by design. It was not treated as passed.
- Local `.venv` lacks SQLAlchemy, Alembic, and psycopg, and Docker/Compose is not installed. Therefore no PostgreSQL connection, migration, Compose startup, or container integration was executed in this environment.
