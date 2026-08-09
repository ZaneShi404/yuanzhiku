# Independent PostgreSQL Backup/Restore Retest

Timestamp: 2026-07-29T05:07:12Z
Role: Independent TEST / verification
Workspace: `E:\源知库`
Runtime isolation: `E:\源知库\tests\runtime\independent-pg-backup-retest-20260729T050712Z`

## Scope and Method

Read frozen `docs/requirements.md`, `docs/test-plan.md`, `docs/api-contract.md`, current source, current tests, and relevant testing/infrastructure reports. I did not read `reports/development/*`, inspect Git history or commit messages, modify application source or existing test code, access `E:\源知库\data`, or create a commit.

Because existing tests hard-code prior `tests/runtime` directories, unmodified copies were run only from the isolated runtime directory. The independent verifier was created only in that isolated runtime directory.

## Findings

### P1: Invalid backup-catalog field values are accepted before PostgreSQL restore work begins

`TransferService._logical_records()` validates only that each row is a dict whose keys exactly match the expected column tuple, in the expected serialization order. It does not validate required field values or types. Therefore a `backups` row with all expected keys but `None` values is accepted by `_backup_records()`.

Evidence:

- `backend/app/services/transfers.py:278-292` validates only table set, list/dict shape, and `tuple(row)`.
- `backend/app/adapters/sqlite.py:153` defines required backup fields: `id`, `archive_name`, `manifest_sha256`, `state`, `created_at`.
- The isolated executable verifier passed a key-complete, all-`None` backup row through `_backup_records()` and recorded: `ACCEPTED (defect: values/types are not validated)`.

Impact: this violates the requested requirement that malformed/invalid backup catalog records fail validation safely. On the PostgreSQL restore path, validation occurs before target initialization, but an invalid value-complete catalog reaches PostgreSQL target initialization, artifact extraction, and `prepare_backup_restore()` before database constraints reject it. This can leave a new target partially initialized/modified rather than failing at archive validation. No live PostgreSQL instance was available to demonstrate the exact partial state, but the control flow is directly established by `backend/app/services/transfers.py:302-334`.

Required correction: validate every backup row against an explicit schema before any target repository is initialized or target files are created. Require non-empty string IDs/archive names/digests/states/timestamps and constrain state to supported values; do not rely on database constraints for untrusted archive validation.

### P1: SQLite portable export archive retains the operational backup catalog

The logical export/reimport inventory intentionally excludes `backups`: `EXPORT_TABLES` excludes it and reimport reads only `records.json`. However `_build_archive()` unconditionally adds `state/knowledge.db` for SQLite, including when `archive_type == "export"`. That SQLite snapshot contains the `backups` table and its records.

Evidence:

- `backend/app/adapters/sqlite.py:123-132`: `EXPORT_TABLES` excludes `backups`; `BACKUP_TABLES` includes it.
- `backend/app/services/transfers.py:89-104`: the SQLite database snapshot is added before selecting backup versus export logical records.
- Independent SQLite runtime validation created a prior backup record, exported, and found one `backups` row in `state/knowledge.db` inside the portable export ZIP. The same run confirmed `records.json` excludes `backups`, reimport produces no catalog rows, and the user/business source is imported successfully.

Impact: portable reimport semantics correctly exclude the catalog, but the portable export itself carries an operational catalog that its logical export contract excludes. This fails the stated export-exclusion acceptance target and carries unnecessary operational state.

Required correction: do not include `state/knowledge.db` in portable export archives. Retain it only for SQLite complete backup archives; portable exports should be solely validated portable logical records plus artifacts/derived data and manifest.

### No P0 evidenced

No data overwrite, access to the daily `E:\源知库\data` directory, or SQLite fallback after a configured PostgreSQL URL was observed.

## Source / Static Verification

These findings are static/source evidence, not proof against a live PostgreSQL server:

- PostgreSQL schema creates `backups` at `backend/migrations/postgresql/001_initial.sql:57`.
- PostgreSQL complete backup collection and insertion both iterate shared `BACKUP_TABLES`, which includes `backups`: `backend/app/adapters/postgres.py:208-225`.
- PostgreSQL portable collection iterates `EXPORT_TABLES`, which excludes `backups`: `backend/app/adapters/postgres.py:200-206`.
- PostgreSQL restore requires a target URL classified as PostgreSQL, and rejects a non-empty target: `backend/app/services/transfers.py:313-333`; `backend/app/adapters/postgres.py:231-238`.
- Explicit `postgresql://`, `postgres://`, and driver-qualified PostgreSQL URLs classify as PostgreSQL; unrecognized schemes raise before repository selection: `backend/app/core/config.py:78-90`.
- `ApplicationServices` selects `PostgresRepository` directly and has no SQLite fallback for that classification: `backend/app/main.py:53-62`.

## Runtime Validation

The independent verifier executed locally with SQLite and a recording/fake PostgreSQL target only. It confirmed:

- Complete backup inventory supplied by `PostgresRepository.rows_for_backup()` includes catalog records and `insert_backup_rows()` emits an `INSERT INTO backups` operation.
- SQLite complete backup/restore preserves an existing backup catalog record.
- Valid logical PostgreSQL backup data is routed to the PostgreSQL target path and includes catalog rows in the insert payload; this is simulated, not live PostgreSQL.
- Restore target safety: a non-empty target root is rejected before target repository initialization, and its sentinel file remains unchanged.
- Missing `backups` table and key-incomplete backup rows are rejected before PostgreSQL target initialization.
- Explicit PostgreSQL URL selects `PostgresRepository`; unreachable PostgreSQL URL and invalid database scheme fail without creating `state/knowledge.db`; unset URL defaults to SQLite.
- Portable logical `records.json` excludes `backups`; reimport preserves the business source and does not create catalog records. The embedded SQLite snapshot defect above remains.

## Automated Tests

All commands used the isolated test directory and never used `E:\源知库\data`.

| Command | Result |
| --- | --- |
| `PYTHONPATH="E:/源知库/backend" "E:/源知库/.venv/Scripts/python.exe" "E:/源知库/tests/runtime/independent-pg-backup-retest-20260729T050712Z/independent_verifier.py"` | Exit 0. Four verifier checks passed. It explicitly demonstrated acceptance of key-complete but value-invalid catalog rows, and detected backup rows in SQLite portable-export snapshot. |
| `PYTHONPATH="E:/源知库/backend" "E:/源知库/.venv/Scripts/python.exe" -m pytest -q "E:/源知库/tests/runtime/independent-pg-backup-retest-20260729T050712Z/tests/unit/test_database_url_selection.py" "E:/源知库/tests/runtime/independent-pg-backup-retest-20260729T050712Z/tests/unit/test_postgres_repository.py" --basetemp="E:/源知库/tests/runtime/independent-pg-backup-retest-20260729T050712Z/pytest-focused-tmp" -o cache_dir="E:/源知库/tests/runtime/independent-pg-backup-retest-20260729T050712Z/pytest-cache"` | `13 passed, 2 skipped` in 17.47s. The skips are the live PostgreSQL source/restore tests gated on both PostgreSQL URL variables. |
| `PYTHONPATH="E:/源知库/backend" "E:/源知库/.venv/Scripts/python.exe" -m pytest -q "E:/源知库/tests/runtime/independent-pg-backup-retest-20260729T050712Z/tests/unit" --basetemp="E:/源知库/tests/runtime/independent-pg-backup-retest-20260729T050712Z/pytest-full-tmp" -o cache_dir="E:/源知库/tests/runtime/independent-pg-backup-retest-20260729T050712Z/pytest-cache"` | `33 passed, 2 skipped` in 153.94s. |

## Live PostgreSQL Integration Blocker

Genuine PostgreSQL source-to-new-target backup/restore was not run and is not a pass.

- `POSTGRES_TEST_URL` is unset.
- `POSTGRES_RESTORE_TEST_URL` is unset.
- `docker --version`: exit 127, command not found.
- `docker compose version`: exit 127, command not found.
- `psql --version`: exit 127, command not found.

Therefore live PostgreSQL migration, physical connection, source backup creation, restore to a separate empty target database, and post-restore catalog query remain unresolved environmental validation. The two integration tests skip for this exact reason.

## Acceptance Gate

**BLOCKED / FAIL.**

Release acceptance cannot pass because two P1 findings violate the requested backup-catalog validation and portable-export exclusion behavior. Even after those defects are corrected, a live PostgreSQL source-to-empty-target integration run is still required before accepting the PostgreSQL backup/restore target.
