# PostgreSQL Backup Catalog And Portable Export Repair

Timestamp: 2026-07-29T05:37:24Z
Role: Development repair engineer
Runtime isolation: `E:\源知库\tests\runtime\development-pg-export-repair-20260729T130000Z`

## Scope

Repaired the two P1 findings recorded in `reports/testing/20260729T050712Z-independent-pg-backup-retest.md`. No files beneath `E:\源知库\data` were accessed or changed. Frozen documentation was read but not changed. Independent testing reports were not changed.

## Root Cause

1. Complete logical PostgreSQL backup validation checked the table inventory, row dictionary shape, and column order only. A `backups` row containing every required key with invalid values or types passed archive validation and could reach PostgreSQL target initialization and backup-restore preparation, relying on database constraints too late.
2. SQLite archive construction always embedded `state/knowledge.db`, including portable exports. Although `records.json` excluded `backups`, the SQLite database snapshot preserved the operational backup catalog in the portable ZIP.

## Changes

- `TransferService._backup_records()` now performs catalog-specific validation before the target PostgreSQL repository is constructed or the target data root is created. Each `backups` row must contain non-empty strings for every column, a plain `.zip` archive name without path separators, a 64-character hexadecimal manifest SHA-256, a supported operational state (`succeeded`, `pruning`, or `discarding`), and an offset-aware ISO timestamp. Duplicate backup IDs or archive names are rejected. Invalid rows raise `ValueError("备份目录记录无效")`.
- SQLite database snapshots are now added only to complete `backup` archives. Portable `export` archives retain the logical export records, artifacts, and SHA-256 manifest, but omit `state/knowledge.db` and therefore cannot carry the local `backups` catalog.
- Focused regression coverage verifies empty and every supported valid catalog state; `None`, blank, wrong-type, unsafe path, invalid digest, unsupported state, naive timestamp, and duplicate catalog cases all reject before target repository initialization and leave an existing empty target directory empty. It also confirms complete SQLite backups retain the database snapshot; portable SQLite exports omit it and the catalog bytes; and portable business data/artifacts reimport without backup records.

## Tests

Commands used isolated paths under `tests/runtime` only:

```text
PYTHONPATH="E:/源知库/backend" E:/源知库/.venv/Scripts/python.exe -m pytest -q E:/源知库/tests/unit/test_postgres_repository.py E:/源知库/tests/unit/test_database_url_selection.py --basetemp=E:/源知库/tests/runtime/development-pg-export-repair-20260729T130000Z/pytest-focused-final2-tmp -o cache_dir=E:/源知库/tests/runtime/development-pg-export-repair-20260729T130000Z/pytest-cache
26 passed, 2 skipped in 76.64s

PYTHONPATH="E:/源知库/backend" E:/源知库/.venv/Scripts/python.exe -m pytest -q E:/源知库/tests/unit --basetemp=E:/源知库/tests/runtime/development-pg-export-repair-20260729T130000Z/pytest-full-final-tmp -o cache_dir=E:/源知库/tests/runtime/development-pg-export-repair-20260729T130000Z/pytest-cache
46 passed, 2 skipped in 211.37s
```

The skips are the actual PostgreSQL integration tests, guarded by source and target PostgreSQL URLs.

## Remaining Blocker

Actual PostgreSQL source-to-empty-target backup/restore remains unexecuted. `POSTGRES_TEST_URL` and `POSTGRES_RESTORE_TEST_URL` are unset; `docker`, `docker compose`, and `psql` are unavailable. A disposable PostgreSQL source and separate empty target are required to run the skipped integration coverage. This report does not make an acceptance claim.
