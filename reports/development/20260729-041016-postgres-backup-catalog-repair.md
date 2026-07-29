# PostgreSQL Backup Catalog Repair

Timestamp: 2026-07-29 04:10 UTC
Role: independent development repair
Scope: Confirmed P1 in `reports/testing/20260729-1127-postgres-regression-check.md`: PostgreSQL logical backup and restore omitted the `backups` catalog.

## Changes

- Defined shared complete-backup and portable-export table inventories in `backend/app/adapters/sqlite.py`.
- Added `backups` to the complete backup inventory used by SQLite and PostgreSQL backup export and restore insertion paths.
- Preserved export/reimport semantics: portable exports exclude `settings`, jobs, audit history, and the local `backups` catalog.
- Added logical-record preflight validation for PostgreSQL archive restore and reimport. It rejects missing, extra, malformed, or incomplete V1 tables before target PostgreSQL initialization or data-root creation.
- Kept PostgreSQL target checks fail-closed: the target database URL must classify as PostgreSQL, target user records must be empty, and no SQLite target is created for logical PostgreSQL restore.
- Documented the catalog inclusion and export/reimport distinction in `docs/operations-and-recovery.md`.

## Tests

- `E:\源知库\.venv\Scripts\python.exe -m pytest ..\tests\unit\test_postgres_repository.py -q` from `backend`: `5 passed, 2 skipped`.
- `E:\源知库\.venv\Scripts\python.exe -m pytest ..\tests\unit\test_api.py ..\tests\unit\test_defect_fixes.py -q` from `backend`: `20 passed`.
- `E:\源知库\.venv\Scripts\python.exe -m pytest ..\tests\unit -q` from `backend`: `33 passed, 2 skipped`.
- Added a conditional live PostgreSQL backup/restore regression test. It requires separate disposable `POSTGRES_TEST_URL` and explicitly empty `POSTGRES_RESTORE_TEST_URL`; neither was configured for this run.

## Remaining Blocker

No live PostgreSQL target was available in this environment, so PostgreSQL migration, SQL execution, and physical source-to-empty-target backup/restore behavior were not exercised here.
